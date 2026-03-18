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
    - **Offer Intelligence**: Imports customer-specific pricing from Magento CSV (via FTP in production), enriches with cost/margin/sales data from `ps_items_dw` and `dw_sales_lines_v` (columns: `qty`, `net_excl`). Features: multi-tab drawer UI (Summary with generated sentence + rules breakdown, Opportunities, Margin Watch, All Offers), offer chips on dashboard/review-ordering tables, KPI cards, configurable settings (margin thresholds, cost source), refresh locking via `sync_job_lock`, email fallback customer resolver, and unresolved tracking. All read-only helpers use `db.engine.connect()` for transaction isolation. Schema: `crm_customer_offer_import_batch`, `crm_customer_offer_raw`, `crm_offer_rule_dim`, `crm_customer_offer_current`, `crm_customer_offer_summary_current`, `crm_customer_offer_unresolved`.
    - **Synchronization & Data Refresh**: Automated FTP login sync, PS365 sync logging, and pending order import from PS365.
    - **CRM Dashboard**: A central dashboard for customer activity monitoring, classification management, delivery slot filtering, task logging, and open orders integration, optimized for performance. Includes an Ordering Window System for managing customer ordering cycles.
    - **Review Ordering (5-State)**: An enhanced review page for managing customer orders with computed states (follow_up, waiting, ordered, ordered_cart, done), integrated with customer profiles and communication tools.

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