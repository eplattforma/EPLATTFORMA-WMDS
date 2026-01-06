"""
Database migration to add unit_types column to batch_picking_sessions table
"""
import logging
from app import app, db
from sqlalchemy import text
import psycopg2

def update_unit_types_schema():
    """Add unit_types column to batch_picking_sessions table"""
    with app.app_context():
        try:
            # Check if the column already exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'batch_picking_sessions' 
                AND column_name = 'unit_types'
            """))
            
            existing_columns = [row[0] for row in result]
            
            if 'unit_types' not in existing_columns:
                logging.info("Adding unit_types column to batch_picking_sessions table...")
                
                # Add the unit_types column
                db.session.execute(text("""
                    ALTER TABLE batch_picking_sessions 
                    ADD COLUMN unit_types VARCHAR(500) DEFAULT NULL
                """))
                
                db.session.commit()
                logging.info("✅ Unit types schema update completed successfully")
            else:
                logging.info("unit_types column already exists in batch_picking_sessions table")
                
        except Exception as e:
            db.session.rollback()
            logging.error(f"❌ Error updating unit types schema: {str(e)}")
            raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_unit_types_schema()