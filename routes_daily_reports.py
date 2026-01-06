"""
Daily Picking Reports
Comprehensive reports showing orders per day with picker performance and time analysis
"""

from flask import render_template, request, jsonify, send_file
from app import app, db
from sqlalchemy import text, func, case
from datetime import datetime, timedelta
import pandas as pd
import io
from werkzeug.utils import secure_filename

@app.route('/reports/daily-picking')
def daily_picking_report():
    """Display daily picking report with filters"""
    # Get date range from request
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    picker_filter = request.args.get('picker', 'all')
    
    # Default to last 7 days if no dates provided
    if not start_date:
        start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    
    # Get daily picking data
    daily_data = get_daily_picking_data(start_date, end_date, picker_filter)
    
    # Get picker list for filter dropdown
    pickers = get_active_pickers()
    
    return render_template('reports/daily_picking.html',
                         daily_data=daily_data,
                         pickers=pickers,
                         start_date=start_date,
                         end_date=end_date,
                         picker_filter=picker_filter)

@app.route('/api/daily-picking-data')
def api_daily_picking_data():
    """API endpoint for daily picking data"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    picker_filter = request.args.get('picker', 'all')
    
    data = get_daily_picking_data(start_date, end_date, picker_filter)
    return jsonify(data)

@app.route('/reports/daily-picking/export')
def export_daily_picking_report():
    """Export daily picking report as Excel"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    picker_filter = request.args.get('picker', 'all')
    
    # Get the data
    daily_data = get_daily_picking_data(start_date, end_date, picker_filter)
    
    # Create temporary file for Excel
    import tempfile
    import os
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
        tmp_filename = tmp_file.name
        
        with pd.ExcelWriter(tmp_filename, engine='xlsxwriter') as writer:
            # Summary sheet
            summary_df = create_summary_dataframe(daily_data)
            summary_df.to_excel(writer, sheet_name='Daily Summary', index=False)
            
            # Detailed sheet
            detailed_df = create_detailed_dataframe(daily_data)
            detailed_df.to_excel(writer, sheet_name='Detailed Report', index=False)
            
            # Batch analysis sheet
            batch_df = create_batch_analysis_dataframe(daily_data)
            batch_df.to_excel(writer, sheet_name='Batch Analysis', index=False)
            
            # Picker performance sheet
            picker_df = create_picker_performance_dataframe(daily_data)
            picker_df.to_excel(writer, sheet_name='Picker Performance', index=False)
    
    filename = f"daily_picking_report_{start_date}_to_{end_date}.xlsx"
    
    def remove_file(response):
        try:
            os.unlink(tmp_filename)
        except Exception:
            pass
        return response
    
    response = send_file(
        tmp_filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )
    
    response.call_on_close(lambda: os.unlink(tmp_filename))
    return response

