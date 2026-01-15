import os
import json
import uuid
import requests
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from flask_login import login_required, current_user
from app import db
from models import PurchaseOrder, PurchaseOrderLine, ReceivingSession, ReceivingLine
from sqlalchemy import func, or_
from shelves_service import fetch_item_shelves, Ps365Error
from utils.image_handler import get_product_image

po_receiving_bp = Blueprint('po_receiving', __name__, url_prefix='/po-receiving')

@po_receiving_bp.route('/api/item-image/<item_code>')
@login_required
def api_item_image(item_code):
    """Proxy for item images to use the same logic as picking"""
    image_path = get_product_image(item_code)
    return redirect(url_for('static', filename=image_path))

POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "").rstrip("/")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")
# Hardcode store to 777 due to environment variable caching issue
PS365_DEFAULT_STORE = "777"

def check_role_access():
    """Check if user has access to PO receiving (admin, warehouse_manager, picker)"""
    if current_user.role not in ['admin', 'warehouse_manager', 'picker']:
        return False
    return True

def to_decimal(value):
    """Safely convert value to Decimal"""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except:
        return None

def to_bool(value):
    """Safely convert value to boolean (handles PS365 API responses)"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return False


def send_receiving_to_ps365(session):
    """Send receiving session data to PS365 via order_pick_list API"""
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        raise Exception("PS365 credentials not configured")
    
    po = session.purchase_order
    
    # Build list of order details with quantities and expiry dates
    list_order_details = []
    skipped_lines = []  # Track lines without PS365 IDs
    pick_order_no = 1
    
    # Group receiving lines by PO line
    for po_line in po.lines.order_by(PurchaseOrderLine.line_number).all():
        # Get all receiving lines for this PO line
        rcv_lines = session.lines.filter_by(po_line_id=po_line.id).all()
        
        if not rcv_lines:
            continue  # Skip lines with no receipts
        
        # CRITICAL: Skip lines without PS365 line_id (manually added items)
        if not po_line.line_id_365:
            total_qty = sum(Decimal(str(rcv.qty_received)) for rcv in rcv_lines)
            skipped_lines.append({
                'item_code': po_line.item_code_365,
                'item_name': po_line.item_name,
                'qty_received': float(total_qty),
                'reason': 'No PS365 line ID - item was manually added to PO'
            })
            continue
        
        # Calculate total received quantity
        total_qty = sum(Decimal(str(rcv.qty_received)) for rcv in rcv_lines)
        
        # Build line detail
        line_detail = {
            "line_id_365": po_line.line_id_365,
            "line_quantity": float(total_qty),
            "pick_order_no": pick_order_no
        }
        
        # Add expiry date analysis only if item requires it
        if po_line.item_has_expiration_date:
            expiry_batches = []
            for rcv in rcv_lines:
                if rcv.expiry_date:
                    expiry_str = rcv.expiry_date.strftime('%Y-%m-%d')
                    expiry_batches.append({
                        "expired_date": expiry_str,
                        "display_date": expiry_str,
                        "lot_quantity": float(rcv.qty_received)
                    })
            
            if expiry_batches:
                line_detail["list_lot_expired_date_analysis"] = expiry_batches
        
        list_order_details.append(line_detail)
        pick_order_no += 1
    
    # Build the complete payload
    payload = {
        "api_credentials": {
            "token": POWERSOFT_TOKEN
        },
        "order": {
            "user_code": session.operator or "warehouse_op",
            "order_type": "PurchaseOrder",
            "comment": f"GRN for {po.code_365 or po.shopping_cart_code} - Receipt {session.receipt_code}",
            "list_order_details": list_order_details
        }
    }
    
    # Send to PS365
    url = f"{POWERSOFT_BASE}/order_pick_list"
    try:
        print(f"DEBUG: Sending receiving data to PS365: {url}")
        print(f"DEBUG: Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        print(f"DEBUG: PS365 Response: {json.dumps(result, indent=2)}")
        
        # Check if successful
        api_response = result.get("api_response", {})
        if api_response.get("response_code") == "1":
            # Update PO status to GRN (Goods Received Note) on successful submission
            po.status_code = "GRN"
            po.status_name = "Goods Received"
            db.session.commit()
            print(f"DEBUG: Updated PO {po.code_365} status to GRN")
            
            return {
                'success': True,
                'pick_list_code': api_response.get("response_id"),
                'message': api_response.get("response_msg", "OK"),
                'skipped_lines': skipped_lines,
                'lines_sent': len(list_order_details)
            }
        else:
            return {
                'success': False,
                'error': api_response.get("response_msg", "Unknown error"),
                'skipped_lines': skipped_lines
            }
    
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Failed to send to PS365: {e}")
        return {
            'success': False,
            'error': str(e),
            'skipped_lines': skipped_lines
        }

def fetch_item_barcodes(item_codes):
    """Fetch barcodes for multiple items from PS365"""
    if not item_codes:
        return {}
    
    barcodes = {}
    # Search for items in batches to get their barcodes
    for item_code in item_codes:
        try:
            search_payload = {
                "api_credentials": {"token": POWERSOFT_TOKEN},
                "search_option": {
                    "only_counted": "N",
                    "page_number": 1,
                    "page_size": 1,
                    "expression_searched": item_code,
                    "search_operator_type": "Equals",
                    "search_in_fields": "ItemCode",
                    "active_type": "all"
                }
            }
            url = f"{POWERSOFT_BASE}/search_item"
            r = requests.post(url, json=search_payload, timeout=15)
            r.raise_for_status()
            result = r.json()
            
            print(f"DEBUG: Barcode search response for {item_code}: {json.dumps(result, indent=2)}")
            
            api_resp = result.get("api_response", {})
            if api_resp.get("response_code") == "1":
                items = result.get("list_items", [])
                if items:
                    item = items[0]
                    # Extract barcode from list_item_barcodes array
                    barcode_list = item.get("list_item_barcodes", [])
                    barcode = None
                    
                    # Prefer barcode with is_label_barcode=true
                    for bc_obj in barcode_list:
                        if bc_obj.get("is_label_barcode") == True:
                            barcode = bc_obj.get("barcode")
                            break
                    
                    # Fallback to first barcode if no label barcode found
                    if not barcode and barcode_list:
                        barcode = barcode_list[0].get("barcode")
                    
                    if barcode and barcode != item_code:  # Don't use item code as barcode
                        barcodes[item_code] = barcode
                        print(f"DEBUG: Found barcode for {item_code}: {barcode}")
                    else:
                        print(f"DEBUG: No valid barcode found for {item_code}")
        except Exception as e:
            print(f"WARNING: Failed to fetch barcode for {item_code}: {e}")
            continue
    
    return barcodes

def fetch_purchase_order_from_ps365(po_code, is_shopping_cart):
    """Fetch purchase order from Powersoft365 API"""
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        raise RuntimeError("Powersoft365 credentials not configured")
    
    params = {
        "token": POWERSOFT_TOKEN,
        "purchase_order_code": po_code,
        "is_shopping_cart_code": "Y" if is_shopping_cart else "N",
    }
    
    url = f"{POWERSOFT_BASE}/purchaseorder?{urlencode(params)}"
    
    try:
        # Increased timeout for large POs: (connect_timeout, read_timeout)
        r = requests.get(url, timeout=(10, 90))
        r.raise_for_status()
        data = r.json()
        
        print(f"DEBUG: PS365 API Response: {json.dumps(data, indent=2)}")
        
        api_resp = data.get("api_response", {})
        if api_resp.get("response_code") != "1":
            error_msg = api_resp.get('response_msg', 'Unknown error')
            print(f"DEBUG: PS365 API Error - Code: {api_resp.get('response_code')}, Message: {error_msg}")
            raise RuntimeError(f"PS365 Error: {error_msg}")
        
        order = data.get("order")
        if not order:
            raise RuntimeError("No order data returned from PS365")
        
        return order
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to connect to PS365: {str(e)}")

def upsert_purchase_order(order_data, username, force_reimport=False):
    """Save or update purchase order in database
    
    Args:
        order_data: Purchase order data from PS365
        username: User performing the import
        force_reimport: If True, delete all existing data including receiving sessions
    
    Returns:
        tuple: (po, had_receiving_data) - The PurchaseOrder object and flag indicating if it had receiving data
    """
    hdr = order_data.get("purchase_order_header", {})
    lines = order_data.get("list_purchase_order_details", []) or []
    
    code_365 = hdr.get("purchase_order_code_365")
    sc_code = hdr.get("shopping_cart_code")
    
    # Find existing order
    query = PurchaseOrder.query
    if code_365:
        query = query.filter_by(code_365=code_365)
    elif sc_code:
        query = query.filter_by(shopping_cart_code=sc_code)
    else:
        raise RuntimeError("No valid purchase order code found")
    
    existing = query.first()
    had_receiving_data = False
    
    if existing:
        # Check if there are any receiving sessions for this PO
        has_sessions = ReceivingSession.query.filter_by(purchase_order_id=existing.id).first() is not None
        
        if has_sessions and not force_reimport:
            # Return flag indicating this PO has receiving data
            return existing, True
        
        had_receiving_data = has_sessions
        po = existing
        
        # Delete all receiving sessions (use ORM delete to trigger cascade properly)
        sessions_to_delete = ReceivingSession.query.filter_by(purchase_order_id=po.id).all()
        for session in sessions_to_delete:
            db.session.delete(session)
        db.session.flush()  # Ensure sessions are deleted before proceeding
        
        # Clear existing lines to re-sync
        for line in po.lines:
            db.session.delete(line)
    else:
        po = PurchaseOrder()
    
    # Update header fields
    po.code_365 = code_365
    po.shopping_cart_code = sc_code
    po.supplier_code = hdr.get("supplier_code_365")
    po.supplier_name = hdr.get("supplier_name") or (f"Supplier {hdr.get('supplier_code_365')}" if hdr.get("supplier_code_365") else None)
    po.status_code = hdr.get("order_status_code_365")
    po.status_name = hdr.get("order_status_name")
    po.order_date_local = hdr.get("order_date_local")
    po.order_date_utc0 = hdr.get("order_date_utc0")
    po.comments = hdr.get("comments")
    po.total_sub = to_decimal(hdr.get("total_sub"))
    po.total_discount = to_decimal(hdr.get("total_discount"))
    po.total_vat = to_decimal(hdr.get("total_vat"))
    po.total_grand = to_decimal(hdr.get("total_grand"))
    po.downloaded_by = username
    
    db.session.add(po)
    db.session.flush()
    
    # Fetch shelf locations and barcodes for all items in this PO
    item_codes = [ln.get("item_code_365") for ln in lines if ln.get("item_code_365")]
    shelves_map = {}
    barcodes_map = {}
    if item_codes and PS365_DEFAULT_STORE:
        try:
            print(f"DEBUG: Fetching shelf locations for {len(item_codes)} items from store {PS365_DEFAULT_STORE}")
            shelves_map = fetch_item_shelves(PS365_DEFAULT_STORE, item_codes)
            print(f"DEBUG: Received shelf data for {len(shelves_map)} items")
        except Ps365Error as e:
            print(f"WARNING: Failed to fetch shelf locations: {e}")
        except Exception as e:
            print(f"WARNING: Unexpected error fetching shelf locations: {e}")
    
    # Fetch barcodes for all items
    if item_codes and POWERSOFT_BASE and POWERSOFT_TOKEN:
        try:
            print(f"DEBUG: Fetching barcodes for {len(item_codes)} items")
            barcodes_map = fetch_item_barcodes(item_codes)
            print(f"DEBUG: Received barcode data for {len(barcodes_map)} items")
        except Exception as e:
            print(f"WARNING: Failed to fetch barcodes: {e}")
    
    # Add lines with shelf location data, barcodes, and tracking requirements
    for ln in lines:
        item_code = ln.get("item_code_365") or ""
        shelf_data = shelves_map.get(item_code, [])
        item_barcode = barcodes_map.get(item_code)
        
        # Try to get unit information from DW if missing in PO line
        unit_type = ln.get("unit_type")
        pieces_per_unit = ln.get("number_of_pieces")
        item_name = ln.get("item_name") or ""
        
        # Priority 1: Smart fallback from item_name (e.g. "1X24" -> 24)
        if (not pieces_per_unit or int(pieces_per_unit) <= 1) and item_name:
            import re
            match = re.search(r'1X(\d+)', item_name, re.IGNORECASE)
            if match:
                pieces_per_unit = int(match.group(1))
                if not unit_type:
                    unit_type = "BOX"

        # Priority 2: Get from ps_items_dw if still missing
        if (not unit_type or not pieces_per_unit) and item_code:
            from models import DwItem
            dw_item = DwItem.query.get(item_code)
            if dw_item:
                # If pieces_per_unit is still 1 or None, check DW
                if not pieces_per_unit or int(pieces_per_unit) <= 1:
                    if dw_item.number_of_pieces and int(dw_item.number_of_pieces) > 1:
                        pieces_per_unit = dw_item.number_of_pieces
                        if not unit_type:
                            unit_type = "CASE"
                
                # Check for unit_type in DW or related fields if available
                # (Assuming DwItem might have unit-related attributes in the future or mapping pieces to type)
                if not unit_type and pieces_per_unit and int(pieces_per_unit) > 1:
                    unit_type = "CASE"

        pol = PurchaseOrderLine(
            purchase_order_id=po.id,
            line_number=int(ln.get("line_number", 0)),
            line_id_365=ln.get("line_id_365"),  # PS365 unique line identifier
            item_code_365=item_code,
            item_name=ln.get("item_name"),
            item_barcode=item_barcode,  # Barcode from PS365
            line_quantity=to_decimal(ln.get("line_quantity")),
            line_price_excl_vat=to_decimal(ln.get("line_price_excl_vat")),
            line_total_sub=to_decimal(ln.get("line_total_sub")),
            line_total_discount=to_decimal(ln.get("line_total_discount")),
            line_total_discount_percentage=to_decimal(ln.get("line_total_discount_percentage")),
            line_vat_code_365=ln.get("line_vat_code_365"),
            line_total_vat=to_decimal(ln.get("line_total_vat")),
            line_total_vat_percentage=to_decimal(ln.get("line_total_vat_percentage")),
            line_total_grand=to_decimal(ln.get("line_total_grand")),
            # Tracking requirements from PS365 (use to_bool to handle string/bool/int values)
            item_has_expiration_date=to_bool(ln.get("item_has_expiration_date")),
            item_has_lot_number=to_bool(ln.get("item_has_lot_number")),
            item_has_serial_number=to_bool(ln.get("item_has_serial_number")),
            shelf_locations=json.dumps(shelf_data) if shelf_data else None,
            unit_type=unit_type,
            pieces_per_unit=int(pieces_per_unit or 1) if pieces_per_unit else None,
        )
        db.session.add(pol)
    
    db.session.commit()
    return po, had_receiving_data

@po_receiving_bp.route('/')
@login_required
def index():
    """List all active (non-archived) purchase orders"""
    if not check_role_access():
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    # Get all non-archived purchase orders with receiving progress
    orders = PurchaseOrder.query.filter_by(is_archived=False).order_by(PurchaseOrder.downloaded_at.desc()).all()
    
    # Calculate receiving progress for each order
    order_data = []
    for po in orders:
        total_ordered = db.session.query(
            func.sum(PurchaseOrderLine.line_quantity)
        ).filter_by(purchase_order_id=po.id).scalar() or Decimal('0')
        
        total_received = db.session.query(
            func.sum(ReceivingLine.qty_received)
        ).join(ReceivingSession).filter(
            ReceivingSession.purchase_order_id == po.id
        ).scalar() or Decimal('0')
        
        # Check if there's an open session
        open_session = ReceivingSession.query.filter_by(
            purchase_order_id=po.id,
            finished_at=None
        ).order_by(ReceivingSession.started_at.desc()).first()
        
        order_data.append({
            'po': po,
            'total_ordered': total_ordered,
            'total_received': total_received,
            'open_session': open_session,
            'is_complete': total_received >= total_ordered if total_ordered > 0 else False
        })
    
    return render_template('po_receiving/index.html', order_data=order_data)

@po_receiving_bp.route('/download', methods=['GET', 'POST'])
@login_required
def download():
    """Download a purchase order from PS365"""
    if not check_role_access():
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        po_code = request.form.get('po_code', '').strip()
        is_cart = request.form.get('is_shopping_cart', 'Y') == 'Y'
        force_reimport = request.form.get('force_reimport') == 'yes'
        
        if not po_code:
            flash('Please enter a purchase order code', 'error')
            return render_template('po_receiving/download.html')
        
        # If user enters only 5 digits, auto-prefix with "PO100"
        if po_code.isdigit() and len(po_code) == 5:
            po_code = f"PO100{po_code}"
        
        try:
            order_data = fetch_purchase_order_from_ps365(po_code, is_cart)
            po, had_receiving_data = upsert_purchase_order(order_data, current_user.username, force_reimport=force_reimport)
            
            # If PO has receiving data and user hasn't confirmed deletion yet
            if had_receiving_data and not force_reimport:
                # Count how many items have been received
                total_received = db.session.query(
                    func.sum(ReceivingLine.qty_received)
                ).join(ReceivingSession).filter(
                    ReceivingSession.purchase_order_id == po.id
                ).scalar() or Decimal('0')
                
                # Show confirmation page
                return render_template('po_receiving/confirm_reimport.html', 
                                     po=po, 
                                     po_code=po_code,
                                     is_cart=is_cart,
                                     total_received=total_received)
            
            # Successfully imported (new PO or re-imported after confirmation)
            if force_reimport:
                flash(f'Purchase order {po_code} re-imported successfully. All previous receiving data has been deleted.', 'warning')
            else:
                flash(f'Purchase order {po_code} downloaded successfully', 'success')
            
            return redirect(url_for('po_receiving.receive', po_id=po.id))
        except Exception as e:
            flash(f'Error downloading order: {str(e)}', 'error')
            return render_template('po_receiving/download.html', error=str(e))
    
    return render_template('po_receiving/download.html')

@po_receiving_bp.route('/receive/<int:po_id>')
@login_required
def receive(po_id):
    """Receive items for a purchase order"""
    if not check_role_access():
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    po = PurchaseOrder.query.get_or_404(po_id)
    
    # Get or create open receiving session
    session = ReceivingSession.query.filter_by(
        purchase_order_id=po.id,
        finished_at=None
    ).order_by(ReceivingSession.started_at.desc()).first()
    
    if not session:
        receipt_code = f"RCV-{uuid.uuid4().hex[:8].upper()}"
        session = ReceivingSession(
            purchase_order_id=po.id,
            receipt_code=receipt_code,
            operator=current_user.username
        )
        db.session.add(session)
        db.session.commit()
    
    # Calculate received quantities per line
    received_by_line = {}
    for line in po.lines:
        total_received = db.session.query(
            func.sum(ReceivingLine.qty_received)
        ).filter_by(
            session_id=session.id,
            po_line_id=line.id
        ).scalar() or Decimal('0')
        received_by_line[line.id] = total_received
    
    # Get receiving notes from settings
    from models import Setting
    default_receiving_notes = "Wrong Barcode\nBarcode not in system\nNew Product\nRepacking\nNeeds Labels"
    receiving_notes_raw = Setting.get(db.session, 'receiving_notes', default_receiving_notes)
    receiving_notes = [note.strip() for note in receiving_notes_raw.split('\n') if note.strip()]
    
    return render_template(
        'po_receiving/receive.html',
        po=po,
        session=session,
        received_by_line=received_by_line,
        receiving_notes=receiving_notes
    )

@po_receiving_bp.route('/api/update-description/<int:po_id>', methods=['POST'])
@login_required
def api_update_po_description(po_id):
    """Update purchase order description via AJAX"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    po = PurchaseOrder.query.get_or_404(po_id)
    data = request.get_json()
    new_desc = data.get('description', '').strip()
    
    try:
        po.description = new_desc
        db.session.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'ok': False, 'error': str(e)}), 500

