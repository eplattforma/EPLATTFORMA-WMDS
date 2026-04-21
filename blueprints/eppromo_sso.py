"""Blueprint exposing the WMDS -> eppromo SSO launch endpoint."""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, url_for
from flask_login import current_user, login_required

from services.eppromo_sso import (
    EppromoSSOConfigError,
    build_login_url,
    is_configured,
)

logger = logging.getLogger(__name__)

eppromo_sso_bp = Blueprint("eppromo_sso", __name__, url_prefix="/admin")


def _safe_dashboard_url() -> str:
    for endpoint in ("dashboard", "index", "home"):
        try:
            return url_for(endpoint)
        except Exception:
            continue
    return "/"


@eppromo_sso_bp.route("/eppromo", methods=["GET"])
@login_required
def launch_eppromo():
    username = getattr(current_user, "username", None) or "?"

    if not is_configured():
        logger.warning(
            "eppromo SSO launch blocked: missing config (user=%s)", username
        )
        flash("EPPROMO SSO is not configured.", "warning")
        return redirect(_safe_dashboard_url())

    try:
        login_url = build_login_url()
    except EppromoSSOConfigError as exc:
        logger.warning("eppromo SSO config error (user=%s): %s", username, exc)
        flash("EPPROMO SSO is not configured.", "warning")
        return redirect(_safe_dashboard_url())
    except Exception:
        logger.exception("eppromo SSO URL generation failed (user=%s)", username)
        flash("Could not start an eppromo session. Please try again.", "danger")
        return redirect(_safe_dashboard_url())

    logger.info("eppromo SSO launch (user=%s) -> redirecting to eppromo /sso", username)
    return redirect(login_url)
