import os
from datetime import date, datetime, timedelta
from flask import Blueprint, request, jsonify, Response
from flask_login import login_required, current_user
from sqlalchemy import text, bindparam
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import String
from app import db

catmgr_bp = Blueprint("catmgr", __name__, url_prefix="/analytics/category-manager")

SALES_SRC = os.getenv("SALES_LINES_SOURCE", "dw_sales_lines_mv")
ITEMS_TBL = "ps_items_dw"
CAT_TBL = "dw_item_categories"

ITEM_NAME_COL = "item_name"
ITEM_CATEGORY_COL = "category_code_365"
ITEM_BRAND_COL = "brand_code_365"

def _role_ok():
    return getattr(current_user, "role", None) in ("admin", "warehouse_manager")

def _safe_float(value, default):
    s = str(value).strip()
    if s.lower() == "nan":
        return default
    return float(s)

def _parse_date(v):
    if not v: return None
    return datetime.strptime(v, "%Y-%m-%d").date()

def _resolve_range(args):
    preset = (args.get("preset") or "last90").lower().strip()
    today = date.today()

    if preset == "last30":
        return today - timedelta(days=29), today
    if preset == "last180":
        return today - timedelta(days=179), today
    if preset in ("last90", ""):
        return today - timedelta(days=89), today

    d_from = _parse_date(args.get("from"))
    d_to = _parse_date(args.get("to"))
    if not d_from or not d_to:
        return today - timedelta(days=89), today
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    return d_from, d_to

def _safe_shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # handles Feb 29
        return d.replace(year=d.year + years, month=2, day=28)

def _bind_array(name, vals):
    return bindparam(name, value=vals, type_=ARRAY(String()))

def _resolve_peer_customers(customer_code, peer_group):
    peer_group = (peer_group or "auto").strip().lower()
    if peer_group.startswith("group:"):
        gid = peer_group.split(":", 1)[1].strip()
        rows = db.session.execute(text("""
          SELECT customer_code_365 FROM customer_reporting_group_members WHERE group_id = :gid
        """), {"gid": gid}).fetchall()
        return [r[0] for r in rows if r and r[0] and r[0] != customer_code]

    seg = db.session.execute(text("""
      SELECT COALESCE(category_1_name,'') AS seg FROM ps_customers WHERE customer_code_365 = :c LIMIT 1
    """), {"c": customer_code}).mappings().first()
    seg_val = (seg["seg"] if seg else "").strip()
    # Allow empty segments to match each other
    # if not seg_val: return []

    rows = db.session.execute(text("""
      SELECT customer_code_365 FROM ps_customers
      WHERE (COALESCE(category_1_name,'') = :seg OR category_1_name IS NULL) AND customer_code_365 <> :c AND deleted_at IS NULL
      LIMIT 400
    """), {"seg": seg_val, "c": customer_code}).fetchall()
    return [r[0] for r in rows if r and r[0]]

