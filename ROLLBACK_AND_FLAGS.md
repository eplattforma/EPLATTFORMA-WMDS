# Feature Flags & Rollback Reference

This document is the **canonical source** for every feature flag introduced by the
WMDS Development Batch. If Section 13 of the brief and this file disagree, this
file wins for the implementation; the brief wins for intent.

All flags live in the `settings` table (key/value text) and are read via
`Setting.get(session, key, default)`. Defaults are seeded by
`services/settings_defaults.py :: ensure_phase1_settings_defaults()` and only
inserted if the key is missing.

## Safety Categories

- **GREEN** — safe to toggle during business hours.
- **YELLOW** — safe but may interrupt active users or require re-login/refresh.
- **RED** — requires drain workflow, operational approval, or quiet period.

## Phase 1 Foundation — Flags Added (all OFF by default for high-risk items)

| Key | Default | Category | Controls | Disable effect | Owner |
|---|---|---|---|---|---|
| `wmds_development_batch_enabled` | `true` | GREEN | Master switch for the entire batch. | Disables every flag below. | Admin |
| `maintenance_mode` | `normal` | YELLOW | Operational mode (`normal` / `draining` / `readonly`). | App returns to normal. | Admin |
| `permissions_enforcement_enabled` | `false` | YELLOW | Backend `@require_permission` returns 403 when missing. Phase 3 ships with this OFF (Verification & Closeout brief Section 1.2, Option A). Admin flips to `true` manually from the Settings UI when production is ready (see Phase 3 section). | Decorator only logs, never blocks. | Admin |
| `permissions_auto_seed_done` | `false` → `true` after first boot | GREEN | One-time marker so the Phase 3 seeder writes role-default rows into `user_permissions` exactly once. Set back to `false` (or use the manual "Re-seed Permissions" button on Manage Users) to re-seed. | Seeder skips on boot. Existing rows preserved. | Admin |
| `permissions_menu_filtering_enabled` | `true` | GREEN | Hides menu items the user lacks permission for. | All menu items visible (subject to existing role checks). | Admin |
| `permissions_role_fallback_enabled` | `true` | GREEN | If user has no explicit perm rows, derive from role. | Users without explicit perms get nothing — keep ON during rollout. | Admin |
| `job_runs_enabled` | `true` | GREEN | Master switch for new job-runs infrastructure. | Disables `job_runs_write_enabled` and `job_runs_ui_enabled`. | Admin |
| `new_logging_enabled` | `true` | GREEN | Wraps existing jobs with the new logger. | Existing jobs continue writing to legacy log paths only. | Admin |
| `job_runs_write_enabled` | `true` | GREEN | Allow writes into `job_runs` table. | Jobs run normally; nothing inserted into `job_runs`. | Admin |
| `job_runs_ui_enabled` | `true` | GREEN | Show new Job Runs page (Phase 2). | Page returns 404; existing Sync Logs page unaffected. | Admin |
| `forecast_watchdog_enabled` | `false` | YELLOW | Phase-2 5-min watchdog cadence. (Existing 10-min watchdog stays.) | Stale forecasts no longer auto-marked by 5-min watchdog. | Admin |
| `job_log_cleanup_enabled` | `false` | GREEN | Daily 06:00 Nicosia cleanup job. | Cleanup never runs; no rows deleted. | Admin |
| `job_log_retention_days` | `90` | GREEN | Retention horizon for the cleanup job. | N/A — numeric. | Admin |
| `forecast_heartbeat_timeout_seconds` | `2700` | GREEN | Watchdog stale threshold. | N/A — numeric. | Admin |
| `forecast_watchdog_interval_minutes` | `5` | GREEN | Phase-2 watchdog interval. | N/A — numeric. | Admin |
| `forecast_max_duration_seconds` | `3600` | GREEN | Warning threshold for healthy long-running forecasts. | N/A — numeric. | Admin |
| `legacy_replenishment_enabled` | `false` | GREEN | Re-enable `/replenishment-mvp` route + menu. | Route shows disabled message / redirects. Tables untouched. | Admin |
| `enable_consolidated_batch_picking` | `false` | YELLOW | Show Consolidated picking mode UI. | Sequential picking only. | Admin |
| `use_db_backed_picking_queue` | `false` | RED | Phase-4 DB-backed picking queue. | New batches use legacy session path. Existing DB-backed batches remain manageable. | Warehouse manager + Admin |
| `allow_legacy_session_picking_fallback` | `true` | YELLOW | Keep legacy session path available. | DB-backed only. Do not disable until Phase 4 stable. | Warehouse manager + Admin |
| `batch_claim_required` | `false` | YELLOW | Force admin/warehouse to click "Pick as myself" before picking. | Picking proceeds; audit logs still capture real username. | Admin |
| `summer_cooler_mode_enabled` | `false` | RED | Separate `wms_zone='SENSITIVE'` items into cooler queue. | Sensitive items remain in normal picking. | Warehouse manager + Admin |
| `cooler_picking_enabled` | `false` | RED | Cooler picking UI + queue creation. | UI hidden; no cooler queue rows created. Existing rows preserved. | Admin |
| `cooler_labels_enabled` | `false` | GREEN | Cooler box label printing UI. | Print buttons hidden. | Admin |
| `cooler_driver_view_enabled` | `false` | GREEN | Show cooler info in driver/loading view. | Driver view unchanged. | Admin |

