import os
import io
import csv
import math
import sys
import smtplib
import requests
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP
from flask import Blueprint, render_template, Response, flash, redirect, url_for, request, jsonify
from flask_login import login_required, current_user

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")
logger = logging.getLogger(__name__)

PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
PS365_TOKEN = os.getenv("PS365_TOKEN", "")


def _fetch_item_pricing_from_ps365(item_codes):
    """Fetch cost_price and vat_code from PS365 for given item codes.
    Uses the same list_items API format as datawarehouse_sync with items_selection filter.
    Returns dict[item_code] = {'cost_price': float, 'vat_code_365': str}
    Also updates DwItem records so data is cached for next time."""
    if not item_codes or not PS365_BASE_URL or not PS365_TOKEN:
        return {}
    
    from ps365_client import call_ps365
    
    pricing = {}
    items_selection = ",".join(item_codes)
    page = 1
    page_size = 100
    
    while True:
        try:
            response = call_ps365("list_items", {
                "filter_define": {
                    "only_counted": "N",
                    "page_number": page,
                    "page_size": page_size,
                    "active_type": "all",
                    "ecommerce_type": "all",
                    "items_selection": items_selection,
                    "categories_selection": "",
                    "departments_selection": "",
                    "items_supplier_selection": "",
                    "brands_selection": "",
                    "seasons_selection": "",
                    "models_selection": "",
                    "colours_selection": "",
                    "sizes_selection": "",
                    "sizes_group_selection": "",
                    "display_fields": "item_code_365,item_name,vat_code_365,vat_percent,price_excl_1",
                },
            })
            
            api_response = response.get("api_response", {}) or {}
            if api_response.get("response_code") != "1":
                logger.warning(f"PS365 list_items error: {api_response.get('response_msg', 'Unknown')}")
                break
            
            items = response.get("list_items") or []
            logger.info(f"_fetch_item_pricing: page {page} returned {len(items)} items")
            if not items:
                break
            
            for item in items:
                code = (item.get("item_code_365") or "").strip()
                if not code:
                    continue
                sell = None
                try:
                    raw = item.get("price_excl_1")
                    if raw is not None and str(raw).strip():
                        parsed = float(raw)
                        if parsed > 0:
                            sell = parsed
                except (ValueError, TypeError):
                    pass
                vat = item.get("vat_code_365") or None
                vat_pct = None
                try:
                    raw_pct = item.get("vat_percent")
                    if raw_pct is not None:
                        vat_pct = float(raw_pct)
                except (ValueError, TypeError):
                    pass
                pricing[code] = {"cost_price": sell, "vat_code_365": vat, "vat_percent": vat_pct}
                logger.info(f"  Item {code}: selling_price={sell}, vat_code={vat}, vat_percent={vat_pct}")
            
            if len(items) < page_size:
                break
            page += 1
        except Exception as e:
            logger.warning(f"Failed to fetch item pricing from PS365 page {page}: {e}")
            break
    
    if pricing:
        try:
            from models import DwItem
            from app import db
            for code, vals in pricing.items():
                dw = DwItem.query.filter_by(item_code_365=code).first()
                if dw:
                    if vals["cost_price"] is not None and vals["cost_price"] > 0 and (dw.cost_price is None or float(dw.cost_price) == 0):
                        dw.cost_price = vals["cost_price"]
                        logger.info(f"  Updated DwItem {code} cost_price = {vals['cost_price']}")
                    if vals["vat_code_365"] and not dw.vat_code_365:
                        dw.vat_code_365 = vals["vat_code_365"]
                        logger.info(f"  Updated DwItem {code} vat_code_365 = {vals['vat_code_365']}")
                    if vals.get("vat_percent") is not None and (dw.vat_percent is None or float(dw.vat_percent) == 0):
                        dw.vat_percent = vals["vat_percent"]
                        logger.info(f"  Updated DwItem {code} vat_percent = {vals['vat_percent']}")
            db.session.commit()
        except Exception as e:
            logger.warning(f"Failed to update DwItem with pricing: {e}")
    
    return pricing


