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
    Blueprint, abort, current_app, flash, jsonify, make_response,
    redirect, render_template, request, send_file, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from models import ActivityLog, BatchPickingSession, Setting
from services.permissions import require_permission
from services.cooler_pdf import (
    render_cooler_label, render_cooler_manifest, render_route_manifest,
)
from services.cooler_box_planner import generate_box_plan
from timezone_utils import get_utc_now


# Per-permission role allow-lists for the cooler workflow. These run
# BEFORE ``@require_permission`` and block regardless of the global
# ``permissions_enforcement_enabled`` setting. They mirror the role
# grants in ``services/permissions.ROLE_PERMISSIONS``:
#   - picker:            cooler.pick
#   - warehouse_manager: cooler.* (pick + manage_boxes + print_labels)
#   - admin:             * (everything)
#
# Pickers must NOT be able to call box-lifecycle / label-print routes
# (cooler.manage_boxes, cooler.print_labels) even when permissions
# enforcement is off, so each route uses the guard matching its
# documented permission key.
_COOLER_ROLES_PICK = frozenset({"admin", "warehouse_manager", "picker"})
_COOLER_ROLES_MANAGE = frozenset({"admin", "warehouse_manager"})
_COOLER_ROLES_PRINT = frozenset({"admin", "warehouse_manager"})

# Union, for tests/introspection only.
_COOLER_ALLOWED_ROLES = _COOLER_ROLES_PICK | _COOLER_ROLES_MANAGE | _COOLER_ROLES_PRINT


def _make_role_guard(allowed_roles, perm_label):
    from functools import wraps

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not getattr(current_user, "is_authenticated", False):
                abort(401)
            role = (getattr(current_user, "role", None) or "").lower()
            if role not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)
        wrapper._cooler_perm_label = perm_label
        return wrapper
    return decorator


_require_cooler_pick = _make_role_guard(_COOLER_ROLES_PICK, "cooler.pick")
_require_cooler_manage = _make_role_guard(_COOLER_ROLES_MANAGE, "cooler.manage_boxes")
_require_cooler_print = _make_role_guard(_COOLER_ROLES_PRINT, "cooler.print_labels")


# ---------------------------------------------------------------------------
# Feature-flag gates
# ---------------------------------------------------------------------------
# The cooler workflow ships behind two production-default-OFF flags so the
# rollout can be paused at any time without redeploying:
#
#   cooler_picking_enabled  - gates picker/box-mutation routes
#                             (cooler.pick + cooler.manage_boxes)
#   cooler_labels_enabled   - gates PDF label / manifest routes
#                             (cooler.print_labels)
#
# When a flag is OFF the route returns HTTP 404 (feature hidden) regardless
# of the user's role / permission grants. ``summer_cooler_mode_enabled``
# only stops NEW SENSITIVE rows from being routed to the cooler queue; it
# does NOT disable mutable cooler box operations against existing/stale
# rows, which is why the per-surface flag gate is required here.
def _flag_enabled(key):
    try:
        return Setting.get(db.session, key, "false").lower() == "true"
    except Exception:
        return False


def _make_flag_gate(flag_key):
    from functools import wraps

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not _flag_enabled(flag_key):
                abort(404)
            return view(*args, **kwargs)
        wrapper._cooler_flag_key = flag_key
        return wrapper
    return decorator


_require_picking_flag = _make_flag_gate("cooler_picking_enabled")
_require_labels_flag = _make_flag_gate("cooler_labels_enabled")

# Backwards-compatible alias used by older tests / call sites that
# only need to assert "any cooler role is allowed". New code should
# pick the permission-specific guard above.
_require_cooler_role = _require_cooler_pick


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


