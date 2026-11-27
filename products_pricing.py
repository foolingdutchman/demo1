import math


USD_PER_METRIC_TON_PER_US_CENT_PER_LB = 22.0462


class PricingInputError(ValueError):
    pass


PRODUCT_VAT_PERCENT = {
    "sesame": 9,
    "peanut": 9,
    "peanut_meal": 0,
}


def calculate_same_cost_import_price(
    current_price_cny_per_ton,
    usd_cny_rate,
    customs_cost_cny_per_ton=250,
    vat_factor=1.09,
):
    try:
        current_price = float(current_price_cny_per_ton)
        exchange_rate = float(usd_cny_rate)
        customs_cost = float(customs_cost_cny_per_ton)
        vat_factor = float(vat_factor)
    except (TypeError, ValueError) as error:
        raise PricingInputError("invalid_import_price_input") from error
    if (
        not all(math.isfinite(value) for value in (
            current_price,
            exchange_rate,
            customs_cost,
            vat_factor,
        ))
        or current_price <= 0
        or exchange_rate <= 0
        or customs_cost < 0
        or vat_factor <= 0
    ):
        raise PricingInputError("invalid_import_price_input")
    price = (current_price - customs_cost) / vat_factor / exchange_rate
    return price if math.isfinite(price) and price > 0 else None


def usd_per_metric_ton_to_us_cents_per_lb(usd_per_metric_ton):
    return usd_per_metric_ton / USD_PER_METRIC_TON_PER_US_CENT_PER_LB


def _number(fields, name, optional=False):
    value = fields.get(name, "")
    if optional and value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise PricingInputError(name) from error
    if not math.isfinite(number):
        raise PricingInputError(name)
    return number


def _percentage(fields, name):
    value = _number(fields, name)
    if value < 0 or value > 100:
        raise PricingInputError(name)
    return value / 100


