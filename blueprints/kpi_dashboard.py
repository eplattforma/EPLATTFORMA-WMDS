import time
import logging
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)

kpi_dashboard_bp = Blueprint("kpi_dashboard", __name__, url_prefix="/dashboard")

ALLOWED_ROLES = {"admin", "warehouse_manager", "crm_admin"}

_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key):
    entry = _cache.get(key)
    if entry:
        val, ts = entry
        if time.time() - ts < _CACHE_TTL:
            return val
    return None


def _cache_set(key, val):
    _cache[key] = (val, time.time())


def _parse_date_range(range_preset, custom_from, custom_to):
    today = date.today()
    if range_preset == "last_12m":
        d_from = today - timedelta(days=365)
        d_to = today
    elif range_preset == "all_time":
        d_from = date(2015, 1, 1)
        d_to = today
    elif range_preset == "custom" and custom_from and custom_to:
        try:
            d_from = datetime.strptime(custom_from, "%Y-%m-%d").date()
            d_to = datetime.strptime(custom_to, "%Y-%m-%d").date()
        except ValueError:
            d_from = date(today.year, 1, 1)
            d_to = today
    else:
        d_from = date(today.year, 1, 1)
        d_to = today
    return d_from, d_to


def _prev_period(d_from: date, d_to: date):
    d_from_prev = d_from - relativedelta(years=1)
    d_to_prev = d_to - relativedelta(years=1)
    return d_from_prev, d_to_prev


def _pct_change(current, previous):
    if previous and previous != 0:
        return round((current - previous) / abs(previous) * 100, 1)
    return None


def _fetch_agents():
    sql = text("""
        SELECT DISTINCT COALESCE(NULLIF(sales_agent, ''), 'Unassigned') AS agent
        FROM pbi_dim_customers
        ORDER BY agent
    """)
    try:
        rows = db.session.execute(sql).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _agent_clause(agent):
    if agent and agent != "ALL":
        return "AND COALESCE(NULLIF(d.sales_agent, ''), 'Unassigned') = :agent"
    return ""


