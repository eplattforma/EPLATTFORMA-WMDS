import os
from datetime import date, datetime, timedelta
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import String
from app import db

peer_bp = Blueprint("peer", __name__, url_prefix="/analytics/peer")

SALES_SRC = os.getenv("SALES_LINES_SOURCE", "dw_sales_lines_mv")
ITEMS_TBL = "ps_items_dw"

ITEM_NAME_COL = "item_name"
ITEM_CATEGORY_COL = "category_1_name"   # Updated based on project context
ITEM_BRAND_COL = "brand_code_365"       # Updated based on project context

def _role_ok():
    r = getattr(current_user, "role", None)
    return r in ("admin", "warehouse_manager")

def _parse_date(v):
    if not v: return None
    return datetime.strptime(v, "%Y-%m-%d").date()

def _resolve_range(args):
    preset = (args.get("preset") or "last90").lower().strip()
    today = date.today()
    if preset == "last90":
        return today - timedelta(days=89), today
    if preset == "last30":
        return today - timedelta(days=29), today
    d_from = args.get("from")
    d_to = args.get("to")
    if d_from and d_to:
        return _parse_date(d_from), _parse_date(d_to)
    return today - timedelta(days=89), today

def _compare_range(d_from, d_to, mode):
    mode = (mode or "").lower().strip()
    if mode not in ("prev", "py"):
        return None
    if mode == "py":
        try:
            return d_from.replace(year=d_from.year-1), d_to.replace(year=d_to.year-1)
        except ValueError:
            return d_from.replace(year=d_from.year-1, month=2, day=28), d_to.replace(year=d_to.year-1, month=2, day=28)
    length = (d_to - d_from).days + 1
    prev_end = d_from - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end

def _bind_array(name, vals):
    return bindparam(name, value=vals, type_=ARRAY(String()))

def _resolve_peer_customers(customer_code, peer_group):
    peer_group = (peer_group or "auto").strip().lower()

    if peer_group.startswith("group:"):
        gid = peer_group.split(":", 1)[1].strip()
        sql = text("""
          SELECT customer_code_365
          FROM customer_reporting_group_members
          WHERE group_id = :gid
        """)
        rows = db.session.execute(sql, {"gid": gid}).fetchall()
        peers = [r[0] for r in rows if r and r[0] and r[0] != customer_code]
        return peers

    seg = db.session.execute(text("""
      SELECT COALESCE(category_1_name,'') AS seg
      FROM ps_customers
      WHERE customer_code_365 = :c
      LIMIT 1
    """), {"c": customer_code}).mappings().first()

    seg_val = (seg["seg"] if seg else "").strip()
    if not seg_val:
        return []

    rows = db.session.execute(text("""
      SELECT customer_code_365
      FROM ps_customers
      WHERE COALESCE(category_1_name,'') = :seg
        AND customer_code_365 <> :c
        AND deleted_at IS NULL
      LIMIT 400
    """), {"seg": seg_val, "c": customer_code}).fetchall()

    return [r[0] for r in rows if r and r[0]]

@peer_bp.route("/<customer_code>")
@login_required
def peer_analysis_page(customer_code):
    if not _role_ok(): return "Forbidden", 403
    cust_row = db.session.execute(
        text("SELECT company_name FROM ps_customers WHERE customer_code_365 = :c LIMIT 1"),
        {"c": customer_code}
    ).fetchone()
    customer_name = cust_row._mapping["company_name"] if cust_row else customer_code
    return render_template("peer_analytics/peer_dashboard.html", 
                           customer_code=customer_code, 
                           customer_name=customer_name)

