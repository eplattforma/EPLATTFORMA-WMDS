import csv
import io
import logging
import os
import ftplib
import time
from collections import Counter
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def _get_excluded_rule_codes():
    try:
        from services.crm_offer_admin import get_excluded_rule_codes
        return get_excluded_rule_codes()
    except Exception:
        return []


def _excluded_rule_sql(alias="", param_name="excluded_rules"):
    codes = _get_excluded_rule_codes()
    if not codes:
        return "", {}
    codes_list = list(codes)
    col = f"{alias}rule_code" if alias else "rule_code"
    return f" AND {col} NOT IN (SELECT unnest(CAST(:{param_name} AS text[])))", {param_name: codes_list}


FTP_HOST = "195.201.199.118"
FTP_PORT = 21
REMOTE_FILE = "customer_price_master.csv"

REQUIRED_COLUMNS = [
    "snapshot_at", "customer_id", "customer_email", "sku",
    "product_name", "rule_code", "rule_name", "rule_description",
    "origin_price", "customer_final_price",
]

DEFAULT_LOW_MARGIN_PCT = 12
DEFAULT_NEGATIVE_MARGIN_PCT = 0
DEFAULT_STRONG_DISCOUNT_PCT = 15
DEFAULT_COST_SOURCE = "cost_price"

PRICE_OFFERS_JOB_NAME = "crm_price_offers_refresh"

VALID_COST_COLUMNS = ["cost_price", "average_cost", "last_purchase_cost", "standard_cost"]


def _safe_decimal(val):
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError):
        return None


def _safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _get_setting(key, default=None):
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM settings WHERE key = :k"),
                {"k": key}
            ).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def _parse_snapshot_at(value):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    s = str(value).strip()
    if not s:
        return datetime.now(timezone.utc)
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _normalize_email(email):
    if not email:
        return None
    return email.strip().lower() or None


def _normalize_rule_code(rc):
    if rc is None:
        return "__NO_RULE__"
    rc = str(rc).strip()
    return rc if rc else "__NO_RULE__"


def _normalize_rule_name(rn, rc):
    if rn is None or str(rn).strip() == "":
        if rc and rc != "__NO_RULE__":
            return rc
        return "No Rule"
    return str(rn).strip()


def _build_customer_id_map():
    m = {}
    with db.engine.connect() as conn:
        try:
            rows = conn.execute(text("""
                SELECT DISTINCT magento_customer_id, customer_code_365
                FROM magento_customer_last_login_current
                WHERE magento_customer_id IS NOT NULL AND customer_code_365 IS NOT NULL
            """)).fetchall()
            for r in rows:
                m[r[0]] = r[1]
        except Exception as e:
            logger.debug(f"ID map from login table: {e}")

        try:
            rows2 = conn.execute(text("""
                SELECT DISTINCT magento_customer_id, customer_code_365
                FROM crm_abandoned_cart_state
                WHERE magento_customer_id IS NOT NULL AND customer_code_365 IS NOT NULL
            """)).fetchall()
            for r in rows2:
                if r[0] not in m:
                    m[r[0]] = r[1]
        except Exception as e:
            logger.debug(f"ID map from abandoned cart: {e}")

    logger.info(f"Customer ID map: {len(m)} magento→ps365 mappings")
    return m


def _build_customer_email_map():
    em = {}
    with db.engine.connect() as conn:
        try:
            rows = conn.execute(text("""
                SELECT LOWER(TRIM(email)), customer_code_365
                FROM magento_customer_last_login_current
                WHERE email IS NOT NULL AND email != ''
                  AND customer_code_365 IS NOT NULL
            """)).fetchall()
            for r in rows:
                if r[0] and r[0] not in em:
                    em[r[0]] = r[1]
        except Exception as e:
            logger.debug(f"Email map from login table: {e}")

        try:
            rows2 = conn.execute(text("""
                SELECT LOWER(TRIM(email)), customer_code_365
                FROM ps_customers
                WHERE email IS NOT NULL AND email != ''
                  AND customer_code_365 IS NOT NULL
            """)).fetchall()
            for r in rows2:
                if r[0] and r[0] not in em:
                    em[r[0]] = r[1]
        except Exception as e:
            logger.debug(f"Email map from ps_customers: {e}")

    logger.info(f"Customer email map: {len(em)} email→ps365 mappings")
    return em


def resolve_customer_code(customer_id_magento, customer_email, id_map, email_map):
    if customer_id_magento and customer_id_magento in id_map:
        return id_map[customer_id_magento]
    norm_email = _normalize_email(customer_email)
    if norm_email and norm_email in email_map:
        return email_map[norm_email]
    return None


def _build_customer_names(codes):
    if not codes:
        return {}
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT customer_code_365, company_name
                FROM ps_customers
                WHERE customer_code_365 = ANY(:codes)
            """), {"codes": list(codes)}).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception as e:
        logger.debug(f"Customer names lookup: {e}")
        return {}


def _build_item_map():
    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT item_code_365, item_name, brand_code_365,
                   supplier_code_365, supplier_name, category_code_365,
                   selling_price, cost_price
            FROM ps_items_dw
        """)).fetchall()
    by_code = {}
    by_sku = {}
    for r in rows:
        item_code = r[0]
        upper_code = item_code.upper().strip() if item_code else None
        info = {
            "item_code_365": item_code,
            "item_name": r[1],
            "brand_name": r[2],
            "supplier_code": r[3],
            "supplier_name": r[4],
            "category_code": r[5],
            "selling_price": r[6],
            "cost_price": r[7],
        }
        if upper_code:
            by_code[upper_code] = info
            by_sku[upper_code] = info
    return by_code, by_sku


