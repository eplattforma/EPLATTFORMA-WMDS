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
    - **Offer Intelligence**: Imports customer-specific pricing from Magento CSV (via FTP in production), enriches with cost/margin/sales data from `ps_items_dw` and `dw_sales_lines_v` (columns: `qty`, `net_excl`). Primary metrics are **offer usage** (bought/total SKUs, usage %) and **sales dependency** (offer sales share of total customer sales 4w). Features: multi-tab drawer UI (Summary with Offer Usage / Sales Dependency / Pricing sections, Unused tab, Sales Dep. tab, All Offers), offer chips on dashboard/review-ordering tables showing `bought/total` format with usage-based states (used≥75%, mixed≥25%, low_usage<25%, unused=0%), KPI cards (With Offers, Avg Usage %, Offer Sales 4w, High Dependency), configurable settings (margin thresholds, cost source), refresh locking via `sync_job_lock`, email fallback customer resolver, and unresolved tracking. All read-only helpers use `db.engine.connect()` for transaction isolation. Summary columns: `offer_usage_pct`, `offer_sales_share_pct`, `total_customer_sales_4w`. Schema: `crm_customer_offer_import_batch`, `crm_customer_offer_raw`, `crm_offer_rule_dim`, `crm_customer_offer_current`, `crm_customer_offer_summary_current`, `crm_customer_offer_unresolved`. **Admin Offers page** (`/crm/admin/offers`): cross-customer analytics with 4 tabs (Overview KPIs/distributions/alerts, Customers table, Rules breakdown, Products analysis), filterable by classification/district/supplier/category/brand/usage-band/sales-band/rule-name, sortable columns, pagination, CSV export. Rules are clickable links to rule detail page. **Rule Detail page** (`/crm/admin/offers/rule/<rule_code>`): deep-dive into one offer/rule with header summary, KPI strip, Products tab (price vs cost review: normal price, offer price, discount %, cost, GP, margin %; filters: supplier/category/brand/line_status/low_margin/negative_margin/missing_cost/unused_only), Customers tab (usage, sales share, margin; filters: classification/district/zero_usage/high_dependency), Insights tab (weak products, zero-usage customers, top products). CSV export per tab. Rule lookup JSON endpoint at `/crm/admin/offers/rules/lookup`. Service layer: `services/crm_offer_admin.py`. Nav: under Admin dropdown.
    - **DW Cost Enrichment**: Invoice lines in the data warehouse (`dw_invoice_line`) for 2026+ invoices are enriched with cost snapshot fields (`unit_cost_snapshot`, `line_cost_total`, `gross_profit`, `gross_margin_pct`, `cost_source`, `cost_snapshot_at`) sourced from `DwItem.cost_price`. Pre-2026 invoices remain with null cost fields. Operator-triggered invoice sync (`background_sync.py`) automatically triggers a lightweight DW catch-up after successful operational sync — single-invoice catch-up calls `sync_invoice_to_dw()` directly by invoice number (no date resolution dependency), date-based catch-up uses `sync_invoices_from_date()`. Both skip MV refresh. `sync_invoice_to_dw()` is fully targeted: scoped header/line preload by invoice number, `_sync_single_invoice_header` for header insert, `_sync_invoice_store_for_header` / `_sync_invoice_cashier_for_header` for store/cashier upsert (no global distinct scan). Scoped preload helpers use `_coerce_to_date()` for robust date comparison. `sync_invoice_headers_from_date()` uses preloaded set for duplicate check (no N+1 per-row queries). `sync_invoices_from_date()` does not close caller-owned session on error. Nightly DW cron retains full MV refresh and global store/cashier sync.
    - **Synchronization & Data Refresh**: Automated FTP login sync, PS365 sync logging, and pending order import from PS365.
    - **CRM Dashboard**: A central dashboard for customer activity monitoring, classification management, delivery slot filtering, task logging, and open orders integration, optimized for performance. Includes an Ordering Window System for managing customer ordering cycles.
    - **Review Ordering (4-State)**: An enhanced review page for managing customer orders with computed states (follow_up, waiting, ordered, close). Within each state, rows with carts sort to the top. Integrated with customer profiles and communication tools.

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
- **PS365**: Used for shelf location, PO receiving, customer data sync, integrated receipts, zone sync, pending orders, and customer statement of account balance lookups.
- **SMTP Email**: For sending supplier purchase orders.
- **Microsms API**: For SMS sending and delivery report handling.
- **OneSignal**: For push notifications.
- **Power BI**: For business intelligence reporting.
- **Magento/BSS**: For customer pricing and abandoned cart data.