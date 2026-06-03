# Cooler Picking — Implementation Instructions

Please apply the following changes. There are **3 files to update**. No database migrations needed.

---

## FILE 1 — Replace `services/cooler_box_planner.py` entirely

```python
"""Cooler box plan generator.

Groups unboxed cooler queue rows into physical boxes based on capacity.
Stop order is LIFO (last delivery stop first) so Box 1 carries the last-stop
items — these go at the bottom/back of the truck and are loaded first.

Box-type selection:
  - When a specific box_type_id is supplied the whole plan uses that type.
  - When box_type_id is None (auto) the planner loads ALL active box types
    and, for each new box, selects the *smallest* type whose usable volume
    and weight limit can hold the current stop's items.

By default (include_pending=True) the plan covers ALL unboxed items —
both pending and picked — so it can be confirmed BEFORE picking starts.
The picker then sees "Pick → Box #N" and places items directly in the
right box. No post-pick sorting needed.
"""
import logging
from collections import defaultdict

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

STOP_ORDER = "last_first"   # 'last_first' | 'first_first'


def _num(value):
    try:
        v = float(value)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _load_box_types(box_type_id=None):
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


def generate_box_plan(route_id, delivery_date, box_type_id=None, include_pending=True):
    """Generate a box plan for cooler items on a route.

    include_pending=True  → plan pending + picked items (use before picking starts)
    include_pending=False → plan only already-picked items (legacy behaviour)
    """
    route_id = int(route_id)
    delivery_date = str(delivery_date)

    box_types = _load_box_types(box_type_id)
    if not box_types:
        logger.warning("generate_box_plan: no active box type found")
        return []

    status_filter = "bpq.status IN ('pending', 'picked')" if include_pending \
        else "bpq.status = 'picked'"

    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, "
            "       COALESCE(bpq.qty_required, 1) AS qty, "
            "       i.customer_code, i.customer_name, "
            "       rs.route_stop_id, rs.seq_no, ii.item_name, bpq.status "
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
            "  AND {status_filter} "
            "  AND i.route_id = :rid "
            "  AND s.delivery_date = :dd "
            "  AND NOT EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.queue_item_id = bpq.id"
            "  ) "
            "ORDER BY COALESCE(rs.seq_no, 0) {order}, bpq.invoice_no, bpq.item_code".format(
                status_filter=status_filter,
                order="DESC" if STOP_ORDER == "last_first" else "ASC",
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
                f"{len(missing_seq)} item(s) have no delivery sequence "
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
            round((current["estimated_fill_cm3"] / usable) * 100, 1) if usable else 0
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
                "queue_status": r[9],
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
            "SELECT bpq.item_code, COALESCE(bpq.qty_required, 1) AS qty "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :rid AND s.delivery_date = :dd"
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
                "length": _num(dr[1]), "width": _num(dr[2]),
                "height": _num(dr[3]), "weight": _num(dr[4]),
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

## FILE 2 — Four targeted edits to `blueprints/cooler_picking.py`

### Edit A — `route_picking` view: track planned box assignments

Find this block (around the `assigned_rows` query):

```python
    assigned_rows = db.session.execute(
        text(
            "SELECT cbi.queue_item_id, cb.box_no "
            "FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "  AND cbi.queue_item_id IS NOT NULL"
        ),
        {"rid": _route_id_int, "dd": str(delivery_date)},
    ).fetchall()
    assigned_to_box = {int(r[0]): int(r[1]) for r in assigned_rows}
```

Replace with:

```python
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
```

### Edit B — `route_picking` view: pass `planned_box` to template

Find:

```python
        assigned_to_box=assigned_to_box,
        picked_unboxed_count=picked_unboxed_count,
```

Replace with:

```python
        assigned_to_box=assigned_to_box,
        planned_box=planned_box,
        picked_unboxed_count=picked_unboxed_count,
```

### Edit C — `confirm_box_plan`: accept pending items as 'planned'

Find the pre-flight check block inside `confirm_box_plan`:

```python
                if qcheck[0] != "picked":
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} status={qcheck[0]} (not picked) — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue

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
                        "bid": box_id,
                        "inv": item["invoice_no"],
                        "cc": item["customer_code"],
                        "cn": item["customer_name"],
                        "rsid": item["route_stop_id"],
                        "seq": item["delivery_sequence"],
                        "ic": item["item_code"],
                        "iname": item["item_name"],
                        "exp": item["qty"],
                        "pq": item["qty"],
                        "who": _username(),
                        "now": now,
                        "qid": qid,
                    },
                )
                items_inserted += 1
