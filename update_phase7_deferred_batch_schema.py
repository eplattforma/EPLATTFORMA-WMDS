"""Phase 7 — Deferred ("Send to Batch") schema migration.

Additive + idempotent. The deferred-batch feature reuses the existing
``batch_picking_sessions`` / ``batch_session_invoices`` / ``batch_pick_queue``
tables; the only new things this migration does are:

  - Widen ``batch_picking_sessions.session_type`` from VARCHAR(20) to
    VARCHAR(30) so the new ``'deferred_route'`` value fits comfortably
    alongside the existing ``'standard'`` and ``'cooler_route'`` values.
  - Backfill ``session_type = 'cooler_route'`` on any pre-Phase-6
    ``COOLER-ROUTE-%`` rows that still have the default ``'standard'``
    (defensive — newly created cooler sessions already stamp it).

No new columns, indexes, or tables. Re-running on boot is safe across
multiple gunicorn workers.

Each step uses its own connection+transaction so that a lock timeout on
one step (e.g. ALTER TABLE) does not abort the remaining steps.
"""
import logging

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


def update_phase7_deferred_batch_schema():
    try:
        dialect_name = db.engine.dialect.name
        is_pg = dialect_name == "postgresql"
    except Exception as exc:
        logger.warning("Phase 7 schema: could not detect dialect: %s", exc)
        return

    # -----------------------------------------------------------------------
    # Step 1 — Widen session_type to VARCHAR(30).
    # Uses its own connection so a LockNotAvailable here does NOT abort the
    # remaining steps.  We check information_schema first so we skip the ALTER
    # entirely when the column is already wide enough (avoids taking any lock
    # at all in the steady-state case).
    # -----------------------------------------------------------------------
    if is_pg:
        try:
            with db.engine.connect() as chk:
                row = chk.execute(text(
                    "SELECT character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'batch_picking_sessions' "
                    "  AND column_name = 'session_type'"
                )).fetchone()
            current_len = row[0] if row else None
            needs_widen = (current_len is not None and current_len < 30)
        except Exception as exc:
            logger.warning("Phase 7: could not check session_type length: %s", exc)
            needs_widen = False

        if needs_widen:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout = '3s'"))
                    conn.execute(text(
                        "ALTER TABLE batch_picking_sessions "
                        "ALTER COLUMN session_type TYPE VARCHAR(30)"
                    ))
                logger.info("Phase 7: widened batch_picking_sessions.session_type to VARCHAR(30)")
            except Exception as exc:
                logger.warning("Phase 7: session_type widen skipped (lock busy, will retry next boot): %s", exc)
        else:
            logger.debug("Phase 7: session_type already VARCHAR(30) or wider — skipping ALTER")

    # -----------------------------------------------------------------------
    # Step 2 — Backfill cooler sessions (own transaction).
    # -----------------------------------------------------------------------
    try:
        with db.engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE batch_picking_sessions "
                "SET session_type = 'cooler_route' "
                "WHERE name LIKE 'COOLER-ROUTE-%' "
                "  AND (session_type IS NULL OR session_type = 'standard')"
            ))
            rc = getattr(result, "rowcount", 0) or 0
        if rc:
            logger.info("Phase 7: backfilled session_type='cooler_route' on %d row(s)", rc)
    except Exception as exc:
        logger.warning("Phase 7: cooler backfill skipped: %s", exc)

    # -----------------------------------------------------------------------
    # Step 3 — Backfill deferred sessions (own transaction).
    # -----------------------------------------------------------------------
    try:
        with db.engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE batch_picking_sessions "
                "SET session_type = 'deferred_route' "
                "WHERE name LIKE 'DEFERRED-ROUTE-%' "
                "  AND (session_type IS NULL OR session_type = 'standard')"
            ))
            rc = getattr(result, "rowcount", 0) or 0
        if rc:
            logger.info("Phase 7: backfilled session_type='deferred_route' on %d row(s)", rc)
    except Exception as exc:
        logger.warning("Phase 7: deferred backfill skipped: %s", exc)

    # -----------------------------------------------------------------------
    # Steps 4a & 4b — Concurrency guard indexes (Postgres only).
    # Each index creation gets its own transaction.
    # -----------------------------------------------------------------------
    if is_pg:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_deferred_session_open_per_route "
                    "ON batch_picking_sessions (route_id) "
                    "WHERE session_type = 'deferred_route' "
                    "  AND status IN ('Created', 'Active', 'Paused')"
                ))
            logger.info("Phase 7: ensured ux_deferred_session_open_per_route")
        except Exception as exc:
            logger.warning("Phase 7: deferred uniq index skipped: %s", exc)

        try:
            with db.engine.begin() as conn:
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_batch_pick_queue_session_invoice_item "
                    "ON batch_pick_queue (batch_session_id, invoice_no, item_code)"
                ))
            logger.info("Phase 7: ensured ux_batch_pick_queue_session_invoice_item")
        except Exception as exc:
            logger.warning("Phase 7: queue uniq index skipped: %s", exc)
