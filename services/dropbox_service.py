import os
import logging
import time
import requests
from datetime import datetime, timedelta
from app import db
from models import ExternalAuthCredential, ExternalFileSyncLog
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)

PROVIDER = 'dropbox'
DROPBOX_AUTH_URL = 'https://www.dropbox.com/oauth2/authorize'
DROPBOX_TOKEN_URL = 'https://api.content.dropboxapi.com/2/oauth2/token'
DROPBOX_TOKEN_URL_API = 'https://api.dropboxapi.com/oauth2/token'
DROPBOX_ACCOUNT_URL = 'https://api.dropboxapi.com/2/users/get_current_account'
DROPBOX_METADATA_URL = 'https://api.dropboxapi.com/2/files/get_metadata'
DROPBOX_DOWNLOAD_URL = 'https://content.dropboxapi.com/2/files/download'

TOKEN_EXPIRY_BUFFER = timedelta(minutes=5)
MAX_RETRIES = 2
RETRY_BACKOFF = [1, 3]


def _get_config():
    app_key = os.environ.get('DROPBOX_APP_KEY', '').strip()
    app_secret = os.environ.get('DROPBOX_APP_SECRET', '').strip()
    redirect_uri = os.environ.get('DROPBOX_REDIRECT_URI', '').strip()
    file_path = os.environ.get('DROPBOX_FILE_PATH', '').strip()
    return {
        'app_key': app_key,
        'app_secret': app_secret,
        'redirect_uri': redirect_uri,
        'file_path': file_path,
        'configured': bool(app_key and app_secret),
    }


def get_dropbox_credentials():
    return db.session.query(ExternalAuthCredential).filter_by(provider=PROVIDER).first()


def build_dropbox_authorize_url(state_token):
    config = _get_config()
    if not config['configured']:
        raise ValueError("Dropbox app key/secret not configured")
    params = {
        'client_id': config['app_key'],
        'response_type': 'code',
        'token_access_type': 'offline',
        'state': state_token,
    }
    if config['redirect_uri']:
        params['redirect_uri'] = config['redirect_uri']
    qs = '&'.join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return f"{DROPBOX_AUTH_URL}?{qs}"


def exchange_code_for_tokens(code):
    config = _get_config()
    if not config['configured']:
        raise ValueError("Dropbox app key/secret not configured")

    data = {
        'code': code,
        'grant_type': 'authorization_code',
        'client_id': config['app_key'],
        'client_secret': config['app_secret'],
    }
    if config['redirect_uri']:
        data['redirect_uri'] = config['redirect_uri']

    logger.info("Exchanging authorization code for tokens")
    resp = requests.post(DROPBOX_TOKEN_URL_API, data=data, timeout=30)
    if resp.status_code != 200:
        logger.error(f"Token exchange failed: {resp.status_code} {resp.text}")
        raise ValueError(f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    now = get_utc_now()

    cred = get_dropbox_credentials()
    if not cred:
        cred = ExternalAuthCredential(provider=PROVIDER)
        db.session.add(cred)

    cred.refresh_token = token_data['refresh_token']
    cred.access_token = token_data['access_token']
    expires_in = token_data.get('expires_in', 14400)
    cred.access_token_expires_at = now + timedelta(seconds=expires_in)
    cred.scope_text = token_data.get('scope', '')
    cred.dropbox_account_id = token_data.get('account_id', '')
    cred.status = 'active'
    cred.last_auth_at = now
    cred.last_error = None

    account_info = _fetch_account_info(token_data['access_token'])
    if account_info:
        cred.dropbox_email = account_info.get('email', '')
        cred.account_label = account_info.get('name', {}).get('display_name', '')

    db.session.commit()
    logger.info(f"Dropbox connected: {cred.dropbox_email or cred.dropbox_account_id}")
    return cred


def _fetch_account_info(access_token):
    try:
        resp = requests.post(
            DROPBOX_ACCOUNT_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=15
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"Could not fetch Dropbox account info: {e}")
    return None


def refresh_dropbox_access_token(force=False):
    config = _get_config()
    cred = get_dropbox_credentials()
    if not cred or cred.status != 'active':
        raise ValueError("No active Dropbox credential found")

    now = get_utc_now()
    if not force and cred.access_token and cred.access_token_expires_at:
        if cred.access_token_expires_at > now + TOKEN_EXPIRY_BUFFER:
            return cred.access_token

    if not cred.refresh_token:
        cred.status = 'auth_error'
        cred.last_error = 'No refresh token available'
        db.session.commit()
        raise ValueError("No refresh token — reconnect required")

    logger.info("Refreshing Dropbox access token")
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': cred.refresh_token,
        'client_id': config['app_key'],
        'client_secret': config['app_secret'],
    }

    resp = requests.post(DROPBOX_TOKEN_URL_API, data=data, timeout=30)
    if resp.status_code != 200:
        error_msg = f"Token refresh failed: {resp.status_code}"
        logger.error(f"{error_msg} — {resp.text}")
        cred.status = 'auth_error'
        cred.last_error = error_msg
        db.session.commit()
        raise ValueError(f"Token refresh failed — reconnect required")

    token_data = resp.json()
    cred.access_token = token_data['access_token']
    expires_in = token_data.get('expires_in', 14400)
    cred.access_token_expires_at = now + timedelta(seconds=expires_in)
    cred.last_refresh_at = now
    cred.last_error = None
    db.session.commit()
    logger.info("Dropbox access token refreshed successfully")
    return cred.access_token


