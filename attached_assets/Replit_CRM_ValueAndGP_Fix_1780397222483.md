# EP SmartGrowth — Fix All Sales Value Figures to Use Net (Excl VAT)

## Root cause

Throughout the codebase, sales **analytics** screens use `total_grand` (gross total
**including VAT**) instead of `total_sub` (subtotal **excluding VAT**). This inflates
every customer sales figure shown on the CRM and cockpit screens by the VAT amount.

> **Note:** `total_grand` is intentionally correct in financial/collection contexts
> (driver cash collection, route reconciliation, receipts, open orders) — those amounts
> include VAT because that is what the customer pays. **Do not change those.** Only
> the sales analytics fields listed in this document need fixing.

**Verified from data:**

**Verified from data:**
| Source | Sales (excl VAT) |
|---|---|
| Power report (source of truth) | €138,617 |
| DW `total_sub` | €137,074 — difference = 17 invoices not yet synced |
| DW `total_grand` (current) | €150,252 — **inflated by ~€13k of VAT** |

The `total_net` column on `DwInvoiceHeader` is NULL for all rows — do not use it.
`total_sub` is the correct excl-VAT field.

---

## CHANGE 1 — Main dashboard `sales_sq` (used for Value 6m, Value 4w, per-customer rows, KPI strip)

**File:** `routes_crm_dashboard.py`

Find the first `sales_sq` subquery (inside the main dashboard function). It contains
these two lines using `total_grand`:

```python
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d6m, DwInvoiceHeader.total_grand), else_=0)
            ), 0).label("value_6m"),
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d4w, DwInvoiceHeader.total_grand), else_=0)
            ), 0).label("value_4w"),
```

**Replace both `total_grand` with `total_sub`:**

```python
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d6m, DwInvoiceHeader.total_sub), else_=0)
            ), 0).label("value_6m"),
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d4w, DwInvoiceHeader.total_sub), else_=0)
            ), 0).label("value_4w"),
```

---

## CHANGE 2 — `review_ordering` function `sales_sq`

**File:** `routes_crm_dashboard.py`

Find the second `sales_sq` subquery (inside the `review_ordering` function). It has:

```python
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d4w, DwInvoiceHeader.total_grand), else_=0)
            ), 0).label("value_4w"),
```

**Replace `total_grand` with `total_sub`:**

```python
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d4w, DwInvoiceHeader.total_sub), else_=0)
            ), 0).label("value_4w"),
```

---

## CHANGE 3 — GP% query must use `total_sub` as denominator

The GP% KPI and per-customer GP% column were added in `Replit_CRM_GP_Percent_4w.md`.
The `gp_sq` subquery uses `dw_invoice_line.line_net_value` as the revenue denominator —
but `line_net_value` is NULL for all rows. The correct line-level revenue field is
`line_total_excl` (which is the line equivalent of `total_sub` at the header level).

Find the `gp_sq` subquery. Wherever it references `dw_invoice_line.line_net_value`,
replace with `dw_invoice_line.line_total_excl`.

Specifically, in the `case` conditions used to sum revenue for the GP% denominator:

**Find** (any variant that references `line_net_value`):**

```python
                    text("dw_invoice_line.line_net_value > 0")
```
and
```python
                    text("dw_invoice_line.line_net_value")
```

**Replace with:**

```python
                    text("dw_invoice_line.line_total_excl > 0")
```
and
```python
                    text("dw_invoice_line.line_total_excl")
```

> After this fix the GP% denominator will use the same excl-VAT revenue base as
> Value 4w, making the two metrics directly comparable.

---

## Expected results after applying

| Metric | Before (wrong) | After (correct) |
|---|---|---|
| Value 4w KPI | ~€150k (incl VAT) | ~€138k (excl VAT, matching system) |
| Value 6m | inflated by VAT | correct excl-VAT figure |
| GP% 4w KPI | denominator wrong (NULL field) | correct — ~16.8% matching system |
| Per-customer GP% | denominator wrong | correct |

The small remaining gap between Value 4w and the system power report (~€1,500) is
17 invoices not yet synced to the DW — this will close automatically once the
next daily cron runs.

---

---

## CHANGE 4 — Cockpit dormant customers panel

**File:** `services/cockpit_data.py`

The dormant customers panel calculates `recent_invoice_value` using `invoices.total_grand`.
Find this SQL query (around line 1589):

```sql
            SUM(i.total_grand)  AS recent_invoice_value,
```

**Replace with:**

```sql
            SUM(i.total_sub)  AS recent_invoice_value,
```

The `invoices` table has a `total_sub` column (confirmed in models.py) — it is the
net excl-VAT equivalent on the operational invoice table.

---

## Summary — all changes

| # | File | Change |
|---|---|---|
| 1 | `routes_crm_dashboard.py` lines 168, 171 | `total_grand` → `total_sub` for Value 6m and Value 4w in main dashboard `sales_sq` |
| 2 | `routes_crm_dashboard.py` line 698 | `total_grand` → `total_sub` for Value 4w in `review_ordering` `sales_sq` |
| 3 | `routes_crm_dashboard.py` — `gp_sq` | `line_net_value` → `line_total_excl` as GP% revenue denominator |
| 4 | `services/cockpit_data.py` line 1589 | `total_grand` → `total_sub` for dormant customers panel |

All other uses of `total_grand` in the codebase are correct (financial collection amounts) and must not be changed.
