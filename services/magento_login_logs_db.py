import os
import time
import logging
import pymysql
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_cache: dict = {}
CACHE_TTL = 90


def _get_cached(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def _get_connection():
    host = os.getenv("MAGENTO_DB_HOST", "")
    port = int(os.getenv("MAGENTO_DB_PORT", "3306"))
    db_name = os.getenv("MAGENTO_DB_NAME", "eplattforma_mage")
    user = os.getenv("MAGENTO_DB_USER", "")
    password = os.getenv("MAGENTO_DB_PASSWORD", "")

    if not host or not user:
        raise ValueError(
            "MAGENTO_DB_HOST and MAGENTO_DB_USER must be set in environment secrets"
        )

    logger.debug("Connecting to MySQL %s@%s:%d/%s", user, host, port, db_name)

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db_name,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _serialize_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        else:
            out[k] = v
    return out


def get_customer_last_login(
    customer_id: Optional[int] = None,
    email: Optional[str] = None,
    ps365_code: Optional[str] = None,
) -> Optional[dict]:
    if not any([customer_id, email, ps365_code]):
        raise ValueError("At least one filter required: customer_id, email, or ps365_code")

    cache_key = f"login_db:{customer_id}:{email}:{ps365_code}"
    cached = _get_cached(cache_key)
    if cached is not None:
        logger.debug("Returning cached login data for %s", cache_key)
        return cached

    conditions = []
    params = []

    if customer_id is not None:
        conditions.append("customer_id = %s")
        params.append(customer_id)
    if email is not None:
        conditions.append("LOWER(email) = LOWER(%s)")
        params.append(email.strip())
    if ps365_code is not None:
        conditions.append("ps365_code = %s")
        params.append(str(ps365_code).strip())

    where_clause = " AND ".join(conditions)

    sql = (
        "SELECT customer_id, email, first_name, last_name, "
        "ps365_code, last_login_at, last_logout_at "
        "FROM bss_customer_login_logs "
        f"WHERE {where_clause} "
        "ORDER BY last_login_at DESC "
        "LIMIT 1"
    )

    logger.info(
        "Querying bss_customer_login_logs: conditions=%s params=%s",
        conditions, params,
    )

    conn = None
    try:
        conn = _get_connection()
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()

        if row is None:
            logger.info("No matching login log found")
            _set_cache(cache_key, None)
            return None

        result = _serialize_row(row)
        logger.info(
            "Found login: email=%s ps365_code=%s last_login_at=%s",
            result.get("email"), result.get("ps365_code"), result.get("last_login_at"),
        )
        _set_cache(cache_key, result)
        return result

    except pymysql.OperationalError as e:
        code = e.args[0] if e.args else 0
        logger.error("MySQL operational error (code=%s): %s", code, e)
        if code in (1045, 1044):
            raise RuntimeError("MySQL access denied. Check MAGENTO_DB_USER and MAGENTO_DB_PASSWORD.") from e
        if code in (2003, 2006, 2013):
            raise RuntimeError(
                "Cannot connect to MySQL. Check MAGENTO_DB_HOST and ensure the DB allows remote access. "
                "The MySQL user may need to be granted access from Replit's IP, not just @localhost."
            ) from e
        raise RuntimeError(f"MySQL error: {e}") from e
    except pymysql.Error as e:
        logger.error("MySQL error: %s", e)
        raise RuntimeError(f"MySQL error: {e}") from e
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
