# Task #30 — Cooler Picking Integration with Regular Order Picking

## What & Why

The Phase 5 cooler picking infrastructure (cooler queue, boxes, labels, manifests, driver overlay) is fully built and tested in production. However, it only triggers when a batch is created via `services/batch_picking.py`. Operations predominantly use **regular order picking** (one picker per invoice via `/picker/dashboard` → `/picker/invoice/<no>/item`), where the cooler routing is never invoked.

Result: SENSITIVE items in regular-picked invoices end up in the normal pick list, exposed to room temperature for the duration of picking — exactly what the cooler workflow is designed to prevent.

This task bridges the two systems. SENSITIVE items must be automatically extracted into the cooler queue when their invoice joins a route, regardless of which picking flow handles the rest of the order. The route's warehouse manager confirms sequencing when ready, the cooler picker works the queue, and items end up in correctly labelled cooler boxes for the driver.

The work has four parts plus one supporting feature:

1. **Phase 1 — Auto-lock on route attachment** (cold-chain protection)
2. **Phase 2 — Lock Cooler Sequencing gate** (warehouse manager commitment)
3. **Phase 3 — Late addition handling** (post-lock invoice arrivals)
4. **Phase 4 — Cancellation and route-removal flows** (post-pick changes)
5. **Box catalogue + estimator** (size types, capacity tracking, pre-pick estimation)

All work builds on existing Phase 5 infrastructure. No changes to the cooler picking blueprint's core flow, no changes to PDF rendering, no changes to the driver overlay structure. New code is additive.

---

## Done Looks Like

- A SENSITIVE item attached to any active route is **automatically** locked from regular picking and added to the cooler queue with `delivery_sequence = NULL`. No manual button click required. The picker opening that invoice cannot accidentally pick it.
- The cooler picking screen displays unsequenced items in a separate **"Unsequenced"** section, and the cooler picker cannot start picking from this section until sequencing is confirmed.
- Route detail page shows a **"Lock Cooler Sequencing"** button (admin / warehouse manager only) when there are unsequenced cooler queue rows for the route. Clicking it stamps `delivery_sequence` onto each row using the live `RouteStop.seq_no`. After lock, items move from "Unsequenced" to the sortable, pickable section.
- An invoice attached **after** sequencing-lock has its SENSITIVE items added to a **"Late Additions"** section visible to the cooler picker. The manager resolves each late add manually: assign a real stop (4.5 between stop 4 and stop 5), then merge into the sequenced list, OR treat as exception with its own dedicated cooler box.
- Exception boxes carry `box_type = 'exception_late_add'` and print labels with a distinctive **"⚠ LATE ADDITION ⚠"** marker. Driver overlay shows them in a separate visual group.
- Cancelled or removed invoices trigger a route-detail alert with three resolution actions: mark for return-on-truck, reopen-and-remove, or cancel-entire-box. The action chosen is audited.
- A new **`cooler_box_types`** catalogue table is administrable via a settings page. Each cooler box references one type. Box types specify internal dimensions, fill-efficiency factor, and optional max weight.
- A new **`estimate_cooler_boxes(route_id)`** service runs at any stage (pre-sequencing, post-sequencing, mid-pick) and returns: total volume, total weight, suggested box allocation across types, confidence level, list of items missing dimensions.
- The route detail page shows the estimate in three modes: rough (no sequencing), medium (sequenced), good (live during picking).
- The cooler picking screen shows real-time per-box capacity ("Box 1: 22L / 60L · 37% full") so the picker knows when to start a new box.
- A new admin report lists items missing dimensions, sorted by frequency on routes, so data-quality work can be prioritised.
- All existing tests pass. New tests cover all five sub-features.

---

## Phase 1 — Auto-Lock On Route Attachment

### Hook point

The single integration point is `services/__init__.py:attach_invoices_to_stop()`. Currently this function creates `RouteStopInvoice` rows linking an invoice to a stop. Extend it to also:

1. Detect SENSITIVE items in the attached invoices
2. Lock them from regular picking
3. Add them to the cooler queue

### Implementation

After the existing `db.session.commit()` at line 118, add a new function call:

```python
from services.cooler_route_extraction import extract_sensitive_for_route_stop_invoices

# Extract SENSITIVE items into cooler queue. Idempotent — items
# already in the cooler queue are not re-added. Honours
# summer_cooler_mode_enabled flag (extraction skipped when off).
if attached:
    extract_sensitive_for_route_stop_invoices(attached)
```

### New service file: `services/cooler_route_extraction.py`

```python
"""Phase 6: bridge between regular order picking and Phase 5 cooler queue.

When invoices are attached to a route, their SENSITIVE items must be
extracted into batch_pick_queue (pick_zone_type = 'cooler') and locked
from regular order picking via InvoiceItem.locked_by_batch_id. This
prevents room-temperature exposure of cool-chain items in the normal
picking flow.

The extraction is idempotent: re-running for the same invoice does not
create duplicate queue rows. delivery_sequence is left NULL until
warehouse manager clicks 'Lock Cooler Sequencing' on the route.
"""
def extract_sensitive_for_route_stop_invoices(rsi_list):
    """
    Extract SENSITIVE items from each RouteStopInvoice's invoice into
    the cooler queue, idempotently.

    Skipped if summer_cooler_mode_enabled is OFF.

    Returns dict: {extracted, already_present, missing_dimensions, picked_warning}
    """
```

### Cooler session per route

The cooler queue rows need a `batch_session_id`. Create one **special cooler session per route**, named `COOLER-ROUTE-<route_id>`, with type `cooler_route`.

Schema additions to `batch_picking_sessions`:
- `session_type` column (default `'standard'`, also accepts `'cooler_route'`)
- Existing columns (status, started_by, etc.) work as-is

The cooler session is created on first extraction for a route and reused for all subsequent extractions for that same route.

### Items already picked at extraction time

If an `InvoiceItem` already has `is_picked = true` when extraction runs, **do not lock it and do not create a queue row**. Instead:

- Add an `ActivityLog` entry: `cooler.warning_already_picked`
- Surface a route-detail warning: *"⚠ N SENSITIVE items were already picked before cooler workflow activated. Move them to cooler manually."*

This catches the edge case where the flag was off when picking started, or where extraction was somehow delayed.

### Items missing dimensions

If a SENSITIVE item has any dimension as NULL, the extraction still locks the item and adds it to the queue (cold-chain safety is non-negotiable). The item is also added to a `cooler_data_quality_log` so the admin report can surface it.

### Tests required

| # | Scenario | Expected |
|---|----------|----------|
| P1.1 | SENSITIVE item attached to route, flag ON | Queue row created, `pick_zone_type='cooler'`, `delivery_sequence=NULL`, `InvoiceItem.locked_by_batch_id` set |
| P1.2 | Same invoice attached twice (re-attached after move) | Single queue row, no duplicates |
| P1.3 | Flag OFF | No extraction, items remain pickable in regular flow |
| P1.4 | Already-picked SENSITIVE item | No queue row; warning logged; ActivityLog entry created |
| P1.5 | Item missing item_length | Queue row created; data_quality_log entry created |
| P1.6 | Multiple invoices attached in same call | All processed, single cooler session reused |
| P1.7 | Invoice moved from Route A to Route B | Old queue rows detached from A's session, new rows on B's session |

---

## Phase 2 — Lock Cooler Sequencing Gate

### Behaviour

Before sequencing is locked, cooler queue rows for the route have `delivery_sequence = NULL`. The cooler picking screen shows them in a "Unsequenced" section with a **disabled pick button** and the message: *"Sequencing not yet locked by warehouse manager. Cannot start picking until route is finalised."*

When a warehouse manager (or admin) clicks **"Lock Cooler Sequencing"** on the route detail page:

1. System queries `RouteStop.seq_no` for each unsequenced cooler queue row's invoice via `RouteStopInvoice` join
2. Stamps the seq_no onto `batch_pick_queue.delivery_sequence` for each row
3. Records `cooler_session.sequence_locked_at` and `cooler_session.sequence_locked_by`
4. ActivityLog: `cooler.sequence_locked` with item count
5. Cooler picking screen reloads — items now in sortable, pickable section
6. Button label changes to **"Re-lock Cooler Sequencing"** (re-clickable if route is later resequenced)

### Re-lock behaviour

The sequence is **always queried live** via the JOIN to `RouteStop` — `delivery_sequence` on the queue row is essentially a snapshot. If the route is resequenced after lock:

