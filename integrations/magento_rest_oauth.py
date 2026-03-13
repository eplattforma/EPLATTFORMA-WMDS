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

    headers = {"Accept": "application/json"}

    last_status = 0
    last_text = ""

    for attempt in range(1, MAX_RETRIES + 1):
        oauth = _build_session()
        logger.debug(f"Magento GET {path} (attempt {attempt})")
        resp = oauth.get(url, headers=headers, timeout=timeout)
        last_status = resp.status_code
        last_text = resp.text

        if resp.status_code != 401:
            if resp.status_code != 200:
                logger.warning(f"Magento {resp.status_code}: {resp.text[:500]}")
            return resp.status_code, resp.text

        logger.warning(f"Magento 401 on attempt {attempt}: {resp.text[:200]}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY * attempt)

    return last_status, last_text
