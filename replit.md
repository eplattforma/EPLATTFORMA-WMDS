# Warehouse Picking Management System

## Overview
This project is a comprehensive warehouse picking management system built with Flask and PostgreSQL. Its primary goal is to optimize warehouse operations by streamlining order picking, batch processing, and time tracking. The system aims to significantly enhance efficiency, reduce operational errors, and provide actionable analytics for warehouse managers. Key capabilities include real-time status updates, AI-powered insights, and robust delivery issue management, contributing to a more efficient and data-driven warehouse environment.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### UI/UX Decisions
- **Frontend**: Utilizes Jinja2 templating with a Bootstrap-based responsive design.
- **Forms**: Server-side rendered forms are implemented with CSRF protection.

### Technical Implementations
- **Backend**: Developed using Flask (Python).
- **Database**: PostgreSQL for production, SQLite for development, managed with SQLAlchemy via Flask-SQLAlchemy.
- **Authentication**: Flask-Login provides role-based access control for `admin`, `picker`, `warehouse_manager`, and `driver` roles.
- **Deployment**: Gunicorn is used for serving the application.
- **Core Features**:
    - **Picking System**: Supports individual and batch picking, skip/collect later functionality, real-time updates, and exception handling.
    - **Time Tracking & Analytics**: Implements phase-based per-item time tracking, shift management, and KPI calculation.
    - **Batch Processing**: Enables zone/corridor-based batch creation and item locking.
    - **Delivery Management**: Includes issue tracking, route planning, driver assignment, progress tracking, and a mobile-optimized driver app for delivery execution with Proof of Delivery (POD) capture and discrepancy integration.
    - **Return & Discrepancy Workflows**: Provides structured processes for handling failed deliveries and verifying discrepancies.
    - **Customer & Order Management**: Features customer payment terms, PO receiving (with Desktop Entry Mode for office/warehouse users — quantity conversion, expiry capture, product images, and shared PS365 GRN submission), intelligent rules engine for WMS attributes, palletization, SKU-level packing profiles, and a standardized order processing flow.
    - **Financials**: Manages invoice import, order/route status lifecycles, various receipt document types, bank statement import and matching, and live customer balance display.
    - **Forecasting & Ordering (Separated)**: Demand classification (smooth/erratic/intermittent/lumpy/new_true/sparse_valid/availability_distorted/no_demand), multi-method forecasting (MA8, Median6, SEEDED_NEW, RATE_BASED, AVAILABILITY_DISTORTED), trend detection, brand→supplier→flat seasonality hierarchy (365-day window), and OOS-aware demand correction. History completeness detection: items with insufficient history are flagged as `history_incomplete` with `INSUFFICIENT_HISTORY` forecast method instead of being silently zeroed. The forecast engine excludes OOS-impacted weeks (3+ OOS days) from base calculations when sufficient clean data exists, with method-specific minimum clean weeks (smooth/erratic: 8, intermittent/lumpy: 6, new_sparse: 4). Trend is suppressed when recent 2 weeks are OOS-impacted. Visual OOS overlays on sales history charts (red bars for 3+ OOS days, amber for 1-2 days) with background shading and tooltip annotations. Seasonality displayed as a line chart (Jan-Dec) with source/confidence metadata. **Forecast Override System**: Planners can apply per-SKU forecast overrides via the workbench UI with reason codes, notes, and 28-day auto-review dates. Overrides are tracked in `sku_forecast_override` table. The ordering service uses override values when active (final_forecast_source = 'override' vs 'system'). The workbench shows override status badges (OVR/DUE/PAST), Ovr/Wk column, Source column, filter by override status, and bulk actions (extend review +4w, mark reviewed, clear). **Forecast and Ordering are separated**: forecast runs are forecast-only (no replenishment, zero PS365 calls), ordering is an on-demand "Refresh Ordering" process that creates `SkuOrderingSnapshot` records with target stock = (weekly_forecast × target_weeks) + lead_time_cover + review_cycle_cover + buffer. Per-item `target_weeks_of_stock` is editable inline in the UI. All ordering data (on_hand, net_available, order quantities) is sourced from the latest ordering snapshot, not from forecast results. The "Refresh Ordering" button uses DB-backed job tracking (`ordering_refresh_jobs` table) with real-time progress polling (every 3s) so the UI only reloads data after the refresh fully completes, preventing stale zero-value display. **Forecast pipeline is optimized**: weekly sales builder uses single INSERT...SELECT...ON CONFLICT (no Python row loops), normal runs use incremental mode (8 weeks), seasonality uses 365-day window with supplier-level aggregation, all steps log timing and row counts with enhanced classification/seasonality breakdowns. Three separate admin API endpoints: `/api/refresh-weekly-sales`, `/api/recompute-seasonality`, `/api/run`. Weekly sales builder supports `rebuild_365` mode alias. **Forecast resilience**: base_forecast loop commits every 500 items (instead of single end-of-loop commit) to bound memory growth, and a `forecast_watchdog` cron sweeps every 10 minutes — any forecast run with no heartbeat for 45+ minutes is auto-marked failed and a fresh run is launched (capped at 3 auto-retries per day, only retries scheduler/watchdog-triggered runs to avoid stepping on admin actions). Heartbeats fire every 25 items in base_forecast for tighter liveness signal.
    - **Communications Hub**: A unified multi-channel platform (SMS, push notifications, call scripts) for customer communications with template-based messages and DLR handling. Includes a comprehensive dashboard.
    - **Offer Intelligence**: Imports customer-specific pricing, enriches with cost/margin/sales data, and provides analytics on offer usage and sales dependency. Features a multi-tab UI with Offer Summary, Unused Offers, Offer-Driven Sales, and All Active Offers, including SMS integration for sending offers. An Admin Offers page provides cross-customer analytics and rule details.
    - **DW Cost Enrichment**: Enriches data warehouse invoice lines with cost snapshots sourced from `DwItem.cost_price` for accurate gross profit and margin calculations. Includes operator-triggered and date-based invoice synchronization.
    - **Dropbox Cost Import**: A pipeline to import item costs from `items.xlsx` in Dropbox to update `ps_items_dw.cost_price`, with an Admin UI for connection status, import summary, and history.
    - **PS365 OOS 777 Daily Sync**: Daily synchronization of Out-Of-Stock (OOS) items for Store 777 (Eshop), capturing only active OOS items for sales review and anomaly protection.
    - **Synchronization & Data Refresh**: Automated FTP login sync, PS365 sync logging, and pending order import from PS365.
    - **CRM Dashboard**: A central dashboard for customer activity monitoring, classification management, delivery slot filtering, task logging, and open orders integration, optimized for performance. Includes an Ordering Window System.
    - **Review Ordering (4-State)**: An enhanced review page for managing customer orders with computed states (follow_up, waiting, ordered, close), integrated with customer profiles and communication tools.
    - **Delivery Date Override**: Allows bulk temporary delivery date reassignment from the Review Ordering dashboard, with a dedicated model and service for managing overrides.
    - **Phase 2 Visibility & Cleanup**: Every scheduled APScheduler job is wrapped with a generic `_tracked(job_id, job_name, trigger_source)` module-level callable that writes one `job_runs` row per tick with three terminal statuses — SUCCESS, SKIPPED (body raised `JobSkipped(reason)` for an early-return guard such as a held lock or "no work to do"), or FAILED (any other exception, re-raised so APScheduler still logs). Long jobs emit progress via a `heartbeat(current_step, progress_message, ...)` helper (currently wired on the forecast pipeline; other long jobs can opt in by importing the same helper). Manual "Run Now" admin clicks flow through the same wrapper with `trigger_source='manual'`. Stale-forecast detection is consolidated in `services/forecast/stale_detection.py` so the live `/forecast/api/suppliers` endpoint and the watchdog cron share one threshold (`forecast_heartbeat_timeout_seconds`, default 2700s). The forecast watchdog is **always scheduled**; the `forecast_watchdog_enabled` flag only tunes cadence — OFF (default) means the legacy 10-min cadence, ON uses `forecast_watchdog_interval_minutes` (default 5, clamped 1..59 — APScheduler `CronTrigger` rejects `*/60`). Legacy MVP Replenishment is hidden from the menu and gated route-by-route on `legacy_replenishment_enabled` (default OFF) — JSON paths return 404, HTML routes flash + redirect — but the blueprint stays registered so Forecast Workbench can keep importing `_build_po_email_content` / `_send_po_email`. The full job catalogue (14 jobs, owner blueprints, cron, including Cost Update at 17:55 Africa/Cairo) lives in `SCHEDULING.md`; the legacy `ftp_price_master_sync` slot is documented as removed.

