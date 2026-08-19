"""
update_activity_tracking_schema.py

Adds the gapless picker activity timeline:
  * picker_segment / picker_action  — the segment ledger, no gaps by construction
  * users.track_activity            — PER-USER tracking gate (not per-role)
  * resolution state machine        — unassigned time is escalated, never dropped
  * integrity + accounting views

Follows the house convention of update_*_schema.py: run it directly.

    python update_activity_tracking_schema.py

Idempotent — safe to re-run. Existing shifts / idle_periods / settings / users
are NOT recreated; only additive changes are made.

WHY AUTOCOMMIT (do not "fix" this):
    011 issues `ALTER TYPE picker_state ADD VALUE 'awaiting_order'` and later
    creates a view that references that value. PostgreSQL refuses to use a new
    enum value in the same transaction that added it. The .sql files therefore
    manage their own BEGIN/COMMIT, and this runner must NOT wrap them in an
    outer transaction. Executing them through db.session.execute(text(...))
    would also fail on the $$-quoted function bodies.

POSTGRES ONLY:
    Uses btree_gist, EXCLUDE constraints, enums and generated columns. The test
    suite's SQLite fallback (app.py -> sqlite:///picking.db) cannot run these;
    the script detects SQLite and exits cleanly rather than half-applying.
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("activity_schema")

# The .sql files live next to this script under sql/. Kept as .sql rather than
# inlined so they stay reviewable, diffable and runnable via psql on a Neon
# branch before anyone points them at production.
SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")
MIGRATIONS = [
    "010_picker_timeline_postgres.sql",
    "011_activity_enums.sql",
    "012_per_user_tracking.sql",
]

VERIFY = [
    ("timeline integrity violations", "SELECT count(*) FROM vw_shift_timeline_integrity"),
    ("accounting reconciliation breaks", "SELECT count(*) FROM vw_accounting_reconciliation"),
    ("users flagged for tracking", "SELECT count(*) FROM users WHERE track_activity"),
    ("distinct roles tracked", "SELECT count(DISTINCT role) FROM users WHERE track_activity"),
]


def _read(filename):
    path = os.path.join(SQL_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — copy the sql/ directory next to this script")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def update_database_schema(engine=None):
    """Apply the activity-tracking migrations.

    engine: optional SQLAlchemy Engine. Defaults to the app's db.engine, so the
            normal invocation needs no arguments.
    """
    if engine is None:
        from app import app, db  # noqa: WPS433  (house convention)
        with app.app_context():
            return _apply(db.engine)
    return _apply(engine)


def _apply(engine):
    dialect = engine.dialect.name
    if dialect != "postgresql":
        log.error(
            "Activity tracking requires PostgreSQL (found '%s'). "
            "The exclusion constraint, enums and generated columns have no "
            "SQLite equivalent. Aborting without partial changes.", dialect)
        return False

    raw = engine.raw_connection()
    try:
        # See module docstring: the .sql files own their transactions.
        raw.set_session(autocommit=True)
        cur = raw.cursor()

        for filename in MIGRATIONS:
            sql = _read(filename)
            log.info("applying %s (%d bytes)", filename, len(sql))
            cur.execute(sql)
            log.info("  ok")

        log.info("verifying")
        all_ok = True
        for label, query in VERIFY:
            cur.execute(query)
            value = cur.fetchone()[0]
            # The two integrity counters must be zero; the roster counters are
            # informational.
            bad = value != 0 and "violations" in label or value != 0 and "breaks" in label
            log.info("  %-34s %s%s", label, value, "   <-- INVESTIGATE" if bad else "")
            all_ok = all_ok and not bad

        cur.execute(
            "SELECT value FROM settings WHERE key = 'activity_mode.enabled'")
        row = cur.fetchone()
        master = (row[0] if row else "false")
        log.info("")
        log.info("master switch activity_mode.enabled = %s", master)
        if str(master).lower() != "true":
            log.info("Nothing is being tracked yet. When ready:")
            log.info("  UPDATE settings SET value='true' "
                     "WHERE key='activity_mode.enabled';")

        cur.close()
        return all_ok
    finally:
        raw.close()


if __name__ == "__main__":
    ok = update_database_schema()
    if not ok:
        log.error("migration reported problems — see above")
        sys.exit(1)
    log.info("done")
