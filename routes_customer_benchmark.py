import logging
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)

benchmark_bp = Blueprint("customer_benchmark", __name__,
                         url_prefix="/admin/customer-benchmark")

ALLOWED_ROLES = {"admin", "warehouse_manager"}

RETURN_PREDICATE = "COALESCE(h.invoice_type,'') ILIKE '%RETURN%'"


def _require_role():
    if getattr(current_user, "role", None) not in ALLOWED_ROLES:
        return jsonify({"error": "Access denied"}), 403
    return None


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def period_prev(start: date, end: date):
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return prev_start, prev_end


def period_py(start: date, end: date):
    return start - timedelta(days=365), end - timedelta(days=365)


def get_customer_group(customer_code: str):
    q = text("""
        SELECT reporting_group
        FROM ps_customers
        WHERE customer_code_365 = :c
          AND reporting_group IS NOT NULL AND reporting_group <> ''
        LIMIT 1
    """)
    r = db.session.execute(q, {"c": customer_code}).fetchone()
    return r[0] if r else None


@benchmark_bp.route("/")
@login_required
def page():
    if getattr(current_user, "role", None) not in ALLOWED_ROLES:
        flash("Access denied. Admin or warehouse manager privileges required.", "danger")
        return redirect(url_for("index"))
    return render_template("customer_benchmark.html")


@benchmark_bp.route("/api/meta")
@login_required
def meta():
    denied = _require_role()
    if denied:
        return denied

    groups = db.session.execute(text("""
        SELECT DISTINCT reporting_group
        FROM ps_customers
        WHERE reporting_group IS NOT NULL AND reporting_group <> ''
        ORDER BY reporting_group
    """)).fetchall()

    return jsonify({
        "groups": [g[0] for g in groups],
        "defaults": {"days": 90, "compare_mode": "prev", "penetration_min": 0.30}
    })


@benchmark_bp.route("/api/customer-lookup")
@login_required
def customer_lookup():
    denied = _require_role()
    if denied:
        return denied

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"rows": []})

    sql = text("""
        SELECT customer_code_365, company_name, reporting_group
        FROM ps_customers
        WHERE customer_code_365 ILIKE :q OR company_name ILIKE :q
        ORDER BY company_name
        LIMIT 20
    """)
    rows = db.session.execute(sql, {"q": f"%{q}%"}).mappings().all()
    return jsonify({"rows": [dict(r) for r in rows]})


