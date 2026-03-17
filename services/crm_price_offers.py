import csv
import io
import logging
import os
import ftplib
import uuid
from datetime import datetime, timezone
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


def _ensure_tables():
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
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_ps_customer ON crm_customer_price_offer(ps_customer_code)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_sku ON crm_customer_price_offer(sku)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_rule ON crm_customer_price_offer(rule_code)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_magento ON crm_customer_price_offer(magento_customer_id)"
        ))
        conn.commit()
    logger.info("Price offer tables ensured")


def _safe_decimal(val):
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val).strip())
    except (InvalidOperation, ValueError):
        return None


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


def import_customer_price_master_csv(csv_text, source_label="manual"):
    _ensure_tables()

    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        return {"success": False, "error": f"Missing columns: {missing}"}

    rows = list(reader)
    if not rows:
        return {"success": False, "error": "CSV is empty"}

    batch_id = f"{source_label}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    raw_inserted = 0
    for row in rows:
        magento_id = None
        try:
            magento_id = int(row.get("customer_id", "").strip())
        except (ValueError, TypeError):
            pass

        snapshot_at = row.get("snapshot_at", "").strip() or None
        db.session.execute(text("""
            INSERT INTO crm_customer_price_offer_import
            (import_batch_id, snapshot_at, magento_customer_id, customer_email,
             sku, product_name, rule_code, rule_name, rule_description,
             origin_price, customer_final_price, imported_at)
            VALUES (:bid, :sa, :mid, :em, :sku, :pn, :rc, :rn, :rd, :op, :cfp, :ia)
        """), {
            "bid": batch_id, "sa": snapshot_at,
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
    logger.info(f"Raw import: {raw_inserted} rows into crm_customer_price_offer_import (batch {batch_id})")

    result = link_customer_price_offers(batch_id)
    result["raw_imported"] = raw_inserted
    result["batch_id"] = batch_id
    return result


def link_customer_price_offers(batch_id):
    cust_map = _build_customer_map()
    item_map = _build_item_map()

    raw_rows = db.session.execute(text("""
        SELECT magento_customer_id, customer_email, sku, product_name,
               rule_code, rule_name, rule_description,
               origin_price, customer_final_price, snapshot_at
        FROM crm_customer_price_offer_import
        WHERE import_batch_id = :bid
    """), {"bid": batch_id}).fetchall()

    now = datetime.now(timezone.utc)
    linked_cust = 0
    unlinked_cust = 0
    linked_item = 0
    unlinked_item = 0
    upserted = 0

    ps_codes_needed = set()
    for r in raw_rows:
        if r[0] and r[0] in cust_map:
            ps_codes_needed.add(cust_map[r[0]])

    cust_names = _build_customer_names(ps_codes_needed)

    skipped_no_id = 0
    for r in raw_rows:
        magento_id = r[0]
        email = r[1]
        sku_raw = (r[2] or "").strip()

        if magento_id is None or not sku_raw:
            skipped_no_id += 1
            continue
        sku_upper = sku_raw.upper()
        product_name = r[3]
        rule_code = r[4]
        rule_name = r[5]
        rule_description = r[6]
        origin_price = r[7]
        customer_final_price = r[8]
        snapshot_at = r[9]

        ps_code = cust_map.get(magento_id) if magento_id else None
        ps_name = cust_names.get(ps_code) if ps_code else None
        is_linked_cust = ps_code is not None

        item_info = item_map.get(sku_upper)
        item_code = item_info[0] if item_info else None
        item_name = item_info[1] if item_info else None
        is_linked_itm = item_info is not None

        if is_linked_cust:
            linked_cust += 1
        else:
            unlinked_cust += 1
        if is_linked_itm:
            linked_item += 1
        else:
            unlinked_item += 1

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
            "sa": snapshot_at, "mid": magento_id, "em": email,
            "psc": ps_code, "psn": ps_name,
            "sku": sku_raw, "pname": product_name,
            "ic": item_code, "iname": item_name,
            "rc": rule_code, "rn": rule_name, "rd": rule_description,
            "op": origin_price, "cfp": customer_final_price,
            "da": disc_amount, "dp": disc_percent,
            "ilc": is_linked_cust, "ili": is_linked_itm,
            "bid": batch_id, "ia": now, "ua": now,
        })
        upserted += 1

    deleted = 0
    existing_count = db.session.execute(text(
        "SELECT count(*) FROM crm_customer_price_offer WHERE import_batch_id != :bid"
    ), {"bid": batch_id}).scalar() or 0

    if upserted > 0 and (upserted >= existing_count * 0.5 or existing_count == 0):
        deleted = db.session.execute(text("""
            DELETE FROM crm_customer_price_offer
            WHERE import_batch_id != :bid
        """), {"bid": batch_id}).rowcount
    elif existing_count > 0:
        logger.warning(
            f"Skipped stale removal: batch has {upserted} rows but {existing_count} existing rows "
            f"would be deleted. This looks like a partial/bad batch."
        )

    db.session.commit()

    stats = {
        "success": True,
        "upserted": upserted,
        "stale_removed": deleted,
        "linked_customers": linked_cust,
        "unlinked_customers": unlinked_cust,
        "linked_items": linked_item,
        "unlinked_items": unlinked_item,
    }
    logger.info(f"Price offer link complete: {stats}")
    return stats


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


