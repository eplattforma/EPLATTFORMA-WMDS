"""Phase 3: seed explicit user_permissions rows from each user's current role.

Idempotent and safe to re-run. Inserts one row per (username, permission_key)
only if it does not already exist. Wildcards from ROLE_PERMISSIONS (e.g. ``*``,
``picking.*``) are stored literally so the existing ``has_permission`` matcher
keeps behaving the same way.

The seeder runs at most once per environment automatically, gated by the marker
setting ``permissions_auto_seed_done``. Admins can also trigger a manual
re-seed from the Manage Users page (idempotent for existing users; useful for
newly-added users).
"""
import logging

from sqlalchemy import text

from app import db
from models import Setting
from services.permissions import ROLE_PERMISSIONS

logger = logging.getLogger(__name__)


def seed_permissions_from_roles(force=False):
    """Walk active users and insert ROLE_PERMISSIONS[user.role] rows.

    Args:
        force: When True, run even if the ``permissions_auto_seed_done`` marker
            is already set.

    Returns:
        Dict with ``users_seen``, ``rows_inserted``, ``rows_skipped``,
        ``ran`` (bool), ``error`` (str or None).
    """
    counts = {
        "users_seen": 0,
        "rows_inserted": 0,
        "rows_skipped": 0,
        "ran": False,
        "error": None,
    }

    try:
        if not force:
            try:
                done = Setting.get(db.session, "permissions_auto_seed_done", "false")
            except Exception:
                done = "false"
            if str(done).lower() == "true":
                logger.info(
                    "Phase 3 seeder: marker already set, skipping "
                    "(use force=True to re-seed)"
                )
                return counts

        with db.engine.connect() as conn:
            users = conn.execute(text(
                "SELECT username, role FROM users WHERE is_active = true"
            )).fetchall()

            for row in users:
                username = row[0]
                role = row[1]
                if not username or not role:
                    continue
                perms = ROLE_PERMISSIONS.get(role, [])
                counts["users_seen"] += 1
                for permission_key in perms:
                    result = conn.execute(
                        text(
                            "INSERT INTO user_permissions "
                            "(username, permission_key, granted_by) "
                            "VALUES (:u, :k, :by) "
                            "ON CONFLICT (username, permission_key) DO NOTHING"
                        ),
                        {
                            "u": username,
                            "k": permission_key,
                            "by": "phase3_role_seeder",
                        },
                    )
                    if (result.rowcount or 0) > 0:
                        counts["rows_inserted"] += 1
                    else:
                        counts["rows_skipped"] += 1
            conn.commit()

        try:
            Setting.set(db.session, "permissions_auto_seed_done", "true")
            db.session.commit()
        except Exception as e:
            logger.warning(
                f"Could not set permissions_auto_seed_done marker: {e}"
            )
            try:
                db.session.rollback()
            except Exception:
                pass

        counts["ran"] = True
        logger.info(
            f"Phase 3 seeder: users={counts['users_seen']} "
            f"inserted={counts['rows_inserted']} kept={counts['rows_skipped']}"
        )
    except Exception as e:
        counts["error"] = str(e)
        logger.error(f"seed_permissions_from_roles failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass

    return counts


def reset_user_to_role_defaults(username, granted_by=None):
    """Replace a single user's explicit permissions with their role defaults.

    Used by the admin Manage Users page when an admin clicks "Reset to role
    defaults" for one user. Idempotent.
    """
    summary = {"deleted": 0, "inserted": 0, "error": None}
    try:
        from sqlalchemy import text as _t
        user = db.session.execute(
            _t("SELECT role FROM users WHERE username = :u"),
            {"u": username},
        ).fetchone()
        if not user:
            summary["error"] = "User not found"
            return summary
        role = user[0]
        perms = ROLE_PERMISSIONS.get(role, [])

        del_res = db.session.execute(
            _t("DELETE FROM user_permissions WHERE username = :u"),
            {"u": username},
        )
        summary["deleted"] = del_res.rowcount or 0

        for permission_key in perms:
            db.session.execute(
                _t(
                    "INSERT INTO user_permissions "
                    "(username, permission_key, granted_by) "
                    "VALUES (:u, :k, :by) "
                    "ON CONFLICT (username, permission_key) DO NOTHING"
                ),
                {
                    "u": username,
                    "k": permission_key,
                    "by": granted_by or "reset_to_role",
                },
            )
            summary["inserted"] += 1
        db.session.commit()
    except Exception as e:
        summary["error"] = str(e)
        try:
            db.session.rollback()
        except Exception:
            pass
    return summary
