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
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%Y-%m-%dT%H:%M:%S%z",
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
        return None


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
                    cur = MagentoCustomerLastLoginCurrent.query.get(customer_code_365)
                    if not cur:
                        cur = MagentoCustomerLastLoginCurrent(customer_code_365=customer_code_365)
                        db.session.add(cur)

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

                updated += 1
            except Exception as e:
                logger.warning("Error importing login log row: %s", e)
                errors += 1

    db.session.commit()
    logger.info("Magento login log import: updated=%d skipped=%d errors=%d file=%s", updated, skipped, errors, fname)
    return {"updated": updated, "skipped": skipped, "errors": errors, "file": fname}
