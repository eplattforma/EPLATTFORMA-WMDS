"""Phase 6 admin pages for cooler box-type catalogue + data-quality
report.

Endpoints:
  * GET/POST  /admin/cooler-box-types/                  — list + create
  * POST      /admin/cooler-box-types/<id>/update       — edit
  * POST      /admin/cooler-box-types/<id>/toggle-active — soft toggle
  * GET       /admin/cooler-items-missing-dimensions/   — DQ report

Permission key: ``cooler.manage_box_catalogue``. Auto-covered by admin
``*`` and warehouse_manager ``cooler.*`` wildcards.
"""
import logging
from functools import wraps

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import text

from app import db
from services.cooler_estimator import items_missing_dimensions_report
from services.permissions import require_permission
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)

cooler_admin_bp = Blueprint(
    "cooler_admin", __name__, url_prefix="/admin",
)

_MANAGE_ROLES = frozenset({"admin", "warehouse_manager"})


def _require_manage(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_authenticated", False):
            abort(401)
        if (getattr(current_user, "role", None) or "").lower() not in _MANAGE_ROLES:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def _fetch_box_types():
    rows = db.session.execute(text(
        "SELECT id, name, description, internal_length_cm, "
        "       internal_width_cm, internal_height_cm, internal_volume_cm3, "
        "       fill_efficiency, max_weight_kg, is_active, sort_order "
        "FROM cooler_box_types "
        "ORDER BY sort_order, name"
    )).fetchall()
    return [
        {
            "id": r[0], "name": r[1], "description": r[2] or "",
            "internal_length_cm": float(r[3]),
            "internal_width_cm": float(r[4]),
            "internal_height_cm": float(r[5]),
            "internal_volume_cm3": float(r[6]),
            "internal_volume_l": round(float(r[6]) / 1000.0, 2),
            "fill_efficiency": float(r[7]),
            "effective_capacity_l": round(
                float(r[6]) * float(r[7]) / 1000.0, 2),
            "max_weight_kg": (
                float(r[8]) if r[8] is not None else None),
            "is_active": bool(r[9]),
            "sort_order": int(r[10] or 0),
        }
        for r in rows
    ]


def _parse_box_form(form):
    """Validate POST data; return (cleaned_dict, error_str_or_None)."""
    name = (form.get("name") or "").strip()
    if not name:
        return None, "Name is required"
    try:
        l = float(form.get("internal_length_cm") or 0)
        w = float(form.get("internal_width_cm") or 0)
        h = float(form.get("internal_height_cm") or 0)
    except (TypeError, ValueError):
        return None, "Dimensions must be numeric"
    if l <= 0 or w <= 0 or h <= 0:
        return None, "Dimensions must be positive"
    try:
        fe = float(form.get("fill_efficiency") or 0.75)
    except (TypeError, ValueError):
        return None, "Fill efficiency must be numeric (e.g. 0.75)"
    if not (0 < fe <= 1):
        return None, "Fill efficiency must be between 0 (excl.) and 1"
    max_weight = form.get("max_weight_kg")
    try:
        max_weight = float(max_weight) if max_weight else None
    except (TypeError, ValueError):
        return None, "Max weight must be numeric or blank"
    try:
        sort_order = int(form.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    return {
        "name": name,
        "description": (form.get("description") or "").strip() or None,
        "internal_length_cm": l,
        "internal_width_cm": w,
        "internal_height_cm": h,
        "internal_volume_cm3": l * w * h,
        "fill_efficiency": fe,
        "max_weight_kg": max_weight,
        "sort_order": sort_order,
    }, None


@cooler_admin_bp.route("/cooler-box-types/", methods=["GET", "POST"])
@login_required
@_require_manage
@require_permission("cooler.manage_box_catalogue")
def cooler_box_types():
    if request.method == "POST":
        cleaned, err = _parse_box_form(request.form)
        if err:
            flash(err, "danger")
            return redirect(url_for("cooler_admin.cooler_box_types"))
        try:
            db.session.execute(text(
                "INSERT INTO cooler_box_types "
                "(name, description, internal_length_cm, internal_width_cm, "
                " internal_height_cm, internal_volume_cm3, fill_efficiency, "
                " max_weight_kg, is_active, sort_order, created_at, updated_at) "
                "VALUES (:n, :d, :l, :w, :h, :v, :fe, :mw, :truthy, :so, :now, :now)"
            ), {**cleaned, "n": cleaned["name"], "d": cleaned["description"],
                "l": cleaned["internal_length_cm"],
                "w": cleaned["internal_width_cm"],
                "h": cleaned["internal_height_cm"],
                "v": cleaned["internal_volume_cm3"],
                "fe": cleaned["fill_efficiency"],
                "mw": cleaned["max_weight_kg"],
                "so": cleaned["sort_order"],
                "truthy": True, "now": get_utc_now()})
            db.session.commit()
            flash(f"Box type '{cleaned['name']}' created", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Could not create box type: {e}", "danger")
        return redirect(url_for("cooler_admin.cooler_box_types"))

    box_types = _fetch_box_types()
    return render_template(
        "admin/cooler_box_types.html", box_types=box_types,
    )


@cooler_admin_bp.route(
    "/cooler-box-types/<int:type_id>/update", methods=["POST"]
)
@login_required
@_require_manage
@require_permission("cooler.manage_box_catalogue")
def cooler_box_type_update(type_id):
    cleaned, err = _parse_box_form(request.form)
    if err:
        flash(err, "danger")
        return redirect(url_for("cooler_admin.cooler_box_types"))
    try:
        db.session.execute(text(
            "UPDATE cooler_box_types "
            "SET name = :n, description = :d, "
            "    internal_length_cm = :l, internal_width_cm = :w, "
            "    internal_height_cm = :h, internal_volume_cm3 = :v, "
            "    fill_efficiency = :fe, max_weight_kg = :mw, "
            "    sort_order = :so, updated_at = :now "
            "WHERE id = :id"
        ), {"id": type_id, "n": cleaned["name"],
            "d": cleaned["description"],
            "l": cleaned["internal_length_cm"],
            "w": cleaned["internal_width_cm"],
            "h": cleaned["internal_height_cm"],
            "v": cleaned["internal_volume_cm3"],
            "fe": cleaned["fill_efficiency"],
            "mw": cleaned["max_weight_kg"],
            "so": cleaned["sort_order"],
            "now": get_utc_now()})
        db.session.commit()
        flash(f"Box type '{cleaned['name']}' updated", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not update box type: {e}", "danger")
    return redirect(url_for("cooler_admin.cooler_box_types"))


@cooler_admin_bp.route(
    "/cooler-box-types/<int:type_id>/toggle-active", methods=["POST"]
)
@login_required
@_require_manage
@require_permission("cooler.manage_box_catalogue")
def cooler_box_type_toggle(type_id):
    try:
        row = db.session.execute(text(
            "SELECT is_active FROM cooler_box_types WHERE id = :id"
        ), {"id": type_id}).fetchone()
        if row is None:
            abort(404)
        new_state = not bool(row[0])
        db.session.execute(text(
            "UPDATE cooler_box_types SET is_active = :a, updated_at = :now "
            "WHERE id = :id"
        ), {"id": type_id, "a": new_state, "now": get_utc_now()})
        db.session.commit()
        flash(
            f"Box type {'activated' if new_state else 'deactivated'}",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Could not toggle box type: {e}", "danger")
    return redirect(url_for("cooler_admin.cooler_box_types"))


@cooler_admin_bp.route("/cooler-items-missing-dimensions/")
@login_required
@_require_manage
@require_permission("cooler.manage_box_catalogue")
def cooler_items_missing_dimensions():
    fmt = (request.args.get("format") or "").lower()
    rows = items_missing_dimensions_report(limit=500)
    if fmt == "json":
        return jsonify(rows)
    return render_template(
        "admin/cooler_items_missing_dims.html", rows=rows,
    )