def _is_cooler_route_pack_complete(route_id, delivery_date):
    """Return True only when ALL of these conditions hold:

    1. No cooler queue rows are unsequenced (delivery_sequence IS NULL).
    2. No cooler queue rows are pending (status = 'picked' is required).
    3. Every picked cooler row has an entry in cooler_box_items.
    4. No box that contains items is still open.
    5. No duplicate queue_item_id exists in cooler_box_items for this route.

    Failing any condition keeps the session active so the manager can fix
    the outstanding issue before the route is marked Completed.
    """
    rid = int(route_id)
    dd = str(delivery_date)

    # 1. Unsequenced rows
    unsequenced = db.session.execute(
        text(
            "SELECT COUNT(*) FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :rid AND s.delivery_date = :dd "
            "  AND bpq.delivery_sequence IS NULL"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if unsequenced > 0:
        return False

    # 2. Pending rows
    pending = db.session.execute(
        text(
            "SELECT COUNT(*) FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :rid AND s.delivery_date = :dd "
            "  AND bpq.status = 'pending'"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if pending > 0:
        return False

    # 2b. Planned rows (pre-assigned but not yet physically picked)
    planned = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "  AND cbi.status = 'planned'"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if planned > 0:
        return False

    # 3. Picked rows that are not yet in a box
    unboxed = db.session.execute(
        text(
            "SELECT COUNT(*) FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :rid AND s.delivery_date = :dd "
            "  AND bpq.status = 'picked' "
            "  AND NOT EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.queue_item_id = bpq.id"
            "  )"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if unboxed > 0:
        return False

    # 4. Open boxes that contain items
    open_with_items = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_boxes cb "
            "WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "  AND cb.status = 'open' "
            "  AND EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.cooler_box_id = cb.id"
            "  )"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if open_with_items > 0:
        return False

    # 5. Duplicate queue_item_id assignments
    duplicates = db.session.execute(
        text(
            "SELECT COUNT(*) FROM ("
            "  SELECT cbi.queue_item_id "
            "  FROM cooler_box_items cbi "
            "  JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "  WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "    AND cbi.queue_item_id IS NOT NULL "
            "  GROUP BY cbi.queue_item_id HAVING COUNT(*) > 1"
            ") AS dups"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if duplicates > 0:
        return False

    return True


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
            "SELECT cbi.invoice_no, cbi.customer_code, cbi.customer_name, "
            "       cbi.route_stop_id, cbi.delivery_sequence, cbi.item_code, "
            "       cbi.item_name, cbi.expected_qty, cbi.picked_qty, cbi.status, "
            "       ii.unit_type, ii.pack "
            "FROM cooler_box_items cbi "
            "LEFT JOIN invoice_items ii "
            "       ON ii.invoice_no = cbi.invoice_no "
            "      AND ii.item_code = cbi.item_code "
            "WHERE cbi.cooler_box_id = :id "
            "ORDER BY cbi.delivery_sequence, cbi.invoice_no, cbi.item_code"
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
            "unit_type": r[10],
            "pack": r[11],
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
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def route_list():
    """List route_id + delivery_date pairs that have pending cooler items.

    route identity comes from ``Invoice.route_id``
    (FK to shipments.id) and the date comes from ``Shipment.delivery_date``,
    NOT from ``Invoice.routing`` (a free-text label) or ``Invoice.upload_date``
    (a string). Cooler boxes and the dispatch system both key on the
    real shipment FK; any other field would mis-attribute boxes.
    """
    rows = db.session.execute(
        text(
            "SELECT bpq.invoice_no, bpq.item_code, bpq.status, "
            "       i.route_id AS route_id, s.delivery_date AS delivery_date "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "ORDER BY i.route_id, s.delivery_date, bpq.invoice_no"
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
        for k, v in sorted(grouped.items(), key=lambda x: (str(x[0][0]), str(x[0][1])))
        if v["pending"] > 0 or v["picked"] > 0 or v["exception"] > 0
    ]
    if not routes:
        from flask import flash
        flash(
            "No pending cooler items found. Make sure SENSITIVE items have been "
            "attached to a route with summer_cooler_mode_enabled ON.",
            "info",
        )
    from services.cooler_box_planner import pre_pick_estimate
    estimates = {}
    for route in routes:
        try:
            estimates[(route["route_id"], str(route["delivery_date"]))] = (
                pre_pick_estimate(route["route_id"], route["delivery_date"])
            )
        except Exception as exc:
            current_app.logger.warning(
                "pre_pick_estimate failed for route %s: %s", route["route_id"], exc
            )
            estimates[(route["route_id"], str(route["delivery_date"]))] = None

    # Box type options for the pre-plan dropdown
    try:
        _bt_rows = db.session.execute(text(
            "SELECT id, name, internal_volume_cm3, fill_efficiency, "
            "       ROUND((internal_volume_cm3 * fill_efficiency / 1000)::numeric, 1) "
            "       AS effective_capacity_l "
            "FROM cooler_box_types WHERE is_active = true ORDER BY sort_order, name"
        )).fetchall()
        box_types_list = [
            {"id": r[0], "name": r[1], "effective_capacity_l": r[4]}
            for r in _bt_rows
        ]
    except Exception:
        box_types_list = []

    # Count existing boxes per route so the template can show the pre-planned badge
    box_counts = {}
    if routes:
        try:
            _bc_rows = db.session.execute(text(
                "SELECT route_id, delivery_date::text, COUNT(*) "
                "FROM cooler_boxes GROUP BY route_id, delivery_date"
            )).fetchall()
            for _r in _bc_rows:
                box_counts[(_r[0], _r[1])] = int(_r[2])
        except Exception:
            pass

    for route in routes:
        route["box_count"] = box_counts.get(
            (route["route_id"], str(route["delivery_date"])), 0
        )

    return render_template(
        "cooler/route_list.html",
        routes=routes,
        estimates=estimates,
        box_types=box_types_list,
    )


@cooler_bp.route("/route/<route_id>/<delivery_date>")
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def route_picking(route_id, delivery_date):
    """Cooler picking screen for a given route + date.

    Sort order (per brief §5.4):
      1. Route / shipment, 2. RouteStop.seq_no, 3. Customer code,
      4. Invoice number, 5. Item code.
    """
    # filter by Invoice.route_id (FK to shipments.id)
    # and Shipment.delivery_date — NOT by Invoice.routing (free-text
    # label) or Invoice.upload_date (string). Cooler boxes are keyed
    # on the real shipment FK; any other field would mis-attribute the
    # picker's work-list.
    try:
        _route_id_int = int(route_id)
    except (TypeError, ValueError):
        _route_id_int = None
    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_required, "
            "       bpq.qty_picked, bpq.status, bpq.wms_zone, "
            "       i.customer_name, i.customer_code, "
            "       rs.seq_no, rs.route_stop_id, bpq.delivery_sequence, "
            "       ii.item_name "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "LEFT JOIN route_stop_invoice rsi "
            "       ON rsi.invoice_no = bpq.invoice_no "
            "      AND rsi.is_active = :truthy "
            "LEFT JOIN route_stop rs "
            "       ON rs.route_stop_id = rsi.route_stop_id "
            "LEFT JOIN invoice_items ii "
            "       ON ii.invoice_no = bpq.invoice_no "
            "      AND ii.item_code = bpq.item_code "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :route_id "
            "  AND s.delivery_date = :delivery_date "
            "ORDER BY bpq.delivery_sequence NULLS LAST, "
            "         rs.seq_no NULLS LAST, i.customer_code, "
            "         bpq.invoice_no, bpq.item_code"
        ),
        {"route_id": _route_id_int, "delivery_date": str(delivery_date),
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
            "delivery_sequence": float(r[11]) if r[11] is not None else None,
            "item_name": r[12] or "",
        }
        for r in rows
    ]
    # Phase 6 — split queue into "Sequenced (pickable)" vs
    # "Unsequenced (blocked until lock-sequencing)".
    sequenced = [q for q in queue if q["delivery_sequence"] is not None]
    unsequenced = [q for q in queue if q["delivery_sequence"] is None]

    # Map queue_item_id -> box_no for items already assigned to a cooler
    # box on this route+date. Lets the template show "→ Box #N" instead
    # of the Assign form so users get immediate visual feedback (and
    # cannot accidentally create duplicate cooler_box_items rows).
    assigned_rows = db.session.execute(
        text(
            "SELECT cbi.queue_item_id, cb.box_no, cbi.status "
            "FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "  AND cbi.queue_item_id IS NOT NULL"
        ),
        {"rid": _route_id_int, "dd": str(delivery_date)},
    ).fetchall()
    assigned_to_box = {int(r[0]): int(r[1]) for r in assigned_rows}
    # planned_box: items pre-assigned to a box but not yet physically picked
    planned_box = {int(r[0]): int(r[1]) for r in assigned_rows if r[2] == "planned"}

    # Phase 6 — fetch the LATEST per-route cooler session lock state.
    # We look up by route_id (preferred) and fall back to the legacy
    # name pattern, ordered by created_at DESC so late-addition sibling
    # batches (COOLER-ROUTE-<id>-2, -3, ...) take precedence.
    lock_row = db.session.execute(text(
        "SELECT id, sequence_locked_at, sequence_locked_by, name, status, assigned_to "
        "FROM batch_picking_sessions "
        "WHERE session_type = 'cooler_route' "
        "  AND (route_id = :rid OR name = :legacy "
        "       OR name LIKE :legacy_prefix) "
        "ORDER BY created_at DESC LIMIT 1"
    ), {
        "rid": _route_id_int,
        "legacy": f"COOLER-ROUTE-{_route_id_int}",
        "legacy_prefix": f"COOLER-ROUTE-{_route_id_int}-%",
    }).fetchone()
    cooler_session = None
    if lock_row is not None:
        cooler_session = {
            "id": lock_row[0],
            "sequence_locked_at": lock_row[1],
            "sequence_locked_by": lock_row[2],
            "name": lock_row[3],
            "status": lock_row[4],
            "is_locked": lock_row[1] is not None,
            "assigned_to": lock_row[5],
        }

    # Cooler enhancement — picking-phase status across ALL cooler sessions
    # for this route (siblings included). The cooler screen is the
    # box-assignment phase; the actual picking happens via the standard
    # batch picking interface (sorted by warehouse location).
    pending_count = sum(1 for q in queue if q["status"] == "pending")
    picked_count = sum(1 for q in queue if q["status"] == "picked")
    other_count = len(queue) - pending_count - picked_count
    picking_phase = {
        "complete": (len(queue) > 0 and pending_count == 0),
        "in_progress": pending_count > 0,
        "empty": len(queue) == 0,
        "pending_count": pending_count,
        "picked_count": picked_count,
        "other_count": other_count,
        "total_count": len(queue),
        "batch_name": cooler_session["name"] if cooler_session else None,
        "batch_status": cooler_session["status"] if cooler_session else None,
    }
    # scope cooler boxes by BOTH route_id and
    # delivery_date. Filtering by delivery_date alone leaks boxes from
    # other routes on the same day into this picker's view, breaking
    # the (route_id, delivery_date, box_no) box-numbering invariant.
    # (``_route_id_int`` was already computed above for the queue query.)
    _boxes_raw = db.session.execute(
        text(
            "SELECT cb.id, cb.route_id, cb.delivery_date, cb.box_no, cb.status, "
            "       cb.first_stop_sequence, cb.last_stop_sequence, "
            "       (SELECT COUNT(*) FROM cooler_box_items cbi "
            "        WHERE cbi.cooler_box_id = cb.id) AS item_count, "
            "       cb.fill_cm3, cb.fill_weight_kg, cb.closed_by, cb.closed_at, "
            "       cbt.name AS box_type_name, "
            "       CASE WHEN cbt.internal_volume_cm3 > 0 AND cbt.fill_efficiency > 0 "
            "            THEN ROUND(cb.fill_cm3 / (cbt.internal_volume_cm3 * cbt.fill_efficiency) * 100) "
            "            ELSE NULL END AS fill_pct "
            "FROM cooler_boxes cb "
            "LEFT JOIN cooler_box_types cbt ON cbt.id = cb.box_type_id "
            "WHERE cb.delivery_date = :delivery_date "
            "  AND cb.route_id = :route_id "
            "ORDER BY cb.box_no"
        ),
        {"delivery_date": str(delivery_date), "route_id": _route_id_int},
    ).fetchall()
    boxes = []
    for _b in _boxes_raw:
        _box = dict(_box_dict(_b), item_count=int(_b[7] or 0))
        _box["fill_cm3"] = float(_b[8]) if _b[8] is not None else None
        _box["fill_weight_kg"] = float(_b[9]) if _b[9] is not None else None
        _box["closed_by"] = _b[10]
        _box["closed_at"] = _b[11]
        _box["box_type_name"] = _b[12] or ""
        _box["fill_pct"] = int(_b[13]) if _b[13] is not None else None
        boxes.append(_box)

    # LIFO display order: boxes covering later stops are shown first
    # so the picker packs last-delivery items into the box first.
    boxes_lifo = sorted(
        boxes,
        key=lambda b: (b["last_stop_sequence"] or 0),
        reverse=True,
    )

    # Pre-fetch box items for the card display so the template can show
    # a collapsible item list without extra AJAX calls.
    box_items_by_box = {}
    if boxes:
        _box_ids = [_bx["id"] for _bx in boxes]
        _item_rows = db.session.execute(
            text(
                "SELECT cbi.cooler_box_id, cbi.invoice_no, cbi.item_code, cbi.item_name, "
                "       cbi.expected_qty, cbi.customer_name, cbi.delivery_sequence, "
                "       cbi.id, cbi.status, cbi.picked_qty "
                "FROM cooler_box_items cbi "
                "WHERE cbi.cooler_box_id = ANY(:bids) "
                "ORDER BY cbi.cooler_box_id, cbi.delivery_sequence DESC NULLS LAST, cbi.invoice_no"
            ),
            {"bids": _box_ids},
        ).fetchall()
        for _r in _item_rows:
            box_items_by_box.setdefault(int(_r[0]), []).append({
                "invoice_no": _r[1],
                "item_code": _r[2],
                "item_name": _r[3] or "",
                "qty": float(_r[4]) if _r[4] is not None else 0,
                "customer_name": _r[5] or "",
                "delivery_sequence": _r[6],
                "cbi_id": _r[7],
                "status": _r[8] or "planned",
                "picked_qty": float(_r[9]) if _r[9] is not None else None,
            })

    # Phase 6 — surface estimator on the picking screen so the
    # warehouse manager sees the suggested box mix before locking.
    estimate = None
    try:
        from services.cooler_estimator import estimate_cooler_boxes
        estimate = estimate_cooler_boxes(_route_id_int)
    except Exception as e:
        import logging as _l
        _l.getLogger(__name__).debug("cooler estimator failed: %s", e)

    open_boxes = [b for b in boxes if b["status"] == "open"]
    box_types = []
    try:
        rows = db.session.execute(text(
            "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
            "FROM cooler_box_types WHERE is_active = true "
            "ORDER BY sort_order, name"
        )).fetchall()
        box_types = [dict(zip(
            ["id", "name", "internal_volume_cm3", "fill_efficiency", "max_weight_kg"], r
        )) for r in rows]
    except Exception:
        box_types = []

    # Count items (pending or picked) not yet assigned to any box — drives
    # the "Plan Cooler Boxes" card visibility so it only appears when useful.
    picked_unboxed_count = db.session.execute(
        text(
            "SELECT COUNT(*) FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND bpq.status IN ('picked', 'pending') "
            "  AND i.route_id = :rid "
            "  AND s.delivery_date = :dd "
            "  AND NOT EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.queue_item_id = bpq.id"
            "  )"
        ),
        {"rid": _route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0

    # ── Self-healing sync ─────────────────────────────────────────────────
    # When items were picked via the cooler route page (queue_pick), only
    # batch_pick_queue is updated — InvoiceItem.is_picked is never set.
    # This leaves invoices stuck at 'picking' after the batch completes.
    # Fix it silently the next time someone visits the packing page.
    if cooler_session and cooler_session.get("status") == "Completed":
        try:
            _stuck = db.session.execute(
                text(
                    "SELECT COUNT(*) "
                    "FROM batch_pick_queue bpq "
                    "JOIN invoice_items ii "
                    "  ON ii.invoice_no = bpq.invoice_no "
                    " AND ii.item_code  = bpq.item_code "
                    "WHERE bpq.batch_session_id = :sid "
                    "  AND bpq.status = 'picked' "
                    "  AND ii.is_picked = FALSE"
                ),
                {"sid": cooler_session["id"]},
            ).scalar() or 0
            if _stuck:
                from batch_aware_order_status import (
                    sync_cooler_invoice_items,
                    update_order_status_batch_aware,
                )
                from models import BatchSessionInvoice as _BSI
                sync_cooler_invoice_items(cooler_session["id"])
                for _si in _BSI.query.filter_by(
                    batch_session_id=cooler_session["id"]
                ).all():
                    try:
                        update_order_status_batch_aware(_si.invoice_no)
                    except Exception:
                        pass
                db.session.commit()
                try:
                    from services.route_warehouse_readiness import (
                        recalculate_route_warehouse_status,
                    )
                    recalculate_route_warehouse_status(_route_id_int)
                except Exception:
                    pass
                current_app.logger.info(
                    "route_picking self-heal: fixed %d stuck item(s) for "
                    "cooler session %s route %s",
                    _stuck, cooler_session["id"], _route_id_int,
                )
        except Exception as _heal_err:
            try:
                db.session.rollback()
            except Exception:
                pass
            current_app.logger.warning(
                "route_picking self-heal failed for session %s: %s",
                cooler_session.get("id"), _heal_err,
            )

    _TERMINAL_COOLER = ("Completed", "Cancelled", "Archived")
    # batch_in_progress is True only when sequencing has been locked AND the
    # batch is not yet finished. Before locking, pickers can use direct Pick
    # buttons (items are not yet in the queue). After locking, the batch
    # interface is the only safe picking path so Pick buttons must be hidden.
    batch_in_progress = (
        cooler_session is not None
        and cooler_session.get("sequence_locked_at") is not None
        and (cooler_session.get("status") or "") not in _TERMINAL_COOLER
    )

    _sinfo = db.session.execute(
        text("SELECT driver_name, route_name FROM shipments WHERE id = :rid"),
        {"rid": _route_id_int},
    ).fetchone()
    route_driver = _sinfo[0] if _sinfo else None
    route_name_val = _sinfo[1] if _sinfo else None

    from models import User
    picker_users = User.query.filter(
        User.is_active == True,
        User.role.in_(["picker", "warehouse_manager", "admin"]),
    ).order_by(User.username).all()

    return render_template(
        "cooler/route_picking.html",
        route_id=route_id, delivery_date=delivery_date,
        queue=queue, sequenced=sequenced, unsequenced=unsequenced,
        cooler_session=cooler_session, estimate=estimate,
        boxes=boxes, boxes_lifo=boxes_lifo, open_boxes=open_boxes,
        box_types=box_types,
        box_items_by_box=box_items_by_box,
        picking_phase=picking_phase,
        batch_in_progress=batch_in_progress,
        assigned_to_box=assigned_to_box,
        planned_box=planned_box,
        picked_unboxed_count=picked_unboxed_count,
        route_driver=route_driver,
        route_name=route_name_val,
        picker_users=picker_users,
    )


@cooler_bp.route("/route/<route_id>/assign-picker", methods=["POST"])
@login_required
@require_permission("cooler.lock_sequencing")
@_require_cooler_manage
@_require_picking_flag
def assign_cooler_picker(route_id):
    """Assign (or re-assign) a picker to the cooler-route batch session."""
    try:
        route_id_int = int(route_id)
    except (TypeError, ValueError):
        flash("Invalid route ID.", "danger")
        return redirect(url_for("cooler.route_list"))

    delivery_date = request.form.get("delivery_date", "")
    batch_id = request.form.get("batch_id", type=int)
    picker_username = request.form.get("picker_username", "").strip()

    if not batch_id:
        flash("No batch session specified.", "warning")
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id, delivery_date=delivery_date))

    session_obj = BatchPickingSession.query.get(batch_id)
    if session_obj is None:
        flash("Batch session not found.", "danger")
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id, delivery_date=delivery_date))

    if picker_username:
        from models import User
        picker = User.query.filter_by(username=picker_username, is_active=True).first()
        if not picker:
            flash("Selected picker not found or inactive.", "warning")
            return redirect(url_for("cooler.route_picking",
                                    route_id=route_id, delivery_date=delivery_date))
        session_obj.assigned_to = picker_username
        db.session.commit()
        display = getattr(picker, "display_name", None) or picker_username
        flash(f"Cooler batch assigned to {display}.", "success")
    else:
        session_obj.assigned_to = None
        db.session.commit()
        flash("Cooler batch unassigned.", "info")

    return redirect(url_for("cooler.route_picking",
                            route_id=route_id, delivery_date=delivery_date))


@cooler_bp.route("/route/<route_id>/lock-sequencing", methods=["POST"])
@login_required
@require_permission("cooler.lock_sequencing")
@_require_cooler_manage
@_require_picking_flag
def lock_sequencing(route_id):
    """Phase 6 — Lock cooler sequencing for a route.

    For every pending cooler queue row on the route, snapshot the
    ``RouteStop.seq_no`` (resolved via ``route_stop_invoice``) into
    ``batch_pick_queue.delivery_sequence`` so the picker UI can render
    them in delivery order. Also stamps
    ``batch_picking_sessions.sequence_locked_at/by`` for audit.

    Idempotent: rows that already have a non-null
    ``delivery_sequence`` are skipped (the lock can be re-run safely
    after late additions arrive).
    """
    try:
        route_id_int = int(route_id)
    except (TypeError, ValueError):
        return jsonify({"error": "route_id must be int"}), 400

    # Look up the latest cooler session for this route (siblings included).
    # We sequence the LATEST session because that's where late-addition
    # rows with NULL delivery_sequence live.
    session_row = db.session.execute(text(
        "SELECT id, sequence_locked_at FROM batch_picking_sessions "
        "WHERE session_type = 'cooler_route' "
        "  AND (route_id = :rid OR name = :legacy "
        "       OR name LIKE :legacy_prefix) "
        "ORDER BY created_at DESC LIMIT 1"
    ), {
        "rid": route_id_int,
        "legacy": f"COOLER-ROUTE-{route_id_int}",
        "legacy_prefix": f"COOLER-ROUTE-{route_id_int}-%",
    }).fetchone()
    if session_row is None:
        return jsonify({
            "error": "No cooler session for this route. "
                     "Attach SENSITIVE invoices first.",
        }), 404
    session_id = session_row[0]
    # Sequential Stop is not production-ready — always force location_order
    # regardless of what the form posts.  If it is ever enabled it must be
    # gated by a feature flag; accepting it from raw POST data is a footgun.
    pack_mode = "location_order"
    box_type_id = request.form.get("cooler_box_type_id") or None
    if box_type_id:
        try:
            box_type_id = int(box_type_id)
        except ValueError:
            box_type_id = None

    rows = db.session.execute(text(
        "SELECT bpq.id, bpq.invoice_no, rs.seq_no "
        "FROM batch_pick_queue bpq "
        "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
        "LEFT JOIN route_stop_invoice rsi "
        "       ON rsi.invoice_no = bpq.invoice_no "
        "      AND rsi.is_active = :truthy "
        "LEFT JOIN route_stop rs "
        "       ON rs.route_stop_id = rsi.route_stop_id "
        "WHERE bpq.pick_zone_type = 'cooler' "
        "  AND bpq.batch_session_id = :sid "
        "  AND bpq.delivery_sequence IS NULL"
    ), {"sid": session_id, "truthy": True}).fetchall()

    stamped = 0
    skipped_no_stop = 0
    skipped_details = []
    for queue_id, inv_no, seq_no in rows:
        if seq_no is None:
            skipped_no_stop += 1
            skipped_details.append({
                "invoice_no": inv_no,
                "reason": "no active route_stop_invoice row",
            })
            continue
        db.session.execute(text(
            "UPDATE batch_pick_queue SET delivery_sequence = :seq, "
            "       updated_at = :now "
            "WHERE id = :id"
        ), {"id": queue_id, "seq": float(seq_no), "now": get_utc_now()})
        stamped += 1

    now = get_utc_now()
    db.session.execute(text(
        "UPDATE batch_picking_sessions "
        "SET sequence_locked_at = :now, sequence_locked_by = :who, "
        "    cooler_pack_mode = :mode, cooler_box_type_id = :btid, "
        "    last_activity_at = :now "
        "WHERE id = :sid"
    ), {"sid": session_id, "now": now, "who": _username(), "mode": pack_mode, "btid": box_type_id})

    _audit(
        "cooler.lock_sequencing",
        f"Locked cooler sequencing for route {route_id_int}: "
        f"stamped={stamped} skipped_no_stop={skipped_no_stop} "
        f"session_id={session_id}",
    )
    db.session.commit()

    # Default response is JSON so API/test clients keep working. The HTML
    # form on the cooler page sets a hidden ``_html_form=1`` marker so we
    # can return a flash + redirect instead. Sniffing ``Accept`` headers
    # is unreliable because Werkzeug's test client and most XHR callers
    # send ``*/*``.
    if not request.form.get("_html_form"):
        return jsonify({
            "ok": True,
            "route_id": route_id_int,
            "session_id": session_id,
            "stamped": stamped,
            "skipped_no_stop": skipped_no_stop,
            "skipped_details": skipped_details,
            "locked_at": now.isoformat(),
            "locked_by": _username(),
        })

    # HTML form POST — flash and redirect back to the picking screen
    if stamped:
        flash(f"Sequencing locked — {stamped} item(s) stamped with delivery order.", "success")
    else:
        flash("Sequencing locked (all items already had a sequence).", "info")
    if skipped_no_stop:
        flash(
            f"{skipped_no_stop} item(s) could not be sequenced (no active route stop): "
            + ", ".join(d['invoice_no'] for d in skipped_details),
            "warning",
        )

    # Prefer delivery_date from form; fall back to looking it up from the shipment
    delivery_date = request.form.get("delivery_date", "").strip()
    if not delivery_date:
        date_row = db.session.execute(
            text("SELECT delivery_date FROM shipments WHERE id = :rid"),
            {"rid": route_id_int},
        ).fetchone()
        delivery_date = str(date_row[0]) if date_row and date_row[0] else ""

    if not delivery_date:
        # Last resort: go back to the list
        return redirect(url_for("cooler.route_list"))

    return redirect(url_for("cooler.route_picking",
                            route_id=route_id_int,
                            delivery_date=delivery_date))


@cooler_bp.route("/box/create", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
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

    if request.form.get("_html_form"):
        flash(f"Box #{box_no} created.", "success")
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id,
                                delivery_date=str(delivery_date)))

    return jsonify({"cooler_box_id": new_id, "status": "open",
                    "created": True}), 201


@cooler_bp.route("/route/<route_id>/<delivery_date>/box-plan", methods=["GET"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_plan_preview(route_id, delivery_date):
    """Return the recommended box plan plus all active box types for the UI."""
    box_type_id = request.args.get("box_type_id") or None
    target_fill = float(request.args.get("target_fill", "0.80"))

    # Parse availability: ?avail=typeId:count,typeId:count
    avail_raw = request.args.get("avail") or ""
    available_type_counts = None
    if avail_raw:
        try:
            available_type_counts = {}
            for part in avail_raw.split(","):
                tid, cnt = part.strip().split(":")
                available_type_counts[int(tid)] = int(cnt)
        except Exception:
            available_type_counts = None

    result = generate_box_plan(
        route_id, delivery_date,
        box_type_id=box_type_id,
        available_type_counts=available_type_counts,
        target_fill_pct=target_fill,
    )
    if isinstance(result, dict) and not result.get("ok", True):
        return jsonify(result)
    plan = result if isinstance(result, list) else result.get("plan", [])
    if not plan:
        return jsonify({
            "ok": True,
            "plan": [],
            "message": "No cooler items found to plan.",
        })

    from services.cooler_box_planner import _load_box_types
    box_types = _load_box_types()

    return jsonify({
        "ok": True,
        "plan": plan,
        "box_types": box_types,
        "target_fill_pct": target_fill,
    })


@cooler_bp.route("/route/<route_id>/<delivery_date>/confirm-box-plan", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def confirm_box_plan(route_id, delivery_date):
    from sqlalchemy.exc import IntegrityError

    try:
        route_id_int = int(route_id)
    except (TypeError, ValueError):
        abort(400)

    def _redirect_back():
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id_int,
                                delivery_date=delivery_date))

    import json as _json
    plan_data_raw = request.form.get("plan_data") or ""
    plan = None

    if plan_data_raw:
        try:
            plan = _json.loads(plan_data_raw)
            if not isinstance(plan, list):
                plan = None
        except Exception:
            plan = None

    if plan is None:
        box_type_id = request.form.get("box_type_id") or None
        result = generate_box_plan(route_id_int, delivery_date, box_type_id)
        if isinstance(result, dict):
            if not result.get("ok", True):
                flash(result.get("message", "Cannot generate box plan."), "warning")
                return _redirect_back()
            plan = result.get("plan", [])
        else:
            plan = result

    if not plan:
        flash("No cooler items found to plan.", "warning")
        return _redirect_back()

    max_box_no = db.session.execute(
        text("SELECT COALESCE(MAX(box_no), 0) FROM cooler_boxes "
             "WHERE route_id = :rid AND delivery_date = :dd"),
        {"rid": route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0

    now = get_utc_now()
    created = 0
    skipped = 0

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, first_stop_sequence, "
                    " last_stop_sequence, created_by, created_at, box_type_id, "
                    " fill_cm3, fill_weight_kg, cooler_session_id) "
                    "VALUES (:rid, :dd, :box_no, 'open', :fs, :ls, :who, :now, "
                    "        :btid, :fill, :weight, :sid) "
                    "RETURNING id"
                ),
                {
                    "rid": route_id_int,
                    "dd": str(delivery_date),
                    "box_no": int(max_box_no) + idx,
                    "fs": box["stop_min"],
                    "ls": box["stop_max"],
                    "who": _username(),
                    "now": now,
                    "btid": box["box_type_id"],
                    "fill": box["estimated_fill_cm3"],
                    "weight": box["estimated_weight_kg"],
                    "sid": None,
                },
            ).fetchone()
            box_id = result_row[0]

            items_inserted = 0
            for item in box["item_summaries"]:
                qid = item["queue_item_id"]

                # Pre-flight: verify the queue row still exists, is still
                # picked, and is not already assigned to a box.
                qcheck = db.session.execute(
                    text(
                        "SELECT bpq.status, "
                        "       (SELECT COUNT(*) FROM cooler_box_items cbi "
                        "        WHERE cbi.queue_item_id = bpq.id) AS already_boxed "
                        "FROM batch_pick_queue bpq WHERE bpq.id = :qid"
                    ),
                    {"qid": qid},
                ).fetchone()

                if qcheck is None:
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} no longer exists — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue
                if qcheck[0] not in ("picked", "pending"):
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} status={qcheck[0]} (not plannable) — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue
                if qcheck[1] > 0:
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} already boxed ({qcheck[1]} row(s)) — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue

                # Items already picked keep status='picked'.
                # Items not yet picked are pre-assigned as status='planned'.
                # NOTE: cooler_box_items.status allows ('planned','picked','exception')
                # — never pass 'pending' (the batch_pick_queue status) directly.
                queue_status = qcheck[0]  # 'picked' or 'pending'
                cbi_status   = "picked" if queue_status == "picked" else "planned"
                _now_or_none = now if queue_status == "picked" else None
                _who_or_none = _username() if queue_status == "picked" else None
                _qty = item["qty"] if queue_status == "picked" else None

                db.session.execute(
                    text(
                        "INSERT INTO cooler_box_items "
                        "(cooler_box_id, invoice_no, customer_code, customer_name, "
                        " route_stop_id, delivery_sequence, item_code, item_name, "
                        " expected_qty, picked_qty, picked_by, picked_at, "
                        " queue_item_id, status, created_at, updated_at) "
                        "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
                        "        :exp, :pq, :who, :now, :qid, :status, :created, :created)"
                    ),
                    {
                        "bid": box_id,
                        "inv": item["invoice_no"],
                        "cc": item["customer_code"],
                        "cn": item["customer_name"],
                        "rsid": item["route_stop_id"],
                        "seq": item["delivery_sequence"],
                        "ic": item["item_code"],
                        "iname": item["item_name"],
                        "exp": item["qty"],
                        "pq": _qty,
                        "who": _who_or_none,
                        "now": _now_or_none,
                        "qid": qid,
                        "status": cbi_status,
                        "created": now,
                    },
                )
                items_inserted += 1

            if items_inserted == 0:
                # All items were skipped — delete the empty box rather than
                # leaving a shell record that will confuse the manifest.
                db.session.execute(
                    text("DELETE FROM cooler_boxes WHERE id = :bid"),
                    {"bid": box_id},
                )
                _audit(
                    "cooler.confirm_plan_empty_box",
                    f"Cooler box #{box_id} (plan slot {idx}) removed — "
                    f"all items skipped during plan confirmation",
                )
            else:
                # Some items were actually inserted — recalculate box header
                # fields based only on what was really placed inside, not the
                # original planner estimates (which may include skipped items).
                recalc = db.session.execute(
                    text(
                        "SELECT MIN(delivery_sequence), MAX(delivery_sequence) "
                        "FROM cooler_box_items "
                        "WHERE cooler_box_id = :bid"
                    ),
                    {"bid": box_id},
                ).fetchone()
                actual_first = recalc[0] if recalc else None
                actual_last = recalc[1] if recalc else None
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, last_stop_sequence = :ls "
                        "WHERE id = :bid"
                    ),
                    {"fs": actual_first, "ls": actual_last, "bid": box_id},
                )
                created += 1

        db.session.commit()

        # Recalculate warehouse readiness after box plan is confirmed
        try:
            from services.route_warehouse_readiness import recalculate_route_warehouse_status
            recalculate_route_warehouse_status(route_id_int)
        except Exception as _wre:
            current_app.logger.warning(
                "warehouse readiness recalc failed after confirm_box_plan route %s: %s",
                route_id_int, _wre,
            )

    except IntegrityError as exc:
        db.session.rollback()
        import logging as _log
        _log.getLogger(__name__).warning("confirm_box_plan IntegrityError: %s", exc)
        flash(
            "Box plan could not be saved — one or more items were already assigned "
            "to a box (possibly by another user). Please refresh and try again.",
            "warning",
        )
        return _redirect_back()

    msg = f"Box plan confirmed — {created} box(es) created."
    if skipped:
        msg += f" {skipped} item(s) skipped (already boxed or status changed)."
    flash(msg, "success")
    return _redirect_back()


