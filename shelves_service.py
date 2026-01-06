import os
import math
import time
from typing import Dict, List, Any
import requests

POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "").rstrip("/")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")
# Hardcode store to 777 due to environment variable caching issue
PS365_DEFAULT_STORE = "777"

SESSION = requests.Session()
TIMEOUTS = (10, 30)  # (connect_timeout, read_timeout) - increased for production stability
RETRY_COUNT = 2

class Ps365Error(Exception):
    pass

def _ps_post(path: str, json_body: dict) -> dict:
    """POST request to PS365 API with retry logic"""
    url = f"{POWERSOFT_BASE}{path}"
    last = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            r = SESSION.post(url, json=json_body, timeout=TIMEOUTS)
            r.raise_for_status()
            data = r.json()
            api = data.get("api_response", {})
            if str(api.get("response_code")) != "1":
                raise Ps365Error(api.get("response_msg", "Unknown PS365 error"))
            return data
        except Exception as e:
            last = e
            if attempt < RETRY_COUNT:
                time.sleep(0.6 * (attempt + 1))
            else:
                raise Ps365Error(f"POST {path} failed: {last}") from last

def _list_shelves_count(store_code: str, item_codes_csv: str) -> int:
    """Get total count of shelf records for given items"""
    body = {
        "api_credentials": {"token": POWERSOFT_TOKEN},
        "filter_define": {
            "page_number": 1,
            "page_size": 1,
            "only_counted": "Y",
            "shelf_code_365_selection": "",
            "store_code_365_selection": store_code,
            "item_code_365_selection": item_codes_csv,
            "only_on_stock": False
        }
    }
    data = _ps_post("/list_shelves", body)
    return int(data.get("total_count_list_shelves", 0))

def _list_shelves_page(store_code: str, item_codes_csv: str, page_number: int, page_size: int = 100) -> List[dict]:
    """Fetch one page of shelf records"""
    body = {
        "api_credentials": {"token": POWERSOFT_TOKEN},
        "filter_define": {
            "page_number": page_number,
            "page_size": page_size,
            "only_counted": "N",
            "shelf_code_365_selection": "",
            "store_code_365_selection": store_code,
            "item_code_365_selection": item_codes_csv,
            "only_on_stock": False
        }
    }
    data = _ps_post("/list_shelves", body)
    return data.get("list_shelves", []) or []

def fetch_item_shelves(store_code: str, item_codes: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch shelf locations for given item codes from PS365.
    Returns { item_code: [ {shelf_code_365, shelf_name, store_code_365, stock, ...}, ... ] }
    """
    item_codes = sorted({(c or "").strip() for c in item_codes if (c or "").strip()})
    if not item_codes:
        return {}

    csv_codes = ",".join(item_codes)
    total = _list_shelves_count(store_code, csv_codes)
    if total == 0:
        return {code: [] for code in item_codes}

    page_size = 100
    pages = math.ceil(total / page_size)
    result = {code: [] for code in item_codes}

    for p in range(1, pages + 1):
        for shelf in _list_shelves_page(store_code, csv_codes, p, page_size):
            shelf_code = shelf.get("shelf_code_365")
            shelf_name = shelf.get("shelf_name")
            for it in shelf.get("list_items", []) or []:
                ic = (it.get("item_code_365") or "").strip()
                sc = str(it.get("store_code_365"))
                if ic in result and sc == str(store_code):
                    result[ic].append({
                        "shelf_code_365": shelf_code,
                        "shelf_name": shelf_name,
                        "store_code_365": sc,
                        "stock": it.get("stock"),
                        "stock_on_transfer": it.get("stock_on_transfer"),
                        "stock_reserved": it.get("stock_reserved"),
                        "stock_ordered": it.get("stock_ordered"),
                        "minimum_stock": it.get("minimum_stock"),
                        "required_stock": it.get("required_stock"),
                    })
    return result
