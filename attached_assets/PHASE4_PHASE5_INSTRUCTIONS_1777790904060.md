# WMDS Development Batch — Phase 4 (Batch Picking Refactor) + Phase 5 (Cooler Picking, Reduced Scope)

**To:** Replit
**Status:** Continuation of the WMDS Development Batch. Phases 1–3 complete and signed off. Phase "4-Job-Runs-UI" complete. This batch covers the **original brief's Phase 4 (Batch Picking Refactor) and Phase 5 (Cooler Picking, reduced scope)** — the two operational phases that were not yet built.
**Priority:** Production-critical changes. Highest-risk work in the entire WMDS Development Batch. Phased rollout, feature flags, and operations sign-off are non-negotiable.

---

## CONTEXT FOR THIS BATCH

The WMDS Development Batch's original Phase 4 (Batch Picking Refactor) and Phase 5 (Cooler Picking) are the two phases that change warehouse operations. Everything built so far has been infrastructure (job_runs, permissions, scheduler hooks). This batch changes the picking workflow itself — the path that warehouse pickers follow every day.

**Production safety boundary:** This batch must be built behind feature flags (mostly already seeded in Phase 1). All flags must remain `false` in production until each phase is verified end-to-end in development with operations sign-off.

The original brief's Section 7 ("Driver Mode invariant") still applies — driver workflow, login, route assignment, delivery confirmation, GPS tracking must remain unchanged. The cooler driver-loading view is an additive overlay shown only when `cooler_driver_view_enabled = true`.

---

## ABSOLUTE PRODUCTION SAFETY BOUNDARY

Before doing any code work, confirm the production state of these flags:

```sql
SELECT key, value FROM settings
WHERE key IN (
    'use_db_backed_picking_queue',
    'allow_legacy_session_picking_fallback',
    'batch_claim_required',
    'enable_consolidated_batch_picking',
    'maintenance_mode',
    'summer_cooler_mode_enabled',
    'cooler_picking_enabled',
    'cooler_labels_enabled',
    'cooler_driver_view_enabled'
)
ORDER BY key;
```

### Required Production State Throughout This Batch

| Flag | Required Value Until Phase Verified | Notes |
|------|-------------------------------------|-------|
| `use_db_backed_picking_queue` | `false` | RED. Stays off in production until Phase 4 is verified end-to-end and operations approves the flip with a drain workflow. |
| `allow_legacy_session_picking_fallback` | `true` | YELLOW. Keep on as the safety net during the transition window. |
| `batch_claim_required` | `false` | YELLOW. Default behaviour throughout this batch; admins can flip after Phase 4 stable. |
| `enable_consolidated_batch_picking` | `false` | YELLOW. Hidden by default; flip only on explicit warehouse decision. |
| `maintenance_mode` | `normal` | YELLOW. Only flips to `draining` during a coordinated deploy. |
| `summer_cooler_mode_enabled` | `false` | RED. Master cooler switch. Stays off until Phase 5 verified and pilot-route plan approved. |
| `cooler_picking_enabled` | `false` | RED. Cooler picking UI/queue creation. Stays off until pilot route ready. |
| `cooler_labels_enabled` | `false` | GREEN but dependent on `cooler_picking_enabled`. |
| `cooler_driver_view_enabled` | `false` | GREEN but dependent on `cooler_picking_enabled`. |

### Rules

1. **Do not enable any flag in production during this batch.** All testing happens in development.
2. **Do not modify the seeded defaults** in `services/settings_defaults.py` for these flags — keep them all `false`.
3. **Do not deploy code to production** until each phase is signed off in development.
4. **Do not delete any existing data or table.** All migrations are additive.
5. **Do not change Driver Mode core workflow.** The cooler driver-loading view is an additive overlay only.

---

## AUTONOMOUS EXECUTION RULES (UNCHANGED FROM ORIGINAL BRIEF)

You may work autonomously on this batch within the scope below. Do not pause for routine clarification; instead use the assumptions log.

### Routine vs Blocking Decisions

**Continue without asking** for: column naming, helper function placement, UI cosmetics, migration ordering, test fixture design, log message wording, file/module organisation, any additive change with a feature flag protecting it, any reversible decision documented in `ASSUMPTIONS_LOG.md`.

**Stop and ask before** any of: deleting data or tables, changing existing column types/constraints, changing PS365 write-back behaviour, changing username PK structure, changing driver login or route assignment workflow, requiring production downtime for a migration, irreversible migration, security-sensitive change not described in this brief, major business workflow not described in this brief, expanding scope beyond what this document specifies.

### Question Policy

- **Routine:** end-of-phase checkpoint, bundled.
- **Non-blocking:** end-of-week checkpoint, bundled.
- **Blocking:** ask immediately with rationale for why no other work can proceed.
- **Production-risk:** ask immediately.

### Assumptions Log Format

Append to `ASSUMPTIONS_LOG.md` for every autonomous decision:

