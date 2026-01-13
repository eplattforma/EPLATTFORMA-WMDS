"""
Enhanced per-product time tracking for AI analysis and warehouse optimization insights
"""
from datetime import datetime
from app import db
from models import ItemTimeTracking, InvoiceItem, User
from timezone_utils import get_utc_now
import re


def parse_location_components(location):
    """
    Parse location string into components for AI analysis
    Examples: 'A1-C2-S3-L4-B5' or '30-07-C02' or 'ZONE-A-001'
    """
    if not location:
        return {
            'corridor': None,
            'shelf': None,
            'level': None,
            'bin_location': None
        }
    
    # Try to extract meaningful components from location
    parts = location.split('-')
    
    result = {
        'corridor': None,
        'shelf': None,
        'level': None,
        'bin_location': None
    }
    
    # Pattern matching for common warehouse location formats
    for part in parts:
        part = part.strip().upper()
        
        # Corridor patterns: C01, C2, COR01
        if re.match(r'^C\w*\d+$', part):
            result['corridor'] = part
        
        # Shelf patterns: S01, S2, SHELF01
        elif re.match(r'^S\w*\d+$', part):
            result['shelf'] = part
        
        # Level patterns: L01, L2, LEV01
        elif re.match(r'^L\w*\d+$', part):
            result['level'] = part
        
        # Bin patterns: B01, B2, BIN01
        elif re.match(r'^B\w*\d+$', part):
            result['bin_location'] = part
        
        # Generic numeric patterns (assign based on position)
        elif re.match(r'^\d+$', part):
            if not result['corridor']:
                result['corridor'] = part
            elif not result['shelf']:
                result['shelf'] = part
            elif not result['level']:
                result['level'] = part
            else:
                result['bin_location'] = part
    
    return result


def start_item_tracking(invoice_no, item_code, picker_username, previous_location=None,
                        start_immediately=True, started_at=None, commit=True):
    """
    Start tracking timing for a specific item pick
    
    Args:
        invoice_no: Invoice number
        item_code: Item code being picked
        picker_username: Username of picker
        previous_location: Location of previous pick (for distance calculation)
        start_immediately: If True, set item_started now. If False, leave it None (set later via arrived endpoint)
        started_at: Optional specific timestamp to use for item_started
        commit: If True, commit to DB. If False, just flush (useful for batch operations)
    
    Returns:
        ItemTimeTracking record
    """
    try:
        # Get item details from invoice
        item = db.session.query(InvoiceItem).filter_by(
            invoice_no=invoice_no,
            item_code=item_code
        ).first()
        
        if not item:
            return None
        
        # Count concurrent pickers
        concurrent_pickers = db.session.query(User).filter(
            User.role == 'picker'
        ).count()
        
        # Parse location components
        location_parts = parse_location_components(item.location)
        
        # Determine start timestamp
        start_ts = None
        if start_immediately:
            start_ts = started_at or get_utc_now()
        
        # Create tracking record
        tracking = ItemTimeTracking(
            invoice_no=invoice_no,
            item_code=item_code,
            picker_username=picker_username,
            item_started=start_ts,
            
            # Item details
            location=item.location,
            zone=item.zone,
            corridor=location_parts['corridor'],
            shelf=location_parts['shelf'],
            level=location_parts['level'],
            bin_location=location_parts['bin_location'],
            
            # Item characteristics
            quantity_expected=item.qty,
            item_weight=item.item_weight,
            item_name=item.item_name,
            unit_type=item.unit_type,
            expected_time=item.exp_time * 60 if item.exp_time else 0,  # Convert to seconds
            
            # Context
            previous_location=previous_location,
            concurrent_pickers=concurrent_pickers
        )
        
        # Set order sequence (position in picking order)
        sequence = db.session.query(InvoiceItem).filter(
            InvoiceItem.invoice_no == invoice_no,
            InvoiceItem.is_picked == True
        ).count()
        tracking.order_sequence = sequence + 1
        
        db.session.add(tracking)
        if commit:
            db.session.commit()
        else:
            db.session.flush()
        
        return tracking
        
    except Exception as e:
        db.session.rollback()
        print(f"Error starting item tracking: {e}")
        return None


