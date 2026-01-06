"""
Shift tracking utility functions for the warehouse picking system
"""
import logging
from datetime import datetime, timedelta
from flask import session
from sqlalchemy import func
from app import db
from models import Shift, IdlePeriod, ActivityLog, Setting, User
from timezone_utils import get_utc_now, get_local_now, format_utc_datetime_to_local, utc_now_for_db

def check_in_picker(username, coordinates=None):
    """
    Check in a picker for their shift
    
    Args:
        username: The picker's username
        coordinates: Optional location coordinates
        
    Returns:
        Shift object that was created
    """
    try:
        # Check if the picker has an active shift already
        active_shift = Shift.query.filter_by(picker_username=username, status='active').first()
        if active_shift:
            # Already checked in, return the existing shift
            logging.info(f"Picker {username} already has active shift #{active_shift.id}")
            return active_shift
            
        # Create a new shift - store check-in time in UTC
        shift = Shift(
            picker_username=username,
            check_in_time=get_utc_now(),
            check_in_coordinates=coordinates,
            status='active'
        )
        db.session.add(shift)
        
        # Log the activity
        activity = ActivityLog(
            picker_username=username,
            activity_type='check_in',
            details=f"Shift started at {format_utc_datetime_to_local(shift.check_in_time, '%Y-%m-%d %H:%M:%S')}"
        )
        db.session.add(activity)
        
        db.session.commit()
        logging.info(f"Picker {username} checked in for shift #{shift.id}")
        return shift
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error checking in picker {username}: {str(e)}")
        return None

def check_out_picker(username, coordinates=None, auto_checkout=False):
    """
    Check out a picker at the end of their shift
    
    Args:
        username: The picker's username
        coordinates: Optional location coordinates
        auto_checkout: Whether this was an automatic end-of-day checkout
        
    Returns:
        Shift object that was updated, or None if no active shift
    """
    try:
        # Find the active shift for this picker
        shift = Shift.query.filter_by(picker_username=username, status='active').first()
        if not shift:
            logging.warning(f"No active shift found for picker {username} to check out")
            return None
            
        # End any ongoing idle periods
        end_idle_period(shift.id)
            
        # Update the shift - store check-out time in UTC
        shift.check_out_time = get_utc_now()
        shift.check_out_coordinates = coordinates
        shift.status = 'completed'
        shift.total_duration_minutes = shift.calculate_duration()
        
        # Log the activity
        details = f"Shift ended at {format_utc_datetime_to_local(shift.check_out_time, '%Y-%m-%d %H:%M:%S')}"
        if auto_checkout:
            details += " (automatic end-of-day checkout)"
        
        activity = ActivityLog(
            picker_username=username,
            activity_type='check_out',
            details=details
        )
        db.session.add(activity)
        
        db.session.commit()
        logging.info(f"Picker {username} checked out from shift #{shift.id}")
        return shift
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error checking out picker {username}: {str(e)}")
        return None

def start_break(username, reason=None):
    """
    Start a break period for a picker
    
    Args:
        username: The picker's username
        reason: Optional reason for the break
        
    Returns:
        IdlePeriod object that was created, or None if no active shift
    """
    try:
        # Find the active shift for this picker
        shift = Shift.query.filter_by(picker_username=username, status='active').first()
        if not shift:
            logging.warning(f"No active shift found for picker {username} to start break")
            return None
            
        # Check if there's already an active idle period
        active_idle = IdlePeriod.query.filter_by(
            shift_id=shift.id, 
            end_time=None
        ).first()
        
        if active_idle:
            # If there's an auto-detected idle period, convert it to a break
            active_idle.is_break = True
            active_idle.break_reason = reason
            idle_period = active_idle
        else:
            # Create a new idle period marked as a break - store start_time in UTC
            idle_period = IdlePeriod(
                shift_id=shift.id,
                start_time=get_utc_now(),
                end_time=None,  # Explicitly set to None to ensure it persists
                is_break=True,
                break_reason=reason
            )
            db.session.add(idle_period)
        
        # Log the activity
        activity = ActivityLog(
            picker_username=username,
            activity_type='start_break',
            details=f"Break started at {format_utc_datetime_to_local(idle_period.start_time, '%Y-%m-%d %H:%M:%S')}" +
                    (f" - Reason: {reason}" if reason else "")
        )
        db.session.add(activity)
        
        db.session.commit()
        # Refresh the idle_period from the database to ensure it has the correct ID and state
        db.session.refresh(idle_period)
        logging.info(f"Picker {username} started break for shift #{shift.id}, idle_period_id={idle_period.id}")
        return idle_period
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error starting break for picker {username}: {str(e)}")
        return None

