import json
import time
import logging
from datetime import datetime
from integrations.magento_rest_oauth import magento_rest_get

logger = logging.getLogger(__name__)

_cache = {}
CACHE_TTL = 90

CANDIDATE_PATHS = [
    "/rest/V1/activitylog",
    "/rest/V1/activitylog/search",
    "/rest/V1/bss/activitylog",
    "/rest/V1/bss-customerloginlogs/activitylog",
    "/rest/V1/customerloginlogs",
]


def _get_cached(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key, data):
    _cache[key] = {"data": data, "ts": time.time()}


def _parse_dt(v):
    if not v:
        return datetime.min
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(str(v), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.min


def fetch_activity_logs():
    cached = _get_cached("all_logs")
    if cached is not None:
        logger.debug("Returning cached activity logs (%d rows)", len(cached))
        return cached

    for path in CANDIDATE_PATHS:
        logger.info("Trying activitylog endpoint: %s", path)
        try:
            status_code, response_text = magento_rest_get(path, timeout=30)
        except Exception as e:
            logger.warning("Error calling %s: %s", path, e)
            continue

        if status_code == 404:
            logger.debug("Path %s returned 404, trying next", path)
            continue
        if status_code != 200:
            logger.warning("Path %s returned %d: %s", path, status_code, response_text[:300])
            continue

        try:
            data = json.loads(response_text)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Path %s returned invalid JSON", path)
            continue

        rows = data
        if isinstance(data, dict):
            for key in ["items", "data", "logs", "result"]:
                if key in data and isinstance(data[key], list):
                    rows = data[key]
                    break

        if isinstance(rows, list):
            logger.info("activitylog endpoint found: %s — returned %d rows", path, len(rows))
            _set_cache("all_logs", rows)
            return rows
        else:
            logger.warning("Path %s returned unexpected format: %s", path, type(data).__name__)

    search_path = "/rest/V1/activitylog"
    search_params = {
        "searchCriteria[pageSize]": "500",
        "searchCriteria[currentPage]": "1",
        "searchCriteria[sortOrders][0][field]": "last_login_at",
        "searchCriteria[sortOrders][0][direction]": "DESC",
    }
    logger.info("Trying searchCriteria approach on %s", search_path)
    try:
        status_code, response_text = magento_rest_get(search_path, params=search_params, timeout=30)
        if status_code == 200:
            data = json.loads(response_text)
            rows = data
            if isinstance(data, dict):
                for key in ["items", "data", "logs", "result"]:
                    if key in data and isinstance(data[key], list):
                        rows = data[key]
                        break
            if isinstance(rows, list):
                logger.info("searchCriteria approach worked — %d rows", len(rows))
                _set_cache("all_logs", rows)
                return rows
    except Exception as e:
        logger.warning("searchCriteria approach failed: %s", e)

    raise RuntimeError(
        "Could not reach the activitylog endpoint. "
        "Tried paths: " + ", ".join(CANDIDATE_PATHS) + " and searchCriteria variant. "
        "Please verify the Magento REST route for resource=activitylog."
    )


def get_customer_last_login(customer_id=None, email=None, ps365_code=None):
    cache_key = f"login:{customer_id}:{email}:{ps365_code}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    filters = []
    if customer_id is not None:
        filters.append(("customer_id", customer_id))
    if email is not None:
        filters.append(("email", email))
    if ps365_code is not None:
        filters.append(("ps365_code", ps365_code))

    logger.info("get_customer_last_login called with filters: %s", filters)

    rows = fetch_activity_logs()
    logger.info("Total activity log rows from Magento: %d", len(rows))

    def match(row):
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
        _set_cache(cache_key, None)
        return None

    filtered.sort(key=lambda r: _parse_dt(r.get("last_login_at")), reverse=True)
    latest = filtered[0]

    result = {
        "customer_id": latest.get("customer_id"),
        "email": latest.get("email"),
        "first_name": latest.get("first_name") or latest.get("firstname"),
        "last_name": latest.get("last_name") or latest.get("lastname"),
        "ps365_code": latest.get("ps365_code"),
        "last_login_at": latest.get("last_login_at"),
        "last_logout_at": latest.get("last_logout_at"),
    }

    logger.info("Found latest login for customer: %s at %s", result.get("email"), result.get("last_login_at"))
    _set_cache(cache_key, result)
    return result
