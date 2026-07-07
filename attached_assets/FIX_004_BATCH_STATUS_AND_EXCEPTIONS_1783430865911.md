# FIX-004 — Unify Batch Statuses & Close Exception Gaps

## Priority: HIGH — Conflict detection and item counts currently lie; short-picked customers can go unrecorded.

Source: WMDS Batch & Picking Review (7 Jul 2026), items B4, B5, B10, P8.

---

## The Problem

**Statuses.** Batch sessions actually move through:
`Created → Active (on assign) → picking (on start) → Completed`, plus
`Paused / Cancelled / Archived`. But several checks test for `'In Progress'` and
`'Assigned'`, which no code ever sets:

- `batch_locking_utils.py:266` (`check_batch_conflicts`) — conflict detection
  ignores every batch that is currently Active or being picked. Conflict warnings at
  creation are therefore unreliable.
- `routes_batch.py:1142` and `1516` (`filter_invoices_for_batch`,
  `batch_picking_create`) — `active_batch_ids` misses Active/picking batches, so
  items locked by a batch being picked RIGHT NOW are counted as available in the
  eligible-item counts shown to the admin. (The hard lock still prevents
  double-locking — the counts and warnings are what lie.)
- `main.py:259` — invoice batch badges (fixed in FIX-003 Change 1).

**Consolidated short picks.** In `complete_batch_confirm` Consolidated mode
(`routes_batch.py` 3140–3207), a short pick is allocated to the earliest invoices
first. Any invoice whose allocation is cut to zero is skipped entirely
(`if allocated_qty > 0 or is_exception`) and the loop `break`s when the quantity runs
out — so those customers get **no PickingException, no status change**, and the
screen moves on. The shortage is only reconstructable from allocation maths in the
admin report.

**Two definitions of "required quantity".** `start_batch_picking` serialises
`expected_pick_pieces` per source item; `batch_picking_item` and
`rebuild_items_from_queue` serialise only `qty`. `complete_batch_confirm` Sequential
mode reads `source_items[0].get('expected_pick_pieces', qty)` — so whether a pick is
flagged as a discrepancy depends on which page happened to build the session cache.

**Skip recycle scope.** The end-of-run skip recycle for standard batches
(`routes_batch.py` 2542–2555) selects `skipped_pending` items by invoice + zone +
corridor, NOT by `locked_by_batch_id` — a different batch's skipped item in the same
zone/invoice can be presented to this picker.

## What Changes

### Change 1 — One status constants module

Create `batch_status_constants.py` (or extend `services/batch_status.py`):

```python
# Non-terminal statuses = a batch that owns its item locks
ACTIVE_BATCH_STATUSES = ['Created', 'Active', 'picking', 'Paused']
TERMINAL_BATCH_STATUSES = ['Completed', 'Cancelled', 'Archived']
```

Replace every hardcoded list:

| File | Line | Current | Replace with |
|------|------|---------|--------------|
| `batch_locking_utils.py` | 266 | `['Created', 'In Progress', 'Assigned']` | `ACTIVE_BATCH_STATUSES` |
| `routes_batch.py` | 1142 | `['Created', 'In Progress', 'Assigned']` | `ACTIVE_BATCH_STATUSES` |
| `routes_batch.py` | 1516 | `['Created', 'In Progress', 'Assigned']` | `ACTIVE_BATCH_STATUSES` |
| `routes_batch.py` | 242 | `['Created', 'picking', 'Active', 'Paused']` | `ACTIVE_BATCH_STATUSES` |
| `routes.py` (pick_item raw SQL) | 3845 | `('Created', 'Active', 'Paused')` | add `'picking'` — a batch being picked must still block regular picking of its items |
| `main.py` | 259 | fixed in FIX-003 | — |

Note the `routes.py:3845` one is itself a latent bug this sweep catches: the
stale-lock auto-clear in `pick_item` treats a batch in status `picking` as
not-active and **clears its locks**, letting a regular picker take items out of a
batch mid-pick.

`grep -rn "'In Progress'\|'Assigned'" --include='*.py'` afterwards must return no
batch-status hits.

### Change 2 — Record exceptions for every short-changed invoice (Consolidated)

In `complete_batch_confirm`, Consolidated branch: after the allocation loop, walk the
remaining sources that received nothing and record them:

