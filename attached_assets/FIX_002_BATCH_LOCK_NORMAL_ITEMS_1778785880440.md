# FIX-002 — Hard Lock Normal Items in Standard Batches

## Priority: CRITICAL — Prevents double-picking

## The Problem

Normal (non-SENSITIVE) items in standard batches are selected via
**zone filtering** (`InvoiceItem.zone IN ['A','B','C']`) but are not
hard-locked via `InvoiceItem.locked_by_batch_id`.

This means:
- Two batches covering the same zone both show the same items
- Two pickers can simultaneously pick the same item from different batches
- The first picker to confirm "wins"; the second picker's pick is a ghost

The `batch_locking_utils.py:check_batch_conflicts()` function exists and
is called at batch creation time — so conflict detection runs, but only
at creation. If a new batch is created while an existing batch is in
progress (or if items are added to a batch after creation), the
conflict check doesn't fire again and `locked_by_batch_id` may not be set.

Cooler batches already use hard locking correctly via
`InvoiceItem.locked_by_batch_id = session.id`. This fix brings normal
batches to the same standard.

## What Changes

### Change 1 — Lock items on batch creation

In `routes_batch.py`, after the batch session is created and saved,
call `lock_items_for_batch()` immediately:

```python
# After db.session.commit() that creates the BatchPickingSession:
from batch_locking_utils import lock_items_for_batch
locked_count = lock_items_for_batch(
    batch_id=new_session.id,
    zones_list=zone_list,
    corridors_list=corridors_list if corridors_list else None,
    unit_types_list=unit_types_list if unit_types_list else None,
    invoice_nos=invoice_numbers,
)
current_app.logger.info(
    f"Batch {new_session.id} created: locked {locked_count} items"
)
```

Verify that `lock_items_for_batch` is defined in `batch_locking_utils.py`
and sets `InvoiceItem.locked_by_batch_id = batch_id`. It already exists
(line 12 of `batch_locking_utils.py`) — confirm it is being called at
creation time and not just at lock time.

### Change 2 — `get_grouped_items()` for standard batches uses lock, not zone filter

In `models.py:BatchPickingSession.get_grouped_items()`, in the
Sequential and Consolidated branches, the current filter includes:

```python
InvoiceItem.locked_by_batch_id.is_(None),  # Only unlocked items
# OR
InvoiceItem.locked_by_batch_id == self.id,
```

**The correct filter for a batch that has locked its items is:**

```python
InvoiceItem.locked_by_batch_id == self.id
```

Remove the `InvoiceItem.locked_by_batch_id.is_(None)` option from the
item query for standard batches. A batch should only see items that IT
has locked. If a batch has not yet locked items (items missing from
`locked_by_batch_id`), it should surface an error, not silently show
unlocked items.

Update `_batch_item_filters()` in `models.py`:

```python
def _batch_item_filters(self, invoice_nos, zones_list, corridors_list,
                        unit_types_list, include_picked, allow_unlocked):
    """
    allow_unlocked is now DEPRECATED for standard batches.
    Standard batches always filter by locked_by_batch_id == self.id.
    allow_unlocked=True is only used during batch creation checks.
    """
    conditions = [
        InvoiceItem.invoice_no.in_(invoice_nos),
        # Hard lock filter — only items this batch owns
        InvoiceItem.locked_by_batch_id == self.id,
    ]
    if not include_picked:
        conditions.extend([
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(
                ['not_picked', 'reset', 'skipped_pending']
            ),
        ])
    return conditions
```

Remove the zone/corridor/unit_type filters from `_batch_item_filters` —
they are no longer needed for item retrieval since the lock already
ensures only the correct items are included.

Keep zone/corridor filters ONLY in the conflict-check and batch-creation
paths where they are used to find which items to lock.

### Change 3 — Unlock items when batch is cancelled or archived

In `routes_batch.py`, in the batch cancellation and archival handlers,
confirm that `unlock_items_for_batch()` is called. This already exists
in `batch_locking_utils.py`. Verify it fires on:

- Cancel batch action
- Archive batch action
- Batch deletion (if supported)

```python
from batch_locking_utils import unlock_items_for_batch
unlock_items_for_batch(batch_id=batch.id, preserve_picked=True)
```

`preserve_picked=True` means items that have already been picked
(is_picked=True) keep their lock for audit purposes. Unpicked items
are released so other batches can pick them up.

### Change 4 — Backfill `locked_by_batch_id` for existing active batches

