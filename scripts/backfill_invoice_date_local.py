"""
One-off backfill: populate dw_invoice_header.invoice_date_local from PS365.

PS365's list_loyalty_invoices_header from/to filter works on the *value date*
(invoice_date_local), which is what Powersoft's own reports use. Our DW only
stored invoice_date_utc0, which can differ by days or even months.

Usage: python scripts/backfill_invoice_date_local.py [from_month] [to_month]
       (months as YYYY-MM; defaults 2023-01 .. current month)
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
import logging
from datetime import date, datetime
from calendar import monthrange

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("backfill")
log.setLevel(logging.INFO)

from main import app  # noqa: E402
from app import db  # noqa: E402
from ps365_client import call_ps365  # noqa: E402
from sqlalchemy import text  # noqa: E402


def month_range(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def run(from_month: str, to_month: str):
    fy, fm = map(int, from_month.split("-"))
    ty, tm = map(int, to_month.split("-"))
    total_updated = 0
    with app.app_context():
        for y, m in month_range(date(fy, fm, 1), date(ty, tm, 1)):
            d_from = f"{y:04d}-{m:02d}-01"
            d_to = f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"
            page = 1
            month_updated = 0
            while True:
                payload = {"filter_define": {
                    "only_counted": "N", "page_number": page, "page_size": 100,
                    "invoice_type": "all",
                    "invoice_number_selection": "",
                    "invoice_customer_code_selection": "",
                    "invoice_customer_name_selection": "",
                    "invoice_customer_email_selection": "",
                    "invoice_customer_phone_selection": "",
                    "from_date": d_from, "to_date": d_to,
                }}
                resp = call_ps365("list_loyalty_invoices_header", payload)
                ar = resp.get("api_response", {})
                if ar.get("response_code") != "1":
                    raise RuntimeError(f"PS365 error {d_from} p{page}: {ar}")
                invs = resp.get("list_invoices", []) or []
                if not invs:
                    break
                rows = []
                for inv in invs:
                    no = inv.get("invoice_no_365")
                    loc = inv.get("invoice_date_local")
                    if not no or not loc:
                        continue
                    try:
                        loc_d = datetime.fromisoformat(str(loc)[:10]).date()
                    except ValueError:
                        continue
                    rows.append({"no": no, "loc": loc_d})
                if rows:
                    result = db.session.execute(text("""
                        UPDATE dw_invoice_header
                        SET invoice_date_local = :loc
                        WHERE invoice_no_365 = :no
                          AND (invoice_date_local IS DISTINCT FROM :loc)
                    """), rows)
                    db.session.commit()
                    month_updated += result.rowcount or 0
                page += 1
                if page > 200:
                    break
                time.sleep(0.15)
            total_updated += month_updated
            log.info("%s: %d headers stamped", f"{y:04d}-{m:02d}", month_updated)
    log.info("DONE — total headers updated: %d", total_updated)


if __name__ == "__main__":
    today = date.today()
    frm = sys.argv[1] if len(sys.argv) > 1 else "2023-01"
    to = sys.argv[2] if len(sys.argv) > 2 else f"{today.year:04d}-{today.month:02d}"
    run(frm, to)