## Dependency Rules

```text
job_runs_write_enabled            requires  job_runs_enabled = true
job_runs_ui_enabled               requires  job_runs_enabled = true
forecast_watchdog_enabled         requires  job_runs_enabled = true AND new_logging_enabled = true
job_log_cleanup_enabled           requires  job_runs_enabled = true
permissions_enforcement_enabled   keep      permissions_role_fallback_enabled = true during rollout
batch_claim_required              applies   only when picking routes are active
cooler_labels_enabled             requires  cooler_picking_enabled = true
cooler_driver_view_enabled        requires  cooler_picking_enabled = true
cooler_picking_enabled            requires  use_db_backed_picking_queue = true
summer_cooler_mode_enabled        enable    only after cooler_picking_enabled is tested
```

## Migration Reversibility

All Phase 1 schema changes are **additive and reversible** in this sense:

- New tables (`job_runs`, `user_permissions`) can be dropped without affecting
  existing flows because no Phase 1 code path requires them.
- New column `users.display_name` is nullable and defaulted to `username` —
  dropping it would require a schema-only rollback; no historical data lost.
- No existing column or table is dropped, renamed, or reshaped in Phase 1.

Rollback SQL for Phase 1 (only if needed):

```sql
-- DROP additions (data loss in job_runs and user_permissions only)
DROP TABLE IF EXISTS user_permissions;
DROP TABLE IF EXISTS job_runs;
ALTER TABLE users DROP COLUMN IF EXISTS display_name;
-- Settings can be left in place; they have no effect when their
-- gated code paths are disabled.
```

## Phase 3 — Permission Enforcement (added 2026-05-02)

| Change | What it does | One-flag rollback |
|---|---|---|
| `permissions_enforcement_enabled` ships `false`; admin manually flips it ON when ready | While `false` (Phase 3 default per Verification & Closeout brief Section 1.2, Option A), `@require_permission` decorators only log missing keys. Flipping to `'true'` activates 403 enforcement (admin role still has `*` wildcard, role fallback still ON as safety net). The manual flip is the signal that "Phase 3 is live in production." | `Setting.set(db.session, 'permissions_enforcement_enabled', 'false')` — decorators revert to log-only. |
| Phase 3 seeder runs once on boot | Walks every active user and writes their role's permission set into `user_permissions`. Idempotent. Marker setting `permissions_auto_seed_done`. | Manual "Re-seed Permissions" button on Manage Users force-runs it again; or set marker to `false` and restart. |
| Per-user permission editor (`/admin/users/<u>/permissions`) | Save replaces only non-wildcard `user_permissions` rows for that user; "reset to role defaults" deletes everything for that user and re-runs the seeder for them. | "Reset to role defaults" button on the editor page. |
| Decorators added to routes (admin/manage-users / scheduler triggers / forecast workbench / data warehouse menu+sync / route management / admin batch endpoints) | 403 enforcement for non-admin/non-WM users without explicit grants. Driver Mode untouched. | Toggle the master flag above. |
| `crm_admin` role added to `ROLE_PERMISSIONS` map | Existing crm_admin users keep access to comms / CRM via role fallback. | Remove the entry to revert; explicit grants in `user_permissions` still work. |
| Templates migrated from `current_user.role == 'admin'` to `has_permission('settings.manage_users')` etc. | Menu items / buttons hide for users without the permission, even if `permissions_menu_filtering_enabled = true`. Driver templates untouched. | Toggle `permissions_role_fallback_enabled = false` to make explicit grants the only source of truth (advanced); or revert templates from git history. |

