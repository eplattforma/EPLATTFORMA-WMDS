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

## Forecast watchdog (gated)

The watchdog is gated by the `forecast_watchdog_enabled` setting (default
OFF in Phase 1, see `services/settings_defaults.py`). When the operator
turns it on:

- Cadence is read from `forecast_watchdog_interval_minutes` (default `5`).
- Stale-run detection logic lives in
  `services/forecast/stale_detection.mark_stale_forecast_run_if_needed`,
  which is also called by the live `/forecast/api/suppliers` endpoint so
  both paths agree on what "stale" means.
- Stale threshold is `forecast_heartbeat_timeout_seconds` (default
  `2700` seconds = 45 minutes).
- Capped at 3 auto-retries per UTC day to stop a permanently broken
  pipeline from looping.

When the watchdog flag is OFF, the live API path still calls the
helper (so a stuck run can never block the suppliers page indefinitely),
but no background sweep tick fires.

## Removed / legacy

- **`ftp_price_master_sync`** — removed from the jobstore on every
  scheduler boot (see the cleanup block right after the daily-job
  registration loop). The 17:55 slot now belongs to `erp_item_cost_refresh`
  (a.k.a. "Cost Update") and the FTP price master pull is owned by
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
