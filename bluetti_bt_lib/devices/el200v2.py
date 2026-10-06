from ..base_devices import BaseDeviceV2
from ..fields import (
    BoolField,
    DecimalField,
    FieldName,
    TemperatureField,
    TimeSlotField,
    UInt32Field,
    UIntField,
)


class EL200V2(BaseDeviceV2):
    """BLUETTI Elite 200 V2.

    Read-only for now. Registers checked against a live unit; see
    docs in hassio-bluetti-bt. AC/DC output and AC ECO are reported but
    never written.
    """

    write_protected_addresses = frozenset(
        {
            2011,  # AC output
            2012,  # DC output
            2013,  # power off
            2017,  # AC ECO
            2018,  # AC ECO shutdown time
            2019,  # AC ECO minimum power
        }
    )

    def __init__(self):
        super().__init__(
            [
                UIntField(FieldName.CHARGE_TIME_REMAINING, 104, max=5993),
                UIntField(FieldName.DISCHARGE_TIME_REMAINING, 105, max=5993),
                # Live power, 32-bit low word first. 142/146 are apparent power.
                UInt32Field(FieldName.DC_OUTPUT_POWER, 140),
                UInt32Field(FieldName.AC_OUTPUT_APPARENT_POWER, 142),
                UInt32Field(FieldName.DC_INPUT_POWER, 144),
                UInt32Field(FieldName.AC_INPUT_APPARENT_POWER, 146),
                # Lifetime counters, 0.1 kWh
                UInt32Field(FieldName.ENERGY_DC_OUTPUT_TOTAL, 150, 0.1),
                UInt32Field(FieldName.ENERGY_AC_OUTPUT_TOTAL, 152, 0.1),
                UInt32Field(FieldName.ENERGY_PV_TOTAL, 154, 0.1),
                UInt32Field(FieldName.ENERGY_GRID_CHARGE_TOTAL, 156, 0.1),
                UInt32Field(FieldName.ENERGY_BATTERY_DISCHARGE_TOTAL, 167, 0.1),
                # Grid (AC input), phase 1
                DecimalField(FieldName.AC_INPUT_VOLTAGE, 1314, 1),
                DecimalField(FieldName.AC_INPUT_CURRENT, 1315, 1),
                # Load (AC output), phase 1
                DecimalField(FieldName.AC_OUTPUT_VOLTAGE, 1431, 1),
                DecimalField(FieldName.AC_OUTPUT_CURRENT, 1432, 1),
                # Inverter
                DecimalField(FieldName.AC_OUTPUT_FREQUENCY, 1500, 1),
                DecimalField(FieldName.INTERNAL_AC_VOLTAGE, 1511, 1),
                # Read-only states, never offered as switches
                BoolField(FieldName.AC_OUTPUT_ON, 2011),
                BoolField(FieldName.DC_OUTPUT_ON, 2012),
                BoolField(FieldName.ECO_AC_ON, 2017),
                # Customized UPS time slots (6 on this model, 3 registers each)
                TimeSlotField(FieldName.TIME_SLOT_1, 2030),
                TimeSlotField(FieldName.TIME_SLOT_2, 2033),
                TimeSlotField(FieldName.TIME_SLOT_3, 2036),
                TimeSlotField(FieldName.TIME_SLOT_4, 2039),
                TimeSlotField(FieldName.TIME_SLOT_5, 2042),
                TimeSlotField(FieldName.TIME_SLOT_6, 2045),
                # Battery
                DecimalField(FieldName.BATTERY_VOLTAGE, 6003, 2),
                UIntField(FieldName.BATTERY_SOH, 6006, max=100),
                TemperatureField(FieldName.BATTERY_TEMPERATURE, 6007),
            ],
        )
