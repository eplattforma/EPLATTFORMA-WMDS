#!/usr/bin/env python3
"""
Migration script to copy data from production database to development database.
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


def table_exists(cur, table_name):
    cur.execute(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = %s);",
        (table_name,)
    )
    result = cur.fetchone()
    return result and result[0]


def migrate_db():
    prod_url = os.environ.get("DATABASE_URL_PROD")
    dev_url = os.environ.get("DATABASE_URL")

    if not prod_url or not dev_url:
        logger.error("Missing database URLs")
        return False

    prod_conn = None
    dev_conn = None

    try:
        logger.info("Connecting to databases...")
        prod_conn = psycopg2.connect(prod_url)
        prod_conn.autocommit = True
        prod_cur = prod_conn.cursor()

        dev_conn = psycopg2.connect(dev_url)
        dev_conn.autocommit = True
        dev_cur = dev_conn.cursor()

        logger.info("Clearing development tables...")
        for table in reversed(TABLES_TO_COPY):
            if table_exists(dev_cur, table):
                try:
                    dev_cur.execute(sql.SQL("TRUNCATE TABLE {} CASCADE;").format(sql.Identifier(table)))
                    logger.info(f"  Truncated {table}")
                except Exception as e:
                    logger.warning(f"  Could not truncate {table}: {str(e)[:50]}")

        total_rows = 0
        tables_copied = 0

        logger.info("\nCopying data...")
        for table in TABLES_TO_COPY:
            if not table_exists(dev_cur, table) or not table_exists(prod_cur, table):
                continue

            prod_cur.execute(sql.SQL("SELECT * FROM {};").format(sql.Identifier(table)))
            rows = prod_cur.fetchall()

            if not rows or prod_cur.description is None:
                continue

            colnames = [desc[0] for desc in prod_cur.description]
            columns = sql.SQL(', ').join(map(sql.Identifier, colnames))
            placeholders = sql.SQL(', ').join([sql.Placeholder()] * len(colnames))
            insert_query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                sql.Identifier(table), columns, placeholders
            )

            inserted = 0
            for row in rows:
                try:
                    dev_cur.execute(insert_query, row)
                    inserted += 1
                except Exception:
                    pass

            if inserted > 0:
                total_rows += inserted
                tables_copied += 1
                logger.info(f"  {table}: {inserted} rows")

        logger.info(f"\nDone! Copied {total_rows} rows across {tables_copied} tables.")
        return True

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return False

    finally:
        if prod_conn:
            prod_conn.close()
        if dev_conn:
            dev_conn.close()


if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("PRODUCTION → DEVELOPMENT DATABASE MIGRATION")
    print("=" * 50 + "\n")

    confirm = input("Type 'yes' to proceed: ")
    if confirm.lower() == 'yes':
        migrate_db()
    else:
        print("Cancelled.")
