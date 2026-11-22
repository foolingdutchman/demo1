---
name: sudan-cotton-pricing
description: Build and update a dynamic CIF Qingdao import-pricing, landed-cost, cash-flow, and profit model for Sudanese cotton. Use when evaluating supplier offers, negotiating CIF/FOB prices, or stress-testing domestic cotton prices, exchange rates, quota costs, taxes, quality discounts, freight, and target margins.
---

# Sudan Cotton CIF Qingdao Pricing Skill

## Purpose

Create a practical, parameter-driven pricing model for Sudanese cotton imported into China and sold domestically. The model must distinguish:

1. **Economic profit** (normally calculated excluding recoverable VAT).
2. **Cash required / cash-flow timing** (including VAT paid at import and later credit/refund).
3. **Supplier quotation** (CIF Qingdao by default; optionally derive FOB when freight and insurance are known).

Never treat assumptions as verified market facts. Clearly label user-provided values, externally sourced market observations, and model assumptions.

## Default case profile (editable)

Use these only as starting defaults. Ask for missing material inputs or show clearly labeled scenarios rather than inventing values.

| Parameter | Default |
|---|---:|
| Origin / product | Sudanese cotton |
| Quality | Staple 26–27 mm; strength 26–27 g/tex |
| Quotation basis | CIF Qingdao |
| Domestic benchmark | CC Index 2227B, if user confirms it is a reasonable comparable |
| Domestic benchmark price | 15,360 CNY/tonne, historical illustrative value; refresh before live decisions |
| Domestic sales VAT rate | 9%, user-confirmed for the current case |
| Import VAT rate | 9%, user-confirmed for the current case |
| Import VAT recoverability | 100%, user-confirmed for the current case |
| Customs duty | 0% only as a user-provided case assumption; verify against the actual quota, HS code, declaration and current tariff |
| Cotton quota acquisition/use cost | 4,200 CNY/tonne, user-provided; paid per imported tonne |
| Quality discount | 500 CNY/tonne, provisional assumption; validate against HVI and mill bids |
| Other nonrecoverable costs | 500 CNY/tonne, provisional assumption (e.g. port/clearance 200 + financing/other 300) |
| Target profit | 300 CNY/tonne |
| USD/CNY | 6.70, user-provided indicative rate; replace with actual settlement/hedge rate |
| Quantity | Ask user; if absent calculate per tonne only |

## Required input parameters

Collect or expose the following dynamic inputs:

- Domestic benchmark price, in CNY/tonne, and whether it is VAT-inclusive.
- Domestic sales price adjustment for the Sudan cotton quality, in CNY/tonne, and whether the adjustment is VAT-inclusive or VAT-exclusive.
- Domestic sales VAT rate.
- Supplier quote and quote currency; quote basis (CIF Qingdao, FOB, etc.).
- USD/CNY exchange rate (or applicable currency pair).
- Import duty rate actually applicable to the shipment.
- Import VAT rate and recoverable proportion (0–100%).
- Quota cost per imported tonne and whether any VAT on that quota cost is recoverable.
- Other costs per tonne, split into recoverable VAT and nonrecoverable cost where possible.
- Target profit per tonne.
- Actual/expected freight and insurance if deriving FOB from CIF.
- Quantity and financing days/rate if calculating total funding and financing cost.
- HVI attributes and buyer/mill price feedback for quality adjustment.

If user gives only a few values, calculate a transparent provisional model and identify the missing variables. Ask only for the most important missing input when necessary.

## Core calculation rules

### 1. Normalize domestic sales price

If the benchmark is VAT-inclusive:

`Domestic gross sale price = benchmark gross price - quality discount (gross-price basis)`

`Net sales revenue = Domestic gross sale price / (1 + domestic sales VAT rate)`

If the quality discount is explicitly VAT-exclusive, subtract it after converting the benchmark to net:

`Net sales revenue = benchmark gross price / (1 + sales VAT rate) - quality discount (net basis)`

Do not mix gross and net amounts.

If a direct, actual buyer bid for the Sudan cotton is provided, prefer it over a generic benchmark-minus-discount estimate.

### 2. CIF landed economic cost

Let:
- `CIF_USD` = supplier CIF price in USD/tonne
- `FX` = CNY per USD
- `DutyRate` = actual applicable ad valorem customs duty
- `QuotaCost` = quota acquisition/use cost in CNY/tonne
- `OtherNonrecoverableCosts` = other costs excluding recoverable VAT

Then:

`CIF_CNY = CIF_USD * FX`

`CustomsDuty = CIF_CNY * DutyRate` (simplified; confirm customs valuation adjustments and actual taxable base)

`EconomicImportVAT = (CIF_CNY + CustomsDuty) * ImportVATRate`

Recoverable import VAT is a tax credit, not an economic cost. The nonrecoverable portion is:

`NonrecoverableImportVAT = EconomicImportVAT * (1 - RecoverableShare)`

`TotalEconomicCost = CIF_CNY + CustomsDuty + QuotaCost + OtherNonrecoverableCosts + NonrecoverableImportVAT`

If a quota fee itself has recoverable VAT, model the fee net of its recoverable VAT and separately show its VAT cash flow. Do not assume a quota-fee invoice or input VAT credit exists; ask or label the assumption.

### 3. Profit per tonne

`ProfitPerTonne = NetSalesRevenue - TotalEconomicCost`