def end_break(username):
    """
    End a break period for a picker
    
    Args:
        username: The picker's username
        
    Returns:
        IdlePeriod object that was updated, or None if no active break
    """
    try:
        # Find the active shift for this picker
        shift = Shift.query.filter_by(picker_username=username, status='active').first()
        if not shift:
            logging.warning(f"No active shift found for picker {username} to end break")
            return None
            
        # Find the active idle period for this shift
        idle_period = IdlePeriod.query.filter_by(
            shift_id=shift.id,
            end_time=None,
            is_break=True
        ).first()
        
        if not idle_period:
            logging.warning(f"No active break found for picker {username} to end")
            return None
            
        # Update the idle period - store end_time in UTC
        now_utc = get_utc_now()
        idle_period.end_time = now_utc
        idle_period.duration_minutes = idle_period.calculate_duration()
        
        # Log the activity
        activity = ActivityLog(
            picker_username=username,
            activity_type='end_break',
            details=f"Break ended at {format_utc_datetime_to_local(now_utc, '%Y-%m-%d %H:%M:%S')}, duration: {idle_period.duration_minutes} minutes"
        )
        db.session.add(activity)
        
        db.session.commit()
        logging.info(f"Picker {username} ended break for shift #{shift.id}")
        return idle_period
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error ending break for picker {username}: {str(e)}")
        return None

def start_idle_period(shift_id):
    """
    Start an automatic idle period for a shift
    
    Args:
        shift_id: ID of the shift
        
    Returns:
        IdlePeriod object that was created
    """
    try:
        # Check if there's already an active idle period for this shift
        existing_idle = IdlePeriod.query.filter_by(
            shift_id=shift_id,
            end_time=None
        ).first()
        
        if existing_idle:
            # Already tracking idle time
            return existing_idle
            
        # Create a new idle period - store start_time in UTC
        idle_period = IdlePeriod(
            shift_id=shift_id,
            start_time=get_utc_now(),
            is_break=False
        )
        db.session.add(idle_period)
        db.session.commit()
        
        logging.info(f"Started idle period for shift #{shift_id}")
        return idle_period
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error starting idle period for shift #{shift_id}: {str(e)}")
        return None

def end_idle_period(shift_id):
    """
    End any active auto-detected idle period for a shift (NOT breaks)
    
    Args:
        shift_id: ID of the shift
        
    Returns:
        IdlePeriod object that was updated, or None if no active idle period
    """
    try:
        # Find the active idle period for this shift - ONLY non-break idle periods
        # We exclude breaks (is_break=True) because they should be manually ended
        idle_period = IdlePeriod.query.filter_by(
            shift_id=shift_id,
            end_time=None,
            is_break=False
        ).first()
        
        if not idle_period:
            # No active auto-detected idle period
            return None
            
        # Update the idle period - store end_time in UTC
        idle_period.end_time = get_utc_now()
        idle_period.duration_minutes = idle_period.calculate_duration()
        
        db.session.commit()
        
        shift = Shift.query.get(shift_id)
        if shift:
            logging.info(f"Ended idle period for picker {shift.picker_username}, shift #{shift_id}, duration: {idle_period.duration_minutes} minutes")
        
        return idle_period
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error ending idle period for shift #{shift_id}: {str(e)}")
        return None

def record_activity(username, activity_type, invoice_no=None, item_code=None, details=None):
    """
    Record a picker activity to reset idle detection
    
    Args:
        username: The picker's username
        activity_type: Type of activity (e.g., 'item_pick', 'location_change', 'screen_interaction')
        invoice_no: Optional invoice number related to the activity
        item_code: Optional item code related to the activity
        details: Optional details about the activity
        
    Returns:
        ActivityLog object that was created
    """
    try:
        # Check if this picker has an active shift
        shift = Shift.query.filter_by(picker_username=username, status='active').first()
        if shift:
            # If there's an active idle period, end it
            end_idle_period(shift.id)
        
        # Create the activity log
        activity = ActivityLog(
            picker_username=username,
            activity_type=activity_type,
            invoice_no=invoice_no,
            item_code=item_code,
            details=details
        )
        db.session.add(activity)
        db.session.commit()
        
        return activity
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error recording activity for picker {username}: {str(e)}")
        return None

