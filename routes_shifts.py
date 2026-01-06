"""
Routes for the shift tracking system
"""
from datetime import datetime, date, timedelta
from flask import render_template, redirect, url_for, request, flash, session
from flask_login import login_required, current_user
from sqlalchemy import desc
from timezone_utils import get_local_time, format_local_time, localize_datetime, get_local_now, get_utc_now, format_utc_datetime_to_local

from app import app, db
from models import Shift, IdlePeriod, ActivityLog, User, Invoice, InvoiceItem
from utils.shift_tracking import (
    check_in_picker, check_out_picker, start_break, end_break,
    record_activity, admin_adjust_shift, get_active_shift, get_picker_on_break,
    get_shift_report, get_picker_shifts
)
from location_utils import validate_location
from sqlalchemy import func

# Periodic tasks - Idle detection
@app.before_request
def check_for_idle_users():
    """Check for idle users before processing each request"""
    try:
        # Only check on certain conditions to avoid excessive processing
        if request.endpoint in ['static', 'shift_check_in', 'shift_check_out']:
            return
            
        # Only run this check occasionally (not on every request)
        last_idle_check = session.get('last_idle_check', 0)
        current_time = int(datetime.now().timestamp())
        
        # Run every 5 minutes
        if current_user.is_authenticated and current_time - last_idle_check > 300:
            from utils.shift_tracking import check_for_idle_pickers
            check_for_idle_pickers()
            session['last_idle_check'] = current_time
            
        # Check for missed checkouts once per hour
        last_checkout_check = session.get('last_checkout_check', 0)
        if current_time - last_checkout_check > 3600:
            from utils.shift_tracking import check_for_missed_checkouts, check_for_long_shift_checkouts
            check_for_missed_checkouts()
            check_for_long_shift_checkouts()
            session['last_checkout_check'] = current_time
    
    except Exception as e:
        # Don't let errors here break the application
        import logging
        logging.error(f"Error in idle detection: {str(e)}")

# Shift management routes
@app.route('/shift/check-in', methods=['GET', 'POST'])
@login_required
def shift_check_in():
    """Shift check-in page for pickers"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the active shift for this picker
    active_shift = get_active_shift(current_user.username)
    
    if request.method == 'POST':
        if active_shift:
            flash('You are already checked in for a shift.', 'warning')
        else:
            coordinates = request.form.get('coordinates', None)
            
            # Validate location if GPS checking is enabled for this user
            if current_user.require_gps_check:
                location_check = validate_location(coordinates)
                if not location_check['valid']:
                    flash(location_check['message'], 'danger')
                    return redirect(url_for('shift_check_in'))
            
            shift = check_in_picker(current_user.username, coordinates)
            
            if shift:
                flash('You have been checked in successfully.', 'success')
                return redirect(url_for('picker_dashboard'))
            else:
                flash('An error occurred during check-in. Please try again.', 'danger')
    
    # Format check-in time for display
    check_in_time_formatted = None
    if active_shift:
        check_in_time_formatted = format_utc_datetime_to_local(active_shift.check_in_time, '%d/%m/%y %H:%M')
    
    return render_template('shift_check_in.html', active_shift=active_shift, check_in_time_formatted=check_in_time_formatted)

@app.route('/shift/check-out', methods=['GET', 'POST'])
@login_required
def shift_check_out():
    """Shift check-out page for pickers"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the active shift for this picker
    active_shift = get_active_shift(current_user.username)
    
    # Get break periods for this shift
    break_periods = []
    if active_shift:
        break_periods = IdlePeriod.query.filter_by(
            shift_id=active_shift.id, 
            is_break=True
        ).order_by(IdlePeriod.start_time.desc()).all()
    
    if request.method == 'POST':
        if not active_shift:
            flash('You are not currently checked in for a shift.', 'warning')
        else:
            coordinates = request.form.get('coordinates', None)
            
            # Validate location if GPS checking is enabled for this user
            if current_user.require_gps_check:
                location_check = validate_location(coordinates)
                if not location_check['valid']:
                    flash(location_check['message'], 'danger')
                    return redirect(url_for('shift_check_out'))
            
            shift = check_out_picker(current_user.username, coordinates)
            
            if shift:
                flash('You have been checked out successfully. Shift duration: {:.1f} hours'.format(
                    shift.total_duration_minutes / 60 if shift.total_duration_minutes else 0
                ), 'success')
                
                return redirect(url_for('picker_dashboard'))
            else:
                flash('An error occurred during check-out. Please try again.', 'danger')
    
    # Format times for display
    check_in_time_formatted = None
    shift_duration_minutes = 0
    if active_shift:
        check_in_time_formatted = format_utc_datetime_to_local(active_shift.check_in_time, '%d/%m/%y %H:%M')
        # Calculate shift duration using UTC times
        elapsed = get_utc_now() - active_shift.check_in_time
        shift_duration_minutes = int(elapsed.total_seconds() / 60)
    
    return render_template('shift_check_out.html', 
                          active_shift=active_shift, 
                          break_periods=break_periods,
                          check_in_time_formatted=check_in_time_formatted,
                          shift_duration_minutes=shift_duration_minutes)

