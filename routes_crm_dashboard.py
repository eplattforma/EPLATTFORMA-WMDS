import json
import logging
from flask import Blueprint, request, render_template, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, and_, or_, case, text
from datetime import date, datetime, timedelta, timezone

from app import db
from models import (
    PSCustomer, CrmCustomerProfile, Setting,
    MagentoCustomerLastLoginCurrent, DwInvoiceHeader,
    CrmAbandonedCartState, CrmTask, CrmInteractionLog,
    CustomerDeliverySlot, PostalCodeLookup,
    CRMCustomerOpenOrders, PSPendingOrderHeader,
    CrmOrderingReview, CRMCommunicationLog,
)
from services.crm_order_window import (
    get_customer_window_status, ATHENS_TZ,
)

logger = logging.getLogger(__name__)

crm_dashboard_bp = Blueprint("crm_dashboard", __name__, url_prefix="/crm")

DEFAULT_CLASSIFICATIONS = [
    "Customer", "EKO", "Petrolina", "SHELL", "Monitor", "At Risk", "Frozen"
]


def _normalize_classifications(raw):
    default_map = {name: {"icon": None} for name in DEFAULT_CLASSIFICATIONS}

    if not raw:
        return default_map

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return default_map

    if isinstance(raw, list):
        out = {}
        for name in raw:
            name = (str(name) or "").strip()
            if name:
                out[name] = {"icon": None}
        return out or default_map

    if isinstance(raw, dict):
        out = {}
        for name, meta in raw.items():
            name = (str(name) or "").strip()
            if not name:
                continue
            if isinstance(meta, dict):
                out[name] = {
                    "icon": meta.get("icon"),
                    "color": meta.get("color"),
                    "sort_order": meta.get("sort_order"),
                }
            else:
                out[name] = {"icon": meta or None}
        return out or default_map

    return default_map


def _get_allowed_classifications():
    s = Setting.query.filter_by(key="crm_customer_classifications").first()
    raw = s.value if s and s.value else None
    return _normalize_classifications(raw)


def _get_review_ordering_classifications():
    """Get only classifications marked for inclusion in Review Ordering"""
    s = Setting.query.filter_by(key="crm_customer_classifications").first()
    raw = s.value if s and s.value else None
    all_classifications = _normalize_classifications(raw)
    
    # Filter to only include those with include_in_review_ordering=True
    filtered = {}
    if isinstance(raw, str):
        try:
            raw_dict = json.loads(raw)
        except Exception:
            raw_dict = {}
    elif isinstance(raw, dict):
        raw_dict = raw
    else:
        raw_dict = {}
    
    for name, meta in all_classifications.items():
        # Check if this classification should be included
        include = True
        if isinstance(raw_dict.get(name), dict):
            include = raw_dict[name].get("include_in_review_ordering", True)
        
        if include:
            filtered[name] = meta
    
    return filtered if filtered else all_classifications


def _get_allowed_classification_names():
    return list(_get_allowed_classifications().keys())



