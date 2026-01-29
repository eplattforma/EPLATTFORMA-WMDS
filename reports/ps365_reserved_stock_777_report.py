#!/usr/bin/env python3
"""
PS365 Reserved Stock Report - Store 777
Outputs:
  - reports_cache/reserved_stock_777_latest.json
  - reports_cache/reserved_stock_777_latest.csv

Data included per item:
  item_code_365, item_name, season_code_365, season_name,
  stock_777, reserved_777, available_now_777, ordered_po_777
"""

import os
import json
import csv
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Any

import requests

STORE_CODE = "777"
CACHE_DIR = os.path.join(os.getcwd(), "reports_cache")
JSON_PATH = os.path.join(CACHE_DIR, "reserved_stock_777_latest.json")
CSV_PATH = os.path.join(CACHE_DIR, "reserved_stock_777_latest.csv")


def _env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def _dec(v) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _pick_list(resp: Dict[str, Any], candidate_keys: List[str]) -> List[Dict[str, Any]]:
    """Return the first found list among candidate keys."""
    for k in candidate_keys:
        val = resp.get(k)
        if isinstance(val, list):
            return val
    return []


def ps365_get(base_url: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    r = requests.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def ps365_post(base_url: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_stock_position_store_777(base_url: str, token: str, page_size: int = 50) -> List[Dict[str, Any]]:
    """
    Uses POST list_items_stock with analytical_per_store=True for store 777.
    Returns rows where reserved > 0 or ordered > 0, including stock, reserved, ordered.
    NOTE: When analytical_per_store=True, PS365 max page_size is 50.
    NOTE: We do NOT filter by stores_selection because it affects the ordered qty incorrectly.
          Instead we fetch all stores and filter for store 777 in the results.
    """
    page = 1
    out: List[Dict[str, Any]] = []

    while True:
        payload = {
            "api_credentials": {"token": token},
            "filter_define": {
                "page_number": page,
                "page_size": page_size,
                "only_counted": "N",
                "stores_selection": "",
                "exclude_stores_selection": "",
                "item_active_type": "all",
                "ecommerce_type": "all",
                "categories_selection": "",
                "departments_selection": "",
                "items_supplier_selection": "",
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
                "model_level": False
            }
        }

        resp = ps365_post(base_url, "list_items_stock", payload)
        
        api_resp = resp.get("api_response", {})
        if api_resp.get("response_code") != "1":
            print(f"API error on page {page}: {api_resp.get('response_msg')}")
            break

        rows = resp.get("list_items_stock") or []
        if not rows:
            break

        for row in rows:
            item_code = (row.get("item_code_365") or "").strip()
            if not item_code:
                continue

            # Get per-store stock data for store 777
            per_store = row.get("list_stock_store") or []
            store_data = next((x for x in per_store if str(x.get("store_code_365")) == STORE_CODE), None)
            if not store_data:
                continue

            stock = _dec(store_data.get("stock"))
            reserved = _dec(store_data.get("stock_reserved"))
            ordered = _dec(store_data.get("stock_ordered"))

            # Include items with reserved > 0 OR ordered PO > 0
            if reserved > 0 or ordered > 0:
                out.append(
                    {
                        "item_code_365": item_code,
                        "stock_777": stock,
                        "reserved_777": reserved,
                        "ordered_po_777": ordered,
                        "available_now_777": stock - reserved,
                    }
                )

        if len(rows) < page_size:
            break
        page += 1
        time.sleep(0.1)

    return out


def fetch_items_season_info(base_url: str, token: str, item_codes: List[str], chunk_size: int = 100) -> Dict[str, Dict[str, Any]]:
    """
    Uses POST list_items with display_fields to fetch season fields per item_code.
    Returns map[item_code] -> {item_name, season_code_365, season_name}
    """
    info: Dict[str, Dict[str, Any]] = {}

    for i in range(0, len(item_codes), chunk_size):
        chunk = item_codes[i : i + chunk_size]
        payload = {
            "api_credentials": {"token": token},
            "filter_define": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 100,
                "active_type": "all",
                "ecommerce_type": "all",
                "items_selection": ",".join(chunk),
                "display_fields": "item_code_365,item_name,season_code_365,season_name",
            },
        }

        resp = ps365_post(base_url, "list_items", payload)
        rows = _pick_list(resp, ["list_items", "items"])
        for r in rows:
            code = (r.get("item_code_365") or "").strip()
            if not code:
                continue
            info[code] = {
                "item_name": r.get("item_name") or "",
                "season_code_365": r.get("season_code_365") or "",
                "season_name": r.get("season_name") or "",
            }

        time.sleep(0.05)

    return info


def build_report_rows(stock_rows: List[Dict[str, Any]], season_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in stock_rows:
        code = r["item_code_365"]
        meta = season_map.get(code, {})
        rows.append(
            {
                "item_code_365": code,
                "item_name": meta.get("item_name", ""),
                "season_code_365": meta.get("season_code_365", ""),
                "season_name": meta.get("season_name", ""),
                "stock_777": str(r["stock_777"]),
                "reserved_777": str(r["reserved_777"]),
                "available_now_777": str(r["available_now_777"]),
                "ordered_po_777": str(r["ordered_po_777"]),
            }
        )

    def _sort_key(x):
        return (Decimal(x["reserved_777"]) * Decimal("-1"), x["item_code_365"])

    rows.sort(key=_sort_key)
    return rows


def write_json_csv(rows: List[Dict[str, Any]]):
    os.makedirs(CACHE_DIR, exist_ok=True)
    captured_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "store_code": STORE_CODE,
        "captured_at_utc": captured_at,
        "count": len(rows),
        "rows": rows,
    }

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    fieldnames = [
        "item_code_365",
        "item_name",
        "season_code_365",
        "season_name",
        "stock_777",
        "reserved_777",
        "available_now_777",
        "ordered_po_777",
    ]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def main():
    base_url = _env("PS365_BASE_URL").rstrip("/")
    token = _env("PS365_TOKEN")

    stock_rows = fetch_stock_position_store_777(base_url, token, page_size=100)
    item_codes = [r["item_code_365"] for r in stock_rows]
    season_map = fetch_items_season_info(base_url, token, item_codes, chunk_size=100)

    rows = build_report_rows(stock_rows, season_map)
    write_json_csv(rows)

    print(f"OK: store {STORE_CODE} reserved report generated")
    print(f"  rows: {len(rows)}")
    print(f"  JSON: {JSON_PATH}")
    print(f"  CSV : {CSV_PATH}")


if __name__ == "__main__":
    main()
