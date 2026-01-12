"""
Flask routes for Customer Payment Terms Management
Handles credit terms, payment methods, and import/export functionality
"""
import io
import datetime as dt
import logging
import traceback
from decimal import Decimal, InvalidOperation
from flask import Blueprint, request, jsonify, render_template, send_file, flash, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import or_
import pandas as pd
from app import app, db
from models import PaymentCustomer, CreditTerms, PSCustomer
from background_sync import start_customer_sync_background, get_sync_status, is_sync_running

bp = Blueprint('payment_terms', __name__, url_prefix='/admin/payment-terms')

# Helper functions
def truthy(v):
    """Convert various boolean representations to Python bool"""
    if pd.isna(v):
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "y")

def safe_int(value):
    """Safely convert value to integer, returning None for empty/invalid values"""
    if value in (None, "", " "):
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        return int(float(value))  # Handle decimals like "30.0"
    except (ValueError, TypeError):
        return None

def safe_decimal(value):
    """Safely convert value to Decimal, returning None for empty/invalid/NaN values"""
    if value in (None, "", " "):
        return None
    try:
        # Check for NaN/Inf using pandas if available
        if pd.isna(value):
            return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        result = Decimal(str(value))
        # Check if result is NaN or Inf
        if not result.is_finite():
            return None
        return result
    except (InvalidOperation, ValueError, TypeError):
        return None

REQUIRED_COLS = [
    "customer_code", "customer_name", "group", "terms_code", "due_days", "is_credit", "credit_limit",
    "allow_cash", "allow_card_pos", "allow_bank_transfer", "allow_cheque", "cheque_days_allowed", "notes"
]

@bp.route('/')
@login_required
def index():
    """Main payment terms management page"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('payment_terms.html')

@bp.route('/sync-customers')
@login_required
def sync_customers_page():
    """Customer synchronization page"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('sync_customers.html')