@crm_dashboard_bp.get("/dashboard")
@login_required
def customer_slot_dashboard():
    slot = request.args.get("slot")
    classification = request.args.getlist("classification")
    district = request.args.getlist("district")
    
    if not classification:
        try:
            default_classifications = Setting.query.filter_by(key="crm_customer_classifications_defaults").first()
            if default_classifications and default_classifications.value:
                val = default_classifications.value
                if isinstance(val, str):
                    classification = json.loads(val)
                else:
                    classification = val
        except Exception:
            pass
    area = request.args.get("area")
    action_only = request.args.get("action_only") == "1"
    has_cart_only = request.args.get("has_cart_only") == "1"
    logged_in_days = request.args.get("logged_in_days")
    search_q = request.args.get("q", "").strip()
    sort_col = request.args.get("sort", "")
    sort_dir = request.args.get("sort_dir", "asc")
    if sort_dir not in ("asc", "desc"):
        sort_dir = "asc"
    python_side_sort = sort_col in ("cycle", "action")
    needs_python_eval = action_only or python_side_sort

    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (ValueError, TypeError):
        page = 1
    try:
        page_size = min(max(int(request.args.get("page_size", 100)), 25), 500)
    except (ValueError, TypeError):
        page_size = 100

    today = date.today()

    d6m = today - timedelta(days=183)
    d4w = today - timedelta(days=28)
    d90 = today - timedelta(days=90)

    sales_sq = (
        db.session.query(
            DwInvoiceHeader.customer_code_365.label("cc"),
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d6m, DwInvoiceHeader.total_grand), else_=0)
            ), 0).label("value_6m"),
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d4w, DwInvoiceHeader.total_grand), else_=0)
            ), 0).label("value_4w"),
            func.max(
                case((DwInvoiceHeader.invoice_date_utc0 >= d90, DwInvoiceHeader.invoice_date_utc0), else_=None)
            ).label("last_invoice_date"),
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d90, 1), else_=0)
            ), 0).label("inv_cnt_90d"),
        )
        .filter(DwInvoiceHeader.invoice_date_utc0 >= d6m)
        .group_by(DwInvoiceHeader.customer_code_365)
        .subquery()
    )

    resolved_district = func.coalesce(
        CrmCustomerProfile.district,
        PostalCodeLookup.district,
    ).label("resolved_district")

    q = (
        db.session.query(
            PSCustomer.customer_code_365,
            PSCustomer.company_name,
            PSCustomer.postal_code,
            PSCustomer.town,
            PSCustomer.category_2_name,

            CrmCustomerProfile.classification,
            resolved_district,
            CrmCustomerProfile.area,
            PSCustomer.delivery_days_status,
            PSCustomer.mobile,
            PSCustomer.sms.label("sms_number"),

            CrmAbandonedCartState.has_abandoned_cart,
            CrmAbandonedCartState.abandoned_cart_amount,

            MagentoCustomerLastLoginCurrent.last_login_at.label("last_login_at"),

            sales_sq.c.value_6m,
            sales_sq.c.value_4w,
            sales_sq.c.last_invoice_date,
            sales_sq.c.inv_cnt_90d,

            CRMCustomerOpenOrders.open_order_amount,
            CRMCustomerOpenOrders.open_order_count,
        )
        .filter(PSCustomer.active.is_(True))
        .filter(PSCustomer.deleted_at.is_(None))
        .outerjoin(CrmCustomerProfile, CrmCustomerProfile.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(PostalCodeLookup, PostalCodeLookup.postcode == PSCustomer.postal_code)
        .outerjoin(CrmAbandonedCartState, CrmAbandonedCartState.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(MagentoCustomerLastLoginCurrent, MagentoCustomerLastLoginCurrent.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(CRMCustomerOpenOrders, CRMCustomerOpenOrders.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(sales_sq, sales_sq.c.cc == PSCustomer.customer_code_365)
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
        include_null = "__NULL__" in classification
        named_classes = [c for c in classification if c != "__NULL__"]
        conditions = []
        if named_classes:
            conditions.append(CrmCustomerProfile.classification.in_(named_classes))
        if include_null:
            conditions.append(CrmCustomerProfile.classification.is_(None))
        if conditions:
            q = q.filter(or_(*conditions))
    if district:
        q = q.filter(or_(
            CrmCustomerProfile.district.in_(district),
            and_(CrmCustomerProfile.district.is_(None),
                 PostalCodeLookup.district.in_(district))
        ))
    if area:
        q = q.filter(CrmCustomerProfile.area == area)
    if has_cart_only:
        q = q.filter(CrmAbandonedCartState.has_abandoned_cart.is_(True))
    if logged_in_days:
        try:
            days = int(logged_in_days)
            q = q.filter(MagentoCustomerLastLoginCurrent.last_login_at.isnot(None))
            cutoff = datetime.combine(date.today() - timedelta(days=days), datetime.min.time()).replace(tzinfo=timezone.utc)
            q = q.filter(MagentoCustomerLastLoginCurrent.last_login_at >= cutoff)
        except (ValueError, TypeError) as e:
            logger.warning("Invalid logged_in_days value: %s", e)
    if slot:
        try:
            dow, week = slot.split("-", 1)
            q = q.join(
                CustomerDeliverySlot,
                and_(
                    CustomerDeliverySlot.customer_code_365 == PSCustomer.customer_code_365,
                    CustomerDeliverySlot.dow == dow,
                    CustomerDeliverySlot.week_code == week,
                )
            )
        except ValueError:
            pass

    total_count = (
        q.order_by(None)
         .with_entities(func.count(func.distinct(PSCustomer.customer_code_365)))
         .scalar()
    ) or 0
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    filtered_codes_sq = (
        q.order_by(None)
         .with_entities(PSCustomer.customer_code_365.label("customer_code_365"))
         .distinct()
         .subquery()
    )

    kpi_row = (
        db.session.query(
            func.count(
                case((CrmAbandonedCartState.has_abandoned_cart.is_(True), 1), else_=None)
            ).label("cart_count"),
            func.coalesce(func.sum(CRMCustomerOpenOrders.open_order_amount), 0).label("total_open_amount"),
        )
        .select_from(filtered_codes_sq)
        .outerjoin(
            CrmAbandonedCartState,
            CrmAbandonedCartState.customer_code_365 == filtered_codes_sq.c.customer_code_365
        )
        .outerjoin(
            CRMCustomerOpenOrders,
            CRMCustomerOpenOrders.customer_code_365 == filtered_codes_sq.c.customer_code_365
        )
        .one()
    )

    sort_col_map = {
        "customer": PSCustomer.company_name,
        "classification": CrmCustomerProfile.classification,
        "cart": CrmAbandonedCartState.abandoned_cart_amount,
        "login": MagentoCustomerLastLoginCurrent.last_login_at,
        "value6m": sales_sq.c.value_6m,
        "value4w": sales_sq.c.value_4w,
        "invoice": sales_sq.c.last_invoice_date,
        "inv90d": sales_sq.c.inv_cnt_90d,
        "orders": CRMCustomerOpenOrders.open_order_amount,
    }

    if needs_python_eval:
        rows = q.order_by(PSCustomer.company_name).all()
    elif sort_col in sort_col_map:
        sql_col = sort_col_map[sort_col]
        if sort_dir == "desc":
            q = q.order_by(sql_col.desc().nulls_last(), PSCustomer.company_name.asc())
        else:
            q = q.order_by(sql_col.asc().nulls_last(), PSCustomer.company_name.asc())
        rows = (q.limit(page_size)
                  .offset((page - 1) * page_size)
                  .all())
    else:
        q = q.order_by(PSCustomer.company_name.asc())
        rows = (q.limit(page_size)
                  .offset((page - 1) * page_size)
                  .all())

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ATHENS_TZ)
    allowed_classifications = _get_allowed_classifications()

    window_hours_setting = Setting.query.filter_by(key="crm_order_window_hours").first()
    window_hours = int(window_hours_setting.value) if window_hours_setting and window_hours_setting.value else 48
    anchor_setting = Setting.query.filter_by(key="crm_delivery_anchor_time").first()
    anchor_time = anchor_setting.value if anchor_setting and anchor_setting.value else "00:01"
    close_hours_setting = Setting.query.filter_by(key="crm_order_window_close_hours").first()
    close_hours = int(close_hours_setting.value) if close_hours_setting and close_hours_setting.value else 0
    close_anchor_setting = Setting.query.filter_by(key="crm_delivery_close_anchor_time").first()
    close_anchor_time = close_anchor_setting.value if close_anchor_setting and close_anchor_setting.value else "00:01"

    page_customer_codes = [r.customer_code_365 for r in rows]
    slots_rows = (
        CustomerDeliverySlot.query
        .filter(CustomerDeliverySlot.customer_code_365.in_(page_customer_codes))
        .all()
    ) if page_customer_codes else []

    customer_slots = {}
    for s in slots_rows:
        customer_slots.setdefault(s.customer_code_365, []).append(
            {"dow": s.dow, "week_code": s.week_code}
        )

    all_districts = [x[0] for x in (
        db.session.query(func.coalesce(CrmCustomerProfile.district, PostalCodeLookup.district))
        .select_from(PSCustomer)
        .outerjoin(CrmCustomerProfile, CrmCustomerProfile.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(PostalCodeLookup, PostalCodeLookup.postcode == PSCustomer.postal_code)
        .filter(PSCustomer.active.is_(True), PSCustomer.deleted_at.is_(None))
        .distinct()
        .order_by(func.coalesce(CrmCustomerProfile.district, PostalCodeLookup.district))
        .all()
    ) if x[0]]

    all_areas = [x[0] for x in (
        db.session.query(CrmCustomerProfile.area)
        .filter(CrmCustomerProfile.area.isnot(None))
        .distinct().order_by(CrmCustomerProfile.area)
        .all()
    )]

    all_slots = sorted({
        f"{s.dow}-{s.week_code}"
        for s in db.session.query(CustomerDeliverySlot.dow, CustomerDeliverySlot.week_code).distinct().all()
    })

    from services.crm_price_offers import load_offer_summary_map, compute_offer_indicator, compute_offer_kpi_from_summaries
    offer_summary_map = load_offer_summary_map(page_customer_codes)

    all_filtered_codes = list(filtered_codes_sq.column_descriptions[0]["expr"]) if False else None
    try:
        all_fc_rows = db.session.execute(
            db.session.query(filtered_codes_sq.c.customer_code_365).statement
        ).fetchall()
        all_filtered_codes = [r[0] for r in all_fc_rows]
    except Exception:
        all_filtered_codes = page_customer_codes
    offer_kpi_map = load_offer_summary_map(all_filtered_codes)
    offer_kpi = compute_offer_kpi_from_summaries(offer_kpi_map)

    dashboard_rows = []

    for r in rows:
        has_cart = bool(r.has_abandoned_cart) if r.has_abandoned_cart is not None else False
        cart_amount = r.abandoned_cart_amount
        last_invoice_date = r.last_invoice_date
        open_order_count = int(r.open_order_count) if r.open_order_count else 0

        slots_for_cust = customer_slots.get(r.customer_code_365, [])
        window_status = get_customer_window_status(
            slots=slots_for_cust,
            last_invoice_date=last_invoice_date,
            open_order_count=open_order_count,
            window_hours=window_hours,
            anchor_time_str=anchor_time,
            close_hours=close_hours,
            close_anchor_time_str=close_anchor_time,
            now_local=now_local,
        )

        done_for_cycle = window_status["done_for_window"]
        done_source = window_status["done_source"]
        window_open = window_status["window_open"]

        if window_open:
            if has_cart:
                next_action = "CART_NUDGE"
            elif not done_for_cycle:
                next_action = "ORDER_REMINDER"
            else:
                next_action = "NO_ACTION"
        else:
            next_action = "NO_ACTION"

        last_login_at = r.last_login_at
        r_login_days = None
        if last_login_at:
            r_login_days = (now_utc.date() - last_login_at.date()).days

        r_invoice_days = None
        if last_invoice_date:
            if isinstance(last_invoice_date, datetime):
                invoice_date_only = last_invoice_date.date()
            else:
                invoice_date_only = last_invoice_date
            r_invoice_days = (now_utc.date() - invoice_date_only).days

        dashboard_rows.append({
            "customer_code_365": r.customer_code_365,
            "customer_name": r.company_name or r.customer_code_365,
            "postal_code": r.postal_code or "",
            "town": r.town or "",
            "category_2_name": r.category_2_name or "",
            "classification": r.classification or "",
            "district": r.resolved_district or "",
            "area": r.area or "",
            "delivery_days_status": r.delivery_days_status or "",
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
            "open_order_amount": float(r.open_order_amount) if r.open_order_amount else 0,
            "open_order_count": open_order_count,
            "window_open": window_open,
            "next_delivery": window_status["next_delivery"].strftime('%a %d-%b') if window_status["next_delivery"] else None,
            "next_delivery_date": window_status["next_delivery"] if window_status["next_delivery"] else None,
            "mobile_number": r.mobile or r.sms_number or "",
        })
        os_data = offer_summary_map.get(r.customer_code_365, {})
        dashboard_rows[-1]["has_special_pricing"] = os_data.get("has_special_pricing", False)
        dashboard_rows[-1]["active_offer_skus"] = os_data.get("active_offer_skus", 0)
        dashboard_rows[-1]["avg_discount_percent"] = os_data.get("avg_discount_percent", 0)
        dashboard_rows[-1]["offered_skus_not_bought"] = os_data.get("offered_skus_not_bought", 0)
        dashboard_rows[-1]["margin_risk_skus"] = os_data.get("margin_risk_skus", 0)
        dashboard_rows[-1]["offer_sales_4w"] = os_data.get("offer_sales_4w", 0)
        dashboard_rows[-1]["offer_utilisation_pct"] = os_data.get("offer_utilisation_pct", 0)
        dashboard_rows[-1]["high_discount_unused_skus"] = os_data.get("high_discount_unused_skus", 0)
        dashboard_rows[-1]["offered_skus_bought_4w"] = os_data.get("offered_skus_bought_4w", 0)
        dashboard_rows[-1]["offer_indicator_state"] = compute_offer_indicator(os_data)

    if action_only:
        dashboard_rows = [r for r in dashboard_rows if r["next_action"] != "NO_ACTION"]

    if not sort_col or sort_col == "cycle":
        cycle_order = {"OPEN": 1, "DONE": 2, "CLOSED": 3}
        rev = sort_dir == "desc" if sort_col == "cycle" else False
        for row in dashboard_rows:
            if row["window_open"] and not row["done_for_cycle"]:
                row["_cg"] = "OPEN"
            elif row["done_for_cycle"]:
                row["_cg"] = "DONE"
            else:
                row["_cg"] = "CLOSED"
        dashboard_rows.sort(key=lambda r: (cycle_order.get(r.get("_cg", "CLOSED"), 3), r.get("customer_name", "")), reverse=rev)
    elif sort_col == "action":
        action_order = {"CART_NUDGE": 1, "ORDER_REMINDER": 2, "CALL": 3, "NO_ACTION": 4}
        rev = sort_dir == "desc"
        dashboard_rows.sort(key=lambda r: action_order.get(r.get("next_action", "NO_ACTION"), 4), reverse=rev)

    kpi_action_count = sum(1 for row in dashboard_rows if row["next_action"] != "NO_ACTION")
    kpi_done_count = sum(1 for row in dashboard_rows if row["done_for_cycle"])

    if needs_python_eval:
        total_count = len(dashboard_rows)
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        dashboard_rows = dashboard_rows[(page - 1) * page_size : page * page_size]

    for row in dashboard_rows:
        if row["window_open"] and not row["done_for_cycle"]:
            row["cycle_group"] = "OPEN"
        elif row["done_for_cycle"]:
            row["cycle_group"] = "DONE"
        else:
            row["cycle_group"] = "CLOSED"

    open_orders_status = None
    try:
        from services.ps365_pending_orders_service import get_open_orders_status
        open_orders_status = get_open_orders_status()
    except Exception as e:
        logger.exception("Failed to load open orders status: %s", e)

    return render_template(
        "crm/dashboard.html",
        rows=dashboard_rows,
        total_count=total_count,
        allowed_classifications=allowed_classifications,
        all_districts=all_districts,
        all_areas=all_areas,
        all_slots=all_slots,
        open_orders_status=open_orders_status,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        kpi_action_count=kpi_action_count,
        kpi_done_count=kpi_done_count,
        kpi_cart_count=int(kpi_row.cart_count or 0),
        kpi_total_open_amount=float(kpi_row.total_open_amount or 0),
        offer_kpi=offer_kpi,
        filters={
            "slot": slot or "",
            "classification": classification or [],
            "district": district or [],
            "area": area or "",
            "action_only": action_only,
            "has_cart_only": has_cart_only,
            "logged_in_days": logged_in_days or "",
            "q": search_q,
            "sort": sort_col,
            "sort_dir": sort_dir,
        },
    )


REVIEW_STATE_ORDER = {"follow_up": 0, "waiting": 1, "ordered_cart": 2, "ordered": 3, "done": 4}
OUTCOME_REASONS = [
    "ordered_normally", "ordered_after_follow_up", "cart_closed_to_order",
    "cart_added_to_existing", "valid_skip", "financial_reason",
    "bought_elsewhere", "customer_not_ready", "other",
]


def _compute_review_state(review_rec, has_order, has_cart, assisted, logged_in_during_window):
    if review_rec and review_rec.review_state == "done":
        return "done"
    if has_order and has_cart:
        return "ordered_cart"
    if has_order:
        return "ordered"
    if review_rec and review_rec.manual_follow_up_flag:
        return "follow_up"
    if assisted and not has_order:
        return "follow_up"
    if has_cart and not has_order:
        return "follow_up"
    if logged_in_during_window and not has_order:
        return "follow_up"
    if review_rec and review_rec.expected_this_cycle and not has_order:
        return "follow_up"
    return "waiting"


def _cart_mode(has_order, has_cart):
    if not has_cart:
        return "none"
    return "add_on" if has_order else "pending_order"


@crm_dashboard_bp.get("/review-ordering")
@login_required
def review_ordering():
    today = date.today()
    d4w = today - timedelta(days=28)
    d90 = today - timedelta(days=90)

    sales_sq = (
        db.session.query(
            DwInvoiceHeader.customer_code_365.label("cc"),
            func.coalesce(func.sum(
                case((DwInvoiceHeader.invoice_date_utc0 >= d4w, DwInvoiceHeader.total_grand), else_=0)
            ), 0).label("value_4w"),
            func.max(
                case((DwInvoiceHeader.invoice_date_utc0 >= d90, DwInvoiceHeader.invoice_date_utc0), else_=None)
            ).label("last_invoice_date"),
        )
        .filter(DwInvoiceHeader.invoice_date_utc0 >= d90)
        .group_by(DwInvoiceHeader.customer_code_365)
        .subquery()
    )

    q = (
        db.session.query(
            PSCustomer.customer_code_365,
            PSCustomer.company_name,
            PSCustomer.mobile,
            PSCustomer.sms.label("sms_number"),
            CrmCustomerProfile.classification,
            CrmCustomerProfile.assisted_ordering,
            func.coalesce(CrmCustomerProfile.district, PostalCodeLookup.district).label("district"),
            CrmAbandonedCartState.has_abandoned_cart,
            CrmAbandonedCartState.abandoned_cart_amount,
            MagentoCustomerLastLoginCurrent.last_login_at,
            sales_sq.c.value_4w,
            sales_sq.c.last_invoice_date,
            CRMCustomerOpenOrders.open_order_amount,
            CRMCustomerOpenOrders.open_order_count,
        )
        .filter(PSCustomer.active.is_(True))
        .filter(PSCustomer.deleted_at.is_(None))
        .outerjoin(CrmCustomerProfile, CrmCustomerProfile.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(PostalCodeLookup, PostalCodeLookup.postcode == PSCustomer.postal_code)
        .outerjoin(CrmAbandonedCartState, CrmAbandonedCartState.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(MagentoCustomerLastLoginCurrent, MagentoCustomerLastLoginCurrent.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(sales_sq, sales_sq.c.cc == PSCustomer.customer_code_365)
        .outerjoin(CRMCustomerOpenOrders, CRMCustomerOpenOrders.customer_code_365 == PSCustomer.customer_code_365)
    )

    search_q = request.args.get("q", "").strip()
    if search_q:
        q = q.filter(or_(
            PSCustomer.company_name.ilike(f"%{search_q}%"),
            PSCustomer.customer_code_365.ilike(f"%{search_q}%"),
        ))

    classification = request.args.getlist("classification")
    if classification:
        named = [c for c in classification if c != "__NULL__"]
        include_null = "__NULL__" in classification
        conds = []
        if named:
            conds.append(CrmCustomerProfile.classification.in_(named))
        if include_null:
            conds.append(CrmCustomerProfile.classification.is_(None))
        if conds:
            q = q.filter(or_(*conds))

    district = request.args.getlist("district")
    if district:
        q = q.filter(or_(
            CrmCustomerProfile.district.in_(district),
            and_(
                CrmCustomerProfile.district.is_(None),
                PostalCodeLookup.district.in_(district),
            ),
        ))

    filter_state = request.args.get("state", "")
    filter_assisted = request.args.get("assisted_only") == "1"
    filter_expected = request.args.get("expected_only") == "1"
    filter_ordered = request.args.get("ordered", "")
    filter_has_cart = request.args.get("has_cart_only") == "1"
    logged_in_days = request.args.get("logged_in_days")
    filter_delivery_slot = request.args.get("delivery_slot", "")

    rows = q.order_by(PSCustomer.company_name).all()

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(ATHENS_TZ)
    allowed_classifications = _get_review_ordering_classifications()

    window_hours_setting = Setting.query.filter_by(key="crm_order_window_hours").first()
    window_hours = int(window_hours_setting.value) if window_hours_setting and window_hours_setting.value else 48
    anchor_setting = Setting.query.filter_by(key="crm_delivery_anchor_time").first()
    anchor_time = anchor_setting.value if anchor_setting and anchor_setting.value else "00:01"
    close_hours_setting = Setting.query.filter_by(key="crm_order_window_close_hours").first()
    close_hours = int(close_hours_setting.value) if close_hours_setting and close_hours_setting.value else 0
    close_anchor_setting = Setting.query.filter_by(key="crm_delivery_close_anchor_time").first()
    close_anchor_time = close_anchor_setting.value if close_anchor_setting and close_anchor_setting.value else "00:01"

    all_codes = [r.customer_code_365 for r in rows]
    slots_rows = (
        CustomerDeliverySlot.query
        .filter(CustomerDeliverySlot.customer_code_365.in_(all_codes))
        .all()
    ) if all_codes else []
    customer_slots = {}
    for s in slots_rows:
        customer_slots.setdefault(s.customer_code_365, []).append(
            {"dow": s.dow, "week_code": s.week_code}
        )

    review_recs = {}
    if all_codes:
        for rv in CrmOrderingReview.query.filter(
            CrmOrderingReview.customer_code_365.in_(all_codes),
            CrmOrderingReview.delivery_date >= today - timedelta(days=14),
        ).all():
            review_recs.setdefault(rv.customer_code_365, {})[rv.delivery_date] = rv

    all_districts_q = [x[0] for x in (
        db.session.query(func.coalesce(CrmCustomerProfile.district, PostalCodeLookup.district))
        .select_from(PSCustomer)
        .outerjoin(CrmCustomerProfile, CrmCustomerProfile.customer_code_365 == PSCustomer.customer_code_365)
        .outerjoin(PostalCodeLookup, PostalCodeLookup.postcode == PSCustomer.postal_code)
        .filter(PSCustomer.active.is_(True), PSCustomer.deleted_at.is_(None))
        .distinct().order_by(func.coalesce(CrmCustomerProfile.district, PostalCodeLookup.district))
        .all()
    ) if x[0]]

    comm_map = {}
    if all_codes:
        comm_logs = (
            CRMCommunicationLog.query
            .filter(
                CRMCommunicationLog.customer_code_365.in_(all_codes),
                CRMCommunicationLog.direction == "outbound",
            )
            .order_by(CRMCommunicationLog.created_at.desc())
            .all()
        )
        for cl in comm_logs:
            if cl.customer_code_365 not in comm_map:
                comm_map[cl.customer_code_365] = {
                    "channel": cl.channel,
                    "status": cl.status,
                    "dlr_status": cl.dlr_status,
                    "created_at": cl.created_at.isoformat() if cl.created_at else None,
                    "template_title": cl.template_title,
                    "created_by": cl.created_by_username,
                    "days_ago": (now_utc - cl.created_at.replace(tzinfo=timezone.utc)).days if cl.created_at else None,
                    "message_text": cl.message_text or "",
                }

    from services.crm_price_offers import load_offer_summary_map, compute_offer_indicator, compute_offer_kpi_from_summaries
    ro_offer_map = load_offer_summary_map(all_codes)

    open_window_rows = []
    all_delivery_slots_map = {}
    allowed_classification_names = set(allowed_classifications.keys())
    
    for r in rows:
        # Exclude customers whose classification is not marked for review ordering
        customer_classification = r.classification or ""
        if customer_classification and customer_classification not in allowed_classification_names:
            continue
        
        slots_for_cust = customer_slots.get(r.customer_code_365, [])
        if not slots_for_cust:
            continue

        last_invoice_date = r.last_invoice_date
        open_order_count = int(r.open_order_count) if r.open_order_count else 0

        window_status = get_customer_window_status(
            slots=slots_for_cust,
            last_invoice_date=last_invoice_date,
            open_order_count=open_order_count,
            window_hours=window_hours,
            anchor_time_str=anchor_time,
            close_hours=close_hours,
            close_anchor_time_str=close_anchor_time,
            now_local=now_local,
        )

        if not window_status["window_open"]:
            continue

        has_cart = bool(r.has_abandoned_cart) if r.has_abandoned_cart is not None else False
        cart_amount = float(r.abandoned_cart_amount) if r.abandoned_cart_amount is not None else 0
        has_order = open_order_count > 0
        invoice_in_window = False
        if last_invoice_date and window_status.get("window_open_at"):
            inv_d = last_invoice_date.date() if isinstance(last_invoice_date, datetime) else last_invoice_date
            if inv_d >= window_status["window_open_at"].date():
                has_order = True
                invoice_in_window = True
        assisted = bool(r.assisted_ordering) if r.assisted_ordering is not None else False

        last_login_at = r.last_login_at
        r_login_days = None
        logged_in_during_window = False
        if last_login_at:
            r_login_days = (now_utc.date() - last_login_at.date()).days
            # Flag if logged in within last 1-2 days (recent engagement) while window is open
            if r_login_days is not None and r_login_days <= 1 and window_status.get("window_open"):
                logged_in_during_window = True

        r_invoice_days = None
        if last_invoice_date:
            if isinstance(last_invoice_date, datetime):
                invoice_date_only = last_invoice_date.date()
            else:
                invoice_date_only = last_invoice_date
            r_invoice_days = (now_utc.date() - invoice_date_only).days

        next_del = window_status["next_delivery"]
        if next_del:
            nd_iso = next_del.isoformat()
            if nd_iso not in all_delivery_slots_map:
                wc = window_status.get("window_close_at")
                all_delivery_slots_map[nd_iso] = {
                    "delivery_date": nd_iso,
                    "delivery_label": next_del.strftime('%a %d-%b'),
                    "close_at": wc.isoformat() if wc else None,
                }

        cust_reviews = review_recs.get(r.customer_code_365, {})
        review_rec = cust_reviews.get(next_del) if next_del else None

        state = _compute_review_state(review_rec, has_order, has_cart, assisted, logged_in_during_window)
        cm = _cart_mode(has_order, has_cart)

        row = {
            "customer_code_365": r.customer_code_365,
            "customer_name": r.company_name or r.customer_code_365,
            "district": r.district or "",
            "classification": r.classification or "",
            "state": state,
            "has_cart": has_cart,
            "cart_amount": cart_amount,
            "cart_mode": cm,
            "last_login_at": last_login_at.isoformat() if last_login_at else None,
            "r_login_days": r_login_days,
            "last_invoice_date": last_invoice_date.isoformat() if isinstance(last_invoice_date, (date, datetime)) else str(last_invoice_date) if last_invoice_date else None,
            "r_invoice_days": r_invoice_days,
            "value_4w": float(r.value_4w or 0),
            "open_order_amount": float(r.open_order_amount) if r.open_order_amount else 0,
            "open_order_count": open_order_count,
            "invoice_in_window": invoice_in_window,
            "next_delivery": next_del.strftime('%a %d-%b') if next_del else None,
            "next_delivery_date": next_del.isoformat() if next_del else None,
            "window_close_at": window_status.get("window_close_at").isoformat() if window_status.get("window_close_at") else None,
            "mobile_number": r.mobile or r.sms_number or "",
            "assisted_ordering": assisted,
            "expected_this_cycle": review_rec.expected_this_cycle if review_rec else False,
            "review_note": review_rec.review_note if review_rec else "",
            "outcome_reason": review_rec.outcome_reason if review_rec else "",
            "last_comm": comm_map.get(r.customer_code_365),
        }
        ros = ro_offer_map.get(r.customer_code_365, {})
        row["has_special_pricing"] = ros.get("has_special_pricing", False)
        row["active_offer_skus"] = ros.get("active_offer_skus", 0)
        row["avg_discount_percent"] = ros.get("avg_discount_percent", 0)
        row["margin_risk_skus"] = ros.get("margin_risk_skus", 0)
        row["offer_sales_4w"] = ros.get("offer_sales_4w", 0)
        row["offer_utilisation_pct"] = ros.get("offer_utilisation_pct", 0)
        row["high_discount_unused_skus"] = ros.get("high_discount_unused_skus", 0)
        row["offered_skus_not_bought"] = ros.get("offered_skus_not_bought", 0)
        row["offered_skus_bought_4w"] = ros.get("offered_skus_bought_4w", 0)
        row["offer_indicator_state"] = compute_offer_indicator(ros)

        if filter_state and row["state"] != filter_state:
            continue
        if filter_assisted and not assisted:
            continue
        if filter_expected and not row["expected_this_cycle"]:
            continue
        if filter_ordered == "ordered" and not has_order:
            continue
        if filter_ordered == "not_ordered" and has_order:
            continue
        if filter_has_cart and not has_cart:
            continue
        if filter_delivery_slot and row.get("next_delivery_date") != filter_delivery_slot:
            continue
        if logged_in_days:
            try:
                days = int(logged_in_days)
                if r_login_days is None or r_login_days > days:
                    continue
            except (ValueError, TypeError) as e:
                logger.warning("Invalid logged_in_days value in review_ordering: %s", e)

        open_window_rows.append(row)

    far_future = date(2099, 12, 31)
    open_window_rows.sort(key=lambda r: (
        REVIEW_STATE_ORDER.get(r["state"], 5),
        r["district"] or "",
        r["next_delivery_date"] or far_future,
        -(r["cart_amount"] or 0),
        r["r_login_days"] if r["r_login_days"] is not None else 9999,
        r["r_invoice_days"] if r["r_invoice_days"] is not None else 9999,
    ))

    kpi = {"follow_up": 0, "waiting": 0, "ordered": 0, "ordered_cart": 0, "done": 0, "has_cart": 0}
    for row in open_window_rows:
        kpi[row["state"]] = kpi.get(row["state"], 0) + 1
        if row["has_cart"]:
            kpi["has_cart"] += 1

    ro_offer_kpi_map = {}
    for row in open_window_rows:
        cc = row.get("customer_code_365")
        if cc and cc in ro_offer_map:
            ro_offer_kpi_map[cc] = ro_offer_map[cc]
    offer_kpi = compute_offer_kpi_from_summaries(ro_offer_kpi_map)

    open_windows_map = {}
    for row in open_window_rows:
        dd = row.get("next_delivery_date")
        wc = row.get("window_close_at")
        if dd and wc and dd not in open_windows_map:
            open_windows_map[dd] = {
                "delivery_date": dd,
                "delivery_label": row.get("next_delivery"),
                "close_at": wc,
            }
    open_windows = sorted(open_windows_map.values(), key=lambda w: w["delivery_date"])
    all_delivery_slots = sorted(all_delivery_slots_map.values(), key=lambda w: w["delivery_date"])

    return render_template(
        "crm/review_ordering.html",
        rows=open_window_rows,
        total_open=len(open_window_rows),
        kpi=kpi,
        offer_kpi=offer_kpi,
        open_windows=open_windows,
        all_delivery_slots=all_delivery_slots,
        allowed_classifications=allowed_classifications,
        all_districts=all_districts_q,
        outcome_reasons=OUTCOME_REASONS,
        filters={
            "q": search_q,
            "classification": classification or [],
            "district": district or [],
            "state": filter_state,
            "assisted_only": filter_assisted,
            "expected_only": filter_expected,
            "ordered": filter_ordered,
            "has_cart_only": filter_has_cart,
            "logged_in_days": logged_in_days or "",
            "delivery_slot": filter_delivery_slot,
        },
    )


@crm_dashboard_bp.get("/help")
@login_required
def crm_help():
    """Display help documentation for EP SmartGrowth CRM"""
    return render_template("crm/help.html")


@crm_dashboard_bp.post("/review-ordering/update-state")
@login_required
def review_ordering_update_state():
    customer_code = request.form.get("customer_code_365", "").strip()
    delivery_date_str = request.form.get("delivery_date", "").strip()
    new_state = request.form.get("state", "").strip()
    outcome_reason = request.form.get("outcome_reason", "").strip()

    if not customer_code or not delivery_date_str:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400
    if new_state not in ("follow_up", "done"):
        return jsonify({"ok": False, "error": "Invalid state"}), 400
    if new_state == "done" and (not outcome_reason or outcome_reason not in OUTCOME_REASONS):
        return jsonify({"ok": False, "error": "Valid outcome reason required for done state"}), 400

    try:
        delivery_date = date.fromisoformat(delivery_date_str)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date"}), 400

    review = CrmOrderingReview.query.filter_by(
        customer_code_365=customer_code, delivery_date=delivery_date
    ).first()
    if not review:
        review = CrmOrderingReview(customer_code_365=customer_code, delivery_date=delivery_date)
        db.session.add(review)

    if new_state == "done":
        review.review_state = "done"
        review.manual_follow_up_flag = False
        review.done_at = datetime.now(timezone.utc)
        review.done_by = getattr(current_user, "username", None)
        if outcome_reason and outcome_reason in OUTCOME_REASONS:
            review.outcome_reason = outcome_reason
    elif new_state == "follow_up":
        review.review_state = "follow_up"
        review.manual_follow_up_flag = True
        review.done_at = None
        review.done_by = None
        review.outcome_reason = None

    db.session.commit()
    return jsonify({"ok": True, "state": review.review_state})


@crm_dashboard_bp.post("/review-ordering/update-flags")
@login_required
def review_ordering_update_flags():
    customer_code = request.form.get("customer_code_365", "").strip()
    delivery_date_str = request.form.get("delivery_date", "").strip()

    if not customer_code or not delivery_date_str:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400
    try:
        delivery_date = date.fromisoformat(delivery_date_str)
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date"}), 400

    review = CrmOrderingReview.query.filter_by(
        customer_code_365=customer_code, delivery_date=delivery_date
    ).first()
    if not review:
        review = CrmOrderingReview(customer_code_365=customer_code, delivery_date=delivery_date)
        db.session.add(review)

    if "expected_this_cycle" in request.form:
        review.expected_this_cycle = request.form.get("expected_this_cycle") == "1"
    if "review_note" in request.form:
        review.review_note = request.form.get("review_note", "").strip() or None

    db.session.commit()
    return jsonify({"ok": True})


@crm_dashboard_bp.post("/review-ordering/set-assisted")
@login_required
def review_ordering_set_assisted():
    customer_code = request.form.get("customer_code_365", "").strip()
    assisted = request.form.get("assisted_ordering") == "1"

    if not customer_code:
        return jsonify({"ok": False, "error": "Missing customer code"}), 400

    prof = CrmCustomerProfile.query.get(customer_code)
    if not prof:
        prof = CrmCustomerProfile(customer_code_365=customer_code)
        db.session.add(prof)

    prof.assisted_ordering = assisted
    prof.updated_at = datetime.now(timezone.utc)
    prof.updated_by = getattr(current_user, "username", None)
    db.session.commit()
    return jsonify({"ok": True, "assisted_ordering": assisted})


@crm_dashboard_bp.post("/customer/<customer_code_365>/set-classification")
@login_required
def set_customer_classification(customer_code_365):
    new_value = (request.form.get("classification") or "").strip()
    allowed = _get_allowed_classification_names()
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


@crm_dashboard_bp.post("/open-orders/refresh")
@login_required
def refresh_open_orders():
    from services.ps365_pending_orders_service import (
        sync_pending_order_totals_from_ps365, acquire_sync_lock, release_sync_lock, JOB_NAME
    )
    username = getattr(current_user, "username", "unknown")
    locked = acquire_sync_lock(JOB_NAME, username)
    if not locked:
        return jsonify({"success": False, "message": "Refresh already in progress"}), 409

    try:
        result = sync_pending_order_totals_from_ps365(triggered_by=username)
        status_code = 200 if result.get("success") else 500
        return jsonify(result), status_code
    finally:
        release_sync_lock(JOB_NAME)


@crm_dashboard_bp.post("/abandoned-carts/refresh")
@login_required
def refresh_abandoned_carts():
    from services.crm_abandoned_cart import sync_abandoned_carts_batch
    
    username = getattr(current_user, "username", "unknown")
    result = sync_abandoned_carts_batch(triggered_by=username)
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


@crm_dashboard_bp.get("/open-orders/status")
@login_required
def open_orders_status_api():
    from services.ps365_pending_orders_service import get_open_orders_status
    return jsonify(get_open_orders_status())


@crm_dashboard_bp.get("/open-orders/customer/<customer_code_365>")
@login_required
def open_orders_customer_detail(customer_code_365):
    orders = (
        PSPendingOrderHeader.query
        .filter_by(customer_code_365=customer_code_365)
        .order_by(PSPendingOrderHeader.order_date_utc0.desc())
        .all()
    )
    return jsonify([{
        "shopping_cart_code": o.shopping_cart_code,
        "customer_name": o.customer_name,
        "order_date": o.order_date_utc0.isoformat() if o.order_date_utc0 else None,
        "deliver_by": o.order_date_deliverby_utc0.isoformat() if o.order_date_deliverby_utc0 else None,
        "total_grand": float(o.total_grand) if o.total_grand else 0,
        "status": o.order_status_name,
        "delivery_town": o.delivery_town,
        "comments": o.comments,
    } for o in orders])


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


@crm_dashboard_bp.route("/customer/<customer_code_365>/price-offer-summary")
@login_required
def api_price_offer_summary(customer_code_365):
    from services.crm_price_offers import get_customer_price_offer_summary
    summary = get_customer_price_offer_summary(ps_customer_code=customer_code_365)
    if summary is None:
        return jsonify({"has_special_pricing": False, "total_skus": 0})
    return jsonify(summary)


@crm_dashboard_bp.route("/customer/<customer_code_365>/price-offers")
@login_required
def api_price_offers(customer_code_365):
    from services.crm_price_offers import get_customer_price_offer_rows
    sort_by = request.args.get("sort", "discount_percent")
    sort_dir = request.args.get("dir", "desc")
    rule_filter = request.args.get("rule")
    search = request.args.get("q")
    rows = get_customer_price_offer_rows(
        ps_customer_code=customer_code_365,
        sort_by=sort_by, sort_dir=sort_dir,
        rule_filter=rule_filter, search=search,
    )
    return jsonify(rows)


@crm_dashboard_bp.route("/customer/<customer_code_365>/offer-intelligence")
@login_required
def api_offer_intelligence(customer_code_365):
    from services.crm_price_offers import get_customer_offer_intelligence
    data = get_customer_offer_intelligence(customer_code_365)
    return jsonify(data)


@crm_dashboard_bp.route("/price-offers/refresh", methods=["POST"])
@login_required
def api_price_offers_refresh():
    from services.crm_price_offers import (
        refresh_all_customer_price_offers,
        acquire_price_offers_lock,
        release_price_offers_lock,
    )
    triggered_by = current_user.username if current_user and hasattr(current_user, "username") else "manual"

    locked = acquire_price_offers_lock(triggered_by)
    if not locked:
        return jsonify({"success": False, "error": "Refresh already in progress"}), 409

    try:
        payload = request.get_json(silent=True) or {}
        csv_path = payload.get("csv_path")
        result = refresh_all_customer_price_offers(csv_path=csv_path, triggered_by=triggered_by)
        return jsonify(result)
    finally:
        release_price_offers_lock()
