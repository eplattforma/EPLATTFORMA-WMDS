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


def _resolve_preset(preset_str):
    today = date.today()
    p = (preset_str or "").lower().strip()
    if p == "last30":
        return today - timedelta(days=29), today
    if p == "mtd":
        return today.replace(day=1), today
    if p == "qtd":
        q = (today.month - 1) // 3 + 1
        start_month = 1 + (q - 1) * 3
        return date(today.year, start_month, 1), today
    if p == "ytd":
        return date(today.year, 1, 1), today
    if p == "last90":
        return today - timedelta(days=89), today
    return None, None


def _get_filters():
    customer = request.args.get("customer_code_365", "").strip()
    preset = request.args.get("preset", "").strip()
    d_from, d_to = _resolve_preset(preset)
    if d_from is None or d_to is None:
        d_from = _parse_date(request.args.get("from"))
        d_to = _parse_date(request.args.get("to"))
    if not d_from or not d_to:
        today = date.today()
        d_from = d_from or (today - timedelta(days=89))
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
                d[k] = round(float(v), 2)
            elif v is not None and isinstance(v, float) and math.isnan(v):
                d[k] = None
            elif isinstance(v, float):
                d[k] = round(v, 2)
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
    benchmark = (request.args.get("benchmark", "median") or "median").lower().strip()
    if benchmark not in ("median", "max"):
        benchmark = "median"
    brand_filter = (request.args.get("brand", "") or "").strip()

    # Always define a "positive sales" filter (for gross positive sales), independent of include_credits
    line_where_pos = """
      sale_date BETWEEN :d_from AND :d_to
      AND customer_code_365 = :customer
      AND qty > 0 AND net_excl > 0
    """

    # Net sales including credits/returns (for reconciliation vs 360)
    line_where_all = """
      sale_date BETWEEN :d_from AND :d_to
      AND customer_code_365 = :customer
      AND qty <> 0
    """

    sql_totals = text(f"""
      SELECT
        COALESCE(SUM(net_excl), 0) AS gross_positive_sales_all_items,
        COALESCE(SUM(qty), 0) AS gross_positive_qty_all_items,
        COUNT(DISTINCT item_code_365) AS distinct_items_positive
      FROM dw_sales_lines_mv
      WHERE {line_where_pos}
    """)

    tot_row = db.session.execute(sql_totals, {
        "customer": customer, "d_from": d_from, "d_to": d_to
    }).mappings().first()

    gross_positive_sales_all = 0.0
    gross_positive_qty_all = 0.0
    distinct_items_positive = 0
    
    if tot_row:
        gross_positive_sales_all = float(tot_row.get("gross_positive_sales_all_items") or 0)
        gross_positive_qty_all = float(tot_row.get("gross_positive_qty_all_items") or 0)
        distinct_items_positive = int(tot_row.get("distinct_items_positive") or 0)

    sql_net = text(f"""
      SELECT COALESCE(SUM(net_excl), 0) AS net_sales_all_lines
      FROM dw_sales_lines_mv
      WHERE {line_where_all}
    """)

    net_row = db.session.execute(sql_net, {
        "customer": customer, "d_from": d_from, "d_to": d_to
    }).mappings().first()

    net_sales_all_lines = float(net_row.get("net_sales_all_lines") or 0) if net_row else 0.0

    line_where = "sale_date BETWEEN :d_from AND :d_to AND customer_code_365 = :customer"
    if include_credits:
        line_where += " AND qty <> 0"
    else:
        line_where += " AND qty > 0 AND net_excl > 0"

    brand_join = ""
    brand_clause = ""
    if brand_filter:
        brand_join = "JOIN ps_items_dw i ON i.item_code_365 = cust.item_code_365"
        brand_clause = "WHERE i.brand_code_365 = :brand"

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
      SELECT cust.*
      FROM cust
      {brand_join}
      {brand_clause}
      ORDER BY revenue DESC
      LIMIT :top_n
    """)
    params = {"customer": customer, "d_from": d_from, "d_to": d_to, "top_n": top_n}
    if brand_filter:
        params["brand"] = brand_filter
    top = db.session.execute(sql_top, params).fetchall()
    top_rows = _json_rows(top)
    item_codes = [r["item_code_365"] for r in top_rows]
    if not item_codes:
        return jsonify({"summary": {}, "items": [], "brands": []})

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
        percentile_cont(0.5) WITHIN GROUP (ORDER BY cust_item_price) AS market_median_price,
        MAX(cust_item_price) AS market_max_price
      FROM cust_item
      WHERE cust_item_price IS NOT NULL
      GROUP BY item_code_365
    """)
    market = db.session.execute(sql_market, {"d_from": d_from, "d_to": d_to, "item_codes": item_codes}).fetchall()
    market_map = {}
    for m in market:
        code = m._mapping["item_code_365"]
        med = m._mapping["market_median_price"]
        mx = m._mapping["market_max_price"]
        market_map[code] = {
            "median": float(med) if med is not None else None,
            "max": float(mx) if mx is not None else None,
        }

    item_brand_sql = text("""
        SELECT item_code_365, COALESCE(brand_code_365, '') AS brand
        FROM ps_items_dw
        WHERE item_code_365 = ANY(CAST(:codes AS text[]))
    """)
    ib_rows = db.session.execute(item_brand_sql, {"codes": item_codes}).mappings().all()
    item_brand_map = {r["item_code_365"]: r["brand"] for r in ib_rows}

    brand_codes = sorted(set(v for v in item_brand_map.values() if v))
    brand_name_map = {}
    if brand_codes:
        bn_sql = text("SELECT brand_code_365, brand_name FROM dw_brands WHERE brand_code_365 = ANY(CAST(:codes AS text[]))")
        bn_rows = db.session.execute(bn_sql, {"codes": brand_codes}).mappings().all()
        brand_name_map = {r["brand_code_365"]: r["brand_name"] or "" for r in bn_rows}
    brands_list = [{"code": c, "name": brand_name_map.get(c, "")} for c in brand_codes]

    total_revenue = 0.0
    total_market_cost = 0.0
    items_out = []

    for r in top_rows:
        code = r["item_code_365"]
        qty = float(r["qty"] or 0)
        revenue = float(r["revenue"] or 0)
        cust_price = float(r["cust_price"]) if r["cust_price"] is not None else None

        mm = market_map.get(code, {})
        market_median = mm.get("median")
        market_max = mm.get("max")
        ref_price = market_median if benchmark == "median" else market_max

        index_val = None
        delta_per_unit = None
        delta_total = None

        if cust_price is not None and ref_price is not None and ref_price != 0:
            index_val = cust_price / ref_price
            delta_per_unit = cust_price - ref_price
            delta_total = delta_per_unit * qty

        total_revenue += revenue
        if ref_price is not None:
            total_market_cost += ref_price * qty

        items_out.append({
            "item_code_365": code,
            "brand": item_brand_map.get(code, ""),
            "qty": qty,
            "revenue": round(revenue, 2),
            "cust_price": round(cust_price, 2) if cust_price is not None else None,
            "market_median_price": round(market_median, 2) if market_median is not None else None,
            "market_max_price": round(market_max, 2) if market_max is not None else None,
            "benchmark": benchmark,
            "market_ref_price": round(ref_price, 2) if ref_price is not None else None,
            "index": round(index_val, 2) if index_val is not None else None,
            "delta_per_unit": round(delta_per_unit, 2) if delta_per_unit is not None else None,
            "delta_total": round(delta_total, 2) if delta_total is not None else None,
        })

    coverage_pct = (total_revenue / gross_positive_sales_all) if gross_positive_sales_all else None
    overall_index = (total_revenue / total_market_cost) if total_market_cost else None
    summary = {
        "customer_code_365": customer,
        "from": str(d_from),
        "to": str(d_to),
        "benchmark": benchmark,
        "total_revenue": round(total_revenue, 2),
        "total_market_cost": round(total_market_cost, 2),
        "overall_index": round(overall_index, 2) if overall_index is not None else None,
        "estimated_overpay": round(total_revenue - total_market_cost, 2) if total_market_cost else None,
        "top_n": top_n,
        "benchmarked_sales_top_n": total_revenue,
        "gross_positive_sales_all_items": gross_positive_sales_all,
        "net_sales_all_lines": net_sales_all_lines,
        "coverage_pct": coverage_pct,
        "distinct_items_positive": distinct_items_positive,
    }

    return jsonify({"summary": summary, "items": items_out, "brands": brands_list})