```markdown
## ASSUMPTION-NNN: [short title]
**Date:** YYYY-MM-DD
**Phase:** Phase 4 (Batch Picking) | Phase 5 (Cooler Picking)
**Files affected:** [list]
**Decision made:** [what was implemented]
**Reason:** [why this choice]
**Safer alternative considered:** [what was rejected and why]
**Feature flag / rollback:** [flag name, rollback steps]
**Reversibility:** High / Medium / Low
**Recommendation if you disagree:** [how to revert]
```

---

## STRUCTURE OF THIS BATCH

| Phase | Title | Risk | Approx effort |
|-------|-------|------|---------------|
| 4 | Batch Picking Refactor | RED — highest in entire WMDS batch | Larger |
| 5 | Cooler Picking (reduced scope) | RED — new operational workflow | Smaller (depends on Phase 4) |

Phase 4 must be code-complete and verified before Phase 5 begins. Phase 5 depends on Phase 4's DB-backed `batch_pick_queue` table.

---

# PART 1 — PHASE 4: BATCH PICKING REFACTOR

## 4.1 — Objective

Move the picking queue from the current Flask-session-based approach to a **DB-backed queue** so picking state survives browser refresh and server restart. Make batch creation **atomic** so two concurrent users cannot pick the same items into different batches. Replace **hard delete** with **cancel/archive**. Add **claim batch** flow so admin/warehouse staff can pick through Picker Mode without losing audit accuracy.

## 4.2 — Schema Migration (Additive)

Create migration `update_phase4_batch_picking_schema.py` callable from `main.py` boot in the same pattern as `update_phase1_foundation_schema.py`. All operations idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).

### New Table: `batch_pick_queue`

```sql
CREATE TABLE IF NOT EXISTS batch_pick_queue (
    id BIGSERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES batch_picking_session(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL,
    invoice_no VARCHAR(50) NOT NULL,
    item_code VARCHAR(50) NOT NULL,
    item_name VARCHAR(200),
    location VARCHAR(100),
    zone VARCHAR(50),                         -- existing batch zone
    wms_zone VARCHAR(50),                     -- snapshot of DwItem.wms_zone at queue creation (Phase 5 hook)
    corridor VARCHAR(10),
    expected_qty NUMERIC(12, 3) NOT NULL,
    picked_qty NUMERIC(12, 3) DEFAULT 0,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    -- pending | picked | skipped | exception | cancelled
    picked_by VARCHAR(64),
    picked_at TIMESTAMP WITH TIME ZONE,
    skipped_reason TEXT,
    source_allocation_json JSONB,
    pick_zone_type VARCHAR(20) NOT NULL DEFAULT 'normal',
    -- normal | cooler   (cooler set in Phase 5)
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_batch_status
  ON batch_pick_queue (batch_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_invoice
  ON batch_pick_queue (invoice_no);
CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_zone_type
  ON batch_pick_queue (pick_zone_type);
CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_pending
  ON batch_pick_queue (batch_id) WHERE status = 'pending';
```

### Existing Table Adjustments (additive only)

If `batch_picking_session` does not already have these fields, add them as nullable:

```sql
ALTER TABLE batch_picking_session
  ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(64),
  ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP WITH TIME ZONE,
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP WITH TIME ZONE,
  ADD COLUMN IF NOT EXISTS cancelled_by VARCHAR(64),
  ADD COLUMN IF NOT EXISTS cancelled_reason TEXT,
  ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP WITH TIME ZONE;
```

Do **not** alter or drop any existing column. Do **not** rename `batch_picking_session`. Do **not** touch `invoice_items.locked_by_batch_id` semantics — the existing locking mechanism is preserved.

## 4.3 — Status Helpers

Create `services/batch_status.py` with these helpers used everywhere status is checked:

```python
ACTIVE_STATUSES = {'created', 'assigned', 'in_progress', 'paused'}
TERMINAL_STATUSES = {'completed', 'cancelled', 'archived'}
EDITABLE_STATUSES = {'created', 'assigned', 'paused'}
CANCELLABLE_STATUSES = {'created', 'assigned', 'in_progress', 'paused'}

def is_active_batch_status(status): ...
def is_terminal_batch_status(status): ...
def can_edit_batch(status): ...
def can_cancel_batch(status): ...
```

Replace scattered hard-coded status checks across `routes_batch.py` and other batch-handling code with these helpers. Do not change the actual status values currently in use — the helpers wrap the existing values.

## 4.4 — Atomic Batch Creation

Move batch creation into a single transaction in `services/batch_picking.py`:

```python
def create_batch_atomic(filters, created_by, mode='sequential'):
    """
    Atomic creation. Either everything commits, or nothing does.
    """
    with db.engine.begin() as conn:
        # 1. Validate filters
        # 2. Find eligible invoice_items (zone + corridor + status filters)
        # 3. Detect conflicts: any item already locked by another active batch
        #    -> raise BatchConflict with the conflicting batch id
        # 4. INSERT batch_picking_session row -> get batch_id
        # 5. INSERT batch_session_invoice rows
        # 6. UPDATE invoice_items SET locked_by_batch_id = batch_id WHERE ...
        # 7. INSERT batch_pick_queue rows (one per item, with snapshot of wms_zone)
        # 8. INSERT activity_log entry: batch.created
        # All within `with db.engine.begin() as conn:` so any failure rolls back.
    return batch_id
```

Custom exception `BatchConflict` carries the conflicting `batch_id` and item codes. Caller surfaces a clear UI message: "These items are already assigned to batch #N. Cancel that batch or choose different items."

## 4.5 — Lock Lifecycle

Document the chosen lock lifecycle in code comments and `ASSUMPTIONS_LOG.md`:

- **Active batch:** items locked by `locked_by_batch_id`. Picked items remain locked (audit history preserved).
- **Cancellation:** unlock all `batch_pick_queue` rows where `status = 'pending'`. Picked rows stay locked (audit). Set queue status `cancelled` for unpicked rows.
- **Completion:** decision needed (assumption-required): keep all picked items locked forever (audit), OR release locks for items that are downstream-shipped. Recommended default: **keep locks** until shipment dispatch event releases them via existing PS365/shipment flow (no change to existing release path).

Do **not** invent a new release path. The existing release semantics (whatever it is today) stays.

## 4.6 — Cancel/Archive (Replaces Hard Delete)

Add `services/batch_picking.py :: cancel_batch(batch_id, cancelled_by, reason)`:

- Set `batch_picking_session.status = 'cancelled'`, `cancelled_at`, `cancelled_by`, `cancelled_reason`.
- Update `batch_pick_queue` rows:
  - `pending` rows → `cancelled`
  - picked / skipped / exception rows → unchanged (audit history)
- Unlock `invoice_items` for cancelled queue rows: `UPDATE invoice_items SET locked_by_batch_id = NULL WHERE invoice_no, item_code in (cancelled queue rows)`.
- INSERT activity_log entry: `batch.cancelled` with reason.

Replace existing hard-delete path on `/batch/delete/<id>` to call `cancel_batch` instead, with audit reason "manual delete via admin UI". Hard delete remains available **only** for empty/test batches with `picking.delete_empty_batch` permission (new key, admin-only). Document the chosen behaviour in `ASSUMPTIONS_LOG.md`.

## 4.7 — Claim Batch / Pick as Myself

Add UI flow and permissions:

- New permission key: `picking.claim_batch` (already in brief; add to `ROLE_PERMISSIONS` if missing — picker has it; warehouse_manager via `picking.*`; admin via `*`).
- Admin/warehouse_manager users in Picker Mode see a "Claim batch" button next to unassigned/assigned-to-someone-else batches.
- Clicking "Claim batch":
  - If `batch_claim_required = true` AND user is not the original assignee → require explicit click before any pick action.
  - If `batch_claim_required = false` (default) → click is optional but updates `claimed_by` / `claimed_at` for audit.
- After claim, all subsequent picks log the **real** clicking user's username in activity_log + `batch_pick_queue.picked_by`. Never log the original assignee for picks done by someone else.

## 4.8 — Sequential vs Consolidated

Default mode is `sequential` (one batch picked end-to-end before next). Consolidated mode is hidden when `enable_consolidated_batch_picking = false` (default). When the flag is on, the existing/legacy consolidated UI re-appears unchanged from current behaviour.

## 4.9 — Drain Workflow

Add admin UI at `/admin/batch/drain-status` and a `services/maintenance/drain.py` helper:

- `maintenance_mode = 'draining'` setting flips the system into drain mode.
- While `draining`:
  - New batch creation disabled for non-admin users.
  - Banner shown to active pickers: "System maintenance scheduled. Please complete current batch within 30 minutes."
  - After 30 minutes, force-pause any still-running batches (status → `paused`); admin alert sent.
- Drain helper queries:
  ```sql
  SELECT id, status, assigned_to, last_activity_at
  FROM batch_picking_session
  WHERE status IN ('in_progress', 'assigned');
  ```
- Restoring `maintenance_mode = 'normal'` re-enables batch creation and removes the banner.

## 4.10 — Migration Reconciliation UI

Add admin-only UI at `/admin/batch/orphaned-locks`:

- Lists `invoice_items` rows where `locked_by_batch_id` references a batch that is in a terminal status (`cancelled`, `archived`) or no longer exists.
- Provides bulk unlock action (`UPDATE invoice_items SET locked_by_batch_id = NULL WHERE id IN (...)`).
- Each unlock writes an `activity_log` entry: `batch.orphan_unlock` with admin username.

## 4.11 — Feature Flag Behaviour

When `use_db_backed_picking_queue = false` (default in production):

