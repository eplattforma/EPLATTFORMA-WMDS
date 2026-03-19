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