- Cooler picker still sees current sequence (live from JOIN)
- The snapshot on `batch_pick_queue.delivery_sequence` becomes stale
- A warning appears on route detail: *"Route resequenced after cooler lock. Re-lock to refresh snapshot."*
- Re-locking updates the snapshot

This is **Option C** from our discussion — gentlest middle ground. Lock holds, warning surfaces, manual re-lock available.

### New permission

Add to `services/permissions.py`:
- `cooler.lock_sequencing` — included in admin (`*`) and `warehouse_manager` (`cooler.*`). Pickers do not have it.

### Route detail UI

A new **"Cooler Status"** card on `/routes/<id>`:

```
COOLER STATUS                                     [ Lock Cooler Sequencing ]
14 SENSITIVE items extracted from 8 invoices
Sequencing: NOT LOCKED · pick blocked

[ View cooler picking screen → ]
```

After lock:

```
COOLER STATUS                                     [ Re-lock Cooler Sequencing ]
14 SENSITIVE items extracted from 8 invoices
Sequencing: LOCKED at 08:30 by Maria
Items picked: 0 / 14
Boxes: 0 (no boxes created yet)
Estimated need: 1× Large + 1× Medium

[ View cooler picking screen → ]
```

### Tests required

| # | Scenario | Expected |
|---|----------|----------|
| P2.1 | Lock with 5 items, all sequences known | All 5 rows updated with seq_no |
| P2.2 | Lock with 1 invoice missing RouteStopInvoice | Row stays NULL, warning shown |
| P2.3 | Picker tries to pick from unsequenced section | 403 / pick action disabled |
| P2.4 | Re-lock after resequence | Updates all rows; ActivityLog records both locks |
| P2.5 | Picker without `cooler.lock_sequencing` perm | Cannot click button (admin / WM only) |
| P2.6 | Route is resequenced after lock | Live sort still correct; warning surfaces; re-lock available |

---

## Phase 3 — Late Addition Handling

### Detection

After sequencing lock, any new `attach_invoices_to_stop()` call that adds SENSITIVE items to this route is flagged on the cooler queue rows. Schema addition to `batch_pick_queue`:

- New column: `added_after_sequencing_lock BOOLEAN DEFAULT false`

The extraction service checks: *if cooler session has `sequence_locked_at IS NOT NULL`, mark new rows with `added_after_sequencing_lock = true`*.

### Cooler picking screen

Three sections shown vertically:

```
SEQUENCED — ready to pick (locked at 08:30 by Maria)
  Stop 1 · CHO-0010 · Restaurant Athens
  ...
  Stop 6 · CHO-0040 · Cafe Syntagma

LATE ADDITIONS — ⚠ added after sequencing locked
  ⊘ Pending stop placement · CHO-0010 · New Customer (added 10:15)
    [ Merge into sequenced list ]  [ Treat as exception ]
  ⊘ Pending stop placement · CHO-0030 · New Customer (added 10:15)
    [ Merge into sequenced list ]  [ Treat as exception ]
```

### Resolution actions

Per item in Late Additions section:

**"Merge into sequenced list"** — only possible if the invoice has been resequenced to a real stop. Stamps the current `seq_no` onto the queue row, sets `added_after_sequencing_lock = false`, item moves to the main sequenced list.

**"Treat as exception"** — leaves `added_after_sequencing_lock = true`. The cooler picker creates a dedicated `box_type = 'exception_late_add'` box for these items. Box label and driver overlay show the exception status distinctly.

### Exception box behaviour

Schema additions to `cooler_boxes`:
- `box_type` column — VARCHAR(50), default `'standard'`. Accepts `'standard'`, `'exception_late_add'`, `'exception_runner'` (the last for state-3 cases — see Phase 4)
- `notes` column — TEXT for free-text per-box notes (e.g. "Stop 4.5 only — single customer")

PDF labels for exception boxes have distinctive header:

```
┌────────────────────────────────────────┐
│ ROUTE 12 — Box EXC-1                    │
│                                          │
│ ⚠ LATE ADDITION — single stop only ⚠    │
│                                          │
│ Stop 4.5                                 │
│ Customer: New Customer Ltd               │
│ 2 items                                  │
│ Added 10:15 by Maria                     │
└────────────────────────────────────────┘
```