def get_customer_price_offer_summary(ps_customer_code=None, magento_customer_id=None):
    where = ""
    params = {}
    if ps_customer_code:
        where = "WHERE ps_customer_code = :code"
        params["code"] = ps_customer_code
    elif magento_customer_id:
        where = "WHERE magento_customer_id = :mid"
        params["mid"] = magento_customer_id
    else:
        return None

    row = db.session.execute(text(f"""
        SELECT
            COUNT(*) AS total_skus,
            COALESCE(AVG(discount_percent), 0) AS avg_discount,
            COALESCE(MAX(discount_percent), 0) AS max_discount,
            MAX(snapshot_at) AS last_snapshot
        FROM crm_customer_price_offer
        {where}
    """), params).fetchone()

    if not row or row[0] == 0:
        return {
            "has_special_pricing": False,
            "total_skus": 0,
            "avg_discount_percent": 0,
            "max_discount_percent": 0,
            "last_snapshot": None,
            "top_rules": [],
        }

    rules = db.session.execute(text(f"""
        SELECT rule_name, COUNT(*) AS cnt
        FROM crm_customer_price_offer
        {where}
        GROUP BY rule_name
        ORDER BY cnt DESC
        LIMIT 5
    """), params).fetchall()

    return {
        "has_special_pricing": True,
        "total_skus": row[0],
        "avg_discount_percent": float(row[1] or 0),
        "max_discount_percent": float(row[2] or 0),
        "last_snapshot": row[3].isoformat() if row[3] else None,
        "top_rules": [{"name": r[0], "count": r[1]} for r in rules],
    }


def get_customer_price_offer_rows(ps_customer_code=None, magento_customer_id=None,
                                   sort_by="sku", sort_dir="asc",
                                   rule_filter=None, search=None):
    where_clauses = []
    params = {}

    if ps_customer_code:
        where_clauses.append("ps_customer_code = :code")
        params["code"] = ps_customer_code
    elif magento_customer_id:
        where_clauses.append("magento_customer_id = :mid")
        params["mid"] = magento_customer_id
    else:
        return []

    if rule_filter:
        where_clauses.append("rule_code = :rf")
        params["rf"] = rule_filter

    if search:
        where_clauses.append("(LOWER(sku) LIKE :s OR LOWER(item_name) LIKE :s OR LOWER(product_name) LIKE :s)")
        params["s"] = f"%{search.lower()}%"

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    allowed_sorts = {
        "sku": "sku", "product_name": "product_name",
        "discount_percent": "discount_percent",
        "origin_price": "origin_price",
        "customer_final_price": "customer_final_price",
        "rule_name": "rule_name",
    }
    order_col = allowed_sorts.get(sort_by, "sku")
    order_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"

    rows = db.session.execute(text(f"""
        SELECT sku, product_name, item_code_365, item_name,
               rule_code, rule_name, rule_description,
               origin_price, customer_final_price,
               discount_amount, discount_percent,
               is_linked_item, snapshot_at
        FROM crm_customer_price_offer
        WHERE {where_sql}
        ORDER BY {order_col} {order_dir}
    """), params).fetchall()

    return [{
        "sku": r[0], "product_name": r[1],
        "item_code_365": r[2], "item_name": r[3],
        "rule_code": r[4], "rule_name": r[5], "rule_description": r[6],
        "origin_price": float(r[7]) if r[7] else None,
        "customer_final_price": float(r[8]) if r[8] else None,
        "discount_amount": float(r[9]) if r[9] else None,
        "discount_percent": float(r[10]) if r[10] else None,
        "is_linked_item": r[11],
        "snapshot_at": r[12].isoformat() if r[12] else None,
    } for r in rows]
