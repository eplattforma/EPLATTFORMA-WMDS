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
    "discrepancy_types",
    "ps_customers",
    "ps_items_dw",
    "payment_customers",
    "credit_terms",
    "invoices",
    "invoice_items",
    "batch_picking_sessions",
    "batch_session_invoices",
    "batch_picked_items",
    "item_time_tracking",
    "order_time_breakdown",
    "picking_exceptions",
    "idle_periods",
    "shifts",
    "time_tracking_alerts",
    "shipments",
    "shipment_orders",
    "route_stop",
    "route_stop_invoice",
    "cod_receipts",
    "pod_records",
    "receipt_log",
    "receipt_sequence",
    "delivery_discrepancies",
    "delivery_discrepancy_events",
    "delivery_events",
    "delivery_lines",
    "invoice_delivery_events",
    "invoice_post_delivery_cases",
    "invoice_route_history",
    "route_delivery_events",
    "reroute_requests",
    "shipping_events",
    "stock_positions",
    "stock_resolutions",
    "purchase_orders",
    "purchase_order_lines",
    "receiving_sessions",
    "receiving_lines",
    "sync_jobs",
    "sync_state",
    "activity_logs",
    "wms_category_defaults",
    "wms_classification_runs",
    "wms_item_overrides",
]


def migrate_db():
    prod_url = os.environ.get("DATABASE_URL_PROD")
    dev_url = os.environ.get("DATABASE_URL")

    if not prod_url:
        logger.error("DATABASE_URL_PROD not found in environment variables.")
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

        logger.info("Clearing development tables in reverse order...")
        for table in reversed(TABLES_TO_COPY):
            dev_cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                (table,)
            )
            result = dev_cur.fetchone()
            if result and result[0]:
                try:
                    dev_cur.execute(sql.SQL("DELETE FROM {};").format(sql.Identifier(table)))
                    dev_conn.commit()
                    logger.info(f"  Cleared {table}")
                except Exception as del_err:
                    dev_conn.rollback()
                    logger.warning(f"  Could not clear {table}: {str(del_err)[:60]}")

        total_rows = 0
        tables_copied = 0

        logger.info("\nCopying data from production...")
        for table in TABLES_TO_COPY:
            dev_cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                (table,)
            )
            result = dev_cur.fetchone()
            if not result or not result[0]:
                continue

            prod_cur.execute(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
                (table,)
            )
            result = prod_cur.fetchone()
            if not result or not result[0]:
                continue

            prod_cur.execute(sql.SQL("SELECT * FROM {};").format(sql.Identifier(table)))
            rows = prod_cur.fetchall()

            if not rows:
                logger.info(f"  {table}: empty")
                continue

            if prod_cur.description is None:
                continue

            colnames = [desc[0] for desc in prod_cur.description]

            columns = sql.SQL(', ').join(map(sql.Identifier, colnames))
            placeholders = sql.SQL(', ').join([sql.Placeholder()] * len(colnames))
            insert_query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table), columns, placeholders
            )

            inserted = 0
            errors = 0
            for row in rows:
                try:
                    dev_cur.execute(insert_query, row)
                    inserted += 1
                except Exception as row_err:
                    dev_conn.rollback()
                    errors += 1
                    if errors <= 2:
                        logger.warning(f"    {table} row error: {str(row_err)[:60]}")

            dev_conn.commit()
            total_rows += inserted
            if inserted > 0:
                tables_copied += 1
            
            status = f"{inserted} rows"
            if errors > 0:
                status += f" ({errors} skipped)"
            logger.info(f"  {table}: {status}")

        logger.info(f"\nMigration completed!")
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
            print("\nMigration had issues. Check the errors above.")
    else:
        print("\nMigration cancelled.")
