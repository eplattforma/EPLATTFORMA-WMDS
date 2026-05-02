# WMDS DEVELOPMENT BATCH — FINAL REPLIT BRIEF

**Status:** Ready for Replit implementation  
**Version:** 3.0 — focused final version  
**Pre-reviewed for completeness:** Yes  

**Instruction to Replit:**  
This batch has been pre-reviewed for completeness. Implementation should follow the pre-send clarifications and ground rules as written. Do not simplify, defer, or materially change items without explicit approval.

---

## 1. OBJECTIVE

Improve WMDS in the following operational areas:

1. User access control, roles, permissions, and user modes.
2. Central Job Runs & Sync Logs with retention and stale-job visibility.
3. Removal of old Replenishment MVP from the normal menu without deleting dependencies.
4. Batch picking reliability through DB-backed queue, safer locking, and better audit trail.
5. Summer Cooler / Sensitive Picking using existing item WMS zone `SENSITIVE`, with real cooler boxes, labels, contents list, and driver/loading information.

This is **one controlled operational improvement project**, not unrelated small fixes.

---

## 2. NON-NEGOTIABLE GROUND RULES

1. Do not change the existing Driver Mode core workflow in this batch.
2. Do not perform a full `username` primary-key migration in this batch.
3. Do not delete historical operational tables unless explicitly instructed.
4. Backend permission enforcement is required. Menu hiding alone is not acceptable.
5. Atomic operations are required for batch creation and item locking. Partial state is not acceptable.
6. Scheduled jobs must use explicit IANA timezone strings, for example `Africa/Cairo`, `Europe/Nicosia`, or `Europe/Athens`.
7. Use additive/reversible migrations wherever practical.
8. If unsure, choose the safer/audit-friendly option and document the assumption in the handover instead of pausing repeatedly.
9. Stop and ask only for decisions that may cause data loss, security exposure, destructive migrations, production downtime, or major workflow change.

---

## 3. AUTONOMOUS EXECUTION RULES

To avoid repeated pauses, Replit should continue implementation without asking for approval on minor product, UI, naming, or technical decisions that can be reasonably inferred from this brief.

### Decision Rules

1. If two reasonable implementation options exist, choose the safer/audit-friendly option.
2. If a choice affects historical records, preserve and soft-disable/archive. Do not delete.
3. If a choice affects access control, choose stricter backend enforcement.
4. If a choice affects picking state, choose DB-backed state over session/browser state.
5. If a choice affects labels/printing, default to browser-generated PDF with A4 fallback.
6. If a choice affects old functionality, keep backward compatibility unless explicitly told to remove it.
7. If a route/module is no longer wanted, hide/disable it first; do not delete code/tables unless approved.
8. If a setting is needed, add it with a safe default and document it.
9. If a migration is needed, make it additive and reversible where practical.
10. Continue with the conservative option and list assumptions in handover notes.

### Allowed Without Further Approval

- Adding additive DB columns/tables required by this brief.
- Adding safe defaults to settings.
- Adding backend permission decorators.
- Hiding the legacy Replenishment MVP menu item.
- Adding disabled-module redirect/message for `/replenishment-mvp`.
- Creating DB-backed queue tables for batch picking/cooler picking.
- Creating cooler box and cooler box item tables.
- Adding PDF label generation.
- Adding admin preview/cleanup actions for log retention.
- Adding indexes for performance.
- Refactoring shared helper functions only where needed and with tests.

### Must Ask Before Proceeding

- Dropping tables or deleting historical data.
- Changing Driver Mode behavior or driver API authentication.
- Migrating `username` primary key to a new `user_id` primary key.
- Removing `blueprints/replenishment_mvp.py` entirely.
- Disabling existing production workflows not mentioned in this brief.
- Making destructive/non-additive DB migrations.
- Changing PS365 posting/write behavior beyond the requested cost update validation.
- Introducing downtime during warehouse operating hours.

### Progress Expectation

- Continue module by module without waiting for approval on every small decision.
- Use an assumptions log in the handover instead of pausing.
- Group genuine questions into checkpoint messages rather than many small interruptions.

Question batching policy:

```text
Routine questions: collect for end-of-phase checkpoint.
Non-blocking questions discovered mid-phase: collect for weekly checkpoint.
Blocking questions that prevent all useful work: ask immediately, but include why it is blocking and what work can continue meanwhile.
Production-risk questions: ask immediately if they involve data loss, destructive migration, security exposure, production downtime, PS365 write behavior, username PK change, or Driver Mode core workflow change.
```

Checkpoint format:

```text
1. Questions requiring decision
2. Recommended answer for each
3. Safe default if no answer is received
4. Feature flag / rollback path affected
5. Work that continued meanwhile
```

Suggested checkpoints:

1. After migrations/settings.
2. After permissions/modes.
3. After batch queue refactor.
4. After cooler picking.
5. Before production deployment.

### Assumptions Log Format

Replit must maintain an assumptions log using this format:

```markdown
## ASSUMPTION-001: [Short Title]

**Date:** YYYY-MM-DD  
**Phase:** Phase number/name  
**Files affected:** file1.py, file2.html  
**Decision made:** What was decided  
**Reason:** Why this was the safest/reasonable choice  
**Safer alternative considered:** Alternative and why it was not chosen now  
**Feature flag / rollback:** Relevant flag or rollback option  
**Reversibility:** High / Medium / Low  
**Recommendation if user disagrees:** How to disable/revert/change later
```

### Definition of Done Per Phase

Each phase is complete only when:

1. Required feature flags/settings for that phase exist and are documented.
2. New code is covered by relevant tests or documented manual validation.
3. Existing critical flows smoke-test successfully.
4. No blocking assumptions remain unresolved.
5. Rollback path for the phase is documented.
6. Replit provides files changed, migrations, screenshots/notes, test results, and known limitations.

Phase 1 specific Definition of Done:

- All foundation infrastructure deployed with high-risk behavior disabled.
- `ROLLBACK_AND_FLAGS.md` created.
- `SCHEDULING.md` created or updated if schedules are touched.
- Existing login, picking, driver route flow, sync, and forecast pages still open/work.
- Permission decorator/helper exists but enforcement can remain disabled.
- Job Runs infrastructure exists without breaking existing jobs.
- Pre-flight checks pass or exceptions are documented.
- Assumption log has no blocking items.

Phase 2 specific Definition of Done:

- Job logging is active for selected scheduled/manual jobs.
- Forecast watchdog can be enabled/disabled by flag.
- Legacy Replenishment MVP is hidden/disabled but not deleted.
- Cost Update schedule reconciliation is documented.
- Existing Forecast Workbench PO email still works.

