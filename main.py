import os
os.environ['TZ'] = 'Europe/Athens'

from app import app

# --- Defaults for NEW customers (override in Replit Secrets) ---
DEFAULT_TERMS_CODE = os.getenv("DEFAULT_TERMS_CODE", "POD")      # e.g., "POD", "COD" or "NET30"
DEFAULT_DUE_DAYS = int(os.getenv("DEFAULT_DUE_DAYS", "0"))       # 0 = POD/COD
DEFAULT_IS_CREDIT = os.getenv("DEFAULT_IS_CREDIT", "false").lower() in ("1","true","yes","y")

DEFAULT_ALLOW_CASH = os.getenv("DEFAULT_ALLOW_CASH", "true").lower() in ("1","true","yes","y")
DEFAULT_ALLOW_CARD_POS = os.getenv("DEFAULT_ALLOW_CARD_POS", "true").lower() in ("1","true","yes","y")
DEFAULT_ALLOW_BANK_TRANSFER = os.getenv("DEFAULT_ALLOW_BANK_TRANSFER", "true").lower() in ("1","true","yes","y")
DEFAULT_ALLOW_CHEQUE = os.getenv("DEFAULT_ALLOW_CHEQUE", "false").lower() in ("1","true","yes","y")

DEFAULT_CHEQUE_DAYS_ALLOWED = os.getenv("DEFAULT_CHEQUE_DAYS_ALLOWED")  # "0","15","30" or None
DEFAULT_CREDIT_LIMIT = os.getenv("DEFAULT_CREDIT_LIMIT")                # "0" or None
import routes  # noqa: F401
import routes_ai_analysis  # noqa: F401
import routes_operations  # noqa: F401
import routes_daily_reports  # noqa: F401
import routes_oi  # noqa: F401  # Operational Intelligence routes
import routes_time_analysis  # noqa: F401  # Time Analysis dashboard
import pytz
from timezone_utils import get_utc_now
from datetime import datetime, timezone

# Import is_production from app
from app import is_production

# Apply performance optimizations
# Note: Don't override SQLALCHEMY_ENGINE_OPTIONS here - it's already set in app.py with production optimizations
app.config.update({
    'DEBUG': False,
    'SESSION_COOKIE_HTTPONLY': True,
    'PERMANENT_SESSION_LIFETIME': 3600,
    'JSON_SORT_KEYS': False,
    'JSONIFY_PRETTYPRINT_REGULAR': False,
})

# Add template filter for timezone conversion
@app.template_filter('local_time')
def local_time_filter(dt, format_str='%d/%m/%y %H:%M'):
    """Display datetime in Athens timezone"""
    if dt is None:
        return 'N/A'
    
    athens_tz = pytz.timezone('Europe/Athens')
    
    # Database stores times in UTC
    athens_dt = dt.astimezone(athens_tz)
    return athens_dt.strftime(format_str)

# Add current time filter for real-time display
@app.template_filter('current_athens_time')
def current_athens_time_filter(placeholder, format_str='%d/%m/%y %H:%M:%S'):
    """Get current time in Athens timezone"""
    athens_tz = pytz.timezone('Europe/Athens')
    utc_now = get_utc_now()
    athens_now = utc_now.astimezone(athens_tz)
    return athens_now.strftime(format_str)

# Add template filter for status display
@app.template_filter('status_badge')
def status_badge_filter(status_value):
    """Display status as a styled badge"""
    from order_status_constants import get_status_info, get_status_badge_class, get_status_icon
    
    status_info = get_status_info(status_value)
    if not status_info:
        return f'<span class="badge bg-secondary">Unknown Status</span>'
    
    badge_class = get_status_badge_class(status_value)
    icon = get_status_icon(status_value)
    label = status_info['label']
    
    return f'<span class="badge {badge_class}"><i class="{icon} me-1"></i>{label}</span>'
from update_schema_skipped_items import update_database_schema
import logging
from routes_batch import batch_bp
from routes_shipments import shipments_bp
from routes_help import help_bp
from routes_delivery_issues import delivery_issues_bp
from routes_routes import bp as routes_bp
from routes_invoices import bp as route_invoices_bp
from routes_powersoft import bp_powersoft
from routes_delivery_dashboard import bp as delivery_dashboard_bp
from routes_driver_api import driver_api_bp
from routes_receipts import bp as receipts_bp
from routes_find_invoice import bp as find_invoice_bp

logging.basicConfig(level=logging.INFO)

# Register the batch picking blueprint
app.register_blueprint(batch_bp, url_prefix='')

# Register the delivery issues blueprint
app.register_blueprint(delivery_issues_bp, url_prefix='')

# Register the route management blueprints
app.register_blueprint(routes_bp, url_prefix='/routes')
app.register_blueprint(route_invoices_bp, url_prefix='/route-invoices')

