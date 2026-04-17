import csv
import io
import logging
from datetime import datetime, date
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, Response, abort, current_app
)
from flask_login import login_required, current_user
from sqlalchemy import func, case, and_, or_, text
from sqlalchemy.exc import IntegrityError

from app import db
from timezone_utils import get_utc_now
from routes import validate_csrf_token
from models import (
    DwItem, DwItemCategory, DwBrand, DwSeason,
    ForecastItemSupplierMap, FactSalesWeeklyItem,
    ForecastSeasonalityMonthly, SkuForecastProfile,
    SkuForecastResult, SkuOrderingSnapshot, SkuForecastOverride,
    ForecastRun, Setting,
    StockPosition,
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


def _compute_override_status(review_due_at, now_utc):
    if not review_due_at:
        return 'active'
    due = review_due_at.replace(tzinfo=None) if review_due_at.tzinfo else review_due_at
    now = now_utc.replace(tzinfo=None) if now_utc.tzinfo else now_utc
    if due < now:
        return 'past_due'
    if (due - now).days <= 7:
        return 'review_due'
    return 'active'


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


@forecast_bp.route('/help')
@admin_or_warehouse_required
def help_manual():
    return render_template('forecast_workbench/help.html')


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
    import logging
    logger = logging.getLogger(__name__)

    try:
        from datetime import datetime, timedelta
        from timezone_utils import get_utc_now
        TIMEOUT_MINUTES = 45

        running_run = ForecastRun.query.filter_by(status="running").order_by(ForecastRun.started_at.desc()).first()
        if running_run:
            reference_time = running_run.last_heartbeat_at or running_run.started_at
            stale_cutoff = datetime.utcnow() - timedelta(minutes=TIMEOUT_MINUTES)
            if reference_time and reference_time < stale_cutoff:
                logger.warning(f"api_suppliers: marking stale run {running_run.id} as failed")
                running_run.status = "failed"
                running_run.completed_at = get_utc_now()
                running_run.notes = f"Marked as failed: no heartbeat for {TIMEOUT_MINUTES}+ minutes"
                db.session.commit()

        last_run = ForecastRun.query.order_by(ForecastRun.started_at.desc()).first()

        from services.forecast.week_utils import get_completed_week_cutoff
        completed_week_cutoff = get_completed_week_cutoff()
        sales_cutoff = completed_week_cutoff - timedelta(weeks=52)

        sql = text("""
            SELECT
                COALESCE(d.supplier_code_365, 'UNMAPPED') AS supplier_code,
                MAX(COALESCE(d.supplier_name, 'Unmapped Items')) AS supplier_name,
                COUNT(p.item_code_365) AS active_skus,
                SUM(CASE WHEN p.review_flag = TRUE THEN 1 ELSE 0 END) AS review_count,
                SUM(CASE WHEN os.rounded_order_qty > 0 THEN 1 ELSE 0 END) AS order_count,
                COALESCE(SUM(os.rounded_order_qty), 0) AS total_order_qty,
                SUM(CASE WHEN p.demand_class = 'smooth' THEN 1 ELSE 0 END) AS smooth_count,
                SUM(CASE WHEN p.demand_class IN ('erratic','intermittent','lumpy') THEN 1 ELSE 0 END) AS irregular_count,
                COALESCE(s.total_sales, 0) AS total_sales
            FROM sku_forecast_profile p
            JOIN ps_items_dw d ON d.item_code_365 = p.item_code_365
            LEFT JOIN LATERAL (
                SELECT rounded_order_qty FROM sku_ordering_snapshot oss
                WHERE oss.item_code_365 = p.item_code_365
                ORDER BY oss.snapshot_at DESC LIMIT 1
            ) os ON TRUE
            LEFT JOIN (
                SELECT COALESCE(d2.supplier_code_365, 'UNMAPPED') AS supplier_code,
                       SUM(f.sales_ex_vat) AS total_sales
                FROM fact_sales_weekly_item f
                JOIN ps_items_dw d2 ON d2.item_code_365 = f.item_code_365
                WHERE f.week_start >= :sales_cutoff AND f.week_start < :week_cutoff
                GROUP BY COALESCE(d2.supplier_code_365, 'UNMAPPED')
            ) s ON s.supplier_code = COALESCE(d.supplier_code_365, 'UNMAPPED')
            GROUP BY COALESCE(d.supplier_code_365, 'UNMAPPED'), s.total_sales
        """)

        try:
            rows = db.session.execute(sql, {
                'sales_cutoff': sales_cutoff,
                'week_cutoff': completed_week_cutoff,
            }).fetchall()
        except Exception as e:
            logger.warning(f"Suppliers query failed (likely timeout during forecast run): {e}")
            db.session.rollback()
            sql_simple = text("""
                SELECT
                    COALESCE(d.supplier_code_365, 'UNMAPPED') AS supplier_code,
                    MAX(COALESCE(d.supplier_name, 'Unmapped Items')) AS supplier_name,
                    COUNT(p.item_code_365) AS active_skus,
                    SUM(CASE WHEN p.review_flag = TRUE THEN 1 ELSE 0 END) AS review_count,
                    SUM(CASE WHEN os.rounded_order_qty > 0 THEN 1 ELSE 0 END) AS order_count,
                    COALESCE(SUM(os.rounded_order_qty), 0) AS total_order_qty,
                    SUM(CASE WHEN p.demand_class = 'smooth' THEN 1 ELSE 0 END) AS smooth_count,
                    SUM(CASE WHEN p.demand_class IN ('erratic','intermittent','lumpy') THEN 1 ELSE 0 END) AS irregular_count,
                    0 AS total_sales
                FROM sku_forecast_profile p
                JOIN ps_items_dw d ON d.item_code_365 = p.item_code_365
                LEFT JOIN LATERAL (
                    SELECT rounded_order_qty FROM sku_ordering_snapshot oss
                    WHERE oss.item_code_365 = p.item_code_365
                    ORDER BY oss.snapshot_at DESC LIMIT 1
                ) os ON TRUE
                GROUP BY COALESCE(d.supplier_code_365, 'UNMAPPED')
            """)
            rows = db.session.execute(sql_simple).fetchall()

        suppliers_list = []
        for row in rows:
            suppliers_list.append({
                'supplier_code': row.supplier_code,
                'supplier_name': row.supplier_name,
                'active_skus': row.active_skus or 0,
                'review_count': int(row.review_count or 0),
                'order_count': int(row.order_count or 0),
                'total_order_qty': float(row.total_order_qty or 0),
                'smooth_count': int(row.smooth_count or 0),
                'irregular_count': int(row.irregular_count or 0),
                'total_sales': float(row.total_sales or 0),
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
                'sales_period_start': getattr(last_run, 'sales_period_start', None),
                'sales_period_end': getattr(last_run, 'sales_period_end', None),
                'sales_total_qty': float(getattr(last_run, 'sales_total_qty', 0) or 0),
                'sales_total_value_ex_vat': float(getattr(last_run, 'sales_total_value_ex_vat', 0) or 0),
            }
            if not last_run_info['sales_period_start']:
                last_completed = ForecastRun.query.filter_by(status="completed").order_by(ForecastRun.id.desc()).first()
                if last_completed:
                    last_run_info['sales_period_start'] = getattr(last_completed, 'sales_period_start', None)
                    last_run_info['sales_period_end'] = getattr(last_completed, 'sales_period_end', None)
                    last_run_info['sales_total_qty'] = float(getattr(last_completed, 'sales_total_qty', 0) or 0)
                    last_run_info['sales_total_value_ex_vat'] = float(getattr(last_completed, 'sales_total_value_ex_vat', 0) or 0)
            if last_run_info['sales_period_start'] and hasattr(last_run_info['sales_period_start'], 'isoformat'):
                last_run_info['sales_period_start'] = last_run_info['sales_period_start'].isoformat()
            if last_run_info['sales_period_end'] and hasattr(last_run_info['sales_period_end'], 'isoformat'):
                last_run_info['sales_period_end'] = last_run_info['sales_period_end'].isoformat()

        return jsonify({
            'suppliers': suppliers_list,
            'last_run': last_run_info,
        })

    except Exception as e:
        logger.error(f"api_suppliers error: {e}")
        db.session.rollback()
        return jsonify({'suppliers': [], 'last_run': None, 'error': str(e)})


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
    active = request.args.get('active', '1')
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
    filter_order_only = order_only == '1'
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

    try:
        from services.forecast.oos_demand_service import bulk_get_oos_total_days
        oos_8w_map = bulk_get_oos_total_days(db.session, num_weeks=8)
    except Exception:
        oos_8w_map = {}

    item_codes_in_result = [r[0].item_code_365 for r in rows]
    try:
        from services.forecast.ordering_refresh_service import get_latest_snapshots
        snap_map = get_latest_snapshots(db.session, item_codes=item_codes_in_result)
    except Exception:
        snap_map = {}

    override_map = {}
    try:
        ovr_q = SkuForecastOverride.query.filter(
            SkuForecastOverride.item_code_365.in_(item_codes_in_result),
            SkuForecastOverride.is_active == True,
        ).all()
        for o in ovr_q:
            code = o.item_code_365
            if code not in override_map or o.created_at > override_map[code].created_at:
                override_map[code] = o
    except Exception:
        override_map = {}
    now_utc = get_utc_now()

    expiry_risk_map = {}
    try:
        today_date = date.today()
        sp_rows = (
            StockPosition.query
            .filter(StockPosition.item_code.in_(item_codes_in_result))
            .filter(StockPosition.stock_quantity > 0)
            .all()
        )
        sp_by_item = {}
        for sp in sp_rows:
            sp_by_item.setdefault(sp.item_code, []).append(sp)
        forecast_map = {}
        for r in rows:
            dw_r, prof_r, res_r = r[0], r[1], r[2]
            if res_r:
                forecast_map[dw_r.item_code_365] = _float(res_r.final_forecast_weekly_qty)
        for ic, batches in sp_by_item.items():
            weekly_rate = forecast_map.get(ic, 0)
            daily_rate = weekly_rate / 7.0 if weekly_rate > 0 else 0
            total_qty = sum(float(b.stock_quantity or 0) for b in batches)
            expired_qty = 0.0
            at_risk_qty = 0.0
            for b in batches:
                qty = float(b.stock_quantity or 0)
                if not b.expiry_date:
                    continue
                try:
                    exp_date = datetime.strptime(b.expiry_date.strip(), '%Y-%m-%d').date()
                    days_left = (exp_date - today_date).days
                    if days_left < 0:
                        expired_qty += qty
                    elif daily_rate > 0 and qty / daily_rate > days_left:
                        at_risk_qty += max(0, qty - daily_rate * days_left)
                    elif daily_rate <= 0 and qty > 0:
                        at_risk_qty += qty
                except (ValueError, TypeError):
                    pass
            if total_qty <= 0:
                expiry_risk_map[ic] = 'no_stock'
            elif expired_qty > 0 or at_risk_qty > 0:
                risk_pct = (expired_qty + at_risk_qty) / total_qty * 100
                if risk_pct >= 50:
                    expiry_risk_map[ic] = 'critical'
                elif risk_pct >= 20:
                    expiry_risk_map[ic] = 'high'
                else:
                    expiry_risk_map[ic] = 'moderate'
            else:
                expiry_risk_map[ic] = 'safe'
    except Exception:
        expiry_risk_map = {}

    items = []
    for dw, prof, res, smap, cat_name, brand_name in rows:
        item_prefix = extract_item_prefix(dw.item_code_365)
        snap = snap_map.get(dw.item_code_365)
        _manual_raw = getattr(prof, 'manual_order_qty', None) if prof else None
        manual_ord = float(_manual_raw) if _manual_raw is not None else None
        effective_order = manual_ord if manual_ord is not None else (_float(snap.rounded_order_qty) if snap else 0)
        if filter_order_only and effective_order <= 0:
            continue
        ovr = override_map.get(dw.item_code_365)
        ovr_status = _compute_override_status(ovr.review_due_at, now_utc) if ovr else None
        final_source = getattr(snap, 'final_forecast_source', None) if snap else None
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
            'system_forecast_weekly': _float(res.final_forecast_weekly_qty) if res else 0,
            'final_forecast_weekly': _float(ovr.override_weekly_qty) if ovr else (_float(res.final_forecast_weekly_qty) if res else 0),
            'final_forecast_daily': _float(res.final_forecast_daily_qty) if res else 0,
            'forecast_change_pct': _float(res.forecast_change_pct) if res else None,
            'target_weeks_of_stock': _float(prof.target_weeks_of_stock) if prof and prof.target_weeks_of_stock else 4.0,
            'on_hand_qty': _float(snap.on_hand_qty) if snap else 0,
            'net_available_qty': _float(snap.net_available_qty) if snap else 0,
            'raw_order_qty': _float(snap.raw_recommended_order_qty) if snap else 0,
            'rounded_order_qty': _float(snap.rounded_order_qty) if snap else 0,
            'manual_order_qty': manual_ord,
            'effective_order_qty': effective_order,
            'target_stock_qty': _float(snap.target_stock_qty) if snap else 0,
            'buffer_stock_qty': _float(snap.buffer_days) if snap else 0,
            'lead_time_days': _float(snap.lead_time_days) if snap else 0,
            'review_cycle_days': _float(snap.review_cycle_days) if snap else 0,
            'incoming_qty': _float(snap.incoming_qty) if snap else 0,
            'reserved_qty': _float(snap.reserved_qty) if snap else 0,
            'ordering_snapshot_at': snap.snapshot_at.isoformat() + 'Z' if snap and snap.snapshot_at else None,
            'final_forecast_source': final_source,
            'forecast_confidence': prof.forecast_confidence if prof else None,
            'seed_source': prof.seed_source if prof else None,
            'oos_weeks_26': getattr(prof, 'oos_weeks_26', 0) or 0 if prof else 0,
            'oos_adjusted': getattr(prof, 'oos_adjusted', False) or False if prof else False,
            'oos_days_8w': oos_8w_map.get(dw.item_code_365, 0),
            'history_incomplete': getattr(prof, 'history_incomplete', False) or False if prof else False,
            'baseline_source': getattr(prof, 'baseline_source', None) if prof else None,
            'expiry_risk': expiry_risk_map.get(dw.item_code_365),
            'override_weekly_qty': _float(ovr.override_weekly_qty) if ovr else None,
            'reason_code': ovr.reason_code if ovr else None,
            'override_reason_code': ovr.reason_code if ovr else None,
            'reason_note': ovr.reason_note if ovr else None,
            'override_reason_note': ovr.reason_note if ovr else None,
            'review_due_at': ovr.review_due_at.isoformat() + 'Z' if ovr and ovr.review_due_at else None,
            'override_review_due_at': ovr.review_due_at.isoformat() + 'Z' if ovr and ovr.review_due_at else None,
            'override_created_at': ovr.created_at.isoformat() + 'Z' if ovr and ovr.created_at else None,
            'override_created_by': ovr.created_by if ovr else None,
            'override_status': ovr_status,
            'is_active': ovr.is_active if ovr else False,
            'has_override': ovr is not None,
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
    oos_by_week = {}
    if prof:
        from datetime import date, timedelta
        weeks = (
            FactSalesWeeklyItem.query
            .filter_by(item_code_365=item_code)
            .order_by(FactSalesWeeklyItem.week_start.desc())
            .limit(26)
            .all()
        )
        sales_by_week = {}
        for w in weeks:
            sales_by_week[w.week_start.isoformat()] = w

        try:
            from services.forecast.oos_demand_service import get_oos_days_by_week
            oos_weekly = get_oos_days_by_week(db.session, item_code, 26)
            oos_by_week = {w["week_start"].isoformat(): w["oos_days"] for w in oos_weekly}
        except Exception:
            oos_by_week = {}

        today = date.today()
        current_monday = today - timedelta(days=today.weekday())
        all_week_starts = []
        for i in range(26):
            ws = current_monday - timedelta(weeks=25 - i)
            all_week_starts.append(ws.isoformat())

        for ws_iso in all_week_starts:
            w = sales_by_week.get(ws_iso)
            weekly_history.append({
                'week_start': ws_iso,
                'gross_qty': _float(w.gross_qty) if w else 0,
                'net_qty': _float(w.net_qty) if w else 0,
                'invoice_count': w.invoice_count if w else 0,
                'customer_count': w.customer_count if w else 0,
                'oos_days': oos_by_week.get(ws_iso, 0),
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
            'oos_weeks_26': getattr(prof, 'oos_weeks_26', 0) or 0,
            'oos_adjusted': getattr(prof, 'oos_adjusted', False) or False,
            'target_weeks_of_stock': _float(prof.target_weeks_of_stock) if prof and prof.target_weeks_of_stock else 4.0,
            'forecast_confidence': prof.forecast_confidence if prof else None,
            'history_incomplete': getattr(prof, 'history_incomplete', False) or False,
            'baseline_source': getattr(prof, 'baseline_source', None),
        } if prof else None,
        'result': {
            'base_forecast_weekly_qty': _float(res.base_forecast_weekly_qty),
            'trend_adjusted_weekly_qty': _float(res.trend_adjusted_weekly_qty),
            'hist_embedded_seasonal_index': _float(res.hist_embedded_seasonal_index),
            'future_seasonal_index': _float(res.future_seasonal_index),
            'final_forecast_weekly_qty': _float(res.final_forecast_weekly_qty),
            'final_forecast_daily_qty': _float(res.final_forecast_daily_qty),
            'forecast_change_pct': _float(res.forecast_change_pct),
            'calculated_at': res.calculated_at.isoformat() if res.calculated_at else None,
            'run_id': res.run_id,
        } if res else None,
        'weekly_history': weekly_history,
        'seasonality': seasonality,
    }

    stock_batches = (
        StockPosition.query
        .filter_by(item_code=item_code)
        .filter(StockPosition.stock_quantity > 0)
        .order_by(StockPosition.expiry_date.asc())
        .all()
    )
    weekly_rate = _float(res.final_forecast_weekly_qty) if res else 0
    daily_rate = weekly_rate / 7.0 if weekly_rate > 0 else 0
    today_date = date.today()
    expiry_batches = []
    total_stock_qty = 0.0
    at_risk_qty = 0.0
    expired_qty = 0.0
    for sp in stock_batches:
        qty = float(sp.stock_quantity or 0)
        total_stock_qty += qty
        exp_str = sp.expiry_date
        exp_date = None
        days_until_expiry = None
        weeks_of_stock_in_batch = None
        batch_status = 'ok'
        if exp_str:
            try:
                exp_date = datetime.strptime(exp_str.strip(), '%Y-%m-%d').date()
                days_until_expiry = (exp_date - today_date).days
                if days_until_expiry < 0:
                    batch_status = 'expired'
                    expired_qty += qty
                elif daily_rate > 0:
                    days_to_sell = qty / daily_rate
                    if days_to_sell > days_until_expiry:
                        batch_status = 'at_risk'
                        oversupply = qty - (daily_rate * days_until_expiry)
                        at_risk_qty += max(0, oversupply)
                    else:
                        batch_status = 'ok'
                    weeks_of_stock_in_batch = round(qty / weekly_rate, 1) if weekly_rate > 0 else None
                else:
                    batch_status = 'no_demand'
                    at_risk_qty += qty
            except (ValueError, TypeError):
                pass
        expiry_batches.append({
            'expiry_date': exp_str,
            'qty': qty,
            'days_until_expiry': days_until_expiry,
            'weeks_of_stock': weeks_of_stock_in_batch,
            'status': batch_status,
        })
    cumulative_days_needed = 0
    for batch in expiry_batches:
        if batch['expiry_date'] and daily_rate > 0:
            cumulative_days_needed += batch['qty'] / daily_rate
            batch['cumulative_sell_through_days'] = round(cumulative_days_needed, 1)
        else:
            batch['cumulative_sell_through_days'] = None
    overall_weeks_cover = round(total_stock_qty / weekly_rate, 1) if weekly_rate > 0 else None
    if total_stock_qty <= 0:
        expiry_risk_level = 'no_stock'
    elif expired_qty > 0 or at_risk_qty > 0:
        risk_pct = (expired_qty + at_risk_qty) / total_stock_qty * 100
        if risk_pct >= 50:
            expiry_risk_level = 'critical'
        elif risk_pct >= 20:
            expiry_risk_level = 'high'
        else:
            expiry_risk_level = 'moderate'
    else:
        expiry_risk_level = 'safe'
    result['stock_expiry'] = {
        'batches': expiry_batches,
        'total_stock_qty': total_stock_qty,
        'expired_qty': expired_qty,
        'at_risk_qty': round(at_risk_qty, 1),
        'weekly_forecast': weekly_rate,
        'overall_weeks_cover': overall_weeks_cover,
        'risk_level': expiry_risk_level,
    }

    latest_snap = (
        db.session.query(SkuOrderingSnapshot)
        .filter_by(item_code_365=item_code)
        .order_by(SkuOrderingSnapshot.snapshot_at.desc())
        .first()
    )
    if latest_snap:
        result['ordering_snapshot_at'] = latest_snap.snapshot_at.isoformat() + 'Z' if latest_snap.snapshot_at else None
        result['result']['on_hand_qty'] = _float(latest_snap.on_hand_qty)
        result['result']['net_available_qty'] = _float(latest_snap.net_available_qty)
        result['result']['target_stock_qty'] = _float(latest_snap.target_stock_qty)
        result['result']['raw_recommended_order_qty'] = _float(latest_snap.raw_recommended_order_qty)
        result['result']['rounded_order_qty'] = _float(latest_snap.rounded_order_qty)
    elif result.get('result'):
        result['result']['on_hand_qty'] = 0
        result['result']['net_available_qty'] = 0
        result['result']['target_stock_qty'] = 0
        result['result']['raw_recommended_order_qty'] = 0
        result['result']['rounded_order_qty'] = 0

    ovr = SkuForecastOverride.query.filter_by(
        item_code_365=item_code, is_active=True
    ).order_by(SkuForecastOverride.created_at.desc()).first()
    if ovr:
        now_utc = get_utc_now()
        ovr_status = _compute_override_status(ovr.review_due_at, now_utc)
        result['override'] = {
            'id': ovr.id,
            'override_weekly_qty': _float(ovr.override_weekly_qty),
            'reason_code': ovr.reason_code,
            'reason_note': ovr.reason_note,
            'review_due_at': ovr.review_due_at.isoformat() + 'Z' if ovr.review_due_at else None,
            'created_at': ovr.created_at.isoformat() + 'Z' if ovr.created_at else None,
            'created_by': ovr.created_by,
            'last_reviewed_at': ovr.last_reviewed_at.isoformat() + 'Z' if ovr.last_reviewed_at else None,
            'status': ovr_status,
        }
    else:
        result['override'] = None

    return jsonify(result)


@forecast_bp.route('/api/run', methods=['POST'])
@admin_or_warehouse_required
def api_run():
    import threading
    from models import ForecastRun
    from timezone_utils import get_utc_now
    from datetime import datetime, timedelta
    from sqlalchemy import text
    TIMEOUT_MINUTES = 45
    now_naive = datetime.utcnow()
    stale_cutoff = now_naive - timedelta(minutes=TIMEOUT_MINUTES)

    stale_runs = ForecastRun.query.filter_by(status='running').all()
    for sr in stale_runs:
        reference_time = sr.last_heartbeat_at or sr.started_at
        if reference_time and reference_time < stale_cutoff:
            logger.warning(f"Marking stale forecast run {sr.id} as failed (last heartbeat {reference_time})")
            sr.status = "failed"
            sr.completed_at = get_utc_now()
            sr.notes = f"Marked as failed: no heartbeat for {TIMEOUT_MINUTES}+ minutes"
    if stale_runs:
        db.session.commit()

    active = ForecastRun.query.filter_by(status='running').first()
    if active:
        reference_time = active.last_heartbeat_at or active.started_at
        if reference_time and reference_time >= stale_cutoff:
            return jsonify({'status': 'already_running', 'run_id': active.id})

    username = current_user.username
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'incremental')
    if mode not in ('incremental', 'full_26', 'full_52', 'full_rebuild'):
        mode = 'incremental'

    def _run_in_background(app, username, mode):
        with app.app_context():
            try:
                from services.forecast.run_service import execute_forecast_run
                from sqlalchemy.orm import sessionmaker
                SessionLocal = sessionmaker(bind=db.engine)
                session = SessionLocal()
                try:
                    execute_forecast_run(session=session, created_by=username, mode=mode)
                finally:
                    session.close()
            except Exception:
                logger.exception("Background forecast run failed")

    t = threading.Thread(target=_run_in_background, args=(current_app._get_current_object(), username, mode), daemon=True)
    t.start()
    return jsonify({'status': 'started', 'mode': mode})


@forecast_bp.route('/api/refresh-weekly-sales', methods=['POST'])
@admin_or_warehouse_required
def api_refresh_weekly_sales():
    import threading

    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'incremental')
    if mode not in ('incremental', 'full_26', 'full_52', 'full_rebuild'):
        mode = 'incremental'

    username = current_user.username

    def _run(app, username, mode):
        with app.app_context():
            try:
                from services.forecast.weekly_sales_builder import build_weekly_sales
                from sqlalchemy.orm import sessionmaker
                SessionLocal = sessionmaker(bind=db.engine)
                session = SessionLocal()
                try:
                    result = build_weekly_sales(session, weeks_back=52, mode=mode)
                    session.commit()
                    rows = result["upserted"] if isinstance(result, dict) else result
                    logger.info(f"[Admin] Weekly sales refresh completed: mode={mode}, rows={rows}, by={username}")
                finally:
                    session.close()
            except Exception:
                logger.exception("Weekly sales refresh failed")

    t = threading.Thread(target=_run, args=(current_app._get_current_object(), username, mode), daemon=True)
    t.start()
    return jsonify({'status': 'started', 'mode': mode})


