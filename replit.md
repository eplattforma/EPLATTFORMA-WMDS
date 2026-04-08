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
    - **Customer & Order Management**: Features customer payment terms, PO receiving, intelligent rules engine for WMS attributes, palletization, SKU-level packing profiles, and a standardized order processing flow.
    - **Financials**: Manages invoice import, order/route status lifecycles, various receipt document types, bank statement import and matching, and live customer balance display.
    - **Forecasting & Ordering (Separated)**: Demand classification (smooth/erratic/intermittent/lumpy/new-sparse/no-demand), multi-method forecasting (MA8, Median6, Croston, ETS), trend detection, brand/prefix seasonality, and OOS-aware demand correction. The forecast engine excludes OOS-impacted weeks (3+ OOS days) from base calculations when sufficient clean data exists, with method-specific minimum clean weeks (smooth/erratic: 8, intermittent/lumpy: 6, new_sparse: 4). Trend is suppressed when recent 2 weeks are OOS-impacted. Visual OOS overlays on sales history charts (red bars for 3+ OOS days, amber for 1-2 days) with background shading and tooltip annotations. **Forecast and Ordering are separated**: forecast runs are forecast-only (no replenishment), ordering is an on-demand "Refresh Ordering" process that creates `SkuOrderingSnapshot` records with target stock = (weekly_forecast × target_weeks) + lead_time_cover + review_cycle_cover + buffer. Per-item `target_weeks_of_stock` is editable inline in the UI. All ordering data (on_hand, net_available, order quantities) is sourced from the latest ordering snapshot, not from forecast results. The "Refresh Ordering" button uses DB-backed job tracking (`ordering_refresh_jobs` table) with real-time progress polling (every 3s) so the UI only reloads data after the refresh fully completes, preventing stale zero-value display.
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
- **Playwright**: Browser automation for ERP export bot.