@benchmark_bp.route("/api/kpis")
@login_required
def kpis():
    denied = _require_role()
    if denied:
        return denied

    customer_code = (request.args.get("customer_code") or "").strip()
    group_name = (request.args.get("group_name") or "").strip()
    start = parse_date(request.args["start"])
    end = parse_date(request.args["end"])
    compare_mode = request.args.get("compare_mode", "prev")

    if not customer_code:
        return jsonify({"error": "customer_code required"}), 400

    if not group_name:
        group_name = get_customer_group(customer_code) or ""

    comp_start = comp_end = None
    if compare_mode == "prev":
        comp_start, comp_end = period_prev(start, end)
    elif compare_mode == "py":
        comp_start, comp_end = period_py(start, end)

    sql = text(f"""
    WITH base AS (
      SELECT
        h.invoice_date_utc0::date AS invoice_date,
        h.customer_code_365,
        h.invoice_no_365,
        l.item_code_365,
        CASE
          WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.quantity,0))
          ELSE COALESCE(l.quantity,0)
        END AS qty_net,
        CASE
          WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.line_total_excl,0))
          ELSE COALESCE(l.line_total_excl,0)
        END AS sales_excl_net,
        CASE
          WHEN {RETURN_PREDICATE} THEN 0
          ELSE GREATEST(COALESCE(l.quantity,0),0)
        END AS qty_gross,
        CASE
          WHEN {RETURN_PREDICATE} THEN 0
          ELSE GREATEST(COALESCE(l.line_total_excl,0),0)
        END AS sales_excl_gross
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
    ),
    cust AS (
      SELECT
        SUM(sales_excl_net) AS net_sales,
        SUM(qty_net) AS net_qty,
        COUNT(DISTINCT invoice_no_365) AS orders,
        COUNT(*) AS lines,
        COUNT(DISTINCT item_code_365) AS uniq_items,
        SUM(sales_excl_gross) AS gross_sales,
        NULLIF(SUM(qty_gross),0) AS gross_qty
      FROM base
      WHERE customer_code_365 = :cust
    ),
    peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    peer_base AS (
      SELECT b.*
      FROM base b
      JOIN peerset p ON p.customer_code_365 = b.customer_code_365
    ),
    peer AS (
      SELECT
        SUM(sales_excl_net) AS net_sales,
        SUM(qty_net) AS net_qty,
        COUNT(DISTINCT invoice_no_365) AS orders,
        COUNT(*) AS lines,
        COUNT(DISTINCT item_code_365) AS uniq_items,
        SUM(sales_excl_gross) AS gross_sales,
        NULLIF(SUM(qty_gross),0) AS gross_qty,
        (SELECT COUNT(*) FROM peerset) AS peer_customers
      FROM peer_base
    )
    SELECT
      (SELECT net_sales FROM cust) AS cust_net_sales,
      (SELECT net_qty FROM cust) AS cust_net_qty,
      (SELECT orders FROM cust) AS cust_orders,
      (SELECT lines FROM cust) AS cust_lines,
      (SELECT uniq_items FROM cust) AS cust_uniq_items,
      (SELECT gross_sales FROM cust) AS cust_gross_sales,
      (SELECT gross_qty FROM cust) AS cust_gross_qty,
      (SELECT net_sales FROM peer) AS peer_net_sales,
      (SELECT net_qty FROM peer) AS peer_net_qty,
      (SELECT orders FROM peer) AS peer_orders,
      (SELECT lines FROM peer) AS peer_lines,
      (SELECT uniq_items FROM peer) AS peer_uniq_items,
      (SELECT gross_sales FROM peer) AS peer_gross_sales,
      (SELECT gross_qty FROM peer) AS peer_gross_qty,
      (SELECT peer_customers FROM peer) AS peer_customers
    """)

    row = db.session.execute(sql, {
        "start": start, "end": end,
        "cust": customer_code, "grp": group_name
    }).mappings().first()

    if not row:
        return jsonify({"error": "no data"}), 404

    def safe_div(a, b):
        return float(a) / float(b) if a is not None and b not in (None, 0) else None

    cust_aov = safe_div(row["cust_net_sales"], row["cust_orders"])
    peer_aov = safe_div(row["peer_net_sales"], row["peer_orders"])
    peer_avg_sales = safe_div(row["peer_net_sales"], row["peer_customers"])
    cust_unit_price_gross = safe_div(row["cust_gross_sales"], row["cust_gross_qty"])
    peer_unit_price_gross = safe_div(row["peer_gross_sales"], row["peer_gross_qty"])

    out = {
        "customer_code": customer_code,
        "group_name": group_name,
        "period": {"start": str(start), "end": str(end), "compare_mode": compare_mode},
        "customer": {
            "net_sales": float(row["cust_net_sales"] or 0),
            "orders": int(row["cust_orders"] or 0),
            "aov": cust_aov,
            "uniq_items": int(row["cust_uniq_items"] or 0),
            "unit_price_gross": cust_unit_price_gross
        },
        "peers": {
            "peer_customers": int(row["peer_customers"] or 0),
            "net_sales_total": float(row["peer_net_sales"] or 0),
            "avg_sales_per_customer": peer_avg_sales,
            "aov": peer_aov,
            "unit_price_gross": peer_unit_price_gross
        },
        "indexes": {
            "sales_index_vs_peer_avg": safe_div(float(row["cust_net_sales"] or 0), peer_avg_sales),
            "price_index_gross": safe_div(cust_unit_price_gross, peer_unit_price_gross)
        }
    }

    if compare_mode in ("prev", "py") and comp_start and comp_end:
        comp_sql = text(f"""
        WITH base AS (
          SELECT
            h.customer_code_365,
            h.invoice_no_365,
            CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.line_total_excl,0)) ELSE COALESCE(l.line_total_excl,0) END AS sales_net
          FROM dw_invoice_header h
          JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
          WHERE h.invoice_date_utc0::date BETWEEN CAST(:cs AS date) AND CAST(:ce AS date)
        )
        SELECT
          SUM(sales_net) AS comp_net_sales,
          COUNT(DISTINCT invoice_no_365) AS comp_orders
        FROM base
        WHERE customer_code_365 = :cust
        """)
        comp_row = db.session.execute(comp_sql, {
            "cs": comp_start, "ce": comp_end, "cust": customer_code
        }).mappings().first()

        if comp_row:
            comp_ns = float(comp_row["comp_net_sales"] or 0)
            comp_ord = int(comp_row["comp_orders"] or 0)
            curr_ns = float(row["cust_net_sales"] or 0)
            curr_ord = int(row["cust_orders"] or 0)
            out["compare_period"] = {"start": str(comp_start), "end": str(comp_end)}
            out["deltas"] = {
                "net_sales_abs": curr_ns - comp_ns,
                "net_sales_pct": (curr_ns - comp_ns) / comp_ns if comp_ns else None,
                "orders_abs": curr_ord - comp_ord,
                "orders_pct": (curr_ord - comp_ord) / comp_ord if comp_ord else None
            }

    return jsonify(out)