@po_receiving_bp.route('/print/<int:po_id>')
@login_required
def print_po(po_id):
    """Print purchase order with barcodes, stock levels, and receiving data"""
    if not check_role_access():
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    po = PurchaseOrder.query.get_or_404(po_id)
    
    # Get all lines with their barcodes, stock, and receiving data
    lines_with_data = []
    for line in po.lines:
        # Use the stored barcode from database (already fetched from PS365 during import)
        barcode = line.item_barcode
        
        # Get stock quantity from shelf locations
        stock_qty = None
        if line.shelf_locations:
            try:
                shelf_data = json.loads(line.shelf_locations)
                # Sum stock from all shelf locations
                total_stock = sum(float(s.get('stock', 0)) for s in shelf_data)
                stock_qty = total_stock if total_stock > 0 else None
            except Exception as e:
                print(f"Warning: Could not parse shelf locations for {line.item_code_365}: {e}")
        
        # Calculate total received for this line across all sessions
        total_received = db.session.query(
            func.sum(ReceivingLine.qty_received)
        ).join(ReceivingSession).filter(
            ReceivingSession.purchase_order_id == po.id,
            ReceivingLine.po_line_id == line.id
        ).scalar() or Decimal('0')
        
        # Get expiration dates and notes from receiving lines
        receiving_lines = ReceivingLine.query.join(ReceivingSession).filter(
            ReceivingSession.purchase_order_id == po.id,
            ReceivingLine.po_line_id == line.id
        ).order_by(ReceivingLine.received_at.desc()).all()
        
        expiry_dates = []
        notes = []
        for rcv_line in receiving_lines:
            if rcv_line.expiry_date:
                expiry_dates.append({
                    'date': rcv_line.expiry_date.strftime('%Y-%m-%d'),
                    'qty': float(rcv_line.qty_received)
                })
            if rcv_line.lot_note:
                notes.append(rcv_line.lot_note)
        
        # Parse shelf locations
        shelf_locations = []
        if line.shelf_locations:
            try:
                shelf_data = json.loads(line.shelf_locations)
                # Use shelf_name (e.g., "31-05-A02") which is more readable than shelf_code_365
                shelf_locations = [s.get('shelf_name', s.get('shelf_code_365', '')) for s in shelf_data if s.get('shelf_name') or s.get('shelf_code_365')]
            except Exception as e:
                print(f"Error parsing shelf locations for {line.item_code_365}: {e}")
        
        lines_with_data.append({
            'line': line,
            'barcode': barcode,
            'stock_qty': stock_qty,
            'total_received': float(total_received),
            'shelf_locations': shelf_locations,
            'expiry_dates': expiry_dates,
            'notes': list(set(notes)),  # Unique notes
            'primary_shelf': shelf_locations[0] if shelf_locations else 'ZZZZ'  # For sorting
        })
    
    # Sort by shelf location (items without shelf go to the end)
    lines_with_data.sort(key=lambda x: x['primary_shelf'])
    
    return render_template(
        'po_receiving/print.html',
        po=po,
        lines_with_data=lines_with_data
    )

