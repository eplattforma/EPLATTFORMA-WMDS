import os
import requests
from requests_oauthlib import OAuth1Session
from datetime import datetime, timedelta


def magento_rest_get(path: str, timeout: int = 30) -> tuple[int, str]:
    """
    Call Magento REST API using OAuth1.
    
    Args:
        path: REST path (e.g., '/rest/V1/carts/search')
        timeout: Request timeout in seconds
    
    Returns:
        (status_code, response_text)
    
    Raises:
        ValueError: If Magento credentials are missing
        requests.RequestException: On network/connection errors
    """
    base_url = os.getenv('MAGENTO_BASE_URL')
    if not base_url:
        raise ValueError("MAGENTO_BASE_URL environment variable not set")
    
    consumer_key = os.getenv('M2_CONSUMER_KEY')
    consumer_secret = os.getenv('M2_CONSUMER_SECRET')
    access_token = os.getenv('M2_ACCESS_TOKEN')
    access_token_secret = os.getenv('M2_ACCESS_TOKEN_SECRET')
    
    if not all([consumer_key, consumer_secret, access_token, access_token_secret]):
        raise ValueError("Missing Magento OAuth credentials (M2_CONSUMER_KEY, M2_CONSUMER_SECRET, M2_ACCESS_TOKEN, M2_ACCESS_TOKEN_SECRET)")
    
    url = f"{base_url}{path}"
    
    oauth = OAuth1Session(
        client_key=consumer_key,
        client_secret=consumer_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_token_secret,
    )
    
    resp = oauth.get(url, timeout=timeout)
    return resp.status_code, resp.text
