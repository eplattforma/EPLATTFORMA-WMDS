"""Phase 5 cooler picking blueprint.

All cooler picking, box lifecycle, and label/manifest generation lives
here. Backed entirely by raw SQL against the Phase 5 schema
(``cooler_boxes`` + ``cooler_box_items``) plus the Phase 4
``batch_pick_queue`` table — no new ORM models are introduced.

Permission keys:

  - ``cooler.pick``         — picker access to the cooler picking UI
  - ``cooler.manage_boxes`` — create / close / remove / cancel
  - ``cooler.print_labels`` — label and manifest printing

Permission keys ``cooler.*`` are already covered for ``warehouse_manager``
and ``admin`` (via the wildcard); ``picker`` gains ``cooler.pick`` in
``services/permissions.py``.

All routes call into ``services.order_readiness.is_order_ready`` /
``services.batch_picking`` rather than open-coding queue rules.
"""
from datetime import datetime, date
from io import BytesIO

from flask import (
    Blueprint, abort, jsonify, make_response, render_template, request,
    send_file, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from models import ActivityLog, Setting
from services.permissions import require_permission
from services.cooler_pdf import (
    render_cooler_label, render_cooler_manifest, render_route_manifest,
)
from timezone_utils import get_utc_now


cooler_bp = Blueprint("cooler", __name__, url_prefix="/cooler")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _username():
    return getattr(current_user, "username", None) or "anonymous"


def _parse_date(s):
    """Accept ``YYYY-MM-DD`` (str or ``date``); return a ``date`` instance.

    Returns ``None`` on parse failure so callers can ``abort(400)``.
    """
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    if isinstance(s, datetime):
        return s.date()
    if not s:
        return None
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _box_dict(row):
    """Materialise a SQLAlchemy row from ``cooler_boxes`` into a dict."""
    return {
        "id": row[0],
        "route_id": row[1],
        "delivery_date": str(row[2]) if row[2] is not None else None,
        "box_no": row[3],
        "status": row[4],
        "first_stop_sequence": float(row[5]) if row[5] is not None else None,
        "last_stop_sequence": float(row[6]) if row[6] is not None else None,
        "route_label": f"Route {row[1]}" if row[1] is not None else "Route -",
    }


def _fetch_box(box_id):
    row = db.session.execute(
        text(
            "SELECT id, route_id, delivery_date, box_no, status, "
            "       first_stop_sequence, last_stop_sequence "
            "FROM cooler_boxes WHERE id = :id"
        ),
        {"id": box_id},
    ).fetchone()
    if row is None:
        return None
    return _box_dict(row)


def _fetch_box_items(box_id):
    rows = db.session.execute(
        text(
            "SELECT invoice_no, customer_code, customer_name, "
            "       route_stop_id, delivery_sequence, item_code, item_name, "
            "       expected_qty, picked_qty, status "
            "FROM cooler_box_items "
            "WHERE cooler_box_id = :id "
            "ORDER BY delivery_sequence, invoice_no, item_code"
        ),
        {"id": box_id},
    ).fetchall()
    return [
        {
            "invoice_no": r[0],
            "customer_code": r[1],
            "customer_name": r[2],
            "route_stop_id": r[3],
            "delivery_sequence": float(r[4]) if r[4] is not None else None,
            "item_code": r[5],
            "item_name": r[6],
            "expected_qty": float(r[7]) if r[7] is not None else 0.0,
            "picked_qty": float(r[8]) if r[8] is not None else 0.0,
            "status": r[9],
        }
        for r in rows
    ]


def _audit(activity_type, details, invoice_no=None, item_code=None):
    db.session.add(ActivityLog(
        picker_username=_username(),
        activity_type=activity_type,
        invoice_no=invoice_no,
        item_code=item_code,
        details=details,
    ))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@cooler_bp.route("/route-list")
@login_required
@require_permission("cooler.pick")
def route_list():
    """List route_id + delivery_date pairs that have pending cooler items."""
    rows = db.session.execute(
        text(
            "SELECT bpq.invoice_no, bpq.item_code, bpq.status, "
            "       i.routing AS route_id, i.upload_date AS delivery_date "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "ORDER BY i.routing, i.upload_date, bpq.invoice_no"
        )
    ).fetchall()

    grouped = {}
    for r in rows:
        key = (r[3] or "-", r[4] or "-")
        bucket = grouped.setdefault(key, {"pending": 0, "picked": 0, "exception": 0, "total": 0})
        bucket["total"] += 1
        st = (r[2] or "").lower()
        if st == "pending":
            bucket["pending"] += 1
        elif st == "picked":
            bucket["picked"] += 1
        elif st == "exception":
            bucket["exception"] += 1

    routes = [
        {
            "route_id": k[0],
            "delivery_date": k[1],
            "pending": v["pending"],
            "picked": v["picked"],
            "exception": v["exception"],
            "total": v["total"],
        }
        for k, v in sorted(grouped.items())
        if v["pending"] > 0 or v["picked"] > 0 or v["exception"] > 0
    ]
    return render_template("cooler/route_list.html", routes=routes)


@cooler_bp.route("/route/<route_id>/<delivery_date>")
@login_required
@require_permission("cooler.pick")
def route_picking(route_id, delivery_date):
    """Cooler picking screen for a given route + date.

    Sort order (per brief §5.4):
      1. Route / shipment, 2. RouteStop.seq_no, 3. Customer code,
      4. Invoice number, 5. Item code.
    """
    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_required, "
            "       bpq.qty_picked, bpq.status, bpq.wms_zone, "
            "       i.customer_name, i.customer_code, "
            "       rs.seq_no, rs.route_stop_id "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN route_stop_invoice rsi "
            "       ON rsi.invoice_no = bpq.invoice_no "
            "      AND rsi.is_active = :truthy "
            "LEFT JOIN route_stop rs "
            "       ON rs.route_stop_id = rsi.route_stop_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.routing = :route_id "
            "  AND i.upload_date = :delivery_date "
            "ORDER BY rs.seq_no NULLS LAST, i.customer_code, "
            "         bpq.invoice_no, bpq.item_code"
        ),
        {"route_id": str(route_id), "delivery_date": str(delivery_date),
         "truthy": True},
    ).fetchall()
    queue = [
        {
            "queue_item_id": r[0],
            "invoice_no": r[1],
            "item_code": r[2],
            "expected_qty": float(r[3]) if r[3] is not None else 0.0,
            "picked_qty": float(r[4]) if r[4] is not None else 0.0,
            "status": r[5],
            "wms_zone": r[6],
            "customer_name": r[7],
            "customer_code": r[8],
            "stop_seq_no": float(r[9]) if r[9] is not None else None,
            "route_stop_id": r[10],
        }
        for r in rows
    ]
    boxes = db.session.execute(
        text(
            "SELECT id, route_id, delivery_date, box_no, status, "
            "       first_stop_sequence, last_stop_sequence "
            "FROM cooler_boxes "
            "WHERE delivery_date = :delivery_date "
            "ORDER BY box_no"
        ),
        {"delivery_date": str(delivery_date)},
    ).fetchall()
    boxes = [_box_dict(b) for b in boxes]
    return render_template(
        "cooler/route_picking.html",
        route_id=route_id, delivery_date=delivery_date,
        queue=queue, boxes=boxes,
    )