@benchmark_bp.route("/api/not-bought")
@login_required
def not_bought():
    denied = _require_role()
    if denied:
        return denied

    customer_code = (request.args.get("customer_code") or "").strip()
    group_name = (request.args.get("group_name") or "").strip()
    start = parse_date(request.args["start"])
    end = parse_date(request.args["end"])
    penetration_min = float(request.args.get("penetration_min", "0.30"))
    limit = int(request.args.get("limit", "200"))

    if not group_name:
        group_name = get_customer_group(customer_code) or ""

    sql = text(f"""
    WITH base AS (
      SELECT
        h.customer_code_365,
        l.item_code_365,
        CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.quantity,0)) ELSE COALESCE(l.quantity,0) END AS qty_net,
        CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.line_total_excl,0)) ELSE COALESCE(l.line_total_excl,0) END AS sales_net
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
    ),
    peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    peer_cnt AS (
      SELECT COUNT(*) AS n FROM peerset
    ),
    peer_item AS (
      SELECT
        b.item_code_365,
        COUNT(DISTINCT b.customer_code_365) FILTER (WHERE b.qty_net > 0) AS buyers,
        SUM(b.sales_net) AS peer_sales
      FROM base b
      JOIN peerset p ON p.customer_code_365 = b.customer_code_365
      GROUP BY b.item_code_365
    ),
    cust_item AS (
      SELECT item_code_365, SUM(qty_net) AS cust_qty
      FROM base
      WHERE customer_code_365 = :cust
      GROUP BY item_code_365
    ),
    cust_last_buy AS (
      SELECT l.item_code_365, MAX(h.invoice_date_utc0::date) AS last_bought
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.customer_code_365 = :cust
        AND NOT ({RETURN_PREDICATE})
      GROUP BY l.item_code_365
    )
    SELECT
      pi.item_code_365,
      i.item_name,
      i.category_code_365 AS category,
      i.brand_code_365 AS brand,
      (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) AS penetration,
      (pi.peer_sales::float / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_avg_sales_per_customer,
      COALESCE(ci.cust_qty,0) AS cust_qty,
      ((pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) * (pi.peer_sales::float / NULLIF((SELECT n FROM peer_cnt),0))) AS opportunity_score,
      clb.last_bought
    FROM peer_item pi
    LEFT JOIN cust_item ci ON ci.item_code_365 = pi.item_code_365
    LEFT JOIN cust_last_buy clb ON clb.item_code_365 = pi.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = pi.item_code_365
    WHERE COALESCE(ci.cust_qty,0) = 0
      AND (pi.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) >= :pen
    ORDER BY opportunity_score DESC NULLS LAST
    LIMIT :lim
    """)
    rows = db.session.execute(sql, {
        "start": start, "end": end,
        "cust": customer_code, "grp": group_name,
        "pen": penetration_min, "lim": limit
    }).mappings().all()
    return jsonify({"rows": [dict(r) for r in rows]})


