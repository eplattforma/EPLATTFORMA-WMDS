"""
Powersoft365 API Integration Service
Fetches invoice data from PS365 API and syncs with local database
"""
import os
import requests
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from app import db
from models import Invoice, utc_now

logger = logging.getLogger(__name__)

API_BASE = os.getenv("POWERSOFT_BASE", "http://api.powersoft365.com")
API_TOKEN = os.getenv("POWERSOFT_TOKEN")


def fetch_invoice_from_ps365(invoice_no, customer_code):
    """
    Fetch invoice details from Powersoft365 API
    
    Args:
        invoice_no: Invoice number (e.g., 'IN10051409')
        customer_code: Customer code (e.g., '77700188')
    
    Returns:
        dict: Invoice data from API or None if not found/error
    """
    if not API_TOKEN:
        logger.error("POWERSOFT_TOKEN environment variable not set")
        return None
    
    url = f"{API_BASE}/list_loyalty_invoices_header"
    
    # Use broad date range to ensure we find the invoice
    from_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")  # 2 years back
    to_date = datetime.now().strftime("%Y-%m-%d")
    
    payload = {
        "api_credentials": {
            "token": API_TOKEN
        },
        "filter_define": {
            "only_counted": "N",
            "page_number": 1,
            "page_size": 1,
            "invoice_type": "all",
            "invoice_number_selection": invoice_no,
            "invoice_customer_code_selection": customer_code,
            "invoice_customer_phone_selection": "",
            "invoice_customer_email_selection": "",
            "invoice_customer_name_selection": "",
            "from_date": from_date,
            "to_date": to_date
        }
    }
    
    try:
        response = requests.post(url, json=payload, timeout=30)
        
        if response.status_code != 200:
            logger.error(f"PS365 API returned status {response.status_code} for invoice {invoice_no}")
            return None
        
        data = response.json()
        
        # Check for API error (response_code '1' means success)
        api_response = data.get("api_response", {})
        if api_response.get("response_code") not in ["1", "200"]:
            logger.error(f"PS365 API error for invoice {invoice_no}: {api_response.get('response_msg')}")
            return None
        
        invoices = data.get("list_invoices") or []
        
        if not invoices:
            logger.warning(f"Invoice {invoice_no} not found in PS365 API")
            return None
        
        return invoices[0]
    
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error fetching invoice {invoice_no} from PS365: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error fetching invoice {invoice_no} from PS365: {str(e)}")
        return None


def sync_invoice_totals(invoice_no, customer_code):
    """
    Fetch invoice data from PS365 and update local database
    
    Args:
        invoice_no: Invoice number
        customer_code: Customer code
    
    Returns:
        dict: Updated invoice data or error info
    """
    # Fetch from API
    ps365_data = fetch_invoice_from_ps365(invoice_no, customer_code)
    
    if not ps365_data:
        return {
            "success": False,
            "error": "Invoice not found in PS365 or API error"
        }
    
    # Get local invoice
    invoice = Invoice.query.get(invoice_no)
    if not invoice:
        return {
            "success": False,
            "error": f"Invoice {invoice_no} not found in local database"
        }
    
    # Update invoice with PS365 data
    invoice.total_grand = Decimal(str(ps365_data.get("total_grand", 0)))
    invoice.total_sub = Decimal(str(ps365_data.get("total_sub", 0)))
    invoice.total_vat = Decimal(str(ps365_data.get("total_vat", 0)))
    invoice.ps365_synced_at = utc_now()
    
    try:
        db.session.commit()
        logger.info(f"Synced invoice {invoice_no} totals from PS365: €{invoice.total_grand}")
        
        return {
            "success": True,
            "invoice_no": invoice_no,
            "total_grand": float(invoice.total_grand),
            "total_sub": float(invoice.total_sub),
            "total_vat": float(invoice.total_vat),
            "synced_at": invoice.ps365_synced_at.isoformat()
        }
    except Exception as e:
        db.session.rollback()
        logger.error(f"Database error updating invoice {invoice_no}: {str(e)}")
        return {
            "success": False,
            "error": f"Database error: {str(e)}"
        }


def sync_route_invoices(route_id):
    """
    Sync all invoices on a route with PS365 API
    
    Args:
        route_id: Shipment/route ID
    
    Returns:
        dict: Summary of sync results
    """
    from models import RouteStopInvoice, RouteStop
    
    # Get all stops on this route
    stops = RouteStop.query.filter_by(shipment_id=route_id).all()
    
    results = {
        "success": True,
        "route_id": route_id,
        "total_invoices": 0,
        "synced": 0,
        "failed": 0,
        "errors": []
    }
    
    for stop in stops:
        # Get all invoices at this stop
        stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop.route_stop_id).all()
        
        for rsi in stop_invoices:
            invoice = Invoice.query.get(rsi.invoice_no)
            if not invoice:
                continue
            
            results["total_invoices"] += 1
            
            # Need customer code to fetch from API
            customer_code = invoice.customer_code or stop.customer_code
            
            if not customer_code:
                results["failed"] += 1
                results["errors"].append({
                    "invoice_no": invoice.invoice_no,
                    "error": "No customer code available"
                })
                continue
            
            # Sync this invoice
            sync_result = sync_invoice_totals(invoice.invoice_no, customer_code)
            
            if sync_result["success"]:
                results["synced"] += 1
            else:
                results["failed"] += 1
                results["errors"].append({
                    "invoice_no": invoice.invoice_no,
                    "error": sync_result.get("error", "Unknown error")
                })
    
    if results["failed"] > 0:
        results["success"] = False
    
    logger.info(f"Route {route_id} sync complete: {results['synced']}/{results['total_invoices']} invoices synced")
    
    return results
