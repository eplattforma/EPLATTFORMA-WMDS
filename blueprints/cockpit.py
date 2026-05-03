"""Cockpit blueprint (Ticket 1 scaffold).

Master flag: ``cockpit_enabled`` (default ``false``). When the flag is OFF
every route returns 404 — the entire URL space is hidden. When ON,
per-permission ``@require_permission`` decorators gate access.

Permission keys (defined in ``services.permissions.ROLE_PERMISSIONS``):
    menu.cockpit              — sees the menu entry & search page
    customers.use_cockpit     — opens an individual cockpit page
    customers.propose_target  — AM proposes a target
    customers.approve_target  — Manager approves/rejects/sets directly
    customers.ask_claude      — Ticket 3
"""
from __future__ import annotations

import logging

from functools import wraps

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

from app import db
from models import Setting
from services.permissions import has_permission


def require_permission_hard(key: str):
    """Per cockpit-brief Section 14: cockpit endpoints must enforce
    permissions **regardless** of the global ``permissions_enforcement_enabled``
    flag. The shared ``services.permissions.require_permission`` decorator is
    non-blocking while that flag is OFF (Phase 1/3 rollout), so we wrap each
    cockpit view in a hard 403 gate as well.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not has_permission(current_user, key):
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator

logger = logging.getLogger(__name__)

cockpit_bp = Blueprint("cockpit", __name__, url_prefix="/cockpit")


def _cockpit_enabled() -> bool:
    try:
        return Setting.get(db.session, "cockpit_enabled", "false").lower() == "true"
    except Exception:
        return False


@cockpit_bp.before_request
def _gate_master_flag():
    if not _cockpit_enabled():
        abort(404)


# ─── Pages ──────────────────────────────────────────────────────────────

def _search_customers(q: str, limit: int = 20) -> list[dict]:
    """Cross-dialect customer search by code or name (LIKE on lowercased values)."""
    if not q:
        return []
    rows = db.session.execute(text("""
        SELECT customer_code_365 AS code,
               COALESCE(company_name, '') AS name
        FROM ps_customers
        WHERE (LOWER(customer_code_365) LIKE :likeq
           OR LOWER(COALESCE(company_name, '')) LIKE :likeq)
          AND deleted_at IS NULL
        ORDER BY customer_code_365
        LIMIT :lim
    """), {"likeq": f"%{q.lower()}%", "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


@cockpit_bp.route("/")
@login_required
@require_permission_hard("menu.cockpit")
def search():
    """Picker landing. If the user submitted a query that uniquely resolves
    to a single customer, redirect straight to that cockpit page (brief
    Section 10 picker behaviour). Otherwise render the chooser."""
    q = (request.args.get("q") or "").strip()
    matches = _search_customers(q) if q else []
    if q:
        # Exact code match → go straight in
        for m in matches:
            if (m["code"] or "").lower() == q.lower():
                return redirect(url_for("cockpit.cockpit", customer_code=m["code"]))
        if len(matches) == 1:
            return redirect(url_for("cockpit.cockpit", customer_code=matches[0]["code"]))
    return render_template("cockpit/search.html", q=q, matches=matches)


@cockpit_bp.route("/api/search")
@login_required
@require_permission_hard("menu.cockpit")
def api_search():
    q = (request.args.get("q") or "").strip()
    return jsonify({"items": _search_customers(q)})


@cockpit_bp.route("/<customer_code>")
@login_required
@require_permission_hard("customers.use_cockpit")
def cockpit(customer_code):
    """Main cockpit page — cockpit-brief §11.

    Page-level controls (period / compare / peer_group) are read from
    the URL query string and propagate to every section.
    """
    from services.cockpit_data import get_cockpit_data

    try:
        period_days = int(request.args.get("period", "90"))
    except (TypeError, ValueError):
        period_days = 90
    if period_days not in (90, 180, 365):
        period_days = 90
    compare = (request.args.get("compare") or "py").lower()
    if compare not in ("py", "prev", "prev_period", "none"):
        compare = "py"
    peer_group = (request.args.get("peer_group") or "auto").strip() or "auto"

    # Customer must exist before we render — surface a clean 404.
    exists = db.session.execute(
        text("SELECT 1 FROM ps_customers WHERE customer_code_365 = :c LIMIT 1"),
        {"c": customer_code},
    ).first()
    if not exists:
        abort(404)

    try:
        data = get_cockpit_data(customer_code,
                                period_days=period_days,
                                compare=compare,
                                peer_group=peer_group)
    except Exception:
        logger.exception("Cockpit data assembly failed for %s", customer_code)
        # Fail loud in the UI rather than silently masking — the template
        # checks for ``data`` and shows an inline error if absent.
        data = None

    return render_template(
        "cockpit/cockpit.html",
        customer_code=customer_code,
        data=data,
        controls={"period": period_days, "compare": compare,
                  "peer_group": peer_group},
    )


@cockpit_bp.route("/admin/targets")
@login_required
@require_permission_hard("customers.approve_target")
def admin_targets():
    from services.cockpit_targets import list_all_targets
    rows = list_all_targets(filters=request.args)
    return render_template(
        "cockpit/admin_targets.html",
        rows=rows,
        filters=request.args,
    )


# ─── Target APIs ────────────────────────────────────────────────────────

def _actor() -> str:
    return getattr(current_user, "username", "?") or "?"


@cockpit_bp.route("/api/<customer_code>/target", methods=["GET"])
@login_required
@require_permission_hard("customers.use_cockpit")
def api_get_target(customer_code):
    from services.cockpit_targets import get_target
    return jsonify(get_target(customer_code))


@cockpit_bp.route("/api/<customer_code>/target/propose", methods=["POST"])
@login_required
@require_permission_hard("customers.propose_target")
def api_propose_target(customer_code):
    from services.cockpit_targets import propose_target
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        out = propose_target(customer_code, payload, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/set", methods=["POST", "PATCH"])
@login_required
@require_permission_hard("customers.approve_target")
def api_set_target(customer_code):
    """Brief §10.5 specifies PATCH; we also accept POST for browsers/forms."""
    from services.cockpit_targets import set_target_directly
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        out = set_target_directly(customer_code, payload, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/clear", methods=["POST"])
@login_required
@require_permission_hard("customers.approve_target")
def api_clear_target(customer_code):
    from services.cockpit_targets import clear_target
    try:
        out = clear_target(customer_code, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/approve", methods=["POST"])
@login_required
@require_permission_hard("customers.approve_target")
def api_approve_target(customer_code):
    from services.cockpit_targets import approve_proposal
    try:
        out = approve_proposal(customer_code, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/reject", methods=["POST"])
@login_required
@require_permission_hard("customers.approve_target")
def api_reject_target(customer_code):
    from services.cockpit_targets import reject_proposal
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        out = reject_proposal(customer_code,
                              reason=payload.get("reason"),
                              actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/history", methods=["GET"])
@login_required
@require_permission_hard("customers.use_cockpit")
def api_target_history(customer_code):
    from services.cockpit_targets import get_target_history
    return jsonify(get_target_history(customer_code))


@cockpit_bp.route("/api/targets/bulk_set", methods=["POST"])
@login_required
@require_permission_hard("customers.approve_target")
def api_bulk_set_targets():
    """Brief 10.5: 'set annual = X for selected'. One DB transaction,
    one history row per customer."""
    from services.cockpit_targets import bulk_set_annual_targets
    payload = request.get_json(silent=True) or request.form.to_dict(flat=False) or {}
    codes = payload.get("codes") or []
    if isinstance(codes, str):
        codes = [c.strip() for c in codes.split(",") if c.strip()]
    annual = payload.get("annual")
    if not codes or annual in (None, ""):
        return jsonify({"error": "codes and annual are required"}), 400
    try:
        result = bulk_set_annual_targets(codes, annual, actor=_actor())
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
