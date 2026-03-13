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
    - **SMS Service**: Integrates with Microsms API for sending template-based SMS messages, including DLR webhook receiver.
    - **PS365 Sync Log**: Provides unified logging for all PS365 synchronization operations.

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
- **Supplier Forecast & Order Workbench**: A comprehensive forecasting and replenishment system integrating with `DwItem` supplier data, `ForecastItemSupplierMap`, and PS365 APIs for stock fetching. It includes demand classification, seasonality adjustments, and review flags.