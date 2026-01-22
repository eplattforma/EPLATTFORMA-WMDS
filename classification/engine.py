"""
Classification engine for Operational Intelligence.

Orchestrates the reclassification process:
1. Loads active items from DwItem
2. Fetches category defaults, SKU overrides, and dynamic rules
3. Computes attributes using rules
4. Resolves final values using precedence
5. Stores results and audit trail
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

from sqlalchemy import or_

from app import db
from models import DwItem, WmsCategoryDefault, WmsItemOverride, WmsClassificationRun

from .rules import (
    compute_unit_type, compute_fragility, compute_spill_risk,
    compute_pressure_sensitivity, compute_stackability,
    compute_temperature_sensitivity, compute_shape_type,
    compute_pick_difficulty, compute_shelf_height,
    compute_box_fit_rule, compute_zone
)
from .resolver import (
    resolve_attribute, get_override_value, get_default_value,
    calculate_overall_confidence, determine_class_source, build_class_notes
)
from .dynamic_rules import load_active_rules, match_best_rule
from packing_profiles import upsert_packing_profile

logger = logging.getLogger(__name__)


def reclassify_items(
    run_by: str,
    threshold: int = 60,
    summer_mode: bool = False,
    item_codes: Optional[List[str]] = None,
    category_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main classification engine entry point.
    
    Args:
        run_by: Username of admin running classification
        threshold: Confidence threshold (default 60 for moderate mode)
        summer_mode: Whether to apply summer mode rules (cooler bags for heat-sensitive)
        item_codes: Optional list of specific item codes to reclassify
        category_code: Optional category code to filter items (use "__EMPTY__" for items without category)
    
    Returns:
        Dictionary with run statistics
    """
    scope = "ALL"
    if item_codes:
        scope = f"ITEMS:{len(item_codes)}"
    elif category_code is not None:
        scope = f"CATEGORY:{category_code}"
    
    run = WmsClassificationRun(
        started_at=datetime.utcnow(),
        run_by=run_by,
        mode=f'moderate_{threshold}'
    )
    db.session.add(run)
    db.session.flush()
    
    try:
        category_defaults = {
            d.category_code_365: d 
            for d in WmsCategoryDefault.query.filter_by(is_active=True).all()
        }
        
        item_overrides = {
            o.item_code_365: o 
            for o in WmsItemOverride.query.filter_by(is_active=True).all()
        }
        
        # Load dynamic rules once for all items
        dynamic_rules_by_attr = load_active_rules()
        
        # Build query with optional filters
        q = DwItem.query.filter(DwItem.active == True)
        
        if item_codes:
            q = q.filter(DwItem.item_code_365.in_(item_codes))
        elif category_code is not None:
            if category_code == "__EMPTY__":
                q = q.filter(or_(DwItem.category_code_365.is_(None), DwItem.category_code_365 == ""))
            else:
                q = q.filter(DwItem.category_code_365 == category_code)
        
        active_items = q.all()
        
        items_scanned = len(active_items)
        items_updated = 0
        items_needing_review = 0
        
        for item in active_items:
            cat_code = item.category_code_365 or "__EMPTY__"
            cat_default = category_defaults.get(cat_code)
            item_override = item_overrides.get(item.item_code_365)
            updated = classify_single_item(
                item, 
                cat_default,
                item_override,
                dynamic_rules_by_attr,
                threshold,
                summer_mode
            )
            
            # Get dynamic rule matches for pack_mode and pallet_role
            dyn_pack = match_best_rule(item, 'pack_mode', dynamic_rules_by_attr)
            dyn_role = match_best_rule(item, 'pallet_role', dynamic_rules_by_attr)
            
            upsert_packing_profile(
                db.session, item, cat_default, item_override,
                dynamic_pack_mode=(dyn_pack['value'] if dyn_pack else None),
                dynamic_pallet_role=(dyn_role['value'] if dyn_role else None),
            )
            
            if updated:
                items_updated += 1
            
            if item.needs_review():
                items_needing_review += 1
        
        db.session.commit()
        
        run.finished_at = datetime.utcnow()
        run.active_items_scanned = items_scanned
        run.items_updated = items_updated
        run.items_needing_review = items_needing_review
        run.notes = f"Scope={scope}; threshold={threshold}; summer_mode={summer_mode}; Completed successfully"
        db.session.commit()
        
        logger.info(f"Classification complete: {items_scanned} scanned, {items_updated} updated, {items_needing_review} need review")
        
        return {
            'success': True,
            'run_id': run.id,
            'items_scanned': items_scanned,
            'items_updated': items_updated,
            'items_needing_review': items_needing_review,
            'run_by': run_by,
            'threshold': threshold
        }
        
    except Exception as e:
        db.session.rollback()
        run.finished_at = datetime.utcnow()
        run.notes = f"Failed: {str(e)}"
        db.session.commit()
        
        logger.error(f"Classification failed: {e}")
        raise


