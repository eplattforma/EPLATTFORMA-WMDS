import csv
import os
import logging
from datetime import datetime, timezone
import pytz
from app import db
from models import MagentoCustomerLoginLog, MagentoCustomerLastLoginCurrent, PSCustomer

logger = logging.getLogger(__name__)

ATHENS_TZ = pytz.timezone("Europe/Athens")


def parse_dt_local_athens_to_utc(value: str):
    if not value:
        return None
    v = value.strip()

    formats = (
        # ISO / DB style
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        # European day-first
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        # US month-first (Magento admin CSV exports)
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
        # Magento admin: "3/13/26, 9:15 AM"
        "%m/%d/%y, %I:%M %p",
        "%m/%d/%Y, %I:%M %p",
        "%m/%d/%y, %I:%M:%S %p",
        "%m/%d/%Y, %I:%M:%S %p",
        # With dash separator
        "%m-%d-%Y %I:%M %p",
        "%m-%d-%y %I:%M %p",
        # "Mar 14, 2026 09:13:06 AM" — Magento admin export format
        "%b %d, %Y %I:%M:%S %p",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %H:%M:%S",
        "%b %d, %Y %H:%M",
        # "13 Mar 2026 09:15:00"
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M",
    )

    for fmt in formats:
        try:
            dt = datetime.strptime(v, fmt)
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc)
            local_dt = ATHENS_TZ.localize(dt)
            return local_dt.astimezone(timezone.utc)
        except Exception:
            pass

    try:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        local_dt = ATHENS_TZ.localize(dt)
        return local_dt.astimezone(timezone.utc)
    except Exception:
        pass

    logger.warning("Could not parse date value: %r", v)
    return None


def preview_csv(filepath: str, max_rows: int = 5) -> dict:
    """Return headers and raw sample rows without importing — for diagnosing format issues."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(filepath)
    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        sample = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            sample.append(dict(row))
    # Also test date parsing on the first row
    parse_test = {}
    if sample:
        for col in ("Last Login", "Last Logout"):
            val = sample[0].get(col, "")
            parsed = parse_dt_local_athens_to_utc(val)
            parse_test[col] = {"raw": val, "parsed_utc": parsed.isoformat() if parsed else None}
    return {"headers": headers, "sample_rows": sample, "parse_test": parse_test}


def import_magento_login_log_csv(filepath: str) -> dict:
    if not os.path.exists(filepath):
        raise FileNotFoundError(filepath)

    mapping = {}
    rows = PSCustomer.query.with_entities(PSCustomer.customer_code_365, PSCustomer.customer_code_secondary).all()
    for code, mid in rows:
        if mid:
            try:
                mapping[int(mid)] = code
            except (ValueError, TypeError):
                pass

    updated, skipped, errors = 0, 0, 0
    fname = os.path.basename(filepath)

    H_LOG_ID = "Log ID"
    H_CUSTOMER_ID = "Customer ID"
    H_FIRST = "First Name"
    H_LAST = "Last Name"
    H_EMAIL = "Email"
    H_PS365 = "PS365 Code"
    H_LOGIN = "Last Login"
    H_LOGOUT = "Last Logout"

    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for req in (H_LOG_ID, H_CUSTOMER_ID, H_LOGIN):
            if req not in (reader.fieldnames or []):
                raise ValueError(f"Missing required column '{req}'. Found: {reader.fieldnames}")

        for r in reader:
            try:
                raw_log_id = (r.get(H_LOG_ID) or "").strip()
                raw_customer_id = (r.get(H_CUSTOMER_ID) or "").strip()
                if not raw_log_id.isdigit() or not raw_customer_id.isdigit():
                    skipped += 1
                    continue

                log_id = int(raw_log_id)
                magento_customer_id = int(raw_customer_id)

                csv_code = (r.get(H_PS365) or "").strip()
                customer_code_365 = csv_code or mapping.get(magento_customer_id)

                obj = MagentoCustomerLoginLog.query.get(log_id)
                if not obj:
                    obj = MagentoCustomerLoginLog(log_id=log_id)
                    db.session.add(obj)

                obj.magento_customer_id = magento_customer_id
                obj.customer_code_365 = customer_code_365
                obj.email = (r.get(H_EMAIL) or "").strip() or None
                obj.first_name = (r.get(H_FIRST) or "").strip() or None
                obj.last_name = (r.get(H_LAST) or "").strip() or None
                obj.last_login_at = parse_dt_local_athens_to_utc(r.get(H_LOGIN))
                obj.last_logout_at = parse_dt_local_athens_to_utc(r.get(H_LOGOUT))
                obj.imported_at = datetime.now(timezone.utc)
                obj.source_filename = fname

                if customer_code_365:
                    # Use merge() so SQLAlchemy handles INSERT-or-UPDATE
                    # without a UniqueViolation when the row already exists.
                    cur = MagentoCustomerLastLoginCurrent.query.get(customer_code_365)
                    if cur is None:
                        cur = MagentoCustomerLastLoginCurrent(customer_code_365=customer_code_365)

                    incoming_login = obj.last_login_at
                    existing_login = cur.last_login_at

                    if (existing_login is None) or (incoming_login and incoming_login >= existing_login):
                        cur.magento_customer_id = magento_customer_id
                        cur.last_login_at = obj.last_login_at
                        cur.last_logout_at = obj.last_logout_at
                        cur.email = obj.email
                        cur.first_name = obj.first_name
                        cur.last_name = obj.last_name
                        cur.imported_at = obj.imported_at
                        cur.source_filename = obj.source_filename

                    db.session.merge(cur)

                db.session.flush()
                updated += 1
            except Exception as e:
                logger.warning("Error importing login log row: %s", e)
                db.session.rollback()
                errors += 1

    db.session.commit()
    logger.info("Magento login log import: updated=%d skipped=%d errors=%d file=%s", updated, skipped, errors, fname)
    return {"updated": updated, "skipped": skipped, "errors": errors, "file": fname}
