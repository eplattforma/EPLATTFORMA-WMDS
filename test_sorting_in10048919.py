#!/usr/bin/env python3
"""
Test script to show sorting sequence for invoice IN10048919
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import app
from models import InvoiceItem, Setting
import json

def sort_items_by_config(items):
    """
    Sort items based on the configured sorting options in the admin settings.
    Also prioritizes regular items over skipped_pending items.
    """
    # Default sorting configuration
    default_sorting = {
        "zone": {"enabled": True, "order": 1, "direction": "asc", "manual_priority": []},
        "corridor": {"enabled": True, "order": 2, "direction": "asc"},
        "shelf": {"enabled": True, "order": 3, "direction": "asc"},
        "level": {"enabled": True, "order": 4, "direction": "asc"},
        "bin": {"enabled": True, "order": 5, "direction": "asc"}
    }
    
    # Get sorting configuration from database
    try:
        setting = Setting.query.filter_by(key='picking_sort_config').first()
        if setting:
            sorting_config = json.loads(setting.value)
        else:
            sorting_config = default_sorting
    except Exception:
        sorting_config = default_sorting
        
    # Split the items into regular and skipped_pending
    regular_items = [item for item in items if item.pick_status != 'skipped_pending']
    skipped_items = [item for item in items if item.pick_status == 'skipped_pending']
    
    def extract_location_parts(location):
        """Extract different parts from a location string like '10-05-A03'"""
        if not location:
            return {'corridor': '', 'aisle': '', 'level': '', 'shelf': ''}
        
        parts = {}
        # Initialize default values
        parts['corridor'] = ''
        parts['aisle'] = ''
        parts['level'] = ''
        parts['shelf'] = ''
        
        # For warehouse location format: '10-05-A03'
        # First segment (10) = corridor, Second segment (05) = aisle, 
        # Last segment (A03) contains level (A) and shelf (03)
        if '-' in location:
            segments = location.split('-')
            
            # Handle corridor part (like '10' in '10-05-A03')
            if len(segments) >= 1:
                parts['corridor'] = segments[0]
                
            # Handle aisle part (like '05' in '10-05-A03')
            if len(segments) >= 2:
                parts['aisle'] = segments[1]
                
            # Handle level and shelf (ex: 'A03' in '10-05-A03')
            if len(segments) >= 3:
                last_segment = segments[2]
                
                # Separate level and shelf
                # Example: 'A03' -> level 'A' and shelf '03'
                import re
                match = re.match(r'([A-Za-z]*)(\d*)', last_segment)
                if match:
                    level_part, shelf_part = match.groups()
                    parts['level'] = level_part
                    parts['shelf'] = shelf_part
        else:
            # If no dashes, treat the entire location as corridor
            parts['corridor'] = location
            
        return parts
    
    def numeric_sort_key(value):
        """Create a sort key that properly handles numeric values within strings"""
        import re
        
        if not value:
            return ('', 0)  # Empty values sort first
            
        # Split the string into text and numeric parts
        parts = re.findall(r'(\d+|\D+)', value)
        
        # Convert numeric strings to integers for proper sorting
        result = []
        for part in parts:
            if part.isdigit():
                # Convert to integer for numeric comparison
                result.append(int(part))
            else:
                # Keep strings as strings
                result.append(part)
                
        # Convert result to a consistent format for comparison
        final_result = []
        for i, item in enumerate(result):
            if isinstance(item, int):
                # All integers will be compared with integers
                final_result.append((1, item, ''))  # Type 1 = integer
            else:
                # All strings will be compared with strings
                final_result.append((0, 0, item))   # Type 0 = string
        
        return tuple(final_result)
    
    def get_sort_key(item):
        """Generate a sort key tuple based on configured sort order"""
        location = item.location or ''
        zone = item.zone or ''  # This is MAIN, SENSITIVE, SNACK from database
        
        # Extract location parts for corridor, aisle, level, shelf
        parts = extract_location_parts(location)
        corridor = parts['corridor'] or ''  # This is 10, 11, 12, 20, 30, etc.
        aisle = parts['aisle'] or ''
        level = parts['level'] or ''
        shelf = parts['shelf'] or ''
        
        # Create sort keys
        zone_key = numeric_sort_key(zone)
        corridor_key = numeric_sort_key(corridor)
        aisle_key = numeric_sort_key(aisle)
        level_key = numeric_sort_key(level)
        shelf_key = numeric_sort_key(shelf)
        
        # Check for manual zone priority (MAIN, SENSITIVE, SNACK)
        manual_zones = sorting_config.get('zone', {}).get('manual_priority', [])
        if manual_zones and zone:
            try:
                if zone in manual_zones:
                    zone_priority = manual_zones.index(zone)
                else:
                    zone_priority = len(manual_zones)
                # Sort by zone priority, then corridor, then aisle, then level, then shelf
                return (zone_priority, corridor_key, aisle_key, level_key, shelf_key)
            except (ValueError, TypeError, IndexError):
                pass
        
        # Default: sort by zone, then corridor, then aisle, then level, then shelf
        return (zone_key, corridor_key, aisle_key, level_key, shelf_key)
    
    # Sort each group separately
    try:
        # Sort regular items by location
        sorted_regular_items = sorted(regular_items, key=get_sort_key)
        
        # Sort skipped items by location
        sorted_skipped_items = sorted(skipped_items, key=get_sort_key)
        
        # Return regular items first, then skipped items
        return sorted_regular_items + sorted_skipped_items
    except Exception as e:
        print(f"Error sorting items: {str(e)}")
        # Fall back to unsorted items but still keep regular items first
        return regular_items + skipped_items

def test_sorting_sequence(invoice_no='IN10048919'):
    """Test the sorting sequence for specified invoice"""
    
    with app.app_context():
        # Get all items for this invoice
        items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
        
        if not items:
            print(f"No items found for invoice {invoice_no}")
            return
        
        print(f"\nFound {len(items)} items for invoice {invoice_no}")
        print("=" * 80)
        
        # Apply the sorting function
        sorted_items = sort_items_by_config(items)
        
        print("\nSORTED PICKING SEQUENCE:")
        print("=" * 80)
        print(f"{'#':<3} {'Item Code':<12} {'Location':<12} {'Zone':<10} {'Corridor':<8} {'Item Name':<50}")
        print("-" * 95)
        
        for i, item in enumerate(sorted_items, 1):
            location = item.location or ''
            zone = item.zone or ''
            corridor = item.corridor or ''
            item_name = item.item_name or ''
            print(f"{i:<3} {item.item_code:<12} {location:<12} {zone:<10} {corridor:<8} {item_name[:45]:<45}")
        
        print("\n" + "=" * 80)
        print("ANALYSIS:")
        print("=" * 80)
        
        # Group by corridor to show the sorting logic
        corridors = {}
        for item in sorted_items:
            corridor = item.corridor or 'Unknown'
            if corridor not in corridors:
                corridors[corridor] = []
            corridors[corridor].append(item)
        
        print(f"\nItems grouped by corridor:")
        for corridor in sorted(corridors.keys()):
            items_in_corridor = corridors[corridor]
            print(f"\nCorridor {corridor}: {len(items_in_corridor)} items")
            for item in items_in_corridor:
                print(f"  - {item.location} -> {item.item_code}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        invoice_no = sys.argv[1]
    else:
        invoice_no = 'IN10048919'
    test_sorting_sequence(invoice_no)