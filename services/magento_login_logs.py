import os
import json
import time
import logging
import requests
from datetime import datetime
from integrations.magento_rest_oauth import magento_rest_get

logger = logging.getLogger(__name__)

_cache = {}
CACHE_TTL = 90

_working_endpoint = None


def _get_base_url():
    base_url = os.getenv('MAGENTO_BASE_URL', '').rstrip('/')
    if base_url.endswith('/graphql'):
        base_url = base_url[:-len('/graphql')]
    return base_url


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


def _extract_rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ["items", "data", "logs", "result", "activitylog", "activitylogs",
                     "collection", "records", "rows"]:
            if key in data and isinstance(data[key], list):
                return data[key]
    return None


def _simi_request(method, path, json_body=None, params=None):
    base_url = _get_base_url()
    url = f"{base_url}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    label = f"Simi {method} {path}"
    logger.info("Trying %s", label)

    try:
        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        elif method == "POST":
            resp = requests.post(url, headers=headers, json=json_body or {}, timeout=30)
        else:
            return None, None

        logger.info("%s → %d (%d bytes)", label, resp.status_code, len(resp.text))

        if resp.status_code == 404:
            return resp.status_code, None
        if resp.status_code not in (200, 201):
            logger.info("%s response: %s", label, resp.text[:300])
            return resp.status_code, None

        try:
            data = json.loads(resp.text)
        except (json.JSONDecodeError, TypeError):
            logger.warning("%s returned non-JSON", label)
            return resp.status_code, None

        if isinstance(data, dict) and "errors" in data:
            logger.info("%s returned errors: %s", label, data["errors"])
            return resp.status_code, None

        rows = _extract_rows(data)
        if rows is not None:
            logger.info("%s found %d rows", label, len(rows))
            return resp.status_code, rows

        logger.info("%s returned unexpected shape: %s", label, str(data)[:300])
        return resp.status_code, data

    except requests.Timeout:
        logger.warning("Timeout on %s", label)
        return None, None
    except Exception as e:
        logger.warning("Error on %s: %s", label, e)
        return None, None


def _try_oauth(path, params=None):
    logger.info("Trying OAuth: %s", path)
    try:
        status_code, response_text = magento_rest_get(path, params=params, timeout=30)
        if status_code == 404:
            return None
        if status_code != 200:
            return None
        data = json.loads(response_text)
        rows = _extract_rows(data)
        if rows is not None:
            logger.info("OAuth %s found %d rows", path, len(rows))
            return rows
        return None
    except Exception as e:
        logger.warning("OAuth error on %s: %s", path, e)
        return None


def fetch_activity_logs():
    global _working_endpoint
    cached = _get_cached("all_logs")
    if cached is not None:
        logger.debug("Returning cached activity logs (%d rows)", len(cached))
        return cached

    if _working_endpoint:
        method, path, body, params = _working_endpoint
        logger.info("Using known working endpoint: %s %s", method, path)
        if method == "OAUTH":
            rows = _try_oauth(path, params)
        else:
            _, rows = _simi_request(method, path, json_body=body, params=params)
        if rows is not None:
            _set_cache("all_logs", rows)
            return rows
        _working_endpoint = None

    tried = []

    simi_v2_base = "/simiconnector/rest/v2"
    simi_probes = [
        ("GET",  f"{simi_v2_base}/activitylogs/index", None, None),
        ("POST", f"{simi_v2_base}/activitylogs", None, None),
        ("POST", f"{simi_v2_base}/activitylogs", {"resource": "activitylog"}, None),
        ("POST", f"{simi_v2_base}/activitylogs", {"method": "index"}, None),
        ("POST", f"{simi_v2_base}/activitylogs/index", None, None),
        ("GET",  f"{simi_v2_base}/activitylogs", None, {"method": "index"}),
        ("GET",  f"{simi_v2_base}/activitylogs", None, {"resource": "activitylog", "method": "index"}),
        ("POST", f"{simi_v2_base}/activitylog", None, None),
        ("POST", f"{simi_v2_base}/activitylog", {"method": "index"}, None),
        ("POST", f"{simi_v2_base}/activitylog/index", None, None),
        ("GET",  f"{simi_v2_base}/activitylog/index", None, None),
    ]

    for method, path, body, params in simi_probes:
        tried.append(f"{method}:{path}")
        status, rows = _simi_request(method, path, json_body=body, params=params)
        if rows is not None and isinstance(rows, list):
            _working_endpoint = (method, path, body, params)
            _set_cache("all_logs", rows)
            return rows

    oauth_paths = [
        "/rest/V1/activitylog",
        "/rest/V1/bss/activitylog",
    ]
    for path in oauth_paths:
        tried.append(f"OAUTH:{path}")
        rows = _try_oauth(path)
        if rows is not None:
            _working_endpoint = ("OAUTH", path, None, None)
            _set_cache("all_logs", rows)
            return rows

    raise RuntimeError(
        "Could not reach the activitylog endpoint. "
        "Tried: " + ", ".join(tried) + ". "
        "The Simi connector at /simiconnector/rest/v2/activitylogs responds but requires the correct method. "
        "Please ask your Magento developer for the exact Simi API call pattern "
        "(HTTP method, URL, and any required parameters)."
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
