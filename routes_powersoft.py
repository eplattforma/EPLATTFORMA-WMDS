"""
Routes for PS365 Customer Sync API
Provides endpoints for bulk sync and single customer operations
"""
from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user
from functools import wraps
from services_powersoft import (
    sync_active_customers,
    upsert_single_customer,
    get_customer_by_code,
    sync_invoices_from_ps365
)
from background_sync import (
    start_invoice_sync_background,
    start_customer_sync_background,
    get_sync_status,
    is_sync_running
)

bp_powersoft = Blueprint('powersoft', __name__, url_prefix='/api/powersoft')

def admin_required(f):
    """Decorator to require admin or warehouse_manager role - returns JSON for API routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"success": False, "error": "Session expired. Please refresh the page and log in again."}), 401
        if current_user.role not in ['admin', 'warehouse_manager']:
            return jsonify({"success": False, "error": "Access denied. Admin or warehouse manager role required."}), 403
        return f(*args, **kwargs)
    return decorated_function

@bp_powersoft.route('/sync/customers', methods=['POST'])
@admin_required
def sync_customers():
    """
    Bulk sync all active customers from PS365 (runs in background to avoid timeout)
    
    Returns:
        JSON with sync status
    
    Example:
        POST /api/powersoft/sync/customers
        Response: {"success": true, "message": "Customer sync started in background"}
    """
    import os
    import logging
    from app import app
    
    logging.info(f"PS365_BASE_URL: {os.getenv('PS365_BASE_URL', 'NOT SET')}")
    logging.info(f"Token present: {bool(os.getenv('PS365_TOKEN'))}")
    
    result = start_customer_sync_background(app)
    
    if result.get("success"):
        return jsonify({
            "success": True,
            "message": "Customer sync started in background",
            "status": result.get("status", {})
        }), 202
    else:
        return jsonify({
            "success": False,
            "error": result.get("error", "Failed to start sync")
        }), 400

@bp_powersoft.route('/customers/upsert', methods=['POST'])
@admin_required
def upsert_customer():
    """
    Upsert a single customer from JSON payload
    
    Request Body:
        JSON object with customer fields (customer_code_365 required)
    
    Returns:
        JSON with success status and customer_code_365
    
    Example:
        POST /api/powersoft/customers/upsert
        Body: {"customer_code_365": "00100010", "first_name": "Alex", "last_name": "Baldwin", ...}
        Response: {"success": true, "customer_code_365": "00100010"}
    """
    try:
        customer_data = request.get_json(force=True) or {}
        result = upsert_single_customer(customer_data)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp_powersoft.route('/customers/<customer_code>', methods=['GET'])
@admin_required
def get_customer(customer_code):
    """
    Get a single customer by customer_code_365
    
    URL Parameters:
        customer_code: The customer_code_365 to look up
    
    Returns:
        JSON with customer data or error
    
    Example:
        GET /api/powersoft/customers/00100010
        Response: {"success": true, "customer": {...}}
    """
    try:
        result = get_customer_by_code(customer_code)
        
        if result is None or not isinstance(result, dict) or not result.get("success"):
            return jsonify(result if result else {"success": False, "error": "Customer not found"}), 404
        
        return jsonify(result), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp_powersoft.route('/sync/invoices', methods=['POST'])
@admin_required
def sync_invoices():
    """
    Sync invoices from PS365 API - runs in background to prevent timeouts
    
    Query Parameters:
        invoice_no: Optional specific invoice number to sync (e.g., ?invoice_no=IN10052209)
        date: Optional date to import all invoices from (e.g., ?date=2025-12-28)
        background: Set to 'false' to run synchronously (default: true)
    
    Returns:
        JSON with sync status (background mode) or results (sync mode)
    
    Examples:
        POST /api/powersoft/sync/invoices?date=2025-12-28
        Starts background sync, returns immediately
        
        POST /api/powersoft/sync/invoices?date=2025-12-28&background=false
        Runs synchronously (may timeout for large imports)
    """
    import logging
    
    try:
        invoice_no = request.args.get('invoice_no')
        import_date = request.args.get('date')
        background = request.args.get('background', 'true').lower() != 'false'
        
        inv_no_str: str = str(invoice_no) if invoice_no is not None else ""
        date_str: str = str(import_date) if import_date is not None else ""
        
        logging.info(f"Starting invoice sync. Invoice: {inv_no_str or 'N/A'}, Date: {date_str or 'N/A'}, Background: {background}")
        
        if background:
            result = start_invoice_sync_background(
                current_app._get_current_object(),
                invoice_no=inv_no_str or None,
                import_date=date_str or None
            )
            return jsonify(result), 200 if result.get("success") else 409
        else:
            result = sync_invoices_from_ps365(invoice_no_365=inv_no_str, import_date=date_str)
            if not result.get("success"):
                return jsonify(result), 500
            return jsonify(result), 200
            
    except Exception as e:
        logging.error(f"Invoice sync endpoint error: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp_powersoft.route('/sync/invoices/status', methods=['GET'])
@admin_required
def sync_invoices_status():
    """
    Get current status of invoice sync operation
    
    Returns:
        JSON with sync status (running, progress, result, error)
    """
    status = get_sync_status("invoices")
    return jsonify({
        "success": True,
        "status": status
    }), 200

@bp_powersoft.route('/sync/customers/status', methods=['GET'])
@admin_required
def sync_customers_status():
    """
    Get current status of customer sync operation
    """
    status = get_sync_status("customers")
    return jsonify({
        "success": True,
        "status": status
    }), 200
