"""Picking report views + shelf-level backfill.

Creates two reporting views on top of the accurate per-pick table
``item_time_tracking`` so picker-speed reporting no longer relies on
``pbi_fact_picking`` order wall-clock durations (which include breaks
and interruptions):

* ``vw_pick_detail``  — one clean row per pick (excludes the
  ``administrator`` test user, skipped picks and zero/blank times),
  with ``met_target`` and ``long_gap`` flags and the shelf ``level``
  letter parsed from ``location``.
* ``vw_picker_daily`` — per picker per day: item count, units,
  MEDIAN seconds per pick, % of picks meeting target, walking share %
  and a count of long gaps to watch.

Also backfills ``item_time_tracking.level`` from ``location`` for
existing rows (idempotent — only touches rows where ``level`` IS NULL).

Nothing here deletes or alters existing tables, and ``pbi_fact_picking``
is left untouched (still used for order status / shipping / delivery).
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

VW_PICK_DETAIL_SQL = """
CREATE OR REPLACE VIEW vw_pick_detail AS
SELECT
  (item_started AT TIME ZONE 'UTC' AT TIME ZONE '{tz}')::date AS pick_date,
  picker_username                                     AS picker,
  invoice_no,
  corridor,
  substring(location from '\d{{2}}-\d{{2}}-([A-Z])')   AS level,
  unit_type,
  quantity_picked                                     AS units,
  round(walking_time::numeric, 1)                     AS walking_seconds,
  round(picking_time::numeric, 1)                     AS picking_seconds,
  round(total_item_time::numeric, 1)                  AS total_seconds,
  round(expected_time::numeric, 1)                    AS expected_seconds,
  (total_item_time <= expected_time)                  AS met_target,
  (walking_time > 60)                                 AS long_gap
FROM item_time_tracking
WHERE picker_username <> 'administrator'
  AND was_skipped = false
  AND total_item_time > 0
  AND expected_time  > 0
"""

VW_PICKER_DAILY_SQL = r"""
CREATE VIEW vw_picker_daily AS
SELECT
  pick_date,
  picker,
  count(*)                                            AS items_picked,
  sum(units)                                          AS units_picked,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_seconds)::numeric, 1)
                                                      AS median_seconds_per_pick,
  round(100.0 * avg(met_target::int), 0)              AS pct_meeting_target,
  round(sum(total_seconds) / 3600.0, 2)               AS active_pick_hours,
  round(100.0 * sum(walking_seconds) / nullif(sum(total_seconds), 0), 0)
                                                      AS walking_share_pct,
  sum(long_gap::int)                                  AS long_gaps_to_watch
FROM vw_pick_detail
GROUP BY pick_date, picker
ORDER BY pick_date DESC, items_picked DESC
"""

VW_IDLE_DEDICATED_SQL = """
CREATE OR REPLACE VIEW vw_idle_dedicated AS
SELECT
  (i.start_time AT TIME ZONE 'UTC' AT TIME ZONE '{tz}')::date                AS idle_date,
  sh.picker_username                                                          AS picker,
  round(sum(CASE WHEN i.duration_minutes <= 60 THEN i.duration_minutes ELSE 0 END)::numeric, 0) AS working_idle_min,
  sum(CASE WHEN i.duration_minutes <= 60 THEN 1 ELSE 0 END)                   AS working_idle_gaps,
  round(sum(CASE WHEN i.duration_minutes > 60 THEN i.duration_minutes ELSE 0 END)::numeric, 0)  AS long_absence_min_to_watch,
  sum(CASE WHEN i.duration_minutes > 60 THEN 1 ELSE 0 END)                    AS long_absence_gaps
FROM idle_periods i
JOIN shifts sh ON sh.id = i.shift_id
WHERE sh.picker_username IN (
  SELECT jsonb_array_elements_text(value::jsonb) FROM settings WHERE key = 'dedicated_pickers'
)
GROUP BY 1, 2
ORDER BY idle_date DESC
"""

# Order-boundary idle: an order occupies the picker from its first pick to
# packing_complete_time; overlapping (batch-picked) orders are merged into
# islands, and idle = gaps between islands. Bounded by the last packing of
# the day, so auto-close padding never enters it.
_ORDER_ISLANDS_CTE = """
WITH iv AS (
  SELECT t.picker_username,
         (t.item_started AT TIME ZONE 'UTC' AT TIME ZONE '{tz}')::date AS d,
         t.invoice_no,
         min(t.item_started)                                      AS s,
         coalesce(i.packing_complete_time, max(t.item_completed)) AS e
  FROM item_time_tracking t
  JOIN invoices i ON i.invoice_no = t.invoice_no
  WHERE t.picker_username <> 'administrator' AND t.was_skipped = false
    AND t.item_started IS NOT NULL
  GROUP BY t.picker_username,
           (t.item_started AT TIME ZONE 'UTC' AT TIME ZONE '{tz}')::date,
           t.invoice_no,
           i.packing_complete_time
  HAVING coalesce(i.packing_complete_time, max(t.item_completed)) IS NOT NULL),
