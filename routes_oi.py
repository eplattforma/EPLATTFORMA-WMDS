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
import threading
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
    WmsDynamicRule, ActivityLog
)

logger = logging.getLogger(__name__)


def _get_running_classification():
    """Check if a classification job is currently running by looking at database."""
    running = WmsClassificationRun.query.filter(
        WmsClassificationRun.started_at.isnot(None),
        WmsClassificationRun.finished_at.is_(None)
    ).first()
    return running


def _get_last_completed_classification():
    """Get the last completed classification run."""
    return WmsClassificationRun.query.filter(
        WmsClassificationRun.finished_at.isnot(None)
    ).order_by(WmsClassificationRun.finished_at.desc()).first()


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
    
    running_job = _get_running_classification()
    last_completed = _get_last_completed_classification()
    
    return render_template('admin/oi/dashboard.html',
                          active_count=active_count,
                          classified_count=classified_count,
                          needs_review_count=needs_review_count,
                          last_run=last_run,
                          coverage_stats=coverage_stats,
                          ambiguous_categories=ambiguous_categories,
                          category_names=category_names,
                          recent_overrides=recent_overrides,
                          override_items=override_items,
                          running_job=running_job,
                          last_completed=last_completed)


def _run_classification_background(username, summer_mode):
    """Run classification in background thread."""
    try:
        from classification.engine import reclassify_items
        
        with app.app_context():
            result = reclassify_items(
                run_by=username,
                threshold=60,
                summer_mode=summer_mode
            )
            
            activity = ActivityLog()
            activity.picker_username = username
            activity.activity_type = 'oi_reclassify'
            activity.details = f"Reclassified {result['items_scanned']} items, {result['items_updated']} updated, {result['items_needing_review']} need review"
            db.session.add(activity)
            db.session.commit()
            
            logger.info(f"Background classification complete: {result}")
            
    except Exception as e:
        logger.error(f"Background reclassification failed: {e}")


@app.route('/admin/oi/reclassify', methods=['POST'])
@login_required
@admin_required
def oi_reclassify():
    """Trigger item reclassification in background."""
    running_job = _get_running_classification()
    if running_job:
        flash('Classification is already running. Please wait for it to complete.', 'warning')
        return redirect(url_for('oi_dashboard'))
    
    summer_mode = request.form.get('summer_mode') == 'on'
    
    thread = threading.Thread(
        target=_run_classification_background,
        args=(current_user.username, summer_mode)
    )
    thread.daemon = True
    thread.start()
    
    flash('Classification started in background. Refresh the page to check status.', 'info')
    return redirect(url_for('oi_dashboard'))


@app.route('/admin/oi/reclassify/status')
@login_required
@admin_required
def oi_reclassify_status():
    """Check status of running classification job."""
    running_job = _get_running_classification()
    last_completed = _get_last_completed_classification()
    
    return jsonify({
        'running': running_job is not None,
        'started_at': running_job.started_at.isoformat() if running_job else None,
        'run_by': running_job.run_by if running_job else None,
        'last_completed': {
            'finished_at': last_completed.finished_at.isoformat() if last_completed and last_completed.finished_at else None,
            'items_scanned': last_completed.active_items_scanned if last_completed else None,
            'items_updated': last_completed.items_updated if last_completed else None,
            'items_needing_review': last_completed.items_needing_review if last_completed else None,
            'notes': last_completed.notes if last_completed else None
        } if last_completed else None
    })


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
    
    if category == '__EMPTY__':
        query = query.filter(or_(DwItem.category_code_365 == None, DwItem.category_code_365 == ''))
    elif category:
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
                              'active_only': 'true' if active_only else ''
                          })


@app.route('/admin/oi/item/<item_code_365>')
@login_required
@admin_required
def oi_item_detail(item_code_365):
    """View detailed classification for a single item."""
    item = DwItem.query.get_or_404(item_code_365)
    
    # Capture return URL for filtered list navigation
    return_url = request.args.get('return_url', '')
    
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
                          evidence=evidence,
                          return_url=return_url)


