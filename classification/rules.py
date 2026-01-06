"""
Classification rules for Operational Intelligence.

Each compute_* function returns a tuple: (value, confidence, reason)
- value: The computed classification value (or None if ambiguous)
- confidence: Integer 0-100 representing classification confidence
- reason: Human-readable explanation for the classification
"""

import re
from typing import Tuple, Optional, Any
from .mappings import (
    LIQUID_CATEGORIES, GLASS_BOTTLE_CATEGORIES, FRAGILE_CATEGORIES,
    HEAT_SENSITIVE_CATEGORIES, HIGH_PRESSURE_SENSITIVITY_CATEGORIES,
    MEDIUM_PRESSURE_SENSITIVITY_CATEGORIES, ROUND_SHAPE_CATEGORIES,
    FLAT_SHAPE_CATEGORIES, LIQUID_KEYWORDS, FRAGILE_KEYWORDS,
    HEAT_SENSITIVE_KEYWORDS, UNIT_TYPE_MAP, ZONE_CATEGORY_MAP
)


def compute_unit_type(dw_item) -> Tuple[Optional[str], int, str]:
    """Compute unit type from attribute_1_code_365."""
    attr1 = dw_item.attribute_1_code_365
    
    if attr1 and attr1.upper() in UNIT_TYPE_MAP:
        unit_type = UNIT_TYPE_MAP[attr1.upper()]
        return (unit_type, 90, f"Unit type '{unit_type}' from attribute_1_code_365='{attr1}'")
    
    if attr1:
        return ('item', 60, f"Unknown attribute_1_code_365='{attr1}', defaulting to 'item'")
    
    return ('item', 40, "No attribute_1_code_365, defaulting to 'item'")


def compute_spill_risk(dw_item) -> Tuple[Optional[bool], int, str]:
    """Compute spill risk based on category and item name."""
    category = (dw_item.category_code_365 or '').upper()
    item_name = (dw_item.item_name or '').lower()
    
    if category in LIQUID_CATEGORIES:
        return (True, 90, f"Category '{category}' indicates liquid product")
    
    for keyword in LIQUID_KEYWORDS:
        if keyword in item_name:
            return (True, 75, f"Item name contains liquid keyword '{keyword}'")
    
    if re.search(r'\d+\s*ml\b', item_name) or re.search(r'\d+\s*l\b', item_name):
        return (True, 80, "Item name contains volume measurement (ml/L)")
    
    return (False, 50, "No liquid indicators found")


def compute_fragility(dw_item) -> Tuple[Optional[str], int, str]:
    """Compute fragility (YES, SEMI, NO) based on category and item name."""
    category = (dw_item.category_code_365 or '').upper()
    item_name = (dw_item.item_name or '').lower()
    
    if category in FRAGILE_CATEGORIES:
        fragility = FRAGILE_CATEGORIES[category]
        return (fragility, 90, f"Category '{category}' has known fragility '{fragility}'")
    
    if category in GLASS_BOTTLE_CATEGORIES:
        return ('YES', 85, f"Category '{category}' contains glass bottles")
    
    for keyword in FRAGILE_KEYWORDS:
        if keyword in item_name:
            return ('YES', 70, f"Item name contains fragile keyword '{keyword}'")
    
    return ('NO', 45, "No fragility indicators found")


def compute_pressure_sensitivity(dw_item) -> Tuple[Optional[str], int, str]:
    """Compute pressure sensitivity (low, medium, high)."""
    category = (dw_item.category_code_365 or '').upper()
    item_name = (dw_item.item_name or '').lower()
    
    if category in HIGH_PRESSURE_SENSITIVITY_CATEGORIES:
        return ('high', 90, f"Category '{category}' is highly pressure sensitive")
    
    if category in MEDIUM_PRESSURE_SENSITIVITY_CATEGORIES:
        return ('medium', 85, f"Category '{category}' is moderately pressure sensitive")
    
    if category in GLASS_BOTTLE_CATEGORIES:
        return ('medium', 80, f"Category '{category}' contains glass (pressure sensitive)")
    
    if any(k in item_name for k in ['chip', 'crisp', 'wafer']):
        return ('high', 75, "Item name indicates crushable product")
    
    return ('low', 50, "No high pressure sensitivity indicators")


def compute_stackability(dw_item, fragility: Optional[str] = None, 
                         pressure: Optional[str] = None) -> Tuple[Optional[str], int, str]:
    """Compute stackability based on fragility and pressure sensitivity."""
    if fragility == 'YES':
        return ('NO', 85, "Fragile items cannot be stacked")
    
    if pressure == 'high':
        return ('NO', 85, "High pressure sensitivity prevents stacking")
    
    if fragility == 'SEMI' or pressure == 'medium':
        return ('LIMITED', 75, "Semi-fragile or medium pressure allows limited stacking")
    
    if fragility is None or pressure is None:
        return ('YES', 40, "Missing fragility/pressure data, assuming stackable")
    
    return ('YES', 70, "No stacking restrictions identified")


