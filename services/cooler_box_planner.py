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


def generate_box_plan(route_id, delivery_date, box_type_id=None, include_pending=False):
    """Generate a box plan for cooler items on a route.

    When ``include_pending=False`` (default) only already-picked items that
    are not yet in a box are included — used by the post-pick box-plan flow
    on the packing screen.

    When ``include_pending=True`` both pending and picked unboxed items are
    included, using ``COALESCE(qty_picked, qty_required, 1)`` for qty — used
    by the pre-plan flow on the route-list screen (plan boxes before picking
    starts).

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

    status_filter = "bpq.status IN ('picked', 'pending')" if include_pending else "bpq.status = 'picked'"

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
    """Return a volume/box estimate for a route BEFORE (or during) picking.

    Reads ALL cooler queue rows (pending + picked + exception) so managers
    can see recommended box sizes before picking starts.  Called by the
    route_list view.
    """
    _EMPTY = {
        "total_items": 0, "total_volume_l": 0,
        "missing_dimension_items": [], "stops": [],
        "box_plan": [], "recommended_box_type": None,
        "all_dims_present": True,
    }

    try:
        route_id = int(route_id)
    except (TypeError, ValueError):
        return _EMPTY
    delivery_date = str(delivery_date)

    rows = db.session.execute(
        text(
            "SELECT bpq.item_code, "
            "       COALESCE(bpq.qty_picked, bpq.qty_required, 1) AS qty, "
            "       COALESCE(rs.seq_no, 0) AS stop_no, "
            "       i.customer_name "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "LEFT JOIN route_stop_invoice rsi "
            "       ON rsi.invoice_no = bpq.invoice_no AND rsi.is_active = TRUE "
            "LEFT JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND i.route_id = :r "
            "  AND s.delivery_date = :d "
            "ORDER BY stop_no"
        ),
        {"r": route_id, "d": delivery_date},
    ).fetchall()

    if not rows:
        return _EMPTY

    item_codes = list({r[0] for r in rows})
    dim_map = {}
    if item_codes:
        try:
            dim_rows = db.session.execute(
                text(
                    "SELECT item_code_365, item_length, item_width, item_height "
                    "FROM ps_items_dw "
                    "WHERE item_code_365 = ANY(:codes)"
                ),
                {"codes": item_codes},
            ).fetchall()
            for dr in dim_rows:
                dim_map[dr[0]] = (_num(dr[1]) or 0.0, _num(dr[2]) or 0.0, _num(dr[3]) or 0.0)
        except Exception as exc:
            logger.warning("pre_pick_estimate: dimension lookup failed: %s", exc)

    box_type_rows = []
    try:
        box_type_rows = db.session.execute(
            text(
                "SELECT id, name, internal_volume_cm3, fill_efficiency "
                "FROM cooler_box_types WHERE is_active = true "
                "ORDER BY internal_volume_cm3"
            )
        ).fetchall()
    except Exception as exc:
        logger.warning("pre_pick_estimate: box types lookup failed: %s", exc)

    box_types = [
        {
            "id": r[0], "name": r[1],
            "capacity_cm3": (_num(r[2]) or 0.0) * (_num(r[3]) or 1.0),
        }
        for r in box_type_rows
    ]
    if not box_types:
        return _EMPTY

    stop_map = defaultdict(list)
    missing = []
    for item_code, qty, stop_no, customer_name in rows:
        l, w, h = dim_map.get(item_code, (0.0, 0.0, 0.0))
        vol = l * w * h * float(qty or 1)
        if l == 0 or w == 0 or h == 0:
            if not any(m["item_code"] == item_code for m in missing):
                missing.append({"item_code": item_code})
            vol = 0.0
        stop_map[int(stop_no or 0)].append({
            "item_code": item_code,
            "quantity": qty,
            "customer_name": customer_name,
            "volume_cm3": vol,
        })

    sorted_stops_lifo = sorted(stop_map.keys(), reverse=True)

    stop_summary = []
    for sn in sorted(stop_map.keys()):
        items = stop_map[sn]
        vol_l = round(sum(i["volume_cm3"] for i in items) / 1000, 2)
        missing_count = sum(
            1 for i in items if dim_map.get(i["item_code"], (0, 0, 0))[0] == 0
        )
        stop_summary.append({
            "stop_no": sn,
            "customer_names": list({i["customer_name"] for i in items if i["customer_name"]}),
            "item_count": sum(int(i["quantity"] or 1) for i in items),
            "volume_l": vol_l,
            "missing_dims": missing_count,
        })

    largest_box = box_types[-1]
    box_plan_raw = []
    current_stops, current_vol, current_missing = [], 0.0, 0

    for stop_no in sorted_stops_lifo:
        items = stop_map[stop_no]
        stop_vol = sum(i["volume_cm3"] for i in items)
        stop_miss = sum(
            1 for i in items if dim_map.get(i["item_code"], (0, 0, 0))[0] == 0
        )
        if current_stops and (current_vol + stop_vol) > largest_box["capacity_cm3"]:
            box_plan_raw.append((current_stops[:], current_vol, current_missing))
            current_stops, current_vol, current_missing = [], 0.0, 0
        current_stops.append(stop_no)
        current_vol += stop_vol
        current_missing += stop_miss

    if current_stops:
        box_plan_raw.append((current_stops, current_vol, current_missing))

    rendered_boxes = []
    for stop_list, vol_cm3, miss in box_plan_raw:
        fitting = next((b for b in box_types if b["capacity_cm3"] >= vol_cm3), None)
        chosen = fitting or box_types[-1]
        over = vol_cm3 > chosen["capacity_cm3"]
        fill_pct = round(vol_cm3 / chosen["capacity_cm3"] * 100) if chosen["capacity_cm3"] else 0
        stops_sorted = sorted(stop_list)
        stops_display = (
            f"Stop {stops_sorted[0]}" if len(stops_sorted) == 1
            else f"Stops {stops_sorted[-1]}\u2192{stops_sorted[0]}"
        )
        rendered_boxes.append({
            "box_type_name": chosen["name"],
            "stops_display": stops_display,
            "estimated_volume_l": round(vol_cm3 / 1000, 2),
            "estimated_fill_pct": fill_pct,
            "over_capacity": over,
            "missing_dims": miss,
        })

    total_vol = sum(
        sum(i["volume_cm3"] for i in items) for items in stop_map.values()
    )
    max_stop_vol = max(
        sum(i["volume_cm3"] for i in items) for items in stop_map.values()
    ) if stop_map else 0
    rec = next(
        (b for b in box_types if b["capacity_cm3"] >= max_stop_vol),
        box_types[-1],
    )

    return {
        "total_items": sum(int(r[1] or 1) for r in rows),
        "total_volume_l": round(total_vol / 1000, 2),
        "missing_dimension_items": missing,
        "all_dims_present": len(missing) == 0,
        "stops": stop_summary,
        "box_plan": rendered_boxes,
        "recommended_box_type": rec["name"],
    }
