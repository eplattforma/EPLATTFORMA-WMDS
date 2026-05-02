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
| `permissions_enforcement_enabled` | `false` | YELLOW | Backend `@require_permission` returns 403 when missing. | Decorator only logs, never blocks. | Admin |
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
8. If needed, `legacy_replenishment_enabled = true`
