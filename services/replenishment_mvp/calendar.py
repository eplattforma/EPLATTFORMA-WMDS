"""
Replenishment MVP - Calendar Logic

Handles date calculations for Tuesday/Friday ordering cycles.
Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4
"""
from datetime import date, timedelta


def get_receipt_date(run_date: date, run_type: str) -> date:
    if run_type == 'tuesday':
        days_ahead = (3 - run_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return run_date + timedelta(days=days_ahead)
    elif run_type == 'friday':
        days_ahead = (1 - run_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return run_date + timedelta(days=days_ahead)
    else:
        raise ValueError(f"Unknown run_type: {run_type}")


def _next_weekday(from_date: date, weekday: int) -> date:
    days_ahead = (weekday - from_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_date + timedelta(days=days_ahead)


def get_pre_receipt_dates(run_date: date, run_type: str, include_today_demand: bool) -> list:
    if run_type == 'tuesday':
        if include_today_demand:
            return [run_date, run_date + timedelta(days=1)]
        else:
            return [run_date + timedelta(days=1)]
    elif run_type == 'friday':
        if include_today_demand:
            monday = _next_weekday(run_date, 0)
            return [run_date, monday]
        else:
            monday = _next_weekday(run_date, 0)
            return [monday]
    else:
        raise ValueError(f"Unknown run_type: {run_type}")


def get_cover_dates_after_receipt(receipt_date: date, run_type: str) -> list:
    if run_type == 'tuesday':
        fri = _next_weekday(receipt_date, 4)
        mon = _next_weekday(fri, 0)
        tue = _next_weekday(fri, 1)
        return [fri, mon, tue]
    elif run_type == 'friday':
        wed = _next_weekday(receipt_date, 2)
        thu = _next_weekday(receipt_date, 3)
        return [wed, thu]
    else:
        raise ValueError(f"Unknown run_type: {run_type}")
