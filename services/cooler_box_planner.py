import logging
from collections import defaultdict

from sqlalchemy import text

from app import db
from models import DwItem

logger = logging.getLogger(__name__)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def generate_box_plan(route_id, delivery_date, box_type_id=None):
    route_id = int(route_id)
    delivery_date = str(delivery_date)

    box_type_row = None
    if box_type_id:
        box_type_row = db.session.execute(
            text(
                "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                "FROM cooler_box_types WHERE id = :id AND is_active = true"
            ),
            {"id": int(box_type_id)},
        ).fetchone()
    if box_type_row is None:
        box_type_row = db.session.execute(
            text(
                "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                "FROM cooler_box_types WHERE is_active = true "
                "ORDER BY (internal_volume_cm3 * COALESCE(fill_efficiency, 1)) DESC, sort_order, name "
                "LIMIT 1"
            )
        ).fetchone()
    if box_type_row is None:
        return []

    box_type = {
        "id": box_type_row[0],
        "name": box_type_row[1],
        "internal_volume_cm3": float(box_type_row[2] or 0),
        "fill_efficiency": float(box_type_row[3] or 1),
        "max_weight_kg": float(box_type_row[4] or 0),
    }
    usable_capacity = box_type["internal_volume_cm3"] * box_type["fill_efficiency"]
    max_weight = box_type["max_weight_kg"] if box_type["max_weight_kg"] > 0 else None

    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_picked, "
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

    by_stop = defaultdict(list)
    for r in rows:
        stop_seq = _num(r[7])
        by_stop[stop_seq].append(r)

    stops = sorted(by_stop.keys(), reverse=True)
    plan = []
    box_no = 1
    current = None

    def flush_box():
        nonlocal current, box_no
        if not current:
            return
        current["estimated_fill_pct"] = round(
            (current["estimated_fill_cm3"] / usable_capacity) * 100, 1
        ) if usable_capacity else 0
        plan.append(current)
        box_no += 1
        current = None

    for stop_seq in stops:
        stop_rows = by_stop[stop_seq]
        stop_volume = 0
        stop_weight = 0
        missing_dimension_count = 0
        item_summaries = []
        queue_item_ids = []
        stop_numbers = []
        for r in stop_rows:
            qty = float(r[3] or 0)
            dw_item = DwItem.query.filter_by(item_code_365=r[2]).first()
            length = _num(getattr(dw_item, "item_length", None)) if dw_item else None
            width = _num(getattr(dw_item, "item_width", None)) if dw_item else None
            height = _num(getattr(dw_item, "item_height", None)) if dw_item else None
            weight = _num(getattr(dw_item, "item_weight", None)) if dw_item else None
            estimated_volume = 0
            estimated_weight = 0
            if length is None or width is None or height is None:
                missing_dimension_count += 1
            else:
                estimated_volume = length * width * height * qty
            if weight is not None:
                estimated_weight = weight * qty
            stop_volume += estimated_volume
            stop_weight += estimated_weight
            queue_item_ids.append(int(r[0]))
            stop_numbers.append(int(stop_seq) if stop_seq is not None else 0)
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
                "estimated_volume_cm3": estimated_volume,
                "estimated_weight_kg": estimated_weight,
            })
        if current and current["estimated_fill_cm3"] + stop_volume <= usable_capacity and (max_weight is None or current["estimated_weight_kg"] + stop_weight <= max_weight):
            current["stop_min"] = int(min(current["stops"] + [int(stop_seq) if stop_seq is not None else 0]))
            current["stop_max"] = int(max(current["stops"] + [int(stop_seq) if stop_seq is not None else 0]))
            current["stops"].append(int(stop_seq) if stop_seq is not None else 0)
            current["stops"] = sorted(set(current["stops"]), reverse=True)
            current["stop_display"] = f"Stops {current['stop_max']} → {current['stop_min']}"
            current["queue_item_ids"].extend(queue_item_ids)
            current["item_summaries"].extend(item_summaries)
            current["estimated_fill_cm3"] += stop_volume
            current["estimated_weight_kg"] += stop_weight
            current["missing_dimension_count"] += missing_dimension_count
            if missing_dimension_count and "Some items are missing dimensions. Box fill estimate may be incomplete." not in current["warnings"]:
                current["warnings"].append("Some items are missing dimensions. Box fill estimate may be incomplete.")
            continue
        flush_box()
        current = {
            "box_no": box_no,
            "box_type_id": box_type["id"],
            "box_type_name": box_type["name"],
            "stop_min": int(min(stop_numbers)) if stop_numbers else None,
            "stop_max": int(max(stop_numbers)) if stop_numbers else None,
            "stop_display": f"Stops {int(max(stop_numbers))} → {int(min(stop_numbers))}" if stop_numbers else "",
            "stops": sorted(set(stop_numbers), reverse=True),
            "queue_item_ids": queue_item_ids,
            "item_summaries": item_summaries,
            "estimated_fill_cm3": stop_volume,
            "estimated_fill_pct": 0,
            "estimated_weight_kg": stop_weight,
            "missing_dimension_count": missing_dimension_count,
            "warnings": [],
        }
        if missing_dimension_count:
            current["warnings"].append("Some items are missing dimensions. Box fill estimate may be incomplete.")
    flush_box()

    return plan