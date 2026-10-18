import asyncio
import logging
import async_timeout
from typing import Any, Callable, List, cast
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .encrypted_session import EncryptedSession
from ..base_devices import BluettiDevice
from ..const import NOTIFY_UUID, WRITE_UUID
from ..registers import ReadableRegisters, DeviceRegister
from ..utils.privacy import mac_loggable


class ResponseTimeout(Exception):
    """No valid answer to a request in time.

    The read is aborted and the connection closed: an answer that arrives late could otherwise
    be taken for the answer to the next request (a function 03 answer does not say which
    registers it holds), which shifted values into other fields (seen in Home Assistant:
    SOC high showing the value of SOC low, time slots showing the neighbouring slot).
    """


# Seconds to wait after an unanswered request before disconnecting, so that a late answer still
# arrives (and is dropped) instead of the link going down while the unit is sending.
LATE_ANSWER_GRACE = 3

# Connection attempts per read. Every attempt costs the unit a new key exchange, and a unit that
# does not answer gets nothing from being asked ten times in a row; the caller retries later.
CONNECT_ATTEMPTS = 3


class DeviceReaderConfig:
    def __init__(
        self,
        timeout: int = 60,
        use_encryption: bool = False,
        keep_alive_seconds: float = 0,
    ):
        self.timeout = timeout
        self.use_encryption = use_encryption
        self.keep_alive_seconds = keep_alive_seconds
        """Hold the link open between reads. 0 disconnects after each read."""


