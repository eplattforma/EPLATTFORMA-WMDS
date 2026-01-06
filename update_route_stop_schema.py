"""
Database schema update for RouteStop contact fields
Adds website and phone columns to route_stop table
"""

from app import app, db
from sqlalchemy import text
import logging

def update_route_stop_schema():
    """Add contact fields to route_stop table"""
    with app.app_context():
        try:
            # Check if website column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'route_stop' AND column_name = 'website'
            """))
            
            if result.fetchone() is None:
                # Add website column
                db.session.execute(text("""
                    ALTER TABLE route_stop 
                    ADD COLUMN website VARCHAR(500)
                """))
                logging.info("Added website column to route_stop table")
            else:
                logging.info("website column already exists in route_stop table")
            
            # Check if phone column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'route_stop' AND column_name = 'phone'
            """))
            
            if result.fetchone() is None:
                # Add phone column
                db.session.execute(text("""
                    ALTER TABLE route_stop 
                    ADD COLUMN phone VARCHAR(50)
                """))
                logging.info("Added phone column to route_stop table")
            else:
                logging.info("phone column already exists in route_stop table")
            
            # Check if delivered_at column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'route_stop' AND column_name = 'delivered_at'
            """))
            
            if result.fetchone() is None:
                # Add delivered_at column
                db.session.execute(text("""
                    ALTER TABLE route_stop 
                    ADD COLUMN delivered_at TIMESTAMP
                """))
                logging.info("Added delivered_at column to route_stop table")
            else:
                logging.info("delivered_at column already exists in route_stop table")
            
            # Check if failed_at column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'route_stop' AND column_name = 'failed_at'
            """))
            
            if result.fetchone() is None:
                # Add failed_at column
                db.session.execute(text("""
                    ALTER TABLE route_stop 
                    ADD COLUMN failed_at TIMESTAMP
                """))
                logging.info("Added failed_at column to route_stop table")
            else:
                logging.info("failed_at column already exists in route_stop table")
            
            # Check if failure_reason column exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'route_stop' AND column_name = 'failure_reason'
            """))
            
            if result.fetchone() is None:
                # Add failure_reason column
                db.session.execute(text("""
                    ALTER TABLE route_stop 
                    ADD COLUMN failure_reason VARCHAR(100)
                """))
                logging.info("Added failure_reason column to route_stop table")
            else:
                logging.info("failure_reason column already exists in route_stop table")
            
            db.session.commit()
            logging.info("✅ RouteStop schema update completed successfully")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating RouteStop schema: {str(e)}")
            raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_route_stop_schema()
