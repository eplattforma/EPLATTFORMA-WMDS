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
    ],
    "ladder_levels": [
      "C",
      "D"
    ]
  },
  "overhead": {
    "start_seconds": 45,
    "end_seconds": 45
  },
  "travel": {
    "sec_align_per_stop": 2,
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
      "case": 3.0,
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
    }
  },
  "pack": {
    "base_seconds": 45,
    "per_line_seconds": 3,
    "special_group_seconds": 20
  }
}


def _to_bool(v: object) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def get_summer_mode() -> bool:
    return _to_bool(Setting.get(db.session, "summer_mode", "false"))


def get_time_params() -> Dict:
    params = Setting.get_json(db.session, "oi_time_params_v1", default=DEFAULT_PARAMS)
    return params if isinstance(params, dict) else DEFAULT_PARAMS


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
    rx = params.get("location", {}).get("regex") or DEFAULT_PARAMS["location"]["regex"]
    try:
        m = re.match(rx, loc.strip())
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


def order_stops_one_trip(stops: List[Stop], params: Dict) -> List[Stop]:
    upper = set(params.get("location", {}).get("upper_corridors") or DEFAULT_PARAMS["location"]["upper_corridors"])

    def is_upper(s: Stop) -> bool:
        return s.corridor_str in upper

    zone_priority = {"CROSS_SHIPPING": 0, "SENSITIVE": 1, "MAIN": 2, "SNACKS": 3}

    def zrank(z: str) -> int:
        return zone_priority.get(z.upper(), 9)

    ground = [s for s in stops if not is_upper(s)]
    upstairs = [s for s in stops if is_upper(s)]
    ground.sort(key=lambda s: (zrank(s.zone), s.corridor, s.bay, s.level, s.pos))
    upstairs.sort(key=lambda s: (zrank(s.zone), s.corridor, s.bay, s.level, s.pos))
    return ground + upstairs


def estimate_travel_seconds(stops_ordered: List[Stop], params: Dict) -> float:
    tr = params.get("travel", {})
    sec_align = float(tr.get("sec_align_per_stop", DEFAULT_PARAMS["travel"]["sec_align_per_stop"]))
    sec_corridor_change = float(tr.get("sec_per_corridor_change", DEFAULT_PARAMS["travel"]["sec_per_corridor_change"]))
    sec_corridor_step = float(tr.get("sec_per_corridor_step", DEFAULT_PARAMS["travel"]["sec_per_corridor_step"]))
    sec_bay_step = float(tr.get("sec_per_bay_step", DEFAULT_PARAMS["travel"]["sec_per_bay_step"]))
    sec_pos_step = float(tr.get("sec_per_pos_step", DEFAULT_PARAMS["travel"]["sec_per_pos_step"]))
    zone_switch = float(tr.get("zone_switch_seconds", DEFAULT_PARAMS["travel"]["zone_switch_seconds"]))
    upper_mult = float(tr.get("upper_walk_multiplier", DEFAULT_PARAMS["travel"]["upper_walk_multiplier"]))

    upper = set(params.get("location", {}).get("upper_corridors") or DEFAULT_PARAMS["location"]["upper_corridors"])

    def is_upper(s: Stop) -> bool:
        return s.corridor_str in upper

    t = 0.0
    prev: Optional[Stop] = None

    for s in stops_ordered:
        t += sec_align
        if prev is None:
            prev = s
            continue

        if prev.zone != s.zone:
            t += zone_switch

        if prev.corridor != s.corridor:
            t += sec_corridor_change
            t += abs(prev.corridor - s.corridor) * sec_corridor_step

        move = abs(prev.bay - s.bay) * sec_bay_step + abs(prev.pos - s.pos) * sec_pos_step
        if is_upper(prev) or is_upper(s):
            move *= upper_mult
        t += move
        prev = s

    if any(is_upper(s) for s in stops_ordered):
        t += float(tr.get("sec_stairs_up", DEFAULT_PARAMS["travel"]["sec_stairs_up"]))
        t += float(tr.get("sec_stairs_down", DEFAULT_PARAMS["travel"]["sec_stairs_down"]))

    return t


def _norm(v: object) -> str:
    return ("" if v is None else str(v)).strip().lower()


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
    t = base + per_qty * max(0, qty - 1)

    # Level from location
    _, _, level, _ = parse_location(inv_item.location, params)
    level = level or "A"
    t += float(level_seconds.get(level, 0))

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
    travel_s = estimate_travel_seconds(ordered, params)

    # Pick sum and per-line
    pick_s_total = 0.0
    per_line_seconds = {}
    for it in items:
        dw = dw_map.get(it.item_code)
        s = estimate_pick_seconds_for_line(it, dw, params, summer_mode)
        per_line_seconds[(it.item_code, it.invoice_no)] = float(s)
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
    inv.total_exp_time = float(result["total_minutes"])

    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    for it in items:
        s = result["per_line_seconds"].get((it.item_code, it.invoice_no), 0.0)
        it.exp_time = float(s) / 60.0

    if commit:
        db.session.commit()

    return result
