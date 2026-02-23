# Warehouse Picking Management System

## Overview
This project is a comprehensive warehouse picking management system built with Flask and PostgreSQL. Its main purpose is to streamline order picking, batch processing, and time tracking within a warehouse environment. Key capabilities include real-time status updates, AI-powered insights for optimization, and robust delivery issue tracking. The system aims to enhance efficiency, reduce errors, and provide valuable analytics for warehouse operations.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### UI/UX Decisions
- **Frontend**: Jinja2 templating with a Bootstrap-based responsive interface.
- **Forms**: Server-side rendered forms with CSRF protection.

### Technical Implementations
- **Backend**: Flask (Python).
- **Database**: PostgreSQL (production), SQLite (development).
- **ORM**: SQLAlchemy with Flask-SQLAlchemy.
- **Authentication**: Flask-Login with role-based access control (`admin`, `picker`, `warehouse_manager`, `driver`).
- **Deployment**: Gunicorn.
- **Core Features**:
    - **Picking System**: Supports individual and batch picking, skip/collect later, real-time updates, and exception handling. Displays normalized item codes and barcodes.
    - **Time Tracking & Analytics**: Phase-based per-item time tracking for walking, picking, and confirmation. Supports shift management and KPI calculation.
    - **Batch Processing**: Zone/corridor-based batch creation, item locking, sequential/optimized picking modes.
    - **Delivery Issue Tracking**: Admin-only system for recording, validating, and resolving discrepancies with photo uploads and audit trails.
    - **Delivery Route Management**: Route planning, driver assignment, stop sequencing, invoice assignment, progress tracking, and printable run sheets. Includes warehouse collection.
    - **Driver App**: Mobile-optimized delivery execution with exception-only workflow, COD collection, thermal PNG receipt printing (BIXOLON SPP-R310 via Web Share API + mPrint), Proof of Delivery (POD) capture, and discrepancy integration.
    - **Route Reconciliation Report Pack**: Excel export system for comprehensive route reconciliation, including summary, invoice detail, stop summary, exceptions, and post-dated register.
    - **Return Handover Workflow**: Two-step confirmation process for failed deliveries (driver and warehouse staff).
    - **Discrepancy Verification Workflow**: Warehouse verification of delivery discrepancies to determine credit note requirements.
    - **Customer Payment Terms Management**: Tracks credit terms, payment methods, and financial limits with version history and import/export.
    - **PO Receiving**: Mobile-optimized receiving for purchase orders with PS365 integration, barcode scanning, dynamic PO modification, multi-lot support, and automated goods receipt submission.
    - **OI Dynamic Rules Engine**: Rule-based classification system for setting WMS attributes based on item fields. Supports auto-refresh upon changes.
    - **Palletization System**: Complete pallet management for delivery routes with visual 8-bit grid allocation, order-level hints, and packing profiles.
    - **SKU-Level Packing Profiles**: Derived pack_mode classification (DIRECT_PALLET, CARTON_HEAVY, CARTON_SMALL, OFF_PALLET) with carton estimates and warnings.
    - **Order Processing Flow**: Import (Excel) → Assignment → Picking → Completion → Shipping → Analytics.
    - **Invoice Import (PS365)**: Optimized single-pass synchronization logic with data normalization, batch lookups, and automated invoice total recalculation.
    - **Order Status Lifecycle**: `not_started` to `DELIVERED`/`RETURNED`/`DELIVERY_FAILED` with intermediate states for picking, packing, and dispatch.
    - **Route Status Lifecycle**: Three-phase lifecycle: Operational (`Shipment.status`), Reconciliation (`Shipment.reconciliation_status`), and Archiving.

### System Design Choices
- **UTC Timestamp Consistency**: All database timestamp writes use UTC; local timezone for display.
- **Performance Optimizations**: Connection pooling, query optimization, database tuning, Gunicorn configuration, and bulk SQL operations.
- **User Roles**: `admin`, `picker`, `warehouse_manager`, `driver` with specific access controls.
- **Delivery Dashboard**: Overview of dispatched routes with on-demand AJAX loading.
- **Data Integrity & Soft Delete System**: Soft deletes and status changes for critical entities to maintain data consistency and audit trails.
- **Find Invoice/Route**: Advanced search with filters, detailed invoice view, payment records, POD, routing history, and discrepancy details.
- **Customer Synchronization**: Dedicated screen for syncing customers from PS365 with bulk operations.
- **Customer 360 Analytics**: Interactive dashboard with KPIs, top items, invoice history, and Item-RFM analysis.
- **Net Value Calculation**: All net values calculated on-the-fly from line and header totals, not from stored columns.
- **Customer Benchmark**: Comparison of customer performance against peer groups, including White Space, Lapsed Items, Category Mix, Price vs Peers, and Item Recency analysis. Includes AI-powered feedback via OpenAI API with response caching (12h TTL in `ai_feedback_cache` table).
- **Pricing Analytics**: Customer-level pricing analysis with modules for Price Index vs Market, Price Dispersion, PVM, and Price Sensitivity signals.
- **Power BI Integration**: Database views providing a star-schema data model for Power BI reporting.

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
- **PS365**: Used for shelf location lookup, PO receiving, customer data synchronization, integrated receipt system, and zone synchronization.
- **SMTP Email**: Configured for sending supplier purchase orders.