#!/usr/bin/env python
"""
Database Maintenance Script for Warehouse Management System
Handles database optimization, cleanup, and maintenance operations
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from sqlalchemy import text, create_engine, inspect
from sqlalchemy.pool import NullPool

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DatabaseMaintenance:
    """Handles all database maintenance operations"""
    
    def __init__(self, database_url=None):
        """Initialize database connection"""
        if database_url is None:
            database_url = os.environ.get('DATABASE_URL')
        
        if not database_url:
            raise ValueError("DATABASE_URL environment variable not set")
        
        self.engine = create_engine(database_url, poolclass=NullPool)
        self.logger = logger
    
    def vacuum_database(self):
        """Run VACUUM to reclaim storage space"""
        try:
            with self.engine.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text("VACUUM ANALYZE"))
                conn.commit()
            self.logger.info("✓ Database VACUUM completed successfully")
            return True
        except Exception as e:
            self.logger.error(f"✗ VACUUM failed: {str(e)}")
            return False
    
    def analyze_tables(self):
        """Analyze all tables for query optimization"""
        try:
            with self.engine.connect() as conn:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                conn.execute(text("ANALYZE"))
                conn.commit()
            self.logger.info("✓ Database ANALYZE completed successfully")
            return True
        except Exception as e:
            self.logger.error(f"✗ ANALYZE failed: {str(e)}")
            return False
    
    def reindex_tables(self):
        """Rebuild all indexes"""
        try:
            with self.engine.connect() as conn:
                inspector = inspect(self.engine)
                tables = inspector.get_table_names()
                
                for table in tables:
                    try:
                        indexes = inspector.get_indexes(table)
                        for index in indexes:
                            index_name = index['name']
                            conn.execution_options(isolation_level="AUTOCOMMIT")
                            conn.execute(text(f"REINDEX INDEX CONCURRENTLY {index_name}"))
                        self.logger.info(f"  ✓ Reindexed table: {table}")
                    except Exception as e:
                        self.logger.warning(f"  ⚠ Could not reindex {table}: {str(e)}")
                
                conn.commit()
            self.logger.info("✓ Index rebuild completed")
            return True
        except Exception as e:
            self.logger.error(f"✗ Reindex failed: {str(e)}")
            return False
    
    def cleanup_old_logs(self, days=30):
        """Clean up old activity logs (default: older than 30 days)"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            with self.engine.connect() as conn:
                result = conn.execute(
                    text("""
                        DELETE FROM activity_logs 
                        WHERE timestamp < :cutoff_date
                    """),
                    {"cutoff_date": cutoff_date}
                )
                rows_deleted = result.rowcount
                conn.commit()
            
            self.logger.info(f"✓ Deleted {rows_deleted} old activity log entries (older than {days} days)")
            return True
        except Exception as e:
            self.logger.error(f"✗ Log cleanup failed: {str(e)}")
            return False
    
    def cleanup_soft_deleted(self):
        """Remove soft-deleted records"""
        try:
            with self.engine.connect() as conn:
                # Get list of tables with soft delete capability
                result = conn.execute(
                    text("""
                        SELECT table_name FROM information_schema.tables 
                        WHERE table_schema = 'public'
                    """)
                )
                tables = result.fetchall()
                
                deleted_count = 0
                for (table_name,) in tables:
                    try:
                        # Check if table has deleted_at column
                        result = conn.execute(
                            text(f"""
                                SELECT column_name FROM information_schema.columns 
                                WHERE table_name = '{table_name}' AND column_name = 'deleted_at'
                            """)
                        )
                        if result.fetchone():
                            res = conn.execute(
                                text(f"DELETE FROM {table_name} WHERE deleted_at IS NOT NULL")
                            )
                            deleted_count += res.rowcount
                    except:
                        pass
                
                conn.commit()
                self.logger.info(f"✓ Cleaned up {deleted_count} soft-deleted records")
                return True
        except Exception as e:
            self.logger.error(f"✗ Soft delete cleanup failed: {str(e)}")
            return False
    
    def check_database_health(self):
        """Check overall database health"""
        try:
            with self.engine.connect() as conn:
                # Check connection
                conn.execute(text("SELECT 1"))
                self.logger.info("✓ Database connection: OK")
                
                # Check table count
                result = conn.execute(
                    text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
                )
                table_count = result.scalar()
                self.logger.info(f"✓ Tables in database: {table_count}")
                
                # Check for unused indexes
                result = conn.execute(
                    text("""
                        SELECT count(*) FROM pg_stat_user_indexes 
                        WHERE idx_scan = 0 AND indexrelname NOT LIKE 'pg_toast%'
                    """)
                )
                unused_indexes = result.scalar() or 0
                if unused_indexes > 0:
                    self.logger.warning(f"⚠ Unused indexes found: {unused_indexes}")
                
                return True
        except Exception as e:
            self.logger.error(f"✗ Health check failed: {str(e)}")
            return False
    
    def run_full_maintenance(self):
        """Run complete maintenance cycle"""
        self.logger.info("=" * 60)
        self.logger.info("Starting Database Maintenance")
        self.logger.info("=" * 60)
        
        success = True
        
        # Health check
        self.check_database_health()
        
        # Clean old data
        self.cleanup_old_logs(days=30)
        self.cleanup_soft_deleted()
        
        # Optimize
        success &= self.analyze_tables()
        success &= self.reindex_tables()
        success &= self.vacuum_database()
        
        self.logger.info("=" * 60)
        if success:
            self.logger.info("✓ Database Maintenance Completed Successfully")
        else:
            self.logger.info("⚠ Database Maintenance Completed with Warnings")
        self.logger.info("=" * 60)
        
        return success


def main():
    """Main entry point"""
    try:
        maintenance = DatabaseMaintenance()
        success = maintenance.run_full_maintenance()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        sys.exit(1)


if __name__ == '__main__':
    main()