@forecast_bp.route('/api/recompute-seasonality', methods=['POST'])
@admin_or_warehouse_required
def api_recompute_seasonality():
    import threading

    username = current_user.username

    def _run(app, username):
        with app.app_context():
            try:
                from services.forecast.seasonality_service import compute_seasonal_indices
                from sqlalchemy.orm import sessionmaker
                SessionLocal = sessionmaker(bind=db.engine)
                session = SessionLocal()
                try:
                    rows = compute_seasonal_indices(session, force=True)
                    session.commit()
                    logger.info(f"[Admin] Seasonality recompute completed: rows={rows}, by={username}")
                finally:
                    session.close()
            except Exception:
                logger.exception("Seasonality recompute failed")

    t = threading.Thread(target=_run, args=(current_app._get_current_object(), username), daemon=True)
    t.start()
    return jsonify({'status': 'started'})


@forecast_bp.route('/api/run/status')
@admin_or_warehouse_required
def api_run_status():
    from models import ForecastRun
    from datetime import datetime, timedelta
    from timezone_utils import get_utc_now
    TIMEOUT_MINUTES = 45
    running = ForecastRun.query.filter_by(status="running").order_by(ForecastRun.started_at.desc()).first()
    run = running or ForecastRun.query.order_by(ForecastRun.id.desc()).first()
    if not run:
        return jsonify({'status': 'none'})
    if run.status == 'running':
        reference_time = run.last_heartbeat_at or run.started_at
        stale_cutoff = datetime.utcnow() - timedelta(minutes=TIMEOUT_MINUTES)
        if reference_time and reference_time < stale_cutoff:
            logger.warning(f"Status poll: marking stale run {run.id} as failed (last heartbeat {reference_time})")
            run.status = "failed"
            run.completed_at = get_utc_now()
            run.notes = f"Marked as failed: no heartbeat for {TIMEOUT_MINUTES}+ minutes"
            db.session.commit()
    sales_period_start = getattr(run, 'sales_period_start', None)
    sales_period_end = getattr(run, 'sales_period_end', None)
    sales_total_qty = float(getattr(run, 'sales_total_qty', 0) or 0)
    sales_total_value_ex_vat = float(getattr(run, 'sales_total_value_ex_vat', 0) or 0)
    if not sales_period_start:
        last_completed = ForecastRun.query.filter_by(status="completed").order_by(ForecastRun.id.desc()).first()
        if last_completed:
            sales_period_start = getattr(last_completed, 'sales_period_start', None)
            sales_period_end = getattr(last_completed, 'sales_period_end', None)
            sales_total_qty = float(getattr(last_completed, 'sales_total_qty', 0) or 0)
            sales_total_value_ex_vat = float(getattr(last_completed, 'sales_total_value_ex_vat', 0) or 0)
    return jsonify({
        'run_id': run.id,
        'status': run.status,
        'started_at': run.started_at.isoformat() + 'Z' if run.started_at else None,
        'completed_at': run.completed_at.isoformat() + 'Z' if run.completed_at else None,
        'sku_count': run.sku_count,
        'notes': run.notes,
        'current_step': run.current_step,
        'progress_note': run.progress_note,
        'last_heartbeat_at': run.last_heartbeat_at.isoformat() + 'Z' if run.last_heartbeat_at else None,
        'sales_period_start': sales_period_start.isoformat() if sales_period_start else None,
        'sales_period_end': sales_period_end.isoformat() if sales_period_end else None,
        'sales_total_qty': sales_total_qty,
        'sales_total_value_ex_vat': sales_total_value_ex_vat,
    })