def check_for_idle_pickers():
    """
    Check for pickers who have been inactive for the threshold period
    and start idle periods for them
    
    Returns:
        List of shift IDs for which idle periods were started
    """
    try:
        idle_shifts = []
        threshold_minutes = int(Setting.get(db.session(), 'idle_time_threshold_minutes', '15'))
        
        # Get all active shifts
        active_shifts = Shift.query.filter_by(status='active').all()
        
        for shift in active_shifts:
            # Check if there's already an active idle period for this shift
            existing_idle = IdlePeriod.query.filter_by(
                shift_id=shift.id,
                end_time=None
            ).first()
            
            if existing_idle:
                # Already tracking idle time for this shift
                continue
                
            # Find the latest activity for this picker
            latest_activity = ActivityLog.query.filter_by(
                picker_username=shift.picker_username
            ).order_by(ActivityLog.timestamp.desc()).first()
            
            if not latest_activity:
                # If no activity found, use the shift check-in time
                last_active_time = shift.check_in_time
            else:
                last_active_time = latest_activity.timestamp
                
            # Calculate the idle time using UTC times to match database storage and current_idle_minutes() calculation
            from timezone_utils import get_utc_now
            now_utc = get_utc_now()
            idle_minutes = (now_utc - last_active_time).total_seconds() / 60
            
            # If idle time exceeds the threshold, start an idle period
            if idle_minutes >= threshold_minutes:
                idle_period = IdlePeriod(
                    shift_id=shift.id,
                    start_time=now_utc,
                    is_break=False
                )
                db.session.add(idle_period)
                idle_shifts.append(shift.id)
                
                logging.info(f"Auto-detected idle time for picker {shift.picker_username}, shift #{shift.id}, idle for {idle_minutes:.1f} minutes")
        
        if idle_shifts:
            db.session.commit()
            
        return idle_shifts
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error checking for idle pickers: {str(e)}")
        return []

def check_for_10hour_shift_closure():
    """
    Check for pickers who have been checked in for 10+ hours and automatically 
    close their shift based on their last recorded activity.
    
    RULES:
    - After 10 hours from check-in, shift should auto-close
    - Checkout time = last recorded activity timestamp
    - Status = based on the last activity type
    - All idle periods must be properly closed at the last activity time
    
    Returns:
        List of usernames of pickers who were automatically checked out
    """
    try:
        now = get_local_now()
        checked_out_users = []
        
        # Find shifts that are still active
        active_shifts = Shift.query.filter_by(status='active').all()
        
        for shift in active_shifts:
            # Calculate shift duration
            shift_duration = (now - shift.check_in_time).total_seconds() / 3600  # Convert to hours
            
            # If shift is 10+ hours, auto-close it
            if shift_duration >= 10:
                # Find the last recorded activity for this picker (excluding auto_checkout events)
                last_activity = ActivityLog.query.filter_by(
                    picker_username=shift.picker_username
                ).filter(
                    ActivityLog.activity_type.notin_(['auto_checkout', 'auto_checkout_12h', 'auto_checkout_10h', 'check_out'])
                ).order_by(ActivityLog.timestamp.desc()).first()
                
                # Use last activity time as checkout time
                if last_activity:
                    checkout_time = last_activity.timestamp
                else:
                    # Fallback: use 10 hours from check-in if no activity found
                    checkout_time = shift.check_in_time + timedelta(hours=10)
                
                # Always set status to auto_closed for automatic closure
                status = 'auto_closed'
                
                # Close any active idle periods at the checkout time
                active_idles = IdlePeriod.query.filter_by(shift_id=shift.id, end_time=None).all()
                for idle in active_idles:
                    idle.end_time = checkout_time
                    idle.duration_minutes = idle.calculate_duration()
                
                # Update the shift with the last activity time as checkout
                shift.check_out_time = checkout_time
                shift.status = status
                shift.total_duration_minutes = shift.calculate_duration()
                
                # Log the activity
                activity = ActivityLog(
                    picker_username=shift.picker_username,
                    activity_type='auto_checkout_10h',
                    details=f"Automatic 10-hour shift closure at {checkout_time.strftime('%Y-%m-%d %H:%M:%S')} (based on last recorded activity)"
                )
                db.session.add(activity)
                
                checked_out_users.append(shift.picker_username)
                logging.info(f"Auto closed shift #{shift.id} for picker {shift.picker_username} after {shift_duration:.1f} hours (checkout: {checkout_time.strftime('%Y-%m-%d %H:%M:%S')}, status: {status})")
        
        if checked_out_users:
            db.session.commit()
            
        return checked_out_users
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error checking for 10-hour shift closures: {str(e)}")
        return []


