# EP SmartGrowth — Configurable PO Email Columns per Supplier

## What this does

Each supplier can have a custom set of columns in their PO email table.
You choose which fields from `ps_items_dw` to include, set the label that
appears in the email header, and control the column order.

Case Qty and Order Qty (units) are always included at the end — they are
not configurable.

**Available fields:**

| Field key | Default label |
|---|---|
| `item_code_365` | Item Code |
| `item_name` | Item Name |
| `selling_qty` | Selling Qty |
| `barcode` | Barcode |
| `supplier_item_code` | Supplier Code |
| `number_of_pieces` | Pieces |
| `attribute_6_code_365` | Attribute 6 |
| `attribute_1_code_365` | Attribute 1 |

---

## CHANGE 1 — `models.py` — add `email_columns_json` to `ReplenishmentSupplier`

Find the `ReplenishmentSupplier` class. After `email_cc`:

```python
    email_cc      = db.Column(db.String(500), nullable=True)
    email_columns_json = db.Column(db.Text, nullable=True)  # JSON column config
```

---

## CHANGE 2 — Schema migration in `main.py`

Add alongside the existing `email`/`email_cc` migration:

```python
    db.session.execute(_text("""
        ALTER TABLE replenishment_suppliers
        ADD COLUMN IF NOT EXISTS email_columns_json TEXT
    """))
    db.session.commit()
```

---

## CHANGE 3 — `blueprints/replenishment_mvp.py` — configurable email builder

### Step 3a — Add `AVAILABLE_EMAIL_COLUMNS` constant

Add near the top of the file (after imports):

```python
# All ps_items_dw fields that can appear in PO emails, with their defaults.
AVAILABLE_EMAIL_COLUMNS = [
    {"key": "item_code_365",       "label": "Item Code",      "sort_order": 1,  "included": False},
    {"key": "item_name",           "label": "Item Name",      "sort_order": 2,  "included": True},
    {"key": "selling_qty",         "label": "Selling Qty",    "sort_order": 3,  "included": False},
    {"key": "barcode",             "label": "Barcode",        "sort_order": 4,  "included": False},
    {"key": "supplier_item_code",  "label": "Supplier Code",  "sort_order": 5,  "included": True},
    {"key": "number_of_pieces",    "label": "Pieces",         "sort_order": 6,  "included": False},
    {"key": "attribute_6_code_365","label": "Attribute 6",    "sort_order": 7,  "included": False},
    {"key": "attribute_1_code_365","label": "Attribute 1",    "sort_order": 8,  "included": False},
]
```

### Step 3b — Helper to resolve column config for a supplier

Add this helper function before `_build_po_email_content`:

```python
def _resolve_email_columns(supplier_code):
    """
    Return the ordered list of active columns for a supplier's PO email.
    Falls back to the default (item_name + supplier_item_code) if not configured.
    Each entry: {"key": str, "label": str}
    """
    import json
    from models import ReplenishmentSupplier

    repl = ReplenishmentSupplier.query.filter_by(supplier_code=supplier_code).first()
    raw = getattr(repl, "email_columns_json", None) if repl else None

    if raw:
        try:
            config = json.loads(raw)
            active = [c for c in config if c.get("included")]
            active.sort(key=lambda c: c.get("sort_order", 99))
            if active:
                return [{"key": c["key"], "label": c.get("label", c["key"])} for c in active]
        except Exception:
            pass

    # Default: item_name + supplier_item_code
    return [
        {"key": "item_name",          "label": "Item Name"},
        {"key": "supplier_item_code", "label": "Supplier Code"},
    ]


def _fetch_item_data(item_codes):
    """
    Fetch ps_items_dw rows for the given item codes.
    Returns dict: item_code -> row dict with all configurable fields.
    """
    if not item_codes:
        return {}
    from sqlalchemy import text
    from app import db
    rows = db.session.execute(text("""
        SELECT item_code_365, item_name, selling_qty, barcode,
               supplier_item_code, number_of_pieces,
               attribute_6_code_365, attribute_1_code_365
        FROM ps_items_dw
        WHERE item_code_365 = ANY(:codes)
    """), {"codes": list(item_codes)}).fetchall()
    return {
        r[0]: {
            "item_code_365":        r[0] or "",
            "item_name":            r[1] or "",
            "selling_qty":          str(r[2]) if r[2] is not None else "",
            "barcode":              r[3] or "",
            "supplier_item_code":   r[4] or "",
            "number_of_pieces":     str(r[5]) if r[5] is not None else "",
            "attribute_6_code_365": r[6] or "",
            "attribute_1_code_365": r[7] or "",
        }
        for r in rows
    }
```

