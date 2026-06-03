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

# Fill the first box to this fraction of its capacity before opening the next,
# so the load is spread more evenly between boxes.
FIRST_BOX_FILL_FACTOR = 0.75


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


def _pick_box_type(box_types, needed_volume, needed_weight, prefer_largest=True):
    """Select the box type for a new box.

    prefer_largest=True  (first box): always use the largest type so the
        biggest box is loaded first and filled to FIRST_BOX_FILL_FACTOR.
    prefer_largest=False (subsequent boxes): use the smallest type whose
        capacity is enough for the remaining stop's items.
    """
    if not box_types:
        return None
    if len(box_types) == 1:
        return box_types[0]
    if prefer_largest:
        return sorted(box_types, key=lambda t: t["usable_capacity"], reverse=True)[0]
    # Smallest-fitting for box 2+
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
            # For the first box apply a soft fill cap so some stops spill into
            # the second box, keeping both boxes at a similar fill level.
            # Subsequent boxes are filled to 100 % of their capacity.
            effective_usable = (usable * FIRST_BOX_FILL_FACTOR
                                if box_no == 1 and len(box_types) > 1
                                else usable)
            fits_vol = effective_usable <= 0 or new_vol <= effective_usable
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

        # Box 1 → largest type (fill until FIRST_BOX_FILL_FACTOR, then open next).
        # Box 2+ → smallest type that fits, to avoid wasting a large box.
        chosen = _pick_box_type(box_types, stop_volume, stop_weight,
                                prefer_largest=(box_no == 1))
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