@app.route('/shift/break', methods=['GET'])
@login_required
def manage_break():
    """Break management page for pickers"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the active shift for this picker
    active_shift = get_active_shift(current_user.username)
    
    # Check if the picker is on break
    active_break = get_picker_on_break(current_user.username)
    
    # Get break history for today
    break_history = []
    if active_shift:
        break_history = IdlePeriod.query.filter_by(
            shift_id=active_shift.id, 
            is_break=True
        ).order_by(IdlePeriod.start_time.desc()).all()
    
    # Record the activity
    if active_shift:
        record_activity(current_user.username, 'screen_interaction', 
                      details='Break management page')
    
    # Calculate durations with proper timezone handling
    # Note: Times are stored as naive local datetimes (Athens time), so compare directly
    from timezone_utils import get_local_now
    now_local = get_local_now()
    
    active_break_duration_minutes = 0
    if active_break:
        # Times are already naive local - compare directly
        active_break_duration_minutes = int((now_local - active_break.start_time).total_seconds() / 60)
    
    shift_duration_minutes = 0
    if active_shift:
        # Times are already naive local - compare directly
        shift_duration_minutes = int((now_local - active_shift.check_in_time).total_seconds() / 60)
    
    # Calculate durations for break history
    break_history_with_durations = []
    for break_period in break_history:
        duration = break_period.duration_minutes
        if duration is None:
            # Currently in progress or duration not calculated
            duration = int((now_local - break_period.start_time).total_seconds() / 60)
        break_history_with_durations.append({
            'period': break_period,
            'duration_minutes': duration
        })
    
    return render_template('break_management.html', 
                          active_shift=active_shift,
                          active_break=active_break,
                          active_break_duration_minutes=active_break_duration_minutes,
                          shift_duration_minutes=shift_duration_minutes,
                          break_history_data=break_history_with_durations)

@app.route('/shift/start-break', methods=['POST'])
@login_required
def start_break_route():
    """Handle start break request"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Check if the picker is already on break
    active_break = get_picker_on_break(current_user.username)
    if active_break:
        flash('You are already on break.', 'warning')
        return redirect(url_for('manage_break'))
    
    # Get the reason
    reason = request.form.get('break_reason', None)
    
    # Start the break
    idle_period = start_break(current_user.username, reason)
    
    if idle_period:
        flash('Break started successfully.', 'success')
    else:
        flash('An error occurred starting your break. Please try again.', 'danger')
    
    return redirect(url_for('manage_break'))