def complete_item_tracking(tracking_id, picked_qty, picked_correctly=True, was_skipped=False, skip_reason=None):
    """
    Complete timing tracking for an item
    
    Args:
        tracking_id: ID of ItemTimeTracking record
        picked_qty: Actual quantity picked
        picked_correctly: Whether item was picked correctly
        was_skipped: Whether item was skipped
        skip_reason: Reason for skipping if applicable
    """
    try:
        tracking = db.session.query(ItemTimeTracking).filter_by(id=tracking_id).first()
        if not tracking:
            return False
        
        # Complete timing
        tracking.item_completed = get_utc_now()
        tracking.quantity_picked = picked_qty
        tracking.picked_correctly = picked_correctly
        tracking.was_skipped = was_skipped
        tracking.skip_reason = skip_reason
        
        # Derive picking_time (between Arrived and Confirm)
        if tracking.item_started:
            tracking.picking_time = max((tracking.item_completed - tracking.item_started).total_seconds(), 0)
        
        # Total = walking + picking + confirmation
        walk = float(tracking.walking_time or 0.0)
        pick = float(tracking.picking_time or 0.0)
        conf = float(tracking.confirmation_time or 0.0)
        tracking.total_item_time = walk + pick + conf
        
        # Calculate all metrics
        tracking.calculate_metrics()
        
        db.session.commit()
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"Error completing item tracking: {e}")
        return False


def update_picking_phase_timing(tracking_id, walking_time=None, picking_time=None, confirmation_time=None):
    """
    Update specific phase timings during the picking process
    
    Args:
        tracking_id: ID of ItemTimeTracking record
        walking_time: Time spent walking to location (seconds)
        picking_time: Time spent actually picking (seconds)
        confirmation_time: Time spent on confirmation screen (seconds)
    """
    try:
        tracking = db.session.query(ItemTimeTracking).filter_by(id=tracking_id).first()
        if not tracking:
            return False
        
        if walking_time is not None:
            tracking.walking_time = walking_time
        if picking_time is not None:
            tracking.picking_time = picking_time
        if confirmation_time is not None:
            tracking.confirmation_time = confirmation_time
        
        db.session.commit()
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"Error updating phase timing: {e}")
        return False


def get_item_tracking_data_for_ai(date_from=None, date_to=None, picker_username=None, zone=None):
    """
    Retrieve item tracking data formatted for AI analysis
    
    Args:
        date_from: Start date filter
        date_to: End date filter
        picker_username: Filter by specific picker
        zone: Filter by specific zone
    
    Returns:
        List of dictionaries with AI-ready data
    """
    try:
        query = db.session.query(ItemTimeTracking)
        
        # Apply filters
        if date_from:
            query = query.filter(ItemTimeTracking.created_at >= date_from)
        if date_to:
            query = query.filter(ItemTimeTracking.created_at <= date_to)
        if picker_username:
            query = query.filter(ItemTimeTracking.picker_username == picker_username)
        if zone:
            query = query.filter(ItemTimeTracking.zone == zone)
        
        # Only get completed items
        query = query.filter(ItemTimeTracking.item_completed.isnot(None))
        
        items = query.all()
        
        # Convert to AI-ready format
        ai_data = []
        for item in items:
            ai_data.append(item.to_ai_dict())
        
        return ai_data
        
    except Exception as e:
        print(f"Error retrieving AI data: {e}")
        return []