def get_daily_picking_data(start_date, end_date, picker_filter='all'):
    """Get comprehensive daily picking data"""
    
    # Build picker filter condition
    picker_condition = ""
    params = {"start_date": start_date, "end_date": end_date}
    
    if picker_filter and picker_filter != 'all':
        picker_condition = "AND i.assigned_to = :picker"
        params["picker"] = picker_filter
    
    # Main query for daily picking data using existing schema with batch information
    query = text(f"""
        WITH daily_stats AS (
            SELECT 
                DATE(COALESCE(i.status_updated_at, CURRENT_DATE)) as pick_date,
                i.invoice_no,
                i.customer_name,
                i.assigned_to as picker,
                ii.item_code,
                ii.item_name as item_description,
                ii.qty as quantity,
                ii.picked_qty,
                ii.zone,
                ii.corridor,
                ii.location,
                ii.is_picked,
                i.upload_date as order_created,
                ii.locked_by_batch_id,
                -- Batch information
                bps.batch_number,
                bps.status as batch_status,
                bps.assigned_to as batch_picker,
                bps.created_at as batch_created,
                -- Calculate estimated time from exp_time or assume 30 seconds per item
                COALESCE(ii.exp_time, ii.qty * 30) as estimated_seconds,
                -- For actual time, we'll use the estimated time as baseline since we don't have picking timestamps
                CASE 
                    WHEN ii.is_picked = true 
                    THEN COALESCE(ii.exp_time, ii.qty * 30)
                    ELSE NULL 
                END as actual_seconds,
                i.status_updated_at,
                i.status
            FROM invoice_items ii
            JOIN invoices i ON ii.invoice_no = i.invoice_no
            LEFT JOIN batch_picking_sessions bps ON ii.locked_by_batch_id = bps.id
            WHERE DATE(COALESCE(i.status_updated_at, CURRENT_DATE)) BETWEEN :start_date AND :end_date
            {picker_condition}
        )
        SELECT 
            pick_date,
            invoice_no,
            customer_name,
            picker,
            COUNT(*) as total_items,
            SUM(quantity) as total_quantity,
            SUM(CASE WHEN is_picked THEN picked_qty ELSE 0 END) as picked_quantity,
            SUM(estimated_seconds) as total_estimated_seconds,
            SUM(actual_seconds) as total_actual_seconds,
            AVG(actual_seconds) as avg_actual_seconds,
            MIN(status_updated_at) as first_pick_time,
            MAX(status_updated_at) as last_pick_time,
            COUNT(CASE WHEN is_picked THEN 1 END) as picked_items,
            STRING_AGG(DISTINCT zone, ', ') as zones_picked,
            STRING_AGG(DISTINCT corridor, ', ') as corridors_picked,
            COUNT(DISTINCT locked_by_batch_id) FILTER (WHERE locked_by_batch_id IS NOT NULL) as batch_count,
            STRING_AGG(DISTINCT batch_number, ', ') FILTER (WHERE batch_number IS NOT NULL) as batch_numbers,
            STRING_AGG(DISTINCT batch_status, ', ') FILTER (WHERE batch_status IS NOT NULL) as batch_statuses,
            MIN(batch_created) as earliest_batch_created,
            status
        FROM daily_stats
        GROUP BY pick_date, invoice_no, customer_name, picker, status
        ORDER BY pick_date DESC, picker, invoice_no
    """)
    
    result = db.session.execute(query, params).fetchall()
    
    # Process results into structured data
    daily_data = {}
    total_stats = {
        'total_orders': 0,
        'total_items': 0,
        'total_picked': 0,
        'total_estimated_time': 0,
        'total_actual_time': 0,
        'unique_pickers': set()
    }
    
    for row in result:
        date_str = row.pick_date.strftime('%Y-%m-%d') if row.pick_date else 'Unknown'
        
        if date_str not in daily_data:
            daily_data[date_str] = {
                'date': date_str,
                'orders': [],
                'daily_totals': {
                    'orders': 0,
                    'items': 0,
                    'picked_items': 0,
                    'estimated_time': 0,
                    'actual_time': 0,
                    'pickers': set()
                }
            }
        
        # Add order data including batch information
        order_data = {
            'invoice_no': row.invoice_no,
            'customer_name': row.customer_name,
            'picker': row.picker or 'Unassigned',
            'total_items': row.total_items,
            'total_quantity': row.total_quantity,
            'picked_quantity': row.picked_quantity or 0,
            'picked_items': row.picked_items,
            'estimated_seconds': row.total_estimated_seconds or 0,
            'actual_seconds': row.total_actual_seconds or 0,
            'avg_seconds': row.avg_actual_seconds or 0,
            'first_pick_time': row.first_pick_time,
            'last_pick_time': row.last_pick_time,
            'zones_picked': row.zones_picked or '',
            'corridors_picked': row.corridors_picked or '',
            'batch_count': row.batch_count or 0,
            'batch_numbers': row.batch_numbers or '',
            'batch_statuses': row.batch_statuses or '',
            'earliest_batch_created': row.earliest_batch_created,
            'completion_rate': (row.picked_items / row.total_items * 100) if row.total_items > 0 else 0,
            'efficiency_ratio': (row.total_estimated_seconds / row.total_actual_seconds) if row.total_actual_seconds and row.total_actual_seconds > 0 else 0,
            'batch_picking': row.batch_count > 0
        }
        
        daily_data[date_str]['orders'].append(order_data)
        
        # Update daily totals
        daily_totals = daily_data[date_str]['daily_totals']
        daily_totals['orders'] += 1
        daily_totals['items'] += row.total_items
        daily_totals['picked_items'] += row.picked_items
        daily_totals['estimated_time'] += row.total_estimated_seconds or 0
        daily_totals['actual_time'] += row.total_actual_seconds or 0
        if row.picker:
            daily_totals['pickers'].add(row.picker)
        
        # Update overall totals
        total_stats['total_orders'] += 1
        total_stats['total_items'] += row.total_items
        total_stats['total_picked'] += row.picked_items
        total_stats['total_estimated_time'] += row.total_estimated_seconds or 0
        total_stats['total_actual_time'] += row.total_actual_seconds or 0
        if row.picker:
            total_stats['unique_pickers'].add(row.picker)
    
    # Convert sets to counts
    for date_data in daily_data.values():
        date_data['daily_totals']['unique_pickers'] = len(date_data['daily_totals']['pickers'])
        date_data['daily_totals']['pickers'] = list(date_data['daily_totals']['pickers'])
    
    total_stats['unique_pickers'] = len(total_stats['unique_pickers'])
    
    return {
        'daily_data': daily_data,
        'total_stats': total_stats,
        'date_range': {'start': start_date, 'end': end_date},
        'picker_filter': picker_filter
    }

