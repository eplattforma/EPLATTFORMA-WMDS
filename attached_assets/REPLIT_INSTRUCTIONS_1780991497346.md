# Route Planner — Replit Implementation Instructions

Please implement the following changes to add a standalone Route Planner feature to this Flask application. There are **3 new files to create** and **2 small edits to `main.py`**.

---

## CHANGE 1 — Create new file: `services/route_optimizer_planner.py`

Create this file at the path `services/route_optimizer_planner.py` with exactly the following content:

```python
"""
Route Optimizer Planner Service
================================
Standalone route optimization — does NOT touch any existing route/shipment records.
Used exclusively by the Route Planner planning screen (/route-planner/).

Algorithm: Nearest-Neighbor Heuristic with:
  - Time window constraints (before / after)
  - Fixed-position anchors (user-pinned stops that don't move)
  - Segment-based free-stop insertion between anchors

No external APIs required — uses straight-line (Haversine) distances and a
configurable average city speed to estimate travel times.
"""

from math import radians, cos, sin, asin, sqrt
import logging

logger = logging.getLogger(__name__)


# ── Distance ──────────────────────────────────────────────────────────────────

def haversine(lat1, lng1, lat2, lng2):
    """Straight-line distance in km between two GPS coordinates."""
    R = 6371.0
    lat1, lng1, lat2, lng2 = map(radians, [float(lat1), float(lng1), float(lat2), float(lng2)])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(max(0.0, min(1.0, a))))


# ── Time helpers ──────────────────────────────────────────────────────────────

def time_to_min(t):
    """'HH:MM' → minutes since midnight.  Returns None if falsy."""
    if not t:
        return None
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def min_to_time(m):
    """Minutes since midnight → 'HH:MM'."""
    m = int(round(m)) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


# ── Nearest-neighbour helpers ─────────────────────────────────────────────────

def _nearest_neighbor_sort(stops, from_lat, from_lng, to_lat=None, to_lng=None):
    """
    Greedy nearest-neighbour sort of *stops* starting from (from_lat, from_lng).
    If to_lat/to_lng are given the algorithm biases the last pick toward the
    destination to reduce the final approach distance.
    """
    if not stops:
        return []
    remaining = list(stops)
    result = []
    cur_lat, cur_lng = from_lat, from_lng

    while remaining:
        if to_lat is not None and len(remaining) == 1:
            best_i = 0
        else:
            def score(s):
                d_here = haversine(cur_lat, cur_lng, s["lat"], s["lng"])
                if to_lat is not None:
                    d_dest = haversine(s["lat"], s["lng"], to_lat, to_lng)
                    return d_here + 0.15 * d_dest
                return d_here

            best_i = min(range(len(remaining)), key=lambda i: score(remaining[i]))

        chosen = remaining.pop(best_i)
        result.append(chosen)
        cur_lat, cur_lng = chosen["lat"], chosen["lng"]

    return result


# ── Time-window arrival calculation ──────────────────────────────────────────

def _calc_arrival(cur_lat, cur_lng, stop, current_time, avg_speed, stop_duration):
    """
    Return enriched stop dict with travel_min, dist_km, arrival_time, wait_min.
    Respects 'after' time windows (driver waits if early).
    """
    dist = haversine(cur_lat, cur_lng, stop["lat"], stop["lng"])
    travel_min = (dist / avg_speed) * 60
    arrival = current_time + travel_min
    wait_min = 0

    if stop.get("tw_type") == "after":
        open_t = time_to_min(stop.get("tw_time"))
        if open_t is not None and arrival < open_t:
            wait_min = open_t - arrival
            arrival = open_t

    return {
        **stop,
        "dist_km": round(dist, 1),
        "travel_min": round(travel_min),
        "arrival_time": arrival,
        "wait_min": round(wait_min),
    }


# ── Main optimisation entry point ─────────────────────────────────────────────

def optimize_route(stops_input, start_time_str="08:00", avg_speed=30.0, stop_duration=5.0):
    """
    Optimise a list of customer stops and return the ordered result.

    Parameters
    ----------
    stops_input : list[dict]
        Each stop must include:
            customer_code   str
            company_name    str
            lat             float
            lng             float
            is_start        bool
            is_end          bool
            fixed_seq       int|None
            tw_type         'none' | 'before' | 'after'
            tw_time         'HH:MM' or ''

    Returns
    -------
    dict with keys: route, start_stop, end_stop, start_time, end_arrival,
                    total_dist, total_time, maps_url, warnings
    """
    avg_speed = max(float(avg_speed), 1.0)
    stop_duration = max(float(stop_duration), 0.0)
    start_min = time_to_min(start_time_str) or 8 * 60

    start_stop = next((s for s in stops_input if s.get("is_start")), None)
    end_stop = next(
        (s for s in stops_input if s.get("is_end") and not s.get("is_start")), None
    )

    middle = [
        s for s in stops_input
        if not s.get("is_start") and not s.get("is_end")
    ]

    fixed_stops = sorted(
        [s for s in middle if s.get("fixed_seq") is not None],
        key=lambda x: int(x["fixed_seq"]),
    )
    free_stops = [s for s in middle if s.get("fixed_seq") is None]

    anchors = []
    if start_stop:
        anchors.append(start_stop)
    anchors.extend(fixed_stops)
    if end_stop:
        anchors.append(end_stop)

    if len(anchors) >= 2:
        segments = [
            {"from": anchors[i], "to": anchors[i + 1], "stops": []}
            for i in range(len(anchors) - 1)
        ]

        for fs in free_stops:
            best_seg = 0
            best_cost = float("inf")
            for i, seg in enumerate(segments):
                d_in = haversine(seg["from"]["lat"], seg["from"]["lng"], fs["lat"], fs["lng"])
                d_out = haversine(fs["lat"], fs["lng"], seg["to"]["lat"], seg["to"]["lng"])
                d_direct = haversine(
                    seg["from"]["lat"], seg["from"]["lng"],
                    seg["to"]["lat"], seg["to"]["lng"],
                )
                cost = d_in + d_out - d_direct
                if cost < best_cost:
                    best_cost = cost
                    best_seg = i
            segments[best_seg]["stops"].append(fs)

    elif len(anchors) == 1:
        anchor = anchors[0]
        segments = [{"from": anchor, "to": None, "stops": free_stops}]
    else:
        if not free_stops:
            return {
                "route": [], "start_stop": None, "end_stop": None,
                "start_time": start_time_str, "end_arrival": None,
                "total_dist": 0, "total_time": 0, "maps_url": "", "warnings": [],
            }
        segments = [{"from": free_stops[0], "to": None, "stops": free_stops[1:]}]
        free_stops[0]["arrival_time"] = start_min
        free_stops[0]["dist_km"] = 0
        free_stops[0]["travel_min"] = 0
        free_stops[0]["wait_min"] = 0

    route = []
    cur_lat = start_stop["lat"] if start_stop else anchors[0]["lat"] if anchors else free_stops[0]["lat"]
    cur_lng = start_stop["lng"] if start_stop else anchors[0]["lng"] if anchors else free_stops[0]["lng"]
    current_time = start_min

    for seg_idx, seg in enumerate(segments):
        is_last_seg = seg_idx == len(segments) - 1
        to_stop = seg["to"]

        ordered_free = _nearest_neighbor_sort(
            seg["stops"],
            cur_lat, cur_lng,
            to_stop["lat"] if to_stop else None,
            to_stop["lng"] if to_stop else None,
        )

        for s in ordered_free:
            enriched = _calc_arrival(cur_lat, cur_lng, s, current_time, avg_speed, stop_duration)
            route.append(enriched)
            cur_lat, cur_lng = s["lat"], s["lng"]
            current_time = enriched["arrival_time"] + stop_duration

        if to_stop and not (is_last_seg and to_stop.get("is_end")):
            enriched = _calc_arrival(cur_lat, cur_lng, to_stop, current_time, avg_speed, stop_duration)
            enriched["is_fixed_anchor"] = True
            route.append(enriched)
            cur_lat, cur_lng = to_stop["lat"], to_stop["lng"]
            current_time = enriched["arrival_time"] + stop_duration

    end_arrival_min = None
    if end_stop:
        dist = haversine(cur_lat, cur_lng, end_stop["lat"], end_stop["lng"])
        travel_min = (dist / avg_speed) * 60
        end_arrival_min = current_time + travel_min
    elif route:
        end_arrival_min = route[-1]["arrival_time"] + stop_duration

    warnings = []
    for s in route:
        if s.get("tw_type") == "before":
            deadline = time_to_min(s.get("tw_time"))
            if deadline is not None and s["arrival_time"] > deadline + 1:
                over = round(s["arrival_time"] - deadline)
                warnings.append(
                    f"{s['company_name']}: arrives {min_to_time(s['arrival_time'])} "
                    f"but window closes {s['tw_time']} ({over} min late)"
                )

    waypoints = [
        f"{s['lat']},{s['lng']}"
        for s in route
        if not s.get("is_start") and not s.get("is_end")
    ]

    if start_stop:
        origin = f"{start_stop['lat']},{start_stop['lng']}"
    elif route:
        origin = f"{route[0]['lat']},{route[0]['lng']}"
    else:
        origin = ""

    dest_s = end_stop or (route[-1] if route else None)
    destination = f"{dest_s['lat']},{dest_s['lng']}" if dest_s else origin

    maps_url = ""
    if origin:
        maps_url = (
            f"https://www.google.com/maps/dir/?api=1"
            f"&origin={origin}&destination={destination}&travelmode=driving"
        )
        if waypoints[:23]:
            maps_url += f"&waypoints={'|'.join(waypoints[:23])}"

    if len(waypoints) > 23:
        warnings.append(
            f"Google Maps shows first 23 of {len(waypoints)} stops. "
            "Consider splitting into two routes."
        )

    total_dist = round(sum(s.get("dist_km", 0) for s in route), 1)
    total_time = round(end_arrival_min - start_min) if end_arrival_min else 0

    return {
        "route": route,
        "start_stop": start_stop,
        "end_stop": end_stop,
        "start_time": start_time_str,
        "end_arrival": min_to_time(end_arrival_min) if end_arrival_min else None,
        "total_dist": total_dist,
        "total_time": total_time,
        "maps_url": maps_url,
        "warnings": warnings,
    }
```