```python
remaining_qty = picked_qty
allocated_map = {}
for source in sorted_sources:
    allocated = min(remaining_qty, source['qty'])
    allocated_map[(source['invoice_no'], source['item_code'])] = allocated
    remaining_qty -= allocated

for source in sorted_sources:
    allocated_qty = allocated_map[(source['invoice_no'], source['item_code'])]
    invoice_item = InvoiceItem.query.filter_by(
        invoice_no=source['invoice_no'], item_code=source['item_code']).first()
    if not invoice_item:
        continue
    if allocated_qty != source['qty']:
        db.session.add(PickingException(
            invoice_no=source['invoice_no'], item_code=source['item_code'],
            expected_qty=source['qty'], picked_qty=allocated_qty,
            picker_username=current_user.username,
            reason=(exception_reason if is_exception else
                    f"Batch picking (consolidated): {allocated_qty} allocated, "
                    f"{source['qty']} required"),
        ))
    invoice_item.picked_qty = allocated_qty
    invoice_item.is_picked = True
    invoice_item.pick_status = ('exception'
                                if (is_exception or allocated_qty != source['qty'])
                                else 'picked')
    # ... existing BatchPickedItem upsert unchanged, but run it for ALL sources,
    # including allocated_qty == 0, so the report shows the zero allocation.
```

Key differences from today: no early `break`; zero-allocation invoices get an
exception row, `pick_status='exception'`, and a BatchPickedItem with qty 0 — so the
invoice advances out of `awaiting_batch_items` and the shortage is visible
everywhere, exactly like Sequential mode.

### Change 3 — One definition of required quantity (B10)

Make every serialiser carry `expected_pick_pieces`:

- `batch_picking_item` serialiser (`routes_batch.py` ~2329): add
  `'expected_pick_pieces': s.get('expected_pick_pieces', s['qty'])` — and when
  building from ORM items, populate it from
  `item.expected_pick_pieces or item.qty` (as `start_batch_picking` already does).
- `services/batch_picking.py:rebuild_items_from_queue` (~line 211): include
  `'expected_pick_pieces': int(r.qty_required or 0)` per source item
  (`qty_required` was already snapshotted from `expected_pick_pieces`/`qty` at
  enqueue time in `_enqueue_locked_items`).

Then `complete_batch_confirm`'s existing
`source_items[0].get('expected_pick_pieces', source_items[0]['qty'])` behaves
identically on every path.

### Change 4 — Scope the skip recycle to this batch's locks (P8)

`routes_batch.py` 2542–2555, standard-batch branch — replace the zone/corridor
filter with the lock:

```python
skipped_items = InvoiceItem.query.filter(
    InvoiceItem.locked_by_batch_id == batch_id,
    InvoiceItem.pick_status == 'skipped_pending',
).all()
```

(This mirrors what the `picking_mode == 'Cooler'` branch directly above it already
does, and what FIX-002 established: the lock, not zone filters, defines batch
membership.)

## Schema Changes

None.

## Tests Required

| # | Scenario | Expected |
|---|----------|----------|
| S1 | Batch A status `picking`; create Batch B over the same zone | Conflict warning names Batch A |
| S2 | Batch A status `Active`; filter page eligible-item counts | Items locked by A are excluded |
| S3 | Batch in `picking`; regular picker opens an invoice with its items | Items stay batch-locked; lock NOT auto-cleared |
| S4 | Consolidated pick 5 of 12 across 3 invoices (5/4/3) | Invoice 1: 5 picked; invoice 2: exception 0/4; invoice 3: exception 0/3; all three advance status |
| S5 | Resume batch from queue rebuild, item with pack-based expected pieces | Same discrepancy behaviour as fresh start |
| S6 | Two batches, same zone, each with a skipped item | End-of-run recycle shows only own batch's skip |
| S7 | grep for `'In Progress'` / `'Assigned'` in batch-status context | No hits |

## Verification

1. Create batch A, assign, start picking. From another browser create batch B over
   the same zone — the conflict warning must name batch A.
2. While A is `picking`, open one of its invoices as a regular picker — the batch
   items must show as locked, not become pickable.
3. Run a Consolidated batch, short-pick a multi-invoice item; check every affected
   invoice shows an exception and none is stuck in `awaiting_batch_items`.
4. Skip an item, exit, reopen the batch from a new device (queue rebuild path),
   finish the run — the skipped item returns with the correct expected quantity.
