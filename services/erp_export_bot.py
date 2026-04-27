import os
import sys
import json
import shutil
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Serializes Chromium install across pre-warm thread and any cron/manual
# invocation that may race during the first run after deploy.
_PLAYWRIGHT_INSTALL_LOCK = threading.Lock()

AUTH_STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'erp_auth_state')
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'erp_exports')
SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'erp_screenshots')
AUTH_STATE_FILE = os.path.join(AUTH_STATE_DIR, 'state.json')
AUTH_STATE_META = os.path.join(AUTH_STATE_DIR, 'meta.json')

DEFAULT_NAV_TIMEOUT = 30000
DEFAULT_ACTION_TIMEOUT = 15000


def _get_config():
    return {
        'base_url': os.environ.get('ERP_BASE_URL', ''),
        'login_url': os.environ.get('ERP_LOGIN_URL', '') or os.environ.get('ERP_BASE_URL', '') or 'https://accpr.powersoft365.com/',
        'username': os.environ.get('ERP_USERNAME', ''),
        'password': os.environ.get('ERP_PASSWORD', ''),
        'headless': os.environ.get('ERP_HEADLESS', 'true').lower() in ('true', '1', 'yes'),
        'browser': os.environ.get('ERP_BROWSER', 'chromium'),
        'timezone': os.environ.get('ERP_TIMEZONE', 'Europe/Athens'),
        'download_dir': os.environ.get('ERP_EXPORT_DOWNLOAD_DIR', DOWNLOAD_DIR),
    }


def _ensure_dirs():
    for d in [AUTH_STATE_DIR, DOWNLOAD_DIR, SCREENSHOT_DIR]:
        os.makedirs(d, exist_ok=True)


def _auth_state_valid():
    if not os.path.exists(AUTH_STATE_FILE) or not os.path.exists(AUTH_STATE_META):
        return False
    try:
        with open(AUTH_STATE_META, 'r') as f:
            meta = json.load(f)
        saved_at = datetime.fromisoformat(meta.get('saved_at', ''))
        max_age_hours = meta.get('max_age_hours', 12)
        if datetime.utcnow() - saved_at > timedelta(hours=max_age_hours):
            logger.info("Auth state expired")
            return False
        return True
    except Exception as e:
        logger.warning(f"Auth state meta invalid: {e}")
        return False


def _save_auth_state_meta():
    with open(AUTH_STATE_META, 'w') as f:
        json.dump({
            'saved_at': datetime.utcnow().isoformat(),
            'max_age_hours': 12,
        }, f)


async def _capture_failure(page, step_name: str, run_id: int = None) -> dict:
    result = {}
    try:
        ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        prefix = f"run{run_id}_" if run_id else ""
        screenshot_name = f"{prefix}{step_name}_{ts}.png"
        screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_name)
        await page.screenshot(path=screenshot_path, full_page=True)
        result['screenshot_path'] = screenshot_path
        result['screenshot_name'] = screenshot_name
        logger.info(f"Failure screenshot saved: {screenshot_path}")
    except Exception as e:
        logger.warning(f"Failed to capture screenshot: {e}")

    try:
        result['url'] = page.url
        result['title'] = await page.title()
    except Exception:
        pass

    return result


async def _login(page, config: dict) -> bool:
    login_url = config['login_url']
    if not login_url:
        raise ValueError("ERP_BASE_URL or ERP_LOGIN_URL not configured")

    logger.info(f"Navigating to login page: {login_url}")
    await page.goto(login_url, wait_until='domcontentloaded', timeout=DEFAULT_NAV_TIMEOUT)

    username = config['username']
    password = config['password']
    if not username or not password:
        raise ValueError("ERP_USERNAME and ERP_PASSWORD must be set")

    logger.info("Filling login credentials")

    username_sel = '#ContentMasterMain_txtUserName'
    password_sel = '#ContentMasterMain_txtPassword'
    submit_sel = '#ContentMasterMain_btnLogin_CD'

    try:
        await page.wait_for_selector(username_sel, timeout=DEFAULT_ACTION_TIMEOUT)
        await page.fill(username_sel, username)
        await page.fill(password_sel, password)
        await page.click(submit_sel)
        await page.wait_for_load_state('networkidle', timeout=DEFAULT_NAV_TIMEOUT)
        await asyncio.sleep(2)
        post_url = (page.url or '').lower()
        login_form_still_present = await page.query_selector(username_sel) is not None
        if 'login.aspx' in post_url or login_form_still_present or 'restricted' not in post_url:
            raise RuntimeError(
                f"Login may have failed — landed on: {page.url} "
                f"(login_form_present={login_form_still_present})"
            )
        logger.info(f"Login successful, current URL: {page.url}")
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise

    return True


