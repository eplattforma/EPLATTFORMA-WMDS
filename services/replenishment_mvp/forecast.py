"""
Replenishment MVP - Forecast Logic

V1.1: Three-tier forecast with fallback:
  A. Same-weekday average (from weekday_avg_map)
  B/C. Fallback daily average (from fallback_avg_map, already resolved)
  D. Zero → caller handles MANUAL_REVIEW_REQUIRED

Per-item forecast_source tracked for audit.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)


def get_forecast_for_dates(item_codes: list, dates: list, weekday_avg_map: dict,
                           fallback_avg_map: dict = None) -> dict:
    result = {}
    for item_code in item_codes:
        item_avgs = weekday_avg_map.get(item_code, {})

        fallback_info = (fallback_avg_map or {}).get(item_code, {})
        fallback_daily = fallback_info.get("daily_avg", 0.0) if fallback_info else 0.0

        result[item_code] = {}
        for d in dates:
            wd = d.weekday()
            wd_avg = item_avgs.get(wd, 0.0)

            if wd_avg > 0:
                result[item_code][d] = wd_avg
            elif fallback_daily > 0:
                result[item_code][d] = fallback_daily
            else:
                result[item_code][d] = 0.0

    return result


def resolve_forecast_sources(item_codes: list, weekday_avg_map: dict,
                             fallback_avg_map: dict = None) -> dict:
    sources = {}
    for item_code in item_codes:
        item_avgs = weekday_avg_map.get(item_code, {})
        has_weekday_data = any(v > 0 for v in item_avgs.values())

        if has_weekday_data:
            sources[item_code] = "weekday_average"
        else:
            fallback_info = (fallback_avg_map or {}).get(item_code, {})
            fb_source = fallback_info.get("source", "none") if fallback_info else "none"
            if fb_source != "none":
                sources[item_code] = fb_source
            else:
                sources[item_code] = "none"
    return sources
