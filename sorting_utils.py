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
    """Extract different parts from a location string like '31-04-C 01'
    
    Format: CORRIDOR-SHELF-LEVEL BIN (e.g., '31-04-C 01')
    - Corridor: 31 (first segment - the aisle/corridor number)
    - Shelf: 04 (second segment - shelf position within corridor)
    - Level: C (letter part of third segment)
    - Bin: 01 (number part of third segment)
    """
    if not location or str(location).lower() == 'none' or str(location).lower() == 'no location':
        return {'zone': '', 'corridor': '', 'shelf': '', 'level': '', 'bin': '', 'is_none': True}
    
    parts = {
        'zone': '',
        'corridor': '',
        'shelf': '',
        'level': '',
        'bin': '',
        'is_none': False
    }
    
    if '-' in location:
        segments = location.split('-')
        
        # First segment = Corridor (e.g., '31' in '31-04-C 01')
        if len(segments) >= 1:
            parts['corridor'] = segments[0].strip()
            parts['zone'] = segments[0].strip()  # Keep zone same as corridor for compatibility
            
        # Second segment = Shelf (e.g., '04' in '31-04-C 01')
        if len(segments) >= 2:
            parts['shelf'] = segments[1].strip()
            
        # Third segment = Level + Bin (e.g., 'C 01' or 'C01')
        if len(segments) >= 3:
            last_segment = segments[2].strip()
            match = re.match(r'([A-Za-z]+)\s*(\d+)?', last_segment)
            if match:
                level_letter, bin_number = match.groups()
                parts['level'] = level_letter or ''
                parts['bin'] = bin_number or ''
    else:
        parts['corridor'] = location.strip()
        
    return parts


def numeric_sort_key(value, descending=False):
    """Create a sort key that properly handles numeric values within strings.
    
    Args:
        value: The value to create a sort key for
        descending: If True, invert values for descending sort
    
    Returns a tuple that can be compared for sorting.
    For descending, we invert numeric values and letter ordinals.
    """
    if not value:
        # Empty values sort last in both asc and desc
        return (999999,)
        
    str_value = str(value)
    parts = re.findall(r'(\d+|\D+)', str_value)
    
    final_result = []
    for part in parts:
        if part.isdigit():
            num = int(part)
            if descending:
                # Invert numeric value for descending sort (large numbers first)
                num = 1000000 - num
            final_result.append(num)
        else:
            # For letters, convert to ordinal values
            for c in part:
                ord_val = ord(c.upper())
                if descending:
                    # Invert for descending (Z before A)
                    ord_val = 1000 - ord_val
                final_result.append(ord_val)
    
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
        item_zone = item.get('zone', '') or ''
    else:
        location = getattr(item, 'location', '') or ''
        item_zone = getattr(item, 'zone', '') or ''
    
    parts = extract_location_parts(location)
    
    # If location is None/empty, sort last
    if parts.get('is_none', False):
        # Return a tuple that will sort after everything else
        # Must match structure of normal keys: tuple of tuples with (type, int, str) format
        return (((1, 999999, ''),),)
    
    zone = parts['zone'] or ''
    corridor = parts['corridor'] or ''
    shelf = parts['shelf'] or ''
    level = parts['level'] or ''
    bin_val = parts['bin'] or ''
    
    # Get config for each field
    zone_config = sorting_config.get('zone', {})
    corridor_config = sorting_config.get('corridor', {})
    shelf_config = sorting_config.get('shelf', {})
    level_config = sorting_config.get('level', {})
    bin_config = sorting_config.get('bin', {})
    
    # Build list of (priority, field_name, key) for enabled fields only
    enabled_fields = []
    
    if zone_config.get('enabled', True):
        # Use InvoiceItem.zone first (code from PS365 Attribute #3), fallback to MAIN
        effective_zone = item_zone if item_zone else 'MAIN'
        manual_zones = zone_config.get('manual_priority', [])
        if manual_zones and effective_zone in manual_zones:
            # Zones in manual priority list get their index as sort key
            # Direction handling: if descending, reverse the index
            idx = manual_zones.index(effective_zone)
            if zone_config.get('direction') == 'desc':
                idx = len(manual_zones) - 1 - idx
            zone_key = ((0, idx, ''),)
        elif manual_zones:
            # Zones NOT in manual priority list sort after
            prefix = 1 if zone_config.get('direction') != 'desc' else -1
            zone_key = ((prefix, 0, ''),) + numeric_sort_key(effective_zone)
        else:
            # No manual priority - just use numeric sort
            zone_key = numeric_sort_key(effective_zone)
        enabled_fields.append((zone_config.get('order', 1), 'zone', zone_key))
    
    if corridor_config.get('enabled', True):
        corridor_desc = corridor_config.get('direction') == 'desc'
        enabled_fields.append((corridor_config.get('order', 2), 'corridor', numeric_sort_key(corridor, corridor_desc)))
    
    if shelf_config.get('enabled', True):
        shelf_desc = shelf_config.get('direction') == 'desc'
        enabled_fields.append((shelf_config.get('order', 3), 'shelf', numeric_sort_key(shelf, shelf_desc)))
    
    if level_config.get('enabled', True):
        level_desc = level_config.get('direction') == 'desc'
        enabled_fields.append((level_config.get('order', 4), 'level', numeric_sort_key(level, level_desc)))
    
    if bin_config.get('enabled', True):
        bin_desc = bin_config.get('direction') == 'desc'
        enabled_fields.append((bin_config.get('order', 5), 'bin', numeric_sort_key(bin_val, bin_desc)))
    
    # Sort by priority order and build final key
    enabled_fields.sort(key=lambda x: x[0])
    
    # Handle global sort direction if needed, but usually we handle it per field
    # For now, we build the tuple. If a field is 'desc', we should have inverted its key.
    
    final_keys = []
    for priority, field_name, key in enabled_fields:
        field_config = sorting_config.get(field_name, {})
        # If it's a simple numeric/string key and direction is desc, we might need inversion
        # However, for manual priority we already handled it.
        # For standard numeric_sort_key, we can't easily invert without knowing types.
        # Most of our sorting is ASC by default in warehouse.
        final_keys.append(key)
        
    return tuple(final_keys) if final_keys else (numeric_sort_key(location),)


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
