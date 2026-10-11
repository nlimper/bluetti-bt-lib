import asyncio
import struct
import unittest
import unittest.mock

from bleak.exc import BleakError

from bluetti_bt_lib.base_devices import BaseDeviceV1
from bluetti_bt_lib import DeviceReader
from bluetti_bt_lib.bluetooth.device_reader import DeviceReaderConfig
from bluetti_bt_lib.bluetooth.encryption import KEX_MAGIC, MessageType, hexsum
from bluetti_bt_lib.fields import FieldName
from bluetti_bt_lib.registers import ReadableRegisters
from bluetti_bt_lib.registers.DeviceRegister import modbus_crc
from bluetti_bt_lib.utils.bleak_client_mock import ClientMockNoEncryption


def build_challenge_frame() -> bytearray:
    """Build a well-formed pre-key-exchange CHALLENGE frame."""
    body = bytes([MessageType.CHALLENGE.value, 0]) + b"\x01\x02\x03\x04"
    return bytearray(KEX_MAGIC + body + hexsum(body, 2))


async def fast_wait_for(fut, timeout):
    return await _real_wait_for(fut, timeout=0.05)


_real_wait_for = asyncio.wait_for


class SilentAfterClient:
    """Answers the first `answers` requests like the mock, then stays silent."""

    def __init__(self, mock, answers: int):
        self.mock = mock
        self.answers = answers
        self.requests = 0

    async def start_notify(self, char, callback, **kwargs):
        await self.mock.start_notify(char, callback)

    async def stop_notify(self, char):
        return

    async def disconnect(self):
        return

    async def write_gatt_char(self, char, data, response=None):
        self.requests += 1
        if self.requests <= self.answers:
            await self.mock.write_gatt_char(char, data)


class FailingWriteClient:
    """BLE client whose writes always fail, as when the peer drops the link."""

    async def write_gatt_char(self, *_args, **_kwargs):
        raise BleakError("Not connected")


