import struct

from . import DeviceField, FieldName


class TemperatureField(DeviceField):
    """Temperature in degrees Celsius, stored with an offset (V2: raw - 40)."""

    def __init__(self, name: FieldName, address: int, offset: int = -40):
        super().__init__(name, address, 1)
        self.offset = offset

    def parse(self, data: bytes) -> int | None:
        raw = struct.unpack("!H", data)[0]
        if raw == 0:
            return None
        return raw + self.offset

    def in_range(self, value: int | None) -> bool:
        return value is not None and -40 <= value <= 120
