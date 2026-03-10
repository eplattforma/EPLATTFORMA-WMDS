"""
Replenishment MVP - PS365 Stock API Client

Fetches current stock snapshot for a supplier's items from warehouse store 777.
Uses the existing ps365_client.call_ps365 for HTTP calls.

Ordered stock: The list_items_stock API's stock_ordered field is unreliable
(does not reflect all open POs). We supplement it by querying
list_purchase_orders directly and aggregating quantities from pending POs.
"""
import logging
import math
from collections import defaultdict
from datetime import date, timedelta

from ps365_client import call_ps365

logger = logging.getLogger(__name__)

REPLENISHMENT_WAREHOUSE_STORE = "777"

PO_PENDING_STATUSES = {"PROC", "PROCESSING", "PENDING", "ORDERED", "APPROVED", "OPEN"}


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

        for item in (resp.get("list_items_stock") or resp.get("items") or []):
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

    po_ordered = _fetch_ordered_from_purchase_orders(supplier_code, warehouse_store_code)
    if po_ordered:
        for item_code, po_qty in po_ordered.items():
            if item_code in items:
                stock_api_ordered = items[item_code]["ordered_now_units"]
                if po_qty > stock_api_ordered:
                    logger.debug(
                        f"{item_code}: PO ordered {po_qty} > stock API ordered {stock_api_ordered}, using PO value"
                    )
                    items[item_code]["ordered_now_units"] = po_qty
            else:
                logger.debug(f"{item_code}: found in POs but not in stock API, skipping")

        po_items_with_data = sum(1 for ic in items if po_ordered.get(ic, 0) > 0)
        logger.info(f"PO ordered quantities applied: {po_items_with_data} items updated from {len(po_ordered)} PO line items")

    logger.info(f"Loaded stock for {len(items)} items from PS365")
    return items


def _fetch_ordered_from_purchase_orders(supplier_code: str, warehouse_store_code: str) -> dict:
    try:
        today = date.today()
        from_date = (today - timedelta(days=180)).isoformat()
        to_date = (today + timedelta(days=365)).isoformat()

        resp = call_ps365("list_purchase_orders", method="POST", payload={
            "filter_define": {
                "page_number": 1,
                "page_size": 100,
                "only_counted": "N",
                "orders_supplier_selection": supplier_code,
                "order_status_selection": "",
                "from_date": from_date,
                "to_date": to_date,
                "items_selection": "",
                "stores_selection": "",
                "orders_type": "all",
                "shopping_cart_code_selection": "",
            }
        })

        if not resp or resp.get("api_response", {}).get("response_code") != "1":
            error_msg = resp.get("api_response", {}).get("response_msg", "Unknown") if resp else "No response"
            logger.warning(f"Failed to fetch POs from PS365: {error_msg}")
            return {}

        pos = resp.get("list_purchase_orders") or []
        logger.info(f"Found {len(pos)} purchase orders for supplier {supplier_code}")

        ordered_by_item = defaultdict(float)

        for po in pos:
            header = po.get("purchase_order_header", {})
            status_code = (header.get("order_status_code") or "").upper().strip()
            is_pending = header.get("is_pending", False)
            po_store = str(header.get("store_code_365", ""))
            po_id = header.get("purchase_order_id", "?")

            if po_store != str(warehouse_store_code):
                logger.debug(f"PO {po_id} is for store {po_store}, skipping (need {warehouse_store_code})")
                continue

            if not is_pending and status_code not in PO_PENDING_STATUSES:
                logger.debug(f"PO {po_id} status={status_code}, is_pending={is_pending}, skipping")
                continue

            items = po.get("list_purchase_order_details") or []
            for item in items:
                item_code = item.get("item_code_365", "")
                qty = float(item.get("line_quantity", 0) or 0)
                received = float(item.get("line_quantity_received", 0) or 0)
                outstanding = qty - received
                if item_code and outstanding > 0:
                    ordered_by_item[item_code] += outstanding

            logger.debug(
                f"PO {po_id}: status={status_code}, pending={is_pending}, "
                f"store={po_store}, items={len(items)}"
            )

        return dict(ordered_by_item)

    except Exception as e:
        logger.exception(f"Error fetching POs for ordered quantities: {e}")
        return {}


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