```

Replace with:

```python
                if qcheck[0] not in ("pending", "picked"):
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} status={qcheck[0]} (not pending/picked) — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue

                # Use 'planned' for pending items (picker will pick them into the box).
                # Use 'picked' for items already physically picked.
                cbi_status = "picked" if qcheck[0] == "picked" else "planned"
                picked_qty_val = item["qty"] if qcheck[0] == "picked" else 0.0
                picked_by_val = _username() if qcheck[0] == "picked" else None
                picked_at_val = now if qcheck[0] == "picked" else None

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
                        "bid": box_id,
                        "inv": item["invoice_no"],
                        "cc": item["customer_code"],
                        "cn": item["customer_name"],
                        "rsid": item["route_stop_id"],
                        "seq": item["delivery_sequence"],
                        "ic": item["item_code"],
                        "iname": item["item_name"],
                        "exp": item["qty"],
                        "pq": picked_qty_val,
                        "who": picked_by_val,
                        "pat": picked_at_val,
                        "qid": qid,
                        "st": cbi_status,
                        "now": now,
                    },
                )
                items_inserted += 1
```

### Edit D — `queue_pick`: auto-promote planned→picked + fix box_close guard

Inside `queue_pick`, find the `_audit("cooler.item_picked", ...)` call and add the pick-to-box block immediately after it (before the `try: session_row = ...` block):

```python
        _audit(
            "cooler.item_picked",
            ...
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
                ...
```

Also in `box_close`, find the unpicked guard:

```python
    unpicked = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_box_items cbi "
            "JOIN batch_pick_queue bpq ON bpq.id = cbi.queue_item_id "
            "WHERE cbi.cooler_box_id = :bid AND bpq.qty_picked = 0"
        ),
        {"bid": box_id},
    ).scalar() or 0
```

Replace with:

```python
    unpicked = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_box_items cbi "
            "WHERE cbi.cooler_box_id = :bid "
            "  AND (cbi.status = 'planned' OR cbi.picked_qty = 0)"
        ),
        {"bid": box_id},
    ).scalar() or 0