@cooler_bp.route("/route/<route_id>/<delivery_date>/pre-plan", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def pre_plan_boxes(route_id, delivery_date):
    """Pre-plan cooler boxes before picking starts.

    Creates cooler_boxes + cooler_box_items for ALL cooler items on the route
    (pending and picked). Items get status='pending' or 'picked' in
    cooler_box_items so the picker sees box assignments on the picking screen.
    """
    try:
        route_id_int = int(route_id)
    except (TypeError, ValueError):
        flash("Invalid route ID.", "danger")
        return redirect(url_for("cooler.route_list"))

    existing_boxes = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = :dd"
        ),
        {"rid": route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0

    if existing_boxes > 0:
        flash(
            f"Boxes are already planned for this route ({existing_boxes} box(es) exist). "
            "Open the packing screen to review or re-plan.",
            "warning",
        )
        return redirect(url_for("cooler.route_list"))

    box_type_id = request.form.get("box_type_id") or None

    from services.cooler_box_planner import generate_box_plan
    result = generate_box_plan(
        route_id_int, delivery_date,
        box_type_id=box_type_id,
        include_pending=True,
    )

    if isinstance(result, dict) and not result.get("ok", True):
        flash(result.get("message", "Cannot generate box plan."), "warning")
        return redirect(url_for("cooler.route_list"))

    plan = result if isinstance(result, list) else result.get("plan", [])

    if not plan:
        flash(
            "No cooler items found to plan. "
            "Make sure sequencing is locked first.",
            "warning",
        )
        return redirect(url_for("cooler.route_list"))

    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, first_stop_sequence, "
                    " last_stop_sequence, created_by, created_at, box_type_id, "
                    " fill_cm3, fill_weight_kg) "
                    "VALUES (:rid, :dd, :box_no, 'open', :fs, :ls, :who, :now, "
                    "        :btid, :fill, :weight) "
                    "RETURNING id"
                ),
                {
                    "rid": route_id_int, "dd": str(delivery_date),
                    "box_no": idx,
                    "fs": box["stop_min"], "ls": box["stop_max"],
                    "who": _username(), "now": now,
                    "btid": box["box_type_id"],
                    "fill": box["estimated_fill_cm3"],
                    "weight": box["estimated_weight_kg"],
                },
            ).fetchone()
            box_id = result_row[0]
            created_boxes += 1

            for item in box["item_summaries"]:
                qid = item["queue_item_id"]
                qcheck = db.session.execute(
                    text(
                        "SELECT bpq.status, "
                        "       (SELECT COUNT(*) FROM cooler_box_items cbi "
                        "        WHERE cbi.queue_item_id = bpq.id) AS already_boxed "
                        "FROM batch_pick_queue bpq WHERE bpq.id = :qid"
                    ),
                    {"qid": qid},
                ).fetchone()
                if qcheck is None or qcheck[1] > 0:
                    skipped_items += 1
                    continue
                if qcheck[0] not in ("picked", "pending"):
                    skipped_items += 1
                    continue

                item_status = qcheck[0]
                db.session.execute(
                    text(
                        "INSERT INTO cooler_box_items "
                        "(cooler_box_id, invoice_no, customer_code, customer_name, "
                        " route_stop_id, delivery_sequence, item_code, item_name, "
                        " expected_qty, picked_qty, picked_by, picked_at, "
                        " queue_item_id, status, created_at, updated_at) "
                        "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
                        "        :exp, :pq, :who, :now, :qid, :status, :ts, :ts)"
                    ),
                    {
                        "bid": box_id,
                        "inv": item["invoice_no"],
                        "cc": item["customer_code"],
                        "cn": item["customer_name"],
                        "rsid": item["route_stop_id"],
                        "seq": item["delivery_sequence"],
                        "ic": item["item_code"],
                        "iname": item["item_name"],
                        "exp": item["qty"],
                        "pq": item["qty"] if item_status == "picked" else None,
                        "who": _username() if item_status == "picked" else None,
                        "now": now if item_status == "picked" else None,
                        "qid": qid,
                        "status": item_status,
                        "ts": now,
                    },
                )

        db.session.commit()
        _audit(
            "cooler.pre_plan",
            f"Pre-planned {created_boxes} box(es) for route {route_id} "
            f"date={delivery_date} — {skipped_items} item(s) skipped",
        )
        flash(
            f"\u2713 {created_boxes} box(es) pre-planned. "
            "Label and place them on the picker\u2019s trolley, then start picking.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        logger.exception("pre_plan_boxes failed for route %s", route_id)
        flash(f"Pre-planning failed: {e}", "danger")

    return redirect(url_for("cooler.route_list"))


@cooler_bp.route("/route/<route_id>/<delivery_date>/cancel-preplan", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def cancel_pre_plan(route_id, delivery_date):
    """Remove all open boxes so a fresh pre-plan can be generated."""
    try:
        route_id_int = int(route_id)
    except (TypeError, ValueError):
        flash("Invalid route ID.", "danger")
        return redirect(url_for("cooler.route_list"))

    db.session.execute(
        text(
            "DELETE FROM cooler_box_items WHERE cooler_box_id IN "
            "(SELECT id FROM cooler_boxes WHERE route_id = :rid "
            " AND delivery_date = :dd AND status = 'open')"
        ),
        {"rid": route_id_int, "dd": str(delivery_date)},
    )
    db.session.execute(
        text(
            "DELETE FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = :dd AND status = 'open'"
        ),
        {"rid": route_id_int, "dd": str(delivery_date)},
    )
    db.session.commit()
    _audit(
        "cooler.cancel_preplan",
        f"Cancelled pre-plan for route {route_id} date={delivery_date}",
    )
    flash("Pre-plan cancelled. You can now generate a new plan.", "info")
    return redirect(url_for("cooler.route_list"))


@cooler_bp.route("/box/<int:box_id>/assign-item", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
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
            "       i.customer_code, i.customer_name, "
            "       i.route_id, s.delivery_date "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN shipments s ON s.id = i.route_id "
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
    if qrow[4] != "picked":
        return jsonify({
            "error": "Item has not been physically picked yet. "
                     "Pick the item first, then assign it to a box."
        }), 400

    # enforce that the queue item's invoice belongs to
    # the SAME route_id and delivery_date as the target box. Without
    # this gate a permitted cooler picker could bind any cooler queue
    # row to any open box by id, mis-attributing items across routes
    # and corrupting driver manifests / cold-chain audit trail.
    invoice_route_id = qrow[8]
    invoice_delivery_date = qrow[9]
    if invoice_route_id is None:
        return jsonify({
            "error": f"Queue item {queue_item_id} has no assigned route "
                     f"(invoice.route_id is NULL); cannot bind to a cooler box."
        }), 400
    if int(invoice_route_id) != int(box["route_id"]):
        return jsonify({
            "error": f"Cross-route assignment refused: queue item "
                     f"{queue_item_id} belongs to route {invoice_route_id} "
                     f"but cooler box #{box_id} is for route "
                     f"{box['route_id']}."
        }), 400
    if str(invoice_delivery_date) != str(box["delivery_date"]):
        return jsonify({
            "error": f"Cross-date assignment refused: queue item "
                     f"{queue_item_id} ships on {invoice_delivery_date} "
                     f"but cooler box #{box_id} is for "
                     f"{box['delivery_date']}."
        }), 400

    # Duplicate-assignment guard: reject if this queue item is already boxed
    existing_box_row = db.session.execute(
        text(
            "SELECT cooler_box_id FROM cooler_box_items "
            "WHERE queue_item_id = :qid LIMIT 1"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if existing_box_row is not None:
        return jsonify({
            "error": f"Queue item {queue_item_id} is already assigned "
                     f"to cooler box #{existing_box_row[0]}."
        }), 409

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

    from sqlalchemy.exc import IntegrityError as _IntegrityError
    now = get_utc_now()
    # Physical picking (pending → picked) is a separate audit event done via
    # queue_pick. Box assignment never changes the queue status — only already-
    # picked items reach this point (enforced by the status guard above).
    try:
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
    except _IntegrityError:
        db.session.rollback()
        return jsonify({
            "error": f"Queue item {queue_item_id} was already assigned "
                     f"to a cooler box (concurrent request). "
                     f"Please refresh and try again."
        }), 409
    # Physical picking (pending → picked) is a separate audit event done via
    # queue_pick. Box assignment never changes the queue status.
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
@_require_cooler_manage
@_require_picking_flag
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
    # The item was physically picked before being boxed.  Removing it from
    # a box does NOT undo the physical pick — the item is now picked/unboxed
    # and will reappear in the Generate Box Plan list.  Do not touch
    # batch_pick_queue status, picked_by, picked_at, or qty_picked.
    _audit(
        "cooler.item_removed",
        f"Cooler box #{box_id} -> unboxed queue #{queue_item_id} "
        f"invoice={cb_row[1]} item={cb_row[2]} (remains picked) by {_username()}",
        invoice_no=cb_row[1], item_code=cb_row[2],
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "picked"}), 200


@cooler_bp.route("/box-item/<int:cbi_id>/move-to-box", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def move_box_item(cbi_id):
    """Move a cooler_box_items row from its current box to a different open box.

    Works for both 'planned' and 'picked' items.
    Both the source and destination boxes must be open.
    """
    data = request.get_json(silent=True) or request.form
    try:
        dest_box_id = int(data.get("destination_box_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "destination_box_id is required and must be int"}), 400

    cbi = db.session.execute(
        text(
            "SELECT cbi.id, cbi.cooler_box_id, cbi.invoice_no, cbi.item_code, "
            "       cbi.status, cb.route_id, cb.delivery_date, cb.status AS box_status "
            "FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cbi.id = :id"
        ),
        {"id": cbi_id},
    ).fetchone()
    if cbi is None:
        return jsonify({"error": "Item not found"}), 404
    if cbi[7] != "open":
        return jsonify({"error": f"Source box is {cbi[7]}; can only move items from open boxes."}), 400

    dest = db.session.execute(
        text(
            "SELECT id, route_id, delivery_date, status "
            "FROM cooler_boxes WHERE id = :id"
        ),
        {"id": dest_box_id},
    ).fetchone()
    if dest is None:
        return jsonify({"error": "Destination box not found"}), 404
    if dest[3] != "open":
        return jsonify({"error": f"Destination box is {dest[3]}; can only move to open boxes."}), 400

    if int(dest[1]) != int(cbi[5]) or str(dest[2]) != str(cbi[6]):
        return jsonify({"error": "Cannot move items between routes or dates."}), 400

    if dest[0] == cbi[1]:
        return jsonify({"error": "Item is already in that box."}), 400

    source_box_id = cbi[1]
    now = get_utc_now()

    db.session.execute(
        text(
            "UPDATE cooler_box_items "
            "SET cooler_box_id = :dest, updated_at = :now "
            "WHERE id = :cbi_id"
        ),
        {"dest": dest_box_id, "now": now, "cbi_id": cbi_id},
    )

    for box_id_to_update in (source_box_id, dest_box_id):
        recalc = db.session.execute(
            text(
                "SELECT MIN(delivery_sequence), MAX(delivery_sequence) "
                "FROM cooler_box_items WHERE cooler_box_id = :bid"
            ),
            {"bid": box_id_to_update},
        ).fetchone()
        db.session.execute(
            text(
                "UPDATE cooler_boxes "
                "SET first_stop_sequence = :fs, last_stop_sequence = :ls "
                "WHERE id = :bid"
            ),
            {"fs": recalc[0], "ls": recalc[1], "bid": box_id_to_update},
        )

    _audit(
        "cooler.item_moved",
        f"cooler_box_items #{cbi_id} moved from box #{source_box_id} "
        f"to box #{dest_box_id} ({cbi[4]}) by {_username()}",
        invoice_no=cbi[2], item_code=cbi[3],
    )
    db.session.commit()
    return jsonify({
        "cbi_id": cbi_id,
        "from_box_id": source_box_id,
        "to_box_id": dest_box_id,
    }), 200


@cooler_bp.route("/box/<int:box_id>/close", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_close(box_id):
    """Close an open cooler box and stamp its stop range."""
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "open":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; only open boxes can be closed."
        }), 400

    # Guard: all assigned items must be picked (skip when force=1)
    force = request.form.get("force") == "1" or request.args.get("force") == "1"
    unpicked = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_box_items cbi "
            "WHERE cbi.cooler_box_id = :bid "
            "  AND (cbi.status = 'planned' OR cbi.picked_qty = 0)"
        ),
        {"bid": box_id},
    ).scalar() or 0
    if unpicked > 0 and not force:
        msg = f"Box #{box_id} still has {unpicked} unpicked item(s) — pick everything before closing."
        if request.form.get("_html_form"):
            flash(msg, "warning")
            return redirect(url_for("cooler.route_picking",
                                    route_id=box["route_id"],
                                    delivery_date=str(box["delivery_date"])))
        return jsonify({"error": msg}), 400

    if unpicked > 0 and force:
        # Mark planned (unphysically-picked) items as exception so the route
        # completion check no longer treats them as blocking planned rows.
        now_f = get_utc_now()
        db.session.execute(
            text(
                "UPDATE cooler_box_items "
                "SET status = 'exception', updated_at = :now "
                "WHERE cooler_box_id = :bid "
                "  AND (status = 'planned' OR picked_qty = 0)"
            ),
            {"bid": box_id, "now": now_f},
        )
        _audit(
            "cooler.box_force_closed",
            f"Cooler box #{box_id} force-closed by {_username()} — "
            f"{unpicked} unpicked item(s) marked exception",
        )

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

    # Phase-5 cooler integration: closing a cooler box is the trigger
    # event that may complete the cooler side of an order. For every
    # invoice that had items in this box, if the order packed regular
    # items earlier and is sitting in 'awaiting_batch_items', and
    # is_order_ready now returns True, promote it to ready_for_dispatch.
    invoice_rows = db.session.execute(
        text(
            "SELECT DISTINCT invoice_no FROM cooler_box_items "
            "WHERE cooler_box_id = :bid"
        ),
        {"bid": box_id},
    ).fetchall()
    promoted = []
    try:
        from services.order_readiness import is_order_ready
        from models import Invoice
        for (inv_no,) in invoice_rows:
            inv = Invoice.query.filter_by(invoice_no=inv_no).first()
            if inv is None:
                continue
            if inv.status == 'awaiting_batch_items' and is_order_ready(inv_no):
                inv.status = 'ready_for_dispatch'
                promoted.append(inv_no)
                _audit(
                    "cooler.order_ready_for_dispatch",
                    f"Invoice {inv_no} promoted "
                    f"awaiting_batch_items -> ready_for_dispatch "
                    f"after cooler box #{box_id} closed",
                    invoice_no=inv_no,
                )
    except Exception as exc:  # never block box close on promotion failure
        current_app.logger.warning(
            "cooler.box_close: promotion check failed for box %s: %s",
            box_id, exc,
        )

    if _is_cooler_route_pack_complete(box["route_id"], box["delivery_date"]):
        db.session.execute(
            text(
                "UPDATE batch_picking_sessions "
                "SET status = 'Completed', last_activity_at = :now "
                "WHERE session_type = 'cooler_route' "
                "  AND route_id = :rid "
                "  AND status NOT IN ('Completed', 'Cancelled', 'Archived')"
            ),
            {"rid": box["route_id"], "now": now},
        )

    db.session.commit()

    # Recalculate warehouse readiness after a box is closed
    try:
        from services.route_warehouse_readiness import recalculate_route_warehouse_status
        recalculate_route_warehouse_status(box["route_id"])
    except Exception as _wre:
        current_app.logger.warning(
            "warehouse readiness recalc failed after box_close %s: %s", box_id, _wre
        )

    if request.form.get("_html_form"):
        flash(f"Box #{box_id} closed.", "success")
        box_data = _fetch_box(box_id)
        route_id = box_data["route_id"] if box_data else None
        delivery_date_val = str(box_data["delivery_date"]) if box_data else ""
        if route_id and delivery_date_val:
            return redirect(url_for("cooler.route_picking",
                                    route_id=route_id,
                                    delivery_date=delivery_date_val))
        return redirect(url_for("cooler.route_list"))

    return jsonify({
        "cooler_box_id": box_id, "status": "closed",
        "first_stop_sequence": float(first_seq) if first_seq is not None else None,
        "last_stop_sequence": float(last_seq) if last_seq is not None else None,
        "promoted_invoices": promoted,
    }), 200


@cooler_bp.route("/box/<int:box_id>/reopen", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
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
    flash(f"Box #{box_id} re-opened.", "success")
    return redirect(url_for("cooler.route_picking",
                            route_id=box["route_id"],
                            delivery_date=box["delivery_date"]))


@cooler_bp.route("/box/<int:box_id>/cancel", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
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
    # Delete box-item assignments only.  The underlying queue rows stay as
    # 'picked' — they were physically picked before being boxed and cancelling
    # a box does not undo the physical pick.  They will reappear as
    # picked/unboxed and show up in the Generate Box Plan list.
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
        f"unboxed {len(rows)} queue row(s) (remain picked)",
    )
    db.session.commit()

    # Recalculate warehouse readiness after a box is cancelled
    try:
        from services.route_warehouse_readiness import recalculate_route_warehouse_status
        recalculate_route_warehouse_status(box["route_id"])
    except Exception as _wre:
        current_app.logger.warning(
            "warehouse readiness recalc failed after box_cancel %s: %s", box_id, _wre
        )

    if request.form.get("_html_form"):
        flash(f"Box #{box_id} deleted.", "success")
        if box["route_id"] and box["delivery_date"]:
            return redirect(url_for("cooler.route_picking",
                                    route_id=box["route_id"],
                                    delivery_date=str(box["delivery_date"])))
        return redirect(url_for("cooler.route_list"))

    return jsonify({"cooler_box_id": box_id, "status": "cancelled"}), 200


# ---------------------------------------------------------------------------
# Pick action — marks a queued item as physically picked from the cooler
# ---------------------------------------------------------------------------
@cooler_bp.route("/queue/<int:queue_item_id>/pick", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def queue_pick(queue_item_id):
    """Mark a cooler queue row as picked (pending → picked).

    Records picked_by, picked_at, qty_picked = qty_required.
    After this the item appears as 'picked' and can be assigned to a box.
    Redirects back to the route picking screen.
    """
    row = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_required, "
            "       bpq.status, bpq.pick_zone_type, "
            "       i.route_id, s.delivery_date "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.id = :qid"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if row is None:
        abort(404)
    if row[5] != "cooler":
        flash("Not a cooler queue item.", "danger")
    elif row[4] != "pending":
        flash(f"Item is already {row[4]} — nothing to do.", "info")
    else:
        now = get_utc_now()
        db.session.execute(
            text(
                "UPDATE batch_pick_queue "
                "SET status = 'picked', picked_by = :who, picked_at = :now, "
                "    qty_picked = qty_required, updated_at = :now "
                "WHERE id = :qid AND status = 'pending'"
            ),
            {"who": _username(), "now": now, "qid": queue_item_id},
        )
        _audit(
            "cooler.item_picked",
            f"Cooler queue #{queue_item_id} invoice={row[1]} item={row[2]} "
            f"picked by {_username()}",
            invoice_no=row[1], item_code=row[2],
        )

        # ── Pick-to-box: promote 'planned' box assignment to 'picked' ─────
        try:
            db.session.execute(
                text(
                    "UPDATE cooler_box_items "
                    "SET status = 'picked', picked_qty = :qty, "
                    "    picked_by = :who, picked_at = :now, updated_at = :now "
                    "WHERE queue_item_id = :qid AND status = 'planned'"
                ),
                {
                    "qid": queue_item_id,
                    "qty": float(row[3]) if row[3] else 0.0,
                    "who": _username(),
                    "now": now,
                },
            )
        except Exception as _ptb_err:
            current_app.logger.warning(
                "cooler.queue_pick pick-to-box promote failed for queue %s: %s",
                queue_item_id, _ptb_err,
            )

        try:
            session_row = db.session.execute(
                text(
                    "SELECT s.id, s.cooler_pack_mode "
                    "FROM batch_picking_sessions s "
                    "JOIN batch_pick_queue bpq ON bpq.batch_session_id = s.id "
                    "WHERE bpq.id = :qid AND s.session_type = 'cooler_route' "
                    "LIMIT 1"
                ),
                {"qid": queue_item_id},
            ).fetchone()
            if session_row and (session_row[1] or "") == "sequential_stop":
                from services.cooler_route_extraction import cooler_auto_assign_item
                cooler_auto_assign_item(session_row[0], queue_item_id)
        except Exception as exc:
            current_app.logger.warning(
                "cooler.queue_pick auto-assign skipped for queue %s: %s",
                queue_item_id,
                exc,
            )

        # Promotion check: if this invoice was waiting on cooler items and is
        # now fully ready, advance it to ready_for_dispatch immediately.
        # This is the safety net for items that are picked but never boxed
        # (location_order mode, or any case where box_close won't fire).
        # The same logic also lives in box_close; having it here ensures the
        # status moves forward even when no box is ever created for the item.
        _invoice_no = row[1]
        try:
            from services.order_readiness import is_order_ready
            from models import Invoice as _Invoice
            _inv = _Invoice.query.filter_by(invoice_no=_invoice_no).first()
            if _inv is not None and _inv.status == "awaiting_batch_items" \
                    and is_order_ready(_invoice_no):
                _inv.status = "ready_for_dispatch"
                _audit(
                    "cooler.order_ready_for_dispatch",
                    f"Invoice {_invoice_no} promoted "
                    f"awaiting_batch_items -> ready_for_dispatch "
                    f"after cooler queue item #{queue_item_id} picked",
                    invoice_no=_invoice_no,
                )
        except Exception as _exc:
            current_app.logger.warning(
                "cooler.queue_pick: promotion check failed for %s: %s",
                _invoice_no, _exc,
            )

        # If this item was pre-assigned to a cooler box as 'planned',
        # upgrade it to 'picked' now that it has been physically collected.
        try:
            qty_req = float(row[3]) if row[3] is not None else None
            db.session.execute(
                text(
                    "UPDATE cooler_box_items "
                    "SET status = 'picked', "
                    "    picked_qty = :qty, "
                    "    picked_by  = :who, "
                    "    picked_at  = :now, "
                    "    updated_at = :now "
                    "WHERE queue_item_id = :qid "
                    "  AND status = 'planned'"
                ),
                {"qid": queue_item_id, "who": _username(), "now": now, "qty": qty_req},
            )
        except Exception as _upgrade_err:
            current_app.logger.warning(
                "cooler.queue_pick: could not upgrade planned box row for queue %s: %s",
                queue_item_id, _upgrade_err,
            )

        db.session.commit()
        flash(f"Picked {row[2]} for {row[1]}.", "success")

    # Redirect back to the picking screen
    route_id = row[6] if row else 0
    delivery_date = str(row[7]) if row and row[7] else ""
    if not delivery_date:
        date_row = db.session.execute(
            text("SELECT delivery_date FROM shipments WHERE id = :rid"),
            {"rid": route_id},
        ).fetchone()
        delivery_date = str(date_row[0]) if date_row and date_row[0] else ""
    if delivery_date:
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id,
                                delivery_date=delivery_date))
    return redirect(url_for("cooler.route_list"))


# ---------------------------------------------------------------------------
# Assign picked item to a box — form-friendly wrapper (queue_item_id in URL)
# ---------------------------------------------------------------------------
@cooler_bp.route("/queue/<int:queue_item_id>/assign-box", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def queue_assign_box(queue_item_id):
    """Assign a picked cooler queue row to a box; POST from the picking screen.

    Expects form fields: box_id, picked_qty, delivery_date (for redirect).
    Performs the same DB work as box_assign_item but redirects back to the
    route picking page instead of returning JSON.
    """
    delivery_date_str = (request.form.get("delivery_date") or "").strip()

    try:
        box_id = int(request.form.get("box_id"))
    except (TypeError, ValueError):
        flash("No box selected.", "danger")
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)

    try:
        picked_qty = float(request.form.get("picked_qty", 0))
    except (TypeError, ValueError):
        picked_qty = 0.0

    box = _fetch_box(box_id)
    if box is None:
        flash(f"Box #{box_id} not found.", "danger")
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)
    if box["status"] != "open":
        flash(f"Box #{box_id} is {box['status']}; only open boxes accept items.", "danger")
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)

    qrow = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_required, "
            "       bpq.status, bpq.pick_zone_type, "
            "       i.customer_code, i.customer_name, "
            "       i.route_id, s.delivery_date "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.id = :qid"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if qrow is None:
        abort(404)
    if qrow[5] != "cooler":
        flash("Not a cooler queue item.", "danger")
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)
    if qrow[4] != "picked":
        flash(
            f"Item status is '{qrow[4]}' — only physically-picked items can be assigned "
            f"to a box. Physical picking and box packing are separate steps.",
            "warning",
        )
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)

    if qrow[8] is None:
        flash("This item has no route assigned and cannot be placed in a box.", "danger")
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)
    if int(qrow[8]) != int(box["route_id"]):
        flash("Cannot assign an item from a different route to this box.", "danger")
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)

    # Duplicate-assignment guard: if this queue row is already linked to a
    # cooler_box_items row, refuse the second click and tell the user where
    # the item lives. Without this, repeated clicks (e.g. when the picker
    # missed the item-count update) silently pile up duplicate rows.
    existing = db.session.execute(
        text(
            "SELECT cb.box_no FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cbi.queue_item_id = :qid LIMIT 1"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    if existing is not None:
        flash(
            f"Item is already assigned to Box #{existing[0]}. "
            "Remove it from that box first if you want to move it.",
            "warning",
        )
        return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)

    invoice_no = qrow[1]
    item_code = qrow[2]
    if picked_qty <= 0:
        picked_qty = float(qrow[3]) if qrow[3] else 1.0

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
            "exp": float(qrow[3]) if qrow[3] else 0.0,
            "pq": picked_qty, "who": _username(), "now": now,
            "qid": queue_item_id,
        },
    )
    # Physical picking (pending → picked) is a separate event (queue_pick).
    # Box assignment never changes the queue status.
    _audit(
        "cooler.item_assigned",
        f"Cooler box #{box_id} <- queue #{queue_item_id} "
        f"invoice={invoice_no} item={item_code} qty={picked_qty} by {_username()}",
        invoice_no=invoice_no, item_code=item_code,
    )
    db.session.commit()
    flash(f"Assigned {item_code} to Box #{box['box_no']}.", "success")
    return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)


