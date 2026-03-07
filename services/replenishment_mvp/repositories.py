"""
Replenishment MVP - Repository Layer

All DB access for replenishment logic. No raw SQL in the blueprint.
Date column: dw_invoice_header.invoice_date_utc0
"""
import logging
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from sqlalchemy import text, func
from app import db
from models import (
    ReplenishmentSupplier, ReplenishmentItemSetting,
    DwItem, StockPosition
)

logger = logging.getLogger(__name__)

SALES_DATE_COLUMN = "invoice_date_utc0"


def get_active_suppliers():
    return ReplenishmentSupplier.query.filter_by(is_active=True).order_by(
        ReplenishmentSupplier.sort_order.asc().nullslast(),
        ReplenishmentSupplier.supplier_name.asc()
    ).all()


def get_supplier_by_code(supplier_code: str):
    return ReplenishmentSupplier.query.filter_by(supplier_code=supplier_code).first()


def get_item_master_for_codes(item_codes: list) -> dict:
    if not item_codes:
        return {}
    items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    result = {}
    for item in items:
        result[item.item_code_365] = {
            "item_code_365": item.item_code_365,
            "item_name": item.item_name,
            "min_order_qty": item.min_order_qty,
            "selling_qty": float(item.selling_qty) if item.selling_qty else None,
            "cost_price": float(item.cost_price) if item.cost_price else None,
            "barcode": item.barcode,
            "supplier_item_code": item.supplier_item_code,
            "brand_code_365": item.brand_code_365,
            "category_code_365": item.category_code_365,
            "season_code_365": item.season_code_365,
            "active": item.active,
        }
    return result


def get_item_settings_for_codes(item_codes: list) -> dict:
    if not item_codes:
        return {}
    settings = ReplenishmentItemSetting.query.filter(
        ReplenishmentItemSetting.item_code_365.in_(item_codes)
    ).all()
    result = {}
    for s in settings:
        result[s.item_code_365] = {
            "case_qty_units": float(s.case_qty_units) if s.case_qty_units else None,
            "safety_days_override": float(s.safety_days_override) if s.safety_days_override else None,
            "min_order_cases": float(s.min_order_cases) if s.min_order_cases else None,
            "is_active": s.is_active,
        }
    return result


def get_same_weekday_sales_averages(item_codes: list, weekdays_needed: list, lookback_occurrences: int = 4, reference_date: date = None) -> dict:
    if not item_codes or not weekdays_needed:
        return {}

    result = defaultdict(dict)

    ref_base = reference_date or date.today()

    for wd in weekdays_needed:
        ref_dates = _find_last_n_weekday_dates(wd, lookback_occurrences, ref_base)
        if not ref_dates:
            for ic in item_codes:
                result[ic][wd] = 0.0
            continue

        sql = text(f"""
            SELECT l.item_code_365,
                   h.{SALES_DATE_COLUMN} AS sale_date,
                   SUM(l.quantity) AS day_qty
            FROM dw_invoice_line l
            JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
            WHERE l.item_code_365 = ANY(:codes)
              AND h.{SALES_DATE_COLUMN} = ANY(:dates)
            GROUP BY l.item_code_365, h.{SALES_DATE_COLUMN}
        """)

        rows = db.session.execute(sql, {
            "codes": list(item_codes),
            "dates": ref_dates,
        }).fetchall()

        item_day_totals = defaultdict(list)
        for row in rows:
            item_day_totals[row.item_code_365].append(float(row.day_qty or 0))

        for ic in item_codes:
            totals = item_day_totals.get(ic, [])
            if totals:
                result[ic][wd] = sum(totals) / len(totals)
            else:
                result[ic][wd] = 0.0

    return dict(result)


def _find_last_n_weekday_dates(weekday: int, n: int, reference_date: date = None) -> list:
    ref = reference_date or date.today()
    dates = []
    d = ref - timedelta(days=1)
    while len(dates) < n and d > ref - timedelta(days=120):
        if d.weekday() == weekday:
            dates.append(d)
        d -= timedelta(days=1)
    return dates


def get_expiry_summary(item_codes: list, warehouse_store_code: str) -> dict:
    if not item_codes:
        return {}

    positions = StockPosition.query.filter(
        StockPosition.item_code.in_(item_codes),
        StockPosition.store_code == warehouse_store_code
    ).all()

    today = date.today()
    thirty_days = today + timedelta(days=30)

    item_expiries = defaultdict(list)
    for pos in positions:
        parsed = _parse_expiry_date(pos.expiry_date)
        if parsed is None:
            continue
        item_expiries[pos.item_code].append({
            "expiry_date": parsed,
            "qty": float(pos.stock_quantity or 0),
        })

    result = {}
    for ic in item_codes:
        entries = item_expiries.get(ic, [])
        if not entries:
            result[ic] = {
                "earliest_expiry_date": None,
                "qty_at_earliest_expiry": 0,
                "expiring_within_30_days_units": 0,
            }
            continue

        entries.sort(key=lambda e: e["expiry_date"])
        earliest = entries[0]
        exp_30 = sum(e["qty"] for e in entries if e["expiry_date"] <= thirty_days)

        result[ic] = {
            "earliest_expiry_date": earliest["expiry_date"],
            "qty_at_earliest_expiry": earliest["qty"],
            "expiring_within_30_days_units": exp_30,
        }

    return result


def _parse_expiry_date(val) -> date | None:
    if not val or not str(val).strip():
        return None
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None