**Driver Mode invariant preserved.** No changes to `templates/driver/*`, `routes_driver.py`, or `routes_driver_api.py`. The override-ordering pipeline regression test (`tests/test_override_ordering_pipeline.py`) still passes.

## Phase 4 — Job Runs UI & Log Cleanup (added 2026-05-03)

| Change | What it does | One-flag rollback |
|---|---|---|
| `job_log_cleanup_enabled` (already seeded `false` in Phase 1) | Daily 06:00 Africa/Cairo cron is **always registered**. While `false`, each tick raises `JobSkipped("disabled by flag")` and `_tracked` records a SKIPPED row in `job_runs`, so the cron is visibly alive without deleting any history. Flipping to `'true'` activates pruning on the next morning's tick. | `Setting.set(db.session, 'job_log_cleanup_enabled', 'false')` — next tick records SKIPPED, no rows deleted. |
| `job_runs_retention_days` (new key, default `90`) | Retention horizon read by `services.maintenance.log_cleanup.delete_old_job_runs()`. Predicate is `WHERE started_at < (NOW() - retention_days * '1 day')` — purely time-based, no status filter. A 0 or negative value is treated as a no-op (defensive pause-without-disable). The summary's `cutoff_utc` field is the actual deletion threshold (`NOW - retention_days`), not wall-clock now, so operators can see exactly which rows were eligible. The legacy `job_log_retention_days` key from Phase 1 is preserved for back-compat but no Phase 4 code reads it. | Set `job_runs_retention_days = 0` (or any non-positive) — cleanup body becomes a no-op while the cron still records SUCCESS rows. |
| Cleanup cron timezone is **Africa/Cairo** (06:00 daily) | The Phase 4 brief mentions Europe/Nicosia but the project's `SCHEDULER_TZ` is anchored to Africa/Cairo for the entire scheduler (Phase 2 decision). 06:00 Cairo ≈ 06:00 Nicosia year-round (≤ 1h DST drift), and a quiet pre-business-hours sweep is the operational intent. This is the accepted product decision; revisit only if Nicosia wall-clock becomes contractual. | Reschedule the `log_cleanup` job from `/datawarehouse/database-settings` (no code deploy required). |
| New `/admin/job-runs` admin page (list + `/admin/job-runs/<id>` detail) | Read-only view onto the `job_runs` table. Filters: job_id (dropdown of distinct values), multi-select status, "Last N hours" (default 24, 0 = no time filter), limit (clamped 10..500). Detail page shows full `result_summary` JSON, full `error_message`, `metadata`, and a link to any `parent_run_id`. | **No kill-switch by design** — the brief deliberately omits a UI flag so the page cannot be hidden out from under operators investigating an incident. Permission gating (`sync.view_logs`) is the only access control. To remove the page entirely, comment out the blueprint registration in `main.py`. |
| Existing `/datawarehouse/logs` (file-based) menu label updated to "Sync Log Files" | The file-based syslog page is still useful for raw text troubleshooting and is kept in place; the relabel just disambiguates it from the new DB-backed Job Runs page. | Revert the two label edits in `datawarehouse_routes.py` (the route itself is unchanged). |
| `_tracked(...)` now persists body return value as `result_summary` | When a tracked job body returns a `dict`, that dict is saved as the SUCCESS row's `result_summary`. Existing body funcs that return `None` are unaffected. Phase 4 cleanup uses this to record `{rows_deleted, retention_days, cutoff_utc}`. | This is a code change, not a flag. Revert `scheduler.py` `_tracked` to drop the `body_result` capture if needed. |

**Permission posture preserved.** No new keys added to `ROLE_PERMISSIONS`. The new page reuses `sync.view_logs` (already granted to `warehouse_manager` and to `admin` via the `*` wildcard), so Phase 3 closeout's "no role-default change" property is intact.

