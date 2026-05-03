# WMDS CRM — Scheduled Jobs Catalogue

Source of truth: `scheduler.py`. All times below are **Africa/Cairo** unless
explicitly UTC. Jobs only register inside the designated scheduler worker
(`GUNICORN_WORKER_AGE=1`) and only when either `REPLIT_DEPLOYMENT=1` or
`ENABLE_BACKGROUND_JOBS=true`.

Every scheduled job is wrapped with the central `services.job_run_logger`
lifecycle so a row appears in `job_runs` for each tick (gated by the
`job_runs_enabled` and `job_runs_write_enabled` settings — both default
ON in Phase 1).

## Daily jobs

| Job ID                        | Cron (Cairo)        | Owner module                              | Notes |
|-------------------------------|---------------------|-------------------------------------------|-------|
| `balance_fetch`               | 16:30               | `routes_reconciliation`                   | Customer balance fetch from PS365 |
| `customer_sync`               | 16:40               | `background_sync.start_customer_sync_background` | Logs to `ps365_sync_log` (CUSTOMER_SYNC) |
| `invoice_sync`                | 16:50               | `datawarehouse_sync.sync_invoices_from_date` | Last 2 days |
| `incremental_dw_sync`         | 13:00, 17:00        | `datawarehouse_sync.incremental_dw_update` | Twice daily |
| `full_dw_sync`                | 17:15               | `datawarehouse_sync.full_dw_update`       | Heavy; 15-min gap after incremental |
| `forecast_run`                | 17:35               | `services.forecast.run_service.execute_forecast_run` | Heartbeats into `forecast_runs.last_heartbeat_at` |
| `expiry_ftp_upload`           | 17:45               | `services.expiry_ftp_upload`              | Pushes expiry CSV to FTP |
| `erp_item_cost_refresh`       | **17:55 (Cost Update)** | `services.erp_export_bot`             | Daily Item Catalogue export → cost_price refresh. **Replaces the legacy `ftp_price_master_sync` slot.** |
| `stock_777_sync_production`   | 18:05 (prod only)   | `services.ps365_stock_777_service`        | Dev variant `stock_777_sync` runs at 23:30 |
| `offers_update`               | 18:10               | `services.crm_price_offers`               | Pulls FTP price master + rebuilds per-customer offer rows. Runs after Cost Update so offer margins use fresh cost. |

## Sub-hourly jobs

| Job ID                   | Cron                          | Owner module                                | Notes |
|--------------------------|-------------------------------|---------------------------------------------|-------|
| `pending_orders_sync`    | every :00 and :30             | `services.ps365_pending_orders_service`     | DB-lock guarded |
| `retry_pending_payments` | every 5 minutes               | `services.payments.commit_to_ps365`         | Up to 10 attempts per PaymentEntry |
| `forecast_watchdog`      | every N minutes (gated)       | `services.forecast.stale_detection`         | See "Forecast watchdog" below |
| `ftp_login_sync`         | every :15 and :45 (deployed)  | `services.ftp_login_sync`                   | Pulls FTP login logs |

## Job-runs lifecycle status semantics

Every tick of every catalogue job writes one row to `job_runs` via the
`_tracked(job_id, job_name, trigger_source)` wrapper:

| Status         | When |
|----------------|------|
| `RUNNING`      | Inserted on tick start. `last_heartbeat` is bumped by `scheduler.heartbeat()` from inside the body (currently wired on the forecast pipeline; other long jobs can opt in by importing the same helper). |
| `SUCCESS`      | Body returned normally (no exception). |
| `SKIPPED`      | Body raised `JobSkipped(reason)` — early-return guards: lock already held, "no work to do", "auto-retry budget spent", PS365 temporarily unavailable, etc. The reason is stored in `result_summary.reason`. |
| `FAILED`       | Body raised any other exception. The exception message is stored in `error_message` and is also re-raised so APScheduler logs it. |
| `STALE_FAILED` | `services.forecast.stale_detection.mark_stale_forecast_run_if_needed` (called by both the watchdog cron and the live `/forecast/api/suppliers` endpoint) detected a `RUNNING` `forecast_run` row whose heartbeat aged past `forecast_heartbeat_timeout_seconds`. The helper marks the `forecast_runs` row failed AND calls `services.job_run_logger.mark_stale_runs(timeout, job_id_filter='forecast_run')` so the matching `job_runs` row is flipped from RUNNING to STALE_FAILED in the same pass. |

