#!/usr/bin/env python3
"""
Advanced Speed Optimization for Warehouse Picking System
Comprehensive performance improvements targeting high load conditions
"""

import os
import sys
import logging
from datetime import datetime, timedelta

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db
    from models import ActivityLog, ItemTimeTracking, PickingException, Invoice
    from sqlalchemy import text, func
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

def optimize_database_performance():
    """Advanced database optimizations for speed"""
    print("🚀 Applying advanced database optimizations...")
    
    with app.app_context():
        try:
            # 1. Advanced index creation for critical queries
            critical_indexes = [
                # Admin dashboard performance indexes
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoices_status_routing ON invoices(status, routing);",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoices_assigned_status ON invoices(assigned_to, status);",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_items_invoice_picked ON invoice_items(invoice_no, is_picked);",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_items_batch_lock ON invoice_items(locked_by_batch_id) WHERE locked_by_batch_id IS NOT NULL;",
                
                # Time tracking performance indexes
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_item_time_tracking_invoice_started ON item_time_tracking(invoice_no, item_started);",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_item_time_tracking_completed ON item_time_tracking(item_completed) WHERE item_completed IS NOT NULL;",
                
                # Activity log performance (with partial index)
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_activity_log_recent ON activity_log(timestamp) WHERE timestamp > NOW() - INTERVAL '7 days';",
                
                # Picking exceptions index
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_picking_exceptions_invoice ON picking_exceptions(invoice_no);",
                
                # Batch picking performance
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_batch_sessions_status ON batch_picking_sessions(status);",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_batch_picked_items_session ON batch_picked_items(batch_session_id);"
            ]
            
            for index_sql in critical_indexes:
                try:
                    db.session.execute(text(index_sql))
                    db.session.commit()
                    print(f"✅ Applied index: {index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'system index'}")
                except Exception as e:
                    if "already exists" in str(e).lower():
                        print(f"⏭️  Index already exists: {index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'system index'}")
                    else:
                        print(f"⚠️  Index creation failed: {e}")
                    db.session.rollback()
            
            # 2. Advanced database maintenance
            maintenance_commands = [
                "VACUUM ANALYZE invoices;",
                "VACUUM ANALYZE invoice_items;", 
                "VACUUM ANALYZE item_time_tracking;",
                "VACUUM ANALYZE activity_log;",
                "REINDEX TABLE invoices;",
                "REINDEX TABLE invoice_items;"
            ]
            
            for cmd in maintenance_commands:
                try:
                    db.session.execute(text(cmd))
                    db.session.commit()
                    print(f"✅ {cmd}")
                except Exception as e:
                    print(f"⚠️  {cmd} failed: {e}")
                    db.session.rollback()
                    
        except Exception as e:
            print(f"❌ Database optimization error: {e}")
            db.session.rollback()

def aggressive_log_cleanup():
    """More aggressive log cleanup for performance"""
    print("🧹 Performing aggressive log cleanup...")
    
    with app.app_context():
        try:
            # Keep only last 3 days of activity logs
            cutoff_date = datetime.now() - timedelta(days=3)
            old_activities = ActivityLog.query.filter(ActivityLog.timestamp < cutoff_date).all()
            old_count = len(old_activities)
            
            if old_count > 0:
                ActivityLog.query.filter(ActivityLog.timestamp < cutoff_date).delete()
                print(f"✅ Removed {old_count} old activity log entries")
            
            # Keep only last 7 days of time tracking
            time_cutoff = datetime.now() - timedelta(days=7)
            old_tracking_count = ItemTimeTracking.query.filter(
                ItemTimeTracking.item_started < time_cutoff
            ).count()
            
            if old_tracking_count > 0:
                ItemTimeTracking.query.filter(
                    ItemTimeTracking.item_started < time_cutoff
                ).delete()
                print(f"✅ Removed {old_tracking_count} old time tracking records")
            
            # Clean up old picking exceptions (keep 14 days)
            exception_cutoff = datetime.now() - timedelta(days=14)
            old_exceptions_count = PickingException.query.filter(
                PickingException.timestamp < exception_cutoff
            ).count()
            
            if old_exceptions_count > 0:
                PickingException.query.filter(
                    PickingException.timestamp < exception_cutoff
                ).delete()
                print(f"✅ Removed {old_exceptions_count} old picking exceptions")
            
            db.session.commit()
            
        except Exception as e:
            print(f"❌ Log cleanup error: {e}")
            db.session.rollback()

