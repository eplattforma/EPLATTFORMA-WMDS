# EP SmartGrowth CRM — Add GP% 4w KPI Card + Per-Customer GP% Column

Adds a **GP% 4w** card to the KPI summary strip showing the blended gross profit margin for the last 4 weeks across all currently filtered customers. Also adds a **GP%** value to each customer row in the table.

Apply on top of existing CRM dashboard changes (V1, V2, V3).

---

## Data model

| Table | Key columns used |
|---|---|
| `dw_invoice_header` | `invoice_no_365`, `customer_code_365`, `invoice_date_utc0` |
| `dw_invoice_line` | `invoice_no_365`, `line_net_value`, `gross_profit`, `gross_margin_pct` |

GP% is calculated as:
```
GP% = SUM(gross_profit) / SUM(line_net_value) * 100
```

Only rows where both `gross_profit` IS NOT NULL and `line_net_value` > 0 are included (rows without a cost snapshot are excluded so they don't distort the margin).

4 weeks = last 28 days (use `CURRENT_DATE - INTERVAL '28 days'`).

---

## CHANGE 1 — `routes_crm_dashboard.py`

### Step 1a — Add a `gp_sq` subquery for GP% per customer over 4w

In the same function that builds `sales_sq`, add a new subquery **below** it:

```python
from sqlalchemy import func, case, text, and_
from datetime import date, timedelta

cutoff_4w = date.today() - timedelta(days=28)

gp_sq = (
    db.session.query(
        text("dw_invoice_header.customer_code_365").label("cc"),
        func.coalesce(func.sum(
            case(
                (
                    and_(
                        text("dw_invoice_line.gross_profit IS NOT NULL"),
                        text("dw_invoice_line.line_net_value > 0")
                    ),
                    text("dw_invoice_line.gross_profit")
                ),
                else_=None
            )
        ), 0).label("gp_sum_4w"),
        func.coalesce(func.sum(
            case(
                (
                    and_(
                        text("dw_invoice_line.gross_profit IS NOT NULL"),
                        text("dw_invoice_line.line_net_value > 0")
                    ),
                    text("dw_invoice_line.line_net_value")
                ),
                else_=None
            )
        ), 0).label("revenue_sum_4w"),
    )
    .select_from(text("dw_invoice_header"))
    .join(text("dw_invoice_line"), text("dw_invoice_line.invoice_no_365 = dw_invoice_header.invoice_no_365"))
    .filter(text("dw_invoice_header.invoice_date_utc0 >= :cutoff").bindparams(cutoff=cutoff_4w.isoformat()))
    .group_by(text("dw_invoice_header.customer_code_365"))
    .subquery()
)
```

> **Note:** If the project already uses SQLAlchemy ORM models for `dw_invoice_header` and `dw_invoice_line`, use those model classes instead of `text("dw_invoice_header")` etc. The logic is the same — join the two tables on `invoice_no_365`, filter by date, group by customer, sum `gross_profit` and `line_net_value`.

---

### Step 1b — Join `gp_sq` into the per-customer rows query

Find the main query that fetches the per-customer rows (the one that already joins `sales_sq` per customer). Add an additional outer join to `gp_sq`:

```python
.outerjoin(
    gp_sq,
    gp_sq.c.cc == filtered_codes_sq.c.customer_code_365
)
```

Then add these two columns to the `.query(...)` select list:

```python
func.coalesce(gp_sq.c.gp_sum_4w, 0).label("gp_sum_4w"),
func.coalesce(gp_sq.c.revenue_sum_4w, 0).label("revenue_sum_4w"),
```

---

### Step 1c — Calculate GP% per customer and pass to template

After fetching the per-customer rows (in the loop where you build the customer list for the template), calculate GP% per customer:

```python
for row in customer_rows:
    rev = float(row.revenue_sum_4w or 0)
    gp  = float(row.gp_sum_4w or 0)
    row_dict["gp_pct_4w"] = round((gp / rev * 100), 1) if rev > 0 else None
    # ... rest of row dict building
```

---

### Step 1d — Add GP% to the KPI strip query

Find the `kpi_row` query (the one that already joins `sales_sq` to get `total_value_4w`). Add `gp_sq` as an additional join and pull the aggregate sums:

```python
kpi_row = (
    db.session.query(
        # ... existing columns ...
        func.coalesce(func.sum(gp_sq.c.gp_sum_4w), 0).label("kpi_gp_sum"),
        func.coalesce(func.sum(gp_sq.c.revenue_sum_4w), 0).label("kpi_rev_sum"),
    )
    .select_from(filtered_codes_sq)
    # ... existing outerjoin calls ...
    .outerjoin(
        gp_sq,
        gp_sq.c.cc == filtered_codes_sq.c.customer_code_365
    )
    .one()
)
```

Then calculate the blended KPI GP% after fetching:

```python
kpi_rev = float(kpi_row.kpi_rev_sum or 0)
kpi_gp  = float(kpi_row.kpi_gp_sum or 0)
kpi_gp_pct_4w = round((kpi_gp / kpi_rev * 100), 1) if kpi_rev > 0 else None
```

---

### Step 1e — Pass values to the template

In the `return render_template(...)` call, add:

```python
kpi_gp_pct_4w=kpi_gp_pct_4w,
customers=customers,   # already passed — each item now has gp_pct_4w
```

---

## CHANGE 2 — `templates/crm/dashboard.html`

### Step 2a — Add the GP% 4w KPI card

In the KPI strip (where the existing cards like Total, Has Cart, On Orders, Value 4w etc. are), add a new card. Place it after the **Value 4w** card:

```html
<div class="card px-3 py-2 border-success">
  <div class="small text-muted">GP% 4w</div>
  <div class="fw-bold fs-5 text-success">
    {% if kpi_gp_pct_4w is not none %}
      {{ kpi_gp_pct_4w }}%
    {% else %}
      —
    {% endif %}
  </div>
</div>
```

---

### Step 2b — Add GP% column header to the customer table

Find the `<thead>` row of the customer table. After the **Value 4w** column header, add:

```html
<th class="text-end">GP% 4w</th>
```

---

### Step 2c — Add GP% cell to each customer row

In the `{% for customer in customers %}` loop, after the **Value 4w** cell, add:

```html
<td class="text-end">
  {% if customer.gp_pct_4w is not none %}
    <span class="{% if customer.gp_pct_4w >= 20 %}text-success{% elif customer.gp_pct_4w >= 10 %}text-warning{% else %}text-danger{% endif %}">
      {{ customer.gp_pct_4w }}%
    </span>
  {% else %}
    <span class="text-muted">—</span>
  {% endif %}
</td>
```

> Colour coding: ≥ 20% = green (healthy margin), 10–19% = amber (watch), < 10% = red (low margin). Adjust thresholds if needed.

---

## Behaviour summary

- **KPI card (GP% 4w):** Shows the blended gross margin % across all filtered customers for the last 28 days. Updates as filters (Agent, Tier, Classification, District etc.) are applied. Shows `—` if no cost data exists yet for the filtered set.
- **Per-customer GP% column:** Shows each customer's individual gross margin % for the last 28 days. Colour-coded green/amber/red. Shows `—` if that customer has no lines with cost data in the period.
- **Null safety:** Lines without a cost snapshot (`gross_profit IS NULL`) are excluded from both numerator and denominator, so missing cost data does not distort the margin — it simply reduces the coverage. A future note could be added to flag customers with partial coverage.

---

## No schema changes required

All data is read from `dw_invoice_header` and `dw_invoice_line` which already store `gross_profit`, `gross_margin_pct`, and `line_net_value` per line. The cron that updates `ps_items_dw.cost_price` and populates these columns does not need to change.
