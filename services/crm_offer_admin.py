import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def get_offer_admin_overview(filters=None):
    where_clauses_summary = []
    where_clauses_current = []
    params = {}
    _apply_filters(filters, where_clauses_summary, where_clauses_current, params)

    sw = " AND ".join(where_clauses_summary) if where_clauses_summary else "1=1"
    cw = " AND ".join(where_clauses_current) if where_clauses_current else "1=1"

    kpi = db.session.execute(text(f"""
        SELECT
            COUNT(*) AS customers_with_offers,
            COALESCE(AVG(s.offer_usage_pct), 0) AS avg_usage_pct,
            COALESCE(AVG(s.offer_sales_share_pct), 0) AS avg_sales_share_pct,
            COALESCE(SUM(s.offer_sales_4w), 0) AS total_offer_sales_4w,
            COUNT(*) FILTER (WHERE s.offer_sales_share_pct >= 50) AS high_dependency,
            COUNT(*) FILTER (WHERE s.offer_usage_pct < 20) AS low_usage,
            COALESCE(SUM(s.active_offer_skus), 0) AS total_active_sku_lines
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND {sw}
    """), params).fetchone()

    hdu = db.session.execute(text(f"""
        SELECT COUNT(*) FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND c.line_status = 'high_discount_unused' AND {cw}
    """), params).fetchone()

    usage_dist = db.session.execute(text(f"""
        SELECT
            COUNT(*) FILTER (WHERE s.offer_usage_pct < 10) AS u0_10,
            COUNT(*) FILTER (WHERE s.offer_usage_pct >= 10 AND s.offer_usage_pct < 30) AS u10_30,
            COUNT(*) FILTER (WHERE s.offer_usage_pct >= 30 AND s.offer_usage_pct < 50) AS u30_50,
            COUNT(*) FILTER (WHERE s.offer_usage_pct >= 50) AS u50_plus
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND {sw}
    """), params).fetchone()

    sales_dist = db.session.execute(text(f"""
        SELECT
            COUNT(*) FILTER (WHERE s.offer_sales_share_pct < 20) AS s0_20,
            COUNT(*) FILTER (WHERE s.offer_sales_share_pct >= 20 AND s.offer_sales_share_pct < 50) AS s20_50,
            COUNT(*) FILTER (WHERE s.offer_sales_share_pct >= 50) AS s50_plus
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND {sw}
    """), params).fetchone()

    review_needed = db.session.execute(text(f"""
        SELECT s.customer_code_365, p.company_name, s.active_offer_skus, s.offer_usage_pct,
               s.offer_sales_share_pct, s.offer_sales_4w
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND s.active_offer_skus > 3 AND s.offer_usage_pct < 30 AND {sw}
        ORDER BY s.active_offer_skus DESC, s.offer_usage_pct ASC
        LIMIT 20
    """), params).fetchall()

    high_dep = db.session.execute(text(f"""
        SELECT s.customer_code_365, p.company_name, s.offer_sales_share_pct,
               s.offer_sales_4w, s.active_offer_skus, s.offer_usage_pct
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND s.offer_sales_share_pct >= 50 AND {sw}
        ORDER BY s.offer_sales_share_pct DESC
        LIMIT 20
    """), params).fetchall()

    weak_skus = db.session.execute(text(f"""
        SELECT c.sku, c.product_name, c.supplier_name, c.category_name,
               COUNT(DISTINCT c.customer_code_365) AS customers_with_offer,
               COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS customers_bought,
               CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                    THEN ROUND(COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0
                         / COUNT(DISTINCT c.customer_code_365), 1)
                    ELSE 0 END AS usage_pct
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND {cw}
        GROUP BY c.sku, c.product_name, c.supplier_name, c.category_name
        HAVING COUNT(DISTINCT c.customer_code_365) >= 3
        ORDER BY COUNT(DISTINCT c.customer_code_365) DESC,
                 CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                      THEN COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0
                           / COUNT(DISTINCT c.customer_code_365)
                      ELSE 0 END ASC
        LIMIT 20
    """), params).fetchall()

    return {
        "kpi": {
            "customers_with_offers": kpi[0] if kpi else 0,
            "avg_usage_pct": round(float(kpi[1]), 1) if kpi else 0,
            "avg_sales_share_pct": round(float(kpi[2]), 1) if kpi else 0,
            "total_offer_sales_4w": round(float(kpi[3]), 2) if kpi else 0,
            "high_dependency": kpi[4] if kpi else 0,
            "low_usage": kpi[5] if kpi else 0,
            "total_active_sku_lines": kpi[6] if kpi else 0,
            "strong_discount_unused": hdu[0] if hdu else 0,
        },
        "usage_distribution": {
            "u0_10": usage_dist[0] if usage_dist else 0,
            "u10_30": usage_dist[1] if usage_dist else 0,
            "u30_50": usage_dist[2] if usage_dist else 0,
            "u50_plus": usage_dist[3] if usage_dist else 0,
        },
        "sales_distribution": {
            "s0_20": sales_dist[0] if sales_dist else 0,
            "s20_50": sales_dist[1] if sales_dist else 0,
            "s50_plus": sales_dist[2] if sales_dist else 0,
        },
        "review_needed": [
            {"customer_code": r[0], "customer_name": r[1], "active_offer_skus": r[2],
             "offer_usage_pct": round(float(r[3]), 1) if r[3] else 0,
             "offer_sales_share_pct": round(float(r[4]), 1) if r[4] else 0,
             "offer_sales_4w": round(float(r[5]), 2) if r[5] else 0}
            for r in review_needed
        ],
        "high_dependency": [
            {"customer_code": r[0], "customer_name": r[1],
             "offer_sales_share_pct": round(float(r[2]), 1) if r[2] else 0,
             "offer_sales_4w": round(float(r[3]), 2) if r[3] else 0,
             "active_offer_skus": r[4],
             "offer_usage_pct": round(float(r[5]), 1) if r[5] else 0}
            for r in high_dep
        ],
        "weak_skus": [
            {"sku": r[0], "product_name": r[1], "supplier_name": r[2], "category_name": r[3],
             "customers_with_offer": r[4], "customers_bought": r[5],
             "usage_pct": round(float(r[6]), 1) if r[6] else 0}
            for r in weak_skus
        ],
    }


