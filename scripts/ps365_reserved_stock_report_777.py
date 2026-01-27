#!/usr/bin/env python3
"""
PS365 Reserved Stock Report - Store 777 (Low-API Optimized)
- FULL REFRESH: delete all rows for store 777, then insert snapshot
- Only includes items where season_name is not null/empty
- Uses local DW tables to avoid per-item PS365 API calls
"""

import os
import sys
import math
import time
import requests
from threading import local
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Optional, Dict, Any, List

os.environ.setdefault("TZ", "Europe/Nicosia")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db
from models import Ps365ReservedStock777, DwItem, DwSeason

PS365_BASE_URL = os.getenv("PS365_BASE_URL", "").rstrip("/")
PS365_TOKEN = os.getenv("PS365_TOKEN", "")
STORE_CODE = "777"

PAGE_SIZE = int(os.getenv("PS365_PAGE_SIZE", "100"))
TIMEOUT = int(os.getenv("PS365_TIMEOUT", "120"))
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


def chunks(lst: List[str], size: int = 800):
    """Yield successive chunks to avoid huge IN() queries."""
    for i in range(0, len(lst), size):
        yield lst[i:i+size]


def load_item_meta_map(item_codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Load season_name + other fields from LOCAL DB (DwItem + DwSeason join).
    NO PS365 API calls here - uses data warehouse tables.
    """
    meta: Dict[str, Dict[str, Any]] = {}

    for part in chunks(item_codes, 800):
        rows = (
            db.session.query(
                DwItem.item_code_365,
                DwSeason.season_name,
                DwItem.item_name,
                DwItem.number_of_pieces,
                DwItem.supplier_item_code,
            )
            .outerjoin(DwSeason, DwItem.season_code_365 == DwSeason.season_code_365)
            .filter(DwItem.item_code_365.in_(part))
            .all()
        )

        for r in rows:
            code = r[0]
            meta[code] = {
                "season_name": r[1],
                "item_name": r[2],
                "number_of_pieces": r[3],
                "supplier_item_code": r[4],
                "number_field_5_value": 0,  # Not stored in DwItem - default to 0
                "stock_ordered": Decimal("0"),  # Not stored locally - default to 0
            }

    return meta


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
    Uses pagination with empty-page detection and safety limit.
    """
    print(f"Identifying items with reservations in Store {STORE_CODE}...")

    reserved_item_data: Dict[str, Any] = {}
    MAX_PAGES = 200  # Safety limit to prevent infinite loops

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

    page = 1
    while page <= MAX_PAGES:
        page_data = ps365_get("list_stock_items_store", {
            "store_code_365": STORE_CODE,
            "available_stock_type": "all",
            "active_type": "all",
            "ecommerce_type": "all",
            "page_number": page,
            "page_size": PAGE_SIZE,
        })

        rows_count = consume_page(page_data)
        
        # Stop if empty page
        if rows_count == 0:
            break
            
        page += 1
        if THROTTLE_SECONDS:
            time.sleep(THROTTLE_SECONDS)

    print(f"Scanned {page} pages, found {len(reserved_item_data)} items with reservations")
    return reserved_item_data


def fetch_stock_ordered_for_items(item_codes: List[str]) -> Dict[str, Decimal]:
    """
    Fetch stock_ordered from PS365 /item endpoint for a list of item codes.
    Returns dict[item_code] = stock_ordered value.
    """
    result: Dict[str, Decimal] = {}
    
    print(f"Fetching stock_ordered from PS365 for {len(item_codes)} items...")
    
    for i, code in enumerate(item_codes):
        item_details = ps365_get("item", {"item_code_365": code})
        item = item_details.get("item") or {}
        result[code] = d(item.get("total_stock_ordered"))
        
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i + 1}/{len(item_codes)}")
        
        if THROTTLE_SECONDS:
            time.sleep(THROTTLE_SECONDS)
    
    return result


def build_rows() -> List[Dict[str, Any]]:
    """
    Build rows using LOCAL DB lookups instead of per-item PS365 API calls.
    PS365 is only called for:
    1. Paginated list_stock_items_store (to find reserved items)
    2. /item endpoint for filtered items only (to get stock_ordered)
    """
    reserved_item_data = fetch_reserved_items_index()
    if not reserved_item_data:
        print("No items with reservations found.")
        return []

    item_codes = list(reserved_item_data.keys())
    print(f"Found {len(item_codes)} reserved items. Loading metadata from local DB...")

    # Local DB lookup instead of per-item PS365 calls
    meta_map = load_item_meta_map(item_codes)

    # First pass: filter items with valid season_name
    filtered_items: List[Dict[str, Any]] = []
    now = datetime.utcnow()

    missing_local = 0
    filtered_no_season = 0

    for code, r_store in reserved_item_data.items():
        meta = meta_map.get(code)
        if not meta:
            missing_local += 1
            continue

        season_name = meta.get("season_name")
        if not non_empty_str(season_name):
            filtered_no_season += 1
            continue

        stock = r_store["stock"]
        stock_reserved = r_store["stock_reserved"]

        filtered_items.append({
            "item_code_365": code,
            "item_name": r_store.get("item_name") or meta.get("item_name") or "",
            "season_name": season_name or "",
            "supplier_item_code": meta.get("supplier_item_code") or "",
            "number_of_pieces": int(d(meta.get("number_of_pieces"))),
            "number_field_5_value": int(d(meta.get("number_field_5_value"))),
            "store_code_365": STORE_CODE,
            "stock": stock,
            "stock_reserved": stock_reserved,
            "available_stock": stock - stock_reserved,
            "stock_ordered": Decimal("0"),  # Placeholder - will be filled below
            "synced_at": now,
        })

    print(f"Local meta missing for {missing_local} items; filtered (no season) {filtered_no_season}.")
    print(f"Filtered down to {len(filtered_items)} items with valid season_name.")

    # Second pass: fetch stock_ordered ONLY for filtered items
    if filtered_items:
        filtered_codes = [item["item_code_365"] for item in filtered_items]
        stock_ordered_map = fetch_stock_ordered_for_items(filtered_codes)
        
        for item in filtered_items:
            code = item["item_code_365"]
            item["stock_ordered"] = stock_ordered_map.get(code, Decimal("0"))

    return filtered_items


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
