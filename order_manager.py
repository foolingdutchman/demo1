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
        | {"benchmark_source", "delivery_date", "supplier_id", "intermediary_id"}
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
    hidden_pricing_available = (
        pricing["domestic_benchmark_cny_per_ton"].strip()
        and pricing["fx_cny_per_usd"].strip()
    )
    if hidden_pricing_available:
        result = sudan_cotton_pricing.calculate_sudan_cotton_pricing(pricing)
    else:
        quantity = float(pricing["quantity_tons"])
        supplier_quote = float(pricing["supplier_quote_usd_per_ton"])
        if quantity <= 0 or supplier_quote <= 0:
            raise ValueError("Quantity and supplier price are required")
        if pricing["supplier_quote_basis"] not in {"CIF", "FOB"}:
            raise ValueError("Invalid supplier quote basis")
        supplier_cif = supplier_quote
        if pricing["supplier_quote_basis"] == "FOB":
            supplier_cif += sum(
                float(pricing[field] or 0)
                for field in (
                    "ocean_freight_usd_per_ton",
                    "insurance_usd_per_ton",
                    "other_cif_cost_usd_per_ton",
                )
            )
        result = {
            "inputs": {
                **pricing,
                "quantity_tons": quantity,
                "supplier_quote_usd_per_ton": supplier_quote,
                "supplier_quote_basis": pricing["supplier_quote_basis"],
            },
            "max_target_cif_usd_per_ton": supplier_cif,
            "break_even_cif_usd_per_ton": supplier_cif,
            "supplier_evaluation": {
                "cif_usd_per_ton": supplier_cif,
                "profit_cny_per_ton": 0,
            },
            "scenarios": {"fx": [], "supplier_quote": []},
        }
    if result["inputs"]["quantity_tons"] is None or result["inputs"]["supplier_quote_usd_per_ton"] is None:
        raise ValueError("Quantity and supplier price are required")
    data = {field: quotation.get(field, "") for field in quotation_manager.QUOTATION_TYPES}
    values = quotation_manager.validate_fields(data)
    delivery_date = quotation_manager.validate_fields(
        {"quotation_date": fields.get("delivery_date", "")}
    )["quotation_date"]
    for field in SOURCE_FIELDS:
        values[field] = quotation.get(field, "").strip() or None
    raw_supplier_id = fields.get("supplier_id", "")
    if raw_supplier_id:
        if not re.fullmatch(r"[1-9]\d{0,18}", raw_supplier_id) or int(raw_supplier_id) >= 2 ** 63:
            raise ValueError("Invalid supplier id")
        supplier_id = int(raw_supplier_id)
    else:
        supplier_id = None
    raw_intermediary_id = fields.get("intermediary_id", "")
    if raw_intermediary_id:
        if (
            not re.fullmatch(r"[1-9]\d{0,18}", raw_intermediary_id)
            or int(raw_intermediary_id) >= 2 ** 63
        ):
            raise ValueError("Invalid intermediary id")
        intermediary_id = int(raw_intermediary_id)
    else:
        intermediary_id = None
    if supplier_id == intermediary_id:
        raise ValueError("Supplier and intermediary must differ")
    return (
        supplier_id, intermediary_id, values, source, delivery_date,
        json.dumps(pricing, ensure_ascii=False, allow_nan=False),
        json.dumps(result, ensure_ascii=False, allow_nan=False),
        result["inputs"]["quantity_tons"], result["inputs"]["supplier_quote_usd_per_ton"],
        result["inputs"]["supplier_quote_basis"],
    )


def create_order(user_id, fields):
    values = validate_order(fields, allow_quotation_id=True)
    if values[0] is None or not db_manager.supplier_belongs_to_user(user_id, values[0]):
        raise ValueError("Supplier is required")
    raw_id = fields.get("quotation_id", "")
    if raw_id:
        if not re.fullmatch(r"[1-9][0-9]{0,18}", raw_id) or int(raw_id) >= 2 ** 63:
            raise ValueError("Invalid quotation id")
        quotation_id = int(raw_id)
    else:
        quotation_id = None
    return db_manager.create_order(
        user_id, values[0], values[1], quotation_id, *values[2:]
    )


def update_order(user_id, order_id, fields):
    values = validate_order(fields, allow_quotation_id=False)
    owned_orders = db_manager.list_orders(user_id, order_id)
    if not owned_orders:
        return {"updated": 0, "purchase_contract_deleted": 0}
    if values[0] is not None and not db_manager.supplier_belongs_to_user(
        user_id, values[0]
    ):
        raise ValueError("Supplier is required")
    if values[1] is not None and not db_manager.supplier_belongs_to_user(
        user_id, values[1]
    ):
        raise ValueError("Intermediary is invalid")
    if values[0] is None:
        values = (owned_orders[0].get("supplier_id"), *values[1:])
    if values[1] is None:
        values = (values[0], owned_orders[0].get("intermediary_id"), *values[2:])
    return db_manager.update_order(user_id, order_id, *values)
