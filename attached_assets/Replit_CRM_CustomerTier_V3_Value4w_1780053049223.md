# EP SmartGrowth CRM — Add Total Value 4w to KPI Strip

Adds a **Total Value 4w** card to the KPI summary strip showing the sum of the last 4 weeks of sales across all currently filtered customers. Updates in real time as any filter (Agent, Tier, Classification, District etc.) is applied.

Apply on top of the previous V1 and V2 tier instructions.

---

## CHANGE 1 — `routes_crm_dashboard.py`

### Step 1a — Add value_4w sum to the kpi_row query

Find the `kpi_row` query. It currently looks like this:

```python
    kpi_row = (
        db.session.query(
            func.count(
                case((CrmAbandonedCartState.has_abandoned_cart.is_(True), 1), else_=None)
            ).label("cart_count"),
            func.coalesce(func.sum(CRMCustomerOpenOrders.open_order_amount), 0).label("total_open_amount"),
        )
        .select_from(filtered_codes_sq)
        .outerjoin(
            CrmAbandonedCartState,
            CrmAbandonedCartState.customer_code_365 == filtered_codes_sq.c.customer_code_365
        )
        .outerjoin(
            CRMCustomerOpenOrders,
            CRMCustomerOpenOrders.customer_code_365 == filtered_codes_sq.c.customer_code_365
        )
        .one()
    )
```

**Replace with** (adds the sales_sq join and value_4w sum):

```python
    kpi_row = (
        db.session.query(
            func.count(
                case((CrmAbandonedCartState.has_abandoned_cart.is_(True), 1), else_=None)
            ).label("cart_count"),
            func.coalesce(func.sum(CRMCustomerOpenOrders.open_order_amount), 0).label("total_open_amount"),
            func.coalesce(func.sum(sales_sq.c.value_4w), 0).label("total_value_4w"),
        )
        .select_from(filtered_codes_sq)
        .outerjoin(
            CrmAbandonedCartState,
            CrmAbandonedCartState.customer_code_365 == filtered_codes_sq.c.customer_code_365
        )
        .outerjoin(
            CRMCustomerOpenOrders,
            CRMCustomerOpenOrders.customer_code_365 == filtered_codes_sq.c.customer_code_365
        )
        .outerjoin(
            sales_sq,
            sales_sq.c.cc == filtered_codes_sq.c.customer_code_365
        )
        .one()
    )
```

> `sales_sq` is the subquery already defined earlier in the same function — it contains `value_4w` per customer. This join is safe because `sales_sq` uses a LEFT approach (customers with no sales simply contribute 0 to the sum).

### Step 1b — Pass the new value to the template

Find the `return render_template(...)` call. It currently passes `kpi_total_open_amount`. Add alongside it:

```python
        kpi_total_value_4w=float(kpi_row.total_value_4w or 0),
```

---

## CHANGE 2 — `templates/crm/dashboard.html`

### Add the Value 4w card to the KPI strip

Find the KPI cards row. It contains cards like Total, Need Action, Has Cart, On Orders, With Offers, Avg Usage, Offer Sales 4w, High Dependency.

Add a **Value 4w** card — place it after "On Orders" or at the end of the strip, wherever fits the layout:

```html
<div class="card px-3 py-2 border-primary">
  <div class="small text-muted">Value 4w</div>
  <div class="fw-bold fs-5 text-primary">
    €{{ "{:,.0f}".format(kpi_total_value_4w) }}
  </div>
</div>
```

> The `"{:,.0f}".format(...)` produces comma-separated thousands (e.g. €14,229) without decimal places.

---

## What it shows

The **Value 4w** card in the KPI strip always reflects the sum of 4-week sales for the customers currently visible — it updates as filters change.

**Examples using the MANOLIS portfolio (72 customers):**

| Filter applied | What Value 4w shows |
|---|---|
| No filter (all 72) | Total 4-week sales across all MANOLIS customers |
| Tier = Champion | 4-week sales from the 13 Champion customers only |
| Tier = At Risk | 4-week sales at risk of being lost (from the 4 At Risk customers) |
| Classification = CUSTOMER | 4-week sales from active customers only |

This makes the KPI strip fully contextual — a manager filtering to At Risk customers immediately sees the value of business at risk, not the total portfolio.

---

## No changes needed to the tier service or model

This is purely a query and template change. The `sales_sq` subquery (which already calculates `value_4w` per customer) is reused — no new data is fetched.