@po_receiving_bp.route('/api/lookup-barcode', methods=['POST'])
@login_required
def api_lookup_barcode():
    """Lookup item code by barcode via PS365 API"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    
    if not barcode:
        return jsonify({'ok': False, 'error': 'No barcode provided'}), 400
    
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        return jsonify({'ok': False, 'error': 'PS365 not configured'}), 500
    
    try:
        # Search PS365 for item by barcode
        search_payload = {
            "api_credentials": {
                "token": POWERSOFT_TOKEN
            },
            "search_option": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 10,
                "expression_searched": barcode,
                "search_operator_type": "Equals",
                "search_in_fields": "ItemBarcode,ItemCode",
                "active_type": "all"
            }
        }
        
        url = f"{POWERSOFT_BASE}/search_item"
        r = requests.post(url, json=search_payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        
        api_resp = result.get("api_response", {})
        if api_resp.get("response_code") != "1":
            return jsonify({
                'ok': False, 
                'error': f"PS365 Error: {api_resp.get('response_msg', 'Unknown error')}"
            }), 400
        
        items = result.get("list_items", [])
        if not items:
            return jsonify({'ok': False, 'error': 'No item found for this barcode'}), 404
        
        # Return first match
        first_item = items[0]
        return jsonify({
            'ok': True,
            'item_code_365': first_item.get('item_code_365'),
            'item_name': first_item.get('item_name'),
            'barcode': barcode
        })
        
    except requests.exceptions.RequestException as e:
        return jsonify({'ok': False, 'error': f'PS365 connection error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

@po_receiving_bp.route('/api/add-po-line', methods=['POST'])
@login_required
def api_add_po_line():
    """Add a new line to the purchase order (for items not originally on the order)"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    po_id = data.get('po_id')
    item_code_365 = data.get('item_code_365', '').strip()
    item_name = data.get('item_name', '').strip()
    
    if not po_id or not item_code_365:
        return jsonify({'ok': False, 'error': 'Missing required fields'}), 400
    
    po = PurchaseOrder.query.get(po_id)
    if not po:
        return jsonify({'ok': False, 'error': 'Purchase order not found'}), 404
    
    # Check if line already exists
    existing = PurchaseOrderLine.query.filter_by(
        purchase_order_id=po.id,
        item_code_365=item_code_365
    ).first()
    
    if existing:
        # Parse existing shelf locations
        shelf_data = []
        if existing.shelf_locations:
            try:
                shelf_data = json.loads(existing.shelf_locations)
            except (ValueError, TypeError):
                shelf_data = []
        
        return jsonify({
            'ok': True,
            'line': {
                'id': existing.id,
                'line_number': existing.line_number,
                'item_code_365': existing.item_code_365,
                'item_name': existing.item_name,
                'line_quantity': float(existing.line_quantity or 0),
                'item_has_expiration_date': existing.item_has_expiration_date,
                'item_has_lot_number': existing.item_has_lot_number,
                'item_has_serial_number': existing.item_has_serial_number,
                'shelf_locations': shelf_data
            },
            'already_exists': True
        })
    
    # Get next line number
    max_line = db.session.query(
        func.max(PurchaseOrderLine.line_number)
    ).filter_by(purchase_order_id=po.id).scalar() or 0
    
    # Fetch shelf location for this item
    shelf_data = []
    if PS365_DEFAULT_STORE:
        try:
            print(f"DEBUG: Fetching shelf location for new item {item_code_365}")
            shelves_map = fetch_item_shelves(PS365_DEFAULT_STORE, [item_code_365])
            shelf_data = shelves_map.get(item_code_365, [])
            print(f"DEBUG: Found {len(shelf_data)} shelf locations for {item_code_365}")
        except Ps365Error as e:
            print(f"WARNING: Failed to fetch shelf location for {item_code_365}: {e}")
        except Exception as e:
            print(f"WARNING: Unexpected error fetching shelf location: {e}")
    
    # Create new line with ordered quantity = 0
    new_line = PurchaseOrderLine(
        purchase_order_id=po.id,
        line_number=max_line + 1,
        item_code_365=item_code_365,
        item_name=item_name or item_code_365,
        line_quantity=Decimal('0'),
        line_price_excl_vat=Decimal('0'),
        line_total_sub=Decimal('0'),
        line_total_discount=Decimal('0'),
        line_total_discount_percentage=Decimal('0'),
        line_vat_code_365='',
        line_total_vat=Decimal('0'),
        line_total_vat_percentage=Decimal('0'),
        line_total_grand=Decimal('0'),
        shelf_locations=json.dumps(shelf_data) if shelf_data else None
    )
    db.session.add(new_line)
    db.session.commit()
    
    return jsonify({
        'ok': True,
        'line': {
            'id': new_line.id,
            'line_number': new_line.line_number,
            'item_code_365': new_line.item_code_365,
            'item_name': new_line.item_name,
            'line_quantity': float(new_line.line_quantity),
            'item_has_expiration_date': new_line.item_has_expiration_date,
            'item_has_lot_number': new_line.item_has_lot_number,
            'item_has_serial_number': new_line.item_has_serial_number,
            'shelf_locations': shelf_data
        },
        'already_exists': False
    })

