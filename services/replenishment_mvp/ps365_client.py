"""
Replenishment MVP - PS365 Stock API Client

Fetches current stock snapshot for a supplier's items from warehouse store 777.
Uses the existing ps365_client.call_ps365 for HTTP calls.
"""
import logging
import math

from ps365_client import call_ps365

logger = logging.getLogger(__name__)

REPLENISHMENT_WAREHOUSE_STORE = "777"


def fetch_supplier_stock(supplier_code: str, warehouse_store_code: str = REPLENISHMENT_WAREHOUSE_STORE) -> dict:
    logger.info(f"Fetching stock for supplier={supplier_code}, store={warehouse_store_code}")

    count_payload = _build_payload(supplier_code, warehouse_store_code, page_number=1, page_size=50, only_counted="Y")
    count_resp = call_ps365("list_items_stock", method="POST", payload=count_payload)

    if not count_resp or count_resp.get("api_response", {}).get("response_code") != "1":
        error_msg = count_resp.get("api_response", {}).get("response_message", "Unknown error") if count_resp else "No response"
        raise RuntimeError(f"PS365 stock count failed: {error_msg}")

    total_items = int(count_resp.get("total_items", 0))
    logger.info(f"PS365 reports {total_items} items for supplier {supplier_code}")

    if total_items == 0:
        return {}

    page_size = 50
    total_pages = math.ceil(total_items / page_size)
    items = {}

    for page in range(1, total_pages + 1):
        logger.debug(f"Fetching page {page}/{total_pages}")
        payload = _build_payload(supplier_code, warehouse_store_code, page_number=page, page_size=page_size, only_counted="N")
        resp = call_ps365("list_items_stock", method="POST", payload=payload)

        if not resp or resp.get("api_response", {}).get("response_code") != "1":
            error_msg = resp.get("api_response", {}).get("response_message", "Unknown error") if resp else "No response"
            raise RuntimeError(f"PS365 stock page {page} failed: {error_msg}")

        for item in resp.get("items", []):
            item_code = item.get("item_code_365", "")
            if not item_code:
                continue

            store_data = _extract_store_stock(item, warehouse_store_code)
            items[item_code] = {
                "item_name": item.get("item_name", ""),
                "stock_now_units": store_data["stock"],
                "reserved_now_units": store_data["stock_reserved"],
                "ordered_now_units": store_data["stock_ordered"],
                "on_transfer_now_units": store_data["stock_on_transfer"],
            }

    logger.info(f"Loaded stock for {len(items)} items from PS365")
    return items


def _extract_store_stock(item: dict, store_code: str) -> dict:
    defaults = {"stock": 0, "stock_reserved": 0, "stock_ordered": 0, "stock_on_transfer": 0}
    store_list = item.get("list_stock_store", [])
    if not store_list:
        return defaults
    for store in store_list:
        if str(store.get("store_code_365", "")) == str(store_code):
            return {
                "stock": float(store.get("stock", 0) or 0),
                "stock_reserved": float(store.get("stock_reserved", 0) or 0),
                "stock_ordered": float(store.get("stock_ordered", 0) or 0),
                "stock_on_transfer": float(store.get("stock_on_transfer", 0) or 0),
            }
    return defaults


def _build_payload(supplier_code: str, store_code: str, page_number: int, page_size: int, only_counted: str) -> dict:
    return {
        "filter_define": {
            "page_number": page_number,
            "page_size": page_size,
            "only_counted": only_counted,
            "stores_selection": store_code,
            "exclude_stores_selection": "",
            "item_active_type": "active",
            "ecommerce_type": "all",
            "categories_selection": "",
            "departments_selection": "",
            "items_supplier_selection": supplier_code,
            "brands_selection": "",
            "seasons_selection": "",
            "models_selection": "",
            "items_selection": "",
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
            "model_level": False,
        }
    }
