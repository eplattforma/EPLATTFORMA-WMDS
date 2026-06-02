# EP SmartGrowth — Fix GP% to Use Net-After-Discount Revenue

## Root cause

`_build_cost_fields` in `datawarehouse_sync.py` calculates `gross_profit` and
`gross_margin_pct` using `line_total_excl` (pre-discount line value) as the revenue
figure. Discounts are not subtracted. This means:

- A line with `line_total_excl=45.00` and `line_total_discount=15.00` has actual
  net revenue of **€30.00**, but the DW stores it as if revenue were €45.00
- GP is overstated by the discount amount on every discounted line
- GP% is wrong on any customer who receives discounts

**Verified with customer 77701369 (PERIPTERO ELENA):**

| | System (correct) | DW (wrong) |
|---|---|---|
| Revenue | €296.79 (after €32.62 discount) | €329.41 (pre-discount) |
| Cost | €260.87 | €263.17 |
| GP | €35.92 | €66.24 |
| **GP%** | **12.1%** | **20.1%** |

The correct formula is:
```
net_revenue     = line_total_excl - line_total_discount
gross_profit    = net_revenue - line_cost_total
gross_margin_pct = gross_profit / net_revenue  (when net_revenue > 0)
```

`line_total_discount` is already extracted in both sync call sites — it just isn't
being passed to `_build_cost_fields`.

---

## CHANGE 1 — `datawarehouse_sync.py` — fix `_build_cost_fields` signature

Find the function definition:

```python
def _build_cost_fields(invoice_date, item_code, quantity, line_total_excl, item_cost_map):
```

**Replace with** (add `line_total_discount=0` parameter):

```python
def _build_cost_fields(invoice_date, item_code, quantity, line_total_excl, item_cost_map, line_total_discount=0):
```

Then inside the function, find:

```python
    qty = float(quantity or 0)
    revenue_excl = float(line_total_excl or 0)

    line_cost_total = qty * unit_cost
    gross_profit = revenue_excl - line_cost_total

    gross_margin_pct = None
    if revenue_excl not in (None, 0):
        gross_margin_pct = gross_profit / revenue_excl
```

**Replace with:**

```python
    qty      = float(quantity or 0)
    pre_disc = float(line_total_excl or 0)
    discount = float(line_total_discount or 0)
    net_revenue = pre_disc - discount   # revenue after discount, excl VAT

    line_cost_total = qty * unit_cost
    gross_profit    = net_revenue - line_cost_total

    gross_margin_pct = None
    if net_revenue not in (None, 0):
        gross_margin_pct = gross_profit / net_revenue
```

---

## CHANGE 2 — `datawarehouse_sync.py` — pass discount at both call sites

There are **two** places where `_build_cost_fields` is called. Both already have
`line_total_discount` extracted — it just isn't being passed. Update both.

### Call site 1 (around line 1555)

Find:

```python
                    cost_fields = _build_cost_fields(
                        invoice_date=invoice_date,
                        item_code=line.get("item_code_365"),
                        quantity=quantity,
                        line_total_excl=line_total_excl,
                        item_cost_map=item_cost_map,
                    )
```

**Replace with:**

```python
                    cost_fields = _build_cost_fields(
                        invoice_date=invoice_date,
                        item_code=line.get("item_code_365"),
                        quantity=quantity,
                        line_total_excl=line_total_excl,
                        item_cost_map=item_cost_map,
                        line_total_discount=line_total_discount,
                    )
```

### Call site 2 (around line 2102)

Find:

```python
                        cost_fields = _build_cost_fields(
                            invoice_date=inv_date_val,
                            item_code=line.get("item_code_365"),
                            quantity=quantity,
                            line_total_excl=line_total_excl,
                            item_cost_map=item_cost_map,
                        )
```

**Replace with:**

```python
                        cost_fields = _build_cost_fields(
                            invoice_date=inv_date_val,
                            item_code=line.get("item_code_365"),
                            quantity=quantity,
                            line_total_excl=line_total_excl,
                            item_cost_map=item_cost_map,
                            line_total_discount=line_total_discount,
                        )
```

---

## CHANGE 3 — `routes_crm_dashboard.py` — fix GP% query denominator

The `gp_sq` subquery uses `dw_invoice_line.line_total_excl` as the revenue denominator.
This must be changed to `line_total_excl - COALESCE(line_total_discount, 0)` everywhere
in the GP% subquery.

Find all references to `line_net_value` or `line_total_excl` used as the revenue
denominator/filter inside the `gp_sq` subquery. Replace:

```python
text("dw_invoice_line.line_total_excl > 0")
```

**with:**

```python
text("(dw_invoice_line.line_total_excl - COALESCE(dw_invoice_line.line_total_discount, 0)) > 0")
```

And replace the revenue sum:

```python
text("dw_invoice_line.line_total_excl")
```

**with:**

```python
text("(dw_invoice_line.line_total_excl - COALESCE(dw_invoice_line.line_total_discount, 0))")
```

---

## CHANGE 4 — Backfill existing rows in `dw_invoice_line`

All lines synced since 2026-01-01 that have a discount were stored with the wrong
`gross_profit` and `gross_margin_pct`. Run this SQL once after deploying the above
changes to correct the existing data:

```sql
UPDATE dw_invoice_line
SET
    gross_profit = (line_total_excl - COALESCE(line_total_discount, 0)) - line_cost_total,
    gross_margin_pct = CASE
        WHEN (line_total_excl - COALESCE(line_total_discount, 0)) <> 0
        THEN ((line_total_excl - COALESCE(line_total_discount, 0)) - line_cost_total)
             / (line_total_excl - COALESCE(line_total_discount, 0))
        ELSE NULL
    END
WHERE
    line_cost_total IS NOT NULL
    AND line_total_discount IS NOT NULL
    AND line_total_discount <> 0;
```

> This only touches lines that (a) have cost data and (b) have a non-zero discount.
> Lines with no discount are unaffected — their existing values are already correct.

After running, verify with customer 77701369:
```sql
SELECT invoice_no_365, item_code_365, line_total_excl, line_total_discount,
       line_cost_total, gross_profit, gross_margin_pct
FROM dw_invoice_line
WHERE invoice_no_365 IN ('IN10055306', 'IN10055030')
ORDER BY item_code_365;
```

Expected: `gross_profit` total ≈ 35.92, blended `gross_margin_pct` ≈ 12.1%

---

## Summary

| # | File | Change |
|---|---|---|
| 1 | `datawarehouse_sync.py` | Add `line_total_discount` param to `_build_cost_fields`; use net-after-discount as revenue |
| 2 | `datawarehouse_sync.py` | Pass `line_total_discount` at both call sites |
| 3 | `routes_crm_dashboard.py` | GP% query denominator uses net-after-discount |
| 4 | Database | One-off UPDATE to backfill all discounted lines since 2026-01-01 |

Going forward, all new invoice lines synced by the daily cron will store the correct
discount-adjusted `gross_profit` and `gross_margin_pct` automatically.
