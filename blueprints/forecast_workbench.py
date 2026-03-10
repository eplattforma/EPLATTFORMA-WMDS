import csv
import io
import logging
from datetime import datetime, date
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, Response, abort
)
from flask_login import login_required, current_user
from sqlalchemy import func, case, and_, or_, text

from app import db
from models import (
    DwItem, DwItemCategory, DwBrand, DwSeason,
    ForecastItemSupplierMap, FactSalesWeeklyItem,
    ForecastSeasonalityMonthly, SkuForecastProfile,
    SkuForecastResult, ForecastRun, Setting,
    extract_item_prefix,
)

logger = logging.getLogger(__name__)

forecast_bp = Blueprint(
    "forecast_workbench", __name__,
    url_prefix="/forecast",
    template_folder="../templates",
)


def admin_or_warehouse_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ('admin', 'warehouse_manager'):
            flash('Access denied. Admin or warehouse manager privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def _float(val, default=0):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@forecast_bp.route('/')
@forecast_bp.route('/suppliers')
@admin_or_warehouse_required
def suppliers():
    return render_template('forecast_workbench/suppliers.html')


@forecast_bp.route('/supplier/<supplier_code>')
@admin_or_warehouse_required
def supplier_detail(supplier_code):
    if supplier_code == 'UNMAPPED':
        supplier_name = 'Unmapped Items'
    else:
        item_with_supplier = DwItem.query.filter_by(supplier_code_365=supplier_code).first()
        supplier_name = item_with_supplier.supplier_name if item_with_supplier else supplier_code
    return render_template(
        'forecast_workbench/supplier_detail.html',
        supplier_code=supplier_code,
        supplier_name=supplier_name,
    )


@forecast_bp.route('/api/suppliers')
@admin_or_warehouse_required
def api_suppliers():
    last_run = ForecastRun.query.order_by(ForecastRun.started_at.desc()).first()

    supplier_code_col = func.coalesce(DwItem.supplier_code_365, 'UNMAPPED')
    supplier_name_col = func.coalesce(DwItem.supplier_name, 'Unmapped Items')

    rows = (
        db.session.query(
            supplier_code_col.label('supplier_code'),
            func.max(supplier_name_col).label('supplier_name'),
            func.count(SkuForecastProfile.item_code_365).label('active_skus'),
            func.sum(case((SkuForecastProfile.review_flag == True, 1), else_=0)).label('review_count'),
            func.sum(case((SkuForecastResult.rounded_order_qty > 0, 1), else_=0)).label('order_count'),
            func.coalesce(func.sum(SkuForecastResult.rounded_order_qty), 0).label('total_order_qty'),
            func.sum(case((SkuForecastProfile.demand_class == 'smooth', 1), else_=0)).label('smooth_count'),
            func.sum(case((SkuForecastProfile.demand_class.in_(['erratic', 'intermittent', 'lumpy']), 1), else_=0)).label('irregular_count'),
        )
        .join(DwItem, DwItem.item_code_365 == SkuForecastProfile.item_code_365)
        .join(SkuForecastResult, SkuForecastResult.item_code_365 == SkuForecastProfile.item_code_365, isouter=True)
        .group_by(supplier_code_col)
        .all()
    )

    suppliers_list = []
    for row in rows:
        suppliers_list.append({
            'supplier_code': row.supplier_code,
            'supplier_name': row.supplier_name,
            'active_skus': row.active_skus or 0,
            'review_count': int(row.review_count or 0),
            'order_count': int(row.order_count or 0),
            'total_order_qty': _float(row.total_order_qty),
            'smooth_count': int(row.smooth_count or 0),
            'irregular_count': int(row.irregular_count or 0),
        })

    last_run_info = None
    if last_run:
        last_run_info = {
            'id': last_run.id,
            'started_at': last_run.started_at.isoformat() if last_run.started_at else None,
            'completed_at': last_run.completed_at.isoformat() if last_run.completed_at else None,
            'status': last_run.status,
            'sku_count': last_run.sku_count,
            'notes': last_run.notes,
        }

    return jsonify({
        'suppliers': suppliers_list,
        'last_run': last_run_info,
    })


@forecast_bp.route('/api/items')
@admin_or_warehouse_required
def api_items():
    supplier = request.args.get('supplier', '')
    category = request.args.get('category', '')
    brand = request.args.get('brand', '')
    demand_class = request.args.get('demand_class', '')
    trend_flag = request.args.get('trend_flag', '')
    seasonality_source = request.args.get('seasonality_source', '')
    review_only = request.args.get('review_only', '')
    order_only = request.args.get('order_only', '')
    active = request.args.get('active', '')
    prefix = request.args.get('prefix', '')

    q = (
        db.session.query(
            DwItem, SkuForecastProfile, SkuForecastResult, ForecastItemSupplierMap,
            DwItemCategory.category_name, DwBrand.brand_name,
        )
        .join(SkuForecastProfile, SkuForecastProfile.item_code_365 == DwItem.item_code_365, isouter=True)
        .join(SkuForecastResult, SkuForecastResult.item_code_365 == DwItem.item_code_365, isouter=True)
        .join(ForecastItemSupplierMap, ForecastItemSupplierMap.item_code_365 == DwItem.item_code_365, isouter=True)
        .join(DwItemCategory, DwItemCategory.category_code_365 == DwItem.category_code_365, isouter=True)
        .join(DwBrand, DwBrand.brand_code_365 == DwItem.brand_code_365, isouter=True)
    )

    if supplier:
        if supplier == 'UNMAPPED':
            q = q.filter(or_(DwItem.supplier_code_365.is_(None), DwItem.supplier_code_365 == ''))
        else:
            q = q.filter(DwItem.supplier_code_365 == supplier)

    if category:
        q = q.filter(DwItem.category_code_365 == category)
    if brand:
        q = q.filter(DwItem.brand_code_365 == brand)
    if demand_class:
        q = q.filter(SkuForecastProfile.demand_class == demand_class)
    if trend_flag:
        q = q.filter(SkuForecastProfile.trend_flag == trend_flag)
    if seasonality_source:
        q = q.filter(SkuForecastProfile.seasonality_source == seasonality_source)
    if review_only == '1':
        q = q.filter(SkuForecastProfile.review_flag == True)
    if order_only == '1':
        q = q.filter(SkuForecastResult.rounded_order_qty > 0)
    if active == '1':
        q = q.filter(DwItem.active == True)
    elif active == '0':
        q = q.filter(DwItem.active == False)
    if prefix:
        q = q.filter(DwItem.item_code_365.like(prefix + '%'))

    q = q.filter(
        or_(SkuForecastProfile.item_code_365.isnot(None), DwItem.active == True)
    )

    rows = q.order_by(DwItem.category_code_365, DwItem.brand_code_365, DwItem.item_code_365).all()

    items = []
    for dw, prof, res, smap, cat_name, brand_name in rows:
        item_prefix = extract_item_prefix(dw.item_code_365)
        items.append({
            'item_code': dw.item_code_365,
            'item_name': dw.item_name,
            'prefix': item_prefix,
            'category_code': dw.category_code_365,
            'category_name': cat_name,
            'brand_code': dw.brand_code_365,
            'brand_name': brand_name,
            'active': dw.active,
            'season_code': dw.season_code_365,
            'supplier_item_code': dw.supplier_item_code,
            'case_qty': dw.case_qty,
            'min_order_qty': dw.min_order_qty,
            'supplier_code': dw.supplier_code_365 or (smap.supplier_code if smap else None),
            'supplier_name': dw.supplier_name or (smap.supplier_name if smap else None),
            'demand_class': prof.demand_class if prof else None,
            'forecast_method': prof.forecast_method if prof else None,
            'trend_flag': prof.trend_flag if prof else None,
            'trend_pct': _float(prof.trend_pct) if prof else None,
            'seasonality_source': prof.seasonality_source if prof else None,
            'seasonality_confidence': prof.seasonality_confidence if prof else None,
            'review_flag': prof.review_flag if prof else False,
            'review_reason': prof.review_reason if prof else None,
            'weeks_non_zero_26': prof.weeks_non_zero_26 if prof else 0,
            'adi_26': _float(prof.adi_26) if prof else None,
            'cv2_26': _float(prof.cv2_26) if prof else None,
            'base_forecast_weekly': _float(res.base_forecast_weekly_qty) if res else 0,
            'final_forecast_weekly': _float(res.final_forecast_weekly_qty) if res else 0,
            'final_forecast_daily': _float(res.final_forecast_daily_qty) if res else 0,
            'forecast_change_pct': _float(res.forecast_change_pct) if res else None,
            'on_hand_qty': _float(res.on_hand_qty) if res else 0,
            'net_available_qty': _float(res.net_available_qty) if res else 0,
            'cover_days': _float(res.cover_days) if res else 0,
            'raw_order_qty': _float(res.raw_recommended_order_qty) if res else 0,
            'rounded_order_qty': _float(res.rounded_order_qty) if res else 0,
            'target_stock_qty': _float(res.target_stock_qty) if res else 0,
            'safety_stock_qty': _float(res.safety_stock_qty) if res else 0,
        })

    return jsonify({'items': items, 'count': len(items)})


@forecast_bp.route('/api/item/<item_code>')
@admin_or_warehouse_required
def api_item_detail(item_code):
    dw = DwItem.query.get(item_code)
    if not dw:
        return jsonify({'error': 'Item not found'}), 404

    prof = SkuForecastProfile.query.get(item_code)
    res = SkuForecastResult.query.get(item_code)
    smap = ForecastItemSupplierMap.query.filter_by(item_code_365=item_code).first()
    cat = DwItemCategory.query.get(dw.category_code_365) if dw.category_code_365 else None
    brand_obj = DwBrand.query.get(dw.brand_code_365) if dw.brand_code_365 else None

    weekly_history = []
    if prof:
        weeks = (
            FactSalesWeeklyItem.query
            .filter_by(item_code_365=item_code)
            .order_by(FactSalesWeeklyItem.week_start.desc())
            .limit(26)
            .all()
        )
        for w in reversed(weeks):
            weekly_history.append({
                'week_start': w.week_start.isoformat(),
                'gross_qty': _float(w.gross_qty),
                'net_qty': _float(w.net_qty),
                'invoice_count': w.invoice_count,
                'customer_count': w.customer_count,
            })

    seasonality = []
    if prof and prof.seasonality_source != 'none' and prof.seasonality_level_code:
        seas = (
            ForecastSeasonalityMonthly.query
            .filter_by(level_type=prof.seasonality_source, level_code=prof.seasonality_level_code)
            .order_by(ForecastSeasonalityMonthly.month_no)
            .all()
        )
        for s in seas:
            seasonality.append({
                'month_no': s.month_no,
                'raw_index': _float(s.raw_index),
                'smoothed_index': _float(s.smoothed_index),
                'confidence': s.confidence,
            })

    result = {
        'item_code': dw.item_code_365,
        'item_name': dw.item_name,
        'active': dw.active,
        'prefix': extract_item_prefix(dw.item_code_365),
        'category_code': dw.category_code_365,
        'category_name': cat.category_name if cat else None,
        'brand_code': dw.brand_code_365,
        'brand_name': brand_obj.brand_name if brand_obj else None,
        'season_code': dw.season_code_365,
        'supplier_item_code': dw.supplier_item_code,
        'case_qty': dw.case_qty,
        'min_order_qty': dw.min_order_qty,
        'supplier': {
            'supplier_code': dw.supplier_code_365 or (smap.supplier_code if smap else None),
            'supplier_name': dw.supplier_name or (smap.supplier_name if smap else None),
            'lead_time_days': _float(smap.lead_time_days) if smap else None,
            'review_cycle_days': _float(smap.review_cycle_days) if smap else None,
            'order_multiple': _float(smap.order_multiple) if smap else None,
            'min_order_qty_override': _float(smap.min_order_qty_override) if smap else None,
        },
        'profile': {
            'demand_class': prof.demand_class if prof else None,
            'forecast_method': prof.forecast_method if prof else None,
            'weeks_non_zero_26': prof.weeks_non_zero_26 if prof else 0,
            'sales_frequency_26': _float(prof.sales_frequency_26) if prof else 0,
            'adi_26': _float(prof.adi_26) if prof else None,
            'avg_non_zero_26': _float(prof.avg_non_zero_26) if prof else None,
            'std_non_zero_26': _float(prof.std_non_zero_26) if prof else None,
            'cv2_26': _float(prof.cv2_26) if prof else None,
            'trend_flag': prof.trend_flag if prof else 'flat',
            'trend_pct': _float(prof.trend_pct) if prof else None,
            'seasonality_source': prof.seasonality_source if prof else 'none',
            'seasonality_level_code': prof.seasonality_level_code if prof else None,
            'seasonality_confidence': prof.seasonality_confidence if prof else 'none',
            'review_flag': prof.review_flag if prof else False,
            'review_reason': prof.review_reason if prof else None,
        } if prof else None,
        'result': {
            'base_forecast_weekly_qty': _float(res.base_forecast_weekly_qty),
            'trend_adjusted_weekly_qty': _float(res.trend_adjusted_weekly_qty),
            'hist_embedded_seasonal_index': _float(res.hist_embedded_seasonal_index),
            'future_seasonal_index': _float(res.future_seasonal_index),
            'final_forecast_weekly_qty': _float(res.final_forecast_weekly_qty),
            'final_forecast_daily_qty': _float(res.final_forecast_daily_qty),
            'cover_days': _float(res.cover_days),
            'lead_time_days': _float(res.lead_time_days),
            'review_cycle_days': _float(res.review_cycle_days),
            'safety_stock_qty': _float(res.safety_stock_qty),
            'target_stock_qty': _float(res.target_stock_qty),
            'on_hand_qty': _float(res.on_hand_qty),
            'incoming_qty': _float(res.incoming_qty),
            'reserved_qty': _float(res.reserved_qty),
            'net_available_qty': _float(res.net_available_qty),
            'raw_recommended_order_qty': _float(res.raw_recommended_order_qty),
            'rounded_order_qty': _float(res.rounded_order_qty),
            'forecast_change_pct': _float(res.forecast_change_pct),
            'calculated_at': res.calculated_at.isoformat() if res.calculated_at else None,
            'run_id': res.run_id,
        } if res else None,
        'weekly_history': weekly_history,
        'seasonality': seasonality,
    }

    return jsonify(result)


@forecast_bp.route('/api/run', methods=['POST'])
@admin_or_warehouse_required
def api_run():
    try:
        from services.forecast.run_service import execute_forecast_run
        result = execute_forecast_run(
            session=db.session,
            created_by=current_user.username,
        )
        return jsonify({'status': 'ok', 'run': result})
    except Exception as e:
        logger.exception("Forecast run failed")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@forecast_bp.route('/api/seasonality/<item_code>')
@admin_or_warehouse_required
def api_seasonality(item_code):
    prof = SkuForecastProfile.query.get(item_code)
    if not prof:
        return jsonify({'seasonality': [], 'source': 'none'})

    result_data = {
        'source': prof.seasonality_source,
        'level_code': prof.seasonality_level_code,
        'confidence': prof.seasonality_confidence,
        'indices': [],
    }

    if prof.seasonality_source != 'none' and prof.seasonality_level_code:
        seas = (
            ForecastSeasonalityMonthly.query
            .filter_by(level_type=prof.seasonality_source, level_code=prof.seasonality_level_code)
            .order_by(ForecastSeasonalityMonthly.month_no)
            .all()
        )
        for s in seas:
            result_data['indices'].append({
                'month_no': s.month_no,
                'raw_index': _float(s.raw_index),
                'smoothed_index': _float(s.smoothed_index),
                'sample_months': s.sample_months,
                'sample_qty': _float(s.sample_qty),
                'confidence': s.confidence,
                'is_reliable': s.is_reliable,
            })

    return jsonify(result_data)


@forecast_bp.route('/api/export/supplier/<supplier_code>')
@admin_or_warehouse_required
def api_export_supplier(supplier_code):
    q = (
        db.session.query(DwItem, SkuForecastProfile, SkuForecastResult)
        .join(SkuForecastProfile, SkuForecastProfile.item_code_365 == DwItem.item_code_365, isouter=True)
        .join(SkuForecastResult, SkuForecastResult.item_code_365 == DwItem.item_code_365, isouter=True)
    )
    if supplier_code == 'UNMAPPED':
        q = q.filter(or_(DwItem.supplier_code_365.is_(None), DwItem.supplier_code_365 == ''))
    else:
        q = q.filter(DwItem.supplier_code_365 == supplier_code)
    q = q.order_by(DwItem.item_code_365)

    rows = q.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Item Code', 'Item Name', 'Active', 'Category', 'Brand', 'Prefix',
        'Demand Class', 'Forecast Method', 'Trend Flag',
        'Seasonality Source', 'Review Flag', 'Review Reason',
        'Base Forecast/Wk', 'Final Forecast/Wk', 'Final Forecast/Day',
        'Forecast Change %',
        'On Hand', 'Net Available', 'Cover Days',
        'Case Qty', 'MOQ', 'Raw Order Qty', 'Rounded Order Qty',
    ])

    for dw, prof, res in rows:
        writer.writerow([
            dw.item_code_365,
            dw.item_name,
            'Yes' if dw.active else 'No',
            dw.category_code_365 or '',
            dw.brand_code_365 or '',
            extract_item_prefix(dw.item_code_365),
            prof.demand_class if prof else '',
            prof.forecast_method if prof else '',
            prof.trend_flag if prof else '',
            prof.seasonality_source if prof else '',
            'Yes' if prof and prof.review_flag else 'No',
            prof.review_reason if prof else '',
            _float(res.base_forecast_weekly_qty) if res else 0,
            _float(res.final_forecast_weekly_qty) if res else 0,
            _float(res.final_forecast_daily_qty) if res else 0,
            _float(res.forecast_change_pct) if res else '',
            _float(res.on_hand_qty) if res else 0,
            _float(res.net_available_qty) if res else 0,
            _float(res.cover_days) if res else 0,
            dw.case_qty or '',
            dw.min_order_qty or '',
            _float(res.raw_recommended_order_qty) if res else 0,
            _float(res.rounded_order_qty) if res else 0,
        ])

    filename = f"forecast_export_{supplier_code}_{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@forecast_bp.route('/admin/supplier-mapping')
