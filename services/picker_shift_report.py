"""Unified end-of-shift performance report for pickers.

Builds the four report sections (speed & throughput, occupancy &
utilization, quality & accuracy, 7-day trends) from real per-pick data:

* ``item_time_tracking``        — per-pick timings and quality flags
* ``shifts`` / ``idle_periods`` — check-in/out bounds and manual breaks

Work days are defined in the configured system timezone: local midnight
bounds are converted to UTC before filtering the (UTC-stored) timestamps,
so early-morning or late-evening picks land on the correct local day.

Occupancy is computed from order-boundary "islands" (time inside open
orders), so automatic shift-close padding never inflates occupied time.

All sections degrade to explicit ``None`` values when the underlying data
is missing; genuine query failures are recorded in ``report['errors']``
so the UI can distinguish "no data" from "report unavailable".
"""
import logging
from datetime import date, datetime, time, timedelta

import pytz
from sqlalchemy import text

from app import db
from timezone_utils import get_system_timezone

logger = logging.getLogger(__name__)

# Predicate for a "completed pick" — the single population used for every
# speed and quality metric so numerators and denominators always agree.
_COMPLETED = "NOT was_skipped AND total_item_time > 0"


def _day_bounds_utc(work_date: date):
    """Return naive-UTC [start, end) bounds for a local calendar day."""
    tz = get_system_timezone()
    start_local = tz.localize(datetime.combine(work_date, time.min))
    end_local = tz.localize(datetime.combine(work_date + timedelta(days=1), time.min))
    return (start_local.astimezone(pytz.UTC).replace(tzinfo=None),
            end_local.astimezone(pytz.UTC).replace(tzinfo=None))


def _rows(sql, **params):
    return db.session.execute(text(sql), params).mappings().all()


def _row(sql, **params):
    got = _rows(sql, **params)
    return got[0] if got else None