@reports_bp.route("/reserved-stock-777")
@login_required
def reserved_stock_777():
    from models import Ps365ReservedStock777, SeasonSupplierSetting, DwItem
    
    rows = Ps365ReservedStock777.query.order_by(Ps365ReservedStock777.stock_reserved.desc(), Ps365ReservedStock777.item_code_365).all()
    seasons = sorted(set(r.season_name for r in rows if r.season_name))
    synced_at = rows[0].synced_at if rows else None
    
    item_codes = [r.item_code_365 for r in rows]
    dw_prices = {}
    if item_codes:
        dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        for d in dw_items:
            dw_prices[d.item_code_365] = {
                "cost_price": float(d.cost_price) if d.cost_price is not None else None,
                "vat_code_365": d.vat_code_365 or "",
            }
    
    season_settings = {}
    all_settings = SeasonSupplierSetting.query.all()
    for s in all_settings:
        season_settings[s.season_code_365] = {
            "supplier_code": s.supplier_code or "",
            "email_to": s.email_to or "",
            "email_cc": s.email_cc or "",
            "email_comment": s.email_comment or ""
        }
    
    return render_template("reports/reserved_stock_777.html", rows=rows, seasons=seasons, synced_at=synced_at, count=len(rows), season_settings=season_settings, dw_prices=dw_prices)

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
        pieces_per_unit = int(r.number_of_pieces or 1)
        min_order_qty = int(r.number_field_5_value or 0)
        shortage = reserved_val - stock_val - ordered_val
        raw_required = int(shortage * pieces_per_unit) if shortage > 0 else 0
        req = max(raw_required, min_order_qty) if raw_required > 0 else 0
        writer.writerow({
            "item_code_365": r.item_code_365,
            "item_name": r.item_name,
            "supplier": r.season_name or "",
            "pieces_per_unit": pieces_per_unit,
            "min_order_qty": min_order_qty,
            "stock": round(stock_val, 1),
            "customer_order": int(reserved_val),
            "available": int(r.available_stock or 0),
            "on_po": int(ordered_val),
            "required": req
        })
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=reserved_stock_777.csv"})

@reports_bp.route("/reserved-stock-777/refresh")
@login_required
def reserved_stock_777_refresh():
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash("Access denied.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    try:
        from scripts.ps365_reserved_stock_report_777 import build_rows, clear_table_for_store, save_to_db, STORE_CODE
        rows = build_rows()
        # Always clear first, then insert (full refresh pattern)
        clear_table_for_store(STORE_CODE)
        if rows:
            save_to_db(rows)
            flash(f"Report refreshed successfully. {len(rows)} items synced from PS365.", "success")
        else:
            flash("No items with reservations found.", "info")
    except Exception as e:
        logger.error(f"Error refreshing report: {e}")
        flash(f"Error refreshing: {str(e)}", "danger")
    return redirect(url_for("reports.reserved_stock_777"))


@reports_bp.route("/reserved-stock-777/save-cost-price", methods=["POST"])
@login_required
def reserved_stock_777_save_cost_price():
    from routes import validate_csrf_token
    if not validate_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token"}), 403

    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"success": False, "error": "Access denied"}), 403

    from models import DwItem
    from app import db

    payload = request.get_json(force=True) or {}
    item_code = (payload.get("item_code_365") or "").strip()
    if not item_code:
        return jsonify({"success": False, "error": "Missing item_code_365"}), 400

    try:
        cost_val = payload.get("cost_price")
        if cost_val is None or str(cost_val).strip() == "":
            cost_price = None
        else:
            cost_price = round(float(cost_val), 4)
            if cost_price < 0:
                return jsonify({"success": False, "error": "Cost price cannot be negative"}), 400
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid cost price value"}), 400

    try:
        dw = DwItem.query.filter_by(item_code_365=item_code).first()
        if not dw:
            return jsonify({"success": False, "error": f"Item {item_code} not found"}), 404
        dw.cost_price = cost_price
        db.session.commit()
        logger.info(f"Cost price updated for {item_code}: {cost_price} by {current_user.username}")
        return jsonify({"success": True, "cost_price": cost_price})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error saving cost price: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@reports_bp.route("/reserved-stock-777/settings/save", methods=["POST"])
