"""Phase 6: bridge between regular order picking and Phase 5 cooler queue.

When invoices are attached to a route, their SENSITIVE items must be
extracted into ``batch_pick_queue`` (``pick_zone_type = 'cooler'``) and
locked from regular order picking via
``InvoiceItem.locked_by_batch_id``. This prevents room-temperature
exposure of cool-chain items in the normal picking flow.

The extraction is idempotent: re-running for the same invoice does not
create duplicate queue rows. ``delivery_sequence`` is left NULL until
the warehouse manager clicks "Lock Cooler Sequencing" (Phase 2).

Honours ``summer_cooler_mode_enabled`` — when OFF, the extractor
short-circuits and returns immediately with no side effects.
"""
import logging

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from models import (
    ActivityLog, BatchPickingSession, BatchSessionInvoice,
    DwItem, Invoice, InvoiceItem, Setting,
)
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)

COOLER_SESSION_TYPE = "cooler_route"

# Statuses that mean a cooler session is "done" — when the latest cooler
# session for a route is in one of these, the next late SENSITIVE addition
# spawns a NEW sibling session (COOLER-ROUTE-<id>-2, -3, ...) instead of
# being silently ignored on a closed batch.
TERMINAL_COOLER_STATUSES = ("Completed", "Cancelled", "Archived")
ROUTE_BATCH_SESSION_TYPE = "route_batch"


def _is_summer_cooler_mode_enabled():
    try:
        return Setting.get(
            db.session, "summer_cooler_mode_enabled", "false"
        ).lower() == "true"
    except Exception:
        return False


def _current_username():
    """Best-effort username for audit columns. Falls back to 'system'
    when no Flask request context is bound (e.g. background extraction)."""
    try:
        from flask import has_request_context
        from flask_login import current_user
        if has_request_context() and getattr(
                current_user, "is_authenticated", False):
            return getattr(current_user, "username", None) or "system"
    except Exception:
        pass
    return "system"


def _latest_cooler_session_for_route(route_id):
    """Return the most-recently-created cooler session for ``route_id``,
    or ``None`` if there is none. Looks up by the ``route_id`` column
    (preferred) and falls back to the legacy ``name`` pattern for any
    rows the migration backfill missed.
    """
    q = BatchPickingSession.query.filter_by(
        session_type=COOLER_SESSION_TYPE,
    )
    try:
        q_by_route = q.filter(BatchPickingSession.route_id == route_id)
        latest = q_by_route.order_by(
            BatchPickingSession.created_at.desc()
        ).first()
        if latest is not None:
            return latest
    except Exception:
        # ORM may not know about route_id yet (e.g. very early test boot).
        pass
    # Legacy fallback: name = "COOLER-ROUTE-<id>" or "COOLER-ROUTE-<id>-N".
    legacy_pattern = f"COOLER-ROUTE-{route_id}"
    return (
        BatchPickingSession.query.filter(
            BatchPickingSession.session_type == COOLER_SESSION_TYPE,
            db.or_(
                BatchPickingSession.name == legacy_pattern,
                BatchPickingSession.name.like(f"{legacy_pattern}-%"),
            ),
        )
        .order_by(BatchPickingSession.created_at.desc())
        .first()
    )


def _next_cooler_session_name(route_id):
    """Compute the next free `COOLER-ROUTE-<id>[-N]` name for ``route_id``.

    First session: ``COOLER-ROUTE-<id>``.
    Subsequent siblings: ``COOLER-ROUTE-<id>-2``, ``-3``, ...
    """
    base = f"COOLER-ROUTE-{route_id}"
    existing_names = {
        n for (n,) in db.session.query(BatchPickingSession.name).filter(
            db.or_(
                BatchPickingSession.name == base,
                BatchPickingSession.name.like(f"{base}-%"),
            ),
        ).all()
    }
    if base not in existing_names:
        return base, f"COOLER-{route_id}"
    n = 2
    while f"{base}-{n}" in existing_names:
        n += 1
    return f"{base}-{n}", f"COOLER-{route_id}-{n}"