@cooler_bp.route("/box/create", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def box_create():
    """Create a cooler box. Idempotent on (route_id, delivery_date, box_no)."""
    data = request.get_json(silent=True) or request.form
    try:
        route_id = int(data.get("route_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "route_id is required and must be int"}), 400
    delivery_date = _parse_date(data.get("delivery_date"))
    if delivery_date is None:
        return jsonify({"error": "delivery_date must be YYYY-MM-DD"}), 400
    try:
        box_no = int(data.get("box_no"))
    except (TypeError, ValueError):
        return jsonify({"error": "box_no is required and must be int"}), 400

    existing = db.session.execute(
        text(
            "SELECT id, status FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = :dd AND box_no = :bn"
        ),
        {"rid": route_id, "dd": delivery_date, "bn": box_no},
    ).fetchone()
    if existing is not None:
        return jsonify({"cooler_box_id": existing[0], "status": existing[1],
                        "created": False}), 200

    now = get_utc_now()
    try:
        result = db.session.execute(
            text(
                "INSERT INTO cooler_boxes "
                "(route_id, delivery_date, box_no, status, created_by, created_at) "
                "VALUES (:rid, :dd, :bn, 'open', :who, :now) "
                "RETURNING id"
            ),
            {"rid": route_id, "dd": delivery_date, "bn": box_no,
             "who": _username(), "now": now},
        )
        new_id = result.scalar()
    except Exception:
        # SQLite has no RETURNING in older versions; fall back to a
        # plain INSERT + SELECT lookup using the natural unique key.
        db.session.rollback()
        try:
            db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, created_by, created_at) "
                    "VALUES (:rid, :dd, :bn, 'open', :who, :now)"
                ),
                {"rid": route_id, "dd": delivery_date, "bn": box_no,
                 "who": _username(), "now": now},
            )
            new_id = db.session.execute(
                text(
                    "SELECT id FROM cooler_boxes "
                    "WHERE route_id = :rid AND delivery_date = :dd AND box_no = :bn"
                ),
                {"rid": route_id, "dd": delivery_date, "bn": box_no},
            ).scalar()
        except IntegrityError:
            db.session.rollback()
            existing = db.session.execute(
                text(
                    "SELECT id, status FROM cooler_boxes "
                    "WHERE route_id = :rid AND delivery_date = :dd AND box_no = :bn"
                ),
                {"rid": route_id, "dd": delivery_date, "bn": box_no},
            ).fetchone()
            if existing is not None:
                return jsonify({"cooler_box_id": existing[0],
                                "status": existing[1], "created": False}), 200
            raise

    _audit(
        "cooler.box_created",
        f"Cooler box #{new_id} route={route_id} date={delivery_date} "
        f"box_no={box_no} by {_username()}",
    )
    db.session.commit()
    return jsonify({"cooler_box_id": new_id, "status": "open",
                    "created": True}), 201