def _fetch_kpis(d_from, d_to, agent):
    ac = _agent_clause(agent)
    params = {"d_from": d_from, "d_to": d_to, "agent": agent or "ALL"}

    # ── Main KPIs from pbi_fact_sales ──────────────────────────────────
    # net_sales = plain SUM(line_total_excl): returns carry negative values so
    # they net out automatically — matches pbi_fact_sales spec.
    sales_sql = text(f"""
        SELECT
            SUM(COALESCE(s.line_total_excl, 0))                                 AS net_sales,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.invoice_no END)                           AS orders,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.customer_code END)                        AS active_customers,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.item_code END)                            AS products_sold,
            SUM(CASE WHEN s.invoice_type = 'SALE RETURN'
                     THEN ABS(COALESCE(s.line_total_excl, 0)) ELSE 0 END)       AS return_value,
            SUM(CASE WHEN s.invoice_type = 'SALE'
                     THEN COALESCE(s.line_total_excl, 0) ELSE 0 END)            AS gross_sales
        FROM pbi_fact_sales s
        LEFT JOIN pbi_dim_customers d ON d.customer_code = s.customer_code
        WHERE s.invoice_date BETWEEN :d_from AND :d_to
          {ac}
    """)
    row = db.session.execute(sales_sql, params).fetchone()
    net_sales = float(row.net_sales or 0)
    orders = int(row.orders or 0)
    active_customers = int(row.active_customers or 0)
    products_sold = int(row.products_sold or 0)
    return_value = float(row.return_value or 0)
    gross_sales = float(row.gross_sales or 0)

    avg_invoice = net_sales / orders if orders else 0
    return_rate = round(return_value / gross_sales * 100, 2) if gross_sales else 0

    # ── Deliveries ──────────────────────────────────────────────────────
    try:
        del_sql = text("""
            SELECT COUNT(DISTINCT invoice_no) AS delivered
            FROM pbi_fact_route_deliveries
            WHERE delivery_status = 'delivered'
              AND delivery_date BETWEEN :d_from AND :d_to
        """)
        del_row = db.session.execute(del_sql, {"d_from": d_from, "d_to": d_to}).fetchone()
        delivered = int(del_row.delivered or 0)
    except Exception:
        delivered = 0

    # ── Gross Margin ────────────────────────────────────────────────────
    try:
        margin_sql = text(f"""
            SELECT
                SUM(l.gross_profit)     AS total_gp,
                SUM(l.line_total_excl)  AS total_rev
            FROM dw_invoice_line l
            JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
            LEFT JOIN pbi_dim_customers d ON d.customer_code = h.customer_code_365
            WHERE l.gross_profit IS NOT NULL
              AND h.invoice_type = 'SALE'
              AND h.invoice_date_utc0 BETWEEN :d_from AND :d_to
              {ac}
        """)
        margin_row = db.session.execute(margin_sql, params).fetchone()
        gp = float(margin_row.total_gp or 0)
        rev = float(margin_row.total_rev or 0)
        gross_margin_pct = round(gp / rev * 100, 1) if rev else 0
    except Exception:
        gross_margin_pct = 0

    # ── New customers ───────────────────────────────────────────────────
    try:
        nc_sql = text("""
            SELECT COUNT(DISTINCT customer_code) AS new_cust
            FROM (
                SELECT customer_code, MIN(invoice_date) AS first_sale
                FROM pbi_fact_sales
                WHERE invoice_type = 'SALE'
                GROUP BY customer_code
            ) fc
            WHERE fc.first_sale BETWEEN :d_from AND :d_to
        """)
        nc_row = db.session.execute(nc_sql, {"d_from": d_from, "d_to": d_to}).fetchone()
        new_customers = int(nc_row.new_cust or 0)
    except Exception:
        new_customers = 0

    # ── Active buyers last 12 weeks (fixed, ignores date filter) ────────
    try:
        w12_from = date.today() - timedelta(weeks=12)
        w12_sql = text("""
            SELECT COUNT(DISTINCT customer_code) AS buyers
            FROM pbi_fact_sales
            WHERE invoice_type = 'SALE'
              AND invoice_date >= :w12_from
        """)
        w12_row = db.session.execute(w12_sql, {"w12_from": w12_from}).fetchone()
        buyers_12w = int(w12_row.buyers or 0)
    except Exception:
        buyers_12w = 0

    return {
        "net_sales": net_sales,
        "orders": orders,
        "active_customers": active_customers,
        "products_sold": products_sold,
        "return_value": return_value,
        "gross_sales": gross_sales,
        "avg_invoice": avg_invoice,
        "return_rate": return_rate,
        "gross_margin_pct": gross_margin_pct,
        "new_customers": new_customers,
        "delivered": delivered,
        "buyers_12w": buyers_12w,
    }