@app.route('/shift/end-break', methods=['POST'])
@login_required
def end_break_route():
    """Handle end break request"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Check if the picker is on break
    active_break = get_picker_on_break(current_user.username)
    if not active_break:
        flash('You are not currently on break.', 'warning')
        return redirect(url_for('manage_break'))
    
    # End the break
    idle_period = end_break(current_user.username)
    
    if idle_period:
        flash('Break ended successfully. Duration: {} minutes'.format(
            idle_period.duration_minutes or 0
        ), 'success')
    else:
        flash('An error occurred ending your break. Please try again.', 'danger')
    
    return redirect(url_for('manage_break'))

# Shift Reports Route
@app.route('/shift/reports', methods=['GET'])
@login_required
def shift_reports():
    """Detailed time and productivity reports"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get filter parameters
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    picker_filter = request.args.get('picker', '')
    
    # Parse dates - default to last 7 days
    if not start_date_str:
        start_date = date.today() - timedelta(days=7)
    else:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = date.today() - timedelta(days=7)
    
    if not end_date_str:
        end_date = date.today()
    else:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = date.today()
    
    # Build date range for queries
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Get all shifts in date range
    shifts_query = Shift.query.filter(
        Shift.check_in_time >= start_datetime,
        Shift.check_in_time <= end_datetime
    )
    
    if picker_filter:
        shifts_query = shifts_query.filter(Shift.picker_username == picker_filter)
    
    shifts = shifts_query.order_by(desc(Shift.check_in_time)).all()
    
    # Calculate summary statistics
    total_shifts = len(shifts)
    total_hours = sum([shift.total_duration_minutes or 0 for shift in shifts]) / 60
    avg_shift_duration = (total_hours / total_shifts) if total_shifts > 0 else 0
    
    # Get activity data for productivity metrics
    activities = ActivityLog.query.filter(
        ActivityLog.timestamp >= start_datetime,
        ActivityLog.timestamp <= end_datetime,
        ActivityLog.activity_type == 'item_pick'
    )
    
    if picker_filter:
        activities = activities.filter(ActivityLog.picker_username == picker_filter)
    
    activity_count = activities.count()
    items_per_hour = (activity_count / total_hours) if total_hours > 0 else 0
    
    # Get picker performance breakdown
    picker_stats = []
    picker_usernames = [shift.picker_username for shift in shifts]
    unique_pickers = list(set(picker_usernames))
    
    for picker in unique_pickers:
        picker_shifts = [s for s in shifts if s.picker_username == picker]
        picker_hours = sum([s.total_duration_minutes or 0 for s in picker_shifts]) / 60
        picker_activities = ActivityLog.query.filter(
            ActivityLog.picker_username == picker,
            ActivityLog.timestamp >= start_datetime,
            ActivityLog.timestamp <= end_datetime,
            ActivityLog.activity_type == 'item_pick'
        ).count()
        
        picker_productivity = (picker_activities / picker_hours) if picker_hours > 0 else 0
        
        picker_stats.append({
            'username': picker,
            'total_shifts': len(picker_shifts),
            'total_hours': picker_hours,
            'items_picked': picker_activities,
            'items_per_hour': picker_productivity,
            'avg_shift_hours': picker_hours / len(picker_shifts) if len(picker_shifts) > 0 else 0
        })
    
    # Sort by productivity
    picker_stats.sort(key=lambda x: x['items_per_hour'], reverse=True)
    
    # Get all users for picker filter dropdown
    all_pickers = User.query.filter_by(role='picker').all()
    
    return render_template('shift_reports.html',
                         shifts=shifts,
                         picker_stats=picker_stats,
                         total_shifts=total_shifts,
                         total_hours=round(total_hours, 2),
                         avg_shift_duration=round(avg_shift_duration, 2),
                         items_per_hour=round(items_per_hour, 2),
                         activity_count=activity_count,
                         start_date=start_date,
                         end_date=end_date,
                         picker_filter=picker_filter,
                         all_pickers=all_pickers)

