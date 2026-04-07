import logging
from datetime import date, timedelta
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)

OOS_THRESHOLD_DAYS = 3


def get_oos_days_by_week(session: Session, item_code: str, num_weeks: int = 26):
    completed_week_cutoff = get_completed_week_cutoff()
    window_start = completed_week_cutoff - timedelta(weeks=num_weeks)

    rows = session.execute(
        text("""
            SELECT snapshot_date
            FROM ps365_oos_777_daily
            WHERE item_code_365 = :item_code
              AND snapshot_date >= :start
              AND snapshot_date < :end
            ORDER BY snapshot_date
        """),
        {"item_code": item_code, "start": window_start, "end": completed_week_cutoff},
    ).fetchall()

    oos_by_week = {}
    for (snap_date,) in rows:
        if isinstance(snap_date, str):
            snap_date = date.fromisoformat(snap_date)
        week_start = snap_date - timedelta(days=snap_date.weekday())
        if week_start not in oos_by_week:
            oos_by_week[week_start] = 0
        oos_by_week[week_start] += 1

    result = []
    ws = window_start
    while ws < completed_week_cutoff and len(result) < num_weeks:
        result.append({
            "week_start": ws,
            "oos_days": oos_by_week.get(ws, 0),
        })
        ws += timedelta(weeks=1)

    return result


def get_oos_weeks_set(session: Session, item_code: str, num_weeks: int = 26,
                      threshold: int = OOS_THRESHOLD_DAYS):
    weekly = get_oos_days_by_week(session, item_code, num_weeks)
    return {w["week_start"] for w in weekly if w["oos_days"] >= threshold}


def bulk_get_oos_weeks(session: Session, num_weeks: int = 26,
                       threshold: int = OOS_THRESHOLD_DAYS):
    completed_week_cutoff = get_completed_week_cutoff()
    window_start = completed_week_cutoff - timedelta(weeks=num_weeks)

    rows = session.execute(
        text("""
            SELECT item_code_365, snapshot_date
            FROM ps365_oos_777_daily
            WHERE snapshot_date >= :start
              AND snapshot_date < :end
            ORDER BY item_code_365, snapshot_date
        """),
        {"start": window_start, "end": completed_week_cutoff},
    ).fetchall()

    item_week_days = defaultdict(lambda: defaultdict(int))
    for item_code, snap_date in rows:
        if isinstance(snap_date, str):
            snap_date = date.fromisoformat(snap_date)
        week_start = snap_date - timedelta(days=snap_date.weekday())
        item_week_days[item_code][week_start] += 1

    result = {}
    for item_code, week_counts in item_week_days.items():
        oos_weeks = {ws for ws, days in week_counts.items() if days >= threshold}
        if oos_weeks:
            result[item_code] = oos_weeks

    logger.info(
        f"OOS bulk load: {len(result)} items with OOS-impacted weeks "
        f"(threshold={threshold} days, window={num_weeks}w)"
    )
    return result


def bulk_get_oos_total_days(session: Session, num_weeks: int = 8):
    today = date.today()
    window_start = today - timedelta(days=num_weeks * 7)

    rows = session.execute(
        text("""
            SELECT item_code_365, COUNT(*) as oos_days
            FROM ps365_oos_777_daily
            WHERE snapshot_date >= :start
              AND snapshot_date <= :today
            GROUP BY item_code_365
        """),
        {"start": window_start, "today": today},
    ).fetchall()

    return {item_code: oos_days for item_code, oos_days in rows}


def filter_oos_weeks(weekly_qtys, week_starts, oos_weeks_set, min_clean_weeks=4):
    clean_qtys = []
    for qty, ws in zip(weekly_qtys, week_starts):
        if ws not in oos_weeks_set:
            clean_qtys.append(qty)

    if len(clean_qtys) < min_clean_weeks:
        return weekly_qtys, False

    return clean_qtys, True