@catmgr_bp.route("/api/category-gaps")
@login_required
def api_category_gaps():
    if not _role_ok(): return jsonify({"error": "forbidden"}), 403
    customer = (request.args.get("customer_code") or "").strip()
    peer_group = request.args.get("peer_group") or "auto"
    include_credits = (request.args.get("include_credits") or "0") == "1"
    min_pen = _safe_float(request.args.get("min_penetration") or "0.30", 0.30)
    d_from, d_to = _resolve_range(request.args)
    peers = _resolve_peer_customers(customer, peer_group)
    if not customer or not peers: return jsonify({"meta": {"peer_customers": len(peers)}, "items": []})
    line_filter = "s.qty <> 0" if include_credits else "s.qty > 0 AND s.net_excl > 0"

    cat_name_expr = f"COALESCE(cat.category_name, i.{ITEM_CATEGORY_COL}, 'Unclassified')"

    sql = text(f"""
      WITH peer_customers AS (SELECT unnest(CAST(:peer_customers AS text[])) AS customer_code_365),
      peer_active AS (
        SELECT COUNT(DISTINCT s.customer_code_365) AS n
        FROM {SALES_SRC} s JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        WHERE s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
      ),
      cust_cat AS (
        SELECT {cat_name_expr} AS category,
               SUM(s.net_excl) AS cust_sales
        FROM {SALES_SRC} s LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = s.item_code_365
        LEFT JOIN {CAT_TBL} cat ON cat.category_code_365 = i.{ITEM_CATEGORY_COL}
        WHERE s.customer_code_365 = :customer AND s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
        GROUP BY 1
      ),
      peer_cat AS (
        SELECT {cat_name_expr} AS category,
               SUM(s.net_excl) AS peer_sales
        FROM {SALES_SRC} s JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = s.item_code_365
        LEFT JOIN {CAT_TBL} cat ON cat.category_code_365 = i.{ITEM_CATEGORY_COL}
        WHERE s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
        GROUP BY 1
      ),
      totals AS (
        SELECT COALESCE((SELECT SUM(cust_sales) FROM cust_cat),0) AS cust_total,
               COALESCE((SELECT SUM(peer_sales) FROM peer_cat),0) AS peer_total
      ),
      peer_item AS (
        SELECT {cat_name_expr} AS category, s.item_code_365, COUNT(DISTINCT s.customer_code_365) AS buyers
        FROM {SALES_SRC} s JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = s.item_code_365
        LEFT JOIN {CAT_TBL} cat ON cat.category_code_365 = i.{ITEM_CATEGORY_COL}
        WHERE s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
        GROUP BY 1, 2
      ),
      cust_bought AS (
        SELECT DISTINCT item_code_365 FROM {SALES_SRC} s
        WHERE s.customer_code_365 = :customer AND s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
      ),
      missing AS (
        SELECT pi.category, COUNT(*) AS missing_items
        FROM peer_item pi CROSS JOIN peer_active pa LEFT JOIN cust_bought cb ON cb.item_code_365 = pi.item_code_365
        WHERE cb.item_code_365 IS NULL AND pa.n >= 5 AND (pi.buyers::numeric / NULLIF(pa.n,0)) >= :min_pen
        GROUP BY pi.category
      )
      SELECT
        COALESCE(c.category, p.category) AS category,
        COALESCE(c.cust_sales,0) AS cust_sales,
        COALESCE(p.peer_sales,0) AS peer_sales,
        CASE WHEN t.cust_total>0 THEN COALESCE(c.cust_sales,0)/t.cust_total ELSE 0 END AS cust_share,
        CASE WHEN t.peer_total>0 THEN COALESCE(p.peer_sales,0)/t.peer_total ELSE 0 END AS peer_share,
        (CASE WHEN t.cust_total>0 THEN COALESCE(c.cust_sales,0)/t.cust_total ELSE 0 END) -
        (CASE WHEN t.peer_total>0 THEN COALESCE(p.peer_sales,0)/t.peer_total ELSE 0 END) AS share_gap,
        COALESCE(m.missing_items,0) AS missing_items
      FROM cust_cat c FULL OUTER JOIN peer_cat p ON p.category = c.category CROSS JOIN totals t
      LEFT JOIN missing m ON m.category = COALESCE(c.category,p.category)
      ORDER BY 6 ASC, 3 DESC LIMIT 200
    """).bindparams(bindparam("peer_customers", type_=ARRAY(String)))
    try:
        rows = db.session.execute(sql, {
            "peer_customers": peers,
            "customer": customer,
            "d_from": d_from,
            "d_to": d_to,
            "min_pen": min_pen
        }).mappings().all()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "query_failed", "detail": str(e)[:400]}), 500

    return jsonify({
        "meta": {
            "customer": customer,
            "peer_group": peer_group,
            "peer_customers": len(peers),
            "from": str(d_from),
            "to": str(d_to),
            "min_penetration": min_pen
        },
        "items": [dict(r) for r in rows]
    })

