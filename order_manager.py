import json
import re

import db_manager
import quotation_manager
import sudan_cotton_pricing


PRICING_FIELDS = {
    "direct_buyer_bid_cny_per_ton", "buyer_bid_vat_inclusive",
    "domestic_benchmark_cny_per_ton", "benchmark_vat_inclusive",
    "quality_discount_cny_per_ton", "quality_discount_basis",
    "domestic_sales_vat_percent", "fx_cny_per_usd", "duty_percent",
    "import_vat_percent", "import_vat_recoverable_percent", "quota_cost_cny_per_ton",
    "quota_recoverable_vat_cny_per_ton", "other_nonrecoverable_cost_cny_per_ton",
    "other_recoverable_vat_cny_per_ton", "target_profit_cny_per_ton",
    "supplier_quote_usd_per_ton", "supplier_quote_basis", "ocean_freight_usd_per_ton",
    "insurance_usd_per_ton", "other_cif_cost_usd_per_ton", "quantity_tons",
}
BENCHMARK_SOURCES = {
    "manual", "zce_cotton", "cc_index_2227b", "cc_index_3128b", "cc_index_2129b",
}
SOURCE_FIELDS = {"source_filename", "source_sheet_name"}


def validate_order(fields, allow_quotation_id):
    pricing = {
        key.removeprefix("pricing_"): value
        for key, value in fields.items() if key.startswith("pricing_")
    }
    quotation = {
        key.removeprefix("quotation_"): value
        for key, value in fields.items() if key.startswith("quotation_")
        and key != "quotation_id"
    }
    allowed = (
        {f"pricing_{field}" for field in PRICING_FIELDS}
        | {f"quotation_{field}" for field in quotation_manager.QUOTATION_TYPES}
        | {f"quotation_{field}" for field in SOURCE_FIELDS}
        | {"benchmark_source", "delivery_date"}
    )
    if allow_quotation_id:
        allowed.add("quotation_id")
    if fields.keys() - allowed or pricing.keys() != PRICING_FIELDS:
        raise ValueError("Invalid order fields")
    if not allow_quotation_id and quotation.keys() != quotation_manager.QUOTATION_TYPES.keys() | SOURCE_FIELDS:
        raise ValueError("Incomplete order quotation fields")
    source = fields.get("benchmark_source")
    if source not in BENCHMARK_SOURCES:
        raise ValueError("Invalid benchmark source")
    if not pricing["quantity_tons"].strip() or not pricing["supplier_quote_usd_per_ton"].strip():
        raise ValueError("Quantity and supplier price are required")
    result = sudan_cotton_pricing.calculate_sudan_cotton_pricing(pricing)
    if result["inputs"]["quantity_tons"] is None or result["inputs"]["supplier_quote_usd_per_ton"] is None:
        raise ValueError("Quantity and supplier price are required")
    data = {field: quotation.get(field, "") for field in quotation_manager.QUOTATION_TYPES}
    values = quotation_manager.validate_fields(data)
    delivery_date = quotation_manager.validate_fields(
        {"quotation_date": fields.get("delivery_date", "")}
    )["quotation_date"]
    for field in SOURCE_FIELDS:
        values[field] = quotation.get(field, "").strip() or None
    return (
        values, source, delivery_date,
        json.dumps(pricing, ensure_ascii=False, allow_nan=False),
        json.dumps(result, ensure_ascii=False, allow_nan=False),
        result["inputs"]["quantity_tons"], result["inputs"]["supplier_quote_usd_per_ton"],
        result["inputs"]["supplier_quote_basis"],
    )


def create_order(user_id, fields):
    values = validate_order(fields, allow_quotation_id=True)
    raw_id = fields.get("quotation_id", "")
    if raw_id:
        if not re.fullmatch(r"[1-9][0-9]{0,18}", raw_id) or int(raw_id) >= 2 ** 63:
            raise ValueError("Invalid quotation id")
        quotation_id = int(raw_id)
    else:
        quotation_id = None
    return db_manager.create_order(user_id, quotation_id, *values)


def update_order(user_id, order_id, fields):
    return db_manager.update_order(user_id, order_id, *validate_order(fields, allow_quotation_id=False))
