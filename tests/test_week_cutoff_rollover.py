"""Task #29 — Configurable Forecast Week Cutoff: unit tests for week_utils.py.

All tests are pure-Python; no Flask app context is required because the
injection overrides (``rollover_weekday``, ``rollover_time``, ``_now_athens``)
bypass the DB call entirely.

Test matrix
-----------
T1  Before Friday rollover → this Monday returned (current week excluded).
T2  Exactly at Friday rollover → next Monday returned (current week included).
T3  After Friday rollover (same day) → next Monday returned.
T4  Saturday after rollover → next Monday returned.
T5  Monday before rollover → this Monday returned.
T6  monday_of() unchanged for a known date.
T7  monday_of() works for a date that is already Monday.
T8  get_data_through_date before rollover → last Sunday of prev week.
T9  get_data_through_date after rollover → Sunday ending current week.
T10 Rollover on Wednesday 08:00 — before moment → this Monday.
T11 Rollover on Wednesday 08:00 — after moment → next Monday.
T12 Rollover weekday=0 (Monday) — first moment of week crosses threshold.
T13 get_completed_week_cutoff accepts rollover_time as string only (no DB).
T14 get_data_through_date is always one day before get_completed_week_cutoff.
"""

import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.forecast.week_utils import (
    monday_of,
    get_completed_week_cutoff,
    get_data_through_date,
)

_ATH = ZoneInfo("Europe/Athens")


def _athens(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, 0, tzinfo=_ATH)


# ---------------------------------------------------------------------------
# T1 — Friday before 10:00 → current week excluded
# ---------------------------------------------------------------------------

def test_t1_before_friday_rollover_excludes_current_week():
    # Wednesday of some week, well before Friday rollover
    now = _athens(2025, 5, 7, 9, 0)   # 2025-05-07 is Wednesday
    cutoff = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 7))
    assert cutoff == this_monday, f"expected {this_monday}, got {cutoff}"


# ---------------------------------------------------------------------------
# T2 — Exactly at rollover moment → current week included
# ---------------------------------------------------------------------------

def test_t2_exactly_at_friday_rollover_includes_current_week():
    # Friday 10:00:00 exactly
    now = _athens(2025, 5, 9, 10, 0)   # 2025-05-09 is Friday
    cutoff = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 9))
    next_monday = this_monday + timedelta(weeks=1)
    assert cutoff == next_monday, f"expected {next_monday}, got {cutoff}"


# ---------------------------------------------------------------------------
# T3 — Friday after rollover (same day) → current week included
# ---------------------------------------------------------------------------

def test_t3_after_friday_rollover_same_day_includes_current_week():
    now = _athens(2025, 5, 9, 14, 30)   # Friday 14:30
    cutoff = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 9))
    next_monday = this_monday + timedelta(weeks=1)
    assert cutoff == next_monday


# ---------------------------------------------------------------------------
# T4 — Saturday (after Friday rollover) → current week still included
# ---------------------------------------------------------------------------

def test_t4_saturday_after_rollover_includes_current_week():
    now = _athens(2025, 5, 10, 8, 0)   # Saturday
    cutoff = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 10))
    next_monday = this_monday + timedelta(weeks=1)
    assert cutoff == next_monday


# ---------------------------------------------------------------------------
# T5 — Monday 09:00 (rollover is Friday) → current week excluded
# ---------------------------------------------------------------------------

def test_t5_monday_before_rollover_excludes_current_week():
    now = _athens(2025, 5, 5, 9, 0)   # Monday
    cutoff = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 5))
    assert cutoff == this_monday


# ---------------------------------------------------------------------------
# T6 — monday_of() for a known mid-week date
# ---------------------------------------------------------------------------

def test_t6_monday_of_mid_week():
    assert monday_of(date(2025, 5, 7)) == date(2025, 5, 5)   # Wed → Mon


# ---------------------------------------------------------------------------
# T7 — monday_of() for a date that is already Monday
# ---------------------------------------------------------------------------

def test_t7_monday_of_already_monday():
    assert monday_of(date(2025, 5, 5)) == date(2025, 5, 5)


# ---------------------------------------------------------------------------
# T8 — get_data_through_date before rollover → last Sunday of prev week
# ---------------------------------------------------------------------------

