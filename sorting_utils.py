"""
Shared sorting utilities for picking operations.
Uses configurable sorting from admin settings.
"""
import re
import json


def get_sorting_config():
    """Get the sorting configuration from database settings."""
    default_sorting = {
        "zone": {"enabled": True, "order": 1, "direction": "asc", "manual_priority": []},
        "corridor": {"enabled": True, "order": 2, "direction": "asc"},
        "shelf": {"enabled": True, "order": 3, "direction": "asc"},
        "level": {"enabled": True, "order": 4, "direction": "asc"},
        "bin": {"enabled": True, "order": 5, "direction": "asc"}
    }
    
    try:
        # Import inside function to avoid circular import
        from models import Setting
        setting = Setting.query.filter_by(key='picking_sort_config').first()
        if setting:
            return json.loads(setting.value)
        return default_sorting
    except Exception:
        return default_sorting


def extract_location_parts(location):
    """Extract different parts from a location string like '10-05-A03'"""
    if not location:
        return {'corridor': '', 'aisle': '', 'level': '', 'shelf': ''}
    
    parts = {
        'corridor': '',
        'aisle': '',
        'level': '',
        'shelf': ''
    }
    
    if '-' in location:
        segments = location.split('-')
        
        if len(segments) >= 1:
            parts['corridor'] = segments[0]
            
        if len(segments) >= 2:
            parts['aisle'] = segments[1]
            
        if len(segments) >= 3:
            last_segment = segments[2]
            match = re.match(r'([A-Za-z]*)(\d*)', last_segment)
            if match:
                level_part, shelf_part = match.groups()
                parts['level'] = level_part
                parts['shelf'] = shelf_part
    else:
        parts['corridor'] = location
        
    return parts


def numeric_sort_key(value):
    """Create a sort key that properly handles numeric values within strings"""
    if not value:
        return ('', 0)
        
    parts = re.findall(r'(\d+|\D+)', str(value))
    
    final_result = []
    for part in parts:
        if part.isdigit():
            final_result.append((1, int(part), ''))
        else:
            final_result.append((0, 0, part))
    
    return tuple(final_result)


def get_item_sort_key(item, sorting_config=None):
    """
    Generate a sort key for an item based on configured sort order and enabled flags.
    Works with both InvoiceItem objects and dictionaries.
    
    Args:
        item: InvoiceItem object or dictionary with location/zone fields
        sorting_config: Optional sorting configuration, will fetch from DB if not provided
    """
    if sorting_config is None:
        sorting_config = get_sorting_config()
    
    # Handle both object and dictionary access
    if isinstance(item, dict):
        location = item.get('location', '') or ''
        zone = item.get('zone', '') or ''
    else:
        location = getattr(item, 'location', '') or ''
        zone = getattr(item, 'zone', '') or ''
    
    parts = extract_location_parts(location)
    corridor = parts['corridor'] or ''
    aisle = parts['aisle'] or ''
    level = parts['level'] or ''
    shelf = parts['shelf'] or ''
    
    # Get config for each field
    zone_config = sorting_config.get('zone', {})
    corridor_config = sorting_config.get('corridor', {})
    shelf_config = sorting_config.get('shelf', {})
    level_config = sorting_config.get('level', {})
    bin_config = sorting_config.get('bin', {})
    
    # Build list of (priority, field_name, key) for enabled fields only
    enabled_fields = []
    
    if zone_config.get('enabled', True):
        effective_zone = zone if zone else 'MAIN'
        manual_zones = zone_config.get('manual_priority', [])
        if manual_zones and effective_zone in manual_zones:
            zone_key = ((1, manual_zones.index(effective_zone), ''),)
        elif manual_zones:
            zone_key = ((1, len(manual_zones), ''),)
        else:
            zone_key = numeric_sort_key(effective_zone)
        enabled_fields.append((zone_config.get('order', 1), 'zone', zone_key))
    
    if corridor_config.get('enabled', True):
        enabled_fields.append((corridor_config.get('order', 2), 'corridor', numeric_sort_key(corridor)))
    
    if shelf_config.get('enabled', True):
        enabled_fields.append((shelf_config.get('order', 3), 'shelf', numeric_sort_key(aisle)))
    
    if level_config.get('enabled', True):
        enabled_fields.append((level_config.get('order', 4), 'level', numeric_sort_key(level)))
    
    if bin_config.get('enabled', True):
        enabled_fields.append((bin_config.get('order', 5), 'bin', numeric_sort_key(shelf)))
    
    # Sort by priority order and build final key
    enabled_fields.sort(key=lambda x: x[0])
    sort_key = tuple(field[2] for field in enabled_fields)
    
    return sort_key if sort_key else (numeric_sort_key(location),)


def sort_items_for_picking(items, sorting_config=None):
    """
    Sort items for picking based on admin configuration.
    Works with both InvoiceItem objects and dictionaries.
    Handles skipped_pending items by putting them at the end.
    
    Args:
        items: List of InvoiceItem objects or dictionaries
        sorting_config: Optional sorting configuration
        
    Returns:
        Sorted list with skipped items at the end
    """
    if not items:
        return items
    
    if sorting_config is None:
        sorting_config = get_sorting_config()
    
    # Split items into regular and skipped
    def is_skipped(item):
        if isinstance(item, dict):
            return item.get('pick_status') == 'skipped_pending'
        return getattr(item, 'pick_status', None) == 'skipped_pending'
    
    regular_items = [item for item in items if not is_skipped(item)]
    skipped_items = [item for item in items if is_skipped(item)]
    
    # Create sort key function with captured config
    def sort_key(item):
        return get_item_sort_key(item, sorting_config)
    
    try:
        sorted_regular = sorted(regular_items, key=sort_key)
        sorted_skipped = sorted(skipped_items, key=sort_key)
        return sorted_regular + sorted_skipped
    except Exception as e:
        import logging
        logging.warning(f"sort_items_for_picking failed, using unsorted fallback: {e}")
        return regular_items + skipped_items


def sort_batch_items(items, sorting_config=None):
    """
    Sort batch picking items (dictionaries) based on admin configuration.
    This is specifically for batch picking where items are already dictionaries.
    
    Args:
        items: List of dictionaries with 'location' and 'zone' keys
        sorting_config: Optional sorting configuration
        
    Returns:
        Sorted list of dictionaries
    """
    if not items:
        return items
    
    if sorting_config is None:
        sorting_config = get_sorting_config()
    
    def sort_key(item):
        return get_item_sort_key(item, sorting_config)
    
    try:
        return sorted(items, key=sort_key)
    except Exception as e:
        import logging
        logging.warning(f"sort_batch_items failed, using unsorted fallback: {e}")
        return items