---

## CHANGE 2 — Create new file: `blueprints/route_planner.py`

Create this file at the path `blueprints/route_planner.py` with exactly the following content:

```python
"""
Route Planner Blueprint
=======================
Standalone planning tool — does NOT read or write to shipment/route records.
Accessible at /route-planner/
"""

from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from models import PSCustomer
from services.route_optimizer_planner import optimize_route
import logging

route_planner_bp = Blueprint("route_planner", __name__, url_prefix="/route-planner")
logger = logging.getLogger(__name__)


@route_planner_bp.route("/")
@login_required
def index():
    return render_template("route_planner.html")


@route_planner_bp.route("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "").strip()
    include_no_coords = request.args.get("include_no_coords", "false").lower() == "true"

    if len(q) < 2:
        return jsonify([])

    base = PSCustomer.query.filter(
        (PSCustomer.company_name.ilike(f"%{q}%")) |
        (PSCustomer.customer_code_365.ilike(f"%{q}%"))
    ).filter(PSCustomer.active == True)

    if not include_no_coords:
        base = base.filter(
            PSCustomer.latitude.isnot(None),
            PSCustomer.longitude.isnot(None),
        )

    customers = base.order_by(PSCustomer.company_name).limit(25).all()

    return jsonify([
        {
            "customer_code": c.customer_code_365,
            "company_name": c.company_name or c.customer_code_365,
            "address": " · ".join(filter(None, [c.address_line_1, c.town])),
            "lat": c.latitude,
            "lng": c.longitude,
            "has_coords": c.latitude is not None and c.longitude is not None,
        }
        for c in customers
    ])


@route_planner_bp.route("/api/optimize", methods=["POST"])
@login_required
def api_optimize():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    stops = data.get("stops", [])
    settings = data.get("settings", {})

    if len(stops) < 2:
        return jsonify({"error": "At least 2 stops are required."}), 400

    missing_coords = [
        s.get("company_name", s.get("customer_code", "?"))
        for s in stops
        if not s.get("lat") or not s.get("lng")
    ]
    if missing_coords:
        return jsonify({
            "error": f"Missing GPS coordinates for: {', '.join(missing_coords)}"
        }), 400

    try:
        result = optimize_route(
            stops_input=stops,
            start_time_str=settings.get("start_time", "08:00"),
            avg_speed=float(settings.get("avg_speed", 30)),
            stop_duration=float(settings.get("stop_duration", 5)),
        )
        return jsonify(result)
    except Exception:
        logger.exception("Route optimisation failed")
        return jsonify({"error": "Optimisation failed — check server logs."}), 500
```