Phase 3 specific Definition of Done:

- Backend permission enforcement works with HTTP 403.
- Role fallback remains available unless explicitly approved otherwise.
- Direct URL/API access tests pass.
- Driver core workflow regression test passes.

Phase 4 specific Definition of Done:

- DB-backed picking queue works in staging.
- Refresh/restart resume works.
- Atomic lock conflict test passes.
- Drain workflow tested.
- Legacy fallback remains available behind flag.

Phase 5 specific Definition of Done:

- Sensitive items are separated using `DwItem.wms_zone = 'SENSITIVE'`.
- Cooler boxes are real records.
- Items are assigned into boxes.
- PDF label and contents list work.
- Driver/loading view shows cooler information only when enabled.
- Cooler mode can be disabled to return to normal picking.

---

## 4. PHASED ROLLOUT PLAN

Do not deploy all high-risk areas in one release. Replit should provide estimated timing per phase before starting. The week numbers below are planning guidance, not permission to delay unnecessarily.

### Phase 1 — Foundation

- Add required settings with safe defaults.
- Add `display_name` field to users, nullable/defaulting to `username`.
- Add active/inactive user support.
- Add Job Runs & Sync Logs infrastructure in parallel to existing logging.
- Add `@require_permission` decorator/helper with role fallback.
- Add permission helper for templates.
- Deploy and verify no existing workflow breaks.

### Phase 2 — Visibility & Cleanup

- Migrate scheduled/manual jobs to central job logging.
- Enable forecast stale-run detection/watchdog with configurable timeout.
- Hide Replenishment MVP from menu/sidebar.
- Add direct-route disabled behavior for `/replenishment-mvp`.
- Validate Cost Update scheduler final state.
- Deploy and monitor.

### Phase 3 — Permission Enforcement

- Auto-derive explicit permissions from current roles.
- Enable backend permission enforcement on protected routes/API endpoints.
- Update templates to use `has_permission()` instead of direct role checks where practical.
- Add/admin UI for user permissions if practical in this batch.
- Deploy with rollback plan.

### Phase 4 — Batch Picking Refactor

- Build DB-backed `batch_pick_queue` parallel to existing session-based flow.
- Add standardized statuses and helpers.
- Add atomic batch creation and item locking.
- Add `Claim Batch` / `Pick as myself` for admin/warehouse users.
- Deploy behind feature flag `use_db_backed_picking_queue`.
- Drain active picking before production deployment.
- Keep old path available temporarily behind feature flag; do not remove old path without approval.

### Phase 5 — Summer Cooler / Sensitive Picking

- Add `cooler_boxes` and `cooler_box_items` tables.
- Build Cooler Picking UI.
- Generate PDF labels and cooler-box contents list.
- Add driver/loading view showing cooler boxes and contents.
- Pilot on one route first.
- Roll out to all routes after pilot success.

---

## 5. TIMEZONE STANDARDIZATION

The app currently sets `os.environ['TZ'] = 'Europe/Athens'` in `main.py`. Business operations may refer to Cairo/Nicosia/Athens. To avoid DST bugs, all scheduled jobs must use explicit IANA timezone strings.

### Rules

1. All scheduled jobs must specify timezone using IANA string, for example:
   - `Africa/Cairo`
   - `Europe/Nicosia`
   - `Europe/Athens`
   - `UTC`
2. Do not use ambiguous wording such as “Cairo time” or “Cyprus time” without IANA timezone.
3. Store DB timestamps in UTC where practical.
4. Display layer converts UTC to UI timezone.
5. Job-run records should store `started_at`, `finished_at`, and `last_heartbeat` as UTC.

### Required Schedules

| Job | Time | Timezone | Notes |
|---|---:|---|---|
| Cost Update / `erp_item_cost_refresh` | 17:55 | `Africa/Cairo` | Daily |
| Log Cleanup / `job_log_cleanup` | 06:00 | `Europe/Nicosia` | Daily |
| Forecast Watchdog | Every 5 min | UTC or `Europe/Athens` | Interval-based |

### Deliverable

Create `SCHEDULING.md` in repo root listing every scheduled job with:

- job name/id
- IANA timezone
- schedule expression
- owner/module
- description

---

## 6. SECTION A — USER ACCESS / ROLES / PERMISSIONS / MODES

### Current Issue

The app uses `users.username` as primary key, Flask-Login identity, operational actor code, picker/driver reference, and audit reference. This makes username changes risky. Access is also hard-coded through many `current_user.role` checks.

### Required Changes

1. Treat `users.username` as a stable internal user code.
2. Do not encourage username renaming in the UI.
3. Add `display_name` to users and use it in UI/reports where a human-readable name is needed.
4. Add active/inactive user support. Use deactivation instead of hard delete.
5. Add central permissions for menu/module/action access.
6. Permissions must control both menu visibility and backend route/API access.
7. Add active mode support for users with multiple allowed modes.

### Required Modes

- Admin Mode
- Warehouse Mode
- Picker Mode

Driver Mode core workflow remains unchanged.

### Admin/Warehouse Picker Requirement

Admin and warehouse users may switch to Picker Mode, but they must explicitly claim or assign the batch to themselves before picking.

Required:

- Add `Pick as myself` / `Claim batch`.
- Do not silently let an admin pick as another picker.
- Picking/activity/audit records must record the real logged-in username.

### Initial Permission Keys

```text
menu.dashboard
menu.crm
menu.forecast
menu.communications
menu.warehouse
menu.picking
menu.driver
menu.datawarehouse
menu.settings
picking.perform
picking.manage_batches
picking.claim_batch
picking.cancel_batch
routes.manage
sync.run_manual
sync.view_logs
settings.manage_users
settings.manage_permissions
cooler.pick
cooler.manage_boxes
cooler.print_labels
```

### Backend Permission Enforcement

Menu hiding is not enough.

Required:

- Add `@require_permission(permission_key)` decorator or equivalent helper.
- Direct URL/API access without permission must return HTTP `403 Forbidden`.
- API endpoints such as claim batch, pick item, cancel batch, run sync, cleanup logs, and user management must validate permissions.
- Menu visibility and backend permissions must use the same permission source.
- Provide `has_permission(permission_key)` helper for templates.

Example:

```python
@app.route('/api/claim-batch/<int:batch_id>', methods=['POST'])
@login_required
@require_permission('picking.claim_batch')
def claim_batch(batch_id):
    ...
```

