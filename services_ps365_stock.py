import os
import requests

POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "").rstrip("/")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")

def fetch_items_stock_for_store(store_code: str, item_codes: list[str]) -> dict:
    """
    Returns: { item_code: {"stock": x, "stock_reserved": y, "stock_ordered": z} }
    Uses /list_items_stock with analytical_per_store=true.
    NOTE: when analytical_per_store=true, PS365 page_size max is 50. (per docs)
    """
    if not item_codes:
        return {}

    out = {}

    # chunk to 50 to respect PS365 limits when analytical_per_store=true
    CHUNK = 50
    for i in range(0, len(item_codes), CHUNK):
        chunk = item_codes[i:i+CHUNK]
        payload = {
            "api_credentials": {"token": POWERSOFT_TOKEN},
            "filter_define": {
                "page_number": 1,
                "page_size": len(chunk),
                "only_counted": "N",
                "stores_selection": str(store_code),
                "exclude_stores_selection": "",
                "item_active_type": "all",
                "ecommerce_type": "all",
                "categories_selection": "",
                "departments_selection": "",
                "items_supplier_selection": "",
                "brands_selection": "",
                "seasons_selection": "",
                "models_selection": "",
                "items_selection": ",".join(chunk),
                "colours_selection": "",
                "sizes_selection": "",
                "sizes_group_selection": "",
                "attributes_1_selection": "",
                "attributes_2_selection": "",
                "attributes_3_selection": "",
                "attributes_4_selection": "",
                "attributes_5_selection": "",
                "attributes_6_selection": "",
                "last_modified_from": "",
                "last_modified_to": "",
                "creation_date_from": "",
                "creation_date_to": "",
                "analytical_per_store": True,
                "model_level": False
            }
        }

        url = f"{POWERSOFT_BASE}/list_items_stock"
        try:
            r = requests.post(url, json=payload, timeout=(15, 120))
            r.raise_for_status()
            data = r.json()

            api = data.get("api_response", {})
            if api.get("response_code") != "1":
                continue # Skip chunk on error

            for row in (data.get("list_items_stock") or []):
                code = row.get("item_code_365")
                per_store = row.get("list_stock_store") or []
                srow = next((x for x in per_store if str(x.get("store_code_365")) == str(store_code)), None)
                if not code or not srow:
                    continue
                out[code] = {
                    "stock": float(srow.get("stock") or 0),
                    "stock_reserved": float(srow.get("stock_reserved") or 0),
                    "stock_ordered": float(srow.get("stock_ordered") or 0),
                }
        except Exception:
            continue

    return out