def get_or_create_cooler_session(route_id, created_by=None):
    """Return an active ``cooler_route`` session for ``route_id``.

    Behaviour:
      - If the latest cooler session for the route is non-terminal
        (Created / In Progress / picking / Active / Paused), reuse it.
      - If the latest is in a terminal status (Completed / Cancelled /
        Archived), or if there is no session yet, create a NEW one.
        New sibling sessions get a sequential name suffix
        (``COOLER-ROUTE-<id>-2``, ``-3``, ...) so the previous closed
        batch stays intact and the new late-arrival items land on a
        fresh batch the picker sees in their list.
    """
    if route_id is None:
        return None

    latest = _latest_cooler_session_for_route(route_id)
    if latest is not None and (latest.status or "") not in TERMINAL_COOLER_STATUSES:
        return latest

    created_by = created_by or _current_username()
    name, batch_number = _next_cooler_session_name(route_id)

    session = BatchPickingSession(
        name=name,
        batch_number=batch_number,
        zones="SENSITIVE",
        picking_mode="Cooler",
        created_by=created_by,
        status="Created",
    )
    # Additive Phase 6 columns — set via setattr so tests on a stock
    # SQLite DB (no migration applied yet) don't crash if the column
    # is absent. db.create_all() picks them up from the ORM anyway.
    try:
        session.session_type = COOLER_SESSION_TYPE
    except Exception:
        pass
    try:
        session.route_id = int(route_id)
    except Exception:
        pass
    try:
        session.last_activity_at = get_utc_now()
    except Exception:
        pass
    # Wrap the insert in a SAVEPOINT so a unique-key collision from a
    # concurrent worker rolls back ONLY the failed insert — not the
    # caller's outer transaction (which may already hold queue rows,
    # audit log entries, lock stamps, etc. for this extraction batch).
    try:
        with db.session.begin_nested():
            db.session.add(session)
            db.session.flush()
    except IntegrityError:
        # Sibling worker won the race — refetch the latest cooler session
        # for this route (it now exists thanks to the other worker). The
        # outer transaction is intact thanks to the SAVEPOINT.
        session = _latest_cooler_session_for_route(route_id)
    return session


def _log_data_quality(invoice_no, item_code, issue_type, details, route_id):
    try:
        db.session.execute(
            text(
                "INSERT INTO cooler_data_quality_log "
                "(invoice_no, item_code, issue_type, details, route_id) "
                "VALUES (:inv, :ic, :it, :d, :rid)"
            ),
            {"inv": invoice_no, "ic": item_code, "it": issue_type,
             "d": details, "rid": route_id},
        )
    except Exception as e:
        logger.warning("cooler_data_quality_log insert failed: %s", e)


def _audit(activity_type, details, invoice_no=None, item_code=None):
    try:
        db.session.add(ActivityLog(
            picker_username=_current_username(),
            activity_type=activity_type,
            invoice_no=invoice_no,
            item_code=item_code,
            details=details,
        ))
    except Exception as e:
        logger.warning("ActivityLog insert failed (%s): %s", activity_type, e)


def _sensitive_codes(item_codes):
    """Return the subset of ``item_codes`` whose ``DwItem.wms_zone`` is
    SENSITIVE. Codes with no DW row or non-SENSITIVE zone are dropped.
    """
    if not item_codes:
        return set()
    rows = db.session.query(
        DwItem.item_code_365, DwItem.wms_zone
    ).filter(DwItem.item_code_365.in_(list(item_codes))).all()
    return {code for code, zone in rows if (zone or "").upper() == "SENSITIVE"}


def _items_missing_dimensions(item_codes):
    """Return the subset of ``item_codes`` missing any of length/width/
    height on the ``DwItem`` row."""
    if not item_codes:
        return set()
    rows = db.session.query(
        DwItem.item_code_365, DwItem.item_length,
        DwItem.item_width, DwItem.item_height,
    ).filter(DwItem.item_code_365.in_(list(item_codes))).all()
    missing = set()
    for code, l, w, h in rows:
        if l is None or w is None or h is None:
            missing.add(code)
    # Items with no DwItem row at all are also "missing".
    seen = {code for code, *_ in rows}
    for code in item_codes:
        if code not in seen:
            missing.add(code)
    return missing


