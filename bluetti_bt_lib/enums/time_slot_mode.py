from enum import Enum, unique


@unique
class TimeSlotMode(Enum):
    OFF = 0
    CHARGE = 1
    DISCHARGE = 2
    STANDBY = 3
