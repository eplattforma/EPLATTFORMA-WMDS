# Supplier Returns — V11 Print Slip: Barcode + Supplier Item Code

Fixes two missing fields on the acknowledgement slip:
- **Barcode** — currently shows "–" for every item
- **Supplier Item Code** — the code the supplier uses for each item (same field the forecasting order email uses)

Both come from `DwItem` — the same model used by `get_item_master_for_codes()` in `services/replenishment_mvp/repositories.py`.

---

## CHANGE 1 — Update the print route in `blueprints/supplier_returns.py`

Find the existing `print_slip` route:

```python
@supplier_returns_bp.route("/print/<supplier_code>")
def print_slip(supplier_code):
    """Print-friendly acknowledgement slip for a single supplier."""
    result = get_returns_stock(force_refresh=False)
    groups = result.get("groups", [])

    group = next(
        (g for g in groups if g["supplier_code_365"] == supplier_code),
        None
    )
    if group is None:
        abort(404)

    return render_template(
        "supplier_returns/print_slip.html",
        group=group,
        print_date=datetime.utcnow().strftime("%d/%m/%Y %H:%M")
    )
```

Replace with:

```python
@supplier_returns_bp.route("/print/<supplier_code>")
def print_slip(supplier_code):
    """Print-friendly acknowledgement slip for a single supplier."""
    from models import DwItem

    result = get_returns_stock(force_refresh=False)
    groups = result.get("groups", [])

    group = next(
        (g for g in groups if g["supplier_code_365"] == supplier_code),
        None
    )
    if group is None:
        abort(404)

    # Enrich each item row with barcode and supplier_item_code from DwItem
    # (same source used by the forecasting order email)
    item_codes = [item["item_code_365"] for item in group.get("item_rows", [])]
    if item_codes:
        dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        dw_map = {d.item_code_365: d for d in dw_items}
    else:
        dw_map = {}

    for item in group.get("item_rows", []):
        dw = dw_map.get(item["item_code_365"])
        item["barcode"]            = (dw.barcode            or "") if dw else ""
        item["supplier_item_code"] = (dw.supplier_item_code or "") if dw else ""

    return render_template(
        "supplier_returns/print_slip.html",
        group=group,
        print_date=datetime.utcnow().strftime("%d/%m/%Y %H:%M")
    )
```

---

## CHANGE 2 — Update the print slip template `templates/supplier_returns/print_slip.html`

### Step 1 — Add the new column to the table header

Find:
```html
<thead>
  <tr>
    <th>Item Code</th>
    <th>Description</th>
    <th class="mono">Barcode</th>
    <th class="right">Cases (system)</th>
    <th class="right">Pieces</th>
    <th class="right">Received Qty</th>
  </tr>
</thead>
```

Replace with:
```html
<thead>
  <tr>
    <th>Item Code</th>
    <th>Supplier Code</th>
    <th>Description</th>
    <th class="mono">Barcode</th>
    <th class="right">Cases (system)</th>
    <th class="right">Pieces</th>
    <th class="right">Received Qty</th>
  </tr>
</thead>
```

---

### Step 2 — Add the supplier item code cell to each item row

Find the item row inside `{% for item in all_rows %}`:
```html
<td class="mono">{{ item.item_code_365 }}</td>
<td>{{ item.item_name or "—" }}</td>
<td class="mono">{{ item.barcode or "—" }}</td>
```

Replace with:
```html
<td class="mono">{{ item.item_code_365 }}</td>
<td class="mono">{{ item.supplier_item_code or "—" }}</td>
<td>{{ item.item_name or "—" }}</td>
<td class="mono">{{ item.barcode or "—" }}</td>
```

---

### Step 3 — Extend the totals row colspan

The totals row currently spans 3 columns for the label. It now needs to span 4 (one extra for the new Supplier Code column).

Find:
```html
<td colspan="3"><strong>TOTAL</strong></td>
```

Replace with:
```html
<td colspan="4"><strong>TOTAL</strong></td>
```

---

## Result after applying

The slip table will now show:

| Item Code | Supplier Code | Description | Barcode | Cases (system) | Pieces | Received Qty |
|-----------|--------------|-------------|---------|---------------|--------|--------------|
| CHG-0001  | SUP-001      | ORBIT ENVELOPE... | 5012345... | 0.42 | 5 | &nbsp; |

- **Supplier Code** = `DwItem.supplier_item_code` — the code the supplier uses for this item in their own system
- **Barcode** = `DwItem.barcode` — the product barcode
- No API calls, no new tables — single DB query against `DwItem` on page load