# Admin routes for shift management
@app.route('/admin/shifts', methods=['GET'])
@login_required
def admin_shift_management():
    """Admin shift management page"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get filter parameters
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    status = request.args.get('status', '')
    show_adjusted = request.args.get('show_adjusted', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Parse dates
    start_date = None
    end_date = None
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid start date format. Please use YYYY-MM-DD.', 'warning')
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid end date format. Please use YYYY-MM-DD.', 'warning')
    
    # Default to the last 7 days if no dates specified
    if not start_date and not end_date:
        end_date = date.today()
        start_date = end_date - timedelta(days=7)
    
    # Build the query
    query = Shift.query
    
    if start_date:
        start_datetime = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Shift.check_in_time >= start_datetime)
    
    if end_date:
        end_datetime = datetime.combine(end_date, datetime.max.time())
        query = query.filter(Shift.check_in_time <= end_datetime)
    
    if status:
        query = query.filter(Shift.status == status)
    
    if show_adjusted == 'yes':
        query = query.filter(Shift.admin_adjusted == True)
    elif show_adjusted == 'no':
        query = query.filter(Shift.admin_adjusted == False)
    
    # Get active shifts (separate query)
    active_shifts = Shift.query.filter_by(status='active').all()
    
    # Format check-in times for active shifts
    for shift in active_shifts:
        shift.check_in_time_formatted = format_utc_datetime_to_local(shift.check_in_time, '%d/%m/%y %H:%M') if shift.check_in_time else None
        elapsed = get_utc_now() - shift.check_in_time
        shift.total_elapsed_minutes = int(elapsed.total_seconds() / 60) if shift.check_in_time else 0
    
    # Paginate the results
    pagination = query.order_by(Shift.check_in_time.desc()).paginate(page=page, per_page=per_page)
    shifts = pagination.items
    
    # Helper functions for the template
    def is_on_break(shift_id):
        return IdlePeriod.query.filter_by(
            shift_id=shift_id, 
            end_time=None,
            is_break=True
        ).first() is not None
    
    def is_idle(shift_id):
        return IdlePeriod.query.filter_by(
            shift_id=shift_id, 
            end_time=None,
            is_break=False
        ).first() is not None
    
    # Use timezone-aware UTC now for datetime calculations with database times
    import pytz
    now_utc = datetime.now(pytz.UTC)
    
    return render_template('admin_shift_management.html', 
                          active_shifts=active_shifts,
                          shifts=shifts,
                          pagination=pagination,
                          now=now_utc,
                          is_on_break=is_on_break,
                          is_idle=is_idle,
                          request=request)

@app.route('/admin/shifts/<int:shift_id>', methods=['GET'])
@login_required
def admin_view_shift(shift_id):
    """Admin view shift details page"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the shift
    shift = Shift.query.get_or_404(shift_id)
    
    # Get activities for this shift
    activities = ActivityLog.query.filter_by(
        picker_username=shift.picker_username
    ).filter(
        ActivityLog.timestamp >= shift.check_in_time,
        ActivityLog.timestamp <= (shift.check_out_time or get_utc_now())
    ).order_by(ActivityLog.timestamp.asc()).all()
    
    # Get idle periods for this shift
    idle_periods = IdlePeriod.query.filter_by(
        shift_id=shift.id
    ).order_by(IdlePeriod.start_time.asc()).all()
    
    return render_template('admin_shift_edit.html', 
                          shift=shift,
                          activities=activities,
                          idle_periods=idle_periods,
                          now=get_local_now(),
                          mode='view')

