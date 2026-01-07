"""
Background sync module for long-running PS365 operations.
Prevents request timeouts by running syncs in background threads.
"""
import threading
import logging
import time
from datetime import datetime

sync_status = {
    "invoices": {
        "running": False,
        "started_at": None,
        "completed_at": None,
        "progress": "",
        "result": None,
        "error": None
    },
    "customers": {
        "running": False,
        "started_at": None,
        "completed_at": None,
        "progress": "",
        "result": None,
        "error": None
    }
}

_lock = threading.Lock()

def get_sync_status(sync_type="invoices"):
    """Get current status of a sync operation"""
    with _lock:
        status = sync_status.get(sync_type, {}).copy()
        return status

def is_sync_running(sync_type="invoices"):
    """Check if a sync is currently running"""
    with _lock:
        return sync_status.get(sync_type, {}).get("running", False)

def _run_invoice_sync(app, invoice_no, import_date):
    """Background worker for invoice sync"""
    from services_powersoft import sync_invoices_from_ps365
    
    with _lock:
        sync_status["invoices"]["running"] = True
        sync_status["invoices"]["started_at"] = datetime.now().isoformat()
        sync_status["invoices"]["completed_at"] = None
        sync_status["invoices"]["progress"] = "Starting sync..."
        sync_status["invoices"]["result"] = None
        sync_status["invoices"]["error"] = None
    
    try:
        with app.app_context():
            logging.info(f"Background sync started: invoice={invoice_no}, date={import_date}")
            
            with _lock:
                sync_status["invoices"]["progress"] = "Fetching invoices from PS365..."
            
            result = sync_invoices_from_ps365(
                invoice_no_365=invoice_no or "",
                import_date=import_date or ""
            )
            
            with _lock:
                sync_status["invoices"]["result"] = result
                sync_status["invoices"]["progress"] = "Completed"
                sync_status["invoices"]["completed_at"] = datetime.now().isoformat()
                
            logging.info(f"Background sync completed: {result}")
            
    except Exception as e:
        logging.error(f"Background sync error: {str(e)}")
        with _lock:
            sync_status["invoices"]["error"] = str(e)
            sync_status["invoices"]["progress"] = f"Error: {str(e)}"
            sync_status["invoices"]["completed_at"] = datetime.now().isoformat()
    finally:
        with _lock:
            sync_status["invoices"]["running"] = False

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
    
    time.sleep(0.1)
    
    return {
        "success": True,
        "message": "Sync started in background",
        "status": get_sync_status("invoices")
    }

def _run_customer_sync(app):
    """Background worker for customer sync"""
    from services_powersoft import sync_active_customers
    
    with _lock:
        sync_status["customers"]["running"] = True
        sync_status["customers"]["started_at"] = datetime.now().isoformat()
        sync_status["customers"]["completed_at"] = None
        sync_status["customers"]["progress"] = "Starting customer sync..."
        sync_status["customers"]["result"] = None
        sync_status["customers"]["error"] = None
    
    try:
        with app.app_context():
            logging.info("Background customer sync started")
            
            result = sync_active_customers()
            
            with _lock:
                sync_status["customers"]["result"] = result
                sync_status["customers"]["progress"] = "Completed"
                sync_status["customers"]["completed_at"] = datetime.now().isoformat()
                
            logging.info(f"Background customer sync completed: {result}")
            
    except Exception as e:
        logging.error(f"Background customer sync error: {str(e)}")
        with _lock:
            sync_status["customers"]["error"] = str(e)
            sync_status["customers"]["progress"] = f"Error: {str(e)}"
            sync_status["customers"]["completed_at"] = datetime.now().isoformat()
    finally:
        with _lock:
            sync_status["customers"]["running"] = False

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
    
    time.sleep(0.1)
    
    return {
        "success": True,
        "message": "Customer sync started in background",
        "status": get_sync_status("customers")
    }
