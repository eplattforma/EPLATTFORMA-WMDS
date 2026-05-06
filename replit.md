# Warehouse Picking Management System

## Overview
This project is a comprehensive warehouse picking management system built with Flask and PostgreSQL. Its primary goal is to optimize warehouse operations by streamlining order picking, batch processing, and time tracking. The system aims to significantly enhance efficiency, reduce operational errors, and provide actionable analytics for warehouse managers. Key capabilities include real-time status updates, AI-powered insights, and robust delivery issue management, contributing to a more efficient and data-driven warehouse environment. The system also includes advanced forecasting and ordering capabilities, customer relationship management (CRM) tools, and offer intelligence to support sales and financial operations.

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
    - **Picking System**: Supports individual and batch picking, skip/collect later functionality, real-time updates, and exception handling. Includes a refactored batch picking system with DB-backed queues, and cooler picking capabilities for temperature-sensitive items.
    - **Time Tracking & Analytics**: Implements phase-based per-item time tracking, shift management, and KPI calculation.
    - **Delivery Management**: Includes issue tracking, route planning, driver assignment, progress tracking, and a mobile-optimized driver app with Proof of Delivery (POD) and discrepancy integration.
    - **Return & Discrepancy Workflows**: Provides structured processes for handling failed deliveries and verifying discrepancies.
    - **Customer & Order Management**: Features customer payment terms, PO receiving with desktop entry mode, intelligent rules engine for WMS attributes, palletization, SKU-level packing profiles, and a standardized order processing flow. Includes an enhanced review page for managing customer orders with computed states.
    - **Financials**: Manages invoice import, order/route status lifecycles, various receipt document types, bank statement import and matching, and live customer balance display.
    - **Forecasting & Ordering**: Features multi-method demand forecasting, trend detection, seasonality hierarchy, and OOS-aware demand correction. Includes a forecast override system for planners and a separated ordering process that generates `SkuOrderingSnapshot` records. The pipeline is optimized for performance and resilience with a watchdog.
    - **Communications Hub**: A unified multi-channel platform (SMS, push notifications, call scripts) with template-based messages and DLR handling.
    - **Offer Intelligence**: Imports customer-specific pricing, enriches with cost/margin/sales data, and provides analytics on offer usage and sales dependency. Includes SMS integration for sending offers.
    - **DW Cost Enrichment & Dropbox Import**: Enriches data warehouse invoice lines with cost snapshots and imports item costs from Dropbox.
    - **PS365 OOS Daily Sync**: Daily synchronization of Out-Of-Stock (OOS) items for Store 777 (Eshop).
    - **Synchronization & Data Refresh**: Automated FTP login sync, PS365 sync logging, and pending order import.
    - **CRM Dashboard**: A central dashboard for customer activity monitoring, classification management, delivery slot filtering, task logging, and open orders integration.
    - **Scheduler Management**: Every scheduled job is wrapped for tracking, logging, and progress reporting. Includes an admin UI for managing jobs (reschedule, pause, resume, run now) and a log cleanup service. Boot-time registration in `scheduler.setup_scheduler` preserves user-edited triggers persisted in `apscheduler_jobs` (via the `_add_job_smart` helper) — code-default `CronTrigger(...)` values only apply on first registration; subsequent edits via the UI survive worker boots. To force a code default to take effect, delete the row from `apscheduler_jobs` for that job id.
    - **Account-Manager Cockpit**: A new module for account managers featuring customer spend targets, performance tracking, offer opportunities, an activity timeline, and a Greek-language Claude-powered Recommended Actions panel + per-section Ask Claude advice (gated by `customers.ask_claude`; requires `ANTHROPIC_API_KEY` secret).

### System Design Choices
- **UTC Timestamp Consistency**: All database timestamps are stored in UTC.
- **Performance Optimizations**: Implements connection pooling, query optimization, and Gunicorn tuning.
- **User Roles**: Defines distinct access levels for various user types. Permissions are managed via a dedicated service with role-based fallback and per-user editing.
- **Delivery Dashboard**: Offers an overview of dispatched routes with on-demand AJAX loading.
- **Data Integrity**: Utilizes soft deletes and status changes for critical entities.
- **Advanced Search**: Provides advanced search capabilities for invoices and routes.
- **Customer Synchronization & Analytics**: Dedicated screens for syncing customer data, a 360-degree analytics dashboard, abandoned cart tracking, and customer benchmarking with AI-powered feedback.
- **Pricing Analytics**: Offers customer-level pricing analysis.
- **Power BI Integration**: Provides database views for Power BI reporting.
- **Additive Schema Changes**: All schema updates are additive and idempotent to ensure smooth migrations.
- **Feature Flagging**: High-risk behaviors and new features are controlled by feature flags, enabled by default only for safe functionalities.

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
- **cachetools**: Caching utility.

### Database Dependencies
- **PostgreSQL 16**: Production database.

### Integrations
- **PS365**: Used for shelf location, PO receiving, customer data sync, integrated receipts, zone sync, pending orders, customer statement of account balance lookups, and daily stock availability sync for Store 777 (Eshop).
- **SMTP Email**: For sending supplier purchase orders.
- **Microsms API**: For SMS sending and delivery report handling.
- **OneSignal**: For push notifications.
- **Power BI**: For business intelligence reporting.
- **Magento/BSS**: For customer pricing and abandoned cart data.
- **Playwright**: Browser automation for ERP export bot (e.g., item costs).
- **APScheduler**: Background scheduler for managing cron jobs.