### System Design Choices
- **UTC Timestamp Consistency**: All database timestamps are stored in UTC.
- **Performance Optimizations**: Implements connection pooling, query optimization, and Gunicorn tuning.
- **User Roles**: Defines distinct access levels for various user types.
- **Delivery Dashboard**: Offers an overview of dispatched routes with on-demand AJAX loading.
- **Data Integrity**: Utilizes soft deletes and status changes for critical entities.
- **Advanced Search**: Provides advanced search capabilities for invoices and routes.
- **Customer Synchronization & Analytics**: Dedicated screens for syncing customer data, a 360-degree analytics dashboard, abandoned cart tracking, and customer benchmarking with AI-powered feedback.
- **Pricing Analytics**: Offers customer-level pricing analysis.
- **Power BI Integration**: Provides database views for Power BI reporting.

## External Dependencies

### Python Libraries
- **Flask**: Web framework.
- **SQLAlchemy**: ORM.
- **Pandas**: Data processing.
- **NumPy**: Numerical computations.
- **Scikit-learn**: Machine learning.
- **Pillow**: Image processing.
- **OpenAI**: AI integration.
- **PyTZ**: Timezone handling.
- **Gunicorn**: WSGI server.
- **Openpyxl, Xlsxwriter**: Excel file handling.
- **ReportLab**: PDF generation.