def resolve_item(sku, item_code_map, item_sku_map):
    sku_upper = sku.upper().strip() if sku else None
    if not sku_upper:
        return None
    info = item_code_map.get(sku_upper)
    if info:
        return info
    info = item_sku_map.get(sku_upper)
    if info:
        return info
    return None


def _build_category_map():
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT category_code_365, category_description
                FROM dw_item_categories
            """)).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _get_cost_column():
    configured = _get_setting("crm_offer_cost_source", DEFAULT_COST_SOURCE)
    if configured and configured in VALID_COST_COLUMNS:
        try:
            with db.engine.connect() as conn:
                exists = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'ps_items_dw' AND column_name = :col
                """), {"col": configured}).fetchone()
            if exists:
                return configured
        except Exception:
            pass
    return "cost_price"


def _build_item_cost_map(cost_column):
    with db.engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT item_code_365, {cost_column}
            FROM ps_items_dw
            WHERE {cost_column} IS NOT NULL
        """)).fetchall()
    return {r[0]: r[1] for r in rows}


def get_item_cost_from_row(item_code, cost_map):
    if not item_code:
        return None
    cost = cost_map.get(item_code)
    return Decimal(str(cost)) if cost is not None else None


def _get_recent_sales_bulk(customer_codes):
    if not customer_codes:
        return {}
    now = date.today()
    d28 = now - timedelta(days=28)
    d90 = now - timedelta(days=90)

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT customer_code_365, item_code_365,
                       COALESCE(SUM(CASE WHEN sale_date >= :d28 THEN qty ELSE 0 END), 0) AS qty_4w,
                       COALESCE(SUM(CASE WHEN sale_date >= :d28 THEN net_excl ELSE 0 END), 0) AS val_4w,
                       COALESCE(SUM(qty), 0) AS qty_90d,
                       COALESCE(SUM(net_excl), 0) AS val_90d,
                       MAX(sale_date) AS last_sold
                FROM dw_sales_lines_v
                WHERE customer_code_365 = ANY(:codes)
                  AND sale_date >= :d90
                GROUP BY customer_code_365, item_code_365
            """), {"codes": list(customer_codes), "d28": d28, "d90": d90}).fetchall()

        result = {}
        for r in rows:
            key = (r[0], r[1])
            result[key] = {
                "sold_qty_4w": float(r[2] or 0),
                "sold_value_4w": float(r[3] or 0),
                "sold_qty_90d": float(r[4] or 0),
                "sold_value_90d": float(r[5] or 0),
                "last_sold_at": r[6],
            }
        return result
    except Exception as e:
        logger.warning(f"Sales enrichment unavailable (dw_sales_lines_v): {e}")
        return {}


def acquire_price_offers_lock(locked_by="manual"):
    try:
        db.session.execute(text("""
            DELETE FROM sync_job_lock
            WHERE job_name = :job_name AND locked_at < NOW() - INTERVAL '15 minutes'
        """), {"job_name": PRICE_OFFERS_JOB_NAME})
        db.session.execute(text("""
            INSERT INTO sync_job_lock (job_name, locked_at, locked_by)
            VALUES (:job_name, NOW(), :locked_by)
        """), {"job_name": PRICE_OFFERS_JOB_NAME, "locked_by": locked_by})
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def release_price_offers_lock():
    db.session.execute(text("DELETE FROM sync_job_lock WHERE job_name = :job_name"),
                       {"job_name": PRICE_OFFERS_JOB_NAME})
    db.session.commit()


def import_customer_price_master_csv(csv_text, source_label="manual"):
    from update_crm_offer_schema import ensure_crm_offer_schema
    ensure_crm_offer_schema()

    db.session.rollback()

    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        return {"success": False, "error": f"Missing columns: {missing}"}

    rows_list = list(reader)
    if not rows_list:
        return {"success": False, "error": "CSV is empty"}

    now = datetime.now(timezone.utc)
    snapshot_val = _parse_snapshot_at(rows_list[0].get("snapshot_at", ""))

    batch_row = db.session.execute(text("""
        INSERT INTO crm_customer_offer_import_batch
        (source_name, snapshot_at, row_count, imported_at, imported_by, status)
        VALUES (:src, :sa, :rc, :ia, :ib, 'processing')
        RETURNING id
    """), {
        "src": source_label, "sa": snapshot_val,
        "rc": len(rows_list), "ia": now, "ib": source_label,
    }).fetchone()
    batch_id = batch_row[0]
    db.session.commit()

    raw_params = []
    for row in rows_list:
        magento_id = None
        try:
            magento_id = int(row.get("customer_id", "").strip())
        except (ValueError, TypeError):
            pass

        snap = _parse_snapshot_at(row.get("snapshot_at", ""))
        raw_params.append({
            "bid": batch_id, "sa": snap,
            "mid": magento_id,
            "em": (row.get("customer_email") or "").strip(),
            "sku": (row.get("sku") or "").strip(),
            "pn": (row.get("product_name") or "").strip(),
            "rc": (row.get("rule_code") or "").strip(),
            "rn": (row.get("rule_name") or "").strip(),
            "rd": (row.get("rule_description") or "").strip(),
            "op": _safe_decimal(row.get("origin_price")),
            "cfp": _safe_decimal(row.get("customer_final_price")),
            "ia": now,
        })

    if raw_params:
        db.session.execute(text("""
            INSERT INTO crm_customer_offer_raw
            (import_batch_id, snapshot_at, customer_id_magento, customer_email,
             sku, product_name, rule_code, rule_name, rule_description,
             origin_price, offer_price, imported_at)
            VALUES (:bid, :sa, :mid, :em, :sku, :pn, :rc, :rn, :rd, :op, :cfp, :ia)
        """), raw_params)
    db.session.commit()
    logger.info(f"Raw import: {len(raw_params)} rows (batch {batch_id})")

    result = _rebuild_from_batch(batch_id)
    result["raw_imported"] = len(raw_params)
    result["batch_id"] = batch_id

    db.session.execute(text("""
        UPDATE crm_customer_offer_import_batch SET status = 'done' WHERE id = :bid
    """), {"bid": batch_id})
    db.session.commit()

    return result


