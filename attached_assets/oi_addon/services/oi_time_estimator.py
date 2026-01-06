"""
services/oi_time_estimator.py

OI Order Time Estimation (ETC) add-on.

- Reads OI attributes already stored on ps_items_dw (wms_* fields).
- Reads invoice lines from invoice_items.
- Computes an explainable estimate: overhead + travel + picking + packing.
- Persists:
  - invoice_items.exp_time (minutes per line)
  - invoices.total_exp_time (minutes per invoice)

Designed for Option A integration: call after invoice import inserts invoice_items.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import re
import math
from datetime import datetime

# IMPORTANT:
# Importing db/models at module import time can cause circular imports in some Flask apps.
# We do local imports inside functions where needed.


@dataclass(frozen=True)
class ParsedLocation:
    corridor: Optional[str]
    bay: Optional[int]
    level: Optional[str]
    pos: Optional[int]


@dataclass
class Stop:
    zone: str
    corridor: Optional[str]
    bay: Optional[int]
    level: Optional[str]
    pos: Optional[int]
    location: Optional[str]


def _safe_int(x, default=None):
    try:
        if x is None:
            return default
        return int(str(x))
    except Exception:
        return default


def get_time_params(session) -> Dict[str, Any]:
    """Load the estimator parameter set from settings."""
    from models import Setting  # local import
    params = Setting.get_json(session, "oi_time_params_v1", default={})
    if not params:
        raise RuntimeError("Missing settings key oi_time_params_v1. Create it from oi_time_params_v1.json.")
    return params


def get_summer_mode(session) -> bool:
    """Summer mode toggle (stored in settings)."""
    from models import Setting  # local import
    val = (Setting.get(session, "summer_mode", default="false") or "false").strip().lower()
    return val in ("true", "1", "yes", "on")


def parse_location(location: Optional[str], corridor_fallback: Optional[str], params: Dict[str, Any]) -> ParsedLocation:
    """Parse warehouse location (e.g., 10-01-A02)."""
    if not location:
        return ParsedLocation(corridor=corridor_fallback, bay=None, level=None, pos=None)

    regex = params.get("location", {}).get("regex")
    if not regex:
        return ParsedLocation(corridor=corridor_fallback, bay=None, level=None, pos=None)

    m = re.match(regex, location.strip())
    if not m:
        return ParsedLocation(corridor=corridor_fallback, bay=None, level=None, pos=None)

    gd = m.groupdict()
    corridor = gd.get("corridor") or corridor_fallback
    bay = _safe_int(gd.get("bay"))
    level = gd.get("level")
    pos = _safe_int(gd.get("pos"))
    return ParsedLocation(corridor=corridor, bay=bay, level=level, pos=pos)


def build_stops(invoice_items: List[Any], params: Dict[str, Any]) -> List[Stop]:
    """Collapse invoice lines into unique stops for travel calculation."""
    seen = set()
    stops: List[Stop] = []

    for line in invoice_items:
        zone = (line.zone or "MAIN").strip().upper()
        parsed = parse_location(getattr(line, "location", None), getattr(line, "corridor", None), params)

        key = (zone, parsed.corridor, parsed.bay, parsed.level, parsed.pos)
        if key in seen:
            continue
        seen.add(key)

        stops.append(
            Stop(
                zone=zone,
                corridor=parsed.corridor,
                bay=parsed.bay,
                level=parsed.level,
                pos=parsed.pos,
                location=getattr(line, "location", None),
            )
        )
    return stops


def order_stops_one_trip(stops: List[Stop], params: Dict[str, Any]) -> List[Stop]:
    """Order stops so we do ground first then upstairs (70/80/90) once."""
    upper = set(params.get("location", {}).get("upper_corridors", []))
    travel_cfg = params.get("travel", {})

    # Zone priority is configurable; defaults can be adjusted.
    zone_priority = travel_cfg.get("zone_priority") or ["CROSS_SHIPPING", "SENSITIVE", "MAIN", "SNACKS"]
    prio_map = {z: i for i, z in enumerate(zone_priority)}

    def stop_sort_key(s: Stop):
        # corridor numeric sorting if possible
        cnum = _safe_int(s.corridor, default=9999)
        return (
            prio_map.get(s.zone, 999),
            cnum,
            s.bay if s.bay is not None else 999,
            s.level or "Z",
            s.pos if s.pos is not None else 999,
        )

    ground = [s for s in stops if (s.corridor not in upper)]
    upstairs = [s for s in stops if (s.corridor in upper)]

    ground.sort(key=stop_sort_key)
    upstairs.sort(key=stop_sort_key)

    return ground + upstairs


def estimate_travel_seconds(ordered_stops: List[Stop], params: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Estimate travel (walking) seconds between ordered stops."""
    if not ordered_stops:
        return 0.0, {"stops": 0}

    loc_cfg = params.get("location", {})
    upper = set(loc_cfg.get("upper_corridors", []))

    tcfg = params.get("travel", {})
    sec_align = float(tcfg.get("sec_align_per_stop", 0))
    sec_corridor_change = float(tcfg.get("sec_per_corridor_change", 0))
    sec_corridor_step = float(tcfg.get("sec_per_corridor_step", 0))
    sec_bay_step = float(tcfg.get("sec_per_bay_step", 0))
    sec_pos_step = float(tcfg.get("sec_per_pos_step", 0))
    sec_zone_switch = float(tcfg.get("zone_switch_seconds", 0))
    up_mult = float(tcfg.get("upper_walk_multiplier", 1.0))

    # One-trip stairs overhead if any upstairs stop exists
    stairs = 0.0
    has_upper = any((s.corridor in upper) for s in ordered_stops)
    if has_upper:
        stairs = float(tcfg.get("sec_stairs_up", 0)) + float(tcfg.get("sec_stairs_down", 0))

    total = 0.0
    prev = None
    zone_switches = 0
    corridor_changes = 0
    bay_steps = 0
    pos_steps = 0

    for s in ordered_stops:
        total += sec_align  # stop alignment
        if prev is not None:
            # zone switch
            if (prev.zone or "") != (s.zone or ""):
                total += sec_zone_switch
                zone_switches += 1

            # corridor change
            if (prev.corridor or "") != (s.corridor or ""):
                total += sec_corridor_change
                corridor_changes += 1
                # extra step penalty if jumping multiple corridors
                prev_c = _safe_int(prev.corridor, default=None)
                cur_c = _safe_int(s.corridor, default=None)
                if prev_c is not None and cur_c is not None:
                    jump = abs(cur_c - prev_c)
                    if jump > 1:
                        total += (jump - 1) * sec_corridor_step

            # bay steps within corridor
            if prev.bay is not None and s.bay is not None:
                steps = abs(s.bay - prev.bay)
                total += steps * sec_bay_step
                bay_steps += steps

            # pos steps
            if prev.pos is not None and s.pos is not None:
                steps = abs(s.pos - prev.pos)
                total += steps * sec_pos_step
                pos_steps += steps

        # Apply upper multiplier when CURRENT stop is upstairs.
        if up_mult != 1.0 and (s.corridor in upper):
            # Multiply only the incremental movement portion at this step.
            # v1 approximation: multiply the alignment portion for upper stops.
            total += sec_align * (up_mult - 1.0)

        prev = s

    total += stairs

    dbg = {
        "stops": len(ordered_stops),
        "zone_switches": zone_switches,
        "corridor_changes": corridor_changes,
        "bay_steps": bay_steps,
        "pos_steps": pos_steps,
        "stairs_seconds": stairs,
    }
    return total, dbg


