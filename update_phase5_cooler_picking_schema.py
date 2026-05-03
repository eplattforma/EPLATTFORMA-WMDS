"""Phase 5 Cooler Picking schema migration.

Additive + idempotent. Mirrors the Phase 4 migration template:

  - ``cooler_boxes`` table (open -> closed -> loaded -> delivered).
  - ``cooler_box_items`` table (assigned -> picked -> removed).
  - ``batch_pick_queue.wms_zone`` snapshot column (additive). Captured at
    queue-creation time so mid-pick reclassification of ``DwItem.wms_zone``
    cannot retroactively move a row between cooler / normal.

Every operation is guarded with ``IF NOT EXISTS`` / ``ADD COLUMN IF NOT
EXISTS`` so re-running on boot is safe across multiple gunicorn workers.
No existing column is altered or dropped.
"""
import logging

from sqlalchemy import inspect, text

from app import db

logger = logging.getLogger(__name__)


def update_phase5_cooler_picking_schema():
    try:
        with db.engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE batch_pick_queue "
                "ADD COLUMN IF NOT EXISTS wms_zone VARCHAR(50)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_wms_zone "
                "ON batch_pick_queue (wms_zone)"
            ))
            logger.info("Phase 5: batch_pick_queue.wms_zone column ensured")

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cooler_boxes (
                    id BIGSERIAL PRIMARY KEY,
                    route_id INTEGER REFERENCES shipments(id) ON DELETE SET NULL,
                    delivery_date DATE NOT NULL,
                    box_no INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    first_stop_sequence NUMERIC(10, 2),
                    last_stop_sequence NUMERIC(10, 2),
                    created_by VARCHAR(64) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    closed_by VARCHAR(64),
                    closed_at TIMESTAMP WITH TIME ZONE,
                    label_printed_at TIMESTAMP WITH TIME ZONE,
                    notes TEXT,
                    UNIQUE (route_id, delivery_date, box_no)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cooler_boxes_route_date "
                "ON cooler_boxes (route_id, delivery_date)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cooler_boxes_status "
                "ON cooler_boxes (status)"
            ))
            logger.info("Phase 5: cooler_boxes table + indexes ensured")

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS cooler_box_items (
                    id BIGSERIAL PRIMARY KEY,
                    cooler_box_id BIGINT NOT NULL REFERENCES cooler_boxes(id) ON DELETE CASCADE,
                    invoice_no VARCHAR(50) NOT NULL,
                    customer_code VARCHAR(50),
                    customer_name VARCHAR(200),
                    route_stop_id INTEGER REFERENCES route_stop(route_stop_id) ON DELETE SET NULL,
                    delivery_sequence NUMERIC(10, 2),
                    item_code VARCHAR(50) NOT NULL,
                    item_name VARCHAR(200),
                    expected_qty NUMERIC(12, 3) NOT NULL,
                    picked_qty NUMERIC(12, 3) DEFAULT 0,
                    picked_by VARCHAR(64),
                    picked_at TIMESTAMP WITH TIME ZONE,
                    queue_item_id BIGINT,
                    status VARCHAR(20) NOT NULL DEFAULT 'assigned',
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cooler_box_items_box "
                "ON cooler_box_items (cooler_box_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cooler_box_items_invoice "
                "ON cooler_box_items (invoice_no)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cooler_box_items_route_stop "
                "ON cooler_box_items (route_stop_id)"
            ))
            logger.info("Phase 5: cooler_box_items table + indexes ensured")

            conn.commit()

        insp = inspect(db.engine)
        bpq_cols = {c["name"] for c in insp.get_columns("batch_pick_queue")}
        if "wms_zone" not in bpq_cols:
            raise RuntimeError(
                "batch_pick_queue.wms_zone not present after Phase 5 migration"
            )
        for required_table in ("cooler_boxes", "cooler_box_items"):
            if required_table not in insp.get_table_names():
                raise RuntimeError(
                    f"{required_table} table not present after Phase 5 migration"
                )
        logger.info("Phase 5 cooler picking schema completed successfully")
    except Exception as e:
        logger.error(f"Phase 5 cooler picking schema failed: {e}")
        raise
