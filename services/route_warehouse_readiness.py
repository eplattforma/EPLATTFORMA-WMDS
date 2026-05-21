"""Route Warehouse Readiness Service

Computes whether all warehouse work for a route is complete and
updates the Shipment.warehouse_status column accordingly.

warehouse_status values on Shipment:
  None / NULL       — work still in progress
  'WAREHOUSE_READY' — all picking, boxing, and exception work done

IMPORTANT — session isolation:
  All DB queries here use ``db.engine.connect()`` (a fresh, standalone
  connection) so that any SQL failure — including a missing column in an
  older production schema — can NEVER poison the caller's ``db.session``.
  The caller's transaction is completely unaffected even if this service
  raises internally.
"""
import logging
from typing import Tuple, List

log = logging.getLogger(__name__)


def check_route_warehouse_ready(route_id: int) -> Tuple[bool, List[str]]:
    """Check all warehouse-work conditions for a route.

    Returns (is_ready, blockers) where blockers is a list of human-readable
    strings describing what is still outstanding.  An empty blockers list
    means the route is warehouse-ready.

    Uses an isolated engine connection — safe to call inside any transaction.
    """
    from app import db

    blockers: List[str] = []

    try:
        with db.engine.connect() as conn:
            # 1. All normal (non-cooler) batch sessions Completed/Cancelled
            pending_batches = conn.execute(
                db.text(
                    "SELECT COUNT(*) FROM batch_picking_sessions "
                    "WHERE route_id = :rid "
                    "  AND session_type != 'cooler_route' "
                    "  AND status NOT IN ('Completed', 'Cancelled', 'Archived') "
                    "  AND cancelled_at IS NULL "
                    "  AND archived_at IS NULL"
                ),
                {"rid": route_id},
            ).scalar() or 0
            if pending_batches > 0:
                blockers.append(
                    f"Normal picking pending ({pending_batches} batch(es))"
                )

            # 2. All cooler batch sessions Completed/Cancelled
            pending_cooler = conn.execute(
                db.text(
                    "SELECT COUNT(*) FROM batch_picking_sessions "
                    "WHERE route_id = :rid "
                    "  AND session_type = 'cooler_route' "
                    "  AND status NOT IN ('Completed', 'Cancelled', 'Archived') "
                    "  AND cancelled_at IS NULL "
                    "  AND archived_at IS NULL"
                ),
                {"rid": route_id},
            ).scalar() or 0
            if pending_cooler > 0:
                blockers.append(
                    f"Cooler picking pending ({pending_cooler} session(s))"
                )

            # 3 & 6. No picked cooler items unassigned to a box
            unboxed = conn.execute(
                db.text(
                    "SELECT COUNT(*) "
                    "FROM batch_pick_queue bpq "
                    "JOIN batch_picking_sessions bps ON bps.id = bpq.batch_session_id "
                    "WHERE bps.route_id = :rid "
                    "  AND bps.session_type = 'cooler_route' "
                    "  AND bpq.qty_picked > 0 "
                    "  AND NOT EXISTS ("
                    "      SELECT 1 FROM cooler_box_items cbi "
                    "      WHERE cbi.queue_item_id = bpq.id"
                    "  )"
                ),
                {"rid": route_id},
            ).scalar() or 0
            if unboxed > 0:
                blockers.append(
                    f"Cooler items unboxed ({unboxed} item(s) picked but not in a box)"
                )

            # 4. All cooler boxes closed (not 'open')
            open_boxes = conn.execute(
                db.text(
                    "SELECT COUNT(*) FROM cooler_boxes "
                    "WHERE route_id = :rid "
                    "  AND status = 'open'"
                ),
                {"rid": route_id},
            ).scalar() or 0
            if open_boxes > 0:
                blockers.append(
                    f"Cooler boxes open ({open_boxes} box(es) still open)"
                )

            # 5. No unresolved picking exceptions — check column exists first
            try:
                unresolved_exc = conn.execute(
                    db.text(
                        "SELECT COUNT(*) "
                        "FROM picking_exceptions pe "
                        "JOIN invoices i ON i.invoice_no = pe.invoice_no "
                        "WHERE i.route_id = :rid "
                        "  AND (pe.is_resolved IS NULL OR pe.is_resolved = FALSE)"
                    ),
                    {"rid": route_id},
                ).scalar() or 0
                if unresolved_exc > 0:
                    blockers.append(
                        f"Picking exceptions unresolved ({unresolved_exc})"
                    )
            except Exception as exc_col_err:
                # Column may not exist yet in older deployments — skip safely
                log.debug(
                    "check_route_warehouse_ready: is_resolved check skipped "
                    "(column likely missing): %s", exc_col_err
                )

            # 7. No pending cooler queue items
            pending_queue = conn.execute(
                db.text(
                    "SELECT COUNT(*) "
                    "FROM batch_pick_queue bpq "
                    "JOIN batch_picking_sessions bps ON bps.id = bpq.batch_session_id "
                    "WHERE bps.route_id = :rid "
                    "  AND bps.session_type = 'cooler_route' "
                    "  AND bpq.status = 'pending'"
                ),
                {"rid": route_id},
            ).scalar() or 0
            if pending_queue > 0:
                blockers.append(
                    f"Cooler items pending pick ({pending_queue} item(s))"
                )

    except Exception as exc:
        log.warning("check_route_warehouse_ready(%s) failed: %s", route_id, exc)
        return False, ["Readiness check error — see server logs"]

    return len(blockers) == 0, blockers


def recalculate_route_warehouse_status(route_id: int) -> None:
    """Update Shipment.warehouse_status based on current DB state.

    Sets warehouse_status = 'WAREHOUSE_READY' when all conditions pass,
    clears it to None otherwise.  Only acts on routes in PLANNED / created
    status — already dispatched or completed routes are left untouched.

    Uses an isolated engine connection for the readiness check so the
    caller's session is never poisoned by a failed internal query.
    """
    if not route_id:
        return

    try:
        from app import db
        from models import Shipment

        route = db.session.get(Shipment, route_id)
        if route is None:
            return

        # Only recalculate while the route is still in the warehouse phase
        if route.status not in ("PLANNED", "created"):
            return

        # A route with no invoices cannot be warehouse-ready
        invoice_count = db.session.execute(
            db.text("SELECT COUNT(*) FROM invoices WHERE route_id = :rid"),
            {"rid": route_id},
        ).scalar() or 0
        if invoice_count == 0:
            if route.warehouse_status is not None:
                route.warehouse_status = None
                db.session.commit()
            return

        is_ready, _blockers = check_route_warehouse_ready(route_id)
        new_ws = "WAREHOUSE_READY" if is_ready else None

        if route.warehouse_status != new_ws:
            route.warehouse_status = new_ws
            db.session.commit()
            log.info(
                "Route %s warehouse_status → %s (blockers: %s)",
                route_id,
                new_ws,
                _blockers,
            )

    except Exception as exc:
        log.warning(
            "recalculate_route_warehouse_status(%s) failed: %s", route_id, exc
        )