@benchmark_bp.route("/api/lapsed-items")
@login_required
def lapsed_items():
    denied = _require_role()
    if denied:
        return denied

    cust = (request.args.get("customer_code") or "").strip()
    grp = (request.args.get("group_name") or "").strip()
    start = parse_date(request.args["start"])
    end = parse_date(request.args["end"])
    compare_mode = request.args.get("compare_mode", "prev")
    limit = int(request.args.get("limit", "200"))

    if not grp:
        grp = get_customer_group(cust) or ""

    if compare_mode == "py":
        comp_start, comp_end = period_py(start, end)
    else:
        comp_start, comp_end = period_prev(start, end)

    sql = text(f"""
    WITH peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    peer_cnt AS (SELECT COUNT(*) AS n FROM peerset),
    comp AS (
      SELECT
        l.item_code_365,
        SUM(CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE GREATEST(COALESCE(l.quantity,0),0) END) AS comp_qty_gross,
        SUM(CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE GREATEST(COALESCE(l.line_total_excl,0),0) END) AS comp_sales_gross
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:cs AS date) AND CAST(:ce AS date)
        AND h.customer_code_365 = :cust
      GROUP BY l.item_code_365
      HAVING SUM(CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE GREATEST(COALESCE(l.quantity,0),0) END) > 0
    ),
    curr AS (
      SELECT
        l.item_code_365,
        SUM(CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE GREATEST(COALESCE(l.quantity,0),0) END) AS curr_qty_gross
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
        AND h.customer_code_365 = :cust
      GROUP BY l.item_code_365
    ),
    peer_curr_buyers AS (
      SELECT
        l.item_code_365,
        COUNT(DISTINCT h.customer_code_365) FILTER (
          WHERE NOT ({RETURN_PREDICATE})
        ) AS buyers
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
        AND h.customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY l.item_code_365
    )
    SELECT
      c.item_code_365,
      i.item_name,
      i.category_code_365 AS category,
      i.brand_code_365 AS brand,
      c.comp_qty_gross,
      c.comp_sales_gross,
      (pcb.buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_penetration_curr
    FROM comp c
    LEFT JOIN curr k ON k.item_code_365 = c.item_code_365
    LEFT JOIN peer_curr_buyers pcb ON pcb.item_code_365 = c.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = c.item_code_365
    WHERE COALESCE(k.curr_qty_gross,0) = 0
    ORDER BY c.comp_sales_gross DESC NULLS LAST
    LIMIT :lim
    """)
    rows = db.session.execute(sql, {
        "cust": cust, "grp": grp,
        "start": start, "end": end,
        "cs": comp_start, "ce": comp_end,
        "lim": limit
    }).mappings().all()

    return jsonify({
        "compare_period": {"start": str(comp_start), "end": str(comp_end)},
        "rows": [dict(r) for r in rows]
    })


@benchmark_bp.route("/api/category-mix")
@login_required
def category_mix():
    denied = _require_role()
    if denied:
        return denied

    cust = (request.args.get("customer_code") or "").strip()
    grp = (request.args.get("group_name") or "").strip()
    start = parse_date(request.args["start"])
    end = parse_date(request.args["end"])
    limit = int(request.args.get("limit", "200"))

    if not grp:
        grp = get_customer_group(cust) or ""

    sql = text(f"""
    WITH base AS (
      SELECT
        h.customer_code_365,
        COALESCE(i.category_code_365,'(Uncategorized)') AS category_code,
        CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.line_total_excl,0)) ELSE COALESCE(l.line_total_excl,0) END AS sales_net
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      LEFT JOIN ps_items_dw i ON i.item_code_365 = l.item_code_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
    ),
    peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    cust_cat AS (
      SELECT category_code, SUM(sales_net) AS sales
      FROM base
      WHERE customer_code_365 = :cust
      GROUP BY category_code
    ),
    peer_cat AS (
      SELECT category_code, SUM(sales_net) AS sales
      FROM base
      WHERE customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY category_code
    ),
    totals AS (
      SELECT
        (SELECT SUM(sales) FROM cust_cat) AS cust_total,
        (SELECT SUM(sales) FROM peer_cat) AS peer_total
    )
    SELECT
      COALESCE(c.category_code, p.category_code) AS category_code,
      COALESCE(cat.category_name, c.category_code, p.category_code) AS category,
      COALESCE(c.sales,0) AS cust_sales,
      COALESCE(p.sales,0) AS peer_sales,
      COALESCE(c.sales,0) / NULLIF((SELECT cust_total FROM totals),0) AS cust_share,
      COALESCE(p.sales,0) / NULLIF((SELECT peer_total FROM totals),0) AS peer_share,
      (COALESCE(c.sales,0) / NULLIF((SELECT cust_total FROM totals),0))
        - (COALESCE(p.sales,0) / NULLIF((SELECT peer_total FROM totals),0)) AS share_diff
    FROM cust_cat c
    FULL OUTER JOIN peer_cat p ON p.category_code = c.category_code
    LEFT JOIN dw_item_categories cat ON cat.category_code_365 = COALESCE(c.category_code, p.category_code)
    ORDER BY ABS(
      (COALESCE(c.sales,0) / NULLIF((SELECT cust_total FROM totals),0))
      - (COALESCE(p.sales,0) / NULLIF((SELECT peer_total FROM totals),0))
    ) DESC NULLS LAST
    LIMIT :lim
    """)
    rows = db.session.execute(sql, {
        "cust": cust, "grp": grp,
        "start": start, "end": end,
        "lim": limit
    }).mappings().all()

    return jsonify({"rows": [dict(r) for r in rows]})


