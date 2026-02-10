import os
from datetime import date, datetime, timedelta
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from sqlalchemy import text
from app import db

customer_analytics_bp = Blueprint("customer_analytics", __name__, url_prefix="/analytics/customers")


def _parse_date(v):
    if not v:
        return None
    return datetime.strptime(v, "%Y-%m-%d").date()

def _safe_shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year + years)

def _resolve_range(args):
    preset = (args.get("preset") or "").lower().strip()
    today = date.today()

    if preset == "last30":
        return today - timedelta(days=29), today
    if preset == "mtd":
        return today.replace(day=1), today
    if preset == "qtd":
        q = (today.month - 1) // 3 + 1
        start_month = 1 + (q - 1) * 3
        return date(today.year, start_month, 1), today
    if preset == "ytd":
        return date(today.year, 1, 1), today
    if preset == "last90" or preset == "":
        return today - timedelta(days=89), today

    d_from = _parse_date(args.get("from"))
    d_to = _parse_date(args.get("to"))
    if not d_from or not d_to:
        return today - timedelta(days=89), today
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    return d_from, d_to

def _compare_range(d_from, d_to, mode):
    mode = (mode or "").lower().strip()
    if mode not in ("prev", "py"):
        return None
    if mode == "py":
        return _safe_shift_year(d_from, -1), _safe_shift_year(d_to, -1)
    length = (d_to - d_from).days + 1
    prev_end = d_from - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end

def _role_ok():
    r = getattr(current_user, "role", None)
    return r in ("admin", "warehouse_manager")


@customer_analytics_bp.route("/")
@login_required
def customer_analytics_home():
    if not _role_ok():
        return render_template("403.html"), 403
    return render_template("customer_analytics/customer_360.html")


@customer_analytics_bp.route("/api/search")
@login_required
def api_search_customers():
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": []})

    sql = text("""
        SELECT customer_code_365 AS code,
               COALESCE(company_name, '') AS name
        FROM ps_customers
        WHERE (customer_code_365 ILIKE :likeq
           OR COALESCE(company_name, '') ILIKE :likeq)
          AND deleted_at IS NULL
        ORDER BY customer_code_365
        LIMIT 20
    """)
    rows = db.session.execute(sql, {"likeq": f"%{q}%"}).mappings().all()
    return jsonify({"items": [dict(r) for r in rows]})


@customer_analytics_bp.route("/api/<customer_code>/header")
@login_required
def api_customer_header(customer_code):
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    sql = text("""
        SELECT
            customer_code_365 AS code,
            COALESCE(company_name, '') AS name,
            vat_registration_number AS vat_no,
            tel_1 AS phone,
            mobile,
            address_line_1 AS address1,
            address_line_2 AS address2,
            address_line_3 AS address3,
            postal_code AS postcode,
            town AS city,
            agent_name,
            category_1_name,
            credit_limit_amount
        FROM ps_customers
        WHERE customer_code_365 = :code
        LIMIT 1
    """)
    row = db.session.execute(sql, {"code": customer_code}).mappings().first()
    if not row:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"customer": dict(row)})


@customer_analytics_bp.route("/api/<customer_code>/summary")
@login_required
def api_customer_summary(customer_code):
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    d_from, d_to = _resolve_range(request.args)
    compare = (request.args.get("compare") or "").lower().strip()
    cmp_range = _compare_range(d_from, d_to, compare)

    base_sql = """
        SELECT
            COALESCE(SUM(l.line_total_excl), 0) AS sales_excl,
            COALESCE(SUM(l.quantity), 0) AS qty,
            COUNT(DISTINCT h.invoice_no_365) AS invoices,
            COUNT(DISTINCT l.item_code_365) AS distinct_items
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :code
          AND h.invoice_date_utc0::date BETWEEN :d_from AND :d_to
    """

    cur = db.session.execute(
        text(base_sql),
        {"code": customer_code, "d_from": d_from, "d_to": d_to}
    ).mappings().first()

    cur_sales = float(cur["sales_excl"] or 0)
    cur_invoices = int(cur["invoices"] or 0)
    avg_invoice = (cur_sales / cur_invoices) if cur_invoices else 0.0

    payload = {
        "range": {"from": str(d_from), "to": str(d_to)},
        "current": {
            "sales_excl": cur_sales,
            "qty": float(cur["qty"] or 0),
            "invoices": cur_invoices,
            "distinct_items": int(cur["distinct_items"] or 0),
            "avg_invoice": avg_invoice,
        },
        "compare_mode": compare if cmp_range else None,
        "compare": None,
        "delta": None
    }

    if cmp_range:
        c_from, c_to = cmp_range
        prev = db.session.execute(
            text(base_sql),
            {"code": customer_code, "d_from": c_from, "d_to": c_to}
        ).mappings().first()

        prev_sales = float(prev["sales_excl"] or 0)
        prev_invoices = int(prev["invoices"] or 0)
        prev_avg_invoice = (prev_sales / prev_invoices) if prev_invoices else 0.0

        payload["compare"] = {
            "range": {"from": str(c_from), "to": str(c_to)},
            "sales_excl": prev_sales,
            "qty": float(prev["qty"] or 0),
            "invoices": prev_invoices,
            "distinct_items": int(prev["distinct_items"] or 0),
            "avg_invoice": prev_avg_invoice,
        }

        def _delta(curv, prevv):
            dv = curv - prevv
            pct = (dv / prevv * 100.0) if prevv else None
            return {"value": dv, "pct": pct}

        payload["delta"] = {
            "sales_excl": _delta(cur_sales, prev_sales),
            "invoices": _delta(cur_invoices, prev_invoices),
            "avg_invoice": _delta(avg_invoice, prev_avg_invoice),
        }

    return jsonify(payload)