@login_required
def reserved_stock_777_save_settings():
    """Save season→supplier mapping and email settings"""
    from routes import validate_csrf_token
    if not validate_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token"}), 403
    
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    from models import SeasonSupplierSetting
    from app import db
    
    payload = request.get_json(force=True) or {}
    season_code = (payload.get("season_code_365") or "").strip()
    if not season_code:
        return jsonify({"success": False, "error": "Missing season_code_365"}), 400
    
    try:
        s = SeasonSupplierSetting.query.get(season_code)
        if not s:
            s = SeasonSupplierSetting(season_code_365=season_code)
            db.session.add(s)
        
        s.supplier_code = (payload.get("supplier_code") or "").strip() or None
        s.email_to = (payload.get("email_to") or "").strip() or None
        s.email_cc = (payload.get("email_cc") or "").strip() or None
        s.email_comment = (payload.get("email_comment") or "").strip() or None
        
        db.session.commit()
        logger.info(f"Saved settings for season {season_code}: supplier={s.supplier_code}, email_to={s.email_to}")
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error saving season settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@reports_bp.route("/reserved-stock-777/send-po", methods=["POST"])
@login_required
def reserved_stock_777_send_po():
    """Create PO in PS365 for a season and email supplier"""
    from routes import validate_csrf_token
    if not validate_csrf_token():
        return jsonify({"success": False, "error": "Invalid CSRF token"}), 403
    
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({"success": False, "error": "Access denied"}), 403
    
    if not PS365_BASE_URL or not PS365_TOKEN:
        return jsonify({"success": False, "error": "PS365 API not configured"}), 400
    
    from models import SeasonSupplierSetting, Ps365ReservedStock777, DwItem
    from app import db
    
    payload = request.get_json(force=True) or {}
    season_code = (payload.get("season_code_365") or "").strip()
    if not season_code:
        return jsonify({"success": False, "error": "Missing season_code_365"}), 400
    
    setting = SeasonSupplierSetting.query.get(season_code)
    if not setting or not setting.supplier_code:
        return jsonify({"success": False, "error": "Missing supplier_code in settings for this season"}), 400
    if not setting.email_to:
        return jsonify({"success": False, "error": "Missing email_to in settings for this season"}), 400
    
    rows = Ps365ReservedStock777.query.filter_by(season_name=season_code).all()
    
    item_codes = [r.item_code_365 for r in rows]
    dw_map = {}
    if item_codes:
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
    for r in rows:
        stock_val = float(r.stock or 0)
        reserved_val = float(r.stock_reserved or 0)
        ordered_val = float(r.stock_ordered or 0)
        pieces_per_unit = int(r.number_of_pieces or 1)
        min_order_qty = int(r.number_field_5_value or 0)
        shortage = reserved_val - stock_val - ordered_val
        raw_required = int(shortage * pieces_per_unit) if shortage > 0 else 0
        required = max(raw_required, min_order_qty) if raw_required > 0 else 0
        
        if required > 0:
            ps365_qty = math.ceil(required / pieces_per_unit)
            dw = dw_map.get(r.item_code_365)
            ps_price = ps365_pricing.get(r.item_code_365, {})
            line_data = {
                "item_code_365": r.item_code_365,
                "item_name": r.item_name,
                "line_quantity": str(ps365_qty),
                "required_qty": required,
                "pieces_per_unit": pieces_per_unit,
                "barcode": r.barcode or "",
                "supplier_item_code": r.supplier_item_code or "",
            }
            cost = (float(dw.cost_price) if dw and dw.cost_price is not None and float(dw.cost_price) > 0 else None)
            if cost is None:
                ps_cost = ps_price.get("cost_price")
                if ps_cost is not None and ps_cost > 0:
                    cost = ps_cost
            vat = (dw.vat_code_365 if dw and dw.vat_code_365 else None) or ps_price.get("vat_code_365")
            vat_pct = (float(dw.vat_percent) if dw and dw.vat_percent is not None else None) or ps_price.get("vat_percent")
            if cost is not None:
                line_data["cost_price"] = cost
            if vat:
                line_data["vat_code_365"] = vat
            if vat_pct is not None:
                line_data["vat_percent"] = vat_pct
            po_lines.append(line_data)
    
    if not po_lines:
        return jsonify({"success": False, "error": "No items require ordering (all items already on PO)"}), 400
    
    try:
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        deliver_by_utc = (now_utc + timedelta(days=7)).replace(microsecond=0)
        shopping_cart_code = f"WMDS-{now_utc.strftime('%Y%m%d-%H%M%S')}-{setting.supplier_code}"
        
        po_payload = {
            "api_credentials": {"token": PS365_TOKEN},
            "order": {
                "purchase_order_header": {
                    "shopping_cart_code": shopping_cart_code,
                    "order_date_local": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_date_utc0": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "order_date_deliverby_utc0": deliver_by_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "supplier_code_365": setting.supplier_code,
                    "agent_code_365": "",
                    "user_code_365": current_user.username,
                    "comments": f"Auto PO from Cross Shipping Report - Season {season_code} - {len(po_lines)} items",
                    "search_additional_barcodes": False
                },
                "list_purchase_order_details": []
            }
        }
        
        for idx, ln in enumerate(po_lines, start=1):
            price = ln.get("cost_price", 0) or 0
            qty = float(ln["line_quantity"])
            vat_pct = ln.get("vat_percent", 0) or 0
            subtotal = round(price * qty, 2)
            vat_amount = round(subtotal * vat_pct / 100, 2)
            line_detail = {
                "line_number": str(idx),
                "item_code_365": ln["item_code_365"],
                "line_quantity": ln["line_quantity"],
                "line_price_excl_vat": str(price),
                "line_total_sub": str(subtotal),
                "line_total_discount": "0",
                "line_total_vat_percentage": str(vat_pct),
                "line_total_vat": str(vat_amount),
            }
            if "vat_code_365" in ln:
                line_detail["line_vat_code_365"] = ln["vat_code_365"]
            po_payload["order"]["list_purchase_order_details"].append(line_detail)
        
        url = f"{PS365_BASE_URL}/purchaseorder"
        resp = requests.post(url, json=po_payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        
        api_response = result.get("api_response", {})
        if api_response.get("response_code") != "1":
            error_msg = api_response.get("response_message", "Unknown error")
            return jsonify({"success": False, "error": f"PS365 error: {error_msg}"}), 400
        
        po_code = api_response.get("response_id", "Unknown")
        logger.info(f"Created PO {po_code} with {len(po_lines)} items for supplier {setting.supplier_code}")
        
        email_result = send_season_po_email(
            to=setting.email_to,
            cc=setting.email_cc,
            po_code=po_code,
            season_code=season_code,
            lines=po_lines,
            comment=setting.email_comment
        )
        
        if not email_result["success"]:
            logger.warning(f"PO created but email failed: {email_result['error']}")
        
        try:
            from scripts.ps365_reserved_stock_report_777 import build_rows, save_to_db, clear_table_for_store, STORE_CODE
            new_rows = build_rows()
            if new_rows:
                clear_table_for_store(STORE_CODE)
                save_to_db(new_rows)
                logger.info(f"Report refreshed after PO creation: {len(new_rows)} items")
        except Exception as refresh_err:
            logger.warning(f"Failed to refresh report after PO: {refresh_err}")
        
        if not email_result["success"]:
            return jsonify({
                "success": True,
                "po_no": po_code,
                "warning": f"PO created but email failed: {email_result['error']}"
            })
        
        return jsonify({"success": True, "po_no": po_code})
        
    except requests.RequestException as e:
        logger.error(f"PS365 connection error: {e}")
        return jsonify({"success": False, "error": f"Failed to connect to PS365: {str(e)}"}), 500
    except Exception as e:
        logger.error(f"Error in send-po: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def send_season_po_email(to, cc, po_code, season_code, lines, comment=None):
    """Send PO email to supplier"""
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
    SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    
    logger.info(f"Attempting to send PO email: to={to}, cc={cc}, po_code={po_code}")
    
    if not all([SMTP_HOST, SMTP_EMAIL, SMTP_PASSWORD]):
        logger.error(f"SMTP not configured: HOST={bool(SMTP_HOST)}, EMAIL={bool(SMTP_EMAIL)}, PASS={bool(SMTP_PASSWORD)}")
        return {"success": False, "error": "SMTP not configured"}
    
    if not to or not to.strip():
        logger.error("No recipient email address provided")
        return {"success": False, "error": "No recipient email address configured for this supplier"}
    
    try:
        total_qty = sum(ln["required_qty"] for ln in lines)
        
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
                .header {{ background-color: #2196F3; color: white; padding: 20px; }}
                .footer {{ margin-top: 20px; padding: 10px; background-color: #f5f5f5; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>Purchase Order: {po_code}</h2>
                <p>Supplier: {season_code}</p>
            </div>
            
            <table>
                <tr>
                    <th>#</th>
                    <th>Item Code</th>
                    <th>Description</th>
                    <th>Barcode</th>
                    <th>Supplier Item Code</th>
                    <th>Selling Price</th>
                    <th>VAT Code</th>
                    <th>Qty (pcs)</th>
                </tr>
        """
        
        for idx, ln in enumerate(lines, start=1):
            cost_display = f"{ln['cost_price']:.2f}" if ln.get('cost_price') else ""
            vat_display = ln.get('vat_code_365', '') or ""
            html_body += f"""
                <tr>
                    <td>{idx}</td>
                    <td>{ln['item_code_365']}</td>
                    <td>{ln['item_name']}</td>
                    <td>{ln.get('barcode', '')}</td>
                    <td>{ln.get('supplier_item_code', '')}</td>
                    <td>{cost_display}</td>
                    <td>{vat_display}</td>
                    <td>{ln['required_qty']}</td>
                </tr>
            """
        
        html_body += f"""
            </table>
            
            <div class="footer">
                <p><strong>Total Items:</strong> {len(lines)}</p>
                <p><strong>Total Quantity:</strong> {total_qty} pieces</p>
        """
        
        if comment:
            html_body += f"<p><strong>Notes:</strong> {comment}</p>"
        
        html_body += """
            </div>
        </body>
        </html>
        """
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Purchase Order {po_code} - Supplier {season_code}"
        msg["From"] = SMTP_EMAIL
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        
        msg.attach(MIMEText(html_body, "html"))
        
        recipients = [to]
        if cc:
            recipients.extend([c.strip() for c in cc.split(",") if c.strip()])
        
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, recipients, msg.as_string())
        
        logger.info(f"Sent PO email for {po_code} to {to} (cc: {cc})")
        return {"success": True}
        
    except Exception as e:
        logger.error(f"Failed to send PO email: {e}")
        return {"success": False, "error": str(e)}


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
    
    from models import Ps365ReservedStock777, DwItem
    
    supplier_filter = request.form.get("supplier_filter", "")
    supplier_code = request.form.get("supplier_code", "").strip()
    
    if not supplier_code:
        flash("Supplier code is required to create a PO.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    rows = Ps365ReservedStock777.query.all()
    
    all_item_codes = [r.item_code_365 for r in rows]
    dw_map2 = {}
    if all_item_codes:
        dw_items2 = DwItem.query.filter(DwItem.item_code_365.in_(all_item_codes)).all()
        dw_map2 = {d.item_code_365: d for d in dw_items2}
    
    missing_pricing_codes2 = [
        code for code in all_item_codes
        if not dw_map2.get(code)
        or dw_map2[code].cost_price is None
        or not dw_map2[code].vat_code_365
        or dw_map2[code].vat_percent is None
    ]
    ps365_pricing2 = _fetch_item_pricing_from_ps365(missing_pricing_codes2) if missing_pricing_codes2 else {}
    if ps365_pricing2:
        dw_items2 = DwItem.query.filter(DwItem.item_code_365.in_(all_item_codes)).all()
        dw_map2 = {d.item_code_365: d for d in dw_items2}
    
    po_lines = []
    for r in rows:
        if supplier_filter and r.season_name != supplier_filter:
            continue
        
        stock_val = float(r.stock or 0)
        reserved_val = float(r.stock_reserved or 0)
        ordered_val = float(r.stock_ordered or 0)
        pieces_per_unit = int(r.number_of_pieces or 1)
        min_order_qty = int(r.number_field_5_value or 0)
        shortage = reserved_val - stock_val - ordered_val
        raw_required = int(shortage * pieces_per_unit) if shortage > 0 else 0
        required = max(raw_required, min_order_qty) if raw_required > 0 else 0
        
        if required > 0:
            ps365_qty = math.ceil(required / pieces_per_unit)
            dw = dw_map2.get(r.item_code_365)
            ps_price = ps365_pricing2.get(r.item_code_365, {})
            line_data = {
                "item_code_365": r.item_code_365,
                "item_name": r.item_name,
                "line_quantity": str(ps365_qty),
                "required_qty": required,
                "pieces_per_unit": pieces_per_unit,
                "barcode": r.barcode or "",
                "supplier_item_code": r.supplier_item_code or "",
            }
            cost = (float(dw.cost_price) if dw and dw.cost_price is not None and float(dw.cost_price) > 0 else None)
            if cost is None:
                ps_cost = ps_price.get("cost_price")
                if ps_cost is not None and ps_cost > 0:
                    cost = ps_cost
            vat = (dw.vat_code_365 if dw and dw.vat_code_365 else None) or ps_price.get("vat_code_365")
            vat_pct = (float(dw.vat_percent) if dw and dw.vat_percent is not None else None) or ps_price.get("vat_percent")
            if cost is not None:
                line_data["cost_price"] = cost
            if vat:
                line_data["vat_code_365"] = vat
            if vat_pct is not None:
                line_data["vat_percent"] = vat_pct
            po_lines.append(line_data)
    
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
                    "search_additional_barcodes": False,
                    "status": "CONFIRMED"
                },
                "list_purchase_order_details": []
            }
        }
        
        for idx, ln in enumerate(po_lines, start=1):
            price = ln.get("cost_price", 0) or 0
            qty = float(ln["line_quantity"])
            vat_pct = ln.get("vat_percent", 0) or 0
            subtotal = round(price * qty, 2)
            vat_amount = round(subtotal * vat_pct / 100, 2)
            line_detail2 = {
                "line_number": str(idx),
                "item_code_365": ln["item_code_365"],
                "line_quantity": ln["line_quantity"],
                "line_price_excl_vat": str(price),
                "line_total_sub": str(subtotal),
                "line_total_discount": "0",
                "line_total_vat_percentage": str(vat_pct),
                "line_total_vat": str(vat_amount),
            }
            if "vat_code_365" in ln:
                line_detail2["line_vat_code_365"] = ln["vat_code_365"]
            payload["order"]["list_purchase_order_details"].append(line_detail2)
        
        import json as _json
        logger.debug("PO payload lines: %s", _json.dumps(payload["order"]["list_purchase_order_details"][:3], indent=2))
        
        url = f"{PS365_BASE_URL}/purchaseorder"
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        
        api_response = result.get("api_response", {})
        if api_response.get("response_code") == "1":
            po_code = api_response.get("response_id", "Unknown")
            flash(f"Purchase Order created successfully! PO Code: {po_code} ({len(po_lines)} items)", "success")
            logger.info(f"Created PO {po_code} with {len(po_lines)} items for supplier {supplier_code}")
            
            # Try to send email if settings are configured for this season/supplier
            if supplier_filter:
                from models import SeasonSupplierSetting
                setting = SeasonSupplierSetting.query.filter_by(season_code_365=supplier_filter).first()
                if setting and setting.email_to:
                    email_result = send_season_po_email(
                        to=setting.email_to,
                        cc=setting.email_cc,
                        po_code=po_code,
                        season_code=supplier_filter,
                        lines=po_lines,
                        comment=setting.email_comment
                    )
                    if email_result["success"]:
                        flash(f"Email sent to {setting.email_to}", "success")
                    else:
                        flash(f"PO created but email failed: {email_result['error']}", "warning")
                else:
                    flash("PO created but no email configured for this supplier.", "info")
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


@reports_bp.route("/reserved-stock-777/email-order", methods=["POST"])
@login_required
def reserved_stock_777_email_order():
    """Send order via email to supplier"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash("Access denied.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
    SMTP_EMAIL = os.getenv("SMTP_EMAIL", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    
    if not all([SMTP_HOST, SMTP_EMAIL, SMTP_PASSWORD]):
        flash("SMTP not configured. Please set SMTP_HOST, SMTP_EMAIL, and SMTP_PASSWORD.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    from models import Ps365ReservedStock777, InvoiceItem
    from app import db
    from sqlalchemy import func
    
    supplier_filter = request.form.get("supplier_filter", "")
    recipient_email = request.form.get("recipient_email", "").strip()
    
    if not recipient_email:
        flash("Recipient email is required.", "danger")
        return redirect(url_for("reports.reserved_stock_777"))
    
    rows = Ps365ReservedStock777.query.all()
    
    item_codes = [r.item_code_365 for r in rows]
    barcode_query = db.session.query(
        InvoiceItem.item_code,
        func.max(InvoiceItem.barcode).label('barcode')
    ).filter(
        InvoiceItem.item_code.in_(item_codes),
        InvoiceItem.barcode.isnot(None)
    ).group_by(InvoiceItem.item_code).all()
    barcode_map = {bc.item_code: bc.barcode for bc in barcode_query}
    
    order_lines = []
    for r in rows:
        if supplier_filter and r.season_name != supplier_filter:
            continue
        
        stock_val = float(r.stock or 0)
        reserved_val = float(r.stock_reserved or 0)
        pieces_per_unit = int(r.number_of_pieces or 1)
        min_order_qty = int(r.number_field_5_value or 0)
        shortage = reserved_val - stock_val
        raw_required = int(shortage * pieces_per_unit) if shortage > 0 else 0
        required = max(raw_required, min_order_qty) if raw_required > 0 else 0
        
        if required > 0:
            order_lines.append({
                "item_code": r.item_code_365,
                "item_name": r.item_name,
                "required_qty": required,
                "pieces_per_unit": pieces_per_unit,
                "supplier_item_code": r.supplier_item_code or "",
                "barcode": barcode_map.get(r.item_code_365, "")
            })
    
    if not order_lines:
        flash("No items with Required > 0 found for the selected filter.", "warning")
        return redirect(url_for("reports.reserved_stock_777"))
    
    try:
        now = datetime.now()
        subject = f"Purchase Order - {supplier_filter or 'All Suppliers'} - {now.strftime('%Y-%m-%d')}"
        
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4472C4; color: white; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
                .header {{ background-color: #f8f9fa; padding: 20px; border-bottom: 2px solid #4472C4; }}
                .total {{ font-weight: bold; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>Purchase Order Request</h2>
                <p><strong>Date:</strong> {now.strftime('%Y-%m-%d %H:%M')}</p>
                <p><strong>Supplier:</strong> {supplier_filter or 'All Suppliers'}</p>
                <p><strong>Requested by:</strong> {current_user.username}</p>
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Supplier Item Code</th>
                        <th>Barcode</th>
                        <th>Item Name</th>
                        <th>Qty Required</th>
                    </tr>
                </thead>
                <tbody>
        """
        
        for idx, line in enumerate(order_lines, start=1):
            html_content += f"""
                    <tr>
                        <td>{idx}</td>
                        <td>{line['supplier_item_code']}</td>
                        <td>{line['barcode']}</td>
                        <td>{line['item_name']}</td>
                        <td style="text-align: right;"><strong>{line['required_qty']}</strong></td>
                    </tr>
            """
        
        html_content += f"""
                </tbody>
            </table>
            
            <p class="total">Total Items: {len(order_lines)}</p>
            <p class="total">Total Quantity: {sum(line['required_qty'] for line in order_lines)}</p>
            
            <hr>
            <p style="color: #666; font-size: 12px;">This is an automated email from the Warehouse Management System.</p>
        </body>
        </html>
        """
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_EMAIL
        msg["To"] = recipient_email
        
        text_content = f"Purchase Order - {supplier_filter or 'All Suppliers'}\n\n"
        text_content += f"Date: {now.strftime('%Y-%m-%d %H:%M')}\n"
        text_content += f"Requested by: {current_user.username}\n\n"
        text_content += "Items:\n"
        for idx, line in enumerate(order_lines, start=1):
            text_content += f"{idx}. {line['supplier_item_code']} | {line['barcode']} | {line['item_name']} | Qty: {line['required_qty']}\n"
        text_content += f"\nTotal Items: {len(order_lines)}"
        text_content += f"\nTotal Quantity: {sum(line['required_qty'] for line in order_lines)}"
        
        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))
        
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, recipient_email, msg.as_string())
        
        flash(f"Order email sent successfully to {recipient_email} ({len(order_lines)} items)", "success")
        logger.info(f"Sent order email to {recipient_email} with {len(order_lines)} items for supplier {supplier_filter}")
    
    except smtplib.SMTPException as e:
        flash(f"Failed to send email: {str(e)}", "danger")
        logger.error(f"SMTP error: {e}")
    except Exception as e:
        flash(f"Error sending email: {str(e)}", "danger")
        logger.error(f"Email error: {e}")
    
    return redirect(url_for("reports.reserved_stock_777"))
