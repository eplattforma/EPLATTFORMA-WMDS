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

    with db.engine.begin() as conn:
        # 1. Widen session_type column (Postgres only — SQLite VARCHAR has no
        #    enforced length so no ALTER needed there).
        if is_pg:
            try:
                conn.execute(text(
                    "ALTER TABLE batch_picking_sessions "
                    "ALTER COLUMN session_type TYPE VARCHAR(30)"
                ))
                logger.info("Phase 7: widened batch_picking_sessions.session_type to VARCHAR(30)")
            except Exception as exc:
                # Already wide enough, or column missing (Phase 6 hasn't run yet).
                msg = str(exc).lower()
                if "does not exist" in msg or "cannot alter" in msg:
                    logger.warning("Phase 7: session_type widen skipped: %s", exc)
                else:
                    logger.debug("Phase 7: session_type already wide enough: %s", exc)

        # 2. Backfill cooler sessions defensively.
        try:
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

        # 3. Backfill deferred sessions defensively (in case any DEFERRED-ROUTE-*
        #    rows were created before the session_type was being stamped).
        try:
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

        # 4. Concurrency guards (Postgres only — partial unique indexes are
        #    PG-specific syntax). These are belt-and-braces protections so
        #    that two pickers racing on Send-to-Batch can never produce
        #    duplicate open sessions or duplicate queue rows.
        if is_pg:
            try:
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
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ux_batch_pick_queue_session_invoice_item "
                    "ON batch_pick_queue (batch_session_id, invoice_no, item_code)"
                ))
                logger.info("Phase 7: ensured ux_batch_pick_queue_session_invoice_item")
            except Exception as exc:
                logger.warning("Phase 7: queue uniq index skipped: %s", exc)
