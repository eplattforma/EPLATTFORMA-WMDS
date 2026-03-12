"""
Replenishment MVP - Calendar Logic

Simple 7-day stock cover from run date.
"""
from datetime import date, timedelta

COVER_DAYS = 7


def get_cover_dates(run_date: date) -> list:
    return [run_date + timedelta(days=i) for i in range(1, COVER_DAYS + 1)]