def get_valid_dropbox_access_token():
    return refresh_dropbox_access_token(force=False)


def _dropbox_api_call(method, url, headers=None, **kwargs):
    token = get_valid_dropbox_access_token()
    hdrs = dict(headers or {})
    hdrs['Authorization'] = f'Bearer {token}'

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, headers=hdrs, timeout=60, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                logger.warning("Dropbox 401 — forcing token refresh and retrying")
                token = refresh_dropbox_access_token(force=True)
                hdrs['Authorization'] = f'Bearer {token}'
                continue
            return resp
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt] if attempt < len(RETRY_BACKOFF) else 3
                logger.warning(f"Dropbox request error (attempt {attempt+1}): {e}, retrying in {wait}s")
                time.sleep(wait)
            else:
                raise
    return resp


def get_dropbox_file_metadata(path):
    import json
    resp = _dropbox_api_call(
        'POST', DROPBOX_METADATA_URL,
        headers={'Content-Type': 'application/json'},
        json={'path': path}
    )
    if resp.status_code != 200:
        raise ValueError(f"Metadata fetch failed ({resp.status_code}): {resp.text}")
    return resp.json()


def download_dropbox_file_bytes(path):
    import json
    resp = _dropbox_api_call(
        'POST', DROPBOX_DOWNLOAD_URL,
        headers={'Dropbox-API-Arg': json.dumps({'path': path})}
    )
    if resp.status_code != 200:
        raise ValueError(f"Download failed ({resp.status_code}): {resp.text}")

    metadata = {}
    api_result = resp.headers.get('Dropbox-API-Result', '')
    if api_result:
        try:
            metadata = json.loads(api_result)
        except Exception:
            pass

    return resp.content, metadata


def get_dropbox_status():
    config = _get_config()
    cred = get_dropbox_credentials()

    last_sync = db.session.query(ExternalFileSyncLog).filter_by(
        provider=PROVIDER
    ).order_by(ExternalFileSyncLog.started_at.desc()).first()

    last_success = db.session.query(ExternalFileSyncLog).filter_by(
        provider=PROVIDER, status='success'
    ).order_by(ExternalFileSyncLog.started_at.desc()).first()

    recent_logs = db.session.query(ExternalFileSyncLog).filter_by(
        provider=PROVIDER
    ).order_by(ExternalFileSyncLog.started_at.desc()).limit(20).all()

    return {
        'configured': config['configured'],
        'file_path': config['file_path'],
        'connected': cred is not None and cred.status == 'active',
        'status': cred.status if cred else 'not_connected',
        'email': cred.dropbox_email if cred else None,
        'account_label': cred.account_label if cred else None,
        'last_auth_at': cred.last_auth_at if cred else None,
        'last_refresh_at': cred.last_refresh_at if cred else None,
        'last_error': cred.last_error if cred else None,
        'last_sync': {
            'status': last_sync.status if last_sync else None,
            'started_at': last_sync.started_at if last_sync else None,
            'finished_at': last_sync.finished_at if last_sync else None,
            'rows_imported': last_sync.rows_imported if last_sync else 0,
            'file_revision': last_sync.file_revision if last_sync else None,
            'file_modified_at': last_sync.file_modified_at if last_sync else None,
            'error_message': last_sync.error_message if last_sync else None,
        },
        'last_success': {
            'started_at': last_success.started_at if last_success else None,
            'rows_imported': last_success.rows_imported if last_success else 0,
            'file_revision': last_success.file_revision if last_success else None,
        },
        'sync_history': recent_logs,
    }