async def _ensure_authenticated(context, page, config: dict) -> bool:
    if _auth_state_valid():
        logger.info("Reusing saved auth state")
        return True

    logger.info("No valid auth state — performing fresh login")
    await _login(page, config)

    try:
        await context.storage_state(path=AUTH_STATE_FILE)
        _save_auth_state_meta()
        logger.info("Auth state saved for reuse")
    except Exception as e:
        logger.warning(f"Could not save auth state: {e}")

    return True


def _playwright_chromium_paths():
    """Return the list of glob patterns where Playwright may have stored Chromium.

    Playwright resolves the cache root in this order:
      1. PLAYWRIGHT_BROWSERS_PATH env var, treated as:
         - any non-empty path -> install there
         - "0" -> install package-locally next to playwright in site-packages
      2. ~/.cache/ms-playwright on Linux (the default)
    Replit's dev container also exposes ~/workspace/.cache/ms-playwright as a
    nested cache that has historically been used. We probe all of these to
    stay robust across dev and deployment.
    """
    candidates = []
    env_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '').strip()
    if env_path == '0':
        # Package-local install: <site-packages>/playwright/driver/.local-browsers
        try:
            import playwright as _pw_pkg  # type: ignore
            pw_dir = os.path.dirname(getattr(_pw_pkg, '__file__', '') or '')
            if pw_dir:
                candidates.append(os.path.join(pw_dir, 'driver', '.local-browsers'))
        except Exception:
            pass
    elif env_path:
        candidates.append(env_path)
    home = os.path.expanduser('~')
    candidates.append(os.path.join(home, '.cache', 'ms-playwright'))
    candidates.append(os.path.join(home, 'workspace', '.cache', 'ms-playwright'))
    seen = set()
    patterns = []
    for base in candidates:
        if not base or base in seen:
            continue
        seen.add(base)
        patterns.append(os.path.join(base, 'chromium-*', 'chrome-linux64', 'chrome'))
        patterns.append(os.path.join(base, 'chromium-*', 'chrome-linux', 'chrome'))
        patterns.append(os.path.join(base, 'chromium_headless_shell-*', 'chrome-linux64', 'headless_shell'))
    return patterns


def _chromium_binary_present() -> bool:
    import glob
    return any(glob.glob(p) for p in _playwright_chromium_paths())


def _ensure_playwright_browsers():
    """Install Playwright Chromium if missing.

    Serialised via _PLAYWRIGHT_INSTALL_LOCK so a pre-warm thread and an early
    cron firing don't both try to install at the same time. The install is
    idempotent and ~80MB, so we allow a generous timeout. We probe multiple
    cache locations because Playwright's resolved cache directory differs
    between Replit dev workspaces and deployments.
    """
    import subprocess
    if _chromium_binary_present():
        return
    with _PLAYWRIGHT_INSTALL_LOCK:
        # Re-check after acquiring the lock — another thread may have just
        # finished installing while we were waiting.
        if _chromium_binary_present():
            return
        logger.info(
            "Chromium not found in any known cache "
            f"(probed: {_playwright_chromium_paths()}). "
            f"Installing via '{sys.executable} -m playwright install chromium'..."
        )
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'playwright', 'install', 'chromium'],
                check=True, timeout=600, capture_output=True, text=True,
            )
            tail = (result.stdout or '')[-400:]
            logger.info(f"Chromium install command finished. stdout tail: {tail!r}")
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Chromium install exited with {e.returncode}. "
                f"stdout={(e.stdout or '')[-400:]!r} stderr={(e.stderr or '')[-400:]!r}"
            )
            raise RuntimeError(
                f"Chromium auto-install failed (rc={e.returncode}): "
                f"{(e.stderr or e.stdout or 'no output')[-200:]}"
            )
        except subprocess.TimeoutExpired as e:
            logger.error(f"Chromium install timed out after {e.timeout}s")
            raise RuntimeError(f"Chromium auto-install timed out after {e.timeout}s")
        except Exception as e:
            logger.error(f"Chromium install raised {type(e).__name__}: {e}", exc_info=True)
            raise RuntimeError(f"Chromium auto-install failed: {e}")

        if not _chromium_binary_present():
            raise RuntimeError(
                "Chromium install command completed but no chrome binary was found "
                f"in any of: {_playwright_chromium_paths()}"
            )
        logger.info("Chromium binary verified present after install.")


