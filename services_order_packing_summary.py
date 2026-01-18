"""
Order-level packing summary service.
Provides aggregated packing guidance for pickers based on pack_mode.
"""
from decimal import Decimal
from math import ceil
from app import db
from models import InvoiceItem, WmsPackingProfile, DwItem


def get_order_packing_summary(invoice_no):
    """
    Compute packing summary for an order.
    Returns aggregated totals by pack_mode with carton estimates and warnings.
    
    Returns dict with:
    - direct: {weight_kg, lines, qty} - direct to pallet items
    - heavy: {weight_kg, lines, cartons_planned} - heavy carton items  
    - small: {qty, lines, cartons_planned} - small carton items
    - off_pallet: {weight_kg, lines} - cooler bag items
    - warnings: list of warning types present
    - base: {weight_kg, lines, corridors} - base layer items (corridors 09-12)
    - cartons_heavy_planned: int
    - cartons_small_planned: int
    """
    items = db.session.query(
        InvoiceItem.item_code,
        InvoiceItem.qty,
        InvoiceItem.line_weight,
        InvoiceItem.location,
        WmsPackingProfile.pack_mode,
        WmsPackingProfile.carton_type_hint,
        WmsPackingProfile.loss_risk,
        WmsPackingProfile.max_carton_weight_kg,
        WmsPackingProfile.spill_risk,
        WmsPackingProfile.fragility,
        WmsPackingProfile.pressure_sensitivity,
        WmsPackingProfile.temperature_sensitivity
    ).outerjoin(
        WmsPackingProfile,
        InvoiceItem.item_code == WmsPackingProfile.item_code_365
    ).filter(
        InvoiceItem.invoice_no == invoice_no
    ).all()
    
    direct = {"weight_kg": Decimal("0"), "lines": 0, "qty": 0}
    heavy = {"weight_kg": Decimal("0"), "lines": 0}
    small = {"qty": 0, "lines": 0}
    off_pallet = {"weight_kg": Decimal("0"), "lines": 0}
    base = {"weight_kg": Decimal("0"), "lines": 0, "corridors": set()}
    
    warnings = {
        "spill_risk": 0,
        "fragile": 0,
        "crushable": 0,
        "heat_sensitive": 0,
        "cool_required": 0,
        "loss_risk": 0
    }
    
    has_spill_risk = False
    
    for item in items:
        item_code, qty, weight, location, pack_mode, carton_hint, loss_risk, max_weight, spill, frag, press, temp = item
        
        qty = qty or 0
        weight = Decimal(str(weight or 0))
        pack_mode = pack_mode or "CARTON_SMALL"
        
        corridor = None
        if location and len(location) >= 2:
            corridor = location[:2]
            if corridor in ("09", "10", "11", "12"):
                base["weight_kg"] += weight
                base["lines"] += 1
                base["corridors"].add(corridor)
        
        if pack_mode == "DIRECT_PALLET":
            direct["weight_kg"] += weight
            direct["lines"] += 1
            direct["qty"] += int(qty)
        elif pack_mode == "CARTON_HEAVY":
            heavy["weight_kg"] += weight
            heavy["lines"] += 1
        elif pack_mode == "OFF_PALLET":
            off_pallet["weight_kg"] += weight
            off_pallet["lines"] += 1
        else:
            small["qty"] += int(qty)
            small["lines"] += 1
        
        if spill:
            warnings["spill_risk"] += 1
            has_spill_risk = True
        if frag and frag.upper() in ("YES", "SEMI"):
            warnings["fragile"] += 1
        if press and press.lower() == "high":
            warnings["crushable"] += 1
        if temp:
            if temp.lower() == "heat_sensitive":
                warnings["heat_sensitive"] += 1
            elif temp.lower() == "cool_required":
                warnings["cool_required"] += 1
        if loss_risk:
            warnings["loss_risk"] += 1
    
    target_heavy_kg = Decimal("14") if has_spill_risk else Decimal("16")
    cartons_heavy = int(ceil(float(heavy["weight_kg"]) / float(target_heavy_kg))) if heavy["weight_kg"] > 0 else 0
    
    cartons_small = int(ceil(small["qty"] / 25)) if small["qty"] > 0 else 0
    
    active_warnings = [w for w, count in warnings.items() if count > 0]
    
    base["corridors"] = sorted(list(base["corridors"]))
    
    return {
        "direct": {
            "weight_kg": float(direct["weight_kg"]),
            "lines": direct["lines"],
            "qty": direct["qty"]
        },
        "heavy": {
            "weight_kg": float(heavy["weight_kg"]),
            "lines": heavy["lines"]
        },
        "small": {
            "qty": small["qty"],
            "lines": small["lines"]
        },
        "off_pallet": {
            "weight_kg": float(off_pallet["weight_kg"]),
            "lines": off_pallet["lines"]
        },
        "base": {
            "weight_kg": float(base["weight_kg"]),
            "lines": base["lines"],
            "corridors": base["corridors"]
        },
        "warnings": warnings,
        "active_warnings": active_warnings,
        "cartons_heavy_planned": cartons_heavy,
        "cartons_small_planned": cartons_small,
        "total_lines": len(items)
    }


def get_packing_summary_for_route(invoice_numbers):
    """
    Get packing summaries for multiple invoices efficiently.
    Returns dict keyed by invoice_no.
    """
    summaries = {}
    for inv_no in invoice_numbers:
        summaries[inv_no] = get_order_packing_summary(inv_no)
    return summaries