def _normalize_unit_type(unit_type: Optional[str]) -> str:
    if not unit_type:
        return "item"
    ut = unit_type.strip().lower()
    # normalize common variants
    if ut in ("units", "unit"):
        return "item"
    if ut in ("vpack", "virtual_pack", "virtual pack", "pieces"):
        return "virtual_pack"
    return ut


def estimate_pick_seconds_for_line(line: Any, dw_item: Any, params: Dict[str, Any], summer_mode: bool) -> Tuple[float, Dict[str, Any]]:
    """
    Estimate seconds to pick a single invoice line (touch time).
    Uses unit_type, qty, location level, wms_* attributes.
    """
    pcfg = params.get("pick", {})
    base_map = pcfg.get("base_by_unit_type", {})
    per_qty_map = pcfg.get("per_qty_by_unit_type", {})
    level_seconds_map = pcfg.get("level_seconds", {})
    difficulty_seconds_map = pcfg.get("difficulty_seconds", {})
    hcfg = pcfg.get("handling_seconds", {})

    # Unit type (prefer line.unit_type; fallback to dw_item.wms_unit_type if present)
    ut = _normalize_unit_type(getattr(line, "unit_type", None) or getattr(dw_item, "wms_unit_type", None))
    base = float(base_map.get(ut, base_map.get("item", 6)))
    per_qty = float(per_qty_map.get(ut, per_qty_map.get("item", 1.0)))

    # Quantity (use display_qty if implemented on InvoiceItem)
    qty = getattr(line, "display_qty", None)
    if qty is None:
        qty = getattr(line, "qty", 1)
    qty = _safe_int(qty, default=1)
    qty = max(1, qty)

    # Location parsing for level penalty
    parsed = parse_location(getattr(line, "location", None), getattr(line, "corridor", None), params)
    lvl = (parsed.level or "").upper()
    level_pen = float(level_seconds_map.get(lvl, 0.0))

    # Pick difficulty (from dw_item.wms_pick_difficulty)
    diff = getattr(dw_item, "wms_pick_difficulty", None)
    diff_key = str(_safe_int(diff, default=2))
    diff_pen = float(difficulty_seconds_map.get(diff_key, 0.0))

    # Handling penalties based on OI attributes
    frag = (getattr(dw_item, "wms_fragility", None) or "").upper()
    spill = getattr(dw_item, "wms_spill_risk", None)
    pressure = (getattr(dw_item, "wms_pressure_sensitivity", None) or "").lower()
    temp = (getattr(dw_item, "wms_temperature_sensitivity", None) or "").lower()

    handling = 0.0
    if frag == "YES":
        handling += float(hcfg.get("fragility_yes", 0))
    elif frag == "SEMI":
        handling += float(hcfg.get("fragility_semi", 0))

    # spill may be bool or string
    if spill is True or str(spill).strip().upper() in ("TRUE", "1", "YES", "Y"):
        handling += float(hcfg.get("spill_true", 0))

    if pressure == "high":
        handling += float(hcfg.get("pressure_high", 0))

    if summer_mode and temp == "heat_sensitive":
        handling += float(hcfg.get("heat_sensitive_summer", 0))

    # Final
    seconds = base + per_qty * (qty - 1) + level_pen + diff_pen + handling

    dbg = {
        "unit_type": ut,
        "qty": qty,
        "base": base,
        "per_qty": per_qty,
        "level": lvl or None,
        "level_pen": level_pen,
        "difficulty": diff_key,
        "difficulty_pen": diff_pen,
        "handling": handling,
        "fragility": frag or None,
        "spill": spill,
        "pressure": pressure or None,
        "temp": temp or None,
        "summer_mode": summer_mode,
    }
    return float(max(0.0, seconds)), dbg