@customer_analytics_bp.route("/api/<customer_code>/top-items")
@login_required
def api_customer_top_items(customer_code):
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    d_from, d_to = _resolve_range(request.args)
    limit = int(request.args.get("limit") or 20)
    limit = max(5, min(limit, 200))
    sort = (request.args.get("sort") or "sales").lower().strip()

    order_col = "sales_excl" if sort == "sales" else "qty"

    sql = text(f"""
        SELECT
            l.item_code_365 AS item_code,
            COALESCE(i.item_name, '') AS item_name,
            COALESCE(SUM(l.quantity), 0) AS qty,
            COALESCE(SUM(l.line_total_excl), 0) AS sales_excl,
            COUNT(DISTINCT h.invoice_no_365) AS invoices
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        LEFT JOIN ps_items_dw i ON i.item_code_365 = l.item_code_365
        WHERE h.customer_code_365 = :code
          AND h.invoice_date_utc0::date BETWEEN :d_from AND :d_to
        GROUP BY l.item_code_365, COALESCE(i.item_name, '')
        ORDER BY {order_col} DESC
        LIMIT :lim
    """)
    rows = db.session.execute(sql, {
        "code": customer_code, "d_from": d_from, "d_to": d_to, "lim": limit
    }).mappings().all()

    return jsonify({
        "range": {"from": str(d_from), "to": str(d_to)},
        "items": [dict(r) for r in rows]
    })


@customer_analytics_bp.route("/api/<customer_code>/invoices")
@login_required
def api_customer_invoices(customer_code):
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    d_from, d_to = _resolve_range(request.args)
    page = max(1, int(request.args.get("page") or 1))
    page_size = max(10, min(int(request.args.get("page_size") or 25), 100))
    offset = (page - 1) * page_size

    sql = text("""
        SELECT
            h.invoice_no_365 AS invoice_no,
            h.invoice_date_utc0 AS invoice_date,
            COALESCE(SUM(l.line_total_excl), 0) AS sales_excl,
            COALESCE(SUM(l.quantity), 0) AS qty,
            COUNT(DISTINCT l.item_code_365) AS distinct_items
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :code
          AND h.invoice_date_utc0::date BETWEEN :d_from AND :d_to
        GROUP BY h.invoice_no_365, h.invoice_date_utc0
        ORDER BY h.invoice_date_utc0 DESC, h.invoice_no_365 DESC
        LIMIT :lim OFFSET :off
    """)

    rows = db.session.execute(sql, {
        "code": customer_code, "d_from": d_from, "d_to": d_to,
        "lim": page_size, "off": offset
    }).mappings().all()

    return jsonify({
        "range": {"from": str(d_from), "to": str(d_to)},
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows]
    })


