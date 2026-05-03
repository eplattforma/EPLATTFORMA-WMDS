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

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import text

from app import db
from models import Setting
from services.permissions import require_permission

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

@cockpit_bp.route("/")
@login_required
@require_permission("menu.cockpit")
def search():
    return render_template("cockpit/search.html")


@cockpit_bp.route("/api/search")
@login_required
@require_permission("menu.cockpit")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": []})
    rows = db.session.execute(text("""
        SELECT customer_code_365 AS code,
               COALESCE(company_name, '') AS name
        FROM ps_customers
        WHERE (customer_code_365 ILIKE :likeq
           OR COALESCE(company_name, '') ILIKE :likeq)
          AND deleted_at IS NULL
        ORDER BY customer_code_365
        LIMIT 20
    """), {"likeq": f"%{q}%"}).mappings().all()
    return jsonify({"items": [dict(r) for r in rows]})


@cockpit_bp.route("/<customer_code>")
@login_required
@require_permission("customers.use_cockpit")
def cockpit(customer_code):
    """Placeholder page — Ticket 2 fills in the full layout."""
    from services.cockpit_targets import get_target, compute_achievement
    state = get_target(customer_code)
    achievement = {p: compute_achievement(customer_code, p)
                   for p in ("mtd", "qtd", "ytd", "weekly_average")}
    return render_template(
        "cockpit/cockpit.html",
        customer_code=customer_code,
        target_state=state,
        achievement=achievement,
        placeholder=True,
    )


@cockpit_bp.route("/admin/targets")
@login_required
@require_permission("customers.approve_target")
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
@require_permission("customers.use_cockpit")
def api_get_target(customer_code):
    from services.cockpit_targets import get_target
    return jsonify(get_target(customer_code))


@cockpit_bp.route("/api/<customer_code>/target/propose", methods=["POST"])
@login_required
@require_permission("customers.propose_target")
def api_propose_target(customer_code):
    from services.cockpit_targets import propose_target
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        out = propose_target(customer_code, payload, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/set", methods=["POST"])
@login_required
@require_permission("customers.approve_target")
def api_set_target(customer_code):
    from services.cockpit_targets import set_target_directly
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        out = set_target_directly(customer_code, payload, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/approve", methods=["POST"])
@login_required
@require_permission("customers.approve_target")
def api_approve_target(customer_code):
    from services.cockpit_targets import approve_proposal
    try:
        out = approve_proposal(customer_code, actor=_actor())
        return jsonify(out)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@cockpit_bp.route("/api/<customer_code>/target/reject", methods=["POST"])
@login_required
@require_permission("customers.approve_target")
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
@require_permission("customers.use_cockpit")
def api_target_history(customer_code):
    from services.cockpit_targets import get_target_history
    return jsonify(get_target_history(customer_code))