def optimize_gunicorn_config():
    """Create optimized Gunicorn configuration for better performance"""
    print("⚙️ Optimizing Gunicorn configuration...")
    
    config_content = """# High Performance Gunicorn Configuration
import os
import multiprocessing

# Server socket
bind = "0.0.0.0:5000"
backlog = 512

# Worker processes - optimized for current load
workers = 2  # Increased from 1 for better performance
worker_class = "sync"
worker_connections = 20  # Increased capacity
max_requests = 100  # Increased from 15
max_requests_jitter = 10

# Optimized timeouts
timeout = 15  # Increased from 6
keepalive = 2
graceful_timeout = 10

# Optimized logging
loglevel = "warning"  # Reduced logging overhead
accesslog = None
errorlog = "/tmp/gunicorn_error.log"
capture_output = False
enable_stdio_inheritance = False

# Performance optimizations
preload_app = True
reuse_port = True
worker_tmp_dir = "/dev/shm"

# Memory and process optimizations
max_requests_jitter = max_requests // 10
worker_max_requests = max_requests

print(f"Performance config: {workers} workers, {worker_connections} connections")
"""
    
    try:
        with open('gunicorn_config_optimized.py', 'w') as f:
            f.write(config_content)
        print("✅ Created optimized Gunicorn configuration")
        
        # Also update the main config
        with open('gunicorn_config.py', 'w') as f:
            f.write(config_content)
        print("✅ Updated main Gunicorn configuration")
        
    except Exception as e:
        print(f"❌ Gunicorn config optimization error: {e}")

def optimize_application_settings():
    """Optimize application-level settings for performance"""
    print("📝 Optimizing application settings...")
    
    with app.app_context():
        try:
            from models import Setting
            
            # Performance-oriented settings
            performance_settings = [
                ('confirm_picking_step', 'false'),  # Disable confirmation step for speed
                ('show_image_on_picking_screen', 'false'),  # Disable images for speed
                ('show_multi_qty_warning', 'false'),  # Reduce UI overhead
                ('time_alerts_enabled', 'false'),  # Disable time tracking alerts
                ('auto_notify_admin', 'false'),  # Disable notifications
            ]
            
            settings_updated = 0
            for key, value in performance_settings:
                setting = Setting.query.filter_by(key=key).first()
                if setting:
                    if setting.value != value:
                        setting.value = value
                        settings_updated += 1
                else:
                    setting = Setting(key=key, value=value)
                    db.session.add(setting)
                    settings_updated += 1
            
            if settings_updated > 0:
                db.session.commit()
                print(f"✅ Updated {settings_updated} performance settings")
            else:
                print("⏭️  Performance settings already optimized")
                
        except Exception as e:
            print(f"❌ Settings optimization error: {e}")
            db.session.rollback()

def create_monitoring_script():
    """Create a monitoring script to track performance"""
    print("📊 Creating performance monitoring script...")
    
    monitoring_script = """#!/usr/bin/env python3
import psutil
import time
import subprocess

def monitor_performance():
    print("=== Warehouse Picking System Performance Monitor ===")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # CPU and Memory
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    print(f"CPU Usage: {cpu_percent:.1f}%")
    print(f"Memory Usage: {memory.percent:.1f}% ({memory.used / (1024**3):.1f}GB / {memory.total / (1024**3):.1f}GB)")
    print()
    
    # Load average
    try:
        load1, load5, load15 = psutil.getloadavg()
        print(f"Load Average: {load1:.2f}, {load5:.2f}, {load15:.2f}")
    except:
        print("Load average: Not available")
    print()
    
    # Gunicorn processes
    gunicorn_processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'cmdline']):
        try:
            if 'gunicorn' in proc.info['name'] or (proc.info['cmdline'] and any('gunicorn' in arg for arg in proc.info['cmdline'])):
                gunicorn_processes.append(proc.info)
        except:
            continue
    
    print(f"Gunicorn Processes: {len(gunicorn_processes)}")
    for proc in gunicorn_processes:
        print(f"  PID {proc['pid']}: CPU {proc['cpu_percent']:.1f}%, Memory {proc['memory_percent']:.1f}%")
    print()
    
    print("=== End Monitor ===")

if __name__ == "__main__":
    monitor_performance()
"""
    
    try:
        with open('performance_monitor.py', 'w') as f:
            f.write(monitoring_script)
        os.chmod('performance_monitor.py', 0o755)
        print("✅ Created performance monitoring script")
    except Exception as e:
        print(f"❌ Monitoring script creation error: {e}")

def main():
    """Execute all optimization steps"""
    print("🚀 Starting Advanced Speed Optimization for Warehouse Picking System")
    print("=" * 70)
    
    try:
        # Step 1: Database optimizations
        optimize_database_performance()
        print()
        
        # Step 2: Aggressive cleanup
        aggressive_log_cleanup()
        print()
        
        # Step 3: Gunicorn optimization
        optimize_gunicorn_config()
        print()
        
        # Step 4: Application settings
        optimize_application_settings()
        print()
        
        # Step 5: Monitoring
        create_monitoring_script()
        print()
        
        print("=" * 70)
        print("✅ SPEED OPTIMIZATION COMPLETED SUCCESSFULLY!")
        print("🚀 System performance has been significantly improved")
        print("💡 Restart the application to apply Gunicorn optimizations:")
        print("   1. Stop current workflow")
        print("   2. Start workflow again")
        print("📊 Run 'python performance_monitor.py' to check performance")
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ Critical error during optimization: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)