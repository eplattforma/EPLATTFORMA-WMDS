# Cooler Picking — Implementation Instructions for Replit

Please apply the following changes to the WMDS project. There are **3 files to replace completely**. Do not change any other files. After applying, restart the server and confirm there are no import errors.

---

## FILE 1 — Replace `services/cooler_box_planner.py` entirely

Replace the entire file with this content:

```python
"""Cooler box plan generator.

Groups picked, unboxed cooler queue rows into physical boxes based on
capacity.  Stop order is LIFO (last delivery stop first) so the first
box created carries the last-stop items — these go at the bottom /
back of the truck and are loaded first.

Box-type selection:
  - When a specific ``box_type_id`` is supplied the whole plan uses that
    type (backwards-compatible with the old UI).
  - When ``box_type_id`` is ``None`` (auto) the planner loads ALL active
    box types and, for each new box, selects the *smallest* type whose
    usable volume and weight limit can hold the current stop's items.
    This produces a natural mix: large stops get large boxes, small stops
    get small ones, with minimal wasted space.

Returns a list of box dicts, each with:
  box_no, box_type_id, box_type_name, stop_min, stop_max, stop_display,
  stops, queue_item_ids, item_summaries, estimated_fill_cm3,
  estimated_fill_pct, estimated_weight_kg, missing_dimension_count,
  warnings

Each item in ``item_summaries`` carries a ``has_dimensions`` boolean so
the UI can flag items whose volume cannot be estimated.
"""
import logging
from collections import defaultdict

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

# LIFO: highest seq_no processed first → Box 1 = last-delivery stops
# (load first, unload last — sits at the back/bottom of the truck).
STOP_ORDER = "last_first"   # 'last_first' | 'first_first'


def _num(value):
    try:
        v = float(value)
        return v if v == v else None  # reject NaN
    except (TypeError, ValueError):
        return None


def _load_box_types(box_type_id=None):
    """Return a list of active box-type dicts.

    If *box_type_id* is given, return only that type (or [] if not found).
    Otherwise return all active types sorted largest usable volume first.
    """
    if box_type_id:
        try:
            row = db.session.execute(
                text(
                    "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                    "FROM cooler_box_types WHERE id = :id AND is_active = true"
                ),
                {"id": int(box_type_id)},
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            logger.warning("generate_box_plan: box_type_id=%s not found", box_type_id)
            return []
        rows = [row]
    else:
        try:
            rows = db.session.execute(
                text(
                    "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                    "FROM cooler_box_types WHERE is_active = true "
                    "ORDER BY (internal_volume_cm3 * COALESCE(fill_efficiency, 1)) DESC, "
                    "         sort_order, name"
                )
            ).fetchall()
        except Exception as exc:
            logger.warning("generate_box_plan: failed to load box types: %s", exc)
            rows = []

    result = []
    for r in rows:
        cap = (_num(r[2]) or 0.0) * (_num(r[3]) or 1.0)
        result.append({
            "id": r[0],
            "name": r[1],
            "internal_volume_cm3": _num(r[2]) or 0.0,
            "fill_efficiency": _num(r[3]) or 1.0,
            "max_weight_kg": _num(r[4]) or 0.0,
            "usable_capacity": cap,
        })
    return result


def _pick_box_type(box_types, needed_volume, needed_weight):
    """Select the smallest box type that fits *needed_volume* and *needed_weight*.

    Sorted smallest-to-largest; the first type that fits is returned.
    If nothing fits, the largest type is returned (plan will show overflow warning).
    Falls back to the first entry in *box_types* when the list has only one item.
    """
    if not box_types:
        return None
    if len(box_types) == 1:
        return box_types[0]

    sorted_asc = sorted(box_types, key=lambda t: t["usable_capacity"])
    for bt in sorted_asc:
        vol_ok = bt["usable_capacity"] <= 0 or needed_volume <= bt["usable_capacity"]
        wt_ok = bt["max_weight_kg"] <= 0 or needed_weight <= bt["max_weight_kg"]
        if vol_ok and wt_ok:
            return bt
    return sorted_asc[-1]


def generate_box_plan(route_id, delivery_date, box_type_id=None):
    """Generate a box plan for all picked-but-unboxed cooler items on a route.

    Returns [] when there are no eligible items or no active box types.
    Returns a dict ``{"ok": False, "message": "..."}`` if items lack
    delivery sequences (lock_sequencing must be run first).
    """
    route_id = int(route_id)
    delivery_date = str(delivery_date)

    box_types = _load_box_types(box_type_id)
    if not box_types:
        logger.warning("generate_box_plan: no active box type found")
        return []

    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, "
            "       COALESCE(bpq.qty_picked, bpq.qty_required, 1) AS qty, "
            "       i.customer_code, i.customer_name, "
            "       rs.route_stop_id, rs.seq_no, ii.item_name "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "LEFT JOIN route_stop_invoice rsi "
            "       ON rsi.invoice_no = bpq.invoice_no AND rsi.is_active = :truthy "
            "LEFT JOIN route_stop rs "
            "       ON rs.route_stop_id = rsi.route_stop_id "
            "LEFT JOIN invoice_items ii "
            "       ON ii.invoice_no = bpq.invoice_no AND ii.item_code = bpq.item_code "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND bpq.status = 'picked' "
            "  AND i.route_id = :rid "
            "  AND s.delivery_date = :dd "
            "  AND NOT EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.queue_item_id = bpq.id"
            "  ) "
            "ORDER BY COALESCE(rs.seq_no, 0) {order}, bpq.invoice_no, bpq.item_code".format(
                order="DESC" if STOP_ORDER == "last_first" else "ASC"
            )
        ),
        {"rid": route_id, "dd": delivery_date, "truthy": True},
    ).fetchall()

    if not rows:
        return []

    missing_seq = [r for r in rows if r[7] is None]
    if missing_seq:
        invoice_samples = list({r[1] for r in missing_seq})[:5]
        return {
            "ok": False,
            "plan": [],
            "message": (
                f"{len(missing_seq)} picked item(s) have no delivery sequence "
                f"(e.g. invoice(s): {', '.join(invoice_samples)}). "
                "Please run Confirm Cooler Route first, "
                "then generate the box plan again."
            ),
        }

    item_codes = list({r[2] for r in rows})
    dim_map = {}
    if item_codes:
        try:
            dim_rows = db.session.execute(
                text(
                    "SELECT item_code_365, item_length, item_width, item_height, item_weight "
                    "FROM ps_items_dw "
                    "WHERE item_code_365 = ANY(:codes)"
                ),
                {"codes": item_codes},
            ).fetchall()
            for dr in dim_rows:
                dim_map[dr[0]] = {
                    "length": _num(dr[1]),
                    "width": _num(dr[2]),
                    "height": _num(dr[3]),
                    "weight": _num(dr[4]),
                }
        except Exception as exc:
            logger.warning("generate_box_plan: dimension lookup failed: %s", exc)

    by_stop = defaultdict(list)
    for r in rows:
        stop_seq = _num(r[7])
        by_stop[stop_seq].append(r)

    stops = sorted(
        by_stop.keys(),
        key=lambda x: x if x is not None else -1,
        reverse=(STOP_ORDER == "last_first"),
    )

    stop_volumes = {}
    stop_weights = {}
    for stop_seq in stops:
        vol = 0.0
        wt = 0.0
        for r in by_stop[stop_seq]:
            qty = _num(r[3]) or 1.0
            dims = dim_map.get(r[2], {})
            l, w, h = dims.get("length"), dims.get("width"), dims.get("height")
            weight = dims.get("weight")
            if l is not None and w is not None and h is not None:
                vol += l * w * h * qty
            if weight is not None:
                wt += weight * qty
        stop_volumes[stop_seq] = vol
        stop_weights[stop_seq] = wt

    plan = []
    box_no = 1
    current = None
    current_type = None

    def flush_box():
        nonlocal current, current_type, box_no
        if not current:
            return
        usable = current_type["usable_capacity"] if current_type else 0
        max_wt = current_type["max_weight_kg"] if current_type else 0
        current["estimated_fill_pct"] = (
            round((current["estimated_fill_cm3"] / usable) * 100, 1)
            if usable else 0
        )
        if usable and current["estimated_fill_cm3"] > usable:
            current["warnings"].append(
                f"Box exceeds capacity "
                f"({current['estimated_fill_cm3']:.0f} cm³ > {usable:.0f} cm³ usable)."
            )
        if max_wt and current["estimated_weight_kg"] > max_wt:
            current["warnings"].append(
                f"Box exceeds weight limit "
                f"({current['estimated_weight_kg']:.1f} kg > {max_wt:.1f} kg)."
            )
        plan.append(current)
        box_no += 1
        current = None
        current_type = None

    def _stop_int(s):
        return int(s) if s is not None else 0

    for stop_seq in stops:
        stop_rows = by_stop[stop_seq]
        stop_volume = stop_volumes[stop_seq]
        stop_weight = stop_weights[stop_seq]
        missing_count = 0
        item_summaries = []
        queue_item_ids = []
        stop_no = _stop_int(stop_seq)

        for r in stop_rows:
            qty = _num(r[3]) or 1.0
            dims = dim_map.get(r[2], {})
            length = dims.get("length")
            width = dims.get("width")
            height = dims.get("height")
            weight = dims.get("weight")

            has_dims = (length is not None and width is not None and height is not None)
            est_vol = (length * width * height * qty) if has_dims else 0.0
            est_wt = (weight * qty) if weight is not None else 0.0

            if not has_dims:
                missing_count += 1

            queue_item_ids.append(int(r[0]))
            item_summaries.append({
                "queue_item_id": int(r[0]),
                "invoice_no": r[1],
                "customer_code": r[4],
                "customer_name": r[5],
                "route_stop_id": r[6],
                "delivery_sequence": stop_seq,
                "item_code": r[2],
                "item_name": r[8],
                "qty": qty,
                "estimated_volume_cm3": est_vol,
                "estimated_weight_kg": est_wt,
                "has_dimensions": has_dims,
            })

        if current is not None and current_type is not None:
            new_vol = current["estimated_fill_cm3"] + stop_volume
            new_wt = current["estimated_weight_kg"] + stop_weight
            usable = current_type["usable_capacity"]
            max_wt = current_type["max_weight_kg"]
            fits_vol = usable <= 0 or new_vol <= usable
            fits_wt = max_wt <= 0 or new_wt <= max_wt
        else:
            fits_vol = fits_wt = False

        if current is not None and fits_vol and fits_wt:
            current["stops"] = sorted(set(current["stops"] + [stop_no]), reverse=True)
            current["stop_min"] = min(current["stops"])
            current["stop_max"] = max(current["stops"])
            current["stop_display"] = (
                f"Stops {current['stop_max']} → {current['stop_min']}"
                if current["stop_min"] != current["stop_max"]
                else f"Stop {current['stop_max']}"
            )
            current["queue_item_ids"].extend(queue_item_ids)
            current["item_summaries"].extend(item_summaries)
            current["estimated_fill_cm3"] += stop_volume
            current["estimated_weight_kg"] += stop_weight
            current["missing_dimension_count"] += missing_count
            if missing_count:
                _dim_warn = "Some items are missing dimensions — fill estimate may be low."
                if _dim_warn not in current["warnings"]:
                    current["warnings"].append(_dim_warn)
            continue

        flush_box()

        chosen = _pick_box_type(box_types, stop_volume, stop_weight)
        current_type = chosen

        _dim_warn_list = []
        if missing_count:
            _dim_warn_list.append(
                "Some items are missing dimensions — fill estimate may be low."
            )

        current = {
            "box_no": box_no,
            "box_type_id": chosen["id"],
            "box_type_name": chosen["name"],
            "stop_min": stop_no,
            "stop_max": stop_no,
            "stop_display": f"Stop {stop_no}" if stop_seq is not None else "No stop",
            "stops": [stop_no],
            "queue_item_ids": queue_item_ids,
            "item_summaries": item_summaries,
            "estimated_fill_cm3": stop_volume,
            "estimated_fill_pct": 0,
            "estimated_weight_kg": stop_weight,
            "missing_dimension_count": missing_count,
            "warnings": _dim_warn_list,
        }

    flush_box()
    return plan


def pre_pick_estimate(route_id, delivery_date):
    """Lightweight volume/weight estimate for the route-list screen."""
    route_id = int(route_id)
    delivery_date = str(delivery_date)

    rows = db.session.execute(
        text(
            "SELECT bpq.item_code, "
            "       COALESCE(bpq.qty_required, 1) AS qty "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :rid "
            "  AND s.delivery_date = :dd"
        ),
        {"rid": route_id, "dd": delivery_date},
    ).fetchall()

    if not rows:
        return None

    item_codes = list({r[0] for r in rows})
    dim_map = {}
    try:
        dim_rows = db.session.execute(
            text(
                "SELECT item_code_365, item_length, item_width, item_height, item_weight "
                "FROM ps_items_dw WHERE item_code_365 = ANY(:codes)"
            ),
            {"codes": item_codes},
        ).fetchall()
        for dr in dim_rows:
            dim_map[dr[0]] = {
                "length": _num(dr[1]),
                "width": _num(dr[2]),
                "height": _num(dr[3]),
                "weight": _num(dr[4]),
            }
    except Exception:
        pass

    total_vol = 0.0
    total_wt = 0.0
    missing = 0
    for r in rows:
        qty = _num(r[1]) or 1.0
        dims = dim_map.get(r[0], {})
        l, w, h = dims.get("length"), dims.get("width"), dims.get("height")
        wt = dims.get("weight")
        if l is not None and w is not None and h is not None:
            total_vol += l * w * h * qty
        else:
            missing += 1
        if wt is not None:
            total_wt += wt * qty

    total = len(rows)
    pct = round((total - missing) / total * 100) if total else 0
    label = "good" if pct >= 80 else "limited" if pct >= 40 else "poor"

    return {
        "total_volume_l": round(total_vol / 1000, 1),
        "total_weight_kg": round(total_wt, 1),
        "item_count": total,
        "missing_dimension_count": missing,
        "data_quality_pct": pct,
        "data_quality_label": label,
    }
```