def prewarm_playwright_browsers_async():
    """Fire-and-forget background install of Chromium.

    Called from scheduler init at app boot in production so the 02:45 ERP cron
    doesn't pay first-time install cost (and any install failure is surfaced in
    boot logs instead of silently killing the cron before it can write its
    BotRunLog row).
    """
    import threading

    def _runner():
        try:
            if _chromium_binary_present():
                logger.info("Playwright pre-warm: Chromium already installed.")
                return
            logger.info("Playwright pre-warm: installing Chromium in background...")
            _ensure_playwright_browsers()
            logger.info("Playwright pre-warm: Chromium ready.")
        except Exception as e:
            logger.error(f"Playwright pre-warm failed: {e}", exc_info=True)

    t = threading.Thread(target=_runner, name='playwright-prewarm', daemon=True)
    t.start()


async def run_export(export_name: str, params: dict = None, triggered_by: str = 'manual') -> dict:
    # IMPORTANT: create the BotRunLog row BEFORE any step that can fail (dirs,
    # Chromium install, flow lookup). Otherwise a scheduler-triggered failure
    # leaves no audit trail and the cron looks like it never fired. This is
    # what we observed in production: zero rows with triggered_by='scheduler'.
    from app import app, db
    from models import BotRunLog

    run_id = None
    with app.app_context():
        run_log = BotRunLog(
            bot_name='erp_export',
            export_name=export_name,
            started_at=datetime.utcnow(),
            status='running',
            triggered_by=triggered_by,
        )
        db.session.add(run_log)
        db.session.commit()
        run_id = run_log.id
        logger.info(f"[bot:{run_id}] Starting export: {export_name} (triggered_by={triggered_by})")

    result = {
        'run_id': run_id,
        'export_name': export_name,
        'status': 'failed',
    }
    page = None
    browser = None
    try:
        from services.erp_export_flows import get_flow

        _ensure_dirs()
        _ensure_playwright_browsers()
        config = _get_config()
        flow = get_flow(export_name)

        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            download_path = config['download_dir']
            os.makedirs(download_path, exist_ok=True)

            launch_args = {
                'headless': True,
                'args': [
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                ],
            }

            browser_type = getattr(pw, config['browser'], pw.chromium)
            logger.info(f"Launching {config['browser']} headless=True")
            browser = await browser_type.launch(**launch_args)

            context_args = {
                'accept_downloads': True,
                'timezone_id': config['timezone'],
                'viewport': {'width': 1920, 'height': 1080},
            }
            if _auth_state_valid():
                context_args['storage_state'] = AUTH_STATE_FILE

            context = await browser.new_context(**context_args)
            context.set_default_timeout(DEFAULT_ACTION_TIMEOUT)
            context.set_default_navigation_timeout(DEFAULT_NAV_TIMEOUT)

            page = await context.new_page()
            flow.set_page(page, context)

            await _ensure_authenticated(context, page, config)

            max_relogin_attempts = 2
            for nav_attempt in range(max_relogin_attempts + 1):
                try:
                    await flow.navigate_to_export_screen()
                    break
                except Exception as nav_err:
                    current_url = ''
                    page_title = ''
                    login_form_visible = False
                    try:
                        current_url = (page.url or '').lower()
                        page_title = (await page.title() or '').lower()
                        login_form_visible = await page.query_selector(
                            '#ContentMasterMain_txtUserName'
                        ) is not None
                    except Exception:
                        pass
                    landed_on_login = (
                        'login.aspx' in current_url
                        or 'login - powersoft' in page_title
                        or login_form_visible
                    )
                    if not landed_on_login or nav_attempt >= max_relogin_attempts:
                        raise
                    logger.warning(
                        f"[bot:{run_id}] navigate_to_export_screen attempt "
                        f"{nav_attempt + 1}/{max_relogin_attempts + 1} failed and landed on "
                        f"login page (url={current_url!r}, title={page_title!r}, "
                        f"login_form={login_form_visible}). Saved auth state appears expired. "
                        f"Original error: {type(nav_err).__name__}: {nav_err}. "
                        f"Re-logging in and retrying."
                    )
                    for p in (AUTH_STATE_FILE, AUTH_STATE_META):
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception as rm_err:
                            logger.warning(f"Could not remove stale auth file {p}: {rm_err}")
                    try:
                        await context.clear_cookies()
                    except Exception as ck_err:
                        logger.warning(f"Could not clear cookies before re-login: {ck_err}")
                    await _login(page, config)
                    try:
                        await context.storage_state(path=AUTH_STATE_FILE)
                        _save_auth_state_meta()
                        logger.info(f"[bot:{run_id}] Refreshed auth state after expiry")
                    except Exception as save_err:
                        logger.warning(f"Could not save refreshed auth state: {save_err}")

            await flow.apply_filters(params)

            await flow.trigger_export()

            dl = flow.get_download_result() if hasattr(flow, 'get_download_result') else {}
            file_path = dl.get('file_path')
            file_name = dl.get('file_name')
            file_size = dl.get('file_size')

            if file_path and os.path.exists(file_path):
                ts = datetime.utcnow().strftime('%Y-%m-%d_%H%M%S')
                ext = os.path.splitext(file_path)[1]
                final_name = f"{export_name}_{ts}{ext}"
                final_path = os.path.join(download_path, final_name)
                if file_path != final_path:
                    shutil.move(file_path, final_path)
                    file_path = final_path
                    file_name = final_name
                    file_size = os.path.getsize(file_path)

                valid = flow.validate_download(file_path)
                if not valid:
                    raise RuntimeError(f"Download validation failed for {file_name}")

                post_result = await flow.post_process(file_path, {
                    'export_name': export_name,
                    'file_path': file_path,
                    'file_name': file_name,
                    'file_size': file_size,
                })

                result.update({
                    'status': 'success',
                    'file_name': file_name,
                    'file_path': file_path,
                    'file_size': file_size,
                    'post_process': post_result,
                })
            else:
                result['status'] = 'success'
                result['note'] = 'Export completed but no downloaded file returned by flow'
                logger.warning(f"[bot:{run_id}] No downloaded file returned by flow")

            await context.storage_state(path=AUTH_STATE_FILE)
            _save_auth_state_meta()

            await browser.close()
            browser = None

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[bot:{run_id}] Export failed: {error_msg}", exc_info=True)
        result['status'] = 'failed'
        result['error_message'] = error_msg

        try:
            if 'page' in dir() and page:
                failure_info = await _capture_failure(page, export_name, run_id)
                result.update(failure_info)
        except Exception:
            pass

        try:
            if browser:
                await browser.close()
        except Exception:
            pass

    with app.app_context():
        log = db.session.query(BotRunLog).get(run_id)
        if log:
            log.status = result.get('status', 'failed')
            log.finished_at = datetime.utcnow()
            log.file_name = result.get('file_name')
            log.file_path = result.get('file_path')
            log.file_size = result.get('file_size')
            log.screenshot_path = result.get('screenshot_path')
            log.error_step = export_name if result.get('status') == 'failed' else None
            log.error_message = result.get('error_message')
            log.metadata_json = {
                k: v for k, v in result.items()
                if k not in ('status', 'file_name', 'file_path', 'file_size',
                             'screenshot_path', 'error_message')
            }
            db.session.commit()

    logger.info(f"[bot:{run_id}] Export {export_name} finished: {result.get('status')}")
    return result


def run_export_sync(export_name: str, params: dict = None, triggered_by: str = 'manual') -> dict:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, run_export(export_name, params, triggered_by))
                return future.result(timeout=600)
        else:
            return loop.run_until_complete(run_export(export_name, params, triggered_by))
    except RuntimeError:
        return asyncio.run(run_export(export_name, params, triggered_by))


def check_concurrent_run(export_name: str) -> bool:
    from app import db
    from models import BotRunLog
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    running = db.session.query(BotRunLog).filter(
        BotRunLog.export_name == export_name,
        BotRunLog.status == 'running',
        BotRunLog.started_at > cutoff,
    ).first()
    return running is not None