`TargetCIF_CNY = NetSalesRevenue - CustomsDutyAtTarget - QuotaCost - OtherNonrecoverableCosts - NonrecoverableImportVATAtTarget - TargetProfit`

For an ad valorem duty and recoverable import VAT, the maximum CIF can be solved algebraically. If all import VAT is recoverable and duty is a simple percentage:

`MaxCIF_CNY = (NetSalesRevenue - QuotaCost - OtherNonrecoverableCosts - TargetProfit) / (1 + DutyRate)`

`MaxCIF_USD = MaxCIF_CNY / FX`

If some import VAT is nonrecoverable, include its nonrecoverable share in the coefficient:

`MaxCIF_CNY = (NetSalesRevenue - QuotaCost - OtherNonrecoverableCosts - TargetProfit) / (1 + DutyRate + (1 - RecoverableShare) * ImportVATRate * (1 + DutyRate))`

This formula assumes import VAT taxable base is CIF plus customs duty and excludes any other customs valuation additions.

### 4. Import VAT and cash-flow view

Show separately:

`ImportVATCashPaid = (CIF_CNY + CustomsDuty) * ImportVATRate`

`RecoverableInputVAT = ImportVATCashPaid * RecoverableShare`

`NonrecoverableVAT = ImportVATCashPaid - RecoverableInputVAT`

Do not subtract recoverable import VAT from economic profit. Include it in initial funding needs and, if relevant, calculate financing cost for the period until credit is usable.

For domestic sales, show:
- Gross sales receipts.
- Output VAT = gross sales price × sales VAT rate / (1 + sales VAT rate).
- Net sales revenue.
- Net VAT payable/credit after eligible input VAT, while noting other input VAT and tax timing may affect the actual tax return.

### 5. Quote conversion

For CIF quotes:

`CIF_USD = CIF_CNY / FX`

If supplier quote is FOB:

`CIF_USD = FOB_USD + ocean_freight_USD + insurance_USD + other_costs_to_CIF_USD`

Conversely:

`FOB_USD = CIF_USD - ocean_freight_USD - insurance_USD - other_CIF_costs_USD`

Do not deduct freight a second time from a CIF quote.

### 6. ICE reference linkage (optional cross-check only)

ICE cotton is a market reference, not an automatic Sudan cotton price formula.

`ICE_USD_per_tonne = ICE_cents_per_lb * 22.0462`

A 1 cent/lb ICE move corresponds to `22.0462 USD/tonne` before basis changes.

Approximate CNY import-cost sensitivity per 1 cent/lb:

`22.0462 * FX * (1 + DutyRate)`

Only use this as a scenario sensitivity, not as a forecast. Sudan basis, quality, origin, shipment timing and buyer demand can move independently.

## Scenario analysis

At minimum, show:
1. Supplier CIF quote scenarios (e.g. quote ±50/100 USD per tonne).
2. Domestic selling price scenarios (e.g. -500, base, +500 CNY/tonne).
3. FX scenarios (e.g. 6.50, current user rate, 6.90; adapt to current context).
4. Quota cost scenarios (e.g. 3,500 / 4,200 / 4,800 CNY/tonne).
5. Target profit and break-even / maximum CIF price.

Use tables with:
- CIF USD/tonne
- CIF CNY/tonne
- quota cost
- duty
- nonrecoverable costs
- import VAT paid / recoverable
- net sales revenue
- profit per tonne
- profit for total quantity

Clearly distinguish the **maximum CIF price for target profit** from the **break-even CIF price**.

## Quality adjustment guidance

Do not automatically equate 26–27 mm / 26–27 g/tex Sudan cotton with CC Index 2227B. The index may be only a provisional benchmark.

Request or analyze HVI data:
- staple length and length uniformity
- strength
- micronaire
- colour grade
- trash / leaf grade
- moisture
- short fibre index, if available

Use actual mill bids for the specific lot whenever possible. State that a flat quality discount (e.g. 500 CNY/tonne) is an assumption until validated. Avoid double-counting quality discounts if the buyer's actual bid already reflects quality.

## Output format

Always provide:

1. **Input assumptions** — distinguish user-provided from provisional.
2. **Tax treatment** — explain VAT-inclusive vs VAT-exclusive calculations.
3. **Core formulas** — enough detail for auditability.
4. **Current result** — max CIF USD/tonne and CNY/tonne for target profit, plus break-even price.
5. **Supplier quote evaluation** — profit at the actual offer, if known.
6. **Scenario tables** — domestic price, FX, quota and supplier quote sensitivity.
7. **Cash-flow view** — import VAT paid and recoverable; funding requirement.
8. **Risks / checks** — actual tariff, quota eligibility, VAT documentation, HS code, customs valuation, HVI and buyer bid.
9. If key values are assumptions, say so prominently and do not present the result as a verified market quote.

## Important safeguards

- The user’s statement that the quota import is duty-free is a case assumption, not a general legal conclusion. Verify actual tariff treatment for the shipment before commitment.
- Do not call the 4,200 CNY/tonne quota cost a customs duty. It is a separate quota acquisition/use cost as described by the user.
- Recoverable import VAT is not a permanent cost, but it is a cash outflow and may create financing cost.
- Do not use domestic VAT-inclusive sale price directly against CIF cost without normalizing VAT.
- Do not add import VAT to cost if fully recoverable and then also claim input VAT credit.
- If quota cost or other expenses have VAT invoices, model their recoverable VAT separately to avoid overstating cost.
- Prices dated in the past must be labeled historical; obtain fresh prices before making current purchasing decisions.