def estimate_pack_seconds(invoice_items: List[Any], dw_items_by_code: Dict[str, Any], params: Dict[str, Any], summer_mode: bool) -> Tuple[float, Dict[str, Any]]:
    """Estimate packing seconds at invoice level."""
    cfg = params.get("pack", {})
    base = float(cfg.get("base_seconds", 0))
    per_line = float(cfg.get("per_line_seconds", 0))
    special_group = float(cfg.get("special_group_seconds", 0))

    total_lines = len(invoice_items)

    # Determine special groups present
    groups = set()
    for line in invoice_items:
        code = getattr(line, "item_code", None)
        dw = dw_items_by_code.get(code)
        if not dw:
            continue
        frag = (getattr(dw, "wms_fragility", None) or "").upper()
        spill = getattr(dw, "wms_spill_risk", None)
        pressure = (getattr(dw, "wms_pressure_sensitivity", None) or "").lower()
        temp = (getattr(dw, "wms_temperature_sensitivity", None) or "").lower()

        if frag == "YES":
            groups.add("fragile")
        if spill is True or str(spill).strip().upper() in ("TRUE", "1", "YES", "Y"):
            groups.add("spill")
        if pressure == "high":
            groups.add("pressure_high")
        if summer_mode and temp == "heat_sensitive":
            groups.add("heat_sensitive_summer")

    seconds = base + per_line * total_lines + special_group * len(groups)
    dbg = {"total_lines": total_lines, "special_groups": sorted(groups), "special_group_count": len(groups)}
    return float(max(0.0, seconds)), dbg


