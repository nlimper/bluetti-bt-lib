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

    def is_valid_response(self, response: bytes) -> bool:
        """Function 16 answers with slave, function, start address and quantity"""
        return (
            len(response) == 8
            and response[:6] == bytes(self.cmd[:6])
            and super().is_valid_response(response)
        )

    def parse_response(self, response: bytes):
        return bytes(response[2:6])

    def __repr__(self):
        values = ", ".join(f"{v:#06x}" for v in self.values)
        return f"WriteableRegisters(address={self.address}, values=[{values}])"
