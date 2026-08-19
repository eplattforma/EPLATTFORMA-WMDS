"""Application integration helpers for the PostgreSQL activity timeline."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator, Optional

from psycopg2.extras import RealDictCursor
from sqlalchemy.exc import SQLAlchemyError

from app import db
from services.activity_service import ActivityService

logger = logging.getLogger(__name__)


@contextmanager
def _raw_connection() -> Iterator[object]:
    connection = db.engine.raw_connection()
    try:
        yield connection
    finally:
        connection.close()


@lru_cache(maxsize=1)
def get_activity_service() -> ActivityService:
    return ActivityService(_raw_connection, cursor_factory=RealDictCursor)


def activity_schema_available() -> bool:
    """Return false until the explicit PostgreSQL migration has been run."""
    if db.engine.dialect.name != "postgresql":
        return False
    try:
        return get_activity_service().schema_available()
    except Exception as exc:
        logger.debug("Activity timeline schema is unavailable: %s", exc)
        return False


def tracking_enabled(username: str) -> bool:
    """The per-user tracking gate, safe before the opt-in schema is installed."""
    if not activity_schema_available():
        return False
    try:
        return get_activity_service().tracking_enabled(username)
    except Exception as exc:
        logger.warning("Could not evaluate activity tracking for %s: %s", username, exc)
        return False


def active_timeline(username: str):
    """Return the open timeline only when this user is actively tracked."""
    if not tracking_enabled(username):
        return None
    try:
        return get_activity_service().current_shift(username)
    except Exception as exc:
        logger.warning("Could not load activity timeline for %s: %s", username, exc)
        return None