### Step 3c — Update `_build_po_email_content`

Replace the existing function signature and body:

**Find:**
```python
def _build_po_email_content(run, order_lines, po_code, sent_at, qty_label="Cases Ordered"):
```

**Replace the entire function with:**

```python
def _build_po_email_content(run, order_lines, po_code, sent_at,
                             qty_label="Cases Ordered",
                             column_config=None, item_data=None):
    """Build the email content (text and HTML bodies).

    column_config: list of {"key": str, "label": str} in display order.
                   If None, defaults to [item_name, supplier_item_code].
    item_data:     dict mapping item_code_365 -> field dict from ps_items_dw.
                   If None, only order_lines data is used (no DW enrichment).
    qty_label:     header for the order quantity column.
    """
    if column_config is None:
        column_config = [
            {"key": "item_name",          "label": "Item Name"},
            {"key": "supplier_item_code", "label": "Supplier Code"},
        ]
    if item_data is None:
        item_data = {}

    # ── Build table headers ──────────────────────────────────────────────
    header_html = "".join(
        f"<th style='background:#4472C4;color:white;padding:8px;border:1px solid #ddd;'>{col['label']}</th>"
        for col in column_config
    )
    header_html += (
        "<th style='background:#4472C4;color:white;padding:8px;border:1px solid #ddd;text-align:right;'>Case Qty</th>"
        f"<th style='background:#4472C4;color:white;padding:8px;border:1px solid #ddd;text-align:right;'>{qty_label}</th>"
    )
    header_text = " | ".join(col["label"] for col in column_config)
    header_text += f" | Case Qty | {qty_label}"

    # ── Build rows ───────────────────────────────────────────────────────
    rows_html = ""
    rows_text = ""
    sorted_lines = sorted(order_lines, key=lambda l: l.item_code_365)

    for idx, line in enumerate(sorted_lines, start=1):
        case_qty = (int(float(line.case_qty_units))
                    if float(line.case_qty_units) == int(float(line.case_qty_units))
                    else float(line.case_qty_units))
        final_cases = (int(float(line.final_cases))
                       if float(line.final_cases) == int(float(line.final_cases))
                       else float(line.final_cases))

        dw = item_data.get(line.item_code_365, {})
        # Fallback for item_name: prefer DW, then order_line
        dw_with_fallback = dict(dw)
        if not dw_with_fallback.get("item_name"):
            dw_with_fallback["item_name"] = line.item_name or ""
        if not dw_with_fallback.get("supplier_item_code"):
            dw_with_fallback["supplier_item_code"] = run.supplier_code

        bg = "#f2f2f2" if idx % 2 == 0 else "#ffffff"
        row_cells = "".join(
            f"<td style='padding:8px;border:1px solid #ddd;background:{bg};'>"
            f"{dw_with_fallback.get(col['key'], '')}</td>"
            for col in column_config
        )
        row_cells += (
            f"<td style='padding:8px;border:1px solid #ddd;background:{bg};text-align:right;'>{case_qty}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;background:{bg};text-align:right;'>{final_cases}</td>"
        )
        rows_html += f"<tr>{row_cells}</tr>"

        text_vals = " | ".join(str(dw_with_fallback.get(col["key"], "")) for col in column_config)
        rows_text += f"{idx}. {text_vals} | {case_qty} | {final_cases}\n"

    html_body = f"""
    <html><head><style>
      body {{ font-family: Arial, sans-serif; }}
      table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
      .header {{ background:#f8f9fa; padding:20px; border-bottom:2px solid #4472C4; }}
    </style></head>
    <body>
      <div class="header">
        <h2>Purchase Order Created</h2>
        <p><strong>PO Code:</strong> {po_code}</p>
        <p><strong>Supplier:</strong> {run.supplier_name} ({run.supplier_code})</p>
        <p><strong>Run ID:</strong> {run.id} (7-day cover)</p>
        <p><strong>Date:</strong> {sent_at.strftime('%Y-%m-%d %H:%M')} UTC</p>
      </div>
      <table>
        <thead><tr>{header_html}</tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style='margin-top:20px;'><strong>Total Items:</strong> {len(order_lines)}</p>
      <hr>
      <p style='color:#666;font-size:12px;'>
        This is an automated email from the Warehouse Management System.
      </p>
    </body></html>
    """

    text_body = f"""Purchase Order Created

PO Code: {po_code}
Supplier: {run.supplier_name} ({run.supplier_code})
Run ID: {run.id} (7-day cover)
Date: {sent_at.strftime('%Y-%m-%d %H:%M')} UTC

Items:
{header_text}
{rows_text}
Total Items: {len(order_lines)}
"""
    return {"text_body": text_body, "html_body": html_body}
```

