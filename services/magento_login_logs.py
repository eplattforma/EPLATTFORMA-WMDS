import os
import json
import time
import logging
import requests
from datetime import datetime
from typing import Optional, Any

logger = logging.getLogger(__name__)

_cache: dict = {}
CACHE_TTL = 90


def _get_base_url() -> str:
    base_url = os.getenv("MAGENTO_BASE_URL", "").rstrip("/")
    if base_url.endswith("/graphql"):
        base_url = base_url[: -len("/graphql")]
    return base_url


def _get_cached(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def _to_dt(value: Any) -> datetime:
    if not value:
        return datetime.min
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%b %d, %Y %I:%M:%S %p"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return datetime.min


def _parse_rows(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "logs", "activitylog", "activitylogs"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _simi_get(path: str) -> dict:
    base_url = _get_base_url()
    if not base_url:
        raise ValueError("MAGENTO_BASE_URL is not configured")

    url = f"{base_url}{path}"
    headers = {"Accept": "application/json"}

    logger.info("Simi GET %s (full URL: %s)", path, url)

    resp = requests.get(url, headers=headers, timeout=30)
    logger.info("Simi GET %s → status=%d, size=%d bytes", path, resp.status_code, len(resp.text))

    if resp.status_code != 200:
        logger.warning("Simi GET %s non-200 response: %s", path, resp.text[:500])

    return {
        "status_code": resp.status_code,
        "text": resp.text,
    }


def fetch_activity_logs() -> list:
    cached = _get_cached("all_logs")
    if cached is not None:
        logger.debug("Returning cached activity logs (%d rows)", len(cached))
        return cached

    simi_path = "/simiconnector/rest/v2/activitylog"

    result = _simi_get(simi_path)
    status = result["status_code"]
    text = result["text"]

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.error("Simi activitylog returned non-JSON (status=%d): %s", status, text[:300])
        raise RuntimeError(
            f"Simi activitylog returned non-JSON response (HTTP {status}). "
            f"Response preview: {text[:200]}"
        )

    if isinstance(data, dict) and "errors" in data:
        errors = data["errors"]
        logger.error(
            "Simi activitylog returned errors (status=%d): %s",
            status, json.dumps(errors),
        )
        raise RuntimeError(
            f"Simi activitylog endpoint returned error: {json.dumps(errors)}. "
            f"URL tested: {simi_path}. "
            f"HTTP status: {status}. "
            "The Magento developer needs to run: "
            "bin/magento setup:di:compile && bin/magento cache:flush"
        )

    if status != 200:
        logger.error("Simi activitylog HTTP %d: %s", status, text[:300])
        raise RuntimeError(
            f"Simi activitylog returned HTTP {status}. "
            f"URL tested: {simi_path}. "
            f"Response: {text[:200]}"
        )

    rows = _parse_rows(data)
    logger.info(
        "Simi activitylog success: response shape=%s, rows=%d",
        type(data).__name__, len(rows),
    )

    if not rows:
        logger.warning(
            "Simi activitylog returned 0 rows. Response keys: %s",
            list(data.keys()) if isinstance(data, dict) else "N/A (list)",
        )

    _set_cache("all_logs", rows)
    return rows


def get_customer_last_login(
    customer_id: Optional[int] = None,
    email: Optional[str] = None,
    ps365_code: Optional[str] = None,
) -> Optional[dict]:
    cache_key = f"login:{customer_id}:{email}:{ps365_code}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    logger.info(
        "get_customer_last_login called: customer_id=%s email=%s ps365_code=%s",
        customer_id, email, ps365_code,
    )

    rows = fetch_activity_logs()
    logger.info("Total activity log rows from Magento: %d", len(rows))

    def match(row: dict) -> bool:
        if customer_id is not None and str(row.get("customer_id", "")) != str(customer_id):
            return False
        if email is not None and (row.get("email") or "").strip().lower() != email.strip().lower():
            return False
        if ps365_code is not None and str(row.get("ps365_code") or "").strip() != str(ps365_code).strip():
            return False
        return True

    filtered = [r for r in rows if match(r)]
    logger.info("Matching rows after filter: %d", len(filtered))

    if not filtered:
        logger.info("No matching login log found for the given filters")
        _set_cache(cache_key, None)
        return None

    filtered.sort(key=lambda r: _to_dt(r.get("last_login_at")), reverse=True)
    latest = filtered[0]

    result = {
        "customer_id": latest.get("customer_id"),
        "email": latest.get("email"),
        "first_name": latest.get("first_name"),
        "last_name": latest.get("last_name"),
        "ps365_code": latest.get("ps365_code"),
        "last_login_at": latest.get("last_login_at"),
        "last_logout_at": latest.get("last_logout_at"),
    }

    logger.info(
        "Found latest login: email=%s ps365_code=%s last_login_at=%s",
        result.get("email"), result.get("ps365_code"), result.get("last_login_at"),
    )
    _set_cache(cache_key, result)
    return result