```

### Also add these 3 new endpoints — insert before `pack_stop`

Find the line:
```python
@cooler_bp.route("/route/<route_id>/pack-stop", methods=["POST"])
```

Insert before it:

```python
@cooler_bp.route("/box/<int:from_box_id>/move-item", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_move_item(from_box_id):
    """Move an item from one open cooler box to another."""
    from_box = _fetch_box(from_box_id)
    if from_box is None:
        abort(404)
    if from_box["status"] != "open":
        return jsonify({"error": f"Source box #{from_box_id} is not open."}), 400
    data = request.get_json(silent=True) or request.form
    try:
        queue_item_id = int(data.get("queue_item_id"))
        to_box_id = int(data.get("to_box_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "queue_item_id and to_box_id are required ints"}), 400
    if to_box_id == from_box_id:
        return jsonify({"error": "Source and destination are the same."}), 400
    to_box = _fetch_box(to_box_id)
    if to_box is None:
        return jsonify({"error": f"Destination box #{to_box_id} not found."}), 404
    if to_box["status"] != "open":
        return jsonify({"error": f"Destination box #{to_box_id} is not open."}), 400
    if int(from_box["route_id"]) != int(to_box["route_id"]) or \
            str(from_box["delivery_date"]) != str(to_box["delivery_date"]):
        return jsonify({"error": "Cannot move items between different routes/dates."}), 400
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
        return jsonify({"error": f"Item {queue_item_id} not in box #{from_box_id}."}), 404
    dup = db.session.execute(
        text("SELECT 1 FROM cooler_box_items WHERE cooler_box_id=:bid AND queue_item_id=:qid LIMIT 1"),
        {"bid": to_box_id, "qid": queue_item_id},
    ).fetchone()
    if dup:
        return jsonify({"error": f"Item already in box #{to_box_id}."}), 409
    now = get_utc_now()
    db.session.execute(
        text("DELETE FROM cooler_box_items WHERE cooler_box_id=:bid AND queue_item_id=:qid"),
        {"bid": from_box_id, "qid": queue_item_id},
    )
    db.session.execute(
        text(
            "INSERT INTO cooler_box_items "
            "(cooler_box_id, invoice_no, customer_code, customer_name, "
            " route_stop_id, delivery_sequence, item_code, item_name, "
            " expected_qty, picked_qty, picked_by, picked_at, "
            " queue_item_id, status, created_at, updated_at) "
            "VALUES (:bid,:inv,:cc,:cn,:rsid,:seq,:ic,:iname,:exp,:pq,:who,:pat,:qid,:st,:now,:now)"
        ),
        {"bid": to_box_id, "inv": src_row[1], "cc": src_row[3], "cn": src_row[4],
         "rsid": src_row[5], "seq": src_row[6], "ic": src_row[2], "iname": src_row[7],
         "exp": src_row[8], "pq": src_row[9], "who": src_row[10], "pat": src_row[11],
         "qid": queue_item_id, "st": src_row[12], "now": now},
    )
    _audit("cooler.item_moved",
           f"Queue #{queue_item_id} moved box #{from_box_id}→#{to_box_id} by {_username()}",
           invoice_no=src_row[1], item_code=src_row[2])
    db.session.commit()
    if request.form.get("_html_form"):
        flash(f"Item moved to Box #{to_box['box_no']}.", "success")
        return redirect(url_for("cooler.route_picking",
                                route_id=from_box["route_id"],
                                delivery_date=str(from_box["delivery_date"])))
    return jsonify({"queue_item_id": queue_item_id, "from_box_id": from_box_id,
                    "to_box_id": to_box_id, "status": "moved"}), 200


@cooler_bp.route("/queue/<int:queue_item_id>/skip", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def queue_skip(queue_item_id):
    """Skip a pending cooler item — mark exception so it can be resumed later."""
    row = db.session.execute(
        text("SELECT invoice_no, item_code, status, pick_zone_type FROM batch_pick_queue WHERE id=:qid"),
        {"qid": queue_item_id},
    ).fetchone()
    if row is None: abort(404)
    if row[3] != "cooler": return jsonify({"error": "Not a cooler row."}), 400
    if row[2] != "pending": return jsonify({"error": f"Only pending items can be skipped (status={row[2]})."}), 400
    now = get_utc_now()
    db.session.execute(
        text("UPDATE batch_pick_queue SET status='exception', updated_at=:now WHERE id=:qid AND status='pending'"),
        {"now": now, "qid": queue_item_id},
    )
    _audit("cooler.item_skipped",
           f"Queue #{queue_item_id} invoice={row[0]} item={row[1]} skipped by {_username()}",
           invoice_no=row[0], item_code=row[1])
    db.session.commit()
    if request.form.get("_html_form"):
        flash(f"Item {row[1]} skipped.", "info")
        return _redirect_to_picking_from_queue(queue_item_id)
    return jsonify({"queue_item_id": queue_item_id, "status": "exception"}), 200


@cooler_bp.route("/queue/<int:queue_item_id>/resume", methods=["POST"])
@login_required
@require_permission("cooler.pick")
@_require_cooler_pick
@_require_picking_flag
def queue_resume(queue_item_id):
    """Resume a skipped/exception cooler item — reset to pending."""
    row = db.session.execute(
        text("SELECT invoice_no, item_code, status, pick_zone_type FROM batch_pick_queue WHERE id=:qid"),
        {"qid": queue_item_id},
    ).fetchone()
    if row is None: abort(404)
    if row[3] != "cooler": return jsonify({"error": "Not a cooler row."}), 400
    if row[2] != "exception": return jsonify({"error": f"Only exception items can be resumed (status={row[2]})."}), 400
    now = get_utc_now()
    db.session.execute(
        text("UPDATE batch_pick_queue SET status='pending', updated_at=:now WHERE id=:qid AND status='exception'"),
        {"now": now, "qid": queue_item_id},
    )
    _audit("cooler.item_resumed",
           f"Queue #{queue_item_id} invoice={row[0]} item={row[1]} resumed by {_username()}",
           invoice_no=row[0], item_code=row[1])
    db.session.commit()
    if request.form.get("_html_form"):
        flash(f"Item {row[1]} resumed.", "success")
        return _redirect_to_picking_from_queue(queue_item_id)
    return jsonify({"queue_item_id": queue_item_id, "status": "pending"}), 200
```

---

## FILE 3 — `templates/cooler/route_picking.html`

The file has already been rewritten and saved in the project folder. Make sure Replit uses the saved version (do not regenerate it). Key things it contains that must be preserved:

- 4-step wizard at the top (Confirm Cooler Route → Pick Items → Assign to Boxes → Close & Dispatch)
- "Confirm Cooler Route" button (not "Prepare Cooler Route")
- Picking table shows **"Pick → Box #N"** (green button) for items that have a pre-planned box assignment, and a plain **"Pick"** (blue) for unassigned items
- **Skip** button next to each pending item; skipped items appear in a separate section with a **Resume** button
- Box cards show a **move item** dropdown to transfer items between open boxes
- Box plan preview shows `⚠️ No dims` badge on items missing dimensions

---

## Workflow summary (what this enables)

```
1. Confirm Cooler Route     → sequences all items
2. Generate Box Plan        → works on PENDING items (before picking starts)
3. Confirm Box Plan         → creates boxes, pre-assigns items (status = 'planned')
4. Picker picks items       → sees "Pick → Box #2", one click picks & marks box assignment done
5. Close boxes              → only allowed once all planned items are physically picked
```

No database schema changes required.
