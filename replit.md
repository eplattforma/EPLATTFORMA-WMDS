# Warehouse Picking Management System

## Overview
This project is a comprehensive warehouse picking management system built with Flask and PostgreSQL. Its primary goal is to optimize warehouse operations by streamlining order picking, batch processing, and time tracking. Key functionalities include real-time status updates, AI-powered insights, and robust delivery issue management. The system aims to significantly enhance efficiency, reduce operational errors, and provide actionable analytics for warehouse managers.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### UI/UX Decisions
- **Frontend**: Utilizes Jinja2 templating with a Bootstrap-based responsive design.
- **Forms**: Server-side rendered forms are implemented with CSRF protection.

### Technical Implementations
- **Backend**: Developed using Flask (Python).
- **Database**: PostgreSQL for production, SQLite for development.
- **ORM**: SQLAlchemy, integrated via Flask-SQLAlchemy.
- **Authentication**: Flask-Login provides role-based access control for `admin`, `picker`, `warehouse_manager`, and `driver` roles.
- **Deployment**: Gunicorn is used for serving the application.
- **Core Features**:
    - **Picking System**: Supports individual and batch picking, skip/collect later functionality, real-time updates, and exception handling, displaying normalized item codes and barcodes.
    - **Time Tracking & Analytics**: Implements phase-based per-item time tracking (walking, picking, confirmation), shift management, and KPI calculation.
    - **Batch Processing**: Enables zone/corridor-based batch creation, item locking, and offers sequential/optimized picking modes.
    - **Delivery Issue Tracking**: An admin-only system for recording, validating, and resolving discrepancies, including photo uploads and audit trails.
    - **Delivery Route Management**: Features route planning, driver assignment, stop sequencing, invoice assignment, progress tracking, and printable run sheets, including warehouse collection.
    - **Driver App**: A mobile-optimized interface for delivery execution with a 4-step guided closeout wizard, sticky header for collected amounts, COD collection, thermal receipt printing, Proof of Delivery (POD) capture, and discrepancy integration.
    - **Route Reconciliation Report Pack**: Generates comprehensive Excel exports for route reconciliation.
    - **Return Handover Workflow**: A two-step confirmation process for failed deliveries.
    - **Discrepancy Verification Workflow**: Facilitates warehouse verification of delivery discrepancies for credit note decisions.
    - **Customer Payment Terms Management**: Tracks credit terms, payment methods, and financial limits with version history.
    - **PO Receiving**: Mobile-optimized receiving for purchase orders with barcode scanning, dynamic PO modification, and automated goods receipt submission.
    - **OI Dynamic Rules Engine**: A rule-based classification system for setting WMS attributes based on item fields.
    - **Palletization System**: Manages pallets for delivery routes with visual allocation and packing profiles.
    - **SKU-Level Packing Profiles**: Classifies pack modes and estimates carton requirements.
    - **Order Processing Flow**: Standardized flow from import to analytics.
    - **Invoice Import (PS365)**: Optimized synchronization logic for invoices, including data normalization, batch barcode lookups, and automated total recalculation.
    - **Order Status Lifecycle**: Manages orders through various states from `not_started` to `DELIVERED`/`RETURNED`/`DELIVERY_FAILED`.
    - **Route Status Lifecycle**: A three-phase lifecycle covering operational, reconciliation, and archiving stages.
    - **Receipt Document Types**: Supports various COD receipt types with a DRAFT→ISSUED→VOIDED lifecycle and integration for online notices.
    - **Bank Statement Import & Matching**: Allows CSV/Excel bank statement uploads for auto-matching credit transactions to pending payments.
    - **Customer Balance (PS365 Statement)**: Displays live customer account balances from PS365 with caching and a comprehensive report page.
    - **Replenishment MVP**: Proposes case quantities to order per supplier based on stock, sales averages, and safety stock, with a multi-tiered forecast fallback system.
    - **SMS Service**: Integrates with Microsms API for sending template-based SMS messages, including DLR webhook receiver. Templates support channel flags (MicroSMS, Phone SMS, Call, WhatsApp, Viber), call scripts, bulk send allowance, and sort ordering.
    - **Communications Hub**: Unified multi-channel customer communications at `/admin/communications`. Supports 6 channels: MicroSMS (API-sent), Phone SMS, Phone Call, WhatsApp, Viber (last 4 via launch URLs), and OneSignal Push Notifications. Features: per-customer compose page with template selection and preview, call outcome modal, live communication history refresh, bulk send from Review Ordering page, push notification with title/URL fields and real-time subscription verification. Tables: `crm_communication_log` (individual messages with DLR tracking), `crm_communication_batch` (bulk operations), `customer_push_identity` (OneSignal push subscription cache). Service layer in `services/communications_service.py` with phone normalization, template rendering, dual-write to legacy `sms_log`. Push service in `services/onesignal_service.py` with user lookup, subscription verification, push send via OneSignal API, and identity caching. DLR handler updates both `sms_log` and `crm_communication_log`. Template `allow_onesignal_push` channel flag with badge in template list.
    - **PS365 Sync Log**: Provides unified logging for all PS365 synchronization operations.
    - **PS365 Pending Orders Import**: Full snapshot sync of pending orders from PS365 `list_pending_orders_header` API. Stores raw orders in `ps_pending_orders_header`, aggregates per-customer totals in `crm_customer_open_orders`. Scheduled every 30 minutes + manual refresh button on CRM dashboard. Uses DB-based locking (`sync_job_lock`) and audit logging (`sync_job_log`). Dashboard shows Open Orders count and On Orders (€) KPI cards plus per-row order badges.
    - **CRM Dashboard**: Branded "EP SmartGrowth CRM" at `/crm/dashboard`. Features customer activity monitoring, classification management with images, delivery slot filtering, district-based filtering via postal code lookup, accordion-style expandable detail rows with color-coded chips, task/interaction logging, timeline modal, and open orders integration. **Performance-optimized** with: DISTINCT count for pagination, lighter KPI subquery (separate from main query), SQL-side sort with secondary tiebreaker, Python-side eval only for `cycle`/`action` sorts or `action_only` filter, consolidated single invoice aggregate subquery, indexed JOIN for slot filtering, index-friendly district filter, separate DISTINCT queries for filter option lists, server-side pagination (100 rows/page default, 25-500 range), and page-based KPI cards (action/done counts are page-scoped, cart/orders are global). **Classification system**: Normalized dict format `{"name": {"icon": "file.png", "color": null, "sort_order": null}}` with `_normalize_classifications()` handling legacy list/string/dict formats. Icons stored as base64 in DB (`crm_classification_images_b64`) with local file cache; served with correct MIME types via `mimetypes.guess_type()`. Path traversal protection on image serve route. **Ordering Window System** (`services/crm_order_window.py`): DONE badge is based on per-customer delivery slot schedule — calculates next delivery date by ISO week parity + day-of-week, then opens an ordering window N working hours before (excluding weekends). Settings: `crm_order_window_hours` (default 48), `crm_delivery_anchor_time` (default 00:01), `crm_order_window_close_hours` (default 0), `crm_delivery_close_anchor_time` (default 00:01). DONE = has open orders OR invoice date >= window open date. Next action (CART_NUDGE / ORDER_REMINDER) only triggers when window is open. Cycle group headers only rendered when sort=cycle. **Default Filters**: Admin can mark classifications as "default" in CRM Settings; dashboard auto-applies them on load. **Admin Menu**: "CRM Settings" (under Admin) manages classification labels, icons, and default filter selection.

