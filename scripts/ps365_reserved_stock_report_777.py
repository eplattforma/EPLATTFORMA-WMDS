#!/usr/bin/env python3
"""
PS365 Reserved Stock Report - Store 777 (Optimized)
- FULL REFRESH: delete all rows for store 777, then insert snapshot
- Only includes items where season_name is not null/empty
"""

import os
import sys
import math
import time
import requests
from threading import local
from decimal import Decimal, InvalidOperation
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, Any, List

os.environ.setdefault("TZ", "Europe/Nicosia")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Ps365ReservedStock777

PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
PS365_TOKEN = os.getenv("PS365_TOKEN", "")
STORE_CODE = "777"

PAGE_SIZE = int(os.getenv("PS365_PAGE_SIZE", "100"))
TIMEOUT = int(os.getenv("PS365_TIMEOUT", "120"))
MAX_WORKERS = int(os.getenv("PS365_MAX_WORKERS", "6"))
THROTTLE_SECONDS = float(os.getenv("PS365_THROTTLE", "0.0"))

_tls = local()


def d(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def non_empty_str(v) -> bool:
    return v is not None and str(v).strip() != ""


def _session() -> requests.Session:
    """Thread-local Session (safe with concurrency)."""
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        _tls.session = s
    return s


def ps365_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{PS365_BASE_URL}/{path.lstrip('/')}"
    params = dict(params or {})
    params["token"] = PS365_TOKEN

    for attempt in range(1, 4):
        try:
            r = _session().get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            print(f"[WARN] GET {path} attempt {attempt} failed: {e}")
            time.sleep(attempt)
    return {}


def ps365_post_json(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{PS365_BASE_URL}/{path.lstrip('/')}"
    for attempt in range(1, 4):
        try:
            r = _session().post(url, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json() or {}
        except Exception as e:
            print(f"[WARN] POST {path} attempt {attempt} failed: {e}")
            time.sleep(attempt)
    return {}


def fetch_reserved_items_index() -> Dict[str, Any]:
    """
    Returns dict[item_code] = {stock, stock_reserved, item_name}
    Only for items with stock_reserved > 0 in store 777.
    """
    print(f"Identifying items with reservations in Store {STORE_CODE}...")

    reserved_item_data: Dict[str, Any] = {}

    def consume_page(page_data: Dict[str, Any]) -> int:
        """Consume a page and return count of rows processed."""
        rows = page_data.get("list_stock_stores_item") or page_data.get("list_stock_items_store") or []
        for r in rows:
            if d(r.get("stock_reserved")) > 0:
                code = r.get("item_code_365")
                if not code:
                    continue
                reserved_item_data[code] = {
                    "stock": d(r.get("stock")),
                    "stock_reserved": d(r.get("stock_reserved")),
                    "item_name": r.get("item_name") or "",
                }
        return len(rows)

    # Page 1 - also used to get total_count
    first = ps365_get("list_stock_items_store", {
        "store_code_365": STORE_CODE,
        "available_stock_type": "all",
        "active_type": "all",
        "ecommerce_type": "all",
        "page_number": 1,
        "page_size": PAGE_SIZE,
    })

    total = int(first.get("total_count_list_items") or 0)
    consume_page(first)

    if total <= 0:
        print(f"Found {len(reserved_item_data)} items with reservations (total_count not provided).")
        return reserved_item_data

    pages = int(math.ceil(total / PAGE_SIZE))
    for p in range(2, pages + 1):
        page_data = ps365_get("list_stock_items_store", {
            "store_code_365": STORE_CODE,
            "available_stock_type": "all",
            "active_type": "all",
            "ecommerce_type": "all",
            "page_number": p,
            "page_size": PAGE_SIZE,
        })
        consume_page(page_data)
        if THROTTLE_SECONDS:
            time.sleep(THROTTLE_SECONDS)

    print(f"Scanned {pages} pages, found {len(reserved_item_data)} items with reservations")
    return reserved_item_data


def build_row_for_item(code: str, r_store: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Builds one output row for an item code.
    Returns None if season_name is null/empty.
    """
    item_details = ps365_get("item", {"item_code_365": code})
    item = item_details.get("item") or {}

    season_name = item.get("season_name")
    if not non_empty_str(season_name):
        return None

    stock = r_store["stock"]
    stock_reserved = r_store["stock_reserved"]
    supplier_item_code = item.get("text_field_2_value") or ""
    stock_ordered = d(item.get("total_stock_ordered"))

    return {
        "item_code_365": code,
        "item_name": r_store.get("item_name") or item.get("item_name") or "",
        "season_name": season_name or "",
        "supplier_item_code": supplier_item_code,
        "number_of_pieces": int(d(item.get("number_of_pieces"))),
        "number_field_5_value": int(d(item.get("number_field_5_value"))),
        "store_code_365": STORE_CODE,
        "stock": stock,
        "stock_reserved": stock_reserved,
        "available_stock": stock - stock_reserved,
        "stock_ordered": stock_ordered,
        "synced_at": datetime.utcnow(),
    }


def build_rows() -> List[Dict[str, Any]]:
    reserved_item_data = fetch_reserved_items_index()
    if not reserved_item_data:
        print("No items with reservations found.")
        return []

    item_codes = list(reserved_item_data.keys())
    print(f"Found {len(item_codes)} reserved items. Fetching details (workers={MAX_WORKERS})...")

    out = []
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(build_row_for_item, code, reserved_item_data[code]): code
            for code in item_codes
        }
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                row = fut.result()
                if row:
                    out.append(row)
            except Exception as e:
                print(f"[WARN] Failed item {code}: {e}")

            completed += 1
            if completed % 50 == 0:
                print(f"Progress: {completed}/{len(item_codes)}")

    return out


def clear_table_for_store(store_code: str) -> None:
    """Delete all rows for given store (used by route handler)."""
    from flask import has_app_context

    def do_clear():
        Ps365ReservedStock777.query.filter_by(store_code_365=store_code).delete(synchronize_session=False)
        db.session.commit()

    if has_app_context():
        do_clear()
    else:
        with app.app_context():
            do_clear()


def save_to_db(rows: list) -> None:
    """Bulk insert rows (used by route handler)."""
    from flask import has_app_context

    def do_save():
        if rows:
            db.session.bulk_insert_mappings(Ps365ReservedStock777, rows)
            db.session.commit()

    if has_app_context():
        do_save()
    else:
        with app.app_context():
            do_save()


def full_refresh_save(rows: list) -> None:
    """
    FULL REFRESH: delete store rows then bulk insert snapshot.
    """
    with app.app_context():
        Ps365ReservedStock777.query.filter_by(store_code_365=STORE_CODE).delete(synchronize_session=False)
        if rows:
            db.session.bulk_insert_mappings(Ps365ReservedStock777, rows)
        db.session.commit()


def main():
    if not PS365_BASE_URL or not PS365_TOKEN:
        raise RuntimeError("Missing PS365_BASE_URL or PS365_TOKEN in environment.")

    rows = build_rows()

    print(f"Refreshing DB table for store {STORE_CODE} (delete then insert)...")
    full_refresh_save(rows)

    print(f"Done! {len(rows)} items synced (season_name not null).")


if __name__ == "__main__":
    main()
