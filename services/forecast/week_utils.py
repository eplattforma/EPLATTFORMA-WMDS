from datetime import date, timedelta


def monday_of(d: date) -> date:
    """Get the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def get_completed_week_cutoff() -> date:
    """
    Get the start of the current (incomplete) week.
    
    Returns the Monday of the current week.
    All forecasting logic should exclude weeks >= this date.
    
    Example: If today is Tuesday of week 14, returns Monday of week 14.
    Week 13 (Monday-Sunday) is the latest completed week.
    """
    return monday_of(date.today())