def estimate_invoice_time(invoice_no: str) -> Dict[str, Any]:
    """Compute estimate for one invoice and return a detailed breakdown (no persistence)."""
    from app import db  # local import
    from models import Invoice, InvoiceItem, DwItem

    session = db.session
    params = get_time_params(session)
    summer_mode = get_summer_mode(session)

    invoice = session.query(Invoice).filter_by(invoice_no=invoice_no).first()
    if not invoice:
        raise ValueError(f"Invoice not found: {invoice_no}")

    lines = session.query(InvoiceItem).filter_by(invoice_no=invoice_no).all()

    # Load DwItem (ps_items_dw) for these codes
    codes = [l.item_code for l in lines if l.item_code]
    dw_rows = session.query(DwItem).filter(DwItem.item_code_365.in_(codes)).all()
    dw_by_code = {d.item_code_365: d for d in dw_rows}

    # Build route stops and compute travel
    stops = build_stops(lines, params)
    ordered_stops = order_stops_one_trip(stops, params)
    travel_sec, travel_dbg = estimate_travel_seconds(ordered_stops, params)

    # Overhead
    overhead_cfg = params.get("overhead", {})
    overhead_sec = float(overhead_cfg.get("start_seconds", 0)) + float(overhead_cfg.get("end_seconds", 0))

    # Pick time per line
    pick_total = 0.0
    line_details = []
    for line in lines:
        dw = dw_by_code.get(line.item_code)
        if not dw:
            # If missing in ps_items_dw, assume minimal defaults with low penalties
            class Dummy: pass
            dw = Dummy()
            setattr(dw, "wms_unit_type", getattr(line, "unit_type", None))
            setattr(dw, "wms_pick_difficulty", 2)
            setattr(dw, "wms_fragility", None)
            setattr(dw, "wms_spill_risk", None)
            setattr(dw, "wms_pressure_sensitivity", None)
            setattr(dw, "wms_temperature_sensitivity", None)

        sec, dbg = estimate_pick_seconds_for_line(line, dw, params, summer_mode)
        pick_total += sec
        line_details.append({"item_code": line.item_code, "qty": getattr(line, "qty", None), "seconds": sec, "debug": dbg})

    # Packing
    pack_sec, pack_dbg = estimate_pack_seconds(lines, dw_by_code, params, summer_mode)

    total_sec = overhead_sec + travel_sec + pick_total + pack_sec

    # Convert to minutes for storage/output
    total_min = total_sec / 60.0
    travel_min = travel_sec / 60.0
    pick_min = pick_total / 60.0
    pack_min = pack_sec / 60.0
    overhead_min = overhead_sec / 60.0

    return {
        "invoice_no": invoice_no,
        "total_seconds": total_sec,
        "total_minutes": total_min,
        "breakdown_minutes": {
            "overhead": overhead_min,
            "travel": travel_min,
            "pick": pick_min,
            "pack": pack_min,
        },
        "debug": {
            "overhead_seconds": overhead_sec,
            "travel_debug": travel_dbg,
            "pack_debug": pack_dbg,
            "stops_ordered": [vars(s) for s in ordered_stops],
        },
        "lines": line_details,
        "summer_mode": summer_mode,
        "params_version": params.get("version", "unknown"),
        "calculated_at_utc": datetime.utcnow().isoformat(),
    }