**Driver Mode invariant preserved (Phase 4).** Phase 4 touches no driver routes or templates. The override-ordering pipeline regression test continues to pass.

## Production Safety Rules

1. Deploy infrastructure first with high-risk flags off (Phase 1 default).
2. Enable one module/flag at a time.
3. Test after each enablement.
4. Do **not** enable DB-backed picking and cooler picking in the same step.
5. Do **not** enable permission enforcement and batch picking refactor in the same step.

## Emergency Disable Order

If production issues occur, disable in this order:

1. `summer_cooler_mode_enabled = false`
2. `cooler_picking_enabled = false`
3. `use_db_backed_picking_queue = false`
4. `batch_claim_required = false`
5. `forecast_watchdog_enabled = false` (Phase-2 watchdog only — existing 10-min stays)
6. `job_log_cleanup_enabled = false`
7. `permissions_enforcement_enabled = false`
8. `job_runs_retention_days = 0` (Phase 4 — pause cleanup body without disabling cron)
9. If needed, `legacy_replenishment_enabled = true`

---

## Phase 4 — Batch Picking Refactor (Task #21)

**Status:** Code merged 2026-05-03. **All flags seeded `false` in production.**

### New flags (all default `false`)
| Flag | Default | What flipping `true` does |
|---|---|---|
| `use_db_backed_picking_queue` | `false` | New batches use the `batch_pick_queue` table; in-progress batches stay on the legacy in-memory queue (see ASSUMPTION-022). |
| `batch_claim_required` | `false` | Pickers must explicitly claim a batch via `/picker/batch/claim/<id>` before picking; legacy auto-assignment continues otherwise. |

### New Setting rows (not flags — operator state)
| Key | Default | Meaning |
|---|---|---|
| `maintenance_mode` | `'normal'` | Set to `'draining'` via `/admin/batch/drain-status` to block new batch creation for non-admins. |

### New permission
- `picking.delete_empty_batch` — **NOT in any role's grant list.** Admin holds it via the `*` wildcard; warehouse_manager via `picking.*`. Hard-delete UI buttons are now the exception path; the default is `cancel_batch` (preserves audit + releases locks).

### Schema (additive — `update_phase4_batch_picking_schema.py`)
- `batch_picking_sessions`: `+cancelled_at`, `+cancelled_by`, `+cancel_reason`, `+claimed_at`, `+claimed_by`, `+last_activity_at`, `+archived_at`, `+archived_by` (all nullable).
- New table `batch_pick_queue` with indexes on `(batch_session_id, status)` and `(invoice_no, item_code)`.

### Rollback
1. Set `use_db_backed_picking_queue = false` and `batch_claim_required = false` (already the default).
2. Set `maintenance_mode = 'normal'` if drain mode was engaged.
3. New columns/table are additive — no migration needed to revert. Code can be reverted; data stays intact.

### Emergency disable order (updated)
Insert before existing step 3:
- `2a. maintenance_mode = 'normal'` (release any drain hold)
- `2b. batch_claim_required = false` (auto-assign returns)
- `2c. use_db_backed_picking_queue = false` (legacy queue resumes)

---

## Phase 5 — Cooler Picking (Reduced Scope, Task #22)

### New / re-affirmed flags (all default `false`)
| Setting key | Default | Effect when `true` |
|---|---|---|
| `summer_cooler_mode_enabled` | `false` | `create_batch_atomic` routes SENSITIVE rows to `pick_zone_type='cooler'` and snapshots `wms_zone` from `dw_items.zone_in_warehouse`. |
| `cooler_picking_enabled` | `false` | `cooler_bp` blueprint endpoints (`/cooler/...`) become operator-visible; `route_detail.html` renders the cooler-boxes overlay; driver-loading overlay shows cooler box counts. |

### New permission keys
- `cooler.pick` — granted to `picker`, `warehouse_manager`, `admin` via wildcard.
- `cooler.manage_boxes` — granted to `warehouse_manager` (via `cooler.*`) and `admin` (via `*`). **NOT** granted to `picker`.