Driver overlay (`cooler_driver_view_enabled = true`) renders exception boxes in a separate visual group with a distinct colour or icon.

### Tests required

| # | Scenario | Expected |
|---|----------|----------|
| P3.1 | Invoice attached after sequence-lock | New rows have `added_after_sequencing_lock = true` |
| P3.2 | Late add resequenced into route | "Merge into sequenced" action moves it back to main list |
| P3.3 | Late add treated as exception | New cooler box created with `box_type = 'exception_late_add'` |
| P3.4 | Exception box PDF label | Contains "LATE ADDITION" header text |
| P3.5 | Driver overlay with exception box | Renders in separate visual section |

---

## Phase 4 — Cancellation and Route-Removal Flows

### Cancellation flow

When `Invoice.status` transitions to `cancelled` and the invoice has cooler queue rows:

1. **Cooler queue rows** marked `status = 'cancelled'` (terminal status). `is_order_ready()` for this invoice now passes vacuously.
2. **`cooler_box_items` rows** flagged with `cancelled_at` timestamp (new column). They remain in the box physically until manager resolves.
3. **Route detail alert** appears: *"⚠ Cancelled order INV-12345 has 4 SENSITIVE items in Box 2. Action required."*
4. **Three resolution buttons** on the alert:
   - **Mark for return-on-truck** — sets `cooler_box_items.action = 'return_on_truck'`. Driver manifest shows these distinctly.
   - **Reopen box and remove items** — opens reopen flow (see below). Items are physically removed; `cooler_box_items` rows deleted. Box re-closed.
   - **Cancel entire box** — sets `cooler_boxes.status = 'cancelled'`. Used when most of the box was for the cancelled invoice.

### Reopen box action

New endpoint: `POST /cooler/box/<id>/reopen`. Permission: `cooler.manage_boxes`. Schema additions to `cooler_boxes`:

- `reopened_by`, `reopened_at`, `reopen_reason` (text), `reopen_count` (int, defaults 0)

Reopening is allowed only when `cooler_boxes.status = 'closed'`. After reopen, status returns to `'open'`. The manager modifies contents, then closes again — a second close generates a fresh `closed_at`. Both actions are audited.

### Route-removal flow

When an invoice is moved from Route A to Route B (`attach_invoices_to_stop` is called for Route B with an invoice that was on Route A):

1. **Cooler queue rows for the invoice** — handling depends on Route B's state:
   - If Route B has no cooler session yet OR Route B's session is not yet sequence-locked → rows are detached from Route A's session and reattached to Route B's session
   - If Route B's session IS sequence-locked → rows are reattached to Route B's session and marked `added_after_sequencing_lock = true` (treated as a Phase 3 late addition on Route B)

2. **`cooler_box_items` rows on Route A** — flagged for physical transfer:
   - New column on `cooler_box_items`: `transfer_status` — VARCHAR(20), accepts `'pending_transfer'`, `'transferred'`, NULL
   - Rows for the moved invoice get `transfer_status = 'pending_transfer'`
   - Route A box label still shows them but with a "PENDING TRANSFER" marker

3. **Route A alert**: *"INV-12345 moved to Route B. 4 SENSITIVE items in Box 2 must be transferred."*
4. **Route B alert**: *"INV-12345 received from Route A. 4 SENSITIVE items pending physical transfer."*

5. **Manual physical transfer flow**:
   - Manager opens Route A Box 2 (Reopen action)
   - Clicks **"Mark items as transferred-out"** for the affected items — sets `transfer_status = 'transferred'`, deletes the `cooler_box_items` rows for those items
   - Closes Route A Box 2 again
   - Items now appear in Route B's cooler queue (as pending-pick)
   - Cooler picker on Route B picks them into a Route B box as normal

### Hard cutoff after dispatch

When `Shipment.status = 'DISPATCHED'`:
- `attach_invoices_to_stop()` for that route's stops is rejected with HTTP 409 Conflict
- Error message: *"Route 12 already dispatched. Assign this invoice to today's next route or tomorrow's route."*

This prevents the system from trying to handle post-dispatch changes.

### Tests required

