import struct
from typing import List

from . import DeviceRegister, RegisterAction


class WriteableRegisters(DeviceRegister):
    """Write several consecutive registers at once (Modbus function 16)."""

    def __init__(self, address: int, values: List[int]):
        super().__init__(
            RegisterAction.WRITE_MULTIPLE,
            struct.pack(f"!HHB{len(values)}H", address, len(values), 2 * len(values), *values),
        )
        self.address = address
        self.values = list(values)

    def response_size(self):
        return 8

    def parse_response(self, response: bytes):
        return bytes(response[2:6])

    def __repr__(self):
        values = ", ".join(f"{v:#06x}" for v in self.values)
        return f"WriteableRegisters(address={self.address}, values=[{values}])"
