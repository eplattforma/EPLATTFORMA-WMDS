"""Apply the PostgreSQL-only, gapless activity-timeline migrations explicitly.

This updater is deliberately *not* called during application start-up.  It adds
tables, constraints, functions and views; review it on a Neon branch first, then
run ``python update_activity_tracking_schema.py`` against that branch.
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("activity_schema")

SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")
MIGRATIONS = (
    "010_picker_timeline_postgres.sql",
    "011_activity_enums.sql",
    "012_per_user_tracking.sql",
)
VERIFY = (
    ("timeline integrity violations", "SELECT count(*) FROM vw_shift_timeline_integrity"),
    ("accounting reconciliation breaks", "SELECT count(*) FROM vw_accounting_reconciliation"),
    ("users flagged for tracking", "SELECT count(*) FROM users WHERE track_activity"),
    ("distinct roles tracked", "SELECT count(DISTINCT role) FROM users WHERE track_activity"),
)


def _read(filename: str) -> str:
    path = os.path.join(SQL_DIR, filename)
    with open(path, encoding="utf-8") as source:
        return source.read()


def update_activity_tracking_schema(engine=None) -> bool:
    if engine is None:
        from app import app, db

        with app.app_context():
            return _apply(db.engine)
    return _apply(engine)


def _apply(engine) -> bool:
    if engine.dialect.name != "postgresql":
        log.error("Activity tracking requires PostgreSQL; no schema changes were made.")
        return False

    raw = engine.raw_connection()
    try:
        raw.set_session(autocommit=True)
        cursor = raw.cursor()
        for filename in MIGRATIONS:
            sql = _read(filename)
            log.info("Applying %s", filename)
            cursor.execute(sql)

        healthy = True
        for label, query in VERIFY:
            cursor.execute(query)
            count = cursor.fetchone()[0]
            is_problem = count != 0 and (
                "violations" in label or "breaks" in label
            )
            log.info("%-34s %s%s", label, count, "  <-- investigate" if is_problem else "")
            healthy = healthy and not is_problem

        cursor.execute("SELECT value FROM settings WHERE key = 'activity_mode.enabled'")
        row = cursor.fetchone()
        log.info("master switch activity_mode.enabled = %s", row[0] if row else "false")
        return healthy
    finally:
        raw.close()


if __name__ == "__main__":
    if not update_activity_tracking_schema():
        sys.exit(1)