@cooler_bp.route("/box/<int:box_id>/assign-item", methods=["POST"])
@login_required
@require_permission("cooler.pick")
def box_assign_item(box_id):
    """Assign a queue row to an open cooler box."""
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "open":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; "
                     f"only open boxes accept item assignments."
        }), 400

    data = request.get_json(silent=True) or request.form
    try:
        queue_item_id = int(data.get("queue_item_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "queue_item_id is required and must be int"}), 400
    try:
        picked_qty = float(data.get("picked_qty"))
    except (TypeError, ValueError):
        return jsonify({"error": "picked_qty is required and must be numeric"}), 400

    qrow = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_required, "
            "       bpq.status, bpq.pick_zone_type, "
            "       i.customer_code, i.customer_name "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "WHERE bpq.id = :qid"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if qrow is None:
        abort(404)
    if qrow[5] != "cooler":
        return jsonify({
            "error": f"Queue item {queue_item_id} is not a cooler row "
                     f"(pick_zone_type={qrow[5]})."
        }), 400
    if qrow[4] not in ("pending",):
        return jsonify({
            "error": f"Queue item {queue_item_id} status={qrow[4]}; "
                     f"only pending rows can be assigned."
        }), 400

    invoice_no = qrow[1]
    item_code = qrow[2]
    expected_qty = float(qrow[3]) if qrow[3] is not None else 0.0

    # Look up route_stop linkage for delivery_sequence snapshot.
    stop_row = db.session.execute(
        text(
            "SELECT rs.route_stop_id, rs.seq_no "
            "FROM route_stop_invoice rsi "
            "JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id "
            "WHERE rsi.invoice_no = :inv AND rsi.is_active = :truthy "
            "ORDER BY rs.seq_no LIMIT 1"
        ),
        {"inv": invoice_no, "truthy": True},
    ).fetchone()
    route_stop_id = stop_row[0] if stop_row else None
    delivery_sequence = stop_row[1] if stop_row else None

    item_name_row = db.session.execute(
        text(
            "SELECT item_name FROM invoice_items "
            "WHERE invoice_no = :inv AND item_code = :ic LIMIT 1"
        ),
        {"inv": invoice_no, "ic": item_code},
    ).fetchone()
    item_name = item_name_row[0] if item_name_row else None

    now = get_utc_now()
    db.session.execute(
        text(
            "INSERT INTO cooler_box_items "
            "(cooler_box_id, invoice_no, customer_code, customer_name, "
            " route_stop_id, delivery_sequence, item_code, item_name, "
            " expected_qty, picked_qty, picked_by, picked_at, "
            " queue_item_id, status, created_at, updated_at) "
            "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
            "        :exp, :pq, :who, :now, :qid, 'picked', :now, :now)"
        ),
        {
            "bid": box_id, "inv": invoice_no, "cc": qrow[6], "cn": qrow[7],
            "rsid": route_stop_id, "seq": delivery_sequence,
            "ic": item_code, "iname": item_name,
            "exp": expected_qty, "pq": picked_qty, "who": _username(),
            "now": now, "qid": queue_item_id,
        },
    )
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'picked', picked_by = :who, picked_at = :now, "
            "    qty_picked = :pq, updated_at = :now "
            "WHERE id = :qid AND status = 'pending'"
        ),
        {"who": _username(), "now": now, "pq": picked_qty, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_assigned",
        f"Cooler box #{box_id} <- queue #{queue_item_id} "
        f"invoice={invoice_no} item={item_code} qty={picked_qty} by {_username()}",
        invoice_no=invoice_no, item_code=item_code,
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "picked"}), 200


