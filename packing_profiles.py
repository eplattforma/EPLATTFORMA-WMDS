"""
Packing profile computation for palletization.
Derives pallet roles, pack modes and flags from OI classification data.
"""
import json
from decimal import Decimal
from timezone_utils import get_utc_now
from models import WmsPackingProfile


def derive_pack_mode(dw_item):
    """
    Derive pack_mode and carton hints from DwItem's WMS attributes.
    Returns dict with pack_mode, carton_type_hint, loss_risk, max_carton_weight_kg.
    
    Pack modes:
    - OFF_PALLET: cooler bag or cool required items
    - DIRECT_PALLET: cases/boxes that can go directly on pallet
    - CARTON_HEAVY: spill risk, bottles, sensitive zone items (heavy cartons)
    - CARTON_SMALL: small items that need carton protection
    """
    unit_type = (dw_item.wms_unit_type or "item").lower()
    press = (dw_item.wms_pressure_sensitivity or "low").lower()
    temp = (dw_item.wms_temperature_sensitivity or "normal").lower()
    spill = bool(dw_item.wms_spill_risk)
    box = (dw_item.wms_box_fit_rule or "MIDDLE").upper()
    stack = (dw_item.wms_stackability or "YES").upper()
    shape = (dw_item.wms_shape_type or "").lower()
    zone = (dw_item.wms_zone or "").upper()
    
    pack_mode = None
    carton_type_hint = None
    loss_risk = False
    max_carton_weight_kg = Decimal("18.0")
    
    if box == "COOLER_BAG" or temp == "cool_required":
        pack_mode = "OFF_PALLET"
    elif unit_type in ("box", "pack", "case", "virtual_pack") and press != "high" and stack != "NO":
        pack_mode = "DIRECT_PALLET"
    elif spill or shape in ("bottle", "jug") or zone == "SENSITIVE":
        pack_mode = "CARTON_HEAVY"
        carton_type_hint = "HEAVY"
        max_carton_weight_kg = Decimal("14.0") if spill else Decimal("18.0")
    else:
        pack_mode = "CARTON_SMALL"
        carton_type_hint = "SMALL"
        if unit_type == "item":
            loss_risk = True
    
    return {
        "pack_mode": pack_mode,
        "carton_type_hint": carton_type_hint,
        "loss_risk": loss_risk,
        "max_carton_weight_kg": max_carton_weight_kg
    }


def derive_packing_profile_from_dw(dw_item):
    """
    Derive packing profile data from a DwItem's WMS attributes.
    Returns a dict with pallet_role, flags, pack_mode, and snapshot of key inputs.
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

    pack_data = derive_pack_mode(dw_item)
    
    return {
        "pallet_role": role,
        "flags": flags,
        "unit_type": unit_type,
        "fragility": frag,
        "pressure_sensitivity": press,
        "stackability": stack,
        "temperature_sensitivity": temp,
        "spill_risk": spill,
        "box_fit_rule": box,
        "pack_mode": pack_data["pack_mode"],
        "carton_type_hint": pack_data["carton_type_hint"],
        "loss_risk": pack_data["loss_risk"],
        "max_carton_weight_kg": pack_data["max_carton_weight_kg"]
    }


def upsert_packing_profile(db_session, dw_item, category_default=None):
    """
    Create or update the packing profile for a DwItem.
    Called during reclassification.
    
    category_default: WmsCategoryDefault object, if any, to apply category pack_mode override.
    """
    data = derive_packing_profile_from_dw(dw_item)
    
    # Apply category default pack_mode if set (overrides computed pack_mode)
    if category_default and category_default.default_pack_mode:
        data["pack_mode"] = category_default.default_pack_mode
        # Adjust carton hints based on forced pack_mode
        if data["pack_mode"] == "CARTON_HEAVY":
            data["carton_type_hint"] = "HEAVY"
            data["max_carton_weight_kg"] = Decimal("16.0")
        elif data["pack_mode"] == "CARTON_SMALL":
            data["carton_type_hint"] = "SMALL"
            data["max_carton_weight_kg"] = Decimal("18.0")
        elif data["pack_mode"] in ("DIRECT_PALLET", "OFF_PALLET"):
            data["carton_type_hint"] = None
            data["max_carton_weight_kg"] = None
    
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
    row.pack_mode = data["pack_mode"]
    row.loss_risk = data["loss_risk"]
    row.carton_type_hint = data["carton_type_hint"]
    row.max_carton_weight_kg = data["max_carton_weight_kg"]
    row.updated_at = get_utc_now()