def _fetch_charts(d_from, d_to, agent):
    ac = _agent_clause(agent)
    params = {"d_from": d_from, "d_to": d_to, "agent": agent or "ALL"}

    # ── All core metrics by month (single pass over pbi_fact_sales) ─────
    month_sql = text(f"""
        SELECT
            s.year_month,
            SUM(COALESCE(s.line_total_excl, 0))                                    AS net_sales,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.invoice_no END)                             AS orders,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.customer_code END)                          AS active_customers,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.item_code END)                              AS products_sold,
            SUM(CASE WHEN s.invoice_type = 'SALE RETURN'
                     THEN ABS(COALESCE(s.line_total_excl, 0)) ELSE 0 END)         AS return_value,
            SUM(CASE WHEN s.invoice_type = 'SALE'
                     THEN COALESCE(s.line_total_excl, 0) ELSE 0 END)              AS gross_sales
        FROM pbi_fact_sales s
        LEFT JOIN pbi_dim_customers d ON d.customer_code = s.customer_code
        WHERE s.invoice_date BETWEEN :d_from AND :d_to
          {ac}
        GROUP BY s.year_month
        ORDER BY s.year_month
    """)
    month_rows = db.session.execute(month_sql, params).fetchall()
    months = [r.year_month for r in month_rows]
    monthly_sales = [float(r.net_sales) for r in month_rows]
    monthly_orders = [int(r.orders) for r in month_rows]
    monthly_active_cust = [int(r.active_customers) for r in month_rows]
    monthly_skus = [int(r.products_sold) for r in month_rows]
    monthly_avg = [
        round(float(r.net_sales) / r.orders, 2) if r.orders else 0
        for r in month_rows
    ]
    monthly_return_rate = [
        round(float(r.return_value) / float(r.gross_sales) * 100, 2)
        if r.gross_sales else 0
        for r in month_rows
    ]

    # ── Gross margin % by month (dw_invoice_line, cost-enriched only) ──
    monthly_margin: list = []
    try:
        gm_month_sql = text(f"""
            SELECT
                TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS ym,
                ROUND(SUM(l.gross_profit)::numeric /
                      NULLIF(SUM(l.line_total_excl), 0) * 100, 1) AS margin_pct
            FROM dw_invoice_line l
            JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
            LEFT JOIN pbi_dim_customers d ON d.customer_code = h.customer_code_365
            WHERE l.gross_profit IS NOT NULL
              AND h.invoice_type = 'SALE'
              AND h.invoice_date_utc0 BETWEEN :d_from AND :d_to
              {ac}
            GROUP BY ym
            ORDER BY ym
        """)
        gm_rows = db.session.execute(gm_month_sql, params).fetchall()
        gm_map = {r.ym: float(r.margin_pct) if r.margin_pct is not None else None
                  for r in gm_rows}
        monthly_margin = [gm_map.get(m) for m in months]
    except Exception:
        monthly_margin = [None] * len(months)

    # ── New customers by month ──────────────────────────────────────────
    monthly_new_cust: list = []
    try:
        nc_month_sql = text("""
            SELECT
                TO_CHAR(fc.first_sale, 'YYYY-MM') AS ym,
                COUNT(DISTINCT fc.customer_code)   AS new_cust
            FROM (
                SELECT customer_code, MIN(invoice_date) AS first_sale
                FROM pbi_fact_sales
                WHERE invoice_type = 'SALE'
                GROUP BY customer_code
            ) fc
            WHERE fc.first_sale BETWEEN :d_from AND :d_to
            GROUP BY ym
            ORDER BY ym
        """)
        nc_rows = db.session.execute(nc_month_sql, {"d_from": d_from, "d_to": d_to}).fetchall()
        nc_map = {r.ym: int(r.new_cust) for r in nc_rows}
        monthly_new_cust = [nc_map.get(m, 0) for m in months]
    except Exception:
        monthly_new_cust = [0] * len(months)

    # ── All core metrics by year ────────────────────────────────────────
    year_sql = text(f"""
        SELECT
            s.year::int                                                            AS yr,
            SUM(COALESCE(s.line_total_excl, 0))                                    AS net_sales,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.invoice_no END)                             AS orders,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.customer_code END)                          AS active_customers,
            COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                THEN s.item_code END)                              AS products_sold,
            SUM(CASE WHEN s.invoice_type = 'SALE RETURN'
                     THEN ABS(COALESCE(s.line_total_excl, 0)) ELSE 0 END)         AS return_value,
            SUM(CASE WHEN s.invoice_type = 'SALE'
                     THEN COALESCE(s.line_total_excl, 0) ELSE 0 END)              AS gross_sales
        FROM pbi_fact_sales s
        LEFT JOIN pbi_dim_customers d ON d.customer_code = s.customer_code
        WHERE s.invoice_date BETWEEN :d_from AND :d_to
          {ac}
        GROUP BY s.year
        ORDER BY s.year
    """)
    year_rows = db.session.execute(year_sql, params).fetchall()
    years = [str(r.yr) for r in year_rows]
    yearly_sales = [float(r.net_sales) for r in year_rows]
    yearly_orders = [int(r.orders) for r in year_rows]
    yearly_active_cust = [int(r.active_customers) for r in year_rows]
    yearly_skus = [int(r.products_sold) for r in year_rows]
    yearly_avg = [
        round(float(r.net_sales) / r.orders, 2) if r.orders else 0
        for r in year_rows
    ]
    yearly_return_rate = [
        round(float(r.return_value) / float(r.gross_sales) * 100, 2)
        if r.gross_sales else 0
        for r in year_rows
    ]

    # ── Gross margin % by year ──────────────────────────────────────────
    yearly_margin: list = []
    try:
        gm_year_sql = text(f"""
            SELECT
                EXTRACT(YEAR FROM h.invoice_date_utc0)::int                     AS yr,
                ROUND(SUM(l.gross_profit)::numeric /
                      NULLIF(SUM(l.line_total_excl), 0) * 100, 1)              AS margin_pct
            FROM dw_invoice_line l
            JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
            LEFT JOIN pbi_dim_customers d ON d.customer_code = h.customer_code_365
            WHERE l.gross_profit IS NOT NULL
              AND h.invoice_type = 'SALE'
              AND h.invoice_date_utc0 BETWEEN :d_from AND :d_to
              {ac}
            GROUP BY yr
            ORDER BY yr
        """)
        gm_y_rows = db.session.execute(gm_year_sql, params).fetchall()
        gm_y_map = {str(r.yr): float(r.margin_pct) if r.margin_pct is not None else None
                    for r in gm_y_rows}
        yearly_margin = [gm_y_map.get(y) for y in years]
    except Exception:
        yearly_margin = [None] * len(years)

    # ── New customers by year ───────────────────────────────────────────
    yearly_new_cust: list = []
    try:
        nc_year_sql = text("""
            SELECT
                EXTRACT(YEAR FROM fc.first_sale)::int AS yr,
                COUNT(DISTINCT fc.customer_code)       AS new_cust
            FROM (
                SELECT customer_code, MIN(invoice_date) AS first_sale
                FROM pbi_fact_sales
                WHERE invoice_type = 'SALE'
                GROUP BY customer_code
            ) fc
            WHERE fc.first_sale BETWEEN :d_from AND :d_to
            GROUP BY yr
            ORDER BY yr
        """)
        nc_y_rows = db.session.execute(nc_year_sql, {"d_from": d_from, "d_to": d_to}).fetchall()
        nc_y_map = {str(r.yr): int(r.new_cust) for r in nc_y_rows}
        yearly_new_cust = [nc_y_map.get(y, 0) for y in years]
    except Exception:
        yearly_new_cust = [0] * len(years)

    # ── Net sales by agent ──────────────────────────────────────────────
    agent_sql = text("""
        SELECT COALESCE(NULLIF(d.sales_agent, ''), 'Unassigned') AS agent,
               SUM(COALESCE(s.line_total_excl, 0))               AS net_sales
        FROM pbi_fact_sales s
        LEFT JOIN pbi_dim_customers d ON d.customer_code = s.customer_code
        WHERE s.invoice_date BETWEEN :d_from AND :d_to
        GROUP BY agent
        ORDER BY net_sales DESC
    """)
    agent_rows = db.session.execute(agent_sql, {"d_from": d_from, "d_to": d_to}).fetchall()
    agent_labels = [r.agent for r in agent_rows]
    agent_sales = [float(r.net_sales) for r in agent_rows]

    return {
        # month series
        "months": months,
        "monthly_sales": monthly_sales,
        "monthly_orders": monthly_orders,
        "monthly_active_cust": monthly_active_cust,
        "monthly_skus": monthly_skus,
        "monthly_avg": monthly_avg,
        "monthly_margin": monthly_margin,
        "monthly_return_rate": monthly_return_rate,
        "monthly_new_cust": monthly_new_cust,
        # year series
        "years": years,
        "yearly_sales": yearly_sales,
        "yearly_orders": yearly_orders,
        "yearly_active_cust": yearly_active_cust,
        "yearly_skus": yearly_skus,
        "yearly_avg": yearly_avg,
        "yearly_margin": yearly_margin,
        "yearly_return_rate": yearly_return_rate,
        "yearly_new_cust": yearly_new_cust,
        # agent chart
        "agent_labels": agent_labels,
        "agent_sales": agent_sales,
    }


