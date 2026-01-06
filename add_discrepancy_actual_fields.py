"""
Migration: Add actual_* fields to delivery_discrepancies for substitution tracking
Run with: python add_discrepancy_actual_fields.py
"""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")

def run_migration():
    engine = create_engine(DATABASE_URL)
    
    with engine.connect() as conn:
        # Check if columns already exist
        check_sql = """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'delivery_discrepancies' 
        AND column_name IN ('actual_item_id', 'actual_item_code', 'actual_item_name', 'actual_qty', 'actual_barcode');
        """
        result = conn.execute(text(check_sql))
        existing_columns = [row[0] for row in result]
        
        if existing_columns:
            print(f"Some columns already exist: {existing_columns}")
            print("Skipping migration to avoid errors")
            return
        
        # Add new columns for substitution tracking
        migration_sql = """
        ALTER TABLE delivery_discrepancies
          ADD COLUMN actual_item_id INTEGER NULL,
          ADD COLUMN actual_item_code TEXT NULL,
          ADD COLUMN actual_item_name TEXT NULL,
          ADD COLUMN actual_qty NUMERIC(12,3) NULL,
          ADD COLUMN actual_barcode TEXT NULL;
        """
        
        print("Adding actual_* columns to delivery_discrepancies table...")
        conn.execute(text(migration_sql))
        conn.commit()
        print("✓ Migration completed successfully!")
        print("  - Added: actual_item_id (INTEGER)")
        print("  - Added: actual_item_code (TEXT)")
        print("  - Added: actual_item_name (TEXT)")
        print("  - Added: actual_qty (NUMERIC(12,3))")
        print("  - Added: actual_barcode (TEXT)")

if __name__ == "__main__":
    run_migration()