### Database Dependencies
- **PostgreSQL 16**: Production database.

### Integrations
- **PS365**: Used for shelf location, PO receiving, customer data sync, integrated receipts, zone sync, pending orders, customer statement of account balance lookups, and daily stock availability sync for Store 777 (Eshop).
- **SMTP Email**: For sending supplier purchase orders.
- **Microsms API**: For SMS sending and delivery report handling.
- **OneSignal**: For push notifications.
- **Power BI**: For business intelligence reporting.
- **Magento/BSS**: For customer pricing and abandoned cart data.
- **Playwright**: Browser automation for ERP export bot. Chromium is auto-installed on first use; in production the scheduler also kicks off a background pre-warm at boot so the daily ERP item-cost cron isn't paying first-time install cost. Install location is probed across `PLAYWRIGHT_BROWSERS_PATH`, `~/.cache/ms-playwright`, and `~/workspace/.cache/ms-playwright`. The cost-refresh cron writes its `bot_run_log` audit row before any browser/install step, and the Powersoft365 nav-with-relogin path retries up to 3 times (clearing cookies between attempts) so a single transient login redirect doesn't kill the whole nightly run.
- **Background scheduler**: APScheduler runs in a single designated gunicorn worker, persisting its job table to the shared Postgres `apscheduler_jobs` table. Because the production deployment is on Replit Autoscale (workers spin down to zero on idle), all daily batch jobs are scheduled in a 16:20–18:05 Cairo window that overlaps with warehouse staff working hours. Sub-hourly jobs (pending orders every 30min, payment retries every 5min, FTP login sync every 30min) keep their original cadence — they fire naturally whenever the app is in use during the day. Misfire grace on every daily job is 6 hours so a slightly delayed worker boot still catches the run.
- **Stock Dashboard reserved-stock column** (`/stock-dashboard`): the dashboard table now includes a "Reserved" column (rightmost) showing how much of each item is reserved in PS365 sales orders for Store 777. Data is stored in the `stock_dashboard_reserved` table (model `StockDashboardReserved` — PK `item_code`, columns `store_code`, `stock_reserved`, `stock_ordered`, `synced_at`) and is refreshed on demand by the `POST /api/refresh-reserved-stock` endpoint (admin/warehouse_manager only). The endpoint pulls every distinct item code from `StockPosition`, calls `services_ps365_stock.fetch_items_stock_for_store("777", codes)` (chunks 50 per PS365 page with `analytical_per_store=True`), and DELETE-then-bulk-inserts the snapshot. The dashboard's `refreshFromERP()` JS chains the two refreshes back-to-back: first `erp_bot.erp_refresh_stock_positions` (the Playwright export), then `/api/refresh-reserved-stock`, then `location.reload()`. If the reserved step fails the user is warned but the page still reloads so they can see the ERP refresh result. The dashboard view also exposes a "Reserved: <local time>" stamp in the H2 subtitle so users can see when the figures were last fetched.
- **Database Settings / Scheduler UI** (`/datawarehouse/database-settings`): admin-only page that lists every registered job with its cron expression, next-run time, and active/paused status, and lets administrators reschedule (hour / minute / day-of-week, cron syntax allowed), pause, resume, or trigger a job on demand ("Run now"). The page (template `templates/datawarehouse/database_settings.html`) is rendered by the `database_settings()` view in `datawarehouse_routes.py`. Form actions POST to the `admin_scheduler_bp` blueprint at `/admin/scheduler/<job_id>/{reschedule,pause,resume,run-now}` (in `routes_admin_scheduler.py`); each handler validates the project's session-based CSRF token, mutates the jobstore via `scheduler.py` helpers, flashes a result, and redirects back to `/datawarehouse/database-settings`. The legacy `/admin/scheduler/` GET URL is kept as a redirect for any existing bookmarks. Reads/writes go through the shared SQLAlchemy jobstore — to support being called from any worker (not just the scheduler-owning one), the helpers wrap each operation in a `_JobstoreContext` that, when the live scheduler isn't on this worker, spins up a temporary `BackgroundScheduler` started in paused mode (so it loads jobs from the DB without firing anything), performs the read/mutate, then shuts it down. Mutations propagate to the live scheduler on its next wake cycle (typically within a few minutes); no redeploy required. "Run now" bypasses the scheduler and invokes the job function directly in a daemon thread, guarded by a per-process lock so a double-click can't launch overlapping runs.
## WMDS Development Batch — Phase 4 (Job Runs UI & Log Cleanup)
- **New `/admin/job-runs` admin page** lists `job_runs` lifecycle rows (started, job, trigger, status, duration, heartbeat, step/progress, result/error). Filters: job_id (dropdown of distinct values), multi-select status, "Last N hours" (default 24, 0 = no time filter), limit (clamped 10..500). Detail page `/admin/job-runs/<id>` shows full `result_summary` JSON, full `error_message`, `metadata`, and a link to `parent_run_id`. Gated solely by `@require_permission('sync.view_logs')` — no UI kill-switch by design (the brief deliberately omits one so the page cannot be hidden during an incident).
- **Existing file-based `/datawarehouse/logs` page** is unchanged; only its menu label was updated to "Sync Log Files" to disambiguate it from the new DB-backed Job Runs page.
- **New scheduled job `log_cleanup`** runs daily at 06:00 Africa/Cairo through the same `_tracked(...)` wrapper as every other catalogue job. The body reads `job_log_cleanup_enabled` (default `false`, seeded by Phase 1) — when OFF it raises `JobSkipped("disabled by flag")` so a SKIPPED row appears each morning (visible inactivity, not silent inactivity). When ON it calls `services.maintenance.log_cleanup.delete_old_job_runs()` which executes a parameterised `DELETE FROM job_runs WHERE started_at < (NOW() - retention_days * '1 day')`. Retention is read from the new `job_runs_retention_days` setting (default `90`); a 0 or negative value is treated as a no-op so cleanup can be paused without disabling the cron. The legacy `job_log_retention_days` key from Phase 1 is preserved as an alias but no Phase 4 code reads it.
- **`_tracked(...)` enhancement**: when a tracked body returns a `dict`, that dict is persisted as the SUCCESS row's `result_summary`. Bodies returning `None` are unaffected. The cleanup body uses this to record `{rows_deleted, retention_days, cutoff_utc}` on every successful sweep.
- **One-flag rollback**: set `job_log_cleanup_enabled = false` to halt deletions instantly (next tick records SKIPPED). For temporary pause without disabling the cron, set `job_runs_retention_days = 0`.
- **Tests**: `tests/test_log_cleanup_service.py` (9 cells: summary shape, delete-only-old, retention=0/negative no-op, default-from-setting, invalid setting, scheduler wrapper OFF/ON, truthy/falsy enabled values) and `tests/test_job_runs_ui.py` (parametrised role × enforcement matrix for admin / WM / picker / driver / crm_admin, filter matrix for status/job_id/hours/distinct dropdown, detail route allow/404/anonymous, defensive query-string parsing). Override-pipeline regression test continues to pass.