@app.route('/admin/oi/item/<item_code_365>/override', methods=['POST'])
@login_required
@admin_required
def oi_item_override(item_code_365):
    """Create or update SKU override."""
    item = DwItem.query.get_or_404(item_code_365)
    
    # Capture return URL for redirect after save
    return_url = request.form.get('return_url', '')
    
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
    
    if request.form.get('pack_mode_override'):
        override.pack_mode_override = request.form.get('pack_mode_override')
    else:
        override.pack_mode_override = None
    
    if request.form.get('pick_difficulty_override'):
        try:
            override.pick_difficulty_override = int(request.form.get('pick_difficulty_override'))
        except (ValueError, TypeError):
            override.pick_difficulty_override = None
    else:
        override.pick_difficulty_override = None
    
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
    
    # Redirect back to item detail preserving return_url
    if return_url:
        return redirect(url_for('oi_item_detail', item_code_365=item_code_365, return_url=return_url))
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


@app.route('/admin/oi/categories/bulk', methods=['POST'])
@login_required
@admin_required
def oi_category_defaults_bulk():
    """Update all category defaults at once."""
    category_codes = request.form.getlist('category_codes')
    updated_count = 0
    
    for code in category_codes:
        fragility = request.form.get(f'fragility_{code}') or None
        spill_val = request.form.get(f'spill_{code}')
        pressure = request.form.get(f'pressure_{code}') or None
        temp = request.form.get(f'temp_{code}') or None
        boxfit = request.form.get(f'boxfit_{code}') or None
        zone = request.form.get(f'zone_{code}') or None
        packmode = request.form.get(f'packmode_{code}') or None
        
        # Convert spill to boolean
        if spill_val == 'true':
            spill = True
        elif spill_val == 'false':
            spill = False
        else:
            spill = None
        
        # Check if anything is set
        has_values = any([fragility, spill is not None, pressure, temp, boxfit, zone, packmode])
        
        cat_default = WmsCategoryDefault.query.get(code)
        
        if has_values:
            if not cat_default:
                cat_default = WmsCategoryDefault(category_code_365=code)
                db.session.add(cat_default)
            
            cat_default.default_fragility = fragility
            cat_default.default_spill_risk = spill
            cat_default.default_pressure_sensitivity = pressure
            cat_default.default_temperature_sensitivity = temp
            cat_default.default_box_fit_rule = boxfit
            cat_default.default_zone = zone
            cat_default.default_pack_mode = packmode
            cat_default.updated_by = current_user.username
            cat_default.updated_at = datetime.utcnow()
            cat_default.is_active = True
            updated_count += 1
        elif cat_default:
            # Clear existing defaults if all fields are empty
            cat_default.default_fragility = None
            cat_default.default_spill_risk = None
            cat_default.default_pressure_sensitivity = None
            cat_default.default_temperature_sensitivity = None
            cat_default.default_box_fit_rule = None
            cat_default.default_zone = None
            cat_default.default_pack_mode = None
            cat_default.updated_by = current_user.username
            cat_default.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    activity = ActivityLog()
    activity.picker_username = current_user.username
    activity.activity_type = 'oi_category_defaults_bulk'
    activity.details = f"Bulk updated category defaults ({updated_count} categories with values)"
    db.session.add(activity)
    db.session.commit()
    
    flash('Category defaults saved. Run reclassification to apply changes.', 'success')
    return redirect(url_for('oi_categories', saved='1'))


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


# =============================================================================
# Dynamic Rules Management
# =============================================================================

@app.route('/admin/oi/rules')
@login_required
@admin_required
def oi_rules():
    """List all dynamic rules with filtering."""
    from classification.dynamic_rules import summarize_conditions, TARGET_ATTRS
    
    target_filter = request.args.get('target', '')
    active_filter = request.args.get('active', '')
    search = request.args.get('search', '').strip()
    
    query = WmsDynamicRule.query
    
    if target_filter:
        query = query.filter(WmsDynamicRule.target_attr == target_filter)
    
    if active_filter == 'true':
        query = query.filter(WmsDynamicRule.is_active == True)
    elif active_filter == 'false':
        query = query.filter(WmsDynamicRule.is_active == False)
    
    if search:
        query = query.filter(or_(
            WmsDynamicRule.name.ilike(f'%{search}%'),
            WmsDynamicRule.notes.ilike(f'%{search}%')
        ))
    
    rules = query.order_by(
        WmsDynamicRule.target_attr,
        WmsDynamicRule.priority.desc()
    ).all()
    
    # Add condition summary to each rule
    for rule in rules:
        rule.condition_summary = summarize_conditions(rule.condition_json)
    
    return render_template('admin/oi/rules.html',
                          rules=rules,
                          target_attrs=list(TARGET_ATTRS.keys()),
                          filters={
                              'target': target_filter,
                              'active': active_filter,
                              'search': search
                          })