@admin_or_warehouse_required
def admin_supplier_mapping():
    return render_template('forecast_workbench/admin_supplier_mapping.html')


@forecast_bp.route('/admin/supplier-mapping/save', methods=['POST'])
@admin_or_warehouse_required
def admin_supplier_mapping_save():
    data = request.get_json()
    if not data or 'mappings' not in data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        for m in data['mappings']:
            item_code = m.get('item_code_365', '').strip()
            if not item_code:
                continue

            existing = ForecastItemSupplierMap.query.filter_by(item_code_365=item_code).first()
            if existing:
                existing.supplier_code = m.get('supplier_code', '').strip()
                existing.supplier_name = m.get('supplier_name', '').strip()
                existing.lead_time_days = m.get('lead_time_days') or None
                existing.review_cycle_days = m.get('review_cycle_days') or 1
                existing.order_multiple = m.get('order_multiple') or None
                existing.min_order_qty_override = m.get('min_order_qty_override') or None
                existing.is_active = m.get('is_active', True)
                existing.notes = m.get('notes', '')
            else:
                new_map = ForecastItemSupplierMap(
                    item_code_365=item_code,
                    supplier_code=m.get('supplier_code', '').strip(),
                    supplier_name=m.get('supplier_name', '').strip(),
                    lead_time_days=m.get('lead_time_days') or None,
                    review_cycle_days=m.get('review_cycle_days') or 1,
                    order_multiple=m.get('order_multiple') or None,
                    min_order_qty_override=m.get('min_order_qty_override') or None,
                    is_active=m.get('is_active', True),
                    notes=m.get('notes', ''),
                )
                db.session.add(new_map)

        db.session.commit()
        return jsonify({'status': 'ok', 'count': len(data['mappings'])})
    except Exception as e:
        db.session.rollback()
        logger.exception("Failed to save supplier mappings")
        return jsonify({'error': str(e)}), 500