| # | Scenario | Expected |
|---|----------|----------|
| P4.1 | Invoice cancelled, items in open box | Alert shown, three resolution buttons |
| P4.2 | "Mark for return-on-truck" action | `cooler_box_items.action = 'return_on_truck'`, driver manifest reflects |
| P4.3 | "Reopen box and remove" action | Box reopened, items deleted, box re-closed, full audit |
| P4.4 | "Cancel entire box" action | Box status = `cancelled`, all items cleaned up |
| P4.5 | Invoice moved to Route B (B unlocked) | Queue rows detach from A, attach to B, no flag |
| P4.6 | Invoice moved to Route B (B locked) | Queue rows on B marked `added_after_sequencing_lock = true` |
| P4.7 | Cancellation after dispatch | Pre-dispatch flow rejected; post-dispatch is paperwork-only |
| P4.8 | Attach-invoice-to-route after DISPATCHED | HTTP 409, descriptive error |

---

## Phase 5 — Box Catalogue and Estimator

### `cooler_box_types` table (new)

```sql
CREATE TABLE cooler_box_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    internal_length_cm NUMERIC(8, 2) NOT NULL,
    internal_width_cm NUMERIC(8, 2) NOT NULL,
    internal_height_cm NUMERIC(8, 2) NOT NULL,
    internal_volume_cm3 NUMERIC(12, 2) NOT NULL,  -- computed at insert
    fill_efficiency NUMERIC(4, 3) NOT NULL DEFAULT 0.75,  -- 0.00–1.00
    max_weight_kg NUMERIC(8, 2),  -- optional weight limit
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER DEFAULT 0,  -- display order in dropdowns
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cooler_boxes
    ADD COLUMN IF NOT EXISTS box_type_id INTEGER
        REFERENCES cooler_box_types(id) ON DELETE SET NULL;
```

### Default seed data (in migration script)

```python
DEFAULT_BOX_TYPES = [
    {"name": "Small", "internal_length_cm": 30, "internal_width_cm": 20, "internal_height_cm": 15,
     "fill_efficiency": 0.70, "sort_order": 1,
     "description": "Single-stop or small pickup orders"},
    {"name": "Medium", "internal_length_cm": 40, "internal_width_cm": 30, "internal_height_cm": 25,
     "fill_efficiency": 0.75, "sort_order": 2,
     "description": "Standard route box, 3-5 stops"},
    {"name": "Large", "internal_length_cm": 50, "internal_width_cm": 40, "internal_height_cm": 30,
     "fill_efficiency": 0.78, "sort_order": 3,
     "description": "Long routes, 6+ stops"},
]
```

These are starting defaults. The admin UI lets operators add, edit, deactivate.

### Admin UI page: `/admin/cooler-box-types`

Permission: `admin` only. Standard CRUD page following existing admin page patterns. Fields: name, description, dimensions, fill efficiency, max weight, sort order, active.

Validation:
- Dimensions must be positive integers
- Fill efficiency between 0.10 and 1.00
- Max weight optional, must be positive if set
- Name must be unique

### `estimate_cooler_boxes()` service function

```python
def estimate_cooler_boxes(route_id):
    """
    Estimate cooler box requirements for a route at any stage.

    Returns dict:
      mode                — "rough" | "medium" | "good"
      total_volume_cm3    — sum of (item_volume × qty) for items with dimensions
      total_weight_kg     — same for weight (None if any item missing weight)
      item_count          — total SENSITIVE items
      items_with_dims     — count with all 3 dimensions
      items_missing_dims  — count missing at least one dimension
      data_quality_pct    — items_with_dims / item_count * 100
      data_quality_label  — "good" / "limited" / "insufficient"
      box_estimates       — list of allocation suggestions
      caveats             — list of human-readable warnings
    """
```

### Mode determination

- **rough** — no sequencing locked yet; estimate based on total volume across all items
- **medium** — sequencing locked, no boxes created yet; estimate based on total volume + stop range hints
- **good** — boxes exist; show actual current capacity per box

### Allocation algorithm (first-fit decreasing)

