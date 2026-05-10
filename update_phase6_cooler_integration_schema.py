"""Phase 6 — Cooler / Regular-Picking Integration schema migration.

Additive + idempotent. Mirrors the Phase 5 migration template:

  - ``batch_picking_sessions.session_type`` (default 'standard') —
    distinguishes the per-route ``cooler_route`` sessions from regular
    batch sessions.
  - ``batch_picking_sessions.sequence_locked_at`` /
    ``sequence_locked_by`` — Phase 2 lock-sequencing audit columns.
  - ``batch_pick_queue.delivery_sequence`` — Phase 2 snapshot of the
    ``RouteStop.seq_no`` taken at lock time. NULL until lock.
  - ``cooler_data_quality_log`` table — Phase 1 surfaces items that
    are SENSITIVE but missing dimensions, or that were already picked
    via the regular flow before the cooler workflow activated.
  - ``cooler_box_types`` table + ``cooler_boxes.box_type_id`` FK —
    Phase 5 catalogue used by the estimator and the admin CRUD page.
    Seeds three default box types (Small/Medium/Large) on first boot.

Branched by dialect (Postgres vs SQLite). Re-running on boot is safe
across multiple gunicorn workers; no existing column is altered or
dropped.
"""
import logging

from sqlalchemy import inspect, text

from app import db

logger = logging.getLogger(__name__)


def _add_column_if_missing(conn, insp, table, column_name, column_ddl_type,
                            is_pg=False):
    if is_pg:
        conn.execute(text(
            f"ALTER TABLE {table} "
            f"ADD COLUMN IF NOT EXISTS {column_name} {column_ddl_type}"
        ))
        try:
            existed = column_name in {
                c["name"] for c in insp.get_columns(table)
            }
        except Exception:
            existed = False
        return not existed
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
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return False
        raise


DEFAULT_BOX_TYPES = [
    {"name": "Small", "internal_length_cm": 30, "internal_width_cm": 20,
     "internal_height_cm": 15, "fill_efficiency": 0.70, "sort_order": 1,
     "description": "Single-stop or small pickup orders"},
    {"name": "Medium", "internal_length_cm": 40, "internal_width_cm": 30,
     "internal_height_cm": 25, "fill_efficiency": 0.75, "sort_order": 2,
     "description": "Standard route box, 3-5 stops"},
    {"name": "Large", "internal_length_cm": 50, "internal_width_cm": 40,
     "internal_height_cm": 30, "fill_efficiency": 0.78, "sort_order": 3,
     "description": "Long routes, 6+ stops"},
]


