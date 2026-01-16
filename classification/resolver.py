"""
Resolution logic for Operational Intelligence classification.

Implements the precedence hierarchy:
1. SKU override (highest priority) - MANUAL
2. Dynamic rule match - DYNAMIC_RULE
3. Category default - CATEGORY_DEFAULT
4. Computed rule result (only if confidence >= threshold) - RULES
5. NULL (if no confident value available)
"""

from typing import Tuple, Any, Optional, Dict


def resolve_attribute(
    attr_name: str,
    computed_value: Any,
    computed_conf: int,
    computed_reason: str,
    item_override_value: Optional[Any],
    category_default_value: Optional[Any],
    threshold: int = 60,
    dynamic_value: Any = None,
    dynamic_conf: int = 0,
    dynamic_reason: str = "",
    dynamic_rule_id: Optional[int] = None,
    dynamic_rule_name: Optional[str] = None
) -> Tuple[Any, int, str, str, Dict]:
    """
    Resolve final attribute value based on precedence.
    
    Returns: (final_value, confidence, reason, source, meta)
    - source: 'MANUAL', 'DYNAMIC_RULE', 'CATEGORY_DEFAULT', or 'RULES'
    - meta: dict with additional info (e.g., rule_id, rule_name for dynamic rules)
    """
    # Priority 1: Manual SKU override
    if item_override_value is not None:
        return (
            item_override_value,
            100,
            f"MANUAL override for {attr_name}",
            'MANUAL',
            {}
        )
    
    # Priority 2: Dynamic rule match
    if dynamic_value is not None:
        meta = {}
        if dynamic_rule_id:
            meta['rule_id'] = dynamic_rule_id
        if dynamic_rule_name:
            meta['rule_name'] = dynamic_rule_name
        return (
            dynamic_value,
            dynamic_conf,
            dynamic_reason,
            'DYNAMIC_RULE',
            meta
        )
    
    # Priority 3: Category default
    if category_default_value is not None:
        return (
            category_default_value,
            85,
            f"CATEGORY default for {attr_name}",
            'CATEGORY_DEFAULT',
            {}
        )
    
    # Priority 4: Computed rules (only if confidence >= threshold)
    if computed_conf >= threshold:
        return (
            computed_value,
            computed_conf,
            computed_reason,
            'RULES',
            {}
        )
    
    # Priority 5: NULL (ambiguous)
    return (
        None,
        computed_conf,
        f"AMBIGUOUS (<{threshold}) – {computed_reason}",
        'RULES',
        {}
    )


def get_override_value(override_obj, attr_name: str) -> Optional[Any]:
    """Get override value for an attribute from WmsItemOverride object."""
    if override_obj is None:
        return None
    
    override_field = f"{attr_name}_override"
    return getattr(override_obj, override_field, None)


def get_default_value(default_obj, attr_name: str) -> Optional[Any]:
    """Get default value for an attribute from WmsCategoryDefault object."""
    if default_obj is None:
        return None
    
    if not getattr(default_obj, 'is_active', True):
        return None
    
    default_field = f"default_{attr_name}"
    return getattr(default_obj, default_field, None)


def calculate_overall_confidence(evidence: dict) -> int:
    """
    Calculate overall classification confidence as average of stored critical attributes.
    
    Critical attributes: fragility, spill_risk, pressure_sensitivity, 
                        temperature_sensitivity, box_fit_rule
    """
    critical_attrs = [
        'fragility', 'spill_risk', 'pressure_sensitivity',
        'temperature_sensitivity', 'box_fit_rule'
    ]
    
    confidences = []
    for attr in critical_attrs:
        if attr in evidence and evidence[attr].get('value') is not None:
            confidences.append(evidence[attr].get('confidence', 0))
    
    if not confidences:
        return 0
    
    return int(sum(confidences) / len(confidences))


def determine_class_source(evidence: dict) -> str:
    """
    Determine overall classification source based on evidence.
    
    Precedence: MANUAL > DYNAMIC_RULE > CATEGORY_DEFAULT > RULES
    """
    has_manual = False
    has_dynamic = False
    has_default = False
    
    for attr, data in evidence.items():
        source = data.get('source', 'RULES')
        if source == 'MANUAL':
            has_manual = True
        elif source == 'DYNAMIC_RULE':
            has_dynamic = True
        elif source == 'CATEGORY_DEFAULT':
            has_default = True
    
    if has_manual:
        return 'MANUAL'
    if has_dynamic:
        return 'DYNAMIC_RULE'
    if has_default:
        return 'CATEGORY_DEFAULT'
    return 'RULES'


def build_class_notes(evidence: dict, overall_confidence: int) -> str:
    """Build human-readable summary of classification."""
    notes_parts = []
    
    notes_parts.append(f"Overall confidence: {overall_confidence}%")
    
    sources = set(data.get('source', 'RULES') for data in evidence.values())
    if 'MANUAL' in sources:
        notes_parts.append("Contains manual overrides")
    if 'DYNAMIC_RULE' in sources:
        notes_parts.append("Uses dynamic rules")
    if 'CATEGORY_DEFAULT' in sources:
        notes_parts.append("Uses category defaults")
    
    low_conf_attrs = [
        attr for attr, data in evidence.items()
        if data.get('confidence', 100) < 60 and data.get('value') is None
    ]
    if low_conf_attrs:
        notes_parts.append(f"Ambiguous: {', '.join(low_conf_attrs)}")
    
    return ". ".join(notes_parts)
