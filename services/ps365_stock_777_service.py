import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional

from sqlalchemy import text
from app import app, db
from models import (
    Ps365Stock777Run,
    Ps365Oos777Daily,
    DwItem,
)
from timezone_utils import get_utc_now
from ps365_client import call_ps365

logger = logging.getLogger(__name__)

STORE_CODE = "777"
PAGE_SIZE = 100
MAX_PAGES = 500


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
                DwItem.season_code_365,
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
                "season_code_365": r.season_code_365,
            }
    return meta


def _get_excluded_seasons() -> set:
    import json
    from models import Setting
    raw = Setting.get(db.session, 'oos_excluded_seasons', '[]')
    try:
        seasons = json.loads(raw)
        return set(seasons) if isinstance(seasons, list) else set()
    except (json.JSONDecodeError, TypeError):
        return set()


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
            logger.info(f"[Stock777] Run #{run_id} completed with 0 items from API")
            return {"success": True, "run_id": run_id, "items_saved": 0}

        item_codes = [it["item_code_365"] for it in api_items]
        dw_meta = _load_dw_item_meta(item_codes)
        excluded_seasons = _get_excluded_seasons()
        if excluded_seasons:
            logger.info(f"[Stock777] Excluding seasons: {excluded_seasons}")

        oos_rows = []
        excluded_count = 0
        for it in api_items:
            code = it["item_code_365"]
            meta = dw_meta.get(code, {})

            is_active = meta.get("is_active")
            if not is_active:
                continue

            season = meta.get("season_code_365") or ""
            if season in excluded_seasons:
                excluded_count += 1
                continue

            stock_val = it["stock"]
            reserved_val = it["stock_reserved"]
            available_qty = max(stock_val - reserved_val, Decimal("0"))

            if available_qty > 0:
                continue

            item_name = meta.get("item_name") or it.get("item_name") or ""
            supplier_code = meta.get("supplier_code_365")
            supplier_name = meta.get("supplier_name")
            barcode = meta.get("barcode")

            oos_rows.append({
                "snapshot_date": snap_date,
                "item_code_365": code,
                "item_name": item_name,
                "supplier_code_365": supplier_code,
                "supplier_name": supplier_name,
                "barcode": barcode,
                "stock": stock_val,
                "stock_reserved": reserved_val,
                "available_qty": available_qty,
                "detected_at": now,
                "source_run_id": run_id,
            })

        db.session.execute(
            text("DELETE FROM ps365_oos_777_daily WHERE snapshot_date = :d"),
            {"d": snap_date},
        )

        batch = 500
        for i in range(0, len(oos_rows), batch):
            db.session.bulk_insert_mappings(Ps365Oos777Daily, oos_rows[i : i + batch])

        fin = get_utc_now()
        run.status = "COMPLETED"
        run.finished_at = fin
        run.duration_seconds = int((fin - run.started_at).total_seconds())
        run.items_found = len(api_items)
        run.items_saved = len(oos_rows)
        run.pages_fetched = pages_fetched
        db.session.commit()

        excl_msg = f", {excluded_count} season-excluded" if excluded_count else ""
        logger.info(
            f"[Stock777] Run #{run_id} completed: {len(oos_rows)} OOS items saved "
            f"(from {len(api_items)} total{excl_msg}), {pages_fetched} pages, {run.duration_seconds}s"
        )
        return {
            "success": True,
            "run_id": run_id,
            "items_total": len(api_items),
            "oos_items_saved": len(oos_rows),
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
