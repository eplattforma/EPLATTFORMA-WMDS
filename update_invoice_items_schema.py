#!/usr/bin/env python3
"""
Database migration script to add pieces_per_unit_snapshot and expected_pick_pieces columns
"""

import sys
import os
from sqlalchemy import text, inspect

def update_invoice_items_schema(db):
    """Add new columns to invoice_items table if they don't exist"""
    
    try:
        # Use inspector to check existing columns
        inspector = inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('invoice_items')]
        
        # Check and add pieces_per_unit_snapshot column
        if 'pieces_per_unit_snapshot' not in columns:
            print("➕ Adding pieces_per_unit_snapshot column...")
            with db.engine.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE invoice_items 
                    ADD COLUMN pieces_per_unit_snapshot INTEGER
                """))
                conn.commit()
            print("✅ pieces_per_unit_snapshot column added successfully")
        else:
            print("✅ pieces_per_unit_snapshot column already exists")
        
        # Check and add expected_pick_pieces column
        if 'expected_pick_pieces' not in columns:
            print("➕ Adding expected_pick_pieces column...")
            with db.engine.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE invoice_items 
                    ADD COLUMN expected_pick_pieces INTEGER
                """))
                conn.commit()
            print("✅ expected_pick_pieces column added successfully")
        else:
            print("✅ expected_pick_pieces column already exists")
        
        print("✅ Invoice items schema update completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error during invoice items schema migration: {e}")
        return False

if __name__ == "__main__":
    from app import db
    update_invoice_items_schema(db)
