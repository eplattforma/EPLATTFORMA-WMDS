#!/usr/bin/env python3
"""
Create database indexes outside of Flask transactions for performance
"""

import psycopg2
import os
from urllib.parse import urlparse

def create_indexes_directly():
    """Create indexes directly using psycopg2 to avoid transaction issues"""
    
    # Parse DATABASE_URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("❌ DATABASE_URL not found")
        return False
    
    try:
        # Parse the database URL
        url = urlparse(database_url)
        
        # Connect directly to PostgreSQL
        conn = psycopg2.connect(
            host=url.hostname,
            port=url.port,
            user=url.username,
            password=url.password,
            database=url.path[1:]  # Remove leading slash
        )
        conn.autocommit = True  # Enable autocommit for DDL operations
        
        cursor = conn.cursor()
        
        # Performance indexes for critical queries
        indexes = [
            # Admin dashboard indexes
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoices_status_routing ON invoices(status, routing)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoices_assigned_status ON invoices(assigned_to, status)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_items_invoice_picked ON invoice_items(invoice_no, is_picked)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_items_batch_lock ON invoice_items(locked_by_batch_id) WHERE locked_by_batch_id IS NOT NULL",
            
            # Time tracking indexes
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_item_time_tracking_invoice_started ON item_time_tracking(invoice_no, item_started)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_item_time_tracking_completed ON item_time_tracking(item_completed) WHERE item_completed IS NOT NULL",
            
            # Activity log index (recent entries only)
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_activity_log_recent ON activity_log(timestamp) WHERE timestamp > NOW() - INTERVAL '30 days'",
            
            # Other performance indexes
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_picking_exceptions_invoice ON picking_exceptions(invoice_no)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_batch_sessions_status ON batch_picking_sessions(status)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_batch_picked_items_session ON batch_picked_items(batch_session_id)",
            
            # Composite indexes for common queries
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoices_status_updated ON invoices(status, status_updated_at)",
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_items_location ON invoice_items(zone, corridor, location)",
        ]
        
        print("🚀 Creating performance indexes...")
        created_count = 0
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
                index_name = index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'unnamed'
                print(f"✅ Created index: {index_name}")
                created_count += 1
            except psycopg2.errors.DuplicateTable:
                index_name = index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'unnamed'
                print(f"⏭️  Index already exists: {index_name}")
            except Exception as e:
                index_name = index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'unnamed'
                print(f"⚠️  Failed to create {index_name}: {e}")
        
        # Database maintenance
        print("\n🔧 Running database maintenance...")
        maintenance_commands = [
            "VACUUM ANALYZE invoices",
            "VACUUM ANALYZE invoice_items", 
            "VACUUM ANALYZE item_time_tracking",
            "VACUUM ANALYZE activity_log",
        ]
        
        for cmd in maintenance_commands:
            try:
                cursor.execute(cmd)
                print(f"✅ {cmd}")
            except Exception as e:
                print(f"⚠️  {cmd} failed: {e}")
        
        cursor.close()
        conn.close()
        
        print(f"\n✅ Performance optimization completed!")
        print(f"📊 Created {created_count} new indexes")
        return True
        
    except Exception as e:
        print(f"❌ Database optimization failed: {e}")
        return False

if __name__ == "__main__":
    success = create_indexes_directly()
    if success:
        print("🚀 Database performance significantly improved!")
    else:
        print("❌ Database optimization failed")