"""
Replenishment MVP - Forecast Logic

V1: demand per future day = historical average for same weekday.
No seasonality, no future order commitments.
"""
from datetime import date


def get_forecast_for_dates(item_codes: list, dates: list, weekday_avg_map: dict) -> dict:
    result = {}
    for item_code in item_codes:
        item_avgs = weekday_avg_map.get(item_code, {})
        result[item_code] = {}
        for d in dates:
            wd = d.weekday()
            result[item_code][d] = item_avgs.get(wd, 0.0)
    return result
