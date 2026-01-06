"""
Update database schema to add new ItemTimeTracking columns for AI analysis
"""
import logging
from sqlalchemy import text
from app import db, app

def update_item_tracking_schema():
    """Add new columns to item_time_tracking table for enhanced AI analysis"""
    
    # List of new columns to add
    new_columns = [
        ("walking_time", "FLOAT DEFAULT 0.0"),
        ("picking_time", "FLOAT DEFAULT 0.0"), 
        ("confirmation_time", "FLOAT DEFAULT 0.0"),
        ("total_item_time", "FLOAT DEFAULT 0.0"),
        ("corridor", "VARCHAR(50)"),
        ("shelf", "VARCHAR(50)"),
        ("level", "VARCHAR(50)"),
        ("bin_location", "VARCHAR(50)"),
        ("quantity_expected", "INTEGER DEFAULT 0"),
        ("quantity_picked", "INTEGER DEFAULT 0"),
        ("item_weight", "FLOAT"),
        ("item_name", "VARCHAR(200)"),
        ("unit_type", "VARCHAR(50)"),
        ("expected_time", "FLOAT DEFAULT 0.0"),
        ("efficiency_ratio", "FLOAT DEFAULT 0.0"),
        ("previous_location", "VARCHAR(100)"),
        ("order_sequence", "INTEGER DEFAULT 0"),
        ("time_of_day", "VARCHAR(10)"),
        ("day_of_week", "VARCHAR(10)"),
        ("picked_correctly", "BOOLEAN DEFAULT TRUE"),
        ("was_skipped", "BOOLEAN DEFAULT FALSE"),
        ("skip_reason", "VARCHAR(200)"),
        ("peak_hours", "BOOLEAN DEFAULT FALSE"),
        ("concurrent_pickers", "INTEGER DEFAULT 1"),
        ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    ]
    
    try:
        # Check if item_time_tracking table exists
        table_exists = db.session.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'item_time_tracking'
            );
        """)).scalar()
        
        if not table_exists:
            logging.info("Creating item_time_tracking table...")
            # Create the table with all columns
            db.session.execute(text("""
                CREATE TABLE item_time_tracking (
                    id SERIAL PRIMARY KEY,
                    invoice_no VARCHAR(50) REFERENCES invoices(invoice_no),
                    item_code VARCHAR(50) NOT NULL,
                    picker_username VARCHAR(64) REFERENCES users(username),
                    item_started TIMESTAMP,
                    item_completed TIMESTAMP,
                    walking_time FLOAT DEFAULT 0.0,
                    picking_time FLOAT DEFAULT 0.0,
                    confirmation_time FLOAT DEFAULT 0.0,
                    total_item_time FLOAT DEFAULT 0.0,
                    location VARCHAR(100),
                    zone VARCHAR(50),
                    corridor VARCHAR(50),
                    shelf VARCHAR(50),
                    level VARCHAR(50),
                    bin_location VARCHAR(50),
                    quantity_expected INTEGER DEFAULT 0,
                    quantity_picked INTEGER DEFAULT 0,
                    item_weight FLOAT,
                    item_name VARCHAR(200),
                    unit_type VARCHAR(50),
                    expected_time FLOAT DEFAULT 0.0,
                    efficiency_ratio FLOAT DEFAULT 0.0,
                    previous_location VARCHAR(100),
                    order_sequence INTEGER DEFAULT 0,
                    time_of_day VARCHAR(10),
                    day_of_week VARCHAR(10),
                    picked_correctly BOOLEAN DEFAULT TRUE,
                    was_skipped BOOLEAN DEFAULT FALSE,
                    skip_reason VARCHAR(200),
                    peak_hours BOOLEAN DEFAULT FALSE,
                    concurrent_pickers INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            logging.info("✅ Created item_time_tracking table with all AI tracking columns")
        else:
            # Add missing columns one by one
            for column_name, column_def in new_columns:
                try:
                    # Check if column exists
                    column_exists = db.session.execute(text(f"""
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns 
                            WHERE table_name = 'item_time_tracking' 
                            AND column_name = '{column_name}'
                        );
                    """)).scalar()
                    
                    if not column_exists:
                        # Add the column
                        db.session.execute(text(f"""
                            ALTER TABLE item_time_tracking 
                            ADD COLUMN {column_name} {column_def};
                        """))
                        logging.info(f"✅ Added column {column_name} to item_time_tracking")
                    
                except Exception as e:
                    logging.warning(f"⚠️ Could not add column {column_name}: {e}")
                    continue
        
        # Commit all changes
        db.session.commit()
        logging.info("✅ Item tracking schema update completed successfully")
        
    except Exception as e:
        logging.error(f"❌ Error updating item tracking schema: {e}")
        db.session.rollback()
        raise

if __name__ == "__main__":
    with app.app_context():
        update_item_tracking_schema()