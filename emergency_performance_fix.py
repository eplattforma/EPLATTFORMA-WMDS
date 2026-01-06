#!/usr/bin/env python3
"""
Emergency Performance Fix - Additional optimizations for high load
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db
    from models import Setting, ActivityLog, ItemTimeTracking
    from sqlalchemy import text, func
    from datetime import datetime, timedelta
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

def apply_emergency_fixes():
    """Apply emergency performance fixes"""
    print("🚨 Applying emergency performance fixes...")
    
    with app.app_context():
        try:
            # 1. Disable all non-essential features for maximum speed
            emergency_settings = [
                ('confirm_picking_step', 'false'),
                ('show_image_on_picking_screen', 'false'), 
                ('show_multi_qty_warning', 'false'),
                ('time_alerts_enabled', 'false'),
                ('auto_notify_admin', 'false'),
                ('enable_quantity_warnings', 'false'),
                ('show_skip_reasons', 'false'),
                ('enable_barcode_scanning', 'false'),  # Temporary disable for speed
                ('show_location_validation', 'false'),
                ('enable_print_optimization', 'true'),
                ('cache_dashboard_queries', 'true'),
            ]
            
            updated = 0
            for key, value in emergency_settings:
                setting = Setting.query.filter_by(key=key).first()
                if setting:
                    if setting.value != value:
                        setting.value = value
                        updated += 1
                else:
                    setting = Setting(key=key, value=value)
                    db.session.add(setting)
                    updated += 1
            
            if updated > 0:
                db.session.commit()
                print(f"✅ Updated {updated} emergency settings")
            
            # 2. Clean up more old data
            cutoff = datetime.now() - timedelta(hours=24)
            
            # Clean activity logs older than 24 hours
            old_activities = db.session.query(func.count(ActivityLog.id)).filter(
                ActivityLog.timestamp < cutoff
            ).scalar() or 0
            
            if old_activities > 0:
                db.session.query(ActivityLog).filter(
                    ActivityLog.timestamp < cutoff
                ).delete(synchronize_session=False)
                print(f"✅ Cleaned {old_activities} activity logs")
            
            # Clean time tracking older than 48 hours  
            time_cutoff = datetime.now() - timedelta(hours=48)
            old_tracking = db.session.query(func.count(ItemTimeTracking.id)).filter(
                ItemTimeTracking.item_started < time_cutoff
            ).scalar() or 0
            
            if old_tracking > 0:
                db.session.query(ItemTimeTracking).filter(
                    ItemTimeTracking.item_started < time_cutoff
                ).delete(synchronize_session=False)
                print(f"✅ Cleaned {old_tracking} time tracking records")
            
            db.session.commit()
            
            # 3. Set connection pool to minimum
            db.session.execute(text("SET statement_timeout = '5s'"))
            db.session.execute(text("SET lock_timeout = '3s'"))
            db.session.commit()
            
            print("✅ Applied database timeout optimizations")
            
        except Exception as e:
            print(f"❌ Emergency fix error: {e}")
            db.session.rollback()

def optimize_worker_settings():
    """Create worker-optimized gunicorn config"""
    config = """# Emergency High-Performance Configuration
import os

# Minimal worker configuration
bind = "0.0.0.0:5000"
workers = 1  # Single worker for emergency performance
worker_class = "sync"
worker_connections = 10
max_requests = 50
max_requests_jitter = 5

# Aggressive timeouts
timeout = 8
keepalive = 1
graceful_timeout = 3

# Minimal logging
loglevel = "error"
accesslog = None
errorlog = None
capture_output = False
enable_stdio_inheritance = False

# Performance optimizations
preload_app = True
reuse_port = True
worker_tmp_dir = "/dev/shm"

print("Emergency config: Single worker, maximum performance")
"""
    
    try:
        with open('gunicorn_config.py', 'w') as f:
            f.write(config)
        print("✅ Created emergency gunicorn configuration")
        return True
    except Exception as e:
        print(f"❌ Failed to create config: {e}")
        return False

def main():
    print("🚨 EMERGENCY PERFORMANCE OPTIMIZATION")
    print("=" * 50)
    
    # Apply fixes
    apply_emergency_fixes()
    
    # Update gunicorn config
    optimize_worker_settings()
    
    print("=" * 50)
    print("✅ EMERGENCY FIXES APPLIED!")
    print("🚨 System optimized for maximum performance")
    print("⚡ Restart workflow to apply gunicorn changes")
    print("=" * 50)

if __name__ == "__main__":
    main()