def _rebuild_from_batch(batch_id):
    t0 = time.time()
    try:
        cust_id_map = _build_customer_id_map()
        cust_email_map = _build_customer_email_map()
        item_code_map, item_sku_map = _build_item_map()
        category_map = _build_category_map()

        low_margin_pct = float(_get_setting("crm_offer_low_margin_pct_threshold", DEFAULT_LOW_MARGIN_PCT))
        strong_discount_pct = float(_get_setting("crm_offer_strong_discount_pct_threshold", DEFAULT_STRONG_DISCOUNT_PCT))

        with db.engine.connect() as conn:
            raw_rows = conn.execute(text("""
                SELECT customer_id_magento, customer_email, sku, product_name,
                       rule_code, rule_name, rule_description,
                       origin_price, offer_price, snapshot_at
                FROM crm_customer_offer_raw
                WHERE import_batch_id = :bid
            """), {"bid": batch_id}).fetchall()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in rebuild setup: {e}")
        raise

    now = datetime.now(timezone.utc)

    rule_codes_seen = {}
    for r in raw_rows:
        rc = _normalize_rule_code(r[4])
        if rc != "__NO_RULE__":
            rule_codes_seen[rc] = {"name": r[5], "desc": r[6]}

    for rc, info in rule_codes_seen.items():
        db.session.execute(text("""
            INSERT INTO crm_offer_rule_dim (rule_code, rule_name, rule_description, last_seen_at)
            VALUES (:rc, :rn, :rd, :ls)
            ON CONFLICT (rule_code) DO UPDATE SET
                rule_name = COALESCE(EXCLUDED.rule_name, crm_offer_rule_dim.rule_name),
                last_seen_at = EXCLUDED.last_seen_at,
                is_active = true
        """), {"rc": rc, "rn": info["name"], "rd": info["desc"], "ls": now})
    db.session.commit()

    rule_id_map = {}
    rule_rows = db.session.execute(text("SELECT id, rule_code FROM crm_offer_rule_dim")).fetchall()
    for rr in rule_rows:
        rule_id_map[rr[1]] = rr[0]

    ps_codes_needed = set()
    for r in raw_rows:
        ps_code = resolve_customer_code(r[0], r[1], cust_id_map, cust_email_map)
        if ps_code:
            ps_codes_needed.add(ps_code)

    cust_names = _build_customer_names(ps_codes_needed)
    sales_data = _get_recent_sales_bulk(list(ps_codes_needed))

    db.session.execute(text("DELETE FROM crm_customer_offer_current"))
    db.session.execute(text(
        "DELETE FROM crm_customer_offer_unresolved WHERE import_batch_id = :bid OR import_batch_id IS NULL"
    ), {"bid": batch_id})

    linked_cust = 0
    unlinked_cust = 0
    linked_item = 0
    unlinked_item = 0
    cost_missing_count = 0
    current_rows = []
    unresolved_rows = []

    for r in raw_rows:
        magento_id = r[0]
        email = r[1]
        sku_raw = (r[2] or "").strip()
        if not sku_raw:
            continue

        product_name = r[3]
        rule_code = _normalize_rule_code(r[4])
        rule_name = _normalize_rule_name(r[5], rule_code)

        offer_price = r[8]
        snapshot_at = _parse_snapshot_at(r[9])

        ps_code = resolve_customer_code(magento_id, email, cust_id_map, cust_email_map)
        if not ps_code:
            unlinked_cust += 1
            unresolved_rows.append({
                "bid": batch_id, "sa": snapshot_at, "mid": magento_id, "em": email,
                "cc": None, "sku": sku_raw, "ic": None, "rc": rule_code,
                "issue_type": "customer_unmapped",
                "issue_detail": f"magento_id={magento_id}, email={email}",
            })
            continue
        linked_cust += 1

        item_info = resolve_item(sku_raw, item_code_map, item_sku_map)
        item_code = item_info["item_code_365"] if item_info else None

        if not item_info:
            unlinked_item += 1
            unresolved_rows.append({
                "bid": batch_id, "sa": snapshot_at, "mid": magento_id, "em": email,
                "cc": ps_code, "sku": sku_raw, "ic": None, "rc": rule_code,
                "issue_type": "sku_unmapped",
                "issue_detail": f"sku={sku_raw} not found in ps_items_dw",
            })
        else:
            linked_item += 1

        if not product_name and item_info and item_info.get("item_name"):
            product_name = item_info["item_name"]

        brand_name = item_info["brand_name"] if item_info else None
        supplier_code = item_info["supplier_code"] if item_info else None
        sup_name = item_info["supplier_name"] if item_info else None
        cat_code = item_info["category_code"] if item_info else None
        cat_name = category_map.get(cat_code) if cat_code else None

        origin_price = None
        if item_info and item_info.get("selling_price") is not None:
            origin_price = item_info["selling_price"]

        cost = None
        if item_info and item_info.get("cost_price") is not None:
            cost = Decimal(str(item_info["cost_price"]))
        if item_info and cost is None:
            cost_missing_count += 1
            unresolved_rows.append({
                "bid": batch_id, "sa": snapshot_at, "mid": magento_id, "em": email,
                "cc": ps_code, "sku": sku_raw, "ic": item_code, "rc": rule_code,
                "issue_type": "cost_missing",
                "issue_detail": f"item_code={item_code}, source=ps_items_dw.cost_price",
            })

        disc_value = None
        disc_percent = None
        if origin_price is not None and offer_price is not None:
            op = Decimal(str(origin_price))
            cfp = Decimal(str(offer_price))
            disc_value = op - cfp
            if op > 0:
                disc_percent = ((op - cfp) / op * Decimal("100")).quantize(Decimal("0.0001"))

        gp = None
        gm_pct = None
        margin_status = "unknown"
        if cost is not None and offer_price is not None:
            cfp = Decimal(str(offer_price))
            gp = cfp - cost
            if cfp > 0:
                gm_pct = (gp / cfp * Decimal("100")).quantize(Decimal("0.0001"))
                if gm_pct < 0:
                    margin_status = "negative"
                elif float(gm_pct) < low_margin_pct:
                    margin_status = "low"
                else:
                    margin_status = "healthy"

        sales_key = (ps_code, item_code) if item_code else None
        sales = sales_data.get(sales_key, {}) if sales_key else {}
        sold_qty_4w = float(sales.get("sold_qty_4w", 0))
        sold_value_4w = float(sales.get("sold_value_4w", 0))
        sold_qty_90d = float(sales.get("sold_qty_90d", 0))
        sold_value_90d = float(sales.get("sold_value_90d", 0))
        last_sold_at = sales.get("last_sold_at")

        if sold_qty_4w > 0:
            line_status = "selling"
        elif margin_status in ("low", "negative"):
            line_status = "margin_risk"
        elif disc_percent is not None and float(disc_percent) >= strong_discount_pct and sold_qty_4w == 0:
            line_status = "high_discount_unused"
        elif sold_qty_4w == 0:
            line_status = "unused"
        else:
            line_status = "unknown"

        rid = rule_id_map.get(rule_code)

        current_rows.append({
            "sa": snapshot_at, "mid": magento_id, "em": email, "cc": ps_code,
            "sku": sku_raw, "ic": item_code, "pn": product_name,
            "bn": brand_name, "sc": supplier_code, "sn": sup_name, "cn": cat_name,
            "rc": rule_code, "rid": rid, "rn": rule_name,
            "op": origin_price, "cfp": offer_price,
            "dv": disc_value, "dp": disc_percent,
            "cost": cost, "gp": gp, "gm": gm_pct, "ms": margin_status,
            "sq4": sold_qty_4w, "sv4": sold_value_4w,
            "sq9": sold_qty_90d, "sv9": sold_value_90d, "lsa": last_sold_at,
            "ls": line_status, "now": now,
        })

    if current_rows:
        deduped = {}
        for row in current_rows:
            key = (row["cc"], row["sku"], row["rc"])
            deduped[key] = row
        current_rows = list(deduped.values())
        logger.info(f"Deduplicated to {len(current_rows)} unique offer rows")

        db.session.execute(text("""
            INSERT INTO crm_customer_offer_current
            (snapshot_at, customer_id_magento, customer_email, customer_code_365,
             sku, item_code_365, product_name, brand_name, supplier_code, supplier_name, category_name,
             rule_code, rule_id, rule_name,
             origin_price, offer_price, discount_value, discount_percent,
             cost, gross_profit, gross_margin_percent, margin_status,
             sold_qty_4w, sold_value_4w, sold_qty_90d, sold_value_90d, last_sold_at,
             line_status, is_active, created_at, updated_at)
            VALUES (:sa, :mid, :em, :cc,
                    :sku, :ic, :pn, :bn, :sc, :sn, :cn,
                    :rc, :rid, :rn,
                    :op, :cfp, :dv, :dp,
                    :cost, :gp, :gm, :ms,
                    :sq4, :sv4, :sq9, :sv9, :lsa,
                    :ls, true, :now, :now)
        """), current_rows)

    if unresolved_rows:
        db.session.execute(text("""
            INSERT INTO crm_customer_offer_unresolved
            (import_batch_id, snapshot_at, customer_id_magento, customer_email,
             customer_code_365, sku, item_code_365, rule_code,
             issue_type, issue_detail, created_at)
            VALUES (:bid, :sa, :mid, :em, :cc, :sku, :ic, :rc, :issue_type, :issue_detail, :now)
        """), [{**ur, "now": now} for ur in unresolved_rows])

    db.session.commit()

    _rebuild_customer_offer_summary()

    duration = time.time() - t0
    stats = {
        "success": True,
        "current_rows": len(current_rows),
        "linked_customers": linked_cust,
        "unlinked_customers": unlinked_cust,
        "linked_items": linked_item,
        "unlinked_items": unlinked_item,
        "cost_missing": cost_missing_count,
        "unresolved_rows": len(unresolved_rows),
        "duration_seconds": round(duration, 2),
    }
    logger.info(f"Offer rebuild complete: {stats}")
    return stats


