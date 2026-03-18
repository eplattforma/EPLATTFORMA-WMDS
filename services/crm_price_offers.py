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
        row = db.session.execute(
            text("SELECT value FROM settings WHERE key = :k"),
            {"k": key}
        ).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def _ensure_legacy_tables():
    with db.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_price_offer_import (
                id SERIAL PRIMARY KEY,
                import_batch_id VARCHAR(64) NOT NULL,
                snapshot_at TIMESTAMPTZ,
                magento_customer_id INTEGER,
                customer_email VARCHAR(255),
                sku VARCHAR(100),
                product_name TEXT,
                rule_code VARCHAR(100),
                rule_name VARCHAR(255),
                rule_description TEXT,
                origin_price NUMERIC(12,4),
                customer_final_price NUMERIC(12,4),
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_import_batch ON crm_customer_price_offer_import(import_batch_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_import_cust ON crm_customer_price_offer_import(magento_customer_id)"
        ))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_price_offer (
                id SERIAL PRIMARY KEY,
                snapshot_at TIMESTAMPTZ,
                magento_customer_id INTEGER,
                customer_email VARCHAR(255),
                ps_customer_code VARCHAR(64),
                ps_customer_name TEXT,
                sku VARCHAR(100),
                item_code_365 VARCHAR(64),
                item_name VARCHAR(255),
                rule_code VARCHAR(100),
                rule_name VARCHAR(255),
                rule_description TEXT,
                origin_price NUMERIC(12,4),
                customer_final_price NUMERIC(12,4),
                discount_amount NUMERIC(12,4),
                discount_percent NUMERIC(8,2),
                is_linked_customer BOOLEAN NOT NULL DEFAULT false,
                is_linked_item BOOLEAN NOT NULL DEFAULT false,
                import_batch_id VARCHAR(64),
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_cpo_cust_sku UNIQUE (magento_customer_id, sku)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cpo_ps_customer ON crm_customer_price_offer(ps_customer_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cpo_sku ON crm_customer_price_offer(sku)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cpo_rule ON crm_customer_price_offer(rule_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_cpo_magento ON crm_customer_price_offer(magento_customer_id)"))
        conn.commit()
    logger.info("Legacy price offer tables ensured")


def _build_customer_map():
    rows = db.session.execute(text("""
        SELECT DISTINCT magento_customer_id, customer_code_365
        FROM magento_customer_last_login_current
        WHERE magento_customer_id IS NOT NULL AND customer_code_365 IS NOT NULL
    """)).fetchall()
    m = {r[0]: r[1] for r in rows}

    rows2 = db.session.execute(text("""
        SELECT DISTINCT magento_customer_id, customer_code_365
        FROM crm_abandoned_cart_state
        WHERE magento_customer_id IS NOT NULL AND customer_code_365 IS NOT NULL
    """)).fetchall()
    for r in rows2:
        if r[0] not in m:
            m[r[0]] = r[1]

    logger.info(f"Customer map: {len(m)} magento→ps365 mappings")
    return m


def _build_customer_names(codes):
    if not codes:
        return {}
    rows = db.session.execute(text("""
        SELECT customer_code_365, company_name
        FROM ps_customers
        WHERE customer_code_365 = ANY(:codes)
    """), {"codes": list(codes)}).fetchall()
    return {r[0]: r[1] for r in rows}


def _build_item_map():
    rows = db.session.execute(text("""
        SELECT UPPER(TRIM(item_code_365)), item_code_365, item_name
        FROM ps_items_dw
    """)).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _build_item_details_map():
    rows = db.session.execute(text("""
        SELECT item_code_365, cost_price,
               supplier_code_365, category_code_365
        FROM ps_items_dw
    """)).fetchall()
    result = {}
    for r in rows:
        result[r[0]] = {
            "cost": r[1],
            "supplier_code": r[2],
            "category_code": r[3],
        }
    return result


def _build_supplier_map():
    try:
        rows = db.session.execute(text("""
            SELECT supplier_code_365, supplier_name
            FROM ps_items_dw
            WHERE supplier_code_365 IS NOT NULL AND supplier_name IS NOT NULL
        """)).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _build_category_map():
    try:
        rows = db.session.execute(text("""
            SELECT category_code_365, category_description
            FROM dw_item_categories
        """)).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _get_recent_sales_bulk(customer_codes):
    if not customer_codes:
        return {}
    now = date.today()
    d28 = now - timedelta(days=28)
    d90 = now - timedelta(days=90)

    try:
        rows = db.session.execute(text("""
            SELECT customer_code_365, item_code_365,
                   COALESCE(SUM(CASE WHEN sale_date >= :d28 THEN quantity ELSE 0 END), 0) AS qty_4w,
                   COALESCE(SUM(CASE WHEN sale_date >= :d28 THEN net_amount ELSE 0 END), 0) AS val_4w,
                   COALESCE(SUM(quantity), 0) AS qty_90d,
                   COALESCE(SUM(net_amount), 0) AS val_90d,
                   MAX(sale_date) AS last_sold
            FROM dw_sales_lines_mv
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
        logger.warning(f"Sales enrichment unavailable: {e}")
        return {}


def import_customer_price_master_csv(csv_text, source_label="manual"):
    _ensure_legacy_tables()

    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        return {"success": False, "error": f"Missing columns: {missing}"}

    rows_list = list(reader)
    if not rows_list:
        return {"success": False, "error": "CSV is empty"}

    now = datetime.now(timezone.utc)
    snapshot_val = rows_list[0].get("snapshot_at", "").strip() or now.isoformat()

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

    raw_inserted = 0
    for row in rows_list:
        magento_id = None
        try:
            magento_id = int(row.get("customer_id", "").strip())
        except (ValueError, TypeError):
            pass

        snap = row.get("snapshot_at", "").strip() or None
        db.session.execute(text("""
            INSERT INTO crm_customer_offer_raw
            (import_batch_id, snapshot_at, customer_id_magento, customer_email,
             sku, product_name, rule_code, rule_name, rule_description,
             origin_price, offer_price, imported_at)
            VALUES (:bid, :sa, :mid, :em, :sku, :pn, :rc, :rn, :rd, :op, :cfp, :ia)
        """), {
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
        raw_inserted += 1

    db.session.commit()
    logger.info(f"Raw import: {raw_inserted} rows (batch {batch_id})")

    result = _rebuild_from_batch(batch_id)
    result["raw_imported"] = raw_inserted
    result["batch_id"] = batch_id

    db.session.execute(text("""
        UPDATE crm_customer_offer_import_batch SET status = 'done' WHERE id = :bid
    """), {"bid": batch_id})
    db.session.commit()

    return result


def _rebuild_from_batch(batch_id):
    t0 = time.time()
    cust_map = _build_customer_map()
    item_map = _build_item_map()
    item_details = _build_item_details_map()
    supplier_map = _build_supplier_map()
    category_map = _build_category_map()

    low_margin_pct = float(_get_setting("crm_offer_low_margin_pct_threshold", DEFAULT_LOW_MARGIN_PCT))
    strong_discount_pct = float(_get_setting("crm_offer_strong_discount_pct_threshold", DEFAULT_STRONG_DISCOUNT_PCT))

    raw_rows = db.session.execute(text("""
        SELECT customer_id_magento, customer_email, sku, product_name,
               rule_code, rule_name, rule_description,
               origin_price, offer_price, snapshot_at
        FROM crm_customer_offer_raw
        WHERE import_batch_id = :bid
    """), {"bid": batch_id}).fetchall()

    now = datetime.now(timezone.utc)

    rule_codes_seen = {}
    for r in raw_rows:
        rc = (r[4] or "").strip()
        if rc:
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
        mid = r[0]
        if mid and mid in cust_map:
            ps_codes_needed.add(cust_map[mid])

    cust_names = _build_customer_names(ps_codes_needed)

    sales_data = _get_recent_sales_bulk(list(ps_codes_needed))

    db.session.execute(text("DELETE FROM crm_customer_offer_current"))

    linked_cust = 0
    unlinked_cust = 0
    linked_item = 0
    unlinked_item = 0
    upserted = 0
    unresolved_rows = []

    for r in raw_rows:
        magento_id = r[0]
        email = r[1]
        sku_raw = (r[2] or "").strip()
        if not sku_raw:
            continue

        sku_upper = sku_raw.upper()
        product_name = r[3]
        rule_code = (r[4] or "").strip()
        rule_name = r[5]
        origin_price = r[7]
        offer_price = r[8]
        snapshot_at = r[9]

        ps_code = cust_map.get(magento_id) if magento_id else None
        if not ps_code:
            unlinked_cust += 1
            unresolved_rows.append({
                "snapshot_at": snapshot_at, "mid": magento_id, "em": email,
                "sku": sku_raw, "rc": rule_code,
                "issue_type": "customer_unmapped",
                "issue_detail": f"magento_id={magento_id}, email={email}",
            })
            continue
        linked_cust += 1

        item_info = item_map.get(sku_upper)
        item_code = item_info[0] if item_info else None
        is_linked_itm = item_info is not None
        if is_linked_itm:
            linked_item += 1
        else:
            unlinked_item += 1

        details = item_details.get(item_code, {}) if item_code else {}
        cost = _safe_decimal(details.get("cost"))
        supplier_code = details.get("supplier_code")
        sup_name = supplier_map.get(supplier_code) if supplier_code else None
        cat_code = details.get("category_code")
        cat_name = category_map.get(cat_code) if cat_code else None

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
        sold_qty_4w = sales.get("sold_qty_4w", 0)
        sold_value_4w = sales.get("sold_value_4w", 0)
        sold_qty_90d = sales.get("sold_qty_90d", 0)
        sold_value_90d = sales.get("sold_value_90d", 0)
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
            ON CONFLICT (customer_code_365, sku, rule_code) DO UPDATE SET
                snapshot_at = EXCLUDED.snapshot_at,
                offer_price = EXCLUDED.offer_price,
                origin_price = EXCLUDED.origin_price,
                discount_value = EXCLUDED.discount_value,
                discount_percent = EXCLUDED.discount_percent,
                cost = EXCLUDED.cost,
                gross_profit = EXCLUDED.gross_profit,
                gross_margin_percent = EXCLUDED.gross_margin_percent,
                margin_status = EXCLUDED.margin_status,
                sold_qty_4w = EXCLUDED.sold_qty_4w,
                sold_value_4w = EXCLUDED.sold_value_4w,
                sold_qty_90d = EXCLUDED.sold_qty_90d,
                sold_value_90d = EXCLUDED.sold_value_90d,
                last_sold_at = EXCLUDED.last_sold_at,
                line_status = EXCLUDED.line_status,
                product_name = EXCLUDED.product_name,
                supplier_name = EXCLUDED.supplier_name,
                category_name = EXCLUDED.category_name,
                rule_name = EXCLUDED.rule_name,
                updated_at = EXCLUDED.updated_at
        """), {
            "sa": snapshot_at, "mid": magento_id, "em": email, "cc": ps_code,
            "sku": sku_raw, "ic": item_code, "pn": product_name,
            "bn": None, "sc": supplier_code, "sn": sup_name, "cn": cat_name,
            "rc": rule_code, "rid": rid, "rn": rule_name,
            "op": origin_price, "cfp": offer_price,
            "dv": disc_value, "dp": disc_percent,
            "cost": cost, "gp": gp, "gm": gm_pct, "ms": margin_status,
            "sq4": sold_qty_4w, "sv4": sold_value_4w,
            "sq9": sold_qty_90d, "sv9": sold_value_90d, "lsa": last_sold_at,
            "ls": line_status, "now": now,
        })
        upserted += 1

    if unresolved_rows:
        db.session.execute(text("DELETE FROM crm_customer_offer_unresolved"))
        for ur in unresolved_rows:
            db.session.execute(text("""
                INSERT INTO crm_customer_offer_unresolved
                (snapshot_at, customer_id_magento, customer_email, sku, rule_code,
                 issue_type, issue_detail, created_at)
                VALUES (:sa, :mid, :em, :sku, :rc, :it, :id, :now)
            """), {
                "sa": ur["snapshot_at"], "mid": ur["mid"], "em": ur["em"],
                "sku": ur["sku"], "rc": ur["rc"],
                "it": ur["issue_type"], "id": ur["issue_detail"], "now": now,
            })

    db.session.commit()

    _rebuild_customer_offer_summary()

    # Also update legacy table for backward compatibility
    _update_legacy_table(raw_rows, cust_map, item_map, cust_names, now)

    duration = time.time() - t0
    stats = {
        "success": True,
        "current_rows": upserted,
        "linked_customers": linked_cust,
        "unlinked_customers": unlinked_cust,
        "linked_items": linked_item,
        "unlinked_items": unlinked_item,
        "unresolved_rows": len(unresolved_rows),
        "duration_seconds": round(duration, 2),
    }
    logger.info(f"Offer rebuild complete: {stats}")
    return stats


def _update_legacy_table(raw_rows, cust_map, item_map, cust_names, now):
    try:
        import uuid
        batch_id = f"offer_rebuild_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        upserted = 0

        for r in raw_rows:
            magento_id = r[0]
            email = r[1]
            sku_raw = (r[2] or "").strip()
            if magento_id is None or not sku_raw:
                continue

            sku_upper = sku_raw.upper()
            ps_code = cust_map.get(magento_id)
            ps_name = cust_names.get(ps_code) if ps_code else None
            item_info = item_map.get(sku_upper)
            item_code = item_info[0] if item_info else None
            item_name = item_info[1] if item_info else None

            origin_price = r[7]
            customer_final_price = r[8]
            disc_amount = None
            disc_percent = None
            if origin_price is not None and customer_final_price is not None:
                op = Decimal(str(origin_price))
                cfp = Decimal(str(customer_final_price))
                disc_amount = op - cfp
                if op > 0:
                    disc_percent = ((op - cfp) / op * 100).quantize(Decimal("0.01"))

            db.session.execute(text("""
                INSERT INTO crm_customer_price_offer
                (snapshot_at, magento_customer_id, customer_email,
                 ps_customer_code, ps_customer_name,
                 sku, product_name, item_code_365, item_name,
                 rule_code, rule_name, rule_description,
                 origin_price, customer_final_price, discount_amount, discount_percent,
                 is_linked_customer, is_linked_item,
                 import_batch_id, imported_at, updated_at)
                VALUES (:sa, :mid, :em, :psc, :psn, :sku, :pname, :ic, :iname,
                        :rc, :rn, :rd, :op, :cfp, :da, :dp,
                        :ilc, :ili, :bid, :ia, :ua)
                ON CONFLICT (magento_customer_id, sku)
                DO UPDATE SET
                    snapshot_at = EXCLUDED.snapshot_at,
                    customer_email = EXCLUDED.customer_email,
                    ps_customer_code = EXCLUDED.ps_customer_code,
                    ps_customer_name = EXCLUDED.ps_customer_name,
                    product_name = EXCLUDED.product_name,
                    item_code_365 = EXCLUDED.item_code_365,
                    item_name = EXCLUDED.item_name,
                    rule_code = EXCLUDED.rule_code,
                    rule_name = EXCLUDED.rule_name,
                    rule_description = EXCLUDED.rule_description,
                    origin_price = EXCLUDED.origin_price,
                    customer_final_price = EXCLUDED.customer_final_price,
                    discount_amount = EXCLUDED.discount_amount,
                    discount_percent = EXCLUDED.discount_percent,
                    is_linked_customer = EXCLUDED.is_linked_customer,
                    is_linked_item = EXCLUDED.is_linked_item,
                    import_batch_id = EXCLUDED.import_batch_id,
                    updated_at = EXCLUDED.updated_at
            """), {
                "sa": r[9], "mid": magento_id, "em": email,
                "psc": ps_code, "psn": ps_name,
                "sku": sku_raw, "pname": r[3],
                "ic": item_code, "iname": item_name,
                "rc": r[4], "rn": r[5], "rd": r[6],
                "op": origin_price, "cfp": customer_final_price,
                "da": disc_amount, "dp": disc_percent,
                "ilc": ps_code is not None, "ili": item_info is not None,
                "bid": batch_id, "ia": now, "ua": now,
            })
            upserted += 1

        if upserted > 0:
            db.session.execute(text("""
                DELETE FROM crm_customer_price_offer WHERE import_batch_id != :bid
            """), {"bid": batch_id})
        db.session.commit()
        logger.info(f"Legacy table updated: {upserted} rows")
    except Exception as e:
        logger.warning(f"Legacy table update failed (non-critical): {e}")
        db.session.rollback()


def _rebuild_customer_offer_summary():
    db.session.execute(text("DELETE FROM crm_customer_offer_summary_current"))

    db.session.execute(text("""
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
            COUNT(DISTINCT rule_code),
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
        WHERE is_active = true AND customer_code_365 IS NOT NULL
        GROUP BY customer_code_365
    """))

    db.session.execute(text("""
        UPDATE crm_customer_offer_summary_current s
        SET top_rule_name = sub.rule_name
        FROM (
            SELECT DISTINCT ON (customer_code_365)
                customer_code_365, rule_name
            FROM (
                SELECT customer_code_365, rule_name, COUNT(*) AS cnt
                FROM crm_customer_offer_current
                WHERE is_active = true AND customer_code_365 IS NOT NULL
                GROUP BY customer_code_365, rule_name
                ORDER BY customer_code_365, cnt DESC
            ) ranked
        ) sub
        WHERE s.customer_code_365 = sub.customer_code_365
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
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            csv_text = f.read()
        return import_customer_price_master_csv(csv_text, source_label=triggered_by or "manual_refresh")

    return sync_price_master_from_ftp()


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
    rules = db.session.execute(text("""
        SELECT rule_name, COUNT(*) AS cnt
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
        GROUP BY rule_name ORDER BY cnt DESC LIMIT 5
    """), {"code": rm["customer_code_365"]}).fetchall()

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
        "sold_qty_4w": _safe_float(r[14]), "sold_value_4w": _safe_float(r[15]),
        "sold_qty_90d": _safe_float(r[16]), "sold_value_90d": _safe_float(r[17]),
        "last_sold_at": r[18].isoformat() if r[18] else None,
        "supplier_name": r[19], "brand_name": r[20], "category_name": r[21],
        "snapshot_at": r[22].isoformat() if r[22] else None,
    } for r in rows]


def get_customer_offer_intelligence(customer_code_365):
    summary = get_customer_price_offer_summary(ps_customer_code=customer_code_365)
    if not summary or not summary.get("has_special_pricing"):
        return {
            "summary": summary or {"has_special_pricing": False, "active_offer_skus": 0},
            "opportunities": [],
            "margin_risks": [],
            "rules_breakdown": [],
            "all_offers": [],
            "generated_sentence": "Customer has no active special pricing.",
        }

    opportunities = db.session.execute(text("""
        SELECT sku, product_name, offer_price, discount_percent,
               gross_margin_percent, supplier_name, brand_name, margin_status
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
          AND line_status IN ('unused', 'high_discount_unused')
          AND margin_status != 'negative'
        ORDER BY discount_percent DESC NULLS LAST
        LIMIT 10
    """), {"code": customer_code_365}).fetchall()

    margin_risks = db.session.execute(text("""
        SELECT sku, product_name, offer_price, cost,
               gross_profit, gross_margin_percent, rule_name, margin_status
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
          AND margin_status IN ('low', 'negative')
        ORDER BY gross_margin_percent ASC NULLS LAST
        LIMIT 10
    """), {"code": customer_code_365}).fetchall()

    rules = db.session.execute(text("""
        SELECT rule_name, rule_code, COUNT(*) AS cnt,
               AVG(discount_percent) AS avg_disc
        FROM crm_customer_offer_current
        WHERE customer_code_365 = :code AND is_active = true
        GROUP BY rule_name, rule_code
        ORDER BY cnt DESC
    """), {"code": customer_code_365}).fetchall()

    s = summary
    sentence = (
        f"Customer has {s.get('active_offer_skus', 0)} active offer SKUs"
        f", bought {s.get('offered_skus_bought_4w', 0)} in the last 4 weeks"
        f", {s.get('offered_skus_not_bought', 0)} remain unused"
        f", average discount {s.get('avg_discount_percent', 0):.1f}%"
    )
    if s.get("margin_risk_skus", 0) > 0:
        sentence += f", margin risk on {s['margin_risk_skus']} lines"
    sentence += "."

    return {
        "summary": summary,
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
        } for r in margin_risks],
        "rules_breakdown": [{
            "rule_name": r[0], "rule_code": r[1],
            "count": r[2], "avg_discount": _safe_float(r[3]),
        } for r in rules],
        "generated_sentence": sentence,
    }
