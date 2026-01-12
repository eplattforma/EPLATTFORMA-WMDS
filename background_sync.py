"""
Background sync module for long-running PS365 operations.
Prevents request timeouts by running syncs in background threads.
Uses file-based status to work across multiple gunicorn workers.
"""
import threading
import logging
import time
import json
import os
from datetime import datetime

STATUS_FILE = "/tmp/sync_status.json"
_lock = threading.Lock()

def _read_status_file():
    """Read status from file"""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"Error reading status file: {e}")
    return {
        "invoices": {"running": False, "started_at": None, "completed_at": None, "progress": "", "result": None, "error": None},
        "customers": {"running": False, "started_at": None, "completed_at": None, "progress": "", "result": None, "error": None}
    }

def _write_status_file(status):
    """Write status to file"""
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(status, f)
    except Exception as e:
        logging.error(f"Error writing status file: {e}")

def _update_status(sync_type, **kwargs):
    """Update specific fields in sync status"""
    with _lock:
        status = _read_status_file()
        if sync_type not in status:
            status[sync_type] = {}
        status[sync_type].update(kwargs)
        _write_status_file(status)

def get_sync_status(sync_type="invoices"):
    """Get current status of a sync operation"""
    with _lock:
        status = _read_status_file()
        return status.get(sync_type, {}).copy()

def is_sync_running(sync_type="invoices"):
    """Check if a sync is currently running"""
    with _lock:
        status = _read_status_file()
        return status.get(sync_type, {}).get("running", False)

def _run_invoice_sync(app, invoice_no, import_date):
    """Background worker for invoice sync"""
    from services_powersoft import sync_invoices_from_ps365
    
    _update_status("invoices",
        running=True,
        started_at=datetime.now().isoformat(),
        completed_at=None,
        progress="Starting sync...",
        result=None,
        error=None
    )
    
    try:
        with app.app_context():
            logging.info(f"Background sync started: invoice={invoice_no}, date={import_date}")
            
            _update_status("invoices", progress="Fetching invoices from PS365...")
            
            result = sync_invoices_from_ps365(
                invoice_no_365=invoice_no or "",
                import_date=import_date or ""
            )
            
            _update_status("invoices",
                result=result,
                progress="Completed",
                completed_at=datetime.now().isoformat()
            )
                
            logging.info(f"Background sync completed: {result}")
            
    except Exception as e:
        logging.error(f"Background sync error: {str(e)}")
        _update_status("invoices",
            error=str(e),
            progress=f"Error: {str(e)}",
            completed_at=datetime.now().isoformat()
        )
    finally:
        _update_status("invoices", running=False)

def start_invoice_sync_background(app, invoice_no=None, import_date=None):
    """
    Start invoice sync in background thread.
    Returns immediately so the request doesn't timeout.
    """
    if is_sync_running("invoices"):
        return {
            "success": False,
            "error": "A sync is already running. Please wait for it to complete.",
            "status": get_sync_status("invoices")
        }
    
    thread = threading.Thread(
        target=_run_invoice_sync,
        args=(app, invoice_no, import_date),
        daemon=True
    )
    thread.start()
    
    time.sleep(0.2)
    
    return {
        "success": True,
        "message": "Sync started in background",
        "status": get_sync_status("invoices")
    }

def _run_customer_sync(app):
    """Background worker for customer sync with payment terms creation"""
    from services_powersoft import sync_active_customers
    
    _update_status("customers",
        running=True,
        started_at=datetime.now().isoformat(),
        completed_at=None,
        progress="Starting customer sync...",
        result=None,
        error=None
    )
    
    try:
        with app.app_context():
            from app import db
            from sqlalchemy import text
            from main import _default_terms_values_for
            
            logging.info("Background customer sync started")
            
            _update_status("customers", progress="Fetching customers from PS365...")
            result = sync_active_customers()
            
            if not result.get("success", True):
                raise Exception(result.get("error", "Customer sync failed"))
            
            _update_status("customers", progress="Syncing payment customers...")
            
            sync_result = db.session.execute(text("""
                INSERT INTO payment_customers (code, name, "group")
                SELECT 
                    pc.customer_code_365,
                    COALESCE(pc.company_name, 
                        TRIM(CONCAT(COALESCE(pc.contact_first_name, ''), ' ', COALESCE(pc.contact_last_name, ''))),
                        COALESCE(pc.customer_name, 'Unknown')),
                    pc.category_1_name
                FROM ps_customers pc
                WHERE pc.customer_code_365 IS NOT NULL
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    "group" = EXCLUDED."group"
            """))
            synced_count = sync_result.rowcount
            db.session.commit()
            logging.info(f"Synced {synced_count} payment customers")
            result["synced_customers"] = synced_count
            
            _update_status("customers", progress="Creating payment terms for new customers...")
            
            terms_defaults = _default_terms_values_for("dummy")
            terms_result = db.session.execute(text("""
                INSERT INTO credit_terms (
                    customer_code, terms_code, due_days, is_credit,
                    credit_limit, allow_cash, allow_card_pos, allow_bank_transfer, allow_cheque,
                    cheque_days_allowed, notes_for_driver, valid_from, valid_to
                )
                SELECT 
                    pc.code,
                    :terms_code,
                    :due_days,
                    :is_credit,
                    :credit_limit,
                    :allow_cash,
                    :allow_card_pos,
                    :allow_bank_transfer,
                    :allow_cheque,
                    :cheque_days_allowed,
                    :notes_for_driver,
                    CURRENT_DATE,
                    NULL
                FROM payment_customers pc
                WHERE NOT EXISTS (
                    SELECT 1 FROM credit_terms ct 
                    WHERE ct.customer_code = pc.code 
                    AND ct.valid_to IS NULL
                )
            """), terms_defaults)
            created_terms = terms_result.rowcount
            db.session.commit()
            logging.info(f"Created {created_terms} default payment terms")
            
            result["created_defaults"] = created_terms
            result["synced_customers"] = result.get("total_customers", 0)
            
            _update_status("customers",
                result=result,
                progress="Completed",
                completed_at=datetime.now().isoformat()
            )
                
            logging.info(f"Background customer sync completed: {result}")
            
    except Exception as e:
        logging.error(f"Background customer sync error: {str(e)}")
        _update_status("customers",
            error=str(e),
            progress=f"Error: {str(e)}",
            completed_at=datetime.now().isoformat()
        )
    finally:
        _update_status("customers", running=False)

def start_customer_sync_background(app):
    """Start customer sync in background thread."""
    if is_sync_running("customers"):
        return {
            "success": False,
            "error": "A customer sync is already running.",
            "status": get_sync_status("customers")
        }
    
    thread = threading.Thread(
        target=_run_customer_sync,
        args=(app,),
        daemon=True
    )
    thread.start()
    
    time.sleep(0.2)
    
    return {
        "success": True,
        "message": "Customer sync started in background",
        "status": get_sync_status("customers")
    }