def _get_total_customer_sales_4w_bulk(customer_codes):
    if not customer_codes:
        return {}
    now = date.today()
    d28 = now - timedelta(days=28)
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT customer_code_365, COALESCE(SUM(net_excl), 0) AS total_sales
                FROM dw_sales_lines_v
                WHERE customer_code_365 = ANY(:codes)
                  AND sale_date >= :d28
                GROUP BY customer_code_365
            """), {"codes": list(customer_codes), "d28": d28}).fetchall()
        return {r[0]: float(r[1]) for r in rows}
    except Exception as e:
        logger.warning(f"Total customer sales 4w unavailable (dw_sales_lines_v): {e}")
        return {}


def _rebuild_customer_offer_summary():
    db.session.execute(text("DELETE FROM crm_customer_offer_summary_current"))

    excl_sql, excl_params = _excluded_rule_sql()

    db.session.execute(text(f"""
        INSERT INTO crm_customer_offer_summary_current (
            customer_code_365, snapshot_at, has_special_pricing,
            active_offer_skus, active_offer_rules,
            avg_discount_percent, max_discount_percent,
            avg_gross_margin_percent,
            margin_risk_skus, negative_margin_skus,
            offered_skus_bought_4w, offered_skus_bought_90d, offered_skus_not_bought,
            offer_sales_4w, offer_sales_90d,
            offer_utilisation_pct,
            high_discount_unused_skus,
            top_rule_name, top_opportunity_count,
            updated_at
        )
        SELECT
            customer_code_365,
            MAX(snapshot_at),
            true,
            COUNT(DISTINCT sku),
            COUNT(DISTINCT CASE WHEN rule_code != '__NO_RULE__' THEN rule_code END),
            AVG(discount_percent),
            MAX(discount_percent),
            AVG(CASE WHEN gross_margin_percent IS NOT NULL THEN gross_margin_percent END),
            COUNT(*) FILTER (WHERE margin_status IN ('low', 'negative')),
            COUNT(*) FILTER (WHERE margin_status = 'negative'),
            COUNT(DISTINCT sku) FILTER (WHERE sold_qty_4w > 0),
            COUNT(DISTINCT sku) FILTER (WHERE sold_qty_90d > 0),
            COUNT(DISTINCT sku) - COUNT(DISTINCT sku) FILTER (WHERE sold_qty_4w > 0),
            COALESCE(SUM(sold_value_4w), 0),
            COALESCE(SUM(sold_value_90d), 0),
            CASE WHEN COUNT(DISTINCT sku) > 0
                THEN (COUNT(DISTINCT sku) FILTER (WHERE sold_qty_4w > 0))::NUMERIC
                     / COUNT(DISTINCT sku) * 100
                ELSE 0
            END,
            COUNT(*) FILTER (WHERE line_status = 'high_discount_unused'),
            NULL,
            COUNT(*) FILTER (WHERE line_status IN ('unused', 'high_discount_unused') AND margin_status != 'negative'),
            NOW()
        FROM crm_customer_offer_current
        WHERE is_active = true AND customer_code_365 IS NOT NULL{excl_sql}
        GROUP BY customer_code_365
    """), excl_params)

    db.session.execute(text(f"""
        UPDATE crm_customer_offer_summary_current s
        SET top_rule_name = ranked.rule_name
        FROM (
            SELECT customer_code_365, rule_name
            FROM (
                SELECT customer_code_365, rule_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY customer_code_365
                           ORDER BY COUNT(*) DESC, rule_name ASC
                       ) AS rn
                FROM crm_customer_offer_current
                WHERE is_active = true AND customer_code_365 IS NOT NULL
                  AND rule_code != '__NO_RULE__'{excl_sql}
                GROUP BY customer_code_365, rule_name
            ) sub
            WHERE rn = 1
        ) ranked
        WHERE s.customer_code_365 = ranked.customer_code_365
    """), excl_params)

    db.session.flush()

    all_codes = [r[0] for r in db.session.execute(text(
        "SELECT customer_code_365 FROM crm_customer_offer_summary_current"
    )).fetchall()]
    total_sales_map = _get_total_customer_sales_4w_bulk(all_codes)

    for cc, total_sales in total_sales_map.items():
        db.session.execute(text("""
            UPDATE crm_customer_offer_summary_current
            SET total_customer_sales_4w = :ts
            WHERE customer_code_365 = :cc
        """), {"ts": total_sales, "cc": cc})

    db.session.execute(text("""
        UPDATE crm_customer_offer_summary_current
        SET offer_usage_pct = CASE
                WHEN active_offer_skus > 0
                THEN (offered_skus_bought_4w::NUMERIC / active_offer_skus * 100)
                ELSE 0
            END,
            offer_sales_share_pct = CASE
                WHEN total_customer_sales_4w > 0
                THEN (offer_sales_4w / total_customer_sales_4w * 100)
                ELSE 0
            END
    """))

    db.session.commit()

    cnt = db.session.execute(text("SELECT COUNT(*) FROM crm_customer_offer_summary_current")).scalar()
    logger.info(f"Summary rebuilt: {cnt} customer summaries")


def sync_price_master_from_ftp():
    ftp_user = os.environ.get("FTP_USERNAME", "")
    ftp_pass = os.environ.get("FTP_PASSWORD", "")
    if not ftp_user or not ftp_pass:
        return {"success": False, "error": "FTP credentials missing"}

    try:
        ftp = ftplib.FTP(FTP_HOST, timeout=30)
        ftp.login(ftp_user, ftp_pass)
        buf = io.BytesIO()
        ftp.retrbinary(f"RETR {REMOTE_FILE}", buf.write)
        ftp.quit()

        csv_text = buf.getvalue().decode("utf-8", errors="replace")
        logger.info(f"FTP price master downloaded: {len(csv_text)} bytes")
        return import_customer_price_master_csv(csv_text, source_label="ftp_sync")
    except Exception as e:
        logger.error(f"FTP price master sync failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def refresh_all_customer_price_offers(csv_path=None, triggered_by=None):
    from update_crm_offer_schema import ensure_crm_offer_schema
    ensure_crm_offer_schema()

    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            csv_text = f.read()
        return import_customer_price_master_csv(csv_text, source_label=triggered_by or "manual_refresh")

    return sync_price_master_from_ftp()


def load_offer_summary_map(customer_codes):
    if not customer_codes:
        return {}
    try:
        offer_rows = db.session.execute(text("""
            SELECT customer_code_365, has_special_pricing, active_offer_skus,
                   avg_discount_percent, offered_skus_not_bought, margin_risk_skus,
                   offer_sales_4w, offer_utilisation_pct, high_discount_unused_skus,
                   offered_skus_bought_4w,
                   COALESCE(offer_usage_pct, 0),
                   COALESCE(total_customer_sales_4w, 0),
                   COALESCE(offer_sales_share_pct, 0)
            FROM crm_customer_offer_summary_current
            WHERE customer_code_365 = ANY(:codes)
        """), {"codes": list(customer_codes)}).fetchall()
        result = {}
        for orow in offer_rows:
            result[orow[0]] = {
                "has_special_pricing": orow[1],
                "active_offer_skus": orow[2],
                "avg_discount_percent": float(orow[3]) if orow[3] else 0,
                "offered_skus_not_bought": orow[4],
                "margin_risk_skus": orow[5],
                "offer_sales_4w": float(orow[6]) if orow[6] else 0,
                "offer_utilisation_pct": float(orow[7]) if orow[7] else 0,
                "high_discount_unused_skus": orow[8],
                "offered_skus_bought_4w": orow[9],
                "offer_usage_pct": float(orow[10]) if orow[10] else 0,
                "total_customer_sales_4w": float(orow[11]) if orow[11] else 0,
                "offer_sales_share_pct": float(orow[12]) if orow[12] else 0,
            }
        return result
    except Exception as e:
        logger.warning(f"Offer summary load failed (non-critical): {e}")
        return {}


def compute_offer_indicator(summary_data):
    if not summary_data or not summary_data.get("has_special_pricing"):
        return "none"
    usage = summary_data.get("offer_usage_pct", summary_data.get("offer_utilisation_pct", 0)) or 0
    bought_4w = summary_data.get("offered_skus_bought_4w", 0)
    if bought_4w == 0:
        return "unused"
    if usage >= 75:
        return "used"
    if usage >= 25:
        return "mixed"
    return "low_usage"


def compute_offer_kpi_from_summaries(summary_map):
    kpi = {
        "kpi_offer_customers_with_offers": 0,
        "kpi_offer_customers_with_unused": 0,
        "kpi_offer_customers_with_margin_risk": 0,
        "kpi_offer_sales_4w": 0,
        "kpi_offer_high_discount_unused": 0,
        "kpi_offer_avg_usage_pct": 0,
        "kpi_offer_avg_sales_share_pct": 0,
        "kpi_offer_customers_high_dependency": 0,
    }
    usage_sum = 0
    share_sum = 0
    count_with_offers = 0
    for code, s in summary_map.items():
        if not s.get("has_special_pricing"):
            continue
        count_with_offers += 1
        kpi["kpi_offer_customers_with_offers"] += 1
        if s.get("offered_skus_bought_4w", 0) == 0:
            kpi["kpi_offer_customers_with_unused"] += 1
        if s.get("margin_risk_skus", 0) > 0:
            kpi["kpi_offer_customers_with_margin_risk"] += 1
        kpi["kpi_offer_sales_4w"] += float(s.get("offer_sales_4w", 0))
        kpi["kpi_offer_high_discount_unused"] += int(s.get("high_discount_unused_skus", 0))
        usage_sum += float(s.get("offer_usage_pct", 0))
        share_pct = float(s.get("offer_sales_share_pct", 0))
        share_sum += share_pct
        if share_pct >= 50:
            kpi["kpi_offer_customers_high_dependency"] += 1
    kpi["kpi_offer_sales_4w"] = round(kpi["kpi_offer_sales_4w"], 2)
    if count_with_offers > 0:
        kpi["kpi_offer_avg_usage_pct"] = round(usage_sum / count_with_offers, 1)
        kpi["kpi_offer_avg_sales_share_pct"] = round(share_sum / count_with_offers, 1)
    return kpi


def get_customer_price_offer_summary(ps_customer_code=None, magento_customer_id=None):
    if ps_customer_code:
        row = db.session.execute(text("""
            SELECT * FROM crm_customer_offer_summary_current
            WHERE customer_code_365 = :code
        """), {"code": ps_customer_code}).fetchone()
    elif magento_customer_id:
        cc = db.session.execute(text("""
            SELECT customer_code_365 FROM magento_customer_last_login_current
            WHERE magento_customer_id = :mid LIMIT 1
        """), {"mid": magento_customer_id}).fetchone()
        if not cc:
            return {"has_special_pricing": False, "total_skus": 0, "active_offer_skus": 0}
        row = db.session.execute(text("""
            SELECT * FROM crm_customer_offer_summary_current
            WHERE customer_code_365 = :code
        """), {"code": cc[0]}).fetchone()
    else:
        return None

    if not row:
        return {
            "has_special_pricing": False,
            "total_skus": 0,
            "active_offer_skus": 0,
            "avg_discount_percent": 0,
            "max_discount_percent": 0,
            "last_snapshot": None,
            "top_rules": [],
        }

    rm = row._mapping
    excl_sql, excl_params = _excluded_rule_sql()
    rules_params = {"code": rm["customer_code_365"]}
    rules_params.update(excl_params)
    rules = db.session.execute(text(f"""
        SELECT rule_name, COUNT(*) AS cnt
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
          AND rule_code != '__NO_RULE__'{excl_sql}
        GROUP BY rule_name ORDER BY cnt DESC LIMIT 5
    """), rules_params).fetchall()

    return {
        "has_special_pricing": rm["has_special_pricing"],
        "total_skus": rm["active_offer_skus"],
        "active_offer_skus": rm["active_offer_skus"],
        "active_offer_rules": rm["active_offer_rules"],
        "avg_discount_percent": _safe_float(rm["avg_discount_percent"]) or 0,
        "max_discount_percent": _safe_float(rm["max_discount_percent"]) or 0,
        "avg_gross_margin_percent": _safe_float(rm["avg_gross_margin_percent"]),
        "margin_risk_skus": rm["margin_risk_skus"],
        "negative_margin_skus": rm["negative_margin_skus"],
        "offered_skus_bought_4w": rm["offered_skus_bought_4w"],
        "offered_skus_bought_90d": rm["offered_skus_bought_90d"],
        "offered_skus_not_bought": rm["offered_skus_not_bought"],
        "offer_sales_4w": _safe_float(rm["offer_sales_4w"]) or 0,
        "offer_sales_90d": _safe_float(rm["offer_sales_90d"]) or 0,
        "offer_utilisation_pct": _safe_float(rm["offer_utilisation_pct"]) or 0,
        "offer_usage_pct": _safe_float(rm.get("offer_usage_pct")) or 0,
        "total_customer_sales_4w": _safe_float(rm.get("total_customer_sales_4w")) or 0,
        "offer_sales_share_pct": _safe_float(rm.get("offer_sales_share_pct")) or 0,
        "high_discount_unused_skus": rm["high_discount_unused_skus"],
        "top_rule_name": rm["top_rule_name"],
        "top_opportunity_count": rm["top_opportunity_count"],
        "last_snapshot": rm["snapshot_at"].isoformat() if rm["snapshot_at"] else None,
        "top_rules": [{"name": r[0], "count": r[1]} for r in rules],
    }


def get_customer_price_offer_rows(ps_customer_code=None, magento_customer_id=None,
                                   sort_by="discount_percent", sort_dir="desc",
                                   rule_filter=None, search=None):
    where_clauses = ["is_active = true"]
    params = {}

    excl_sql, excl_params = _excluded_rule_sql()
    if excl_sql:
        where_clauses.append(excl_sql.lstrip(" AND "))
        params.update(excl_params)

    if ps_customer_code:
        where_clauses.append("customer_code_365 = :code")
        params["code"] = ps_customer_code
    elif magento_customer_id:
        cc = db.session.execute(text("""
            SELECT customer_code_365 FROM magento_customer_last_login_current
            WHERE magento_customer_id = :mid LIMIT 1
        """), {"mid": magento_customer_id}).fetchone()
        if not cc:
            return []
        where_clauses.append("customer_code_365 = :code")
        params["code"] = cc[0]
    else:
        return []

    if rule_filter:
        where_clauses.append("rule_code = :rf")
        params["rf"] = rule_filter

    if search:
        where_clauses.append("(LOWER(sku) LIKE :s OR LOWER(product_name) LIKE :s)")
        params["s"] = f"%{search.lower()}%"

    where_sql = " AND ".join(where_clauses)

    allowed_sorts = {
        "sku": "sku", "product_name": "product_name",
        "discount_percent": "discount_percent",
        "origin_price": "origin_price", "offer_price": "offer_price",
        "rule_name": "rule_name", "gross_margin_percent": "gross_margin_percent",
        "sold_qty_4w": "sold_qty_4w", "last_sold_at": "last_sold_at",
    }
    order_col = allowed_sorts.get(sort_by, "discount_percent")
    order_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"

    rows = db.session.execute(text(f"""
        SELECT sku, product_name, item_code_365,
               rule_code, rule_name,
               origin_price, offer_price,
               discount_value, discount_percent,
               cost, gross_profit, gross_margin_percent,
               margin_status, line_status,
               sold_qty_4w, sold_value_4w, sold_qty_90d, sold_value_90d, last_sold_at,
               supplier_name, brand_name, category_name,
               snapshot_at
        FROM crm_customer_offer_current
        WHERE {where_sql}
        ORDER BY {order_col} {order_dir} NULLS LAST
    """), params).fetchall()

    return [{
        "sku": r[0], "product_name": r[1], "item_code_365": r[2],
        "rule_code": r[3], "rule_name": r[4],
        "origin_price": _safe_float(r[5]), "offer_price": _safe_float(r[6]),
        "discount_value": _safe_float(r[7]), "discount_percent": _safe_float(r[8]),
        "cost": _safe_float(r[9]), "gross_profit": _safe_float(r[10]),
        "gross_margin_percent": _safe_float(r[11]),
        "margin_status": r[12], "line_status": r[13],
        "sold_qty_4w": _safe_float(r[14]) or 0, "sold_value_4w": _safe_float(r[15]) or 0,
        "sold_qty_90d": _safe_float(r[16]) or 0, "sold_value_90d": _safe_float(r[17]) or 0,
        "last_sold_at": r[18].isoformat() if r[18] else None,
        "supplier_name": r[19], "brand_name": r[20], "category_name": r[21],
        "snapshot_at": r[22].isoformat() if r[22] else None,
    } for r in rows]


def _generate_sentence(summary):
    s = summary or {}
    skus = s.get("active_offer_skus") or 0
    bought_4w = s.get("offered_skus_bought_4w") or 0
    not_bought = s.get("offered_skus_not_bought") or 0
    usage_pct = s.get("offer_usage_pct") or s.get("offer_utilisation_pct") or 0
    offer_sales = s.get("offer_sales_4w") or 0
    share_pct = s.get("offer_sales_share_pct") or 0

    parts = [f"Customer has {skus} active offer SKUs"]

    if bought_4w > 0:
        parts.append(f"using {bought_4w} ({usage_pct:.0f}% usage)")
    else:
        parts.append("none bought in the last 4 weeks")

    if not_bought > 0:
        parts.append(f"{not_bought} remain unused")

    if offer_sales > 0:
        parts.append(f"€{offer_sales:,.0f} in offer sales (4w)")

    if share_pct >= 50:
        parts.append(f"high sales dependency ({share_pct:.0f}% of total)")
    elif share_pct > 0:
        parts.append(f"{share_pct:.0f}% of sales from offers")

    return ", ".join(parts) + "."


def get_customer_offer_intelligence(customer_code_365):
    summary = get_customer_price_offer_summary(ps_customer_code=customer_code_365)

    cust_row = db.session.execute(text("""
        SELECT COALESCE(NULLIF(mobile,''), NULLIF(sms,''), NULLIF(tel_1,''), '') AS mobile,
               COALESCE(NULLIF(company_name,''), customer_code_365) AS customer_name
        FROM ps_customers WHERE customer_code_365 = :code
    """), {"code": customer_code_365}).fetchone()
    customer_mobile = cust_row[0] if cust_row else ""
    customer_name_resolved = cust_row[1] if cust_row else customer_code_365

    if not summary or not summary.get("has_special_pricing"):
        return {
            "summary": summary or {"has_special_pricing": False, "active_offer_skus": 0},
            "opportunities": [],
            "margin_risks": [],
            "rules_breakdown": [],
            "all_offers": [],
            "generated_sentence": "Customer has no active special pricing.",
            "customer_mobile": customer_mobile,
            "customer_name": customer_name_resolved,
        }

    excl_sql, excl_params = _excluded_rule_sql()
    oi_params = {"code": customer_code_365}
    oi_params.update(excl_params)

    opportunities = db.session.execute(text(f"""
        SELECT sku, product_name, offer_price, discount_percent,
               gross_margin_percent, supplier_name, brand_name, margin_status
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
          AND line_status IN ('unused', 'high_discount_unused')
          AND margin_status != 'negative'{excl_sql}
        ORDER BY discount_percent DESC NULLS LAST
        LIMIT 20
    """), oi_params).fetchall()

    margin_risks = db.session.execute(text(f"""
        SELECT sku, product_name, offer_price, cost,
               gross_profit, gross_margin_percent, rule_name, margin_status,
               sold_qty_4w, sold_value_4w, discount_percent
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
          AND sold_qty_4w > 0{excl_sql}
        ORDER BY sold_value_4w DESC NULLS LAST
        LIMIT 20
    """), oi_params).fetchall()

    rules = db.session.execute(text(f"""
        SELECT rule_name, rule_code, 
               SUM(CASE WHEN sold_qty_4w > 0 THEN 1 ELSE 0 END) AS cnt,
               AVG(CASE WHEN sold_qty_4w > 0 THEN discount_percent ELSE NULL END) AS avg_disc
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
          AND rule_code != '__NO_RULE__'{excl_sql}
        GROUP BY rule_name, rule_code
        ORDER BY cnt DESC, rule_name
    """), oi_params).fetchall()

    all_offers = get_customer_price_offer_rows(ps_customer_code=customer_code_365)

    sentence = _generate_sentence(summary)

    return {
        "summary": summary,
        "customer_mobile": customer_mobile,
        "customer_name": customer_name_resolved,
        "opportunities": [{
            "sku": r[0], "product_name": r[1],
            "offer_price": _safe_float(r[2]),
            "discount_percent": _safe_float(r[3]),
            "gross_margin_percent": _safe_float(r[4]),
            "supplier_name": r[5], "brand_name": r[6],
            "margin_status": r[7],
        } for r in opportunities],
        "margin_risks": [{
            "sku": r[0], "product_name": r[1],
            "offer_price": _safe_float(r[2]), "cost": _safe_float(r[3]),
            "gross_profit": _safe_float(r[4]),
            "gross_margin_percent": _safe_float(r[5]),
            "rule_name": r[6], "margin_status": r[7],
            "sold_qty_4w": int(r[8]) if r[8] else 0,
            "sold_value_4w": _safe_float(r[9]),
            "discount_percent": _safe_float(r[10]),
        } for r in margin_risks],
        "rules_breakdown": [{
            "rule_name": r[0], "rule_code": r[1],
            "count": r[2], "avg_discount": _safe_float(r[3]),
        } for r in rules],
        "all_offers": all_offers,
        "generated_sentence": sentence,
    }
