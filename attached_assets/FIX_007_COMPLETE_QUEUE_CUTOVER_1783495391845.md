# FIX-007 — Complete the Queue Cutover (FIX-006 Phase A is half-wired)

## Priority: CRITICAL — Do not run production picking on this build until Changes 1–2 land. The screen and the confirm action currently read from two different lists.

Found during review of the FIX-004/005/006 implementation (upload of 8 Jul 2026).

---

## The Problem

Phase A converted the **display** route to the queue, but not the **action** routes:

- `batch_picking_item` (display), for DB-backed non-cooler batches:
  `items = rebuild_items_from_queue(batch_id)`, `current_item_index = 0` — queue head
  is the current item. Correct.
- `complete_batch_confirm` (~line 2816) and `skip_batch_item` (~line 4190) still do
  `items = session['batch_items_<id>'][current_item_index]` with a
  `get_grouped_items()` fallback — **cookie + index, not the queue**.

Why that breaks:

1. The two lists are sorted differently. Queue rebuild: `sequence_no` order
   (= `invoice_no, item_code` from `_enqueue_locked_items` / enumeration order from
   `create_batch_atomic`), with a routing/location re-sort for Sequential.
   Cookie list: `get_grouped_items()` → `sorting_utils` walking order
   (zone → corridor → shelf → level → bin). With the default sort config these
   orders differ, so **the item shown on screen and the item that gets the
   confirmed quantity can be different items.**
2. The index desyncs. Display forces `current_item_index = 0` on every view;
   confirm increments it. In Consolidated mode the cookie list is built once and
   never re-filtered, so after the first pick the confirm handler processes
   cookie[0] (already picked) or cookie[1] while the screen shows the queue head —
   whichever way the commits land, it is not guaranteed to be the displayed item.
3. **This is live even with the flag off.** The legacy creation paths still call
   `_enqueue_locked_items` (lines 612, 1017), so every newly created standard batch
   has queue rows → `is_db_backed_batch()` is True → the display switches to the
   queue while confirm stays on the cookie. The desync does not wait for the flag.

Two further gaps:

4. **The flag is not actually on in production.** `is_db_queue_enabled()` now
   defaults to `"true"`, but `services/settings_defaults.py:57` still seeds
   `"use_db_backed_picking_queue": "false"`, and the production settings row
   already exists with `"false"`. `Setting.get` returns the stored row — the code
   default never applies. Batch creation therefore still runs the legacy path.
5. **Walking order is lost on the queue path.** `sequence_no` is written in
   `invoice_no, item_code` order, not in the admin-configured walking order
   (FIX-006 step 4 was skipped). Once the queue is the work-list, Consolidated
   pickers zigzag across corridors instead of following the configured route.

## What Changes

### Change 1 — Confirm and skip resolve the current item from the queue

In both `complete_batch_confirm` and `skip_batch_item`, replace the cookie/index
resolution for DB-backed non-cooler batches:

```python
from services.batch_picking import (
    is_db_backed_batch as _is_db_backed,
    rebuild_items_from_queue as _rebuild_from_queue,
)
_queue_primary = (_is_db_backed(batch_id)
                  and getattr(batch_session, 'session_type', None) != 'cooler_route')
if _queue_primary:
    items = _rebuild_from_queue(batch_id)
    if not items:
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
    current_item = items[0]          # queue head == what the screen showed
else:
    # existing cookie/index path for legacy + cooler batches (unchanged)
    ...
```

And on the queue-primary path do **not** touch `current_item_index` (no increment
in confirm, no increment in skip — the row leaving `pending` advances the queue).
Guard the existing `current_item_index += 1` / `>= len(items)` completion blocks
with `if not _queue_primary:`; for queue-primary batches the empty-queue check in
`batch_picking_item` already handles completion and the skip recycle.

Belt-and-braces: have the pick form post the displayed identity and verify it —
add `<input type="hidden" name="item_code" value="{{ item.item_code }}">` (and
`current_invoice` for Sequential) in `batch_picking_item.html`'s pickForm and
skipForm; in the handlers, if the posted `item_code` differs from
`current_item['item_code']`, redirect back to `batch_picking_item` with a
"list refreshed, please re-check" flash instead of writing anything.

