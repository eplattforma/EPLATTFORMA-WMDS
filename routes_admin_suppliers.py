"""
Admin Suppliers — CRUD for ReplenishmentSupplier.
Not synced from PS365; managed manually here.
"""
import logging
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db
from models import ReplenishmentSupplier

logger = logging.getLogger(__name__)

admin_suppliers_bp = Blueprint("admin_suppliers", __name__, url_prefix="/admin/suppliers")


def _require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            return redirect(url_for("login"))
        if (getattr(current_user, "role", "") or "").lower() not in ("admin", "warehouse_manager"):
            flash("Access denied.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


# ── List ────────────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/")
@login_required
@_require_admin
def supplier_list():
    suppliers = (ReplenishmentSupplier.query
                 .order_by(ReplenishmentSupplier.sort_order,
                           ReplenishmentSupplier.supplier_name)
                 .all())

    known_codes = {s.supplier_code for s in suppliers}
    dw_rows = db.session.execute(text("""
        SELECT DISTINCT supplier_code_365, MIN(supplier_name) AS supplier_name
        FROM ps_items_dw
        WHERE supplier_code_365 IS NOT NULL AND supplier_code_365 <> ''
        GROUP BY supplier_code_365
        ORDER BY MIN(supplier_name)
    """)).fetchall()
    missing_suppliers = [
        {"code": r[0], "name": r[1] or r[0]}
        for r in dw_rows
        if (r[0] or "").strip().upper() not in known_codes
    ]

    return render_template("admin/suppliers.html",
                           suppliers=suppliers,
                           missing_suppliers=missing_suppliers)


# ── Create ──────────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/create", methods=["POST"])
@login_required
@_require_admin
def supplier_create():
    code = (request.form.get("supplier_code") or "").strip().upper()
    name = (request.form.get("supplier_name") or "").strip()
    if not code or not name:
        flash("Supplier code and name are required.", "danger")
        return redirect(url_for("admin_suppliers.supplier_list"))

    existing = ReplenishmentSupplier.query.filter_by(supplier_code=code).first()
    if existing:
        flash(f"Supplier '{code}' already exists.", "warning")
        return redirect(url_for("admin_suppliers.supplier_list"))

    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0

    s = ReplenishmentSupplier(
        supplier_code=code,
        supplier_name=name,
        email=(request.form.get("email") or "").strip() or None,
        email_cc=(request.form.get("email_cc") or "").strip() or None,
        notes=(request.form.get("notes") or "").strip() or None,
        sort_order=sort_order,
        is_active=True,
    )
    db.session.add(s)
    db.session.commit()
    flash(f"Supplier '{name}' created.", "success")
    return redirect(url_for("admin_suppliers.supplier_list"))


# ── Update ──────────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/<int:supplier_id>/update", methods=["POST"])
@login_required
@_require_admin
def supplier_update(supplier_id):
    s = ReplenishmentSupplier.query.get_or_404(supplier_id)
    name = (request.form.get("supplier_name") or "").strip()
    if not name:
        flash("Supplier name is required.", "danger")
        return redirect(url_for("admin_suppliers.supplier_list"))

    try:
        sort_order = int(request.form.get("sort_order") or 0)
    except ValueError:
        sort_order = s.sort_order or 0

    s.supplier_name = name
    s.email         = (request.form.get("email")    or "").strip() or None
    s.email_cc      = (request.form.get("email_cc") or "").strip() or None
    s.notes         = (request.form.get("notes")    or "").strip() or None
    s.sort_order    = sort_order
    db.session.commit()
    flash(f"Supplier '{name}' updated.", "success")
    return redirect(url_for("admin_suppliers.supplier_list"))


# ── Toggle active ────────────────────────────────────────────────────────────

@admin_suppliers_bp.route("/<int:supplier_id>/toggle", methods=["POST"])
@login_required
@_require_admin
def supplier_toggle(supplier_id):
    s = ReplenishmentSupplier.query.get_or_404(supplier_id)
    s.is_active = not s.is_active
    db.session.commit()
    state = "activated" if s.is_active else "deactivated"
    flash(f"Supplier '{s.supplier_name}' {state}.", "success")
    return redirect(url_for("admin_suppliers.supplier_list"))
