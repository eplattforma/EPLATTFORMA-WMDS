"""
Time Analysis Routes - Detailed breakdown of walking, picking, and packing times
"""

from flask import render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import app, db
from models import (Invoice, InvoiceItem, User, OrderTimeBreakdown, ItemTimeTracking, 
                   ActivityLog, PickingException)
from datetime import datetime, timedelta
import pytz

# Set timezone for consistent reporting
TIMEZONE = pytz.timezone('Europe/Athens')

@app.route('/admin/time_analysis')
@login_required
def time_analysis_dashboard():
    """Admin dashboard for detailed time analysis"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    # Get date filters from request
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    picker_filter = request.args.get('picker', '')
    
    # Set default date range (last 7 days)
    if not start_date_str:
        start_date = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    else:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(tzinfo=TIMEZONE)
    
    if not end_date_str:
        end_date = datetime.now(TIMEZONE).replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=TIMEZONE)
    
    # Base query for completed orders in date range
    # Try to find orders with packing_complete_time in the date range first
    orders_query = Invoice.query.filter(
        Invoice.packing_complete_time >= start_date,
        Invoice.packing_complete_time <= end_date
    )
    
    # Apply picker filter if specified
    if picker_filter:
        orders_query = orders_query.filter(Invoice.assigned_to == picker_filter)
    
    completed_orders = orders_query.order_by(Invoice.packing_complete_time.desc()).all()
    
    # If no orders found, expand search to any recent orders with packing_complete_time
    if not completed_orders:
        orders_query = Invoice.query.filter(
            Invoice.packing_complete_time.isnot(None)
        ).order_by(Invoice.packing_complete_time.desc()).limit(100)
        
        if picker_filter:
            orders_query = orders_query.filter(Invoice.assigned_to == picker_filter)
        
        completed_orders = orders_query.all()
    
    # Get all pickers for filter dropdown
    pickers = User.query.filter_by(role='picker').all()
    
    # Calculate detailed time breakdowns for each order
    order_analytics = []
    total_orders = len(completed_orders)
    total_walking_time = 0
    total_picking_time = 0
    total_packing_time = 0
    
    for invoice in completed_orders:
        # Get ACTUAL times from ItemTimeTracking records (button clicks)
        item_tracking = ItemTimeTracking.query.filter_by(invoice_no=invoice.invoice_no).filter(
            ItemTimeTracking.item_completed.isnot(None)
        ).order_by(ItemTimeTracking.item_completed.asc()).all()
        
        actual_times = calculate_actual_times_from_tracking(invoice, item_tracking) if item_tracking else None
        
        # Extract actual times from tracking data
        actual_picking_time = 0  # Combined walking + picking (from button clicks)
        actual_packing_time = 0
        picking_started = None
        picking_completed = None
        
        if actual_times:
            actual_picking_time = actual_times.get('total_picking_minutes', 0)
            actual_packing_time = actual_times.get('packing_minutes', 0)
            picking_started = actual_times.get('picking_started')
            picking_completed = actual_times.get('picking_completed')
        
        # Use invoice.total_exp_time for the estimated time (already aggregated correctly)
        # Fallback to summing item-level exp_time if invoice total is not available
        estimated_total_time = invoice.total_exp_time or sum(item.exp_time or 0 for item in invoice.items)
        
        # Calculate actual total time
        actual_total_time = actual_picking_time + actual_packing_time
        
        # Calculate metrics for this order
        order_data = {
            'invoice': invoice,
            'actual_picking_time': round(actual_picking_time, 2),
            'actual_packing_time': round(actual_packing_time, 2),
            'actual_total_time': round(actual_total_time, 2),
            'estimated_time': round(estimated_total_time, 1),
            'picking_started': picking_started,
            'picking_completed': picking_completed,
            'packing_completed': to_athens_tz(invoice.packing_complete_time),
            'total_items': InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no).count(),
            'picked_items': InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no, is_picked=True).count(),
            'unique_locations': len(set(item.location for item in invoice.items if item.location)),
            'efficiency_score': calculate_actual_efficiency(actual_total_time, estimated_total_time),
            'has_tracking': actual_times is not None
        }
        
        order_analytics.append(order_data)
        
        # Add to totals (only if we have actual tracking data)
        if actual_times:
            total_picking_time += actual_picking_time
            total_packing_time += actual_packing_time
    
    # Calculate summary statistics
    summary_stats = {
        'total_orders': total_orders,
        'avg_walking_time': round(total_walking_time / max(total_orders, 1), 1),
        'avg_picking_time': round(total_picking_time / max(total_orders, 1), 1),
        'avg_packing_time': round(total_packing_time / max(total_orders, 1), 1),
        'total_time': total_walking_time + total_picking_time + total_packing_time,
        'walking_percentage': round((total_walking_time / max(total_walking_time + total_picking_time + total_packing_time, 1)) * 100, 1),
        'picking_percentage': round((total_picking_time / max(total_walking_time + total_picking_time + total_packing_time, 1)) * 100, 1),
        'packing_percentage': round((total_packing_time / max(total_walking_time + total_picking_time + total_packing_time, 1)) * 100, 1)
    }
    
    return render_template('time_analysis_dashboard.html',
                         order_analytics=order_analytics,
                         summary_stats=summary_stats,
                         pickers=pickers,
                         start_date=start_date,
                         end_date=end_date,
                         picker_filter=picker_filter)

@app.route('/admin/time_analysis/order/<invoice_no>')
@login_required
def detailed_order_analysis(invoice_no):
    """Detailed time analysis for a specific order"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    invoice = Invoice.query.get_or_404(invoice_no)
    time_breakdown = OrderTimeBreakdown.query.filter_by(invoice_no=invoice_no).first()
    
    if not time_breakdown:
        time_breakdown = create_time_breakdown_from_activities(invoice)
    
    # Get item-level time tracking ordered by completion time
    item_tracking = ItemTimeTracking.query.filter_by(invoice_no=invoice_no).filter(
        ItemTimeTracking.item_completed.isnot(None)
    ).order_by(ItemTimeTracking.item_completed.asc()).all()
    
    # Calculate ACTUAL times from ItemTimeTracking records
    actual_times = calculate_actual_times_from_tracking(invoice, item_tracking)
    
    # Get all activity logs for this invoice
    activity_logs = ActivityLog.query.filter_by(invoice_no=invoice_no).order_by(ActivityLog.timestamp).all()
    
    # Get all items with their picking details
    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    
    # Calculate location-based metrics
    location_metrics = calculate_location_metrics(items, activity_logs)
    
    return render_template('detailed_order_analysis.html',
                         invoice=invoice,
                         time_breakdown=time_breakdown,
                         actual_times=actual_times,
                         item_tracking=item_tracking,
                         activity_logs=activity_logs,
                         items=items,
                         location_metrics=location_metrics)


