import json
import logging
from flask import Blueprint, request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, and_
from datetime import date, datetime, timedelta, timezone

from app import db
from models import (
    PSCustomer, CrmCustomerProfile, Setting,
    MagentoCustomerLastLoginCurrent, DwInvoiceHeader,
    CrmAbandonedCartState, CrmTask, CrmInteractionLog,
)

logger = logging.getLogger(__name__)

crm_dashboard_bp = Blueprint("crm_dashboard", __name__, url_prefix="/crm")


def _get_allowed_classifications():
    s = Setting.query.filter_by(key="crm_customer_classifications").first()
    if not s or not s.value:
        return ["Customer", "EKO", "Petrolina", "SHELL", "Monitor", "At Risk", "Frozen"]
    try:
        return json.loads(s.value)
    except Exception:
        return ["Customer", "EKO", "Petrolina", "SHELL", "Monitor", "At Risk", "Frozen"]


@crm_dashboard_bp.get("/dashboard")
@login_required
def customer_slot_dashboard():
    slot = request.args.get("slot")
    classification = request.args.get("classification")
    district = request.args.get("district")
    area = request.args.get("area")
    action_only = request.args.get("action_only") == "1"
    has_cart_only = request.args.get("has_cart_only") == "1"
    logged_in_days = request.args.get("logged_in_days")
    search_q = request.args.get("q", "").strip()

    today = date.today()
    cycle_end = today
    cycle_start = today - timedelta(days=7)

    d6m = today - timedelta(days=183)
    d4w = today - timedelta(days=28)
    d90 = today - timedelta(days=90)

    sales_6m_sq = (
        db.session.query(
            DwInvoiceHeader.customer_code_365.label("cc"),
            func.coalesce(func.sum(DwInvoiceHeader.total_grand), 0).label("value_6m"),
        )
        .filter(DwInvoiceHeader.invoice_date_utc0 >= d6m)
        .group_by(DwInvoiceHeader.customer_code_365)
        .subquery()
    )

    sales_4w_sq = (
        db.session.query(
            DwInvoiceHeader.customer_code_365.label("cc"),
            func.coalesce(func.sum(DwInvoiceHeader.total_grand), 0).label("value_4w"),
        )
        .filter(DwInvoiceHeader.invoice_date_utc0 >= d4w)
        .group_by(DwInvoiceHeader.customer_code_365)
        .subquery()
    )

    last_invoice_sq = (
        db.session.query(
            DwInvoiceHeader.customer_code_365.label("cc"),
            func.max(DwInvoiceHeader.invoice_date_utc0).label("last_invoice_date"),
            func.count(DwInvoiceHeader.invoice_no_365).label("inv_cnt_90d"),
        )
        .filter(DwInvoiceHeader.invoice_date_utc0 >= d90)
        .group_by(DwInvoiceHeader.customer_code_365)
        .subquery()
    )

    done_sq = (
        db.session.query(
            DwInvoiceHeader.customer_code_365.label("cc"),
            func.count(DwInvoiceHeader.invoice_no_365).label("inv_in_cycle"),
        )
        .filter(and_(
            DwInvoiceHeader.invoice_date_utc0 >= cycle_start,
            DwInvoiceHeader.invoice_date_utc0 <= cycle_end,
        ))
        .group_by(DwInvoiceHeader.customer_code_365)
        .subquery()
    )

    q = (
        db.session.query(
            PSCustomer.customer_code_365,
            PSCustomer.company_name,

            CrmCustomerProfile.classification,
            CrmCustomerProfile.district,
            CrmCustomerProfile.area,

            CrmAbandonedCartState.has_abandoned_cart,
            CrmAbandonedCartState.abandoned_cart_amount,

            MagentoCustomerLastLoginCurrent.last_login_at.label("last_login_at"),

            sales_6m_sq.c.value_6m,
            sales_4w_sq.c.value_4w,
            last_invoice_sq.c.last_invoice_date,
            last_invoice_sq.c.inv_cnt_90d,

            done_sq.c.inv_in_cycle,
        )
        .filter(PSCustomer.active == True)
        .filter(PSCustomer.deleted_at.is_(None))
        .outerjoin(CrmCustomerProfile, CrmCustomerProfile.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(CrmAbandonedCartState, CrmAbandonedCartState.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(MagentoCustomerLastLoginCurrent, MagentoCustomerLastLoginCurrent.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(sales_6m_sq, sales_6m_sq.c.cc == PSCustomer.customer_code_365)
        .outerjoin(sales_4w_sq, sales_4w_sq.c.cc == PSCustomer.customer_code_365)
        .outerjoin(last_invoice_sq, last_invoice_sq.c.cc == PSCustomer.customer_code_365)
        .outerjoin(done_sq, done_sq.c.cc == PSCustomer.customer_code_365)
    )

    if search_q:
        like_pat = f"%{search_q}%"
        q = q.filter(
            db.or_(
                PSCustomer.customer_code_365.ilike(like_pat),
                PSCustomer.company_name.ilike(like_pat),
            )
        )

    if classification:
        q = q.filter(CrmCustomerProfile.classification == classification)
    if district:
        q = q.filter(CrmCustomerProfile.district == district)
    if area:
        q = q.filter(CrmCustomerProfile.area == area)
    if has_cart_only:
        q = q.filter(CrmAbandonedCartState.has_abandoned_cart.is_(True))
    if logged_in_days:
        try:
            days = int(logged_in_days)
            q = q.filter(MagentoCustomerLastLoginCurrent.last_login_at.isnot(None))
            q = q.filter(MagentoCustomerLastLoginCurrent.last_login_at >= datetime.now(timezone.utc) - timedelta(days=days))
        except Exception:
            pass

    rows = q.all()

    now_utc = datetime.now(timezone.utc)
    allowed_classifications = _get_allowed_classifications()

    all_districts = sorted({r.district for r in rows if r.district})
    all_areas = sorted({r.area for r in rows if r.area})

    dashboard_rows = []
    for r in rows:
        inv_in_cycle = r.inv_in_cycle or 0
        done_for_cycle = inv_in_cycle > 0
        done_source = "INVOICE" if done_for_cycle else "NONE"

        has_cart = bool(r.has_abandoned_cart) if r.has_abandoned_cart is not None else False
        cart_amount = r.abandoned_cart_amount

        last_login_at = r.last_login_at
        r_login_days = None
        if last_login_at:
            r_login_days = (now_utc.date() - last_login_at.date()).days

        last_invoice_date = r.last_invoice_date
        r_invoice_days = None
        if last_invoice_date:
            r_invoice_days = (now_utc.date() - last_invoice_date).days

        if has_cart:
            next_action = "CART_NUDGE"
        elif not done_for_cycle:
            next_action = "ORDER_REMINDER"
        else:
            next_action = "NO_ACTION"

        if action_only and next_action == "NO_ACTION":
            continue

        dashboard_rows.append({
            "customer_code_365": r.customer_code_365,
            "customer_name": r.company_name or r.customer_code_365,
            "classification": r.classification or "",
            "district": r.district or "",
            "area": r.area or "",
            "done_for_cycle": done_for_cycle,
            "done_source": done_source,
            "has_cart": has_cart,
            "cart_amount": float(cart_amount) if cart_amount is not None else None,
            "last_login_at": last_login_at,
            "r_login_days": r_login_days,
            "value_6m": float(r.value_6m or 0),
            "value_4w": float(r.value_4w or 0),
            "last_invoice_date": last_invoice_date,
            "r_invoice_days": r_invoice_days,
            "inv_cnt_90d": int(r.inv_cnt_90d or 0),
            "next_action": next_action,
        })

    return render_template(
        "crm/dashboard.html",
        rows=dashboard_rows,
        total_count=len(rows),
        allowed_classifications=allowed_classifications,
        all_districts=all_districts,
        all_areas=all_areas,
        filters={
            "slot": slot or "",
            "classification": classification or "",
            "district": district or "",
            "area": area or "",
            "action_only": action_only,
            "has_cart_only": has_cart_only,
            "logged_in_days": logged_in_days or "",
            "q": search_q,
        },
    )


@crm_dashboard_bp.post("/customer/<customer_code_365>/set-classification")
@login_required
def set_customer_classification(customer_code_365):
    new_value = (request.form.get("classification") or "").strip()
    allowed = _get_allowed_classifications()
    if new_value and new_value not in allowed:
        return jsonify({"ok": False, "error": "Invalid classification"}), 400

    prof = CrmCustomerProfile.query.get(customer_code_365)
    if not prof:
        prof = CrmCustomerProfile(customer_code_365=customer_code_365)
        db.session.add(prof)

    prof.classification = new_value or None
    prof.updated_at = datetime.now(timezone.utc)
    prof.updated_by = getattr(current_user, "username", None)
    db.session.commit()
    return jsonify({"ok": True, "customer_code_365": customer_code_365, "classification": new_value})


@crm_dashboard_bp.post("/customer/<customer_code_365>/log-interaction")
@login_required
def log_interaction(customer_code_365):
    channel = (request.form.get("channel") or "").strip().upper()
    if channel not in ("SMS", "PUSH", "CALL", "VISIT", "OFFER"):
        return jsonify({"ok": False, "error": "Invalid channel"}), 400

    outcome = (request.form.get("outcome") or "COMPLETED").strip()
    notes = (request.form.get("notes") or "").strip()

    log_entry = CrmInteractionLog(
        customer_code_365=customer_code_365,
        channel=channel,
        outcome=outcome,
        message_text=notes or None,
        created_by=getattr(current_user, "username", None),
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(log_entry)
    db.session.commit()
    return jsonify({"ok": True, "id": log_entry.id})


@crm_dashboard_bp.post("/customer/<customer_code_365>/create-task")
@login_required
def create_task(customer_code_365):
    task_type = (request.form.get("task_type") or "").strip().upper()
    if task_type not in ("CALL", "VISIT", "FOLLOW_UP", "OFFER_FOLLOWUP"):
        return jsonify({"ok": False, "error": "Invalid task type"}), 400

    notes = (request.form.get("notes") or "").strip()
    priority = (request.form.get("priority") or "MED").strip().upper()

    task = CrmTask(
        customer_code_365=customer_code_365,
        task_type=task_type,
        status="OPEN",
        priority=priority,
        notes=notes or None,
        assigned_to=getattr(current_user, "username", None),
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(task)
    db.session.commit()
    return jsonify({"ok": True, "id": task.id})


@crm_dashboard_bp.get("/customer/<customer_code_365>/timeline")
@login_required
def customer_timeline(customer_code_365):
    interactions = (
        CrmInteractionLog.query
        .filter_by(customer_code_365=customer_code_365)
        .order_by(CrmInteractionLog.created_at.desc())
        .limit(50)
        .all()
    )
    tasks = (
        CrmTask.query
        .filter_by(customer_code_365=customer_code_365)
        .order_by(CrmTask.created_at.desc())
        .limit(50)
        .all()
    )

    timeline = []
    for i in interactions:
        timeline.append({
            "type": "interaction",
            "channel": i.channel,
            "outcome": i.outcome,
            "notes": i.message_text,
            "created_by": i.created_by,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        })
    for t in tasks:
        timeline.append({
            "type": "task",
            "task_type": t.task_type,
            "status": t.status,
            "priority": t.priority,
            "notes": t.notes,
            "assigned_to": t.assigned_to,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    timeline.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return jsonify(timeline)