- New batch creation continues to use the existing Flask-session-based path.
- The `batch_pick_queue` table is created and indexed but receives **zero writes** from the existing path.
- Code paths that read the queue must check the flag and fall through to the legacy session reader when off.

When `use_db_backed_picking_queue = true`:

- New batches go through `create_batch_atomic` and write to `batch_pick_queue`.
- Existing batches created before the flip continue to be readable through the legacy path until they complete or are cancelled.
- Both paths coexist while `allow_legacy_session_picking_fallback = true`.

When both `use_db_backed_picking_queue = true` AND `allow_legacy_session_picking_fallback = false`:

- Legacy reader is removed from rotation.
- This is the **post-stabilization state** and must not be flipped during this batch.

## 4.12 — Required Phase 4 Tests

Add to `tests/test_phase4_batch_picking.py`:

### Atomic Operations & Concurrency

| # | Scenario | Expected |
|---|----------|----------|
| P4-01 | Create a batch with 50 items via `create_batch_atomic` | 1 batch row + 50 queue rows |
| P4-02 | Create a batch where 1 item is already locked by another active batch | `BatchConflict` raised; 0 batch rows; 0 queue rows |
| P4-03 | Two simulated transactions creating batches with overlapping items | Exactly one succeeds; other gets `BatchConflict` |
| P4-04 | Create a batch with 500 items | Atomic creation < 5 seconds |
| P4-05 | Create-then-rollback (force exception step 7) | All inserts rolled back; `invoice_items.locked_by_batch_id` reset |

### Status Helpers

| # | Scenario | Expected |
|---|----------|----------|
| P4-06 | `is_active_batch_status('in_progress')` | True |
| P4-07 | `is_active_batch_status('cancelled')` | False |
| P4-08 | `is_terminal_batch_status('completed')` | True |
| P4-09 | `can_edit_batch('completed')` | False |
| P4-10 | `can_cancel_batch('archived')` | False |
| P4-11 | `can_cancel_batch('paused')` | True |

### Picking Resume

| # | Scenario | Expected |
|---|----------|----------|
| P4-12 | Pick 5 items, simulate session loss, fetch queue from DB | 5 items show `picked`, queue continues at item 6 |
| P4-13 | Pick mid-batch, restart Flask app | Same picker reopens, queue resumes from DB state |

### Cancel / Lock Lifecycle

| # | Scenario | Expected |
|---|----------|----------|
| P4-14 | Active batch with 10 unpicked items: `cancel_batch(...)` | All 10 unlock; activity_log has `batch.cancelled` |
| P4-15 | Batch with 5 picked + 5 unpicked: `cancel_batch(...)` | 5 unpicked unlock; 5 picked remain in audit |
| P4-16 | Cancelled batch's queue rows: 5 `picked`, 5 `cancelled` | Direct DB inspection confirms |

### Claim Flow

| # | Scenario | Expected |
|---|----------|----------|
| P4-17 | Admin (Picker Mode) picks without claiming when `batch_claim_required = true` | UI blocks pick action with "Click Claim batch first" |
| P4-18 | Admin clicks "Claim batch" | `claimed_by`, `claimed_at` set; subsequent picks pass |
| P4-19 | Admin picks 3 items after claim | activity_log has admin's username on all 3 entries |

### Drain Workflow

| # | Scenario | Expected |
|---|----------|----------|
| P4-20 | Set `maintenance_mode = 'draining'` while picker is active | Banner shows; new batch creation blocked for non-admin |
| P4-21 | After simulated 30-min timeout | Active batch status → `paused`; alert recorded |
| P4-22 | Restore `maintenance_mode = 'normal'` | Banner cleared; batch creation re-enabled |

### Migration Reconciliation

| # | Scenario | Expected |
|---|----------|----------|
| P4-23 | Insert orphan: `invoice_items.locked_by_batch_id = 99999` (non-existent) | `/admin/batch/orphaned-locks` lists the row |
| P4-24 | Bulk unlock from UI | Row's `locked_by_batch_id = NULL`; activity_log has `batch.orphan_unlock` |

### Feature Flag Coexistence

| # | Scenario | Expected |
|---|----------|----------|
| P4-25 | `use_db_backed_picking_queue = false`: create new batch | Legacy session path used; no `batch_pick_queue` rows |
| P4-26 | `use_db_backed_picking_queue = true`: create new batch | DB-backed path used; queue rows present |
| P4-27 | Mid-flight flag flip while picking is in progress | Document behaviour in `ASSUMPTIONS_LOG.md`; existing batches finish on their original path |

### Audit Trail

| # | Scenario | Expected |
|---|----------|----------|
| P4-28 | Each of: created, assigned, claimed, started, item_picked, item_skipped, exception, force_completed, cancelled, archived | activity_log row with corresponding action and username |

## 4.13 — Phase 4 Definition of Done

Phase 4 is complete when:

- [ ] Migration `update_phase4_batch_picking_schema.py` deployed (additive, idempotent)
- [ ] `services/batch_status.py` helpers in place; legacy hard-coded checks migrated
- [ ] `services/batch_picking.py :: create_batch_atomic` operational and tested
- [ ] `services/batch_picking.py :: cancel_batch` operational and tested; hard-delete path replaced
- [ ] Claim flow UI + audit trail in place
- [ ] `services/maintenance/drain.py` operational
- [ ] `/admin/batch/orphaned-locks` UI present and tested
- [ ] All 28 Phase 4 tests pass: `pytest -q tests/test_phase4_batch_picking.py`
- [ ] Override-ordering pipeline regression still passes: `pytest -q tests/test_override_ordering_pipeline.py`
- [ ] `use_db_backed_picking_queue` remains `false` in production
- [ ] Phase 4 closeout entry added to `PHASE_TEST_RESULTS.md` with file:line evidence per test
- [ ] `ASSUMPTIONS_LOG.md` updated with all Phase 4 decisions
- [ ] `ROLLBACK_AND_FLAGS.md` Phase 4 section added with rollback paths
- [ ] Architect code review completed; any Critical/Severe issues fixed before sign-off
- [ ] Driver Mode invariant preserved: zero edits to `templates/driver/*` and driver routes

After Phase 4 is signed off, Phase 5 may begin. **Do not start Phase 5 work in parallel.**

---

# PART 2 — PHASE 5: COOLER PICKING (REDUCED SCOPE)

## 5.1 — Reduced Scope Confirmation

The original brief's Phase 5 included capacity tracking, returns inventory, idle detection, type management, and time/temperature tracking. **All five are explicitly out of scope for this batch** by operational decision.

### What IS in scope

- Master setting `summer_cooler_mode_enabled` separates `wms_zone = 'SENSITIVE'` items from normal picking.
- Dedicated cooler picking queue using the Phase 4 `batch_pick_queue` table with `pick_zone_type = 'cooler'`.
- Cooler boxes as real DB rows: open → close → assigned to a route.
- Cooler box label printing (PDF + QR).
- Cooler driver/loading view (additive overlay, gated by flag).
- Order readiness rule: orders with mixed normal + cooler items not marked ready until both queues complete.

### What is NOT in scope (deferred to a future batch)

- Cooler box capacity tracking (no weight/volume limits)
- Cooler box returns / physical box inventory
- Cooler box idle detection (no auto-flag for boxes left open)
- Cooler box type management (no Small/Medium/Large with different limits)
- Cooler box time/temperature tracking

The flags `cooler_capacity_warn_percent`, `cooler_capacity_block_percent`, `cooler_box_idle_timeout_minutes` from the original brief Section 14 are **not implemented**. If they were seeded in Phase 1, leave them as-is — they are reserved namespace for a future batch.

## 5.2 — Schema Migration

Create `update_phase5_cooler_picking_schema.py`:

```sql
CREATE TABLE IF NOT EXISTS cooler_boxes (
    id BIGSERIAL PRIMARY KEY,
    route_id INTEGER REFERENCES shipments(id) ON DELETE SET NULL,
    delivery_date DATE NOT NULL,
    box_no INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    -- open | closed | loaded | delivered | cancelled
    first_stop_sequence NUMERIC(10, 2),
    last_stop_sequence NUMERIC(10, 2),
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    closed_by VARCHAR(64),
    closed_at TIMESTAMP WITH TIME ZONE,
    label_printed_at TIMESTAMP WITH TIME ZONE,
    notes TEXT,
    UNIQUE (route_id, delivery_date, box_no)
);

CREATE INDEX IF NOT EXISTS idx_cooler_boxes_route_date
  ON cooler_boxes (route_id, delivery_date);
CREATE INDEX IF NOT EXISTS idx_cooler_boxes_status
  ON cooler_boxes (status);

CREATE TABLE IF NOT EXISTS cooler_box_items (
    id BIGSERIAL PRIMARY KEY,
    cooler_box_id BIGINT NOT NULL REFERENCES cooler_boxes(id) ON DELETE CASCADE,
    invoice_no VARCHAR(50) NOT NULL,
    customer_code VARCHAR(50),
    customer_name VARCHAR(200),
    route_stop_id INTEGER REFERENCES route_stop(route_stop_id) ON DELETE SET NULL,
    delivery_sequence NUMERIC(10, 2),
    item_code VARCHAR(50) NOT NULL,
    item_name VARCHAR(200),
    expected_qty NUMERIC(12, 3) NOT NULL,
    picked_qty NUMERIC(12, 3) DEFAULT 0,
    picked_by VARCHAR(64),
    picked_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) NOT NULL DEFAULT 'assigned',
    -- assigned | picked | removed
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cooler_box_items_box
  ON cooler_box_items (cooler_box_id);
CREATE INDEX IF NOT EXISTS idx_cooler_box_items_invoice
  ON cooler_box_items (invoice_no);
CREATE INDEX IF NOT EXISTS idx_cooler_box_items_route_stop
  ON cooler_box_items (route_stop_id);
```