def _existing_queue_keys(session_id):
    """Return the set of (invoice_no, item_code) tuples already present
    on the cooler session's queue — for idempotency."""
    if session_id is None:
        return set()
    rows = db.session.execute(
        text(
            "SELECT invoice_no, item_code FROM batch_pick_queue "
            "WHERE batch_session_id = :sid AND pick_zone_type = 'cooler'"
        ),
        {"sid": session_id},
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def release_cooler_locks_for_invoice(invoice_no, full_reset=False):
    """Release all cooler batch holds for an invoice being removed from a route.

    When full_reset=False (default / existing behaviour):
        - Deletes pending cooler queue rows
        - Clears batch locks on unpicked invoice items
        - Preserves picked rows and box assignments (audit trail)

    When full_reset=True (user confirmed unassign with warning):
        - Everything above PLUS:
        - Removes cooler_box_items for this invoice
        - Deletes picked batch_pick_queue rows for this invoice
        - Resets invoice_items.is_picked = FALSE for affected items
        - Recalculates fill on any boxes that lost items
        - Cancels any boxes that are now completely empty

    Returns dict with counters.
    """
    # 0) Remove planned cooler_box_items rows whose backing queue row is a
    #    PENDING row for this invoice — those queue rows are deleted in
    #    step 1 below, and leaving the planned box assignments behind would
    #    create dangling queue_item_id references (orphans on manifests and
    #    in the pick-to-box flow). Runs in BOTH the default and full_reset
    #    paths; in full_reset the broader delete later is a harmless no-op
    #    for these rows.
    res0 = db.session.execute(
        text(
            "DELETE FROM cooler_box_items "
            "WHERE queue_item_id IN ( "
            "    SELECT id FROM batch_pick_queue "
            "    WHERE invoice_no = :inv "
            "      AND pick_zone_type = 'cooler' "
            "      AND status IN ('pending', 'skipped_pending') "
            ")"
        ),
        {"inv": invoice_no},
    )
    planned_box_items_removed = res0.rowcount or 0

    # 0b) Capture the cooler session(s) holding queue rows for this invoice
    # BEFORE deleting anything, so we can check for session completion at
    # the end (Bug fix: sessions whose every invoice is unassigned never
    # reach box_close and would otherwise stay non-terminal forever).
    affected_session_ids = [
        r[0] for r in db.session.execute(
            text(
                "SELECT DISTINCT batch_session_id FROM batch_pick_queue "
                "WHERE invoice_no = :inv "
                "  AND pick_zone_type = 'cooler' "
                "  AND batch_session_id IS NOT NULL"
            ),
            {"inv": invoice_no},
        ).fetchall()
    ]

    # 1) Drop pending cooler queue rows (always)
    res = db.session.execute(
        text(
            "DELETE FROM batch_pick_queue "
            "WHERE invoice_no = :inv "
            "  AND pick_zone_type = 'cooler' "
            "  AND status IN ('pending', 'skipped_pending')"
        ),
        {"inv": invoice_no},
    )
    queue_deleted = res.rowcount or 0

    # 2) Clear locks on unpicked InvoiceItems (always)
    res2 = db.session.execute(
        text(
            "UPDATE invoice_items "
            "SET locked_by_batch_id = NULL "
            "WHERE invoice_no = :inv "
            "  AND is_picked = FALSE "
            "  AND locked_by_batch_id IN ( "
            "    SELECT id FROM batch_picking_sessions "
            "    WHERE session_type = 'cooler_route' "
            "  )"
        ),
        {"inv": invoice_no},
    )
    items_unlocked = res2.rowcount or 0

    # 2b) Default (non-full_reset) path: picked queue rows are preserved
    #     for audit, but they must not keep blocking the OLD route's
    #     WAREHOUSE_READY check forever (readiness condition 3 counts
    #     picked-but-unboxed rows via bps.route_id). Flag them
    #     'needs_return' — not deleted, because the picker may physically
    #     have these items in hand and they need follow-up (return to
    #     cooler stock). Readiness only counts status = 'picked'.
    if not full_reset:
        res_nr = db.session.execute(
            text(
                "UPDATE batch_pick_queue "
                "SET status = 'needs_return', "
                "    updated_at = :now "
                "WHERE invoice_no = :inv "
                "  AND pick_zone_type = 'cooler' "
                "  AND status = 'picked'"
            ),
            {"inv": invoice_no, "now": get_utc_now()},
        )
        picked_flagged_for_return = res_nr.rowcount or 0

    box_items_removed = 0
    picked_queue_deleted = 0
    items_unpicked = 0
    boxes_cancelled = 0
    closed_boxes_voided = 0
    picked_flagged_for_return = 0

    if full_reset:
        # 3) Find which boxes contain items for this invoice (before deleting)
        affected_box_ids = db.session.execute(
            text(
                "SELECT DISTINCT cooler_box_id "
                "FROM cooler_box_items "
                "WHERE invoice_no = :inv"
            ),
            {"inv": invoice_no},
        ).scalars().all()

        # 4) Remove cooler_box_items rows for this invoice
        res3 = db.session.execute(
            text(
                "DELETE FROM cooler_box_items "
                "WHERE invoice_no = :inv"
            ),
            {"inv": invoice_no},
        )
        box_items_removed = res3.rowcount or 0

        # 5) Delete picked cooler queue rows for this invoice
        res4 = db.session.execute(
            text(
                "DELETE FROM batch_pick_queue "
                "WHERE invoice_no = :inv "
                "  AND pick_zone_type = 'cooler' "
                "  AND status = 'picked'"
            ),
            {"inv": invoice_no},
        )
        picked_queue_deleted = res4.rowcount or 0

        # 5b) Remove BatchPickedItem records for cooler_route sessions
        db.session.execute(
            text(
                "DELETE FROM batch_picked_items "
                "WHERE invoice_no = :inv "
                "  AND batch_session_id IN ( "
                "    SELECT id FROM batch_picking_sessions "
                "    WHERE session_type = 'cooler_route' "
                "  )"
            ),
            {"inv": invoice_no},
        )

        # 6) Reset is_picked on invoice_items that were picked in cooler context.
        #
        # WHY THE BROAD CONDITION: we cannot filter on the current
        # locked_by_batch_id — once picking completes, the batch auto-clear /
        # orphan-lock cleanup sets locked_by_batch_id = NULL, so a filter on
        # the lock matches nothing by the time a manager runs a full reset
        # (leaving is_picked stuck TRUE forever). We also cannot join through
        # the queue rows, because step 5 above already deleted them. Resetting
        # ALL is_picked = TRUE rows for this invoice is safe HERE because
        # this function only runs for invoices in the cooler unassign flow,
        # and full_reset means the user explicitly confirmed a complete
        # return-to-warehouse: every physically picked item for this invoice
        # (cooler or otherwise) is coming back to stock and must be re-picked
        # when the invoice is re-routed. Normal-zone items use a different
        # is_picked lifecycle that does not pass through this function, and
        # after a full reset they too must be re-picked from scratch.
        res5 = db.session.execute(
            text(
                "UPDATE invoice_items "
                "SET is_picked = FALSE, "
                "    locked_by_batch_id = NULL "
                "WHERE invoice_no = :inv "
                "  AND is_picked = TRUE"
            ),
            {"inv": invoice_no},
        )
        items_unpicked = res5.rowcount or 0

        # 7) Recalculate fill on affected boxes; cancel any that are now empty
        for box_id in affected_box_ids:
            remaining = db.session.execute(
                text(
                    "SELECT COUNT(*), "
                    "       MIN(delivery_sequence), "
                    "       MAX(delivery_sequence) "
                    "FROM cooler_box_items "
                    "WHERE cooler_box_id = :bid"
                ),
                {"bid": box_id},
            ).fetchone()

            if not remaining or remaining[0] == 0:
                # Cancel open boxes; only count when a row actually changed.
                res_open = db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET status = 'cancelled' "
                        "WHERE id = :bid AND status = 'open'"
                    ),
                    {"bid": box_id},
                )
                if (res_open.rowcount or 0) > 0:
                    boxes_cancelled += 1
                else:
                    # The box was already 'closed'. It cannot be un-closed,
                    # but it is now EMPTY — mark it 'cancelled' so it stops
                    # appearing on manifests/labels, and count separately.
                    res_closed = db.session.execute(
                        text(
                            "UPDATE cooler_boxes "
                            "SET status = 'cancelled' "
                            "WHERE id = :bid AND status = 'closed'"
                        ),
                        {"bid": box_id},
                    )
                    closed_boxes_voided += res_closed.rowcount or 0
            else:
                # Box still has items: re-sum fill volume/weight from the
                # remaining cooler_box_items (dimensions from ps_items_dw,
                # weight fallback from invoice_items) and refresh the stop
                # sequence window. Uses picked_qty when set, else expected_qty
                # — mirroring the planner's estimation formula.
                fill_row = db.session.execute(
                    text(
                        "SELECT "
                        "  COALESCE(SUM("
                        "    COALESCE(dw.item_length, 0) * "
                        "    COALESCE(dw.item_width, 0) * "
                        "    COALESCE(dw.item_height, 0) * "
                        "    COALESCE(NULLIF(cbi.picked_qty, 0), cbi.expected_qty, 0)"
                        "  ), 0) AS vol_cm3, "
                        "  COALESCE(SUM("
                        "    COALESCE(dw.item_weight, ii.item_weight, 0) * "
                        "    COALESCE(NULLIF(cbi.picked_qty, 0), cbi.expected_qty, 0)"
                        "  ), 0) AS weight_kg "
                        "FROM cooler_box_items cbi "
                        "LEFT JOIN ps_items_dw dw "
                        "  ON dw.item_code_365 = cbi.item_code "
                        "LEFT JOIN invoice_items ii "
                        "  ON ii.invoice_no = cbi.invoice_no "
                        " AND ii.item_code = cbi.item_code "
                        "WHERE cbi.cooler_box_id = :bid"
                    ),
                    {"bid": box_id},
                ).fetchone()

                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, "
                        "    last_stop_sequence  = :ls, "
                        "    fill_cm3            = :fc, "
                        "    fill_weight_kg      = :fw "
                        "WHERE id = :bid"
                    ),
                    {
                        "fs": remaining[1],
                        "ls": remaining[2],
                        "fc": float(fill_row[0] or 0),
                        "fw": float(fill_row[1] or 0),
                        "bid": box_id,
                    },
                )

    # 8) If a cooler session that held this invoice now has zero
    # non-cancelled queue rows left, mark it Completed so it cannot
    # block warehouse readiness forever (box_close never fires when
    # there are no boxes left to close).
    for _sid in affected_session_ids:
        try:
            remaining = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM batch_pick_queue "
                    "WHERE batch_session_id = :sid "
                    "  AND status != 'cancelled'"
                ),
                {"sid": _sid},
            ).scalar() or 0
            if remaining == 0:
                db.session.execute(
                    text(
                        "UPDATE batch_picking_sessions "
                        "SET status = 'Completed', last_activity_at = :now "
                        "WHERE id = :sid "
                        "  AND session_type = 'cooler_route' "
                        "  AND status NOT IN ('Completed', 'Cancelled', 'Archived')"
                    ),
                    {"sid": _sid, "now": get_utc_now()},
                )
                logger.info(
                    "release_cooler_locks_for_invoice: cooler session %s "
                    "marked Completed (no remaining queue rows) after "
                    "invoice %s released", _sid, invoice_no,
                )
        except Exception as _done_err:
            logger.warning(
                "release_cooler_locks_for_invoice: completion check failed "
                "for session %s: %s", _sid, _done_err,
            )

    return {
        "queue_deleted": queue_deleted,
        "items_unlocked": items_unlocked,
        "box_items_removed": box_items_removed,
        "picked_queue_deleted": picked_queue_deleted,
        "items_unpicked": items_unpicked,
        "boxes_cancelled": boxes_cancelled,
        "closed_boxes_voided": closed_boxes_voided,
        "planned_box_items_removed": planned_box_items_removed,
        "picked_flagged_for_return": picked_flagged_for_return,
    }