@peer_bp.route("/api/missing-items")
@login_required
def api_missing_items():
    if not _role_ok():
        return jsonify({"error": "forbidden"}), 403

    customer = (request.args.get("customer_code") or "").strip()
    peer_group = request.args.get("peer_group") or "auto"
    min_pen = float(request.args.get("min_penetration") or "0.30")
    limit = max(20, min(int(request.args.get("limit") or 200), 500))
    include_credits = (request.args.get("include_credits") or "0") == "1"

    d_from, d_to = _resolve_range(request.args)
    peers = _resolve_peer_customers(customer, peer_group)
    if not customer or not peers:
        return jsonify({"items": [], "meta": {"peer_customers": len(peers), "from": str(d_from), "to": str(d_to)}})

    line_filter = "s.qty <> 0" if include_credits else "s.qty > 0 AND s.net_excl > 0"

    sql = text(f"""
      WITH peer_customers AS (
        SELECT unnest(:peer_customers::text[]) AS customer_code_365
      ),
      peer_active AS (
        SELECT COUNT(DISTINCT s.customer_code_365) AS n
        FROM {SALES_SRC} s
        JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        WHERE s.sale_date BETWEEN :d_from AND :d_to
          AND {line_filter}
      ),
      peer_item AS (
        SELECT
          s.item_code_365,
          COUNT(DISTINCT s.customer_code_365) AS buyers,
          SUM(s.net_excl) AS peer_sales
        FROM {SALES_SRC} s
        JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        WHERE s.sale_date BETWEEN :d_from AND :d_to
          AND {line_filter}
        GROUP BY s.item_code_365
      ),
      cust_bought AS (
        SELECT DISTINCT item_code_365
        FROM {SALES_SRC}
        WHERE customer_code_365 = :customer
          AND sale_date BETWEEN :d_from AND :d_to
          AND { "qty <> 0" if include_credits else "qty > 0 AND net_excl > 0" }
      ),
      cust_last AS (
        SELECT
          item_code_365,
          MAX(sale_date) AS last_bought_date
        FROM {SALES_SRC}
        WHERE customer_code_365 = :customer
          AND sale_date >= (CURRENT_DATE - INTERVAL '3 years')
        GROUP BY item_code_365
      )
      SELECT
        pi.item_code_365 AS item_code,
        COALESCE(i.{ITEM_NAME_COL}, '') AS item_name,
        COALESCE(i.{ITEM_CATEGORY_COL}, '') AS category,
        COALESCE(i.{ITEM_BRAND_COL}, '') AS brand,
        pi.buyers,
        (pi.buyers::numeric / NULLIF(pa.n,0)) AS penetration,
        (pi.peer_sales::numeric / NULLIF(pi.buyers,0)) AS peer_avg_sales,
        (pi.peer_sales::numeric / NULLIF(pa.n,0)) AS peer_avg_sales_per_peer,
        COALESCE(cl.last_bought_date, NULL) AS last_bought,
        ((pi.buyers::numeric / NULLIF(pa.n,0)) * (pi.peer_sales::numeric / NULLIF(pi.buyers,0))) AS score
      FROM peer_item pi
      CROSS JOIN peer_active pa
      LEFT JOIN cust_bought cb ON cb.item_code_365 = pi.item_code_365
      LEFT JOIN cust_last cl ON cl.item_code_365 = pi.item_code_365
      LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = pi.item_code_365
      WHERE cb.item_code_365 IS NULL
        AND pa.n >= 5
        AND (pi.buyers::numeric / NULLIF(pa.n,0)) >= :min_pen
      ORDER BY score DESC NULLS LAST, penetration DESC
      LIMIT :lim
    """)

    sql = sql.bindparams(_bind_array("peer_customers", peers))
    rows = db.session.execute(sql, {
        "peer_customers": peers,
        "customer": customer,
        "d_from": d_from, "d_to": d_to,
        "min_pen": min_pen,
        "lim": limit
    }).mappings().all()

    return jsonify({
        "meta": {
            "customer": customer,
            "peer_group": peer_group,
            "peer_customers": len(peers),
            "from": str(d_from), "to": str(d_to),
            "min_penetration": min_pen
        },
        "items": [dict(r) for r in rows]
    })

