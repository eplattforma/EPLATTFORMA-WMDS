"""
Resolution logic for Operational Intelligence classification.

Implements the precedence hierarchy:
1. SKU override (highest priority)
2. Category default
3. Computed rule result (only if confidence >= threshold)
4. NULL (if no confident value available)
"""

from typing import Tuple, Any, Optional


def resolve_attribute(
    attr_name: str,
    computed_value: Any,
    computed_conf: int,
    computed_reason: str,
    item_override_value: Optional[Any],
    category_default_value: Optional[Any],
    threshold: int = 60
) -> Tuple[Any, int, str, str]:
    """
    Resolve final attribute value based on precedence.
    
    Returns: (final_value, confidence, reason, source)
    - source: 'MANUAL', 'CATEGORY_DEFAULT', or 'RULES'
    """
    if item_override_value is not None:
        return (
            item_override_value,
            100,
            f"MANUAL override for {attr_name}",
            'MANUAL'
        )
    
    if category_default_value is not None:
        return (
            category_default_value,
            85,
            f"CATEGORY default for {attr_name}",
            'CATEGORY_DEFAULT'
        )
    
    if computed_conf >= threshold:
        return (
            computed_value,
            computed_conf,
            computed_reason,
            'RULES'
        )
    
    return (
        None,
        computed_conf,
        f"AMBIGUOUS (<{threshold}) – {computed_reason}",
        'RULES'
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
    
    Returns 'MANUAL' if any override was used,
    'CATEGORY_DEFAULT' if any default was used (and no overrides),
    'RULES' otherwise.
    """
    has_manual = False
    has_default = False
    
    for attr, data in evidence.items():
        source = data.get('source', 'RULES')
        if source == 'MANUAL':
            has_manual = True
        elif source == 'CATEGORY_DEFAULT':
            has_default = True
    
    if has_manual:
        return 'MANUAL'
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
    if 'CATEGORY_DEFAULT' in sources:
        notes_parts.append("Uses category defaults")
    
    low_conf_attrs = [
        attr for attr, data in evidence.items()
        if data.get('confidence', 100) < 60 and data.get('value') is None
    ]
    if low_conf_attrs:
        notes_parts.append(f"Ambiguous: {', '.join(low_conf_attrs)}")
    
    return ". ".join(notes_parts)
