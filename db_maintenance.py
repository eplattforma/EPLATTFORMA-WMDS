#!/usr/bin/env python3
"""
Database Maintenance Script for Picking Application
Performs routine database optimization, cleanup, and health checks
"""

import os
import sys
import logging
from datetime import datetime, timedelta
import psycopg2
from psycopg2 import sql
import argparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DatabaseMaintenance:
    def __init__(self, database_url):
        """Initialize database connection"""
        self.database_url = database_url
        self.conn = None
        self.cursor = None
    
    def connect(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(self.database_url)
            self.cursor = self.conn.cursor()
            logger.info("✓ Connected to database")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to connect to database: {str(e)}")
            return False
    
    def disconnect(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()
        logger.info("✓ Disconnected from database")
    
    def execute_query(self, query, params=None, fetch=False):
        """Execute SQL query safely"""
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
            
            if fetch:
                return self.cursor.fetchall()
            self.conn.commit()
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"✗ Query failed: {str(e)}")
            return None
    
    def vacuum_and_analyze(self):
        """Run VACUUM and ANALYZE on all tables"""
        logger.info("\n📊 Running VACUUM and ANALYZE...")
        try:
            self.cursor.execute("VACUUM ANALYZE;")
            self.conn.commit()
            logger.info("✓ VACUUM and ANALYZE completed")
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"✗ VACUUM/ANALYZE failed: {str(e)}")
            return False
    
    def reindex_tables(self):
        """Rebuild indexes on all tables"""
        logger.info("\n🔧 Reindexing tables...")
        try:
            self.cursor.execute("REINDEX DATABASE CONCURRENTLY;")
            self.conn.commit()
            logger.info("✓ Reindexing completed")
            return True
        except Exception as e:
            self.conn.rollback()
            logger.error(f"✗ Reindexing failed: {str(e)}")
            return False
    
    def cleanup_idle_periods(self, days=30):
        """Remove completed idle periods older than specified days"""
        logger.info(f"\n🗑️  Cleaning up idle periods older than {days} days...")
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            query = """
                DELETE FROM idle_periods 
                WHERE end_time IS NOT NULL 
                AND end_time < %s
            """
            self.cursor.execute(query, (cutoff_date,))
            deleted = self.cursor.rowcount
            self.conn.commit()
            logger.info(f"✓ Deleted {deleted} old idle periods")
            return deleted
        except Exception as e:
            self.conn.rollback()
            logger.error(f"✗ Idle period cleanup failed: {str(e)}")
            return 0
    
    def cleanup_activity_logs(self, days=90):
        """Remove old activity logs beyond retention period"""
        logger.info(f"\n🗑️  Cleaning up activity logs older than {days} days...")
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            query = """
                DELETE FROM activity_logs 
                WHERE timestamp < %s
            """
            self.cursor.execute(query, (cutoff_date,))
            deleted = self.cursor.rowcount
            self.conn.commit()
            logger.info(f"✓ Deleted {deleted} old activity logs")
            return deleted
        except Exception as e:
            self.conn.rollback()
            logger.error(f"✗ Activity log cleanup failed: {str(e)}")
            return 0
    
    def cleanup_broken_idle_periods(self):
        """Remove idle periods with NULL end_time older than 24 hours"""
        logger.info("\n🔍 Cleaning up orphaned idle periods...")
        try:
            query = """
                DELETE FROM idle_periods 
                WHERE end_time IS NULL 
                AND start_time < NOW() - INTERVAL '24 hours'
            """
            self.cursor.execute(query)
            deleted = self.cursor.rowcount
            self.conn.commit()
            logger.info(f"✓ Deleted {deleted} orphaned idle periods")
            return deleted
        except Exception as e:
            self.conn.rollback()
            logger.error(f"✗ Orphaned idle period cleanup failed: {str(e)}")
            return 0
    
    def check_connection_pool(self):
        """Check current database connections"""
        logger.info("\n📡 Checking database connections...")
        try:
            query = """
                SELECT 
                    datname,
                    count(*) as connections,
                    max_conn,
                    ROUND(100.0 * count(*) / max_conn, 2) as usage_percent
                FROM (
                    SELECT datname, count(*) as cnt FROM pg_stat_activity GROUP BY datname
                ) s,
                (SELECT setting::int as max_conn FROM pg_settings WHERE name = 'max_connections') m
                WHERE datname IS NOT NULL
                GROUP BY datname, max_conn
            """
            results = self.execute_query(query, fetch=True)
            
            for db_info in results:
                db_name, conn_count, max_conn, usage_percent = db_info
                logger.info(f"  Database: {db_name}")
                logger.info(f"    Active connections: {conn_count}/{max_conn} ({usage_percent}%)")
                
                if usage_percent > 80:
                    logger.warning(f"    ⚠️  High connection usage!")
            
            return results
        except Exception as e:
            logger.error(f"✗ Connection check failed: {str(e)}")
            return None
    
    def get_table_sizes(self):
        """Get size statistics for all tables"""
        logger.info("\n📈 Table size analysis...")
        try:
            query = """
                SELECT 
                    schemaname,
                    tablename,
                    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
                    pg_total_relation_size(schemaname||'.'||tablename) AS size_bytes
                FROM pg_tables 
                WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
            """
            results = self.execute_query(query, fetch=True)
            
            logger.info("  Top 10 largest tables:")
            for i, (schema, table, size_str, size_bytes) in enumerate(results[:10], 1):
                logger.info(f"    {i}. {schema}.{table}: {size_str}")
            
            return results
        except Exception as e:
            logger.error(f"✗ Table size analysis failed: {str(e)}")
            return None
    
    def get_database_health(self):
        """Generate comprehensive health report"""
        logger.info("\n🏥 Database Health Report")
        logger.info("=" * 50)
        
        try:
            # Total size
            query = "SELECT pg_size_pretty(pg_database_size(current_database()))"
            result = self.execute_query(query, fetch=True)
            if result:
                logger.info(f"  Total database size: {result[0][0]}")
            
            # Table count
            query = """
                SELECT count(*) FROM information_schema.tables 
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            """
            result = self.execute_query(query, fetch=True)
            if result:
                logger.info(f"  Total tables: {result[0][0]}")
            
            # Record counts
            query = """
                SELECT 
                    'shifts' as table_name, count(*) as count FROM shifts
                UNION ALL
                SELECT 'idle_periods', count(*) FROM idle_periods
                UNION ALL
                SELECT 'activity_logs', count(*) FROM activity_logs
                UNION ALL
                SELECT 'invoices', count(*) FROM invoices
                ORDER BY table_name
            """
            results = self.execute_query(query, fetch=True)
            if results:
                logger.info("  Record counts:")
                for table_name, count in results:
                    logger.info(f"    {table_name}: {count:,}")
            
            logger.info("=" * 50)
            return True
        except Exception as e:
            logger.error(f"✗ Health check failed: {str(e)}")
            return False
    
    def run_full_maintenance(self, cleanup_days_idle=30, cleanup_days_logs=90):
        """Run complete maintenance suite"""
        logger.info("🚀 Starting full database maintenance...")
        logger.info("=" * 50)
        
        success_count = 0
        
        # Health check
        if self.get_database_health():
            success_count += 1
        
        # Connections check
        self.check_connection_pool()
        
        # Table sizes
        self.get_table_sizes()
        
        # Cleanup operations
        if self.cleanup_broken_idle_periods():
            success_count += 1
        
        if self.cleanup_idle_periods(cleanup_days_idle) >= 0:
            success_count += 1
        
        if self.cleanup_activity_logs(cleanup_days_logs) >= 0:
            success_count += 1
        
        # Optimization
        if self.vacuum_and_analyze():
            success_count += 1
        
        # Reindex
        if self.reindex_tables():
            success_count += 1
        
        logger.info("=" * 50)
        logger.info(f"✅ Maintenance complete! ({success_count}/6 operations successful)")