## Forecast watchdog (cadence flag)

The watchdog **always runs**; the `forecast_watchdog_enabled` flag only
tunes how often it sweeps:

- **OFF (default)** — legacy 10-min cadence.
- **ON** — cadence is read from `forecast_watchdog_interval_minutes`
  (default `5`, clamped 1..59 — APScheduler `CronTrigger` rejects
  `*/60`) so operators can tighten the sweep without a code deploy.

Detection / retry behaviour (independent of the cadence flag):

- Stale-run detection logic lives in
  `services/forecast/stale_detection.mark_stale_forecast_run_if_needed`,
  which is also called by the live `/forecast/api/suppliers` endpoint so
  both paths agree on what "stale" means.
- Stale threshold is `forecast_heartbeat_timeout_seconds` (default
  `2700` seconds = 45 minutes).
- Each early-return guard inside the watchdog raises `JobSkipped` with
  a reason ("nothing to do", "budget spent", "another run already in
  progress", etc.) so each tick's `job_runs` row reflects the actual
  outcome.
- Capped at 3 auto-retries per UTC day to stop a permanently broken
  pipeline from looping.

Even with the flag OFF, both paths run: the background sweep ticks
every 10 min (legacy cadence) AND the live `/forecast/api/suppliers`
call self-heals on every page hit, so a stuck run can never block the
suppliers page indefinitely.

## Job Runs cleanup (Phase 4)

| Slot          | Cadence                       | Owner module                       | Status |
|---------------|-------------------------------|------------------------------------|--------|
| `log_cleanup` | Daily at 06:00 Africa/Cairo   | `services.maintenance.log_cleanup` | **Implemented in Phase 4.** Goes through `_tracked(...)` like every other job. The body is gated by `job_log_cleanup_enabled` (default `false`) — when OFF the wrapper records a SKIPPED row each morning so the cron is visibly alive without deleting any history. When ON, deletes `job_runs` rows whose `started_at` is older than `job_runs_retention_days` (default 90, no-op when ≤ 0). The body returns `{rows_deleted, retention_days, cutoff_utc}` and `_tracked` persists it as the row's `result_summary`. |

Operator default posture: ship with `job_log_cleanup_enabled=false` so
the table accumulates indefinitely until an admin flips the flag. The
scheduled job is **always** registered so the moment the flag is
flipped to `true` the next 06:00 sweep runs without a code deploy.

Visibility: every fire is visible at `/admin/job-runs` (gated solely by
the `sync.view_logs` permission — no separate UI kill-switch) with the
same RUNNING/SUCCESS/SKIPPED/FAILED/STALE_FAILED semantics as every
other job.

## Removed / legacy

- **`ftp_price_master_sync`** — removed from the jobstore on every
  scheduler boot. If the legacy slot is found we log a one-time `WARN`
  for explicit reconciliation visibility (jobstore is persistent — a
  silent INFO would be easy to miss); subsequent boots are silent
  because the slot is already gone. The 17:55 slot now belongs to
  `erp_item_cost_refresh` (a.k.a. "Cost Update") and the FTP price
  master pull is owned by
  `offers_update` at 18:10. There is no duplicate registration.

## Boot-time catch-up

On the designated scheduler worker, two startup checks run after a 60-second
warm-up:

1. **Full DW sync catch-up** — if no successful FULL_DW_UPDATE in the past
   20 hours, fire `_run_full_sync` immediately.
2. **Invoice sync catch-up** — same logic for INVOICE_SYNC.
3. **Stock 777 catch-up** — production only, fires once on boot if no run
   started today.

Each catch-up respects an in-flight `RUNNING` log row from the previous
worker to avoid double-fire.

## Phase 5 — Cooler Picking (Reduced Scope, Task #22)

**No new scheduled jobs.** Cooler boxes are operator-driven (open/assign/close
via `/cooler/...` endpoints). There is no watchdog, no auto-close worker, and
no nightly reconciliation. Orphaned `Open` boxes left after a flag-down event
surface in the existing orphan-locks reconciliation UI; see GAP-P5-01.