@app.route('/admin/shifts/<int:shift_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_shift(shift_id):
    """Admin edit shift page"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the shift
    shift = Shift.query.get_or_404(shift_id)
    
    if request.method == 'POST':
        # Parse the form data
        check_in_date = request.form.get('check_in_date')
        check_in_time = request.form.get('check_in_time')
        check_out_date = request.form.get('check_out_date')
        check_out_time = request.form.get('check_out_time')
        check_in_coordinates = request.form.get('check_in_coordinates')
        check_out_coordinates = request.form.get('check_out_coordinates')
        status = request.form.get('status')
        adjustment_note = request.form.get('adjustment_note')
        
        # Parse datetimes
        check_in_datetime = None
        check_out_datetime = None
        
        if check_in_date and check_in_time:
            try:
                check_in_datetime = datetime.strptime(f"{check_in_date} {check_in_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                flash('Invalid check-in date or time format.', 'danger')
                return redirect(url_for('admin_edit_shift', shift_id=shift_id))
        
        if check_out_date and check_out_time:
            try:
                check_out_datetime = datetime.strptime(f"{check_out_date} {check_out_time}", "%Y-%m-%d %H:%M")
            except ValueError:
                flash('Invalid check-out date or time format.', 'danger')
                return redirect(url_for('admin_edit_shift', shift_id=shift_id))
        
        # Make the adjustment
        updated_shift = admin_adjust_shift(
            shift_id,
            current_user.username,
            check_in_time=check_in_datetime,
            check_out_time=check_out_datetime,
            check_in_coordinates=check_in_coordinates,
            check_out_coordinates=check_out_coordinates,
            status=status,
            note=adjustment_note
        )
        
        if updated_shift:
            flash('Shift record has been updated successfully.', 'success')
            return redirect(url_for('admin_shift_management'))
        else:
            flash('An error occurred updating the shift record.', 'danger')
    
    # Get activities for this shift
    activities = ActivityLog.query.filter_by(
        picker_username=shift.picker_username
    ).filter(
        ActivityLog.timestamp >= shift.check_in_time,
        ActivityLog.timestamp <= (shift.check_out_time or get_utc_now())
    ).order_by(ActivityLog.timestamp.asc()).all()
    
    # Get idle periods for this shift
    idle_periods = IdlePeriod.query.filter_by(
        shift_id=shift.id
    ).order_by(IdlePeriod.start_time.asc()).all()
    
    # Format times for display
    for activity in activities:
        activity.timestamp_formatted = format_utc_datetime_to_local(activity.timestamp, '%H:%M') if activity.timestamp else None
        activity.timestamp_full = format_utc_datetime_to_local(activity.timestamp, '%Y-%m-%d %H:%M:%S') if activity.timestamp else None
    
    for idle in idle_periods:
        idle.start_time_formatted = format_utc_datetime_to_local(idle.start_time, '%H:%M') if idle.start_time else None
        idle.end_time_formatted = format_utc_datetime_to_local(idle.end_time, '%H:%M') if idle.end_time else None
    
    shift.check_in_time_formatted = format_utc_datetime_to_local(shift.check_in_time, '%d/%m/%y %H:%M') if shift.check_in_time else None
    shift.check_out_time_formatted = format_utc_datetime_to_local(shift.check_out_time, '%d/%m/%y %H:%M') if shift.check_out_time else None
    
    return render_template('admin_shift_edit.html', 
                          shift=shift,
                          activities=activities,
                          idle_periods=idle_periods,
                          now=get_utc_now(),
                          mode='edit')

@app.route('/admin/shifts/<int:shift_id>/checkout', methods=['POST'])
@login_required
def admin_checkout_picker(shift_id):
    """Admin force check-out a picker"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the shift
    shift = Shift.query.get_or_404(shift_id)
    
    if shift.status != 'active':
        flash('This shift is not active.', 'warning')
        return redirect(url_for('admin_shift_management'))
    
    # Update the shift - store times in UTC
    shift.check_out_time = get_utc_now()
    shift.status = 'completed'
    shift.total_duration_minutes = shift.calculate_duration()
    shift.admin_adjusted = True
    shift.adjustment_by = current_user.username
    shift.adjustment_time = get_utc_now()
    shift.adjustment_note = f"Admin forced checkout by {current_user.username}"
    
    # End any active idle periods
    active_idle = IdlePeriod.query.filter_by(
        shift_id=shift.id, 
        end_time=None
    ).all()
    
    for idle in active_idle:
        idle.end_time = get_utc_now()
        idle.duration_minutes = idle.calculate_duration()
    
    # Log the activity
    activity = ActivityLog(
        picker_username=shift.picker_username,
        activity_type='admin_checkout',
        timestamp=get_utc_now(),
        details=f"Admin {current_user.username} checked out picker from shift #{shift.id}"
    )
    db.session.add(activity)
    
    db.session.commit()
    
    flash(f'Picker {shift.picker_username} has been checked out successfully.', 'success')
    return redirect(url_for('admin_shift_management'))

@app.route('/admin/shifts/export', methods=['GET'])
@login_required
def admin_export_shifts():
    """Export shifts data as CSV"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get filter parameters
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    
    # Parse dates
    start_date = None
    end_date = None
    
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date = None
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            end_date = None
    
    # Generate the report
    report = get_shift_report(start_date, end_date)
    
    # Create a CSV file
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write the header
    writer.writerow([
        'Shift ID', 'Picker', 'Check-In Time', 'Check-Out Time', 
        'Duration (min)', 'Idle Time (min)', 'Break Count',
        'Status', 'Admin Adjusted', 'Adjustment Note'
    ])
    
    # Write the data
    for entry in report:
        writer.writerow([
            entry['shift_id'],
            entry['username'],
            entry['check_in_time'].strftime('%Y-%m-%d %H:%M:%S') if entry['check_in_time'] else '',
            entry['check_out_time'].strftime('%Y-%m-%d %H:%M:%S') if entry['check_out_time'] else '',
            entry['duration_minutes'],
            entry['idle_time_minutes'],
            entry['break_count'],
            entry['status'],
            'Yes' if entry['admin_adjusted'] else 'No',
            entry['adjustment_note'] or ''
        ])
    
    # Prepare the response
    output.seek(0)
    from flask import Response
    date_str = datetime.now().strftime('%Y%m%d')
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename=shift_report_{date_str}.csv'}
    )