def get_offer_admin_customer_rows(filters=None, sort="offer_sales_share_pct", sort_dir="desc", page=1, page_size=100):
    where_clauses = []
    params = {}
    _apply_filters(filters, where_clauses, [], params)
    w = " AND ".join(where_clauses) if where_clauses else "1=1"

    allowed_sorts = {
        "active_offer_skus": "s.active_offer_skus",
        "offer_usage_pct": "s.offer_usage_pct",
        "offer_sales_share_pct": "s.offer_sales_share_pct",
        "offer_sales_4w": "s.offer_sales_4w",
        "offered_skus_not_bought": "s.offered_skus_not_bought",
        "avg_discount_percent": "s.avg_discount_percent",
        "customer_name": "p.company_name",
        "district": "cp.district",
    }
    sort_col = allowed_sorts.get(sort, "s.offer_sales_share_pct")
    direction = "DESC" if sort_dir == "desc" else "ASC"

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_row = db.session.execute(text(f"""
        SELECT COUNT(*)
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND {w}
    """), params).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.session.execute(text(f"""
        SELECT s.customer_code_365, p.company_name, COALESCE(cp.classification, '') as classification,
               COALESCE(cp.district, '') as district, s.active_offer_skus, s.offered_skus_bought_4w, s.offered_skus_not_bought,
               s.offer_usage_pct, s.offer_sales_4w, s.total_customer_sales_4w, s.offer_sales_share_pct,
               s.avg_discount_percent, s.top_rule_name, s.high_discount_unused_skus
        FROM crm_customer_offer_summary_current s
        LEFT JOIN ps_customers p ON p.customer_code_365 = s.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = s.customer_code_365
        WHERE s.has_special_pricing = true AND {w}
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [
            {
                "customer_code_365": r[0], "customer_name": r[1] or "", "classification": r[2] or "",
                "district": r[3] or "", "active_offer_skus": r[4] or 0,
                "offered_skus_bought_4w": r[5] or 0, "offered_skus_not_bought": r[6] or 0,
                "offer_usage_pct": round(float(r[7]), 1) if r[7] else 0,
                "offer_sales_4w": round(float(r[8]), 2) if r[8] else 0,
                "total_customer_sales_4w": round(float(r[9]), 2) if r[9] else 0,
                "offer_sales_share_pct": round(float(r[10]), 1) if r[10] else 0,
                "avg_discount_percent": round(float(r[11]), 1) if r[11] else 0,
                "top_rule_name": r[12] or "", "high_discount_unused_skus": r[13] or 0,
            }
            for r in rows
        ],
    }


def get_offer_admin_rule_rows(filters=None, sort="customers_count", sort_dir="desc"):
    where_clauses = []
    params = {}
    _apply_filters(filters, [], where_clauses, params)
    w = " AND ".join(where_clauses) if where_clauses else "1=1"

    allowed_sorts = {
        "customers_count": "customers_count",
        "total_offer_sales_4w": "total_offer_sales_4w",
        "avg_discount_percent": "avg_disc",
        "unused_lines_count": "unused_lines",
        "active_offer_sku_lines": "active_lines",
    }
    sort_col = allowed_sorts.get(sort, "customers_count")
    direction = "DESC" if sort_dir == "desc" else "ASC"

    rows = db.session.execute(text(f"""
        SELECT c.rule_code, c.rule_name,
               COUNT(DISTINCT c.customer_code_365) AS customers_count,
               COUNT(*) FILTER (WHERE c.is_active) AS active_lines,
               COUNT(DISTINCT c.sku) AS distinct_skus,
               COALESCE(AVG(c.discount_percent), 0) AS avg_disc,
               COALESCE(SUM(c.sold_value_4w), 0) AS total_offer_sales_4w,
               COUNT(*) FILTER (WHERE c.line_status = 'unused') AS unused_lines,
               COUNT(*) FILTER (WHERE c.line_status = 'high_discount_unused') AS hdu_lines
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND {w}
        GROUP BY c.rule_code, c.rule_name
        ORDER BY {sort_col} {direction} NULLS LAST
    """), params).fetchall()

    return [
        {
            "rule_code": r[0] or "", "rule_name": r[1] or "", "customers_count": r[2],
            "active_offer_sku_lines": r[3], "distinct_skus": r[4],
            "avg_discount_percent": round(float(r[5]), 1) if r[5] else 0,
            "total_offer_sales_4w": round(float(r[6]), 2) if r[6] else 0,
            "unused_lines_count": r[7], "high_discount_unused_count": r[8],
        }
        for r in rows
    ]


def get_offer_admin_price_review_rows(filters=None, sort="selling_price", sort_dir="desc", page=1, page_size=100):
    where_clauses = []
    params = {}
    _apply_filters(filters, [], where_clauses, params)
    w = " AND ".join(where_clauses) if where_clauses else "1=1"

    allowed_sorts = {
        "selling_price": "i.selling_price",
        "cost_price": "i.cost_price",
        "min_offer_price": "min_offer",
        "max_offer_price": "max_offer",
        "avg_offer_price": "avg_offer",
        "avg_discount_percent": "avg_disc",
        "min_margin_percent": "min_margin",
        "customers_with_offer": "cust_count",
        "rules_count": "rules_count",
        "product_name": "c.product_name",
    }
    sort_col = allowed_sorts.get(sort, "i.selling_price")
    direction = "DESC" if sort_dir == "desc" else "ASC"

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_row = db.session.execute(text(f"""
        SELECT COUNT(DISTINCT c.item_code_365)
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND c.item_code_365 IS NOT NULL AND {w}
    """), params).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.session.execute(text(f"""
        SELECT
            c.item_code_365,
            c.sku,
            MAX(c.product_name) AS product_name,
            MAX(c.supplier_name) AS supplier_name,
            MAX(c.category_name) AS category_name,
            MAX(c.brand_name) AS brand_name,
            i.selling_price,
            i.cost_price,
            MIN(c.offer_price) AS min_offer,
            MAX(c.offer_price) AS max_offer,
            ROUND(AVG(c.offer_price)::numeric, 2) AS avg_offer,
            ROUND(AVG(c.discount_percent)::numeric, 1) AS avg_disc,
            MIN(c.discount_percent) AS min_disc,
            MAX(c.discount_percent) AS max_disc,
            CASE WHEN i.cost_price > 0 AND MIN(c.offer_price) IS NOT NULL AND MIN(c.offer_price) != 0
                 THEN ROUND(((MIN(c.offer_price) - i.cost_price) / MIN(c.offer_price) * 100)::numeric, 1)
                 ELSE NULL END AS min_margin,
            CASE WHEN i.cost_price > 0 AND MAX(c.offer_price) IS NOT NULL AND MAX(c.offer_price) != 0
                 THEN ROUND(((MAX(c.offer_price) - i.cost_price) / MAX(c.offer_price) * 100)::numeric, 1)
                 ELSE NULL END AS max_margin,
            COUNT(DISTINCT c.customer_code_365) AS cust_count,
            COUNT(DISTINCT c.rule_code) FILTER (WHERE c.rule_code != '__NO_RULE__') AS rules_count,
            STRING_AGG(DISTINCT c.rule_code, ', ' ORDER BY c.rule_code) FILTER (WHERE c.rule_code != '__NO_RULE__') AS rule_codes,
            COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS cust_bought,
            COALESCE(SUM(c.sold_value_4w), 0) AS total_sales_4w,
            COALESCE(i.active, true) AS item_active
        FROM crm_customer_offer_current c
        LEFT JOIN ps_items_dw i ON i.item_code_365 = c.item_code_365
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND c.item_code_365 IS NOT NULL AND {w}
        GROUP BY c.item_code_365, c.sku, i.selling_price, i.cost_price, i.active
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    def _flag(selling, cost, offer_price, active=True):
        flags = []
        if not active:
            flags.append("inactive_item")
        if selling is not None and offer_price is not None:
            if float(offer_price) > float(selling):
                flags.append("offer_above_list")
        if cost is not None and offer_price is not None:
            if float(offer_price) <= float(cost):
                flags.append("offer_below_cost")
        if selling is None:
            flags.append("no_selling_price")
        if cost is None:
            flags.append("no_cost_price")
        return flags

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [
            {
                "item_code_365": r[0] or "",
                "sku": r[1] or "",
                "product_name": r[2] or "",
                "supplier_name": r[3] or "",
                "category_name": r[4] or "",
                "brand_name": r[5] or "",
                "selling_price": round(float(r[6]), 2) if r[6] else None,
                "cost_price": round(float(r[7]), 4) if r[7] else None,
                "min_offer_price": round(float(r[8]), 2) if r[8] else None,
                "max_offer_price": round(float(r[9]), 2) if r[9] else None,
                "avg_offer_price": round(float(r[10]), 2) if r[10] else None,
                "avg_discount_percent": round(float(r[11]), 1) if r[11] else 0,
                "min_discount_percent": round(float(r[12]), 1) if r[12] else 0,
                "max_discount_percent": round(float(r[13]), 1) if r[13] else 0,
                "min_margin_percent": round(float(r[14]), 1) if r[14] is not None else None,
                "max_margin_percent": round(float(r[15]), 1) if r[15] is not None else None,
                "customers_with_offer": r[16],
                "rules_count": r[17],
                "rule_codes": r[18] or "",
                "customers_bought_4w": r[19],
                "total_offer_sales_4w": round(float(r[20]), 2) if r[20] else 0,
                "item_active": bool(r[21]),
                "flags": _flag(r[6], r[7], r[8], active=bool(r[21])),
            }
            for r in rows
        ],
    }


def get_offer_admin_product_rows(filters=None, sort="customers_with_offer", sort_dir="desc", page=1, page_size=100):
    where_clauses = []
    params = {}
    _apply_filters(filters, [], where_clauses, params)
    w = " AND ".join(where_clauses) if where_clauses else "1=1"

    allowed_sorts = {
        "customers_with_offer": "customers_with_offer",
        "customer_usage_pct": "usage_pct",
        "total_offer_sales_4w": "total_sales_4w",
        "avg_discount_percent": "avg_disc",
    }
    sort_col = allowed_sorts.get(sort, "customers_with_offer")
    direction = "DESC" if sort_dir == "desc" else "ASC"

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_row = db.session.execute(text(f"""
        SELECT COUNT(DISTINCT c.sku)
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND {w}
    """), params).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.session.execute(text(f"""
        SELECT c.sku, c.item_code_365, c.product_name, c.supplier_name, c.category_name, c.brand_name,
               COUNT(DISTINCT c.customer_code_365) AS customers_with_offer,
               COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS customers_bought,
               CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                    THEN ROUND(COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0
                         / COUNT(DISTINCT c.customer_code_365), 1)
                    ELSE 0 END AS usage_pct,
               COALESCE(SUM(c.sold_value_4w), 0) AS total_sales_4w,
               COALESCE(AVG(c.discount_percent), 0) AS avg_disc,
               COUNT(*) FILTER (WHERE c.line_status = 'high_discount_unused') AS hdu_count,
               (SELECT cc.rule_name FROM crm_customer_offer_current cc
                WHERE cc.sku = c.sku AND cc.is_active = true
                GROUP BY cc.rule_name ORDER BY COUNT(*) DESC LIMIT 1) AS top_rule_name
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND {w}
        GROUP BY c.sku, c.item_code_365, c.product_name, c.supplier_name, c.category_name, c.brand_name
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [
            {
                "sku": r[0] or "", "item_code_365": r[1] or "", "product_name": r[2] or "",
                "supplier_name": r[3] or "", "category_name": r[4] or "", "brand_name": r[5] or "",
                "customers_with_offer": r[6], "customers_bought_4w": r[7],
                "customer_usage_pct": round(float(r[8]), 1) if r[8] else 0,
                "total_offer_sales_4w": round(float(r[9]), 2) if r[9] else 0,
                "avg_discount_percent": round(float(r[10]), 1) if r[10] else 0,
                "high_discount_unused_count": r[11],
                "top_rule_name": r[12] or "",
            }
            for r in rows
        ],
    }


def get_offer_admin_product_by_rule_rows(filters=None, sort="customers_with_offer", sort_dir="desc", page=1, page_size=100):
    """Get product performance broken down by rule."""
    where_clauses = []
    params = {}
    _apply_filters(filters, [], where_clauses, params)
    w = " AND ".join(where_clauses) if where_clauses else "1=1"

    allowed_sorts = {
        "customers_with_offer": "customers_with_offer",
        "customer_usage_pct": "usage_pct",
        "total_offer_sales_4w": "total_sales_4w",
        "avg_discount_percent": "avg_disc",
    }
    sort_col = allowed_sorts.get(sort, "customers_with_offer")
    direction = "DESC" if sort_dir == "desc" else "ASC"

    offset = (page - 1) * page_size
    params["limit"] = page_size
    params["offset"] = offset

    count_row = db.session.execute(text(f"""
        SELECT COUNT(DISTINCT (c.sku, c.rule_code))
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND c.rule_code != '__NO_RULE__' AND {w}
    """), params).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.session.execute(text(f"""
        SELECT c.sku, c.item_code_365, c.product_name, c.supplier_name, c.category_name, c.brand_name,
               c.rule_code, c.rule_name,
               COUNT(DISTINCT c.customer_code_365) AS customers_with_offer,
               COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS customers_bought,
               CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                    THEN ROUND(COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0
                         / COUNT(DISTINCT c.customer_code_365), 1)
                    ELSE 0 END AS usage_pct,
               COALESCE(SUM(c.sold_value_4w), 0) AS total_sales_4w,
               COALESCE(AVG(c.discount_percent), 0) AS avg_disc,
               COUNT(*) FILTER (WHERE c.line_status = 'high_discount_unused') AS hdu_count
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND c.rule_code != '__NO_RULE__' AND {w}
        GROUP BY c.sku, c.item_code_365, c.product_name, c.supplier_name, c.category_name, c.brand_name, c.rule_code, c.rule_name
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [
            {
                "sku": r[0] or "", "item_code_365": r[1] or "", "product_name": r[2] or "",
                "supplier_name": r[3] or "", "category_name": r[4] or "", "brand_name": r[5] or "",
                "rule_code": r[6] or "", "rule_name": r[7] or "",
                "customers_with_offer": r[8], "customers_bought_4w": r[9],
                "customer_usage_pct": round(float(r[10]), 1) if r[10] else 0,
                "total_offer_sales_4w": round(float(r[11]), 2) if r[11] else 0,
                "avg_discount_percent": round(float(r[12]), 1) if r[12] else 0,
                "high_discount_unused_count": r[13],
            }
            for r in rows
        ],
    }


def get_offer_admin_export(tab, filters=None, sort=None, sort_dir="desc"):
    if tab == "customers":
        data = get_offer_admin_customer_rows(filters, sort or "offer_sales_share_pct", sort_dir, page=1, page_size=10000)
        headers = ["Customer Code", "Customer Name", "Classification", "District",
                    "Active Offer SKUs", "Bought 4w", "Not Bought", "Usage %",
                    "Offer Sales 4w", "Total Sales 4w", "Sales Share %",
                    "Avg Discount %", "Top Rule", "High Disc Unused"]
        rows = []
        for r in data["rows"]:
            rows.append([r["customer_code_365"], r["customer_name"], r["classification"],
                         r["district"], r["active_offer_skus"], r["offered_skus_bought_4w"],
                         r["offered_skus_not_bought"], r["offer_usage_pct"], r["offer_sales_4w"],
                         r["total_customer_sales_4w"], r["offer_sales_share_pct"],
                         r["avg_discount_percent"], r["top_rule_name"], r["high_discount_unused_skus"]])
        return headers, rows
    elif tab == "rules":
        data = get_offer_admin_rule_rows(filters, sort or "customers_count", sort_dir)
        headers = ["Rule Code", "Rule Name", "Customers", "Active Lines", "Distinct SKUs",
                    "Avg Discount %", "Offer Sales 4w", "Unused Lines", "High Disc Unused"]
        rows = []
        for r in data:
            rows.append([r["rule_code"], r["rule_name"], r["customers_count"],
                         r["active_offer_sku_lines"], r["distinct_skus"],
                         r["avg_discount_percent"], r["total_offer_sales_4w"],
                         r["unused_lines_count"], r["high_discount_unused_count"]])
        return headers, rows
    elif tab == "products":
        data = get_offer_admin_product_rows(filters, sort or "customers_with_offer", sort_dir, page=1, page_size=10000)
        headers = ["SKU", "Item Code", "Product Name", "Supplier", "Category", "Brand",
                    "Customers Offered", "Customers Bought 4w", "Usage %",
                    "Offer Sales 4w", "Avg Discount %", "High Disc Unused", "Top Rule"]
        rows = []
        for r in data["rows"]:
            rows.append([r["sku"], r["item_code_365"], r["product_name"], r["supplier_name"],
                         r["category_name"], r["brand_name"], r["customers_with_offer"],
                         r["customers_bought_4w"], r["customer_usage_pct"],
                         r["total_offer_sales_4w"], r["avg_discount_percent"],
                         r["high_discount_unused_count"], r["top_rule_name"]])
        return headers, rows
    elif tab == "price_review":
        data = get_offer_admin_price_review_rows(filters, sort or "selling_price", sort_dir, page=1, page_size=10000)
        headers = ["Item Code", "SKU", "Product Name", "Supplier", "Category",
                    "Selling Price", "Cost Price", "Min Offer", "Max Offer", "Avg Offer",
                    "Avg Disc %", "Min Disc %", "Max Disc %",
                    "Min Margin %", "Max Margin %",
                    "Customers", "Bought 4w", "Sales 4w", "Rules", "Rule Names", "Flags"]
        rows = []
        for r in data["rows"]:
            rows.append([r["item_code_365"], r["sku"], r["product_name"], r["supplier_name"],
                         r["category_name"],
                         r["selling_price"], r["cost_price"],
                         r["min_offer_price"], r["max_offer_price"], r["avg_offer_price"],
                         r["avg_discount_percent"], r["min_discount_percent"], r["max_discount_percent"],
                         r["min_margin_percent"], r["max_margin_percent"],
                         r["customers_with_offer"], r["customers_bought_4w"],
                         r["total_offer_sales_4w"], r["rules_count"], r["rule_codes"],
                         ", ".join(r["flags"])])
        return headers, rows
    return [], []


def _apply_filters(filters, summary_clauses, current_clauses, params):
    if not filters:
        return
    q = (filters.get("q") or "").strip()
    if q:
        params["q"] = f"%{q}%"
        summary_clauses.append("(s.customer_code_365 ILIKE :q OR p.company_name ILIKE :q)")
        current_clauses.append("(c.customer_code_365 ILIKE :q OR c.product_name ILIKE :q OR c.sku ILIKE :q OR p.company_name ILIKE :q)")

    classification = filters.get("classification")
    if classification:
        params["classification"] = classification
        summary_clauses.append("cp.classification = :classification")
        current_clauses.append("EXISTS (SELECT 1 FROM crm_customer_profile xp WHERE xp.customer_code_365 = c.customer_code_365 AND xp.classification = :classification)")

    district = filters.get("district")
    if district:
        params["district"] = district
        summary_clauses.append("cp.district = :district")
        current_clauses.append("EXISTS (SELECT 1 FROM crm_customer_profile xd WHERE xd.customer_code_365 = c.customer_code_365 AND xd.district = :district)")

    rule_code = filters.get("rule_code")
    if rule_code:
        params["rule_code"] = rule_code
        current_clauses.append("c.rule_code = :rule_code")
        summary_clauses.append("EXISTS (SELECT 1 FROM crm_customer_offer_current x WHERE x.customer_code_365 = s.customer_code_365 AND x.rule_code = :rule_code AND x.is_active)")

    supplier = filters.get("supplier")
    if supplier:
        params["supplier"] = f"%{supplier}%"
        current_clauses.append("c.supplier_name ILIKE :supplier")

    category = filters.get("category")
    if category:
        params["category"] = f"%{category}%"
        current_clauses.append("c.category_name ILIKE :category")

    brand = filters.get("brand")
    if brand:
        params["brand"] = f"%{brand}%"
        current_clauses.append("c.brand_name ILIKE :brand")

    usage_band = filters.get("usage_band")
    if usage_band == "0-20":
        summary_clauses.append("s.offer_usage_pct < 20")
    elif usage_band == "20-50":
        summary_clauses.append("s.offer_usage_pct >= 20 AND s.offer_usage_pct < 50")
    elif usage_band == "50+":
        summary_clauses.append("s.offer_usage_pct >= 50")

    sales_band = filters.get("sales_band")
    if sales_band == "0-20":
        summary_clauses.append("s.offer_sales_share_pct < 20")
    elif sales_band == "20-50":
        summary_clauses.append("s.offer_sales_share_pct >= 20 AND s.offer_sales_share_pct < 50")
    elif sales_band == "50+":
        summary_clauses.append("s.offer_sales_share_pct >= 50")

    if filters.get("high_dependency"):
        summary_clauses.append("s.offer_sales_share_pct >= 50")

    if filters.get("unused_offers"):
        summary_clauses.append("s.offered_skus_not_bought > 0")

    rule_name = (filters.get("rule_name") or "").strip()
    if rule_name:
        params["rule_name"] = f"%{rule_name}%"
        summary_clauses.append("EXISTS (SELECT 1 FROM crm_customer_offer_current xr WHERE xr.customer_code_365 = s.customer_code_365 AND xr.rule_name ILIKE :rule_name AND xr.is_active)")
        current_clauses.append("c.rule_name ILIKE :rule_name")


def get_offer_rule_lookup(search=None):
    params = {}
    where = "c.is_active = true"
    if search:
        params["search"] = f"%{search}%"
        where += " AND (c.rule_name ILIKE :search OR c.rule_code::text ILIKE :search)"
    rows = db.session.execute(text(f"""
        SELECT c.rule_code, c.rule_name, COUNT(DISTINCT c.customer_code_365) AS cust_count, COUNT(DISTINCT c.sku) AS sku_count
        FROM crm_customer_offer_current c
        WHERE {where}
        GROUP BY c.rule_code, c.rule_name
        ORDER BY c.rule_name
    """), params).fetchall()
    return [{"rule_code": r[0], "rule_name": r[1] or "", "customer_count": r[2], "sku_count": r[3]} for r in rows]


def get_offer_rule_overview(rule_code):
    params = {"rule_code": str(rule_code)}
    header = db.session.execute(text("""
        SELECT c.rule_code, c.rule_name, c.snapshot_at,
               COUNT(DISTINCT c.customer_code_365) AS customers_on_offer,
               COUNT(DISTINCT c.sku) AS products_on_offer,
               COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS customers_buying_4w,
               COALESCE(SUM(c.sold_value_4w), 0) AS offer_sales_4w,
               COALESCE(AVG(c.discount_percent), 0) AS avg_discount_percent,
               COALESCE(AVG(c.gross_margin_percent) FILTER (WHERE c.gross_margin_percent IS NOT NULL), 0) AS avg_offer_margin_pct,
               COUNT(*) FILTER (WHERE c.line_status = 'high_discount_unused') AS high_discount_unused_lines
        FROM crm_customer_offer_current c
        WHERE c.is_active = true AND c.rule_code = :rule_code
        GROUP BY c.rule_code, c.rule_name, c.snapshot_at
    """), params).fetchone()

    if not header:
        return None

    customers_on = header[3] or 0
    customers_buying = header[5] or 0
    usage_pct = round(customers_buying * 100.0 / customers_on, 1) if customers_on > 0 else 0
    offer_sales = round(float(header[6]), 2)

    summary_sentence = (
        f"This offer is assigned to {customers_on} customers across {header[4]} products. "
        f"In the last 4 weeks, {customers_buying} customers bought at least one product from this offer, "
        f"generating €{offer_sales:,.0f} in offer sales."
    )

    return {
        "rule_code": header[0],
        "rule_name": header[1] or "",
        "snapshot_at": str(header[2]) if header[2] else "",
        "customers_on_offer": customers_on,
        "products_on_offer": header[4] or 0,
        "customers_buying_4w": customers_buying,
        "offer_usage_by_customers_pct": usage_pct,
        "offer_sales_4w": offer_sales,
        "avg_discount_percent": round(float(header[7]), 1),
        "avg_offer_margin_pct": round(float(header[8]), 1),
        "high_discount_unused_lines": header[9] or 0,
        "summary_sentence": summary_sentence,
    }


def _build_rule_detail_product_filter(filters, params):
    clauses = []
    if not filters:
        return clauses
    supplier = (filters.get("supplier") or "").strip()
    if supplier:
        params["r_supplier"] = f"%{supplier}%"
        clauses.append("c.supplier_name ILIKE :r_supplier")
    category = (filters.get("category") or "").strip()
    if category:
        params["r_category"] = f"%{category}%"
        clauses.append("c.category_name ILIKE :r_category")
    brand = (filters.get("brand") or "").strip()
    if brand:
        params["r_brand"] = f"%{brand}%"
        clauses.append("c.brand_name ILIKE :r_brand")
    q = (filters.get("q") or "").strip()
    if q:
        params["r_q"] = f"%{q}%"
        clauses.append("(c.sku ILIKE :r_q OR c.product_name ILIKE :r_q OR c.supplier_name ILIKE :r_q)")
    line_status = (filters.get("line_status") or "").strip()
    if line_status:
        params["r_line_status"] = line_status
        clauses.append("c.line_status = :r_line_status")
    if filters.get("low_margin"):
        clauses.append("c.gross_margin_percent IS NOT NULL AND c.gross_margin_percent < 15")
    if filters.get("negative_margin"):
        clauses.append("c.gross_margin_percent IS NOT NULL AND c.gross_margin_percent < 0")
    if filters.get("missing_cost"):
        clauses.append("c.cost IS NULL")
    if filters.get("only_unused"):
        clauses.append("c.sold_qty_4w = 0")
    if filters.get("only_bought"):
        clauses.append("c.sold_qty_4w > 0")
    return clauses


def _build_rule_detail_customer_filter(filters, params):
    clauses = []
    if not filters:
        return clauses
    classification = (filters.get("classification") or "").strip()
    if classification:
        params["r_classification"] = classification
        clauses.append("cp.classification = :r_classification")
    district = (filters.get("district") or "").strip()
    if district:
        params["r_district"] = district
        clauses.append("cp.district = :r_district")
    q = (filters.get("q") or "").strip()
    if q:
        params["r_cq"] = f"%{q}%"
        clauses.append("(c.customer_code_365 ILIKE :r_cq OR p.company_name ILIKE :r_cq)")
    if filters.get("zero_usage"):
        clauses.append("NOT EXISTS (SELECT 1 FROM crm_customer_offer_current x2 WHERE x2.rule_code = c.rule_code AND x2.customer_code_365 = c.customer_code_365 AND x2.is_active AND x2.sold_qty_4w > 0)")
    if filters.get("high_dependency"):
        clauses.append("s.offer_sales_share_pct >= 50")
    return clauses


def get_offer_rule_product_rows(rule_code, filters=None, sort="customers_with_offer", sort_dir="desc", page=1, page_size=50):
    params = {"rule_code": str(rule_code)}
    extra = _build_rule_detail_product_filter(filters, params)
    w = " AND ".join(["c.is_active = true", "c.rule_code = :rule_code"] + extra)

    allowed_sorts = {
        "customers_with_offer": "customers_with_offer",
        "customer_usage_pct": "usage_pct",
        "avg_discount_percent": "avg_disc",
        "avg_gross_margin_percent": "avg_margin",
        "total_offer_sales_4w": "total_sales",
        "high_discount_unused_customer_count": "hdu_count",
        "product_name": "c.product_name",
    }
    sort_col = allowed_sorts.get(sort, "customers_with_offer")
    direction = "DESC" if sort_dir == "desc" else "ASC"
    offset = max(0, (page - 1) * page_size)
    params["limit"] = page_size
    params["offset"] = offset

    count_row = db.session.execute(text(f"SELECT COUNT(DISTINCT c.sku) FROM crm_customer_offer_current c WHERE {w}"), params).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.session.execute(text(f"""
        SELECT c.sku, c.item_code_365, c.product_name, c.supplier_name, c.category_name, c.brand_name,
               COUNT(DISTINCT c.customer_code_365) AS customers_with_offer,
               COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS customers_bought,
               CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                    THEN ROUND(COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0 / COUNT(DISTINCT c.customer_code_365), 1)
                    ELSE 0 END AS usage_pct,
               COALESCE(AVG(c.origin_price), 0) AS avg_origin,
               COALESCE(AVG(c.offer_price), 0) AS avg_offer,
               COALESCE(AVG(c.discount_percent), 0) AS avg_disc,
               AVG(c.cost) FILTER (WHERE c.cost IS NOT NULL) AS avg_cost,
               AVG(c.gross_profit) FILTER (WHERE c.gross_profit IS NOT NULL) AS avg_gp,
               AVG(c.gross_margin_percent) FILTER (WHERE c.gross_margin_percent IS NOT NULL) AS avg_margin,
               COALESCE(SUM(c.sold_value_4w), 0) AS total_sales,
               COALESCE(MAX(ps.total_net), 0) AS total_product_sales
        FROM crm_customer_offer_current c
        LEFT JOIN (
            SELECT item_code_365, SUM(net_excl) AS total_net
            FROM dw_sales_lines_mv
            WHERE sale_date >= NOW() - INTERVAL '28 days'
            GROUP BY item_code_365
        ) ps ON ps.item_code_365 = c.item_code_365
        WHERE {w}
        GROUP BY c.sku, c.item_code_365, c.product_name, c.supplier_name, c.category_name, c.brand_name
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    result_rows = []
    for r in rows:
        offer_sales = float(r[15]) if r[15] else 0
        total_product_sales = float(r[16]) if r[16] else 0
        
        offer_penetration_pct = (offer_sales / total_product_sales * 100) if total_product_sales > 0 else 0
        
        result_rows.append({
            "sku": r[0] or "", "item_code_365": r[1] or "", "product_name": r[2] or "",
            "supplier_name": r[3] or "", "category_name": r[4] or "", "brand_name": r[5] or "",
            "customers_with_offer": r[6], "customers_bought_4w": r[7],
            "customer_usage_pct": round(float(r[8]), 1) if r[8] else 0,
            "avg_origin_price": round(float(r[9]), 2) if r[9] else 0,
            "avg_offer_price": round(float(r[10]), 2) if r[10] else 0,
            "avg_discount_percent": round(float(r[11]), 1) if r[11] else 0,
            "avg_cost": round(float(r[12]), 2) if r[12] else None,
            "avg_gross_profit": round(float(r[13]), 2) if r[13] else None,
            "avg_gross_margin_percent": round(float(r[14]), 1) if r[14] else None,
            "total_offer_sales_4w": round(offer_sales, 2),
            "total_product_sales_4w": round(total_product_sales, 2),
            "offer_penetration_pct": round(offer_penetration_pct, 1),
        })
    
    return {
        "total": total, "page": page, "page_size": page_size,
        "rows": result_rows,
    }


def get_offer_rule_customer_rows(rule_code, filters=None, sort="offer_sales_4w", sort_dir="desc", page=1, page_size=50):
    params = {"rule_code": str(rule_code)}
    extra_cust = _build_rule_detail_customer_filter(filters, params)
    base_where = "c.is_active = true AND c.rule_code = :rule_code"
    extra_w = (" AND " + " AND ".join(extra_cust)) if extra_cust else ""

    allowed_sorts = {
        "offer_usage_pct": "usage_pct",
        "offer_sales_4w": "offer_sales",
        "offer_sales_share_pct": "sales_share",
        "offered_products_count": "offered_count",
        "bought_products_count_4w": "bought_count",
        "avg_discount_percent": "avg_disc",
        "customer_name": "p.company_name",
    }
    sort_col = allowed_sorts.get(sort, "offer_sales")
    direction = "DESC" if sort_dir == "desc" else "ASC"
    offset = max(0, (page - 1) * page_size)
    params["limit"] = page_size
    params["offset"] = offset

    count_sql = f"""
        SELECT COUNT(DISTINCT c.customer_code_365)
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = c.customer_code_365
        LEFT JOIN postal_code_lookup pcl ON pcl.postcode = p.postal_code
        LEFT JOIN crm_customer_offer_summary_current s ON s.customer_code_365 = c.customer_code_365
        WHERE {base_where}{extra_w}
    """
    count_row = db.session.execute(text(count_sql), params).fetchone()
    total = count_row[0] if count_row else 0

    rows = db.session.execute(text(f"""
        SELECT c.customer_code_365, p.company_name,
               COALESCE(cp.classification, '') AS classification,
               COALESCE(cp.district, pcl.district, '') AS district,
               COUNT(DISTINCT c.sku) AS offered_count,
               COUNT(DISTINCT c.sku) FILTER (WHERE c.sold_qty_4w > 0) AS bought_count,
               CASE WHEN COUNT(DISTINCT c.sku) > 0
                    THEN ROUND(COUNT(DISTINCT c.sku) FILTER (WHERE c.sold_qty_4w > 0) * 100.0 / COUNT(DISTINCT c.sku), 1)
                    ELSE 0 END AS usage_pct,
               COALESCE(SUM(c.sold_value_4w), 0) AS offer_sales,
               COALESCE(MAX(s.total_customer_sales_4w), 0) AS total_cust_sales,
               CASE WHEN COALESCE(MAX(s.total_customer_sales_4w), 0) > 0
                    THEN ROUND(SUM(c.sold_value_4w) * 100.0 / MAX(s.total_customer_sales_4w), 1)
                    ELSE 0 END AS sales_share,
               COALESCE(AVG(c.discount_percent), 0) AS avg_disc,
               AVG(c.gross_margin_percent) FILTER (WHERE c.gross_margin_percent IS NOT NULL) AS avg_margin,
               MAX(c.last_sold_at) AS last_purchase
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        LEFT JOIN crm_customer_profile cp ON cp.customer_code_365 = c.customer_code_365
        LEFT JOIN postal_code_lookup pcl ON pcl.postcode = p.postal_code
        LEFT JOIN crm_customer_offer_summary_current s ON s.customer_code_365 = c.customer_code_365
        WHERE {base_where}{extra_w}
        GROUP BY c.customer_code_365, p.company_name, cp.classification, cp.district, pcl.district
        ORDER BY {sort_col} {direction} NULLS LAST
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    return {
        "total": total, "page": page, "page_size": page_size,
        "rows": [
            {
                "customer_code_365": r[0] or "", "customer_name": r[1] or "",
                "classification": r[2] or "", "district": r[3] or "",
                "offered_products_count": r[4] or 0, "bought_products_count_4w": r[5] or 0,
                "offer_usage_pct": round(float(r[6]), 1) if r[6] else 0,
                "offer_sales_4w": round(float(r[7]), 2) if r[7] else 0,
                "total_customer_sales_4w": round(float(r[8]), 2) if r[8] else 0,
                "offer_sales_share_pct": round(float(r[9]), 1) if r[9] else 0,
                "avg_discount_percent": round(float(r[10]), 1) if r[10] else 0,
                "avg_offer_margin_percent": round(float(r[11]), 1) if r[11] else None,
                "last_offer_purchase_date": str(r[12]) if r[12] else "",
            }
            for r in rows
        ],
    }


def get_offer_rule_alerts(rule_code):
    params = {"rule_code": str(rule_code)}
    weak_products = db.session.execute(text("""
        SELECT c.sku, c.product_name,
               COUNT(DISTINCT c.customer_code_365) AS cust_with,
               COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) AS cust_bought,
               CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                    THEN ROUND(COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0 / COUNT(DISTINCT c.customer_code_365), 1)
                    ELSE 0 END AS usage_pct
        FROM crm_customer_offer_current c
        WHERE c.is_active = true AND c.rule_code = :rule_code
        GROUP BY c.sku, c.product_name
        HAVING COUNT(DISTINCT c.customer_code_365) >= 2
        ORDER BY COUNT(DISTINCT c.customer_code_365) DESC,
                 CASE WHEN COUNT(DISTINCT c.customer_code_365) > 0
                      THEN COUNT(DISTINCT c.customer_code_365) FILTER (WHERE c.sold_qty_4w > 0) * 100.0 / COUNT(DISTINCT c.customer_code_365)
                      ELSE 0 END ASC
        LIMIT 10
    """), params).fetchall()

    zero_customers = db.session.execute(text("""
        SELECT c.customer_code_365, p.company_name, COUNT(DISTINCT c.sku) AS offered_count
        FROM crm_customer_offer_current c
        LEFT JOIN ps_customers p ON p.customer_code_365 = c.customer_code_365
        WHERE c.is_active = true AND c.rule_code = :rule_code
        GROUP BY c.customer_code_365, p.company_name
        HAVING COUNT(DISTINCT c.sku) FILTER (WHERE c.sold_qty_4w > 0) = 0
        ORDER BY COUNT(DISTINCT c.sku) DESC
        LIMIT 10
    """), params).fetchall()

    top_products = db.session.execute(text("""
        SELECT c.sku, c.product_name, COALESCE(SUM(c.sold_value_4w), 0) AS sales
        FROM crm_customer_offer_current c
        WHERE c.is_active = true AND c.rule_code = :rule_code AND c.sold_qty_4w > 0
        GROUP BY c.sku, c.product_name
        ORDER BY SUM(c.sold_value_4w) DESC
        LIMIT 10
    """), params).fetchall()

    return {
        "weak_products": [{"sku": r[0], "product_name": r[1], "customers_with": r[2], "customers_bought": r[3], "usage_pct": float(r[4])} for r in weak_products],
        "zero_usage_customers": [{"customer_code": r[0], "customer_name": r[1] or "", "offered_count": r[2]} for r in zero_customers],
        "top_products": [{"sku": r[0], "product_name": r[1], "sales_4w": round(float(r[2]), 2)} for r in top_products],
    }


def get_offer_rule_export(rule_code, tab, filters=None, sort=None, sort_dir="desc"):
    if tab == "rule_products":
        data = get_offer_rule_product_rows(rule_code, filters, sort or "customers_with_offer", sort_dir, page=1, page_size=10000)
        headers = ["SKU", "Item Code", "Product Name", "Supplier", "Category", "Brand",
                    "Customers Offered", "Customers Bought 4w", "Usage %",
                    "Avg Normal Price", "Avg Offer Price", "Avg Discount %",
                    "Avg Cost", "Avg Gross Profit", "Avg Margin %",
                    "Offer Sales 4w", "High Disc Unused"]
        rows = [[r["sku"], r["item_code_365"], r["product_name"], r["supplier_name"],
                 r["category_name"], r["brand_name"], r["customers_with_offer"],
                 r["customers_bought_4w"], r["customer_usage_pct"],
                 r["avg_origin_price"], r["avg_offer_price"], r["avg_discount_percent"],
                 r["avg_cost"], r["avg_gross_profit"], r["avg_gross_margin_percent"],
                 r["total_offer_sales_4w"], r["high_discount_unused_customer_count"]]
                for r in data["rows"]]
        return headers, rows
    elif tab == "rule_customers":
        data = get_offer_rule_customer_rows(rule_code, filters, sort or "offer_sales_4w", sort_dir, page=1, page_size=10000)
        headers = ["Customer Code", "Customer Name", "Classification", "District",
                    "Offered Products", "Bought 4w", "Usage %",
                    "Offer Sales 4w", "Total Sales 4w", "Sales Share %",
                    "Avg Discount %", "Avg Margin %", "Last Purchase"]
        rows = [[r["customer_code_365"], r["customer_name"], r["classification"],
                 r["district"], r["offered_products_count"], r["bought_products_count_4w"],
                 r["offer_usage_pct"], r["offer_sales_4w"], r["total_customer_sales_4w"],
                 r["offer_sales_share_pct"], r["avg_discount_percent"],
                 r["avg_offer_margin_percent"], r["last_offer_purchase_date"]]
                for r in data["rows"]]
        return headers, rows
    return [], []
