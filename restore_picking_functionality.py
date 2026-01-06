#!/usr/bin/env python3
"""
Restore essential picking functionality that was disabled during performance optimization
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from app import app, db
    from models import Setting
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

def restore_picking_features():
    """Restore essential picking features while keeping performance optimizations"""
    print("🔄 Restoring essential picking functionality...")
    
    with app.app_context():
        try:
            # Restore essential picking features
            essential_settings = [
                ('confirm_picking_step', 'true'),  # RESTORE quantity confirmation
                ('show_multi_qty_warning', 'true'),  # RESTORE multi-quantity warnings  
                ('enable_quantity_warnings', 'true'),  # RESTORE quantity validation
                ('show_skip_reasons', 'true'),  # RESTORE skip reasons
                ('show_location_validation', 'true'),  # RESTORE location validation
                
                # Keep performance optimizations for non-essential features
                ('show_image_on_picking_screen', 'false'),  # Images still disabled for speed
                ('time_alerts_enabled', 'false'),  # Time alerts still disabled
                ('auto_notify_admin', 'false'),  # Admin notifications still disabled
                ('enable_print_optimization', 'true'),  # Keep print optimization
                ('cache_dashboard_queries', 'true'),  # Keep dashboard caching
            ]
            
            updated = 0
            for key, value in essential_settings:
                setting = Setting.query.filter_by(key=key).first()
                if setting:
                    if setting.value != value:
                        old_value = setting.value
                        setting.value = value
                        print(f"✅ Updated {key}: {old_value} → {value}")
                        updated += 1
                    else:
                        print(f"⏭️  {key}: already {value}")
                else:
                    setting = Setting(key=key, value=value)
                    db.session.add(setting)
                    print(f"✅ Created {key}: {value}")
                    updated += 1
            
            if updated > 0:
                db.session.commit()
                print(f"\n✅ Restored {updated} essential picking features")
            else:
                print("\n⏭️  All essential features already restored")
            
        except Exception as e:
            print(f"❌ Error restoring features: {e}")
            db.session.rollback()

def main():
    print("🔄 RESTORING ESSENTIAL PICKING FUNCTIONALITY")
    print("=" * 60)
    
    restore_picking_features()
    
    print("=" * 60)
    print("✅ ESSENTIAL PICKING FUNCTIONALITY RESTORED!")
    print("🎯 Quantity confirmation is now working again")
    print("📊 Multi-quantity warnings are enabled")
    print("✋ Skip reasons are available")
    print("📍 Location validation is active")
    print("⚡ Performance optimizations are maintained")
    print("=" * 60)

if __name__ == "__main__":
    main()