def estimate_and_persist_invoice_time(invoice_no: str) -> Dict[str, Any]:
    """
    Compute and persist:
      - invoice_items.exp_time (minutes per line)
      - invoices.total_exp_time (minutes)
    Returns the same breakdown as estimate_invoice_time() for UI display.
    """
    from app import db  # local import
    from models import Invoice, InvoiceItem, DwItem

    session = db.session
    params = get_time_params(session)
    summer_mode = get_summer_mode(session)

    invoice = session.query(Invoice).filter_by(invoice_no=invoice_no).first()
    if not invoice:
        raise ValueError(f"Invoice not found: {invoice_no}")

    lines = session.query(InvoiceItem).filter_by(invoice_no=invoice_no).all()
    codes = [l.item_code for l in lines if l.item_code]
    dw_rows = session.query(DwItem).filter(DwItem.item_code_365.in_(codes)).all()
    dw_by_code = {d.item_code_365: d for d in dw_rows}

    # Route & travel
    stops = build_stops(lines, params)
    ordered_stops = order_stops_one_trip(stops, params)
    travel_sec, travel_dbg = estimate_travel_seconds(ordered_stops, params)

    overhead_cfg = params.get("overhead", {})
    overhead_sec = float(overhead_cfg.get("start_seconds", 0)) + float(overhead_cfg.get("end_seconds", 0))

    pick_total = 0.0
    line_details = []
    for line in lines:
        dw = dw_by_code.get(line.item_code)
        if not dw:
            class Dummy: pass
            dw = Dummy()
            setattr(dw, "wms_unit_type", getattr(line, "unit_type", None))
            setattr(dw, "wms_pick_difficulty", 2)
            setattr(dw, "wms_fragility", None)
            setattr(dw, "wms_spill_risk", None)
            setattr(dw, "wms_pressure_sensitivity", None)
            setattr(dw, "wms_temperature_sensitivity", None)

        sec, dbg = estimate_pick_seconds_for_line(line, dw, params, summer_mode)
        pick_total += sec

        # Persist per-line expected time in MINUTES (consistent)
        line.exp_time = float(sec / 60.0)
        line_details.append({"item_code": line.item_code, "minutes": line.exp_time, "debug": dbg})

    pack_sec, pack_dbg = estimate_pack_seconds(lines, dw_by_code, params, summer_mode)

    total_sec = overhead_sec + travel_sec + pick_total + pack_sec
    invoice.total_exp_time = float(total_sec / 60.0)

    session.commit()

    return {
        "invoice_no": invoice_no,
        "total_minutes": invoice.total_exp_time,
        "breakdown_minutes": {
            "overhead": overhead_sec / 60.0,
            "travel": travel_sec / 60.0,
            "pick": pick_total / 60.0,
            "pack": pack_sec / 60.0,
        },
        "debug": {
            "travel_debug": travel_dbg,
            "pack_debug": pack_dbg,
            "stops_ordered": [vars(s) for s in ordered_stops],
        },
        "lines": line_details,
        "summer_mode": summer_mode,
        "params_version": params.get("version", "unknown"),
        "persisted_at_utc": datetime.utcnow().isoformat(),
    }
