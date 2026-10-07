from enum import Enum, unique


@unique
class BatteryChargingStatus(Enum):
    """Battery charging status (V2 register 103), as BMSChargingStatus in the app."""

    NONE = 0
    CHARGE = 1
    DISCHARGE = 2
