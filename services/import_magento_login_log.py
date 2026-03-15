import csv
import os
import logging
from datetime import datetime, timezone
import pytz
from app import db
from models import MagentoCustomerLoginLog, MagentoCustomerLastLoginCurrent, PSCustomer

logger = logging.getLogger(__name__)

ATHENS_TZ = pytz.timezone("Europe/Athens")

BATCH_SIZE = 100


def parse_dt_local_athens_to_utc(value: str):
    if not value:
        return None
    v = value.strip()

    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
        "%m/%d/%y, %I:%M %p",
        "%m/%d/%Y, %I:%M %p",
        "%m/%d/%y, %I:%M:%S %p",
        "%m/%d/%Y, %I:%M:%S %p",
        "%m-%d-%Y %I:%M %p",
        "%m-%d-%y %I:%M %p",
        "%b %d, %Y %I:%M:%S %p",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %H:%M:%S",
        "%b %d, %Y %H:%M",
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

    parsed_rows = []
    with open(filepath, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for req in (H_LOG_ID, H_CUSTOMER_ID, H_LOGIN):
            if req not in (reader.fieldnames or []):
                raise ValueError(f"Missing required column '{req}'. Found: {reader.fieldnames}")

        for r in reader:
            raw_log_id = (r.get(H_LOG_ID) or "").strip()
            raw_customer_id = (r.get(H_CUSTOMER_ID) or "").strip()
            if not raw_log_id.isdigit() or not raw_customer_id.isdigit():
                skipped += 1
                continue

            parsed_rows.append({
                "log_id": int(raw_log_id),
                "magento_customer_id": int(raw_customer_id),
                "customer_code_365_csv": (r.get(H_PS365) or "").strip(),
                "email": (r.get(H_EMAIL) or "").strip() or None,
                "first_name": (r.get(H_FIRST) or "").strip() or None,
                "last_name": (r.get(H_LAST) or "").strip() or None,
                "last_login_at": parse_dt_local_athens_to_utc(r.get(H_LOGIN)),
                "last_logout_at": parse_dt_local_athens_to_utc(r.get(H_LOGOUT)),
            })

    now_utc = datetime.now(timezone.utc)
    existing_log_ids = set()
    if parsed_rows:
        all_log_ids = [pr["log_id"] for pr in parsed_rows]
        for chunk_start in range(0, len(all_log_ids), 500):
            chunk = all_log_ids[chunk_start:chunk_start + 500]
            found = db.session.query(MagentoCustomerLoginLog.log_id).filter(
                MagentoCustomerLoginLog.log_id.in_(chunk)
            ).all()
            existing_log_ids.update(r[0] for r in found)

    existing_current = {}
    if parsed_rows:
        all_codes = list(set(
            pr.get("customer_code_365_csv") or mapping.get(pr["magento_customer_id"]) or ""
            for pr in parsed_rows
        ))
        all_codes = [c for c in all_codes if c]
        for chunk_start in range(0, len(all_codes), 500):
            chunk = all_codes[chunk_start:chunk_start + 500]
            found = MagentoCustomerLastLoginCurrent.query.filter(
                MagentoCustomerLastLoginCurrent.customer_code_365.in_(chunk)
            ).all()
            for row in found:
                existing_current[row.customer_code_365] = row

    batch_count = 0
    for pr in parsed_rows:
        try:
            log_id = pr["log_id"]
            magento_customer_id = pr["magento_customer_id"]
            customer_code_365 = pr["customer_code_365_csv"] or mapping.get(magento_customer_id)

            if log_id in existing_log_ids:
                obj = MagentoCustomerLoginLog.query.get(log_id)
            else:
                obj = MagentoCustomerLoginLog(log_id=log_id)
                db.session.add(obj)
                existing_log_ids.add(log_id)

            obj.magento_customer_id = magento_customer_id
            obj.customer_code_365 = customer_code_365
            obj.email = pr["email"]
            obj.first_name = pr["first_name"]
            obj.last_name = pr["last_name"]
            obj.last_login_at = pr["last_login_at"]
            obj.last_logout_at = pr["last_logout_at"]
            obj.imported_at = now_utc
            obj.source_filename = fname

            if customer_code_365:
                cur = existing_current.get(customer_code_365)
                if cur is None:
                    cur = MagentoCustomerLastLoginCurrent(customer_code_365=customer_code_365)
                    existing_current[customer_code_365] = cur

                incoming_login = pr["last_login_at"]
                existing_login = cur.last_login_at

                if (existing_login is None) or (incoming_login and incoming_login >= existing_login):
                    cur.magento_customer_id = magento_customer_id
                    cur.last_login_at = pr["last_login_at"]
                    cur.last_logout_at = pr["last_logout_at"]
                    cur.email = pr["email"]
                    cur.first_name = pr["first_name"]
                    cur.last_name = pr["last_name"]
                    cur.imported_at = now_utc
                    cur.source_filename = fname

                db.session.merge(cur)

            updated += 1
            batch_count += 1

            if batch_count >= BATCH_SIZE:
                db.session.commit()
                batch_count = 0

        except Exception as e:
            logger.warning("Error importing login log row %s: %s", pr.get("log_id"), e)
            db.session.rollback()
            errors += 1
            batch_count = 0

    if batch_count > 0:
        try:
            db.session.commit()
        except Exception as e:
            logger.error("Final batch commit error: %s", e)
            db.session.rollback()
            errors += batch_count
            updated -= batch_count

    logger.info("Magento login log import: updated=%d skipped=%d errors=%d file=%s", updated, skipped, errors, fname)
    return {"updated": updated, "skipped": skipped, "errors": errors, "file": fname}