### Change 2 — Write `sequence_no` in walking order

- `services/batch_picking.py:create_batch_atomic` step 6: sort `free` with the
  same walking-order logic before enumerating:

```python
from sorting_utils import sort_items_for_picking
free = sort_items_for_picking(free)
for seq, item in enumerate(free, start=1):
    ...
```

- `routes_batch.py:_enqueue_locked_items`: the SQL `ROW_NUMBER() OVER (ORDER BY
  ii.invoice_no, ii.item_code)` cannot express the configured walking order.
  Replace the raw INSERT with a small Python loop: select the locked InvoiceItems,
  `sort_items_for_picking(...)`, then insert queue rows with the loop index as
  `sequence_no` (keep the NOT EXISTS guard per row).

Note `rebuild_items_from_queue` re-sorts Sequential batches by routing/invoice —
keep that, but change its within-invoice tiebreak from plain `location` string to
the queue `sequence_no` so the walking order set at enqueue time survives.

### Change 3 — Actually flip the flag

- `services/settings_defaults.py:57` → `"use_db_backed_picking_queue": "true"`.
- The seeder is `ON CONFLICT DO NOTHING`, so the existing production row keeps
  its old value. Run once against production:

```sql
UPDATE settings SET value = 'true' WHERE key = 'use_db_backed_picking_queue';
```

- Update `ROLLBACK_AND_FLAGS.md` (rollback = set the row back to 'false'; batches
  created while it was on finish correctly via per-batch dispatch).

### Change 4 — Small residuals from FIX-004/005 (fold in here)

1. `complete_batch_confirm` consolidated branch: `record_pick_to_queue(...,
   qty_picked=_src.get('qty', picked_qty))` records the REQUIRED qty. Pass the
   allocation: `qty_picked=allocated_map.get((_src['invoice_no'],
   _src['item_code']), _src.get('qty', picked_qty))` so the quick-view modal shows
   real picked numbers for short picks.
2. The cookie serialiser in `batch_picking_item` (~line 2244) still drops
   `expected_pick_pieces` from `source_items` — add it (matters for the legacy /
   cooler cookie path that remains).
3. `blueprints/cooler_picking.py:2619` (`box_reopen`) sets the cooler session
   status to `'In Progress'` — a status outside `ACTIVE_BATCH_STATUSES`
   (pre-existing, but now it makes the reopened session vanish from the Manage
   page AND lets `pick_item`'s stale-lock clearer strip the batch's item locks).
   Set it to `'picking'`.
4. `templates/batch_report.html`: still loads Bootstrap/FontAwesome from CDNs —
   switch to the local `static/css` files (printing must work offline); and the
   title/header still print `Batch {{ batch.id }}` — use
   `{{ batch.batch_number or 'BATCH-' ~ batch.id }}`.

## Schema Changes

None.

## Tests Required

| # | Scenario | Expected |
|---|----------|----------|
| C1 | Consolidated DB-backed batch, 3 items in different corridors; pick all via UI posts | Each confirm writes the item that was displayed (assert by item_code) |
| C2 | Same batch: confirm item, refresh, skip next item | Skip lands on the displayed item, not cookie index |
| C3 | Stale form post (item_code no longer queue head) | No write; redirect with warning |
| C4 | Batch created via legacy path (flag off) | Display and confirm agree (queue rows exist → both use queue) |
| C5 | Admin walking order = corridor asc; create batch spanning corridors 09/30/70 | Queue-primary display presents corridor 09 items first |
| C6 | Fresh DB boot | settings row seeded 'true'; create goes through create_batch_atomic |
| C7 | Consolidated short pick 2 of 6 across 2 invoices | quick-view shows qty_picked 2 and 0, not 4 and 2 |
| C8 | Cooler box reopen | Session status 'picking'; still listed on Manage page; locks intact |

## Verification

1. On a test DB, create a Consolidated batch of 10+ items spanning corridors with
   the default sort config. Pick it end-to-end on a phone, confirming each screen's
   item code against `batch_pick_queue` rows as they flip to `picked`. Zero
   mismatches allowed.
2. Kill the browser mid-batch, resume on another device, continue — same guarantee.
3. `SELECT value FROM settings WHERE key='use_db_backed_picking_queue'` → 'true'
   in production after deploy.