```jinja
{% if has_permission('settings.manage_users') %}
  <a href="{{ url_for('users') }}">Manage Users</a>
{% endif %}
```

### Permission Migration Strategy

#### Phase A — Backward-Compatible Coexistence

Define default role-to-permissions mapping in code. The decorator should first check explicit permissions. If a user has no explicit permissions yet, fall back to current role mapping.

Example:

```python
ROLE_PERMISSIONS = {
    'admin': ['*'],
    'warehouse_manager': [
        'menu.warehouse', 'menu.picking', 'picking.*',
        'cooler.*', 'sync.view_logs'
    ],
    'picker': [
        'menu.picking', 'picking.perform', 'picking.claim_batch'
    ],
    'driver': [
        'menu.driver', 'driver.*'
    ],
}
```

#### Phase B — Explicit Permissions

- Migrate users to explicit permissions auto-derived from current role.
- Keep role fallback until explicit permissions are verified stable.
- Do not remove fallback without approval.

#### Phase C — Template Cleanup

- Replace direct role checks with `has_permission()` where practical.
- Run audit to find remaining `current_user.role` checks.
- Driver templates must not break during permission migration.

### Permission Caching

- Cache user permissions in session on login if helpful.
- On permission changes, invalidate session or require re-login.
- Admin UI should state: “Permission changes take effect on next login.”

### Validation

- Existing users can still log in.
- Disabled user cannot log in.
- `display_name` can be edited without changing username.
- Menu visibility follows permissions.
- Direct URL access returns 403 if permission is missing.
- Admin can switch to Picker Mode.
- Admin cannot pick until `Pick as myself` / `Claim batch`.
- After claiming, picked records show the real admin username.
- Driver Mode core workflow remains unchanged.
- Permission changes are applied on next login or after explicit session invalidation.

---

## 7. DRIVER MODE — HONEST SCOPE

The requirement is: Driver Mode core workflow must not change.

### Unchanged

- Driver login flow.
- Route assignment workflow.
- Stop sequence logic.
- Delivery confirmation flow.
- COD/receipt capture.
- Existing exception behavior.
- GPS tracking behavior.
- Driver authentication method.

### Allowed Changes

- Driver users may receive explicit permissions auto-derived from current role.
- Driver/loading view may show cooler-box information only when `summer_cooler_mode_enabled = true`.
- Driver operational actions should continue logging to existing operational/audit tables. Do not move every driver delivery action into Job Runs & Sync Logs, because Job Runs is for system jobs/manual admin jobs.

### Required Regression Test

- Existing driver completes a route end-to-end with `summer_cooler_mode_enabled = false` and no workflow regression.
- Driver authentication still works after permission migration.
- Cooler box display appears only when feature flag is enabled.
- Delivery confirmation remains unchanged.

---

## 8. SECTION B — JOB RUNS / SYNC LOGS / FORECAST / LOG RETENTION

### Current Issue

`View Sync Logs` is too narrow and partly file-based. We need a central page/table for scheduled/manual jobs, forecast, syncs, imports, cost update, watchdog, and cleanup.

### Required Changes

1. Create/extend central `Job Runs & Sync Logs`.
2. Every scheduled/manual job creates a job-run record when it starts.
3. Long-running jobs update heartbeat/progress where practical.
4. Jobs finish with one of:
   - `SUCCESS`
   - `FAILED`
   - `SKIPPED`
   - `STALE_FAILED`
   - `CANCELLED`

### Job Run Fields

```text
id
job_id / job_name
trigger_source: scheduled/manual/watchdog/startup_catchup/retry
started_at UTC
finished_at UTC
duration_seconds
last_heartbeat UTC
status
current_step
progress_current
progress_total
progress_message
result_summary JSON
error_message
metadata JSON
created_by if manual
parent_run_id if retry
created_at UTC
updated_at UTC
```

### Required Operations

1. Convert/expand `View Sync Logs` into `Job Runs & Sync Logs`.
2. Keep physical log viewing/download where useful, but DB job-run table is the main operational source.
3. Add stale-running detection for long-running jobs.
4. Add forecast watchdog every 5 minutes.
5. Centralize stale-run detection into a helper used by both workbench endpoints and watchdog.

### Stale Forecast Detection

Required settings:

```text
forecast_heartbeat_timeout_seconds = 2700
forecast_watchdog_interval_minutes = 5
forecast_max_duration_seconds = 3600
```

Behavior:

- If status is running and `last_heartbeat` is older than `forecast_heartbeat_timeout_seconds`, mark as `STALE_FAILED`.
- Watchdog runs every `forecast_watchdog_interval_minutes`.
- Workbench endpoints and watchdog use the same helper.
- Admin can manually retry/mark failed where appropriate.
- `forecast_max_duration_seconds` is for warning/escalation only, not for killing healthy jobs with active heartbeat.

Example helper:

```python
def mark_stale_forecasts(timeout_seconds=None):
    """Check for hanging forecasts and mark them STALE_FAILED."""
    ...
```

### Log Retention

Required setting:

```text
job_log_retention_days = 90
```

Cleanup job:

- Scheduled daily at 06:00 `Europe/Nicosia`.
- Deletes eligible technical/job logs older than retention.
- Logs itself in Job Runs & Sync Logs.
- Manual admin action: `Run Log Cleanup Now` with preview before delete.

Allowed cleanup scope:

- Job-run logs.
- Sync logs.
- Heartbeat/progress logs.
- Old physical files in `logs/`.

Do not delete:

- Active/running job rows.
- Logs marked keep/pinned if added.
- Invoices or invoice lines.
- Purchase orders or purchase order lines.
- PS365 invoices or invoice lines.
- Picking exceptions.
- Delivery exceptions/events.
- Batch picked records.
- POD records.
- Receipt/COD records.
- Cooler boxes or cooler box items.
- Financial/legal/audit transaction records.

Cleanup preview should show:

- Row counts by table.
- Files by directory.
- Retention period.
- Exclusions respected.

### Cost Update Schedule Validation

Final expected state:

```text
Active job id: erp_item_cost_refresh
Display name: Cost Update / ERP Item Catalogue Cost Refresh
Schedule: daily 17:55 Africa/Cairo
Path: _run_erp_item_cost_refresh → run_export_sync('item_catalogue')
Old ftp_price_master_sync must not be active as the cost update job
```

If an old persistent APScheduler jobstore row exists for `ftp_price_master_sync`:

- Disable/remove it safely during startup cleanup or scheduler reconciliation.
- Log the action in Job Runs & Sync Logs.
- Do not leave duplicate active cost update jobs.

