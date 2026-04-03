# Warehouse Picking Management System

## Overview
This project is a comprehensive warehouse picking management system built with Flask and PostgreSQL. Its primary goal is to optimize warehouse operations by streamlining order picking, batch processing, and time tracking. The system aims to significantly enhance efficiency, reduce operational errors, and provide actionable analytics for warehouse managers. Key capabilities include real-time status updates, AI-powered insights, and robust delivery issue management.

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
    - **Delivery Management**: Includes issue tracking, route planning, driver assignment, progress tracking, and a mobile-optimized driver app for delivery execution with POD capture and discrepancy integration.
    - **Return & Discrepancy Workflows**: Provides structured processes for handling failed deliveries and verifying discrepancies.
    - **Customer & Order Management**: Features customer payment terms, PO receiving, an intelligent rules engine for WMS attributes, palletization, SKU-level packing profiles, and a standardized order processing flow from import to analytics.
    - **Financials**: Manages invoice import, order/route status lifecycles, various receipt document types, bank statement import and matching, and live customer balance display.
    - **Replenishment**: MVP for suggesting case quantities per supplier based on stock, sales averages, and safety stock.
    - **Communications Hub**: A unified multi-channel platform (SMS, push notifications, call scripts) for customer communications, supporting template-based messages and DLR handling. Includes a comprehensive dashboard for managing and tracking customer interactions.
    - **Offer Intelligence**: Imports customer-specific pricing from Magento CSV (via FTP in production), enriches with cost/margin/sales data from `ps_items_dw` and `dw_sales_lines_v` (columns: `qty`, `net_excl`). Primary metrics are **offer usage** (bought/total SKUs, usage %) and **sales dependency** (offer sales share of total customer sales 4w). Features: multi-tab drawer UI with 4 redesigned tabs: **Offer Summary** (Sales Dependency / Pricing / Rules Breakdown sections), **Unused Offers** (minimal Product + Offer columns), **Offer-Driven Sales** (Product, Offer, Sold 4W, Value — sorted by Value desc), **All Active Offers** (Select checkbox, Product, Offer Price — with Send SMS button for composing/sending offer SMS via Microsat). 5 KPI cards: SKUs, Usage, Sales 4W, Total Sales, Sales Share. SMS integration: SMS compose modal with character count, segment estimation, auto-prefill from selected offers; sends via existing `communications_service.send_microsms`; mobile resolved server-side from `ps_customers`; logged to `crm_communication_log` with `source_screen=offer_popup_screen_4`. Endpoint: `POST /crm/customer/<code>/offer-sms`. Offer chips on dashboard/review-ordering tables showing `bought/total` format with usage-based states (used≥75%, mixed≥25%, low_usage<25%, unused=0%), configurable settings (margin thresholds, cost source), refresh locking via `sync_job_lock`, email fallback customer resolver, and unresolved tracking. All read-only helpers use `db.engine.connect()` for transaction isolation. Summary columns: `offer_usage_pct`, `offer_sales_share_pct`, `total_customer_sales_4w`. Schema: `crm_customer_offer_import_batch`, `crm_customer_offer_raw`, `crm_offer_rule_dim`, `crm_customer_offer_current`, `crm_customer_offer_summary_current`, `crm_customer_offer_unresolved`. **Admin Offers page** (`/crm/admin/offers`): cross-customer analytics with 4 tabs (Overview KPIs/distributions/alerts, Customers table, Rules breakdown, Products analysis), filterable by classification/district/supplier/category/brand/usage-band/sales-band/rule-name, sortable columns, pagination, CSV export. Rules are clickable links to rule detail page. **Rule Detail page** (`/crm/admin/offers/rule/<rule_code>`): deep-dive into one offer/rule with header summary, KPI strip, Products tab (price vs cost review: normal price, offer price, discount %, cost, GP, margin %; filters: supplier/category/brand/line_status/low_margin/negative_margin/missing_cost/unused_only), Customers tab (usage, sales share, margin; filters: classification/district/zero_usage/high_dependency), Insights tab (weak products, zero-usage customers, top products). CSV export per tab. Rule lookup JSON endpoint at `/crm/admin/offers/rules/lookup`. Service layer: `services/crm_offer_admin.py`. Nav: under Admin dropdown.
    - **DW Cost Enrichment**: Invoice lines in the data warehouse (`dw_invoice_line`) for 2026+ invoices are enriched with cost snapshot fields (`unit_cost_snapshot`, `line_cost_total`, `gross_profit`, `gross_margin_pct`, `cost_source`, `cost_snapshot_at`) sourced from `DwItem.cost_price`. Pre-2026 invoices remain with null cost fields. Operator-triggered invoice sync (`background_sync.py`) automatically triggers a lightweight DW catch-up after successful operational sync — single-invoice catch-up calls `sync_invoice_to_dw()` directly by invoice number (no date resolution dependency), date-based catch-up uses `sync_invoices_from_date()`. Both skip MV refresh. `sync_invoice_to_dw()` is fully targeted: scoped header/line preload by invoice number, `_sync_single_invoice_header` for header insert, `_sync_invoice_store_for_header` / `_sync_invoice_cashier_for_header` for store/cashier upsert (no global distinct scan). Scoped preload helpers use `_coerce_to_date()` for robust date comparison. `sync_invoice_headers_from_date()` uses preloaded set for duplicate check (no N+1 per-row queries). `sync_invoices_from_date()` does not close caller-owned session on error. Nightly DW cron retains full MV refresh and global store/cashier sync.
    - **Dropbox Cost Import**: Dedicated Dropbox → `ps_items_dw.cost_price` import pipeline. Downloads `items.xlsx` from Dropbox path (env: `DROPBOX_FILE_PATH`), auto-detects "Item Code" and "Cost" columns in Excel header, matches each row to `DwItem.item_code_365`, updates only `cost_price` — no new records created, no other fields touched, blank costs skipped. OAuth2 code flow with offline refresh tokens, Fernet-encrypted at rest (key from SESSION_SECRET). Admin UI at `/admin/integrations/dropbox` shows connection status, import summary (rows read/matched/updated/skipped/unmatched), unmatched item codes list, and import history. Service: `services/dropbox_service.py` (`_cost_import_processor`). Route: `routes_dropbox.py` (blueprint `dropbox_integration`). CLI: `python -m jobs.dropbox_sync`. Import metadata stored in `external_file_sync_log.metadata_json` (rows_read, rows_matched, rows_updated, rows_skipped_blank_cost, rows_skipped_no_code, parse_errors, unmatched_count, unmatched_codes). All sync statuses: success, success_no_change, auth_error, download_error, parse_error, config_error, running, skipped_concurrent. Secrets: `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REDIRECT_URI`, `DROPBOX_FILE_PATH`.
    - **PS365 OOS 777 Daily Sync**: Exception-based daily OOS snapshot for Store 777 (Eshop). Service: `services/ps365_stock_777_service.py`. Tables: `ps365_stock_777_runs` (run log), `ps365_oos_777_daily` (only active items with `available_qty <= 0`, unique on `snapshot_date + item_code_365`). Uses `list_stock_items_store` GET endpoint, enriches with DwItem metadata (supplier, barcode, active status). Only inserts active OOS items — keeps the table small (exceptions only, not the full stock universe). Computes `available_qty = max(stock - stock_reserved, 0)`; OOS = `available_qty <= 0`. Scheduled daily at 5:30 AM. Used for sales review, forecast anomaly protection, and OOS analysis. Old tables `ps365_stock_snapshot_777_daily` and `ps365_stock_777_current` are retired (historical data left in place).
    - **Synchronization & Data Refresh**: Automated FTP login sync, PS365 sync logging, and pending order import from PS365.
    - **CRM Dashboard**: A central dashboard for customer activity monitoring, classification management, delivery slot filtering, task logging, and open orders integration, optimized for performance. Includes an Ordering Window System for managing customer ordering cycles.
    - **Review Ordering (4-State)**: An enhanced review page for managing customer orders with computed states (follow_up, waiting, ordered, close). Within each state, rows with carts sort to the top. Integrated with customer profiles and communication tools. Filters: Assisted (assisted_only), Cart (has_cart_only), Has Messages (action_only—shows only customers who have received messages), Classification, District, Delivery Slot, State (follow_up/waiting/ordered/exclude), Show All Customers (show_all—bypasses open-window filter).
    - **Delivery Date Override**: Bulk temporary delivery date reassignment from the Review Ordering dashboard. Model: `CustomerDeliveryDateOverride` with partial unique index (active per customer+original_date). Service: `services/crm_delivery_overrides.py` (resolve_effective_delivery, apply/clear overrides). Endpoints: `POST /crm/api/delivery-overrides/bulk-assign` (date, reason, notes) and `POST /crm/api/delivery-overrides/bulk-clear`. Reason codes: holiday, weather, logistics, customer_request, route_change, other. UI: "Move Delivery" / "Clear Moved" bulk action buttons, assign modal with date picker + reason dropdown + notes, "Moved" badge on delivery column, override detail section in customer drawer with clear button. Overrides recalculate effective window open/close times. **Show All Customers mode** (`?show_all=1`): toggle button bypasses the open-window filter to show all customers with delivery slots, so overrides can be assigned to any customer. Closed-window rows display a grey clock badge ("Ordering window closed"). Open-windows header chips and KPI counts only reflect truly open windows regardless of show_all mode.

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
- **Playwright**: Browser automation for ERP export bot.