def update_phase6_cooler_integration_schema():
    try:
        dialect_name = db.engine.dialect.name
        is_pg = dialect_name == "postgresql"

        if is_pg:
            pk_type = "BIGSERIAL PRIMARY KEY"
            ts_type = "TIMESTAMP WITH TIME ZONE"
            ts_default = "DEFAULT NOW()"
            box_type_fk = (
                "INTEGER REFERENCES cooler_box_types(id) ON DELETE SET NULL"
            )
        else:
            pk_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
            ts_type = "TIMESTAMP"
            ts_default = "DEFAULT CURRENT_TIMESTAMP"
            box_type_fk = "INTEGER"

        with db.engine.connect() as conn:
            insp = inspect(conn)

            # ── batch_picking_sessions: session_type, lock cols ──
            if "batch_picking_sessions" in insp.get_table_names():
                _add_column_if_missing(
                    conn, insp, "batch_picking_sessions", "session_type",
                    "VARCHAR(20) DEFAULT 'standard'", is_pg=is_pg,
                )
                _add_column_if_missing(
                    conn, insp, "batch_picking_sessions",
                    "sequence_locked_at", ts_type, is_pg=is_pg,
                )
                _add_column_if_missing(
                    conn, insp, "batch_picking_sessions",
                    "sequence_locked_by", "VARCHAR(64)", is_pg=is_pg,
                )
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS "
                    "idx_batch_picking_sessions_session_type "
                    "ON batch_picking_sessions (session_type)"
                ))

            # ── batch_pick_queue: delivery_sequence ──
            if "batch_pick_queue" in insp.get_table_names():
                _add_column_if_missing(
                    conn, insp, "batch_pick_queue",
                    "delivery_sequence", "NUMERIC(10, 2)", is_pg=is_pg,
                )
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS "
                    "idx_batch_pick_queue_delivery_sequence "
                    "ON batch_pick_queue (delivery_sequence)"
                ))

            # ── cooler_data_quality_log ──
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS cooler_data_quality_log (
                    id {pk_type},
                    invoice_no VARCHAR(50),
                    item_code VARCHAR(50),
                    issue_type VARCHAR(40) NOT NULL,
                    details TEXT,
                    route_id INTEGER,
                    created_at {ts_type} NOT NULL {ts_default}
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS "
                "idx_cooler_dq_log_item ON cooler_data_quality_log (item_code)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS "
                "idx_cooler_dq_log_issue ON cooler_data_quality_log (issue_type)"
            ))

            # ── cooler_box_types ──
            conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS cooler_box_types (
                    id {pk_type},
                    name VARCHAR(50) NOT NULL UNIQUE,
                    description TEXT,
                    internal_length_cm NUMERIC(8, 2) NOT NULL,
                    internal_width_cm NUMERIC(8, 2) NOT NULL,
                    internal_height_cm NUMERIC(8, 2) NOT NULL,
                    internal_volume_cm3 NUMERIC(12, 2) NOT NULL,
                    fill_efficiency NUMERIC(4, 3) NOT NULL DEFAULT 0.75,
                    max_weight_kg NUMERIC(8, 2),
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    created_at {ts_type} NOT NULL {ts_default},
                    updated_at {ts_type} NOT NULL {ts_default}
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS "
                "idx_cooler_box_types_active ON cooler_box_types (is_active)"
            ))

            # ── cooler_boxes.box_type_id FK ──
            if "cooler_boxes" in insp.get_table_names():
                _add_column_if_missing(
                    conn, insp, "cooler_boxes", "box_type_id", box_type_fk,
                    is_pg=is_pg,
                )

            # ── Seed default box types (only if empty) ──
            existing = conn.execute(text(
                "SELECT COUNT(*) FROM cooler_box_types"
            )).scalar() or 0
            if existing == 0:
                for bt in DEFAULT_BOX_TYPES:
                    vol = (bt["internal_length_cm"]
                           * bt["internal_width_cm"]
                           * bt["internal_height_cm"])
                    conn.execute(text(
                        "INSERT INTO cooler_box_types "
                        "(name, description, internal_length_cm, "
                        " internal_width_cm, internal_height_cm, "
                        " internal_volume_cm3, fill_efficiency, sort_order) "
                        "VALUES (:n, :d, :l, :w, :h, :v, :fe, :so)"
                    ), {
                        "n": bt["name"], "d": bt["description"],
                        "l": bt["internal_length_cm"],
                        "w": bt["internal_width_cm"],
                        "h": bt["internal_height_cm"],
                        "v": vol, "fe": bt["fill_efficiency"],
                        "so": bt["sort_order"],
                    })
                logger.info(
                    "Phase 6: seeded %d default cooler box types",
                    len(DEFAULT_BOX_TYPES),
                )

            conn.commit()

        # Final verification
        insp = inspect(db.engine)
        for required_table in ("cooler_data_quality_log", "cooler_box_types"):
            if required_table not in insp.get_table_names():
                raise RuntimeError(
                    f"{required_table} not present after Phase 6 migration"
                )
        if "batch_picking_sessions" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("batch_picking_sessions")}
            for required in ("session_type", "sequence_locked_at",
                             "sequence_locked_by"):
                if required not in cols:
                    raise RuntimeError(
                        f"batch_picking_sessions.{required} missing after "
                        f"Phase 6 migration"
                    )
        logger.info("Phase 6 cooler integration schema completed successfully")
    except Exception as e:
        logger.error(f"Phase 6 cooler integration schema failed: {e}")
        raise