### Step 3d — Update `email_order` and `email_preview` in replenishment to pass config

In `email_order` (replenishment), after resolving `run`, add before calling `_build_po_email_content`:

```python
    col_config = _resolve_email_columns(run.supplier_code)
    item_codes  = {line.item_code_365 for line in order_lines}
    item_data   = _fetch_item_data(item_codes)
```

Then pass to `_build_po_email_content`:
```python
    content = _build_po_email_content(
        run, order_lines, po_code, now_utc,
        column_config=col_config,
        item_data=item_data,
    )
```

Do the same in `email_preview`.

---

## CHANGE 4 — `blueprints/forecast_workbench.py` — pass config to forecast email

In `supplier_email_preview` and `supplier_email_order`, add before the
`_build_po_email_content` / `_send_po_email` call:

```python
    from blueprints.replenishment_mvp import _resolve_email_columns, _fetch_item_data
    col_config = _resolve_email_columns(supplier_code)
    item_codes  = {line.item_code_365 for line in order_lines}
    item_data   = _fetch_item_data(item_codes)
```

Pass to `_build_po_email_content`:
```python
    content = _build_po_email_content(
        run_shim, order_lines, po_code_or_err, now_utc,
        qty_label="Order Qty (units)",
        column_config=col_config,
        item_data=item_data,
    )
```

And in `supplier_email_order`, pass to `_send_po_email` via content directly
(since `_send_po_email` calls `_build_po_email_content` internally — update
`_send_po_email` to accept and pass through `column_config` and `item_data`):

```python
    ok, err = _send_po_email(
        run_shim, order_lines, po_code_or_err, now_utc, recipient_email,
        qty_label="Order Qty (units)",
        column_config=col_config,
        item_data=item_data,
    )
```

Update `_send_po_email` signature:
```python
def _send_po_email(run, order_lines, po_code, sent_at,
                   recipient="eplattforma@gmail.com", cc=None,
                   qty_label="Cases Ordered",
                   column_config=None, item_data=None):
```
And pass them through to the `_build_po_email_content(...)` call inside it.

---

## CHANGE 5 — Supplier admin — column configurator UI

### Step 5a — `routes_admin_suppliers.py` — add save/load for column config

Add a new route to save column config for a supplier:

```python
import json as _json

@admin_suppliers_bp.route("/<int:supplier_id>/save-columns", methods=["POST"])
@login_required
@_require_admin
def supplier_save_columns(supplier_id):
    s = ReplenishmentSupplier.query.get_or_404(supplier_id)
    from blueprints.replenishment_mvp import AVAILABLE_EMAIL_COLUMNS

    config = []
    for col in AVAILABLE_EMAIL_COLUMNS:
        key = col["key"]
        included  = request.form.get(f"col_included_{key}") == "1"
        label     = (request.form.get(f"col_label_{key}") or col["label"]).strip()
        try:
            sort_order = int(request.form.get(f"col_sort_{key}") or col["sort_order"])
        except ValueError:
            sort_order = col["sort_order"]
        config.append({
            "key": key, "label": label,
            "sort_order": sort_order, "included": included,
        })
    config.sort(key=lambda c: c["sort_order"])
    s.email_columns_json = _json.dumps(config)
    db.session.commit()
    flash(f"Email columns saved for {s.supplier_name}.", "success")
    return redirect(url_for("admin_suppliers.supplier_list") + f"#supplier-{supplier_id}")
```