### ERP Export Bot
Playwright-based unattended browser automation for Powersoft365 ERP data exports. Login URL: `https://accpr.powersoft365.com/` (redirects to `accv3.powersoft365.com` after auth). Login selectors: `#ContentMasterMain_txtUserName`, `#ContentMasterMain_txtPassword`, `#ContentMasterMain_btnLogin_CD` (DevExpress button wrapper). Framework: `services/erp_export_bot.py` (orchestration), `services/erp_export_flows/` (pluggable export flows). Admin UI: `/admin/erp-bot/`. CLI: `python -m jobs.erp_export_job --export <name>`. Model: `BotRunLog`. Auth state persisted in `data/erp_auth_state/`. Downloads to `data/erp_exports/`. Failure screenshots in `data/erp_screenshots/`. Secrets: `ERP_USERNAME`, `ERP_PASSWORD`, `ERP_HEADLESS`. System deps: nss, nspr, libxkbcommon, mesa, gtk3, etc. (for Chromium). Working flows: (1) `stock_position` — navigates to Stock Control > Reports > Stock Control > Serials Stock Report (`repPowerSerials.aspx`), generates report (DevExpress Report Viewer), exports as XLSX via toolbar dropdown (`[title="Export To"]` > `.dxrd-preview-export-menu-item` with text "XLSX"), captures download via Playwright download event. Output: `stock_position_<timestamp>.xlsx` (~129KB, ~3700 rows). Post-process: truncates/inserts into `stock_positions` table. (2) `item_catalogue` — navigates to Item Catalogue (`repPowerItemCatalogue.aspx`), opens Report Parameters popup, checks "Cost" checkbox (`#ContentMasterMain_ContentMasterReports_popupParms_chkPShowCost_S_D`), saves, clicks Export (xlsx preset). Output: `item_catalogue_<timestamp>.xlsx` (~137KB, ~2558 items). Post-process: parses `€X.XX` cost values, updates `ps_items_dw.cost_price` by matching `item_code_365`. Route: `/admin/erp-bot/refresh-item-costs` (POST). Adding new flows: create a class in `services/erp_export_flows/` extending `BaseExportFlow`, register in `__init__.py`.