"""
Time tracking alert utilities for monitoring order duration
"""
from datetime import datetime, timedelta
from models import TimeTrackingAlert, ActivityLog, Invoice, Setting
from app import db
from timezone_utils import get_utc_now


def _utc_now_for_db():
    """Get current UTC time for database operations - matches UTCDateTime column type"""
    return get_utc_now()


def check_order_time_alerts(invoice_no, picker_username):
    """
    Check if an order is exceeding expected duration and create alerts
    
    Args:
        invoice_no: Invoice number to check
        picker_username: Username of the picker
        
    Returns:
        Dict with alert information or None if no alert needed
    """
    # Get alert settings
    alert_settings = get_alert_settings()
    if not alert_settings['enabled']:
        return None
    
    # Get the invoice
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice or invoice.status not in ['In Progress']:
        return None
    
    # Find when picking started
    start_log = ActivityLog.query.filter_by(
        invoice_no=invoice_no,
        activity_type='picking_started'
    ).order_by(ActivityLog.timestamp.asc()).first()
    
    if not start_log:
        return None
    
    # Calculate elapsed time using UTC (matches UTCDateTime storage)
    now = _utc_now_for_db()
    elapsed_time = (now - start_log.timestamp).total_seconds() / 60  # minutes
    expected_time = invoice.total_exp_time or 0
    
    if expected_time <= 0:
        return None
    
    # Calculate percentage over expected
    percentage = (elapsed_time / expected_time) * 100
    
    # Determine alert type based on thresholds
    alert_type = None
    threshold = 0
    
    if percentage >= alert_settings['critical_threshold']:
        alert_type = 'critical'
        threshold = alert_settings['critical_threshold']
    elif percentage >= alert_settings['warning_threshold']:
        alert_type = 'warning'
        threshold = alert_settings['warning_threshold']
    
    if not alert_type:
        return None
    
    # Check if we already have an unresolved alert of this type
    existing_alert = TimeTrackingAlert.query.filter_by(
        invoice_no=invoice_no,
        alert_type=alert_type,
        is_resolved=False
    ).first()
    
    if existing_alert:
        # Update existing alert with current duration
        existing_alert.actual_duration = elapsed_time
        db.session.commit()
        return {
            'alert_id': existing_alert.id,
            'type': alert_type,
            'existing': True
        }
    
    # Create new alert
    new_alert = TimeTrackingAlert(
        invoice_no=invoice_no,
        picker_username=picker_username,
        alert_type=alert_type,
        expected_duration=expected_time,
        actual_duration=elapsed_time,
        threshold_percentage=threshold
    )
    
    db.session.add(new_alert)
    db.session.commit()
    
    return {
        'alert_id': new_alert.id,
        'type': alert_type,
        'expected': expected_time,
        'actual': elapsed_time,
        'percentage': percentage,
        'new': True
    }

def get_alert_settings():
    """Get current alert configuration settings"""
    default_settings = {
        'enabled': True,
        'warning_threshold': 120,  # 120% of expected time
        'critical_threshold': 150,  # 150% of expected time
        'auto_notify_admin': True,
        'show_picker_warnings': True
    }
    
    return Setting.get_json(db.session, 'time_tracking_alerts', default_settings)

def update_alert_settings(new_settings):
    """Update alert configuration settings"""
    Setting.set_json(db.session, 'time_tracking_alerts', new_settings)

def get_active_alerts(limit=50):
    """Get all active (unresolved) time tracking alerts"""
    return TimeTrackingAlert.query.filter_by(
        is_resolved=False
    ).order_by(TimeTrackingAlert.created_at.desc()).limit(limit).all()

def resolve_alert(alert_id, resolved_by_username, notes=None):
    """Mark an alert as resolved"""
    alert = TimeTrackingAlert.query.get(alert_id)
    if alert:
        alert.is_resolved = True
        alert.resolved_at = _utc_now_for_db()
        alert.resolved_by = resolved_by_username
        if notes:
            alert.notes = notes
        db.session.commit()
        return True
    return False

def get_picker_alerts(picker_username):
    """Get active alerts for a specific picker"""
    return TimeTrackingAlert.query.filter_by(
        picker_username=picker_username,
        is_resolved=False
    ).order_by(TimeTrackingAlert.created_at.desc()).all()