def get_active_pickers():
    """Get list of active pickers for filter dropdown"""
    result = db.session.execute(text("""
        SELECT DISTINCT assigned_to
        FROM invoices
        WHERE assigned_to IS NOT NULL
        AND status_updated_at > NOW() - INTERVAL '30 days'
        ORDER BY assigned_to
    """)).fetchall()
    
    return [row.assigned_to for row in result]

def create_summary_dataframe(daily_data):
    """Create summary DataFrame for Excel export"""
    summary_data = []
    
    for date_str, date_data in daily_data['daily_data'].items():
        totals = date_data['daily_totals']
        summary_data.append({
            'Date': date_str,
            'Total Orders': totals['orders'],
            'Total Items': totals['items'],
            'Picked Items': totals['picked_items'],
            'Completion Rate %': (totals['picked_items'] / totals['items'] * 100) if totals['items'] > 0 else 0,
            'Estimated Time (hours)': totals['estimated_time'] / 3600,
            'Actual Time (hours)': totals['actual_time'] / 3600,
            'Efficiency Ratio': (totals['estimated_time'] / totals['actual_time']) if totals['actual_time'] > 0 else 0,
            'Active Pickers': totals['unique_pickers'],
            'Picker Names': ', '.join(totals['pickers'])
        })
    
    return pd.DataFrame(summary_data)

def create_detailed_dataframe(daily_data):
    """Create detailed DataFrame for Excel export"""
    detailed_data = []
    
    for date_str, date_data in daily_data['daily_data'].items():
        for order in date_data['orders']:
            detailed_data.append({
                'Date': date_str,
                'Invoice No': order['invoice_no'],
                'Customer': order['customer_name'],
                'Picker': order['picker'],
                'Total Items': order['total_items'],
                'Picked Items': order['picked_items'],
                'Total Quantity': order['total_quantity'],
                'Picked Quantity': order['picked_quantity'],
                'Completion Rate %': order['completion_rate'],
                'Estimated Time (min)': order['estimated_seconds'] / 60,
                'Actual Time (min)': order['actual_seconds'] / 60,
                'Avg Time per Item (sec)': order['avg_seconds'],
                'Efficiency Ratio': order['efficiency_ratio'],
                'Batch Picking': 'Yes' if order['batch_picking'] else 'No',
                'Batch Numbers': order['batch_numbers'],
                'Batch Statuses': order['batch_statuses'],
                'Batch Count': order['batch_count'],
                'Earliest Batch Created': order['earliest_batch_created'],
                'First Pick': order['first_pick_time'],
                'Last Pick': order['last_pick_time'],
                'Zones': order['zones_picked'],
                'Corridors': order['corridors_picked']
            })
    
    return pd.DataFrame(detailed_data)

