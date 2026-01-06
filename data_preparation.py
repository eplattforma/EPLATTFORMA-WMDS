"""
Data preparation module for warehouse picking AI analysis
Loads and merges picking data with item characteristics
"""
import pandas as pd
from datetime import datetime, timedelta
from app import db
from models import ItemTimeTracking, InvoiceItem, Invoice
from sqlalchemy import text

def load_picking_data(date_from=None, date_to=None, picker_username=None, zone=None):
    """
    Load picking data from the database with optional filters
    
    Args:
        date_from: Start date filter
        date_to: End date filter
        picker_username: Filter by specific picker
        zone: Filter by specific zone
        
    Returns:
        DataFrame with picking data
    """
    # Get order-level timing data using subquery
    from sqlalchemy import func, case
    
    order_timing_subquery = db.session.query(
        ItemTimeTracking.invoice_no,
        func.min(ItemTimeTracking.item_started).label('order_start_time'),
        func.max(ItemTimeTracking.item_started).label('order_end_time')
    ).group_by(ItemTimeTracking.invoice_no).subquery()
    
    query = db.session.query(
        ItemTimeTracking.id.label('tracking_id'),
        ItemTimeTracking.invoice_no,
        ItemTimeTracking.item_code,
        ItemTimeTracking.picker_username,
        ItemTimeTracking.location,
        ItemTimeTracking.zone,
        ItemTimeTracking.corridor,
        ItemTimeTracking.shelf,
        ItemTimeTracking.level,
        ItemTimeTracking.bin_location,
        ItemTimeTracking.picking_time,
        ItemTimeTracking.walking_time,
        ItemTimeTracking.confirmation_time,
        ItemTimeTracking.total_item_time,
        ItemTimeTracking.quantity_picked,
        ItemTimeTracking.quantity_expected,
        ItemTimeTracking.picked_correctly,
        ItemTimeTracking.was_skipped,
        ItemTimeTracking.skip_reason,
        ItemTimeTracking.item_started,
        ItemTimeTracking.item_completed,
        ItemTimeTracking.item_name,
        ItemTimeTracking.unit_type,
        ItemTimeTracking.item_weight,
        ItemTimeTracking.expected_time,
        ItemTimeTracking.efficiency_ratio,
        Invoice.customer_name,
        Invoice.routing,
        Invoice.picking_complete_time,
        Invoice.packing_complete_time,
        order_timing_subquery.c.order_start_time,
        order_timing_subquery.c.order_end_time,
        # Calculate total order time in seconds
        case(
            (Invoice.packing_complete_time.isnot(None) & order_timing_subquery.c.order_start_time.isnot(None),
             func.extract('epoch', Invoice.packing_complete_time - order_timing_subquery.c.order_start_time)),
            (Invoice.picking_complete_time.isnot(None) & order_timing_subquery.c.order_start_time.isnot(None),
             func.extract('epoch', Invoice.picking_complete_time - order_timing_subquery.c.order_start_time)),
            else_=None
        ).label('total_order_time_seconds')
    ).join(
        Invoice,
        ItemTimeTracking.invoice_no == Invoice.invoice_no
    ).outerjoin(
        order_timing_subquery,
        ItemTimeTracking.invoice_no == order_timing_subquery.c.invoice_no
    )
    
    # Apply filters
    if date_from:
        query = query.filter(ItemTimeTracking.item_started >= date_from)
    if date_to:
        query = query.filter(ItemTimeTracking.item_started <= date_to)
    if picker_username:
        query = query.filter(ItemTimeTracking.picker_username == picker_username)
    if zone:
        query = query.filter(ItemTimeTracking.zone == zone)
    
    # Convert to DataFrame
    data = []
    for row in query.all():
        data.append({
            'tracking_id': row.tracking_id,
            'invoice_no': row.invoice_no,
            'item_code': row.item_code,
            'item_name': row.item_name,
            'picker_username': row.picker_username,
            'location': row.location,
            'zone': row.zone,
            'corridor': row.corridor,
            'shelf': row.shelf,
            'level': row.level,
            'bin': row.bin_location,
            'unit_type': row.unit_type,
            'requested_qty': row.quantity_expected,
            'picked_qty': row.quantity_picked,
            'weight_per_unit': row.item_weight,
            'exp_time_minutes': row.expected_time / 60 if row.expected_time else 0,
            'picking_time_seconds': row.picking_time,
            'walking_time_seconds': row.walking_time,
            'confirmation_time_seconds': row.confirmation_time,
            'total_time_seconds': row.total_item_time,
            'picked_correctly': row.picked_correctly,
            'was_skipped': row.was_skipped,
            'skip_reason': row.skip_reason,
            'start_time': row.item_started,
            'end_time': row.item_completed,
            'efficiency_ratio': row.efficiency_ratio,
            'customer_name': row.customer_name,
            'routing': row.routing
        })
    
    df = pd.DataFrame(data)
    
    if not df.empty:
        # Add calculated fields
        df['total_weight'] = df['picked_qty'] * df['weight_per_unit']
        df['picking_efficiency'] = df['exp_time_minutes'] * 60 / df['total_time_seconds'].where(df['total_time_seconds'] > 0, 1)
        df['items_per_second'] = df['picked_qty'] / df['total_time_seconds'].where(df['total_time_seconds'] > 0, 1)
        
        # Time-based features
        df['hour_of_day'] = pd.to_datetime(df['start_time']).dt.hour
        df['day_of_week'] = pd.to_datetime(df['start_time']).dt.dayofweek
        
        # Location complexity
        df['location_complexity'] = df.apply(calculate_location_complexity, axis=1)
        
    return df

