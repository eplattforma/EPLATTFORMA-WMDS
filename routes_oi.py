"""
Operational Intelligence Admin Routes

Provides server-rendered admin pages for managing item classification:
- Dashboard with KPIs and reclassification trigger
- Items list with filtering and override capability
- Category defaults management
- SKU overrides management
- Classification run history
"""

import json
import logging
from datetime import datetime
from functools import wraps

from flask import (
    render_template, request, redirect, url_for, flash, 
    current_app, jsonify
)
from flask_login import login_required, current_user
from sqlalchemy import func, or_, text

from app import app, db
from models import (
    DwItem, DwItemCategory, DwBrand, 
    WmsCategoryDefault, WmsItemOverride, WmsClassificationRun,
    ActivityLog
)

logger = logging.getLogger(__name__)


def admin_required(f):
    """Decorator to require admin role for OI routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Please log in to access this page.', 'warning')
            return redirect(url_for('login'))
        if current_user.role not in ('admin', 'warehouse_manager'):
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/admin/oi/dashboard')
@login_required
@admin_required
def oi_dashboard():
    """Operational Intelligence Dashboard with KPIs and actions."""
    from models import Setting
    import json
    
    active_count = DwItem.query.filter(DwItem.active == True).count()
    
    needs_review_count = db.session.query(func.count(DwItem.item_code_365)).filter(
        DwItem.active == True,
        or_(
            DwItem.wms_fragility == None,
            DwItem.wms_spill_risk == None,
            DwItem.wms_pressure_sensitivity == None,
            DwItem.wms_temperature_sensitivity == None,
            DwItem.wms_box_fit_rule == None,
            DwItem.wms_class_confidence < 60
        )
    ).scalar() or 0
    
    classified_count = db.session.query(func.count(DwItem.item_code_365)).filter(
        DwItem.active == True,
        DwItem.wms_classified_at != None
    ).scalar() or 0
    
    last_run = WmsClassificationRun.query.order_by(
        WmsClassificationRun.started_at.desc()
    ).first()
    
    critical_attrs = ['wms_fragility', 'wms_spill_risk', 'wms_pressure_sensitivity',
                      'wms_temperature_sensitivity', 'wms_box_fit_rule']
    coverage_stats = {}
    for attr in critical_attrs:
        non_null = db.session.query(func.count(DwItem.item_code_365)).filter(
            DwItem.active == True,
            getattr(DwItem, attr) != None
        ).scalar() or 0
        coverage_stats[attr.replace('wms_', '')] = round(
            (non_null / active_count * 100) if active_count > 0 else 0, 1
        )
    
    ambiguous_categories = db.session.query(
        DwItem.category_code_365,
        func.count(DwItem.item_code_365).label('total'),
        func.sum(
            func.cast(
                or_(
                    DwItem.wms_fragility == None,
                    DwItem.wms_class_confidence < 60
                ), db.Integer
            )
        ).label('needs_review')
    ).filter(
        DwItem.active == True,
        DwItem.category_code_365 != None
    ).group_by(DwItem.category_code_365).order_by(
        text('needs_review DESC')
    ).limit(10).all()
    
    category_names = {
        c.category_code_365: c.category_name 
        for c in DwItemCategory.query.all()
    }
    
    recent_overrides = WmsItemOverride.query.order_by(
        WmsItemOverride.updated_at.desc()
    ).limit(10).all()
    
    override_items = {}
    if recent_overrides:
        item_codes = [o.item_code_365 for o in recent_overrides]
        items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        override_items = {i.item_code_365: i for i in items}
    
    return render_template('admin/oi/dashboard.html',
                          active_count=active_count,
                          classified_count=classified_count,
                          needs_review_count=needs_review_count,
                          last_run=last_run,
                          coverage_stats=coverage_stats,
                          ambiguous_categories=ambiguous_categories,
                          category_names=category_names,
                          recent_overrides=recent_overrides,
                          override_items=override_items)


@app.route('/admin/oi/reclassify', methods=['POST'])
@login_required
@admin_required
def oi_reclassify():
    """Trigger item reclassification."""
    try:
        from classification.engine import reclassify_items
        
        summer_mode = request.form.get('summer_mode') == 'on'
        
        result = reclassify_items(
            run_by=current_user.username,
            threshold=60,
            summer_mode=summer_mode
        )
        
        activity = ActivityLog()
        activity.picker_username = current_user.username
        activity.activity_type = 'oi_reclassify'
        activity.details = f"Reclassified {result['items_scanned']} items, {result['items_updated']} updated, {result['items_needing_review']} need review"
        db.session.add(activity)
        db.session.commit()
        
        flash(f"Classification complete: {result['items_scanned']} items scanned, "
              f"{result['items_updated']} updated, {result['items_needing_review']} need review.", 
              'success')
        
    except Exception as e:
        logger.error(f"Reclassification failed: {e}")
        flash(f'Classification failed: {str(e)}', 'danger')
    
    return redirect(url_for('oi_dashboard'))


@app.route('/admin/oi/items')
@login_required
@admin_required
def oi_items():
    """List items with classification data and filtering."""
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    search = request.args.get('search', '').strip()
    category = request.args.get('category', '')
    brand = request.args.get('brand', '')
    zone = request.args.get('zone', '')
    fragility = request.args.get('fragility', '')
    needs_review = request.args.get('needs_review', '')
    active_only = request.args.get('active_only', 'true') == 'true'
    
    query = DwItem.query
    
    if active_only:
        query = query.filter(DwItem.active == True)
    
    if search:
        query = query.filter(or_(
            DwItem.item_code_365.ilike(f'%{search}%'),
            DwItem.item_name.ilike(f'%{search}%')
        ))
    
    if category:
        query = query.filter(DwItem.category_code_365 == category)
    
    if brand:
        query = query.filter(DwItem.brand_code_365 == brand)
    
    if zone:
        query = query.filter(DwItem.wms_zone == zone)
    
    if fragility:
        if fragility == 'NULL':
            query = query.filter(DwItem.wms_fragility == None)
        else:
            query = query.filter(DwItem.wms_fragility == fragility)
    
    if needs_review == 'true':
        query = query.filter(or_(
            DwItem.wms_fragility == None,
            DwItem.wms_spill_risk == None,
            DwItem.wms_pressure_sensitivity == None,
            DwItem.wms_temperature_sensitivity == None,
            DwItem.wms_box_fit_rule == None,
            DwItem.wms_class_confidence < 60
        ))
    
    query = query.order_by(DwItem.item_code_365)
    
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    items = pagination.items
    
    categories = DwItemCategory.query.order_by(DwItemCategory.category_name).all()
    brands = DwBrand.query.order_by(DwBrand.brand_name).all()
    
    zones = db.session.query(DwItem.wms_zone).filter(
        DwItem.wms_zone != None
    ).distinct().all()
    zones = [z[0] for z in zones if z[0]]
    
    return render_template('admin/oi/items.html',
                          items=items,
                          pagination=pagination,
                          categories=categories,
                          brands=brands,
                          zones=zones,
                          filters={
                              'search': search,
                              'category': category,
                              'brand': brand,
                              'zone': zone,
                              'fragility': fragility,
                              'needs_review': needs_review,
                              'active_only': active_only
                          })


@app.route('/admin/oi/item/<item_code_365>')
@login_required
@admin_required
def oi_item_detail(item_code_365):
    """View detailed classification for a single item."""
    item = DwItem.query.get_or_404(item_code_365)
    
    category = None
    if item.category_code_365:
        category = DwItemCategory.query.get(item.category_code_365)
    
    brand = None
    if item.brand_code_365:
        brand = DwBrand.query.get(item.brand_code_365)
    
    override = WmsItemOverride.query.get(item_code_365)
    
    category_default = None
    if item.category_code_365:
        category_default = WmsCategoryDefault.query.get(item.category_code_365)
    
    evidence = {}
    if item.wms_class_evidence:
        try:
            evidence = json.loads(item.wms_class_evidence)
        except:
            pass
    
    return render_template('admin/oi/item_detail.html',
                          item=item,
                          category=category,
                          brand=brand,
                          override=override,
                          category_default=category_default,
                          evidence=evidence)


@app.route('/admin/oi/item/<item_code_365>/override', methods=['POST'])
@login_required
@admin_required
def oi_item_override(item_code_365):
    """Create or update SKU override."""
    item = DwItem.query.get_or_404(item_code_365)
    
    override = WmsItemOverride.query.get(item_code_365)
    if not override:
        override = WmsItemOverride(item_code_365=item_code_365)
        db.session.add(override)
    
    if request.form.get('zone_override'):
        override.zone_override = request.form.get('zone_override')
    else:
        override.zone_override = None
        
    if request.form.get('fragility_override'):
        override.fragility_override = request.form.get('fragility_override')
    else:
        override.fragility_override = None
        
    if request.form.get('stackability_override'):
        override.stackability_override = request.form.get('stackability_override')
    else:
        override.stackability_override = None
        
    if request.form.get('temperature_sensitivity_override'):
        override.temperature_sensitivity_override = request.form.get('temperature_sensitivity_override')
    else:
        override.temperature_sensitivity_override = None
        
    if request.form.get('pressure_sensitivity_override'):
        override.pressure_sensitivity_override = request.form.get('pressure_sensitivity_override')
    else:
        override.pressure_sensitivity_override = None
        
    if request.form.get('spill_risk_override'):
        override.spill_risk_override = request.form.get('spill_risk_override') == 'true'
    else:
        override.spill_risk_override = None
        
    if request.form.get('box_fit_rule_override'):
        override.box_fit_rule_override = request.form.get('box_fit_rule_override')
    else:
        override.box_fit_rule_override = None
    
    override.override_reason = request.form.get('override_reason', '')
    override.updated_by = current_user.username
    override.updated_at = datetime.utcnow()
    override.is_active = True
    
    db.session.commit()
    
    activity = ActivityLog()
    activity.picker_username = current_user.username
    activity.activity_type = 'oi_item_override'
    activity.item_code = item_code_365
    activity.details = f"Updated override for {item_code_365}: {override.override_reason}"
    db.session.add(activity)
    db.session.commit()
    
    flash(f'Override saved for {item_code_365}. Run reclassification to apply.', 'success')
    return redirect(url_for('oi_item_detail', item_code_365=item_code_365))


@app.route('/admin/oi/categories')
@login_required
@admin_required
def oi_categories():
    """Manage category defaults."""
    categories = db.session.query(
        DwItemCategory,
        func.count(DwItem.item_code_365).label('sku_count'),
        func.sum(
            func.cast(
                or_(
                    DwItem.wms_fragility == None,
                    DwItem.wms_class_confidence < 60
                ), db.Integer
            )
        ).label('needs_review')
    ).outerjoin(
        DwItem, DwItem.category_code_365 == DwItemCategory.category_code_365
    ).filter(
        or_(DwItem.active == True, DwItem.active == None)
    ).group_by(DwItemCategory.category_code_365).order_by(
        DwItemCategory.category_name
    ).all()
    
    defaults = {
        d.category_code_365: d 
        for d in WmsCategoryDefault.query.all()
    }
    
    return render_template('admin/oi/categories.html',
                          categories=categories,
                          defaults=defaults)


@app.route('/admin/oi/category/<category_code_365>/defaults', methods=['POST'])
@login_required
@admin_required
def oi_category_defaults(category_code_365):
    """Update category defaults."""
    cat_default = WmsCategoryDefault.query.get(category_code_365)
    if not cat_default:
        cat_default = WmsCategoryDefault(category_code_365=category_code_365)
        db.session.add(cat_default)
    
    cat_default.default_zone = request.form.get('default_zone') or None
    cat_default.default_fragility = request.form.get('default_fragility') or None
    cat_default.default_stackability = request.form.get('default_stackability') or None
    cat_default.default_temperature_sensitivity = request.form.get('default_temperature_sensitivity') or None
    cat_default.default_pressure_sensitivity = request.form.get('default_pressure_sensitivity') or None
    cat_default.default_shape_type = request.form.get('default_shape_type') or None
    cat_default.default_box_fit_rule = request.form.get('default_box_fit_rule') or None
    
    spill = request.form.get('default_spill_risk')
    if spill == 'true':
        cat_default.default_spill_risk = True
    elif spill == 'false':
        cat_default.default_spill_risk = False
    else:
        cat_default.default_spill_risk = None
    
    cat_default.notes = request.form.get('notes', '')
    cat_default.updated_by = current_user.username
    cat_default.updated_at = datetime.utcnow()
    cat_default.is_active = True
    
    db.session.commit()
    
    activity = ActivityLog()
    activity.picker_username = current_user.username
    activity.activity_type = 'oi_category_default'
    activity.details = f"Updated defaults for category {category_code_365}"
    db.session.add(activity)
    db.session.commit()
    
    flash(f'Defaults saved for category {category_code_365}. Run reclassification to apply.', 'success')
    return redirect(url_for('oi_categories'))


@app.route('/admin/oi/overrides')
@login_required
@admin_required
def oi_overrides():
    """List all SKU overrides."""
    search = request.args.get('search', '').strip()
    
    query = WmsItemOverride.query
    
    if search:
        item_codes = [i.item_code_365 for i in DwItem.query.filter(or_(
            DwItem.item_code_365.ilike(f'%{search}%'),
            DwItem.item_name.ilike(f'%{search}%')
        )).all()]
        query = query.filter(WmsItemOverride.item_code_365.in_(item_codes))
    
    overrides = query.order_by(WmsItemOverride.updated_at.desc()).all()
    
    item_codes = [o.item_code_365 for o in overrides]
    items = {i.item_code_365: i for i in DwItem.query.filter(
        DwItem.item_code_365.in_(item_codes)
    ).all()}
    
    return render_template('admin/oi/overrides.html',
                          overrides=overrides,
                          items=items,
                          search=search)


@app.route('/admin/oi/override/<item_code_365>/disable', methods=['POST'])
@login_required
@admin_required
def oi_override_disable(item_code_365):
    """Disable a SKU override."""
    override = WmsItemOverride.query.get_or_404(item_code_365)
    override.is_active = False
    override.updated_by = current_user.username
    override.updated_at = datetime.utcnow()
    db.session.commit()
    
    flash(f'Override disabled for {item_code_365}. Run reclassification to apply.', 'success')
    return redirect(url_for('oi_overrides'))


@app.route('/admin/oi/runs')
@login_required
@admin_required
def oi_runs():
    """View classification run history."""
    runs = WmsClassificationRun.query.order_by(
        WmsClassificationRun.started_at.desc()
    ).limit(50).all()
    
    return render_template('admin/oi/runs.html', runs=runs)


@app.route('/admin/oi/manual')
@login_required
@admin_required
def oi_manual():
    """Display Operational Intelligence manual and help."""
    return render_template('admin/oi/manual.html')


