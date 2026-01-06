#!/usr/bin/env python3
"""
Check order 08318 to identify which items require expiration dates
"""
import os
import sys
import json
import requests
from urllib.parse import urlencode

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db

POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "").rstrip("/")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")

def fetch_purchase_order(po_code, is_shopping_cart=True):
    """Fetch purchase order from PS365"""
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        raise RuntimeError("Powersoft365 credentials not configured")
    
    params = {
        "token": POWERSOFT_TOKEN,
        "purchase_order_code": po_code,
        "is_shopping_cart_code": "Y" if is_shopping_cart else "N",
    }
    
    url = f"{POWERSOFT_BASE}/purchaseorder?{urlencode(params)}"
    
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        
        api_resp = data.get("api_response", {})
        if api_resp.get("response_code") != "1":
            error_msg = api_resp.get('response_msg', 'Unknown error')
            raise RuntimeError(f"PS365 Error: {error_msg}")
        
        order = data.get("order")
        if not order:
            raise RuntimeError("No order data returned from PS365")
        
        return order
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to connect to PS365: {str(e)}")

def analyze_order_expiration_requirements(order_code):
    """Analyze an order to identify which items require expiration dates"""
    
    print(f"\n{'='*80}")
    print(f"Analyzing Order: {order_code}")
    print(f"{'='*80}\n")
    
    # Try as shopping cart first
    try:
        order_data = fetch_purchase_order(order_code, is_shopping_cart=True)
    except Exception as e:
        print(f"Not found as shopping cart, trying as PO code...")
        try:
            order_data = fetch_purchase_order(order_code, is_shopping_cart=False)
        except Exception as e2:
            print(f"ERROR: Could not fetch order: {e2}")
            return
    
    # Show full raw data for inspection
    print("RAW ORDER DATA:")
    print(json.dumps(order_data, indent=2))
    print("\n" + "="*80 + "\n")
    
    # Parse header
    header = order_data.get("purchase_order_header", {})
    lines = order_data.get("list_purchase_order_details", []) or []
    
    print(f"Order Code: {header.get('purchase_order_code_365')}")
    print(f"Shopping Cart: {header.get('shopping_cart_code')}")
    print(f"Supplier: {header.get('supplier_name')} ({header.get('supplier_code_365')})")
    print(f"Status: {header.get('order_status_name')}")
    print(f"Total Items: {len(lines)}")
    print(f"\n{'='*80}\n")
    
    # Analyze each item
    print("ITEM ANALYSIS:")
    print(f"{'Line':<6} {'Item Code':<15} {'Item Name':<40} {'Qty':<8} {'Exp Date Required?'}")
    print("-" * 120)
    
    items_with_exp = []
    items_without_exp = []
    
    for line in lines:
        line_num = line.get("line_number", "?")
        item_code = line.get("item_code_365", "")
        item_name = line.get("item_name", "")[:40]
        qty = line.get("line_quantity", "")
        
        # Check for expiration date fields in the line data
        # Common field names: requires_expiry, has_lot_number, track_expiry, etc.
        exp_required = "UNKNOWN"
        
        # Look for any fields that might indicate expiration date requirement
        exp_fields = []
        for key, value in line.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ['expir', 'expiry', 'lot', 'batch', 'serial', 'track']):
                exp_fields.append(f"{key}={value}")
        
        if exp_fields:
            exp_required = "YES (tracking fields found)"
            items_with_exp.append({
                'line': line_num,
                'code': item_code,
                'name': item_name,
                'qty': qty,
                'fields': exp_fields
            })
        else:
            exp_required = "NO (no tracking fields)"
            items_without_exp.append({
                'line': line_num,
                'code': item_code,
                'name': item_name,
                'qty': qty
            })
        
        print(f"{str(line_num):<6} {item_code:<15} {item_name:<40} {str(qty):<8} {exp_required}")
        if exp_fields:
            for field in exp_fields:
                print(f"       → {field}")
    
    print("\n" + "="*80 + "\n")
    print(f"SUMMARY:")
    print(f"  Items requiring expiration dates: {len(items_with_exp)}")
    print(f"  Items NOT requiring expiration dates: {len(items_without_exp)}")
    
    if items_with_exp:
        print(f"\n  Items WITH expiration tracking:")
        for item in items_with_exp:
            print(f"    - {item['code']}: {item['name']}")
    
    if items_without_exp:
        print(f"\n  Items WITHOUT expiration tracking:")
        for item in items_without_exp:
            print(f"    - {item['code']}: {item['name']}")

if __name__ == '__main__':
    with app.app_context():
        # Get PO code from command line or use default
        po_code = sys.argv[1] if len(sys.argv) > 1 else "PO10008318"
        analyze_order_expiration_requirements(po_code)