o AS (SELECT picker_username, d, s, e,
        max(e) OVER (PARTITION BY picker_username, d ORDER BY s
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) pm
      FROM iv),
g AS (SELECT picker_username, d, s, e,
        sum(CASE WHEN pm IS NULL OR s > pm THEN 1 ELSE 0 END)
          OVER (PARTITION BY picker_username, d ORDER BY s) grp
      FROM o),
isl AS (SELECT picker_username, d, min(s) a, max(e) b
        FROM g GROUP BY picker_username, d, grp)
"""

VW_PICKER_IDLE_DAILY_SQL = r"""
CREATE OR REPLACE VIEW vw_picker_idle_daily AS
""" + _ORDER_ISLANDS_CTE + r"""
SELECT picker_username AS picker, d AS work_date,
  round((EXTRACT(epoch FROM (max(b)-min(a)))/60.0)::numeric,0)  AS span_min,
  round((sum(EXTRACT(epoch FROM (b-a)))/60.0)::numeric,0)       AS in_order_min,
  round(((EXTRACT(epoch FROM (max(b)-min(a)))
          - sum(EXTRACT(epoch FROM (b-a))))/60.0)::numeric,0)   AS idle_between_orders_min
FROM isl
GROUP BY picker_username, d
"""

VW_PICKER_OCCUPANCY_DAILY_SQL = r"""
CREATE OR REPLACE VIEW vw_picker_occupancy_daily AS
""" + _ORDER_ISLANDS_CTE + r""",
gaps AS (SELECT picker_username, d, a, b,
        EXTRACT(epoch FROM (a - lag(b) OVER (
            PARTITION BY picker_username, d ORDER BY a)))/60.0 AS gap
      FROM isl)
SELECT picker_username AS picker, d AS work_date,
  min(a) AS first_order, max(b) AS last_order_end,
  EXTRACT(epoch FROM (max(b)-min(a)))                           AS span_sec,
  round((EXTRACT(epoch FROM (max(b)-min(a)))/60.0)::numeric,0)  AS span_min,
  round((sum(EXTRACT(epoch FROM (b-a)))/60.0)::numeric,0)       AS occupied_min,
  round(((EXTRACT(epoch FROM (max(b)-min(a)))
          - sum(EXTRACT(epoch FROM (b-a))))/60.0)::numeric,0)   AS idle_min,
  round((100.0*sum(EXTRACT(epoch FROM (b-a)))
         / nullif(EXTRACT(epoch FROM (max(b)-min(a))),0))::numeric,0) AS occupancy_pct,
  count(*) FILTER (WHERE gap > 1)  AS idle_gaps,
  round(max(gap)::numeric,0)       AS longest_idle_min
FROM gaps
GROUP BY picker_username, d
"""

VW_IDLE_GAPS_SQL = r"""
CREATE OR REPLACE VIEW vw_idle_gaps AS
""" + _ORDER_ISLANDS_CTE + r""",
gg AS (SELECT picker_username, d, a AS gap_end,
        lag(b) OVER (PARTITION BY picker_username, d ORDER BY a) AS gap_start
      FROM isl)
SELECT picker_username AS picker, d AS work_date,
  gap_start AS idle_from, gap_end AS idle_to,
  round((EXTRACT(epoch FROM (gap_end - gap_start))/60.0)::numeric,1) AS idle_min,
  (EXTRACT(epoch FROM (gap_end - gap_start))/60.0 >= 20)             AS long_block