@po_receiving_bp.route('/api/add-lot', methods=['POST'])
@login_required
def api_add_lot():
    """Add a received lot"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    session_id = data.get('session_id')
    po_line_id = data.get('po_line_id')
    qty_received = data.get('qty_received')
    expiry_date = data.get('expiry_date')
    lot_note = data.get('lot_note', '')
    barcode_scanned = data.get('barcode_scanned', '')
    
    # Validate session
    session = ReceivingSession.query.get(session_id)
    if not session or session.finished_at is not None:
        return jsonify({'ok': False, 'error': 'Invalid or closed session'}), 400
    
    # Validate PO line
    po_line = PurchaseOrderLine.query.get(po_line_id)
    if not po_line or po_line.purchase_order_id != session.purchase_order_id:
        return jsonify({'ok': False, 'error': 'Invalid purchase order line'}), 400
    
    # Check if this line has already been fully received
    total_already_received = db.session.query(func.sum(ReceivingLine.qty_received))\
        .filter(
            ReceivingLine.session_id == session.id,
            ReceivingLine.po_line_id == po_line.id
        ).scalar() or Decimal('0')
    
    # Block receiving if already fully received (unless line_quantity is 0, which means dynamically added)
    if po_line.line_quantity > 0 and total_already_received >= po_line.line_quantity:
        return jsonify({
            'ok': False, 
            'error': f'This line has already been fully received ({total_already_received}/{po_line.line_quantity} units). Use the reset button to receive again.'
        }), 400
    
    # Validate quantity
    try:
        qty = Decimal(str(qty_received))
        if qty <= 0:
            return jsonify({'ok': False, 'error': 'Quantity must be greater than 0'}), 400
    except:
        return jsonify({'ok': False, 'error': 'Invalid quantity'}), 400
    
    # Validate expiration date based on item requirements
    expiry_dt = None
    if po_line.item_has_expiration_date:
        # Item requires expiration date
        if not expiry_date:
            return jsonify({'ok': False, 'error': 'Expiration date is required for this item'}), 400
        try:
            expiry_dt = datetime.strptime(expiry_date, '%Y-%m-%d').date()
        except:
            return jsonify({'ok': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    else:
        # Item does NOT require expiration date - ignore if provided
        if expiry_date:
            # Optionally parse it if provided, but don't require it
            try:
                expiry_dt = datetime.strptime(expiry_date, '%Y-%m-%d').date()
            except:
                # Silently ignore invalid dates for items that don't require them
                expiry_dt = None
    
    # Create receiving line
    rcv_line = ReceivingLine(
        session_id=session.id,
        po_line_id=po_line.id,
        barcode_scanned=barcode_scanned or None,
        item_code_365=po_line.item_code_365,
        qty_received=qty,
        expiry_date=expiry_dt,
        lot_note=lot_note or None
    )
    db.session.add(rcv_line)
    db.session.commit()
    
    return jsonify({'ok': True, 'id': rcv_line.id})

@po_receiving_bp.route('/api/get-received-quantities', methods=['POST'])
@login_required
def api_get_received_quantities():
    """Get updated received quantities for all PO lines in a session"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    session_id = data.get('session_id')
    
    session = ReceivingSession.query.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    po = session.purchase_order
    
    # Calculate received quantities by line
    received_by_line = {}
    for line in po.lines.all():
        total_received = db.session.query(func.sum(ReceivingLine.qty_received))\
            .filter(
                ReceivingLine.session_id == session.id,
                ReceivingLine.po_line_id == line.id
            ).scalar() or Decimal('0')
        
        # Get all receiving lines for this PO line to show expiry dates and allow reset
        receiving_lines = ReceivingLine.query.filter_by(
            session_id=session.id,
            po_line_id=line.id
        ).order_by(ReceivingLine.received_at.desc()).all()
        
        lots = []
        for rcv_line in receiving_lines:
            lots.append({
                'id': rcv_line.id,
                'qty': float(rcv_line.qty_received),
                'expiry_date': rcv_line.expiry_date.strftime('%Y-%m-%d') if rcv_line.expiry_date else None,
                'lot_note': rcv_line.lot_note,
                'received_at': rcv_line.received_at.strftime('%Y-%m-%d %H:%M')
            })
        
        received_by_line[line.id] = {
            'received': float(total_received),
            'ordered': float(line.line_quantity),
            'is_fully_received': total_received >= line.line_quantity and total_received > 0 and line.line_quantity > 0,
            'lots': lots
        }
    
    return jsonify({'ok': True, 'received_by_line': received_by_line})

