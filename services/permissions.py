"""Centralised permission helpers for the WMDS Development Batch.

Phase 1 shipped this module **disabled by default**:

  - `permissions_enforcement_enabled = false` in settings
  - `@require_permission(key)` logs but never blocks while disabled
  - `has_permission(user, key)` returns True with role-fallback when disabled

Phase 3 keeps `permissions_enforcement_enabled = false` as the seeded default
(Verification & Closeout brief Section 1.2, Option A). Admins flip the flag to
`true` manually from the Settings UI when production is ready; that flip is
the signal that "Phase 3 enforcement is live." When the flag is `true`, direct
URL/API access without the right key returns HTTP 403. Role fallback
(`permissions_role_fallback_enabled`) stays ON as the safety net so admin /
warehouse_manager / crm_admin users keep working without per-user grants.

Phase 3 also adds request-scoped caching: each request resolves a given user's
explicit permissions exactly once even when many menu items / decorators check
the same user.
"""
import logging
from functools import wraps

from flask import abort, g, has_request_context
from flask_login import current_user

from app import db
from models import Setting

logger = logging.getLogger(__name__)


ROLE_PERMISSIONS = {
    "admin": ["*"],
    "warehouse_manager": [
        "menu.dashboard", "menu.warehouse", "menu.picking", "menu.crm",
        "menu.forecast", "menu.communications", "menu.datawarehouse",
        "picking.*", "cooler.*", "sync.view_logs", "routes.manage",
    ],
    "crm_admin": [
        "menu.dashboard", "menu.crm", "menu.communications",
        "comms.*",
    ],
    "picker": [
        "menu.picking", "picking.perform", "picking.claim_batch",
        "cooler.pick",
    ],
    # Note: ``picking.delete_empty_batch`` (Phase 4) is intentionally NOT
    # in any role's grant list — only admins (via the ``*`` wildcard) and
    # warehouse_manager (via ``picking.*``) can hard-delete an empty batch.
    # Cancel/archive replaces hard-delete for everyone else.
    "driver": [
        "menu.driver", "driver.*",
    ],
}


def _is_enforcement_enabled():
    try:
        return Setting.get(db.session, "permissions_enforcement_enabled", "false").lower() == "true"
    except Exception:
        return False


def _is_role_fallback_enabled():
    try:
        return Setting.get(db.session, "permissions_role_fallback_enabled", "true").lower() == "true"
    except Exception:
        return True


def _explicit_permissions_for(username):
    """Return the set of explicit permission keys for a username.

    Result is memoized for the duration of the current Flask request via
    ``flask.g`` so that templates with many menu items and routes with many
    decorator hits don't each issue their own SELECT.
    """
    cache = None
    try:
        if has_request_context():
            cache = getattr(g, "_user_permissions_cache", None)
            if cache is None:
                cache = {}
                g._user_permissions_cache = cache
            if username in cache:
                return cache[username]
    except Exception:
        cache = None

    try:
        from sqlalchemy import text
        rows = db.session.execute(
            text("SELECT permission_key FROM user_permissions WHERE username = :u"),
            {"u": username},
        ).fetchall()
        result = {r[0] for r in rows}
    except Exception as e:
        logger.debug(f"explicit_permissions lookup failed for {username}: {e}")
        result = set()

    if cache is not None:
        cache[username] = result

    return result


def _role_permissions_for(role):
    perms = ROLE_PERMISSIONS.get(role, [])
    return set(perms)


def _matches(granted, key):
    if granted == "*" or granted == key:
        return True
    if granted.endswith(".*"):
        prefix = granted[:-2]
        return key == prefix or key.startswith(prefix + ".")
    return False


def role_covers_wildcard(role_grants, wildcard):
    """Return True if any grant in `role_grants` makes `wildcard` redundant.

    A user-specific wildcard row is "redundant" (and revoking it is a no-op
    in effective access) when some role grant already matches every key
    that `wildcard` covers. Used by the per-user permission editor to
    classify wildcards as inherited (read-only) vs direct (removable),
    so admins aren't shown a "revoke" affordance whose effect role
    fallback would silently undo.

    Cases:
      - role has `*`           → covers any wildcard
      - role has the same wildcard literally
      - role has a broader `g.*` that is a prefix of `wildcard` (e.g.
        role grants `picking.*`, user has `picking.warehouse.*`)
    """
    if not wildcard:
        return False
    for g in role_grants:
        if g == "*":
            return True
        if g == wildcard:
            return True
        if g.endswith(".*"):
            g_prefix = g[:-2]
            if wildcard == "*":
                # Only '*' covers '*'; a g.* grant cannot.
                continue
            if wildcard.endswith(".*"):
                w_prefix = wildcard[:-2]
                if w_prefix == g_prefix or w_prefix.startswith(g_prefix + "."):
                    return True
    return False


def has_permission(user, key):
    """Return True if `user` is allowed to use the permission `key`.

    Resolution order:
      1. Explicit `user_permissions` rows.
      2. If `permissions_role_fallback_enabled` (default true), role-derived perms.
      3. Otherwise, deny.

    When enforcement is disabled, this still returns the correct answer so
    callers and templates behave the same way; only the `@require_permission`
    decorator changes its blocking behaviour based on the flag.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False

    username = getattr(user, "username", None) or getattr(user, "get_id", lambda: None)()
    role = getattr(user, "role", None)

    if username:
        explicit = _explicit_permissions_for(username)
        for granted in explicit:
            if _matches(granted, key):
                return True

    if _is_role_fallback_enabled() and role:
        for granted in _role_permissions_for(role):
            if _matches(granted, key):
                return True

    return False


def require_permission(key):
    """Decorator. While enforcement is OFF it only logs missing permissions.

    Use freely on routes during Phase 1; flip the master flag in Phase 3 to
    activate 403 responses.
    """
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            allowed = has_permission(current_user, key)
            if not allowed:
                if _is_enforcement_enabled():
                    logger.warning(
                        f"Permission denied: user={getattr(current_user, 'username', '?')} "
                        f"key={key} route={view.__name__}"
                    )
                    abort(403)
                else:
                    logger.debug(
                        f"Permission missing (enforcement off): "
                        f"user={getattr(current_user, 'username', '?')} key={key} "
                        f"route={view.__name__}"
                    )
            return view(*args, **kwargs)
        return wrapper
    return decorator


def register_template_helpers(app):
    """Expose `has_permission` to all Jinja templates."""
    @app.context_processor
    def _inject_permission_helpers():
        return {"has_permission": lambda key: has_permission(current_user, key)}