```python
def _allocate_volume_to_boxes(total_volume, box_types):
    """
    Greedy first-fit decreasing.
    NOT optimal bin-packing — heuristic that produces useful answers
    for typical wholesale cooler items.
    """
    sorted_types = sorted(box_types, key=lambda t: -t.effective_capacity)
    remaining = total_volume
    allocation = []
    for bt in sorted_types:
        if remaining <= 0:
            break
        n = int(remaining // bt.effective_capacity)
        if n > 0:
            allocation.append({
                "box_type_id": bt.id,
                "box_type_name": bt.name,
                "count": n,
                "filled_cm3": n * bt.effective_capacity,
            })
            remaining -= n * bt.effective_capacity
    if remaining > 0:
        # Need one more box — pick smallest type that fits
        smallest_fitting = next(
            (bt for bt in reversed(sorted_types)
             if bt.effective_capacity >= remaining),
            sorted_types[0]  # if remaining > all types, use largest
        )
        allocation.append({
            "box_type_id": smallest_fitting.id,
            "box_type_name": smallest_fitting.name,
            "count": 1,
            "filled_cm3": remaining,
        })
    return allocation
```

### Multiple suggestion variants

In addition to the primary allocation (first-fit decreasing), provide alternatives:

- **All-largest:** "Use only Large boxes" — fewest total boxes
- **All-medium:** "Use only Medium boxes" — most flexibility per stop
- **Optimal-mix:** the FFD result above

Three options surfaced to the operator. They pick based on operational preference.

### Data quality computation

```
items_with_dims = count of SENSITIVE items where length/width/height all not null
data_quality_pct = items_with_dims / total_sensitive_items * 100

label:
  > 80%  → "good"     (estimate trusted)
  50–80% → "limited"  (estimate may be 10-20% off)
  < 50%  → "insufficient" (use as rough indicator only)
```

### Caveats list

The function returns human-readable warnings:
- *"3 items missing dimensions — estimate excludes them"*
- *"No items have weight data — weight check disabled"*
- *"Item CHO-0010 dimension is unrealistically large (item_length = 200cm) — verify"*

### Surfacing in UI

**On route detail page** (within Cooler Status card):
```
ESTIMATED BOX REQUIREMENTS
14 SENSITIVE items, 47L total
Quality: GOOD (12/14 items have dimensions)

Suggestions:
  Option 1: 1× Large + 1× Medium     (recommended)
  Option 2: 2× Medium                (more boxes, more granular)
  Option 3: 1× Large                 (fewer boxes, may overflow)

Caveats: 2 items missing dimensions — actual need may be 5-10% higher
```

**On cooler picking screen** (per box, when box exists):
```
Box 1 — open · Stops 1-4 · Medium (30L cap)
  Used: 22L / 30L (73%)
  Items: 8
  ⚠ Approaching capacity — consider closing and starting Box 2
```

**On admin "Items missing dimensions" report**:
```
ITEMS MISSING DIMENSIONS — most-frequently routed
SKU       Name              Routes (last 30d)   Action
CHO-0010  Yogurt 500g       12                  [ Edit ]
CHO-0030  Cheese 250g       8                   [ Edit ]
...
```

Sortable, filterable. Edit links to existing item-management page.

### Tests required

| # | Scenario | Expected |
|---|----------|----------|
| P5.1 | Box type creation with valid data | Row inserted, computed volume correct |
| P5.2 | Box type with negative dimensions | Validation error |
| P5.3 | Box type with fill_efficiency > 1.0 | Validation error |
| P5.4 | Estimate with 0 items | Returns zero-state, no division by zero |
| P5.5 | Estimate with all items having dimensions | `data_quality_label = "good"`, no caveats |
| P5.6 | Estimate with 60% items having dimensions | `data_quality_label = "limited"`, caveat present |
| P5.7 | Estimate suggests at least one box for any non-zero volume | Always returns at least one box if total_volume > 0 |
| P5.8 | Estimate uses largest available box first | FFD result has correct box ordering |
| P5.9 | Live capacity shows correct percentage per box | Computed from `cooler_box_items` join with `ps_items_dw` |
| P5.10 | Items-missing-dimensions report | Lists items with NULL dimensions sorted by route frequency |

---

## Out Of Scope

