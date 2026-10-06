import struct

from . import DeviceField, FieldName


class UInt32Field(DeviceField):
    """Unsigned 32-bit value over two registers, low word first.

    This is how the BLUETTI app reads V2 totals (bit32HexSwap): power at
    140-147 and the energy counters from 150 on.
    """

    def __init__(self, name: FieldName, address: int, multiplier: float = 1):
        super().__init__(name, address, 2)
        self.multiplier = multiplier

    def parse(self, data: bytes) -> float | int:
        low, high = struct.unpack("!HH", data)
        val = (high << 16) | low
        if self.multiplier != 1:
            return round(val * self.multiplier, 2)
        return val
