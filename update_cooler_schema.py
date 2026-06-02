"""
Adds 'planned' as a valid status in cooler_box_items.
Also ensures the id surrogate PK column exists.
Run once on startup via main.py.

Each ALTER runs in its own connection with a short lock_timeout so a
busy table in production never stalls the boot indefinitely.
A pre-check via information_schema skips the ALTER entirely when the
DB is already in the desired state (steady-state path takes no lock).
"""
import logging
logger = logging.getLogger(__name__)


def update_cooler_schema():
    try:
        from app import db
        from sqlalchemy import text

        is_pg = db.engine.dialect.name == "postgresql"

        # -----------------------------------------------------------------------
        # Step 1 — Widen the status CHECK constraint to include 'planned'.
        # Pre-check so we don't attempt an ALTER (and risk a lock) when already done.
        # -----------------------------------------------------------------------
        needs_constraint_update = True
        if is_pg:
            try:
                with db.engine.connect() as chk:
                    row = chk.execute(text(
                        "SELECT pg_get_constraintdef(oid) "
                        "FROM pg_constraint "
                        "WHERE conname = 'cooler_box_items_status_check'"
                    )).fetchone()
                if row and 'planned' in (row[0] or ''):
                    needs_constraint_update = False
            except Exception as exc:
                logger.debug("update_cooler_schema: constraint pre-check error: %s", exc)

        if needs_constraint_update:
            try:
                with db.engine.begin() as conn:
                    if is_pg:
                        conn.execute(text("SET LOCAL lock_timeout = '3s'"))
                    conn.execute(text(
                        "ALTER TABLE cooler_box_items "
                        "DROP CONSTRAINT IF EXISTS cooler_box_items_status_check"
                    ))
                    conn.execute(text(
                        "ALTER TABLE cooler_box_items "
                        "ADD CONSTRAINT cooler_box_items_status_check "
                        "CHECK (status IN ('planned', 'picked', 'exception'))"
                    ))
                logger.info("cooler_box_items status constraint updated to include 'planned'")
            except Exception as e:
                logger.warning(
                    "Could not update cooler_box_items constraint (lock busy, will retry next boot): %s", e
                )
        else:
            logger.debug("update_cooler_schema: status constraint already includes 'planned' — skipped")

        # -----------------------------------------------------------------------
        # Step 2 — Ensure the id SERIAL column exists.
        # ADD COLUMN IF NOT EXISTS is safe to run every time without a pre-check.
        # -----------------------------------------------------------------------
        try:
            with db.engine.begin() as conn:
                if is_pg:
                    conn.execute(text("SET LOCAL lock_timeout = '3s'"))
                conn.execute(text(
                    "ALTER TABLE cooler_box_items "
                    "ADD COLUMN IF NOT EXISTS id SERIAL"
                ))
            logger.info("cooler_box_items.id column ensured")
        except Exception as e:
            logger.warning("cooler_box_items id column skipped (lock busy, will retry next boot): %s", e)

        logger.info("update_cooler_schema complete")
    except Exception as e:
        logger.error("update_cooler_schema failed: %s", e)
