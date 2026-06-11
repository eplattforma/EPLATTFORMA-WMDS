from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_ATHENS_TZ = ZoneInfo("Europe/Athens")
_DEFAULT_ROLLOVER_WEEKDAY = 4    # Friday (0=Mon … 6=Sun)
_DEFAULT_ROLLOVER_TIME = "10:00"


def monday_of(d: date) -> date:
    """Get the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _get_rollover_config():
    """Read rollover weekday (int 0-6) and hour/minute from settings, with safe fallback."""
    try:
        from app import db
        from models import Setting
        wd_str = Setting.get(db.session, 'forecast_week_rollover_weekday',
                              str(_DEFAULT_ROLLOVER_WEEKDAY))
        tm_str = Setting.get(db.session, 'forecast_week_rollover_time',
                              _DEFAULT_ROLLOVER_TIME)
        weekday = int(wd_str)
        if not (0 <= weekday <= 6):
            weekday = _DEFAULT_ROLLOVER_WEEKDAY
        h, m = tm_str.split(':')
        hour, minute = int(h), int(m)
    except Exception:
        weekday = _DEFAULT_ROLLOVER_WEEKDAY
        hour, minute = 10, 0
    return weekday, hour, minute


def get_completed_week_cutoff(
    *,
    rollover_weekday: Optional[int] = None,
    rollover_time: Optional[str] = None,
    _now_athens: Optional[datetime] = None,
) -> date:
    """Return the exclusive week_start upper bound for the forecast.

    SQL pattern used throughout the app: ``WHERE week_start < :week_cutoff``.

    A week is only complete when 'now' is strictly past the last day of that
    week (its Sunday). The current week is always in progress, so the cutoff
    is always this_monday: the latest completed week is the one starting
    this_monday - 7 days. Rollover settings no longer shift the cutoff —
    a partial week must never enter the forecast history window.

    Default rollover: Friday 10:00 Athens time (``forecast_week_rollover_weekday=4``,
    ``forecast_week_rollover_time="10:00"``).

    Keyword-only overrides (``rollover_weekday``, ``rollover_time``, ``_now_athens``)
    are accepted so unit tests can inject values without touching the database or clock.
    """
    if rollover_weekday is None or rollover_time is None:
        db_weekday, db_hour, db_minute = _get_rollover_config()
        if rollover_weekday is None:
            rollover_weekday = db_weekday
        if rollover_time is None:
            rollover_time = f"{db_hour:02d}:{db_minute:02d}"

    h, m = rollover_time.split(':')
    hour, minute = int(h), int(m)

    now_athens = _now_athens if _now_athens is not None else datetime.now(tz=_ATHENS_TZ)
    this_monday = monday_of(now_athens.date())

    # Invariant: a week is only complete when 'now' is strictly past its last
    # day (Sunday). The current week (starting this_monday) is by definition
    # still in progress, so it must never enter the history window, regardless
    # of the configured rollover weekday/time. The exclusive cutoff is
    # therefore always this_monday. rollover_weekday / hour / minute are kept
    # in the signature for backward compatibility with callers and tests, but
    # they no longer shift the cutoff forward.
    _ = (rollover_weekday, hour, minute)
    return this_monday


def get_data_through_date(
    *,
    rollover_weekday: Optional[int] = None,
    rollover_time: Optional[str] = None,
    _now_athens: Optional[datetime] = None,
) -> date:
    """Return the last calendar day included in the forecast window.

    Always equal to ``get_completed_week_cutoff(...) - 1 day``.

    * Current week excluded → last Sunday of the *previous* week.
    * Current week included → the Sunday ending the *current* ISO week.
    """
    cutoff = get_completed_week_cutoff(
        rollover_weekday=rollover_weekday,
        rollover_time=rollover_time,
        _now_athens=_now_athens,
    )
    return cutoff - timedelta(days=1)