@bp.route('/list')
@login_required
def list_terms():
    """API endpoint to list active payment terms with optional search"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"error": "Access denied"}), 403
    
    q = (request.args.get("query") or "").strip()
    
    try:
        query = (db.session.query(
            PaymentCustomer.code.label("customer_code"),
            PaymentCustomer.name.label("customer_name"),
            PaymentCustomer.group.label("group"),
            CreditTerms.terms_code,
            CreditTerms.due_days,
            CreditTerms.is_credit,
            CreditTerms.credit_limit,
            CreditTerms.allow_cash,
            CreditTerms.allow_card_pos,
            CreditTerms.allow_bank_transfer,
            CreditTerms.allow_cheque,
            CreditTerms.cheque_days_allowed,
            CreditTerms.notes_for_driver.label("notes"),
            PSCustomer.latitude,
            PSCustomer.longitude,
        )
        .join(CreditTerms, CreditTerms.customer_code == PaymentCustomer.code)
        .outerjoin(PSCustomer, PSCustomer.customer_code_365 == PaymentCustomer.code)
        .filter(CreditTerms.valid_to.is_(None)))
        
        if q:
            search_term = f"%{q}%"
            query = query.filter(or_(
                PaymentCustomer.code.ilike(search_term),
                PaymentCustomer.name.ilike(search_term),
                PaymentCustomer.group.ilike(search_term)
            ))
        
        rows = []
        for r in query.order_by(PaymentCustomer.code.asc()).all():
            row_dict = dict(r._asdict())
            # Convert Decimal to float for JSON serialization
            if row_dict.get('credit_limit') is not None:
                row_dict['credit_limit'] = float(row_dict['credit_limit'])
            rows.append(row_dict)
        return jsonify({"items": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/save', methods=['POST'])
@login_required
def save_terms():
    """Save or update payment terms (creates new version if changed)"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"error": "Access denied"}), 403
    
    data = request.get_json(force=True)
    
    try:
        # Upsert customer
        customer = PaymentCustomer.query.filter_by(code=data["customer_code"]).first()
        if not customer:
            customer = PaymentCustomer(
                code=data["customer_code"],
                name=data["customer_name"],
                group=data.get("group")
            )
            db.session.add(customer)
        else:
            customer.name = data["customer_name"]
            customer.group = data.get("group")
        
        # Get active terms
        active = (CreditTerms.query
                  .filter(CreditTerms.customer_code == data["customer_code"], CreditTerms.valid_to.is_(None))
                  .order_by(CreditTerms.valid_from.desc())
                  .first())
        
        # Create new terms object with safe numeric parsing
        due_days = safe_int(data.get("due_days")) or 0
        new_terms = CreditTerms(
            customer_code=data["customer_code"],
            terms_code=data["terms_code"].strip(),
            due_days=due_days,
            is_credit=bool(data.get("is_credit")) if data.get("is_credit") is not None else due_days > 0,
            credit_limit=safe_decimal(data.get("credit_limit")),
            allow_cash=bool(data.get("allow_cash")),
            allow_card_pos=bool(data.get("allow_card_pos")),
            allow_bank_transfer=bool(data.get("allow_bank_transfer")),
            allow_cheque=bool(data.get("allow_cheque")),
            cheque_days_allowed=safe_int(data.get("cheque_days_allowed")),
            notes_for_driver=(data.get("notes") or None),
            valid_from=dt.date.today(),
        )
        
        # Compare terms to check if changed
        def terms_key(t):
            return (t.terms_code, t.due_days, t.is_credit, t.credit_limit,
                    t.allow_cash, t.allow_card_pos, t.allow_bank_transfer, t.allow_cheque,
                    t.cheque_days_allowed, t.notes_for_driver or "")
        
        if active and terms_key(active) == terms_key(new_terms):
            return jsonify({"status": "no_change"})
        
        # Handle same-day updates: delete old terms if created today, else close them
        if active and active.valid_to is None:
            if active.valid_from == dt.date.today():
                # Same-day update: delete old terms to avoid constraint violation
                db.session.delete(active)
                db.session.flush()  # Flush delete before adding new
            else:
                # Close previous version with yesterday's date
                active.valid_to = dt.date.today() - dt.timedelta(days=1)
        
        db.session.add(new_terms)
        db.session.commit()
        
        return jsonify({"status": "ok"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@bp.route('/export.xlsx')
@login_required
def export_terms():
    """Export active payment terms to Excel"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('admin_dashboard'))
    
    try:
        rows = (
            db.session.query(
                PaymentCustomer.code.label("customer_code"),
                PaymentCustomer.name.label("customer_name"),
                PaymentCustomer.group.label("group"),
                CreditTerms.terms_code,
                CreditTerms.due_days,
                CreditTerms.is_credit,
                CreditTerms.credit_limit,
                CreditTerms.allow_cash,
                CreditTerms.allow_card_pos,
                CreditTerms.allow_bank_transfer,
                CreditTerms.allow_cheque,
                CreditTerms.cheque_days_allowed,
                CreditTerms.notes_for_driver.label("notes"),
            )
            .join(CreditTerms, CreditTerms.customer_code == PaymentCustomer.code)
            .filter(CreditTerms.valid_to.is_(None))
            .order_by(PaymentCustomer.code.asc())
            .all()
        )
        
        df = pd.DataFrame(rows, columns=REQUIRED_COLS)
        
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="CreditTerms")
        buf.seek(0)
        
        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="credit_terms_export.xlsx",
        )
    except Exception as e:
        flash(f'Export failed: {str(e)}', 'danger')
        return redirect(url_for('payment_terms.index'))

@bp.route('/import', methods=['POST'])
@login_required
def import_terms():
    """Import payment terms from Excel/CSV with optional dry run"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"error": "Access denied"}), 403
    
    dry_run = request.args.get("dry_run") in ("1", "true", "yes")
    file = request.files.get("file")
    
    if not file:
        return jsonify({"error": "Upload a file in form field 'file' (.xlsx or .csv)"}), 400
    
    # Read file
    filename = (file.filename or "").lower()
    try:
        if filename.endswith(".xlsx"):
            df = pd.read_excel(file)
        elif filename.endswith(".csv"):
            df = pd.read_csv(file)
        else:
            return jsonify({"error": "Unsupported file. Use .xlsx or .csv"}), 400
    except Exception as e:
        return jsonify({"error": f"Failed to read file: {str(e)}"}), 400
    
    # Normalize columns
    df.columns = [c.strip() for c in df.columns]
    for col in REQUIRED_COLS:
        if col not in df.columns:
            return jsonify({"error": f"Missing required column: {col}"}), 400
    
    # Convert data types
    df["due_days"] = pd.to_numeric(df["due_days"], errors="coerce").fillna(0).astype(int)
    df["credit_limit"] = pd.to_numeric(df["credit_limit"], errors="coerce")
    df["cheque_days_allowed"] = pd.to_numeric(df["cheque_days_allowed"], errors="coerce")
    
    for bool_col in ["is_credit", "allow_cash", "allow_card_pos", "allow_bank_transfer", "allow_cheque"]:
        df[bool_col] = df[bool_col].apply(truthy)
    df["notes"] = df["notes"].fillna("")
    
    created_customers = 0
    created_terms = 0
    closed_versions = 0
    updated_terms = 0
    skipped_rows = 0
    skipped_codes = []
    batch_count = 0
    
    try:
        for idx, row in df.iterrows():
            customer_code = str(row["customer_code"]).strip()
            
            # Skip if customer code is empty
            if not customer_code or customer_code.lower() in ('', 'nan', 'none'):
                skipped_rows += 1
                continue
            
            # Check if customer exists in payment_customers
            customer = PaymentCustomer.query.filter_by(code=customer_code).first()
            
            # Skip non-matching customers (not in payment_customers table)
            if not customer:
                skipped_rows += 1
                if len(skipped_codes) < 10:  # Limit to first 10 for reporting
                    skipped_codes.append(customer_code)
                continue
            
            # Update customer info from file
            customer.name = row["customer_name"]
            customer.group = row["group"]
            
            # Get active terms (use no_autoflush to prevent duplicate key errors)
            with db.session.no_autoflush:
                active = (
                    CreditTerms.query
                    .filter(CreditTerms.customer_code == customer_code, CreditTerms.valid_to.is_(None))
                    .order_by(CreditTerms.valid_from.desc())
                    .first()
                )
            
            # Create new terms with safe numeric parsing
            terms_code = str(row["terms_code"]).strip() if pd.notna(row["terms_code"]) else ""
            
            # Skip if terms_code is empty
            if not terms_code or terms_code.lower() in ('nan', 'none'):
                skipped_rows += 1
                if len(skipped_codes) < 10:
                    skipped_codes.append(f"{customer_code} (empty terms_code)")
                continue
            
            due_days = safe_int(row["due_days"]) or 0
            new_terms = CreditTerms(
                customer_code=customer_code,
                terms_code=terms_code,
                due_days=due_days,
                is_credit=bool(row["is_credit"]) if pd.notna(row["is_credit"]) else (due_days > 0),
                credit_limit=safe_decimal(row["credit_limit"]),
                allow_cash=bool(row["allow_cash"]),
                allow_card_pos=bool(row["allow_card_pos"]),
                allow_bank_transfer=bool(row["allow_bank_transfer"]),
                allow_cheque=bool(row["allow_cheque"]),
                cheque_days_allowed=safe_int(row["cheque_days_allowed"]),
                notes_for_driver=(row["notes"] or None),
                valid_from=dt.date.today(),
            )
            
            def as_key(t):
                return (
                    t.terms_code, t.due_days, t.is_credit, t.credit_limit,
                    t.allow_cash, t.allow_card_pos, t.allow_bank_transfer, t.allow_cheque,
                    t.cheque_days_allowed, (t.notes_for_driver or "")
                )
            
            if active and as_key(active) == as_key(new_terms):
                continue  # No change
            
            if dry_run:
                updated_terms += 1 if active else 0
                created_terms += 1 if not active else 0
                continue
            
            # Handle same-day updates: delete old terms if created today, else close them
            if active and active.valid_to is None:
                if active.valid_from == dt.date.today():
                    # Same-day update: delete old terms to avoid constraint violation
                    db.session.delete(active)
                    db.session.flush()  # Flush delete
                else:
                    # Close previous version with yesterday's date
                    active.valid_to = dt.date.today() - dt.timedelta(days=1)
                    closed_versions += 1
            
            db.session.add(new_terms)
            created_terms += 1
            batch_count += 1
            
            # Batch commit every 10 rows for performance
            if batch_count >= 10:
                db.session.commit()
                batch_count = 0
        
        # Final commit for remaining rows
        if not dry_run and batch_count > 0:
            db.session.commit()
        
        result = {
            "status": "dry_run_ok" if dry_run else "ok",
            "rows_processed": int(df.shape[0]),
            "rows_updated": int(df.shape[0]) - skipped_rows,
            "rows_skipped": skipped_rows,
            "created_customers": created_customers,
            "created_terms_versions": created_terms,
            "closed_previous_versions": closed_versions,
            "updated_existing": updated_terms
        }
        
        if skipped_codes:
            result["skipped_sample"] = skipped_codes[:10]
            result["note"] = f"Skipped {skipped_rows} rows with customer codes not in payment_customers table"
        
        return jsonify(result)
    except Exception as e:
        if not dry_run:
            db.session.rollback()
        return jsonify({"error": str(e)}), 500

@bp.route('/reconcile', methods=['POST'])
@login_required
def reconcile_missing_terms():
    """Start customer sync in background to avoid timeout"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"error": "Access denied"}), 403
    
    result = start_customer_sync_background(app)
    
    if result.get("success"):
        return jsonify({
            "status": "started",
            "message": "Customer sync started in background"
        }), 202
    else:
        return jsonify({
            "status": "error",
            "error": result.get("error", "Failed to start sync")
        }), 400


@bp.route('/sync-status', methods=['GET'])
@login_required
def sync_status():
    """Get the current status of a running customer sync"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"error": "Access denied"}), 403
    
    status = get_sync_status("customers")
    return jsonify(status)


@bp.route('/reconcile-sync', methods=['POST'])
@login_required
def reconcile_sync():
    """Legacy sync endpoint - runs synchronously (may timeout on large datasets)"""
    from ps365_client import call_ps365
    
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"error": "Access denied"}), 403
    
    try:
        from sqlalchemy.sql import exists, and_
        from sqlalchemy.dialects.postgresql import insert
        from models import PSCustomer
        from main import _default_terms_values_for
        import datetime as dt
        
        logging.info("Starting reconcile missing terms...")
        
        page = 1
        total_fetched = 0
        PAGE_SIZE = 100
        
        while True:
            try:
                response = call_ps365('list_customers', {
                    "filter_define": {
                        "only_counted": "N",
                        "page_number": page,
                        "page_size": PAGE_SIZE,
                        "active_type": "all"
                    }
                }, method="POST")
                
                customers = response.get('list_customers', [])
                if not customers:
                    break
                
                for cust in customers:
                    ps_cust = PSCustomer.query.filter_by(
                        customer_code_365=cust.get('customer_code_365')
                    ).first()
                    
                    if not ps_cust:
                        ps_cust = PSCustomer()
                        db.session.add(ps_cust)
                    
                    ps_cust.customer_code_365 = cust.get('customer_code_365')
                    ps_cust.company_name = cust.get('company_name', '')
                    ps_cust.contact_first_name = cust.get('contact_first_name', '')
                    ps_cust.contact_last_name = cust.get('contact_last_name', '')
                    ps_cust.category_1_name = cust.get('category_1_name', '')
                    ps_cust.active = cust.get('active', True)
                
                db.session.commit()
                total_fetched += len(customers)
                logging.info(f"Fetched page {page}: {len(customers)} customers (total: {total_fetched})")
                page += 1
                
            except Exception as e:
                logging.error(f"Error fetching page {page}: {str(e)}")
                break
        
        logging.info(f"Fetched {total_fetched} customers from PS365 API")
        
        from sqlalchemy import text
        
        result = db.session.execute(text("""
            INSERT INTO payment_customers (code, name, "group")
            SELECT 
                pc.customer_code_365,
                COALESCE(pc.company_name, 
                    TRIM(CONCAT(COALESCE(pc.contact_first_name, ''), ' ', COALESCE(pc.contact_last_name, ''))),
                    'Unknown'),
                pc.category_1_name
            FROM ps_customers pc
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                "group" = EXCLUDED."group"
        """))
        synced = result.rowcount
        db.session.commit()
        logging.info(f"Synced {synced} customers from ps_customers")
        
        terms_defaults = _default_terms_values_for("dummy")
        result = db.session.execute(text("""
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
        created = result.rowcount
        db.session.commit()
        logging.info(f"Created {created} default payment terms")
        
        logging.info(f"Reconcile complete: synced={synced}, created={created}")
        return jsonify({
            "status": "ok", 
            "synced_customers": synced,
            "created_defaults": created
        })
    except Exception as e:
        logging.error(f"Reconcile failed: {str(e)}")
        logging.error(traceback.format_exc())
        db.session.rollback()
        return jsonify({
            "error": str(e),
            "type": type(e).__name__
        }), 500