# Register the delivery dashboard blueprint
app.register_blueprint(delivery_dashboard_bp, url_prefix='')

# Register the PS365 customer sync API blueprint
app.register_blueprint(bp_powersoft)

# Register the shipment management blueprint (conditionally based on feature flag)
with app.app_context():
    from app import db
    from models import Setting
    use_shipments_raw = Setting.get(db.session, 'use_shipments', 'false')
    use_shipments = str(use_shipments_raw).strip().lower() in ('true', '1', 'yes', 'on')
    
    if use_shipments:
        app.register_blueprint(shipments_bp, url_prefix='')
        logging.info("Shipments blueprint registered - feature enabled")
    else:
        logging.info("Shipments blueprint NOT registered - feature disabled")

# Register the help documentation blueprint
app.register_blueprint(help_bp, url_prefix='')

# Register the driver API blueprint
app.register_blueprint(driver_api_bp)

# Register the receipts blueprint
app.register_blueprint(receipts_bp)

# Register the find invoice/route blueprint
app.register_blueprint(find_invoice_bp)

# Register the payment terms blueprint
from routes_payment_terms import bp as payment_terms_bp
app.register_blueprint(payment_terms_bp)

# Register the driver app blueprint
from routes_driver import driver_bp
app.register_blueprint(driver_bp)

# Register the warehouse intake blueprint
from routes_warehouse_intake import warehouse_bp
app.register_blueprint(warehouse_bp)

# Register the PO receiving blueprint
from routes_po_receiving import po_receiving_bp
app.register_blueprint(po_receiving_bp)

# Register the item scanner blueprint
from routes_item_scanner import item_scanner_bp
app.register_blueprint(item_scanner_bp)

# Register the OI Time Estimator (ETC) admin blueprint
from routes_oi_time_admin import oi_time_admin_bp
app.register_blueprint(oi_time_admin_bp)

# Register the OI Reports blueprint
from routes_oi_reports import oi_reports_bp
app.register_blueprint(oi_reports_bp)

# Register the Admin Tools blueprint
try:
    from routes_admin_tools import bp as admin_tools_bp
    app.register_blueprint(admin_tools_bp)
except ValueError:
    logging.info("Admin Tools blueprint already registered")

# --- Helper function to create default payment terms ---
import datetime as dt
from decimal import Decimal

def _default_terms_values_for(code: str):
    """Generate default payment terms values for a new customer"""
    credit_limit_val = None
    if DEFAULT_CREDIT_LIMIT not in (None, "", "None"):
        try:
            credit_limit_val = Decimal(DEFAULT_CREDIT_LIMIT)
        except:
            credit_limit_val = None
    
    cheque_days_val = None
    if DEFAULT_CHEQUE_DAYS_ALLOWED not in (None, "", "None"):
        try:
            cheque_days_val = int(DEFAULT_CHEQUE_DAYS_ALLOWED)
        except:
            cheque_days_val = None
    
    return {
        "customer_code": code,
        "terms_code": DEFAULT_TERMS_CODE,
        "due_days": DEFAULT_DUE_DAYS,
        "is_credit": DEFAULT_IS_CREDIT,
        "credit_limit": credit_limit_val,
        "allow_cash": DEFAULT_ALLOW_CASH,
        "allow_card_pos": DEFAULT_ALLOW_CARD_POS,
        "allow_bank_transfer": DEFAULT_ALLOW_BANK_TRANSFER,
        "allow_cheque": DEFAULT_ALLOW_CHEQUE,
        "cheque_days_allowed": cheque_days_val,
        "valid_from": dt.date.today(),
        "notes_for_driver": None,
    }

# --- Auto-create default terms using SQLAlchemy event ---
from sqlalchemy import event
from sqlalchemy.sql import text
from models import PaymentCustomer, CreditTerms

@event.listens_for(PaymentCustomer, "after_insert")
def _create_default_terms_after_customer_insert(mapper, connection, target: PaymentCustomer):
    """
    Create a default active credit_terms row for the new customer
    IF there isn't already an active row (valid_to IS NULL).
    Uses a single SQL statement safe for SQLite/Postgres.
    
    NOTE: This is disabled during bulk sync operations to prevent timeouts.
    Use the "Reconcile Missing Terms" button to backfill after bulk imports.
    """
    # Skip auto-creation during bulk operations (PS365 sync)
    # The reconcile endpoint can be used to backfill afterward
    import threading
    skip_auto_create = getattr(threading.current_thread(), 'skip_auto_payment_terms', False)
    
    if skip_auto_create:
        return
    
    sql = text("""
        INSERT INTO credit_terms (
            customer_code, terms_code, due_days, is_credit,
            credit_limit, allow_cash, allow_card_pos, allow_bank_transfer, allow_cheque,
            cheque_days_allowed, notes_for_driver, valid_from, valid_to
        )
        SELECT
            :customer_code, :terms_code, :due_days, :is_credit,
            :credit_limit, :allow_cash, :allow_card_pos, :allow_bank_transfer, :allow_cheque,
            :cheque_days_allowed, :notes_for_driver, :valid_from, NULL
        WHERE NOT EXISTS (
            SELECT 1 FROM credit_terms
            WHERE customer_code = :customer_code AND valid_to IS NULL
        )
    """)
    params = _default_terms_values_for(target.code)
    params["customer_code"] = target.code
    connection.execute(sql, params)