### Validation

- Manual job creates job-run record.
- Failed job records `FAILED` with error details.
- Forecast run shows progress/heartbeat.
- Simulated stale forecast is marked `STALE_FAILED` by watchdog.
- Logs show manual vs scheduled trigger.
- Cleanup preview identifies logs older than 90 days.
- Cleanup deletes only eligible logs.
- Cleanup logs its own run.
- Active/running logs are not deleted.
- Schedule Management shows `erp_item_cost_refresh` at 17:55 `Africa/Cairo`.
- Old `ftp_price_master_sync` is no longer active as cost update job.

---

## 9. SECTION C — REMOVE LEGACY REPLENISHMENT MVP FROM MENU

### Current Issue

There is an old Replenishment MVP module at `/replenishment-mvp`. It should no longer appear in normal UI. Operational replenishment/ordering should continue through Forecast Workbench.

### Required Changes

1. Hide/remove legacy Replenishment MVP from menu/sidebar.
2. Do not delete code or database tables yet.
3. Do not blindly remove `blueprints/replenishment_mvp.py`.

### Confirmed Dependency

`blueprints/forecast_workbench.py` imports from `blueprints/replenishment_mvp.py`:

```text
_build_po_email_content
_send_po_email
```

### Required Approach

- Hide/disable menu and direct route only.
- **Keep blueprint registration in `main.py`** (line 263: `from blueprints.replenishment_mvp import replenishment_bp`) so that `_build_po_email_content` and `_send_po_email` remain importable by Forecast Workbench. Do NOT comment out the blueprint registration.
- Inside the blueprint route handlers, check `legacy_replenishment_enabled` setting; if `false`, return redirect to Forecast Workbench or show disabled-module message.
- If later removing the blueprint entirely, first move shared PO email helpers into `services/purchase_order_email.py` or similar, then update Forecast Workbench imports.
- Test Forecast Workbench PO preview/send after changes.

Required setting:

```text
legacy_replenishment_enabled = false
```

Direct URL behavior:

- If user opens `/replenishment-mvp` when disabled, redirect to Forecast Workbench ordering or show clear disabled-module message.

Tables to keep:

- `replenishment_runs`
- `replenishment_run_lines`
- `replenishment_suppliers`
- `replenishment_item_settings`

### Validation

- Legacy Replenishment menu no longer appears.
- Direct `/replenishment-mvp` is blocked/redirected or shows disabled message.
- Forecast Workbench supplier ordering/email functions still work.
- No replenishment tables are dropped.

---

## 10. SECTION D — BATCH PICKING HARDENING

### Current Issue

Batch picking is useful but needs hardening. Current weaknesses include:

- Inconsistent statuses.
- Picking queue stored in Flask session.
- Non-atomic item locking.
- Hard deletion risk.
- Unclear admin/warehouse picking behavior.

### Required Changes

#### 1. Standardize Batch Statuses

```text
created
assigned
in_progress
paused
completed
cancelled
archived optional if needed
```

#### 2. Add Status Helpers

```python
def is_active_batch_status(status: str) -> bool: ...
def is_terminal_batch_status(status: str) -> bool: ...
def can_edit_batch(status: str) -> bool: ...
def can_cancel_batch(status: str) -> bool: ...
```

Replace scattered hard-coded status checks with helpers.

#### 3. Move Picking Queue Out of Flask Session

Add/extend DB-backed queue table, for example:

```sql
CREATE TABLE batch_pick_queue (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    invoice_no VARCHAR(50) NOT NULL,
    item_code VARCHAR(50) NOT NULL,
    item_name VARCHAR(200),
    location VARCHAR(100),
    zone VARCHAR(50),
    wms_zone VARCHAR(50),
    corridor VARCHAR(10),
    expected_qty INTEGER NOT NULL,
    picked_qty INTEGER DEFAULT 0,
    status VARCHAR(30) DEFAULT 'pending',
    picked_by VARCHAR(64),
    picked_at TIMESTAMP WITH TIME ZONE,
    skipped_reason TEXT,
    source_allocation_json JSONB,
    pick_zone_type VARCHAR(20) DEFAULT 'normal',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_batch_pick_queue_batch_status ON batch_pick_queue(batch_id, status);
CREATE INDEX idx_batch_pick_queue_invoice ON batch_pick_queue(invoice_no);
CREATE INDEX idx_batch_pick_queue_zone_type ON batch_pick_queue(pick_zone_type);
```

Use actual existing PK/FK names from the codebase. Adjust SQL accordingly.

#### 4. Atomic Batch Creation

Batch creation must happen in a single transaction:

```text
BEGIN
  validate filters
  find eligible invoice items
  check conflicts/no active locks
  create batch
  link invoices
  lock invoice items
  create queue rows
COMMIT
ROLLBACK on failure
```

#### 5. Lock Lifecycle

- Active locks remain while batch is active.
- Cancellation releases unpicked locks.
- Picked records and audit events remain.
- Avoid leaving completed items locked forever unless deliberately designed and documented.

#### 6. Replace Hard Delete with Cancel/Archive

- Cancelled batch unlocks unpicked items.
- Keep picked records, exceptions, and activity logs.
- Hard delete only for empty/test batches with explicit admin permission, if allowed at all.

#### 7. Claim Batch / Pick as Myself

- Required for admin/warehouse users who switch to Picker Mode.
- Picker Mode users see relevant assigned/claimable batches.
- Admin can claim batch only with `picking.claim_batch` permission.

#### 8. Picking Mode Defaults

- Sequential picking is default/preferred.
- Hide/restrict Consolidated mode unless setting is enabled:

```text
enable_consolidated_batch_picking = false
```

#### 9. Batch Management UI

Show:

- Batch status.
- Assigned picker/display name.
- Claimed by/display name if different.
- Started at.
- Last activity.
- Completed at.
- Total queue rows.
- Picked/skipped/exception rows.
- Locked item count.
- Progress by invoice.
- Progress by zone/corridor.

#### 10. Audit Trail

Add/ensure audit events for:

```text
batch.created
batch.assigned
batch.claimed
batch.started
batch.item_picked
batch.item_skipped
batch.exception
batch.force_completed
batch.cancelled
batch.archived
```

### Batch Migration Safety

Moving from Flask-session queues to DB-backed queues requires a safe transition.

Pre-deployment checks:

```sql
SELECT id, status, assigned_to, created_at
FROM batch_picking_session
WHERE status IN ('in_progress', 'assigned', 'picking');

SELECT COUNT(*), locked_by_batch_id
FROM invoice_items
WHERE locked_by_batch_id IS NOT NULL
GROUP BY locked_by_batch_id;
```