---

## CHANGE 3 — Create new file: `templates/route_planner.html`

Create this file at the path `templates/route_planner.html` with exactly the following content:

```html
{% extends "base.html" %}
{% block title %}Route Planner{% endblock %}

{% block head %}
<style>
/* ── Layout ───────────────────────────────────────────────── */
.rp-page { display: grid; grid-template-columns: 380px 1fr; gap: 0; height: calc(100vh - 56px); overflow: hidden; }
.rp-sidebar { background: #fff; border-right: 1px solid #dee2e6; display: flex; flex-direction: column; overflow: hidden; }
.rp-main { overflow-y: auto; background: #f8f9fa; }

.rp-sidebar-header { padding: 14px 16px; border-bottom: 1px solid #dee2e6; background: #0d6efd; color: #fff; }
.rp-sidebar-header h5 { margin: 0; font-size: 15px; font-weight: 600; }
.rp-sidebar-header small { opacity: .8; font-size: 12px; }

.rp-sidebar-body { flex: 1; overflow-y: auto; padding: 14px; }
.rp-sidebar-footer { padding: 12px 14px; border-top: 1px solid #dee2e6; background: #f8f9fa; }

/* ── Search ───────────────────────────────────────────────── */
.search-wrap { position: relative; }
.search-dropdown { position: absolute; top: 100%; left: 0; right: 0; z-index: 1050;
  background: #fff; border: 1px solid #dee2e6; border-top: none;
  border-radius: 0 0 6px 6px; max-height: 280px; overflow-y: auto;
  box-shadow: 0 4px 12px rgba(0,0,0,.12); }
.search-item { padding: 9px 12px; cursor: pointer; border-bottom: 1px solid #f0f0f0; transition: background .1s; }
.search-item:hover, .search-item.active { background: #e7f1ff; }
.search-item:last-child { border-bottom: none; }
.search-item .name { font-weight: 600; font-size: 13px; }
.search-item .meta { font-size: 11px; color: #6c757d; }
.search-item .no-coord { color: #dc3545; font-size: 11px; }

/* ── Stop cards ──────────────────────────────────────────── */
.stop-list { display: flex; flex-direction: column; gap: 8px; min-height: 40px; }
.stop-card { background: #fff; border: 1px solid #dee2e6; border-radius: 8px;
  padding: 10px 12px; position: relative; transition: border-color .15s, box-shadow .15s; }
.stop-card:hover { border-color: #0d6efd; box-shadow: 0 2px 6px rgba(13,110,253,.12); }
.stop-card.is-start  { border-left: 4px solid #198754; }
.stop-card.is-end    { border-left: 4px solid #dc3545; }
.stop-card.is-fixed  { border-left: 4px solid #f0ad00; }

.stop-num { width: 26px; height: 26px; border-radius: 50%;
  background: #0d6efd; color: #fff; font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.stop-num.start-dot { background: #198754; }
.stop-num.end-dot   { background: #dc3545; }
.stop-num.fixed-dot { background: #f0ad00; }

.stop-name { font-weight: 600; font-size: 13px; }
.stop-addr { font-size: 11px; color: #6c757d; }
.stop-no-coord { font-size: 11px; color: #dc3545; font-weight: 500; }

.stop-badges { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 5px; }
.stop-controls { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; margin-top: 8px; padding-top: 8px; border-top: 1px solid #f0f0f0; }

.role-btn { padding: 3px 9px; font-size: 11px; border-radius: 10px; border: 1px solid #dee2e6;
  background: #f8f9fa; color: #495057; cursor: pointer; transition: all .15s; white-space: nowrap; }
.role-btn:hover { border-color: #0d6efd; color: #0d6efd; }
.role-btn.active-start { background: #d1e7dd; border-color: #198754; color: #0a3622; font-weight: 600; }
.role-btn.active-end   { background: #f8d7da; border-color: #dc3545; color: #58151c; font-weight: 600; }
.role-btn.active-fixed { background: #fff3cd; border-color: #f0ad00; color: #664d03; font-weight: 600; }

.fixed-seq-wrap { display: none; align-items: center; gap: 4px; }
.fixed-seq-wrap.visible { display: flex; }
.fixed-seq-wrap input { width: 52px; padding: 2px 6px; font-size: 12px; border: 1px solid #dee2e6; border-radius: 4px; text-align: center; }

.tw-wrap { display: flex; align-items: center; gap: 4px; }
.tw-wrap select { font-size: 11px; padding: 2px 5px; border: 1px solid #dee2e6; border-radius: 4px; }
.tw-wrap input[type=time] { font-size: 11px; padding: 2px 5px; border: 1px solid #dee2e6; border-radius: 4px; width: 88px; }

.stop-remove { position: absolute; top: 8px; right: 8px; background: none; border: none;
  color: #adb5bd; font-size: 14px; cursor: pointer; padding: 0 4px; line-height: 1; }
.stop-remove:hover { color: #dc3545; }

/* ── Settings ────────────────────────────────────────────── */
.settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.field-label { font-size: 11px; font-weight: 500; color: #6c757d; margin-bottom: 3px; }
.field-input { width: 100%; padding: 6px 8px; font-size: 13px; border: 1px solid #dee2e6; border-radius: 4px; }
.field-input:focus { outline: none; border-color: #0d6efd; box-shadow: 0 0 0 2px rgba(13,110,253,.15); }

.section-label { font-size: 11px; font-weight: 600; color: #6c757d; text-transform: uppercase;
  letter-spacing: .5px; margin: 14px 0 8px; }
.section-label:first-child { margin-top: 0; }

/* ── Results ─────────────────────────────────────────────── */
.rp-main { padding: 20px; }

.result-summary-bar { background: #0d6efd; color: #fff; border-radius: 8px;
  padding: 12px 18px; display: flex; gap: 24px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
.rsb-kpi { text-align: center; }
.rsb-kpi .val { font-size: 20px; font-weight: 700; }
.rsb-kpi .lbl { font-size: 11px; opacity: .8; }
.rsb-div { width: 1px; background: rgba(255,255,255,.3); height: 36px; }

.result-stop-row { background: #fff; border: 1px solid #dee2e6; border-radius: 8px;
  padding: 12px 14px; display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.result-stop-row.is-anchor    { border-left: 4px solid #f0ad00; }
.result-stop-row.is-start-row { border-left: 4px solid #198754; background: #f0fdf4; }
.result-stop-row.is-end-row   { border-left: 4px solid #dc3545; background: #fff5f5; }

.result-connector { text-align: center; font-size: 11px; color: #6c757d; margin: -3px 0;
  display: flex; align-items: center; gap: 6px; padding: 0 14px; }
.result-connector::before, .result-connector::after { content: ''; flex: 1; height: 1px; background: #dee2e6; }

.result-info { flex: 1; min-width: 0; }
.result-name { font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.result-addr { font-size: 11px; color: #6c757d; }
.result-time { font-size: 12px; font-weight: 600; color: #0d6efd; white-space: nowrap; }
.result-travel { font-size: 11px; color: #6c757d; }

.warn-box { background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px;
  padding: 10px 14px; font-size: 12px; color: #664d03; margin-bottom: 12px; }

.maps-box { background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px;
  padding: 10px 12px; font-size: 11px; font-family: monospace; word-break: break-all;
  color: #6c757d; margin-bottom: 10px; max-height: 72px; overflow-y: auto; }

.empty-state { text-align: center; padding: 60px 20px; color: #adb5bd; }
.empty-state .icon { font-size: 48px; margin-bottom: 12px; }
.empty-state p { font-size: 14px; }

.badge-fixed    { background: #fff3cd; color: #664d03; border: 1px solid #ffc107; }
.badge-tw-before { background: #fff3cd; color: #7a4700; }
.badge-tw-after  { background: #cfe2ff; color: #084298; }

@media(max-width:900px){
  .rp-page { grid-template-columns: 1fr; height: auto; }
  .rp-sidebar { height: auto; border-right: none; border-bottom: 1px solid #dee2e6; }
}
</style>
{% endblock %}

{% block content %}
<div class="rp-page">

  <div class="rp-sidebar">
    <div class="rp-sidebar-header">
      <h5><i class="fas fa-route me-2"></i>Route Planner</h5>
      <small>Build &amp; optimise a delivery run</small>
    </div>

    <div class="rp-sidebar-body">
      <div class="section-label">Add Customers</div>
      <div class="search-wrap mb-1">
        <input id="customer-search" type="text" class="field-input"
               placeholder="Search by name or code…" autocomplete="off">
        <div id="search-dropdown" class="search-dropdown" style="display:none"></div>
      </div>
      <div class="form-check mb-2">
        <input class="form-check-input" type="checkbox" id="include-no-coords">
        <label class="form-check-label" for="include-no-coords" style="font-size:11px;color:#6c757d">
          Show customers without GPS coordinates
        </label>
      </div>

      <div class="section-label">
        Stops
        <span id="stop-count" class="badge bg-primary ms-1" style="font-size:10px">0</span>
        <button class="btn btn-link btn-sm text-danger p-0 ms-2" style="font-size:11px" onclick="clearAllStops()">Clear all</button>
      </div>
      <div id="stop-list" class="stop-list"></div>
      <div id="stop-empty" class="text-center py-3" style="color:#adb5bd;font-size:12px">
        <i class="fas fa-map-pin mb-1 d-block" style="font-size:20px"></i>
        Search and add customers above
      </div>

      <div class="section-label mt-3">Driver Settings</div>
      <div class="settings-grid">
        <div>
          <div class="field-label">Start Time</div>
          <input id="cfg-start-time" type="time" class="field-input" value="08:00">
        </div>
        <div>
          <div class="field-label">Avg Speed (km/h)</div>
          <input id="cfg-speed" type="number" class="field-input" value="30" min="5" max="120">
        </div>
        <div>
          <div class="field-label">Stop Duration (min)</div>
          <input id="cfg-duration" type="number" class="field-input" value="5" min="1" max="120">
        </div>
      </div>
    </div>

    <div class="rp-sidebar-footer">
      <button id="optimize-btn" class="btn btn-success w-100" onclick="runOptimize()">
        <i class="fas fa-magic me-2"></i>Optimise Route
      </button>
      <div id="opt-error" class="alert alert-danger mt-2 mb-0 py-2 px-3" style="display:none;font-size:12px"></div>
    </div>
  </div>

  <div class="rp-main" id="rp-main">
    <div id="results-empty" class="empty-state">
      <div class="icon"><i class="fas fa-route text-primary"></i></div>
      <p class="text-muted">Add your stops on the left, then click <strong>Optimise Route</strong><br>to see the best sequence here.</p>
    </div>

    <div id="results-panel" style="display:none">
      <div class="result-summary-bar mb-3">
        <div class="rsb-kpi"><div class="val" id="r-stops">—</div><div class="lbl">Stops</div></div>
        <div class="rsb-div"></div>
        <div class="rsb-kpi"><div class="val" id="r-dist">—</div><div class="lbl">km (est.)</div></div>
        <div class="rsb-div"></div>
        <div class="rsb-kpi"><div class="val" id="r-time">—</div><div class="lbl">Duration</div></div>
        <div class="rsb-div"></div>
        <div class="rsb-kpi"><div class="val" id="r-finish">—</div><div class="lbl">Est. Finish</div></div>
        <div class="ms-auto d-flex gap-2 flex-wrap">
          <button class="btn btn-light btn-sm" onclick="openMaps()"><i class="fas fa-map-marked-alt me-1"></i>Open in Maps</button>
          <button class="btn btn-outline-light btn-sm" onclick="copyUrl()"><i class="fas fa-copy me-1"></i>Copy URL</button>
          <button class="btn btn-outline-light btn-sm" onclick="copyList()"><i class="fas fa-list me-1"></i>Copy List</button>
        </div>
      </div>
      <div id="r-warnings" class="warn-box" style="display:none"></div>
      <div id="r-route-list"></div>
      <div class="mt-3">
        <div class="field-label mb-1">Google Maps URL</div>
        <div class="maps-box" id="r-maps-url">—</div>
      </div>
    </div>
  </div>

</div>
{% endblock %}

{% block scripts %}
<script>
let stops = [];
let lastResult = null;
let searchTimer = null;
let selectedSearchIdx = -1;
let searchResults = [];

function uid() { return Math.random().toString(36).slice(2); }
function minToTime(m) {
  m = Math.round(m) % (24 * 60);
  return String(Math.floor(m / 60)).padStart(2,'0') + ':' + String(m % 60).padStart(2,'0');
}
function esc(s) {
  return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.getElementById('customer-search').addEventListener('input', function() {
  clearTimeout(searchTimer);
  const q = this.value.trim();
  if (q.length < 2) { hideDropdown(); return; }
  searchTimer = setTimeout(() => fetchCustomers(q), 220);
});

document.getElementById('customer-search').addEventListener('keydown', function(e) {
  const dd = document.getElementById('search-dropdown');
  if (dd.style.display === 'none') return;
  if (e.key === 'ArrowDown') { selectedSearchIdx = Math.min(selectedSearchIdx + 1, searchResults.length - 1); highlightItem(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { selectedSearchIdx = Math.max(selectedSearchIdx - 1, 0); highlightItem(); e.preventDefault(); }
  else if (e.key === 'Enter') { if (selectedSearchIdx >= 0) addCustomer(searchResults[selectedSearchIdx]); hideDropdown(); e.preventDefault(); }
  else if (e.key === 'Escape') { hideDropdown(); }
});

document.getElementById('include-no-coords').addEventListener('change', function() {
  const q = document.getElementById('customer-search').value.trim();
  if (q.length >= 2) fetchCustomers(q);
});

document.addEventListener('click', function(e) {
  if (!e.target.closest('.search-wrap')) hideDropdown();
});

function fetchCustomers(q) {
  const inc = document.getElementById('include-no-coords').checked ? 'true' : 'false';
  fetch(`/route-planner/api/search?q=${encodeURIComponent(q)}&include_no_coords=${inc}`)
    .then(r => r.json()).then(data => { searchResults = data; selectedSearchIdx = -1; renderDropdown(data); })
    .catch(() => hideDropdown());
}

function renderDropdown(items) {
  const dd = document.getElementById('search-dropdown');
  if (!items.length) { hideDropdown(); return; }
  dd.innerHTML = items.map((c, i) => `
    <div class="search-item" data-idx="${i}" onclick="addCustomer(searchResults[${i}]); hideDropdown();">
      <div class="name">${esc(c.company_name)}</div>
      <div class="meta">${esc(c.customer_code)}${c.address ? ' · ' + esc(c.address) : ''}
        ${!c.has_coords ? '<span class="no-coord ms-1"><i class="fas fa-exclamation-triangle"></i> No GPS</span>' : ''}
      </div>
    </div>`).join('');
  dd.style.display = 'block';
}

function highlightItem() {
  document.querySelectorAll('.search-item').forEach((el, i) => el.classList.toggle('active', i === selectedSearchIdx));
}
function hideDropdown() { document.getElementById('search-dropdown').style.display = 'none'; }

function addCustomer(c) {
  if (stops.find(s => s.customer_code === c.customer_code)) { showFlash(`${c.company_name} is already in the list.`, 'warning'); return; }
  stops.push({ id: uid(), customer_code: c.customer_code, company_name: c.company_name,
    address: c.address, lat: c.lat, lng: c.lng, has_coords: c.has_coords,
    is_start: false, is_end: false, fixed_seq: null, tw_type: 'none', tw_time: '' });
  document.getElementById('customer-search').value = '';
  hideDropdown();
  renderStops();
}

function removeStop(id) { stops = stops.filter(s => s.id !== id); renderStops(); }
function clearAllStops() { if (stops.length === 0 || confirm('Remove all stops?')) { stops = []; renderStops(); } }

function toggleStart(id) {
  const s = stops.find(x => x.id === id); if (!s) return;
  if (s.is_start) { s.is_start = false; } else { stops.forEach(x => { x.is_start = false; }); s.is_start = true; s.is_end = false; s.fixed_seq = null; }
  renderStops();
}

function toggleEnd(id) {
  const s = stops.find(x => x.id === id); if (!s) return;
  if (s.is_end) { s.is_end = false; } else { stops.forEach(x => { x.is_end = false; }); s.is_end = true; s.is_start = false; s.fixed_seq = null; }
  renderStops();
}

function toggleFixed(id) {
  const s = stops.find(x => x.id === id); if (!s || s.is_start || s.is_end) return;
  if (s.fixed_seq !== null) { s.fixed_seq = null; } else {
    const used = stops.filter(x => x.fixed_seq !== null).map(x => x.fixed_seq);
    let next = 1; while (used.includes(next)) next++;
    s.fixed_seq = next;
  }
  renderStops();
}

function updateFixedSeq(id, val) { const s = stops.find(x => x.id === id); if (s) s.fixed_seq = parseInt(val) || 1; }
function updateTwType(id, val) { const s = stops.find(x => x.id === id); if (s) { s.tw_type = val; renderStops(); } }
function updateTwTime(id, val) { const s = stops.find(x => x.id === id); if (s) s.tw_time = val; }

function renderStops() {
  const list = document.getElementById('stop-list');
  const empty = document.getElementById('stop-empty');
  document.getElementById('stop-count').textContent = stops.length;
  if (!stops.length) { list.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  list.innerHTML = stops.map(s => {
    const roleClass = s.is_start ? 'is-start' : s.is_end ? 'is-end' : s.fixed_seq !== null ? 'is-fixed' : '';
    const dotClass  = s.is_start ? 'start-dot' : s.is_end ? 'end-dot' : s.fixed_seq !== null ? 'fixed-dot' : '';
    const dotLabel  = s.is_start ? '▶' : s.is_end ? '🏁' : s.fixed_seq !== null ? s.fixed_seq : '?';
    const startActive = s.is_start ? 'active-start' : '';
    const endActive   = s.is_end   ? 'active-end'   : '';
    const fixedActive = s.fixed_seq !== null ? 'active-fixed' : '';
    const twTimeInput = (s.tw_type !== 'none') ? `<input type="time" value="${s.tw_time || ''}" onchange="updateTwTime('${s.id}', this.value)" style="font-size:11px;padding:2px 5px;border:1px solid #dee2e6;border-radius:4px;width:88px">` : '';
    return `
    <div class="stop-card ${roleClass}" id="sc-${s.id}">
      <button class="stop-remove" onclick="removeStop('${s.id}')" title="Remove">&times;</button>
      <div class="d-flex align-items-start gap-2">
        <div class="stop-num ${dotClass}">${dotLabel}</div>
        <div style="flex:1;min-width:0">
          <div class="stop-name">${esc(s.company_name)}</div>
          ${s.address ? `<div class="stop-addr">${esc(s.address)}</div>` : ''}
          ${!s.has_coords ? `<div class="stop-no-coord"><i class="fas fa-exclamation-triangle me-1"></i>No GPS — will be skipped</div>` : ''}
        </div>
      </div>
      <div class="stop-controls">
        <button class="role-btn ${startActive}" onclick="toggleStart('${s.id}')"><i class="fas fa-play-circle me-1"></i>Start</button>
        <button class="role-btn ${endActive}" onclick="toggleEnd('${s.id}')"><i class="fas fa-flag-checkered me-1"></i>End</button>
        ${!s.is_start && !s.is_end ? `<button class="role-btn ${fixedActive}" onclick="toggleFixed('${s.id}')"><i class="fas fa-thumbtack me-1"></i>Fixed</button>` : ''}
        ${s.fixed_seq !== null && !s.is_start && !s.is_end ? `<div class="fixed-seq-wrap visible ms-1"><span style="font-size:11px;color:#6c757d">Pos:</span><input type="number" min="1" value="${s.fixed_seq}" onchange="updateFixedSeq('${s.id}', this.value)"></div>` : ''}
        <div class="tw-wrap ms-auto">
          <select onchange="updateTwType('${s.id}', this.value)" style="font-size:11px;padding:2px 5px;border:1px solid #dee2e6;border-radius:4px">
            <option value="none"   ${s.tw_type==='none'   ?'selected':''}>No window</option>
            <option value="before" ${s.tw_type==='before' ?'selected':''}>Before</option>
            <option value="after"  ${s.tw_type==='after'  ?'selected':''}>After</option>
          </select>
          ${twTimeInput}
        </div>
      </div>
    </div>`;
  }).join('');
}

function runOptimize() {
  const btn = document.getElementById('optimize-btn');
  const errBox = document.getElementById('opt-error');
  errBox.style.display = 'none';
  if (stops.length < 2) { errBox.textContent = 'Add at least 2 stops first.'; errBox.style.display = 'block'; return; }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Optimising…';
  const payload = {
    stops: stops.map(s => ({ customer_code: s.customer_code, company_name: s.company_name,
      lat: s.lat, lng: s.lng, is_start: s.is_start, is_end: s.is_end,
      fixed_seq: s.fixed_seq, tw_type: s.tw_type, tw_time: s.tw_time })),
    settings: { start_time: document.getElementById('cfg-start-time').value,
      avg_speed: document.getElementById('cfg-speed').value,
      stop_duration: document.getElementById('cfg-duration').value }
  };
  fetch('/route-planner/api/optimize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrf() },
    body: JSON.stringify(payload)
  })
  .then(r => r.json())
  .then(data => {
    btn.disabled = false; btn.innerHTML = '<i class="fas fa-magic me-2"></i>Optimise Route';
    if (data.error) { errBox.textContent = data.error; errBox.style.display = 'block'; return; }
    lastResult = data; renderResults(data);
  })
  .catch(() => { btn.disabled = false; btn.innerHTML = '<i class="fas fa-magic me-2"></i>Optimise Route'; errBox.textContent = 'Network error — please try again.'; errBox.style.display = 'block'; });
}

function renderResults(data) {
  document.getElementById('results-empty').style.display = 'none';
  document.getElementById('results-panel').style.display = 'block';
  const hh = Math.floor(data.total_time / 60), mm = data.total_time % 60;
  document.getElementById('r-stops').textContent = data.route.length;
  document.getElementById('r-dist').textContent = data.total_dist;
  document.getElementById('r-time').textContent = hh > 0 ? `${hh}h ${mm}m` : `${mm}m`;
  document.getElementById('r-finish').textContent = data.end_arrival || '—';
  const warnBox = document.getElementById('r-warnings');
  if (data.warnings && data.warnings.length) {
    warnBox.style.display = 'block';
    warnBox.innerHTML = '<i class="fas fa-exclamation-triangle me-2"></i><strong>Attention:</strong><br>' + data.warnings.map(w => `• ${esc(w)}`).join('<br>');
  } else { warnBox.style.display = 'none'; }

  let html = '';
  if (data.start_stop) {
    html += `<div class="result-stop-row is-start-row">
      <div class="stop-num start-dot">▶</div>
      <div class="result-info"><div class="result-name">${esc(data.start_stop.company_name)}</div><div class="result-addr">${esc(data.start_stop.address || '')}</div></div>
      <div class="text-end"><div class="result-time">${esc(document.getElementById('cfg-start-time').value)}</div><div class="result-travel">Departure</div></div>
    </div>`;
  }
  data.route.forEach((s, i) => {
    const isAnchor = s.is_fixed_anchor;
    const twBadge = s.tw_type === 'before' ? `<span class="badge badge-tw-before ms-1" style="font-size:10px">⏰ Before ${esc(s.tw_time)}</span>`
      : s.tw_type === 'after' ? `<span class="badge badge-tw-after ms-1" style="font-size:10px">🕐 After ${esc(s.tw_time)}</span>` : '';
    const fixedBadge = isAnchor ? `<span class="badge badge-fixed ms-1" style="font-size:10px"><i class="fas fa-thumbtack"></i> Fixed</span>` : '';
    const waitNote = s.wait_min > 0 ? `<span style="font-size:10px;color:#f0ad00;margin-left:6px">⏳ Wait ${s.wait_min} min</span>` : '';
    if (i > 0 || data.start_stop) html += `<div class="result-connector">${s.travel_min} min · ${s.dist_km} km</div>`;
    html += `<div class="result-stop-row ${isAnchor ? 'is-anchor' : ''}">
      <div class="stop-num">${i + 1}</div>
      <div class="result-info"><div class="result-name">${esc(s.company_name)}${fixedBadge}${twBadge}${waitNote}</div><div class="result-addr">${esc(s.address || (s.lat + ', ' + s.lng))}</div></div>
      <div class="text-end"><div class="result-time">Arrive ${minToTime(s.arrival_time)}</div><div class="result-travel">${s.travel_min} min · ${s.dist_km} km</div></div>
    </div>`;
  });
  if (data.end_stop) {
    const lastS = data.route.length ? data.route[data.route.length - 1] : null;
    if (lastS) { const d = haversineJS(lastS.lat, lastS.lng, data.end_stop.lat, data.end_stop.lng); const t = Math.round((d / parseFloat(document.getElementById('cfg-speed').value)) * 60); html += `<div class="result-connector">${t} min · ${Math.round(d*10)/10} km</div>`; }
    html += `<div class="result-stop-row is-end-row">
      <div class="stop-num end-dot">🏁</div>
      <div class="result-info"><div class="result-name">${esc(data.end_stop.company_name)}</div><div class="result-addr">${esc(data.end_stop.address || '')}</div></div>
      <div class="text-end"><div class="result-time">${data.end_arrival || '—'}</div><div class="result-travel">Finish</div></div>
    </div>`;
  }
  document.getElementById('r-route-list').innerHTML = html;
  document.getElementById('r-maps-url').textContent = data.maps_url || '—';
  document.getElementById('rp-main').scrollTo({ top: 0, behavior: 'smooth' });
}

function openMaps() { if (lastResult && lastResult.maps_url) window.open(lastResult.maps_url, '_blank'); }
function copyUrl() { if (!lastResult) return; navigator.clipboard.writeText(lastResult.maps_url || '').then(() => showFlash('URL copied!', 'success')); }
function copyList() {
  if (!lastResult) return;
  const lines = lastResult.route.map((s, i) => `${i+1}. ${s.company_name} — Arrive ${minToTime(s.arrival_time)}${s.tw_type !== 'none' ? ` [${s.tw_type} ${s.tw_time}]` : ''}`);
  const start = lastResult.start_stop;
  const header = `Route — ${new Date().toLocaleDateString()}\n` + (start ? `Start: ${start.company_name} at ${document.getElementById('cfg-start-time').value}\n` : '') + `\n`;
  const footer = lastResult.end_stop ? `\nFinish: ${lastResult.end_stop.company_name} ~${lastResult.end_arrival}` : '';
  navigator.clipboard.writeText(header + lines.join('\n') + footer).then(() => showFlash('Stop list copied!', 'success'));
}

function haversineJS(lat1, lng1, lat2, lng2) {
  const R = 6371, toR = Math.PI / 180;
  const dLat = (lat2-lat1)*toR, dLng = (lng2-lng1)*toR;
  const a = Math.sin(dLat/2)**2 + Math.cos(lat1*toR)*Math.cos(lat2*toR)*Math.sin(dLng/2)**2;
  return R * 2 * Math.asin(Math.sqrt(a));
}

function getCsrf() {
  const m = document.cookie.match(/csrf_token=([^;]+)/);
  if (m) return decodeURIComponent(m[1]);
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

function showFlash(msg, type) {
  const el = document.createElement('div');
  el.className = `alert alert-${type} alert-dismissible position-fixed`;
  el.style.cssText = 'bottom:20px;right:20px;z-index:9999;min-width:220px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,.15)';
  el.innerHTML = `${msg}<button type="button" class="btn-close btn-close-sm" onclick="this.parentNode.remove()"></button>`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

renderStops();
</script>
{% endblock %}
```

---

## CHANGE 4 — Edit existing file: `main.py`

Find this block (around line 284):
```python
from routes_routes import bp as routes_bp
from routes_invoices import bp as route_invoices_bp
```

Add one line immediately after it:
```python
from blueprints.route_planner import route_planner_bp
```

Then find this block (a few lines below):
```python
app.register_blueprint(routes_bp, url_prefix='/routes')
app.register_blueprint(route_invoices_bp, url_prefix='/route-invoices')
```

Add one line immediately after it:
```python
app.register_blueprint(route_planner_bp)
```

---

## Summary

After these 4 changes the Route Planner will be live at `/route-planner/`. No database migrations needed — it reads from the existing `ps_customers` table (which already has `latitude` and `longitude` columns).