@benchmark_bp.route("/api/price-outliers")
@login_required
def price_outliers():
    denied = _require_role()
    if denied:
        return denied

    cust = (request.args.get("customer_code") or "").strip()
    grp = (request.args.get("group_name") or "").strip()
    start = parse_date(request.args["start"])
    end = parse_date(request.args["end"])
    min_qty = float(request.args.get("min_qty", "1"))
    limit = int(request.args.get("limit", "100"))

    if not grp:
        grp = get_customer_group(cust) or ""

    sql = text(f"""
    WITH peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    base_gross AS (
      SELECT
        h.customer_code_365,
        l.item_code_365,
        SUM(CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE GREATEST(COALESCE(l.quantity,0),0) END) AS qty_gross,
        SUM(CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE GREATEST(COALESCE(l.line_total_excl,0),0) END) AS sales_gross
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
      GROUP BY h.customer_code_365, l.item_code_365
    ),
    cust_item AS (
      SELECT item_code_365,
             SUM(qty_gross) AS qty,
             SUM(sales_gross) AS sales
      FROM base_gross
      WHERE customer_code_365 = :cust
      GROUP BY item_code_365
      HAVING SUM(qty_gross) >= :min_qty
    ),
    peer_item AS (
      SELECT item_code_365,
             SUM(qty_gross) AS qty,
             SUM(sales_gross) AS sales
      FROM base_gross
      WHERE customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY item_code_365
      HAVING SUM(qty_gross) >= :min_qty
    )
    SELECT
      c.item_code_365,
      i.item_name,
      i.category_code_365 AS category,
      i.brand_code_365 AS brand,
      (c.sales / NULLIF(c.qty,0)) AS cust_unit_price,
      (p.sales / NULLIF(p.qty,0)) AS peer_unit_price,
      (c.sales / NULLIF(c.qty,0)) / NULLIF((p.sales / NULLIF(p.qty,0)),0) AS price_index,
      c.qty AS cust_qty,
      p.qty AS peer_qty
    FROM cust_item c
    JOIN peer_item p ON p.item_code_365 = c.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = c.item_code_365
    ORDER BY ABS(((c.sales / NULLIF(c.qty,0)) / NULLIF((p.sales / NULLIF(p.qty,0)),0)) - 1) DESC NULLS LAST
    LIMIT :lim
    """)
    rows = db.session.execute(sql, {
        "cust": cust, "grp": grp,
        "start": start, "end": end,
        "min_qty": min_qty, "lim": limit
    }).mappings().all()

    return jsonify({"rows": [dict(r) for r in rows]})


@benchmark_bp.route("/api/trends-monthly")
@login_required
def trends_monthly():
    denied = _require_role()
    if denied:
        return denied

    cust = (request.args.get("customer_code") or "").strip()
    grp = (request.args.get("group_name") or "").strip()
    start = parse_date(request.args["start"])
    end = parse_date(request.args["end"])

    if not cust:
        return jsonify({"error": "customer_code required"}), 400

    if not grp:
        grp = get_customer_group(cust) or ""

    sql = text(f"""
    WITH peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    peer_cnt AS (SELECT COUNT(*)::int AS n FROM peerset),
    base AS (
      SELECT
        date_trunc('month', h.invoice_date_utc0)::date AS month_start,
        h.customer_code_365,
        h.invoice_no_365,
        CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.line_total_excl,0)) ELSE COALESCE(l.line_total_excl,0) END AS sales_net,
        CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE 1 END AS is_purchase_line
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
    ),
    cust_m AS (
      SELECT
        month_start,
        SUM(sales_net) AS cust_net_sales,
        COUNT(DISTINCT invoice_no_365) FILTER (WHERE is_purchase_line=1) AS cust_orders
      FROM base
      WHERE customer_code_365 = :cust
      GROUP BY month_start
    ),
    peer_m AS (
      SELECT
        month_start,
        SUM(sales_net) AS peer_net_sales_total,
        COUNT(DISTINCT invoice_no_365) FILTER (WHERE is_purchase_line=1) AS peer_orders_total
      FROM base
      WHERE customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY month_start
    )
    SELECT
      COALESCE(c.month_start, p.month_start) AS month_start,
      COALESCE(c.cust_net_sales,0) AS cust_net_sales,
      COALESCE(c.cust_orders,0) AS cust_orders,
      COALESCE(p.peer_net_sales_total,0) AS peer_net_sales_total,
      COALESCE(p.peer_orders_total,0) AS peer_orders_total,
      (COALESCE(p.peer_net_sales_total,0) / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_avg_sales_per_customer,
      (COALESCE(p.peer_orders_total,0)::float / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_avg_orders_per_customer,
      (SELECT n FROM peer_cnt) AS peer_customers
    FROM cust_m c
    FULL OUTER JOIN peer_m p ON p.month_start = c.month_start
    ORDER BY month_start
    """)

    rows = db.session.execute(sql, {
        "cust": cust, "grp": grp, "start": start, "end": end
    }).mappings().all()

    return jsonify({"rows": [dict(r) for r in rows]})


