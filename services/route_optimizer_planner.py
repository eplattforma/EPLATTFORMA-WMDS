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
