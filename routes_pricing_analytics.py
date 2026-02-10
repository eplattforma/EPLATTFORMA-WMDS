import math
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db

pricing_bp = Blueprint("pricing", __name__, url_prefix="/pricing")


def _require_role(*roles):
    if not current_user.is_authenticated:
        return False
    return current_user.role in roles


def _parse_date(s):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def _daterange_prev_period(d1, d2):
    days = (d2 - d1).days
    prev_end = d1 - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days)
    return prev_start, prev_end


def _daterange_py(d1, d2):
    try:
        return d1.replace(year=d1.year - 1), d2.replace(year=d2.year - 1)
    except ValueError:
        def clamp(dt):
            if dt.month == 2 and dt.day == 29:
                return dt.replace(year=dt.year - 1, day=28)
            return dt.replace(year=dt.year - 1)
        return clamp(d1), clamp(d2)


def _get_filters():
    customer = request.args.get("customer_code_365", "").strip()
    d_from = _parse_date(request.args.get("from"))
    d_to = _parse_date(request.args.get("to"))
    if not d_from or not d_to:
        today = date.today()
        d_from = d_from or today.replace(day=1)
        d_to = d_to or today
    compare = request.args.get("compare", "none").lower()
    include_credits = request.args.get("include_credits", "0") == "1"

    if compare == "prev":
        b_from, b_to = _daterange_prev_period(d_from, d_to)
    elif compare == "py":
        b_from, b_to = _daterange_py(d_from, d_to)
    else:
        b_from, b_to = None, None

    return customer, d_from, d_to, compare, b_from, b_to, include_credits


def _json_rows(result):
    rows = []
    for r in result:
        d = dict(r._mapping)
        for k, v in d.items():
            if hasattr(v, "as_tuple"):
                d[k] = float(v)
            elif v is not None and isinstance(v, float) and math.isnan(v):
                d[k] = None
        rows.append(d)
    return rows


@pricing_bp.route("/customer/<customer_code_365>")
@login_required
def customer_pricing(customer_code_365):
    if not _require_role("admin", "warehouse_manager"):
        return "Access denied", 403
    cust_row = db.session.execute(
        text("SELECT company_name FROM ps_customers WHERE customer_code_365 = :c LIMIT 1"),
        {"c": customer_code_365}
    ).fetchone()
    customer_name = cust_row._mapping["company_name"] if cust_row else customer_code_365
    return render_template(
        "pricing_analytics/customer_pricing.html",
        customer_code_365=customer_code_365,
        customer_name=customer_name,
    )


@pricing_bp.route("/api/price-index")
@login_required
def api_price_index():
    if not _require_role("admin", "warehouse_manager"):
        return jsonify({"error": "forbidden"}), 403
    customer, d_from, d_to, _, _, _, include_credits = _get_filters()
    top_n = int(request.args.get("top_n", "50"))

    line_where = "sale_date BETWEEN :d_from AND :d_to AND customer_code_365 = :customer"
    if include_credits:
        line_where += " AND qty <> 0"
    else:
        line_where += " AND qty > 0 AND net_excl > 0"

    sql_top = text(f"""
      WITH cust AS (
        SELECT
          item_code_365,
          SUM(qty) AS qty,
          SUM(net_excl) AS revenue,
          CASE WHEN SUM(qty) <> 0 THEN SUM(net_excl)/SUM(qty) ELSE NULL END AS cust_price
        FROM dw_sales_lines_mv
        WHERE {line_where}
        GROUP BY item_code_365
      )
      SELECT *
      FROM cust
      ORDER BY revenue DESC
      LIMIT :top_n
    """)
    top = db.session.execute(sql_top, {"customer": customer, "d_from": d_from, "d_to": d_to, "top_n": top_n}).fetchall()
    top_rows = _json_rows(top)
    item_codes = [r["item_code_365"] for r in top_rows]
    if not item_codes:
        return jsonify({"summary": {}, "items": []})

    sql_market = text("""
      WITH cust_item AS (
        SELECT
          item_code_365,
          customer_code_365,
          CASE WHEN SUM(qty) <> 0 THEN SUM(net_excl)/SUM(qty) ELSE NULL END AS cust_item_price
        FROM dw_sales_lines_mv
        WHERE sale_date BETWEEN :d_from AND :d_to
          AND item_code_365 = ANY(CAST(:item_codes AS text[]))
          AND qty > 0 AND net_excl > 0
        GROUP BY item_code_365, customer_code_365
      )
      SELECT
        item_code_365,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY cust_item_price) AS market_median_price
      FROM cust_item
      WHERE cust_item_price IS NOT NULL
      GROUP BY item_code_365
    """)
    market = db.session.execute(sql_market, {"d_from": d_from, "d_to": d_to, "item_codes": item_codes}).fetchall()
    market_map = {}
    for m in market:
        mp = m._mapping["market_median_price"]
        market_map[m._mapping["item_code_365"]] = float(mp) if mp is not None else None

    total_revenue = 0.0
    total_market_cost = 0.0
    items_out = []

    for r in top_rows:
        code = r["item_code_365"]
        qty = float(r["qty"] or 0)
        revenue = float(r["revenue"] or 0)
        cust_price = float(r["cust_price"]) if r["cust_price"] is not None else None
        market_price = market_map.get(code)

        index_val = None
        delta_per_unit = None
        delta_total = None

        if cust_price is not None and market_price and market_price != 0:
            index_val = cust_price / market_price
            delta_per_unit = cust_price - market_price
            delta_total = delta_per_unit * qty

        total_revenue += revenue
        if market_price is not None:
            total_market_cost += market_price * qty

        items_out.append({
            "item_code_365": code,
            "qty": qty,
            "revenue": revenue,
            "cust_price": cust_price,
            "market_price": market_price,
            "index": index_val,
            "delta_per_unit": delta_per_unit,
            "delta_total": delta_total,
        })

    overall_index = (total_revenue / total_market_cost) if total_market_cost else None
    summary = {
        "customer_code_365": customer,
        "from": str(d_from),
        "to": str(d_to),
        "total_revenue": total_revenue,
        "total_market_cost": total_market_cost,
        "overall_index": overall_index,
        "estimated_overpay": (total_revenue - total_market_cost) if total_market_cost else None,
    }

    return jsonify({"summary": summary, "items": items_out})