def sync_dropbox_file(file_processor=None):
    config = _get_config()
    file_path = config['file_path'] or '(not configured)'

    log = ExternalFileSyncLog(
        provider=PROVIDER,
        file_path=file_path,
        status='running',
        started_at=get_utc_now(),
    )
    db.session.add(log)
    db.session.commit()

    try:
        if not config['configured']:
            raise _SyncError("Dropbox app key/secret not configured in environment", 'config_error')
        if not config['file_path']:
            raise _SyncError("DROPBOX_FILE_PATH not configured", 'config_error')

        cred = get_dropbox_credentials()
        if not cred or cred.status != 'active':
            raise _SyncError("Dropbox not connected — please connect from the admin UI", 'auth_error')

        logger.info(f"Dropbox sync: fetching metadata for {file_path}")
        metadata = get_dropbox_file_metadata(file_path)
        log.file_name = metadata.get('name', '')
        log.file_revision = metadata.get('rev', '')
        modified_str = metadata.get('server_modified', '')
        if modified_str:
            try:
                log.file_modified_at = datetime.fromisoformat(modified_str.replace('Z', '+00:00'))
            except Exception:
                pass
        log.metadata_json = {
            'size': metadata.get('size'),
            'content_hash': metadata.get('content_hash'),
        }
        db.session.commit()

        logger.info(f"Dropbox sync: downloading {file_path}")
        file_bytes, dl_metadata = download_dropbox_file_bytes(file_path)
        logger.info(f"Dropbox sync: downloaded {len(file_bytes)} bytes")

        rows_imported = 0
        if file_processor:
            rows_imported = file_processor(file_bytes, metadata)
        else:
            rows_imported = _default_stock_processor(file_bytes)

        log.status = 'success'
        log.rows_imported = rows_imported
        log.finished_at = get_utc_now()
        db.session.commit()
        logger.info(f"Dropbox sync complete: {rows_imported} rows imported")
        return log

    except _SyncError as e:
        log.status = e.sync_status
        log.error_message = str(e)
        log.finished_at = get_utc_now()
        db.session.commit()
        logger.error(f"Dropbox sync failed: {e}")
        raise ValueError(str(e))

    except ValueError as e:
        error_msg = str(e)
        if 'auth' in error_msg.lower() or '401' in error_msg or 'token' in error_msg.lower():
            log.status = 'auth_error'
        elif 'not_found' in error_msg.lower() or '409' in error_msg:
            log.status = 'download_error'
        else:
            log.status = 'download_error'
        log.error_message = error_msg
        log.finished_at = get_utc_now()
        db.session.commit()
        logger.error(f"Dropbox sync failed: {error_msg}")
        raise

    except Exception as e:
        log.status = 'download_error'
        log.error_message = str(e)
        log.finished_at = get_utc_now()
        db.session.commit()
        logger.error(f"Dropbox sync error: {e}")
        raise


class _SyncError(Exception):
    def __init__(self, message, sync_status='download_error'):
        super().__init__(message)
        self.sync_status = sync_status


def _default_stock_processor(file_bytes):
    import openpyxl
    from io import BytesIO
    from sqlalchemy import text

    workbook = openpyxl.load_workbook(BytesIO(file_bytes))
    worksheet = workbook.active
    if worksheet is None:
        raise ValueError("No worksheet found in workbook")

    rows = list(worksheet.iter_rows(min_row=5, values_only=True))
    records = []

    for row in rows:
        if len(row) < 7:
            continue
        item_code = str(row[0]).strip() if row[0] else ''
        if not item_code:
            continue
        item_description = str(row[1]).strip() if row[1] else ''
        store_code = str(row[2]).strip() if row[2] else ''
        store_name = str(row[3]).strip() if row[3] else ''
        if store_code != '777':
            continue
        expiry_date = row[4] if row[4] else None
        if isinstance(expiry_date, str):
            try:
                expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d')
            except Exception:
                expiry_date = None
        stock_quantity = 0
        try:
            stock_quantity = float(row[6]) if row[6] else 0
        except (ValueError, TypeError):
            stock_quantity = 0

        records.append({
            'item_code': item_code,
            'item_description': item_description,
            'store_code': store_code,
            'store_name': store_name,
            'expiry_date': expiry_date,
            'stock_quantity': stock_quantity,
        })

    if not records:
        return 0

    db.session.execute(text("TRUNCATE TABLE stock_positions"))

    batch_size = 1000
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        from models import StockPosition
        db.session.bulk_insert_mappings(StockPosition, batch)

    db.session.commit()
    logger.info(f"Imported {len(records)} stock position records from Dropbox")
    return len(records)


def disconnect_dropbox():
    cred = get_dropbox_credentials()
    if cred:
        cred.status = 'disconnected'
        cred.access_token = None
        cred.access_token_expires_at = None
        cred.last_error = None
        db.session.commit()
        logger.info("Dropbox disconnected")
    return True


def test_dropbox_connection():
    config = _get_config()
    if not config['file_path']:
        return {'success': False, 'error': 'DROPBOX_FILE_PATH not configured'}

    cred = get_dropbox_credentials()
    if not cred or cred.status != 'active':
        return {'success': False, 'error': 'Dropbox not connected'}

    try:
        metadata = get_dropbox_file_metadata(config['file_path'])
        return {
            'success': True,
            'file_name': metadata.get('name', ''),
            'file_size': metadata.get('size', 0),
            'modified': metadata.get('server_modified', ''),
            'revision': metadata.get('rev', ''),
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}
