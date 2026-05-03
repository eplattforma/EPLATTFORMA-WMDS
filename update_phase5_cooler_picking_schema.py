"""Phase 5 Cooler Picking schema migration.

Additive + idempotent. Mirrors the Phase 4 migration template:

  - ``cooler_boxes`` table (open -> closed -> loaded -> delivered).
  - ``cooler_box_items`` table (assigned -> picked -> removed).
  - ``batch_pick_queue.wms_zone`` snapshot column (additive). Captured at
    queue-creation time so mid-pick reclassification of ``DwItem.wms_zone``
    cannot retroactively move a row between cooler / normal.

the original migration used Postgres-only DDL
(``BIGSERIAL``, ``ADD COLUMN IF NOT EXISTS``,
``TIMESTAMP WITH TIME ZONE``) which broke the SQLite test path. The
migration now branches by dialect so both Postgres and SQLite engines
work. Re-running on boot remains safe across multiple gunicorn workers.
No existing column is altered or dropped.
"""
import logging

from sqlalchemy import inspect, text

from app import db

logger = logging.getLogger(__name__)


def _add_column_if_missing(conn, insp, table, column_name, column_ddl_type,
                            is_pg=False):
    """Race-safe ``ADD COLUMN`` that works on every dialect.

    ``column_ddl_type`` is the type fragment after the column name
    (e.g. ``"VARCHAR(50)"``).

    Concurrency model:
      * **PostgreSQL** — uses native ``ALTER TABLE ... ADD COLUMN
        IF NOT EXISTS``, which is atomic against concurrent multi-worker
        boot (gunicorn forks running this migration at the same time).
      * **Other dialects (SQLite, etc.)** — fall back to the inspector
        check-then-add pattern (SQLite cold-boot is single-process in
        the test fixtures + dev laptop, so the race does not apply),
        and any duplicate-column race that does occur is swallowed and
        logged so a sibling worker losing the race does not crash the
        cold boot.
    """
    if is_pg:
        # Native, atomic; no inspector race window.
        conn.execute(text(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {column_name} {column_ddl_type}"
        ))
        # We cannot tell from the DDL alone whether the column was
        # already present; report False (no-op) when it existed.
        try:
            existed = column_name in {
                c["name"] for c in insp.get_columns(table)
            }
        except Exception:
            existed = False
        return not existed
    # Non-PG path (SQLite, etc.).
    try:
        cols = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        cols = set()
    if column_name in cols:
        return False
    try:
        conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN {column_name} {column_ddl_type}"
        ))
        return True
    except Exception as exc:
        # Sibling worker won the race — swallow duplicate-column errors
        # so cold boot is idempotent. Re-raise anything else.
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            logger.info(
                "Phase 5: %s.%s already added by sibling worker",
                table, column_name,
            )
            return False
        raise


def update_phase5_cooler_picking_schema():
    try:
        dialect_name = db.engine.dialect.name
        is_pg = dialect_name == "postgresql"

        # Dialect-aware DDL fragments
        if is_pg:
            pk_type = "BIGSERIAL PRIMARY KEY"
            box_id_fk_type = "BIGINT"
            ts_type = "TIMESTAMP WITH TIME ZONE"
            ts_default = "DEFAULT NOW()"
            route_fk = "INTEGER REFERENCES shipments(id) ON DELETE SET NULL"
            box_fk = (
                "BIGINT NOT NULL REFERENCES cooler_boxes(id) ON DELETE CASCADE"
            )
            stop_fk = (
                "INTEGER REFERENCES route_stop(route_stop_id) ON DELETE SET NULL"
            )
        else:
            # SQLite (and other dialects used in tests): use portable
            # types and skip FKs that depend on Postgres semantics.
            pk_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
            box_id_fk_type = "INTEGER"
            ts_type = "TIMESTAMP"
            ts_default = "DEFAULT CURRENT_TIMESTAMP"
            route_fk = "INTEGER"
            box_fk = "INTEGER NOT NULL"
            stop_fk = "INTEGER"

        with db.engine.connect() as conn:
            insp = inspect(conn)

            # ── batch_pick_queue.wms_zone (additive) ──
            if "batch_pick_queue" in insp.get_table_names():
                added = _add_column_if_missing(
                    conn, insp, "batch_pick_queue", "wms_zone", "VARCHAR(50)",
                    is_pg=is_pg,
                )
                if added:
                    logger.info(
                        "Phase 5: batch_pick_queue.wms_zone column added"
                    )
                else:
                    logger.info(
                        "Phase 5: batch_pick_queue.wms_zone already present"
                    )
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_batch_pick_queue_wms_zone "
                    "ON batch_pick_queue (wms_zone)"
                ))
            else:
                logger.info(
                    "Phase 5: batch_pick_queue table not present — "
                    "wms_zone column add skipped"
                )

            # ── cooler_boxes ──
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS cooler_boxes (
                    id {pk_type},
                    route_id {route_fk},
                    delivery_date DATE NOT NULL,
                    box_no INTEGER NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'open',
                    first_stop_sequence NUMERIC(10, 2),
                    last_stop_sequence NUMERIC(10, 2),
                    created_by VARCHAR(64) NOT NULL,
                    created_at {ts_type} NOT NULL {ts_default},
                    closed_by VARCHAR(64),
                    closed_at {ts_type},
                    label_printed_at {ts_type},
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

            # ── cooler_box_items ──
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS cooler_box_items (
                    id {pk_type},
                    cooler_box_id {box_fk},
                    invoice_no VARCHAR(50) NOT NULL,
                    customer_code VARCHAR(50),
                    customer_name VARCHAR(200),
                    route_stop_id {stop_fk},
                    delivery_sequence NUMERIC(10, 2),
                    item_code VARCHAR(50) NOT NULL,
                    item_name VARCHAR(200),
                    expected_qty NUMERIC(12, 3) NOT NULL,
                    picked_qty NUMERIC(12, 3) DEFAULT 0,
                    picked_by VARCHAR(64),
                    picked_at {ts_type},
                    queue_item_id {box_id_fk_type},
                    status VARCHAR(20) NOT NULL DEFAULT 'assigned',
                    created_at {ts_type} NOT NULL {ts_default},
                    updated_at {ts_type} NOT NULL {ts_default}
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

        # Final verification
        insp = inspect(db.engine)
        if "batch_pick_queue" in insp.get_table_names():
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
