# FIX-005 — One Batch Report Template, Complete and Offline-Safe

## Priority: MEDIUM — Reports are the paper trail checkers and drivers use; today they are duplicated, incomplete, and depend on internet access to print.

Source: WMDS Batch & Picking Review (7 Jul 2026), item P5 + report consistency findings. Apply FIX-003 Changes 4–5 first (weight field, SKIPPED badge) or fold them in here.

---

## The Problem

- `templates/batch_print_reports.html` (picker report) and
  `templates/batch_admin_print_report.html` (admin report) are ~95% identical —
  same CSS, same layout — but rendered from two different route functions with two
  different data shapes. Every fix must be made twice; today each has bugs the
  other doesn't.
- Neither batch report prints **Unit or Pack**, although the pick screen displays
  pack size in large type because piece-vs-case errors matter. A checker reading
  the report cannot tell 5 pieces from 5 cases. The single-order report
  (`print_picking_report.html`) already has a UNIT column.
- **Manually-picked items** are counted in the picker report's totals but appear in
  no table — lines vanish from the printout.
- Both reports print the internal DB id ("Batch 293"); every screen shows the batch
  number (`BATCH-20260707-001`). Give the paper the same identity as the screen.
- Both batch reports load Bootstrap + FontAwesome from CDNs
  (`cdn.jsdelivr.net`, `cdnjs.cloudflare.com`). If the warehouse internet drops,
  printouts lose all layout. `print_picking_report.html` correctly uses local
  `static/css` files.
- `batch_print_reports()` (`routes_batch.py` 4506–4687) builds **two parallel data
  structures** (`invoices_data` plus `batch_invoices_with_data`/`invoice_items`) —
  the template only uses `invoices_data`. It also runs `Invoice.query.get()` in two
  loops (N+1).

## What Changes

### Change 1 — Single shared template

Create `templates/batch_report.html` used by BOTH routes. Data contract (one shape):

```python
{
  'batch': batch_session,          # header uses batch.batch_number or 'BATCH-'+id
  'generated_at': get_local_time(),
  'picker_name': batch_session.assigned_to or 'Unassigned',
  'invoices': [{
      'invoice': invoice,
      'routing_label': ...,        # existing _routing_label_for_invoice()
      'picked': [ {item_code, item_name, location, unit_type, pack,
                   qty, picked_qty, source} ],   # source: 'batch' | 'manual'
      'problems': [ {item_code, item_name, location, unit_type, pack,
                     qty, picked_qty, status, reason} ],  # status: 'skipped' | 'exception' | 'unpicked'
      'total_lines': n, 'total_units': n, 'total_weight': kg,
      'completion_pct': f,
  }]
}
```

Template columns (both tables): Item Code | Description | Location | **Unit** |
**Pack** | Required | Picked (+ Status/Reason on the problems table). Show a small
"manual" tag on rows with `source == 'manual'` so hand-picked lines are visible
instead of silently counted.

Header: `{{ batch.batch_number or 'BATCH-' ~ batch.id }}` — drop the duplicated
"Batch {{ id }} / Batch ID: {{ id }}" pair. Footer:
`Generated {{ generated_at }} | Picker: {{ picker_name }}`.

### Change 2 — Local assets

Replace the CDN links with the same local files `print_picking_report.html` uses:

```html
<link rel="stylesheet" href="{{ url_for('static', filename='css/bootstrap.min.css') }}">
<link rel="stylesheet" href="{{ url_for('static', filename='css/all.min.css') }}">
```

(Both files already exist in `static/css` — verify, else copy them in.)

### Change 3 — Slim the two routes

- `batch_print_reports()`: delete the unused second data build
  (`batch_invoices_with_data`, `invoice_items`, the per-invoice
  `completion_status` loop — nothing consumes it). Replace per-invoice
  `Invoice.query.get()` with one
  `Invoice.query.filter(Invoice.invoice_no.in_(invoice_nos))` prefetch.
  Map its output into the shared contract: batch-picked and manually-picked rows
  both go into `picked` with their `source` tag; unpicked/skipped rows into
  `problems` with real `status` and `skip_reason`.
- `batch_admin_print_report()`: keep its allocation-based math (it is the more
  correct of the two), add `unit_type`/`pack` (already in its item dicts) and the
  `status`/`reason` fields from FIX-003 Change 5, and map into the same contract.
- Delete `templates/batch_print_reports.html` and
  `templates/batch_admin_print_report.html` once both routes render
  `batch_report.html`.

### Change 4 — Decide whether two reports are still needed

The two reports now differ only in when they're linked (active vs completed) and
picked-data source. Recommendation: keep the two routes (access rules differ) but
consider linking only ONE "Print report" button per batch row on the Manage page,
choosing the route by batch status — one button, one paper format, less operator
confusion.

## Schema Changes

None.

## Tests Required

| # | Scenario | Expected |
|---|----------|----------|
| R1 | Picker report, batch with pack-based item (e.g. 3 cases of 12) | Unit + Pack columns printed |
| R2 | Batch where one line was picked manually outside the batch | Line appears with "manual" tag; totals unchanged |
| R3 | Batch with skip + short pick | Problems table shows SKIPPED with reason and EXCEPTION with shortage |
| R4 | Header/footer | Batch number (not DB id), generation time, picker name |
| R5 | Print with network blocked (devtools offline) | Layout intact |
| R6 | Admin report totals vs picker report totals for same batch | Identical lines/units/weight |

## Verification

1. Run one real batch with: a full pick, a short pick, a skip left unresolved, and
   one line picked manually. Print both reports; every line must be accounted for
   and the two printouts must agree.
2. Diff the HTML of both rendered reports — only the data may differ, not the layout.
3. Print with wifi off on the warehouse PC.