def build_shift_report(picker: str, work_date: date) -> dict:
    """Assemble the unified end-of-shift report for one picker + local day."""
    report = {
        'picker': picker,
        'work_date': work_date,
        'speed': None,
        'occupancy': None,
        'quality': None,
        'trends': [],
        'trend_summary': None,
        'shifts': [],
        'insights': [],
        'errors': [],   # section names whose queries failed (not "no data")
    }

    tz_name = str(get_system_timezone())
    utc_start, utc_end = _day_bounds_utc(work_date)

    # ---- 1. Speed & throughput + quality (single scan) -------------------
    try:
        speed = _row(
            f"""
            SELECT
              count(*) FILTER (WHERE {_COMPLETED})       AS items_completed,
              coalesce(sum(quantity_picked)
                       FILTER (WHERE {_COMPLETED}), 0)   AS total_quantity,
              count(DISTINCT invoice_no)
                FILTER (WHERE {_COMPLETED})              AS orders_completed,
              round(avg(total_item_time)
                    FILTER (WHERE {_COMPLETED})::numeric, 1)
                                                         AS avg_time_per_item_sec,
              round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_item_time)
                    FILTER (WHERE {_COMPLETED})::numeric, 1)
                                                         AS median_time_per_item_sec,
              round((count(*) FILTER (WHERE {_COMPLETED}) * 3600.0
                     / nullif(sum(total_item_time)
                              FILTER (WHERE {_COMPLETED}), 0))::numeric, 0)
                                                         AS items_per_hour,
              round((sum(total_item_time)
                     FILTER (WHERE {_COMPLETED} AND expected_time > 0)
                     / nullif(sum(expected_time)
                              FILTER (WHERE {_COMPLETED} AND expected_time > 0), 0))::numeric, 2)
                                                         AS efficiency_ratio,
              round((sum(picking_time)
                     FILTER (WHERE {_COMPLETED}) / 60.0)::numeric, 0)
                                                         AS picking_min,
              round((sum(walking_time)
                     FILTER (WHERE {_COMPLETED}) / 60.0)::numeric, 0)
                                                         AS walking_min,
              round((sum(confirmation_time)
                     FILTER (WHERE {_COMPLETED}) / 60.0)::numeric, 0)
                                                         AS confirmation_min,
              -- quality: errors restricted to the SAME completed population
              count(*) FILTER (WHERE was_skipped)        AS items_skipped,
              count(*) FILTER (WHERE {_COMPLETED} AND picked_correctly = false)
                                                         AS error_count,
              count(*) FILTER (WHERE {_COMPLETED} AND expected_time > 0
                               AND total_item_time > 2 * expected_time)
                                                         AS very_slow_picks,
              count(*) FILTER (WHERE {_COMPLETED} AND expected_time > 0
                               AND total_item_time > expected_time
                               AND total_item_time <= 2 * expected_time)
                                                         AS slow_picks,
              count(*) FILTER (WHERE {_COMPLETED} AND expected_time > 0
                               AND total_item_time <= expected_time)
                                                         AS on_target_picks
            FROM item_time_tracking
            WHERE picker_username = :picker
              AND item_started >= :t0 AND item_started < :t1
            """,
            picker=picker, t0=utc_start, t1=utc_end)
    except Exception:
        logger.exception("shift report: speed query failed for %s %s", picker, work_date)
        db.session.rollback()
        speed = None
        report['errors'].append('speed')

    if speed and (speed['items_completed'] or 0) > 0:
        s = dict(speed)
        total_picks = (s['items_completed'] or 0) + (s['items_skipped'] or 0)
        correct = (s['items_completed'] or 0) - (s['error_count'] or 0)
        s['skip_rate_pct'] = round(100.0 * (s['items_skipped'] or 0) / total_picks, 1) if total_picks else None
        s['accuracy_pct'] = (round(100.0 * max(correct, 0) / s['items_completed'], 1)
                             if s['items_completed'] else None)
        report['speed'] = s
        report['quality'] = {
            'total_picks': total_picks,
            'items_completed': s['items_completed'],
            'items_skipped': s['items_skipped'],
            'skip_rate_pct': s['skip_rate_pct'],
            'error_count': s['error_count'],
            'correct_picks': max(correct, 0),
            'accuracy_pct': s['accuracy_pct'],
            'very_slow_picks': s['very_slow_picks'],
            'slow_picks': s['slow_picks'],
            'on_target_picks': s['on_target_picks'],
        }

    # ---- 2. Occupancy & utilization --------------------------------------
    # Order-boundary islands computed inline with local-day UTC bounds
    # (the shared vw_picker_occupancy_daily buckets by UTC date, which is
    # wrong near local-day boundaries). Auto-close padding never counted.
    try:
        occ = _row(
            """
            WITH iv AS (
              SELECT t.invoice_no,
                     min(t.item_started)                                      AS s,
                     coalesce(i.packing_complete_time, max(t.item_completed)) AS e
              FROM item_time_tracking t
              JOIN invoices i ON i.invoice_no = t.invoice_no
              WHERE t.picker_username = :picker AND t.was_skipped = false
                AND t.item_started >= :t0 AND t.item_started < :t1
              GROUP BY t.invoice_no, i.packing_complete_time
              HAVING coalesce(i.packing_complete_time, max(t.item_completed)) IS NOT NULL),
            o AS (SELECT s, e,
                    max(e) OVER (ORDER BY s
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) pm
                  FROM iv),
            g AS (SELECT s, e,
                    sum(CASE WHEN pm IS NULL OR s > pm THEN 1 ELSE 0 END)
                      OVER (ORDER BY s) grp
                  FROM o),
            isl AS (SELECT min(s) a, max(e) b FROM g GROUP BY grp),
            gaps AS (SELECT a, b,
                       EXTRACT(epoch FROM (a - lag(b) OVER (ORDER BY a)))/60.0 AS gap
                     FROM isl)
            SELECT
              min(a) AS first_order, max(b) AS last_order_end,
              round((EXTRACT(epoch FROM (max(b)-min(a)))/60.0)::numeric,0)  AS span_min,
              round((sum(EXTRACT(epoch FROM (b-a)))/60.0)::numeric,0)       AS occupied_min,
              round(((EXTRACT(epoch FROM (max(b)-min(a)))
                      - sum(EXTRACT(epoch FROM (b-a))))/60.0)::numeric,0)   AS idle_min,
              round((100.0*sum(EXTRACT(epoch FROM (b-a)))
                     / nullif(EXTRACT(epoch FROM (max(b)-min(a))),0))::numeric,0) AS occupancy_pct,
              count(*) FILTER (WHERE gap > 1)  AS idle_gaps,
              round(max(gap)::numeric,0)       AS longest_idle_min
            FROM gaps
            HAVING count(*) > 0
            """,
            picker=picker, t0=utc_start, t1=utc_end)
    except Exception:
        logger.exception("shift report: occupancy query failed for %s %s", picker, work_date)
        db.session.rollback()
        occ = None
        report['errors'].append('occupancy')

    shifts_info, break_min = [], 0
    try:
        shift_rows = _rows(
            """
            SELECT s.id, s.check_in_time, s.check_out_time, s.status,
                   s.total_duration_minutes,
                   coalesce((SELECT sum(ip.duration_minutes)
                             FROM idle_periods ip
                             WHERE ip.shift_id = s.id AND ip.is_break = true
                               AND ip.end_time IS NOT NULL), 0) AS break_min,
                   EXISTS (SELECT 1 FROM activity_logs al
                           WHERE al.picker_username = s.picker_username
                             AND al.activity_type LIKE 'auto_checkout%'
                             AND al.timestamp >= s.check_in_time
                             AND (s.check_out_time IS NULL
                                  OR al.timestamp <= s.check_out_time + interval '5 minutes')
                          ) AS auto_closed
            FROM shifts s
            WHERE s.picker_username = :picker
              AND s.check_in_time >= :t0 AND s.check_in_time < :t1
            ORDER BY s.check_in_time
            """,
            picker=picker, t0=utc_start, t1=utc_end)
        shifts_info = [dict(r) for r in shift_rows]
        break_min = sum(int(r['break_min'] or 0) for r in shifts_info)
    except Exception:
        logger.exception("shift report: shift query failed for %s %s", picker, work_date)
        db.session.rollback()
        report['errors'].append('shifts')

    shift_total_min = sum(int(r['total_duration_minutes'] or 0) for r in shifts_info) or None
    any_auto_closed = any(r.get('auto_closed') for r in shifts_info)

    if occ and occ['span_min'] is not None:
        o = dict(occ)
        o['break_min'] = break_min
        o['unclassified_idle_min'] = max(int(o['idle_min'] or 0) - break_min, 0)
        o['shift_total_min'] = shift_total_min
        o['auto_closed'] = any_auto_closed
        report['occupancy'] = o
    elif shifts_info:
        report['occupancy'] = {
            'span_min': None, 'occupied_min': None, 'idle_min': None,
            'occupancy_pct': None, 'idle_gaps': None, 'longest_idle_min': None,
            'first_order': None, 'last_order_end': None,
            'break_min': break_min, 'unclassified_idle_min': None,
            'shift_total_min': shift_total_min, 'auto_closed': any_auto_closed,
        }
    report['shifts'] = shifts_info

    # ---- 3. 7-day trends (bucketed by LOCAL date) --------------------------
    trend_start, _ = _day_bounds_utc(work_date - timedelta(days=6))
    try:
        trend_rows = _rows(
            f"""
            SELECT (t.item_started AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date
                                                            AS work_date,
                   count(*) FILTER (WHERE {_COMPLETED})     AS items,
                   round(avg(t.total_item_time)
                         FILTER (WHERE {_COMPLETED})::numeric, 1)
                                                            AS avg_time_sec,
                   round((100.0 * count(*) FILTER (
                            WHERE {_COMPLETED} AND t.expected_time > 0
                              AND t.total_item_time <= t.expected_time)
                          / nullif(count(*) FILTER (
                            WHERE {_COMPLETED} AND t.expected_time > 0), 0))::numeric, 0)
                                                            AS pct_on_target,
                   round((100.0 * count(*) FILTER (
                            WHERE {_COMPLETED} AND t.picked_correctly IS DISTINCT FROM false)
                          / nullif(count(*) FILTER (WHERE {_COMPLETED}), 0))::numeric, 1)
                                                            AS accuracy_pct
            FROM item_time_tracking t
            WHERE t.picker_username = :picker
              AND t.item_started >= :t0 AND t.item_started < :t1
            GROUP BY 1
            HAVING count(*) FILTER (WHERE {_COMPLETED}) > 0
            ORDER BY 1 DESC
            """,
            picker=picker, tz=tz_name, t0=trend_start, t1=utc_end)
        report['trends'] = [dict(r) for r in trend_rows]
    except Exception:
        logger.exception("shift report: trends query failed for %s %s", picker, work_date)
        db.session.rollback()
        report['errors'].append('trends')

    if report['trends']:
        items = [r['items'] for r in report['trends'] if r['items']]
        times = [float(r['avg_time_sec']) for r in report['trends'] if r['avg_time_sec'] is not None]
        if items:
            avg_items = sum(items) / len(items)
            report['trend_summary'] = {
                'days': len(report['trends']),
                'avg_items': round(avg_items, 0),
                'avg_time_sec': round(sum(times) / len(times), 1) if times else None,
                'items_variance_pct': (round(100.0 * (max(items) - min(items)) / avg_items, 0)
                                       if avg_items else None),
            }

    # ---- 4. Plain-language insights ---------------------------------------
    ins = report['insights']
    sp, oc, q = report['speed'], report['occupancy'], report['quality']
    if sp:
        if sp['efficiency_ratio'] is not None:
            r = float(sp['efficiency_ratio'])
            if r <= 1.0:
                ins.append(('good', f"Picking pace beat the estimate ({int(round((1 - r) * 100))}% faster overall)."))
            elif r <= 1.25:
                ins.append(('ok', f"Picking pace within normal range ({int(round((r - 1) * 100))}% over estimate)."))
            else:
                ins.append(('watch', f"Picking ran {int(round((r - 1) * 100))}% over the estimated time — check the slow picks below."))
    if q:
        if q['accuracy_pct'] is not None and q['accuracy_pct'] >= 98:
            ins.append(('good', f"Accuracy {q['accuracy_pct']}% — excellent."))
        elif q['accuracy_pct'] is not None:
            ins.append(('watch', f"Accuracy {q['accuracy_pct']}% — below the 98% target."))
        if q['items_skipped']:
            ins.append(('watch', f"{q['items_skipped']} item(s) skipped."))
    if oc:
        if oc.get('occupancy_pct') is not None:
            p = int(oc['occupancy_pct'])
            if 60 <= p <= 85:
                ins.append(('good', f"Occupancy {p}% — in the healthy range."))
            elif p < 60:
                ins.append(('watch', f"Occupancy {p}% — a lot of time between orders."))
            else:
                ins.append(('ok', f"Occupancy {p}% — very dense day."))
        if oc.get('auto_closed'):
            ins.append(('note', "Shift was closed automatically — shift duration may be padded; order-span numbers above are still accurate."))
    if not sp and 'speed' not in report['errors']:
        ins.append(('note', "No completed picks recorded for this day."))

    return report


def pickers_with_data(limit_days: int = 60):
    """Usernames that actually have pick data (for the report picker list)."""
    try:
        return [r['picker_username'] for r in _rows(
            """
            SELECT DISTINCT picker_username FROM item_time_tracking
            WHERE picker_username <> 'administrator'
              AND item_started >= CURRENT_DATE - :days * interval '1 day'
            ORDER BY picker_username
            """, days=limit_days)]
    except Exception:
        logger.exception("shift report: picker list failed")
        db.session.rollback()
        return []
