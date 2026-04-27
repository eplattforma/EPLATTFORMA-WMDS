import os
import time
import logging
import urllib.parse
from requests_oauthlib import OAuth1Session

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY = 0.5


def _build_session():
    consumer_key = os.getenv('M2_CONSUMER_KEY')
    consumer_secret = os.getenv('M2_CONSUMER_SECRET')
    access_token = os.getenv('M2_ACCESS_TOKEN')
    access_token_secret = os.getenv('M2_ACCESS_TOKEN_SECRET')

    if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
        raise ValueError("Missing Magento OAuth credentials")

    return OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )


def _build_query_string(params: dict) -> str:
    if not params:
        return ""
    parts = []
    for key, value in params.items():
        parts.append(f"{key}={urllib.parse.quote(str(value), safe='')}")
    return "&".join(parts)


def magento_rest_get(path: str, params: dict = None, timeout: int = 30) -> tuple[int, str]:
    base_url = os.getenv('MAGENTO_BASE_URL', '').rstrip('/')
    if base_url.endswith('/graphql'):
        base_url = base_url[:-len('/graphql')]
    if not base_url:
        raise ValueError("MAGENTO_BASE_URL environment variable not set")

    url = f"{base_url}{path}"
    if params:
        url = f"{url}?{_build_query_string(params)}"

    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    cf_header_name = os.getenv('MAGENTO_CF_BYPASS_HEADER_NAME')
    cf_header_value = os.getenv('MAGENTO_CF_BYPASS_HEADER_VALUE')
    if cf_header_name and cf_header_value:
        headers[cf_header_name] = cf_header_value

    last_status = 0
    last_text = ""

    for attempt in range(1, MAX_RETRIES + 1):
        oauth = _build_session()
        logger.debug(f"Magento GET {path} (attempt {attempt})")
        resp = oauth.get(url, headers=headers, timeout=timeout)
        last_status = resp.status_code
        last_text = resp.text

        if resp.status_code == 200:
            return resp.status_code, resp.text

        if resp.status_code in (401, 403, 429, 502, 503, 504):
            logger.warning(
                f"Magento {resp.status_code} on attempt {attempt}: {resp.text[:200]}"
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
                continue

        logger.warning(f"Magento {resp.status_code}: {resp.text[:500]}")
        return resp.status_code, resp.text

    return last_status, last_text
