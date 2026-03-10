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


@replenishment_bp.route('/run/<int:run_id>/refresh-stock', methods=['POST'])
@admin_or_warehouse_required
def refresh_stock(run_id):
    import math
    from services.replenishment_mvp.ps365_client import fetch_supplier_stock, REPLENISHMENT_WAREHOUSE_STORE
    from services.replenishment_mvp.forecast import get_forecast_for_dates, resolve_forecast_sources
    from services.replenishment_mvp.repositories import (
        get_item_master_for_codes, get_item_settings_for_codes,
        get_same_weekday_sales_averages, get_fallback_daily_averages,
        get_expiry_summary,
    )
    from services.replenishment_mvp.calendar import (
        get_receipt_date, get_pre_receipt_dates, get_cover_dates_after_receipt,
    )
    from services.replenishment_mvp.planner import _resolve_case_qty, _build_warnings

    run = ReplenishmentRun.query.get_or_404(run_id)
    if run.status == 'po_sent':
        flash('Cannot refresh stock on a run that already has a PO sent.', 'warning')
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    try:
        stock_snapshot = fetch_supplier_stock(run.supplier_code, REPLENISHMENT_WAREHOUSE_STORE)
    except Exception as e:
        logger.exception(f"Stock refresh failed for run #{run_id}")
        flash(f'Error fetching stock from PS365: {str(e)}', 'error')
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    if not stock_snapshot:
        flash('No stock data returned from PS365.', 'error')
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    receipt_date = get_receipt_date(run.run_date, run.run_type)
    pre_receipt_dates = get_pre_receipt_dates(run.run_date, run.run_type, run.include_today_demand)
    cover_dates = get_cover_dates_after_receipt(receipt_date, run.run_type)
    all_needed_weekdays = list(set(d.weekday() for d in pre_receipt_dates + cover_dates))

    item_codes = list(stock_snapshot.keys())
    item_master = get_item_master_for_codes(item_codes)
    item_settings = get_item_settings_for_codes(item_codes)
    weekday_avgs = get_same_weekday_sales_averages(item_codes, all_needed_weekdays, reference_date=run.run_date)
    fallback_avgs = get_fallback_daily_averages(item_codes, reference_date=run.run_date)
    expiry_data = get_expiry_summary(item_codes, REPLENISHMENT_WAREHOUSE_STORE)

    forecast_sources = resolve_forecast_sources(item_codes, weekday_avgs, fallback_avgs)
    pre_forecast = get_forecast_for_dates(item_codes, pre_receipt_dates, weekday_avgs, fallback_avgs)
    cover_forecast = get_forecast_for_dates(item_codes, cover_dates, weekday_avgs, fallback_avgs)

    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()
    line_map = {l.item_code_365: l for l in lines}

    updated = 0
    stale_count = 0
    for item_code in list(line_map.keys()):
        line = line_map[item_code]
        api_data = stock_snapshot.get(item_code)

        if not api_data:
            line.warning_code = "STOCK_NOT_IN_SNAPSHOT"
            line.warning_text = "Item not returned by PS365 stock query during refresh"
            stale_count += 1
            continue

        master = item_master.get(item_code, {})
        settings = item_settings.get(item_code, {})
        expiry = expiry_data.get(item_code, {})
        fb_info = fallback_avgs.get(item_code, {})
        fcst_source = forecast_sources.get(item_code, "none")

        stock_now = api_data["stock_now_units"]
        reserved_now = api_data["reserved_now_units"]
        ordered_now = api_data["ordered_now_units"]
        on_transfer_now = api_data["on_transfer_now_units"]

        available_base = stock_now - reserved_now + ordered_now

        pre_receipt_fcst = sum(pre_forecast.get(item_code, {}).get(d, 0) for d in pre_receipt_dates)
        projected_at_receipt = available_base - pre_receipt_fcst

        cover_fcst = sum(cover_forecast.get(item_code, {}).get(d, 0) for d in cover_dates)

        case_qty, case_qty_source = _resolve_case_qty(master, settings)
        min_order_cases = float(settings.get("min_order_cases") or 1)
        safety_days = float(settings.get("safety_days_override") or 1.0)

        num_cover_dates = len(cover_dates)
        avg_daily_cover = cover_fcst / num_cover_dates if num_cover_dates > 0 else 0
        safety_stock = avg_daily_cover * safety_days

        raw_needed = max(0, cover_fcst + safety_stock - projected_at_receipt)

        if case_qty and case_qty > 0 and raw_needed > 0:
            suggested_cases = math.ceil(raw_needed / case_qty)
            suggested_cases = max(suggested_cases, min_order_cases)
            suggested_units = suggested_cases * case_qty
        else:
            suggested_cases = 0
            suggested_units = 0

        warnings = _build_warnings(
            case_qty, projected_at_receipt, reserved_now, ordered_now,
            expiry, suggested_cases, pre_receipt_fcst, cover_fcst,
            available_base, fcst_source
        )
        warning_code = warnings[0][0] if warnings else None
        warning_text = warnings[0][1] if warnings else None

        line.case_qty_units = Decimal(str(case_qty or 0))
        line.stock_now_units = Decimal(str(stock_now))
        line.reserved_now_units = Decimal(str(reserved_now))
        line.ordered_now_units = Decimal(str(ordered_now))
        line.on_transfer_now_units = Decimal(str(on_transfer_now))
        line.available_base_units = Decimal(str(available_base))
        line.pre_receipt_forecast_units = Decimal(str(round(pre_receipt_fcst, 2)))
        line.projected_units_at_receipt = Decimal(str(round(projected_at_receipt, 2)))
        line.cover_forecast_units = Decimal(str(round(cover_fcst, 2)))
        line.safety_stock_units = Decimal(str(round(safety_stock, 2)))
        line.raw_needed_units = Decimal(str(round(raw_needed, 2)))
        line.suggested_cases = Decimal(str(round(suggested_cases, 2)))
        line.suggested_units = Decimal(str(round(suggested_units, 2)))
        line.final_cases = Decimal(str(round(suggested_cases, 2)))
        line.final_units = Decimal(str(round(suggested_units, 2)))
        line.warning_code = warning_code
        line.warning_text = warning_text

        line.explanation_text = (
            f"Available base = stock {stock_now:.0f} - reserved {reserved_now:.0f} "
            f"+ ordered {ordered_now:.0f} = {available_base:.0f}. "
            f"Pre-receipt forecast {pre_receipt_fcst:.1f} => "
            f"projected at receipt {projected_at_receipt:.1f}. "
            f"Cover forecast {cover_fcst:.1f} + safety {safety_stock:.1f} "
            f"=> raw need {raw_needed:.1f}. "
            f"Forecast source: {fcst_source}."
        )

        weekday_avgs_used = {}
        for wd in all_needed_weekdays:
            weekday_avgs_used[str(wd)] = weekday_avgs.get(item_code, {}).get(wd, 0)

        line.calc_json = {
            "pre_receipt_dates": [str(d) for d in pre_receipt_dates],
            "cover_dates": [str(d) for d in cover_dates],
            "weekday_averages": weekday_avgs_used,
            "forecast_source": fcst_source,
            "fallback_avg_30d": fb_info.get("avg_30d", 0),
            "fallback_avg_90d": fb_info.get("avg_90d", 0),
            "fallback_avg_180d": fb_info.get("avg_180d", 0),
            "fallback_daily_avg": fb_info.get("daily_avg", 0),
            "available_base_units": available_base,
            "pre_receipt_forecast_units": pre_receipt_fcst,
            "projected_units_at_receipt": projected_at_receipt,
            "cover_forecast_units": cover_fcst,
            "safety_days": safety_days,
            "safety_stock_units": safety_stock,
            "raw_needed_units": raw_needed,
            "case_qty_units": case_qty or 0,
            "case_qty_source": case_qty_source,
            "min_order_cases": min_order_cases,
            "sales_date_field": "invoice_date_utc0",
        }

        expiry_info = expiry_data.get(item_code, {})
        line.earliest_expiry_date = expiry_info.get("earliest_expiry_date")
        line.qty_at_earliest_expiry = Decimal(str(expiry_info.get("qty_at_earliest_expiry", 0)))
        line.expiring_within_30_days_units = Decimal(str(expiry_info.get("expiring_within_30_days_units", 0)))

        updated += 1

    db.session.commit()
    msg = f'Stock refreshed from PS365. {updated} items updated with recalculated suggestions.'
    if stale_count > 0:
        msg += f' {stale_count} items not found in PS365 snapshot.'
    flash(msg, 'success')
    logger.info(f"Stock refreshed for run #{run_id}: {updated} items updated, {stale_count} stale")
    return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))


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
        'Ordered Now', 'Available Base', 'Pre-Receipt Forecast',
        'Projected At Receipt', 'Cover Forecast', 'Safety Stock',
        'Suggested Cases', 'Suggested Units', 'Final Cases', 'Final Units',
        'Earliest Expiry', 'Expiry Qty', 'Warning', 'Explanation'
    ])

    for line in lines:
        writer.writerow([
            line.item_code_365, line.item_name,
            float(line.case_qty_units), float(line.stock_now_units),
            float(line.reserved_now_units), float(line.ordered_now_units),
            float(line.available_base_units),
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


@replenishment_bp.route('/run/<int:run_id>/export-order-csv')
@admin_or_warehouse_required
def export_order_csv(run_id):
    from services.replenishment_mvp.repositories import get_item_master_for_codes

    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).order_by(
        ReplenishmentRunLine.item_code_365.asc()
    ).all()

    order_lines = [l for l in lines if (l.final_cases or l.suggested_cases) > 0]

    item_codes = [l.item_code_365 for l in order_lines]
    item_master = get_item_master_for_codes(item_codes)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Item Code', 'Supplier Item Code', 'Barcode', 'Item Name',
        'Order Qty (Cases)', 'Units Per Case', 'Order Qty (Units)'
    ])

    for line in order_lines:
        master = item_master.get(line.item_code_365, {})
        cases = float(line.final_cases if line.final_cases is not None else line.suggested_cases)
        case_qty = float(line.case_qty_units)
        units = cases * case_qty

        writer.writerow([
            line.item_code_365,
            master.get("supplier_item_code", ""),
            master.get("barcode", ""),
            line.item_name or "",
            int(cases) if cases == int(cases) else cases,
            int(case_qty) if case_qty == int(case_qty) else case_qty,
            int(units) if units == int(units) else units,
        ])

    filename = f"order_{run.supplier_code}_{run.run_date}_{run.run_type}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@replenishment_bp.route('/run/<int:run_id>/send-po', methods=['POST'])
