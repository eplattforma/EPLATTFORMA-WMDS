# Supplier Returns — V8 Supplier Acknowledgement Slip

Two changes:
1. New route `GET /supplier-returns/print/<supplier_code>` — renders a clean print-only page
2. A **Print** button added to each supplier card header in the main template

No new DB tables. No new API calls. Uses existing `get_returns_stock()` data.

---

## CHANGE 1 — New route in `blueprints/supplier_returns.py`

Add this import at the top if not already present:
```python
from flask import render_template, request, jsonify, abort
```

Add this route **after** the existing routes:

```python
@supplier_returns_bp.route("/print/<supplier_code>")
def print_slip(supplier_code):
    """Print-friendly acknowledgement slip for a single supplier."""
    result = get_returns_stock(force_refresh=False)
    groups = result.get("groups", [])

    # Find the matching supplier group
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

Make sure `datetime` is imported at the top:
```python
from datetime import datetime
```

---

## CHANGE 2 — New template `templates/supplier_returns/print_slip.html`

Create this file (no base template — it is self-contained):

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Purchase Returns — {{ group.supplier_name }}</title>
  <style>
    /* ── Reset & base ── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: Arial, sans-serif;
      font-size: 11pt;
      color: #000;
      background: #fff;
      padding: 20mm 15mm;
    }

    /* ── Header ── */
    .slip-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 2px solid #000;
      padding-bottom: 8px;
      margin-bottom: 12px;
    }
    .slip-header .doc-title {
      font-size: 16pt;
      font-weight: bold;
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .slip-header .doc-meta {
      text-align: right;
      font-size: 9pt;
      color: #333;
      line-height: 1.6;
    }

    /* ── Supplier block ── */
    .supplier-block {
      margin-bottom: 14px;
      font-size: 10pt;
    }
    .supplier-block .label {
      font-weight: bold;
      display: inline-block;
      width: 110px;
    }

    /* ── Items table ── */
    table {
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 20px;
      font-size: 10pt;
    }
    thead tr {
      background: #000;
      color: #fff;
    }
    thead th {
      padding: 5px 8px;
      text-align: left;
      font-weight: bold;
    }
    thead th.right { text-align: right; }
    tbody tr:nth-child(even) { background: #f5f5f5; }
    tbody td {
      padding: 5px 8px;
      border-bottom: 1px solid #ddd;
      vertical-align: middle;
    }
    tbody td.right { text-align: right; }
    tbody td.mono { font-family: monospace; font-size: 9.5pt; }

    /* ── Totals row ── */
    .totals-row td {
      border-top: 2px solid #000 !important;
      font-weight: bold;
      background: #fff !important;
    }

    /* ── No items ── */
    .no-items {
      text-align: center;
      padding: 20px;
      color: #666;
      font-style: italic;
    }

    /* ── Signature section ── */
    .signature-section {
      margin-top: 24px;
      border-top: 1px solid #bbb;
      padding-top: 16px;
      page-break-inside: avoid;
    }
    .signature-section .sig-title {
      font-weight: bold;
      font-size: 10pt;
      margin-bottom: 16px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .sig-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 32px;
    }
    .sig-field { margin-bottom: 24px; }
    .sig-field .sig-line {
      border-bottom: 1px solid #000;
      height: 30px;
      margin-bottom: 4px;
    }
    .sig-field .sig-label {
      font-size: 8.5pt;
      color: #555;
    }

    /* ── Footer ── */
    .slip-footer {
      margin-top: 16px;
      font-size: 8pt;
      color: #888;
      text-align: center;
      border-top: 1px solid #ddd;
      padding-top: 6px;
    }

    /* ── Print button (hidden when printing) ── */
    .print-controls {
      position: fixed;
      top: 10px;
      right: 10px;
      display: flex;
      gap: 8px;
      z-index: 100;
    }
    .btn-print {
      padding: 7px 18px;
      background: #000;
      color: #fff;
      border: none;
      font-size: 11pt;
      cursor: pointer;
      border-radius: 4px;
    }
    .btn-print:hover { background: #333; }
    .btn-back {
      padding: 7px 18px;
      background: #fff;
      color: #000;
      border: 1px solid #000;
      font-size: 11pt;
      cursor: pointer;
      border-radius: 4px;
      text-decoration: none;
    }

    @media print {
      .print-controls { display: none; }
      body { padding: 10mm 12mm; }
    }
  </style>
</head>
<body>

  <!-- Print / Back controls (screen only) -->
  <div class="print-controls">
    <a class="btn-back" href="/supplier-returns">← Back</a>
    <button class="btn-print" onclick="window.print()">🖨 Print</button>
  </div>

  <!-- Header -->
  <div class="slip-header">
    <div>
      <div class="doc-title">Purchase Returns</div>
      <div style="font-size:9pt;color:#555;margin-top:3px;">Supplier Acknowledgement Slip</div>
    </div>
    <div class="doc-meta">
      Date: <strong>{{ print_date }}</strong><br>
      Printed by: warehouse
    </div>
  </div>

  <!-- Supplier info -->
  <div class="supplier-block">
    <div>
      <span class="label">Supplier:</span>
      <strong>{{ group.supplier_name or "Unknown" }}</strong>
    </div>
    <div>
      <span class="label">Supplier Code:</span>
      {{ group.supplier_code_365 or "—" }}
    </div>
  </div>

  <!-- Items table -->
  {% set available_rows = group.item_rows | selectattr("fully_committed", "equalto", false) | list %}
  {% set all_rows = group.item_rows %}

  {% if all_rows %}
  <table>
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
    <tbody>
      {% for item in all_rows %}
      <tr>
        <td class="mono">{{ item.item_code_365 }}</td>
        <td>{{ item.item_name or "—" }}</td>
        <td class="mono">{{ item.barcode or "—" }}</td>
        <td class="right">
          {% if item.fully_committed %}
            <em style="color:#888">On PO</em>
          {% else %}
            {{ "%.4f"|format(item.stock_cases) | replace("0000","") | replace(".",".",1) }}
          {% endif %}
        </td>
        <td class="right">{{ item.pieces if item.pieces is defined else "—" }}</td>
        <td class="right">&nbsp;</td>
      </tr>
      {% endfor %}

      <!-- Totals -->
      <tr class="totals-row">
        <td colspan="3"><strong>TOTAL</strong></td>
        <td class="right">
          {% set total_cases = all_rows | map(attribute='stock_cases') | sum %}
          <strong>{{ "%.4f"|format(total_cases) }}</strong>
        </td>
        <td class="right">
          {% set total_pieces = namespace(val=0) %}
          {% for item in all_rows %}
            {% if item.pieces is defined and item.pieces %}
              {% set total_pieces.val = total_pieces.val + item.pieces %}
            {% endif %}
          {% endfor %}
          <strong>{{ total_pieces.val }}</strong>
        </td>
        <td></td>
      </tr>
    </tbody>
  </table>
  {% else %}
  <p class="no-items">No items found for this supplier in store 100.</p>
  {% endif %}

  <!-- Signature section -->
  <div class="signature-section">
    <div class="sig-title">Acknowledgement</div>
    <p style="font-size:9.5pt; margin-bottom:16px;">
      I confirm that the above items have been collected / received in full as described.
    </p>
    <div class="sig-grid">
      <div>
        <div class="sig-field">
          <div class="sig-line"></div>
          <div class="sig-label">Supplier Representative — Signature</div>
        </div>
        <div class="sig-field">
          <div class="sig-line"></div>
          <div class="sig-label">Supplier Representative — Printed Name</div>
        </div>
      </div>
      <div>
        <div class="sig-field">
          <div class="sig-line"></div>
          <div class="sig-label">Warehouse Staff — Signature</div>
        </div>
        <div class="sig-field">
          <div class="sig-line"></div>
          <div class="sig-label">Date Collected</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Footer -->
  <div class="slip-footer">
    This document is for internal warehouse use only. No monetary values are stated.
  </div>

</body>
</html>
```

