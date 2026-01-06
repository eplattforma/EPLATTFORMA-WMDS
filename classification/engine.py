"""
Classification engine for Operational Intelligence.

Orchestrates the reclassification process:
1. Loads active items from DwItem
2. Fetches category defaults and SKU overrides
3. Computes attributes using rules
4. Resolves final values using precedence
5. Stores results and audit trail
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

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

logger = logging.getLogger(__name__)


def reclassify_items(run_by: str, threshold: int = 60, 
                     summer_mode: bool = False) -> Dict[str, Any]:
    """
    Main classification engine entry point.
    
    Args:
        run_by: Username of admin running classification
        threshold: Confidence threshold (default 60 for moderate mode)
        summer_mode: Whether to apply summer mode rules (cooler bags for heat-sensitive)
    
    Returns:
        Dictionary with run statistics
    """
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
        
        active_items = DwItem.query.filter(DwItem.active == True).all()
        
        items_scanned = len(active_items)
        items_updated = 0
        items_needing_review = 0
        
        for item in active_items:
            updated = classify_single_item(
                item, 
                category_defaults.get(item.category_code_365),
                item_overrides.get(item.item_code_365),
                threshold,
                summer_mode
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
        run.notes = f"Completed successfully. Threshold: {threshold}, Summer mode: {summer_mode}"
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


def classify_single_item(item: DwItem, 
                         category_default: Optional[WmsCategoryDefault],
                         item_override: Optional[WmsItemOverride],
                         threshold: int = 60,
                         summer_mode: bool = False) -> bool:
    """
    Classify a single item and update its wms_* fields.
    
    Returns True if any field was updated.
    """
    evidence = {}
    any_updated = False
    
    unit_val, unit_conf, unit_reason = compute_unit_type(item)
    final_unit, final_unit_conf, final_unit_reason, unit_source = resolve_attribute(
        'unit_type', unit_val, unit_conf, unit_reason,
        get_override_value(item_override, 'unit_type'),
        None,
        threshold
    )
    evidence['unit_type'] = {
        'value': final_unit, 'confidence': final_unit_conf, 
        'reason': final_unit_reason, 'source': unit_source
    }
    
    spill_val, spill_conf, spill_reason = compute_spill_risk(item)
    final_spill, final_spill_conf, final_spill_reason, spill_source = resolve_attribute(
        'spill_risk', spill_val, spill_conf, spill_reason,
        get_override_value(item_override, 'spill_risk'),
        get_default_value(category_default, 'spill_risk'),
        threshold
    )
    evidence['spill_risk'] = {
        'value': final_spill, 'confidence': final_spill_conf,
        'reason': final_spill_reason, 'source': spill_source
    }
    
    frag_val, frag_conf, frag_reason = compute_fragility(item)
    final_frag, final_frag_conf, final_frag_reason, frag_source = resolve_attribute(
        'fragility', frag_val, frag_conf, frag_reason,
        get_override_value(item_override, 'fragility'),
        get_default_value(category_default, 'fragility'),
        threshold
    )
    evidence['fragility'] = {
        'value': final_frag, 'confidence': final_frag_conf,
        'reason': final_frag_reason, 'source': frag_source
    }
    
    press_val, press_conf, press_reason = compute_pressure_sensitivity(item)
    final_press, final_press_conf, final_press_reason, press_source = resolve_attribute(
        'pressure_sensitivity', press_val, press_conf, press_reason,
        get_override_value(item_override, 'pressure_sensitivity'),
        get_default_value(category_default, 'pressure_sensitivity'),
        threshold
    )
    evidence['pressure_sensitivity'] = {
        'value': final_press, 'confidence': final_press_conf,
        'reason': final_press_reason, 'source': press_source
    }
    
    stack_val, stack_conf, stack_reason = compute_stackability(item, final_frag, final_press)
    final_stack, final_stack_conf, final_stack_reason, stack_source = resolve_attribute(
        'stackability', stack_val, stack_conf, stack_reason,
        get_override_value(item_override, 'stackability'),
        get_default_value(category_default, 'stackability'),
        threshold
    )
    evidence['stackability'] = {
        'value': final_stack, 'confidence': final_stack_conf,
        'reason': final_stack_reason, 'source': stack_source
    }
    
    temp_val, temp_conf, temp_reason = compute_temperature_sensitivity(item)
    final_temp, final_temp_conf, final_temp_reason, temp_source = resolve_attribute(
        'temperature_sensitivity', temp_val, temp_conf, temp_reason,
        get_override_value(item_override, 'temperature_sensitivity'),
        get_default_value(category_default, 'temperature_sensitivity'),
        threshold
    )
    evidence['temperature_sensitivity'] = {
        'value': final_temp, 'confidence': final_temp_conf,
        'reason': final_temp_reason, 'source': temp_source
    }
    
    shape_val, shape_conf, shape_reason = compute_shape_type(item)
    final_shape, final_shape_conf, final_shape_reason, shape_source = resolve_attribute(
        'shape_type', shape_val, shape_conf, shape_reason,
        get_override_value(item_override, 'shape_type'),
        get_default_value(category_default, 'shape_type'),
        threshold
    )
    evidence['shape_type'] = {
        'value': final_shape, 'confidence': final_shape_conf,
        'reason': final_shape_reason, 'source': shape_source
    }
    
    diff_val, diff_conf, diff_reason = compute_pick_difficulty(item, final_frag, final_press)
    final_diff, final_diff_conf, final_diff_reason, diff_source = resolve_attribute(
        'pick_difficulty', diff_val, diff_conf, diff_reason,
        get_override_value(item_override, 'pick_difficulty'),
        get_default_value(category_default, 'pick_difficulty'),
        threshold
    )
    evidence['pick_difficulty'] = {
        'value': final_diff, 'confidence': final_diff_conf,
        'reason': final_diff_reason, 'source': diff_source
    }
    
    shelf_val, shelf_conf, shelf_reason = compute_shelf_height(item)
    final_shelf, final_shelf_conf, final_shelf_reason, shelf_source = resolve_attribute(
        'shelf_height', shelf_val, shelf_conf, shelf_reason,
        get_override_value(item_override, 'shelf_height'),
        get_default_value(category_default, 'shelf_height'),
        threshold
    )
    evidence['shelf_height'] = {
        'value': final_shelf, 'confidence': final_shelf_conf,
        'reason': final_shelf_reason, 'source': shelf_source
    }
    
    box_val, box_conf, box_reason = compute_box_fit_rule(
        item, final_frag, final_spill, final_press, final_temp, summer_mode
    )
    final_box, final_box_conf, final_box_reason, box_source = resolve_attribute(
        'box_fit_rule', box_val, box_conf, box_reason,
        get_override_value(item_override, 'box_fit_rule'),
        get_default_value(category_default, 'box_fit_rule'),
        threshold
    )
    evidence['box_fit_rule'] = {
        'value': final_box, 'confidence': final_box_conf,
        'reason': final_box_reason, 'source': box_source
    }
    
    zone_val, zone_conf, zone_reason = compute_zone(item, final_temp)
    final_zone, final_zone_conf, final_zone_reason, zone_source = resolve_attribute(
        'zone', zone_val, zone_conf, zone_reason,
        get_override_value(item_override, 'zone'),
        get_default_value(category_default, 'zone'),
        threshold
    )
    evidence['zone'] = {
        'value': final_zone, 'confidence': final_zone_conf,
        'reason': final_zone_reason, 'source': zone_source
    }
    
    overall_conf = calculate_overall_confidence(evidence)
    class_source = determine_class_source(evidence)
    class_notes = build_class_notes(evidence, overall_conf)
    
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