If active sessions exist, follow a drain workflow:

1. Set `maintenance_mode = 'draining'`.
2. Disable new batch creation for non-admin users.
3. Show banner to current pickers to complete current batch.
4. Pause/resolve remaining batches with admin review.
5. Deploy only after operations confirms safe window.

Post-deployment reconciliation:

- Identify orphaned locked items.
- Provide admin UI/report: `Orphaned Locked Items`.
- Allow safe unlock individually or in bulk with audit log.

Rollback safety:

```text
use_db_backed_picking_queue = true
```

Keep old code path temporarily behind feature flag. Do not remove old code path without approval, even after stable operation.

### Validation

- Create a batch and confirm DB queue rows are created.
- Browser refresh during picking resumes correctly.
- Server restart/deploy during test batch resumes from DB state.
- Overlapping item lock conflict is blocked atomically.
- Cancelled batch unlocks unpicked items.
- Picked records/activity/exceptions remain after cancellation.
- Admin cannot pick until they claim the batch.
- After claim, logs show actual admin username.
- Sequential picking works end-to-end.
- Consolidated mode hidden/disabled unless setting enables it.
- Batch management page shows accurate progress and exceptions.
- Drain workflow tested in staging.
- Feature flag rollback tested.

---

## 11. SECTION E — SUMMER COOLER / SENSITIVE PICKING

### Business Requirement

During summer, items in WMS zone `SENSITIVE` must be separated from normal picking and picked together into one or more cooler boxes. A dedicated picker may pick these items. The process must follow driver delivery sequence and provide labels/contents information to the driver.

### Data Trigger Confirmed

```text
Model:  DwItem
Table:  ps_items_dw
Column: wms_zone
Value:  SENSITIVE
```

Do not rely on Excel column position. Do not add a duplicate sensitive flag at first.

### Required Setting

```text
summer_cooler_mode_enabled = false
```

### Required Behavior

When `summer_cooler_mode_enabled = false`:

- Normal picking includes all items as today.

When `summer_cooler_mode_enabled = true`:

- Items where `DwItem.wms_zone = 'SENSITIVE'` are excluded from normal picking or clearly shown as `Assigned to Cooler Picking`.
- These lines are added to dedicated Cooler Picking queue.

### Cooler Picking Queue

- Use DB-backed `batch_pick_queue` with `pick_zone_type = 'cooler'`.
- Snapshot `wms_zone` on queue row at queue creation.
- During picking, use the queue snapshot, not live `DwItem.wms_zone`, to avoid mid-pick reclassification confusion.

### Cooler Picking UI

Required:

- Dedicated Cooler Picking screen for authorized warehouse/picker users.
- Permission: `cooler.pick`.
- User selects route/shipment/delivery date.
- Queue sorted by:
  1. route/shipment
  2. delivery sequence / stop sequence
  3. customer
  4. invoice/order
  5. item

### Order Readiness Rule

An order/stop must not be marked fully ready until both normal items and sensitive/cooler items are picked or exceptioned.

### Cooler Box Tables

Cooler boxes must be real system records, not just printed labels.

Suggested structure:

```sql
CREATE TABLE cooler_boxes (
    id SERIAL PRIMARY KEY,
    route_id INTEGER,
    delivery_date DATE NOT NULL,
    box_no INTEGER NOT NULL,
    status VARCHAR(20) DEFAULT 'open',
    first_stop_sequence INTEGER,
    last_stop_sequence INTEGER,
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    closed_by VARCHAR(64),
    closed_at TIMESTAMP WITH TIME ZONE,
    label_printed_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(route_id, delivery_date, box_no)
);

CREATE TABLE cooler_box_items (
    id SERIAL PRIMARY KEY,
    cooler_box_id INTEGER NOT NULL REFERENCES cooler_boxes(id),
    invoice_no VARCHAR(50) NOT NULL,
    customer_code VARCHAR(50),
    customer_name VARCHAR(200),
    route_stop_id INTEGER,
    delivery_sequence INTEGER,
    item_code VARCHAR(50) NOT NULL,
    item_name VARCHAR(200),
    expected_qty INTEGER NOT NULL,
    picked_qty INTEGER DEFAULT 0,
    picked_by VARCHAR(64),
    picked_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) DEFAULT 'assigned'
);

CREATE INDEX idx_cooler_box_items_box ON cooler_box_items(cooler_box_id);
CREATE INDEX idx_cooler_box_items_invoice ON cooler_box_items(invoice_no);
```

Use actual existing route/shipment/stop FK names from the codebase.

### Cooler Box Assignment Rules

- One cooler box may contain items for multiple stops on the same route.
- Contents must be sorted by delivery sequence.
- Driver/loading view groups by cooler box, then by stop sequence inside the box.
- Label should show delivery range where practical, using first/last stop sequence.
- Picker can create/open Cooler Box 1, Cooler Box 2, etc.
- Picker assigns picked sensitive items to the selected cooler box.
- Picker can close a box and create/open another.

### Cooler Picking Workflow

1. Warehouse/admin opens Cooler Picking for route/shipment/date.
2. System finds all order lines where `DwItem.wms_zone = 'SENSITIVE'` and cooler mode is enabled.
3. System creates cooler picking queue.
4. Cooler picker starts picking sensitive items.
5. Picker creates/opens Cooler Box 1.
6. Picker assigns picked items into Cooler Box 1.
7. When full or operationally appropriate, picker closes Box 1 and opens Box 2.
8. System tracks exactly which customer/order/item/quantity is in each box.
9. Picker prints box labels and contents list.
10. Driver/loading view shows boxes and contents for the route.

### Labels / Driver Information

Default printing:

- Browser/PDF based.
- Primary label size: 4x6 thermal label / 100mm x 150mm where practical.
- A4 fallback for normal printers.
- Add QR code/barcode with `cooler_box_id` where practical.

Cooler box label should include:

- Route/shipment.
- Delivery date.
- Cooler box number.
- Stop range, for example `Stops 3–7`, where practical.
- `SENSITIVE ITEMS / KEEP COOL`.
- QR/barcode with `cooler_box_id` where practical.

Contents list / manifest should include all items in the box sorted by delivery sequence:

- stop sequence
- customer
- invoice/order
- item code
- item name
- quantity

Customer/order sensitive-item label is optional but useful if low-risk:

- stop sequence
- customer code/name
- invoice/order number
- cooler box number
- item summary
- `KEEP COOL`