---

## CHANGE 3 — Add Print button to each supplier card header in `templates/supplier_returns/index.html`

### Step 1 — Find the supplier card header buttons area

Inside the `{% for group in data.groups %}` loop, find the supplier card header. It will look like:

```html
<div class="card-header d-flex justify-content-between align-items-center">
```

Inside that header, there is already a collapse toggle button. Find the end of the button group — it is usually a `<div class="d-flex gap-2">` or similar containing the toggle chevron.

Add a Print button **before** the collapse toggle:

```html
<a href="/supplier-returns/print/{{ group.supplier_code_365 }}"
   target="_blank"
   class="btn btn-outline-secondary btn-sm"
   title="Print acknowledgement slip"
   onclick="event.stopPropagation()">
  <i class="fas fa-print"></i>
</a>
```

The `onclick="event.stopPropagation()"` stops the click from collapsing/expanding the card when the print button is clicked. `target="_blank"` opens the slip in a new tab so the main page stays open.

---

## How it works after applying

- Each supplier card now has a **print icon button** (🖨) in its header
- Clicking it opens `/supplier-returns/print/SUPPLIER_CODE` in a new tab
- That page shows: supplier name, supplier code, date, and a table of all items with item code, description, barcode, system cases qty, pieces, and a blank "Received Qty" column for the supplier to fill in
- Items fully on a PO show "On PO" in the cases column — they are still listed so the supplier can confirm what has already been committed
- A totals row sums cases and pieces
- A signature block at the bottom has space for: supplier rep signature, printed name, warehouse staff signature, and date collected
- The **Print** button (top-right of the screen) triggers `window.print()` — browser print dialog opens
- The Back button returns to the main supplier returns page
- No prices or monetary values appear anywhere on the slip

---

## Notes for Replit

- The `pieces` field must exist on each `item_row` dict. In the service, `pieces` is already calculated as `round(stock_cases × selling_qty)`. Confirm `item_row` dicts include a `pieces` key — if not, add it in `get_returns_stock()` when building `item_rows`.
- The `barcode` field should come from `DwItem` or the cache table. If it is not currently stored in `supplier_returns_stock_cache`, add a `barcode VARCHAR(64)` column to the cache table and populate it from `DwItem.barcode` during the refresh write.
- The slip uses no external CSS or JS — it is fully self-contained and will print correctly without an internet connection.