@pricing_bp.route("/api/pvm")
@login_required
def api_pvm():
    if not _require_role("admin", "warehouse_manager"):
        return jsonify({"error": "forbidden"}), 403
    customer, d_from, d_to, compare, b_from, b_to, include_credits = _get_filters()
    if compare == "none":
        return jsonify({"error": "compare must be 'prev' or 'py' for PVM"}), 400

    price_abs_thr = float(request.args.get("price_abs_thr", "0.10"))   # €0.10
    price_pct_thr = float(request.args.get("price_pct_thr", "0.005"))  # 0.5%

    totals_sql = text("""
      SELECT
        COALESCE(SUM(CASE WHEN qty <> 0 THEN net_excl ELSE 0 END), 0) AS net_sales,
        COALESCE(SUM(CASE WHEN qty > 0 AND net_excl > 0 THEN net_excl ELSE 0 END), 0) AS gross_positive_sales,
        COALESCE(SUM(CASE WHEN (net_excl < 0 OR qty < 0) THEN net_excl ELSE 0 END), 0) AS credits_sales
      FROM dw_sales_lines_mv
      WHERE sale_date BETWEEN :d_from AND :d_to
        AND customer_code_365 = :customer
    """)

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

        -- PRICE effect (COMMON only) using CURRENT qty => eliminates “interaction mix”
        CASE
          WHEN q0 > 0 AND q1 > 0 THEN
            q1 * (
              CASE
                WHEN ABS(p1 - p0) < :price_abs_thr THEN 0
                WHEN p0 > 0 AND (ABS(p1 - p0) / p0) < :price_pct_thr THEN 0
                ELSE (p1 - p0)
              END
            )
          ELSE 0
        END AS price_effect,

        -- VOLUME effect (COMMON or LOST)
        CASE
          WHEN q0 > 0 AND q1 > 0 THEN (p0 * (q1 - q0))
          WHEN q0 > 0 AND q1 = 0 THEN (-r0)   -- LOST
          ELSE 0
        END AS volume_effect,

        -- “MIX” column becomes “NEW items effect”
        CASE
          WHEN q0 = 0 AND q1 > 0 THEN r1      -- NEW
          ELSE 0
        END AS mix_effect
      FROM joined
      ORDER BY ABS(r1 - r0) DESC
      LIMIT 200
    """)

    res = db.session.execute(sql, {
        "customer": customer,
        "d_from": d_from, "d_to": d_to,
        "b_from": b_from, "b_to": b_to,
        "price_abs_thr": price_abs_thr,
        "price_pct_thr": price_pct_thr
    }).fetchall()
    rows = _json_rows(res)

    new_rev = 0.0
    lost_rev = 0.0
    common_delta = 0.0
    common_price = 0.0
    common_volume = 0.0
    new_count = 0
    lost_count = 0
    common_count = 0

    for r in rows:
        rq1 = float(r.get("q1") or 0)
        rq0 = float(r.get("q0") or 0)
        rr1 = float(r.get("r1") or 0)
        rr0 = float(r.get("r0") or 0)

        is_new = (rq0 == 0 and rq1 > 0)
        is_lost = (rq1 == 0 and rq0 > 0)
        is_common = (rq0 > 0 and rq1 > 0)

        r["is_new_item"] = is_new
        r["is_lost_item"] = is_lost
        r["is_common_item"] = is_common

        if is_new:
            new_rev += rr1
            new_count += 1
        elif is_lost:
            lost_rev += rr0
            lost_count += 1
        elif is_common:
            common_count += 1
            common_delta += (rr1 - rr0)
            common_price += float(r.get("price_effect") or 0)
            common_volume += float(r.get("volume_effect") or 0)

    cur_tot = db.session.execute(totals_sql, {
        "customer": customer, "d_from": d_from, "d_to": d_to
    }).mappings().first()

    base_tot = db.session.execute(totals_sql, {
        "customer": customer, "d_from": b_from, "d_to": b_to
    }).mappings().first()

    net_cur = float(cur_tot["net_sales"] or 0) if cur_tot else 0.0
    net_base = float(base_tot["net_sales"] or 0) if base_tot else 0.0

    gross_cur = float(cur_tot["gross_positive_sales"] or 0) if cur_tot else 0.0
    gross_base = float(base_tot["gross_positive_sales"] or 0) if base_tot else 0.0

    credits_cur = float(cur_tot["credits_sales"] or 0) if cur_tot else 0.0
    credits_base = float(base_tot["credits_sales"] or 0) if base_tot else 0.0

    price_eff = sum(r["price_effect"] for r in rows)
    vol_eff = sum(r["volume_effect"] for r in rows)
    mix_eff = sum(r["mix_effect"] for r in rows)

    summary = {
        "customer_code_365": customer,
        "from": str(d_from), "to": str(d_to),
        "baseline_from": str(b_from), "baseline_to": str(b_to),
        "compare": compare,

        # NET (includes CN/returns)
        "net_revenue_current": net_cur,
        "net_revenue_baseline": net_base,
        "delta_net_revenue": net_cur - net_base,

        # GROSS positive sales (what PVM explains)
        "gross_revenue_current": gross_cur,
        "gross_revenue_baseline": gross_base,
        "delta_gross_revenue": gross_cur - gross_base,

        # CREDITS/RETURNS effect (negative)
        "credits_current": credits_cur,
        "credits_baseline": credits_base,
        "delta_credits": credits_cur - credits_base,

        # PVM components explain delta_gross_revenue
        "price_effect": price_eff,
        "volume_effect": vol_eff,
        "mix_effect": mix_eff,

        # New/Lost/Common breakdown
        "new_items_revenue_current": new_rev,
        "lost_items_revenue_baseline": lost_rev,
        "common_items_delta_revenue": common_delta,
        "common_price_effect": common_price,
        "common_volume_effect": common_volume,
        "new_items_count": new_count,
        "lost_items_count": lost_count,
        "common_items_count": common_count,
    }
    return jsonify({"summary": summary, "items": rows})


@pricing_bp.route("/api/stale-pricing")
@login_required
def api_stale_pricing():
    if not _require_role("admin", "warehouse_manager"):
        return jsonify({"error": "forbidden"}), 403

    customer = (request.args.get("customer_code_365") or "").strip()

    stale_min = int(request.args.get("stale_min", "300"))
    stale_max = int(request.args.get("stale_max", "400"))
    if stale_min < 0:
        stale_min = 0
    if stale_max < stale_min:
        stale_max = stale_min

    market_days = int(request.args.get("market_days", "90"))
    market_days = max(14, min(market_days, 365))

    benchmark = (request.args.get("benchmark", "median") or "median").lower().strip()
    if benchmark not in ("median", "max"):
        benchmark = "median"

    limit = int(request.args.get("limit", "200"))
    limit = max(20, min(limit, 500))

    # Use the max sale date in the entire DB as 'today' for recency if current date has no data
    # This ensures the demo/dev data works even if it's old
    today_sql = text("SELECT MAX(sale_date) FROM dw_sales_lines_mv")
    max_date = db.session.execute(today_sql).scalar() or date.today()

    sql = text("""
      WITH cust_items AS (
        SELECT
          item_code_365,
          MAX(sale_date) AS last_purchase_date
        FROM dw_sales_lines_mv
        WHERE customer_code_365 = :customer
          AND qty > 0 AND net_excl > 0
        GROUP BY item_code_365
      ),
      stale AS (
        SELECT
          ci.item_code_365,
          ci.last_purchase_date,
          (:today - ci.last_purchase_date) AS recency_days,
          date_trunc('month', ci.last_purchase_date)::date AS last_month_start,
          (date_trunc('month', ci.last_purchase_date) + interval '1 month' - interval '1 day')::date AS last_month_end
        FROM cust_items ci
        WHERE (:today - ci.last_purchase_date) BETWEEN :stale_min AND :stale_max
      ),
      last_paid AS (
        SELECT
          s.item_code_365,
          SUM(v.qty) AS last_qty,
          SUM(v.net_excl) AS last_revenue,
          CASE WHEN SUM(v.qty) <> 0 THEN SUM(v.net_excl)/SUM(v.qty) ELSE NULL END AS last_unit_price
        FROM stale s
        JOIN dw_sales_lines_mv v
          ON v.customer_code_365 = :customer
         AND v.item_code_365 = s.item_code_365
         AND v.sale_date = s.last_purchase_date
        WHERE v.qty > 0 AND v.net_excl > 0
        GROUP BY s.item_code_365
      )
      SELECT
        s.item_code_365,
        COALESCE(i.item_name, '') AS item_name,
        s.last_purchase_date,
        s.recency_days,
        lp.last_qty,
        lp.last_revenue,
        lp.last_unit_price,

        m_last.market_median_price AS market_median_at_last,
        m_last.market_max_price    AS market_max_at_last,

        m_cur.market_median_price  AS market_median_current,
        m_cur.market_max_price     AS market_max_current

      FROM stale s
      LEFT JOIN last_paid lp ON lp.item_code_365 = s.item_code_365
      LEFT JOIN ps_items_dw i ON i.item_code_365 = s.item_code_365

      LEFT JOIN LATERAL (
        WITH cust_item AS (
          SELECT
            customer_code_365,
            CASE WHEN SUM(qty) <> 0 THEN SUM(net_excl)/SUM(qty) ELSE NULL END AS cust_item_price
          FROM dw_sales_lines_mv
          WHERE sale_date BETWEEN s.last_month_start AND s.last_month_end
            AND item_code_365 = s.item_code_365
            AND qty > 0 AND net_excl > 0
          GROUP BY customer_code_365
        )
        SELECT
          percentile_cont(0.5) WITHIN GROUP (ORDER BY cust_item_price) AS market_median_price,
          MAX(cust_item_price) AS market_max_price
        FROM cust_item
        WHERE cust_item_price IS NOT NULL
      ) m_last ON true

      LEFT JOIN LATERAL (
        WITH cust_item AS (
          SELECT
            customer_code_365,
            CASE WHEN SUM(qty) <> 0 THEN SUM(net_excl)/SUM(qty) ELSE NULL END AS cust_item_price
          FROM dw_sales_lines_mv
          WHERE sale_date >= (:today - CAST(:market_days AS integer) * INTERVAL '1 day')
            AND item_code_365 = s.item_code_365
            AND qty > 0 AND net_excl > 0
          GROUP BY customer_code_365
        )
        SELECT
          percentile_cont(0.5) WITHIN GROUP (ORDER BY cust_item_price) AS market_median_price,
          MAX(cust_item_price) AS market_max_price
        FROM cust_item
        WHERE cust_item_price IS NOT NULL
      ) m_cur ON true

      ORDER BY s.recency_days DESC, COALESCE(lp.last_revenue,0) DESC
      LIMIT :lim
    """)

    res = db.session.execute(sql, {
        "customer": customer,
        "stale_min": stale_min,
        "stale_max": stale_max,
        "market_days": market_days,
        "lim": limit,
        "today": max_date
    }).fetchall()

    rows = _json_rows(res)

    for r in rows:
        last_price = r.get("last_unit_price")
        if benchmark == "median":
            ref_at_last = r.get("market_median_at_last")
            ref_current = r.get("market_median_current")
        else:
            ref_at_last = r.get("market_max_at_last")
            ref_current = r.get("market_max_current")

        r["benchmark"] = benchmark
        r["ref_at_last"] = round(ref_at_last, 2) if ref_at_last is not None else None
        r["ref_current"] = round(ref_current, 2) if ref_current is not None else None

        r["delta_vs_ref_at_last"] = round(last_price - ref_at_last, 2) if (last_price is not None and ref_at_last is not None) else None
        r["delta_vs_ref_current"] = round(last_price - ref_current, 2) if (last_price is not None and ref_current is not None) else None

        cur_med = r.get("market_median_current")
        r["suggested_winback_price"] = round(min(last_price, cur_med), 2) if (last_price is not None and cur_med is not None) else None

    return jsonify({
        "customer_code_365": customer,
        "stale_min": stale_min,
        "stale_max": stale_max,
        "market_days": market_days,
        "benchmark": benchmark,
        "items": rows
    })