def to_athens_tz(dt):
    """Convert datetime to Athens timezone for display"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Assume UTC if naive
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(TIMEZONE)


def calculate_actual_times_from_tracking(invoice, item_tracking):
    """Calculate actual picking times from ItemTimeTracking records"""
    if not item_tracking:
        return None
    
    # Sort by item_completed to get proper sequence
    sorted_tracking = sorted(
        [t for t in item_tracking if t.item_completed],
        key=lambda x: x.item_completed
    )
    
    if not sorted_tracking:
        return None
    
    # Build lookup for item details (qty) from InvoiceItem
    invoice_items = InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no).all()
    item_details = {item.item_code: item for item in invoice_items}
    
    # Calculate total picking time (sum of all item times)
    total_picking_seconds = sum(t.total_item_time or 0 for t in sorted_tracking)
    
    # First pick started time
    first_item = min((t for t in sorted_tracking if t.item_started), 
                     key=lambda x: x.item_started, default=None)
    # Last pick completed time
    last_item = max((t for t in sorted_tracking if t.item_completed), 
                    key=lambda x: x.item_completed, default=None)
    
    picking_started = first_item.item_started if first_item else None
    picking_completed = last_item.item_completed if last_item else None
    
    # Convert to Athens timezone for display
    picking_started_tz = to_athens_tz(picking_started)
    picking_completed_tz = to_athens_tz(picking_completed)
    
    # Calculate wall clock time (total elapsed time)
    wall_clock_seconds = 0
    if picking_started and picking_completed:
        wall_clock_seconds = (picking_completed - picking_started).total_seconds()
    
    # Calculate packing time if available
    packing_seconds = 0
    if invoice.picking_complete_time and invoice.packing_complete_time:
        packing_seconds = (invoice.packing_complete_time - invoice.picking_complete_time).total_seconds()
    
    # Build per-item data with qty, zone, and estimated time from InvoiceItem
    per_item_times = []
    for t in sorted_tracking:
        inv_item = item_details.get(t.item_code)
        # Get estimated time: exp_time from InvoiceItem (in minutes), convert to seconds
        # exp_time includes walking + picking time estimate
        estimated_seconds = (inv_item.exp_time * 60) if inv_item and inv_item.exp_time else 0
        per_item_times.append({
            'item_code': t.item_code,
            'item_name': t.item_name or (inv_item.item_name if inv_item else ''),
            'location': t.location,
            'zone': t.zone or (inv_item.zone if inv_item else ''),
            'qty': inv_item.qty if inv_item else 0,
            'time_seconds': t.total_item_time or 0,
            'estimated_seconds': estimated_seconds,
            'started': to_athens_tz(t.item_started),
            'completed': to_athens_tz(t.item_completed)
        })
    
    return {
        'total_picking_seconds': total_picking_seconds,
        'total_picking_minutes': round(total_picking_seconds / 60, 2),
        'wall_clock_seconds': wall_clock_seconds,
        'wall_clock_minutes': round(wall_clock_seconds / 60, 2),
        'packing_seconds': packing_seconds,
        'packing_minutes': round(packing_seconds / 60, 2),
        'total_seconds': wall_clock_seconds + packing_seconds,
        'total_minutes': round((wall_clock_seconds + packing_seconds) / 60, 2),
        'picking_started': picking_started_tz,
        'picking_completed': picking_completed_tz,
        'items_tracked': len(sorted_tracking),
        'per_item_times': per_item_times
    }

def create_time_breakdown_from_activities(invoice):
    """Create time breakdown record by analyzing activity logs"""
    try:
        # Get all picking activities for this invoice
        picking_activities = ActivityLog.query.filter_by(
            invoice_no=invoice.invoice_no,
            activity_type='item_pick'
        ).order_by(ActivityLog.timestamp).all()
        
        if not picking_activities:
            return None
        
        # Create time breakdown record
        time_breakdown = OrderTimeBreakdown(
            invoice_no=invoice.invoice_no,
            picker_username=invoice.assigned_to or 'unknown',
            picking_started=picking_activities[0].timestamp,
            picking_completed=picking_activities[-1].timestamp,
            packing_started=picking_activities[-1].timestamp,  # Assume packing starts after last pick
            packing_completed=invoice.packing_complete_time
        )
        
        # Count items and locations
        items = InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no).all()
        time_breakdown.total_items_picked = len([item for item in items if item.is_picked])
        time_breakdown.total_locations_visited = len(set(item.location for item in items if item.location))
        
        # Calculate time breakdowns
        time_breakdown.calculate_times()
        
        # Save to database
        db.session.add(time_breakdown)
        db.session.commit()
        
        return time_breakdown
        
    except Exception as e:
        print(f"Error creating time breakdown for {invoice.invoice_no}: {e}")
        return None

def calculate_actual_efficiency(actual_time, estimated_time):
    """Calculate efficiency score based on actual vs estimated time"""
    if not actual_time or not estimated_time or estimated_time == 0:
        return None
    
    # Calculate efficiency (estimated/actual * 100)
    # If actual < estimated, efficiency > 100% (faster than expected)
    # If actual > estimated, efficiency < 100% (slower than expected)
    efficiency = (estimated_time / actual_time) * 100
    return round(min(efficiency, 200), 1)  # Cap at 200%


def calculate_efficiency_score(invoice, time_breakdown):
    """Calculate an efficiency score based on time vs expected time"""
    if not time_breakdown or not time_breakdown.total_picking_time:
        return None
    
    # Get expected time from items
    items = InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no).all()
    expected_time = sum(item.exp_time or 0 for item in items)
    
    if expected_time == 0:
        return None
    
    # Calculate efficiency (expected/actual * 100)
    actual_time = time_breakdown.total_picking_time + time_breakdown.total_walking_time
    efficiency = (expected_time / actual_time) * 100
    
    return round(efficiency, 1)

def calculate_location_metrics(items, activity_logs):
    """Calculate metrics by location"""
    location_data = {}
    
    for item in items:
        if not item.location:
            continue
            
        if item.location not in location_data:
            location_data[item.location] = {
                'items_count': 0,
                'total_quantity': 0,
                'zone': item.zone,
                'estimated_time': 0
            }
        
        location_data[item.location]['items_count'] += 1
        location_data[item.location]['total_quantity'] += item.qty or 0
        location_data[item.location]['estimated_time'] += item.exp_time or 0
    
    return location_data

@app.route('/admin/time_analysis/export')
@login_required
def export_time_analysis():
    """Export time analysis data as CSV"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    
    # This would generate a CSV export of the time analysis data
    # Implementation would be similar to other export functions
    pass