def _detach_queue_rows_from_other_sessions(invoice_no, keep_session_id):
    """When an invoice is moved between routes, drop its cooler queue
    rows from any OTHER cooler session so the new route's session owns
    them. Picked rows are preserved (audit trail). Locked InvoiceItem
    rows are released for re-locking by the new session below."""
    db.session.execute(
        text(
            "DELETE FROM batch_pick_queue "
            "WHERE invoice_no = :inv "
            "  AND pick_zone_type = 'cooler' "
            "  AND batch_session_id != :keep "
            "  AND status IN ('pending', 'skipped_pending')"
        ),
        {"inv": invoice_no, "keep": keep_session_id},
    )


def extract_sensitive_for_route_stop_invoices(rsi_list):
    """Extract SENSITIVE items from the given ``RouteStopInvoice`` rows
    into the per-route cooler queue. Idempotent.

    Args:
        rsi_list: iterable of ``RouteStopInvoice`` objects (typically
            the return value of ``services.attach_invoices_to_stop``).

    Returns:
        dict with counters: ``extracted``, ``already_present``,
        ``missing_dimensions``, ``picked_warning``.
    """
    summary = {"extracted": 0, "already_present": 0,
               "missing_dimensions": 0, "picked_warning": 0}

    if not _is_summer_cooler_mode_enabled():
        return summary
    if not rsi_list:
        return summary

    # Collect (invoice_no, route_stop_id) pairs and resolve their route_id.
    rsi_pairs = [(rsi.invoice_no, rsi.route_stop_id) for rsi in rsi_list
                 if getattr(rsi, "invoice_no", None)]
    if not rsi_pairs:
        return summary

    # Resolve invoice → route_id via the Invoice model (since RSI's
    # route_stop carries shipment_id transitively).
    invoice_nos = list({inv for inv, _ in rsi_pairs})
    invoices = {
        inv.invoice_no: inv
        for inv in Invoice.query.filter(
            Invoice.invoice_no.in_(invoice_nos)
        ).all()
    }

    # Group invoices by route_id. Skip any with no route_id.
    by_route = {}
    for inv_no, rs_id in rsi_pairs:
        inv = invoices.get(inv_no)
        if inv is None or inv.route_id is None:
            continue
        by_route.setdefault(inv.route_id, []).append((inv_no, rs_id))

    now = get_utc_now()

    for route_id, pairs in by_route.items():
        # Fetch all items across these invoices in one query.
        invs_for_route = list({inv for inv, _ in pairs})
        items = InvoiceItem.query.filter(
            InvoiceItem.invoice_no.in_(invs_for_route),
        ).all()

        # Determine which items are SENSITIVE in one DwItem query.
        all_codes = {it.item_code for it in items}
        sensitive = _sensitive_codes(all_codes)
        if not sensitive:
            # No SENSITIVE items on this route — do NOT create an empty
            # cooler session. (Pre-fix this would leave a "COOLER-ROUTE-N"
            # shell on every active route.)
            continue
        missing_dims = _items_missing_dimensions(sensitive)

        # Now that we know there's real cooler work, materialize the
        # singleton cooler session for this route.
        session = get_or_create_cooler_session(route_id)
        if session is None:
            continue

        existing_keys = _existing_queue_keys(session.id)

        for item in items:
            if item.item_code not in sensitive:
                continue

            # Edge case: item already picked via the regular flow before
            # the cooler workflow activated. Don't lock, don't queue —
            # log a warning so the warehouse can move the picked item to
            # the cooler manually.
            if getattr(item, "is_picked", False):
                summary["picked_warning"] += 1
                _audit(
                    "cooler.warning_already_picked",
                    f"SENSITIVE item already picked before cooler workflow "
                    f"activated; route={route_id} invoice={item.invoice_no} "
                    f"item={item.item_code}",
                    invoice_no=item.invoice_no, item_code=item.item_code,
                )
                _log_data_quality(
                    item.invoice_no, item.item_code,
                    "already_picked",
                    f"is_picked=True at cooler extraction time on route "
                    f"{route_id}",
                    route_id,
                )
                continue

            # Idempotency check.
            key = (item.invoice_no, item.item_code)
            if key in existing_keys:
                summary["already_present"] += 1
                continue

            # Move queue rows for this invoice off any other cooler session
            # (handles invoice-moved-between-routes case).
            _detach_queue_rows_from_other_sessions(
                item.invoice_no, session.id,
            )

            # Lock the InvoiceItem from regular picking. This is the
            # cold-chain non-negotiable bit — even if the queue insert
            # races/fails below, the lock prevents a regular picker
            # from grabbing the item.
            try:
                item.locked_by_batch_id = session.id
                db.session.flush()
            except Exception as e:
                logger.warning(
                    "cooler extraction: failed to lock item %s/%s: %s",
                    item.invoice_no, item.item_code, e,
                )

            # Insert queue row. ``delivery_sequence`` left NULL — Phase 2
            # lock-sequencing stamps it from RouteStop.seq_no.
            qty_required = float(getattr(item, "qty", 0) or 0)
            # Wrap each per-item insert in a SAVEPOINT so an
            # IntegrityError (e.g. concurrent duplicate) only rolls
            # back this row, not prior inserts/locks/audits in the
            # same extraction transaction.
            try:
                with db.session.begin_nested():
                    db.session.execute(
                        text(
                            "INSERT INTO batch_pick_queue "
                            "(batch_session_id, invoice_no, item_code, "
                            " pick_zone_type, status, qty_required, qty_picked, "
                            " wms_zone, created_at, updated_at) "
                            "VALUES (:sid, :inv, :ic, 'cooler', 'pending', "
                            "        :qty, 0, 'SENSITIVE', :now, :now)"
                        ),
                        {"sid": session.id, "inv": item.invoice_no,
                         "ic": item.item_code, "qty": qty_required,
                         "now": now},
                    )
                summary["extracted"] += 1
                existing_keys.add(key)
            except IntegrityError:
                summary["already_present"] += 1
                continue

            # Ensure BatchSessionInvoice junction row exists (idempotent).
            jn = BatchSessionInvoice.query.filter_by(
                batch_session_id=session.id, invoice_no=item.invoice_no,
            ).first()
            if jn is None:
                db.session.add(BatchSessionInvoice(
                    batch_session_id=session.id,
                    invoice_no=item.invoice_no,
                ))

            if item.item_code in missing_dims:
                summary["missing_dimensions"] += 1
                _log_data_quality(
                    item.invoice_no, item.item_code,
                    "missing_dimensions",
                    f"SENSITIVE item locked + queued without complete "
                    f"dimensions; route={route_id}",
                    route_id,
                )

        _audit(
            "cooler.extract",
            f"Cooler extraction for route={route_id} session_id={session.id}: "
            f"extracted={summary['extracted']} "
            f"already_present={summary['already_present']} "
            f"missing_dimensions={summary['missing_dimensions']} "
            f"picked_warning={summary['picked_warning']}",
        )

    try:
        db.session.commit()
    except Exception as e:
        logger.error("cooler extraction commit failed: %s", e)
        db.session.rollback()
        return summary

    # After locking cooler items, re-evaluate the invoice status for every
    # affected invoice. Without this, an invoice that was already
    # ``ready_for_dispatch`` (all regular items picked) stays incorrectly in
    # that state even though it now has unpicked cooler items locked to the
    # cooler batch. The status should be demoted to ``awaiting_batch_items``
    # so it doesn't appear ready for shipment prematurely.
    affected_invoices = {rsi.invoice_no for rsi in rsi_list if rsi.invoice_no}

    if affected_invoices:
        try:
            from batch_aware_order_status import update_order_status_batch_aware
            for inv_no in affected_invoices:
                update_order_status_batch_aware(inv_no)
            logger.info(
                "cooler extraction: re-evaluated status for %d invoice(s): %s",
                len(affected_invoices),
                ", ".join(sorted(affected_invoices)),
            )
        except Exception as e:
            logger.warning("cooler extraction: status re-evaluation failed: %s", e)

    return summary


