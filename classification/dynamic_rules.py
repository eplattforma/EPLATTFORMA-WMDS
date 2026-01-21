"""
Dynamic rules engine for Operational Intelligence classification.

Provides safe JSON-based rule evaluation without eval().
Rules can target item fields: item_name, brand_code_365, attribute_1..6_code_365, item_weight
"""

import json
import logging
from typing import Dict, List, Any, Optional

from models import WmsDynamicRule

logger = logging.getLogger(__name__)

# Allowed fields for rule conditions
ALLOWED_FIELDS = {
    'item_name': 'text',
    'brand_code_365': 'code',
    'category_code_365': 'code',          # Category field for "no category" rules
    'attribute_1_code_365': 'code',
    'attribute_2_code_365': 'code',
    'attribute_3_code_365': 'code',
    'attribute_4_code_365': 'code',
    'attribute_5_code_365': 'code',
    'attribute_6_code_365': 'code',
    'item_weight': 'number',
}

# Allowed operators by field type
OPERATORS_BY_TYPE = {
    'text': ['contains', 'not_contains', 'starts_with', 'ends_with', 'eq', 'is_empty', 'is_not_empty'],
    'code': ['eq', 'neq', 'in', 'not_in', 'is_empty', 'is_not_empty'],
    'number': ['gt', 'gte', 'lt', 'lte', 'eq', 'is_empty', 'is_not_empty'],
}

# Target attributes and their valid values (matches compute_* rules and UI)
TARGET_ATTRS = {
    'fragility': ['YES', 'SEMI', 'NO'],
    'spill_risk': ['true', 'false'],  # Will be cast to bool
    'pressure_sensitivity': ['high', 'medium', 'low'],
    'temperature_sensitivity': ['normal', 'heat_sensitive', 'cool_required'],
    'stackability': ['YES', 'LIMITED', 'NO'],
    'shape_type': ['cubic', 'flat', 'round', 'irregular'],
    'pick_difficulty': ['1', '2', '3', '4', '5'],  # Will be cast to int
    'shelf_height': ['LOW', 'MID', 'HIGH'],
    'box_fit_rule': ['BOTTOM', 'MIDDLE', 'TOP', 'COOLER_BAG'],
    'zone': ['MAIN', 'SENSITIVE', 'SNACKS', 'CROSS_SHIPPING'],
    'unit_type': ['item', 'pack', 'box', 'case', 'virtual_pack'],
}


def get_field_type(field: str) -> Optional[str]:
    """Get the type of a field for operator validation."""
    return ALLOWED_FIELDS.get(field)


def get_allowed_operators(field: str) -> List[str]:
    """Get allowed operators for a field."""
    field_type = get_field_type(field)
    if not field_type:
        return []
    return OPERATORS_BY_TYPE.get(field_type, [])


def load_active_rules() -> Dict[str, List[WmsDynamicRule]]:
    """
    Load all active dynamic rules from database, grouped by target attribute.
    
    Returns:
        Dict mapping target_attr to list of rules sorted by priority (descending)
    """
    rules = WmsDynamicRule.query.filter_by(is_active=True).order_by(
        WmsDynamicRule.priority.desc()
    ).all()
    
    rules_by_attr: Dict[str, List[WmsDynamicRule]] = {}
    for rule in rules:
        if rule.target_attr not in rules_by_attr:
            rules_by_attr[rule.target_attr] = []
        rules_by_attr[rule.target_attr].append(rule)
    
    return rules_by_attr


def get_item_field_value(item, field: str) -> Any:
    """Safely get a field value from a DwItem."""
    if field not in ALLOWED_FIELDS:
        return None
    return getattr(item, field, None)


def evaluate_condition(item, condition: dict) -> bool:
    """
    Evaluate a single condition against an item.
    
    Condition format: {"field": "...", "op": "...", "value": ...}
    """
    field = condition.get('field')
    op = condition.get('op')
    expected = condition.get('value')
    
    if field not in ALLOWED_FIELDS:
        return False
    
    field_type = ALLOWED_FIELDS[field]
    if op not in OPERATORS_BY_TYPE.get(field_type, []):
        return False
    
    actual = get_item_field_value(item, field)
    
    # Handle empty checks first
    if op == 'is_empty':
        return actual is None or actual == ''
    if op == 'is_not_empty':
        return actual is not None and actual != ''
    
    # For other operations, if actual is None/empty, condition fails
    if actual is None or actual == '':
        return False
    
    # Text operations (case-insensitive)
    if field_type == 'text':
        actual_lower = str(actual).lower()
        expected_lower = str(expected).lower() if expected else ''
        
        if op == 'contains':
            return expected_lower in actual_lower
        elif op == 'not_contains':
            return expected_lower not in actual_lower
        elif op == 'starts_with':
            return actual_lower.startswith(expected_lower)
        elif op == 'ends_with':
            return actual_lower.endswith(expected_lower)
        elif op == 'eq':
            return actual_lower == expected_lower
    
    # Code operations (case-sensitive for codes)
    elif field_type == 'code':
        actual_str = str(actual).upper()
        
        if op == 'eq':
            return actual_str == str(expected).upper()
        elif op == 'neq':
            return actual_str != str(expected).upper()
        elif op == 'in':
            if isinstance(expected, list):
                return actual_str in [str(v).upper() for v in expected]
            return False
        elif op == 'not_in':
            if isinstance(expected, list):
                return actual_str not in [str(v).upper() for v in expected]
            return True
    
    # Number operations
    elif field_type == 'number':
        try:
            actual_num = float(actual) if actual is not None else None
            expected_num = float(expected) if expected is not None else None
            
            if actual_num is None or expected_num is None:
                return False
            
            if op == 'gt':
                return actual_num > expected_num
            elif op == 'gte':
                return actual_num >= expected_num
            elif op == 'lt':
                return actual_num < expected_num
            elif op == 'lte':
                return actual_num <= expected_num
            elif op == 'eq':
                return actual_num == expected_num
        except (ValueError, TypeError):
            return False
    
    return False