@app.route('/admin/oi/rules/new', methods=['GET', 'POST'])
@login_required
@admin_required
def oi_rule_new():
    """Create a new dynamic rule."""
    from classification.dynamic_rules import (
        ALLOWED_FIELDS, OPERATORS_BY_TYPE, TARGET_ATTRS,
        validate_rule_condition, validate_target_attr
    )
    
    if request.method == 'POST':
        # Parse form data
        name = request.form.get('name', '').strip()
        target_attr = request.form.get('target_attr', '')
        action_value = request.form.get('action_value', '')
        confidence = request.form.get('confidence', 65, type=int)
        priority = request.form.get('priority', 100, type=int)
        notes = request.form.get('notes', '').strip()
        
        # Parse conditions from repeating form fields
        fields = request.form.getlist('cond_field')
        ops = request.form.getlist('cond_op')
        vals = request.form.getlist('cond_value')
        
        conditions = []
        errors = []
        
        for i, (f, o, v) in enumerate(zip(fields, ops, vals)):
            if not f or not o:
                continue
            
            # Parse value - handle "in" operator with comma-separated values
            if o in ('in', 'not_in') and v:
                parsed_value = [x.strip() for x in v.split(',') if x.strip()]
            else:
                parsed_value = v.strip() if v else ''
            
            cond = {'field': f, 'op': o, 'value': parsed_value}
            
            is_valid, err = validate_rule_condition(cond)
            if not is_valid:
                errors.append(f"Condition {i+1}: {err}")
            else:
                conditions.append(cond)
        
        # Validate target and action
        if not name:
            errors.append("Rule name is required")
        
        is_valid, err = validate_target_attr(target_attr, action_value)
        if not is_valid:
            errors.append(err)
        
        if confidence < 0 or confidence > 100:
            errors.append("Confidence must be 0-100")
        
        if not conditions:
            errors.append("At least one condition is required")
        
        if errors:
            for err in errors:
                flash(err, 'danger')
            return render_template('admin/oi/rule_form.html',
                                  rule=None,
                                  allowed_fields=ALLOWED_FIELDS,
                                  operators_by_type=OPERATORS_BY_TYPE,
                                  target_attrs=TARGET_ATTRS,
                                  form_data=request.form)
        
        # Create rule
        condition_json = json.dumps({'all': conditions})
        
        rule = WmsDynamicRule(
            name=name,
            target_attr=target_attr,
            action_value=action_value,
            confidence=confidence,
            priority=priority,
            condition_json=condition_json,
            notes=notes,
            updated_by=current_user.username,
            updated_at=datetime.utcnow()
        )
        db.session.add(rule)
        db.session.commit()
        
        activity = ActivityLog()
        activity.picker_username = current_user.username
        activity.activity_type = 'oi_rule_create'
        activity.details = f"Created dynamic rule: {name} ({target_attr}={action_value})"
        db.session.add(activity)
        db.session.commit()
        
        flash(f'Rule "{name}" created. Run reclassification to apply.', 'success')
        return redirect(url_for('oi_rules'))
    
    return render_template('admin/oi/rule_form.html',
                          rule=None,
                          allowed_fields=ALLOWED_FIELDS,
                          operators_by_type=OPERATORS_BY_TYPE,
                          target_attrs=TARGET_ATTRS,
                          form_data=None)


