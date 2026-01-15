items_for_picking(items)

    item_codes = [it.item_code for it in items if it.item_code]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items if getattr(d, "active", True)}

    # Build stops in picking order
    from collections import OrderedDict
    seen_stops = OrderedDict()
    for it in items:
        c2, b2, l2, p2 = parse_location(it.location, params)
        corridor = c2 if c2 is not None else _safe_int(it.corridor, 0)
        bay = b2 if b2 is not None else 0
        level = l2 or "A"
        pos = p2 if p2 is not None else 0
        zone = (it.zone or "MAIN").strip().upper()
        stop_key = (zone, corridor, bay, level, pos)
        if stop_key not in seen_stops:
            seen_stops[stop_key] = Stop(zone=zone, corridor=corridor, bay=bay, level=level, pos=pos)
    
    ordered = list(seen_stops.values())
    travel_res = estimate_travel_seconds(ordered, params)
    travel_s = travel_res.get("total", 0.0)

    # Use authoritative stop travel mapping for allocation
    location_walk_map = {stop_data["key"]: stop_data["seconds"] for stop_data in travel_res.get("stops", [])}

    # Pick sum and per-line
    pick_s_total = 0.0
    per_line_seconds = {}
    
    # Track seen locations for travel allocation
    location_seen = set()

    for it in items:
        dw = dw_map.get(it.item_code)
        s = estimate_pick_seconds_for_line(it, dw, params, summer_mode)
        
        # Allocate travel time to the first line at each location
        c2, b2, l2, p2 = parse_location(it.location, params)
        corridor = c2 if c2 is not None else _safe_int(it.corridor, 0)
        bay = b2 if b2 is not None else 0
        level = l2 or "A"
        pos = p2 if p2 is not None else 0
        zone = (it.zone or "MAIN").strip().upper()
        loc_key = (zone, corridor, bay, level, pos)
        
        walk_s = 0.0
        if loc_key in location_walk_map and loc_key not in location_seen:
            walk_s = location_walk_map[loc_key]
            location_seen.add(loc_key)
        
        # Use stable database ID as key (or fallback tuple for unsaved items)
        line_key = getattr(it, "id", None) or (it.invoice_no, it.item_code, it.location, float(it.qty or 0))
        per_line_seconds[line_key] = float(s) + float(walk_s)
        pick_s_total += float(s)

    # Packing
    pack_s = estimate_pack_seconds(items, dw_map, params, summer_mode)

    # Overhead
    ov = params.get("overhead", {})
    overhead_s = float(ov.get("start_seconds", DEFAULT_PARAMS["overhead"]["start_seconds"])) + float(ov.get("end_seconds", DEFAULT_PARAMS["overhead"]["end_seconds"]))

    total_s = overhead_s + travel_s + pick_s_total + pack_s
    total_min = total_s / 60.0

    return {
        "invoice_no": invoice_no,
        "total_seconds": float(total_s),
        "total_minutes": float(total_min),
        "breakdown": {
            "overhead_seconds": float(overhead_s),
            "travel_seconds": float(travel_s),
            "travel_details": travel_res,
            "pick_seconds": float(pick_s_total),
            "pack_seconds": float(pack_s)
        },
        "per_line_seconds": per_line_seconds,
        "summer_mode": bool(summer_mode),
        "params_version": params.get("version", "v1")
    }


def estimate_and_persist_invoice_time(invoice_no: str, commit: bool = True) -> Dict:
    result = estimate_invoice_time(invoice_no)

    inv = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if inv:
        inv.total_exp_time = float(result["total_minutes"])

    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    for it in items:
        # Use stable database ID as key (matching estimate_invoice_time)
        line_key = getattr(it, "id", None) or (it.invoice_no, it.item_code, it.location, float(it.qty or 0))
        s = result["per_line_seconds"].get(line_key, 0.0)
        it.exp_time = float(s) / 60.0

    if commit:
        db.session.commit()

    return result


