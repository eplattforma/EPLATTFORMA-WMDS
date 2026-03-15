import pytz
from datetime import date, datetime, time, timedelta

ATHENS_TZ = pytz.timezone("Europe/Athens")

DOW_INT_MAP = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


def iso_week_parity(d: date) -> int:
    wk = d.isocalendar().week
    return 2 if (wk % 2 == 0) else 1


def next_delivery_date_for_slot(dow_int: int, week_code: int, from_date: date | None = None) -> date:
    if not from_date:
        from_date = date.today()

    target_wd = DOW_INT_MAP.get(dow_int, dow_int - 1)
    target_parity = int(week_code)

    for i in range(0, 28):
        d = from_date + timedelta(days=i)
        if d.weekday() == target_wd and iso_week_parity(d) == target_parity:
            return d

    return from_date + timedelta(days=14)


def subtract_working_minutes(dt_local: datetime, minutes: int) -> datetime:
    remaining = minutes
    cur = dt_local
    step = timedelta(minutes=1)

    while remaining > 0:
        cur = cur - step
        if cur.weekday() in (5, 6):
            continue
        remaining -= 1

    return cur


def order_window_open_at(delivery_date: date, window_hours: int, anchor_time_str: str = "00:01") -> datetime:
    hh, mm = anchor_time_str.split(":")
    delivery_anchor_local = ATHENS_TZ.localize(
        datetime.combine(delivery_date, time(int(hh), int(mm)))
    )
    open_local = subtract_working_minutes(delivery_anchor_local, window_hours * 60)
    return open_local


def get_customer_window_status(
    slots: list,
    last_invoice_date: date | None,
    open_order_count: int,
    window_hours: int,
    anchor_time_str: str,
    now_local: datetime | None = None,
) -> dict:
    if now_local is None:
        now_local = datetime.now(ATHENS_TZ)

    if not slots:
        return {
            "done_for_window": False,
            "done_source": "NONE",
            "window_open": False,
            "next_delivery": None,
            "window_open_at": None,
        }

    best_delivery = None
    best_open = None
    best_anchor = None
    best_window_open = False

    for s in slots:
        delivery = next_delivery_date_for_slot(s["dow"], s["week_code"], from_date=now_local.date())
        open_at = order_window_open_at(delivery, window_hours, anchor_time_str)

        hh, mm = anchor_time_str.split(":")
        anchor = ATHENS_TZ.localize(
            datetime.combine(delivery, time(int(hh), int(mm)))
        )

        is_open = open_at <= now_local < anchor

        if best_delivery is None or delivery < best_delivery:
            best_delivery = delivery
            best_open = open_at
            best_anchor = anchor
            best_window_open = is_open

    done_for_window = False
    done_source = "NONE"

    if open_order_count and open_order_count > 0:
        done_for_window = True
        done_source = "OPEN_ORDER"
    elif last_invoice_date and best_open and last_invoice_date >= best_open.date():
        done_for_window = True
        done_source = "INVOICE"

    return {
        "done_for_window": done_for_window,
        "done_source": done_source,
        "window_open": best_window_open,
        "next_delivery": best_delivery,
        "window_open_at": best_open,
    }
