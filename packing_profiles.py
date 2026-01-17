"""
Packing profile computation for palletization.
Derives pallet roles and flags from OI classification data.
"""
import json
from timezone_utils import get_utc_now
from models import WmsPackingProfile


def derive_packing_profile_from_dw(dw_item):
    """
    Derive packing profile data from a DwItem's WMS attributes.
    Returns a dict with pallet_role, flags, and snapshot of key inputs.
    """
    flags = []

    unit_type = (dw_item.wms_unit_type or "item").lower()
    frag = (dw_item.wms_fragility or "NO").upper()
    press = (dw_item.wms_pressure_sensitivity or "low").lower()
    temp = (dw_item.wms_temperature_sensitivity or "normal").lower()
    spill = bool(dw_item.wms_spill_risk)
    box = (dw_item.wms_box_fit_rule or "MIDDLE").upper()
    stack = (dw_item.wms_stackability or "YES").upper()

    if frag in ("YES", "SEMI"):
        flags.append("FRAGILE" if frag == "YES" else "SEMI_FRAGILE")
    if press in ("medium", "high"):
        flags.append("CRUSHABLE" if press == "high" else "PRESSURE_MEDIUM")
    if spill:
        flags.append("SPILL_RISK")
    if temp in ("heat_sensitive", "cool_required"):
        flags.append(temp.upper())
    if box == "COOLER_BAG":
        flags.append("OFF_PALLET")

    if box == "COOLER_BAG" or temp == "cool_required":
        role = "OFF_PALLET"
    elif frag == "YES" or press == "high" or box == "TOP":
        role = "TOP_ONLY"
    elif spill or unit_type in ("case", "box") or box == "BOTTOM":
        role = "BASE"
    else:
        role = "MIDDLE"

    return {
        "pallet_role": role,
        "flags": flags,
        "unit_type": unit_type,
        "fragility": frag,
        "pressure_sensitivity": press,
        "stackability": stack,
        "temperature_sensitivity": temp,
        "spill_risk": spill,
        "box_fit_rule": box
    }


def upsert_packing_profile(db_session, dw_item):
    """
    Create or update the packing profile for a DwItem.
    Called during reclassification.
    """
    data = derive_packing_profile_from_dw(dw_item)
    row = db_session.get(WmsPackingProfile, dw_item.item_code_365)
    if not row:
        row = WmsPackingProfile(item_code_365=dw_item.item_code_365)
        db_session.add(row)

    row.pallet_role = data["pallet_role"]
    row.flags_json = json.dumps(data["flags"])
    row.unit_type = data["unit_type"]
    row.fragility = data["fragility"]
    row.pressure_sensitivity = data["pressure_sensitivity"]
    row.stackability = data["stackability"]
    row.temperature_sensitivity = data["temperature_sensitivity"]
    row.spill_risk = data["spill_risk"]
    row.box_fit_rule = data["box_fit_rule"]
    row.updated_at = get_utc_now()