### Step 5b — `templates/admin/suppliers.html` — column configurator panel

In the supplier list table, add a collapsible "Configure Email Columns" panel
for each row. Add it as a second row below each supplier:

```html
{# Inside the {% for s in suppliers %} loop, AFTER the main row #}
<tr>
  <td colspan="7" class="p-0">
    <div class="collapse" id="col-config-{{ s.id }}">
      <div class="p-3 border-top" style="background:#f8f9fa;">
        <strong class="small">Email Column Configuration — {{ s.supplier_name }}</strong>
        <p class="small text-muted mb-2">
          Select which fields appear in the PO email, set their label, and drag to reorder.
          Case Qty and Order Qty are always included at the end.
        </p>
        <form method="POST"
              action="{{ url_for('admin_suppliers.supplier_save_columns', supplier_id=s.id) }}">

          {% set saved = s.email_columns_json | default('[]') | from_json %}
          {% set saved_map = {} %}
          {% for c in saved %}{% set _ = saved_map.update({c.key: c}) %}{% endfor %}

          <table class="table table-sm table-bordered mb-2" style="max-width:600px;">
            <thead class="table-secondary">
              <tr>
                <th style="width:40px">Include</th>
                <th>Field</th>
                <th>Label in email</th>
                <th style="width:80px">Order</th>
              </tr>
            </thead>
            <tbody>
              {% for col in available_columns %}
              {% set saved_col = saved_map.get(col.key, col) %}
              <tr>
                <td class="text-center">
                  <input type="checkbox" name="col_included_{{ col.key }}"
                         value="1"
                         {% if saved_col.get('included', col.included) %}checked{% endif %}>
                </td>
                <td class="small font-monospace">{{ col.key }}</td>
                <td>
                  <input name="col_label_{{ col.key }}"
                         value="{{ saved_col.get('label', col.label) }}"
                         class="form-control form-control-sm">
                </td>
                <td>
                  <input type="number" name="col_sort_{{ col.key }}"
                         value="{{ saved_col.get('sort_order', col.sort_order) }}"
                         class="form-control form-control-sm" min="1" max="99">
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          <button class="btn btn-sm btn-primary">Save Column Config</button>
        </form>
      </div>
    </div>
  </td>
</tr>
```

Add a toggle button in the main supplier row (next to the Save button):

```html
<button type="button" class="btn btn-sm btn-outline-info"
        data-bs-toggle="collapse"
        data-bs-target="#col-config-{{ s.id }}">
  <i class="fas fa-columns me-1"></i>Email Columns
</button>
```

### Step 5c — Pass `available_columns` to template from `supplier_list` route

In `routes_admin_suppliers.py`, in `supplier_list`:

```python
    from blueprints.replenishment_mvp import AVAILABLE_EMAIL_COLUMNS
    return render_template("admin/suppliers.html",
                           suppliers=suppliers,
                           missing_suppliers=missing_suppliers,
                           available_columns=AVAILABLE_EMAIL_COLUMNS)
```

### Step 5d — Add `from_json` Jinja2 filter

In `main.py` or `app.py` where Jinja2 filters are registered, add:

```python
import json as _json
app.jinja_env.filters['from_json'] = lambda s: _json.loads(s) if s else []
```

---

## Result

Each supplier's PO email will have exactly the columns you configured, in the
order you set. The CORINA SNACKS example would be configured with:
- ✓ Item Name (order 1)
- ✓ Supplier Code (order 2)
- ✓ Case Qty (always last, auto)
- ✓ Order Qty units (always last, auto)

To add Barcode for a different supplier, just tick it and set order 3.
