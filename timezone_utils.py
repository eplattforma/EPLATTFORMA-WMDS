"""
Timezone utility functions for the warehouse picking system
"""
import pytz
from datetime import datetime

def get_system_timezone():
    """Get the configured system timezone, defaults to Europe/Athens"""
    import pytz
    from flask import current_app
    from sqlalchemy.sql import text
    try:
        # Avoid direct import of models to prevent circular dependency
        from app import db
        # Use raw SQL to get setting without importing Model
        result = db.session.execute(text("SELECT value FROM settings WHERE key = 'system_timezone'")).fetchone()
        timezone_str = result[0] if result else 'Europe/Athens'
        return pytz.timezone(timezone_str)
    except Exception:
        return pytz.timezone('Europe/Athens')

def get_utc_now():
    """Return timezone-aware UTC now."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def utc_now_for_db():
    """
    Standardized UTC timestamp for database writes.
    Use this for ALL database timestamp assignments to ensure consistency.
    UTCDateTime columns expect timezone-aware UTC datetimes.
    """
    return get_utc_now()


def get_utc_today():
    """Return current UTC date."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date()

def get_local_time():
    """Get current time in the configured timezone"""
    tz = get_system_timezone()
    return get_utc_now().astimezone(tz)

def format_utc_datetime_to_local(dt, format_str='%Y-%m-%d %H:%M:%S'):
    """Convert UTC datetime to local timezone and format for display"""
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        dt = pytz.UTC.localize(dt)
    
    local_dt = dt.astimezone(get_system_timezone())
    return local_dt.strftime(format_str)

def get_local_now():
    """Get current time in local timezone for display and operations"""
    return get_local_time().replace(tzinfo=None)

def format_local_time(dt=None, format_str='%Y-%m-%d %H:%M:%S'):
    """Format datetime in local timezone"""
    if dt is None:
        dt = get_local_time()
    elif dt.tzinfo is None:
        # Assume UTC if no timezone info
        dt = pytz.UTC.localize(dt)
        dt = dt.astimezone(get_system_timezone())
    
    return dt.strftime(format_str)

def localize_datetime(dt):
    """Convert a UTC datetime to local timezone"""
    if dt is None:
        return None
    
    if dt.tzinfo is None:
        # Assume UTC if no timezone info
        dt = pytz.UTC.localize(dt)
    
    return dt.astimezone(get_system_timezone())