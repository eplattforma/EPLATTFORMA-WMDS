"""Phase 4 Batch Picking Refactor schema migration.

Additive + idempotent, mirrors the Phase 1 migration template
(``update_phase1_foundation_schema.py``):

  - ``batch_picking_sessions`` gains audit columns for the cancel/archive
    + claim flow (``cancelled_at/by``, ``cancel_reason``, ``claimed_at/by``,
    ``last_activity_at``, ``archived_at/by``).
  - ``batch_pick_queue`` is created (DB-backed picking queue used when
    the ``use_db_backed_picking_queue`` flag is ON; empty otherwise so
    the legacy session path is unaffected).

Every operation is guarded with ``IF NOT EXISTS``/``ADD COLUMN IF NOT
EXISTS`` so re-running on boot is safe across multiple gunicorn
workers. No existing column is altered or dropped.

Note: the actual table name is ``batch_picking_sessions`` (plural) —
the brief uses singular but production uses plural; the FK on the new
queue table matches the real schema.
"""
import logging

from sqlalchemy import inspect, text

from app import db

logger = logging.getLogger(__name__)


def update_phase4_batch_picking_schema():
    try:
        with db.engine.connect() as conn:
            # 1. Audit columns on batch_picking_sessions
            for col_def in (
                "ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP WITH TIME ZONE",
                "ADD COLUMN IF NOT EXISTS cancelled_by VARCHAR(64)",
                "ADD COLUMN IF NOT EXISTS cancel_reason TEXT",
                "ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP WITH TIME ZONE",
                "ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(64)",
                "ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMP WITH TIME ZONE",
                "ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE",
                "ADD COLUMN IF NOT EXISTS archived_by VARCHAR(64)",
            ):
                conn.execute(text(f"ALTER TABLE batch_picking_sessions {col_def}"))
            logger.info("Phase 4: batch_picking_sessions audit columns ensured")

            # 2. batch_pick_queue table (DB-backed durable queue)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS batch_pick_queue (
                    id BIGSERIAL PRIMARY KEY,
                    batch_session_id INTEGER NOT NULL REFERENCES batch_picking_sessions(id) ON DELETE CASCADE,
                    invoice_no VARCHAR(50) NOT NULL,
                    item_code VARCHAR(50) NOT NULL,
                    pick_zone_type VARCHAR(20) NOT NULL DEFAULT 'normal',
                    sequence_no INTEGER,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    qty_required NUMERIC(12,3),
                    qty_picked NUMERIC(12,3) DEFAULT 0,
                    picked_by VARCHAR(64),
                    picked_at TIMESTAMP WITH TIME ZONE,
                    cancelled_at TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_session_status "
                "ON batch_pick_queue (batch_session_id, status)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_invoice_item "
                "ON batch_pick_queue (invoice_no, item_code)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_zone_type "
                "ON batch_pick_queue (pick_zone_type)"
            ))
            logger.info("Phase 4: batch_pick_queue table + indexes ensured")

            conn.commit()

        # Verification — fail loudly if the migration didn't take effect
        insp = inspect(db.engine)
        bps_cols = {c["name"] for c in insp.get_columns("batch_picking_sessions")}
        for required in (
            "cancelled_at", "cancelled_by", "cancel_reason",
            "claimed_at", "claimed_by", "last_activity_at",
            "archived_at", "archived_by",
        ):
            if required not in bps_cols:
                raise RuntimeError(
                    f"batch_picking_sessions.{required} not present after Phase 4 migration"
                )
        if "batch_pick_queue" not in insp.get_table_names():
            raise RuntimeError("batch_pick_queue table not present after Phase 4 migration")

        logger.info("Phase 4 batch picking schema completed successfully")
    except Exception as e:
        logger.error(f"Phase 4 batch picking schema failed: {e}")
        raise
