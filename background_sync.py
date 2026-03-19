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
    """Write status to file atomically"""
    try:
        temp_file = STATUS_FILE + ".tmp"
        with open(temp_file, 'w') as f:
            json.dump(status, f)
        os.replace(temp_file, STATUS_FILE)
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

STALE_LOCK_TIMEOUT_SECONDS = 1800

def is_sync_running(sync_type="invoices"):
    """Check if a sync is currently running. Auto-clears stale locks older than 30 minutes."""
    with _lock:
        status = _read_status_file()
        sync_status = status.get(sync_type, {})
        if not sync_status.get("running", False):
            return False
        started_at = sync_status.get("started_at")
        if started_at:
            try:
                started_dt = datetime.fromisoformat(started_at)
                elapsed = (datetime.now() - started_dt).total_seconds()
                if elapsed > STALE_LOCK_TIMEOUT_SECONDS:
                    logging.warning(f"Clearing stale {sync_type} sync lock (started {elapsed:.0f}s ago)")
                    status[sync_type]["running"] = False
                    status[sync_type]["error"] = f"Stale lock cleared after {elapsed:.0f}s"
                    status[sync_type]["completed_at"] = datetime.now().isoformat()
                    _write_status_file(status)
                    return False
            except (ValueError, TypeError):
                pass
        return True

def _resolve_invoice_date_from_ps365(invoice_no: str):
    """Resolve invoice date from PS365 API for a given invoice number."""
    try:
        from ps365_client import call_ps365

        payload = {
            "filter_define": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 10,
                "invoice_type": "all",
                "invoice_number_selection": invoice_no,
                "invoice_customer_code_selection": "",
                "invoice_customer_name_selection": "",
                "invoice_customer_email_selection": "",
                "invoice_customer_phone_selection": "",
                "from_date": "",
                "to_date": "",
            }
        }

        response = call_ps365("list_loyalty_invoices_header", payload)
        invoices = response.get("list_invoices", []) or []
        if not invoices:
            return None

        inv_date = invoices[0].get("invoice_date_utc0")
        if not inv_date:
            return None

        if isinstance(inv_date, str):
            from datetime import datetime as dt_cls
            return dt_cls.fromisoformat(inv_date.replace("Z", "+00:00")).date().isoformat()

        return inv_date.date().isoformat() if hasattr(inv_date, "date") else str(inv_date)
    except Exception as e:
        logging.warning(f"Could not resolve invoice date from PS365 for {invoice_no}: {e}")
        return None


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
                progress="Operational sync completed. Updating data warehouse...",
                completed_at=datetime.now().isoformat()
            )
                
            logging.info(f"Background sync completed: {result}")
            
            dw_catchup_status = None
            if result.get("success", True):
                dw_date = import_date or None
                if not dw_date and invoice_no:
                    logging.info(f"Resolving invoice date from PS365 for invoice {invoice_no}...")
                    dw_date = _resolve_invoice_date_from_ps365(invoice_no)
                    logging.info(f"Resolved DW catch-up date: {dw_date}")
                
                if dw_date:
                    try:
                        from app import db
                        from datawarehouse_sync import sync_invoices_from_date
                        logging.info(f"Starting DW catch-up for date {dw_date}...")
                        _update_status("invoices", progress="Updating data warehouse...")
                        sync_invoices_from_date(db.session, dw_date, dw_date, sync_trigger='operator', refresh_mv=False)
                        logging.info(f"DW catch-up completed for date {dw_date}")
                    except Exception as dw_err:
                        logging.error(f"DW catch-up failed (operational sync still OK): {dw_err}")
                        dw_catchup_status = f"Completed (DW catch-up failed: {str(dw_err)[:100]})"
            else:
                logging.info("Operational sync was not successful, skipping DW catch-up")
            
            _update_status("invoices", progress=dw_catchup_status or "Completed")
            
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
                    COALESCE(
                        NULLIF(pc.company_name, ''), 
                        NULLIF(TRIM(CONCAT(COALESCE(pc.contact_first_name, ''), ' ', COALESCE(pc.contact_last_name, ''))), ''),
                        'Unknown'
                    ),
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
