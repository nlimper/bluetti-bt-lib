from typing import Any

from . import FieldName, UIntField


class NumberField(UIntField):
    """Writeable unsigned value with a fixed range.

    must_be_below / must_be_above name another field this value has to stay
    under / over (e.g. SOC low limit below SOC high limit).
    """

    def __init__(
        self,
        name: FieldName,
        address: int,
        min: int,
        max: int,
        must_be_below: FieldName | None = None,
        must_be_above: FieldName | None = None,
    ):
        super().__init__(name, address, min=min, max=max)
        self.must_be_below = must_be_below.value if must_be_below else None
        self.must_be_above = must_be_above.value if must_be_above else None

    def is_writeable(self):
        return True

    def allowed_write_type(self, value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)
