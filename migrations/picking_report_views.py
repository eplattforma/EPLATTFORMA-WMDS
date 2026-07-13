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

VW_PICK_DETAIL_SQL = r"""
CREATE OR REPLACE VIEW vw_pick_detail AS
SELECT
  item_started::date                                  AS pick_date,
  picker_username                                     AS picker,
  invoice_no,
  corridor,
  substring(location from '\d{2}-\d{2}-([A-Z])')      AS level,
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
CREATE OR REPLACE VIEW vw_picker_daily AS
SELECT
  pick_date,
  picker,
  count(*)                                            AS items_picked,
  sum(units)                                          AS units_picked,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_seconds)::numeric, 1)
                                                      AS median_seconds_per_pick,
  round(100.0 * avg(met_target::int), 0)              AS pct_meeting_target,
  round(100.0 * sum(walking_seconds) / nullif(sum(total_seconds), 0), 0)
                                                      AS walking_share_pct,
  sum(long_gap::int)                                  AS long_gaps_to_watch
FROM vw_pick_detail
GROUP BY pick_date, picker
ORDER BY pick_date DESC, items_picked DESC
"""

VW_IDLE_DEDICATED_SQL = r"""
CREATE OR REPLACE VIEW vw_idle_dedicated AS
SELECT
  i.start_time::date                                                          AS idle_date,
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
    """
    from app import db

    with db.engine.connect() as conn:
        if conn.dialect.name != "postgresql":
            logger.info("Picking report views skipped (dialect=%s)", conn.dialect.name)
            return
        conn.execute(text(VW_PICK_DETAIL_SQL))
        conn.execute(text(VW_PICKER_DAILY_SQL))
        conn.execute(text(VW_IDLE_DEDICATED_SQL))

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