@app.route('/admin/oi/rules/<int:rule_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def oi_rule_edit(rule_id):
    """Edit an existing dynamic rule."""
    from classification.dynamic_rules import (
        ALLOWED_FIELDS, OPERATORS_BY_TYPE, TARGET_ATTRS,
        validate_rule_condition, validate_target_attr
    )
    
    rule = WmsDynamicRule.query.get_or_404(rule_id)
    
    if request.method == 'POST':
        # Parse form data
        name = request.form.get('name', '').strip()
        target_attr = request.form.get('target_attr', '')
        action_value = request.form.get('action_value', '')
        confidence = request.form.get('confidence', 65, type=int)
        priority = request.form.get('priority', 100, type=int)
        notes = request.form.get('notes', '').strip()
        
        # Parse conditions
        fields = request.form.getlist('cond_field')
        ops = request.form.getlist('cond_op')
        vals = request.form.getlist('cond_value')
        
        conditions = []
        errors = []
        
        for i, (f, o, v) in enumerate(zip(fields, ops, vals)):
            if not f or not o:
                continue
            
            if o in ('in', 'not_in') and v:
                parsed_value = [x.strip() for x in v.split(',') if x.strip()]
            else:
                parsed_value = v.strip() if v else ''
            
            cond = {'field': f, 'op': o, 'value': parsed_value}
            
            is_valid, err = validate_rule_condition(cond)
            if not is_valid:
                errors.append(f"Condition {i+1}: {err}")
            else:
                conditions.append(cond)
        
        if not name:
            errors.append("Rule name is required")
        
        is_valid, err = validate_target_attr(target_attr, action_value)
        if not is_valid:
            errors.append(err)
        
        if confidence < 0 or confidence > 100:
            errors.append("Confidence must be 0-100")
        
        if not conditions:
            errors.append("At least one condition is required")
        
        if errors:
            for err in errors:
                flash(err, 'danger')
            return render_template('admin/oi/rule_form.html',
                                  rule=rule,
                                  allowed_fields=ALLOWED_FIELDS,
                                  operators_by_type=OPERATORS_BY_TYPE,
                                  target_attrs=TARGET_ATTRS,
                                  form_data=request.form)
        
        # Update rule
        rule.name = name
        rule.target_attr = target_attr
        rule.action_value = action_value
        rule.confidence = confidence
        rule.priority = priority
        rule.condition_json = json.dumps({'all': conditions})
        rule.notes = notes
        rule.updated_by = current_user.username
        rule.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        activity = ActivityLog()
        activity.picker_username = current_user.username
        activity.activity_type = 'oi_rule_edit'
        activity.details = f"Updated dynamic rule: {name} (id={rule_id})"
        db.session.add(activity)
        db.session.commit()
        
        flash(f'Rule "{name}" updated. Run reclassification to apply.', 'success')
        return redirect(url_for('oi_rules'))
    
    # Parse existing conditions for form
    existing_conditions = []
    try:
        cond_data = json.loads(rule.condition_json)
        for cond in cond_data.get('all', []):
            val = cond.get('value', '')
            if isinstance(val, list):
                val = ', '.join(str(v) for v in val)
            existing_conditions.append({
                'field': cond.get('field', ''),
                'op': cond.get('op', ''),
                'value': val
            })
    except:
        pass
    
    return render_template('admin/oi/rule_form.html',
                          rule=rule,
                          existing_conditions=existing_conditions,
                          allowed_fields=ALLOWED_FIELDS,
                          operators_by_type=OPERATORS_BY_TYPE,
                          target_attrs=TARGET_ATTRS,
                          form_data=None)


@app.route('/admin/oi/rules/<int:rule_id>/toggle', methods=['POST'])
@login_required
@admin_required
def oi_rule_toggle(rule_id):
    """Enable or disable a dynamic rule."""
    rule = WmsDynamicRule.query.get_or_404(rule_id)
    
    rule.is_active = not rule.is_active
    rule.updated_by = current_user.username
    rule.updated_at = datetime.utcnow()
    db.session.commit()
    
    status = 'enabled' if rule.is_active else 'disabled'
    flash(f'Rule "{rule.name}" {status}. Run reclassification to apply.', 'success')
    return redirect(url_for('oi_rules'))


@app.route('/admin/oi/rules/test', methods=['GET', 'POST'])
@login_required
@admin_required
def oi_rules_test():
    """Test which rules would match a given item."""
    from classification.dynamic_rules import (
        load_active_rules, test_rule_against_item, ALLOWED_FIELDS
    )
    
    item = None
    test_results = []
    
    if request.method == 'POST' or request.args.get('item_code'):
        item_code = request.form.get('item_code', '') or request.args.get('item_code', '')
        item_code = item_code.strip()
        
        if item_code:
            item = DwItem.query.get(item_code)
            if not item:
                flash(f'Item "{item_code}" not found.', 'warning')
            else:
                # Test all active rules against this item
                all_rules = WmsDynamicRule.query.filter_by(is_active=True).order_by(
                    WmsDynamicRule.target_attr,
                    WmsDynamicRule.priority.desc()
                ).all()
                
                for rule in all_rules:
                    result = test_rule_against_item(item, rule)
                    test_results.append(result)
    
    # Get field values for the item
    item_fields = {}
    if item:
        for field in ALLOWED_FIELDS.keys():
            item_fields[field] = getattr(item, field, None)
    
    # Get all rules for the dropdown
    all_rules = WmsDynamicRule.query.filter_by(is_active=True).order_by(
        WmsDynamicRule.priority.desc()
    ).all()
    
    return render_template('admin/oi/rules_test.html',
                          item=item,
                          item_fields=item_fields,
                          test_results=test_results,
                          allowed_fields=list(ALLOWED_FIELDS.keys()),
                          all_rules=all_rules)


@app.route('/admin/oi/rules/<int:rule_id>/matches')
@login_required
@admin_required
def oi_rule_matches(rule_id):
    """Show all items that match a specific rule."""
    from classification.dynamic_rules import evaluate_rule_conditions
    import logging
    
    try:
        rule = WmsDynamicRule.query.get_or_404(rule_id)
        
        # Get all active items
        items = DwItem.query.filter(DwItem.active == True).all()
        
        # Find all items that match this rule
        matching_items = []
        
        # Build target attribute name
        target_attr_name = f"wms_{rule.target_attr}" if rule.target_attr else None
        
        for item in items:
            try:
                if evaluate_rule_conditions(item, rule.condition_json):
                    # Get current value of the target attribute for display
                    current_value = getattr(item, target_attr_name, None) if target_attr_name else None
                    matching_items.append({
                        'item': item,
                        'current_value': current_value
                    })
            except Exception as e:
                logging.debug(f"Rule evaluation error for item {item.item_code_365}: {e}")
                continue
        
        return render_template('admin/oi/rule_matches.html',
                              rule=rule,
                              matching_items=matching_items,
                              total_items=len(items))
    except Exception as e:
        logging.error(f"Error in oi_rule_matches: {e}", exc_info=True)
        raise


@app.route('/admin/oi/rules/preview-matches', methods=['POST'])
@login_required
@admin_required
def oi_rules_preview_matches():
    """API endpoint to preview items matching conditions (AJAX)."""
    from classification.dynamic_rules import evaluate_rule_conditions
    
    try:
        data = request.get_json()
        conditions_list = data.get('conditions', [])
        
        if not conditions_list:
            return jsonify({'count': 0, 'items': []})
        
        # Parse conditions - handle comma-separated values for 'in' operator
        parsed_conditions = []
        for c in conditions_list:
            op = c.get('op', '')
            val = c.get('value', '')
            if op in ('in', 'not_in') and isinstance(val, str):
                val = [x.strip() for x in val.split(',') if x.strip()]
            parsed_conditions.append({
                'field': c.get('field', ''),
                'op': op,
                'value': val
            })
        
        # Convert to JSON string for evaluate_rule_conditions
        condition_json_str = json.dumps({'all': parsed_conditions})
        
        # Get all active items
        items = DwItem.query.filter(DwItem.active == True).all()
        
        # Find matching items
        matching = []
        for item in items:
            try:
                if evaluate_rule_conditions(item, condition_json_str):
                    matching.append({
                        'code': item.item_code_365,
                        'name': item.item_name or ''
                    })
            except:
                continue
        
        # Return first 100 items for preview
        return jsonify({
            'count': len(matching),
            'items': matching[:100]
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400