def evaluate_rule_conditions(item, condition_json: str) -> bool:
    """
    Evaluate all conditions in a rule against an item.
    
    Condition JSON format: {"all": [conditions...]} for AND logic
    """
    try:
        conditions = json.loads(condition_json)
    except json.JSONDecodeError:
        logger.warning(f"Invalid condition JSON: {condition_json}")
        return False
    
    # Currently only supports "all" (AND) logic
    if 'all' in conditions:
        for condition in conditions['all']:
            if not evaluate_condition(item, condition):
                return False
        return True
    
    return False


def cast_action_value(target_attr: str, action_value: str) -> Any:
    """
    Cast action_value to the appropriate type for the target attribute.
    
    - spill_risk: boolean
    - pick_difficulty: integer
    - others: string
    """
    if target_attr == 'spill_risk':
        return action_value.lower() in ('true', '1', 'yes')
    elif target_attr == 'pick_difficulty':
        try:
            return int(action_value)
        except ValueError:
            return None
    return action_value


def match_best_rule(item, target_attr: str, rules_by_attr: Dict[str, List[WmsDynamicRule]]) -> Optional[Dict[str, Any]]:
    """
    Find the best matching rule for an item and target attribute.
    
    Rules are processed in priority order (highest first).
    Returns None if no rule matches.
    
    Returns:
        dict with: value, confidence, reason, rule_id, rule_name
    """
    rules = rules_by_attr.get(target_attr, [])
    
    for rule in rules:
        if evaluate_rule_conditions(item, rule.condition_json):
            casted_value = cast_action_value(target_attr, rule.action_value)
            
            return {
                'value': casted_value,
                'confidence': rule.confidence,
                'reason': f"Dynamic rule '{rule.name}' (priority {rule.priority})",
                'rule_id': rule.id,
                'rule_name': rule.name
            }
            
            # If stop_processing is True, we stop after first match (default behavior)
            # Since rules are sorted by priority, first match = highest priority match
    
    return None


def validate_rule_condition(condition: dict) -> tuple[bool, str]:
    """
    Validate a single condition.
    
    Returns (is_valid, error_message)
    """
    field = condition.get('field')
    op = condition.get('op')
    value = condition.get('value')
    
    if not field:
        return False, "Missing field"
    
    if field not in ALLOWED_FIELDS:
        return False, f"Invalid field: {field}"
    
    if not op:
        return False, "Missing operator"
    
    allowed_ops = get_allowed_operators(field)
    if op not in allowed_ops:
        return False, f"Invalid operator '{op}' for field '{field}'"
    
    # Value required for most operators
    if op not in ('is_empty', 'is_not_empty'):
        if value is None or value == '':
            return False, f"Value required for operator '{op}'"
        
        # For 'in' and 'not_in', value should be a list
        if op in ('in', 'not_in') and not isinstance(value, list):
            return False, f"Operator '{op}' requires a list of values"
    
    return True, ""


def validate_target_attr(target_attr: str, action_value: str) -> tuple[bool, str]:
    """
    Validate that target_attr and action_value are valid.
    
    Returns (is_valid, error_message)
    """
    if target_attr not in TARGET_ATTRS:
        return False, f"Invalid target attribute: {target_attr}"
    
    valid_values = TARGET_ATTRS[target_attr]
    if action_value.lower() not in [v.lower() for v in valid_values]:
        return False, f"Invalid action value '{action_value}' for {target_attr}. Valid: {', '.join(valid_values)}"
    
    return True, ""


def summarize_conditions(condition_json: str) -> str:
    """Generate a human-readable summary of rule conditions."""
    try:
        conditions = json.loads(condition_json)
    except json.JSONDecodeError:
        return "Invalid conditions"
    
    if 'all' not in conditions:
        return "No conditions"
    
    parts = []
    for cond in conditions.get('all', []):
        field = cond.get('field', '?')
        op = cond.get('op', '?')
        value = cond.get('value', '')
        
        # Shorten field names for display
        field_short = field.replace('_code_365', '').replace('attribute_', 'attr')
        
        if op in ('is_empty', 'is_not_empty'):
            parts.append(f"{field_short} {op.replace('_', ' ')}")
        elif op in ('in', 'not_in'):
            values_str = ', '.join(str(v) for v in (value if isinstance(value, list) else [value]))
            parts.append(f"{field_short} {op.replace('_', ' ')} [{values_str}]")
        else:
            parts.append(f"{field_short} {op} {value}")
    
    return ' AND '.join(parts) if parts else "No conditions"


def test_rule_against_item(item, rule: WmsDynamicRule) -> Dict[str, Any]:
    """
    Test a rule against an item and return detailed results.
    
    Returns dict with match status and field values.
    """
    matches = evaluate_rule_conditions(item, rule.condition_json)
    
    # Get field values used in the rule
    try:
        conditions = json.loads(rule.condition_json)
    except json.JSONDecodeError:
        conditions = {'all': []}
    
    field_values = {}
    for cond in conditions.get('all', []):
        field = cond.get('field')
        if field:
            field_values[field] = get_item_field_value(item, field)
    
    return {
        'rule_id': rule.id,
        'rule_name': rule.name,
        'matches': matches,
        'target_attr': rule.target_attr,
        'action_value': rule.action_value,
        'confidence': rule.confidence,
        'priority': rule.priority,
        'field_values': field_values
    }
