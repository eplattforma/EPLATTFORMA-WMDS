"""Phase 6 (Phase 5 of brief) — Cooler box estimator.

Computes box-allocation suggestions for a route's SENSITIVE items
using a first-fit-decreasing (FFD) heuristic over the active
``cooler_box_types`` catalogue.

Public surface:
  * ``estimate_cooler_boxes(route_id)`` — main entry point.
  * ``items_missing_dimensions_report(limit=200)`` — feeds the admin
    report at ``/admin/cooler-items-missing-dimensions``.

The estimator is read-only — it never writes to the queue or to the
data-quality log. Mode determination:
  * ``rough``  — no cooler session yet, or sequencing not locked.
  * ``medium`` — sequencing locked, no boxes created yet.
  * ``good``   — at least one cooler box exists.
"""
import logging

from sqlalchemy import text

from app import db
from models import DwItem, Invoice, InvoiceItem

logger = logging.getLogger(__name__)


def _fetch_active_box_types():
    """Return the active box-type catalogue with computed effective
    capacity. Returns a list of dicts ordered by ``-effective_capacity``."""
    try:
        rows = db.session.execute(text(
            "SELECT id, name, internal_volume_cm3, fill_efficiency, "
            "       max_weight_kg, sort_order, "
            "       internal_length_cm, internal_width_cm, internal_height_cm "
            "FROM cooler_box_types "
            "WHERE is_active = :truthy "
            "ORDER BY sort_order, name"
        ), {"truthy": True}).fetchall()
    except Exception as e:
        logger.warning("box type lookup failed: %s", e)
        return []
    out = []
    for r in rows:
        vol = float(r[2] or 0)
        fe = float(r[3] or 0.75)
        out.append({
            "id": r[0], "name": r[1],
            "internal_volume_cm3": vol,
            "fill_efficiency": fe,
            "effective_capacity": vol * fe,
            "max_weight_kg": float(r[4]) if r[4] is not None else None,
            "sort_order": r[5] or 0,
            "internal_length_cm": float(r[6]) if r[6] is not None else 0.0,
            "internal_width_cm": float(r[7]) if r[7] is not None else 0.0,
            "internal_height_cm": float(r[8]) if r[8] is not None else 0.0,
        })
    return out