@admin_or_warehouse_required
def send_po_to_ps365(run_id):
    import os
    import requests as http_requests
    from datetime import datetime, timezone, timedelta
    from models import DwItem
    from routes_reports import _build_po_lines, _fetch_item_pricing_from_ps365

    PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
    PS365_TOKEN = os.getenv("PS365_TOKEN", "")

    if not PS365_BASE_URL or not PS365_TOKEN:
        flash("PS365 API not configured.", "error")
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()

    order_lines = [l for l in lines if l.final_units and float(l.final_units) > 0]
    if not order_lines:
        flash("No items with Final Units > 0 to order.", "warning")
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    item_codes = [l.item_code_365 for l in order_lines]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items}

    missing_pricing_codes = [
        code for code in item_codes
        if not dw_map.get(code)
        or dw_map[code].cost_price is None
        or not dw_map[code].vat_code_365
        or dw_map[code].vat_percent is None
    ]
    ps365_pricing = _fetch_item_pricing_from_ps365(missing_pricing_codes) if missing_pricing_codes else {}
    if ps365_pricing:
        dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        dw_map = {d.item_code_365: d for d in dw_items}

    po_lines = []
    for line in order_lines:
        dw = dw_map.get(line.item_code_365)
        ps_price = ps365_pricing.get(line.item_code_365, {})
        cost = (float(dw.cost_price) if dw and dw.cost_price is not None and float(dw.cost_price) > 0 else None)
        if cost is None:
            ps_cost = ps_price.get("cost_price")
            if ps_cost is not None and ps_cost > 0:
                cost = ps_cost
        vat = (dw.vat_code_365 if dw and dw.vat_code_365 else None) or ps_price.get("vat_code_365")
        vat_pct = (float(dw.vat_percent) if dw and dw.vat_percent is not None else None) or ps_price.get("vat_percent")

        line_data = {
            "item_code_365": line.item_code_365,
            "line_quantity": str(int(float(line.final_units))),
        }
        if cost is not None:
            line_data["cost_price"] = cost
        if vat:
            line_data["vat_code_365"] = vat
        if vat_pct is not None:
            line_data["vat_percent"] = vat_pct
        po_lines.append(line_data)

    try:
        detail_lines, h_totals = _build_po_lines(po_lines)
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        deliver_by_utc = (now_utc + timedelta(days=7)).replace(microsecond=0)
        shopping_cart_code = f"WMDS-RPL-{now_utc.strftime('%Y%m%d-%H%M%S')}-{run.supplier_code}"

        payload = {
            "api_credentials": {"token": PS365_TOKEN},
            "order": {
                "purchase_order_header": {
                    "shopping_cart_code": shopping_cart_code,
                    "order_date_local": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_date_utc0": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_date_deliverby_utc0": deliver_by_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "supplier_code_365": run.supplier_code,
                    "agent_code_365": "",
                    "user_code_365": current_user.username,
                    "comments": f"Replenishment Run #{run.id} - {run.run_type} - {len(po_lines)} items",
                    "search_additional_barcodes": False,
                    "order_status_code_365": "PROC",
                    "order_status_name": "PROCESSING",
                    **h_totals,
                },
                "list_purchase_order_details": detail_lines,
            }
        }

        import json as _json
        logger.debug("Replenishment PO payload lines: %s", _json.dumps(detail_lines[:3], indent=2))

        url = f"{PS365_BASE_URL}/purchaseorder"
        resp = http_requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()

        api_response = result.get("api_response", {})
        if api_response.get("response_code") == "1":
            po_code = api_response.get("response_id", "Unknown")
            flash(f"Purchase Order created successfully! PO Code: {po_code} ({len(po_lines)} items)", "success")
            logger.info(f"Replenishment PO {po_code} created for run #{run.id}, supplier {run.supplier_code}")
            run.status = 'po_sent'
            run.notes = (run.notes or '') + f"\nPO {po_code} sent {now_utc.strftime('%Y-%m-%d %H:%M')}"
            db.session.commit()
        else:
            error_msg = api_response.get("response_msg", "Unknown error")
            logger.error(f"PS365 PO creation failed for replenishment run #{run.id}: {api_response}")
            flash(f"PS365 error: {error_msg}", "error")
    except Exception as e:
        logger.exception(f"Failed to create PO for replenishment run #{run.id}")
        flash(f"Error creating PO: {str(e)}", "error")

    return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))


