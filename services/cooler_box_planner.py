"""Cooler box plan generator.

Groups picked, unboxed cooler queue rows into physical boxes based on
capacity.  Stops are processed last-stop-first (highest seq_no first) so
that the first loaded box holds the last stops — matching truck loading
order.

Returns a list of box dicts, each with:
  box_no, box_type_id, box_type_name, stop_min, stop_max, stop_display,
  stops, queue_item_ids, item_summaries, estimated_fill_cm3,
  estimated_fill_pct, estimated_weight_kg, missing_dimension_count,
  warnings
"""
import logging
from collections import defaultdict

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


def _num(value):
    try:
        v = float(value)
        return v if v == v else None  # reject NaN
    except (TypeError, ValueError):
        return None


def generate_box_plan(route_id, delivery_date, box_type_id=None):
    """Generate a box plan for all picked-but-unboxed cooler items on a route.

    Returns [] when there are no eligible items or no active box types.
    """
    route_id = int(route_id)
    delivery_date = str(delivery_date)

    # ── Resolve box type ────────────────────────────────────────────────────
    box_type_row = None
    if box_type_id:
        try:
            box_type_row = db.session.execute(
                text(
                    "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                    "FROM cooler_box_types WHERE id = :id AND is_active = true"
                ),
                {"id": int(box_type_id)},
            ).fetchone()
        except Exception:
            pass

    if box_type_row is None:
        try:
            box_type_row = db.session.execute(
                text(
                    "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                    "FROM cooler_box_types WHERE is_active = true "
                    "ORDER BY (internal_volume_cm3 * COALESCE(fill_efficiency, 1)) DESC, "
                    "         sort_order, name "
                    "LIMIT 1"
                )
            ).fetchone()
        except Exception:
            pass

    if box_type_row is None:
        logger.warning("generate_box_plan: no active box type found")
        return []

    box_type = {
        "id": box_type_row[0],
        "name": box_type_row[1],
        "internal_volume_cm3": _num(box_type_row[2]) or 0.0,
        "fill_efficiency": _num(box_type_row[3]) or 1.0,
        "max_weight_kg": _num(box_type_row[4]) or 0.0,
    }
    usable_capacity = box_type["internal_volume_cm3"] * box_type["fill_efficiency"]
    max_weight = box_type["max_weight_kg"] if box_type["max_weight_kg"] > 0 else None

    # ── Fetch picked-but-unboxed cooler queue rows ───────────────────────────
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
            "ORDER BY COALESCE(rs.seq_no, 0) DESC, bpq.invoice_no, bpq.item_code"
        ),
        {"rid": route_id, "dd": delivery_date, "truthy": True},
    ).fetchall()

    if not rows:
        return []

    # ── Guard: reject plan if any picked item has no delivery sequence ───
    # Items without a delivery_sequence cannot be placed into a stop-ordered
    # box plan.  The user must run "Prepare Cooler Route" (lock_sequencing)
    # first so every row gets a seq_no stamped from route_stop.
    missing_seq = [r for r in rows if r[7] is None]
    if missing_seq:
        invoice_samples = list({r[1] for r in missing_seq})[:5]
        return {
            "ok": False,
            "plan": [],
            "message": (
                f"{len(missing_seq)} picked item(s) have no delivery sequence "
                f"(e.g. invoice(s): {', '.join(invoice_samples)}). "
                "Please run Refresh Route Order / Prepare Cooler Route first, "
                "then generate the box plan again."
            ),
        }

    # ── Bulk-fetch item dimensions to avoid N+1 queries ────────────────────
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

    # ── Group rows by stop sequence ─────────────────────────────────────────
    by_stop = defaultdict(list)
    for r in rows:
        stop_seq = _num(r[7])
        by_stop[stop_seq].append(r)

    stops = sorted(by_stop.keys(), key=lambda x: x if x is not None else -1, reverse=True)

    plan = []
    box_no = 1
    current = None

    def flush_box():
        nonlocal current, box_no
        if not current:
            return
        current["estimated_fill_pct"] = (
            round((current["estimated_fill_cm3"] / usable_capacity) * 100, 1)
            if usable_capacity else 0
        )
        if usable_capacity and current["estimated_fill_cm3"] > usable_capacity:
            current["warnings"].append(
                f"This box exceeds capacity "
                f"({current['estimated_fill_cm3']:.0f} cm³ > {usable_capacity:.0f} cm³ usable)."
            )
        if max_weight and current["estimated_weight_kg"] > max_weight:
            current["warnings"].append(
                f"This box exceeds weight limit "
                f"({current['estimated_weight_kg']:.1f} kg > {max_weight:.1f} kg)."
            )
        plan.append(current)
        box_no += 1
        current = None

    def _stop_int(s):
        return int(s) if s is not None else 0

    for stop_seq in stops:
        stop_rows = by_stop[stop_seq]
        stop_volume = 0.0
        stop_weight = 0.0
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

            est_vol = 0.0
            est_wt = 0.0
            if length is None or width is None or height is None:
                missing_count += 1
            else:
                est_vol = length * width * height * qty
            if weight is not None:
                est_wt = weight * qty

            stop_volume += est_vol
            stop_weight += est_wt
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
            })

        # Try to fit this stop into the current box
        fits_volume = (usable_capacity <= 0 or
                       current is None or
                       current["estimated_fill_cm3"] + stop_volume <= usable_capacity)
        fits_weight = (max_weight is None or
                       current is None or
                       current["estimated_weight_kg"] + stop_weight <= max_weight)

        if current is not None and fits_volume and fits_weight:
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
        current = {
            "box_no": box_no,
            "box_type_id": box_type["id"],
            "box_type_name": box_type["name"],
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
            "warnings": [],
        }
        if missing_count:
            current["warnings"].append(
                "Some items are missing dimensions — fill estimate may be low."
            )

    flush_box()
    return plan
