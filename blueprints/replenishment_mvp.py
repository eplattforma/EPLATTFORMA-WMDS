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


def legacy_required(f):
    """Phase 2 gate: every route in this blueprint short-circuits when the
    `legacy_replenishment_enabled` setting is OFF (default in Phase 1).

    The blueprint stays registered (Forecast Workbench imports
    `_build_po_email_content` and `_send_po_email` from this module, so the
    module must still load) but no Replenishment URL is reachable while the
    flag is off. JSON / CSV endpoints return a JSON 404; HTML routes flash
    and redirect home so warehouse staff who deep-linked an old bookmark see
    a clear message instead of stale data.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from models import Setting
        raw = Setting.get(db.session, 'legacy_replenishment_enabled', 'false')
        enabled = str(raw).strip().lower() in ('true', '1', 'yes', 'on')
        if not enabled:
            wants_json = (
                request.path.startswith('/replenishment-mvp/api/')
                or 'application/json' in (request.headers.get('Accept') or '')
            )
            if wants_json:
                return jsonify({
                    "error": "legacy_replenishment_disabled",
                    "message": (
                        "The MVP Replenishment module is disabled. "
                        "Use Forecast Workbench instead."
                    ),
                }), 404
            flash(
                'The MVP Replenishment module has been retired. '
                'Use Forecast Workbench for ordering proposals.',
                'warning',
            )
            # Redirect to Forecast Workbench (the replacement) so the user
            # lands directly on the supported tool, not the generic home.
            try:
                return redirect(url_for('forecast_workbench.suppliers'))
            except Exception:
                # Fall back to home only if the workbench endpoint is not
                # registered (e.g. blueprint disabled in some env).
                return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@replenishment_bp.route('/')
@admin_or_warehouse_required
@legacy_required
def index():
    from services.replenishment_mvp.repositories import get_active_suppliers
    suppliers = get_active_suppliers()

    recent_runs = ReplenishmentRun.query.order_by(
        ReplenishmentRun.created_at.desc()
    ).limit(20).all()

    today = date.today()

    return render_template(
        'replenishment_mvp/index.html',
        suppliers=suppliers,
        recent_runs=recent_runs,
        today=today.isoformat(),
    )


@replenishment_bp.route('/generate', methods=['POST'])
@admin_or_warehouse_required
@legacy_required
def generate():
    from services.replenishment_mvp.planner import generate_replenishment_run

    supplier_code = request.form.get('supplier_code', '').strip()
    run_date_str = request.form.get('run_date', '').strip()

    if not supplier_code or not run_date_str:
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
@legacy_required
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
@legacy_required
def refresh_stock(run_id):
    import math
    from services.replenishment_mvp.ps365_client import fetch_supplier_stock, REPLENISHMENT_WAREHOUSE_STORE
    from services.replenishment_mvp.repositories import (
        get_item_master_for_codes, get_item_settings_for_codes,
        get_expiry_summary,
    )
    from services.replenishment_mvp.planner import _resolve_case_qty, _build_warnings
    from models import SkuForecastResult, SkuForecastProfile, ForecastItemSupplierMap, Setting

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

    cover_days = int(Setting.get(db.session, "forecast_default_cover_days", "7"))
    buffer_days = float(Setting.get(db.session, "forecast_buffer_stock_days", "1"))
    default_review_cycle = float(Setting.get(db.session, "forecast_review_cycle_days", "1"))

    item_codes = list(stock_snapshot.keys())
    item_master = get_item_master_for_codes(item_codes)
    item_settings = get_item_settings_for_codes(item_codes)
    expiry_data = get_expiry_summary(item_codes, REPLENISHMENT_WAREHOUSE_STORE)

    forecast_results = {}
    fr_rows = db.session.query(SkuForecastResult).filter(
        SkuForecastResult.item_code_365.in_(item_codes)
    ).all()
    for fr in fr_rows:
        forecast_results[fr.item_code_365] = fr

    forecast_profiles = {}
    fp_rows = db.session.query(SkuForecastProfile).filter(
        SkuForecastProfile.item_code_365.in_(item_codes)
    ).all()
    for fp in fp_rows:
        forecast_profiles[fp.item_code_365] = fp

    supplier_maps = {}
    sm_rows = db.session.query(ForecastItemSupplierMap).filter(
        ForecastItemSupplierMap.item_code_365.in_(item_codes),
        ForecastItemSupplierMap.is_active == True,
    ).all()
    for sm in sm_rows:
        supplier_maps[sm.item_code_365] = sm

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
        fcst_result = forecast_results.get(item_code)
        fcst_profile = forecast_profiles.get(item_code)
        smap = supplier_maps.get(item_code)

        stock_now = api_data["stock_now_units"]
        reserved_now = api_data["reserved_now_units"]
        ordered_now = api_data["ordered_now_units"]
        on_transfer_now = api_data["on_transfer_now_units"]

        net_available = stock_now - reserved_now + ordered_now

        lead_time = 0.0
        review_cycle = default_review_cycle
        if smap:
            if smap.lead_time_days is not None:
                lead_time = float(smap.lead_time_days)
            if smap.review_cycle_days is not None:
                review_cycle = float(smap.review_cycle_days)

        if fcst_result:
            daily_forecast = float(fcst_result.final_forecast_daily_qty or 0)
            weekly_forecast = float(fcst_result.final_forecast_weekly_qty or 0)
            base_weekly = float(fcst_result.base_forecast_weekly_qty or 0)
            trend_adjusted_weekly = float(fcst_result.trend_adjusted_weekly_qty or 0)
        else:
            daily_forecast = 0.0
            weekly_forecast = 0.0
            base_weekly = 0.0
            trend_adjusted_weekly = 0.0

        demand_class = fcst_profile.demand_class if fcst_profile else "no_data"
        forecast_method = fcst_profile.forecast_method if fcst_profile else "NONE"
        trend_flag = fcst_profile.trend_flag if fcst_profile else "flat"
        forecast_confidence = fcst_profile.forecast_confidence if fcst_profile else "none"

        buffer_stock = daily_forecast * buffer_days
        total_cover = cover_days + lead_time + review_cycle
        cover_fcst = daily_forecast * cover_days
        target_stock = daily_forecast * total_cover + buffer_stock

        raw_needed = max(0, target_stock - net_available)

        case_qty, case_qty_source = _resolve_case_qty(master, settings)
        min_order_cases = float(settings.get("min_order_cases") or 1)

        if case_qty and case_qty > 0 and raw_needed > 0:
            suggested_cases = math.ceil(raw_needed / case_qty)
            suggested_cases = max(suggested_cases, min_order_cases)
            suggested_units = suggested_cases * case_qty
        else:
            suggested_cases = 0
            suggested_units = 0

        forecast_source = forecast_method if fcst_profile else "none"
        warnings = _build_warnings(
            case_qty, net_available - cover_fcst, reserved_now, ordered_now,
            expiry_data.get(item_code, {}), suggested_cases, 0, cover_fcst,
            net_available, forecast_source
        )
        warning_code = warnings[0][0] if warnings else None
        warning_text = warnings[0][1] if warnings else None

        line.case_qty_units = Decimal(str(case_qty or 0))
        line.stock_now_units = Decimal(str(stock_now))
        line.reserved_now_units = Decimal(str(reserved_now))
        line.ordered_now_units = Decimal(str(ordered_now))
        line.on_transfer_now_units = Decimal(str(on_transfer_now))
        line.available_base_units = Decimal(str(net_available))
        line.pre_receipt_forecast_units = Decimal("0")
        line.projected_units_at_receipt = Decimal(str(round(net_available, 2)))
        line.cover_forecast_units = Decimal(str(round(cover_fcst, 2)))
        line.safety_stock_units = Decimal(str(round(buffer_stock, 2)))
        line.raw_needed_units = Decimal(str(round(raw_needed, 2)))
        line.suggested_cases = Decimal(str(round(suggested_cases, 2)))
        line.suggested_units = Decimal(str(round(suggested_units, 2)))
        line.final_cases = Decimal(str(round(suggested_cases, 2)))
        line.final_units = Decimal(str(round(suggested_units, 2)))
        line.warning_code = warning_code
        line.warning_text = warning_text

        line.explanation_text = (
            f"Demand class: {demand_class}. Method: {forecast_method}. "
            f"Weekly forecast: {weekly_forecast:.2f}. Daily forecast: {daily_forecast:.2f}. "
            f"Cover {cover_days}d + LT {lead_time:.0f}d + RC {review_cycle:.0f}d = {total_cover:.0f}d. "
            f"Buffer stock ({buffer_days:.0f}d): {buffer_stock:.2f}. "
            f"Target: {target_stock:.1f}. "
            f"Net available = stock {stock_now:.0f} - reserved {reserved_now:.0f} "
            f"+ ordered {ordered_now:.0f} = {net_available:.0f}. "
            f"Raw need: {raw_needed:.1f}."
        )

        line.calc_json = {
            "demand_class": demand_class,
            "forecast_method": forecast_method,
            "forecast_confidence": forecast_confidence,
            "trend_flag": trend_flag,
            "base_weekly_forecast": round(base_weekly, 4),
            "trend_adjusted_weekly": round(trend_adjusted_weekly, 4),
            "final_weekly_forecast": round(weekly_forecast, 4),
            "final_daily_forecast": round(daily_forecast, 4),
            "cover_days": cover_days,
            "lead_time_days": lead_time,
            "review_cycle_days": review_cycle,
            "total_cover_days": total_cover,
            "buffer_days": buffer_days,
            "buffer_stock_qty": round(buffer_stock, 4),
            "target_stock_qty": round(target_stock, 4),
            "stock_now": stock_now,
            "reserved_now": reserved_now,
            "ordered_now": ordered_now,
            "net_available": net_available,
            "cover_forecast_units": round(cover_fcst, 4),
            "raw_needed_units": round(raw_needed, 4),
            "case_qty_units": case_qty or 0,
            "case_qty_source": case_qty_source,
            "min_order_cases": min_order_cases,
        }

        if fcst_profile:
            line.calc_json["weeks_non_zero_26"] = fcst_profile.weeks_non_zero_26
            line.calc_json["adi_26"] = float(fcst_profile.adi_26) if fcst_profile.adi_26 else None
            line.calc_json["cv2_26"] = float(fcst_profile.cv2_26) if fcst_profile.cv2_26 else None
            line.calc_json["seed_source"] = fcst_profile.seed_source
            line.calc_json["review_flag"] = fcst_profile.review_flag
            line.calc_json["review_reason"] = fcst_profile.review_reason

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
@legacy_required
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
@legacy_required
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

    filename = f"replenishment_{run.supplier_code}_{run.run_date}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@replenishment_bp.route('/run/<int:run_id>/export-order-csv')
@admin_or_warehouse_required
@legacy_required
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

    filename = f"order_{run.supplier_code}_{run.run_date}.csv"
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


@replenishment_bp.route('/run/<int:run_id>/send-po', methods=['POST'])
@admin_or_warehouse_required
@legacy_required
def send_po_to_ps365(run_id):
    from datetime import datetime, timezone
    from services.ps365_purchase_order_service import create_ps365_purchase_order

    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()

    positive = [l for l in lines if l.final_units and float(l.final_units) > 0]
    if not positive:
        flash("No items with Final Units > 0 to order.", "warning")
        return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))

    order_lines = [
        {"item_code_365": l.item_code_365,
         "line_quantity": int(float(l.final_units))}
        for l in positive
    ]

    result = create_ps365_purchase_order(
        supplier_code=run.supplier_code,
        order_lines=order_lines,
        user_code=current_user.username,
        comments=f"Replenishment Run #{run.id} - 7-day cover - {len(order_lines)} items",
        cart_prefix="WMDS-RPL",
    )

    if result["success"]:
        po_code = result["po_code"]
        flash(f"Purchase Order created successfully! PO Code: {po_code} ({result['lines_count']} items)", "success")
        run.status = 'po_sent'
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        run.notes = (run.notes or '') + f"\nPO {po_code} sent {now_utc.strftime('%Y-%m-%d %H:%M')}"
        db.session.commit()
    else:
        flash(f"PS365 error: {result['error']}", "error")

    return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))


def _build_po_email_content(run, order_lines, po_code, sent_at, qty_label="Cases Ordered"):
    """Build the email content (text and HTML bodies). Returns dict with text_body and html_body.

    qty_label controls the header of the rightmost column. Replenishment passes
    cases (default); forecast supplier passes units, so it overrides this.
    """
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
            <p><strong>Run ID:</strong> {run.id} (7-day cover)</p>
            <p><strong>Date:</strong> {sent_at.strftime('%Y-%m-%d %H:%M')} UTC</p>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Item Name</th>
                    <th>Supplier Code</th>
                    <th>Case Qty</th>
                    <th>{qty_label}</th>
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
Run ID: {run.id} (7-day cover)
Date: {sent_at.strftime('%Y-%m-%d %H:%M')} UTC

Items:
Item Name | Supplier Code | Case Qty | {qty_label}
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

    if not SMTP_HOST or not SMTP_EMAIL or not SMTP_PASSWORD:
        logger.error("SMTP not configured (SMTP_HOST/SMTP_EMAIL/SMTP_PASSWORD missing)")
        return False, "SMTP not configured on server."

    try:
        logger.info(f"Attempting to connect to SMTP {SMTP_HOST}:{SMTP_PORT}")
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            logger.info(f"SMTP connection established, logging in as {SMTP_EMAIL}")
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            logger.info(f"SMTP login successful, sending to {RECIPIENT}")
            server.sendmail(SMTP_EMAIL, RECIPIENT, msg.as_string())
            logger.info(f"SMTP sendmail completed for {RECIPIENT}")
        logger.info(f"PO email sent to {RECIPIENT} for PO {po_code}")
        return True, None
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        return False, f"SMTP authentication failed: {e}"
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending PO email to {RECIPIENT}: {type(e).__name__}: {e}")
        return False, f"SMTP error: {type(e).__name__}: {e}"
    except Exception as e:
        logger.error(f"Error sending PO email to {RECIPIENT}: {type(e).__name__}: {e}", exc_info=True)
        return False, f"Error sending email: {type(e).__name__}: {e}"


@replenishment_bp.route('/run/<int:run_id>/email-preview', methods=['GET'])
@admin_or_warehouse_required
@legacy_required
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
@legacy_required
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
    ok, err = _send_po_email(run, order_lines, po_code, now_utc, recipient_email)
    if ok:
        flash(f"Order email sent to {recipient_email} ({len(order_lines)} items).", "success")
    else:
        flash(f"Failed to send order email: {err}", "error")
    return redirect(url_for('replenishment_mvp.run_detail', run_id=run_id))


@replenishment_bp.route('/api/run/<int:run_id>')
@admin_or_warehouse_required
@legacy_required
def api_run_json(run_id):
    run = ReplenishmentRun.query.get_or_404(run_id)
    lines = ReplenishmentRunLine.query.filter_by(run_id=run_id).all()
    return jsonify({
        "run": {
            "id": run.id,
            "supplier_code": run.supplier_code,
            "supplier_name": run.supplier_name,
            "run_date": str(run.run_date),
            "cover_days": 7,
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