@pricing_bp.route("/api/price-dispersion")
@login_required
def api_price_dispersion():
    if not _require_role("admin", "warehouse_manager"):
        return jsonify({"error": "forbidden"}), 403
    customer, d_from, d_to, _, _, _, include_credits = _get_filters()

    line_where = "sale_date BETWEEN :d_from AND :d_to AND customer_code_365 = :customer"
    if include_credits:
        line_where += " AND qty <> 0"
    else:
        line_where += " AND qty > 0 AND net_excl > 0"

    sql = text(f"""
      WITH lines AS (
        SELECT
          item_code_365,
          (net_excl / NULLIF(qty,0))::numeric AS unit_price
        FROM dw_sales_lines_mv
        WHERE {line_where}
          AND qty <> 0
      ),
      agg AS (
        SELECT
          item_code_365,
          COUNT(*) AS line_count,
          MIN(unit_price) AS min_price,
          percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price) AS median_price,
          MAX(unit_price) AS max_price,
          AVG(unit_price) AS avg_price,
          STDDEV_SAMP(unit_price) AS stddev_price
        FROM lines
        WHERE unit_price IS NOT NULL AND unit_price > 0
        GROUP BY item_code_365
      )
      SELECT
        item_code_365,
        line_count,
        min_price,
        median_price,
        max_price,
        avg_price,
        stddev_price,
        CASE WHEN median_price <> 0 THEN (max_price - min_price) / median_price ELSE NULL END AS dispersion_pct,
        CASE WHEN avg_price <> 0 THEN stddev_price / avg_price ELSE NULL END AS cv
      FROM agg
      ORDER BY dispersion_pct DESC NULLS LAST, line_count DESC
      LIMIT 200
    """)

    res = db.session.execute(sql, {"customer": customer, "d_from": d_from, "d_to": d_to}).fetchall()
    return jsonify({"customer_code_365": customer, "from": str(d_from), "to": str(d_to), "items": _json_rows(res)})


@pricing_bp.route("/api/pvm")
@login_required
def api_pvm():
    if not _require_role("admin", "warehouse_manager"):
        return jsonify({"error": "forbidden"}), 403
    customer, d_from, d_to, compare, b_from, b_to, include_credits = _get_filters()
    if compare == "none":
        return jsonify({"error": "compare must be 'prev' or 'py' for PVM"}), 400

    credit_filter_cur = "AND s.qty <> 0" if include_credits else "AND s.qty > 0 AND s.net_excl > 0"
    credit_filter_base = credit_filter_cur

    sql = text(f"""
      WITH cur AS (
        SELECT
          item_code_365,
          SUM(qty) AS q1,
          SUM(net_excl) AS r1,
          CASE WHEN SUM(qty) <> 0 THEN SUM(net_excl)/SUM(qty) ELSE 0 END AS p1
        FROM dw_sales_lines_mv s
        WHERE s.sale_date BETWEEN :d_from AND :d_to
          AND s.customer_code_365 = :customer
          {credit_filter_cur}
        GROUP BY item_code_365
      ),
      base AS (
        SELECT
          item_code_365,
          SUM(qty) AS q0,
          SUM(net_excl) AS r0,
          CASE WHEN SUM(qty) <> 0 THEN SUM(net_excl)/SUM(qty) ELSE 0 END AS p0
        FROM dw_sales_lines_mv s
        WHERE s.sale_date BETWEEN :b_from AND :b_to
          AND s.customer_code_365 = :customer
          {credit_filter_base}
        GROUP BY item_code_365
      ),
      joined AS (
        SELECT
          COALESCE(cur.item_code_365, base.item_code_365) AS item_code_365,
          COALESCE(cur.q1,0) AS q1,
          COALESCE(cur.r1,0) AS r1,
          COALESCE(cur.p1,0) AS p1,
          COALESCE(base.q0,0) AS q0,
          COALESCE(base.r0,0) AS r0,
          COALESCE(base.p0,0) AS p0
        FROM cur
        FULL OUTER JOIN base USING (item_code_365)
      )
      SELECT
        item_code_365,
        q1, r1, p1,
        q0, r0, p0,
        (r1 - r0) AS delta_revenue,
        (p0 * (q1 - q0)) AS volume_effect,
        (q0 * (p1 - p0)) AS price_effect,
        ((p1*q1 - p0*q0) - (p0*(q1-q0)) - (q0*(p1-p0))) AS mix_effect
      FROM joined
      ORDER BY ABS(r1 - r0) DESC
      LIMIT 200
    """)

    res = db.session.execute(sql, {
        "customer": customer,
        "d_from": d_from, "d_to": d_to,
        "b_from": b_from, "b_to": b_to
    }).fetchall()
    rows = _json_rows(res)

    r1 = sum(r["r1"] for r in rows)
    r0 = sum(r["r0"] for r in rows)
    delta = r1 - r0
    price_eff = sum(r["price_effect"] for r in rows)
    vol_eff = sum(r["volume_effect"] for r in rows)
    mix_eff = sum(r["mix_effect"] for r in rows)

    summary = {
        "customer_code_365": customer,
        "from": str(d_from), "to": str(d_to),
        "baseline_from": str(b_from), "baseline_to": str(b_to),
        "compare": compare,
        "revenue_current": r1,
        "revenue_baseline": r0,
        "delta_revenue": delta,
        "price_effect": price_eff,
        "volume_effect": vol_eff,
        "mix_effect": mix_eff,
    }
    return jsonify({"summary": summary, "items": rows})