# Run schema updates for both development and production
with app.app_context():
    # Update schema for skip and collect functionality
    try:
        update_database_schema()
    except Exception as e:
        logging.error(f"Error updating skip schema: {str(e)}")
    
    # Update schema for batch picking
    try:
        from update_batch_picking_schema import update_database_schema as update_batch_schema
        update_batch_schema()
    except Exception as e:
        logging.error(f"Error updating batch schema: {str(e)}")
        
    # Update schema to add batch_number field
    try:
        from update_batch_number_schema import update_database_schema as update_batch_number_schema
        update_batch_number_schema()
        logging.info("Batch number schema updates completed")
    except Exception as e:
        logging.error(f"Error updating batch number schema: {str(e)}")
        
    # Update schema to add unit_types field
    try:
        from update_unit_types_schema import update_unit_types_schema
        update_unit_types_schema()
        logging.info("Unit types schema updates completed")
    except Exception as e:
        logging.error(f"Error updating unit types schema: {str(e)}")
        
    # Update schema for item time tracking AI features
    try:
        from update_item_tracking_schema import update_item_tracking_schema
        update_item_tracking_schema()
        logging.info("Item tracking schema updates completed")
    except Exception as e:
        logging.error(f"Error updating item tracking schema: {str(e)}")
    
    # Update invoice status timestamp schema
    try:
        from update_invoice_status_timestamp import add_status_timestamp_column
        add_status_timestamp_column()
        logging.info("Invoice status timestamp schema updates completed")
    except Exception as e:
        logging.error(f"Error updating invoice status timestamp schema: {str(e)}")
    
    # Update RouteStop schema for contact fields
    try:
        from update_route_stop_schema import update_route_stop_schema
        update_route_stop_schema()
        logging.info("RouteStop schema updates completed")
    except Exception as e:
        logging.error(f"Error updating RouteStop schema: {str(e)}")
    
    # Update Shipment settlement schema
    try:
        from update_shipment_settlement_schema import update_shipment_settlement_schema
        update_shipment_settlement_schema()
        logging.info("Shipment settlement schema updates completed")
    except Exception as e:
        logging.error(f"Error updating Shipment settlement schema: {str(e)}")
    
    # Update Warehouse Intake schema (post-delivery cases, reroute, route history)
    try:
        from update_warehouse_intake_schema import update_warehouse_intake_schema
        update_warehouse_intake_schema()
        logging.info("Warehouse intake schema updates completed")
    except Exception as e:
        logging.error(f"Error updating warehouse intake schema: {str(e)}")
    
    # Update schema for Operational Intelligence (OI) module
    try:
        from update_oi_schema import update_oi_schema
        update_oi_schema()
        logging.info("OI schema updates completed")
    except Exception as e:
        logging.error(f"Error updating OI schema: {str(e)}")
    
    # Initialize remaining tables
    from app import db
    db.create_all()
    
    # Update to new order status system
    try:
        from update_order_status_system import update_order_status_system
        update_order_status_system()
        logging.info("Order status system migration completed")
    except Exception as e:
        logging.error(f"Error updating order status system: {str(e)}")
    
    # Initialize settings if they don't exist
    try:
        from models import Setting
        # Check if skip_reasons setting exists
        skip_reasons = Setting.query.filter_by(key='skip_reasons').first()
        if not skip_reasons:
            # Create default skip reasons
            import json
            default_reasons = ["Out of Stock", "Damaged", "Location Empty", "Other"]
            new_setting = Setting()
            new_setting.key = 'skip_reasons'
            new_setting.value = json.dumps(default_reasons)
            db.session.add(new_setting)
            db.session.commit()
            logging.info("Default skip reasons initialized")
    except Exception as e:
        logging.error(f"Error initializing settings: {str(e)}")
        db.session.rollback()

# Add download route for project export
from flask import send_file
import os as os_module

@app.route('/download-project-export')
def download_project_export():
    """Download the project export zip file"""
    file_path = '/home/runner/workspace/warehouse-system-export.zip'
    if os_module.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name='warehouse-system-export.zip')
    else:
        return "Export file not found. Please create it first.", 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
