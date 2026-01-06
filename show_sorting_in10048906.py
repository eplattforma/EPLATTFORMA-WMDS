#!/usr/bin/env python3
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import app
from models import InvoiceItem

with app.app_context():
    items = InvoiceItem.query.filter_by(invoice_no='IN10048906').all()
    print(f'Found {len(items)} items for IN10048906')
    print('=' * 80)
    print('SORTING SEQUENCE FOR IN10048906:')
    print('=' * 80)
    
    # Sort items manually to show sequence
    sorted_items = sorted(items, key=lambda x: (
        x.zone or 'ZZZ',  # Sort zones first
        int(x.corridor) if x.corridor and x.corridor.isdigit() else 999,  # Then corridors numerically
        x.location or 'ZZZ'  # Then by location
    ))
    
    header = f"{'#':<3} {'Item Code':<12} {'Location':<15} {'Zone':<10} {'Corridor':<8} {'Status':<10}"
    print(header)
    print('-' * len(header))
    
    for i, item in enumerate(sorted_items, 1):
        location = item.location or 'NO LOCATION'
        zone = item.zone or ''
        corridor = item.corridor or ''
        status = 'PICKED' if item.is_picked else 'NOT PICKED'
        print(f'{i:<3} {item.item_code:<12} {location:<15} {zone:<10} {corridor:<8} {status:<10}')
    
    print('\n' + '=' * 80)
    print('ANALYSIS BY CORRIDOR:')
    print('=' * 80)
    
    # Group by corridor
    corridors = {}
    for item in sorted_items:
        corridor = item.corridor or 'NO CORRIDOR'
        if corridor not in corridors:
            corridors[corridor] = []
        corridors[corridor].append(item)
    
    for corridor in sorted(corridors.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        items_in_corridor = corridors[corridor]
        print(f'\nCorridor {corridor}: {len(items_in_corridor)} items')
        for item in items_in_corridor:
            location = item.location or 'NO LOCATION'
            print(f'  - {location} -> {item.item_code}')