def _resolve_with_dynamic(attr_name: str, computed_val, computed_conf: int, 
                          computed_reason: str, item, item_override, 
                          category_default, dynamic_rules_by_attr: Dict,
                          threshold: int, skip_category_default: bool = False) -> Dict:
    """
    Helper to resolve an attribute with dynamic rule support.
    
    Returns dict with: value, confidence, reason, source, and optional meta
    """
    # Check for dynamic rule match
    dyn = match_best_rule(item, attr_name, dynamic_rules_by_attr)
    
    cat_default = None if skip_category_default else get_default_value(category_default, attr_name)
    
    final_val, final_conf, final_reason, source, meta = resolve_attribute(
        attr_name, computed_val, computed_conf, computed_reason,
        get_override_value(item_override, attr_name),
        cat_default,
        threshold,
        dynamic_value=(dyn['value'] if dyn else None),
        dynamic_conf=(dyn['confidence'] if dyn else 0),
        dynamic_reason=(dyn['reason'] if dyn else ""),
        dynamic_rule_id=(dyn['rule_id'] if dyn else None),
        dynamic_rule_name=(dyn['rule_name'] if dyn else None),
    )
    
    result = {
        'value': final_val,
        'confidence': final_conf,
        'reason': final_reason,
        'source': source
    }
    result.update(meta)
    return result


