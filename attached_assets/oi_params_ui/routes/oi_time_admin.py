import json
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app import db
from models import Setting, Invoice
from services.oi_time_estimator import DEFAULT_PARAMS, estimate_and_persist_invoice_time


oi_time_admin_bp = Blueprint("oi_time_admin", __name__)

def _require_admin():
    if not current_user.is_authenticated:
        return False
    return getattr(current_user, "role", "").lower() in ("admin", "warehouse_manager")

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
                Setting.set_json(db.session, "oi_time_params_v1", params)
                flash("ETC parameters saved.", "success")
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
                    result = estimate_and_persist_invoice_time(invoice_no, commit=True)
                    flash(f"Recalculated ETC for invoice {invoice_no}.", "success")
                except Exception as e:
                    flash(f"Recalculation failed: {e}", "danger")

        elif action == "recalc_open":
            # Recalculate for orders that are not shipped/delivered
            statuses = request.form.getlist("statuses") or ["not_started", "picking", "ready_for_dispatch"]
            q = Invoice.query.filter(Invoice.status.in_(statuses))
            count = 0
            last = None
            for inv in q.limit(500).all():  # safety cap
                try:
                    last = estimate_and_persist_invoice_time(inv.invoice_no, commit=False)
                    count += 1
                except Exception:
                    continue
            db.session.commit()
            flash(f"Recalculated ETC for {count} open invoices (statuses: {', '.join(statuses)}).", "success")
            result = last

        return redirect(url_for("oi_time_admin.oi_time_params"))

    params = Setting.get_json(db.session, "oi_time_params_v1", default=DEFAULT_PARAMS)
    summer_mode = Setting.get(db.session, "summer_mode", "false").lower() in ("1", "true", "yes", "on")

    return render_template(
        "admin/oi_time_params.html",
        params_json=json.dumps(params, indent=2, ensure_ascii=False),
        summer_mode=summer_mode,
        last_result=Setting.get_json(db.session, "oi_time_last_result", default=None)
    )