class DeviceReader:
    def __init__(
        self,
        mac: str,
        bluetti_device: BluettiDevice,
        future_builder_method: Callable[[], asyncio.Future[Any]],
        config: DeviceReaderConfig = DeviceReaderConfig(),
        lock: asyncio.Lock = asyncio.Lock(),
        ble_client: BleakClient | None = None,
    ):
        self.mac = mac
        self.bluetti_device = bluetti_device
        self.create_future = future_builder_method
        self.config = config
        self.polling_lock = lock

        self.ble_client = ble_client
        """Used for unittests"""

        self.logger = logging.getLogger(
            f"{__name__}.{mac_loggable(mac).replace(':', '_')}"
        )

        self.device = None
        self.client = None

        self.has_notifier = False
        self.current_registers = None
        self.notify_response = bytearray()
        self.notify_future: asyncio.Future[Any] | None = None
        self.session = EncryptedSession(self.logger)
        self._generation = 0
        self._read_ok = False

    @property
    def encryption(self):
        return self.session.encryption

    @property
    def encrypted_buffer(self):
        return self.session.buffer

    @property
    def handshake_failed(self) -> bool:
        return self.session.failed

    @handshake_failed.setter
    def handshake_failed(self, value: bool):
        self.session.failed = value

    async def read(
        self, only_registers: List[ReadableRegisters] | None = None, raw: bool = False
    ) -> dict | None:

        registers = self.bluetti_device.get_polling_registers()
        pack_registers = self.bluetti_device.get_pack_polling_registers()

        if only_registers is not None:
            registers = only_registers
            pack_registers = []

        parsed_data: dict = {}
        self._generation += 1
        self._read_ok = False

        self.logger.debug("Reading device registers")

        async with self.polling_lock:
            try:
                async with async_timeout.timeout(self.config.timeout):
                    if self._reusable():
                        self.logger.debug("Reusing the open connection")
                    else:
                        await self._teardown()
                        self.session.start()

                        self.logger.debug("Searching for device")

                        if self.ble_client:
                            self.device = None
                        else:
                            self.device = await BleakScanner.find_device_by_address(
                                self.mac, timeout=5
                            )

                            if self.device is None:
                                self.logger.error("Device not found")
                                return

                        self.logger.debug("Connecting to device")

                        if self.ble_client:
                            self.client = self.ble_client
                        else:
                            self.client = await establish_connection(
                                BleakClientWithServiceCache,
                                self.device,
                                self.device.name or "Unknown Device",
                                max_attempts=CONNECT_ATTEMPTS,
                            )

                        self.logger.debug("Connected to device")

                        await self.client.start_notify(
                            NOTIFY_UUID, self._notification_handler
                        )
                        self.has_notifier = True

                        self.logger.debug("Notification handler setup complete")

                    while (
                        self.config.use_encryption
                        and not self.session.is_ready
                    ):
                        if self.session.failed:
                            # Only a reconnect makes the peer send a fresh challenge.
                            self.logger.warning(
                                "Handshake failed, reconnecting on the next read"
                            )
                            return None
                        await asyncio.sleep(5)
                        self.logger.debug("Encryption handshake not finished yet")

                    for register in registers:
                        body = register.parse_response(
                            await self._async_send_command(register)
                        )

                        self.logger.debug("Raw data: %s", body)

                        if raw:
                            d = {}
                            d[register.starting_address] = body
                            parsed_data.update(d)
                            continue

                        parsed = self.bluetti_device.parse(
                            register.starting_address, body
                        )

                        self.logger.debug("Parsed data: %s", parsed)

                        parsed_data.update(parsed)

                    for pack in range(1, self.bluetti_device.max_packs + 1):
                        body = register.parse_response(
                            await self._async_send_command(
                                self.bluetti_device.get_pack_selector(pack),
                            )
                        )

                        # We need to wait for the powerstation to populate all registers
                        await asyncio.sleep(3)

                        for register in pack_registers:
                            body = register.parse_response(
                                await self._async_send_command(register)
                            )

                            self.logger.debug("Raw data: %s", body)

                            if raw:
                                d = {}
                                d[register.starting_address] = body
                                parsed_data.update(d)
                                continue

                            parsed = self.bluetti_device.parse(
                                register.starting_address,
                                body,
                                pack_num=pack,
                            )

                            self.logger.debug("Parsed data: %s", parsed)

                            parsed_data.update(parsed)

                    self._read_ok = bool(parsed_data)

            except ResponseTimeout as err:
                self.logger.warning("No valid response to %s, read aborted", err)
                await asyncio.sleep(LATE_ANSWER_GRACE)
                return None
            except TimeoutError:
                self.logger.warning("Timeout")
                return None
            except BleakError as err:
                self.logger.warning("Bleak error: %s", err)
                return None
            except Exception as err:
                # Not BaseException: cancelling (Home Assistant stopping) must stop the read,
                # not let it continue with answers that may belong to other requests.
                self.logger.warning("Unknown error %s", err)
                return None
            finally:
                if self._keep_alive_wanted():
                    self._schedule_release()
                else:
                    await self._teardown()

            # Check if dict is empty
            if not parsed_data:
                return None

            return parsed_data

    async def release(self):
        """Give the device up so another client can take it.

        A unit accepts one connection at a time, so a held link blocks the
        writer and the vendor app until it is dropped.
        """
        self._generation += 1
        async with self.polling_lock:
            await self._teardown()

    def _keep_alive_wanted(self) -> bool:
        return (
            self.config.keep_alive_seconds > 0
            and self._read_ok
            and not self.session.failed
            and self.client is not None
        )

    def _schedule_release(self):
        self._generation += 1
        asyncio.ensure_future(
            self._release_after(self.config.keep_alive_seconds, self._generation)
        )

    async def _release_after(self, delay: float, generation: int):
        await asyncio.sleep(delay)
        async with self.polling_lock:
            # A read that started meanwhile bumped the generation and owns the link.
            if generation != self._generation:
                return
            await self._teardown()
            self.logger.debug("Released the idle connection")

    async def _teardown(self):
        if self.has_notifier and self.client:
            try:
                await self.client.stop_notify(NOTIFY_UUID)
                self.logger.debug("Stopped notifier")
            except Exception:
                pass
            self.has_notifier = False
        if self.client:
            try:
                await self.client.disconnect()
                self.logger.debug("Disconnected from device")
            except Exception:
                pass
        self.client = None
        self.session.reset()

    def _reusable(self) -> bool:
        if self.config.keep_alive_seconds <= 0 or self.client is None:
            return False
        if not self.client.is_connected:
            return False
        if not self.has_notifier:
            return False
        return self.session.is_ready or not self.config.use_encryption

    async def _async_send_command(self, registers: DeviceRegister) -> bytes:
        """Send a command and return the validated response.

        Raises ResponseTimeout when no valid answer arrives in time; the caller must then stop
        using this connection. A MODBUS exception answer (e.g. a register range the device does
        not support) returns an empty response.
        """
        self.current_registers = registers
        self.notify_response = bytearray()
        self.notify_future = self.create_future()
        self.session.buffer.clear()

        command_bytes = bytes(registers)

        # Encrypt command
        if self.config.use_encryption is True:
            if not self.session.is_ready:
                return bytes()
            command_bytes = self.session.encrypt_command(command_bytes)

        try:
            # Make request
            await self.client.write_gatt_char(WRITE_UUID, command_bytes)

            self.logger.debug("Request sent (%s)", registers)

            # Wait for response
            res = await asyncio.wait_for(self.notify_future, timeout=5)

            self.logger.debug("Got response")

            return cast(bytes, res)
        except asyncio.TimeoutError as err:
            raise ResponseTimeout(registers) from err
        finally:
            # Answers that arrive from now on are not for a pending request: drop them
            self.notify_future = None

    async def _notification_handler(self, _: int, data: bytearray):
        """Handle bt data: collect the answer to the current request and check it."""
        self.logger.debug("Got new data (%d bytes)", len(data))

        if self.config.use_encryption is True:
            payload = await self.session.consume(data, self.client)
            if payload is None:
                return
            data = payload

        if self.notify_future is None:
            self.logger.debug("Dropping notification, no request pending")
            return

        if self.notify_future.done():
            self.logger.debug("Dropping notification for already completed future")
            return

        self.notify_response.extend(data)
        response = bytes(self.notify_response)
        registers = self.current_registers

        if registers.is_valid_exception(response):
            self.logger.debug("Exception response to %s", registers)
            self.notify_future.set_result(bytes())
            return

        size = registers.response_size()
        if len(response) < size:
            return  # wait for the rest

        if len(response) == size and registers.is_valid_response(response):
            self.notify_future.set_result(response)
            return

        # Not the answer to this request (late answer to an earlier one, or corrupted):
        # discard and keep waiting; if no valid answer follows, the request times out.
        self.logger.debug("Discarding response that does not match %s: %s", registers, response.hex())
        self.notify_response = bytearray()
