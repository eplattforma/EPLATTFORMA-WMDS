"""
Routes for PS365 Customer Sync API
Provides endpoints for bulk sync and single customer operations
"""
from flask import Blueprint, jsonify, request
from flask_login import current_user
from functools import wraps
from services_powersoft import (
    sync_active_customers,
    upsert_single_customer,
    get_customer_by_code,
    sync_invoices_from_ps365
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
    Bulk sync all active customers from PS365
    
    Returns:
        JSON with sync statistics (total, pages, updated count)
    
    Example:
        POST /api/powersoft/sync/customers
        Response: {"success": true, "total_customers": 1500, "total_pages": 8, "updated_count": 1500}
    """
    import os
    import logging
    
    # Log environment info for debugging production issues
    logging.info(f"PS365_BASE_URL: {os.getenv('PS365_BASE_URL', 'NOT SET')}")
    logging.info(f"Token present: {bool(os.getenv('PS365_TOKEN'))}")
    
    try:
        result = sync_active_customers()
        # Return appropriate HTTP status based on success
        if not result.get("success"):
            return jsonify(result), 500
        return jsonify(result), 200
    except Exception as e:
        logging.error(f"Customer sync failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp_powersoft.route('/customers/upsert', methods=['POST'])
@admin_required
def upsert_customer():
    """
    Upsert a single customer from JSON payload
    
    Request Body:
        JSON object with customer_code_365 field (required)
    
    Returns:
        JSON with success status and customer_code_365
    
    Example:
        POST /api/powersoft/customers/upsert
        Body: {"customer_code_365": "00100010"}
        Response: {"success": true, "customer_code_365": "00100010"}
    """
    try:
        payload = request.get_json(silent=True) or {}
        code = (payload.get("customer_code_365") or "").strip()
        
        if not code:
            return jsonify({
                "success": False,
                "error": "customer_code_365 is required"
            }), 400
        
        customer = upsert_single_customer(code)  # Pass string, not dict
        
        if not customer:
            return jsonify({
                "success": False,
                "error": "Customer not found in PS365"
            }), 404
        
        return jsonify({
            "success": True,
            "customer_code_365": customer.customer_code_365
        }), 200
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
        customer = get_customer_by_code(customer_code)
        
        if customer is None:
            return jsonify({"success": False, "error": "Customer not found"}), 404
        
        # Convert model to dict
        customer_data = {
            "customer_code_365": customer.customer_code_365,
            "customer_name": customer.customer_name,
            "customer_email": customer.customer_email,
            "customer_phone": customer.customer_phone,
            "last_synced_at": customer.last_synced_at.isoformat() if customer.last_synced_at else None
        }
        
        return jsonify({"success": True, "customer": customer_data}), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp_powersoft.route('/sync/invoices', methods=['POST'])
@admin_required
def sync_invoices():
    """
    Sync invoices from PS365 API
    
    Query Parameters:
        invoice_no: Optional specific invoice number to sync (e.g., ?invoice_no=IN10052209)
        date: Optional date to import all invoices from (e.g., ?date=2025-12-28)
    
    Returns:
        JSON with sync statistics (total_invoices_imported, total_items_imported, errors)
    
    Examples:
        POST /api/powersoft/sync/invoices?invoice_no=IN10052209
        Syncs only invoice IN10052209
        
        POST /api/powersoft/sync/invoices?date=2025-12-28
        Syncs all invoices from that date
        
        Response: {"success": true, "total_invoices_imported": 5, "total_items_imported": 23, "errors": 0}
    """
    import logging
    
    try:
        # Get optional parameters
        invoice_no = request.args.get('invoice_no')
        import_date = request.args.get('date')
        
        # Ensure parameters are strings for type safety
        inv_no_str: str = str(invoice_no) if invoice_no is not None else ""
        date_str: str = str(import_date) if import_date is not None else ""
        
        logging.info(f"Starting invoice sync. Invoice: {inv_no_str or 'N/A'}, Date: {date_str or 'N/A'}")
        
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


@bp_powersoft.route('/sync/invoices/async', methods=['POST'])
@admin_required
def sync_invoices_async():
    """
    Start an async invoice sync job (recommended for date-based imports).
    Returns immediately with a job ID that can be polled for status.
    
    Query Parameters:
        date: Required date to import all invoices from (e.g., ?date=2025-12-28)
    
    Returns:
        JSON with job_id for polling status
    
    Example:
        POST /api/powersoft/sync/invoices/async?date=2025-12-28
        Response: {"success": true, "job_id": "sync_20250128_143022_abc12345", "message": "..."}
    """
    import logging
    
    try:
        import_date = request.args.get('date')
        
        if not import_date:
            return jsonify({
                "success": False,
                "error": "Date parameter is required for async sync"
            }), 400
        
        from sync_jobs import start_date_sync_async
        
        result = start_date_sync_async(
            import_date=import_date,
            created_by=current_user.username if current_user.is_authenticated else None
        )
        
        return jsonify(result), 202  # 202 Accepted
        
    except Exception as e:
        logging.error(f"Async invoice sync endpoint error: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@bp_powersoft.route('/jobs/<job_id>', methods=['GET'])
@admin_required
def get_job_status(job_id):
    """
    Get the status of a sync job.
    
    URL Parameters:
        job_id: The job ID returned from async sync endpoint
    
    Returns:
        JSON with job status, progress, and results
    
    Example:
        GET /api/powersoft/jobs/sync_20250128_143022_abc12345
        Response: {"id": "...", "status": "running", "progress_current": 5, "progress_total": 20, ...}
    """
    from sync_jobs import get_job_status as get_status
    
    status = get_status(job_id)
    
    if not status:
        return jsonify({
            "success": False,
            "error": "Job not found"
        }), 404
    
    return jsonify(status), 200


@bp_powersoft.route('/jobs', methods=['GET'])
@admin_required
def list_recent_jobs():
    """
    List recent sync jobs for monitoring.
    
    Query Parameters:
        limit: Number of jobs to return (default: 20, max: 100)
    
    Returns:
        JSON array of recent jobs
    
    Example:
        GET /api/powersoft/jobs?limit=10
        Response: [{"id": "...", "status": "completed", ...}, ...]
    """
    from sync_jobs import get_recent_jobs
    
    limit = min(int(request.args.get('limit', 20)), 100)
    jobs = get_recent_jobs(limit=limit)
    
    return jsonify({
        "success": True,
        "jobs": jobs
    }), 200
