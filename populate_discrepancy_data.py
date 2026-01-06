#!/usr/bin/env python3
"""
Script to populate discrepancy_types and stock_resolutions tables
Run this once to initialize the production database with discrepancy data
"""
import os
from app import app, db
from models import DiscrepancyType, StockResolution

def populate_data():
    with app.app_context():
        # Check if data already exists
        existing_count = DiscrepancyType.query.count()
        if existing_count > 0:
            print(f"✓ Discrepancy types already exist ({existing_count} types found)")
        else:
            print("Adding discrepancy types...")
            # Add discrepancy types
            types_data = [
                {'name': 'missing', 'display_name': 'Missing', 'sort_order': 1},
                {'name': 'wrong_item', 'display_name': 'Wrong Item', 'sort_order': 2},
                {'name': 'damaged', 'display_name': 'Damaged', 'sort_order': 3},
                {'name': 'short_pick', 'display_name': 'Short Pick', 'sort_order': 4},
                {'name': 'over_pick', 'display_name': 'Over Pick', 'sort_order': 5},
                {'name': 'extra_item', 'display_name': 'Extra Item', 'sort_order': 6},
                {'name': 'other', 'display_name': 'Other', 'sort_order': 7},
            ]
            
            for type_data in types_data:
                dt = DiscrepancyType(
                    name=type_data['name'],
                    display_name=type_data['display_name'],
                    is_active=True,
                    sort_order=type_data['sort_order']
                )
                db.session.add(dt)
            
            db.session.commit()
            print(f"✓ Added {len(types_data)} discrepancy types")
        
        # Check if resolutions already exist
        existing_resolutions = StockResolution.query.count()
        if existing_resolutions > 0:
            print(f"✓ Stock resolutions already exist ({existing_resolutions} resolutions found)")
        else:
            print("Adding stock resolutions...")
            # Add stock resolutions
            resolutions_data = [
                # Missing
                {'type': 'missing', 'name': 'In stock', 'order': 1},
                {'type': 'missing', 'name': 'Not in stock', 'order': 2},
                # Wrong item
                {'type': 'wrong_item', 'name': 'Returned to Stock', 'order': 1},
                {'type': 'wrong_item', 'name': 'Not Returned', 'order': 2},
                # Damaged
                {'type': 'damaged', 'name': 'Send to Returns', 'order': 1},
                {'type': 'damaged', 'name': 'Send to Obsolete', 'order': 2},
                {'type': 'damaged', 'name': 'Send to Discount', 'order': 3},
                # Short pick
                {'type': 'short_pick', 'name': 'In stock', 'order': 1},
                {'type': 'short_pick', 'name': 'Not in stock', 'order': 2},
                # Over pick
                {'type': 'over_pick', 'name': 'Returned to Stock', 'order': 1},
                {'type': 'over_pick', 'name': 'Not Returned', 'order': 2},
                # Extra item
                {'type': 'extra_item', 'name': 'Returned to Stock', 'order': 1},
                {'type': 'extra_item', 'name': 'Not Returned', 'order': 2},
                # Other
                {'type': 'other', 'name': 'In stock', 'order': 1},
                {'type': 'other', 'name': 'Not in stock', 'order': 2},
                {'type': 'other', 'name': 'Returned to Stock', 'order': 3},
                {'type': 'other', 'name': 'Not Returned', 'order': 4},
                {'type': 'other', 'name': 'Send to Returns', 'order': 5},
                {'type': 'other', 'name': 'Send to Obsolete', 'order': 6},
                {'type': 'other', 'name': 'Send to Discount', 'order': 7},
            ]
            
            for res_data in resolutions_data:
                sr = StockResolution(
                    discrepancy_type=res_data['type'],
                    resolution_name=res_data['name'],
                    is_active=True,
                    sort_order=res_data['order']
                )
                db.session.add(sr)
            
            db.session.commit()
            print(f"✓ Added {len(resolutions_data)} stock resolutions")
        
        # Verify
        total_types = DiscrepancyType.query.count()
        total_resolutions = StockResolution.query.count()
        print(f"\n✓ Database populated successfully!")
        print(f"  - {total_types} discrepancy types")
        print(f"  - {total_resolutions} stock resolutions")

if __name__ == '__main__':
    populate_data()
