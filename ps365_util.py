# ps365_util.py
import os
import requests
from math import ceil
from typing import Optional

DEFAULT_PAGE_SIZE = 100  # PS365 API max page size is 100
DEFAULT_TIMEOUT = 30  # seconds - increased for shelf lookups during bulk import

class Ps365LookupError(RuntimeError):
    pass

def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise Ps365LookupError(f"Missing required environment variable: {name}")
    return v

def _post_json(base_url: str, path: str, payload: dict, timeout: int = DEFAULT_TIMEOUT) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json() or {}
    except requests.Timeout:
        raise Ps365LookupError(f"Timeout calling {url} after {timeout}s")
    except requests.RequestException as e:
        raise Ps365LookupError(f"HTTP error calling {url}: {e}") from e
    except ValueError as e:
        raise Ps365LookupError(f"Non-JSON response from {url}") from e

def _norm_code(val):
    if val is None: return ""
    return str(val).strip().upper()

def _norm_barcode(val):
    if val is None: return None
    v = str(val).strip()
    return v if v else None

def _parse_qty_int(val):
    try:
        return int(float(val))
    except:
        return 0

def find_barcode_for_item_ps365(
    item: str,
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """
    Returns the barcode for `item` from PS365 search_item endpoint, or None if not found.
    Prefers barcodes with is_label_barcode=true, falls back to first barcode.
    """
    base_url = (base_url or os.getenv("PS365_BASE_URL", "")).strip()
    token = (token or os.getenv("PS365_TOKEN", "")).strip()
    if not base_url:
        raise Ps365LookupError("PS365_BASE_URL not set")
    if not token:
        raise Ps365LookupError("PS365_TOKEN not set")
    
    payload = {
        "api_credentials": {"token": token},
        "search_option": {
            "only_counted": "N",
            "page_number": 1,
            "page_size": 1,
            "expression_searched": str(item),
            "search_operator_type": "Equals",
            "search_in_fields": "ItemCode",
            "active_type": "all"
        }
    }
    
    data = _post_json(base_url, "/search_item", payload, timeout=timeout)
    items = data.get("list_items") or []
    
    if not items:
        return None
    
    item_obj = items[0]
    barcode_list = item_obj.get("list_item_barcodes", []) or []
    
    # Prefer barcode with is_label_barcode=true
    for bc_obj in barcode_list:
        if bc_obj.get("is_label_barcode") == True:
            barcode = bc_obj.get("barcode")
            if barcode and barcode != str(item):  # Don't use item code as barcode
                return barcode
    
    # Fallback to first barcode if no label barcode found
    if barcode_list:
        barcode = barcode_list[0].get("barcode")
        if barcode and barcode != str(item):
            return barcode
    
    return None


def find_barcodes_for_items_ps365(
    items: list,
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    Batch fetch barcodes for multiple items from PS365 search_item endpoint.
    Returns a dict of {item_code: barcode} for items that have barcodes.
    
    This is much faster than calling find_barcode_for_item_ps365 for each item
    because it uses a single API call with comma-separated item codes.
    """
    if not items:
        return {}
    
    base_url = (base_url or os.getenv("PS365_BASE_URL", "")).strip()
    token = (token or os.getenv("PS365_TOKEN", "")).strip()
    if not base_url:
        raise Ps365LookupError("PS365_BASE_URL not set")
    if not token:
        raise Ps365LookupError("PS365_TOKEN not set")
    
    result = {}
    
    # Process items in batches of 50 to avoid API limits
    batch_size = 50
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        items_csv = ",".join(str(item) for item in batch)
        
        payload = {
            "api_credentials": {"token": token},
            "search_option": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 100,
                "expression_searched": items_csv,
                "search_operator_type": "Contained",
                "search_in_fields": "ItemCode",
                "active_type": "all"
            }
        }
        
        try:
            data = _post_json(base_url, "/search_item", payload, timeout=timeout)
            items_list = data.get("list_items") or []
            
            for item_obj in items_list:
                item_code = item_obj.get("item_code_365") or item_obj.get("item_code")
                if not item_code:
                    continue
                
                barcode_list = item_obj.get("list_item_barcodes", []) or []
                
                # Prefer barcode with is_label_barcode=true
                barcode = None
                for bc_obj in barcode_list:
                    if bc_obj.get("is_label_barcode") == True:
                        bc = bc_obj.get("barcode")
                        if bc and bc != str(item_code):
                            barcode = bc
                            break
                
                # Fallback to first barcode
                if not barcode and barcode_list:
                    bc = barcode_list[0].get("barcode")
                    if bc and bc != str(item_code):
                        barcode = bc
                
                if barcode:
                    result[item_code] = barcode
        except Exception as e:
            # Log but continue - partial results are better than none
            import logging
            logging.warning(f"Batch barcode lookup error for batch {i}: {str(e)}")
    
    return result


def find_shelf_for_item_ps365(
    store: str,
    item: str,
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    only_on_stock: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[str]:
    """
    Returns the shelf_code_365 for `item` in `store`, or None if not found.

    Uses the Powersoft 365 POST /list_shelves endpoint to fetch shelf assignments.
    """
    base_url = (base_url or os.getenv("PS365_BASE_URL", "")).strip()
    token = (token or os.getenv("PS365_TOKEN", "")).strip()
    if not base_url:
        raise Ps365LookupError("PS365_BASE_URL not set")
    if not token:
        raise Ps365LookupError("PS365_TOKEN not set")

    # 1) Count total shelves matching criteria
    count_payload = {
        "api_credentials": {"token": token},
        "filter_define": {
            "page_number": 1,
            "page_size": page_size,
            "only_counted": "Y",
            "shelf_code_365_selection": "",
            "store_code_365_selection": str(store),
            "item_code_365_selection": str(item),
            "only_on_stock": bool(only_on_stock),
        },
    }
    data = _post_json(base_url, "/list_shelves", count_payload, timeout=timeout)
    total = int(data.get("total_count_list_shelves") or 0)
    if total <= 0:
        return None

    # 2) Page through results to find matching shelf
    pages = max(1, ceil(total / page_size))
    for page_number in range(1, pages + 1):
        payload = {
            "api_credentials": {"token": token},
            "filter_define": {
                "page_number": page_number,
                "page_size": page_size,
                "only_counted": "N",
                "shelf_code_365_selection": "",
                "store_code_365_selection": str(store),
                "item_code_365_selection": str(item),
                "only_on_stock": bool(only_on_stock),
            },
        }
        page = _post_json(base_url, "/list_shelves", payload, timeout=timeout)
        for shelf in page.get("list_shelves") or []:
            shelf_code = shelf.get("shelf_code_365")
            for row in shelf.get("list_items") or []:
                if str(row.get("store_code_365")) == str(store) and str(row.get("item_code_365")).strip() == str(item).strip():
                    return shelf_code
    return None

if __name__ == "__main__":
    # Quick CLI smoke test:
    # python ps365_util.py GLT-0027 777
    import sys
    if len(sys.argv) < 3:
        print("Usage: python ps365_util.py <ITEM_CODE> <STORE_CODE>")
        sys.exit(2)
    item_code = sys.argv[1]
    store_code = sys.argv[2]
    try:
        shelf = find_shelf_for_item_ps365(store=store_code, item=item_code)
        if shelf:
            print(f"Found shelf for item {item_code} in store {store_code}: {shelf}")
            sys.exit(0)
        else:
            print(f"No shelf found for item {item_code} in store {store_code}")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(3)