@benchmark_bp.route("/api/item-rfm")
@login_required
def item_rfm():
    denied = _require_role()
    if denied:
        return denied

    cust = (request.args.get("customer_code") or "").strip()
    grp = (request.args.get("group_name") or "").strip()
    end = parse_date(request.args["end"])
    lookback_days = int(request.args.get("lookback_days", "365"))
    limit = int(request.args.get("limit", "300"))

    if not cust:
        return jsonify({"error": "customer_code required"}), 400

    if not grp:
        grp = get_customer_group(cust) or ""

    start = end - timedelta(days=lookback_days - 1)

    sql = text(f"""
    WITH peerset AS (
      SELECT customer_code_365
      FROM ps_customers
      WHERE reporting_group = :grp
        AND customer_code_365 <> :cust
    ),
    peer_cnt AS (SELECT COUNT(*)::int AS n FROM peerset),
    cust_lines AS (
      SELECT
        l.item_code_365,
        h.invoice_date_utc0::date AS inv_date,
        CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.quantity,0)) ELSE COALESCE(l.quantity,0) END AS qty_net,
        CASE WHEN {RETURN_PREDICATE} THEN -ABS(COALESCE(l.line_total_excl,0)) ELSE COALESCE(l.line_total_excl,0) END AS sales_net,
        CASE WHEN {RETURN_PREDICATE} THEN 0 ELSE 1 END AS is_purchase
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
        AND h.customer_code_365 = :cust
    ),
    cust_rfm AS (
      SELECT
        item_code_365,
        MAX(inv_date) FILTER (WHERE is_purchase = 1) AS last_purchase_date,
        (CAST(:end AS date) - MAX(inv_date) FILTER (WHERE is_purchase = 1)) AS recency_days,
        COUNT(DISTINCT inv_date) FILTER (WHERE is_purchase = 1) AS frequency,
        SUM(sales_net) AS monetary,
        SUM(qty_net) AS total_qty
      FROM cust_lines
      GROUP BY item_code_365
    ),
    peer_item AS (
      SELECT
        l.item_code_365,
        COUNT(DISTINCT h.customer_code_365) FILTER (WHERE NOT ({RETURN_PREDICATE})) AS peer_buyers
      FROM dw_invoice_header h
      JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
      WHERE h.invoice_date_utc0::date BETWEEN CAST(:start AS date) AND CAST(:end AS date)
        AND h.customer_code_365 IN (SELECT customer_code_365 FROM peerset)
      GROUP BY l.item_code_365
    )
    SELECT
      cr.item_code_365,
      i.item_name,
      i.category_code_365 AS category,
      i.brand_code_365 AS brand,
      cr.last_purchase_date,
      cr.recency_days,
      cr.frequency,
      cr.monetary,
      cr.total_qty,
      (pi.peer_buyers::float / NULLIF((SELECT n FROM peer_cnt),0)) AS peer_penetration
    FROM cust_rfm cr
    LEFT JOIN peer_item pi ON pi.item_code_365 = cr.item_code_365
    LEFT JOIN ps_items_dw i ON i.item_code_365 = cr.item_code_365
    ORDER BY cr.recency_days DESC NULLS LAST, cr.monetary DESC NULLS LAST
    LIMIT :lim
    """)

    rows = db.session.execute(sql, {
        "cust": cust, "grp": grp,
        "start": start, "end": end,
        "lim": limit
    }).mappings().all()

    return jsonify({
        "lookback_days": lookback_days,
        "period": {"start": str(start), "end": str(end)},
        "rows": [dict(r) for r in rows]
    })