def main():
    parser = argparse.ArgumentParser(description='Database Maintenance Script')
    parser.add_argument('--full', action='store_true', help='Run full maintenance')
    parser.add_argument('--vacuum', action='store_true', help='Run VACUUM/ANALYZE only')
    parser.add_argument('--reindex', action='store_true', help='Reindex tables only')
    parser.add_argument('--cleanup-idle', type=int, default=30, help='Days for idle period cleanup (default: 30)')
    parser.add_argument('--cleanup-logs', type=int, default=90, help='Days for activity log cleanup (default: 90)')
    parser.add_argument('--health', action='store_true', help='Show health report only')
    
    args = parser.parse_args()
    
    # Get database URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("❌ DATABASE_URL environment variable not set")
        sys.exit(1)
    
    # Initialize maintenance object
    maintenance = DatabaseMaintenance(database_url)
    
    if not maintenance.connect():
        sys.exit(1)
    
    try:
        # If no specific operation, run full maintenance
        if not any([args.full, args.vacuum, args.reindex, args.health]):
            maintenance.run_full_maintenance(args.cleanup_idle, args.cleanup_logs)
        else:
            if args.health:
                maintenance.get_database_health()
                maintenance.check_connection_pool()
            
            if args.vacuum:
                maintenance.vacuum_and_analyze()
            
            if args.reindex:
                maintenance.reindex_tables()
            
            if args.full:
                maintenance.run_full_maintenance(args.cleanup_idle, args.cleanup_logs)
    
    finally:
        maintenance.disconnect()


if __name__ == '__main__':
    main()