def estimate_and_snapshot_invoice(invoice_no: str, reason: str = "manual", commit: bool = True) -> Dict:
    """
    Run the estimator and create an audit snapshot in oi_estimate_runs/oi_estimate_lines.
    This is the authoritative estimation function that should be called after PS365 import
    or when recalculating estimates.
    """
    import json
    from models import OiEstimateRun, OiEstimateLine
    
    params = get_time_params()
    params_revision = get_params_revision()
    summer_mode = get_summer_mode()

    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        raise ValueError(f"Invoice not found: {invoice_no}")

    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    if not items:
        return {"invoice_no": invoice_no, "total_minutes": 0.0, "run_id": None}

    from sorting_utils import sort_items_for_picking
    items = sort_items_for_picking(items)

    item_codes = [it.item_code for it in items if it.item_code]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items if getattr(d, "active", True)}

    # Build stops and compute travel with line-level allocation
    from collections import OrderedDict
    seen_stops = OrderedDict()
    for it in items:
        c2, b2, l2, p2 = parse_location(it.location, params)
        corridor = c2 if c2 is not None else _safe_int(getattr(it, 'corridor', 0), 0)
        bay = b2 if b2 is not None else 0
        level = l2 or "A"
        pos = p2 if p2 is not None else 0
        zone = (it.zone or "MAIN").strip().upper()
        stop_key = (zone, corridor, bay, level, pos)
        if stop_key not in seen_stops:
            seen_stops[stop_key] = Stop(zone=zone, corridor=corridor, bay=bay, level=level, pos=pos)
    
    ordered = list(seen_stops.values())
    travel_res = estimate_travel_seconds(ordered, params)
    travel_s = travel_res.get("total", 0.0)

    # Use authoritative stop travel mapping for allocation
    location_walk_map = {stop_data["key"]: stop_data["seconds"] for stop_data in travel_res.get("stops", [])}

    # Pick sum and per-line with walk allocation
    pick_s_total = 0.0
    per_line_data = []
    location_seen = set()
    
    for it in items:
        dw = dw_map.get(it.item_code)
        pick_s = estimate_pick_seconds_for_line(it, dw, params, summer_mode)
        pick_s_total += float(pick_s)
        
        # Determine walk seconds for this line
        c2, b2, l2, p2 = parse_location(it.location, params)
        corridor = c2 if c2 is not None else _safe_int(getattr(it, 'corridor', 0), 0)
        bay = b2 if b2 is not None else 0
        level = l2 or "A"
        pos = p2 if p2 is not None else 0
        zone = (it.zone or "MAIN").strip().upper()
        loc_key = (zone, corridor, bay, level, pos)
        
        if loc_key not in location_seen:
            walk_s = location_walk_map.get(loc_key, 0.0)
            location_seen.add(loc_key)
        else:
            walk_s = 0.0
        
        norm_unit = normalize_unit_type(it.unit_type)
        per_line_data.append({
            "item": it,
            "item_code": it.item_code,
            "location": it.location,
            "unit_type_normalized": norm_unit,
            "qty": float(it.qty) if it.qty else 0.0,
            "pick_seconds": float(pick_s),
            "walk_seconds": float(walk_s),
            "total_seconds": float(pick_s) + float(walk_s)
        })

    # Packing
    pack_s = estimate_pack_seconds(items, dw_map, params, summer_mode)

    # Overhead
    ov = params.get("overhead", {})
    overhead_s = float(ov.get("start_seconds", DEFAULT_PARAMS["overhead"]["start_seconds"])) + \
                 float(ov.get("end_seconds", DEFAULT_PARAMS["overhead"]["end_seconds"]))

    total_s = overhead_s + travel_s + pick_s_total + pack_s
    total_min = total_s / 60.0

    # Update invoice and items
    invoice.total_exp_time = float(total_min)
    for line_data in per_line_data:
        it = line_data["item"]
        it.exp_time = float(line_data["total_seconds"]) / 60.0

    # Create audit run
    breakdown = {
        "overhead_seconds": float(overhead_s),
        "travel_seconds": float(travel_s),
        "travel_details": travel_res,
        "pick_seconds": float(pick_s_total),
        "pack_seconds": float(pack_s)
    }
    
    run = OiEstimateRun()
    run.invoice_no=invoice_no
    run.estimator_version=ESTIMATOR_VERSION
    run.params_revision=params_revision
    run.params_snapshot_json=json.dumps(params)
    run.estimated_total_seconds=float(total_s)
    run.estimated_pick_seconds=float(pick_s_total)
    run.estimated_travel_seconds=float(travel_s)
    run.breakdown_json=json.dumps(breakdown)
    run.reason=reason
    
    db.session.add(run)
    db.session.flush()

    # Create audit lines
    for line_data in per_line_data:
        it = line_data["item"]
        line = OiEstimateLine()
        line.run_id=run.id
        line.invoice_no=invoice_no
        # Ensure we use 'id' attribute correctly if it exists, otherwise use None
        line.invoice_item_id=getattr(it, 'id', None)
        line.item_code=line_data["item_code"]
        line.location=line_data["location"]
        line.unit_type_normalized=line_data["unit_type_normalized"]
        line.qty=line_data["qty"]
        line.estimated_pick_seconds=line_data["pick_seconds"]
        line.estimated_walk_seconds=line_data["walk_seconds"]
        line.estimated_total_seconds=line_data["total_seconds"]
        line.breakdown_json=None
        
        db.session.add(line)

    if commit:
        db.session.commit()

    return {
        "invoice_no": invoice_no,
        "total_seconds": float(total_s),
        "total_minutes": float(total_min),
        "run_id": run.id,
        "breakdown": breakdown,
        "per_line_count": len(per_line_data),
        "summer_mode": bool(summer_mode),
        "params_revision": params_revision
    }