@forecast_bp.route('/admin/supplier-mapping/import', methods=['POST'])
@admin_or_warehouse_required
def admin_supplier_mapping_import():
    file = request.files.get('file')
    if not file:
        flash('No file uploaded.', 'error')
        return redirect(url_for('forecast_workbench.admin_supplier_mapping'))

    try:
        stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)
        count = 0
        for row in reader:
            item_code = row.get('item_code_365', '').strip()
            supplier_code = row.get('supplier_code', '').strip()
            supplier_name = row.get('supplier_name', '').strip()
            if not item_code or not supplier_code:
                continue

            existing = ForecastItemSupplierMap.query.filter_by(item_code_365=item_code).first()
            if existing:
                existing.supplier_code = supplier_code
                existing.supplier_name = supplier_name
                existing.lead_time_days = row.get('lead_time_days') or None
                existing.review_cycle_days = row.get('review_cycle_days') or 1
                existing.order_multiple = row.get('order_multiple') or None
                existing.min_order_qty_override = row.get('min_order_qty_override') or None
                existing.is_active = row.get('is_active', 'true').lower() in ('1', 'true', 'yes')
            else:
                new_map = ForecastItemSupplierMap(
                    item_code_365=item_code,
                    supplier_code=supplier_code,
                    supplier_name=supplier_name,
                    lead_time_days=row.get('lead_time_days') or None,
                    review_cycle_days=row.get('review_cycle_days') or 1,
                    order_multiple=row.get('order_multiple') or None,
                    min_order_qty_override=row.get('min_order_qty_override') or None,
                    is_active=row.get('is_active', 'true').lower() in ('1', 'true', 'yes'),
                )
                db.session.add(new_map)
            count += 1

        db.session.commit()
        flash(f'Imported {count} supplier mappings.', 'success')
    except Exception as e:
        db.session.rollback()
        logger.exception("CSV import failed")
        flash(f'Import failed: {str(e)}', 'error')

    return redirect(url_for('forecast_workbench.admin_supplier_mapping'))