def _allocate_first_fit_decreasing(total_volume, box_types):
    """Greedy FFD over effective capacity. Always returns at least one
    box if total_volume > 0. Not optimal bin-packing — heuristic only.
    """
    if total_volume <= 0 or not box_types:
        return []
    sorted_types = sorted(box_types, key=lambda t: -t["effective_capacity"])
    remaining = total_volume
    allocation = []
    for bt in sorted_types:
        cap = bt["effective_capacity"]
        if cap <= 0 or remaining <= 0:
            continue
        n = int(remaining // cap)
        if n > 0:
            allocation.append({
                "box_type_id": bt["id"],
                "box_type_name": bt["name"],
                "count": n,
                "filled_cm3": n * cap,
            })
            remaining -= n * cap
    if remaining > 0:
        smallest_fitting = next(
            (bt for bt in reversed(sorted_types)
             if bt["effective_capacity"] >= remaining),
            sorted_types[0],
        )
        allocation.append({
            "box_type_id": smallest_fitting["id"],
            "box_type_name": smallest_fitting["name"],
            "count": 1,
            "filled_cm3": remaining,
        })
    return allocation


def _allocate_uniform(total_volume, box_types, name):
    """All-one-type allocation (e.g. 'all Large')."""
    if total_volume <= 0:
        return []
    target = next((bt for bt in box_types
                   if bt["name"].lower() == name.lower()), None)
    if target is None or target["effective_capacity"] <= 0:
        return []
    n = int(total_volume // target["effective_capacity"])
    remaining = total_volume - n * target["effective_capacity"]
    if remaining > 0:
        n += 1
    return [{
        "box_type_id": target["id"],
        "box_type_name": target["name"],
        "count": n,
        "filled_cm3": min(total_volume, n * target["effective_capacity"]),
    }] if n > 0 else []


def _data_quality_label(pct):
    if pct > 80:
        return "good"
    if pct >= 50:
        return "limited"
    return "insufficient"


def _determine_mode(route_id):
    """rough / medium / good based on session state + box presence."""
    try:
        boxes = db.session.execute(text(
            "SELECT COUNT(*) FROM cooler_boxes WHERE route_id = :rid"
        ), {"rid": route_id}).scalar() or 0
        if boxes > 0:
            return "good"
    except Exception:
        pass
    try:
        locked = db.session.execute(text(
            "SELECT sequence_locked_at FROM batch_picking_sessions "
            "WHERE name = :n LIMIT 1"
        ), {"n": f"COOLER-ROUTE-{route_id}"}).scalar()
        if locked is not None:
            return "medium"
    except Exception:
        pass
    return "rough"


def _sensitive_items_for_route(route_id):
    """Return list of (item_code, qty, length, width, height, weight)
    for all SENSITIVE items on the route's invoices. Pulls dimensions
    from DwItem and joins them with InvoiceItem rows on the route's
    invoices.
    """
    if route_id is None:
        return []
    inv_nos = [inv.invoice_no for inv in Invoice.query.filter_by(
        route_id=route_id
    ).all()]
    if not inv_nos:
        return []
    items = InvoiceItem.query.filter(
        InvoiceItem.invoice_no.in_(inv_nos)
    ).all()
    if not items:
        return []
    codes = {it.item_code for it in items}
    dw_rows = db.session.query(
        DwItem.item_code_365, DwItem.wms_zone, DwItem.item_length,
        DwItem.item_width, DwItem.item_height, DwItem.item_weight,
    ).filter(DwItem.item_code_365.in_(list(codes))).all()
    dw_map = {
        r[0]: {
            "wms_zone": (r[1] or "").upper(),
            "length": float(r[2]) if r[2] is not None else None,
            "width": float(r[3]) if r[3] is not None else None,
            "height": float(r[4]) if r[4] is not None else None,
            "weight": float(r[5]) if r[5] is not None else None,
        }
        for r in dw_rows
    }
    out = []
    for it in items:
        dw = dw_map.get(it.item_code, {})
        if dw.get("wms_zone") != "SENSITIVE":
            continue
        out.append({
            "item_code": it.item_code,
            "qty": float(it.qty or 0),
            "length": dw.get("length"),
            "width": dw.get("width"),
            "height": dw.get("height"),
            "weight": dw.get("weight"),
        })
    return out


def estimate_cooler_boxes(route_id):
    """Estimate cooler-box requirements for a route at any stage.

    Returns a dict with keys: ``mode``, ``total_volume_cm3``,
    ``total_weight_kg``, ``item_count``, ``items_with_dims``,
    ``items_missing_dims``, ``data_quality_pct``,
    ``data_quality_label``, ``box_estimates``, ``caveats``.
    """
    box_types = _fetch_active_box_types()
    items = _sensitive_items_for_route(route_id)

    item_count = len(items)
    items_with_dims = sum(
        1 for it in items
        if it["length"] is not None
        and it["width"] is not None
        and it["height"] is not None
    )
    items_missing_dims = item_count - items_with_dims

    total_volume = 0.0
    total_weight = 0.0
    weight_complete = True
    for it in items:
        if (it["length"] is not None and it["width"] is not None
                and it["height"] is not None):
            total_volume += (
                it["length"] * it["width"] * it["height"] * it["qty"]
            )
        if it["weight"] is None:
            weight_complete = False
        else:
            total_weight += it["weight"] * it["qty"]

    pct = (items_with_dims / item_count * 100.0) if item_count else 0.0
    label = _data_quality_label(pct)

    caveats = []
    if items_missing_dims > 0:
        caveats.append(
            f"{items_missing_dims} item(s) missing dimensions — "
            f"estimate excludes them"
        )
    if not weight_complete and item_count > 0:
        caveats.append("No complete weight data — weight check disabled")
    # Caveat: any item that does not fit any active box type on every
    # axis (allowing rotation by sorting axes). Falls back to the old
    # >200cm sanity check if there are no active boxes to compare to.
    def _fits_any_box(it):
        if not box_types:
            return None  # cannot decide
        idims = sorted([
            (it.get("length") or 0),
            (it.get("width") or 0),
            (it.get("height") or 0),
        ], reverse=True)
        for bt in box_types:
            bdims = sorted([
                float(bt.get("internal_length_cm") or 0),
                float(bt.get("internal_width_cm") or 0),
                float(bt.get("internal_height_cm") or 0),
            ], reverse=True)
            if all(idims[i] <= bdims[i] for i in range(3)):
                return True
        return False

    for it in items:
        if it.get("length") is None or it.get("width") is None or it.get("height") is None:
            continue
        fit = _fits_any_box(it)
        if fit is False:
            caveats.append(
                f"Item {it['item_code']} exceeds every active box "
                f"type's internal dimensions — verify"
            )
            break
        if fit is None and any((d or 0) > 200 for d in
                               (it["length"], it["width"], it["height"])):
            caveats.append(
                f"Item {it['item_code']} has unrealistically large "
                f"dimension — verify"
            )
            break

    primary = _allocate_first_fit_decreasing(total_volume, box_types)
    alt_large = _allocate_uniform(total_volume, box_types, "Large")
    alt_medium = _allocate_uniform(total_volume, box_types, "Medium")

    box_estimates = []
    if primary:
        box_estimates.append({
            "label": "Optimal mix (recommended)",
            "allocation": primary,
        })
    if alt_large and alt_large != primary:
        box_estimates.append({
            "label": "All-Large (fewest boxes)",
            "allocation": alt_large,
        })
    if alt_medium and alt_medium != primary:
        box_estimates.append({
            "label": "All-Medium (more granular)",
            "allocation": alt_medium,
        })

    return {
        "mode": _determine_mode(route_id),
        "total_volume_cm3": round(total_volume, 2),
        "total_volume_l": round(total_volume / 1000.0, 2),
        "total_weight_kg": round(total_weight, 3) if weight_complete else None,
        "item_count": item_count,
        "items_with_dims": items_with_dims,
        "items_missing_dims": items_missing_dims,
        "data_quality_pct": round(pct, 1),
        "data_quality_label": label,
        "box_estimates": box_estimates,
        "caveats": caveats,
    }


def items_missing_dimensions_report(limit=200):
    """Report most-frequently-routed SENSITIVE items missing dimensions.

    Joins ``cooler_data_quality_log`` (issue_type='missing_dimensions')
    onto ``DwItem`` for the friendly name. Falls back to a direct
    ``DwItem`` scan when the log is empty (cold start).
    """
    try:
        rows = db.session.execute(text(
            "SELECT cdql.item_code, COUNT(*) AS occurrences, "
            "       MAX(cdql.created_at) AS last_seen "
            "FROM cooler_data_quality_log cdql "
            "WHERE cdql.issue_type = 'missing_dimensions' "
            "GROUP BY cdql.item_code "
            "ORDER BY occurrences DESC, last_seen DESC "
            f"LIMIT {int(limit)}"
        )).fetchall()
    except Exception as e:
        logger.warning("items_missing_dimensions_report query failed: %s", e)
        rows = []

    if not rows:
        # Cold-start fallback: scan DwItem directly for SENSITIVE rows
        # missing any dimension.
        try:
            dw_rows = db.session.query(
                DwItem.item_code_365, DwItem.item_name,
            ).filter(
                DwItem.wms_zone == "SENSITIVE",
                ((DwItem.item_length.is_(None))
                 | (DwItem.item_width.is_(None))
                 | (DwItem.item_height.is_(None))),
            ).limit(limit).all()
            return [
                {"item_code": r[0], "item_name": r[1] or "",
                 "occurrences": 0, "last_seen": None}
                for r in dw_rows
            ]
        except Exception as e:
            logger.warning("DwItem scan fallback failed: %s", e)
            return []

    codes = [r[0] for r in rows]
    name_map = {}
    if codes:
        try:
            for r in db.session.query(
                DwItem.item_code_365, DwItem.item_name,
            ).filter(DwItem.item_code_365.in_(codes)).all():
                name_map[r[0]] = r[1] or ""
        except Exception:
            pass

    return [
        {"item_code": r[0], "item_name": name_map.get(r[0], ""),
         "occurrences": int(r[1]), "last_seen": r[2]}
        for r in rows
    ]