def _fetch_top_customers(d_from, d_to, agent, d_from_prev, d_to_prev):
    ac = _agent_clause(agent)
    params = {"d_from": d_from, "d_to": d_to, "agent": agent or "ALL"}

    top_sql = text(f"""
        SELECT s.customer_code,
               MAX(d.customer_name) AS customer_name,
               SUM(COALESCE(s.line_total_excl, 0))                      AS net_sales,
               COUNT(DISTINCT CASE WHEN s.invoice_type = 'SALE'
                                   THEN s.invoice_no END)                AS orders
        FROM pbi_fact_sales s
        LEFT JOIN pbi_dim_customers d ON d.customer_code = s.customer_code
        WHERE s.invoice_date BETWEEN :d_from AND :d_to
          {ac}
        GROUP BY s.customer_code
        ORDER BY net_sales DESC
        LIMIT 10
    """)
    top_rows = db.session.execute(top_sql, params).fetchall()
    top_codes = [r.customer_code for r in top_rows]

    # Prev period sales for % change
    prev_sales_map = {}
    if top_codes and d_from_prev:
        prev_sql = text("""
            SELECT customer_code,
                   SUM(CASE WHEN invoice_type = 'SALE'
                            THEN COALESCE(line_total_excl, 0) ELSE 0 END) AS net_sales
            FROM pbi_fact_sales
            WHERE invoice_date BETWEEN :d_from_prev AND :d_to_prev
              AND customer_code = ANY(:codes)
            GROUP BY customer_code
        """)
        prev_rows = db.session.execute(prev_sql, {
            "d_from_prev": d_from_prev,
            "d_to_prev": d_to_prev,
            "codes": top_codes,
        }).fetchall()
        prev_sales_map = {r.customer_code: float(r.net_sales) for r in prev_rows}

    customers = []
    for r in top_rows:
        prev = prev_sales_map.get(r.customer_code)
        ns = float(r.net_sales)
        customers.append({
            "code": r.customer_code,
            "name": r.customer_name or r.customer_code,
            "net_sales": ns,
            "orders": int(r.orders),
            "pct_change": _pct_change(ns, prev) if prev is not None else None,
        })
    return customers


