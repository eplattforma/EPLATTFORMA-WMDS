# Scheduled Jobs — Canonical List

All scheduled jobs use explicit IANA timezone strings. Never use ambiguous wording
such as "Cairo time" or "Cyprus time" in code or docs.

## Conventions

- All `started_at`, `finished_at`, `last_heartbeat` timestamps are stored in **UTC**.
- Display layer converts UTC to the user's UI timezone.
- Cron expressions below are read in the **listed IANA timezone**.

## Active Schedules

| Job ID | Display Name | Schedule | Timezone | Module / Function | Notes |
|---|---|---|---|---|---|
| `full_dw_sync` | Full Data Warehouse Sync | `0 3 * * *` | `Europe/Athens` | `scheduler.py :: _run_full_sync` | Daily at 03:00 |
| `incremental_dw_sync` | Incremental DW Sync | `0 1,13 * * *` | `Europe/Athens` | `scheduler.py :: _run_incremental_sync` | Twice daily |
| `customer_sync` | Customer Sync from PS365 | `0 4 * * *` | `Europe/Athens` | `scheduler.py :: _run_customer_sync` | Daily at 04:00 |
| `invoice_sync` | Invoice Sync from PS365 | `0 18 * * *` | `Europe/Athens` | `scheduler.py :: _run_invoice_sync` | Daily at 18:00 |
| `balance_fetch` | Customer Balance Fetch | `30 2 * * *` | `Europe/Athens` | `scheduler.py :: _run_balance_fetch` | Daily at 02:30 |
| `nightly_forecast` | Nightly Forecast Run | `0 5 * * *` | `Europe/Athens` | `scheduler.py :: _run_forecast` | Daily at 05:00 |
| `forecast_watchdog` | Forecast Watchdog | every 10 min | UTC | `scheduler.py :: _run_forecast_watchdog` | Existing 10-min watchdog. Phase-2 brief change to 5-min lives behind `forecast_watchdog_enabled` flag. |
| `pending_orders` | PS365 Pending Orders Sync | every 30 min | UTC | `scheduler.py :: _run_pending_orders_sync` | |
| `payments_retry` | Retry PENDING_RETRY Payments | every 5 min | `Europe/Athens` | `scheduler.py :: _retry_pending_payments` | |
| `ftp_login_sync` | FTP Magento Login Sync | per `scheduler.py` | `Africa/Cairo` | `scheduler.py :: _run_ftp_login_sync` | |
| `expiry_ftp_upload` | Expiry Dates FTP Upload | `45 17 * * *` | `Africa/Cairo` | `scheduler.py :: _run_expiry_ftp_upload` | Daily at 17:45 |
| `erp_item_cost_refresh` | **Cost Update** (ERP Item Catalogue Cost Refresh) | `55 17 * * *` | `Africa/Cairo` | `scheduler.py :: _run_erp_item_cost_refresh` | Daily at 17:55. Authoritative cost-update job. |
| `stock_777_sync` | PS365 Stock 777 Sync | `5 18 * * *` | `Africa/Cairo` | `scheduler.py :: _run_stock_777_sync` | Daily at 18:05 |
| `offers_update` | Offers Update (FTP price master + customer offers) | `10 18 * * *` | `Africa/Cairo` | `scheduler.py :: _run_offers_update` | Daily at 18:10. DB lock + chunked imports. |

## Phase 2 Additions (planned)

| Job ID | Display Name | Schedule | Timezone | Module / Function | Notes |
|---|---|---|---|---|---|
| `job_log_cleanup` | Log Cleanup | `0 6 * * *` | `Europe/Nicosia` | TBD `services/job_log_cleanup.py` | Daily at 06:00. Behind `job_log_cleanup_enabled` flag. Default OFF in Phase 1. |
| `forecast_watchdog_5m` | Forecast Watchdog (5-min cadence) | every 5 min | UTC | `scheduler.py :: _run_forecast_watchdog` | Brief Section 5 cadence. Behind `forecast_watchdog_enabled` flag. Default OFF in Phase 1. |

## Reconciliation Notes

- `ftp_price_master_sync` job: must **not** be the active cost-update job. The
  scheduler already removes the legacy job at boot
  (see `scheduler.py:_register_job_funcs()` cleanup). The authoritative cost-update
  job is `erp_item_cost_refresh` at 17:55 `Africa/Cairo`.

## Deferred / Manual-Only

- Forecast retry watchdog (creates `auto_retry_watchdog` runs) — already shipped, kept ON.
- Stock 777 startup catch-up — runs once on boot if not already run today.