@catmgr_bp.route("/api/category-suggestions")
@login_required
def api_category_suggestions():
    if not _role_ok(): return jsonify({"error": "forbidden"}), 403
    customer = (request.args.get("customer_code") or "").strip()
    peer_group = request.args.get("peer_group") or "auto"
    category = (request.args.get("category") or "").strip()
    include_credits = (request.args.get("include_credits") or "0") == "1"
    min_pen = _safe_float(request.args.get("min_penetration") or "0.30", 0.30)
    must_pen = _safe_float(request.args.get("must_pen") or "0.60", 0.60)
    variety_pen = _safe_float(request.args.get("variety_pen") or "0.15", 0.15)
    limit = max(30, min(int(request.args.get("limit") or 250), 500))
    d_from, d_to = _resolve_range(request.args)
    peers = _resolve_peer_customers(customer, peer_group)
    if not customer or not peers or not category: return jsonify({"meta": {}, "blocks": {"must": [], "should": [], "variety": []}})
    line_filter = "s.qty <> 0" if include_credits else "s.qty > 0 AND s.net_excl > 0"

    sql = text(f"""
      WITH peer_customers AS (SELECT unnest(CAST(:peer_customers AS text[])) AS customer_code_365),
      peer_active AS (
        SELECT COUNT(DISTINCT s.customer_code_365) AS n
        FROM {SALES_SRC} s JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        WHERE s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
      ),
      peer_item AS (
        SELECT s.item_code_365, COUNT(DISTINCT s.customer_code_365) AS buyers, SUM(s.net_excl) AS peer_sales
        FROM {SALES_SRC} s JOIN peer_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = s.item_code_365
        LEFT JOIN {CAT_TBL} cat ON cat.category_code_365 = i.{ITEM_CATEGORY_COL}
        WHERE s.sale_date BETWEEN :d_from AND :d_to AND {line_filter} AND COALESCE(cat.category_name, i.{ITEM_CATEGORY_COL}, 'Unclassified') = :category
        GROUP BY s.item_code_365
      ),
      cust_bought AS (
        SELECT DISTINCT item_code_365 FROM {SALES_SRC} s
        WHERE s.customer_code_365 = :customer AND s.sale_date BETWEEN :d_from AND :d_to AND {line_filter}
      ),
      cust_last AS (
        SELECT item_code_365, MAX(sale_date) AS last_bought FROM {SALES_SRC} s
        WHERE s.customer_code_365 = :customer AND s.sale_date >= (CURRENT_DATE - INTERVAL '3 years')
        GROUP BY item_code_365
      )
      SELECT
        pi.item_code_365 AS item_code, COALESCE(i.{ITEM_NAME_COL}, '') AS item_name, COALESCE(i.{ITEM_BRAND_COL}, '') AS brand,
        pi.buyers, pa.n AS peer_active, (pi.buyers::numeric / NULLIF(pa.n,0)) AS penetration,
        (pi.peer_sales::numeric / NULLIF(pi.buyers,0)) AS peer_avg_sales,
        ((pi.buyers::numeric / NULLIF(pa.n,0)) * (pi.peer_sales::numeric / NULLIF(pi.buyers,0))) AS score,
        cl.last_bought
      FROM peer_item pi CROSS JOIN peer_active pa LEFT JOIN cust_bought cb ON cb.item_code_365 = pi.item_code_365
      LEFT JOIN cust_last cl ON cl.item_code_365 = pi.item_code_365
      LEFT JOIN {ITEMS_TBL} i ON i.item_code_365 = pi.item_code_365
      WHERE cb.item_code_365 IS NULL AND pa.n >= 5 AND (pi.buyers::numeric / NULLIF(pa.n,0)) >= :variety_pen
      ORDER BY 8 DESC NULLS LAST, 6 DESC LIMIT :lim
    """).bindparams(bindparam("peer_customers", type_=ARRAY(String)))
    try:
        rows = db.session.execute(sql, {
            "peer_customers": peers,
            "customer": customer,
            "category": category,
            "d_from": d_from,
            "d_to": d_to,
            "variety_pen": variety_pen,
            "lim": limit
        }).mappings().all()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "query_failed", "detail": str(e)[:400]}), 500

    must, should, variety = [], [], []
    for r in rows:
        rr = dict(r)
        rr["tag"] = "WINBACK" if rr.get("last_bought") and str(rr["last_bought"]) < str(d_from) else "NEW"
        pen = float(rr["penetration"] or 0)
        if pen >= must_pen: must.append(rr)
        elif pen >= min_pen: should.append(rr)
        else: variety.append(rr)

    return jsonify({"meta": {"customer": customer, "peer_group": peer_group, "peer_customers": len(peers), "category": category, "from": str(d_from), "to": str(d_to)}, "blocks": {"must": must[:80], "should": should[:120], "variety": variety[:120]}})

@catmgr_bp.route("/export/proposal.csv", methods=["POST"])
@login_required
def export_proposal_csv():
    if not _role_ok(): return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    codes = [c.strip() for c in (data.get("item_codes") or []) if c and str(c).strip()][:500]
    if not codes: return jsonify({"error": "no_items"}), 400
    sql = text(f"SELECT i.item_code_365 AS item_code, COALESCE(i.{ITEM_NAME_COL}, '') AS item_name, COALESCE(cat.category_name, i.{ITEM_CATEGORY_COL}, '') AS category, COALESCE(i.{ITEM_BRAND_COL}, '') AS brand FROM {ITEMS_TBL} i LEFT JOIN {CAT_TBL} cat ON cat.category_code_365 = i.{ITEM_CATEGORY_COL} WHERE i.item_code_365 = ANY(:codes) ORDER BY category, i.{ITEM_BRAND_COL}")
    rows = db.session.execute(sql, {"codes": codes}).mappings().all()
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["item_code", "item_name", "category", "brand"])
    for r in rows: w.writerow([r["item_code"], r["item_name"], r["category"], r["brand"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=proposal.csv"})