def cooler_auto_assign_item(session_id, queue_item_id):
    row = db.session.execute(
        text(
            "SELECT s.cooler_pack_mode, i.route_id, s.route_id "
            "FROM batch_pick_queue bpq "
            "JOIN batch_picking_sessions s ON s.id = bpq.batch_session_id "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "WHERE bpq.id = :qid AND s.id = :sid"
        ),
        {"qid": queue_item_id, "sid": session_id},
    ).fetchone()
    if row is None or (row[0] or "") != "sequential_stop":
        return None

    stop_row = db.session.execute(
        text(
            "SELECT rs.route_stop_id, rs.seq_no "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN route_stop_invoice rsi ON rsi.invoice_no = bpq.invoice_no "
            "JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id "
            "WHERE bpq.id = :qid AND rsi.is_active = :truthy "
            "ORDER BY rs.seq_no DESC LIMIT 1"
        ),
        {"qid": queue_item_id, "truthy": True},
    ).fetchone()
    if stop_row is None:
        return None

    box_row = db.session.execute(
        text(
            "SELECT id FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = ("
            "  SELECT s.delivery_date FROM shipments s WHERE s.id = :rid"
            ") AND status = 'open' "
            "ORDER BY box_no DESC LIMIT 1"
        ),
        {"rid": row[1]},
    ).fetchone()
    if box_row is None:
        return None

    db.session.execute(
        text(
            "INSERT INTO cooler_box_items "
            "(cooler_box_id, invoice_no, customer_code, customer_name, route_stop_id, "
            " delivery_sequence, item_code, item_name, expected_qty, picked_qty, "
            " picked_by, picked_at, queue_item_id, status, created_at, updated_at) "
            "SELECT :box_id, bpq.invoice_no, i.customer_code, i.customer_name, "
            "       rs.route_stop_id, rs.seq_no, bpq.item_code, ii.item_name, "
            "       bpq.qty_required, bpq.qty_picked, :who, :now, bpq.id, 'picked', "
            "       :now, :now "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN invoice_items ii ON ii.invoice_no = bpq.invoice_no AND ii.item_code = bpq.item_code "
            "JOIN route_stop_invoice rsi ON rsi.invoice_no = bpq.invoice_no "
            "JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id "
            "WHERE bpq.id = :qid AND rsi.is_active = :truthy "
            "ORDER BY rs.seq_no DESC LIMIT 1"
        ),
        {"box_id": box_row[0], "who": _current_username(), "now": get_utc_now(), "qid": queue_item_id, "truthy": True},
    )
    return box_row[0]


