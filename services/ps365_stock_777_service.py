import os
import logging
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional

from sqlalchemy import text
from app import app, db
from models import (
    Ps365Stock777Run,
    Ps365StockSnapshot777Daily,
    Ps365Stock777Current,
    DwItem,
)
from timezone_utils import get_utc_now
from ps365_client import call_ps365

logger = logging.getLogger(__name__)

STORE_CODE = "777"
PAGE_SIZE = 100
MAX_PAGES = 500
LOW_STOCK_THRESHOLD = 5


def _dec(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _fetch_all_stock_items_777() -> tuple[List[Dict[str, Any]], int, bool]:
    items: Dict[str, Dict[str, Any]] = {}
    pages_fetched = 0
    fetch_ok = True

    page = 1
    while page <= MAX_PAGES:
        try:
            data = call_ps365(
                "list_stock_items_store",
                {
                    "store_code_365": STORE_CODE,
                    "available_stock_type": "all",
                    "active_type": "all",
                    "ecommerce_type": "all",
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                },
                method="GET",
            )
        except Exception as e:
            logger.error(f"[Stock777] API error on page {page}: {e}")
            fetch_ok = False
            break

        rows = (
            data.get("list_stock_stores_item")
            or data.get("list_stock_items_store")
            or []
        )
        pages_fetched += 1

        if not rows:
            break

        for r in rows:
            code = (r.get("item_code_365") or "").strip()
            if not code:
                continue

            items[code] = {
                "item_code_365": code,
                "item_name": r.get("item_name") or "",
                "stock": _dec(r.get("stock")),
                "stock_reserved": _dec(r.get("stock_reserved")),
                "stock_on_transfer": _dec(r.get("stock_on_transfer")),
                "stock_ordered": _dec(r.get("stock_ordered")),
            }

        if len(rows) < PAGE_SIZE:
            break

        page += 1

    logger.info(f"[Stock777] Fetched {len(items)} items across {pages_fetched} pages (ok={fetch_ok})")
    return list(items.values()), pages_fetched, fetch_ok


def _load_dw_item_meta(item_codes: List[str]) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    chunk_size = 800
    for i in range(0, len(item_codes), chunk_size):
        chunk = item_codes[i : i + chunk_size]
        rows = (
            db.session.query(
                DwItem.item_code_365,
                DwItem.supplier_code_365,
                DwItem.supplier_name,
                DwItem.barcode,
                DwItem.active,
                DwItem.item_name,
            )
            .filter(DwItem.item_code_365.in_(chunk))
            .all()
        )
        for r in rows:
            meta[r.item_code_365] = {
                "supplier_code_365": r.supplier_code_365,
                "supplier_name": r.supplier_name,
                "barcode": r.barcode,
                "is_active": r.active,
                "item_name": r.item_name,
            }
    return meta


def _compute_flags(stock_val: Decimal, reserved_val: Decimal) -> dict:
    available = max(stock_val - reserved_val, Decimal("0"))
    is_available = available > 0
    is_oos = available <= 0 and stock_val <= 0
    is_low_stock = Decimal("0") < available <= Decimal(str(LOW_STOCK_THRESHOLD))
    return {
        "available_qty": available,
        "is_available": is_available,
        "is_oos": is_oos,
        "is_low_stock": is_low_stock,
    }


def sync_ps365_stock_777(snapshot_date: Optional[date] = None, trigger: str = "manual") -> dict:
    snap_date = snapshot_date or date.today()
    now = get_utc_now()

    run = Ps365Stock777Run(
        status="RUNNING",
        trigger=trigger,
        snapshot_date=snap_date,
        started_at=now,
    )
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    logger.info(f"[Stock777] Run #{run_id} started for {snap_date} (trigger={trigger})")

    try:
        api_items, pages_fetched, fetch_ok = _fetch_all_stock_items_777()

        if not fetch_ok:
            run.status = "FAILED"
            run.finished_at = get_utc_now()
            run.duration_seconds = int((run.finished_at - run.started_at).total_seconds())
            run.items_found = len(api_items)
            run.items_saved = 0
            run.pages_fetched = pages_fetched
            run.error_message = "API fetch failed mid-pagination; aborting to prevent partial data"
            db.session.commit()
            logger.error(f"[Stock777] Run #{run_id} ABORTED: API fetch incomplete ({len(api_items)} items from {pages_fetched} pages)")
            return {"success": False, "run_id": run_id, "error": "API fetch incomplete"}

        if not api_items:
            run.status = "COMPLETED"
            run.finished_at = get_utc_now()
            run.duration_seconds = int((run.finished_at - run.started_at).total_seconds())
            run.items_found = 0
            run.items_saved = 0
            run.pages_fetched = pages_fetched
            db.session.commit()
            logger.info(f"[Stock777] Run #{run_id} completed with 0 items")
            return {"success": True, "run_id": run_id, "items_saved": 0}

        item_codes = [it["item_code_365"] for it in api_items]
        dw_meta = _load_dw_item_meta(item_codes)

        db.session.execute(
            text("DELETE FROM ps365_stock_snapshot_777_daily WHERE snapshot_date = :d"),
            {"d": snap_date},
        )

        db.session.execute(
            text("DELETE FROM ps365_stock_777_current"),
        )

        snapshot_rows = []
        current_rows = []

        for it in api_items:
            code = it["item_code_365"]
            meta = dw_meta.get(code, {})

            item_name = meta.get("item_name") or it.get("item_name") or ""
            supplier_code = meta.get("supplier_code_365")
            supplier_name = meta.get("supplier_name")
            barcode = meta.get("barcode")
            is_active = meta.get("is_active")

            stock_val = it["stock"]
            reserved_val = it["stock_reserved"]
            flags = _compute_flags(stock_val, reserved_val)

            snap_row = {
                "snapshot_date": snap_date,
                "snapshot_ts": now,
                "store_code_365": STORE_CODE,
                "item_code_365": code,
                "item_name": item_name,
                "supplier_code_365": supplier_code,
                "supplier_name": supplier_name,
                "barcode": barcode,
                "stock": stock_val,
                "stock_reserved": reserved_val,
                "stock_on_transfer": it["stock_on_transfer"],
                "stock_ordered": it["stock_ordered"],
                "available_qty": flags["available_qty"],
                "is_active": is_active,
                "is_available": flags["is_available"],
                "is_oos": flags["is_oos"],
                "is_low_stock": flags["is_low_stock"],
                "source_run_id": run_id,
            }
            snapshot_rows.append(snap_row)

            cur_row = {
                "item_code_365": code,
                "item_name": item_name,
                "supplier_code_365": supplier_code,
                "supplier_name": supplier_name,
                "barcode": barcode,
                "store_code_365": STORE_CODE,
                "stock": stock_val,
                "stock_reserved": reserved_val,
                "stock_on_transfer": it["stock_on_transfer"],
                "stock_ordered": it["stock_ordered"],
                "available_qty": flags["available_qty"],
                "is_active": is_active,
                "is_available": flags["is_available"],
                "is_oos": flags["is_oos"],
                "is_low_stock": flags["is_low_stock"],
                "last_snapshot_date": snap_date,
                "updated_at": now,
                "source_run_id": run_id,
            }
            current_rows.append(cur_row)

        batch = 500
        for i in range(0, len(snapshot_rows), batch):
            db.session.bulk_insert_mappings(Ps365StockSnapshot777Daily, snapshot_rows[i : i + batch])
        for i in range(0, len(current_rows), batch):
            db.session.bulk_insert_mappings(Ps365Stock777Current, current_rows[i : i + batch])

        fin = get_utc_now()
        run.status = "COMPLETED"
        run.finished_at = fin
        run.duration_seconds = int((fin - run.started_at).total_seconds())
        run.items_found = len(api_items)
        run.items_saved = len(snapshot_rows)
        run.pages_fetched = pages_fetched
        db.session.commit()

        logger.info(
            f"[Stock777] Run #{run_id} completed: {len(snapshot_rows)} items saved, "
            f"{pages_fetched} pages, {run.duration_seconds}s"
        )
        return {
            "success": True,
            "run_id": run_id,
            "items_saved": len(snapshot_rows),
            "pages_fetched": pages_fetched,
            "duration_seconds": run.duration_seconds,
        }

    except Exception as e:
        db.session.rollback()
        logger.error(f"[Stock777] Run #{run_id} FAILED: {e}", exc_info=True)
        try:
            run_obj = db.session.get(Ps365Stock777Run, run_id)
            if run_obj:
                run_obj.status = "FAILED"
                run_obj.finished_at = get_utc_now()
                run_obj.duration_seconds = int(
                    (run_obj.finished_at - run_obj.started_at).total_seconds()
                )
                run_obj.error_message = str(e)[:2000]
                db.session.commit()
        except Exception:
            db.session.rollback()

        return {"success": False, "run_id": run_id, "error": str(e)}