@po_receiving_bp.route('/api/send-to-ps365', methods=['POST'])
@login_required
def api_send_to_ps365():
    """Send receiving session data to PS365 (without finishing session)"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'ok': False, 'error': 'Invalid request'}), 400
    
    session_id = data.get('session_id')
    if not session_id:
        return jsonify({'ok': False, 'error': 'Session ID required'}), 400
    
    session = ReceivingSession.query.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    # Send to PS365
    try:
        ps365_result = send_receiving_to_ps365(session)
        return jsonify({'ok': True, 'ps365': ps365_result})
    except Exception as e:
        print(f"ERROR: Failed to send to PS365: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500

@po_receiving_bp.route('/api/finish-session', methods=['POST'])
@login_required
def api_finish_session():
    """Finish a receiving session and export data"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    data = request.get_json()
    session_id = data.get('session_id')
    
    session = ReceivingSession.query.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': 'Session not found'}), 404
    
    if session.finished_at:
        # Already finished, return export
        return jsonify({'ok': True, 'export': build_export(session)})
    
    # Send to PS365 before marking as finished
    ps365_result = None
    try:
        ps365_result = send_receiving_to_ps365(session)
    except Exception as e:
        print(f"WARNING: Failed to send to PS365: {e}")
        ps365_result = {'success': False, 'error': str(e)}
    
    # Mark session as finished
    session.finished_at = datetime.utcnow()
    db.session.commit()
    
    # Build response with export and PS365 result
    export_data = build_export(session)
    export_data['ps365_submission'] = ps365_result
    
    return jsonify({'ok': True, 'export': export_data})

