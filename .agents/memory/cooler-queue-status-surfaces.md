---
name: New batch_pick_queue status fan-out
description: Adding a non-terminal queue status (e.g. skipped_pending) requires updating every cancel/unassign/extraction path, not just the picking read surfaces.
---

# Adding a non-terminal batch_pick_queue status

When you introduce a NEW non-terminal value to `batch_pick_queue.status`
(e.g. cooler `skipped_pending` = collect-later), the obvious surfaces are
the picking read/write paths (status buckets, outstanding counts,
pack-complete checks, box_close). The non-obvious — and dangerous —
surfaces are the **teardown** paths that previously handled the status you
replaced:

- `services/batch_picking.py` `cancel_batch`: only cancels `'pending'`
  rows and releases their InvoiceItem locks. A new non-terminal status left
  out here survives cancellation AND keeps its lock → the invoice is parked
  at `awaiting_batch_items` forever.
- `services/cooler_route_extraction.py` invoice unassign / route-move:
  three DELETEs filter `status = 'pending'` (queue rows + planned
  `cooler_box_items`). A new non-terminal status left out survives
  unassignment, still blocks `route_warehouse_readiness` (which has no
  batch/invoice-route filter), and the recycle loop re-presents an item
  whose invoice is no longer on the route.

**Why:** these paths key off the specific status string, so a status that
means "outstanding work" must be treated like `'pending'` everywhere work
can be torn down, or rows become immortal.

**How to apply:** when adding such a status, grep ALL of `cancel`,
unassign/extraction, and readiness paths for `status = 'pending'` /
`status IN (...)` and for the OLD status you are replacing; add the new
status wherever `'pending'` is treated as live work. `batch_pick_queue.status`
is varchar(20) with no check constraint, so new values are schema-safe —
the risk is purely missed code surfaces.