@customer_analytics_bp.route("/api/<customer_code>/item-rfm")
@login_required
def api_customer_item_rfm(customer_code):
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    lookback_days = int(request.args.get("lookback_days") or 730)
    stale_days = int(request.args.get("stale_days") or 90)
    min_freq = int(request.args.get("min_freq") or 1)
    stale_only = (request.args.get("stale_only") or "1") == "1"
    brand_filter = (request.args.get("brand") or "").strip()

    today = date.today()
    d_from = today - timedelta(days=max(30, min(lookback_days, 3650)))

    brand_clause = "AND i.brand_code_365 = :brand" if brand_filter else ""

    sql = text(f"""
        WITH agg AS (
            SELECT
                l.item_code_365 AS item_code,
                MAX(h.invoice_date_utc0) AS last_purchase_date,
                COUNT(DISTINCT h.invoice_no_365) AS frequency,
                COALESCE(SUM(l.line_total_excl), 0) AS monetary
            FROM dw_invoice_header h
            JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
            WHERE h.customer_code_365 = :code
              AND h.invoice_date_utc0::date BETWEEN :d_from AND :d_to
            GROUP BY l.item_code_365
        )
        SELECT
            a.item_code,
            COALESCE(i.item_name, '') AS item_name,
            COALESCE(i.brand_code_365, '') AS brand,
            a.last_purchase_date,
            (DATE(:d_to) - a.last_purchase_date) AS recency_days,
            a.frequency,
            a.monetary
        FROM agg a
        LEFT JOIN ps_items_dw i ON i.item_code_365 = a.item_code
        WHERE a.frequency >= :min_freq
          AND (i.active IS NULL OR i.active = true)
          AND (:stale_only_flag = 0 OR (DATE(:d_to) - a.last_purchase_date) >= :stale_days)
          {brand_clause}
        ORDER BY recency_days DESC, monetary DESC
        LIMIT 500
    """)

    params = {
        "code": customer_code,
        "d_from": d_from,
        "d_to": today,
        "stale_days": stale_days,
        "min_freq": min_freq,
        "stale_only_flag": 1 if stale_only else 0
    }
    if brand_filter:
        params["brand"] = brand_filter

    rows = db.session.execute(sql, params).mappings().all()

    brand_codes = sorted(set(r["brand"] for r in rows if r["brand"]))
    brand_name_map = {}
    if brand_codes:
        bn_sql = text("SELECT brand_code_365, brand_name FROM dw_brands WHERE brand_code_365 = ANY(CAST(:codes AS text[]))")
        bn_rows = db.session.execute(bn_sql, {"codes": brand_codes}).mappings().all()
        brand_name_map = {r["brand_code_365"]: r["brand_name"] or "" for r in bn_rows}

    brands = [{"code": c, "name": brand_name_map.get(c, "")} for c in brand_codes]

    return jsonify({
        "lookback_days": lookback_days,
        "stale_days": stale_days,
        "min_freq": min_freq,
        "stale_only": stale_only,
        "brand": brand_filter,
        "brands": brands,
        "items": [dict(r) for r in rows]
    })


@customer_analytics_bp.route("/api/<customer_code>/monthly-trend")
@login_required
def api_customer_monthly_trend(customer_code):
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    months = max(3, min(int(request.args.get("months") or 12), 36))
    today = date.today()
    d_from = date(today.year, today.month, 1) - timedelta(days=months * 31)
    d_from = d_from.replace(day=1)

    sql = text("""
        SELECT
            TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS month,
            COALESCE(SUM(l.line_total_excl), 0) AS sales_excl,
            COALESCE(SUM(l.quantity), 0) AS qty,
            COUNT(DISTINCT h.invoice_no_365) AS invoices
        FROM dw_invoice_header h
        JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.customer_code_365 = :code
          AND h.invoice_date_utc0::date BETWEEN :d_from AND :d_to
        GROUP BY TO_CHAR(h.invoice_date_utc0, 'YYYY-MM')
        ORDER BY month
    """)

    rows = db.session.execute(sql, {
        "code": customer_code, "d_from": d_from, "d_to": today
    }).mappings().all()

    return jsonify({"months": [dict(r) for r in rows]})


@customer_analytics_bp.route("/api/item-names")
@login_required
def api_item_names():
    codes_param = request.args.get("codes", "").strip()
    if not codes_param:
        return jsonify({"names": {}})
    codes = [c.strip() for c in codes_param.split(",") if c.strip()]
    if not codes or len(codes) > 500:
        return jsonify({"names": {}})
    sql = text("SELECT item_code_365, item_name FROM ps_items_dw WHERE item_code_365 = ANY(CAST(:codes AS text[]))")
    rows = db.session.execute(sql, {"codes": codes}).fetchall()
    names = {r._mapping["item_code_365"]: r._mapping["item_name"] or "" for r in rows}
    return jsonify({"names": names})