def calculate_sudan_cotton_pricing(fields):
    benchmark = _number(fields, "domestic_benchmark_cny_per_ton")
    direct_buyer_bid = _number(fields, "direct_buyer_bid_cny_per_ton", optional=True)
    quality_discount = _number(fields, "quality_discount_cny_per_ton")
    sales_vat_rate = _percentage(fields, "domestic_sales_vat_percent")
    fx = _number(fields, "fx_cny_per_usd")
    duty_rate = _percentage(fields, "duty_percent")
    import_vat_rate = _percentage(fields, "import_vat_percent")
    recoverable_share = _percentage(fields, "import_vat_recoverable_percent")
    quota_cost = _number(fields, "quota_cost_cny_per_ton")
    quota_recoverable_vat = _number(
        fields, "quota_recoverable_vat_cny_per_ton"
    )
    other_nonrecoverable_cost = _number(
        fields, "other_nonrecoverable_cost_cny_per_ton"
    )
    other_recoverable_vat = _number(
        fields, "other_recoverable_vat_cny_per_ton"
    )
    target_profit = _number(fields, "target_profit_cny_per_ton")
    supplier_quote = _number(
        fields, "supplier_quote_usd_per_ton", optional=True
    )
    quote_basis = fields.get("supplier_quote_basis")
    freight = _number(fields, "ocean_freight_usd_per_ton")
    insurance = _number(fields, "insurance_usd_per_ton")
    other_cif_cost = _number(fields, "other_cif_cost_usd_per_ton")
    quantity = _number(fields, "quantity_tons", optional=True)

    nonnegative_values = {
        "domestic_benchmark_cny_per_ton": benchmark,
        "quality_discount_cny_per_ton": quality_discount,
        "fx_cny_per_usd": fx,
        "quota_cost_cny_per_ton": quota_cost,
        "quota_recoverable_vat_cny_per_ton": quota_recoverable_vat,
        "other_nonrecoverable_cost_cny_per_ton": other_nonrecoverable_cost,
        "other_recoverable_vat_cny_per_ton": other_recoverable_vat,
        "target_profit_cny_per_ton": target_profit,
        "ocean_freight_usd_per_ton": freight,
        "insurance_usd_per_ton": insurance,
        "other_cif_cost_usd_per_ton": other_cif_cost,
    }
    if supplier_quote is not None:
        nonnegative_values["supplier_cif_usd_per_ton"] = supplier_quote
    if any(value < 0 for value in nonnegative_values.values()):
        raise PricingInputError("negative_value")
    if benchmark == 0 or fx == 0:
        raise PricingInputError("invalid_rate_or_price")
    if supplier_quote == 0:
        raise PricingInputError("supplier_quote_usd_per_ton")
    if quote_basis not in {"CIF", "FOB"}:
        raise PricingInputError("supplier_quote_basis")
    if quantity is not None and quantity <= 0:
        raise PricingInputError("quantity_tons")

    benchmark_vat_inclusive = fields.get("benchmark_vat_inclusive")
    if benchmark_vat_inclusive not in {"true", "false"}:
        raise PricingInputError("benchmark_vat_inclusive")
    buyer_bid_vat_inclusive = fields.get("buyer_bid_vat_inclusive")
    if buyer_bid_vat_inclusive not in {"true", "false"}:
        raise PricingInputError("buyer_bid_vat_inclusive")
    discount_basis = fields.get("quality_discount_basis")
    if discount_basis not in {"gross", "net"}:
        raise PricingInputError("quality_discount_basis")
    supplier_cif_quote = (
        supplier_quote + freight + insurance + other_cif_cost
        if supplier_quote is not None and quote_basis == "FOB"
        else supplier_quote
    )

    def net_sale_revenue(benchmark_price, buyer_bid=None):
        if buyer_bid is not None:
            return (
                buyer_bid / (1 + sales_vat_rate)
                if buyer_bid_vat_inclusive == "true"
                else buyer_bid
            )
        benchmark_net = (
            benchmark_price / (1 + sales_vat_rate)
            if benchmark_vat_inclusive == "true"
            else benchmark_price
        )
        discount_net = (
            quality_discount / (1 + sales_vat_rate)
            if discount_basis == "gross"
            else quality_discount
        )
        return benchmark_net - discount_net

    if direct_buyer_bid is not None and direct_buyer_bid <= 0:
        raise PricingInputError("direct_buyer_bid_cny_per_ton")
    scenario_base = direct_buyer_bid if direct_buyer_bid is not None else benchmark
    base_net_sales = net_sale_revenue(benchmark, direct_buyer_bid)
    if base_net_sales <= 0:
        raise PricingInputError("invalid_sale_price")

    import_vat_cost_factor = (
        (1 - recoverable_share)
        * import_vat_rate
        * (1 + duty_rate)
    )
    cif_cost_factor = 1 + duty_rate + import_vat_cost_factor

    def max_cif(net_sales, quota, profit_target):
        return (
            net_sales - quota - other_nonrecoverable_cost - profit_target
        ) / cif_cost_factor

    max_target_cif_cny = max_cif(base_net_sales, quota_cost, target_profit)
    break_even_cif_cny = max_cif(base_net_sales, quota_cost, 0)
    max_target_cif_usd = max_target_cif_cny / fx
    break_even_cif_usd = break_even_cif_cny / fx

    def evaluate_quote(cif_usd, entered_quote=None):
        cif_cny = cif_usd * fx
        customs_duty = cif_cny * duty_rate
        import_vat_paid = (cif_cny + customs_duty) * import_vat_rate
        recoverable_import_vat = import_vat_paid * recoverable_share
        nonrecoverable_import_vat = import_vat_paid - recoverable_import_vat
        economic_cost = (
            cif_cny
            + customs_duty
            + quota_cost
            + other_nonrecoverable_cost
            + nonrecoverable_import_vat
        )
        profit = base_net_sales - economic_cost
        output_vat = base_net_sales * sales_vat_rate
        initial_funding_per_ton = (
            cif_cny
            + customs_duty
            + import_vat_paid
            + quota_cost
            + quota_recoverable_vat
            + other_nonrecoverable_cost
            + other_recoverable_vat
        )
        return {
            "cif_usd_per_ton": cif_usd,
            "cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
                cif_usd
            ),
            "supplier_quote_usd_per_ton": entered_quote,
            "cif_cny_per_ton": cif_cny,
            "customs_duty_cny_per_ton": customs_duty,
            "economic_cost_cny_per_ton": economic_cost,
            "profit_cny_per_ton": profit,
            "profit_for_quantity_cny": (
                profit * quantity if quantity is not None else None
            ),
            "import_vat_paid_cny_per_ton": import_vat_paid,
            "recoverable_import_vat_cny_per_ton": recoverable_import_vat,
            "nonrecoverable_import_vat_cny_per_ton": nonrecoverable_import_vat,
            "gross_sales_receipts_cny_per_ton": base_net_sales
            * (1 + sales_vat_rate),
            "output_vat_cny_per_ton": output_vat,
            "net_vat_payable_cny_per_ton": (
                output_vat
                - recoverable_import_vat
                - quota_recoverable_vat
                - other_recoverable_vat
            ),
            "initial_funding_cny_per_ton": initial_funding_per_ton,
            "initial_funding_for_quantity_cny": (
                initial_funding_per_ton * quantity
                if quantity is not None
                else None
            ),
        }

    supplier_evaluation = (
        evaluate_quote(supplier_cif_quote, supplier_quote)
        if supplier_cif_quote is not None
        else None
    )
    reference_cif_usd = (
        supplier_cif_quote
        if supplier_cif_quote is not None
        else max_target_cif_usd if max_target_cif_usd > 0 else None
    )
    cash_flow_reference = (
        evaluate_quote(reference_cif_usd)
        if reference_cif_usd is not None
        else None
    )

    domestic_scenarios = []
    for delta in (-500, 0, 500):
        scenario_benchmark = scenario_base + delta
        scenario_sales = net_sale_revenue(
            scenario_benchmark,
            (
                scenario_benchmark
                if direct_buyer_bid is not None
                else None
            ),
        )
        scenario_cif_cny = max_cif(
            scenario_sales, quota_cost, target_profit
        )
        domestic_scenarios.append(
            {
                "benchmark_delta_cny_per_ton": delta,
                "benchmark_cny_per_ton": scenario_benchmark,
                "max_target_cif_usd_per_ton": scenario_cif_cny / fx,
                "max_target_cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
                    scenario_cif_cny / fx
                ),
            }
        )

    fx_scenarios = []
    for scenario_fx in dict.fromkeys((6.50, fx, 6.90)):
        fx_scenarios.append(
            {
                "fx_cny_per_usd": scenario_fx,
                "max_target_cif_usd_per_ton": max_target_cif_cny
                / scenario_fx,
                "max_target_cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
                    max_target_cif_cny / scenario_fx
                ),
            }
        )

    quota_scenarios = []
    for scenario_quota in dict.fromkeys((3500, quota_cost, 4800)):
        scenario_cif_cny = max_cif(
            base_net_sales, scenario_quota, target_profit
        )
        quota_scenarios.append(
            {
                "quota_cost_cny_per_ton": scenario_quota,
                "max_target_cif_usd_per_ton": scenario_cif_cny / fx,
                "max_target_cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
                    scenario_cif_cny / fx
                ),
            }
        )

    quote_scenarios = []
    if supplier_quote is not None:
        for delta in (-100, -50, 0, 50, 100):
            scenario_quote = supplier_quote + delta
            if scenario_quote > 0:
                scenario_cif = (
                    scenario_quote + freight + insurance + other_cif_cost
                    if quote_basis == "FOB"
                    else scenario_quote
                )
                quote_scenarios.append(
                    {
                        "supplier_quote_usd_per_ton": scenario_quote,
                        "cif_usd_per_ton": scenario_cif,
                        "cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
                            scenario_cif
                        ),
                        "profit_cny_per_ton": evaluate_quote(
                            scenario_cif, scenario_quote
                        )["profit_cny_per_ton"],
                    }
                )

    return {
        "inputs": {
            "domestic_benchmark_cny_per_ton": benchmark,
            "benchmark_vat_inclusive": benchmark_vat_inclusive == "true",
            "direct_buyer_bid_cny_per_ton": direct_buyer_bid,
            "buyer_bid_vat_inclusive": buyer_bid_vat_inclusive == "true",
            "quality_discount_cny_per_ton": quality_discount,
            "quality_discount_basis": discount_basis,
            "domestic_sales_vat_percent": sales_vat_rate * 100,
            "fx_cny_per_usd": fx,
            "duty_percent": duty_rate * 100,
            "import_vat_percent": import_vat_rate * 100,
            "import_vat_recoverable_percent": recoverable_share * 100,
            "quota_cost_cny_per_ton": quota_cost,
            "quota_recoverable_vat_cny_per_ton": quota_recoverable_vat,
            "other_nonrecoverable_cost_cny_per_ton": other_nonrecoverable_cost,
            "other_recoverable_vat_cny_per_ton": other_recoverable_vat,
            "target_profit_cny_per_ton": target_profit,
            "supplier_quote_usd_per_ton": supplier_quote,
            "supplier_quote_basis": quote_basis,
            "ocean_freight_usd_per_ton": freight,
            "insurance_usd_per_ton": insurance,
            "other_cif_cost_usd_per_ton": other_cif_cost,
            "quantity_tons": quantity,
        },
        "sales": {
            "gross_receipts_cny_per_ton": base_net_sales
            * (1 + sales_vat_rate),
            "net_revenue_cny_per_ton": base_net_sales,
            "output_vat_cny_per_ton": base_net_sales * sales_vat_rate,
        },
        "max_target_cif_cny_per_ton": max_target_cif_cny,
        "max_target_cif_usd_per_ton": max_target_cif_usd,
        "max_target_cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
            max_target_cif_usd
        ),
        "break_even_cif_cny_per_ton": break_even_cif_cny,
        "break_even_cif_usd_per_ton": break_even_cif_usd,
        "break_even_cif_us_cents_per_lb": usd_per_metric_ton_to_us_cents_per_lb(
            break_even_cif_usd
        ),
        "supplier_evaluation": supplier_evaluation,
        "cash_flow_reference": cash_flow_reference,
        "scenarios": {
            "domestic_price": domestic_scenarios,
            "fx": fx_scenarios,
            "quota": quota_scenarios,
            "supplier_quote": quote_scenarios,
        },
    }


