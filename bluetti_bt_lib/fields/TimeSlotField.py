from dataclasses import dataclass
from typing import Any, List

from ..enums import TimeSlotMode
from . import DeviceField, FieldName


@dataclass(frozen=True)
class TimeSlot:
    mode: TimeSlotMode
    start: str  # "HH:MM"
    end: str  # "HH:MM"


class TimeSlotField(DeviceField):
    """One time slot of the V2 working-time list (2030 on, 3 registers each).

    Registers: [flag, mode in the low byte], [start hour | start minute],
    [end hour | end minute]. A slot with 00:00-00:00 is unused, whatever its
    mode, as in the BLUETTI app.
    """

    def __init__(self, name: FieldName, address: int):
        super().__init__(name, address, 3)

    def parse(self, data: bytes) -> TimeSlot | None:
        mode_raw = data[1]
        start_h, start_m, end_h, end_m = data[2], data[3], data[4], data[5]

        if mode_raw not in [m.value for m in TimeSlotMode]:
            return None
        if start_h > 23 or end_h > 24 or start_m > 59 or end_m > 59:
            return None

        mode = TimeSlotMode(mode_raw)
        if start_h == start_m == end_h == end_m == 0:
            mode = TimeSlotMode.OFF

        return TimeSlot(mode, f"{start_h:02d}:{start_m:02d}", f"{end_h:02d}:{end_m:02d}")

    def in_range(self, value: TimeSlot | None) -> bool:
        return value is not None

    def is_writeable(self):
        return True

    def allowed_write_type(self, value: Any) -> bool:
        if not isinstance(value, TimeSlot) or not isinstance(value.mode, TimeSlotMode):
            return False
        start, end = _minutes(value.start), _minutes(value.end)
        if start is None or end is None:
            return False
        # An unused slot may hold any times; an active one must run forward
        return value.mode == TimeSlotMode.OFF or start < end

    def encode(self, value: TimeSlot) -> List[int]:
        """Register values for a slot: mode, start hh|mm, end hh|mm."""
        sh, sm = (int(x) for x in value.start.split(":"))
        eh, em = (int(x) for x in value.end.split(":"))
        return [value.mode.value, (sh << 8) | sm, (eh << 8) | em]


def _minutes(hhmm: str) -> int | None:
    """Minutes since midnight for "HH:MM" (00:00-23:59), None when invalid."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except (AttributeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m
