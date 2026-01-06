"""
Flask blueprint for Item Scanner
Standalone barcode scanner to view item information, location, and stock
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
import requests
import os
import re
from shelves_service import fetch_item_shelves
from app import db
from models import StockPosition

item_scanner_bp = Blueprint('item_scanner', __name__, url_prefix='/item-scanner')

POWERSOFT_BASE = os.environ.get('POWERSOFT_BASE')
POWERSOFT_TOKEN = os.environ.get('POWERSOFT_TOKEN')
# Use hardcoded store 777 like other parts of the system
PS365_DEFAULT_STORE = "777"

SHELF_REGEX = re.compile(
    r"""^
        (?P<zone>\d{2})
        [-\s]?
        (?P<aisle>\d{2})
        [-\s]?
        (?P<section>[A-Za-z])
        (?P<slot>\d{2})
    $""",
    re.VERBOSE
)

def parse_and_normalize_shelf_code(raw):
    """
    Accept formats like: 10-01-c03, 10 01 C03, 1001c03.
    Returns:
      - normalized_for_api: '1001C03'
      - display_format:     '10-01-C03'
    Raises ValueError if invalid.
    """
    if not raw:
        raise ValueError("Empty shelf code.")

    code = raw.strip()
    m = SHELF_REGEX.match(code) or SHELF_REGEX.match(code.replace("-", "").replace(" ", ""))
    if not m:
        raise ValueError("Invalid shelf code format. Use like 10-01-C03.")

    zone = m.group("zone")
    aisle = m.group("aisle")
    section = m.group("section").upper()
    slot = m.group("slot")

    normalized_for_api = f"{zone}{aisle}{section}{slot}"
    display_format = f"{zone}-{aisle}-{section}{slot}"

    return normalized_for_api, display_format


def role_required(*roles):
    """Decorator to require specific roles"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role not in roles:
                from flask import abort
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@item_scanner_bp.route('/')
@login_required
@role_required('admin', 'warehouse_manager', 'picker')
def scan_item_page():
    """Display the item scanner page"""
    return render_template('scan_item.html')