def classify_single_item(item: DwItem, 
                         category_default: Optional[WmsCategoryDefault],
                         item_override: Optional[WmsItemOverride],
                         dynamic_rules_by_attr: Dict,
                         threshold: int = 60,
                         summer_mode: bool = False) -> bool:
    """
    Classify a single item and update its wms_* fields.
    
    Returns True if any field was updated.
    """
    evidence = {}
    any_updated = False
    
    # Unit type (no category default)
    unit_val, unit_conf, unit_reason = compute_unit_type(item)
    evidence['unit_type'] = _resolve_with_dynamic(
        'unit_type', unit_val, unit_conf, unit_reason,
        item, item_override, category_default, dynamic_rules_by_attr,
        threshold, skip_category_default=True
    )
    final_unit = evidence['unit_type']['value']
    
    # Spill risk
    spill_val, spill_conf, spill_reason = compute_spill_risk(item)
    evidence['spill_risk'] = _resolve_with_dynamic(
        'spill_risk', spill_val, spill_conf, spill_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_spill = evidence['spill_risk']['value']
    
    # Fragility
    frag_val, frag_conf, frag_reason = compute_fragility(item)
    evidence['fragility'] = _resolve_with_dynamic(
        'fragility', frag_val, frag_conf, frag_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_frag = evidence['fragility']['value']
    
    # Pressure sensitivity
    press_val, press_conf, press_reason = compute_pressure_sensitivity(item)
    evidence['pressure_sensitivity'] = _resolve_with_dynamic(
        'pressure_sensitivity', press_val, press_conf, press_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_press = evidence['pressure_sensitivity']['value']
    
    # Stackability (depends on fragility and pressure)
    stack_val, stack_conf, stack_reason = compute_stackability(item, final_frag, final_press)
    evidence['stackability'] = _resolve_with_dynamic(
        'stackability', stack_val, stack_conf, stack_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_stack = evidence['stackability']['value']
    
    # Temperature sensitivity
    temp_val, temp_conf, temp_reason = compute_temperature_sensitivity(item)
    evidence['temperature_sensitivity'] = _resolve_with_dynamic(
        'temperature_sensitivity', temp_val, temp_conf, temp_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_temp = evidence['temperature_sensitivity']['value']
    
    # Shape type
    shape_val, shape_conf, shape_reason = compute_shape_type(item)
    evidence['shape_type'] = _resolve_with_dynamic(
        'shape_type', shape_val, shape_conf, shape_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_shape = evidence['shape_type']['value']
    
    # Pick difficulty (depends on fragility and pressure)
    diff_val, diff_conf, diff_reason = compute_pick_difficulty(item, final_frag, final_press)
    evidence['pick_difficulty'] = _resolve_with_dynamic(
        'pick_difficulty', diff_val, diff_conf, diff_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_diff = evidence['pick_difficulty']['value']
    
    # Shelf height
    shelf_val, shelf_conf, shelf_reason = compute_shelf_height(item)
    evidence['shelf_height'] = _resolve_with_dynamic(
        'shelf_height', shelf_val, shelf_conf, shelf_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_shelf = evidence['shelf_height']['value']
    
    # Box fit rule (depends on multiple attributes)
    box_val, box_conf, box_reason = compute_box_fit_rule(
        item, final_frag, final_spill, final_press, final_temp, summer_mode
    )
    evidence['box_fit_rule'] = _resolve_with_dynamic(
        'box_fit_rule', box_val, box_conf, box_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_box = evidence['box_fit_rule']['value']
    
    # Zone (depends on temperature)
    zone_val, zone_conf, zone_reason = compute_zone(item, final_temp)
    evidence['zone'] = _resolve_with_dynamic(
        'zone', zone_val, zone_conf, zone_reason,
        item, item_override, category_default, dynamic_rules_by_attr, threshold
    )
    final_zone = evidence['zone']['value']
    
    # Calculate overall metrics
    overall_conf = calculate_overall_confidence(evidence)
    class_source = determine_class_source(evidence)
    class_notes = build_class_notes(evidence, overall_conf)
    
    # Update item fields
    if item.wms_unit_type != final_unit:
        item.wms_unit_type = final_unit
        any_updated = True
    if item.wms_spill_risk != final_spill:
        item.wms_spill_risk = final_spill
        any_updated = True
    if item.wms_fragility != final_frag:
        item.wms_fragility = final_frag
        any_updated = True
    if item.wms_pressure_sensitivity != final_press:
        item.wms_pressure_sensitivity = final_press
        any_updated = True
    if item.wms_stackability != final_stack:
        item.wms_stackability = final_stack
        any_updated = True
    if item.wms_temperature_sensitivity != final_temp:
        item.wms_temperature_sensitivity = final_temp
        any_updated = True
    if item.wms_shape_type != final_shape:
        item.wms_shape_type = final_shape
        any_updated = True
    if item.wms_pick_difficulty != final_diff:
        item.wms_pick_difficulty = final_diff
        any_updated = True
    if item.wms_shelf_height != final_shelf:
        item.wms_shelf_height = final_shelf
        any_updated = True
    if item.wms_box_fit_rule != final_box:
        item.wms_box_fit_rule = final_box
        any_updated = True
    if item.wms_zone != final_zone:
        item.wms_zone = final_zone
        any_updated = True
    
    item.wms_class_confidence = overall_conf
    item.wms_class_source = class_source
    item.wms_class_notes = class_notes
    item.wms_classified_at = datetime.utcnow()
    item.wms_class_evidence = json.dumps(evidence)
    
    return any_updated