@cooler_bp.route("/box/<int:box_id>/remove-item", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def box_remove_item(box_id):
    """Remove an item from an open cooler box; reverse the assignment."""
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "open":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; "
                     f"items can only be removed from open boxes."
        }), 400

    data = request.get_json(silent=True) or request.form
    try:
        queue_item_id = int(data.get("queue_item_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "queue_item_id is required and must be int"}), 400

    cb_row = db.session.execute(
        text(
            "SELECT id, invoice_no, item_code FROM cooler_box_items "
            "WHERE cooler_box_id = :bid AND queue_item_id = :qid LIMIT 1"
        ),
        {"bid": box_id, "qid": queue_item_id},
    ).fetchone()
    if cb_row is None:
        return jsonify({"error": "No matching cooler_box_items row to remove."}), 404

    db.session.execute(
        text("DELETE FROM cooler_box_items WHERE id = :id"),
        {"id": cb_row[0]},
    )
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'pending', picked_by = NULL, picked_at = NULL, "
            "    qty_picked = 0, updated_at = :now "
            "WHERE id = :qid"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_removed",
        f"Cooler box #{box_id} -> reverted queue #{queue_item_id} "
        f"invoice={cb_row[1]} item={cb_row[2]} by {_username()}",
        invoice_no=cb_row[1], item_code=cb_row[2],
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "pending"}), 200


@cooler_bp.route("/box/<int:box_id>/close", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def box_close(box_id):
    """Close an open cooler box and stamp its stop range."""
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "open":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; only open boxes can be closed."
        }), 400

    seq_row = db.session.execute(
        text(
            "SELECT MIN(delivery_sequence), MAX(delivery_sequence) "
            "FROM cooler_box_items "
            "WHERE cooler_box_id = :bid AND delivery_sequence IS NOT NULL"
        ),
        {"bid": box_id},
    ).fetchone()
    first_seq = seq_row[0] if seq_row else None
    last_seq = seq_row[1] if seq_row else None

    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE cooler_boxes "
            "SET status = 'closed', closed_by = :who, closed_at = :now, "
            "    first_stop_sequence = :fs, last_stop_sequence = :ls "
            "WHERE id = :bid"
        ),
        {"who": _username(), "now": now,
         "fs": first_seq, "ls": last_seq, "bid": box_id},
    )
    _audit(
        "cooler.box_closed",
        f"Cooler box #{box_id} closed by {_username()} "
        f"first_seq={first_seq} last_seq={last_seq}",
    )
    db.session.commit()
    return jsonify({
        "cooler_box_id": box_id, "status": "closed",
        "first_stop_sequence": float(first_seq) if first_seq is not None else None,
        "last_stop_sequence": float(last_seq) if last_seq is not None else None,
    }), 200


@cooler_bp.route("/box/<int:box_id>/reopen", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def box_reopen(box_id):
    """Re-open a previously closed cooler box.

    Only ``closed`` boxes can be re-opened (cancelled boxes are terminal —
    their items have already been reverted to pending). The stop-range
    stamps are intentionally left in place for audit; ``box_close`` will
    overwrite them on the next close.
    """
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "closed":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; only closed "
                     f"boxes can be re-opened."
        }), 400
    db.session.execute(
        text("UPDATE cooler_boxes SET status = 'open' WHERE id = :bid"),
        {"bid": box_id},
    )
    _audit(
        "cooler.box_reopened",
        f"Cooler box #{box_id} re-opened by {_username()}",
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "status": "open"}), 200


