from flask import Blueprint, render_template, abort
from flask_login import login_required
from app import db
from models import Invoice, InvoiceItem, DwItem, Setting
from services_oi_time_estimator import (
    estimate_invoice_time, get_time_params, parse_location, 
    order_stops_one_trip, build_stops, estimate_travel_breakdown_between
)
from datetime import datetime
import pytz

def current_athens_time():
    tz = pytz.timezone('Europe/Athens')
    return datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')

oi_reports_bp = Blueprint('oi_reports', __name__)

@oi_reports_bp.context_processor
def utility_processor():
    return dict(current_athens_time=current_athens_time)

@oi_reports_bp.route('/admin/oi/invoice/<invoice_no>/motion-study')
@login_required
def motion_study_report(invoice_no):
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        abort(404)
        
    try:
        analysis = estimate_invoice_time(invoice_no)
    except Exception as e:
        return f"Error calculating analysis: {str(e)}", 500

    params = get_time_params()
    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    
    # Re-calculate stops to show step-by-step travel
    stops = build_stops(items, params)
    ordered_stops = order_stops_one_trip(stops, params)
    
    # Build a list of actions for the "Motion Study"
    actions = []
    
    # 1. Prep
    actions.append({
        "step": "Start Prep",
        "description": "Initialize picking session and prepare equipment",
        "duration_sec": params.get("overhead", {}).get("start_seconds", 45),
        "type": "overhead"
    })
    
    # 2. Travel & Picks
    tr_params = params.get("travel", {})
    
    # Map items to stops for better display
    items_by_stop = {}
    for it in items:
        c, b, l, p = parse_location(it.location, params)
        key = (it.zone or "MAIN", c or 0, b or 0, l or "A", p or 0)
        if key not in items_by_stop:
            items_by_stop[key] = []
        items_by_stop[key].append(it)

    prev_stop = None
    for i, s in enumerate(ordered_stops):
        # Pick the items at this stop FIRST if they match the sequence logic
        # But usually we travel THEN pick. 
        # The user says "follow the picking sequence"
        
        # Travel to stop
        if i == 0:
            # First stop travel
            sec_align = float(tr_params.get("sec_align_per_stop", 2))
            has_upper = any(s.corridor_str in params.get("location", {}).get("upper_corridors", []) for s in ordered_stops)
            stairs_sec = float(tr_params.get("sec_stairs_up", 25)) if has_upper else 0.0
            
            actions.append({
                "step": f"Travel to First Location",
                "description": f"Go to Zone: {s.zone}, Corridor: {s.corridor_str}, Bay: {s.bay}",
                "duration_sec": sec_align + stairs_sec,
                "type": "travel",
                "travel_details": {
                    "align_seconds": sec_align,
                    "stairs_seconds": stairs_sec,
                    "total": sec_align + stairs_sec
                }
            })
        else:
            # Move between stops
            if prev_stop is not None:
                move_res = estimate_travel_breakdown_between(prev_stop, s, params)
                actions.append({
                    "step": f"Move to Next Location",
                    "description": f"Move from {prev_stop.corridor_str}-{prev_stop.bay} to {s.corridor_str}-{s.bay}",
                    "duration_sec": move_res["total"],
                    "type": "travel",
                    "travel_details": move_res
                })
            else:
                # Fallback for unexpected None prev_stop in non-first index
                actions.append({
                    "step": "Travel to Location",
                    "description": f"Go to Zone: {s.zone}, Corridor: {s.corridor_str}, Bay: {s.bay}",
                    "duration_sec": 0,
                    "type": "travel"
                })
            
        # Pick the items at this stop
        stop_key = (s.zone, s.corridor, s.bay, s.level, s.pos)
        stop_items = items_by_stop.get(stop_key, [])
        
        # IMPORTANT: To follow picking sequence precisely, we should sort items at the same stop
        # by their position/level if multiple items share a stop (though build_stops should have handled unique stops)
        for it in stop_items:
            line_sec = analysis['per_line_seconds'].get((it.item_code, it.invoice_no), 0)
            desc = getattr(it, 'item_name', 'Unknown Item')
            
            actions.append({
                "step": f"Pick {it.item_code}",
                "description": f"Grab {it.qty} {it.unit_type or 'item'}(s) - {desc[:40]}...",
                "duration_sec": line_sec,
                "type": "pick",
                "location": it.location
            })
        prev_stop = s

    # 3. Finalize
    actions.append({
        "step": "Packing & Labeling",
        "description": "Secure items and apply shipping labels",
        "duration_sec": analysis['breakdown']['pack_seconds'],
        "type": "pack"
    })
    
    actions.append({
        "step": "Complete Prep",
        "description": "Finalize documentation and move to dispatch area",
        "duration_sec": params.get("overhead", {}).get("end_seconds", 45),
        "type": "overhead"
    })

    return render_template('admin/oi/motion_study.html', 
                           invoice=invoice, 
                           analysis=analysis, 
                           actions=actions)