def calculate_location_complexity(row):
    """
    Calculate location complexity score based on zone, level, and other factors
    """
    complexity = 0
    
    # Zone complexity
    if row['zone'] == 'SENSITIVE':
        complexity += 2  # Sensitive items require more care
    elif row['zone'] == 'MAIN':
        complexity += 1
    
    # Level complexity (higher/lower levels are harder)
    if pd.notna(row['level']):
        try:
            level = int(row['level'])
            if level == 1:  # Ground level is easiest
                complexity += 0
            elif level <= 3:
                complexity += 1
            else:
                complexity += 2  # High levels are harder
        except:
            complexity += 1
    
    # Unit type complexity
    if row['unit_type'] in ['PALLET', 'CASE']:
        complexity += 2  # Heavy items
    elif row['unit_type'] in ['BOX']:
        complexity += 1
    
    return complexity

def get_picker_statistics(df):
    """
    Calculate picker performance statistics
    """
    if df.empty:
        return pd.DataFrame()
    
    picker_stats = df.groupby('picker_username').agg({
        'total_time_seconds': ['mean', 'std', 'count'],
        'picking_efficiency': 'mean',
        'picked_correctly': 'mean',
        'was_skipped': 'mean',
        'items_per_second': 'mean',
        'location_complexity': 'mean'
    }).round(2)
    
    # Flatten column names
    picker_stats.columns = ['_'.join(col).strip() for col in picker_stats.columns]
    picker_stats = picker_stats.reset_index()
    
    return picker_stats

def get_zone_statistics(df):
    """
    Calculate zone performance statistics
    """
    if df.empty:
        return pd.DataFrame()
    
    zone_stats = df.groupby('zone').agg({
        'total_time_seconds': ['mean', 'std', 'count'],
        'walking_time_seconds': 'mean',
        'picking_time_seconds': 'mean',
        'picking_efficiency': 'mean',
        'location_complexity': 'mean'
    }).round(2)
    
    # Flatten column names
    zone_stats.columns = ['_'.join(col).strip() for col in zone_stats.columns]
    zone_stats = zone_stats.reset_index()
    
    return zone_stats

def get_item_statistics(df):
    """
    Calculate item-level performance statistics
    """
    if df.empty:
        return pd.DataFrame()
    
    item_stats = df.groupby(['item_code', 'item_name']).agg({
        'total_time_seconds': ['mean', 'std', 'count'],
        'picking_efficiency': 'mean',
        'unit_type': 'first',
        'weight_per_unit': 'first',
        'location_complexity': 'mean'
    }).round(2)
    
    # Flatten column names
    item_stats.columns = ['_'.join(col).strip() for col in item_stats.columns]
    item_stats = item_stats.reset_index()
    
    return item_stats