def _next_route_batch_name(route_id):
    base = f"ROUTE-BATCH-{route_id}"
    existing = {
        n for (n,) in db.session.query(BatchPickingSession.name).filter(
            db.or_(
                BatchPickingSession.name == base,
                BatchPickingSession.name.like(f"{base}-%"),
            ),
        ).all()
    }
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _session_locked(session_id):
    try:
        row = db.session.execute(
            text(
                "SELECT sequence_locked_at FROM batch_picking_sessions WHERE id = :sid"
            ),
            {"sid": session_id},
        ).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def _get_route_id_for_stop(route_stop_id):
    try:
        row = db.session.execute(
            text("SELECT shipment_id FROM route_stop WHERE route_stop_id = :rid"),
            {"rid": route_stop_id},
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _get_or_create_route_batch_session(route_id, creator):
    session = BatchPickingSession.query.filter_by(
        route_id=route_id,
        session_type=ROUTE_BATCH_SESSION_TYPE,
    ).order_by(BatchPickingSession.created_at.desc()).first()
    if session and not _session_locked(session.id):
        return {"id": session.id, "name": session.name, "created": False}
    name = _next_route_batch_name(route_id)
    session = BatchPickingSession(
        name=name,
        batch_number=name,
        zones="MAIN",
        picking_mode="Consolidated",
        created_by=creator or _current_username(),
        status="Created",
    )
    try:
        session.session_type = ROUTE_BATCH_SESSION_TYPE
    except Exception:
        pass
    try:
        session.route_id = route_id
    except Exception:
        pass
    db.session.add(session)
    db.session.flush()
    return {"id": session.id, "name": session.name, "created": True}


def extract_normal_items_for_route_stop_invoices(rsi_list, creator=None):
    """Extract non-SENSITIVE unpicked items into the route's ROUTE-BATCH session.

    SENSITIVE items are owned by the cooler pipeline and are skipped here.
    Idempotent — safe to call multiple times for the same RSI.
    Short-circuits when ``route_batch_mode_enabled`` is OFF.
    """
    summary = {"extracted": 0, "already_present": 0, "skipped_picked": 0}
    if Setting.get(db.session, "route_batch_mode_enabled", "false").lower() != "true":
        return summary
    if not rsi_list:
        return summary

    for rsi in rsi_list:
        route_id = _get_route_id_for_stop(rsi.route_stop_id)
        if not route_id:
            continue

        session = _get_or_create_route_batch_session(route_id, creator or _current_username())
        session_id = session["id"]

        # Handle late-joining invoice when session is already locked.
        if _session_locked(session_id):
            session = _get_or_create_route_batch_session(route_id, creator or _current_username())
            session_id = session["id"]

        items = db.session.query(InvoiceItem).filter(
            InvoiceItem.invoice_no == rsi.invoice_no,
        ).all()

        # Build SENSITIVE code set for this invoice to exclude cooler items.
        all_codes = {it.item_code for it in items}
        sensitive = _sensitive_codes(all_codes) if all_codes else set()

        for item in items:
            # Skip items that belong to the cooler pipeline.
            if item.item_code in sensitive:
                continue

            if item.is_picked:
                summary["skipped_picked"] += 1
                logger.warning(
                    "route batch extraction skipped already-picked item "
                    "invoice=%s item=%s route=%s",
                    item.invoice_no,
                    item.item_code,
                    route_id,
                )
                continue

            if item.locked_by_batch_id == session_id:
                summary["already_present"] += 1
                continue

            # Skip items already owned by a different batch (e.g. manual batch).
            if item.locked_by_batch_id and item.locked_by_batch_id != session_id:
                continue

            item.locked_by_batch_id = session_id
            summary["extracted"] += 1

        # Ensure junction row exists (idempotent).
        existing_jn = BatchSessionInvoice.query.filter_by(
            batch_session_id=session_id, invoice_no=rsi.invoice_no,
        ).first()
        if existing_jn is None:
            db.session.add(BatchSessionInvoice(
                batch_session_id=session_id, invoice_no=rsi.invoice_no,
            ))

    try:
        db.session.commit()
    except Exception as e:
        logger.error("route batch extraction commit failed: %s", e)
        db.session.rollback()

    return summary