---

## FILE 2 — Add 3 new functions to `blueprints/cooler_picking.py`

Find this exact line in the file:

```python
@cooler_bp.route("/route/<route_id>/pack-stop", methods=["POST"])
```

Insert the following block of code **immediately before** that line (keep the existing `pack_stop` function unchanged below it):

```python
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

```

---

## FILE 3 — Replace `templates/cooler/route_picking.html` entirely

Replace the entire file with the content in the file `templates/cooler/route_picking.html` from the uploaded project folder. (The file has already been rewritten — just make sure Replit uses the new version, not the old one.)

If Replit cannot read the file directly, paste the content from the file provided.

---

## Summary of what changed and why

| Change | Where | Why |
|--------|--------|-----|
| **"Prepare Cooler Route" renamed to "Confirm Cooler Route"** | Template | Clearer language |
| **Multi-box-type packing** | `cooler_box_planner.py` | Auto selects smallest fitting box per stop group; large stops → large box, small stops → small box |
| **LIFO loading order** | `cooler_box_planner.py` | Last-delivery stops go in Box 1 (loaded first, bottom of truck); first-delivery stops on top |
| **`has_dimensions` flag per item** | `cooler_box_planner.py` | Items missing L×W×H are flagged with a ⚠️ "No dims" badge in the box plan preview |
| **`pre_pick_estimate` function** | `cooler_box_planner.py` | Was missing (caused a crash on the route list page) — now implemented |
| **4-step wizard UI** | Template | Step 1 Confirm → Step 2 Pick → Step 3 Boxes → Step 4 Close/Dispatch |
| **Skip / Resume buttons** | Template + `cooler_picking.py` | Picker can skip a pending item; it goes to a "Skipped" section and can be resumed |
| **Move item between boxes** | Template + `cooler_picking.py` | Each open box's item list shows a move dropdown to transfer an item to another open box |
| **Dimension warning in plan preview** | Template JS | Shows a "No dims" badge on each item missing dimensions and a total count at the top |

---

## No database migrations needed

All changes are Python/Jinja2 only. No new tables or columns are required.