### Schema (additive — `update_phase5_cooler_picking_schema.py`)
- New tables `cooler_boxes`, `cooler_box_items` with FK to `batch_picking_sessions.id`.
- `batch_pick_queue` gains nullable `pick_zone_type VARCHAR(20)` and `wms_zone VARCHAR(50)`.

### Rollback
1. Set `summer_cooler_mode_enabled = false` and `cooler_picking_enabled = false` (already the default).
2. Schema is additive — no data migration needed to revert. Code can be reverted without touching the new tables.

### Emergency disable order (updated, after Phase 4 block)
- `2d. cooler_picking_enabled = false` (overlay + cooler endpoints disappear from operator UI)
- `2e. summer_cooler_mode_enabled = false` (new batches stop snapshotting cooler zones)

In-flight cooler boxes already opened before the flag flip are not auto-closed; the operator closes them via `/cooler/box/<id>/close` or leaves them open until the order ships (the close is idempotent — see P5-32).

---

## Cockpit — Ticket 1 (Account-Manager Cockpit scaffold, Task #24)

### New flag (default `false`)

| Flag | Default | What it gates |
|---|---|---|
| `cockpit_enabled` | `false` | Master flag for the entire `/cockpit/...` URL space. When `false`, every cockpit route returns HTTP 404 (the URL space is hidden, not forbidden) and the "AM Cockpit" menu entry under Customers is hidden via `has_permission('menu.cockpit')` (no-op while permission keys are unassigned). |

### New permission keys (registered, intentionally unassigned)

`menu.cockpit`, `customers.use_cockpit`, `customers.propose_target`,
`customers.approve_target`, `customers.ask_claude`. Listed in
`services.permissions.COCKPIT_PERMISSION_KEYS`. **No role grants them by
default** — admins receive them via the `*` wildcard. Claudio assigns
them per-user at rollout (cockpit-brief Section 14). Until then, AMs and
managers see the same UX as today.

### Schema (additive — `migrations/cockpit_schema.py`)

- `customer_spend_target` — one row per customer (PK `customer_code_365` → `ps_customers`); cadence columns + `status` ∈ {`active`, `proposed`, `no_target`} + proposed/approved/last-modified actor + timestamps.
- `customer_spend_target_history` — append-only audit log; one row per `customer.target.{proposed,set,approved,active_snapshot,rejected}` event.
- `vw_customer_offer_opportunity` (Postgres only) — read-only view returning customer-SKU pairs where the customer bought ≥ €100 in the last 90 days, has no active offer, and ≥ 3 peers in the same `reporting_group` do. Computed on read (cockpit-brief 10.2 — re-pointed to actual schema; see ASSUMPTION-034). SQLite skips the view; the cockpit service degrades to an empty list, never raises.

Idempotent: `CREATE TABLE IF NOT EXISTS` + `CREATE OR REPLACE VIEW`. Wired into `main.py` immediately after the Phase 5 schema runner so cold boot under both Postgres and SQLite ensures the schema before the blueprint is registered.

### Rollback

1. Set `cockpit_enabled = false` (already the default). All routes return 404; menu entry vanishes.
2. (Optional) Revoke the five permission keys from any users granted them.
3. (Optional, destructive) `DROP VIEW vw_customer_offer_opportunity; DROP TABLE customer_spend_target_history; DROP TABLE customer_spend_target;`. Tickets 2 and 3 add no further schema, only services.

### Emergency disable order (updated, appended after Phase 5 block)

- `3a. cockpit_enabled = false` (entire `/cockpit/...` URL space returns 404; menu entry hidden; existing customer reports — Customer 360, Benchmark, Peer Analytics, Pricing Analytics, Abandoned Carts, CRM Dashboard — keep working unchanged).


## Cockpit — Ticket 2 (Main Cockpit Page, Task #25)

### No new flag

Continues to ride on the existing `cockpit_enabled` master flag (default `false`). When the flag is `false`, `/cockpit/<customer_code>` returns 404 just like every other cockpit route.

### No new permission keys

Reuses `customers.use_cockpit` for the page. The four target write endpoints continue to require `customers.propose_target` / `customers.approve_target`. There is no live-cart JSON endpoint — the panel is server-rendered (see ASSUMPTION-045).

### No schema changes

