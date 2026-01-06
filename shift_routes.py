"""
Route handlers for the shift tracking system
"""
import csv
import io
import logging
from datetime import datetime, date, timedelta
from flask import render_template, redirect, url_for, request, flash, session, Response
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import desc, asc, func

from app import app, db
from models import Shift, IdlePeriod, ActivityLog, User
from timezone_utils import utc_now_for_db
from utils.shift_tracking import (
    check_in_picker, check_out_picker, start_break, end_break, 
    record_activity, check_for_idle_pickers, check_for_missed_checkouts,
    admin_adjust_shift, get_active_shift, get_picker_on_break, 
    get_picker_shifts, get_shift_report
)

# Helper functions
def parse_datetime(date_str, time_str):
    """Parse date and time strings into a datetime object"""
    if not date_str or not time_str:
        return None
    
    try:
        return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None

# Periodic tasks
@app.before_request
def check_for_idle_users():
    """Check for idle users before processing each request"""
    try:
        # Only check on GET requests and not on static files
        if request.method != 'GET' or request.path.startswith('/static/'):
            return
            
        # Check for idle pickers
        check_for_idle_pickers()
            
        # Check for missed checkouts (do this less frequently)
        # Only check once per hour to avoid excessive database queries
        last_checkout_check = session.get('last_checkout_check', 0)
        current_time = int(datetime.now().timestamp())
        
        if current_time - last_checkout_check > 3600:  # 1 hour
            check_for_missed_checkouts()
            session['last_checkout_check'] = current_time
    except Exception as e:
        logging.error(f"Error in before_request idle check: {str(e)}")

# Picker routes
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
            shift = check_in_picker(current_user.username, coordinates)
            
            if shift:
                flash('You have been checked in successfully.', 'success')
                # Record the activity
                record_activity(current_user.username, 'screen_interaction', 
                               details='Shift check-in page')
                return redirect(url_for('picker_dashboard'))
            else:
                flash('An error occurred during check-in. Please try again.', 'danger')
    
    return render_template('shift_check_in.html', active_shift=active_shift)

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
            shift = check_out_picker(current_user.username, coordinates)
            
            if shift:
                flash('You have been checked out successfully. Shift duration: {:.1f} hours'.format(
                    shift.total_duration_minutes / 60 if shift.total_duration_minutes else 0
                ), 'success')
                
                # Record the activity
                record_activity(current_user.username, 'screen_interaction', 
                               details='Shift check-out page')
                
                return redirect(url_for('index'))
            else:
                flash('An error occurred during check-out. Please try again.', 'danger')
    
    return render_template('shift_check_out.html', 
                          active_shift=active_shift, 
                          break_periods=break_periods,
                          now=datetime.now())

@app.route('/shift/break', methods=['GET', 'POST'])
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
    record_activity(current_user.username, 'screen_interaction', 
                   details='Break management page')
    
    return render_template('break_management.html', 
                          active_shift=active_shift,
                          active_break=active_break,
                          break_history=break_history,
                          now=datetime.now())

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

# Admin routes
@app.route('/admin/shifts', methods=['GET'])
@login_required
def admin_shift_management():
    """Admin shift management page"""
    if current_user.role != 'admin':
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
    
    return render_template('admin_shift_management.html', 
                          active_shifts=active_shifts,
                          shifts=shifts,
                          pagination=pagination,
                          now=datetime.now(),
                          is_on_break=is_on_break,
                          is_idle=is_idle,
                          request=request)

@app.route('/admin/shifts/<int:shift_id>', methods=['GET'])
@login_required
def admin_view_shift(shift_id):
    """Admin view shift details page"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the shift
    shift = Shift.query.get_or_404(shift_id)
    
    # Get activities for this shift
    activities = ActivityLog.query.filter_by(
        picker_username=shift.picker_username
    ).filter(
        ActivityLog.timestamp >= shift.check_in_time,
        ActivityLog.timestamp <= (shift.check_out_time or datetime.now())
    ).order_by(ActivityLog.timestamp.asc()).all()
    
    # Get idle periods for this shift
    idle_periods = IdlePeriod.query.filter_by(
        shift_id=shift.id
    ).order_by(IdlePeriod.start_time.asc()).all()
    
    return render_template('admin_shift_edit.html', 
                          shift=shift,
                          activities=activities,
                          idle_periods=idle_periods,
                          now=datetime.now(),
                          mode='view')

@app.route('/admin/shifts/<int:shift_id>/edit', methods=['GET', 'POST'])
@login_required
def admin_edit_shift(shift_id):
    """Admin edit shift page"""
    if current_user.role != 'admin':
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
        check_in_datetime = parse_datetime(check_in_date, check_in_time)
        check_out_datetime = parse_datetime(check_out_date, check_out_time)
        
        if not check_in_datetime:
            flash('Invalid check-in date or time.', 'danger')
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
        ActivityLog.timestamp <= (shift.check_out_time or datetime.now())
    ).order_by(ActivityLog.timestamp.asc()).all()
    
    # Get idle periods for this shift
    idle_periods = IdlePeriod.query.filter_by(
        shift_id=shift.id
    ).order_by(IdlePeriod.start_time.asc()).all()
    
    return render_template('admin_shift_edit.html', 
                          shift=shift,
                          activities=activities,
                          idle_periods=idle_periods,
                          now=datetime.now(),
                          mode='edit')

@app.route('/admin/shifts/<int:shift_id>/checkout', methods=['POST'])
@login_required
def admin_checkout_picker(shift_id):
    """Admin force check-out a picker"""
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the shift
    shift = Shift.query.get_or_404(shift_id)
    
    if shift.status != 'active':
        flash('This shift is not active.', 'warning')
        return redirect(url_for('admin_shift_management'))
    
    # Update the shift
    shift.check_out_time = utc_now_for_db()
    shift.status = 'completed'
    shift.total_duration_minutes = shift.calculate_duration()
    shift.admin_adjusted = True
    shift.adjustment_by = current_user.username
    shift.adjustment_time = utc_now_for_db()
    shift.adjustment_note = f"Admin forced checkout by {current_user.username}"
    
    # End any active idle periods
    active_idle = IdlePeriod.query.filter_by(
        shift_id=shift.id, 
        end_time=None
    ).all()
    
    for idle in active_idle:
        idle.end_time = utc_now_for_db()
        idle.duration_minutes = idle.calculate_duration()
    
    # Log the activity
    activity = ActivityLog(
        picker_username=shift.picker_username,
        activity_type='admin_checkout',
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
    if current_user.role != 'admin':
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
    date_str = datetime.now().strftime('%Y%m%d')
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename=shift_report_{date_str}.csv'}
    )

# Add this to your existing routes to integrated with picker dashboard
@app.route('/picker/dashboard')
@login_required
def picker_dashboard():
    """
    This route should already exist in your application.
    We'll modify it to include shift status.
    """
    # Get active shift for this picker
    active_shift = get_active_shift(current_user.username)
    
    # Check if the picker is on break
    on_break = get_picker_on_break(current_user.username)
    
    # Record the activity
    record_activity(current_user.username, 'screen_interaction', 
                   details='Picker dashboard page')
    
    # Your existing picker dashboard code, with added shift status
    # ...
    
    # For now, just render the page
    return render_template('picker_dashboard.html', 
                          active_shift=active_shift,
                          on_break=on_break)