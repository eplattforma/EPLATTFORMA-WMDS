"""
Fix and prevent duplicate BatchPickedItem records
This script adds database constraints and fixes the batch picking logic to prevent duplicates
"""

from app import app, db
from models import BatchPickedItem
from sqlalchemy import text

def add_unique_constraint():
    """Add unique constraint to prevent duplicate BatchPickedItem records"""
    with app.app_context():
        try:
            # Check if the constraint already exists
            constraint_check = db.session.execute(text("""
                SELECT COUNT(*) 
                FROM information_schema.table_constraints 
                WHERE constraint_name = 'uq_batch_picked_items_unique' 
                AND table_name = 'batch_picked_items'
            """)).scalar()
            
            if constraint_check == 0:
                # Add unique constraint to prevent duplicates
                db.session.execute(text("""
                    ALTER TABLE batch_picked_items 
                    ADD CONSTRAINT uq_batch_picked_items_unique 
                    UNIQUE (batch_session_id, invoice_no, item_code)
                """))
                db.session.commit()
                print("✅ Added unique constraint to prevent duplicate BatchPickedItem records")
            else:
                print("✅ Unique constraint already exists")
                
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error adding constraint: {e}")

def clean_existing_duplicates():
    """Clean up any existing duplicate records"""
    with app.app_context():
        try:
            # Find and remove duplicates, keeping only the first occurrence of each
            duplicate_query = text("""
                DELETE FROM batch_picked_items 
                WHERE ctid NOT IN (
                    SELECT MIN(ctid) 
                    FROM batch_picked_items 
                    GROUP BY batch_session_id, invoice_no, item_code
                )
            """)
            
            result = db.session.execute(duplicate_query)
            deleted_count = result.rowcount
            db.session.commit()
            
            if deleted_count > 0:
                print(f"✅ Removed {deleted_count} duplicate BatchPickedItem records")
            else:
                print("✅ No duplicate records found")
                
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error cleaning duplicates: {e}")

if __name__ == "__main__":
    print("🔧 Fixing BatchPickedItem duplicate prevention...")
    clean_existing_duplicates()
    add_unique_constraint()
    print("✅ BatchPickedItem duplicate prevention completed!")