Ticket 2 is **service + template only**. All data is computed on read from already-shipped tables (`dw_invoice_header`, `dw_invoice_line`, `ps_items_dw`, `dw_item_categories`, `ps_customers`, `crm_customer_offer_current`, `crm_customer_offer_summary_current`, `crm_abandoned_cart_state`, `magento_customer_last_login_current`, `sms_log`, `customer_spend_target_history`, `vw_customer_offer_opportunity`).

### New dependency

- Python: `cachetools` (TTL cache for the cockpit payload — see ASSUMPTION-042).

### New files

- `services/cockpit_data.py` — orchestrator (caching + per-section queries; never modifies existing routes; see ASSUMPTION-039 through ASSUMPTION-044 for design choices).
- `static/cockpit/cockpit.css`, `static/cockpit/cockpit.js` — page-only assets (Chart.js loaded from CDN, scoped to the cockpit page).

### New routes (all under the same `cockpit_enabled` flag)

- `GET /cockpit/<customer_code>` — main page (replaces the Ticket 1 placeholder). The live-cart inline panel is rendered server-side from the same payload (no extra API route).

### Rollback

1. Set `cockpit_enabled = false` (already the default). The page returns 404. Existing customer reports keep working — they were never touched.
2. (Optional) Remove the `cachetools` package once Ticket 2 has been reverted everywhere.

### Emergency disable order (updated)

- `3a.` (unchanged) `cockpit_enabled = false` — disables the full URL space and the menu entry simultaneously. No data deletion required.

## Cockpit Ticket 3 — Greek Claude advice + Recommended Actions panel

### New dependency

- Python: `anthropic==0.97.0` (Anthropic SDK).

### New env / secrets (both optional)

- `ANTHROPIC_API_KEY` — without it the advice endpoint returns 503 with `{"configured": false}` and the rest of the cockpit page is unaffected.
- `CLAUDE_MODEL` — defaults to `claude-sonnet-4-5` if unset.

### New files

- `services/claude_advice_service.py` — lazy Anthropic client + 12h cache (`cockpit_`-prefixed in `ai_feedback_cache`).
- `templates/cockpit/_partials/recommended_actions.html` — auto-loading panel + Bootstrap modal for section-level advice.
- `tests/test_cockpit_ticket3.py` — unit + endpoint coverage (no live API calls).

### Modified files (additive)

- `app.py` — read `ANTHROPIC_API_KEY` / `CLAUDE_MODEL` into `app.config` at boot.
- `blueprints/cockpit.py` — `_ADVICE_SECTION_KEYS`, `_build_advice_snapshot`, `POST /cockpit/api/<code>/advice`.
- `templates/cockpit/cockpit.html` — replace the Ticket 3 placeholder with the partial include; add four Ask Claude buttons (page-level + offers + opportunities + pricing + risk), all permission-gated.
- `static/cockpit/cockpit.js` — `askClaude(section)`, `_loadRecommendedActions()`, modal renderer, HTML escaping.

### New route (under existing `cockpit_enabled` master flag)

- `POST /cockpit/api/<customer_code>/advice` — JSON body `{section: all|offers|opportunities|pricing|risk}`. Requires `customers.ask_claude` (hard permission). Returns the Claude advice JSON on 200; `{configured: false, message: <Greek>}` on 503 (API key unset); `{message: <Greek>}` on 500 (Anthropic error — full detail logged server-side only). 404 for unknown customer or master flag off.

### New permission (already registered in Ticket 1)

- `customers.ask_claude` — already in `COCKPIT_PERMISSION_KEYS` and `ALL_EDITOR_KEYS`; unassigned by default. Grant via the standard user-permissions editor.

### Rollback

1. Set `cockpit_enabled = false` — the entire `/cockpit/...` URL space (including `/advice`) returns 404.
2. To remove the panel without disabling the cockpit, revoke the `customers.ask_claude` permission for all users — the panel and all Ask Claude buttons disappear server-side.
3. Full revert: delete the four files above, revert the `app.py` / `blueprints/cockpit.py` / `cockpit.html` / `cockpit.js` patches, run `uv remove anthropic`. No schema changes were made; the shared `ai_feedback_cache` rows can be cleaned with `DELETE FROM ai_feedback_cache WHERE payload_hash LIKE 'cockpit_%';`.