def create_batch_analysis_dataframe(daily_data):
    """Create batch analysis DataFrame for Excel export"""
    batch_data = []
    
    for date_str, date_data in daily_data['daily_data'].items():
        for order in date_data['orders']:
            if order['batch_picking'] and order['batch_numbers']:
                batch_numbers = order['batch_numbers'].split(', ')
                batch_statuses = order['batch_statuses'].split(', ') if order['batch_statuses'] else []
                
                for i, batch_number in enumerate(batch_numbers):
                    batch_status = batch_statuses[i] if i < len(batch_statuses) else 'Unknown'
                    batch_data.append({
                        'Date': date_str,
                        'Batch Number': batch_number,
                        'Batch Status': batch_status,
                        'Invoice No': order['invoice_no'],
                        'Customer': order['customer_name'],
                        'Picker': order['picker'],
                        'Items in Batch': order['total_items'],
                        'Items Picked': order['picked_items'],
                        'Completion Rate %': order['completion_rate'],
                        'Estimated Time (min)': order['estimated_seconds'] / 60,
                        'Actual Time (min)': order['actual_seconds'] / 60,
                        'Efficiency Ratio': order['efficiency_ratio'],
                        'Zones': order['zones_picked'],
                        'Corridors': order['corridors_picked'],
                        'Batch Created': order['earliest_batch_created']
                    })
    
    return pd.DataFrame(batch_data)

def create_picker_performance_dataframe(daily_data):
    """Create picker performance DataFrame for Excel export"""
    picker_stats = {}
    
    for date_str, date_data in daily_data['daily_data'].items():
        for order in date_data['orders']:
            picker = order['picker']
            if picker not in picker_stats:
                picker_stats[picker] = {
                    'picker': picker,
                    'total_orders': 0,
                    'total_items': 0,
                    'picked_items': 0,
                    'total_estimated_time': 0,
                    'total_actual_time': 0,
                    'working_days': set()
                }
            
            stats = picker_stats[picker]
            stats['total_orders'] += 1
            stats['total_items'] += order['total_items']
            stats['picked_items'] += order['picked_items']
            stats['total_estimated_time'] += order['estimated_seconds']
            stats['total_actual_time'] += order['actual_seconds']
            stats['working_days'].add(date_str)
    
    # Convert to list format
    performance_data = []
    for picker, stats in picker_stats.items():
        performance_data.append({
            'Picker': picker,
            'Working Days': len(stats['working_days']),
            'Total Orders': stats['total_orders'],
            'Total Items': stats['total_items'],
            'Picked Items': stats['picked_items'],
            'Completion Rate %': (stats['picked_items'] / stats['total_items'] * 100) if stats['total_items'] > 0 else 0,
            'Avg Orders per Day': stats['total_orders'] / len(stats['working_days']) if stats['working_days'] else 0,
            'Avg Items per Day': stats['total_items'] / len(stats['working_days']) if stats['working_days'] else 0,
            'Estimated Time (hours)': stats['total_estimated_time'] / 3600,
            'Actual Time (hours)': stats['total_actual_time'] / 3600,
            'Efficiency Ratio': (stats['total_estimated_time'] / stats['total_actual_time']) if stats['total_actual_time'] > 0 else 0,
            'Avg Time per Item (min)': (stats['total_actual_time'] / stats['picked_items'] / 60) if stats['picked_items'] > 0 else 0
        })
    
    return pd.DataFrame(performance_data)