@item_scanner_bp.route('/api/lookup-item', methods=['POST'])
@login_required
@role_required('admin', 'warehouse_manager', 'picker')
def api_lookup_item():
    """
    Lookup item information by barcode via PS365 API
    Returns item details, shelf location, and current stock
    """
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    
    if not barcode:
        return jsonify({'ok': False, 'error': 'No barcode provided'}), 400
    
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        return jsonify({'ok': False, 'error': 'PS365 not configured'}), 500
    
    try:
        # Step 1: Search PS365 for item by barcode
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
        
        # Get first matching item - all data is in search_item response
        item = items[0]
        item_code = item.get('item_code_365')
        
        # Extract all barcodes
        barcode_list = item.get("list_item_barcodes", [])
        all_barcodes = []
        for bc_obj in barcode_list:
            bc_code = bc_obj.get("barcode", "")
            is_label = bc_obj.get("is_label_barcode", False)
            if bc_code:
                all_barcodes.append({
                    'barcode': bc_code,
                    'is_label': is_label
                })
        
        # Get stock from search_item response
        total_stock = item.get('total_stock', 0)
        
        # Get unit of measure
        unit_code = item.get('um_code_365', '')
        unit_name = item.get('um_name', '')
        
        # Get prices
        selling_price = item.get('price_incl_1', 0)  # Price including VAT
        cost_price = item.get('item_cost', 0)
        
        # Get attribute 6 and number field 1
        attribute_6_name = item.get('attribute_6_name', '')
        number_field_1_value = item.get('number_field_1_value', '')
        
        # Get category
        category = item.get('category_name', '')
        
        # Check if active
        is_active = item.get('active', True)
        
        # Fetch shelf location and per-store stock using list_shelves
        shelf_location = ''
        shelf_locations = []
        store_stock = 0
        
        try:
            shelves_data = fetch_item_shelves(PS365_DEFAULT_STORE, [item_code])
            item_shelves = shelves_data.get(item_code, [])
            
            if item_shelves:
                # Get total stock for this store
                for shelf_info in item_shelves:
                    store_stock += float(shelf_info.get('stock', 0))
                    shelf_code = shelf_info.get('shelf_code_365', '')
                    if shelf_code and shelf_code not in shelf_locations:
                        shelf_locations.append(shelf_code)
                
                # Use first shelf as primary
                if shelf_locations:
                    shelf_location = shelf_locations[0]
        except Exception as e:
            print(f"Warning: Could not fetch shelf data: {e}")
            # Fallback to search_item shelf
            shelf_location = item.get('shelf_code_365') or ''
        
        # Format shelf display
        shelf_display = shelf_location
        if len(shelf_locations) > 1:
            shelf_display = f"{shelf_location} (+{len(shelf_locations)-1} more)"
        
        # Use store stock if available, otherwise fall back to total stock
        display_stock = store_stock if store_stock > 0 else total_stock
        
        # Return comprehensive item information
        return jsonify({
            'ok': True,
            'item_code': item_code,
            'item_name': item.get('item_name', ''),
            'item_name_2': item.get('item_name_2', ''),
            'barcode': barcode,
            'shelf_location': shelf_display,
            'shelf_locations': shelf_locations,
            'stock_qty': display_stock,
            'total_stock': total_stock,
            'unit_type': unit_code,
            'unit_name': unit_name,
            'category': category,
            'supplier_name': item.get('supplier_name', ''),
            'all_barcodes': all_barcodes,
            'is_active': is_active,
            'cost_price': cost_price,
            'selling_price': selling_price,
            'attribute_6_name': attribute_6_name,
            'number_field_1_value': number_field_1_value,
            'vat_percent': item.get('vat_percent', 0)
        })
        
    except requests.exceptions.RequestException as e:
        return jsonify({'ok': False, 'error': f'PS365 connection error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@item_scanner_bp.route('/api/search-items', methods=['POST'])
@login_required
@role_required('admin', 'warehouse_manager', 'picker')
def api_search_items():
    """
    Search items by name in PS365
    Returns active items matching the search query
    """
    data = request.get_json()
    query = data.get('query', '').strip()
    
    if not query or len(query) < 4:
        return jsonify({'ok': False, 'error': 'Search query must be at least 4 characters'}), 400
    
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        return jsonify({'ok': False, 'error': 'PS365 not configured'}), 500
    
    try:
        payload = {
            "api_credentials": {
                "token": POWERSOFT_TOKEN
            },
            "search_option": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 50,
                "expression_searched": query,
                "search_operator_type": "Contains",
                "search_in_fields": "ItemCode,ItemName,ItemBarcode",
                "active_type": "active"
            }
        }
        
        url = f"{POWERSOFT_BASE}/search_item"
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        
        api_resp = result.get("api_response", {})
        if api_resp.get("response_code") != "1":
            return jsonify({'ok': False, 'error': f"PS365 Error: {api_resp.get('response_msg', 'Unknown error')}"}), 400
        
        items = result.get("list_items", [])
        results = []
        
        for item in items[:50]:
            barcode = extract_barcode_from_list(item)
            results.append({
                'item_code': item.get('item_code_365', ''),
                'item_name': item.get('item_name', ''),
                'barcode': barcode,
                'total_stock': 0,
                'active': item.get('active', True)
            })
        
        return jsonify({
            'ok': True,
            'query': query,
            'results': results,
            'count': len(results)
        }), 200
        
    except requests.exceptions.RequestException as e:
        return jsonify({'ok': False, 'error': f'PS365 connection error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@item_scanner_bp.route('/api/shelf-items/<shelf_code>', methods=['GET'])
@login_required
@role_required('admin', 'warehouse_manager', 'picker')
def api_shelf_items(shelf_code):
    """
    Get all items on a specific shelf
    """
    if not shelf_code:
        return jsonify({'ok': False, 'error': 'Shelf code is required'}), 400
    
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        return jsonify({'ok': False, 'error': 'PS365 not configured'}), 500
    
    try:
        # Normalize shelf code
        try:
            normalized_for_api, display_format = parse_and_normalize_shelf_code(shelf_code)
        except ValueError:
            normalized_for_api = shelf_code.replace("-", "").replace(" ", "").upper()
            display_format = shelf_code
        
        payload = {
            "api_credentials": {
                "token": POWERSOFT_TOKEN
            },
            "filter_define": {
                "page_number": 1,
                "page_size": 100,
                "only_counted": "N",
                "shelf_code_365_selection": normalized_for_api,
                "store_code_365_selection": PS365_DEFAULT_STORE,
                "item_code_365_selection": "",
                "only_on_stock": False
            }
        }
        
        url = f"{POWERSOFT_BASE}/list_shelves"
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        
        api_resp = result.get("api_response", {})
        if api_resp.get("response_code") != "1":
            return jsonify({'ok': False, 'error': f"PS365 Error: {api_resp.get('response_msg', 'Unknown error')}"}), 400
        
        shelves = result.get("list_shelves", [])
        items = []
        
        for shelf in shelves:
            items_list = shelf.get("list_items", [])
            if items_list:
                for item in items_list:
                    if item.get("store_code_365") == PS365_DEFAULT_STORE:
                        items.append({
                            'item_code': item.get('item_code_365', ''),
                            'item_name': item.get('item_name', ''),
                            'stock': item.get('stock', 0),
                            'stock_reserved': item.get('stock_reserved', 0),
                            'stock_ordered': item.get('stock_ordered', 0)
                        })
        
        return jsonify({
            'ok': True,
            'shelf_code': display_format,
            'items': items,
            'count': len(items)
        }), 200
        
    except requests.exceptions.RequestException as e:
        return jsonify({'ok': False, 'error': f'PS365 connection error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@item_scanner_bp.route('/api/stock-position/<item_code>', methods=['GET'])
@login_required
@role_required('admin', 'warehouse_manager', 'picker')
def api_stock_position(item_code):
    """
    Get stock position from database for a specific item
    Returns stock by store and expiration date
    """
    if not item_code:
        return jsonify({'ok': False, 'error': 'Item code is required'}), 400
    
    try:
        # Query stock positions for this item
        stocks = db.session.query(StockPosition).filter(
            StockPosition.item_code == item_code
        ).all()
        
        if not stocks:
            return jsonify({
                'ok': True,
                'item_code': item_code,
                'stores': [],
                'total_stock': 0,
                'count': 0
            }), 200
        
        # Group by store
        stores_map = {}
        total_stock = 0
        
        for stock in stocks:
            store_key = f"{stock.store_code} - {stock.store_name}"
            if store_key not in stores_map:
                stores_map[store_key] = {'locations': [], 'total': 0}
            
            stores_map[store_key]['locations'].append({
                'expiry_date': stock.expiry_date,
                'quantity': float(stock.stock_quantity)
            })
            stores_map[store_key]['total'] += float(stock.stock_quantity)
            total_stock += float(stock.stock_quantity)
        
        # Convert to list
        stores_list = []
        for store_name, store_data in sorted(stores_map.items()):
            stores_list.append({
                'store': store_name,
                'total': store_data['total'],
                'locations': store_data['locations']
            })
        
        return jsonify({
            'ok': True,
            'item_code': item_code,
            'stores': stores_list,
            'total_stock': total_stock,
            'count': len(stocks)
        }), 200
        
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


def extract_barcode_from_list(item):
    """Extract barcode from PS365 item's list_item_barcodes array"""
    barcode = ""
    barcode_list = item.get("list_item_barcodes", [])
    
    if barcode_list:
        # Prefer label barcode
        for bc in barcode_list:
            if bc.get("is_label_barcode", False):
                barcode = bc.get("barcode", "")
                break
        
        # Fall back to first barcode
        if not barcode and barcode_list:
            barcode = barcode_list[0].get("barcode", "")
    
    return barcode
