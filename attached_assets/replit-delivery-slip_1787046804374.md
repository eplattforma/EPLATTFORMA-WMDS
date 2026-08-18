# Replit — turn the picking report into a delivery slip

Change the post-picking printout (`print_picking_report.html`, served by `print_invoice` at `/admin/invoice/<invoice_no>/print`) from a corridor-ordered *picking* report into a clean *delivery / pallet-identification slip*. Same data source, far less clutter. Its two jobs: the **driver** sees what's inside and for whom, and staff can **identify the order on the staging pallet**.

## Decisions applied (defaults — change if you disagree)
- **Item code:** kept, but small (left of the name) for scan/verification. Customers read names, so the name stays dominant.
- **Chilled flag:** shown — a small "CHILLED" tag on cooler items so they're handled separately in the van (only if an item cooler/temperature flag exists; otherwise skip).
- **Short-picks:** shown as "picked / ordered" only when they differ (e.g. `1 / 2`), so a shortage is visible on delivery. Fully-picked lines show just the quantity.

## Remove (none of it serves the driver or pallet ID)
`LOCATION` column · `QTY REQ` column · zone headers ("Manually Picked by Zone / ZONE MAIN") · the `Manual Zones / Manually Picked / Batch Zones / Batch Picked` counters · `Picker:` / `Picked by`. (Keep picker name only as tiny footer text if you want internal traceability.)

## Keep and enlarge
- **Customer name** — the biggest element on the page (pallet identifier).
- **Invoice barcode** (`*INxx…*`) — centred and enlarged for scanning.
- **Route + stop number + date + driver** (from `route_id` → shipment and `stop_id` → route_stop).
- **Contents:** item code (small) · item name · unit · qty (short-pick shown as `pic / req` when different) · optional CHILLED tag.
- **Totals:** pieces, weight, and a blank **Boxes ___** to write on.

## Target template (replace the body of `print_picking_report.html`)
Bind to the existing context (`invoice`, the picked-items list, route/stop/driver already available to this template). Illustrative Jinja:

```html
<style>
  @media print { @page { margin: 8mm; } }
  .slip { font-family: Arial, sans-serif; color:#000; max-width: 190mm; }
  .cust { font-size: 30px; font-weight: 700; line-height: 1.05; }
  .meta { display:flex; justify-content:space-between; font-size:13px; margin-top:2px; }
  .stop { font-size: 20px; font-weight: 700; text-align:right; }
  .barcode { font-family:'Libre Barcode 39', 'Code39', monospace; font-size: 46px;
             text-align:center; letter-spacing:2px; margin:6px 0; }
  .bc-num { text-align:center; font-family:monospace; font-size:13px; margin-top:-6px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; border-bottom:1.5px solid #000; padding:4px 6px; font-size:11px; }
  td { padding:4px 6px; border-bottom:0.5px solid #ccc; }
  td.qty, th.qty { text-align:right; white-space:nowrap; }
  .code { font-family:monospace; font-size:11px; color:#444; }
  .chilled { font-size:10px; border:1px solid #0C447C; color:#0C447C; border-radius:3px; padding:0 4px; }
  .short  { color:#A32D2D; font-weight:700; }
  .totals { display:flex; gap:24px; justify-content:flex-end; font-size:14px;
            border-top:1.5px solid #000; margin-top:8px; padding-top:6px; }
  .totals b { font-size:18px; }
</style>

<div class="slip">
  <div style="display:flex; justify-content:space-between; align-items:flex-start;">
    <div class="cust">{{ invoice.customer_name }}</div>
    <div class="stop">Stop {{ stop_number or '—' }}<div style="font-size:12px;font-weight:400;">Route {{ route_name }}</div></div>
  </div>
  <div class="meta">
    <span>{{ delivery_date }} &nbsp;·&nbsp; Driver: {{ driver_name }}</span>
    <span class="code">{{ invoice.customer_code_365 or invoice.customer_code }}</span>
  </div>

  <div class="barcode">*{{ invoice.invoice_no }}*</div>
  <div class="bc-num">{{ invoice.invoice_no }}</div>

  <table>
    <thead><tr><th style="width:16%">Code</th><th>Item</th><th style="width:16%">Unit</th><th class="qty" style="width:14%">Qty</th></tr></thead>
    <tbody>
    {% for it in items %}
      <tr>
        <td class="code">{{ it.item_code }}</td>
        <td>{{ it.item_name }}{% if it.is_chilled %} <span class="chilled">CHILLED</span>{% endif %}</td>
        <td>{{ it.unit_label }}</td>
        <td class="qty">{% if it.qty_picked < it.qty %}<span class="short">{{ it.qty_picked }} / {{ it.qty }}</span>{% else %}{{ it.qty_picked }}{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <div class="totals">
    <span><b>{{ invoice.total_items }}</b> pieces</span>
    <span><b>{{ invoice.total_weight | round(1) }}</b> kg</span>
    <span>Boxes <b>_____</b></span>
  </div>
</div>
```

Notes for the Agent:
- `items` = the picked items already gathered in `print_invoice` (flatten the existing `manually_picked` + batch lists; drop the zone grouping — a single flat list is fine for delivery).
- `route_name`, `driver_name`, `stop_number`, `delivery_date` are already shown on the current template; reuse those bindings.
- `it.is_chilled` only if a cooler/temperature flag exists on the item; otherwise omit the tag.
- Keep the existing barcode font the current report already uses (the `*INxxxx*` render); the CSS names above are placeholders.

## Paste to the Replit Agent
> Rewrite `print_picking_report.html` as a delivery slip: remove the LOCATION and QTY-REQ columns, the zone grouping and the Manual/Batch counters, and the "Picked by" line. Make `customer_name` the largest element, enlarge and centre the `*invoice_no*` barcode, and add route + stop number + driver + date. Show items as a single flat list (code small, name, unit, qty) with short-picks rendered as "picked / ordered" when they differ, an optional CHILLED tag on cooler items, and a footer of total pieces, total weight, and a blank "Boxes ___". Keep the same route/print button and data source; only the layout and columns change.