Driver/loading view should show:

- route/shipment
- delivery date
- cooler boxes assigned to route
- box status
- contents of each box
- customers/items inside each box sorted by delivery sequence

Driver delivery view should remain stop-focused. Within each stop, show normal delivery plus any cooler items and their cooler box reference.

### Exception Handling

Support:

- Sensitive item unavailable.
- Partial quantity picked.
- Substitute item.
- Item intentionally picked with normal goods, with reason.
- Move item to cooler picking manually, with permission and audit.
- Remove item from cooler box before closing.
- Cooler box damaged before dispatch, if easy to support.

### Deferred / Phase 2 Cooler Enhancements

Do not let these delay the core summer workflow. Prepare schema only if low-risk.

Deferred unless specifically approved:

- Cooler box capacity by weight/volume.
- `cooler_box_types` table.
- 80% capacity warning / 100% capacity block.
- Physical cooler box inventory/returns tracking.
- Lost/damaged/retired cooler box lifecycle.
- Daily expected returns vs actual report.
- Idle/open box automatic timeout.
- IoT or temperature sensor integration.

Core required now:

- Sensitive item separation.
- DB-backed cooler queue.
- Real cooler boxes.
- Assign items into boxes.
- PDF labels.
- Contents list.
- Driver/loading view.
- Audit trail.

### Validation

- `summer_cooler_mode_enabled = false`: normal picking includes all items as before.
- `summer_cooler_mode_enabled = true`: order with normal + WMS `SENSITIVE` items separates correctly.
- Normal picking excludes or marks sensitive items as assigned to Cooler Picking.
- Cooler queue contains only WMS zone `SENSITIVE` items.
- Cooler queue is sorted by delivery sequence.
- Create Cooler Box 1 and Box 2.
- Pick/assign sensitive items into boxes.
- Each cooler box has contents records.
- Print cooler box label.
- Print cooler box contents list.
- Driver/loading view shows boxes and contents.
- Order is not fully ready until normal + cooler items are complete or exceptioned.
- Partial/unavailable exception works.
- Reclassification mid-pick uses queue snapshot.
- Manual move from normal to cooler picking works with audit, if implemented.
- Actions record real picker username and timestamps.

---

## 12. TESTING REQUIREMENTS

### Unit Tests Required

- Permission decorator allow/deny/role-fallback.
- `has_permission()` helper.
- Batch status helpers.
- Stale forecast detection helper.
- Job run lifecycle: start/heartbeat/finish/stale.
- Timezone helpers for UTC/local conversion where implemented.

### Integration Tests Required

- Atomic batch creation and rollback on partial failure.
- Concurrent batch creation with overlapping items.
- Backend permission enforcement on actual routes/API endpoints.
- Log retention cleanup with active/excluded records.
- Cooler picking end-to-end: queue → box → label → driver view.
- Forecast watchdog with simulated stale run.
- Cost update job schedule validation.

### Manual Test Scenarios

1. Picker mid-batch deployment/restart: picker resumes from DB state.
2. Admin claims batch: activity logs show admin’s real username.
3. Permission change while logged in: behavior matches documented session invalidation/re-login rule.
4. Forecast watchdog catches stale run.
5. Cooler picking with multiple boxes spanning multiple stops.
6. Mixed normal+cooler order completion: not ready until both complete.
7. Concurrent batch creation conflict: one succeeds, the other gets clear error.
8. Drain workflow before deployment.
9. Cost update timezone validation.
10. Driver flow regression with `summer_cooler_mode_enabled = false`.

### Performance Targets

These are targets, not automatic blockers unless performance is clearly operationally unacceptable.

- Batch creation with 500+ items should complete in reasonable operational time, target under 5 seconds.
- Permission checks should not create noticeable page latency, target under 10ms per request.
- Cooler box contents/label generation should be responsive for normal route volumes.
- Log cleanup should not lock critical production tables for long periods.

### Regression Tests

Must not break:

- PS365 sync.
- Magento order import.
- Driver delivery flow.
- Forecast Workbench PO email preview/send.
- Existing login flow.
- Receipt/POD capture.
- Picking exception logging.
- Customer dashboard / CRM views.
- Reports generation.

---

## 13. CONSOLIDATED SETTINGS LIST

Add settings with safe defaults:

```text
# Job Runs & Logging
job_runs_enabled = true
new_logging_enabled = true
job_runs_write_enabled = true
job_runs_ui_enabled = true
forecast_watchdog_enabled = false
job_log_cleanup_enabled = false
job_log_retention_days = 90
forecast_heartbeat_timeout_seconds = 2700
forecast_watchdog_interval_minutes = 5
forecast_max_duration_seconds = 3600

# Replenishment
legacy_replenishment_enabled = false

# Batch Picking
enable_consolidated_batch_picking = false
use_db_backed_picking_queue = false
allow_legacy_session_picking_fallback = true
batch_claim_required = false
maintenance_mode = normal

# Cooler Picking
summer_cooler_mode_enabled = false
cooler_picking_enabled = false
cooler_labels_enabled = false
cooler_driver_view_enabled = false
```

Optional Phase 2 settings if implemented later:

```text
cooler_box_idle_timeout_minutes = 30
cooler_capacity_warn_percent = 80
cooler_capacity_block_percent = 100
```

> **Important notes on this list:**
>
> 1. **Section 14 (Feature Flags) is the canonical source.** Section 13 above is a quick-reference subset. If there is any conflict between Section 13 and Section 14, Section 14 wins.
>
> 2. **`batch_claim_required = false` is intentional.** The default is `false` to avoid blocking warehouse operations on day 1 of Phase 4 deployment. The Section 6 requirement that admin/warehouse must claim before picking is operationally enforced once this flag is toggled to `true`. Recommended toggle window: within 7 days of stable Phase 4 operation. Until the flag is enabled, audit logs still record the real logged-in username, and the `Pick as myself` button exists and is functional — it is just not strictly required to be clicked first.
>
> 3. **`job_runs_write_enabled` and `job_runs_ui_enabled` are sub-switches** under the master `job_runs_enabled` flag. See Section 14 dependency rules.

---

## 14. FEATURE FLAGS, MODULE DISABLE SWITCHES & ROLLBACK STRATEGY

This project must be implemented so each major change can be enabled, disabled, or rolled back independently. Do not make the system depend on one big irreversible deployment.

### Required Feature Flags / Disable Switches

Add or confirm the following settings:

```text
# Global safety
wmds_development_batch_enabled = true
maintenance_mode = normal

# Permissions / access control
permissions_enforcement_enabled = false
permissions_menu_filtering_enabled = true
permissions_role_fallback_enabled = true

# Job Runs & Sync Logs
job_runs_enabled = true
new_logging_enabled = true
job_runs_write_enabled = true
job_runs_ui_enabled = true
forecast_watchdog_enabled = false
job_log_cleanup_enabled = false

# Legacy Replenishment
legacy_replenishment_enabled = false

# Batch Picking
use_db_backed_picking_queue = false
allow_legacy_session_picking_fallback = true
enable_consolidated_batch_picking = false
batch_claim_required = false

# Cooler Picking
summer_cooler_mode_enabled = false
cooler_picking_enabled = false
cooler_labels_enabled = false
cooler_driver_view_enabled = false
```

Safe default principle:

- New infrastructure can be deployed with write paths disabled or shadow-only where practical.
- High-risk behavior changes should default to off until tested.
- Feature flags should be editable by admin/settings or environment variable fallback.
- Turning a feature off must not delete data already created.
- Logging failures must not stop scheduled jobs from running.

### Feature Flag Safety Categories

Replit must classify every flag in `ROLLBACK_AND_FLAGS.md` as one of:

```text
GREEN  = safe to toggle during business hours
YELLOW = safe but may interrupt active users or require re-login/refresh
RED    = requires drain workflow, operational approval, or quiet period before toggling
```

Examples:

```text
cooler_labels_enabled = GREEN
job_log_cleanup_enabled = GREEN/YELLOW depending on cleanup implementation
permissions_enforcement_enabled = YELLOW
forecast_watchdog_enabled = YELLOW
use_db_backed_picking_queue = RED
summer_cooler_mode_enabled = RED if active picking is underway
```

### Feature Flag Dependency Rules

Replit must document dependencies between flags. At minimum:

```text
job_runs_write_enabled requires job_runs_enabled = true
job_runs_ui_enabled requires job_runs_enabled = true
forecast_watchdog_enabled requires job_runs_enabled = true and new_logging_enabled = true
job_log_cleanup_enabled requires job_runs_enabled = true
permissions_enforcement_enabled should keep permissions_role_fallback_enabled = true during rollout
batch_claim_required applies only when picking routes are active
cooler_labels_enabled requires cooler_picking_enabled = true
cooler_driver_view_enabled requires cooler_picking_enabled = true
cooler_picking_enabled should only be enabled when use_db_backed_picking_queue = true
summer_cooler_mode_enabled should only be enabled after cooler_picking_enabled is tested
```

### Module-by-Module Disable Behavior

#### Permissions

If `permissions_enforcement_enabled = false`:

- Existing role-based behavior should continue.
- `@require_permission` may log/report missing permissions but should not block production users.
- Menu filtering may still be tested separately using `permissions_menu_filtering_enabled`.

If `permissions_role_fallback_enabled = true`:

- Users without explicit permissions still work using role-derived permissions.

Rollback expectation:

- Disable enforcement.
- Keep `display_name` and permission tables in DB.
- App continues using existing roles.

#### Job Runs & Sync Logs

If `job_runs_enabled = false`:

- Existing job behavior must continue.
- Job logging should not block the actual job execution.

If `job_runs_write_enabled = false`:

- Jobs run normally but do not write to the new job-run tables.

If `forecast_watchdog_enabled = false`:

- Watchdog stops marking stale jobs.
- Existing forecast pages should still work.

If `job_log_cleanup_enabled = false`:

- Cleanup does not delete any logs.

Rollback expectation:

- Disable watchdog and cleanup first if problems occur.
- Leave historical job-run records in place.

#### Replenishment MVP

If `legacy_replenishment_enabled = true`:

- Old `/replenishment-mvp` route/menu can be temporarily re-enabled if Forecast Workbench dependency issues appear.

Rollback expectation:

- Toggle route/menu back on.
- No table restoration should be needed because no tables are deleted.

#### Batch Picking

If `use_db_backed_picking_queue = false`:

- System should fall back to the existing legacy/session picking path where still available.

If `allow_legacy_session_picking_fallback = true`:

- Replit must keep the old path available during the stabilization period.

If `batch_claim_required = false`:

- Claim/Pick-as-myself requirement can be temporarily disabled if it blocks operations unexpectedly.

Rollback expectation:

- Stop new DB-backed batch creation.
- Complete or cancel active DB-backed test batches.
- Re-enable legacy path.
- Do not delete `batch_pick_queue`; keep data for audit/debugging.

Important:

- Replit must clearly document what happens to active DB-backed batches if the flag is switched off.
- The safest behavior is: existing DB-backed batches remain viewable/manageable, but new batches use the selected active path.

#### Cooler Picking

If `summer_cooler_mode_enabled = false`:

- Sensitive items remain in normal picking as before.

If `cooler_picking_enabled = false`:

- Cooler Picking UI and queue creation are disabled.

If `cooler_labels_enabled = false`:

- Cooler box records may still exist, but label printing is hidden/disabled.

If `cooler_driver_view_enabled = false`:

- Driver/loading view does not show cooler box information.

Rollback expectation:

- Disable `summer_cooler_mode_enabled` to return to normal picking behavior.
- Keep cooler box records for audit/debugging.
- Do not delete cooler tables or picked records.

### Migration / Database Rollback Rules

1. Prefer additive migrations:
   - new tables
   - new nullable columns
   - new indexes
   - new settings
2. Avoid destructive migrations:
   - no dropping columns
   - no dropping tables
   - no changing primary keys
   - no irreversible data rewrites
3. Every migration must include:
   - forward migration
   - rollback SQL or rollback explanation
   - data safety notes
4. If a migration cannot be cleanly rolled back, Replit must state this before applying it.
5. New columns should be nullable or have safe defaults.
6. Backfills should be idempotent and re-runnable.

### Production Safety Rules

- Deploy infrastructure first with high-risk flags off.
- Enable one module/flag at a time.
- Test after each enablement.
- Do not enable batch DB-backed picking and cooler picking in the same production step.
- Do not enable permission enforcement and batch picking refactor in the same production step.
- For each flag, provide:
  - purpose
  - default value
  - how to enable
  - how to disable
  - expected effect of disabling
  - any data left behind after disabling

### Emergency Disable Order

If production issues occur, disable in this order:

1. `summer_cooler_mode_enabled = false`
2. `cooler_picking_enabled = false`
3. `use_db_backed_picking_queue = false`
4. `batch_claim_required = false`
5. `forecast_watchdog_enabled = false`
6. `job_log_cleanup_enabled = false`
7. `permissions_enforcement_enabled = false`
8. If needed, set `legacy_replenishment_enabled = true`

