import os
import sys
import psycopg2
from psycopg2 import sql
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def migrate_db():
    prod_url = os.environ.get("DATABASE_URL_PROD")
    dev_url = os.environ.get("DATABASE_URL") # development is the default DATABASE_URL

    if not prod_url:
        logger.error("DATABASE_URL_PROD not found in environment variables.")
        return

    if not dev_url:
        logger.error("DATABASE_URL (Development) not found in environment variables.")
        return

    # List of tables to copy - prioritize core data
    # Note: Order matters for foreign keys if we don't disable constraints
    tables = [
        "users",
        "settings",
        "ps_customers",
        "invoices",
        "invoice_items",
        "batch_picking_sessions",
        "batch_picked_items",
        "item_time_tracking",
        "order_time_breakdowns",
        "shipments",
        "route_stops",
        "route_stop_invoices",
        "delivery_discrepancies",
        "delivery_discrepancy_events"
    ]

    try:
        logger.info("Connecting to Production database...")
        prod_conn = psycopg2.connect(prod_url)
        prod_cur = prod_conn.cursor()

        logger.info("Connecting to Development database...")
        dev_conn = psycopg2.connect(dev_url)
        dev_cur = dev_conn.cursor()

        # Disable triggers to avoid foreign key issues during bulk load
        dev_cur.execute("SET session_replication_role = 'replica';")

        for table in tables:
            logger.info(f"Copying table: {table}")
            
            # Check if table exists in dev
            dev_cur.execute(f"SELECT exists (SELECT FROM information_schema.tables WHERE table_name = '{table}');")
            if not dev_cur.fetchone()[0]:
                logger.warning(f"Table {table} does not exist in development. Skipping.")
                continue

            # Clear dev table
            dev_cur.execute(f"TRUNCATE TABLE {table} CASCADE;")
            
            # Get data from prod
            prod_cur.execute(f"SELECT * FROM {table};")
            rows = prod_cur.fetchall()
            
            if not rows:
                logger.info(f"Table {table} is empty in production.")
                continue

            # Get column names
            colnames = [desc[0] for desc in prod_cur.description]
            
            # Prepare insert query
            columns = sql.SQL(', ').join(map(sql.Identifier, colnames))
            placeholders = sql.SQL(', ').join(sql.Placeholder() * len(colnames))
            insert_query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), columns, placeholders
            )

            # Insert data into dev
            dev_cur.executemany(insert_query, rows)
            logger.info(f"Successfully copied {len(rows)} rows into {table}.")

        # Re-enable triggers
        dev_cur.execute("SET session_replication_role = 'origin';")
        
        dev_conn.commit()
        logger.info("Migration completed successfully!")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        if 'dev_conn' in locals():
            dev_conn.rollback()
    finally:
        if 'prod_conn' in locals(): prod_conn.close()
        if 'dev_conn' in locals(): dev_conn.close()

if __name__ == "__main__":
    confirm = input("This will OVERWRITE your development database with production data. Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        migrate_db()
    else:
        print("Migration cancelled.")
