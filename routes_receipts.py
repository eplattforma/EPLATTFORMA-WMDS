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
PS365_RECEIPT_DESC_MAX = int(os.getenv("PS365_RECEIPT_DESC_MAX", "20"))
PS365_RECEIPT_COMMENTS_MAX = int(os.getenv("PS365_RECEIPT_COMMENTS_MAX", "255"))
PS365_CHEQUE_PAYMENT_TYPE_CODE = os.getenv("PS365_CHEQUE_PAYMENT_TYPE_CODE", "CHEQ")

def redact_ps365_request(req: dict) -> dict:
    safe = json.loads(json.dumps(req))  # deep copy
    if isinstance(safe.get("api_credentials"), dict):
        safe["api_credentials"]["token"] = "***REDACTED***"
    return safe

def normalize_yyyy_mm_dd(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # yyyy-mm-dd or yyyy-mm-ddTHH:MM...
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    # dd/mm/yyyy
    if len(s) >= 10 and s[2] == "/" and s[5] == "/":
        dd, mm, yyyy = s[:2], s[3:5], s[6:10]
        return f"{yyyy}-{mm}-{dd}"
    return s

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
                        invoice_no: str = None, driver_username: str = None, route_stop_id: int = None,
                        cheque_number: str = "", cheque_date: str = "",
                        allow_duplicate_stop: bool = False,
                        payment_type_code_override: str = "",
                        receipt_date_override: str = "",
                        bank_reference: str = ""):
    """
    Core receipt creation logic used by both API and form routes
    """
    if receipt_date_override:
        receipt_date_local = receipt_date_override
        receipt_date_utc0 = f"{receipt_date_override} 00:00:00"
    else:
        receipt_date_local, receipt_date_utc0 = local_and_utc_now()
    
    try:
        # Check if receipt already exists for this route stop
        if route_stop_id and not allow_duplicate_stop:
            existing_receipt = ReceiptLog.query.filter_by(route_stop_id=route_stop_id).first()
            if existing_receipt:
                raise Exception(f"Receipt already exists for this customer. Reference: {existing_receipt.reference_number}")
        
        # Generate reference number
        reference_number = next_reference_number()
        
        # Build receipt description: [cheque_number] [invoices] [name]
        customer = PSCustomer.query.filter_by(customer_code_365=customer_code).first()
        customer_name = ""
        if customer and customer.company_name:
            customer_name = customer.company_name.upper()
        
        desc_parts = []
        if bank_reference:
            desc_parts.append(bank_reference)
        elif cheque_number:
            desc_parts.append(cheque_number)
        
        if invoice_no:
            desc_parts.append(invoice_no)
        
        if customer_name:
            desc_parts.append(customer_name)
            
        receipt_description = " ".join(desc_parts).strip()
        receipt_description = receipt_description[:PS365_RECEIPT_DESC_MAX].strip()
        if not receipt_description:
            receipt_description = "RECEIPT"
        
        payment_type_code = "DRVR1"
        driver_obj = None
        if driver_username:
            from models import User
            driver_obj = User.query.filter_by(username=driver_username).first()

        if payment_type_code_override:
            payment_type_code = payment_type_code_override
        elif driver_obj and driver_obj.payment_type_code_365:
            payment_type_code = driver_obj.payment_type_code_365
        
        cheque_number = (cheque_number or "").strip()
        cheque_date = normalize_yyyy_mm_dd(cheque_date)

        comments = (comments or "").strip()
        if PS365_RECEIPT_COMMENTS_MAX and len(comments) > PS365_RECEIPT_COMMENTS_MAX:
            comments = comments[:PS365_RECEIPT_COMMENTS_MAX]

        if cheque_number or cheque_date:
            cheque_code = PS365_CHEQUE_PAYMENT_TYPE_CODE
            if driver_obj and driver_obj.cheque_payment_type_code_365:
                cheque_code = driver_obj.cheque_payment_type_code_365
            payment_type_code = cheque_code
            
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
                "cheque_number": cheque_number,
                "cheque_date": cheque_date,
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
                raise Exception(f"Powersoft365 receipt creation failed: {error_msg}")
        else:
            raise Exception("Powersoft365 API not configured. Receipt creation requires valid API credentials.")

        # Redact token before storing ReceiptLog.request_json
        safe_req_obj = redact_ps365_request(req_obj)

        # Log successful receipt
        log = ReceiptLog(
            reference_number=reference_number,
            customer_code_365=customer_code,
            amount=amount_val,
            comments=comments or "",
            response_id=response_id,
            success=1,
            request_json=json.dumps(safe_req_obj, ensure_ascii=False),
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
        allow_duplicate_stop = (current_user.role == "admin") and (request.args.get("force_test") == "1")
        ok, reference_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code, amount_val, comments, agent_code, user_code, 
            invoice_no, current_user.username,
            cheque_number=payload.get("cheque_number") or payload.get("cheque_no") or "",
            cheque_date=payload.get("cheque_date") or payload.get("post_date") or "",
            allow_duplicate_stop=allow_duplicate_stop
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
    
    # Get customer from first invoice (filter for active customers only)
    first_invoice = invoices[0]
    customer = PSCustomer.query.filter_by(company_name=first_invoice.customer_name, active=True).first()
    customer_code = customer.customer_code_365 if customer else None
    
    # Combine all invoice numbers
    invoice_numbers = ", ".join([inv.invoice_no for inv in invoices])
    
    # Calculate total amount from synced grand totals
    total_amount = sum([float(inv.total_grand or 0) for inv in invoices])
    
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
        customer = PSCustomer.query.filter_by(customer_code_365=customer_code).first()
    elif invoice_no:
        invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
        if invoice and invoice.customer_name:
            customer = PSCustomer.query.filter_by(company_name=invoice.customer_name, active=True).first()
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
        allow_duplicate_stop = (current_user.role == "admin") and (request.args.get("force_test") == "1")
        ok, reference_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code, amount_val, comments, "2", current_user.username, 
            invoice_no, current_user.username, route_stop_id,
            cheque_number=request.form.get("cheque_number", ""),
            cheque_date=request.form.get("cheque_date", ""),
            allow_duplicate_stop=allow_duplicate_stop
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
        
        if cod_receipt.route and cod_receipt.route.reconciliation_status == 'RECONCILED':
            return jsonify({
                'error': 'Cannot send: Route is already reconciled'
            }), 400

        if cod_receipt.ps365_receipt_id or cod_receipt.ps365_reference_number:
            return jsonify({
                'error': 'Receipt already sent to PS365',
                'reference': cod_receipt.ps365_reference_number or cod_receipt.ps365_receipt_id
            }), 400
        
        from datetime import date as date_type
        if (cod_receipt.payment_method and cod_receipt.payment_method.lower() == 'cheque'
                and cod_receipt.cheque_date and cod_receipt.cheque_date > date_type.today()):
            return jsonify({
                'error': f'Post-dated cheque cannot be sent to PS365 until {cod_receipt.cheque_date.strftime("%d/%m/%Y")}'
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
        allow_duplicate_stop = (current_user.role == "admin") and (request.args.get("force_test") == "1")
        ok, reference_number, response_id, status_code, ps_json = create_receipt_core(
            customer_code=customer_code,
            amount_val=float(cod_receipt.received_amount),
            comments=comments,
            agent_code="2",
            user_code=current_user.username,
            invoice_no=invoice_nos,
            driver_username=cod_receipt.driver_username,
            route_stop_id=cod_receipt.route_stop_id,
            cheque_number=cod_receipt.cheque_number or "",
            cheque_date=cod_receipt.cheque_date.strftime('%Y-%m-%d') if cod_receipt.cheque_date else "",
            allow_duplicate_stop=allow_duplicate_stop
        )
        
        cod_receipt.ps365_reference_number = reference_number
        cod_receipt.ps365_receipt_id = str(response_id) if response_id else reference_number
        cod_receipt.ps365_synced_at = datetime.utcnow()
        db.session.commit()
        
        # If called from a form (not AJAX), redirect back to reconciliation page
        if not request.is_json and request.referrer:
            flash(f'Receipt sent to PS365: {reference_number}', 'success')
            return redirect(request.referrer)
        
        return jsonify({
            'success': True,
            'reference_number': reference_number,
            'transaction_code': response_id
        }), 200
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
