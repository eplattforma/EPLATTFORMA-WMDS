"""
Comprehensive timezone fix for the warehouse picking system
This script updates all datetime operations to use proper timezone handling
"""

import os
import sys
import re
from datetime import datetime
import pytz
from app import app, db
from models import *
from timezone_utils import get_local_now, get_utc_now

def fix_all_timestamps():
    """Fix all existing timestamps in the database to ensure proper timezone handling"""
    with app.app_context():
        try:
            print("Starting comprehensive timezone fix...")
            
            # Get Athens timezone
            athens_tz = pytz.timezone('Europe/Athens')
            
            # Fix Invoice timestamps
            invoices = Invoice.query.all()
            for invoice in invoices:
                if invoice.picking_complete_time and invoice.picking_complete_time.tzinfo is None:
                    athens_dt = athens_tz.localize(invoice.picking_complete_time)
                    invoice.picking_complete_time = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
                
                if invoice.packing_complete_time and invoice.packing_complete_time.tzinfo is None:
                    athens_dt = athens_tz.localize(invoice.packing_complete_time)
                    invoice.packing_complete_time = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            
            # Fix InvoiceItem timestamps
            items = InvoiceItem.query.all()
            for item in items:
                if item.reset_timestamp and item.reset_timestamp.tzinfo is None:
                    athens_dt = athens_tz.localize(item.reset_timestamp)
                    item.reset_timestamp = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
                
                if item.skip_timestamp and item.skip_timestamp.tzinfo is None:
                    athens_dt = athens_tz.localize(item.skip_timestamp)
                    item.skip_timestamp = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            
            # Fix PickingException timestamps
            exceptions = PickingException.query.all()
            for exception in exceptions:
                if exception.timestamp and exception.timestamp.tzinfo is None:
                    athens_dt = athens_tz.localize(exception.timestamp)
                    exception.timestamp = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            
            # Fix BatchPickingSession timestamps
            sessions = BatchPickingSession.query.all()
            for session in sessions:
                if session.created_at and session.created_at.tzinfo is None:
                    athens_dt = athens_tz.localize(session.created_at)
                    session.created_at = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            
            # Fix ItemTimeTracking timestamps
            try:
                from models import ItemTimeTracking
                trackings = ItemTimeTracking.query.all()
                for tracking in trackings:
                    if tracking.start_time and tracking.start_time.tzinfo is None:
                        athens_dt = athens_tz.localize(tracking.start_time)
                        tracking.start_time = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
                    
                    if tracking.end_time and tracking.end_time.tzinfo is None:
                        athens_dt = athens_tz.localize(tracking.end_time)
                        tracking.end_time = athens_dt.astimezone(pytz.UTC).replace(tzinfo=None)
            except Exception as e:
                print(f"Note: ItemTimeTracking table may not exist yet: {e}")
            
            # Commit all changes
            db.session.commit()
            print("✅ All timestamps have been fixed to use proper timezone handling")
            
        except Exception as e:
            print(f"❌ Error fixing timestamps: {e}")
            db.session.rollback()

def set_timezone_setting():
    """Ensure the system timezone setting is properly configured"""
    with app.app_context():
        try:
            timezone_setting = Setting.query.filter_by(key='system_timezone').first()
            if not timezone_setting:
                Setting.set(db.session, 'system_timezone', 'Europe/Athens')
                print("✅ System timezone setting configured")
            else:
                print("✅ System timezone setting already exists")
        except Exception as e:
            print(f"❌ Error setting timezone: {e}")
            db.session.rollback()

if __name__ == '__main__':
    set_timezone_setting()
    fix_all_timestamps()
    print("🕐 Timezone fix completed. All times should now display correctly in Athens timezone.")