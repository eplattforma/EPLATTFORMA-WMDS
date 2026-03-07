"""
Replenishment MVP Blueprint

URL prefix: /replenishment-mvp

V1 limitations documented:
- Uses current reserved stock only (not future reserved by delivery date)
- Uses current ordered stock only
- Historical weekday sales averages only (no seasonality)
- No expiry-based ordering math
- No auto PO creation or approval workflow
"""
import csv
import io
import logging
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, Response
)
from flask_login import login_required, current_user

from app import db
from models import ReplenishmentRun, ReplenishmentRunLine

logger = logging.getLogger(__name__)

replenishment_bp = Blueprint(
    "replenishment_mvp", __name__,
    url_prefix="/replenishment-mvp",
    template_folder="../templates"
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


@replenishment_bp.route('/')
@admin_or_warehouse_required
def index():
    from services.replenishment_mvp.repositories import get_active_suppliers
    suppliers = get_active_suppliers()

    recent_runs = ReplenishmentRun.query.order_by(
        ReplenishmentRun.created_at.desc()
    ).limit(20).all()

    today = date.today()
    wd = today.weekday()
    if wd == 1:
        default_type = 'tuesday'
    elif wd == 4:
        default_type = 'friday'
    else:
        default_type = 'tuesday'

    return render_template(
        'replenishment_mvp/index.html',
        suppliers=suppliers,
        recent_runs=recent_runs,
        today=today.isoformat(),
        default_type=default_type,
    )


@replenishment_bp.route('/generate', methods=['POST'])
@admin_or_warehouse_required
def generate():
    from services.replenishment_mvp.planner import generate_replenishment_run

    supplier_code = request.form.get('supplier_code', '').strip()
    run_date_str = request.form.get('run_date', '').strip()
    run_type = request.form.get('run_type', '').strip()
    include_today = request.form.get('include_today_demand') == 'on'

    if not supplier_code or not run_date_str or run_type not in ('tuesday', 'friday'):
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('replenishment_mvp.index'))

    try:
        run_date = datetime.strptime(run_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format.', 'error')
        return redirect(url_for('replenishment_mvp.index'))

    today = date.today()
    if run_date > today:
        flash('Run date cannot be in the future (no sales data exists yet).', 'error')
        return redirect(url_for('replenishment_mvp.index'))
    if (today - run_date).days > 7:
        flash('Run date is more than 7 days in the past. Stock snapshot may be stale.', 'warning')

    try:
        run_id = generate_replenishment_run(
            supplier_code=supplier_code,
            run_date=run_date,
            run_type=run_type,
            include_today_demand=include_today,
            current_user=current_user,
        )
        flash(f'Replenishment run #{run_id} generated successfully.', 'success')
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))
    except Exception as e:
        logger.exception(f"Replenishment generation failed: {e}")
        flash(f'Error generating proposal: {str(e)}', 'error')
        return redirect(url_for('replenishment_mvp.index'))


@replenishment_bp.route('/run/<int:run_id>')
@admin_or_warehouse_required
def run_detail(run_id):
    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).order_by(
        ReplenishmentRunLine.suggested_cases.desc()
    ).all()
    return render_template(
        'replenishment_mvp/run_detail.html',
        run=run,
        lines=lines,
    )


@replenishment_bp.route('/run/<int:run_id>/save', methods=['POST'])
@admin_or_warehouse_required
def save_finals(run_id):
    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()

    for line in lines:
        key = f"final_cases_{line.id}"
        val = request.form.get(key, '').strip()
        if val:
            try:
                fc = Decimal(val)
                line.final_cases = fc
                line.final_units = fc * line.case_qty_units
            except Exception:
                pass

    run.status = 'saved'
    db.session.commit()
    flash('Final quantities saved.', 'success')
    return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))


@replenishment_bp.route('/run/<int:run_id>/export-csv')
@admin_or_warehouse_required
def export_csv(run_id):
    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).order_by(
        ReplenishmentRunLine.suggested_cases.desc()
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Item Code', 'Item Name', 'Case Qty', 'Stock Now', 'Reserved Now',
        'Ordered Now', 'On Transfer', 'Available Base', 'Pre-Receipt Forecast',
        'Projected At Receipt', 'Cover Forecast', 'Safety Stock',
        'Suggested Cases', 'Suggested Units', 'Final Cases', 'Final Units',
        'Earliest Expiry', 'Expiry Qty', 'Warning', 'Explanation'
    ])

    for line in lines:
        writer.writerow([
            line.item_code_365, line.item_name,
            float(line.case_qty_units), float(line.stock_now_units),
            float(line.reserved_now_units), float(line.ordered_now_units),
            float(line.on_transfer_now_units), float(line.available_base_units),
            float(line.pre_receipt_forecast_units), float(line.projected_units_at_receipt),
            float(line.cover_forecast_units), float(line.safety_stock_units),
            float(line.suggested_cases), float(line.suggested_units),
            float(line.final_cases or 0), float(line.final_units or 0),
            str(line.earliest_expiry_date or ''), float(line.qty_at_earliest_expiry or 0),
            line.warning_code or '', line.explanation_text or '',
        ])

    filename = f"replenishment_{run.supplier_code}_{run.run_date}_{run.run_type}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@replenishment_bp.route('/api/run/<int:run_id>')
@admin_or_warehouse_required
def api_run_json(run_id):
    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()
    return jsonify({
        "run": {
            "id": run.id,
            "supplier_code": run.supplier_code,
            "supplier_name": run.supplier_name,
            "run_date": str(run.run_date),
            "run_type": run.run_type,
            "receipt_date": str(run.receipt_date),
            "status": run.status,
            "created_by": run.created_by,
            "created_at": str(run.created_at),
        },
        "lines": [{
            "item_code_365": l.item_code_365,
            "item_name": l.item_name,
            "calc_json": l.calc_json,
            "warning_code": l.warning_code,
            "suggested_cases": float(l.suggested_cases),
            "final_cases": float(l.final_cases or 0),
        } for l in lines]
    })