def _get_ordering_job_status():
    try:
        row = db.session.execute(text(
            "SELECT status, started_at, completed_at, progress, error_message, snapshot_count "
            "FROM ordering_refresh_jobs ORDER BY id DESC LIMIT 1"
        )).fetchone()
        if row:
            return {
                'status': row[0],
                'started_at': row[1].isoformat() + 'Z' if row[1] else None,
                'completed_at': row[2].isoformat() + 'Z' if row[2] else None,
                'progress': row[3],
                'error': row[4],
                'snapshot_count': row[5],
            }
    except Exception:
        db.session.rollback()
    return None

def _ensure_ordering_jobs_table():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS ordering_refresh_jobs (
                id SERIAL PRIMARY KEY,
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                progress TEXT,
                error_message TEXT,
                snapshot_count INTEGER
            )
        """))
        db.session.commit()
    except Exception:
        db.session.rollback()

@forecast_bp.route('/api/ordering/refresh', methods=['POST'])
@admin_or_warehouse_required
def api_ordering_refresh():
    import threading

    _ensure_ordering_jobs_table()

    existing = _get_ordering_job_status()
    if existing and existing['status'] == 'running':
        from datetime import datetime
        started = datetime.fromisoformat(existing['started_at'].replace('Z', '+00:00')) if existing['started_at'] else None
        stale_minutes = 15
        if started and (get_utc_now().replace(tzinfo=None) - started.replace(tzinfo=None)).total_seconds() > stale_minutes * 60:
            db.session.execute(text(
                "UPDATE ordering_refresh_jobs SET status = 'failed', completed_at = :now, "
                "error_message = 'Timed out after 15 minutes' "
                "WHERE id = (SELECT MAX(id) FROM ordering_refresh_jobs) AND status = 'running'"
            ))
            db.session.commit()
        else:
            return jsonify({'status': 'already_running', 'started_at': existing['started_at'], 'progress': existing['progress']})

    supplier = request.json.get('supplier_code') if request.is_json else request.form.get('supplier_code')
    username = current_user.username

    now = get_utc_now()
    db.session.execute(text(
        "INSERT INTO ordering_refresh_jobs (status, started_at, progress) VALUES ('running', :now, 'Starting...')"
    ), {'now': now})
    db.session.commit()

    def _run_ordering(app, username, supplier_code):
        with app.app_context():
            try:
                from services.forecast.ordering_refresh_service import refresh_ordering_snapshot
                from sqlalchemy.orm import sessionmaker
                SessionLocal = sessionmaker(bind=db.engine)
                session = SessionLocal()
                progress_session = SessionLocal()
                try:
                    def _progress(msg):
                        try:
                            progress_session.execute(text(
                                "UPDATE ordering_refresh_jobs SET progress = :msg "
                                "WHERE id = (SELECT MAX(id) FROM ordering_refresh_jobs)"
                            ), {'msg': msg[:500]})
                            progress_session.commit()
                        except Exception:
                            progress_session.rollback()

                    result = refresh_ordering_snapshot(
                        session=session,
                        supplier_code=supplier_code,
                        created_by=username,
                        progress_callback=_progress,
                    )
                    session.commit()

                    snap_count = result.get('snapshot_count', 0)
                    session.execute(text(
                        "UPDATE ordering_refresh_jobs SET status = 'completed', completed_at = :now, "
                        "progress = :msg, snapshot_count = :cnt "
                        "WHERE id = (SELECT MAX(id) FROM ordering_refresh_jobs)"
                    ), {'now': get_utc_now(), 'msg': f"Done — {snap_count} items refreshed", 'cnt': snap_count})
                    session.commit()
                finally:
                    session.close()
                    progress_session.close()
            except Exception as e:
                logger.exception("Background ordering refresh failed")
                try:
                    from sqlalchemy.orm import sessionmaker as sm2
                    s2 = sm2(bind=db.engine)()
                    s2.execute(text(
                        "UPDATE ordering_refresh_jobs SET status = 'failed', completed_at = :now, "
                        "error_message = :err, progress = :msg "
                        "WHERE id = (SELECT MAX(id) FROM ordering_refresh_jobs)"
                    ), {'now': get_utc_now(), 'err': str(e)[:2000], 'msg': f"Failed: {str(e)[:100]}"})
                    s2.commit()
                    s2.close()
                except Exception:
                    pass

    t = threading.Thread(
        target=_run_ordering,
        args=(current_app._get_current_object(), username, supplier),
        daemon=True,
    )
    t.start()
    return jsonify({'status': 'started', 'supplier_code': supplier})


@forecast_bp.route('/api/item/<item_code>/target-weeks', methods=['POST'])
@admin_or_warehouse_required
def api_set_target_weeks(item_code):
    prof = SkuForecastProfile.query.get(item_code)
    if not prof:
        return jsonify({'error': 'Profile not found'}), 404

    data = request.get_json(silent=True) or {}
    target_weeks = data.get('target_weeks_of_stock')
    if target_weeks is None:
        return jsonify({'error': 'target_weeks_of_stock is required'}), 400

    try:
        target_weeks = float(target_weeks)
        if target_weeks < 0 or target_weeks > 52:
            return jsonify({'error': 'target_weeks_of_stock must be between 0 and 52'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid value for target_weeks_of_stock'}), 400

    prof.target_weeks_of_stock = Decimal(str(round(target_weeks, 4)))
    prof.target_weeks_updated_at = get_utc_now()
    prof.target_weeks_updated_by = current_user.username
    db.session.commit()

    try:
        from services.forecast.ordering_refresh_service import refresh_ordering_snapshot
        refresh_ordering_snapshot(
            session=db.session,
            item_codes=[item_code],
            created_by=current_user.username,
        )
        db.session.commit()
    except Exception:
        logger.exception(f"Failed to recalculate ordering for {item_code} after target_weeks change")

    return jsonify({
        'status': 'ok',
        'item_code': item_code,
        'target_weeks_of_stock': float(prof.target_weeks_of_stock),
    })


@forecast_bp.route('/api/item/<item_code>/manual-order-qty', methods=['POST'])
@admin_or_warehouse_required
def api_set_manual_order_qty(item_code):
    prof = SkuForecastProfile.query.get(item_code)
    if not prof:
        return jsonify({'error': 'Profile not found'}), 404

    data = request.get_json(silent=True) or {}
    qty = data.get('manual_order_qty')

    if qty is None:
        # Clear the manual override
        prof.manual_order_qty = None
        prof.manual_order_qty_updated_at = get_utc_now()
        prof.manual_order_qty_updated_by = current_user.username
        db.session.commit()
        return jsonify({'status': 'ok', 'item_code': item_code, 'manual_order_qty': None})

    try:
        qty = float(qty)
        if qty < 0:
            return jsonify({'error': 'Quantity cannot be negative'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid quantity value'}), 400

    prof.manual_order_qty = Decimal(str(round(qty, 6)))
    prof.manual_order_qty_updated_at = get_utc_now()
    prof.manual_order_qty_updated_by = current_user.username
    db.session.commit()

    return jsonify({
        'status': 'ok',
        'item_code': item_code,
        'manual_order_qty': float(prof.manual_order_qty),
    })


@forecast_bp.route('/api/ordering/status')
@admin_or_warehouse_required
def api_ordering_status():
    _ensure_ordering_jobs_table()
    job = _get_ordering_job_status()

    if job and job['status'] == 'running':
        return jsonify({
            'status': 'running',
            'started_at': job['started_at'],
            'progress': job['progress'],
        })

    latest = (
        db.session.query(SkuOrderingSnapshot)
        .order_by(SkuOrderingSnapshot.snapshot_at.desc())
        .first()
    )
    resp = {
        'status': 'completed' if latest else 'none',
        'snapshot_at': latest.snapshot_at.isoformat() + 'Z' if latest and latest.snapshot_at else None,
        'created_by': latest.created_by if latest else None,
    }
    if job:
        if job['completed_at']:
            resp['last_refresh_at'] = job['completed_at']
        if job['error']:
            resp['last_error'] = job['error']
        if job['snapshot_count'] is not None:
            resp['snapshot_count'] = job['snapshot_count']
    return jsonify(resp)


@forecast_bp.route('/api/overrides')
@admin_or_warehouse_required
def api_overrides():
    supplier_code = request.args.get('supplier', '')
    if not supplier_code:
        return jsonify({'error': 'supplier is required'}), 400

    from sqlalchemy.orm import aliased
    dw = aliased(DwItem)
    smap = aliased(ForecastItemSupplierMap)

    q = db.session.query(dw.item_code_365).outerjoin(
        smap, dw.item_code_365 == smap.item_code_365
    )
    if supplier_code == 'UNMAPPED':
        q = q.filter(or_(dw.supplier_code_365.is_(None), dw.supplier_code_365 == ''))
    else:
        q = q.filter(
            or_(dw.supplier_code_365 == supplier_code, smap.supplier_code == supplier_code)
        )
    item_codes_q = q.distinct().all()
    item_codes = [r[0] for r in item_codes_q]

    overrides = SkuForecastOverride.query.filter(
        SkuForecastOverride.item_code_365.in_(item_codes),
        SkuForecastOverride.is_active == True,
    ).order_by(SkuForecastOverride.created_at.desc()).all()

    now_utc = get_utc_now()
    result = []
    for o in overrides:
        status = _compute_override_status(o.review_due_at, now_utc)
        result.append({
            'id': o.id,
            'item_code_365': o.item_code_365,
            'override_weekly_qty': _float(o.override_weekly_qty),
            'reason_code': o.reason_code,
            'reason_note': o.reason_note,
            'is_active': o.is_active,
            'review_due_at': o.review_due_at.isoformat() + 'Z' if o.review_due_at else None,
            'created_at': o.created_at.isoformat() + 'Z' if o.created_at else None,
            'created_by': o.created_by,
            'last_reviewed_at': o.last_reviewed_at.isoformat() + 'Z' if o.last_reviewed_at else None,
            'last_reviewed_by': o.last_reviewed_by,
            'status': status,
        })
    return jsonify({'overrides': result, 'count': len(result)})


@forecast_bp.route('/api/override/apply', methods=['POST'])
@admin_or_warehouse_required
def api_override_apply():
    if not validate_csrf_token():
        return jsonify({'error': 'Invalid CSRF token'}), 403
    from datetime import timedelta
    data = request.get_json(silent=True) or {}
    item_code = data.get('item_code', '').strip()
    override_qty = data.get('override_weekly_qty')
    reason_code = data.get('reason_code', '').strip() or None
    reason_note = data.get('reason_note', '').strip() or None

    if not item_code:
        return jsonify({'error': 'item_code is required'}), 400
    if override_qty is None:
        return jsonify({'error': 'override_weekly_qty is required'}), 400
    try:
        override_qty = float(override_qty)
        if override_qty < 0:
            return jsonify({'error': 'override_weekly_qty must be >= 0'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid override_weekly_qty'}), 400

    dw = DwItem.query.get(item_code)
    if not dw:
        return jsonify({'error': 'Item not found'}), 404

    try:
        now = get_utc_now()
        review_due = now + timedelta(days=28)

        SkuForecastOverride.query.filter_by(
            item_code_365=item_code, is_active=True
        ).update({'is_active': False, 'cleared_at': now, 'cleared_by': current_user.username})

        db.session.flush()

        result = db.session.execute(
            text("""
                INSERT INTO sku_forecast_override
                    (item_code_365, override_weekly_qty, reason_code, reason_note,
                     created_at, created_by, is_active, review_due_at)
                VALUES
                    (:item_code, :qty, :reason_code, :reason_note,
                     :now, :created_by, true, :review_due)
                ON CONFLICT (item_code_365) WHERE is_active = true
                DO UPDATE SET
                    override_weekly_qty = EXCLUDED.override_weekly_qty,
                    reason_code         = EXCLUDED.reason_code,
                    reason_note         = EXCLUDED.reason_note,
                    created_at          = EXCLUDED.created_at,
                    created_by          = EXCLUDED.created_by,
                    review_due_at       = EXCLUDED.review_due_at
                RETURNING id
            """),
            {
                'item_code': item_code,
                'qty': str(Decimal(str(round(override_qty, 6)))),
                'reason_code': reason_code,
                'reason_note': reason_note,
                'now': now,
                'created_by': current_user.username,
                'review_due': review_due,
            }
        )
        new_id = result.scalar()
        db.session.commit()
        return jsonify({'status': 'ok', 'item_code': item_code, 'override_id': new_id})
    except Exception:
        db.session.rollback()
        logger.exception(f"Failed to apply override for {item_code}")
        return jsonify({'error': 'Failed to apply override'}), 500


@forecast_bp.route('/api/override/clear', methods=['POST'])
@admin_or_warehouse_required
def api_override_clear():
    if not validate_csrf_token():
        return jsonify({'error': 'Invalid CSRF token'}), 403
    data = request.get_json(silent=True) or {}
    item_code = data.get('item_code', '').strip()
    if not item_code:
        return jsonify({'error': 'item_code is required'}), 400

    try:
        updated = SkuForecastOverride.query.filter_by(
            item_code_365=item_code, is_active=True
        ).update({'is_active': False, 'cleared_at': get_utc_now(), 'cleared_by': current_user.username})
        db.session.commit()
        return jsonify({'status': 'ok', 'item_code': item_code, 'cleared_count': updated})
    except Exception:
        db.session.rollback()
        logger.exception(f"Failed to clear override for {item_code}")
        return jsonify({'error': 'Failed to clear override'}), 500


@forecast_bp.route('/api/override/bulk', methods=['POST'])
@admin_or_warehouse_required
def api_override_bulk():
    if not validate_csrf_token():
        return jsonify({'error': 'Invalid CSRF token'}), 403
    from datetime import timedelta
    data = request.get_json(silent=True) or {}
    action = data.get('action', '')
    item_codes = data.get('item_codes', [])
    if not item_codes or not isinstance(item_codes, list):
        return jsonify({'error': 'item_codes list is required'}), 400
    if action not in ('extend_review', 'clear', 'mark_reviewed', 'change_review_date'):
        return jsonify({'error': 'Invalid action'}), 400

    new_date = None
    if action == 'change_review_date':
        new_date_str = data.get('review_due_date', '')
        if not new_date_str:
            return jsonify({'error': 'review_due_date is required for change_review_date'}), 400
        try:
            from datetime import datetime as dt_cls
            new_date = dt_cls.fromisoformat(new_date_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid review_due_date format'}), 400

    def _run_bulk():
        now = get_utc_now()
        affected = 0

        if action == 'clear':
            affected = SkuForecastOverride.query.filter(
                SkuForecastOverride.item_code_365.in_(item_codes),
                SkuForecastOverride.is_active == True,
            ).update({'is_active': False, 'cleared_at': now, 'cleared_by': current_user.username}, synchronize_session=False)

        elif action == 'extend_review':
            overrides = SkuForecastOverride.query.filter(
                SkuForecastOverride.item_code_365.in_(item_codes),
                SkuForecastOverride.is_active == True,
            ).all()
            for o in overrides:
                base = o.review_due_at if o.review_due_at and o.review_due_at > now else now
                o.review_due_at = base + timedelta(weeks=4)
                affected += 1

        elif action == 'mark_reviewed':
            overrides = SkuForecastOverride.query.filter(
                SkuForecastOverride.item_code_365.in_(item_codes),
                SkuForecastOverride.is_active == True,
            ).all()
            for o in overrides:
                o.last_reviewed_at = now
                o.last_reviewed_by = current_user.username
                if o.review_due_at and o.review_due_at < now:
                    o.review_due_at = now + timedelta(weeks=4)
                affected += 1

        elif action == 'change_review_date':
            affected = SkuForecastOverride.query.filter(
                SkuForecastOverride.item_code_365.in_(item_codes),
                SkuForecastOverride.is_active == True,
            ).update({'review_due_at': new_date}, synchronize_session=False)

        db.session.commit()
        return affected

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            affected = _run_bulk()
            return jsonify({'status': 'ok', 'action': action, 'affected': affected})
        except IntegrityError as ie:
            db.session.rollback()
            logger.warning(
                "Bulk override action '%s' hit IntegrityError on attempt %d/%d (likely concurrent override on overlapping items): %s",
                action, attempt, max_attempts, ie.orig if hasattr(ie, 'orig') else ie,
            )
            if attempt >= max_attempts:
                break
            continue
        except Exception:
            db.session.rollback()
            logger.exception(f"Bulk override action '{action}' failed")
            return jsonify({'error': 'Bulk action failed'}), 500

    logger.error(
        "Bulk override action '%s' aborted after %d IntegrityError retries for %d items",
        action, max_attempts, len(item_codes),
    )
    return jsonify({
        'error': 'Bulk action conflicted with concurrent override changes; please retry',
        'detail': 'unique_constraint_violation',
    }), 409


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

    item_codes = [dw.item_code_365 for dw, _, _ in rows]
    try:
        from services.forecast.ordering_refresh_service import get_latest_snapshots
        snap_map = get_latest_snapshots(db.session, item_codes=item_codes)
    except Exception:
        snap_map = {}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Item Code', 'Item Name', 'Active', 'Category', 'Brand', 'Prefix',
        'Demand Class', 'Forecast Method', 'Trend Flag',
        'Seasonality Source', 'Review Flag', 'Review Reason',
        'Base Forecast/Wk', 'Final Forecast/Wk', 'Final Forecast/Day',
        'Forecast Change %', 'Target Weeks',
        'On Hand', 'Net Available',
        'MOQ', 'Raw Order Qty', 'Rounded Order Qty',
    ])

    for dw, prof, res in rows:
        snap = snap_map.get(dw.item_code_365)
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
            _float(prof.target_weeks_of_stock) if prof and prof.target_weeks_of_stock else 4.0,
            _float(snap.on_hand_qty) if snap else 0,
            _float(snap.net_available_qty) if snap else 0,
            dw.min_order_qty or '',
            _float(snap.raw_recommended_order_qty) if snap else 0,
            _float(snap.rounded_order_qty) if snap else 0,
        ])

    filename = f"forecast_export_{supplier_code}_{date.today().isoformat()}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@forecast_bp.route('/supplier/<supplier_code>/send-po', methods=['POST'])
@admin_or_warehouse_required
def send_supplier_po(supplier_code):
    """Send a forecasting supplier PO to PS365 using the shared sender.

    Aggregates all items for the supplier whose effective order qty
    (manual override > 0 if set, else snapshot rounded_order_qty) is positive
    and posts a single PO via the shared service.
    """
    from services.ps365_purchase_order_service import create_ps365_purchase_order
    from services.forecast.ordering_refresh_service import get_latest_snapshots

    if not supplier_code or supplier_code == 'UNMAPPED':
        flash("A real supplier code is required to send a PO.", "error")
        return redirect(url_for('forecast_workbench.supplier_detail', supplier_code=supplier_code))

    rows = (
        db.session.query(DwItem, SkuForecastProfile)
        .join(SkuForecastProfile, SkuForecastProfile.item_code_365 == DwItem.item_code_365, isouter=True)
        .filter(DwItem.supplier_code_365 == supplier_code)
        .all()
    )
    if not rows:
        flash(f"No items found for supplier {supplier_code}.", "warning")
        return redirect(url_for('forecast_workbench.supplier_detail', supplier_code=supplier_code))

    item_codes = [dw.item_code_365 for dw, _ in rows]
    snap_map = get_latest_snapshots(db.session, item_codes=item_codes)

    order_lines = []
    for dw, prof in rows:
        snap = snap_map.get(dw.item_code_365)
        manual_raw = getattr(prof, 'manual_order_qty', None) if prof else None
        manual_ord = float(manual_raw) if manual_raw is not None else None
        if manual_ord is not None:
            qty = manual_ord
        elif snap and snap.rounded_order_qty is not None:
            qty = float(snap.rounded_order_qty)
        else:
            qty = 0
        if qty > 0:
            order_lines.append({
                "item_code_365": dw.item_code_365,
                "line_quantity": int(qty),
            })

    if not order_lines:
        flash(f"No items with a positive order quantity for supplier {supplier_code}.", "warning")
        return redirect(url_for('forecast_workbench.supplier_detail', supplier_code=supplier_code))

    logger.info("Forecast supplier PO send: supplier=%s lines=%d items=%s",
                supplier_code, len(order_lines),
                [(l["item_code_365"], l["line_quantity"]) for l in order_lines])

    result = create_ps365_purchase_order(
        supplier_code=supplier_code,
        order_lines=order_lines,
        user_code=current_user.username,
        comments=f"Forecast supplier order {supplier_code} - {len(order_lines)} items",
        cart_prefix="WMDS-FCT",
    )

    if result["success"]:
        logger.info("Forecast PO created: %s supplier=%s lines=%d",
                    result["po_code"], supplier_code, result["lines_count"])
        flash(f"Purchase Order created! PO Code: {result['po_code']} ({result['lines_count']} items)", "success")
    else:
        logger.error("Forecast PO send failed for supplier %s: %s",
                     supplier_code, result["error"])
        flash(f"PS365 error: {result['error']}", "error")

    return redirect(url_for('forecast_workbench.supplier_detail', supplier_code=supplier_code))


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
        'forecast_buffer_stock_days',
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
        'forecast_buffer_stock_days',
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


@forecast_bp.route('/api/debug/<item_code>')
@admin_or_warehouse_required
def api_debug(item_code):
    from datetime import timedelta
    from services.forecast.classification_service import _get_weekly_gross_qtys, _compute_profile
    from services.forecast.base_forecast_service import (
        _get_recent_weekly_qtys, _compute_ma8, _compute_median6,
        _compute_seeded_forecast, _get_seasonality_indexes,
    )

    dw = DwItem.query.get(item_code)
    if not dw:
        return jsonify({'error': 'Item not found'}), 404

    prof = SkuForecastProfile.query.get(item_code)
    res = SkuForecastResult.query.get(item_code)

    weekly_qtys_26 = _get_weekly_gross_qtys(db.session, item_code, 26)
    recent_26 = _get_recent_weekly_qtys(db.session, item_code, 26)
    profile_data = _compute_profile(weekly_qtys_26)

    ma8_val = _compute_ma8(recent_26)
    median6_val = _compute_median6(recent_26)

    last2 = recent_26[:2]
    avg_last2 = sum(last2) / len(last2) if last2 else 0.0

    today = date.today()
    current_monday = today - timedelta(days=today.weekday())
    week_labels = []
    for i in range(26):
        ws = current_monday - timedelta(weeks=(i + 1))
        week_labels.append(ws.isoformat())

    cover_days = int(Setting.get(db.session, 'forecast_default_cover_days', '7'))
    buffer_days = float(Setting.get(db.session, 'forecast_buffer_stock_days', '1'))
    review_cycle = float(Setting.get(db.session, 'forecast_review_cycle_days', '1'))

    smap = ForecastItemSupplierMap.query.filter_by(item_code_365=item_code).first()
    lead_time = float(smap.lead_time_days) if smap and smap.lead_time_days else 0.0
    if smap and smap.review_cycle_days is not None:
        review_cycle = float(smap.review_cycle_days)

    target_weeks = _float(prof.target_weeks_of_stock) if prof and prof.target_weeks_of_stock else 4.0

    latest_snap = (
        db.session.query(SkuOrderingSnapshot)
        .filter_by(item_code_365=item_code)
        .order_by(SkuOrderingSnapshot.snapshot_at.desc())
        .first()
    )

    debug = {
        'item_code': item_code,
        'item_name': dw.item_name,
        'active': dw.active,
        'supplier_code': dw.supplier_code_365,
        'brand_code': dw.brand_code_365,
        'category_code': dw.category_code_365,
        'min_order_qty': dw.min_order_qty,
        'weekly_quantities_26': {
            'labels': week_labels,
            'values': recent_26,
        },
        'classification': profile_data,
        'forecasts': {
            'MA8': round(ma8_val, 4),
            'MEDIAN6': round(median6_val, 4),
            'last_2_weeks': last2,
            'avg_last_2': round(avg_last2, 4),
        },
        'stored_profile': {
            'demand_class': prof.demand_class if prof else None,
            'forecast_method': prof.forecast_method if prof else None,
            'forecast_confidence': prof.forecast_confidence if prof else None,
            'trend_flag': prof.trend_flag if prof else None,
            'trend_pct': _float(prof.trend_pct) if prof else None,
            'seed_source': prof.seed_source if prof else None,
            'analogue_level': prof.analogue_level if prof else None,
            'review_flag': prof.review_flag if prof else None,
            'review_reason': prof.review_reason if prof else None,
            'weeks_non_zero_26': prof.weeks_non_zero_26 if prof else 0,
            'adi_26': _float(prof.adi_26) if prof else None,
            'cv2_26': _float(prof.cv2_26) if prof else None,
            'seasonality_source': prof.seasonality_source if prof else None,
            'seasonality_confidence': prof.seasonality_confidence if prof else None,
            'oos_weeks_26': getattr(prof, 'oos_weeks_26', 0) or 0 if prof else 0,
            'oos_adjusted': getattr(prof, 'oos_adjusted', False) or False if prof else False,
            'target_weeks_of_stock': target_weeks,
        } if prof else None,
        'stored_result': {
            'base_forecast_weekly_qty': _float(res.base_forecast_weekly_qty),
            'trend_adjusted_weekly_qty': _float(res.trend_adjusted_weekly_qty),
            'final_forecast_weekly_qty': _float(res.final_forecast_weekly_qty),
            'final_forecast_daily_qty': _float(res.final_forecast_daily_qty),
            'hist_embedded_seasonal_index': _float(res.hist_embedded_seasonal_index),
            'future_seasonal_index': _float(res.future_seasonal_index),
            'forecast_change_pct': _float(res.forecast_change_pct),
        } if res else None,
        'ordering_snapshot': {
            'snapshot_at': latest_snap.snapshot_at.isoformat() + 'Z' if latest_snap and latest_snap.snapshot_at else None,
            'created_by': latest_snap.created_by if latest_snap else None,
            'target_weeks_of_stock': _float(latest_snap.target_weeks_of_stock) if latest_snap else target_weeks,
            'lead_time_days': _float(latest_snap.lead_time_days) if latest_snap else lead_time,
            'review_cycle_days': _float(latest_snap.review_cycle_days) if latest_snap else review_cycle,
            'buffer_days': _float(latest_snap.buffer_days) if latest_snap else buffer_days,
            'target_stock_qty': _float(latest_snap.target_stock_qty) if latest_snap else 0,
            'on_hand_qty': _float(latest_snap.on_hand_qty) if latest_snap else 0,
            'incoming_qty': _float(latest_snap.incoming_qty) if latest_snap else 0,
            'reserved_qty': _float(latest_snap.reserved_qty) if latest_snap else 0,
            'net_available_qty': _float(latest_snap.net_available_qty) if latest_snap else 0,
            'raw_recommended_order_qty': _float(latest_snap.raw_recommended_order_qty) if latest_snap else 0,
            'rounded_order_qty': _float(latest_snap.rounded_order_qty) if latest_snap else 0,
            'explanation': latest_snap.explanation_json if latest_snap else None,
        },
        'ordering_params': {
            'target_weeks_of_stock': target_weeks,
            'lead_time_days': lead_time,
            'review_cycle_days': review_cycle,
            'buffer_days': buffer_days,
        },
        'formulas': {
            'daily_forecast': 'final_forecast_weekly_qty / 7',
            'base_target_stock': f'weekly_forecast × {target_weeks} target weeks',
            'lead_time_cover': f'daily_forecast × {lead_time} LT days',
            'review_cycle_cover': f'daily_forecast × {review_cycle} RC days',
            'buffer_stock': f'daily_forecast × {buffer_days} buffer days',
            'target_stock': 'base_target + lead_time_cover + review_cycle_cover + buffer',
            'net_available': 'on_hand + incoming - reserved',
            'raw_order': 'max(0, target_stock - net_available)',
            'rounded_order': 'ceil to integer, enforce MOQ',
        },
    }

    return jsonify(debug)