def calculate_product_purchase_pricing(fields):
    product = fields.get("product")
    if product not in PRODUCT_VAT_PERCENT:
        raise PricingInputError("product")
    vat_percent = PRODUCT_VAT_PERCENT[product]
    pricing_fields = {
        "direct_buyer_bid_cny_per_ton": "",
        "buyer_bid_vat_inclusive": "true",
        "domestic_benchmark_cny_per_ton": fields.get(
            "domestic_benchmark_cny_per_ton", ""
        ),
        "benchmark_vat_inclusive": "true",
        "quality_discount_cny_per_ton": fields.get(
            "quality_discount_cny_per_ton", "0"
        ),
        "quality_discount_basis": "gross",
        "domestic_sales_vat_percent": str(vat_percent),
        "fx_cny_per_usd": fields.get("fx_cny_per_usd", ""),
        "duty_percent": fields.get("duty_percent", "0"),
        "import_vat_percent": str(vat_percent),
        "import_vat_recoverable_percent": fields.get(
            "import_vat_recoverable_percent", "100"
        ),
        "quota_cost_cny_per_ton": "0",
        "quota_recoverable_vat_cny_per_ton": "0",
        "other_nonrecoverable_cost_cny_per_ton": fields.get(
            "other_nonrecoverable_cost_cny_per_ton", "0"
        ),
        "other_recoverable_vat_cny_per_ton": fields.get(
            "other_recoverable_vat_cny_per_ton", "0"
        ),
        "target_profit_cny_per_ton": fields.get(
            "target_profit_cny_per_ton", "0"
        ),
        "supplier_quote_usd_per_ton": fields.get(
            "supplier_quote_usd_per_ton", ""
        ),
        "supplier_quote_basis": fields.get("supplier_quote_basis", "CIF"),
        "ocean_freight_usd_per_ton": fields.get(
            "ocean_freight_usd_per_ton", "0"
        ),
        "insurance_usd_per_ton": fields.get("insurance_usd_per_ton", "0"),
        "other_cif_cost_usd_per_ton": fields.get(
            "other_cif_cost_usd_per_ton", "0"
        ),
        "quantity_tons": fields.get("quantity_tons", ""),
    }
    result = calculate_sudan_cotton_pricing(pricing_fields)
    result["inputs"]["product"] = product
    result["scenarios"]["quota"] = []
    return result
