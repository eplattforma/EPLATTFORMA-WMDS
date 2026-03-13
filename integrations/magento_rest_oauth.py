import os
import logging
from requests_oauthlib import OAuth1Session

logger = logging.getLogger(__name__)


def magento_rest_get(path: str, params: dict = None, timeout: int = 30) -> tuple[int, str]:
    base_url = os.getenv('MAGENTO_BASE_URL', '').rstrip('/')
    if base_url.endswith('/graphql'):
        base_url = base_url[:-len('/graphql')]
    if not base_url:
        raise ValueError("MAGENTO_BASE_URL environment variable not set")

    consumer_key = os.getenv('M2_CONSUMER_KEY')
    consumer_secret = os.getenv('M2_CONSUMER_SECRET')
    access_token = os.getenv('M2_ACCESS_TOKEN')
    access_token_secret = os.getenv('M2_ACCESS_TOKEN_SECRET')

    if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
        raise ValueError("Missing Magento OAuth credentials")

    url = f"{base_url}{path}"

    oauth = OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    logger.debug(f"Magento GET {url} params={list((params or {}).keys())}")
    resp = oauth.get(url, params=params, headers=headers, timeout=timeout)
    logger.debug(f"Magento response: {resp.status_code}")
    return resp.status_code, resp.text