@cooler_bp.route("/box/<int:from_box_id>/move-item", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_move_item(from_box_id):
    """Move an item from one open cooler box to another open cooler box."""
    from_box = _fetch_box(from_box_id)
    if from_box is None:
        abort(404)
    if from_box["status"] != "open":
        return jsonify({"error": f"Source box #{from_box_id} is {from_box['status']}; "
                                  "only open boxes support item moves."}), 400

    data = request.get_json(silent=True) or request.form
    try:
        queue_item_id = int(data.get("queue_item_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "queue_item_id is required and must be int"}), 400
    try:
        to_box_id = int(data.get("to_box_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "to_box_id is required and must be int"}), 400

    if to_box_id == from_box_id:
        return jsonify({"error": "Source and destination box are the same."}), 400

    to_box = _fetch_box(to_box_id)
    if to_box is None:
        return jsonify({"error": f"Destination box #{to_box_id} not found."}), 404
    if to_box["status"] != "open":
        return jsonify({"error": f"Destination box #{to_box_id} is {to_box['status']}; "
                                  "only open boxes accept items."}), 400

    if int(from_box["route_id"]) != int(to_box["route_id"]) or \
            str(from_box["delivery_date"]) != str(to_box["delivery_date"]):
        return jsonify({"error": "Cannot move items between boxes on different routes or dates."}), 400

    src_row = db.session.execute(
        text(
            "SELECT id, invoice_no, item_code, customer_code, customer_name, "
            "       route_stop_id, delivery_sequence, item_name, expected_qty, "
            "       picked_qty, picked_by, picked_at, status "
            "FROM cooler_box_items "
            "WHERE cooler_box_id = :bid AND queue_item_id = :qid LIMIT 1"
        ),
        {"bid": from_box_id, "qid": queue_item_id},
    ).fetchone()
    if src_row is None:
        return jsonify({"error": f"Queue item {queue_item_id} not found in box #{from_box_id}."}), 404

    dup = db.session.execute(
        text("SELECT 1 FROM cooler_box_items WHERE cooler_box_id = :bid AND queue_item_id = :qid LIMIT 1"),
        {"bid": to_box_id, "qid": queue_item_id},
    ).fetchone()
    if dup is not None:
        return jsonify({"error": f"Item is already in destination box #{to_box_id}."}), 409

    now = get_utc_now()
    db.session.execute(
        text("DELETE FROM cooler_box_items WHERE cooler_box_id = :bid AND queue_item_id = :qid"),
        {"bid": from_box_id, "qid": queue_item_id},
    )
    db.session.execute(
        text(
            "INSERT INTO cooler_box_items "
            "(cooler_box_id, invoice_no, customer_code, customer_name, "
            " route_stop_id, delivery_sequence, item_code, item_name, "
            " expected_qty, picked_qty, picked_by, picked_at, "
            " queue_item_id, status, created_at, updated_at) "
            "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
            "        :exp, :pq, :who, :pat, :qid, :st, :now, :now)"
        ),
        {
            "bid": to_box_id,
            "inv": src_row[1], "cc": src_row[3], "cn": src_row[4],
            "rsid": src_row[5], "seq": src_row[6],
            "ic": src_row[2], "iname": src_row[7],
            "exp": src_row[8], "pq": src_row[9],
            "who": src_row[10], "pat": src_row[11],
            "qid": queue_item_id, "st": src_row[12],
            "now": now,
        },
    )
    _audit(
        "cooler.item_moved",
        f"Queue #{queue_item_id} invoice={src_row[1]} item={src_row[2]} "
        f"moved from box #{from_box_id} → box #{to_box_id} by {_username()}",
        invoice_no=src_row[1], item_code=src_row[2],
    )
    db.session.commit()

    if request.form.get("_html_form"):
        flash(f"Item moved to Box #{to_box['box_no']}.", "success")
        return redirect(url_for("cooler.route_picking",
                                route_id=from_box["route_id"],
                                delivery_date=str(from_box["delivery_date"])))

    return jsonify({
        "queue_item_id": queue_item_id,
        "from_box_id": from_box_id,
        "to_box_id": to_box_id,
        "status": "moved",
    }), 200


@cooler_bp.route("/queue/<int:queue_item_id>/skip", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def queue_skip(queue_item_id):
    """Skip a pending cooler item — mark as exception so it can be resumed later."""
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
        return jsonify({"error": f"Only pending items can be skipped (status={row[2]})."}), 400

    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'exception', updated_at = :now "
            "WHERE id = :qid AND status = 'pending'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_skipped",
        f"Cooler queue #{queue_item_id} invoice={row[0]} item={row[1]} "
        f"skipped by {_username()} (__skip__)",
        invoice_no=row[0], item_code=row[1],
    )
    db.session.commit()

    if request.form.get("_html_form"):
        flash(f"Item {row[1]} skipped — it appears in the Skipped section below.", "info")
        return _redirect_to_picking_from_queue(queue_item_id)

    return jsonify({"queue_item_id": queue_item_id, "status": "exception",
                    "reason": "__skip__"}), 200


@cooler_bp.route("/queue/<int:queue_item_id>/resume", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def queue_resume(queue_item_id):
    """Resume a skipped or exception cooler item — reset back to pending."""
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
    if row[2] != "exception":
        return jsonify({"error": f"Only exception items can be resumed (status={row[2]})."}), 400

    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'pending', updated_at = :now "
            "WHERE id = :qid AND status = 'exception'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_resumed",
        f"Cooler queue #{queue_item_id} invoice={row[0]} item={row[1]} "
        f"resumed (exception → pending) by {_username()}",
        invoice_no=row[0], item_code=row[1],
    )
    db.session.commit()

    if request.form.get("_html_form"):
        flash(f"Item {row[1]} resumed — it is back in the pick list.", "success")
        return _redirect_to_picking_from_queue(queue_item_id)

    return jsonify({"queue_item_id": queue_item_id, "status": "pending"}), 200


@cooler_bp.route("/route/<route_id>/pack-stop", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def pack_stop(route_id):
    """DISABLED — superseded by generate-box-plan / confirm-box-plan."""
    return jsonify({"error": "This endpoint has been disabled."}), 410

    delivery_date_str = (request.form.get("delivery_date") or "").strip()
    if not delivery_date_str:
        flash("Missing delivery date.", "danger")
        return redirect(url_for("cooler.route_list"))
    try:
        delivery_sequence = float(request.form.get("delivery_sequence"))
    except (TypeError, ValueError):
        flash("Invalid delivery stop sequence.", "danger")
        return redirect(url_for("cooler.route_list"))

    # Collect all picked, unboxed queue items for this stop
    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_picked, "
            "       i.customer_code, i.customer_name, "
            "       rs.route_stop_id, rs.seq_no, ii.item_name "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN route_stop_invoice rsi "
            "       ON rsi.invoice_no = bpq.invoice_no "
            "      AND rsi.is_active = :truthy "
            "LEFT JOIN route_stop rs "
            "       ON rs.route_stop_id = rsi.route_stop_id "
            "LEFT JOIN invoice_items ii "
            "       ON ii.invoice_no = bpq.invoice_no "
            "      AND ii.item_code = bpq.item_code "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :rid "
            "  AND bpq.delivery_sequence = :seq "
            "  AND bpq.status = 'picked' "
            "  AND NOT EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.queue_item_id = bpq.id"
            "  ) "
            "ORDER BY bpq.invoice_no, bpq.item_code"
        ),
        {"rid": route_id_int, "truthy": True, "seq": delivery_sequence},
    ).fetchall()

    if not rows:
        flash("No picked unassigned items for this stop.", "warning")
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id_int,
                                delivery_date=delivery_date_str))

    # Find the next available box number for this route + date
    last_no = db.session.execute(
        text(
            "SELECT COALESCE(MAX(box_no), 0) FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = :dd"
        ),
        {"rid": route_id_int, "dd": delivery_date_str},
    ).scalar() or 0
    new_box_no = int(last_no) + 1

    now = get_utc_now()
    result = db.session.execute(
        text(
            "INSERT INTO cooler_boxes "
            "(route_id, delivery_date, box_no, status, created_at, created_by) "
            "VALUES (:rid, :dd, :bno, 'open', :now, :who) RETURNING id"
        ),
        {
            "rid": route_id_int,
            "dd": delivery_date_str,
            "bno": new_box_no,
            "now": now,
            "who": _username(),
        },
    ).fetchone()
    box_id = result[0]

    for r in rows:
        queue_item_id = r[0]
        invoice_no = r[1]
        item_code = r[2]
        qty_picked = float(r[3] or 0)
        db.session.execute(
            text(
                "INSERT INTO cooler_box_items "
                "(cooler_box_id, invoice_no, customer_code, customer_name, "
                " route_stop_id, delivery_sequence, item_code, item_name, "
                " expected_qty, picked_qty, picked_by, picked_at, "
                " queue_item_id, status, created_at) "
                "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
                "        :exp, :pq, :who, :now, :qid, 'picked', :now)"
            ),
            {
                "bid": box_id, "inv": invoice_no,
                "cc": r[4], "cn": r[5],
                "rsid": r[6], "seq": r[7],
                "ic": item_code, "iname": r[8],
                "exp": qty_picked, "pq": qty_picked,
                "who": _username(), "now": now,
                "qid": queue_item_id,
            },
        )

    _audit(
        "cooler.pack_stop",
        f"Box #{new_box_no} (id={box_id}) created for stop "
        f"seq={delivery_sequence} route={route_id_int} "
        f"with {len(rows)} item(s) by {_username()}",
    )
    db.session.commit()
    flash(
        f"Box #{new_box_no} created with {len(rows)} item(s) "
        f"for stop {int(delivery_sequence)}.",
        "success",
    )
    return redirect(url_for("cooler.route_picking",
                            route_id=route_id_int,
                            delivery_date=delivery_date_str))


def _redirect_to_picking_from_queue(queue_item_id, delivery_date_str=""):
    """Helper: redirect back to the route picking page after a queue action."""
    row = db.session.execute(
        text(
            "SELECT i.route_id, s.delivery_date "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "LEFT JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.id = :qid"
        ),
        {"qid": queue_item_id},
    ).fetchone()
    route_id = row[0] if row else None
    delivery_date = delivery_date_str or (str(row[1]) if row and row[1] else "")
    if not delivery_date and route_id:
        date_row = db.session.execute(
            text("SELECT delivery_date FROM shipments WHERE id = :rid"),
            {"rid": route_id},
        ).fetchone()
        delivery_date = str(date_row[0]) if date_row and date_row[0] else ""
    if route_id and delivery_date:
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id,
                                delivery_date=delivery_date))
    return redirect(url_for("cooler.route_list"))


