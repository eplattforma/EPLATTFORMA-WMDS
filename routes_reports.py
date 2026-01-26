import os
import io
import csv
import math
import sys
import requests
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP
from flask import Blueprint, render_template, Response, flash, redirect, url_for, request, jsonify
from flask_login import login_required, current_user

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")
logger = logging.getLogger(__name__)

PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
PS365_TOKEN = os.getenv("PS365_TOKEN", "")

@reports_bp.route("/reserved-stock-777")
@login_required
def reserved_stock_777():
    from models import Ps365ReservedStock777
    rows = Ps365ReservedStock777.query.order_by(Ps365ReservedStock777.stock_reserved.desc(), Ps365ReservedStock777.item_code_365).all()
    seasons = sorted(set(r.season_name for r in rows if r.season_name))
    synced_at = rows[0].synced_at if rows else None
    return render_template("reports/reserved_stock_777.html", rows=rows, seasons=seasons, synced_at=synced_at, count=len(rows))

@reports_bp.route("/reserved-stock-777/download")
@login_required
def reserved_stock_777_download():
    from models import Ps365ReservedStock777
    rows = Ps365ReservedStock777.query.order_by(Ps365ReservedStock777.stock_reserved.desc(), Ps365ReservedStock777.item_code_365).all()
    if not rows:
        flash("No data found.", "warning")
        return redirect(url_for("reports.reserved_stock_777"))
    
    output = io.StringIO()
    fieldnames = ["item_code_365", "item_name", "supplier", "pieces_per_unit", "min_order_qty", "stock", "customer_order", "available", "on_po", "required"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        stock_val = float(r.stock or 0)
        reserved_val = float(r.stock_reserved or 0)
        ordered_val = float(r.stock_ordered or 0)
        req = max(0, reserved_val - stock_val - ordered_val)
        writer.writerow({
            "item_code_365": r.item_code_365,
            "item_name": r.item_name,
            "supplier": r.season_name or "",
            "pieces_per_unit": int(r.number_of_pieces or 0),
            "min_order_qty": int(r.number_field_5_value or 0),
            "stock": round(stock_val, 1),
            "customer_order": int(reserved_val),
            "available": int(r.available_stock or 0),
            "on_po": int(ordered_val),
            "required": int(math.ceil(req))
        })
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=reserved_stock_777.csv"})

@reports_bp.route("/reserved-stock-777/refresh")
@login_required
def reserved_stock_777_refresh():
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash("Access denied.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    try:
        from scripts.ps365_reserved_stock_report_777 import build_rows, save_to_db
        rows = build_rows()
        if rows:
            save_to_db(rows)
            flash(f"Report refreshed successfully. {len(rows)} items synced from PS365.", "success")
        else:
            flash("No items with reservations found.", "info")
    except Exception as e:
        logger.error(f"Error refreshing report: {e}")
        flash(f"Error refreshing: {str(e)}", "danger")
    return redirect(url_for("reports.reserved_stock_777"))


@reports_bp.route("/reserved-stock-777/create-po", methods=["POST"])
@login_required
def reserved_stock_777_create_po():
    """Create a purchase order in PS365 with items that have Required > 0"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash("Access denied.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    if not PS365_BASE_URL or not PS365_TOKEN:
        flash("PS365 API not configured. Please set PS365_BASE_URL and PS365_TOKEN.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    from models import Ps365ReservedStock777
    
    supplier_filter = request.form.get("supplier_filter", "")
    supplier_code = request.form.get("supplier_code", "").strip()
    
    if not supplier_code:
        flash("Supplier code is required to create a PO.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    rows = Ps365ReservedStock777.query.all()
    
    po_lines = []
    for r in rows:
        if supplier_filter and r.season_name != supplier_filter:
            continue
        
        stock_val = float(r.stock or 0)
        reserved_val = float(r.stock_reserved or 0)
        ordered_val = float(r.stock_ordered or 0)
        required = max(0, reserved_val - stock_val - ordered_val)
        
        if required > 0:
            po_qty = Decimal(str(required)).quantize(Decimal("1"), rounding=ROUND_UP)
            po_lines.append({
                "item_code_365": r.item_code_365,
                "line_quantity": str(int(po_qty))
            })
    
    if not po_lines:
        flash("No items with Required > 0 found for the selected filter.", "warning")
        return redirect(url_for("reports.reserved_stock_777"))
    
    try:
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        deliver_by_utc = (now_utc + timedelta(days=7)).replace(microsecond=0)
        shopping_cart_code = f"WMDS-{now_utc.strftime('%Y%m%d-%H%M%S')}-{supplier_code}"
        
        payload = {
            "api_credentials": {"token": PS365_TOKEN},
            "order": {
                "purchase_order_header": {
                    "shopping_cart_code": shopping_cart_code,
                    "order_date_local": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_date_utc0": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_date_deliverby_utc0": deliver_by_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "supplier_code_365": supplier_code,
                    "agent_code_365": "",
                    "user_code_365": current_user.username,
                    "comments": f"Auto PO from Cross Shipping Report - {len(po_lines)} items",
                    "search_additional_barcodes": False
                },
                "list_purchase_order_details": []
            }
        }
        
        for idx, ln in enumerate(po_lines, start=1):
            payload["order"]["list_purchase_order_details"].append({
                "line_number": str(idx),
                "item_code_365": ln["item_code_365"],
                "line_quantity": ln["line_quantity"]
            })
        
        url = f"{PS365_BASE_URL}/purchaseorder"
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        
        api_response = result.get("api_response", {})
        if api_response.get("response_code") == "1":
            po_code = api_response.get("response_id", "Unknown")
            flash(f"Purchase Order created successfully! PO Code: {po_code} ({len(po_lines)} items)", "success")
            logger.info(f"Created PO {po_code} with {len(po_lines)} items for supplier {supplier_code}")
        else:
            error_msg = api_response.get("response_message", "Unknown error")
            flash(f"PS365 error: {error_msg}", "danger")
            logger.error(f"PS365 PO creation failed: {api_response}")
    
    except requests.RequestException as e:
        flash(f"Failed to connect to PS365: {str(e)}", "danger")
        logger.error(f"PS365 connection error: {e}")
    except Exception as e:
        flash(f"Error creating PO: {str(e)}", "danger")
        logger.error(f"PO creation error: {e}")
    
    return redirect(url_for("reports.reserved_stock_777"))
