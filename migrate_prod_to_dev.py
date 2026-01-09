#!/usr/bin/env python3
"""
Migration script to copy data from production database to development database.
This will OVERWRITE all data in the development database with production data.
"""

import os
import psycopg2
from psycopg2 import sql
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

TABLES_TO_COPY = [
    "users",
    "settings",
    "ps_customers",
    "payment_customers",
    "credit_terms",
    "invoices",
    "invoice_items",
    "batch_picking_sessions",
    "batch_picked_items",
    "item_time_tracking",
    "order_time_breakdowns",
    "picking_exceptions",
    "shipments",
    "route_stops",
    "route_stop_invoices",
    "delivery_discrepancies",
    "delivery_discrepancy_events",
    "purchase_orders",
    "purchase_order_lines",
    "receiving_sessions",
    "receiving_lines",
]


def migrate_db():
    prod_url = os.environ.get("DATABASE_URL_PROD")
    dev_url = os.environ.get("DATABASE_URL")

    if not prod_url:
        logger.error("DATABASE_URL_PROD not found in environment variables.")
        logger.info("Please add DATABASE_URL_PROD secret with your production connection string.")
        return False

    if not dev_url:
        logger.error("DATABASE_URL (Development) not found in environment variables.")
        return False

    prod_conn = None
    dev_conn = None

    try:
        logger.info("Connecting to Production database...")
        prod_conn = psycopg2.connect(prod_url)
        prod_cur = prod_conn.cursor()

        logger.info("Connecting to Development database...")
        dev_conn = psycopg2.connect(dev_url)
        dev_cur = dev_conn.cursor()

        dev_cur.execute("SET session_replication_role = 'replica';")

        total_rows = 0
        tables_copied = 0

        for table in TABLES_TO_COPY:
            logger.info(f"Processing table: {table}")

            dev_cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                (table,)
            )
            result = dev_cur.fetchone()
            if not result or not result[0]:
                logger.warning(f"  Table {table} does not exist in development. Skipping.")
                continue

            prod_cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                (table,)
            )
            result = prod_cur.fetchone()
            if not result or not result[0]:
                logger.warning(f"  Table {table} does not exist in production. Skipping.")
                continue

            dev_cur.execute(sql.SQL("TRUNCATE TABLE {} CASCADE;").format(sql.Identifier(table)))

            prod_cur.execute(sql.SQL("SELECT * FROM {};").format(sql.Identifier(table)))
            rows = prod_cur.fetchall()

            if not rows:
                logger.info(f"  Table {table} is empty in production.")
                continue

            if prod_cur.description is None:
                logger.warning(f"  Could not get column info for {table}. Skipping.")
                continue

            colnames = [desc[0] for desc in prod_cur.description]

            columns = sql.SQL(', ').join(map(sql.Identifier, colnames))
            placeholders = sql.SQL(', ').join([sql.Placeholder()] * len(colnames))
            insert_query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), columns, placeholders
            )

            for row in rows:
                try:
                    dev_cur.execute(insert_query, row)
                except Exception as row_err:
                    logger.warning(f"  Error inserting row in {table}: {row_err}")
                    continue

            total_rows += len(rows)
            tables_copied += 1
            logger.info(f"  Copied {len(rows)} rows into {table}.")

        dev_cur.execute("SET session_replication_role = 'origin';")

        dev_conn.commit()
        logger.info(f"\nMigration completed successfully!")
        logger.info(f"Copied {total_rows} total rows across {tables_copied} tables.")
        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        if dev_conn:
            dev_conn.rollback()
        return False

    finally:
        if prod_conn:
            prod_conn.close()
        if dev_conn:
            dev_conn.close()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PRODUCTION TO DEVELOPMENT DATABASE MIGRATION")
    print("=" * 60)
    print("\nWARNING: This will OVERWRITE your development database")
    print("with data from production.\n")

    confirm = input("Type 'yes' to proceed: ")
    if confirm.lower() == 'yes':
        print()
        success = migrate_db()
        if success:
            print("\nYour development database now mirrors production.")
        else:
            print("\nMigration failed. Check the errors above.")
    else:
        print("\nMigration cancelled.")