- No changes to Phase 5 cooler picking blueprint core flow (route_picking, box_create, box_close, label, manifest).
- No changes to PDF rendering structure (just header text changes for exception boxes).
- No changes to the `is_order_ready()` core logic (just terminal status additions for cancelled).
- No changes to driver overlay structure (just visual styling for exception boxes).
- No 3D bin-packing optimisation (using FFD heuristic only).
- No automatic resequencing of routes (manager triggers via existing flow).
- No changes to PS365 sync for item dimensions (existing data is used as-is).
- No notifications, alerts, or scheduled reports based on cooler events.

---

## Implementation Notes

### Sequencing of work

Implement in this order to allow incremental testing:

1. **Phase 1 first** (extraction service + auto-lock). Test in isolation with extraction unit tests.
2. **Phase 2 next** (lock sequencing button). Cooler picker can now actually pick.
3. **Phase 5 (box catalogue and estimator) before Phases 3/4** — the estimator and box types are foundational for late-add and exception-box flows.
4. **Phase 3** (late additions) — uses Phase 5's `box_type` column.
5. **Phase 4** (cancellation/removal) — uses Phase 5's box catalogue and Phase 3's exception-box machinery.

This sequence delivers value incrementally and tests build on each other.

### Feature flag

This entire task is gated by the existing `summer_cooler_mode_enabled` flag. When OFF, none of this code runs — `extract_sensitive_for_route_stop_invoices()` short-circuits at the start, and the route detail UI does not show the Cooler Status card.

No new feature flag is introduced. Operators turn cooler mode on via the existing flag, which now controls both batch-picking and regular-picking cooler routing.

### Logging discipline

Every cooler-related action logs at INFO level with a consistent prefix:
- `cooler.extract` — Phase 1 events
- `cooler.lock_sequencing` — Phase 2
- `cooler.late_add` — Phase 3
- `cooler.cancel`, `cooler.transfer` — Phase 4
- `cooler.estimate` — Phase 5

This makes operational debugging straightforward via grep on log files.

### Existing test compatibility

All 47 existing Phase 5 cooler tests in `tests/test_phase5_cooler_picking.py` must continue to pass without modification. The new code is additive; the existing cooler picking flow is untouched.

---

## Closeout

When complete, provide:

1. All Phase 1–5 tests passing (each phase has a numbered test list above)
2. Existing tests still passing: `pytest -q tests/test_phase5_cooler_picking.py tests/test_phase4_batch_refactor.py tests/test_override_ordering_pipeline.py`
3. Manual verification per phase:
   - **Phase 1**: attach an invoice with SENSITIVE items to a route in dev, verify cooler queue rows are created and the item is locked from regular picking
   - **Phase 2**: click Lock Cooler Sequencing, verify the cooler picking screen unlocks
   - **Phase 3**: attach a late invoice, verify it appears in Late Additions section, test both resolution actions
   - **Phase 4**: cancel a picked invoice, test all three resolution actions; move an invoice between routes, verify physical-transfer flow
   - **Phase 5**: create the three default box types, verify estimate works for a route in all three modes (rough/medium/good), check the items-missing-dimensions report
4. Screenshots of: route detail Cooler Status card, cooler picking screen with all three sections, exception box label, items-missing-dimensions report
5. Append assumption entries to `ASSUMPTIONS_LOG.md` for any autonomous decisions (default fill_efficiency values, FFD vs other allocation algorithms, exception label exact wording, etc.)

---

## Critical Constraints

- The `summer_cooler_mode_enabled` flag must remain the master switch — when OFF, no extraction, no UI, no behaviour changes.
- Cold-chain protection (Phase 1 auto-lock) is non-negotiable. SENSITIVE items must NEVER be pickable by a regular picker when cooler mode is on, regardless of which trigger ran.
- All migrations must be additive and idempotent (existing Phase 5 pattern).
- The `extract_sensitive_for_route_stop_invoices()` function must be idempotent — re-running for the same invoice must not create duplicate queue rows.
- All state-changing actions must produce ActivityLog entries with consistent activity_type prefixes (`cooler.*`).
- The `is_order_ready()` function must continue to gate dispatch correctly — cancelled invoices vacuously ready, late additions blocking until resolved.
- Production must NOT be modified during development. Test in development with copied production data.
- Permission `cooler.lock_sequencing` must be added to admin (`*`) and warehouse_manager (`cooler.*`) only.
- The hard DISPATCHED cutoff in Phase 4 must reject `attach_invoices_to_stop()` cleanly with HTTP 409, not crash.
