"""Shared picking helpers used by multiple route files."""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def get_picking_eligible_users():
    """Return User objects who have picking.perform or picking.claim_batch access.

    Checks both explicit user_permissions rows and role-based defaults:
      - 'picker' role has both keys by default
      - 'warehouse_manager' has picking.* which covers both keys
    Any user granted either key explicitly, regardless of role, is included.
    """
    from app import db
    from models import User

    try:
        rows = db.session.execute(text("""
            SELECT username FROM users
            WHERE role IN ('picker', 'warehouse_manager', 'admin')
            UNION
            SELECT DISTINCT username FROM user_permissions
            WHERE permission_key IN ('picking.perform', 'picking.claim_batch')
        """)).fetchall()
        usernames = [r[0] for r in rows]
        if not usernames:
            return []
        return User.query.filter(User.username.in_(usernames)).order_by(User.username).all()
    except Exception as e:
        logger.error(f"get_picking_eligible_users failed: {e}")
        from models import User
        return User.query.filter_by(role='picker').order_by(User.username).all()
