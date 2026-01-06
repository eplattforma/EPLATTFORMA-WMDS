"""
Flask blueprint for customer receipts via Powersoft365 API
"""
import os
import json
from datetime import datetime
from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from functools import wraps
from app import db
from models import ReceiptSequence, ReceiptLog, PSCustomer, Invoice
from sqlalchemy import text
import requests

bp = Blueprint("receipts", __name__)

# Config
POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")
LOCAL_TZ = os.getenv("LOCAL_TZ", "Asia/Nicosia")

def driver_required(f):
    """Decorator to ensure only drivers can access receipt routes"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['driver', 'admin', 'warehouse_manager']:
            flash('Access denied. Only drivers can issue receipts.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def next_reference_number():
    """
    Generates the next reference number, starting at R1000001.
    Uses SELECT ... FOR UPDATE with WHERE clause to ensure uniqueness under concurrency.
    Thread-safe and atomic.
    """
    # Try to lock the single sequence row (id=1) - creates it if doesn't exist
    seq = db.session.execute(text("SELECT id, last_number FROM receipt_sequence WHERE id=1 FOR UPDATE")).first()
    
    if not seq:
        # No row exists - insert it atomically
        # Use INSERT ON CONFLICT to handle race conditions
        db.session.execute(text("""
            INSERT INTO receipt_sequence (id, last_number) 
            VALUES (1, 1000000) 
            ON CONFLICT (id) DO NOTHING
        """))
        db.session.flush()
        # Now lock and get the row
        seq = db.session.execute(text("SELECT id, last_number FROM receipt_sequence WHERE id=1 FOR UPDATE")).first()
    
    nxt = seq.last_number + 1
    db.session.execute(text("UPDATE receipt_sequence SET last_number=:n, updated_at=NOW() WHERE id=1"), {"n": nxt})
    return f"R{nxt:07d}"  # R1000001 formatting with 7 digits after R

def local_and_utc_now():
    """Get current datetime in local timezone and UTC"""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(LOCAL_TZ)
        now_local = datetime.now(tz)
    except Exception:
        # Fallback if zoneinfo not available
        from datetime import timezone
        now_local = datetime.now(timezone.utc)
    
    now_utc = datetime.utcnow()
    return now_local.date().isoformat(), now_utc.strftime("%Y-%m-%d %H:%M:%S")

def create_receipt_core(customer_code: str, amount_val: float, comments: str, 
                        agent_code: str = "2", user_code: str = "", 
                        invoice_no: str = None, driver_username: str = None, route_stop_id: int = None):
    """
    Core receipt creation logic used by both API and form routes
    """
    receipt_date_local, receipt_date_utc0 = local_and_utc_now()
    
    try:
        # Check if receipt already exists for this route stop
        if route_stop_id:
            existing_receipt = ReceiptLog.query.filter_by(route_stop_id=route_stop_id).first()
            if existing_receipt:
                raise Exception(f"Receipt already exists for this customer. Reference: {existing_receipt.reference_number}")
        
        # Generate reference number
        reference_number = next_reference_number()
        
        # Build receipt description: [first 7 chars of customer name]/[invoice numbers] truncated to 30 total
        customer = PSCustomer.query.get(customer_code)
        customer_prefix = ""
        if customer and customer.company_name:
            customer_prefix = customer.company_name[:7].upper()
        
        # Build full description then truncate to 30 characters
        if invoice_no:
            receipt_description = f"{customer_prefix}/{invoice_no}"[:30]
        else:
            receipt_description = (customer_prefix or "Receipt")[:30]
        
        # Get payment type code from driver's user profile
        payment_type_code = "DRVR1"  # Default fallback
        if driver_username:
            from models import User
            driver = User.query.get(driver_username)
            if driver and driver.payment_type_code_365:
                payment_type_code = driver.payment_type_code_365
        
        # Build request for Powersoft365
        req_obj = {
            "api_credentials": {"token": POWERSOFT_TOKEN},
            "customer_receipt": {
                "customer_code_365": customer_code,
                "receipt_date_local": receipt_date_local,
                "receipt_date_utc0": receipt_date_utc0,
                "reference_number": reference_number,
                "receipt_description": receipt_description,
                "amount": float(amount_val),
                "agent_code_365": agent_code,
                "payment_type_code_365": payment_type_code,
                "cheque_number": "",
                "cheque_date": "",
                "comments": comments or "",
                "user_code": user_code or ""
            }
        }

        # Call external API if credentials are configured
        response_id = None
        ps_json = {}
        ok = True
        status_code = 200
        
        if POWERSOFT_BASE and POWERSOFT_TOKEN:
            url = f"{POWERSOFT_BASE.rstrip('/')}/customer_receipt"
            ps_resp = requests.post(url, json=req_obj, timeout=20)
            status_code = ps_resp.status_code
            try:
                ps_json = ps_resp.json()
            except Exception:
                ps_json = {"raw": ps_resp.text}

            # Extract response_id and check actual API response code
            api_response = (ps_json or {}).get("api_response", {})
            response_code = api_response.get("response_code", "")
            response_id = api_response.get("response_id") or ps_json.get("response_id")
            
            # Powersoft API returns 200 even for errors, so check response_code
            # Response code "1" means success, anything else is an error
            ok = ps_resp.ok and response_code == "1" and response_id
            
            # CRITICAL: If no valid transaction number, fail completely
            if not ok or not response_id:
                error_msg = api_response.get("response_msg", "Unknown error from Powersoft365")
                db.session.rollback()  # Roll back the reference number increment
                raise Exception(f"Powersoft365 receipt creation failed: {error_msg}")
        else:
            # Powersoft not configured - fail completely
            db.session.rollback()
            raise Exception("Powersoft365 API not configured. Receipt creation requires valid API credentials.")

        # Log successful receipt
        log = ReceiptLog(
            reference_number=reference_number,
            customer_code_365=customer_code,
            amount=amount_val,
            comments=comments or "",
            response_id=response_id,
            success=1,
            request_json=json.dumps(req_obj, ensure_ascii=False),
            response_json=json.dumps(ps_json, ensure_ascii=False),
            invoice_no=invoice_no,
            driver_username=driver_username,
            route_stop_id=route_stop_id
        )
        db.session.add(log)
        db.session.commit()

        return ok, reference_number, response_id, status_code, ps_json

    except Exception as e:
        db.session.rollback()
        raise

# API endpoint for mobile apps
@bp.post("/api/receipts")
@login_required
@driver_required
def create_receipt_api():
    """Create customer receipt via API"""
    payload = request.get_json(silent=True) or {}
    customer_code = (payload.get("customer_code_365") or "").strip()
    amount = payload.get("amount")
    comments = payload.get("comments") or ""
    agent_code = (payload.get("agent_code_365") or "2").strip()
    user_code = payload.get("user_code") or current_user.username
    invoice_no = payload.get("invoice_no")

    if not customer_code:
        return jsonify({"error": "customer_code_365 is required"}), 400
    try:
        amount_val = float(amount)
        if amount_val <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"error": "amount must be a positive number"}), 400

    try:
        ok, reference_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code, amount_val, comments, agent_code, user_code, 
            invoice_no, current_user.username
        )

        # If we reach here, receipt was created successfully
        return jsonify({
            "ok": True,
            "reference_number": reference_number,
            "transaction_code": response_id,
            "powersoft_response": ps_json
        }), 201

    except Exception as e:
        # Receipt creation failed
        return jsonify({"error": "receipt_creation_failed", "detail": str(e)}), 400

# Form routes for drivers

@bp.get("/receipts/new/stop/<int:route_stop_id>")
@login_required
@driver_required
def new_receipt_form_for_stop(route_stop_id):
    """Show receipt form for all invoices at a stop/customer"""
    from models import RouteStop, RouteStopInvoice
    
    # Get the stop
    stop = RouteStop.query.get_or_404(route_stop_id)
    
    # Get all invoices for this stop
    invoices = db.session.query(Invoice).join(
        RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
    ).filter(
        RouteStopInvoice.route_stop_id == route_stop_id
    ).all()
    
    if not invoices:
        flash("No invoices found for this stop", "warning")
        return redirect(url_for("delivery_dashboard.dashboard"))
    
    # Get customer from first invoice
    first_invoice = invoices[0]
    customer = PSCustomer.query.filter_by(company_name=first_invoice.customer_name).first()
    customer_code = customer.customer_code_365 if customer else None
    
    # Combine all invoice numbers
    invoice_numbers = ", ".join([inv.invoice_no for inv in invoices])
    
    # Calculate total amount (Invoice doesn't have total_value, so we calculate from items)
    total_amount = 0
    for inv in invoices:
        for item in inv.items:
            # This is a placeholder - driver can edit the amount on the form
            pass
    
    return render_template("receipt_form.html", 
                         invoice_no=invoice_numbers,
                         customer_code=customer_code,
                         customer=customer,
                         amount=total_amount,
                         is_stop_receipt=True,
                         stop_name=stop.stop_name or stop.customer_code,
                         route_stop_id=route_stop_id)

@bp.get("/receipts/new")
@login_required
@driver_required
def new_receipt_form():
    """Show receipt form for drivers"""
    # Get invoice_no and customer_code from query params if provided
    invoice_no = request.args.get("invoice_no")
    customer_code = request.args.get("customer_code")
    
    customer = None
    if customer_code:
        customer = PSCustomer.query.get(customer_code)
    elif invoice_no:
        invoice = Invoice.query.get(invoice_no)
        if invoice and invoice.customer_name:
            customer = PSCustomer.query.filter_by(company_name=invoice.customer_name).first()
            if customer:
                customer_code = customer.customer_code_365
    
    return render_template("receipt_form.html", 
                         invoice_no=invoice_no,
                         customer_code=customer_code,
                         customer=customer)

@bp.post("/receipts/submit")
@login_required
@driver_required
def submit_receipt_form():
    """Handle receipt form submission"""
    customer_code = (request.form.get("customer_code_365") or "").strip()
    amount_raw = request.form.get("amount") or ""
    comments = request.form.get("comments") or ""
    invoice_no = request.form.get("invoice_no") or None
    route_stop_id_raw = request.form.get("route_stop_id") or None
    
    route_stop_id = None
    if route_stop_id_raw:
        try:
            route_stop_id = int(route_stop_id_raw)
        except Exception:
            pass

    if not customer_code:
        flash("Customer code is required", "danger")
        return redirect(url_for("receipts.new_receipt_form"))

    try:
        amount_val = float(amount_raw)
        if amount_val <= 0:
            raise ValueError()
    except Exception:
        flash("Amount must be a positive number", "danger")
        return redirect(url_for("receipts.new_receipt_form", 
                               customer_code=customer_code, 
                               invoice_no=invoice_no))

    try:
        ok, reference_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code, amount_val, comments, "2", current_user.username, 
            invoice_no, current_user.username, route_stop_id
        )

        # If we reach here, receipt was created successfully
        flash(f"Receipt {reference_number} created successfully! Transaction: {response_id}", "success")
        
        # If receipt was created from a route stop, redirect back to the route
        if route_stop_id:
            from models import RouteStop
            stop = RouteStop.query.get(route_stop_id)
            if stop:
                return redirect(url_for("routes.detail", shipment_id=stop.shipment_id))
        
        return redirect(url_for("receipts.receipt_success", reference_number=reference_number))

    except Exception as e:
        # Receipt creation failed - show error and return to form
        flash(f"Receipt creation failed: {str(e)}", "danger")
        return redirect(url_for("receipts.new_receipt_form", 
                               customer_code=customer_code, 
                               invoice_no=invoice_no))

@bp.get("/receipts/<reference_number>")
@login_required
@driver_required
def receipt_success(reference_number):
    """Show receipt details after successful creation"""
    receipt = ReceiptLog.query.filter_by(reference_number=reference_number).first_or_404()
    return render_template("receipt_success.html", receipt=receipt)

@bp.post("/cod_receipts/<int:cod_receipt_id>/send")
@login_required
def send_cod_receipt(cod_receipt_id):
    """Send COD receipt to PS365 API and store reference number"""
    from models import CODReceipt, RouteStop
    
    try:
        # Get the COD receipt
        cod_receipt = CODReceipt.query.get_or_404(cod_receipt_id)
        
        # Check if already sent
        if cod_receipt.ps365_receipt_id:
            return jsonify({
                'error': 'Receipt already sent to PS365',
                'reference': cod_receipt.ps365_receipt_id
            }), 400
        
        # Get stop to get customer code
        stop = RouteStop.query.get(cod_receipt.route_stop_id)
        if not stop:
            return jsonify({'error': 'Stop not found'}), 404
        
        # Get PS365 customer code from PSCustomer table or use route stop customer code
        customer_code = stop.customer_code
        if customer_code:
            # Try to get PS365 code from PSCustomer table
            ps_customer = PSCustomer.query.filter_by(customer_code_365=customer_code).first()
            if ps_customer:
                customer_code = ps_customer.customer_code_365
        
        if not customer_code:
            return jsonify({'error': 'Customer code not found'}), 400
        
        # Build invoice numbers string
        invoice_nos = ", ".join(cod_receipt.invoice_nos) if isinstance(cod_receipt.invoice_nos, list) else str(cod_receipt.invoice_nos)
        
        # Build comments from customer code, payment method and notes
        comments = f"Cust: {customer_code} | {cod_receipt.payment_method.upper()} payment"
        if cod_receipt.note:
            comments += f" | {cod_receipt.note}"
        
        # Create receipt via PS365
        ok, reference_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code=customer_code,
            amount_val=float(cod_receipt.received_amount),
            comments=comments,
            agent_code="2",
            user_code=current_user.username,
            invoice_no=invoice_nos,
            driver_username=cod_receipt.driver_username,
            route_stop_id=cod_receipt.route_stop_id
        )
        
        # Update COD receipt with PS365 reference
        cod_receipt.ps365_receipt_id = reference_number
        cod_receipt.ps365_synced_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'reference_number': reference_number,
            'transaction_code': response_id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