def _build_po_email_content(run, order_lines, po_code, sent_at):
    """Build the email content (text and HTML bodies). Returns dict with text_body and html_body."""
    rows_html = ""
    rows_text = ""
    for idx, line in enumerate(sorted(order_lines, key=lambda l: l.item_code_365), start=1):
        case_qty = int(float(line.case_qty_units)) if float(line.case_qty_units) == int(float(line.case_qty_units)) else float(line.case_qty_units)
        final_cases = int(float(line.final_cases)) if float(line.final_cases) == int(float(line.final_cases)) else float(line.final_cases)
        item_name = line.item_name or ""
        rows_html += (
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #ddd;'>{item_name}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;'>{run.supplier_code}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;text-align:right;'>{case_qty}</td>"
            f"<td style='padding:8px;border:1px solid #ddd;text-align:right;'>{final_cases}</td>"
            f"</tr>"
        )
        rows_text += f"{idx}. {item_name} | {run.supplier_code} | {case_qty} | {final_cases}\n"

    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #4472C4; color: white; }}
            tr:nth-child(even) {{ background-color: #f2f2f2; }}
            .header {{ background-color: #f8f9fa; padding: 20px; border-bottom: 2px solid #4472C4; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Purchase Order Created</h2>
            <p><strong>PO Code:</strong> {po_code}</p>
            <p><strong>Supplier:</strong> {run.supplier_name} ({run.supplier_code})</p>
            <p><strong>Run ID:</strong> {run.id} ({run.run_type})</p>
            <p><strong>Date:</strong> {sent_at.strftime('%Y-%m-%d %H:%M')} UTC</p>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Item Name</th>
                    <th>Supplier Code</th>
                    <th>Case Qty</th>
                    <th>Cases Ordered</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        <p style='margin-top:20px;'><strong>Total Items:</strong> {len(order_lines)}</p>
        <hr>
        <p style='color: #666; font-size: 12px;'>This is an automated email from the Warehouse Management System.</p>
    </body>
    </html>
    """

    text_body = f"""Purchase Order Created

PO Code: {po_code}
Supplier: {run.supplier_name} ({run.supplier_code})
Run ID: {run.id} ({run.run_type})
Date: {sent_at.strftime('%Y-%m-%d %H:%M')} UTC

Items:
Item Name | Supplier Code | Case Qty | Cases Ordered
{rows_text}
Total Items: {len(order_lines)}
"""
    return {"text_body": text_body, "html_body": html_body}


def _send_po_email(run, order_lines, po_code, sent_at, recipient="eplattforma@gmail.com"):
    import os
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
    SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    RECIPIENT = recipient

    logger.info(f"_send_po_email: SMTP_HOST={bool(SMTP_HOST)}, SMTP_EMAIL={SMTP_EMAIL}, RECIPIENT={RECIPIENT}, order_lines={len(order_lines)}")

    content = _build_po_email_content(run, order_lines, po_code, sent_at)
    text_body = content["text_body"]
    html_body = content["html_body"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"PO {po_code} - {run.supplier_name} - {len(order_lines)} items"
    msg["From"] = SMTP_EMAIL
    msg["To"] = RECIPIENT
    
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        logger.info(f"Attempting to connect to SMTP {SMTP_HOST}:{SMTP_PORT}")
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            logger.info(f"SMTP connection established, logging in as {SMTP_EMAIL}")
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            logger.info(f"SMTP login successful, sending to {RECIPIENT}")
            server.sendmail(SMTP_EMAIL, RECIPIENT, msg.as_string())
            logger.info(f"SMTP sendmail completed for {RECIPIENT}")
        logger.info(f"PO email sent to {RECIPIENT} for PO {po_code}")
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending PO email to {RECIPIENT}: {type(e).__name__}: {e}")
    except Exception as e:
        logger.error(f"Error sending PO email to {RECIPIENT}: {type(e).__name__}: {e}", exc_info=True)


@replenishment_bp.route('/run/<int:run_id>/email-preview', methods=['GET'])
@admin_or_warehouse_required
def email_preview(run_id):
    from datetime import datetime, timezone

    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()
    order_lines = [l for l in lines if l.final_units and float(l.final_units) > 0]

    if not order_lines:
        return jsonify({"error": "No items with Final Units > 0"}), 400

    po_code = ""
    if run.notes:
        import re
        match = re.search(r'PO (\S+) sent', run.notes)
        if match:
            po_code = match.group(1)
    if not po_code:
        po_code = f"Run-{run.id}"

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    content = _build_po_email_content(run, order_lines, po_code, now_utc)
    
    return jsonify({
        "subject": f"PO {po_code} - {run.supplier_name} - {len(order_lines)} items",
        "text_body": content["text_body"],
        "html_body": content["html_body"]
    })


@replenishment_bp.route('/run/<int:run_id>/email-order', methods=['POST'])
@admin_or_warehouse_required
def email_order(run_id):
    from datetime import datetime, timezone

    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()
    order_lines = [l for l in lines if l.final_units and float(l.final_units) > 0]

    if not order_lines:
        flash("No items with Final Units > 0 to email.", "warning")
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    recipient_email = request.form.get("recipient_email", "eplattforma@gmail.com").strip()
    if not recipient_email:
        flash("Recipient email is required.", "warning")
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    po_code = ""
    if run.notes:
        import re
        match = re.search(r'PO (\S+) sent', run.notes)
        if match:
            po_code = match.group(1)
    if not po_code:
        po_code = f"Run-{run.id}"

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    _send_po_email(run, order_lines, po_code, now_utc, recipient_email)
    flash(f"Order email sent to {recipient_email} ({len(order_lines)} items).", "success")
    return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))


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
