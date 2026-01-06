#!/usr/bin/env python3
"""
SQLite-compatible migration for soft delete columns.

SQLite supports basic ALTER TABLE ADD COLUMN, which we use here.
This script adds the necessary columns for the soft delete system.

Usage:
    python migration_soft_delete_sqlite.py
"""

import os
import sys

# Ensure we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from sqlalchemy import text

def column_exists(table_name, column_name):
    """Check if a column exists in a table"""
    inspector = db.inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns

def add_column_if_missing(table_name, column_name, column_type):
    """Add a column to a table if it doesn't exist"""
    if not column_exists(table_name, column_name):
        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        print(f"  Adding {table_name}.{column_name}...")
        db.session.execute(text(sql))
        db.session.commit()
        return True
    return False

def run_migration():
    """Run the soft delete migration for SQLite"""
    with app.app_context():
        print("🔄 Running soft delete migration for SQLite...")
        print(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        
        try:
            added_count = 0
            
            # User table - Activatable columns
            print("\n📋 Migrating users table...")
            if add_column_if_missing('users', 'is_active', 'BOOLEAN NOT NULL DEFAULT 1'):
                added_count += 1
            if add_column_if_missing('users', 'disabled_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('users', 'disabled_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            # Invoices table - Soft delete columns
            print("\n📋 Migrating invoices table...")
            if add_column_if_missing('invoices', 'deleted_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('invoices', 'deleted_by', 'VARCHAR(64) NULL'):
                added_count += 1
            if add_column_if_missing('invoices', 'delete_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            # Shipments table - Soft delete columns
            print("\n📋 Migrating shipments table...")
            if add_column_if_missing('shipments', 'deleted_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('shipments', 'deleted_by', 'VARCHAR(64) NULL'):
                added_count += 1
            if add_column_if_missing('shipments', 'delete_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            # RouteStop table - Soft delete columns
            print("\n📋 Migrating route_stop table...")
            if add_column_if_missing('route_stop', 'deleted_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('route_stop', 'deleted_by', 'VARCHAR(64) NULL'):
                added_count += 1
            if add_column_if_missing('route_stop', 'delete_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            # BatchPickingSession table - Soft delete columns
            print("\n📋 Migrating batch_picking_sessions table...")
            if add_column_if_missing('batch_picking_sessions', 'deleted_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('batch_picking_sessions', 'deleted_by', 'VARCHAR(64) NULL'):
                added_count += 1
            if add_column_if_missing('batch_picking_sessions', 'delete_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            # PSCustomer table - Soft delete + Activatable columns
            print("\n📋 Migrating ps_customers table...")
            if add_column_if_missing('ps_customers', 'deleted_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('ps_customers', 'deleted_by', 'VARCHAR(64) NULL'):
                added_count += 1
            if add_column_if_missing('ps_customers', 'delete_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            if add_column_if_missing('ps_customers', 'is_active', 'BOOLEAN NOT NULL DEFAULT 1'):
                added_count += 1
                # Sync is_active with existing active column
                db.session.execute(text("UPDATE ps_customers SET is_active = active WHERE active IS NOT NULL"))
                db.session.commit()
            if add_column_if_missing('ps_customers', 'disabled_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('ps_customers', 'disabled_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            # PurchaseOrder table - Soft delete columns
            print("\n📋 Migrating purchase_orders table...")
            if add_column_if_missing('purchase_orders', 'deleted_at', 'TIMESTAMP NULL'):
                added_count += 1
            if add_column_if_missing('purchase_orders', 'deleted_by', 'VARCHAR(64) NULL'):
                added_count += 1
            if add_column_if_missing('purchase_orders', 'delete_reason', 'VARCHAR(255) NULL'):
                added_count += 1
            
            print(f"\n✅ Migration completed successfully!")
            print(f"   Added {added_count} columns")
            
            # Verify the columns were added
            print("\n🔍 Verifying migration...")
            inspector = db.inspect(db.engine)
            
            # Check critical tables
            for table_name in ['users', 'invoices', 'shipments', 'route_stop', 'batch_picking_sessions', 'ps_customers', 'purchase_orders']:
                if inspector.has_table(table_name):
                    columns = [col['name'] for col in inspector.get_columns(table_name)]
                    print(f"  {table_name}: {len(columns)} columns")
            
            print("\n✅ Soft delete system is ready to use!")
            
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            sys.exit(1)

if __name__ == '__main__':
    run_migration()