## WMDS Development Batch — Phase 1 (Foundation)

This is the first phase of the multi-phase WMDS Development Batch. The full brief is at `attached_assets/wmds_app_review_development_backlog_FINAL_*.md`. Phase 1 lays the foundation; **all high-risk behaviour ships disabled by default**.

### Reference docs (repo root)
- `SCHEDULING.md` — every scheduled job with explicit IANA timezone.
- `ROLLBACK_AND_FLAGS.md` — canonical list of all feature flags, defaults, dependencies, and emergency disable order.
- `ASSUMPTIONS_LOG.md` — assumptions made under the brief's "autonomous execution" rules.

### What Phase 1 added
- **Schema** (`update_phase1_foundation_schema.py`, called from `main.py`):
  - `users.display_name VARCHAR(120) NULL` — backfilled from `username`. Falls back to `username` everywhere it is consumed.
  - `job_runs` table — central log for scheduled / manual jobs (status, heartbeat, progress, result_summary, error_message, parent_run_id). Indexes on `(job_id, started_at DESC)`, `status`, and a partial index on `last_heartbeat WHERE status='RUNNING'` for the watchdog.
  - `user_permissions` table — `(username, permission_key)` unique pair, FK to `users.username`. Username PK migration is **not** in scope per brief Ground Rule 2.
