"""
Update database schema to add batch_number to batch_picking_sessions table
"""
import logging
from sqlalchemy import inspect, text
from app import app, db

logger = logging.getLogger(__name__)

def update_database_schema():
    """
    Add batch_number column to batch_picking_sessions table
    """
    with app.app_context():
        inspector = inspect(db.engine)
        
        # Check if batch_picking_sessions table exists
        if inspector.has_table('batch_picking_sessions'):
            # Check if batch_number column exists
            columns = inspector.get_columns('batch_picking_sessions')
            column_names = [column['name'] for column in columns]
            
            if 'batch_number' not in column_names:
                logger.info("Adding batch_number column to batch_picking_sessions")
                with db.engine.begin() as conn:
                    conn.execute(text("""
                        ALTER TABLE batch_picking_sessions
                        ADD COLUMN batch_number VARCHAR(20) UNIQUE
                    """))
                logger.info("Added batch_number column to batch_picking_sessions")
            else:
                logger.info("batch_number column already exists in batch_picking_sessions")
        else:
            logger.warning("batch_picking_sessions table does not exist")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_database_schema()