def build_export(session):
    """Build JSON export of receiving session"""
    po = session.purchase_order
    lines = []
    
    for po_line in po.lines:
        lots = []
        for rcv_line in session.lines.filter_by(po_line_id=po_line.id).all():
            lots.append({
                'barcode_scanned': rcv_line.barcode_scanned,
                'item_code_365': rcv_line.item_code_365,
                'qty_received': float(rcv_line.qty_received),
                'expiry_date': rcv_line.expiry_date.isoformat() if rcv_line.expiry_date else None,
                'lot_note': rcv_line.lot_note
            })
        
        if lots:
            lines.append({
                'line_number': po_line.line_number,
                'item_code_365': po_line.item_code_365,
                'item_name': po_line.item_name,
                'ordered_qty': float(po_line.line_quantity) if po_line.line_quantity else 0,
                'lots': lots
            })
    
    export = {
        'receipt_code': session.receipt_code,
        'operator': session.operator,
        'order': {
            'purchase_order_code_365': po.code_365,
            'shopping_cart_code': po.shopping_cart_code,
            'supplier_code_365': po.supplier_code,
            'status': {'code': po.status_code, 'name': po.status_name},
            'order_date_local': po.order_date_local,
            'comments': po.comments
        },
        'started_at': session.started_at.isoformat() + 'Z',
        'finished_at': session.finished_at.isoformat() + 'Z' if session.finished_at else None,
        'lines': lines
    }
    
    return export