### Required Replit Deliverable

Replit must provide a `ROLLBACK_AND_FLAGS.md` document containing:

1. Every feature flag/setting added.
2. Default value.
3. What the flag controls.
4. Safety category: GREEN / YELLOW / RED.
5. Whether it is safe to toggle during business hours.
6. Prerequisite/dependent flags.
7. What happens to existing data if disabled.
8. Step-by-step emergency rollback procedure.
9. Any migration that cannot be fully reversed.
10. Owner/recommended person allowed to toggle in production.

---

## 15. HANDOVER REQUIREMENTS

Before handover for each phase, provide:

1. List of files changed with brief description per file.
2. List of DB migrations added, including rollback SQL or rollback approach.
3. New settings and default values.
4. Screenshots or notes for each new UI area.
5. Test results for relevant acceptance criteria.
6. Performance results for relevant performance targets.
7. Known limitations/deferred items with rationale.
8. Rollback plan if migration or workflow fails.
9. Updated `SCHEDULING.md` if scheduled jobs changed.
10. Assumptions log showing decisions made without pausing.

---

## 16. FINAL ACCEPTANCE RULE

This development batch is accepted only when each phase is demonstrated end-to-end in development/staging and we confirm:

- Access control works as expected, including backend 403 enforcement.
- Old Replenishment MVP is hidden/disabled and Forecast Workbench PO email still works.
- Job Runs & Sync Logs are visible and retained safely.
- Log cleanup respects exclusions.
- Batch picking is DB-backed, resumable, atomic, and audit-safe.
- Admin/warehouse `Claim Batch` / `Pick as myself` works.
- Cooler picking uses `DwItem.wms_zone = 'SENSITIVE'`.
- Cooler picking creates real cooler boxes with assigned contents.
- Labels and contents lists are printable.
- Driver/loading view shows cooler box information when cooler mode is enabled.
- Existing Driver Mode core workflow has not changed when cooler mode is off.
- Phased rollout is followed; no big-bang deployment of high-risk sections.

---

## 17. CRITICAL REMINDERS FOR REPLIT

1. Do not simplify or defer items without explicit approval.
2. Do not combine high-risk phases into a single production deployment.
3. Atomic operations are required for batch creation and item locking.
4. Permission enforcement must be backend-based, not menu hiding only.
5. Use IANA timezone strings.
6. Driver Mode core workflow must remain unchanged.
7. Cooler Picking core is required: sensitive item separation, DB queue, real cooler boxes, item assignment, labels, contents list, and driver/loading view.
8. Cooler capacity, physical cooler inventory, returns tracking, and idle handling are Phase 2 unless they can be added without delaying the core summer workflow.
9. Provide test plan, execution log, performance results, rollback plan, and assumptions log.

---

## APPENDIX A — PRE-FLIGHT CHECKS

Run or verify these before production deployment. If any check fails, update the implementation plan with corrected information.

### Already Verified Against Codebase (as of brief preparation)

The following assumptions have already been confirmed by direct inspection of the codebase:

| Item | Status | Evidence |
|------|--------|----------|
| `DwItem.wms_zone` column exists | ✅ Verified | `models.py:1935` — `wms_zone = db.Column(db.String(50), nullable=True)  # MAIN, SENSITIVE, SNACKS, CROSS_SHIPPING` |
| Table name is `ps_items_dw` | ✅ Verified | `models.py:1889` — `__tablename__ = "ps_items_dw"` |
| `SENSITIVE` is a valid value | ✅ Verified | Source code comment lists it as one of: MAIN, SENSITIVE, SNACKS, CROSS_SHIPPING |
| Forecast Workbench imports `_build_po_email_content` | ✅ Verified | `blueprints/forecast_workbench.py:1838` |
| Forecast Workbench imports `_send_po_email` | ✅ Verified | `blueprints/forecast_workbench.py:1863` |
| `main.py` registers `replenishment_bp` | ✅ Verified | `main.py:263` — must remain registered |
| `erp_item_cost_refresh` job exists | ✅ Verified | `scheduler.py:181-183` |
| `ftp_price_master_sync` job exists | ✅ Verified | `scheduler.py:258-260` — must be reconciled out |
| `_run_erp_item_cost_refresh` function | ✅ Verified | `scheduler.py:720` |
| `_run_ftp_price_master_sync` function | ✅ Verified | `scheduler.py:706` |

### Checks to Run in Target Environment

```sql
-- Check 1: Confirm wms_zone column exists
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'ps_items_dw'
  AND column_name = 'wms_zone';

-- Check 2: Count existing SENSITIVE items
SELECT COUNT(*), wms_zone
FROM ps_items_dw
WHERE wms_zone = 'SENSITIVE'
GROUP BY wms_zone;

-- Check 3: Find currently locked items
SELECT COUNT(*), locked_by_batch_id
FROM invoice_items
WHERE locked_by_batch_id IS NOT NULL
GROUP BY locked_by_batch_id;

-- Check 4: Find active batch sessions
SELECT id, status, assigned_to, created_at
FROM batch_picking_session
WHERE status IN ('in_progress', 'assigned', 'picking');
```

```python
# Check 5: Confirm Replenishment MVP imports in Forecast Workbench
import re
with open('blueprints/forecast_workbench.py') as f:
    content = f.read()
imports = re.findall(r'from blueprints\.replenishment_mvp import ([\w_, ]+)', content)
print('Replenishment imports:', imports)
# Expected to include: _build_po_email_content, _send_po_email
```

Scheduler check:

```python
# Check 6: Scheduler state - confirm cost update job configuration
from sqlalchemy import text
from app import db

result = db.session.execute(text("""
    SELECT id, next_run_time, job_state IS NOT NULL AS has_state
    FROM apscheduler_jobs
    WHERE id IN ('erp_item_cost_refresh', 'ftp_price_master_sync')
""")).fetchall()
for row in result:
    print(row)
```

Expected outcome:

- `erp_item_cost_refresh` row exists with a future `next_run_time` (should align to 17:55 Africa/Cairo)
- `ftp_price_master_sync` row is either absent, or present but should be removed/disabled as part of this batch
- Confirmed in source code (already verified): `scheduler.py` defines both `_run_erp_item_cost_refresh` and `_run_ftp_price_master_sync`. Reconciliation must ensure only `erp_item_cost_refresh` is the active scheduled cost update job.

---

**END OF BRIEF**

