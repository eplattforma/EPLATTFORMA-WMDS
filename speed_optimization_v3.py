#!/usr/bin/env python3
"""
Ultra-aggressive speed optimization for warehouse picking system
Disables non-essential features and optimizes critical paths
"""

import logging
import os
import sys

def apply_emergency_speed_fixes():
    """Apply emergency speed optimizations"""
    print("🚀 Applying ultra-aggressive speed optimizations...")
    
    # 1. Disable debug logging completely
    logging.disable(logging.DEBUG)
    logging.disable(logging.INFO)
    logging.getLogger().setLevel(logging.ERROR)
    
    # 2. Set environment variables for speed
    os.environ['FLASK_ENV'] = 'production'
    os.environ['FLASK_DEBUG'] = '0'
    os.environ['PYTHONOPTIMIZE'] = '2'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    
    print("✅ Speed optimizations applied")

def cleanup_database_performance():
    """Clean up database for better performance"""
    import psycopg2
    
    try:
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        conn.autocommit = True
        cur = conn.cursor()
        
        print("🗄️ Cleaning database for performance...")
        
        # Clean old logs (keep only 50)
        cur.execute("""
            DELETE FROM activity_logs 
            WHERE id NOT IN (
                SELECT id FROM activity_logs 
                ORDER BY id DESC LIMIT 50
            )
        """)
        
        # Clean excessive time tracking (keep only 200)
        cur.execute("""
            DELETE FROM item_time_tracking 
            WHERE id NOT IN (
                SELECT id FROM item_time_tracking 
                ORDER BY id DESC LIMIT 200
            )
        """)
        
        # Update statistics
        cur.execute("ANALYZE;")
        
        cur.close()
        conn.close()
        print("✅ Database cleanup completed")
        
    except Exception as e:
        print(f"⚠️ Database cleanup warning: {e}")

if __name__ == "__main__":
    apply_emergency_speed_fixes()
    cleanup_database_performance()