@kpi_dashboard_bp.route("/")
@login_required
def index():
    if getattr(current_user, "role", None) not in ALLOWED_ROLES:
        abort(403)

    range_preset = request.args.get("range", "this_year")
    custom_from = request.args.get("from", "")
    custom_to = request.args.get("to", "")
    agent = request.args.get("agent", "ALL")

    d_from, d_to = _parse_date_range(range_preset, custom_from, custom_to)
    d_from_prev, d_to_prev = _prev_period(d_from, d_to)

    cache_key = (str(d_from), str(d_to), agent)

    cached = _cache_get(cache_key)
    if cached:
        kpis, charts, top_customers, agents = cached
    else:
        try:
            kpis = _fetch_kpis(d_from, d_to, agent)
        except Exception as e:
            logger.error(f"KPI dashboard kpis error: {e}")
            kpis = {}
        try:
            charts = _fetch_charts(d_from, d_to, agent)
        except Exception as e:
            logger.error(f"KPI dashboard charts error: {e}")
            charts = {}
        try:
            top_customers = _fetch_top_customers(d_from, d_to, agent, d_from_prev, d_to_prev)
        except Exception as e:
            logger.error(f"KPI dashboard top customers error: {e}")
            top_customers = []
        try:
            agents = _fetch_agents()
        except Exception as e:
            logger.error(f"KPI dashboard agents error: {e}")
            agents = []
        _cache_set(cache_key, (kpis, charts, top_customers, agents))

    # YoY KPIs (no cache — quick scalar query)
    kpis_prev = {}
    try:
        kpis_prev = _fetch_kpis(d_from_prev, d_to_prev, agent)
    except Exception:
        pass

    net_sales_pct = _pct_change(kpis.get("net_sales", 0), kpis_prev.get("net_sales"))
    orders_pct = _pct_change(kpis.get("orders", 0), kpis_prev.get("orders"))

    return render_template(
        "dashboard/index.html",
        kpis=kpis,
        kpis_prev=kpis_prev,
        net_sales_pct=net_sales_pct,
        orders_pct=orders_pct,
        charts=charts,
        top_customers=top_customers,
        agents=agents,
        selected_agent=agent,
        range_preset=range_preset,
        custom_from=custom_from,
        custom_to=custom_to,
        d_from=d_from,
        d_to=d_to,
    )
