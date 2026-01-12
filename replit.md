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
- **Authentication**: Flask-Login with role-based access control (`admin`, `picker`, `warehouse_manager`, `driver`). User management includes enable/disable functionality with automatic session invalidation for inactive accounts.
- **Deployment**: Gunicorn.
- **Core Models**: Users, Invoices, InvoiceItems, BatchPickingSession, ItemTimeTracking, DeliveryDiscrepancy, DeliveryDiscrepancyEvent, Settings, RouteStop, RouteStopInvoice, PSCustomer, Shipment, PaymentCustomer, CreditTerms, PurchaseOrder, PurchaseOrderLine, ReceivingSession, ReceivingLine.
- **Picking System**: Supports individual and batch picking, skip/collect later, real-time updates, and exception handling. Displays item codes and barcodes (normalized from PS365) in format "ITEM-CODE / BARCODE" for improved picker efficiency.
- **Time Tracking & Analytics**: Per-item time tracking, shift management, KPI calculation, AI analysis.
- **Batch Processing**: Zone/corridor-based batch creation, item locking, sequential/optimized picking modes.
- **Delivery Issue Tracking**: Admin-only system for recording, validating, and resolving discrepancies with photo uploads, configurable types, stock resolutions, and audit trails.
- **Delivery Route Management**: Comprehensive route planning, driver assignment, stop sequencing, invoice assignment, progress tracking, and printable run sheets. Includes warehouse collection option that automatically marks orders as DELIVERED for customer pickup scenarios.
- **Driver App**: Mobile-optimized delivery execution with exception-only delivery workflow, COD collection, receipt printing (A5 PDF via ReportLab), Proof of Delivery (POD) capture, delivery discrepancy integration, and settlement flow.
- **Customer Payment Terms Management**: Tracks credit terms, payment methods, and financial limits, with auto-creation for new customers, configurable defaults, version history, and Excel import/export.
- **PO Receiving**: Mobile-optimized warehouse receiving for purchase orders with PS365 integration for PO and shelf location lookup, barcode scanning (QuaggaJS), dynamic PO modification, multi-lot support, resume capability, conditional expiration date tracking (only required for items flagged by PS365), configurable receiving notes (e.g., "Wrong Barcode", "Barcode not in system", "New Product", "Repacking", "Needs Labels") with display on receiving screen and printouts, automatic goods receipt submission back to PS365 via order_pick_list API upon session completion, PO archiving for organizing completed orders, editable PO descriptions for better organization, smart re-import system with confirmation warnings, duplicate receiving prevention with reset capability, automatic row reordering (recently received items move to top of main order table), and printable PO sheets with actual barcodes from PS365 and notes displayed under barcodes.
- **Order Processing Flow**: Import (Excel) → Assignment → Picking → Completion → Shipping → Analytics.
- **Invoice Import (PS365)**: Optimized single-pass synchronization logic with data normalization (_norm_code, _norm_barcode), batch lookups for shelf locations and barcodes, and automated invoice total recalculation. Prevents duplicates and ensures high data integrity for barcodes.
- **Shelf Location Format**: Locations are stored as 7-character codes (e.g., "1006A01") and displayed as "CORRIDOR-SHELF-LEVEL BIN" (e.g., "10-06-A 01"). NULL locations default to "No Location" for sorting consistency.
- **Batch Processing Flow**: Creation → Locking → Assignment → Execution → Completion.
- **Order Status Lifecycle**: `not_started` → `picking` → `awaiting_batch_items` → `awaiting_packing` → `ready_for_dispatch` → `SHIPPED` → `OUT_FOR_DELIVERY` → `DELIVERED`/`RETURNED`/`DELIVERY_FAILED` → (`cancelled`/`returned_to_warehouse`). Note: Orders move to `awaiting_packing` after all items are picked, then to `ready_for_dispatch` only after packing is confirmed.
- **Route Status Lifecycle**: Three-phase lifecycle separating operational and administrative concerns:
  - **Operational Status** (`Shipment.status`): `created` → `PLANNED` → `DISPATCHED` → `IN_TRANSIT` → `COMPLETED` (or `CANCELLED`). Routes auto-complete when all RouteStopInvoice statuses are terminal (DELIVERED or FAILED).
  - **Reconciliation Status** (`Shipment.reconciliation_status`): `NOT_READY` → `PENDING` → `IN_REVIEW` → `RECONCILED`. Becomes PENDING when route completes; requires admin verification of cash, POD, returns, and discrepancies.
  - **Archiving** (`Shipment.is_archived`): Routes remain visible until reconciled and archived. `services_route_lifecycle.py` contains `recompute_route_completion()` (auto-triggered after driver actions), `reconcile_route()`, and `get_dashboard_routes()`.

### System Design Choices
- **UTC Timestamp Consistency**: All database timestamp writes use UTC via `utc_now_for_db()` helper from `timezone_utils.py`. Duration calculations performed in UTC to avoid DST issues. Athens timezone (`Europe/Athens`) used ONLY for display conversions via `to_athens_tz()` helper. Context fields (time_of_day, day_of_week, peak_hours) derived from local time for analytics.
- **Performance Optimizations**: Connection pooling, query optimization, database tuning, Gunicorn configuration. Bulk SQL operations for customer synchronization and payment terms reconciliation to prevent worker timeouts.
- **User Roles**: `admin`, `picker`, `warehouse_manager`, `driver` with specific access controls.
- **Delivery Dashboard**: Provides an overview of dispatched routes with on-demand loading via AJAX.
- **Data Integrity & Soft Delete System**: Implements soft deletes and status changes for critical entities with financial, audit, or proof-of-delivery dependencies to prevent hard deletion and maintain data consistency. Uses SQLAlchemy event listeners (`SoftDeleteMixin`, `ActivatableMixin`) to enforce policies.
- **Find Invoice/Route**: Advanced search interface with comprehensive filters, detailed invoice view including line items, payment records (COD), proof of delivery (POD), routing history, and delivery discrepancies. Supports compact A4 printing for all invoice details.
- **Customer Synchronization**: Dedicated screen under Operations menu for syncing customers from PS365 with bulk operations and automatic payment terms creation.

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
- **ReportLab**: PDF generation for receipts and reports.

### Database Dependencies
- **PostgreSQL 16**: Production database.

### Integrations
- **PS365**: Used for automatic shelf location lookup, PO receiving, customer data synchronization, and integrated receipt system.