class TestDeviceReader(unittest.IsolatedAsyncioTestCase):
    def __init__(self, methodName="runTest"):
        super().__init__(methodName)
        self.ble_mock = ClientMockNoEncryption()

        # Device type
        self.ble_mock.add_r_str(10, "AC300", 6)
        # Serial
        self.ble_mock.add_r_sn(17, 2300000000000)
        # DC input power
        self.ble_mock.add_r_int(36, 10)
        # AC input power
        self.ble_mock.add_r_int(37, 8)
        # AC output power
        self.ble_mock.add_r_int(38, 9)
        # AC output power
        self.ble_mock.add_r_int(39, 7)
        # SOC
        self.ble_mock.add_r_int(43, 78)

    async def test_read_all_correct(self):
        device = BaseDeviceV1()
        reader = DeviceReader(
            "00:11:00:11:00:11",
            device,
            asyncio.Future,
            ble_client=self.ble_mock,
        )

        data = await reader.read()

        self.assertEqual(data.get(FieldName.DEVICE_TYPE.value), "AC300")
        self.assertEqual(data.get(FieldName.DEVICE_SN.value), 2300000000000)
        self.assertEqual(data.get(FieldName.DC_INPUT_POWER.value), 10)
        self.assertEqual(data.get(FieldName.AC_INPUT_POWER.value), 8)
        self.assertEqual(data.get(FieldName.AC_OUTPUT_POWER.value), 9)
        self.assertEqual(data.get(FieldName.DC_OUTPUT_POWER.value), 7)
        self.assertEqual(data.get(FieldName.BATTERY_SOC.value), 78)

    async def test_notification_with_done_future(self):
        # Late or duplicate notifications must not raise InvalidStateError (#64)
        device = BaseDeviceV1()
        reader = DeviceReader(
            "00:11:00:11:00:11",
            device,
            asyncio.Future,
            ble_client=self.ble_mock,
        )
        reader.current_registers = ReadableRegisters(43, 1)
        answer = await self.ble_mock._get_register(43, 1)

        # A valid answer resolves the future
        reader.notify_future = asyncio.Future()
        await reader._notification_handler(0, answer)
        self.assertTrue(reader.notify_future.done())

        # Duplicate notification arriving after the future is resolved
        await reader._notification_handler(0, bytearray(b"\x03\x04"))
        self.assertEqual(reader.notify_future.result(), bytes(answer))

        # Late notification after the future was cancelled (response timeout)
        reader.notify_response = bytearray()
        reader.notify_future = asyncio.Future()
        reader.notify_future.cancel()
        await reader._notification_handler(0, bytearray(b"\x05\x06"))
        self.assertTrue(reader.notify_future.cancelled())

    async def test_answer_for_other_request_is_discarded(self):
        # A late answer to an earlier request (other length) must not be taken as the answer
        # to the current one; the right answer that follows is accepted.
        reader = DeviceReader("00:11:00:11:00:11", BaseDeviceV1(), asyncio.Future, ble_client=self.ble_mock)
        reader.current_registers = ReadableRegisters(43, 1)
        reader.notify_response = bytearray()
        reader.notify_future = asyncio.Future()
        await reader._notification_handler(0, await self.ble_mock._get_register(36, 4))
        self.assertFalse(reader.notify_future.done())
        await reader._notification_handler(0, await self.ble_mock._get_register(43, 1))
        self.assertEqual(reader.current_registers.parse_response(reader.notify_future.result()), b"\x00\x4e")

    async def test_corrupt_answer_is_discarded(self):
        reader = DeviceReader("00:11:00:11:00:11", BaseDeviceV1(), asyncio.Future, ble_client=self.ble_mock)
        reader.current_registers = ReadableRegisters(43, 1)
        reader.notify_response = bytearray()
        reader.notify_future = asyncio.Future()
        bad = bytearray(await self.ble_mock._get_register(43, 1))
        bad[4] ^= 0xFF                                  # CRC no longer matches
        await reader._notification_handler(0, bad)
        self.assertFalse(reader.notify_future.done())

    async def test_answer_in_pieces(self):
        reader = DeviceReader("00:11:00:11:00:11", BaseDeviceV1(), asyncio.Future, ble_client=self.ble_mock)
        reader.current_registers = ReadableRegisters(36, 4)
        reader.notify_response = bytearray()
        reader.notify_future = asyncio.Future()
        answer = await self.ble_mock._get_register(36, 4)
        await reader._notification_handler(0, answer[:6])
        self.assertFalse(reader.notify_future.done())
        await reader._notification_handler(0, answer[6:])
        self.assertEqual(reader.notify_future.result(), bytes(answer))

    async def test_exception_answer_gives_empty_body(self):
        reader = DeviceReader("00:11:00:11:00:11", BaseDeviceV1(), asyncio.Future, ble_client=self.ble_mock)
        reader.current_registers = ReadableRegisters(190, 10)
        reader.notify_response = bytearray()
        reader.notify_future = asyncio.Future()
        exc = bytearray(b"\x01\x83\x02\x00\x00")
        struct.pack_into("<H", exc, -2, modbus_crc(exc[:-2]))
        await reader._notification_handler(0, exc)
        self.assertEqual(reader.notify_future.result(), bytes())

    async def test_timeout_aborts_the_read(self):
        # No answer to one request: the whole read fails (no partial data that could be shifted)
        mock = SilentAfterClient(self.ble_mock, answers=1)
        reader = DeviceReader("00:11:00:11:00:11", BaseDeviceV1(), asyncio.Future, ble_client=mock)
        with unittest.mock.patch("bluetti_bt_lib.bluetooth.device_reader.asyncio.wait_for",
                                 side_effect=fast_wait_for):
            data = await reader.read()
        self.assertIsNone(data)
        self.assertEqual(mock.requests, 2)        # stopped after the request without an answer

    async def test_cancel_is_not_swallowed(self):
        mock = SilentAfterClient(self.ble_mock, answers=0)
        reader = DeviceReader("00:11:00:11:00:11", BaseDeviceV1(), asyncio.Future, ble_client=mock)
        task = asyncio.ensure_future(reader.read())
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_read_soc_wrong(self):
        # SOC
        self.ble_mock.add_r_int(43, 1234)

        device = BaseDeviceV1()
        reader = DeviceReader(
            "00:11:00:11:00:11",
            device,
            asyncio.Future,
            ble_client=self.ble_mock,
        )

        data = await reader.read()

        self.assertIsNone(data.get(FieldName.BATTERY_SOC.value))

    async def test_handshake_write_failure_is_contained(self):
        # A write can fail mid-handshake when the peripheral drops the link.
        # The notification handler must not let BleakError escape into bleak's
        # callback dispatch, where it surfaces without naming the stalled read.
        reader = DeviceReader(
            "00:11:00:11:00:11",
            BaseDeviceV1(),
            asyncio.Future,
            config=DeviceReaderConfig(use_encryption=True),
        )
        reader.client = FailingWriteClient()

        await reader._notification_handler(0, build_challenge_frame())