For batches already in production that were created before this fix,
their items have `locked_by_batch_id = NULL`. Run this once:

```python
# In the Replit shell — run once after deploying
from app import app, db
from models import BatchPickingSession, InvoiceItem, BatchSessionInvoice
from sqlalchemy import and_, text

with app.app_context():
    # Find all active standard batches
    active = BatchPickingSession.query.filter(
        BatchPickingSession.status.in_(['Created', 'In Progress']),
        BatchPickingSession.session_type == 'standard',
    ).all()

    for session in active:
        # Get all invoices in this batch
        inv_nos = [bi.invoice_no for bi in session.invoices]
        if not inv_nos:
            continue

        # Lock any unlocked items on these invoices
        # matching the batch's zones
        zones = [z.strip() for z in session.zones.split(',') if z.strip()]
        updated = db.session.query(InvoiceItem).filter(
            InvoiceItem.invoice_no.in_(inv_nos),
            InvoiceItem.zone.in_(zones),
            InvoiceItem.is_picked == False,
            InvoiceItem.locked_by_batch_id.is_(None),
        ).update(
            {InvoiceItem.locked_by_batch_id: session.id},
            synchronize_session=False
        )
        print(f"Batch {session.id} ({session.name}): locked {updated} items")

    db.session.commit()
    print("Backfill complete")
```

### Change 5 — Batch creation conflict check uses lock status

In `routes_batch.py:batch_picking_create_simple()`, the conflict check
at line ~640 already calls `check_batch_conflicts()`. Confirm this
function checks `InvoiceItem.locked_by_batch_id IS NOT NULL` (not just
zone overlap). Update `batch_locking_utils.py:check_batch_conflicts()`
if it only checks zone overlap:

```python
def check_batch_conflicts(zones_list, corridors_list, unit_types_list,
                          invoice_nos=None):
    """
    Returns items that are already locked by another active batch.
    Uses locked_by_batch_id — not zone filter — as the source of truth.
    """
    filter_conditions = [
        InvoiceItem.zone.in_(zones_list),
        InvoiceItem.is_picked == False,
        InvoiceItem.locked_by_batch_id.isnot(None),
    ]
    if corridors_list:
        filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
    if invoice_nos:
        filter_conditions.append(InvoiceItem.invoice_no.in_(invoice_nos))

    locked_items = db.session.query(
        InvoiceItem.invoice_no,
        InvoiceItem.item_code,
        InvoiceItem.locked_by_batch_id,
    ).filter(and_(*filter_conditions)).all()

    # Group by batch
    conflicts = {}
    for item in locked_items:
        bid = item.locked_by_batch_id
        if bid not in conflicts:
            session = BatchPickingSession.query.get(bid)
            conflicts[bid] = {
                'batch_id': bid,
                'batch_name': session.name if session else f'#{bid}',
                'items': [],
            }
        conflicts[bid]['items'].append({
            'invoice_no': item.invoice_no,
            'item_code': item.item_code,
        })

    return {
        'has_conflicts': len(conflicts) > 0,
        'conflicts': list(conflicts.values()),
        'total_conflicting_items': sum(
            len(c['items']) for c in conflicts.values()
        ),
    }
```

## Schema Changes

None. `locked_by_batch_id` already exists on `InvoiceItem`.

## Tests Required

Add to `tests/test_batch_locking.py`:

| # | Scenario | Expected |
|---|----------|----------|
| L1 | Create batch → items in batch zones have `locked_by_batch_id` set | ✓ |
| L2 | Create second batch covering same zone | Conflict detected, batch not created |
| L3 | Cancel batch → `locked_by_batch_id` cleared for unpicked items | ✓ |
| L4 | Cancel batch → `locked_by_batch_id` kept for picked items | ✓ |
| L5 | `get_grouped_items()` only returns items locked by this batch | ✓ |
| L6 | `get_grouped_items()` does NOT return unlocked items | ✓ |
| L7 | Cooler batches unaffected — still use `locked_by_batch_id` as before | ✓ |

## Verification

1. Create Batch A covering Zone A
2. Verify Zone A items have `locked_by_batch_id = A.id`
3. Try to create Batch B covering Zone A → conflict error shown
4. Cancel Batch A
5. Verify Zone A items have `locked_by_batch_id = NULL`
6. Create Batch B covering Zone A → succeeds, items locked by B.id
7. Pick one item in Batch B → confirm no other batch can claim it
