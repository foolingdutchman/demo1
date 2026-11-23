from datetime import date
import math
import re

from excel_analysis import COTTON_QUOTATION_SHEETS


QUOTATION_TYPES = {
    column: kind for column, _header, kind in COTTON_QUOTATION_SHEETS[0]["columns"]
}
QUOTATION_TYPES["quotation_date"] = "DATE"


def validate_fields(fields, allow_empty_date=False):
    if not fields or not fields.keys() <= QUOTATION_TYPES.keys():
        raise ValueError("Invalid quotation fields")
    values = {}
    for column, raw in fields.items():
        value = raw.strip()
        kind = QUOTATION_TYPES[column]
        if kind == "DATE":
            if not value and allow_empty_date:
                value = None
            else:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("Invalid date")
                date.fromisoformat(value)
        elif not value:
            value = None
        elif kind == "INTEGER":
            if not re.fullmatch(r"[+-]?\d+", value):
                raise ValueError("Invalid integer")
            value = int(value)
            if not -(2 ** 63) <= value < 2 ** 63:
                raise ValueError("Integer out of range")
        elif kind == "REAL":
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("Invalid number")
        values[column] = value
    return values
