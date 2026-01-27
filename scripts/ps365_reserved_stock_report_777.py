#!/usr/bin/env python3
"""
PS365 Reserved Stock Report - Store 777
"""

import os
import sys
import csv
import math
import time
import requests
from decimal import Decimal, InvalidOperation, ROUND_UP
from datetime import datetime

os.environ.setdefault("TZ", "Europe/Nicosia")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Ps365ReservedStock777

PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
PS365_TOKEN = os.getenv("PS365_TOKEN", "")
STORE_CODE = "777"
PAGE_SIZE = 100
TIMEOUT = 120

def d(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")

def non_empty_str(v) -> bool:
    return v is not None and str(v).strip() != ""

def ps365_get(path: str, params: dict) -> dict:
    url = f"{PS365_BASE_URL}/{path.lstrip('/')}"
    params = dict(params or {})
    params["token"] = PS365_TOKEN
    for attempt in range(1, 4):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1 * attempt)
    return {}

def build_rows() -> list:
    # 1) Get items with reservations in Store 777
    id_list_data = ps365_get("list_stock_items_store", {
        "store_code_365": STORE_CODE,
        "available_stock_type": "all",
        "active_type": "all",
        "ecommerce_type": "all",
    })
    
    total = int(id_list_data.get("total_count_list_items") or 0)
    pages = int(math.ceil(total / PAGE_SIZE))
    reserved_item_data = {}
    
    print(f"Identifying items with reservations in Store {STORE_CODE}...")
    for p in range(1, pages + 1):
        page_data = ps365_get("list_stock_items_store", {
            "store_code_365": STORE_CODE,
            "available_stock_type": "all",
            "active_type": "all",
            "ecommerce_type": "all",
            "page_number": p,
            "page_size": PAGE_SIZE,
        })
        rows = page_data.get("list_stock_stores_item") or page_data.get("list_stock_items_store") or []
        for r in rows:
            if d(r.get("stock_reserved")) > 0:
                code = r.get("item_code_365")
                reserved_item_data[code] = {
                    "stock": d(r.get("stock")),
                    "stock_reserved": d(r.get("stock_reserved")),
                    "item_name": r.get("item_name")
                }
        time.sleep(0.05)
    
    if not reserved_item_data:
        print("No items with reservations found.")
        return []

    print(f"Found {len(reserved_item_data)} items. Fetching all stock info...")
    
    out = []
    item_codes = list(reserved_item_data.keys())
    
    # Process items to get accurate totals
    for code in item_codes:
        r_store = reserved_item_data[code]
        
        # We use list_items_stock (POST) for each item to get the most accurate 'total_stock_ordered'
        # This endpoint is generally the source of truth for "On PO" across the system
        url = f"{PS365_BASE_URL}/list_items_stock"
        payload = {
            "api_credentials": {"token": PS365_TOKEN},
            "filter_define": {
                "page_number": 1,
                "page_size": 1,
                "items_selection": code,
                "analytical_per_store": False
            }
        }
        
        stock_ordered = Decimal("0")
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT)
            if resp.status_code == 200:
                data = resp.json().get("list_items_stock") or []
                if data:
                    # PS365 total_stock_ordered is the most reliable "On PO" field
                    stock_ordered = d(data[0].get("total_stock_ordered"))
        except Exception as e:
            print(f"Error fetching ordered qty for {code}: {e}")

        item_details = ps365_get("item", {"item_code_365": code})
        item = item_details.get("item") or {}
        
        season_name = item.get("season_name")
        if not non_empty_str(season_name):
            continue

        out.append({
            "item_code_365": code,
            "item_name": r_store["item_name"] or item.get("item_name") or "",
            "season_name": season_name or "",
            "number_of_pieces": int(d(item.get("number_of_pieces"))),
            "number_field_5_value": int(d(item.get("number_field_5_value"))),
            "stock": r_store["stock"],
            "stock_reserved": r_store["stock_reserved"],
            "available_stock": r_store["stock"] - r_store["stock_reserved"],
            "stock_ordered": stock_ordered,
            "store_code_365": STORE_CODE
        })
        time.sleep(0.02) # Small throttle

    return out

def clear_table_for_store(store_code: str) -> None:
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
    from flask import has_app_context
    now = datetime.utcnow()

    def do_save():
        if not rows:
            return

        for row in rows:
            rec = Ps365ReservedStock777(
                item_code_365=row["item_code_365"],
                item_name=row["item_name"],
                season_name=row["season_name"],
                number_of_pieces=row["number_of_pieces"],
                number_field_5_value=row["number_field_5_value"],
                store_code_365=row["store_code_365"],
                stock=row["stock"],
                stock_reserved=row["stock_reserved"],
                stock_ordered=row["stock_ordered"],
                available_stock=row["available_stock"],
                synced_at=now,
            )
            db.session.add(rec)

        db.session.commit()

    if has_app_context():
        do_save()
    else:
        with app.app_context():
            do_save()

def main():
    # Always clear first so the table is a true snapshot
    clear_table_for_store(STORE_CODE)
    print(f"Cleared existing reserved stock rows for store {STORE_CODE}.")

    rows = build_rows()
    save_to_db(rows)

    print(f"Done! {len(rows)} items synced.")

if __name__ == "__main__":
    main()