def compute_temperature_sensitivity(dw_item) -> Tuple[Optional[str], int, str]:
    """Compute temperature sensitivity (normal, heat_sensitive, cool_required)."""
    category = (dw_item.category_code_365 or '').upper()
    item_name = (dw_item.item_name or '').lower()
    
    if category in HEAT_SENSITIVE_CATEGORIES:
        if category in {'FRO', 'ICE'}:
            return ('cool_required', 95, f"Category '{category}' requires cold storage")
        return ('heat_sensitive', 90, f"Category '{category}' is heat sensitive")
    
    for keyword in HEAT_SENSITIVE_KEYWORDS:
        if keyword in item_name:
            if 'frozen' in keyword or 'ice cream' in keyword:
                return ('cool_required', 80, f"Item name contains cold keyword '{keyword}'")
            return ('heat_sensitive', 75, f"Item name contains heat-sensitive keyword '{keyword}'")
    
    return ('normal', 60, "No temperature sensitivity indicators")


def compute_shape_type(dw_item) -> Tuple[Optional[str], int, str]:
    """Compute shape type (cubic, flat, round, irregular)."""
    category = (dw_item.category_code_365 or '').upper()
    item_name = (dw_item.item_name or '').lower()
    
    if category in ROUND_SHAPE_CATEGORIES:
        return ('round', 80, f"Category '{category}' typically has round/cylindrical products")
    
    if category in FLAT_SHAPE_CATEGORIES:
        return ('flat', 80, f"Category '{category}' typically has flat products")
    
    if any(k in item_name for k in ['bottle', 'can', 'jar', 'spray']):
        return ('round', 70, "Item name indicates cylindrical container")
    
    if any(k in item_name for k in ['set', 'kit', 'organizer', 'combo']):
        return ('irregular', 65, "Item name indicates multi-piece or irregular shape")
    
    return ('cubic', 55, "Default to cubic shape")


def compute_pick_difficulty(dw_item, fragility: Optional[str] = None,
                            pressure: Optional[str] = None) -> Tuple[Optional[int], int, str]:
    """Compute pick difficulty score (1-5)."""
    score = 2
    reasons = []
    confidence = 60
    
    weight = float(dw_item.item_weight or 0)
    if weight > 10:
        score += 2
        reasons.append("Heavy item (>10kg)")
        confidence = max(confidence, 70)
    elif weight > 5:
        score += 1
        reasons.append("Moderately heavy (>5kg)")
        confidence = max(confidence, 65)
    
    if fragility == 'YES':
        score += 1
        reasons.append("Fragile item")
        confidence = max(confidence, 70)
    
    if pressure == 'high':
        score += 1
        reasons.append("High pressure sensitivity")
        confidence = max(confidence, 70)
    
    score = min(5, max(1, score))
    
    if not reasons:
        reasons.append("Standard picking difficulty")
    
    return (score, confidence, "; ".join(reasons))


def compute_shelf_height(dw_item) -> Tuple[Optional[str], int, str]:
    """Compute recommended shelf height (LOW, MID, HIGH)."""
    weight = float(dw_item.item_weight or 0)
    
    if weight > 8:
        return ('LOW', 70, "Heavy item (>8kg) should be on low shelf")
    
    if weight > 4:
        return ('MID', 60, "Medium weight item")
    
    return (None, 35, "Weight data insufficient for shelf height recommendation")


def compute_box_fit_rule(dw_item, fragility: Optional[str] = None,
                         spill_risk: Optional[bool] = None,
                         pressure: Optional[str] = None,
                         temperature: Optional[str] = None,
                         summer_mode: bool = False) -> Tuple[Optional[str], int, str]:
    """Compute box fit rule (BOTTOM, MIDDLE, TOP, COOLER_BAG)."""
    reasons = []
    
    if temperature in ('heat_sensitive', 'cool_required') and summer_mode:
        return ('COOLER_BAG', 90, "Heat/cool sensitive item in summer mode")
    
    if temperature == 'cool_required':
        return ('COOLER_BAG', 85, "Item requires cool storage")
    
    weight = float(dw_item.item_weight or 0)
    if spill_risk and weight > 2:
        return ('BOTTOM', 85, "Heavy liquid should go at bottom")
    
    if fragility == 'YES':
        return ('TOP', 85, "Fragile item should go on top")
    
    if pressure == 'high':
        return ('TOP', 80, "Pressure-sensitive item should go on top")
    
    if spill_risk:
        return ('BOTTOM', 70, "Liquid item should go at bottom")
    
    if fragility is None and pressure is None:
        return (None, 40, "Missing data for box-fit determination")
    
    return ('MIDDLE', 65, "Standard item goes in middle")


def compute_zone(dw_item, temperature: Optional[str] = None) -> Tuple[Optional[str], int, str]:
    """Compute warehouse zone (MAIN, SENSITIVE, SNACKS, CROSS_SHIPPING)."""
    category = (dw_item.category_code_365 or '').upper()
    
    if category in ZONE_CATEGORY_MAP:
        zone = ZONE_CATEGORY_MAP[category]
        return (zone, 85, f"Category '{category}' maps to zone '{zone}'")
    
    if temperature in ('heat_sensitive', 'cool_required'):
        return ('SENSITIVE', 80, "Temperature-sensitive item goes to SENSITIVE zone")
    
    return ('MAIN', 60, "Default zone assignment")