Confirmed against the existing codebase: `DwItem.wms_zone` is a real column at `models.py:1938` with valid values `MAIN`, `SENSITIVE`, `SNACKS`, `CROSS_SHIPPING`. No new column needed for the trigger.

## 5.3 — Cooler Trigger

When `summer_cooler_mode_enabled = true`:

- Items where `DwItem.wms_zone = 'SENSITIVE'` are excluded from the normal picking queue.
- They are added to the cooler picking queue using `batch_pick_queue.pick_zone_type = 'cooler'` AND `batch_pick_queue.wms_zone = 'SENSITIVE'` snapshot.
- Snapshot the `wms_zone` value at queue creation time. Do not re-evaluate during picking even if `DwItem.wms_zone` changes.

When `summer_cooler_mode_enabled = false` (default):

- Normal picking includes all items (including SENSITIVE) — pre-batch behaviour preserved.

## 5.4 — Cooler Picking UI

Add new blueprint `blueprints/cooler_picking.py` with routes:

- `GET /cooler/route-list` — list routes with delivery dates that have cooler items pending. Gated by `@require_permission('cooler.pick')`.
- `GET /cooler/route/<route_id>/<delivery_date>` — cooler picking screen for that route. Shows queue sorted by:
  1. Route / shipment
  2. RouteStop.seq_no (delivery sequence)
  3. Customer code
  4. Invoice number
  5. Item code
- `POST /cooler/box/create` — create a new cooler box. Body: `{route_id, delivery_date, box_no}`. Returns `{cooler_box_id, status}`. Idempotent on `(route_id, delivery_date, box_no)` UNIQUE constraint — return existing box id if collision.
- `POST /cooler/box/<id>/assign-item` — assign a queue item to a cooler box. Body: `{queue_item_id, picked_qty}`. Updates queue row status → `picked`, inserts `cooler_box_items` row. **No capacity check** (out of scope).
- `POST /cooler/box/<id>/remove-item` — remove an item from an open box. Reverses the assignment.
- `POST /cooler/box/<id>/close` — close an open box. Sets `status = 'closed'`, `closed_by`, `closed_at`. Computes and writes `first_stop_sequence` / `last_stop_sequence` from the assigned items.
- `GET /cooler/box/<id>/label` — returns PDF label (4×6 thermal default, A4 fallback via `?size=a4`). Includes route, date, box number, stop range, "SENSITIVE ITEMS / KEEP COOL" warning, QR code with `cooler_box_id`.
- `GET /cooler/box/<id>/manifest` — returns PDF manifest (contents list sorted by delivery sequence).
- `GET /cooler/route/<route_id>/<delivery_date>/manifest` — combined manifest for the whole route.

All routes gated by appropriate permissions:

- `cooler.pick` — picker access to cooler picking UI
- `cooler.manage_boxes` — create / close / remove items / cancel
- `cooler.print_labels` — label and manifest printing

Add to `ROLE_PERMISSIONS`:

- `warehouse_manager` already has `cooler.*` (verified in current code).
- `picker` should gain `cooler.pick` (NEW). Document in `ASSUMPTIONS_LOG.md`.
- Admin via `*` already covers everything.

## 5.5 — PDF Generation

Use the existing PDF stack already used elsewhere in the codebase (most likely WeasyPrint or ReportLab — check existing report generation modules and reuse). Do not introduce a new library.

### Cooler Box Label (4×6 thermal, 100mm × 150mm)

Layout:
- Top: route number (large), delivery date
- Middle: large "BOX N" with box number; stop range "Stops 3 to 7" (using `first_stop_sequence` / `last_stop_sequence`)
- Lower middle: large "SENSITIVE ITEMS / KEEP COOL" warning
- Bottom: QR code encoding `cooler_box_id` (so a future scan-to-load workflow can lift it)
- A4 fallback (`?size=a4`) renders the same content centered on a portrait A4 page

### Cooler Box Manifest (A4)

Table sorted by delivery sequence:

| Stop seq | Customer | Invoice | Item code | Item name | Qty |
|----------|----------|---------|-----------|-----------|-----|

Header: route, delivery date, box number, generation timestamp.

### Combined Route Manifest (A4)

All cooler boxes for the route, grouped by box, with the same per-box manifest format. Used by warehouse loading.

## 5.6 — Driver / Loading View

When `cooler_driver_view_enabled = true`:

- Existing driver loading view (`templates/driver/route_load.html` or equivalent) gets a new section: "Cooler boxes for this route".
- For each cooler box, show: box number, stop range, item count, status (closed / loaded).
- Each cooler box row links to the box's manifest PDF.
- This is a **read-only additive section**. Driver workflow does not change. No new buttons that change driver actions.

