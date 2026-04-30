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