### System Design Choices
- **UTC Timestamp Consistency**: All database timestamps are stored in UTC.
- **Performance Optimizations**: Implements connection pooling, query optimization, and Gunicorn tuning.
- **User Roles**: Defines distinct access levels for `admin`, `picker`, `warehouse_manager`, and `driver`.
- **Delivery Dashboard**: Offers an overview of dispatched routes with on-demand AJAX loading.
- **Data Integrity & Soft Delete System**: Utilizes soft deletes and status changes for critical entities.
- **Find Invoice/Route**: Provides advanced search capabilities with detailed views.
- **Customer Synchronization**: Dedicated screen for syncing customer data from PS365.
- **Customer 360 Analytics**: An interactive dashboard for KPIs, invoice history, Item-RFM analysis, and Magento abandoned cart status badge with live refresh.
- **Abandoned Carts (Magento)**: Pulls live abandoned cart data from Magento via OAuth1 REST API. Standalone browse page at `/customers/abandoned-carts` and per-customer badge integrated into Customer 360. Links PSCustomer to Magento via `customer_code_secondary` (Magento customer ID). Model: `CrmAbandonedCartState`.
- **Net Value Calculation**: Net values are calculated dynamically from line and header totals.
- **Customer Benchmark**: Compares customer performance against peer groups with AI-powered feedback.
- **Pricing Analytics**: Offers customer-level pricing analysis including Price Index, Dispersion, and Sensitivity signals.
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
- **PS365**: Used for shelf location, PO receiving, customer data sync, integrated receipts, zone sync, and customer statement of account balance lookups.
- **SMTP Email**: Configured for sending supplier purchase orders.
- **Microsms API**: For SMS sending and delivery report handling.
- **Power BI**: For business intelligence reporting.

### Forecast Workbench
- **Supplier Forecast & Order Workbench**: A comprehensive forecasting and replenishment system integrating with `DwItem` supplier data, `ForecastItemSupplierMap`, and PS365 APIs for stock fetching. It includes demand classification, seasonality adjustments, and review flags.    - **Review Ordering (5-State)**: Enhanced review page at `/crm/review-ordering` with 5 computed states: `follow_up` (assisted/cart/login-during-window/expected/manual flag), `waiting` (no signals), `ordered` (has open orders), `ordered_cart` (ordered + active cart), `done` (manually marked). **Model**: `CrmOrderingReview` (per customer+delivery_date) stores `review_state`, `outcome_reason`, `expected_this_cycle`, `manual_follow_up_flag`, `review_note`. `CrmCustomerProfile.assisted_ordering` boolean flag. **UI**: Flat table sorted by state priority then delivery date then cart amount then login/invoice days. KPI bar with per-state counts. Filter bar with search, state, classification, district, ordered/not-ordered, assisted-only, expected-only, has-cart-only, login-days. Right-side drawer panel for per-customer detail with current cycle info, manual flags (assisted ordering toggle, expected this cycle toggle, short note), state actions (Follow Up / Done with outcome reason selector). **API endpoints**: `POST /crm/review-ordering/update-state`, `POST /crm/review-ordering/update-flags`, `POST /crm/review-ordering/set-assisted`. Cart mode computed as `pending_order` or `add_on`. SMS compose modal integrated.
