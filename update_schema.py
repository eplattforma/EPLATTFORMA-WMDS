import logging
import psycopg2
import os
from psycopg2 import sql

# Set up logging
logging.basicConfig(level=logging.INFO)

def update_database_schema():
    """
    Add new columns to the invoice_items table for admin corrections
    """
    # Get database connection string from environment
    db_url = os.environ.get("DATABASE_URL")
    
    if not db_url:
        logging.error("DATABASE_URL environment variable not found")
        return False
    
    try:
        # Connect to the database
        logging.info("Connecting to database...")
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # Add columns if they don't exist
        logging.info("Adding pick_status column...")
        cursor.execute(
            """
            DO $$
            BEGIN
                BEGIN
                    ALTER TABLE invoice_items ADD COLUMN pick_status VARCHAR(20) DEFAULT 'not_picked';
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END;
            END $$;
            """
        )
        
        logging.info("Adding reset_by column...")
        cursor.execute(
            """
            DO $$
            BEGIN
                BEGIN
                    ALTER TABLE invoice_items ADD COLUMN reset_by VARCHAR(64);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END;
            END $$;
            """
        )
        
        logging.info("Adding reset_timestamp column...")
        cursor.execute(
            """
            DO $$
            BEGIN
                BEGIN
                    ALTER TABLE invoice_items ADD COLUMN reset_timestamp TIMESTAMP;
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END;
            END $$;
            """
        )
        
        logging.info("Adding reset_note column...")
        cursor.execute(
            """
            DO $$
            BEGIN
                BEGIN
                    ALTER TABLE invoice_items ADD COLUMN reset_note VARCHAR(500);
                EXCEPTION
                    WHEN duplicate_column THEN NULL;
                END;
            END $$;
            """
        )
        
        # Initialize existing rows with default values
        logging.info("Initializing existing rows with default values...")
        cursor.execute(
            """
            UPDATE invoice_items 
            SET pick_status = CASE 
                WHEN is_picked THEN 'picked' 
                ELSE 'not_picked' 
            END
            WHERE pick_status IS NULL;
            """
        )
        
        # Commit the changes
        conn.commit()
        logging.info("Successfully updated the database schema")
        
        # Close connection
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        logging.error(f"Error updating database schema: {e}")
        return False

if __name__ == "__main__":
    update_database_schema()