@po_receiving_bp.route('/archive/<int:po_id>', methods=['POST'])
@login_required
def archive_po(po_id):
    """Archive a purchase order"""
    if not check_role_access():
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    po = PurchaseOrder.query.get_or_404(po_id)
    
    # Archive the PO
    po.is_archived = True
    po.archived_at = datetime.utcnow()
    po.archived_by = current_user.username
    db.session.commit()
    
    flash(f'Purchase order {po.code_365 or po.shopping_cart_code} archived successfully', 'success')
    return jsonify({'success': True})

@po_receiving_bp.route('/unarchive/<int:po_id>', methods=['POST'])
@login_required
def unarchive_po(po_id):
    """Unarchive a purchase order"""
    if not check_role_access():
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    po = PurchaseOrder.query.get_or_404(po_id)
    
    # Unarchive the PO
    po.is_archived = False
    po.archived_at = None
    po.archived_by = None
    db.session.commit()
    
    flash(f'Purchase order {po.code_365 or po.shopping_cart_code} restored from archive', 'success')
    return jsonify({'success': True})

@po_receiving_bp.route('/archived')
@login_required
def archived():
    """List all archived purchase orders"""
    if not check_role_access():
        flash('Access denied', 'error')
        return redirect(url_for('dashboard'))
    
    # Get all archived purchase orders
    orders = PurchaseOrder.query.filter_by(is_archived=True).order_by(PurchaseOrder.archived_at.desc()).all()
    
    # Calculate receiving progress for each order
    order_data = []
    for po in orders:
        total_ordered = db.session.query(
            func.sum(PurchaseOrderLine.line_quantity)
        ).filter_by(purchase_order_id=po.id).scalar() or Decimal('0')
        
        total_received = db.session.query(
            func.sum(ReceivingLine.qty_received)
        ).join(ReceivingSession).filter(
            ReceivingSession.purchase_order_id == po.id
        ).scalar() or Decimal('0')
        
        order_data.append({
            'po': po,
            'total_ordered': total_ordered,
            'total_received': total_received,
            'is_complete': total_received >= total_ordered if total_ordered > 0 else False
        })
    
    return render_template('po_receiving/archived.html', order_data=order_data)

