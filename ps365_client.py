import os
import time
import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Connection timeouts: (connect_timeout, read_timeout)
PS365_CONNECT_TIMEOUT = 10   # 10 seconds to establish connection
PS365_READ_TIMEOUT = 60      # 60 seconds to read response

# Retry configuration
PS365_MAX_RETRIES = 3
PS365_BACKOFF_FACTOR = 1.0   # Wait 1s, 2s, 4s between retries

# Create a session with retry logic
def _get_session():
    """Create a requests session with retry logic and connection pooling."""
    session = requests.Session()
    retry_strategy = Retry(
        total=PS365_MAX_RETRIES,
        backoff_factor=PS365_BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False  # Don't raise, we'll handle it ourselves
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=5, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

# Reusable session
_session = None

def get_ps365_session():
    """Get or create the PS365 session."""
    global _session
    if _session is None:
        _session = _get_session()
    return _session


def call_ps365(endpoint: str, payload: dict | None = None, method: str = "POST"):
    """
    Generic call to Powersoft365 API with retry logic and proper timeouts.
    Supports both GET and POST methods.
    
    Args:
        endpoint: API endpoint (e.g., 'list_items', 'list_brands')
        payload: Request payload (for POST) or query params (for GET)
        method: HTTP method ('GET' or 'POST')
    """
    start_time = time.time()
    
    # Read token fresh each time to pick up runtime changes
    ps365_token = os.getenv("PS365_TOKEN", "")
    ps365_base_url = os.getenv("PS365_BASE_URL", "https://api.powersoft365.com")
    
    logger.info(f"[PS365 CLIENT] Token present: {bool(ps365_token)}, length: {len(ps365_token) if ps365_token else 0}")
    logger.debug(f"[PS365 CLIENT] Base URL: {ps365_base_url}")
    
    if not ps365_token:
        raise ValueError("PS365_TOKEN environment variable not set")
    
    base_url = ps365_base_url.rstrip("/")
    url = f"{base_url}/{endpoint}"
    
    logger.info(f"[PS365 CLIENT] Calling {method} {url}")
    
    session = get_ps365_session()
    timeout = (PS365_CONNECT_TIMEOUT, PS365_READ_TIMEOUT)
    
    try:
        if method.upper() == "GET":
            # For GET requests, pass token as query parameter
            params = payload or {}
            params["token"] = ps365_token
            resp = session.get(url, params=params, timeout=timeout)
        else:
            # For POST requests, pass token in JSON body
            data = {
                "api_credentials": {
                    "token": ps365_token
                }
            }
            data.update(payload or {})
            resp = session.post(url, json=data, timeout=timeout)
    except requests.exceptions.Timeout as e:
        elapsed = time.time() - start_time
        logger.error(f"[PS365 CLIENT] Timeout after {elapsed:.1f}s calling {endpoint}: {e}")
        raise ValueError(f"PS365 API timeout after {elapsed:.1f}s - the server took too long to respond") from e
    except requests.exceptions.ConnectionError as e:
        elapsed = time.time() - start_time
        logger.error(f"[PS365 CLIENT] Connection error after {elapsed:.1f}s calling {endpoint}: {e}")
        raise ValueError(f"PS365 API connection failed - please check network connectivity") from e
    
    elapsed = time.time() - start_time
    
    # Check for non-JSON response before parsing
    content_type = resp.headers.get('Content-Type', '')
    logger.info(f"[PS365 CLIENT] Response status: {resp.status_code}, Content-Type: {content_type}, Time: {elapsed:.1f}s")
    
    if resp.status_code != 200:
        logger.error(f"[PS365 CLIENT] Error response: {resp.text[:500]}")
        resp.raise_for_status()
    
    if 'application/json' not in content_type:
        logger.error(f"[PS365 CLIENT] Non-JSON response: {resp.text[:500]}")
        raise ValueError(f"PS365 API returned non-JSON response (Content-Type: {content_type}). Response: {resp.text[:200]}")
    
    try:
        return resp.json()
    except Exception as e:
        logger.error(f"[PS365 CLIENT] JSON parse error: {resp.text[:500]}")
        raise ValueError(f"PS365 API returned invalid JSON: {resp.text[:200]}") from e