def check_for_long_shift_checkouts():
    """
    Check for pickers who have been checked in for longer than 12 hours
    and automatically check them out based on their last recorded activity
    
    Returns:
        List of usernames of pickers who were automatically checked out
    """
    try:
        now = get_local_now()
        checked_out_users = []
        
        # Find shifts that are still active
        active_shifts = Shift.query.filter_by(status='active').all()
        
        for shift in active_shifts:
            # Calculate shift duration
            shift_duration = (now - shift.check_in_time).total_seconds() / 3600  # Convert to hours
            
            # If shift is longer than 12 hours
            if shift_duration > 12:
                # Find the last recorded activity for this picker
                last_activity = ActivityLog.query.filter_by(
                    picker_username=shift.picker_username
                ).order_by(ActivityLog.timestamp.desc()).first()
                
                # Use last activity time as checkout time, or shift check-in + 12 hours if no activity
                if last_activity:
                    checkout_time = last_activity.timestamp
                else:
                    checkout_time = shift.check_in_time + timedelta(hours=12)
                
                # Close any active idle periods at the checkout time
                active_idles = IdlePeriod.query.filter_by(shift_id=shift.id, end_time=None).all()
                for idle in active_idles:
                    idle.end_time = checkout_time
                    idle.duration_minutes = idle.calculate_duration()
                
                # Auto check out the picker
                shift.check_out_time = checkout_time
                shift.status = 'auto_closed'
                shift.total_duration_minutes = shift.calculate_duration()
                
                # Log the activity
                activity = ActivityLog(
                    picker_username=shift.picker_username,
                    activity_type='auto_checkout_12h',
                    details=f"Automatic checkout after 12+ hours of continuous shift at {checkout_time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                db.session.add(activity)
                
                checked_out_users.append(shift.picker_username)
                logging.info(f"Auto checked out picker {shift.picker_username} from shift #{shift.id} after {shift_duration:.1f} hours (based on last activity at {checkout_time.strftime('%Y-%m-%d %H:%M:%S')})")
        
        if checked_out_users:
            db.session.commit()
            
        return checked_out_users
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error checking for long shift checkouts: {str(e)}")
        return []

def check_for_missed_checkouts():
    """
    Check for pickers who are still checked in after the end of business day
    and automatically check them out
    
    Returns:
        List of usernames of pickers who were automatically checked out
    """
    try:
        # Get the end of business day time setting
        eod_time_str = Setting.get(db.session(), 'end_of_business_day_time', '18:00')
        
        # Parse the time
        hour, minute = map(int, eod_time_str.split(':'))
        
        # Get the current time
        now = datetime.now()
        
        # Create a datetime for today's end of business day
        eod_datetime = datetime(now.year, now.month, now.day, hour, minute)
        
        # If we're past the end of business day
        if now >= eod_datetime:
            # Find shifts that are still active
            active_shifts = Shift.query.filter_by(status='active').all()
            
            checked_out_users = []
            for shift in active_shifts:
                # Check if the shift started today (we don't want to auto-close shifts from previous days)
                shift_date = shift.check_in_time.date()
                today = now.date()
                
                if shift_date == today:
                    # Auto check out the picker
                    shift.check_out_time = eod_datetime
                    shift.status = 'auto_closed'
                    shift.total_duration_minutes = shift.calculate_duration()
                    
                    # End any active idle periods
                    end_idle_period(shift.id)
                    
                    # Log the activity
                    activity = ActivityLog(
                        picker_username=shift.picker_username,
                        activity_type='auto_checkout',
                        details=f"Automatic end-of-day checkout at {eod_datetime.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    db.session.add(activity)
                    
                    checked_out_users.append(shift.picker_username)
                    logging.info(f"Auto checked out picker {shift.picker_username} from shift #{shift.id} at end of business day")
                    
            if checked_out_users:
                db.session.commit()
                
            return checked_out_users
                
        return []
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error checking for missed checkouts: {str(e)}")
        return []

def admin_adjust_shift(shift_id, admin_username, check_in_time=None, check_out_time=None, 
                       check_in_coordinates=None, check_out_coordinates=None, 
                       status=None, note=None):
    """
    Allow an admin to adjust a shift record
    
    Args:
        shift_id: ID of the shift to adjust
        admin_username: Username of the admin making the adjustment
        check_in_time: Optional new check-in time
        check_out_time: Optional new check-out time
        check_in_coordinates: Optional new check-in coordinates
        check_out_coordinates: Optional new check-out coordinates
        status: Optional new status
        note: Optional note explaining the adjustment
        
    Returns:
        Shift object that was updated, or None if shift not found
    """
    try:
        # Find the shift
        shift = Shift.query.get(shift_id)
        if not shift:
            logging.warning(f"Shift #{shift_id} not found for admin adjustment")
            return None
            
        # Update the fields if provided
        if check_in_time is not None:
            shift.check_in_time = check_in_time
            
        if check_out_time is not None:
            shift.check_out_time = check_out_time
            
        if check_in_coordinates is not None:
            shift.check_in_coordinates = check_in_coordinates
            
        if check_out_coordinates is not None:
            shift.check_out_coordinates = check_out_coordinates
            
        if status is not None:
            shift.status = status
            
        # Mark as admin adjusted
        shift.admin_adjusted = True
        shift.adjustment_by = admin_username
        shift.adjustment_time = utc_now_for_db()
        shift.adjustment_note = note
        
        # Recalculate duration if both times are set
        if shift.check_in_time and shift.check_out_time:
            shift.total_duration_minutes = shift.calculate_duration()
            
        # Log the activity
        activity = ActivityLog(
            picker_username=shift.picker_username,
            activity_type='admin_shift_adjustment',
            details=f"Shift #{shift_id} adjusted by admin {admin_username}" +
                    (f" - Note: {note}" if note else "")
        )
        db.session.add(activity)
        
        db.session.commit()
        logging.info(f"Admin {admin_username} adjusted shift #{shift_id} for picker {shift.picker_username}")
        return shift
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error in admin adjustment of shift #{shift_id}: {str(e)}")
        return None

def get_active_shift(username):
    """
    Get the active shift for a picker
    
    Args:
        username: The picker's username
        
    Returns:
        Shift object or None if no active shift
    """
    return Shift.query.filter_by(picker_username=username, status='active').first()

def get_picker_on_break(username):
    """
    Check if a picker is currently on break
    
    Args:
        username: The picker's username
        
    Returns:
        IdlePeriod object if on break, None otherwise
    """
    shift = get_active_shift(username)
    if not shift:
        return None
    
    # Ensure we have the latest data from the database
    db.session.expire_all()
    
    active_break = IdlePeriod.query.filter_by(
        shift_id=shift.id,
        end_time=None,
        is_break=True
    ).first()
    
    if active_break:
        logging.info(f"Picker {username} is on break, idle_period_id={active_break.id}")
    
    return active_break

def get_picker_shifts(username, start_date=None, end_date=None):
    """
    Get shifts for a picker within a date range
    
    Args:
        username: The picker's username
        start_date: Optional start date (inclusive)
        end_date: Optional end date (inclusive)
        
    Returns:
        List of Shift objects
    """
    query = Shift.query.filter_by(picker_username=username)
    
    if start_date:
        start_datetime = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Shift.check_in_time >= start_datetime)
        
    if end_date:
        end_datetime = datetime.combine(end_date, datetime.max.time())
        query = query.filter(Shift.check_in_time <= end_datetime)
        
    return query.order_by(Shift.check_in_time.desc()).all()

def get_shift_report(start_date=None, end_date=None):
    """
    Generate a report of all shifts within a date range
    
    Args:
        start_date: Optional start date (inclusive)
        end_date: Optional end date (inclusive)
        
    Returns:
        List of dictionaries containing shift data
    """
    try:
        query = db.session.query(
            Shift,
            User.username
        ).join(User, Shift.picker_username == User.username)
        
        if start_date:
            start_datetime = datetime.combine(start_date, datetime.min.time())
            query = query.filter(Shift.check_in_time >= start_datetime)
            
        if end_date:
            end_datetime = datetime.combine(end_date, datetime.max.time())
            query = query.filter(Shift.check_in_time <= end_datetime)
            
        results = query.order_by(Shift.check_in_time.desc()).all()
        
        report = []
        for shift, username in results:
            # Calculate total idle time
            idle_time = db.session.query(func.sum(IdlePeriod.duration_minutes)) \
                .filter(IdlePeriod.shift_id == shift.id) \
                .scalar() or 0
                
            # Count breaks
            break_count = db.session.query(func.count(IdlePeriod.id)) \
                .filter(IdlePeriod.shift_id == shift.id, IdlePeriod.is_break == True) \
                .scalar() or 0
                
            report_entry = {
                'shift_id': shift.id,
                'username': username,
                'check_in_time': shift.check_in_time,
                'check_out_time': shift.check_out_time,
                'duration_minutes': shift.total_duration_minutes,
                'status': shift.status,
                'idle_time_minutes': idle_time,
                'break_count': break_count,
                'admin_adjusted': shift.admin_adjusted,
                'adjustment_note': shift.adjustment_note
            }
            report.append(report_entry)
            
        return report
    except Exception as e:
        logging.error(f"Error generating shift report: {str(e)}")
        return []