@pricing_bp.route("/api/price-sensitivity")
@login_required
def api_price_sensitivity():
    if not _require_role("admin", "warehouse_manager"):
        return jsonify({"error": "forbidden"}), 403
    customer = request.args.get("customer_code_365", "").strip()
    months = int(request.args.get("months", "18"))
    step = float(request.args.get("price_step", "0.05") or 0.05)
    if step < 0.05:
        step = 0.05

    sql = text("""
      WITH base0 AS (
        SELECT
          date_trunc('month', sale_date)::date AS m,
          item_code_365,
          SUM(qty) AS q,
          SUM(net_excl) AS r,
          CASE WHEN SUM(qty) <> 0 THEN (SUM(net_excl)/SUM(qty)) ELSE NULL END AS p_raw
        FROM dw_sales_lines_mv
        WHERE customer_code_365 = :customer
          AND sale_date >= (CURRENT_DATE - (:months * INTERVAL '1 month'))
          AND qty > 0 AND net_excl > 0
        GROUP BY 1, 2
      ),
      base AS (
        SELECT
          m,
          item_code_365,
          q, r,
          CASE
            WHEN p_raw IS NULL THEN NULL
            ELSE (ROUND(p_raw / :step) * :step)
          END AS p
        FROM base0
      ),
      seq AS (
        SELECT
          item_code_365,
          m,
          q, r, p,
          LAG(p) OVER (PARTITION BY item_code_365 ORDER BY m) AS p_prev,
          LEAD(q) OVER (PARTITION BY item_code_365 ORDER BY m) AS q_next
        FROM base
      ),
      feats AS (
        SELECT
          item_code_365,
          m,
          q,
          p,
          CASE
            WHEN p_prev IS NULL OR p_prev = 0 THEN NULL
            WHEN ABS(p - p_prev) < :step THEN NULL
            ELSE (p - p_prev) / p_prev
          END AS dP,
          CASE
            WHEN q IS NULL OR q = 0 THEN NULL
            ELSE (q_next - q) / q
          END AS dQnext,
          CASE
            WHEN p_prev IS NOT NULL AND p_prev > 0
             AND ABS(p - p_prev) >= :step
             AND ((p - p_prev)/p_prev) >= 0.08
             AND COALESCE(q_next,0) = 0
            THEN 1 ELSE 0
          END AS dropout_after_rise
        FROM seq
      ),
      agg AS (
        SELECT
          item_code_365,
          COUNT(*) FILTER (WHERE dP IS NOT NULL AND dQnext IS NOT NULL) AS pairs,
          CORR(dP, dQnext) FILTER (WHERE dP IS NOT NULL AND dQnext IS NOT NULL) AS sensitivity_corr,
          SUM(dropout_after_rise) AS dropouts_after_rise
        FROM feats
        GROUP BY item_code_365
      )
      SELECT
        item_code_365,
        pairs,
        sensitivity_corr,
        dropouts_after_rise
      FROM agg
      WHERE pairs >= 6
      ORDER BY sensitivity_corr ASC NULLS LAST
      LIMIT 200
    """)

    res = db.session.execute(sql, {"customer": customer, "months": months, "step": step}).fetchall()
    return jsonify({"customer_code_365": customer, "months": months, "price_step": step, "items": _json_rows(res)})