def test_t8_data_through_before_rollover_is_prev_sunday():
    # Wednesday 09:00, rollover Friday
    now = _athens(2025, 5, 7, 9, 0)
    dt = get_data_through_date(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 7))
    prev_sunday = this_monday - timedelta(days=1)
    assert dt == prev_sunday, f"expected {prev_sunday}, got {dt}"
    assert dt.weekday() == 6, "data-through should be a Sunday"


# ---------------------------------------------------------------------------
# T9 — get_data_through_date after rollover → Sunday ending current week
# ---------------------------------------------------------------------------

def test_t9_data_through_after_rollover_is_current_week_sunday():
    now = _athens(2025, 5, 9, 11, 0)   # Friday 11:00 — past rollover
    dt = get_data_through_date(
        rollover_weekday=4,
        rollover_time="10:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 9))
    current_week_sunday = this_monday + timedelta(days=6)
    assert dt == current_week_sunday, f"expected {current_week_sunday}, got {dt}"
    assert dt.weekday() == 6


# ---------------------------------------------------------------------------
# T10 — Custom rollover Wed 08:00 — before moment → this Monday
# ---------------------------------------------------------------------------

def test_t10_custom_wed_rollover_before():
    now = _athens(2025, 5, 7, 7, 59)   # Wednesday 07:59
    cutoff = get_completed_week_cutoff(
        rollover_weekday=2,
        rollover_time="08:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 7))
    assert cutoff == this_monday


# ---------------------------------------------------------------------------
# T11 — Custom rollover Wed 08:00 — after moment → next Monday
# ---------------------------------------------------------------------------

def test_t11_custom_wed_rollover_after():
    now = _athens(2025, 5, 7, 8, 1)   # Wednesday 08:01
    cutoff = get_completed_week_cutoff(
        rollover_weekday=2,
        rollover_time="08:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 7))
    assert cutoff == this_monday + timedelta(weeks=1)


# ---------------------------------------------------------------------------
# T12 — Rollover weekday=0 (Monday) — first moment of the week
# ---------------------------------------------------------------------------

def test_t12_monday_rollover_at_start_of_week():
    # Monday 05:59 — before rollover at 06:00
    now = _athens(2025, 5, 5, 5, 59)
    cutoff_before = get_completed_week_cutoff(
        rollover_weekday=0,
        rollover_time="06:00",
        _now_athens=now,
    )
    this_monday = monday_of(date(2025, 5, 5))
    assert cutoff_before == this_monday   # still excluded

    # Monday 06:00 — exactly at rollover
    now2 = _athens(2025, 5, 5, 6, 0)
    cutoff_at = get_completed_week_cutoff(
        rollover_weekday=0,
        rollover_time="06:00",
        _now_athens=now2,
    )
    assert cutoff_at == this_monday + timedelta(weeks=1)   # included


# ---------------------------------------------------------------------------
# T13 — rollover_time as string passed directly (no DB call needed)
# ---------------------------------------------------------------------------

def test_t13_rollover_time_string_override():
    now = _athens(2025, 5, 9, 13, 0)   # Friday 13:00
    cutoff = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="14:00",   # rollover is at 14:00
        _now_athens=now,
    )
    # 13:00 < 14:00 → not yet crossed → this Monday
    this_monday = monday_of(date(2025, 5, 9))
    assert cutoff == this_monday

    cutoff2 = get_completed_week_cutoff(
        rollover_weekday=4,
        rollover_time="12:00",   # rollover was at 12:00 → already passed
        _now_athens=now,
    )
    assert cutoff2 == this_monday + timedelta(weeks=1)


# ---------------------------------------------------------------------------
# T14 — get_data_through_date is always cutoff - 1 day
# ---------------------------------------------------------------------------

def test_t14_data_through_is_cutoff_minus_one():
    for wd in range(7):
        for hour in (0, 6, 12, 23):
            now = _athens(2025, 5, 7, hour, 0)
            cutoff = get_completed_week_cutoff(
                rollover_weekday=wd,
                rollover_time=f"{hour:02d}:00",
                _now_athens=now,
            )
            dt = get_data_through_date(
                rollover_weekday=wd,
                rollover_time=f"{hour:02d}:00",
                _now_athens=now,
            )
            assert dt == cutoff - timedelta(days=1), (
                f"wd={wd} hour={hour}: data_through={dt} cutoff={cutoff}"
            )