FROM gg
WHERE gap_start IS NOT NULL AND gap_end > gap_start
"""

# Per-order performance: hands-on work vs estimate, with interruptions
# (long walking gaps) stripped out so the pace judgement is fair, plus
# packing time vs its estimate and a "closed too fast" adoption flag.
VW_ORDER_PERFORMANCE_SQL = """
CREATE OR REPLACE VIEW vw_order_performance AS
SELECT
  t.invoice_no,
  t.picker_username                                          AS picker,
  (min(t.item_started) AT TIME ZONE 'UTC' AT TIME ZONE '{tz}')::date AS pick_date,
  count(*)                                                   AS lines,
  sum(t.quantity_picked)                                     AS units,
  round((sum(t.expected_time)/60.0)::numeric,1)              AS estimated_min,
  round((sum(t.picking_time + LEAST(t.walking_time,120))/60.0)::numeric,1)
                                                             AS working_min,
  round((sum(GREATEST(t.walking_time-120,0))/60.0)::numeric,1)
                                                             AS interruption_min,
  round((sum(t.total_item_time)/60.0)::numeric,1)            AS elapsed_min,
  round((100.0*sum(t.expected_time)
         / nullif(sum(t.picking_time + LEAST(t.walking_time,120)),0))::numeric,0)
                                                             AS pace_vs_estimate_pct,
  CASE WHEN i.packing_complete_time IS NULL OR i.picking_complete_time IS NULL
       THEN NULL
       ELSE round((GREATEST(EXTRACT(epoch FROM
           (i.packing_complete_time - i.picking_complete_time)),0)/60.0)::numeric,1)
  END                                                        AS packing_min,
  round(((45 + 3*count(*))/60.0)::numeric,1)                 AS packing_estimate_min,
  CASE WHEN i.packing_complete_time IS NULL OR i.picking_complete_time IS NULL
       THEN NULL
       ELSE (EXTRACT(epoch FROM
           (i.packing_complete_time - i.picking_complete_time))
             < 0.3*(45 + 3*count(*)))
  END                                                        AS packing_suspiciously_fast
FROM item_time_tracking t
JOIN invoices i ON i.invoice_no = t.invoice_no
WHERE t.picker_username <> 'administrator'
  AND t.was_skipped = false
  AND t.total_item_time > 0
  AND t.expected_time  > 0
GROUP BY t.invoice_no, t.picker_username,
         i.packing_complete_time, i.picking_complete_time
"""

BACKFILL_LEVEL_SQL = r"""
UPDATE item_time_tracking
SET level = substring(location from '\d{2}-\d{2}-([A-Z])')
WHERE level IS NULL
  AND location ~ '\d{2}-\d{2}-[A-Z]'
"""

_BACKFILL_MARKER_KEY = "picking_level_backfill_done"


def ensure_picking_report_views():
    """Create/refresh the picking report views and backfill shelf level.

    Idempotent: CREATE OR REPLACE VIEW plus a WHERE level IS NULL backfill.
    The backfill runs once per database — a marker row in ``settings``
    skips the full-table scan on subsequent boots (new writes set
    ``level`` themselves via ``parse_location_components``).
    PostgreSQL only (uses percentile_cont and regex operators).

    All date bucketing uses the configured system timezone so that daily
    numbers agree with the shift performance report around local midnight.
    """
    from app import db
    from timezone_utils import get_system_timezone

    with db.engine.connect() as conn:
        if conn.dialect.name != "postgresql":
            logger.info("Picking report views skipped (dialect=%s)", conn.dialect.name)
            return

        tz_name = str(get_system_timezone())
        logger.info("Picking report views using timezone: %s", tz_name)

        conn.execute(text(VW_PICK_DETAIL_SQL.format(tz=tz_name)))
        # DROP first: active_pick_hours was inserted mid-column-list, which
        # CREATE OR REPLACE VIEW cannot do on an existing view.
        conn.execute(text("DROP VIEW IF EXISTS vw_picker_daily"))
        conn.execute(text(VW_PICKER_DAILY_SQL))
        conn.execute(text(VW_IDLE_DEDICATED_SQL.format(tz=tz_name)))
        conn.execute(text(VW_PICKER_IDLE_DAILY_SQL.format(tz=tz_name)))
        # DROP first: these evolved column types (time -> timestamp) which
        # CREATE OR REPLACE VIEW cannot do on an existing view.
        conn.execute(text("DROP VIEW IF EXISTS vw_picker_occupancy_daily"))
        conn.execute(text(VW_PICKER_OCCUPANCY_DAILY_SQL.format(tz=tz_name)))
        conn.execute(text("DROP VIEW IF EXISTS vw_idle_gaps"))
        conn.execute(text(VW_IDLE_GAPS_SQL.format(tz=tz_name)))
        conn.execute(text("DROP VIEW IF EXISTS vw_order_performance"))
        conn.execute(text(VW_ORDER_PERFORMANCE_SQL.format(tz=tz_name)))

        already_done = conn.execute(
            text("SELECT 1 FROM settings WHERE key = :k AND value = 'true'"),
            {"k": _BACKFILL_MARKER_KEY},
        ).first()
        if already_done:
            conn.commit()
            logger.info("Picking report views ensured (backfill already done)")
            return

        result = conn.execute(text(BACKFILL_LEVEL_SQL))
        conn.execute(
            text("""
                INSERT INTO settings (key, value)
                VALUES (:k, 'true')
                ON CONFLICT (key) DO UPDATE SET value = 'true'
            """),
            {"k": _BACKFILL_MARKER_KEY},
        )
        conn.commit()
        logger.info(
            "Picking report views ensured; level backfilled on %s row(s)",
            result.rowcount,
        )