- **Settings** (seeded by `services/settings_defaults.py :: ensure_phase1_settings_defaults()`, idempotent; never overwrites operator-set values):
  - 24 flags from brief Section 14. High-risk flags default OFF (`permissions_enforcement_enabled`, `legacy_replenishment_enabled`, `use_db_backed_picking_queue`, `summer_cooler_mode_enabled`, `cooler_picking_enabled`, etc.).
  - GREEN flags default ON (`job_runs_enabled`, `new_logging_enabled`, `permissions_role_fallback_enabled`).
- **Permissions service** (`services/permissions.py`):
  - `ROLE_PERMISSIONS` map (admin / warehouse_manager / picker / driver) supports wildcards (`picking.*`, `*`).
  - `has_permission(user, key)` — explicit `user_permissions` first, then role fallback (when `permissions_role_fallback_enabled=true`).
  - `@require_permission(key)` — Phase 1 is **non-blocking**: it only logs missing permissions while `permissions_enforcement_enabled=false`. Phase 3 toggles to active 403 enforcement.
  - `register_template_helpers(app)` — exposes `has_permission()` to all Jinja templates.
- **Job-run logger** (`services/job_run_logger.py`):
  - `start_job_run`, `heartbeat`, `finish_job_run`, `mark_stale_runs`, `get_recent_runs`.
  - All exception-safe: failures are logged at WARN level and never propagate (per brief Section 14: "logging failures must not stop scheduled jobs from running").
  - Gated by `job_runs_enabled` AND `job_runs_write_enabled`.

### What Phase 1 did NOT change
- No existing route, blueprint, scheduler job, or model field was removed or repurposed.
- The existing 10-min forecast watchdog continues unchanged — the brief's 5-min cadence is gated behind `forecast_watchdog_enabled` and is a Phase 2 change (see `ASSUMPTION-001`).
- `replenishment_mvp` blueprint stays registered (needed by `forecast_workbench` PO email helpers per brief Section 9 / Appendix A).
- Driver Mode core workflow untouched (brief Section 7).

### Operational notes
- New DB objects are visible at: `\d job_runs`, `\d user_permissions`, `\d users` (look for `display_name`).
- Toggle flags via `Setting.set(db.session, key, value)` or by directly editing the `settings` table.
- Phase 2/3/4/5 will each be done in their own focused batch — do not enable two high-risk flags in the same production step (see `ROLLBACK_AND_FLAGS.md` Production Safety Rules).

## WMDS Development Batch — Phase 3 (Permission Enforcement)

Phase 3 turns the Phase 1 permission scaffolding ON across the app and migrates role-string checks to the `has_permission(...)` API. **Driver Mode workflow is untouched** (brief Section 7).