When `cooler_driver_view_enabled = false` (default):

- Driver view unchanged from current behaviour.

## 5.7 — Order Readiness Rule

An order with both normal and cooler items must not be marked fully ready until **both** queues complete (or are exceptioned).

Implementation:

- Add `services/order_readiness.py :: is_order_ready(invoice_no)` returning True only when:
  - All normal queue items for the invoice are in terminal status (`picked` / `skipped` / `exception` / `cancelled`), AND
  - All cooler queue items for the invoice are in terminal status (`picked` / `skipped` / `exception` / `cancelled`), AND
  - All cooler boxes containing items for the invoice are in `closed` status (or beyond)

When `summer_cooler_mode_enabled = false`, the cooler check is a no-op (no cooler queue rows).

Existing order-status logic that decides "ready for shipment" must call `is_order_ready` instead of its current check. Identify the existing call sites and refactor; do not duplicate logic.

## 5.8 — Exception Handling

Support these exception flows in the cooler picking UI:

- Item unavailable → mark queue row `status = 'exception'` with reason; order does not block waiting for it.
- Partial quantity picked → `picked_qty < expected_qty`, mark `status = 'picked'` with note.
- Substitute item → out of scope for this batch; flag and document as a future enhancement.
- Manual move from normal queue to cooler queue → admin-only action with `cooler.manage_boxes`. Audit log entry.
- Manual move from cooler queue back to normal → admin-only action. Audit log entry.

## 5.9 — Required Phase 5 Tests

Add to `tests/test_phase5_cooler_picking.py`:

### Mode Toggle

| # | Scenario | Expected |
|---|----------|----------|
| P5-01 | `summer_cooler_mode_enabled = false`: order with normal + SENSITIVE items, run normal picking | All items in normal queue |
| P5-02 | `summer_cooler_mode_enabled = true`: same order | SENSITIVE items in cooler queue (`pick_zone_type = 'cooler'`); normal queue excludes them |
| P5-03 | Snapshot `wms_zone = 'SENSITIVE'` is on queue row | Direct DB inspection confirms |
| P5-04 | Mid-pick reclassification: change `DwItem.wms_zone` after queue created | Queue row keeps original snapshot value |

### Cooler Picking Queue

| # | Scenario | Expected |
|---|----------|----------|
| P5-05 | Cooler queue sorted by stop sequence | First row has lowest `RouteStop.seq_no` |
| P5-06 | Cooler queue contains only SENSITIVE items | No `pick_zone_type = 'normal'` rows in cooler view |

### Cooler Box Lifecycle

| # | Scenario | Expected |
|---|----------|----------|
| P5-07 | Create cooler box for a route | Row inserted, `status = 'open'`, `box_no = 1` |
| P5-08 | Assign 3 items to box | 3 `cooler_box_items` rows; queue rows → `picked` |
| P5-09 | Close box | `status = 'closed'`, `closed_by`, `closed_at` set; `first_stop_sequence` / `last_stop_sequence` computed |
| P5-10 | Try to assign item to closed box | 400 error, clear message |
| P5-11 | Remove item from open box | Row deleted; queue row reverts to `pending` |
| P5-12 | Try to remove item from closed box | 400 error |
| P5-13 | Idempotent box creation: same `(route, date, box_no)` twice | Second call returns existing box id |

### Order Readiness

| # | Scenario | Expected |
|---|----------|----------|
| P5-14 | Order: 3 normal + 2 cooler. Pick 3 normal only | `is_order_ready = false` |
| P5-15 | Same order: pick + assign 2 cooler to box (still open) | `is_order_ready = false` |
| P5-16 | Same order: close the box | `is_order_ready = true` |
| P5-17 | Order with all SENSITIVE: complete cooler only | `is_order_ready = true` |
| P5-18 | Order with no SENSITIVE, `summer_cooler_mode_enabled = true`: complete normal | `is_order_ready = true` (cooler check is no-op) |
| P5-19 | Order with no SENSITIVE, `summer_cooler_mode_enabled = false`: complete normal | `is_order_ready = true` |

### Labels & Manifests

| # | Scenario | Expected |
|---|----------|----------|
| P5-20 | `GET /cooler/box/<id>/label` for a closed box | PDF returned with route, date, box no, stop range, KEEP COOL warning, QR encoding `cooler_box_id` |
| P5-21 | `GET /cooler/box/<id>/label?size=a4` | A4 PDF with same content |
| P5-22 | `GET /cooler/box/<id>/manifest` | PDF table sorted by delivery sequence |
| P5-23 | `GET /cooler/route/<rid>/<date>/manifest` | Combined manifest with all boxes for the route |

### Driver View

| # | Scenario | Expected |
|---|----------|----------|
| P5-24 | `cooler_driver_view_enabled = true`: driver loads route with cooler items | Cooler box section visible |
| P5-25 | `cooler_driver_view_enabled = false`: same route | No cooler box section; driver view unchanged |
| P5-26 | Driver mobile API: existing endpoints unchanged | Regression baseline passes |

