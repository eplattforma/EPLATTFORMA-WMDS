"""
Update seq_no column to support decimal values
"""
from app import app, db
from sqlalchemy import text

def update_seq_no_column():
    """Update seq_no column from Integer to Numeric(10,2)"""
    with app.app_context():
        try:
            # Check if column exists and its type
            result = db.session.execute(text("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'route_stop' AND column_name = 'seq_no'
            """))
            current_type = result.scalar()
            
            if current_type and current_type != 'numeric':
                print(f"Current seq_no type: {current_type}")
                print("Updating seq_no column to NUMERIC(10,2)...")
                
                # Alter the column type
                db.session.execute(text("""
                    ALTER TABLE route_stop 
                    ALTER COLUMN seq_no TYPE NUMERIC(10,2)
                """))
                
                db.session.commit()
                print("✓ seq_no column updated successfully to NUMERIC(10,2)")
            else:
                print(f"seq_no column already is type: {current_type}")
                
        except Exception as e:
            db.session.rollback()
            print(f"Error updating seq_no column: {e}")
            raise

if __name__ == "__main__":
    update_seq_no_column()
