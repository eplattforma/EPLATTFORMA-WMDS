import os
import time
import uuid
import requests
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Context variable for correlation ID (thread-safe)
_correlation_id: ContextVar[str] = ContextVar('correlation_id', default='')


def get_correlation_id() -> str:
    """Get the current correlation ID, or generate one if not set."""
    cid = _correlation_id.get()
    if not cid:
        cid = f"ps365_{uuid.uuid4().hex[:12]}"
        _correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str | None = None) -> str:
    """Set a correlation ID for the current context. Returns the ID."""
    if cid is None:
        cid = f"ps365_{uuid.uuid4().hex[:12]}"
    _correlation_id.set(cid)
    return cid


def clear_correlation_id():
    """Clear the correlation ID for the current context."""
    _correlation_id.set('')

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


def call_ps365(endpoint: str, payload: dict | None = None, method: str = "POST", 
               page: int | None = None, date_from: str | None = None, date_to: str | None = None):
    """
    Generic call to Powersoft365 API with retry logic, proper timeouts, and structured logging.
    Supports both GET and POST methods.
    
    Args:
        endpoint: API endpoint (e.g., 'list_items', 'list_brands')
        payload: Request payload (for POST) or query params (for GET)
        method: HTTP method ('GET' or 'POST')
        page: Page number for paginated requests (for logging)
        date_from: Start date for date-range requests (for logging)
        date_to: End date for date-range requests (for logging)
    """
    start_time = time.time()
    cid = get_correlation_id()
    
    # Build structured log context
    log_context = {
        'correlation_id': cid,
        'endpoint': endpoint,
        'method': method
    }
    if page is not None:
        log_context['page'] = page
    if date_from:
        log_context['date_from'] = date_from
    if date_to:
        log_context['date_to'] = date_to
    
    # Read token fresh each time to pick up runtime changes
    ps365_token = os.getenv("PS365_TOKEN", "")
    ps365_base_url = os.getenv("PS365_BASE_URL", "https://api.powersoft365.com")
    
    logger.info(f"[PS365] [{cid}] Starting request: endpoint={endpoint}, page={page}, dates={date_from or 'N/A'} to {date_to or 'N/A'}")
    logger.debug(f"[PS365] [{cid}] Token present: {bool(ps365_token)}, Base URL: {ps365_base_url}")
    
    if not ps365_token:
        logger.error(f"[PS365] [{cid}] Missing PS365_TOKEN")
        raise ValueError("PS365_TOKEN environment variable not set")
    
    base_url = ps365_base_url.rstrip("/")
    url = f"{base_url}/{endpoint}"
    
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
        logger.error(f"[PS365] [{cid}] TIMEOUT after {elapsed:.1f}s: endpoint={endpoint}, page={page}")
        raise ValueError(f"PS365 API timeout after {elapsed:.1f}s - the server took too long to respond") from e
    except requests.exceptions.ConnectionError as e:
        elapsed = time.time() - start_time
        logger.error(f"[PS365] [{cid}] CONNECTION_ERROR after {elapsed:.1f}s: endpoint={endpoint}, error={str(e)[:100]}")
        raise ValueError(f"PS365 API connection failed - please check network connectivity") from e
    
    elapsed = time.time() - start_time
    
    # Check for non-JSON response before parsing
    content_type = resp.headers.get('Content-Type', '')
    
    if resp.status_code != 200:
        logger.error(f"[PS365] [{cid}] HTTP_{resp.status_code} after {elapsed:.1f}s: endpoint={endpoint}, page={page}, response={resp.text[:300]}")
        resp.raise_for_status()
    
    logger.info(f"[PS365] [{cid}] SUCCESS: endpoint={endpoint}, page={page}, status={resp.status_code}, time={elapsed:.1f}s")
    
    if 'application/json' not in content_type:
        logger.error(f"[PS365] [{cid}] INVALID_CONTENT_TYPE: {content_type}, response={resp.text[:200]}")
        raise ValueError(f"PS365 API returned non-JSON response (Content-Type: {content_type}). Response: {resp.text[:200]}")
    
    try:
        return resp.json()
    except Exception as e:
        logger.error(f"[PS365] [{cid}] JSON_PARSE_ERROR: {resp.text[:300]}")
        raise ValueError(f"PS365 API returned invalid JSON: {resp.text[:200]}") from e