### Permissions

| # | Scenario | Expected |
|---|----------|----------|
| P5-27 | Admin / warehouse_manager / picker (with `cooler.pick`) hit `/cooler/route-list` | 200 |
| P5-28 | Picker without `cooler.pick` hits `/cooler/route-list` | 403 |
| P5-29 | Driver hits `/cooler/route-list` | 403 |
| P5-30 | crm_admin hits `/cooler/route-list` | 403 |

### Exceptions

| # | Scenario | Expected |
|---|----------|----------|
| P5-31 | Mark cooler queue row as `exception` | Order readiness treats as terminal; box close still permitted for other items |
| P5-32 | Manual move from normal to cooler | activity_log entry; queue row moved with snapshot of `wms_zone` |
| P5-33 | Manual move from cooler to normal | activity_log entry; reverse |

## 5.10 — Phase 5 Definition of Done

Phase 5 is complete when:

- [ ] Migration `update_phase5_cooler_picking_schema.py` deployed (additive, idempotent)
- [ ] `blueprints/cooler_picking.py` operational with all routes
- [ ] PDF labels and manifests render correctly (4×6 thermal + A4 fallback)
- [ ] Driver loading view additive overlay works behind `cooler_driver_view_enabled`
- [ ] `services/order_readiness.py :: is_order_ready` operational and called from existing ready-for-shipment checks
- [ ] All 33 Phase 5 tests pass: `pytest -q tests/test_phase5_cooler_picking.py`
- [ ] All Phase 4 tests still pass: `pytest -q tests/test_phase4_batch_picking.py`
- [ ] Override-ordering pipeline regression still passes
- [ ] All cooler flags remain `false` in production
- [ ] Phase 5 closeout entry in `PHASE_TEST_RESULTS.md`
- [ ] `ASSUMPTIONS_LOG.md` updated with all Phase 5 decisions including the explicit out-of-scope items in 5.1
- [ ] `ROLLBACK_AND_FLAGS.md` Phase 5 section added
- [ ] `KNOWN_GAPS.md` updated with deferred items (capacity, returns inventory, idle detection, type management, time/temperature) — each with severity and recommended future fix
- [ ] Architect code review completed; any Critical/Severe issues fixed
- [ ] Driver Mode invariant preserved

---

# PART 3 — DELIVERABLES

When both phases are complete, provide a single closeout response with:

## 3.1 — Production Flag State Report

Current production values for all 9 batch + cooler flags. Confirm all remain `false` (with `allow_legacy_session_picking_fallback = true`).

## 3.2 — Updated Documentation

- `PHASE_TEST_RESULTS.md` — Phase 4 + Phase 5 closeout entries
- `ASSUMPTIONS_LOG.md` — every Phase 4 + Phase 5 decision
- `ROLLBACK_AND_FLAGS.md` — Phase 4 + Phase 5 sections
- `KNOWN_GAPS.md` — 5 cooler items deferred (with rationale)
- `replit.md` — Phase 4 + Phase 5 sections matching the existing format
- `SCHEDULING.md` — confirm no new scheduled jobs were added (this batch should not add any)

## 3.3 — Architect Code Review Report

For each phase, the architect's review notes plus the resolution of any Critical / Severe issues found.

## 3.4 — Test Results Summary

| Test file | Test count | Pass |
|-----------|-----------|------|
| `tests/test_phase4_batch_picking.py` | 28 | / 28 |
| `tests/test_phase5_cooler_picking.py` | 33 | / 33 |
| `tests/test_override_ordering_pipeline.py` | 1 | / 1 |
| All previous test files (regression) | (unchanged) | (unchanged) |

## 3.5 — Outstanding Risks

Anything identified during build or test that has not been mitigated. For each: severity, file:line if applicable, recommended next step.

---

# PART 4 — FINAL ACCEPTANCE RULE

This batch is accepted when:

- Both Phase 4 and Phase 5 Definitions of Done are met
- All test suites pass (Phase 4: 28, Phase 5: 33, plus regression baselines)
- Production flag state report confirms zero unintended flips
- Documentation up to date across all 6 docs
- Architect review signed off
- Owner sign-off recorded in `PHASE_TEST_RESULTS.md`

The actual flag flips in production are **separate operator decisions** and out of scope for this batch. When operations is ready to flip, the order is:

1. Phase 4 first: drain workflow → flip `use_db_backed_picking_queue = true` → observe 7 days → flip `allow_legacy_session_picking_fallback = false`
2. Phase 5 second, on a pilot route: flip `summer_cooler_mode_enabled = true` and `cooler_picking_enabled = true` together for the pilot route only → observe → expand to all routes

The pilot-route plan is a separate operational document. This batch is responsible for making both phases code-ready and verified, not for executing the production rollout.

---

**End of instructions.**
