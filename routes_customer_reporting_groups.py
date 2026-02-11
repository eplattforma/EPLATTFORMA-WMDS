import logging
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required, current_user
from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

crg_bp = Blueprint('customer_reporting_groups', __name__,
                   url_prefix='/admin/customer-reporting-groups')


def _require_admin():
    return current_user.is_authenticated and current_user.role == 'admin'


@crg_bp.route('/')
@login_required
def index():
    if not _require_admin():
        return "Access denied", 403
    return render_template('customer_reporting_groups.html')


@crg_bp.route('/api/metadata')
@login_required
def api_metadata():
    if not _require_admin():
        return jsonify({"error": "forbidden"}), 403

    groups_rows = db.session.execute(text("""
        SELECT DISTINCT reporting_group
        FROM ps_customers
        WHERE reporting_group IS NOT NULL AND reporting_group <> ''
        ORDER BY reporting_group
    """)).fetchall()
    groups = [r[0] for r in groups_rows]

    cat1_rows = db.session.execute(text("""
        SELECT DISTINCT category_code_1_365, category_1_name
        FROM ps_customers
        WHERE category_code_1_365 IS NOT NULL AND category_code_1_365 <> ''
        ORDER BY category_code_1_365
    """)).fetchall()
    categories = [{"code": r[0], "name": r[1] or ""} for r in cat1_rows]

    agent_rows = db.session.execute(text("""
        SELECT DISTINCT agent_code_365, agent_name
        FROM ps_customers
        WHERE agent_code_365 IS NOT NULL AND agent_code_365 <> ''
        ORDER BY agent_code_365
    """)).fetchall()
    agents = [{"code": r[0], "name": r[1] or ""} for r in agent_rows]

    town_rows = db.session.execute(text("""
        SELECT DISTINCT town FROM ps_customers
        WHERE town IS NOT NULL AND town <> ''
        ORDER BY town
    """)).fetchall()
    towns = [r[0] for r in town_rows]

    return jsonify({
        "groups": groups,
        "categories": categories,
        "agents": agents,
        "towns": towns
    })


def _build_filter_clause(filters):
    clauses = []
    params = {}

    q = (filters.get("q") or "").strip()
    if q:
        clauses.append("(customer_code_365 ILIKE :q OR company_name ILIKE :q)")
        params["q"] = f"%{q}%"

    rg = (filters.get("reporting_group") or "").strip()
    if rg == "__NONE__":
        clauses.append("(reporting_group IS NULL OR reporting_group = '')")
    elif rg:
        clauses.append("reporting_group = :rg")
        params["rg"] = rg

    cat = (filters.get("category") or "").strip()
    if cat:
        clauses.append("category_code_1_365 = :cat")
        params["cat"] = cat

    agent = (filters.get("agent") or "").strip()
    if agent:
        clauses.append("agent_code_365 = :agent")
        params["agent"] = agent

    town = (filters.get("town") or "").strip()
    if town:
        clauses.append("town = :town")
        params["town"] = town

    active = filters.get("active")
    if active == "1":
        clauses.append("active = true")
    elif active == "0":
        clauses.append("active = false")

    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


@crg_bp.route('/api/search')
@login_required
def api_search():
    if not _require_admin():
        return jsonify({"error": "forbidden"}), 403

    filters = {
        "q": request.args.get("q", ""),
        "reporting_group": request.args.get("reporting_group", ""),
        "category": request.args.get("category", ""),
        "agent": request.args.get("agent", ""),
        "town": request.args.get("town", ""),
        "active": request.args.get("active", ""),
    }

    page = max(1, int(request.args.get("page", "1")))
    per_page = min(200, max(10, int(request.args.get("per_page", "50"))))

    where, params = _build_filter_clause(filters)

    count_sql = text(f"SELECT COUNT(*) FROM ps_customers WHERE {where}")
    total = db.session.execute(count_sql, params).scalar()

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    data_sql = text(f"""
        SELECT customer_code_365, company_name, category_code_1_365, category_1_name,
               agent_code_365, agent_name, town, active, reporting_group
        FROM ps_customers
        WHERE {where}
        ORDER BY company_name NULLS LAST, customer_code_365
        LIMIT :limit OFFSET :offset
    """)
    rows = db.session.execute(data_sql, params).fetchall()

    items = []
    for r in rows:
        m = r._mapping
        items.append({
            "customer_code_365": m["customer_code_365"],
            "company_name": m["company_name"] or "",
            "category": m["category_code_1_365"] or "",
            "category_name": m["category_1_name"] or "",
            "agent": m["agent_code_365"] or "",
            "agent_name": m["agent_name"] or "",
            "town": m["town"] or "",
            "active": bool(m["active"]),
            "reporting_group": m["reporting_group"] or "",
        })

    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-total // per_page)),
    })


@crg_bp.route('/api/assign', methods=['POST'])
@login_required
def api_assign():
    if not _require_admin():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(force=True)
    reporting_group = (data.get("reporting_group") or "").strip()
    rg_value = reporting_group if reporting_group else None

    apply_to_filter = data.get("apply_to_filter", False)

    if apply_to_filter:
        filters = data.get("filters", {})
        where, params = _build_filter_clause(filters)
        params["rg_val"] = rg_value

        update_sql = text(f"""
            UPDATE ps_customers SET reporting_group = :rg_val
            WHERE {where}
        """)
        result = db.session.execute(update_sql, params)
        db.session.commit()
        matched = result.rowcount
        return jsonify({"matched": matched, "updated": matched})
    else:
        codes = data.get("customer_codes", [])
        if not codes:
            return jsonify({"error": "No customer codes provided"}), 400

        update_sql = text("""
            UPDATE ps_customers SET reporting_group = :rg_val
            WHERE customer_code_365 = ANY(CAST(:codes AS text[]))
        """)
        result = db.session.execute(update_sql, {"rg_val": rg_value, "codes": codes})
        db.session.commit()
        return jsonify({"matched": result.rowcount, "updated": result.rowcount})