@cooler_bp.route("/box/<int:box_id>/cancel", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def box_cancel(box_id):
    """Cancel an open box; revert all assigned items back to pending."""
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "open":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; only open boxes can be cancelled."
        }), 400

    rows = db.session.execute(
        text(
            "SELECT queue_item_id FROM cooler_box_items "
            "WHERE cooler_box_id = :bid AND queue_item_id IS NOT NULL"
        ),
        {"bid": box_id},
    ).fetchall()
    now = get_utc_now()
    for r in rows:
        db.session.execute(
            text(
                "UPDATE batch_pick_queue "
                "SET status = 'pending', picked_by = NULL, picked_at = NULL, "
                "    qty_picked = 0, updated_at = :now "
                "WHERE id = :qid"
            ),
            {"now": now, "qid": r[0]},
        )
    db.session.execute(
        text("DELETE FROM cooler_box_items WHERE cooler_box_id = :bid"),
        {"bid": box_id},
    )
    db.session.execute(
        text(
            "UPDATE cooler_boxes SET status = 'cancelled' "
            "WHERE id = :bid"
        ),
        {"bid": box_id},
    )
    _audit(
        "cooler.box_cancelled",
        f"Cooler box #{box_id} cancelled by {_username()}; "
        f"reverted {len(rows)} queue row(s)",
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "status": "cancelled"}), 200