def get_performance_insights(picker_username=None, days=30):
    """
    Generate performance insights from tracking data
    
    Args:
        picker_username: Filter by specific picker
        days: Number of days to analyze
    
    Returns:
        Dictionary with performance insights
    """
    try:
        from datetime import timedelta
        
        # Calculate date range
        end_date = get_utc_now()
        start_date = end_date - timedelta(days=days)
        
        query = db.session.query(ItemTimeTracking).filter(
            ItemTimeTracking.created_at >= start_date,
            ItemTimeTracking.item_completed.isnot(None)
        )
        
        if picker_username:
            query = query.filter(ItemTimeTracking.picker_username == picker_username)
        
        items = query.all()
        
        if not items:
            return {"message": "No data available for analysis"}
        
        # Calculate insights
        total_items = len(items)
        total_time = sum(item.total_item_time for item in items)
        avg_time_per_item = total_time / total_items if total_items > 0 else 0
        
        # Efficiency analysis
        efficient_items = [item for item in items if item.efficiency_ratio <= 1.0 and item.efficiency_ratio > 0]
        efficiency_rate = len(efficient_items) / total_items * 100 if total_items > 0 else 0
        
        # Zone performance
        zone_performance = {}
        for item in items:
            if item.zone:
                if item.zone not in zone_performance:
                    zone_performance[item.zone] = {
                        'count': 0,
                        'total_time': 0,
                        'avg_efficiency': 0
                    }
                zone_performance[item.zone]['count'] += 1
                zone_performance[item.zone]['total_time'] += item.total_item_time
        
        # Calculate zone averages
        for zone in zone_performance:
            zone_data = zone_performance[zone]
            zone_data['avg_time'] = zone_data['total_time'] / zone_data['count']
        
        # Time of day performance
        time_performance = {'morning': [], 'afternoon': [], 'evening': []}
        for item in items:
            if item.time_of_day:
                time_performance[item.time_of_day].append(item.efficiency_ratio)
        
        for period in time_performance:
            if time_performance[period]:
                time_performance[period] = sum(time_performance[period]) / len(time_performance[period])
            else:
                time_performance[period] = 0
        
        return {
            'summary': {
                'total_items_picked': total_items,
                'total_time_seconds': total_time,
                'average_time_per_item': avg_time_per_item,
                'efficiency_rate_percent': efficiency_rate,
                'analysis_period_days': days
            },
            'zone_performance': zone_performance,
            'time_of_day_efficiency': time_performance,
            'recommendations': generate_ai_recommendations(items)
        }
        
    except Exception as e:
        print(f"Error generating insights: {e}")
        return {"error": f"Failed to generate insights: {e}"}


def generate_ai_recommendations(items):
    """
    Generate AI-style recommendations based on tracking data
    """
    recommendations = []
    
    if not items:
        return recommendations
    
    # Analyze slow items
    slow_items = [item for item in items if item.efficiency_ratio > 1.5]
    if slow_items:
        slow_zones = {}
        for item in slow_items:
            if item.zone:
                slow_zones[item.zone] = slow_zones.get(item.zone, 0) + 1
        
        if slow_zones:
            worst_zone = max(slow_zones, key=slow_zones.get)
            recommendations.append({
                'type': 'zone_optimization',
                'priority': 'high',
                'message': f"Zone {worst_zone} shows {slow_zones[worst_zone]} slow picks. Consider reorganizing layout or providing additional training.",
                'data': slow_zones
            })
    
    # Analyze time of day patterns
    morning_items = [item for item in items if item.time_of_day == 'morning']
    afternoon_items = [item for item in items if item.time_of_day == 'afternoon']
    
    if morning_items and afternoon_items:
        morning_avg = sum(item.efficiency_ratio for item in morning_items) / len(morning_items)
        afternoon_avg = sum(item.efficiency_ratio for item in afternoon_items) / len(afternoon_items)
        
        if morning_avg < afternoon_avg * 0.8:
            recommendations.append({
                'type': 'scheduling',
                'priority': 'medium',
                'message': f"Morning efficiency is {morning_avg:.1f}x vs afternoon {afternoon_avg:.1f}x. Consider scheduling complex picks in the morning.",
                'data': {'morning_efficiency': morning_avg, 'afternoon_efficiency': afternoon_avg}
            })
    
    # Analyze frequent skips
    skipped_items = [item for item in items if item.was_skipped]
    if len(skipped_items) > len(items) * 0.1:  # More than 10% skipped
        skip_reasons = {}
        for item in skipped_items:
            if item.skip_reason:
                skip_reasons[item.skip_reason] = skip_reasons.get(item.skip_reason, 0) + 1
        
        if skip_reasons:
            main_reason = max(skip_reasons, key=skip_reasons.get)
            recommendations.append({
                'type': 'quality_improvement',
                'priority': 'high',
                'message': f"High skip rate ({len(skipped_items)} items). Main reason: {main_reason}. Consider inventory audit or layout review.",
                'data': skip_reasons
            })
    
    return recommendations