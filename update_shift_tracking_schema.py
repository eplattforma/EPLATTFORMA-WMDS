import os
import logging
from datetime import datetime
from app import app, db
from sqlalchemy import text

def update_database_schema():
    """
    Add shift tracking tables and related columns to the database
    """
    try:
        with app.app_context():
            # Check if the shifts table exists
            inspector = db.inspect(db.engine)
            tables = inspector.get_table_names()
            tables_created = []
            
            # Create shifts table if it doesn't exist
            if 'shifts' not in tables:
                db.session.execute(text('''
                    CREATE TABLE shifts (
                        id SERIAL PRIMARY KEY,
                        picker_username VARCHAR(64) REFERENCES users(username),
                        check_in_time TIMESTAMP NOT NULL,
                        check_out_time TIMESTAMP,
                        check_in_coordinates VARCHAR(100),
                        check_out_coordinates VARCHAR(100),
                        total_duration_minutes INTEGER,
                        status VARCHAR(20) DEFAULT 'active',
                        admin_adjusted BOOLEAN DEFAULT FALSE,
                        adjustment_note TEXT,
                        adjustment_by VARCHAR(64) REFERENCES users(username),
                        adjustment_time TIMESTAMP
                    )
                '''))
                tables_created.append('shifts')
            
            # Create idle_periods table if it doesn't exist
            if 'idle_periods' not in tables:
                db.session.execute(text('''
                    CREATE TABLE idle_periods (
                        id SERIAL PRIMARY KEY,
                        shift_id INTEGER REFERENCES shifts(id),
                        start_time TIMESTAMP NOT NULL,
                        end_time TIMESTAMP,
                        duration_minutes INTEGER,
                        is_break BOOLEAN DEFAULT FALSE,
                        break_reason VARCHAR(200)
                    )
                '''))
                tables_created.append('idle_periods')
                
            # Create activity_logs table if it doesn't exist
            if 'activity_logs' not in tables:
                db.session.execute(text('''
                    CREATE TABLE activity_logs (
                        id SERIAL PRIMARY KEY,
                        picker_username VARCHAR(64) REFERENCES users(username),
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        activity_type VARCHAR(50) NOT NULL,
                        invoice_no VARCHAR(50),
                        item_code VARCHAR(50),
                        details TEXT
                    )
                '''))
                tables_created.append('activity_logs')
                
            # Add settings for shift tracking if they don't exist
            session = db.session()
            
            from models import Setting
            
            # Add default idle time threshold (15 minutes)
            if not Setting.get(session, 'idle_time_threshold_minutes', ''):
                Setting.set(session, 'idle_time_threshold_minutes', '15')
                
            # Add default end of business day time (18:00)
            if not Setting.get(session, 'end_of_business_day_time', ''):
                Setting.set(session, 'end_of_business_day_time', '18:00')
                
            # Add setting for auto-detecting idle time
            if not Setting.get(session, 'auto_detect_idle_enabled', ''):
                Setting.set(session, 'auto_detect_idle_enabled', 'true')
                
            # Add setting for alerting on missed checkouts
            if not Setting.get(session, 'alert_missed_checkouts', ''):
                Setting.set(session, 'alert_missed_checkouts', 'true')
                
            db.session.commit()
            
            if tables_created:
                logging.info(f"Created new tables: {', '.join(tables_created)}")
            
            return True, "Database schema updated for shift tracking system"
    except Exception as e:
        logging.error(f"Error updating database schema: {str(e)}")
        return False, f"Error updating database schema: {str(e)}"

# Run the update when this file is executed directly
if __name__ == "__main__":
    success, message = update_database_schema()
    print(message)