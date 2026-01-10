import json
import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app import db
from models import Setting, Invoice
from services_oi_time_estimator import (
    DEFAULT_PARAMS, 
    estimate_and_persist_invoice_time, 
    estimate_invoice_time,
    estimate_and_snapshot_invoice,
    get_params_revision,
    ESTIMATOR_VERSION
)


oi_time_admin_bp = Blueprint("oi_time_admin", __name__)

def _require_admin():
    if not current_user.is_authenticated:
        return False
    return getattr(current_user, "role", "").lower() in ("admin", "warehouse_manager")

@oi_time_admin_bp.route("/admin/oi/api/estimate", methods=["POST"])
@login_required
def api_estimate():
    if not _require_admin():
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    data = request.get_json() or {}
    invoice_no = data.get("invoice_no")
    if not invoice_no:
        return jsonify({"success": False, "error": "No invoice number"}), 400
        
    try:
        res = estimate_invoice_time(invoice_no)
        # Convert tuple keys for JSON
        if 'per_line_seconds' in res:
             res['per_line_seconds'] = {f"{k[0]}|{k[1]}": v for k, v in res['per_line_seconds'].items()}
        return jsonify({"success": True, **res})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@oi_time_admin_bp.route("/admin/oi/time-params", methods=["GET", "POST"])
@login_required
def oi_time_params():
    if not _require_admin():
        flash("Access denied.", "danger")
        return redirect(url_for("index"))

    result = None

    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "save":
            raw = request.form.get("params_json", "").strip()
            try:
                params = json.loads(raw)
                if not isinstance(params, dict):
                    raise ValueError("Params JSON must be an object.")
                
                required_keys = ["travel", "pick", "pack", "overhead"]
                for key in required_keys:
                    if key not in params:
                        raise ValueError(f"Missing required top-level key: {key}")
                
                Setting.set_json(db.session, "oi_time_params_v1", params)
                
                rev = get_params_revision()
                Setting.set(db.session, "oi_time_params_v1_revision", str(rev + 1))
                db.session.commit()
                
                flash(f"ETC parameters saved (revision {rev + 1}).", "success")
            except Exception as e:
                flash(f"Could not save parameters: {e}", "danger")

        elif action == "toggle_summer":
            enabled = request.form.get("summer_mode", "off").lower() in ("1", "true", "on", "yes")
            Setting.set(db.session, "summer_mode", "true" if enabled else "false")
            flash(f"Summer mode set to {'ON' if enabled else 'OFF'}.", "success")

        elif action == "recalc_invoice":
            invoice_no = (request.form.get("invoice_no") or "").strip()
            if not invoice_no:
                flash("Please provide an invoice number.", "warning")
            else:
                try:
                    result = estimate_and_snapshot_invoice(invoice_no, reason="admin_recalc", commit=True)
                    flash(f"Recalculated ETC for invoice {invoice_no} (run ID: {result.get('run_id')}).", "success")
                except Exception as e:
                    logging.error(f"Recalculation failed for {invoice_no}: {e}")
                    flash(f"Recalculation failed: {e}", "danger")

        elif action == "recalc_open":
            statuses = request.form.getlist("statuses") or ["not_started", "picking", "ready_for_dispatch"]
            q = Invoice.query.filter(Invoice.status.in_(statuses))
            count = 0
            last = None
            for inv in q.limit(500).all():
                try:
                    last = estimate_and_snapshot_invoice(inv.invoice_no, reason="admin_batch_recalc", commit=False)
                    count += 1
                except Exception as e:
                    logging.warning(f"Recalc failed for {inv.invoice_no}: {e}")
                    continue
            db.session.commit()
            flash(f"Recalculated ETC for {count} open invoices (statuses: {', '.join(statuses)}).", "success")
            result = last

        return redirect(url_for("oi_time_admin.oi_time_params"))

    params = Setting.get_json(db.session, "oi_time_params_v1", default=DEFAULT_PARAMS)
    summer_mode = Setting.get(db.session, "summer_mode", "false").lower() in ("1", "true", "yes", "on")
    params_revision = get_params_revision()

    return render_template(
        "admin/oi_time_params.html",
        params_json=json.dumps(params, indent=2, ensure_ascii=False),
        summer_mode=summer_mode,
        last_result=Setting.get_json(db.session, "oi_time_last_result", default=None),
        invoice_no="IN10052585",
        params_revision=params_revision,
        estimator_version=ESTIMATOR_VERSION
    )