# ---------------------------------------------------------------------------
# Exception handling (§5.8)
# ---------------------------------------------------------------------------
@cooler_bp.route("/queue/<int:queue_item_id>/exception", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
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
@_require_cooler_manage
@_require_picking_flag
def queue_move_to_normal(queue_item_id):
    """Admin-only: move a cooler queue row back to normal."""
    return _move_zone(queue_item_id, "cooler", "normal")


@cooler_bp.route("/queue/<int:queue_item_id>/move-to-cooler", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
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
@_require_cooler_print
@_require_labels_flag
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
@_require_cooler_print
@_require_labels_flag
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
@_require_cooler_print
@_require_labels_flag
def route_manifest(route_id, delivery_date):
    if _parse_date(delivery_date) is None:
        return jsonify({"error": "delivery_date must be YYYY-MM-DD"}), 400

    # All boxes whose items belong to invoices on this route + date.
    # filter on Invoice.route_id (FK to shipments.id),
    # NOT Invoice.routing (free-text label). The cooler_boxes.route_id
    # column is itself the shipment FK, so we can short-circuit and
    # filter directly on the box (no Invoice.routing join needed).
    try:
        _route_id_int = int(route_id)
    except (TypeError, ValueError):
        _route_id_int = None
    box_rows = db.session.execute(
        text(
            "SELECT DISTINCT cb.id, cb.route_id, cb.delivery_date, cb.box_no, "
            "       cb.status, cb.first_stop_sequence, cb.last_stop_sequence "
            "FROM cooler_boxes cb "
            "WHERE cb.delivery_date = :dd "
            "  AND cb.route_id = :rid "
            "ORDER BY cb.box_no"
        ),
        {"dd": str(delivery_date), "rid": _route_id_int},
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
