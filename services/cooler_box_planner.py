"""Cooler box plan generator — two-phase smart recommender.

Phase 1: Group stops LIFO (consecutive only) using the *largest* available
         box type as the capacity ceiling.  This establishes which stops
         travel together.

Phase 2: Right-size each group — pick the *smallest* box type whose usable
         volume satisfies  fill % >= target_fill_pct.  Falls back to the
         smallest type that physically fits when no type hits the target.

Stop order is LIFO (last delivery stop first) so Box 1 carries the last-stop
items — these go at the bottom/back of the truck and are loaded first.

By default (include_pending=True) the plan covers ALL unboxed items —
both pending and picked — so it can be confirmed BEFORE picking starts.
The picker then sees "Pick → Box #N" and places items directly in the
right box.  No post-pick sorting needed.
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
    """Return active box types ordered largest → smallest by usable capacity."""
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
            logger.warning("_load_box_types: box_type_id=%s not found", box_type_id)
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
            logger.warning("_load_box_types: failed: %s", exc)
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


def _smallest_fitting(box_types, vol, wt, available_type_counts=None, used_counts=None):
    """Return the smallest box type (by usable capacity) that physically holds
    vol/wt, respecting availability counts when provided.  Falls back to
    ignoring availability when nothing is left."""
    used = used_counts or {}

    def _avail(bt):
        if available_type_counts is None:
            return True
        limit = available_type_counts.get(bt["id"], 0)
        return used.get(bt["id"], 0) < limit

    def _fits(bt):
        vol_ok = bt["usable_capacity"] <= 0 or vol <= bt["usable_capacity"]
        wt_ok  = bt["max_weight_kg"]    <= 0 or wt  <= bt["max_weight_kg"]
        return vol_ok and wt_ok

    asc = sorted(box_types, key=lambda t: t["usable_capacity"])

    # Prefer available + fits
    for bt in asc:
        if _fits(bt) and _avail(bt):
            return bt
    # Over-allocate: fits + user gave any count (used up but allowed)
    for bt in asc:
        if _fits(bt) and (available_type_counts is None or available_type_counts.get(bt["id"], 0) > 0):
            return bt
    # Nothing available holds the volume → use the largest type the user has any of.
    # It will overflow; the caller/cascade handles splitting.
    for bt in reversed(asc):
        if available_type_counts is None or available_type_counts.get(bt["id"], 0) > 0:
            return bt
    # Absolute last resort: every type is set to 0 — just find something that fits
    for bt in asc:
        if _fits(bt):
            return bt
    return asc[-1]


def generate_box_plan(
    route_id,
    delivery_date,
    box_type_id=None,
    include_pending=True,
    available_type_counts=None,
    target_fill_pct=0.80,
):
    """Generate a two-phase cooler box plan.

    Parameters
    ----------
    route_id, delivery_date : identifiers
    box_type_id : int | None
        Force a single box type; None → auto select.
    include_pending : bool
        True  → plan pending + picked items (pre-pick planning).
        False → plan only picked items (legacy).
    available_type_counts : dict | None
        {type_id: max_count} — how many boxes of each type are available today.
        None means unlimited.
    target_fill_pct : float
        Phase 2 tries to find the smallest type where fill ≥ this value.
        Default 0.80 (80 %).
    """
    route_id     = int(route_id)
    delivery_date = str(delivery_date)

    box_types = _load_box_types(box_type_id)
    if not box_types:
        logger.warning("generate_box_plan: no active box type found")
        return []

    # ── Fetch unboxed queue rows ─────────────────────────────────────────────
    status_filter = (
        "bpq.status IN ('pending', 'picked')" if include_pending
        else "bpq.status = 'picked'"
    )

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
        samples = list({r[1] for r in missing_seq})[:5]
        return {
            "ok": False,
            "plan": [],
            "message": (
                f"{len(missing_seq)} item(s) have no delivery sequence "
                f"(e.g. invoice(s): {', '.join(samples)}). "
                "Please run Confirm Cooler Route first, "
                "then generate the box plan again."
            ),
        }

    # ── Dimension lookup ─────────────────────────────────────────────────────
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
                    "width":  _num(dr[2]),
                    "height": _num(dr[3]),
                    "weight": _num(dr[4]),
                }
        except Exception as exc:
            logger.warning("generate_box_plan: dimension lookup failed: %s", exc)

    # ── Group items by stop ──────────────────────────────────────────────────
    by_stop = defaultdict(list)
    for r in rows:
        by_stop[_num(r[7])].append(r)

    stops = sorted(
        by_stop.keys(),
        key=lambda x: x if x is not None else -1,
        reverse=(STOP_ORDER == "last_first"),
    )

    def _stop_int(s):
        return int(s) if s is not None else 0

    # Pre-compute per-stop volumes, weights, and item summaries
    stop_data = {}
    for seq in stops:
        vol = wt = missing = 0.0
        items = []
        for r in by_stop[seq]:
            qty   = _num(r[3]) or 1.0
            dims  = dim_map.get(r[2], {})
            l, w, h, weight = dims.get("length"), dims.get("width"), dims.get("height"), dims.get("weight")
            has_dims = l is not None and w is not None and h is not None
            est_vol  = l * w * h * qty if has_dims else 0.0
            est_wt   = weight * qty    if weight is not None else 0.0
            if not has_dims:
                missing += 1
            vol += est_vol
            wt  += est_wt
            items.append({
                "queue_item_id":      int(r[0]),
                "invoice_no":         r[1],
                "customer_code":      r[4],
                "customer_name":      r[5],
                "route_stop_id":      r[6],
                "delivery_sequence":  seq,
                "item_code":          r[2],
                "item_name":          r[8],
                "qty":                qty,
                "estimated_volume_cm3":  est_vol,
                "estimated_weight_kg":   est_wt,
                "has_dimensions":     has_dims,
                "queue_status":       r[9],
            })
        stop_data[seq] = {"vol": vol, "wt": wt, "missing": int(missing), "items": items}

    # ── Phase 1: group consecutive stops LIFO using largest box as ceiling ───
    largest_cap = max(bt["usable_capacity"] for bt in box_types) if box_types else 0
    largest_wt  = max(bt["max_weight_kg"]   for bt in box_types) if box_types else 0

    slots = []   # each slot: {seqs, vol, wt, missing, items}

    def _new_slot(seq):
        sd = stop_data[seq]
        return {
            "seqs":    [seq],
            "vol":     sd["vol"],
            "wt":      sd["wt"],
            "missing": sd["missing"],
            "items":   list(sd["items"]),
        }

    def _flush(slot):
        if slot:
            slots.append(slot)

    cur = None
    for seq in stops:
        sd  = stop_data[seq]
        svol = sd["vol"]
        swt  = sd["wt"]

        # Oversized stop: split across 2 sub-boxes immediately
        if largest_cap > 0 and svol > largest_cap:
            _flush(cur)
            cur = None
            # Split items into two halves by cumulative volume
            half = largest_cap * 0.95
            sub1_items, sub2_items = [], []
            sub1_vol = sub1_wt = sub2_vol = sub2_wt = 0.0
            sub1_miss = sub2_miss = 0
            for it in sd["items"]:
                if sub1_vol + it["estimated_volume_cm3"] <= half:
                    sub1_items.append(it)
                    sub1_vol  += it["estimated_volume_cm3"]
                    sub1_wt   += it["estimated_weight_kg"]
                    sub1_miss += 0 if it["has_dimensions"] else 1
                else:
                    sub2_items.append(it)
                    sub2_vol  += it["estimated_volume_cm3"]
                    sub2_wt   += it["estimated_weight_kg"]
                    sub2_miss += 0 if it["has_dimensions"] else 1
            if sub1_items:
                slots.append({"seqs": [seq], "vol": sub1_vol, "wt": sub1_wt,
                              "missing": sub1_miss, "items": sub1_items})
            if sub2_items:
                slots.append({"seqs": [seq], "vol": sub2_vol, "wt": sub2_wt,
                              "missing": sub2_miss, "items": sub2_items})
            continue

        # Try to add stop to current slot
        if cur is not None:
            new_vol = cur["vol"] + svol
            new_wt  = cur["wt"]  + swt
            vol_ok  = largest_cap <= 0 or new_vol <= largest_cap
            wt_ok   = largest_wt  <= 0 or new_wt  <= largest_wt
            if vol_ok and wt_ok:
                cur["seqs"].append(seq)
                cur["vol"]    = new_vol
                cur["wt"]     = new_wt
                cur["missing"] += sd["missing"]
                cur["items"].extend(sd["items"])
                continue

        _flush(cur)
        cur = _new_slot(seq)

    _flush(cur)

    # ── Phase 2: right-size each slot ────────────────────────────────────────
    used_counts: dict = defaultdict(int)
    plan = []

    def _pick_box_type(V, W):
        """Pick the best available box type for volume V and weight W."""
        max_cap_for_target = (V / target_fill_pct) if target_fill_pct > 0 else float("inf")

        def _avail(bt):
            if available_type_counts is None:
                return True
            return used_counts[bt["id"]] < available_type_counts.get(bt["id"], 0)

        asc = sorted(box_types, key=lambda t: t["usable_capacity"])

        # 1. Hits fill target + fits + available
        candidates = [
            bt for bt in asc
            if bt["usable_capacity"] >= V
            and bt["usable_capacity"] <= max_cap_for_target
            and (bt["max_weight_kg"] <= 0 or bt["max_weight_kg"] >= W)
            and _avail(bt)
        ]
        if candidates:
            return candidates[0]

        # 2. Physically fits + available (ignore fill target)
        fits_avail = [
            bt for bt in asc
            if bt["usable_capacity"] >= V
            and (bt["max_weight_kg"] <= 0 or bt["max_weight_kg"] >= W)
            and _avail(bt)
        ]
        if fits_avail:
            return fits_avail[0]

        # 3. Physically fits + user gave any count (over-allocate)
        fits_allowed = [
            bt for bt in asc
            if bt["usable_capacity"] >= V
            and (bt["max_weight_kg"] <= 0 or bt["max_weight_kg"] >= W)
            and (available_type_counts is None or available_type_counts.get(bt["id"], 0) > 0)
        ]
        if fits_allowed:
            return fits_allowed[0]

        # 4. Nothing available physically holds the volume.
        #    Use the LARGEST type the user gave any count to — the bin-packer
        #    below will split items across as many boxes of this type as needed.
        allowed_asc = [
            bt for bt in asc
            if available_type_counts is None or available_type_counts.get(bt["id"], 0) > 0
        ]
        if allowed_asc:
            return allowed_asc[-1]

        # 5. Absolute last resort — every type is set to 0
        fits_any = [
            bt for bt in asc
            if bt["usable_capacity"] >= V
            and (bt["max_weight_kg"] <= 0 or bt["max_weight_kg"] >= W)
        ]
        return fits_any[0] if fits_any else asc[-1]

    def _make_box_entry(items, chosen):
        """Build a plan dict for a list of items packed into one box of `chosen`."""
        bv = sum(it["estimated_volume_cm3"] or 0.0 for it in items)
        bw = sum(it["estimated_weight_kg"]  or 0.0 for it in items)
        usable   = chosen["usable_capacity"]
        fill_pct = round((bv / usable) * 100, 1) if usable else 0.0
        seqs     = [it["delivery_sequence"] for it in items]
        stop_ints = [_stop_int(s) for s in seqs]
        smin, smax = min(stop_ints), max(stop_ints)
        stop_display = (
            f"Stops {smax} → {smin}" if smin != smax else f"Stop {smax}"
        )
        missing = sum(0 if it.get("has_dimensions") else 1 for it in items)
        warnings = []
        if missing:
            warnings.append("Some items are missing dimensions — fill estimate may be low.")
        if usable and bv > usable:
            warnings.append(
                f"Box exceeds capacity ({bv:.0f} cm³ > {usable:.0f} cm³ usable)."
            )
        if chosen["max_weight_kg"] and bw > chosen["max_weight_kg"]:
            warnings.append(
                f"Box exceeds weight limit ({bw:.1f} kg > {chosen['max_weight_kg']:.1f} kg)."
            )
        if (
            available_type_counts is not None
            and available_type_counts.get(chosen["id"], 0) == 0
        ):
            warnings.append(
                f"No {chosen['name']} boxes were available — used as last resort. "
                "Increase availability or add a larger box type."
            )
        used_counts[chosen["id"]] += 1
        return {
            "box_no":               0,          # renumbered at the end
            "box_type_id":          chosen["id"],
            "box_type_name":        chosen["name"],
            "usable_capacity_cm3":  usable,
            "max_weight_kg":        chosen["max_weight_kg"],
            "stop_min":             smin,
            "stop_max":             smax,
            "stop_display":         stop_display,
            "stops":                sorted(set(stop_ints), reverse=True),
            "queue_item_ids":       [it["queue_item_id"] for it in items],
            "item_summaries":       items,
            "estimated_fill_cm3":   bv,
            "estimated_fill_pct":   fill_pct,
            "estimated_weight_kg":  bw,
            "missing_dimension_count": missing,
            "warnings":             warnings,
        }

    for slot in slots:
        V, W = slot["vol"], slot["wt"]
        chosen = _pick_box_type(V, W)
        usable = chosen["usable_capacity"]

        # ── Bin-pack: split slot across as many boxes as needed ──────────────
        # If the whole slot fits in one box, we still go through this loop —
        # it just produces a single bin.
        if usable <= 0:
            # No capacity info — put everything in one box
            plan.append(_make_box_entry(slot["items"], chosen))
            continue

        # bins stores (items_list, box_type) — each bin remembers its own type
        bins: list[tuple] = []
        cur_bin: list = []
        cur_vol = cur_wt = 0.0

        all_items = slot["items"]
        for idx, item in enumerate(all_items):
            iv = item["estimated_volume_cm3"] or 0.0
            iw = item["estimated_weight_kg"]  or 0.0
            wt_limit_ok = (
                chosen["max_weight_kg"] <= 0
                or cur_wt + iw <= chosen["max_weight_kg"]
            )
            # Start a new bin if adding this item would overflow — but only
            # if the current bin already has at least one item (a single item
            # larger than the box still goes into its own bin with a warning).
            if cur_bin and (cur_vol + iv > usable or not wt_limit_ok):
                bins.append((cur_bin, chosen))
                cur_bin = []
                cur_vol = cur_wt = 0.0
                # Re-pick for the remaining items to get the best available type
                remaining = all_items[idx:]
                rem_vol = sum(it["estimated_volume_cm3"] or 0.0 for it in remaining)
                rem_wt  = sum(it["estimated_weight_kg"]  or 0.0 for it in remaining)
                chosen = _pick_box_type(rem_vol, rem_wt)
                usable = chosen["usable_capacity"] or usable
            cur_bin.append(item)
            cur_vol += iv
            cur_wt  += iw

        if cur_bin:
            bins.append((cur_bin, chosen))

        for bin_items, bin_type in bins:
            plan.append(_make_box_entry(bin_items, bin_type))

    # Assign final box numbers
    for i, entry in enumerate(plan, start=1):
        entry["box_no"] = i

    return plan


def pre_pick_estimate(route_id, delivery_date):
    """Lightweight volume/weight estimate for the route-list screen."""
    route_id      = int(route_id)
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

    total_vol = total_wt = 0.0
    missing = 0
    for r in rows:
        qty  = _num(r[1]) or 1.0
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
    pct   = round((total - missing) / total * 100) if total else 0
    label = "good" if pct >= 80 else "limited" if pct >= 40 else "poor"

    return {
        "total_volume_l":          round(total_vol / 1000, 1),
        "total_weight_kg":         round(total_wt, 1),
        "item_count":              total,
        "missing_dimension_count": missing,
        "data_quality_pct":        pct,
        "data_quality_label":      label,
    }