@forecast_bp.route('/admin/settings')
@admin_or_warehouse_required
def admin_settings():
    setting_keys = [
        'forecast_default_cover_days',
        'forecast_review_cycle_days',
        'forecast_trend_uplift_trigger',
        'forecast_trend_down_trigger',
        'forecast_trend_uplift_cap',
        'forecast_trend_down_floor',
        'forecast_seasonal_cap_min',
        'forecast_seasonal_cap_max',
    ]
    settings = {}
    for key in setting_keys:
        settings[key] = Setting.get(db.session, key, '')
    return render_template('forecast_workbench/admin_settings.html', settings=settings)


@forecast_bp.route('/admin/settings', methods=['POST'])
@admin_or_warehouse_required
def admin_settings_save():
    setting_keys = [
        'forecast_default_cover_days',
        'forecast_review_cycle_days',
        'forecast_trend_uplift_trigger',
        'forecast_trend_down_trigger',
        'forecast_trend_uplift_cap',
        'forecast_trend_down_floor',
        'forecast_seasonal_cap_min',
        'forecast_seasonal_cap_max',
    ]

    for key in setting_keys:
        val = request.form.get(key, '').strip()
        if val:
            Setting.set(db.session, key, val)

    db.session.commit()
    flash('Forecast settings saved.', 'success')
    return redirect(url_for('forecast_workbench.admin_settings'))