@po_receiving_bp.route('/api/reset-receiving-line/<int:line_id>', methods=['POST'])
@login_required
def api_reset_receiving_line(line_id):
    """Delete a receiving line to reset/undo a received item"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    # Get the receiving line
    rcv_line = ReceivingLine.query.get(line_id)
    if not rcv_line:
        return jsonify({'ok': False, 'error': 'Receiving line not found'}), 404
    
    # Verify the session hasn't been finished yet
    session = rcv_line.session
    if session.finished_at:
        return jsonify({'ok': False, 'error': 'Cannot reset - session already finished'}), 400
    
    # Delete the receiving line
    db.session.delete(rcv_line)
    db.session.commit()
    
    return jsonify({'ok': True, 'message': 'Receiving line reset successfully'})

@po_receiving_bp.route('/update-description/<int:po_id>', methods=['POST'])
@login_required
def update_description(po_id):
    """Update the description for a purchase order"""
    if not check_role_access():
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    
    po = PurchaseOrder.query.get_or_404(po_id)
    
    data = request.get_json()
    description = data.get('description', '').strip()
    
    # Update the description
    po.description = description if description else None
    db.session.commit()
    
    return jsonify({'success': True, 'description': po.description or ''})

@po_receiving_bp.route('/api/refresh-shelf-locations/<int:po_id>', methods=['POST'])
@login_required
def api_refresh_shelf_locations(po_id):
    """Refresh shelf locations and stock levels for all items in a PO"""
    if not check_role_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    
    po = PurchaseOrder.query.get_or_404(po_id)
    
    # Get all item codes from this PO
    item_codes = [line.item_code_365 for line in po.lines if line.item_code_365]
    
    if not item_codes:
        return jsonify({'ok': False, 'error': 'No items found in purchase order'}), 400
    
    if not PS365_DEFAULT_STORE:
        return jsonify({'ok': False, 'error': 'PS365 store not configured'}), 500
    
    try:
        # Fetch fresh shelf location data from PS365
        print(f"DEBUG: Refreshing shelf locations for {len(item_codes)} items from store {PS365_DEFAULT_STORE}")
        shelves_map = fetch_item_shelves(PS365_DEFAULT_STORE, item_codes)
        print(f"DEBUG: Received shelf data for {len(shelves_map)} items")
        
        # Update each line with fresh shelf data
        updated_count = 0
        for line in po.lines:
            if line.item_code_365 in shelves_map:
                shelf_data = shelves_map[line.item_code_365]
                line.shelf_locations = json.dumps(shelf_data) if shelf_data else None
                updated_count += 1
        
        db.session.commit()
        
        return jsonify({
            'ok': True, 
            'message': f'Refreshed shelf locations and stock for {updated_count} items',
            'updated_count': updated_count
        })
        
    except Ps365Error as e:
        return jsonify({'ok': False, 'error': f'PS365 Error: {str(e)}'}), 500
    except Exception as e:
        print(f"ERROR: Failed to refresh shelf locations: {e}")
        return jsonify({'ok': False, 'error': f'Failed to refresh: {str(e)}'}), 500
