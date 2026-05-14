# FIX-001 — Three Cooler Production Bugs

## Priority: CRITICAL — Fix before any new development

Three bugs are blocking the cooler picking workflow end-to-end.
Fix all three in this session. They are independent — fix in any order.

---

## Bug 1 — Box assignment fails silently (Assign button does nothing)

### Symptom
All items show "Picked" on the cooler screen. Boxes are open.
Clicking "Assign" on any item does nothing — the box items count stays
at "—" and no error is shown.

### Root Cause
`blueprints/cooler_picking.py:box_assign_item()` at line 709 reads
`queue_item_id` and `picked_qty` from the POST body. After the cooler
batch picking fix (items now picked via the standard batch interface),
`batch_pick_queue.qty_picked` may be 0 or NULL because the batch
picking confirm handler updates `InvoiceItem.is_picked` but does not
reliably update `batch_pick_queue.qty_picked`.

The assignment endpoint then validates `picked_qty` from the form, but
the template's Assign button sends `picked_qty` derived from the queue
row's `qty_picked` field — which is 0. The endpoint rejects 0 qty.

Additionally: the template sends `queue_item_id` but may not be sending
the correct ID for items that were batch-picked (the queue row ID vs the
InvoiceItem ID are being confused).

### Diagnostic — run in shell first

```python
from app import app, db
from sqlalchemy import text

with app.app_context():
    # Check what cooler queue rows look like after batch picking
    rows = db.session.execute(text("""
        SELECT bpq.id, bpq.invoice_no, bpq.item_code,
               bpq.status, bpq.qty_required, bpq.qty_picked,
               bpq.pick_zone_type
        FROM batch_pick_queue bpq
        WHERE bpq.pick_zone_type = 'cooler'
        AND bpq.status = 'picked'
        LIMIT 10
    """)).fetchall()
    for r in rows:
        print(dict(zip(['id','invoice_no','item_code','status',
                        'qty_required','qty_picked','zone'], r)))
```

Expected: `qty_picked` should equal `qty_required` for picked rows.
If `qty_picked` is NULL or 0, that is the bug.

### Fix A — Ensure `qty_picked` is set when batch picking confirms an item

In `routes_batch.py`, in the item confirmation handler (where
`InvoiceItem.is_picked = True` is set), also update the cooler queue row:

```python
# After marking InvoiceItem as picked, update the cooler queue row
if batch_session.session_type == 'cooler_route':
    db.session.execute(text("""
        UPDATE batch_pick_queue
        SET status = 'picked',
            qty_picked = qty_required
        WHERE invoice_no = :inv
          AND item_code = :item
          AND batch_session_id = :sid
          AND pick_zone_type = 'cooler'
    """), {
        "inv": invoice_no,
        "item": item_code,
        "sid": batch_session.id,
    })
```

### Fix B — Assign endpoint: use `qty_required` when `picked_qty` is 0

In `blueprints/cooler_picking.py:box_assign_item()`, after fetching
`qrow`, use a fallback for `picked_qty`:

```python
# Replace the current picked_qty validation with:
try:
    picked_qty = float(data.get("picked_qty") or 0)
except (TypeError, ValueError):
    picked_qty = 0.0

# If qty is 0 or not provided, use qty_required from the queue row
if picked_qty <= 0:
    picked_qty = float(qrow[3]) if qrow[3] is not None else 1.0
```

### Fix C — Add error visibility to the Assign button

In `templates/cooler/route_picking.html`, the Assign button currently
submits via AJAX or form. If it returns a non-200 response, the error
is silently swallowed. Add visible error handling:

```javascript
// If using fetch/AJAX for assign:
.then(response => {
    if (!response.ok) {
        return response.json().then(err => {
            alert('Assignment failed: ' + (err.error || response.statusText));
            throw new Error(err.error);
        });
    }
    return response.json();
})
```

Or if using a form POST, ensure the endpoint flashes an error and
redirects back rather than returning JSON that the page ignores.

### Verification
1. Pick items via COOLER-ROUTE batch
2. Go to cooler screen
3. Click Assign → item count on box updates from "—" to the correct count
4. Check DB: `SELECT * FROM cooler_box_items WHERE cooler_box_id = <id>`
   should show rows

---

## Bug 2 — Pick buttons show when batch is in progress

### Symptom
Picker opens `/cooler/route/<id>/<date>` after being assigned the
COOLER-ROUTE batch. The Sequenced section shows individual Pick buttons
on each row. Picker clicks Pick directly on the cooler screen instead of
using the batch interface. Items get picked without location guidance,
in the wrong order, with no confirmation screen.

### Fix
In `blueprints/cooler_picking.py:route_picking()`, add `batch_in_progress`
to the template context:

```python
# After fetching the cooler session, determine if batch is in progress
batch_in_progress = False
if cooler_session:
    batch_in_progress = cooler_session["status"] not in (
        "Completed", "Cancelled", "Archived"
    ) and cooler_session.get("sequence_locked_at") is not None
```

In `templates/cooler/route_picking.html`, in the Sequenced items table,
the Action column currently shows a Pick button unconditionally for
`status == 'pending'` items. Change to:

```html
{# Action column — Pick button only when no batch in progress #}
{% if q.status == 'pending' %}
  {% if batch_in_progress %}
    <span class="text-muted small">
      <i class="fas fa-hourglass-half me-1"></i>via batch
    </span>
  {% else %}
    <form method="post" action="{{ url_for('cooler.pick_item',
          route_id=route_id) }}" style="display:inline;">
      <input type="hidden" name="queue_item_id" value="{{ q.queue_item_id }}">
      <button type="submit" class="btn btn-sm btn-primary">
        <i class="fas fa-hand-pointer me-1"></i>Pick
      </button>
    </form>
  {% endif %}
{% elif q.status == 'picked' %}
  {# Assign to box — always available after picking #}
  ...existing assign UI...
{% endif %}
```

The "Assign to box" action for already-picked items is unaffected —
it should always be visible regardless of batch status.

### Verification
1. Lock sequencing on a route
2. Open the cooler screen as picker
3. Confirm: NO pick buttons visible in Sequenced section
4. Confirm: banner "Picking in progress — batch COOLER-ROUTE-412 not yet
   complete" is shown with a link to the batch
5. Click the batch link → opens standard batch picking interface

---

## Bug 3 — Cooler batch shows no warehouse location

### Symptom
Picker opens COOLER-ROUTE batch in My Batch Picking Sessions.
Items appear in the list but show no warehouse location (blank or "—").
Items may be sorted by stop sequence rather than warehouse location,
causing the picker to walk inefficiently.

### Root Cause
`InvoiceItem.location` is NULL for items that were added to the cooler
queue via `cooler_route_extraction.py` — the extraction sets
`locked_by_batch_id` on `InvoiceItem` but does not ensure the location
field is populated. The location comes from `ps_items_dw.wms_location`
(or `wms_aisle`/`wms_bay`/`wms_level`/`wms_position`) and may not be
synced onto `InvoiceItem`.

### Diagnostic

```sql
-- Check if InvoiceItem has location for cooler-locked items
SELECT ii.invoice_no, ii.item_code, ii.location, ii.zone,
       psi.wms_location, psi.wms_aisle, psi.wms_bay
FROM invoice_items ii
LEFT JOIN ps_items_dw psi ON psi.item_code_365 = ii.item_code
WHERE ii.locked_by_batch_id IN (
    SELECT id FROM batch_picking_sessions
    WHERE session_type = 'cooler_route'
    AND status NOT IN ('Completed', 'Cancelled', 'Archived')
)
LIMIT 10;
```

If `ii.location` is NULL but `psi.wms_location` is not, that is the bug.

### Fix A — Enrich location in `get_grouped_items()` Cooler branch

In `models.py:BatchPickingSession.get_grouped_items()`, in the Cooler
branch, after fetching `cooler_items`, enrich any item with NULL location
from `ps_items_dw`:

```python
# After fetching cooler_items in the Cooler branch:
item_codes_missing_loc = {
    it.item_code for it in cooler_items
    if not it.location
}
dw_locations = {}
if item_codes_missing_loc:
    dw_rows = db.session.execute(
        _sql_text(
            "SELECT item_code_365, wms_location, "
            "wms_aisle, wms_bay, wms_level, wms_position "
            "FROM ps_items_dw "
            "WHERE item_code_365 = ANY(:codes)"
        ),
        {"codes": list(item_codes_missing_loc)}
    ).fetchall()
    dw_locations = {r[0]: r for r in dw_rows}

for item in cooler_items:
    if not item.location and item.item_code in dw_locations:
        dw = dw_locations[item.item_code]
        # Do NOT persist — just set for this session's sort/display
        item.location = dw[1]  # wms_location
```

### Fix B — Sort cooler batch items by warehouse location

In the same Cooler branch, after enriching locations, confirm that
`sort_items_for_picking(cooler_items)` is called — this function uses
the same location-based sort as standard batches. If the sort function
requires `item.aisle`/`item.bay` fields rather than `item.location`,
also set those from the dw_locations dict.

Verify the sort function signature:

```python
# In services/batch_picking.py or wherever sort_items_for_picking is defined:
# Check what fields it reads — aisle, bay, level, position or location string
```

Align the Cooler branch enrichment with whatever fields the sort uses.

### Fix C — Display location in batch picking item screen

In `templates/batch_picking_item.html` (the screen shown to the picker
when working through a batch item by item), confirm that the location
field is displayed prominently. Search for `item.location` or `location`
in the template. If it shows `item.location or 'NO LOCATION'`, that is
correct — Fix A above ensures the field is populated.

### Verification
1. Open COOLER-ROUTE batch as picker
2. First item shows warehouse location (aisle/bay)
3. Items sorted in logical warehouse walking order, not stop order
4. Complete picking of one item → item appears as "Picked" on cooler screen

---

## Test After All Three Fixes

Run the complete end-to-end flow:

1. Create a route with at least one invoice containing SENSITIVE items
2. Confirm auto-extraction fired: `SELECT * FROM batch_pick_queue WHERE pick_zone_type = 'cooler'`
3. Lock sequencing on the cooler screen
4. Assign the COOLER-ROUTE batch to a picker
5. Picker opens batch → sees items with warehouse locations, in location order
6. Picker picks all items → NO pick buttons on cooler screen during this
7. Batch completes → picker is redirected to cooler screen
8. Cooler screen shows all items "Picked", "Assign" buttons visible
9. Click Assign for each item → items count updates on box
10. Close box → `first_stop_sequence` and `last_stop_sequence` populated
11. All invoices on route show "Ready for Dispatch"

## No schema changes. No migrations. Logic and template fixes only.
