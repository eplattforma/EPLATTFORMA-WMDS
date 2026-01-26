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
    url = f"{PS365_BASE_URL}/{path.lstrip('/')}"
    params = dict(params or {})
    params["token"] = PS365_TOKEN
    for attempt in range(1, 4):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1.5 * attempt)
    return {}

def build_rows() -> list:
    data = ps365_get("list_stock_items_store", {
        "store_code_365": STORE_CODE,
        "available_stock_type": "all",
        "active_type": "all",
        "ecommerce_type": "all",
    })
    total = int(data.get("total_count_list_items") or 0)
    if total <= 0: return []

    pages = int(math.ceil(total / PAGE_SIZE))
    reserved_items = []
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
                reserved_items.append(r)
        time.sleep(0.05)

    out = []
    for r in reserved_items:
        item_code = (r.get("item_code_365") or "").strip()
        if not item_code: continue
        
        item_data = ps365_get("item", {"item_code_365": item_code})
        item = item_data.get("item") or {}
        
        stock = d(r.get("stock"))
        stock_reserved = d(r.get("stock_reserved"))
        available = stock - stock_reserved

        out.append({
            "item_code_365": item_code,
            "item_name": r.get("item_name") or item.get("item_name") or "",
            "season_name": item.get("season_name") or "",
            "number_of_pieces": int(d(item.get("number_of_pieces"))),
            "number_field_5_value": int(d(item.get("number_field_5_value"))),
            "stock": stock,
            "stock_reserved": stock_reserved,
            "available_stock": available,
            "stock_ordered": d(r.get("stock_ordered")),
            "store_code_365": STORE_CODE
        })
    return out

def save_to_db(rows: list) -> None:
    from flask import has_app_context
    now = datetime.utcnow()
    
    def do_save():
        for row in rows:
            rec = Ps365ReservedStock777.query.get(row["item_code_365"])
            if not rec:
                rec = Ps365ReservedStock777(item_code_365=row["item_code_365"])
                db.session.add(rec)
            rec.item_name = row["item_name"]
            rec.season_name = row["season_name"]
            rec.number_of_pieces = row["number_of_pieces"]
            rec.number_field_5_value = row["number_field_5_value"]
            rec.store_code_365 = row["store_code_365"]
            rec.stock = row["stock"]
            rec.stock_reserved = row["stock_reserved"]
            rec.stock_ordered = row["stock_ordered"]
            rec.available_stock = row["available_stock"]
            rec.synced_at = now
        db.session.commit()
    
    if has_app_context():
        do_save()
    else:
        with app.app_context():
            do_save()

def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    rows = build_rows()
    if rows:
        save_to_db(rows)
        print(f"Done! {len(rows)} items synced.")

if __name__ == "__main__":
    main()