### What Phase 3 added
- **Seeder** (`services/permission_seeding.py`): on first boot after Phase 3, `seed_permissions_from_roles()` walks every active user and inserts the role's permission keys (literal wildcards preserved — the matcher already handles them) into `user_permissions`. Gated by the one-time `permissions_auto_seed_done` setting. Manual "Re-seed Permissions" button on Manage Users force-runs it. Helper `reset_user_to_role_defaults(username)` clears one user back to defaults.
- **Per-user permission editor** (`/admin/users/<username>/permissions`, defined in `routes.py`): 21-key checkbox grid grouped into Menu / Picking / Routes & Sync / Settings. Save replaces only non-wildcard rows (`NOT LIKE '%*%'`) so admin-style wildcards stay intact. "Reset to role defaults" calls the seeder for that one user. Linked from a shield button on each row of Manage Users.
- **Per-request cache** in `services/permissions.py`: `_explicit_permissions_for(username)` memoizes on `flask.g`, so a page that checks 30 menu items issues one SELECT instead of 30.
- **Enforcement flag stays OFF by default**: `permissions_enforcement_enabled` defaults to `false` per the Verification & Closeout brief Section 1.2 (Option A). Admins flip it to `true` manually from the Settings UI when production is ready. While off, `@require_permission` decorators only log missing keys (the seeder still runs once on first boot so explicit rows exist by the time the flag is flipped). `permissions_role_fallback_enabled` stays `true` as the safety net. Admin role keeps the `*` wildcard.
- **`crm_admin` role added to `ROLE_PERMISSIONS`**: previously a string-only role inside `_role_ok()` in comms/sms blueprints. Now has `menu.dashboard`, `menu.crm`, `menu.communications`, `comms.*` so role fallback covers them.
- **Decorators added** (`@require_permission(...)`):
  - `settings.manage_users` — admin user routes (`manage_users`, `edit_user`, `delete_user`, `toggle_user_status`, `admin_reset_password`, `admin_sorting_settings`, `manage_user_permissions`, `seed_permissions_admin`).
  - `sync.run_manual` — scheduler (`routes_admin_scheduler.py`: run_now/pause/resume/reschedule), forecast workbench (`blueprints/forecast_workbench.py`: api/run, refresh-weekly-sales, recompute-seasonality, ordering/refresh, stock/refresh), data warehouse (`datawarehouse_routes.py`: full-sync, incremental-sync, incremental-sync-execute, test-one-item, invoice-sync, import-suppliers).
  - `menu.datawarehouse` — `datawarehouse.dw_menu`.
  - `picking.manage_batches` — every `/admin/batch/*` endpoint plus `/batch/<id>/force_complete` and `/batch/delete/<id>` in `routes_batch.py`.
  - `routes.manage` — wired into the existing `admin_required` decorator in `routes_routes.py` (admin/WM/explicit-grant pass; everyone else gets 403).
- **Templates migrated** from `current_user.role == 'admin'` (or `current_user.role in ['admin', 'warehouse_manager']`) to `has_permission(...)`:
  - `templates/base.html` (admin dropdown), `batch_picking_debug.html`, `batch_picking_list.html`, `batch_picking_view.html`, `help_section.html`, `help_dashboard.html`, `admin_dashboard.html`, `admin/review_delivery_issues.html`, `shipped_orders_report.html` → `settings.manage_users`.
  - `reports/reserved_stock_777.html` → `menu.warehouse`.
  - `routes_dashboard.html`, `route_detail.html` → `routes.manage`.
- **What was deliberately left as role-only checks**:
  - Three workflow-routing cases that aren't access control: `templates/admin_dashboard.html:211` (warehouse-manager-specific issue badge), `templates/admin/review_delivery_issues.html:198,203` (warehouse-manager filter UI), `templates/change_password.html:59` (picker dashboard URL pick after password reset).
  - Inline `if current_user.role not in ['admin', 'warehouse_manager']:` blocks inside batch routes — kept as defense in depth alongside the new decorator.

### Task #17 — analytics blueprints migrated (2026-05-03)
The three remaining `_role_ok()` holdovers from Phase 3 closeout (deferred via ASSUMPTION-019 / GAP-002) were migrated to `has_permission(current_user, "menu.warehouse")`:
  - `routes_customer_analytics.py` (11 call sites)
  - `blueprints/category_manager.py` (3 call sites)
  - `blueprints/peer_analytics.py` (4 call sites)

