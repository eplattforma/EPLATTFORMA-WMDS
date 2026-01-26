#!/usr/bin/env python3
"""
PS365 Reserved Stock Report - Store 777
Fetches reserved stock data from PS365, saves to database, and exports CSV.

Data includes:
- Stock, reserved, ordered, on_transfer quantities
- Season info (code + name)
- number_of_pieces, number_field_5_value (min order qty)
"""

import os
import sys
import csv
import time
import math
import requests
from decimal import Decimal, InvalidOperation
from datetime import datetime

os.environ.setdefault("TZ", "Europe/Nicosia")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Ps365ReservedStock777

PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
PS365_TOKEN = os.getenv("PS365_TOKEN", "")
STORE_CODE = "777"

PAGE_SIZE = int(os.getenv("PS365_PAGE_SIZE", "100"))
TIMEOUT = 120

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports_cache")


def d(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def ps365_get(path: str, params: dict) -> dict:
    if not PS365_BASE_URL or not PS365_TOKEN:
        raise RuntimeError("PS365_BASE_URL and PS365_TOKEN must be set")

    url = f"{PS365_BASE_URL}/{path.lstrip('/')}"
    params = dict(params or {})
    params["token"] = PS365_TOKEN

    last_exc = None
    for attempt in range(1, 4):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_exc = exc
            time.sleep(1.5 * attempt)
    raise last_exc


def get_total_count_store_stock() -> int:
    data = ps365_get(
        "list_stock_items_store",
        {
            "store_code_365": STORE_CODE,
            "available_stock_type": "all",
            "active_type": "all",
            "ecommerce_type": "all",
        },
    )
    return int(data.get("total_count_list_items") or 0)


def get_store_stock_page(page_number: int) -> list:
    data = ps365_get(
        "list_stock_items_store",
        {
            "store_code_365": STORE_CODE,
            "available_stock_type": "all",
            "active_type": "all",
            "ecommerce_type": "all",
            "page_number": page_number,
            "page_size": PAGE_SIZE,
        },
    )
    return data.get("list_stock_stores_item") or data.get("list_stock_items_store") or []


def get_item_details(item_code_365: str) -> dict:
    data = ps365_get("item", {"item_code_365": item_code_365})
    return data.get("item") or {}


def build_rows() -> list:
    total = get_total_count_store_stock()
    if total <= 0:
        print("No items found in store stock")
        return []

    pages = int(math.ceil(total / PAGE_SIZE))
    reserved_items = []

    print(f"Fetching {total} items across {pages} pages...")
    for p in range(1, pages + 1):
        page_rows = get_store_stock_page(p)
        for r in page_rows:
            if d(r.get("stock_reserved")) > 0:
                reserved_items.append(r)
        if p % 10 == 0:
            print(f"  Page {p}/{pages} - found {len(reserved_items)} reserved items so far")
        time.sleep(0.05)

    print(f"Found {len(reserved_items)} items with reservations. Enriching with item details...")
    
    out = []
    for idx, r in enumerate(reserved_items):
        item_code = (r.get("item_code_365") or "").strip()
        if not item_code:
            continue

        item = get_item_details(item_code)
        stock = d(r.get("stock"))
        stock_reserved = d(r.get("stock_reserved"))
        available = stock - stock_reserved

        out.append({
            "store_code_365": STORE_CODE,
            "item_code_365": item_code,
            "item_name": r.get("item_name") or item.get("item_name") or "",
            "season_code_365": item.get("season_code_365") or "",
            "season_name": item.get("season_name") or "",
            "number_of_pieces": d(item.get("number_of_pieces")),
            "number_field_5_value": d(item.get("number_field_5_value")),
            "stock": stock,
            "stock_reserved": stock_reserved,
            "stock_ordered": d(r.get("stock_ordered")),
            "stock_on_transfer": d(r.get("stock_on_transfer")),
            "available_stock": available,
        })

        if (idx + 1) % 50 == 0:
            print(f"  Enriched {idx + 1}/{len(reserved_items)} items")
        time.sleep(0.05)

    return out


def save_to_db(rows: list) -> None:
    now = datetime.utcnow()
    with app.app_context():
        for row in rows:
            rec = Ps365ReservedStock777.query.get(row["item_code_365"])
            if not rec:
                rec = Ps365ReservedStock777(item_code_365=row["item_code_365"])
                db.session.add(rec)

            rec.item_name = row["item_name"]
            rec.season_code_365 = row["season_code_365"]
            rec.season_name = row["season_name"]
            rec.number_of_pieces = row["number_of_pieces"]
            rec.number_field_5_value = row["number_field_5_value"]
            rec.store_code_365 = row["store_code_365"]
            rec.stock = row["stock"]
            rec.stock_reserved = row["stock_reserved"]
            rec.stock_ordered = row["stock_ordered"]
            rec.stock_on_transfer = row["stock_on_transfer"]
            rec.available_stock = row["available_stock"]
            rec.synced_at = now

        db.session.commit()
    print(f"Saved {len(rows)} records to database")


def export_csv(rows: list, filepath: str) -> None:
    fieldnames = [
        "store_code_365",
        "item_code_365",
        "item_name",
        "season_code_365",
        "season_name",
        "number_of_pieces",
        "number_field_5_value",
        "stock",
        "stock_reserved",
        "available_stock",
        "stock_ordered",
        "stock_on_transfer",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            rr = dict(row)
            for k in ["number_of_pieces", "number_field_5_value", "stock", "stock_reserved", 
                      "available_stock", "stock_ordered", "stock_on_transfer"]:
                rr[k] = str(rr[k])
            w.writerow(rr)
    print(f"Exported CSV: {filepath}")


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    print(f"PS365 Reserved Stock Report (Store {STORE_CODE})")
    print("=" * 50)
    
    with app.app_context():
        db.create_all()
    
    rows = build_rows()
    print(f"Reserved items found: {len(rows)}")

    if rows:
        save_to_db(rows)
        
        csv_path = os.path.join(CACHE_DIR, "reserved_stock_777_latest.csv")
        export_csv(rows, csv_path)
        
        print("=" * 50)
        print(f"Done! {len(rows)} items saved to database and CSV")
    else:
        print("No reserved items found (stock_reserved <= 0 for all items).")


if __name__ == "__main__":
    main()