@peer_bp.route("/api/category-mix")
@login_required
def api_category_mix():
    return _api_mix(ITEM_CATEGORY_COL, "category")

@peer_bp.route("/api/brand-mix")
@login_required
def api_brand_mix():
    return _api_mix(ITEM_BRAND_COL, "brand")

def _api_mix(col, label):
    if not _role_ok(): return jsonify({"error": "forbidden"}), 403
    customer = (request.args.get("customer_code") or "").strip()
    peer_group = request.args.get("peer_group") or "auto"
    compare = (request.args.get("compare") or "none").lower().strip()
    include_credits = (request.args.get("include_credits") or "0") == "1"
    d_from, d_to = _resolve_range(request.args)
    cmp = _compare_range(d_from, d_to, compare)
    peers = _resolve_peer_customers(customer, peer_group)
    if not customer or not peers: return jsonify({"items": [], "meta": {"peer_customers": len(peers)}})
    line_filter = "s.qty <> 0" if include_credits else "s.qty > 0 AND s.net_excl > 0"

    def run_mix(x_from, x_to):
        sql = text(f"""
          WITH peer_customers AS (SELECT unnest(:peer_customers::text[]) AS customer_code_365),
          cust AS (
            SELECT COALESCE(i.{col}, 'Unclassified') AS k, SUM(s.net_excl) AS sales
            FROM {SALES_SRC} s LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = s.item_code_365
            WHERE s.customer_code_365 = :customer AND s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
            GROUP BY 1
          ),
          peer AS (
            SELECT COALESCE(i.{col}, 'Unclassified') AS k, SUM(s.net_excl) AS sales
            FROM {SALES_SRC} s JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
            LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = s.item_code_365
            WHERE s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
            GROUP BY 1
          ),
          totals AS (SELECT (SELECT SUM(sales) FROM cust) AS cust_total, (SELECT SUM(sales) FROM peer) AS peer_total)
          SELECT COALESCE(c.k, p.k) AS {label}, COALESCE(c.sales, 0) AS cust_sales, COALESCE(p.sales, 0) AS peer_sales,
            CASE WHEN t.cust_total > 0 THEN COALESCE(c.sales,0)/t.cust_total ELSE 0 END AS cust_share,
            CASE WHEN t.peer_total > 0 THEN COALESCE(p.sales,0)/t.peer_total ELSE 0 END AS peer_share,
            (CASE WHEN t.cust_total > 0 THEN COALESCE(c.sales,0)/t.cust_total ELSE 0 END) -
            (CASE WHEN t.peer_total > 0 THEN COALESCE(p.sales,0)/t.peer_total ELSE 0 END) AS share_gap
          FROM cust c FULL OUTER JOIN peer p ON p.k = c.k CROSS JOIN totals t
          ORDER BY ABS(share_gap) DESC
        """)
        return db.session.execute(sql.bindparams(_bind_array("peer_customers", peers)),
                                  {"peer_customers": peers, "customer": customer, "d_from": x_from, "d_to": x_to}).mappings().all()

    cur = run_mix(d_from, d_to)
    base = run_mix(cmp[0], cmp[1]) if cmp else None
    cur_map = {r[label]: dict(r) for r in cur}
    base_map = {r[label]: dict(r) for r in (base or [])}
    out = []
    for k in (set(cur_map.keys()) | set(base_map.keys())):
        c = cur_map.get(k, {label: k, "cust_sales": 0, "peer_sales": 0, "cust_share": 0, "peer_share": 0, "share_gap": 0})
        b = base_map.get(k, {"share_gap": 0})
        c["delta_share_gap"] = c.get("share_gap", 0) - b.get("share_gap", 0)
        out.append(c)
    out.sort(key=lambda r: abs(r.get("share_gap", 0)), reverse=True)
    return jsonify({"meta": {"customer": customer, "peer_group": peer_group, "peer_customers": len(peers), "from": str(d_from), "to": str(d_to)}, "items": out[:80]})
