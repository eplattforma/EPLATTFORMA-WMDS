"""OI Order Time Estimator (ETC) - parameter-driven, explainable.

Writes:
  - invoices.total_exp_time (minutes per invoice)
  - invoice_items.exp_time (minutes per line; pick-handling time only)

Assumptions:
  - Location format like '10-01-A02'
  - Upper corridors are configured (default: 70/80/90)
  - One upstairs trip per order (stairs added once if any upper corridor present)
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app import db
from models import Setting, Invoice, InvoiceItem, DwItem


ESTIMATOR_VERSION = "oi_estimator_v1.1"

DEFAULT_PARAMS = {
  "version": "v1",
  "store_units": {
    "line_exp_time": "minutes",
    "invoice_total_exp_time": "minutes"
  },
  "location": {
    "regex": "^(?P<corridor>\\d{2})-(?P<bay>\\d{2})-(?P<level>[A-Z])(?P<pos>\\d{2})$",
    "upper_corridors": [
      "70",
      "80",
      "90"
    ]
  },
  "overhead": {
    "start_seconds": 45,
    "end_seconds": 45
  },
  "travel": {
    "sec_align_per_move": 13,
    "sec_align_per_stop": 13,
    "sec_per_corridor_change": 14,
    "sec_per_corridor_step": 4,
    "sec_per_bay_step": 2.5,
    "sec_per_pos_step": 0.6,
    "sec_stairs_up": 25,
    "sec_stairs_down": 20,
    "upper_walk_multiplier": 1.05,
    "zone_switch_seconds": 4
  },
  "pick": {
    "sec_align_scan_per_line": 13,
    "base_by_unit_type": {
      "item": 6,
      "pack": 8,
      "box": 10,
      "case": 13,
      "virtual_pack": 6
    },
    "per_qty_by_unit_type": {
      "item": 1.1,
      "pack": 1.6,
      "box": 2.0,
      "case": 0,
      "virtual_pack": 1.1
    },
    "level_seconds": {
      "A": 0,
      "B": 2,
      "C": 12,
      "D": 14
    },
    "difficulty_seconds": {
      "1": 0,
      "2": 2,
      "3": 6,
      "4": 12,
      "5": 20
    },
    "handling_seconds": {
      "fragility_yes": 6,
      "fragility_semi": 3,
      "spill_true": 5,
      "pressure_high": 4,
      "heat_sensitive_summer": 8
    },
    "ladder_rules": [
      {
        "corridors": ["11", "13"],
        "levels": ["C"],
        "ladder_seconds": 15
      }
    ]
  },
  "pack": {
    "base_seconds": 45,
    "per_line_seconds": 3,
    "special_group_seconds": 20
  }
}


def normalize_unit_type(raw):
    """Normalize unit type from PS365 or other sources to standard types"""
    if not raw:
        return "item"
    u = str(raw).strip().upper()
    
    mapping = {
        "PCS": "item", "PIECE": "item", "ITEM": "item", "EA": "item",
        "PK": "pack", "PACK": "pack",
        "BX": "box", "BOX": "box",
        "CS": "case", "CASE": "case",
        "VPACK": "virtual_pack", "VIRTUAL_PACK": "virtual_pack",
    }
    return mapping.get(u, u.lower())


def _to_bool(v: object) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def get_summer_mode() -> bool:
    return _to_bool(Setting.get(db.session, "summer_mode", "false"))


def get_time_params() -> Dict:
    params = Setting.get_json(db.session, "oi_time_params_v1", default=DEFAULT_PARAMS)
    if not isinstance(params, dict):
        return DEFAULT_PARAMS
    
    # Ensure critical hidden parameters like regex exist
    if "location" not in params:
        params["location"] = {}
    if "regex" not in params["location"]:
        params["location"]["regex"] = DEFAULT_PARAMS["location"]["regex"]
    
    # Backward compatibility: migrate old sec_align_per_stop to new split keys
    if "travel" not in params:
        params["travel"] = {}
    if "pick" not in params:
        params["pick"] = {}
    
    # Migration and ensuring existence of keys
    if "sec_align_per_stop" not in params["travel"]:
        params["travel"]["sec_align_per_stop"] = DEFAULT_PARAMS["travel"]["sec_align_per_stop"]

    old_align = params["travel"].get("sec_align_per_stop", 13)
    if "sec_align_per_move" not in params["travel"]:
        params["travel"]["sec_align_per_move"] = old_align
    if "sec_align_scan_per_line" not in params["pick"]:
        params["pick"]["sec_align_scan_per_line"] = old_align
        
    return params


def get_params_revision() -> int:
    """Get the current params revision number"""
    rev_str = Setting.get(db.session, "oi_time_params_v1_revision", "1")
    try:
        return int(rev_str)
    except (ValueError, TypeError):
        return 1


@dataclass(frozen=True)
class Stop:
    zone: str
    corridor: int
    bay: int
    level: str
    pos: int

    @property
    def corridor_str(self) -> str:
        return f"{self.corridor:02d}" if self.corridor < 100 else str(self.corridor)


def parse_location(loc: Optional[str], params: Dict) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[int]]:
    if not loc:
        return None, None, None, None
    
    # Normalization: remove internal spaces, trim, uppercase
    # Example: "31-04-E 02" -> "31-04-E02"
    clean_loc = "".join(loc.split()).strip().upper()
    
    rx = params.get("location", {}).get("regex") or DEFAULT_PARAMS["location"]["regex"]
    try:
        m = re.match(rx, clean_loc)
    except re.error:
        m = None
    if not m:
        return None, None, None, None
    gd = m.groupdict()
    try:
        corridor = int(gd["corridor"])
        bay = int(gd["bay"])
        level = gd["level"]
        pos = int(gd["pos"])
        return corridor, bay, level, pos
    except Exception:
        return None, None, None, None


def _safe_int(v: object, default: int = 0) -> int:
    try:
        if v is None:
            return default
        s = str(v).strip()
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default


def build_stops(items: List[InvoiceItem], params: Dict) -> List[Stop]:
    uniq = {}
    for it in items:
        c2, b2, l2, p2 = parse_location(it.location, params)
        corridor = c2 if c2 is not None else _safe_int(it.corridor, 0)
        bay = b2 if b2 is not None else 0
        level = l2 or "A"
        pos = p2 if p2 is not None else 0
        zone = (it.zone or "MAIN").strip().upper()
        key = (zone, corridor, bay, level, pos)
        if key not in uniq:
            uniq[key] = Stop(zone=zone, corridor=corridor, bay=bay, level=level, pos=pos)
    return list(uniq.values())


def _get_zone_priority_from_settings() -> Dict[str, int]:
    """Get zone priority from picking_sort_config settings, with fallback defaults"""
    try:
        import json
        setting = Setting.query.filter_by(key='picking_sort_config').first()
        if setting and setting.value:
            config = json.loads(setting.value) if isinstance(setting.value, str) else setting.value
            zone_config = config.get('zone', {})
            manual_priority = zone_config.get('manual_priority', [])
            if manual_priority and isinstance(manual_priority, list) and len(manual_priority) > 0:
                return {zone.upper(): idx for idx, zone in enumerate(manual_priority)}
    except Exception:
        pass
    return {}


def order_stops_one_trip(stops: List[Stop], params: Dict) -> List[Stop]:
    upper_corridors_raw = params.get("location", {}).get("upper_corridors") or DEFAULT_PARAMS["location"]["upper_corridors"]
    upper = set(str(x).zfill(2) for x in upper_corridors_raw)

    def is_upper(s: Stop) -> bool:
        return s.corridor_str in upper

    # Get zone priority from settings (if configured) - no hardcoded defaults
    zone_priority = _get_zone_priority_from_settings()

    def zrank(z: str) -> int:
        z_upper = z.upper()
        if zone_priority and z_upper in zone_priority:
            return zone_priority[z_upper]
        return 999

    # Separate ground and upstairs to ensure full level completion before moving floor
    ground = [s for s in stops if not is_upper(s)]
    upstairs = [s for s in stops if is_upper(s)]
    
    # Ground: Sort by Zone -> Corridor -> Bay -> Level -> Pos
    ground.sort(key=lambda s: (zrank(s.zone), s.corridor, s.bay, s.level, s.pos))
    
    # Upstairs: Sort by Zone -> Corridor -> Bay -> Level -> Pos
    upstairs.sort(key=lambda s: (zrank(s.zone), s.corridor, s.bay, s.level, s.pos))
    
    return ground + upstairs


def estimate_travel_seconds(stops_ordered: List[Stop], params: Dict) -> Dict[str, float]:
    tr = params.get("travel", {})
    # Use sec_align_per_move instead of sec_align_per_stop for travel calculation
    sec_align = float(tr.get("sec_align_per_move", DEFAULT_PARAMS["travel"]["sec_align_per_move"]))
    sec_corridor_change = float(tr.get("sec_per_corridor_change", DEFAULT_PARAMS["travel"]["sec_per_corridor_change"]))
    sec_corridor_step = float(tr.get("sec_per_corridor_step", DEFAULT_PARAMS["travel"]["sec_per_corridor_step"]))
    sec_bay_step = float(tr.get("sec_per_bay_step", DEFAULT_PARAMS["travel"]["sec_per_bay_step"]))
    sec_pos_step = float(tr.get("sec_per_pos_step", DEFAULT_PARAMS["travel"]["sec_per_pos_step"]))
    zone_switch = float(tr.get("zone_switch_seconds", DEFAULT_PARAMS["travel"]["zone_switch_seconds"]))
    upper_mult = float(tr.get("upper_walk_multiplier", DEFAULT_PARAMS["travel"]["upper_walk_multiplier"]))

    upper = set(params.get("location", {}).get("upper_corridors") or DEFAULT_PARAMS["location"]["upper_corridors"])

    def is_upper(s: Stop) -> bool:
        return s.corridor_str in upper

    breakdown = {
        "align_seconds": 0.0,
        "zone_switch_seconds": 0.0,
        "corridor_change_seconds": 0.0,
        "walking_seconds": 0.0,
        "stairs_seconds": 0.0
    }

    prev: Optional[Stop] = None

    for s in stops_ordered:
        breakdown["align_seconds"] += sec_align
        if prev is None:
            prev = s
            continue

        if prev.zone != s.zone:
            breakdown["zone_switch_seconds"] += zone_switch

        if prev.corridor != s.corridor:
            breakdown["corridor_change_seconds"] += sec_corridor_change
            breakdown["walking_seconds"] += abs(prev.corridor - s.corridor) * sec_corridor_step

        move = abs(prev.bay - s.bay) * sec_bay_step + abs(prev.pos - s.pos) * sec_pos_step
        if is_upper(prev) or is_upper(s):
            move *= upper_mult
        breakdown["walking_seconds"] += move
        prev = s

    if any(is_upper(s) for s in stops_ordered):
        breakdown["stairs_seconds"] += float(tr.get("sec_stairs_up", DEFAULT_PARAMS["travel"]["sec_stairs_up"]))
        breakdown["stairs_seconds"] += float(tr.get("sec_stairs_down", DEFAULT_PARAMS["travel"]["sec_stairs_down"]))

    breakdown["total"] = sum(v for k, v in breakdown.items())
    return breakdown


def estimate_travel_breakdown_between(s1: Stop, s2: Stop, params: Dict) -> Dict[str, float]:
    """Calculate detailed travel breakdown between two specific stops."""
    tr = params.get("travel", {})
    # Use sec_align_per_move instead of sec_align_per_stop for travel calculation
    sec_align = float(tr.get("sec_align_per_move", DEFAULT_PARAMS["travel"]["sec_align_per_move"]))
    sec_corridor_change = float(tr.get("sec_per_corridor_change", DEFAULT_PARAMS["travel"]["sec_per_corridor_change"]))
    sec_corridor_step = float(tr.get("sec_per_corridor_step", DEFAULT_PARAMS["travel"]["sec_per_corridor_step"]))
    sec_bay_step = float(tr.get("sec_per_bay_step", DEFAULT_PARAMS["travel"]["sec_per_bay_step"]))
    sec_pos_step = float(tr.get("sec_per_pos_step", DEFAULT_PARAMS["travel"]["sec_per_pos_step"]))
    zone_switch = float(tr.get("zone_switch_seconds", DEFAULT_PARAMS["travel"]["zone_switch_seconds"]))
    upper_mult = float(tr.get("upper_walk_multiplier", DEFAULT_PARAMS["travel"]["upper_walk_multiplier"]))

    upper = set(params.get("location", {}).get("upper_corridors") or DEFAULT_PARAMS["location"]["upper_corridors"])

    def is_upper(s: Stop) -> bool:
        return s.corridor_str in upper

    res = {
        "align_seconds": sec_align,
        "zone_switch_seconds": zone_switch if s1.zone != s2.zone else 0.0,
        "corridor_change_seconds": sec_corridor_change if s1.corridor != s2.corridor else 0.0,
        "walking_seconds": 0.0,
        "stairs_seconds": 0.0
    }

    if s1.corridor != s2.corridor:
        res["walking_seconds"] += abs(s1.corridor - s2.corridor) * sec_corridor_step

    move = abs(s1.bay - s2.bay) * sec_bay_step + abs(s1.pos - s2.pos) * sec_pos_step
    if is_upper(s1) or is_upper(s2):
        move *= upper_mult
    res["walking_seconds"] += move

    # Stairs are usually handled once per order, but for step breakdown we skip adding them here
    # to avoid double counting unless it's the very first trip (handled in routes_oi_reports)

    res["total"] = sum(v for k, v in res.items())
    return res


def _norm(v: object) -> str:
    return ("" if v is None else str(v)).strip().lower()


def ladder_seconds_for(corridor: str, level: str, params: Dict) -> float:
    """Calculate ladder seconds if corridor/level matches any ladder rule."""
    rules = ((params.get("pick") or {}).get("ladder_rules") or [])
    corridor = (corridor or "").zfill(2)
    level = (level or "").upper()
    for r in rules:
        corridors = [str(x).zfill(2) for x in (r.get("corridors") or [])]
        levels = [str(x).upper() for x in (r.get("levels") or [])]
        if corridor in corridors and level in levels:
            return float(r.get("ladder_seconds") or 0)
    return 0.0


def estimate_pick_seconds_for_line(inv_item: InvoiceItem, dw_item: Optional[DwItem], params: Dict, summer_mode: bool) -> float:
    pk = params.get("pick", {})
    base_map = pk.get("base_by_unit_type", DEFAULT_PARAMS["pick"]["base_by_unit_type"])
    per_qty_map = pk.get("per_qty_by_unit_type", DEFAULT_PARAMS["pick"]["per_qty_by_unit_type"])
    level_seconds = pk.get("level_seconds", DEFAULT_PARAMS["pick"]["level_seconds"])
    diff_seconds = pk.get("difficulty_seconds", DEFAULT_PARAMS["pick"]["difficulty_seconds"])
    handling = pk.get("handling_seconds", DEFAULT_PARAMS["pick"]["handling_seconds"])

    unit_type = _norm(inv_item.unit_type) or "item"
    qty = _safe_int(getattr(inv_item, "display_qty", None) or inv_item.qty, 0)
    qty = max(qty, 0)

    base = float(base_map.get(unit_type, base_map.get("item", 6)))
    per_qty = float(per_qty_map.get(unit_type, per_qty_map.get("item", 1.1)))
    
    # Calculate base picking time
    t = base + per_qty * max(0, qty - 1)
    
    # Add travel/walking alignment time for this specific pick
    # Note: Global travel between locations is calculated in estimate_travel_seconds
    # This alignment time accounts for the final approach to the item
    # Since the user requested 13 sec (0.216 min) for a case, we set sec_align_per_stop to 13
    sec_align = float(pk.get("sec_align_scan_per_line", 0))
    t += sec_align

    # Level from location
    corridor, _, level, _ = parse_location(inv_item.location, params)
    level = level or "A"
    t += float(level_seconds.get(level, 0))
    
    # Ladder penalty (conditional on corridor + level match)
    corridor_str = f"{corridor:02d}" if corridor is not None else "00"
    t += ladder_seconds_for(corridor_str, level, params)

    # Pick difficulty from OI
    pick_diff = "2"
    if dw_item is not None:
        v = getattr(dw_item, "wms_pick_difficulty", None)
        if v is not None and str(v).strip() != "":
            pick_diff = str(v).strip()
    t += float(diff_seconds.get(pick_diff, 0))

    # Handling penalties from OI
    frag = _norm(getattr(dw_item, "wms_fragility", None)) if dw_item is not None else ""
    if frag in ("yes", "y", "true", "fragile"):
        t += float(handling.get("fragility_yes", 0))
    elif frag in ("semi", "moderate", "medium"):
        t += float(handling.get("fragility_semi", 0))

    spill = _norm(getattr(dw_item, "wms_spill_risk", None)) if dw_item is not None else ""
    if spill in ("true", "1", "yes", "y"):
        t += float(handling.get("spill_true", 0))

    pressure = _norm(getattr(dw_item, "wms_pressure_sensitivity", None)) if dw_item is not None else ""
    if pressure == "high":
        t += float(handling.get("pressure_high", 0))

    temp = _norm(getattr(dw_item, "wms_temperature_sensitivity", None)) if dw_item is not None else ""
    if summer_mode and temp == "heat_sensitive":
        t += float(handling.get("heat_sensitive_summer", 0))

    return t


def estimate_pack_seconds(items: List[InvoiceItem], dw_map: Dict[str, DwItem], params: Dict, summer_mode: bool) -> float:
    pk = params.get("pack", {})
    base = float(pk.get("base_seconds", DEFAULT_PARAMS["pack"]["base_seconds"]))
    per_line = float(pk.get("per_line_seconds", DEFAULT_PARAMS["pack"]["per_line_seconds"]))
    per_group = float(pk.get("special_group_seconds", DEFAULT_PARAMS["pack"]["special_group_seconds"]))

    has_fragile = False
    has_spill = False
    has_pressure = False
    has_heat = False

    for it in items:
        dw = dw_map.get(it.item_code)
        if not dw:
            continue
        frag = _norm(getattr(dw, "wms_fragility", None))
        if frag in ("yes", "y", "true", "fragile", "semi", "moderate", "medium"):
            has_fragile = True
        spill = _norm(getattr(dw, "wms_spill_risk", None))
        if spill in ("true", "1", "yes", "y"):
            has_spill = True
        pressure = _norm(getattr(dw, "wms_pressure_sensitivity", None))
        if pressure == "high":
            has_pressure = True
        temp = _norm(getattr(dw, "wms_temperature_sensitivity", None))
        if summer_mode and temp == "heat_sensitive":
            has_heat = True

    special_groups = sum([has_fragile, has_spill, has_pressure, has_heat])
    return base + per_line * len(items) + per_group * special_groups


def estimate_invoice_time(invoice_no: str) -> Dict:
    params = get_time_params()
    summer_mode = get_summer_mode()

    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        raise ValueError(f"Invoice not found: {invoice_no}")

    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    if not items:
        return {"invoice_no": invoice_no, "total_minutes": 0.0, "breakdown": {"overhead": 0, "travel": 0, "pick": 0, "pack": 0}}

    item_codes = [it.item_code for it in items if it.item_code]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items if getattr(d, "active", True)}

    # Travel via stops
    stops = build_stops(items, params)
    ordered = order_stops_one_trip(stops, params)
    travel_res = estimate_travel_seconds(ordered, params)
    travel_s = travel_res.get("total", 0.0)

    # Pick sum and per-line
    pick_s_total = 0.0
    per_line_seconds = {}
    
    # Track seen locations for travel allocation
    location_seen = set()
    location_walk_map = {}
    if ordered:
        sec_align_move = float(params.get("travel", {}).get("sec_align_per_move", 15))
        for i, stop in enumerate(ordered):
            loc_key = (stop.zone, stop.corridor, stop.bay, stop.level, stop.pos)
            if loc_key not in location_walk_map:
                base_s = sec_align_move
                if i == 0:
                    location_walk_map[loc_key] = base_s
                else:
                    prev = ordered[i - 1]
                    walk_s = _compute_walk_between_stops(prev, stop, params)
                    location_walk_map[loc_key] = base_s + walk_s

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
            
        per_line_seconds[(it.item_code, it.invoice_no)] = float(s) + float(walk_s)
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
        s = result["per_line_seconds"].get((it.item_code, it.invoice_no), 0.0)
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

    item_codes = [it.item_code for it in items if it.item_code]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items if getattr(d, "active", True)}

    # Build stops and compute travel with line-level allocation
    stops = build_stops(items, params)
    ordered = order_stops_one_trip(stops, params)
    travel_res = estimate_travel_seconds(ordered, params)
    travel_s = travel_res.get("total", 0.0)

    # Use "Align per move" setting for the base travel time allocated to each location stop
    sec_align_move = float(params.get("travel", {}).get("sec_align_per_move", 15))

    # Create location -> walk seconds mapping for first line at each location
    location_walk_map = {}
    if ordered:
        for i, stop in enumerate(ordered):
            loc_key = (stop.zone, stop.corridor, stop.bay, stop.level, stop.pos)
            if loc_key not in location_walk_map:
                # Every location stop gets the "Align per move" time
                base_s = sec_align_move
                if i == 0:
                    location_walk_map[loc_key] = base_s
                else:
                    prev = ordered[i - 1]
                    walk_s = _compute_walk_between_stops(prev, stop, params)
                    location_walk_map[loc_key] = base_s + walk_s

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


def _compute_walk_between_stops(prev: Stop, curr: Stop, params: Dict) -> float:
    """Compute walk seconds between two stops"""
    travel = params.get("travel", {})
    
    align = float(travel.get("sec_align_per_move", travel.get("sec_align_per_stop", 13)))
    s = align
    
    upper_corridors = set(params.get("location", {}).get("upper_corridors", ["70", "80", "90"]))
    
    # Zone switch
    if prev.zone != curr.zone:
        s += float(travel.get("zone_switch_seconds", 4))
    
    # Corridor change
    if prev.corridor != curr.corridor:
        s += float(travel.get("sec_per_corridor_change", 14))
        corridor_diff = abs(curr.corridor - prev.corridor)
        s += float(travel.get("sec_per_corridor_step", 4)) * corridor_diff
    
    # Bay change
    bay_diff = abs(curr.bay - prev.bay)
    s += float(travel.get("sec_per_bay_step", 2.5)) * bay_diff
    
    # Position change
    pos_diff = abs(curr.pos - prev.pos)
    s += float(travel.get("sec_per_pos_step", 0.6)) * pos_diff
    
    # Upper floor multiplier
    if curr.corridor_str in upper_corridors:
        s *= float(travel.get("upper_walk_multiplier", 1.05))
    
    return s