# ---------------------------------------------------------------------------
# Exception handling (§5.8)
# ---------------------------------------------------------------------------
@cooler_bp.route("/queue/<int:queue_item_id>/exception", methods=["POST"])
@login_required
@require_permission("cooler.pick")
def queue_exception(queue_item_id):
    """Mark a cooler queue row as ``exception`` with reason."""
    data = request.get_json(silent=True) or request.form
    reason = (data.get("reason") or "").strip() or "unspecified"

    row = db.session.execute(
        text(
            "SELECT invoice_no, item_code, status, pick_zone_type "
            "FROM batch_pick_queue WHERE id = :qid"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if row is None:
        abort(404)
    if row[3] != "cooler":
        return jsonify({"error": "Not a cooler queue row."}), 400
    if row[2] != "pending":
        return jsonify({
            "error": f"Queue item {queue_item_id} status={row[2]}; "
                     f"only pending rows can be exceptioned here."
        }), 400

    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'exception', updated_at = :now "
            "WHERE id = :qid"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.queue_exception",
        f"Cooler queue #{queue_item_id} invoice={row[0]} item={row[1]} "
        f"marked exception by {_username()}; reason={reason}",
        invoice_no=row[0], item_code=row[1],
    )
    db.session.commit()
    return jsonify({"queue_item_id": queue_item_id, "status": "exception",
                    "reason": reason}), 200


@cooler_bp.route("/queue/<int:queue_item_id>/move-to-normal", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def queue_move_to_normal(queue_item_id):
    """Admin-only: move a cooler queue row back to normal."""
    return _move_zone(queue_item_id, "cooler", "normal")


@cooler_bp.route("/queue/<int:queue_item_id>/move-to-cooler", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
def queue_move_to_cooler(queue_item_id):
    """Admin-only: move a normal queue row to cooler."""
    return _move_zone(queue_item_id, "normal", "cooler")


def _move_zone(queue_item_id, expected_from, target):
    row = db.session.execute(
        text(
            "SELECT invoice_no, item_code, status, pick_zone_type, wms_zone "
            "FROM batch_pick_queue WHERE id = :qid"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if row is None:
        abort(404)
    if row[3] != expected_from:
        return jsonify({
            "error": f"Queue item {queue_item_id} pick_zone_type={row[3]} "
                     f"(expected {expected_from})."
        }), 400
    if row[2] != "pending":
        return jsonify({
            "error": f"Queue item {queue_item_id} status={row[2]}; "
                     f"only pending rows can be moved."
        }), 400

    now = get_utc_now()
    snapshot_zone = row[4]
    if target == "cooler" and not snapshot_zone:
        snapshot_zone = "SENSITIVE"

    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET pick_zone_type = :tgt, wms_zone = :zone, updated_at = :now "
            "WHERE id = :qid"
        ),
        {"tgt": target, "zone": snapshot_zone, "now": now, "qid": queue_item_id},
    )
    _audit(
        f"cooler.move_to_{target}",
        f"Queue #{queue_item_id} invoice={row[0]} item={row[1]} "
        f"moved {expected_from} -> {target} by {_username()} "
        f"(wms_zone snapshot={snapshot_zone})",
        invoice_no=row[0], item_code=row[1],
    )
    db.session.commit()
    return jsonify({
        "queue_item_id": queue_item_id, "pick_zone_type": target,
        "wms_zone": snapshot_zone,
    }), 200


# ---------------------------------------------------------------------------
# PDF endpoints
# ---------------------------------------------------------------------------
def _pdf_response(pdf_bytes, filename):
    resp = make_response(send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
    ))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@cooler_bp.route("/box/<int:box_id>/label")
@login_required
@require_permission("cooler.print_labels")
def box_label(box_id):
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    size = (request.args.get("size") or "thermal").lower()
    if size not in ("thermal", "a4"):
        size = "thermal"
    pdf = render_cooler_label(box, size=size)
    # Stamp label_printed_at for traceability (idempotent on multiple prints).
    db.session.execute(
        text("UPDATE cooler_boxes SET label_printed_at = :now WHERE id = :bid"),
        {"now": get_utc_now(), "bid": box_id},
    )
    db.session.commit()
    return _pdf_response(pdf, f"cooler_box_{box_id}_label_{size}.pdf")


@cooler_bp.route("/box/<int:box_id>/manifest")
@login_required
@require_permission("cooler.print_labels")
def box_manifest(box_id):
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    items = _fetch_box_items(box_id)
    pdf = render_cooler_manifest(box, items, generated_at=get_utc_now().isoformat())
    return _pdf_response(pdf, f"cooler_box_{box_id}_manifest.pdf")


@cooler_bp.route("/route/<route_id>/<delivery_date>/manifest")
@login_required
@require_permission("cooler.print_labels")
def route_manifest(route_id, delivery_date):
    if _parse_date(delivery_date) is None:
        return jsonify({"error": "delivery_date must be YYYY-MM-DD"}), 400

    # All boxes whose items belong to invoices on this route + date.
    box_rows = db.session.execute(
        text(
            "SELECT DISTINCT cb.id, cb.route_id, cb.delivery_date, cb.box_no, "
            "       cb.status, cb.first_stop_sequence, cb.last_stop_sequence "
            "FROM cooler_boxes cb "
            "JOIN cooler_box_items cbi ON cbi.cooler_box_id = cb.id "
            "JOIN invoices i ON i.invoice_no = cbi.invoice_no "
            "WHERE cb.delivery_date = :dd "
            "  AND i.routing = :rid "
            "ORDER BY cb.box_no"
        ),
        {"dd": str(delivery_date), "rid": str(route_id)},
    ).fetchall()
    boxes_with_items = [
        (_box_dict(b), _fetch_box_items(b[0])) for b in box_rows
    ]
    pdf = render_route_manifest(
        route_id=route_id, delivery_date=delivery_date,
        boxes_with_items=boxes_with_items,
        generated_at=get_utc_now().isoformat(),
    )
    return _pdf_response(pdf, f"route_{route_id}_{delivery_date}_cooler_manifest.pdf")


# ---------------------------------------------------------------------------
# Driver overlay helper (template-side; flag-gated)
# ---------------------------------------------------------------------------
def is_driver_view_enabled():
    """Read ``cooler_driver_view_enabled`` flag (defaults OFF)."""
    try:
        return Setting.get(db.session, "cooler_driver_view_enabled", "false").lower() == "true"
    except Exception:
        return False


def cooler_boxes_for_route(route_id, delivery_date):
    """Return cooler boxes for the route+date overlay (read-only view)."""
    rows = db.session.execute(
        text(
            "SELECT cb.id, cb.route_id, cb.delivery_date, cb.box_no, "
            "       cb.status, cb.first_stop_sequence, cb.last_stop_sequence, "
            "       (SELECT COUNT(*) FROM cooler_box_items "
            "        WHERE cooler_box_id = cb.id) AS item_count "
            "FROM cooler_boxes cb "
            "WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "ORDER BY cb.box_no"
        ),
        {"rid": route_id, "dd": str(delivery_date)},
    ).fetchall()
    out = []
    for r in rows:
        d = _box_dict(r)
        d["item_count"] = int(r[7] or 0)
        d["manifest_url"] = url_for("cooler.box_manifest", box_id=r[0])
        out.append(d)
    return out


def register_template_helpers(app):
    """Expose ``cooler_driver_view_enabled`` + ``cooler_boxes_for_route``
    to Jinja so the route_detail.html overlay can render itself without
    a fetch round-trip. The block stays invisible when the flag is off.
    """
    @app.context_processor
    def _inject_cooler_helpers():
        return {
            "cooler_driver_view_enabled": is_driver_view_enabled,
            "cooler_boxes_for_route": cooler_boxes_for_route,
        }
