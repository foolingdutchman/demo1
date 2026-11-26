from datetime import date
import math
import re

from excel_analysis import COTTON_QUOTATION_SHEETS, QUOTATION_SHEETS


QUOTATION_TYPES = {
    column: kind for column, _header, kind in COTTON_QUOTATION_SHEETS[0]["columns"]
}
QUOTATION_TYPES["quotation_date"] = "DATE"
PRODUCT_QUOTATION_KEYS = {
    "cotton": "quotation_summary",
    "sesame": "sesame",
    "peanut": "peanut",
    "peanut_meal": "peanut_meal",
}


def quotation_config(product):
    key = PRODUCT_QUOTATION_KEYS.get(product)
    return next(
        (config for config in QUOTATION_SHEETS if config["key"] == key),
        None,
    )


def validate_fields(fields, allow_empty_date=False, product=None):
    config = quotation_config(product) if product else None
    types = (
        {column: kind for column, _header, kind in config["columns"]}
        if config else QUOTATION_TYPES
    )
    types["quotation_date"] = "DATE"
    if not fields or not fields.keys() <= types.keys():
        raise ValueError("Invalid quotation fields")
    values = {}
    for column, raw in fields.items():
        value = raw.strip()
        kind = types[column]
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