`menu.warehouse` was used for all three (not `menu.crm` for customer analytics) so that role fallback continues to allow only `admin` (`*`) and `warehouse_manager` by default. Using `menu.crm` would have widened Customer 360 access to the `crm_admin` role (which holds `menu.crm` in `ROLE_PERMISSIONS`), violating the "no other role gains analytics access by default" constraint. The `_role_ok()` helper signature was preserved in each file as a thin wrapper around `has_permission(...)` so the 18 call sites are unchanged. No edits to `ROLE_PERMISSIONS` were needed (admin already has `*`, warehouse_manager already has `menu.warehouse`). Admins can now grant `menu.warehouse` to a custom-role user from the per-user permission editor to give them analytics access without making them a warehouse manager. `tests/test_permission_enforcement.py` `ROUTE_MATRIX` extended with one allow (admin → 200) + one deny (picker → 403) + one `crm_admin` deny (→ 403, pins the no-widening property) cell per blueprint, plus a parametrised `test_explicit_grant_non_wm_user_passes_analytics` proving a `picker` with an explicit `menu.warehouse` grant passes all three surfaces under role-fallback OFF. `KNOWN_GAPS.md` GAP-002 removed; `ASSUMPTIONS_LOG.md` ASSUMPTION-019 marked SUPERSEDED.

### One-flag rollback
`Setting.set(db.session, 'permissions_enforcement_enabled', 'false')` reverts `@require_permission` to log-only mode without touching code or templates. The `admin_required` widening in `routes_routes.py` automatically respects the flag through `has_permission`. `ROLLBACK_AND_FLAGS.md` Phase 3 section is the canonical reference.

### Verification (PHASE_TEST_RESULTS.md, 2026-05-02)
- Override-ordering pipeline regression: 1 passed.
- Both gunicorn workers reach `PHASE 7: main.py fully loaded` cleanly.
- Phase 3 seeder runs once on first boot, marker set, idempotent on subsequent boots (`Phase 3 seeder: skipped (already done)`).
- Driver Mode invariant preserved (zero edits to `templates/driver/*` or driver routes).

## WMDS Development Batch — Phase 4 (Batch Picking Refactor — Task #21)

- **Schema (additive, idempotent):** `update_phase4_batch_picking_schema.py` adds `cancelled_at/by`, `cancel_reason`, `claimed_at/by`, `last_activity_at`, `archived_at/by` to `batch_picking_sessions` and creates the new `batch_pick_queue` table with `(batch_session_id, status)` and `(invoice_no, item_code)` indexes. Wired into `main.py` immediately after the legacy `update_batch_picking_schema` runner; logs `Phase 4: ... ensured` lines on every boot. SQLite test DBs get the same columns through the ORM declarations on `BatchPickingSession`.
- **`services/batch_status.py`** — single source of truth for ACTIVE/TERMINAL/EDITABLE/CANCELLABLE/CLAIMABLE state sets and the `is_active/is_terminal/can_edit/can_cancel/can_claim` helpers (case-insensitive via `.lower()`). Status strings stay Title Case (`Created`, `In Progress`, `Completed`, `Paused`, plus new `Cancelled`/`Archived`).
- **`services/batch_picking.py`** — atomic `create_batch_atomic(filters, created_by, mode)` that locks rows with `SELECT ... FOR UPDATE SKIP LOCKED` (Postgres) and raises `BatchConflict(conflicting_batch_id, conflicting_items)` if any candidate item is already locked, rolling back the whole transaction (no orphan session row). Also: `cancel_batch` (releases unpicked locks + ActivityLog `batch.cancelled`), `claim_batch` (sets `assigned_to`/`claimed_by`/`claimed_at` + `batch.claimed` log), `find_orphaned_locks`, `bulk_unlock_orphans` (ActivityLog `batch.orphan_unlock`), and `is_db_queue_enabled()` reading the `use_db_backed_picking_queue` Setting.
- **`services/maintenance/drain.py`** — `set_mode/get_mode/is_draining/get_drain_banner` backed by the `maintenance_mode` Setting (`'normal'` / `'draining'`). `is_creation_allowed_for(user)` returns `False` for non-admins while draining. `force_pause_stuck_batches()` flips active batches whose `last_activity_at` is >30 min old to `Paused` so a drain can complete cleanly.
- **`routes_admin_batch_phase4.py`** (registered as `admin_batch_phase4_bp`): `POST /picker/batch/claim/<id>`, `POST /admin/batch/cancel/<id>`, `GET /admin/batch/orphaned-locks` + `POST .../unlock-all` + `GET .../orphaned-locks.json`, `GET/POST /admin/batch/drain-status` + `POST .../force-pause`. All routes go through `require_permission` and renderwith inline templates that fall back gracefully when the optional `admin_orphaned_locks.html` template is not present.
- **Hard-delete refactor:** Both legacy delete handlers in `routes_batch.py` (`/admin/batch/delete/<id>` and `/batch/delete/<id>`) are now gated by the new `picking.delete_empty_batch` permission, which is **not** in any role's grant list — only admins (via `*` wildcard) and warehouse_manager (via `picking.*`) hold it. Non-empty batches are auto-routed through `cancel_batch` to preserve audit history and release locks instead of being hard-deleted.
- **Production posture:** Every Phase 4 flag stays `false`. The legacy in-memory queue path is the live default; the new code is exercised only by tests. Driver Mode invariant preserved (no driver routes touched). Mid-flight flag flips are explicitly documented as "applies to newly-created batches only" (ASSUMPTION-022).
- **Tests:** `tests/test_phase4_batch_picking.py` covers the full P4-01..28 matrix (28/28 passing in 4.58s on in-memory SQLite). `override-pipeline` regression still passing.

## WMDS Development Batch — Phase 5 (Cooler Picking, Reduced Scope — Task #22)

- **Schema (additive, idempotent):** `update_phase5_cooler_picking_schema.py` creates `cooler_boxes` and `cooler_box_items` tables (FK to `batch_picking_sessions.id`), and adds nullable `pick_zone_type VARCHAR(20)` + `wms_zone VARCHAR(50)` columns to `batch_pick_queue`. Wired into `main.py` immediately after the Phase 4 runner; logs `Phase 5: ... ensured` lines on every boot.
- **Cooler routing:** `services/batch_picking.create_batch_atomic` reads `summer_cooler_mode_enabled` once at batch creation. When `true`, SENSITIVE rows are stamped `pick_zone_type='cooler'` with a `wms_zone` snapshot from `dw_items.zone_in_warehouse`; everything else stays `pick_zone_type='normal'`. Decision is frozen on insert (ASSUMPTION-023) — flag flips affect only newly-created batches.
- **Order readiness:** `services/order_readiness.is_order_ready(invoice_no)` aggregates pick state across normal + cooler queues and returns a single boolean; `routes.py:2554` was refactored to call it (single source of truth instead of two parallel pick-status queries).
- **Cooler blueprint:** `blueprints/cooler_picking.py` exposes `/cooler/route-list`, `/cooler/route/<id>/<date>`, `/cooler/box/create|assign|close|reopen`, `/cooler/box/<id>/label.pdf`, `/cooler/route/<id>/<date>/manifest`, plus PDF helpers in `services/cooler_pdf.py` (ReportLab + qrcode). Permission gates: `cooler.pick` (picker+) for read/assign, `cooler.manage_boxes` (warehouse_manager+) for create/close/reopen.
- **UI overlays:** `templates/cooler/{route_list,route_picking}.html` are dedicated screens; `templates/route_detail.html` line 396 shows a flag-gated cooler-boxes summary (visible only when `cooler_picking_enabled=true`). Driver Mode invariant preserved — no driver-only routes were modified.
- **Tests:** `tests/test_phase5_cooler_picking.py` 33/33 passing (sensitive-routing, box lifecycle, readiness, permissions, PDFs, overlay flag-gating, exception paths). Phase 4 regression suite still 30/30; override-pipeline still green.
- **Production posture:** `summer_cooler_mode_enabled` and `cooler_picking_enabled` both stay seeded `false`. Sensitive items continue to flow through the normal pick zone with no `wms_zone` snapshot until the operator explicitly flips both flags. No new scheduled jobs (SCHEDULING.md updated).
