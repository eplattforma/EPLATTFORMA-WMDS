-- ============================================================================
-- Gapless Picker Timeline  —  PostgreSQL 16 (Neon)
-- Migration 010
--
-- Supersedes the earlier MySQL-syntax migrations 001/002/003 (do not run those).
-- Verified against live schema: shifts(picker_username,...), settings(key,value),
-- idle_periods(...) all already exist and are NOT recreated here.
--
-- REVIEW BEFORE RUNNING. This is DDL against production.
-- Recommended: run on a Neon branch first, then promote.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. Extension required for the overlap-exclusion constraint
--    (currently only plpgsql is installed)
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS btree_gist;


-- ----------------------------------------------------------------------------
-- 1. State enum.  'unassigned' replaces NULL.  'offline' is distinct from it.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'picker_state') THEN
    CREATE TYPE picker_state AS ENUM (
      'picking',
      'break',
      'restock',
      'assist',
      'repacking',
      'unassigned',   -- accounted for, not yet declared  (never NULL)
      'offline'       -- device unreachable: we know that we don't know
    );
  END IF;
END $$;


-- ----------------------------------------------------------------------------
-- 2. Heartbeat column on the existing shifts table
-- ----------------------------------------------------------------------------
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS last_heartbeat_at timestamp;


-- ----------------------------------------------------------------------------
-- 3. The timeline ledger.
--    Segments must exactly partition [check_in_time, check_out_time].
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS picker_segment (
    id                bigserial PRIMARY KEY,
    shift_id          integer   NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    picker_username   varchar   NOT NULL,

    state             picker_state NOT NULL,
    started_at        timestamp NOT NULL,
    ended_at          timestamp,

    -- Derived, never hand-written.
    duration_sec      integer GENERATED ALWAYS AS (
                        CASE WHEN ended_at IS NULL THEN NULL
                             ELSE (EXTRACT(EPOCH FROM (ended_at - started_at)))::int
                        END) STORED,

    prev_segment_id   bigint REFERENCES picker_segment(id),

    -- Provenance: who/what caused this segment to exist and to end.
    opened_by         varchar NOT NULL
                        CHECK (opened_by IN ('picker','system','reaper','admin')),
    close_reason      varchar
                        CHECK (close_reason IS NULL OR close_reason IN
                          ('declared','packing_complete','order_assigned',
                           'check_out','heartbeat_lost','heartbeat_resumed',
                           'reaper','admin')),

    -- When the picker actually tapped (may differ from started_at).
    declared_at       timestamp,
    -- Seconds absorbed by the grace window; keeps the generosity auditable.
    backfill_sec      integer NOT NULL DEFAULT 0,

    -- Retro-classification at check-out (or by a supervisor).
    reclassified_from picker_state,
    reclassified_at   timestamp,
    reclassified_by   varchar,

    created_at        timestamp NOT NULL DEFAULT now(),

    CONSTRAINT seg_ends_after_start CHECK (ended_at IS NULL OR ended_at > started_at),
    CONSTRAINT seg_backfill_sane    CHECK (backfill_sec >= 0)
);

CREATE INDEX IF NOT EXISTS idx_seg_shift        ON picker_segment (shift_id, started_at);
CREATE INDEX IF NOT EXISTS idx_seg_picker_day   ON picker_segment (picker_username, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_seg_state        ON picker_segment (state);

-- HARD GUARANTEE 1: no two segments of a shift may overlap.
-- Also implies at most one open segment (two unbounded ranges always overlap).
ALTER TABLE picker_segment DROP CONSTRAINT IF EXISTS seg_no_overlap;
ALTER TABLE picker_segment ADD CONSTRAINT seg_no_overlap
  EXCLUDE USING gist (
    shift_id WITH =,
    tsrange(started_at, ended_at, '[)') WITH &&
  );

-- Belt and braces: clearer error than the exclusion constraint gives.
CREATE UNIQUE INDEX IF NOT EXISTS uq_seg_one_open_per_shift
  ON picker_segment (shift_id) WHERE ended_at IS NULL;


-- ----------------------------------------------------------------------------
-- 4. Idempotency ledger — kills double-taps and network retries.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS picker_action (
    action_id   uuid      PRIMARY KEY,           -- generated client-side
    shift_id    integer   NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    segment_id  bigint    NOT NULL REFERENCES picker_segment(id) ON DELETE CASCADE,
    created_at  timestamp NOT NULL DEFAULT now()
);


-- ----------------------------------------------------------------------------
-- 5. HARD GUARANTEE 2: no gaps.
--    A new segment must begin exactly where the previous one ended,
--    and the first segment must begin at check_in_time.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_segment_no_gap() RETURNS trigger AS $$
DECLARE
  v_prev_end    timestamp;
  v_shift_start timestamp;
  v_open_id     bigint;
  v_n           int;
BEGIN
  SELECT check_in_time INTO v_shift_start FROM shifts WHERE id = NEW.shift_id;

  IF v_shift_start IS NULL THEN
    RAISE EXCEPTION 'shift % has no check_in_time', NEW.shift_id;
  END IF;

  -- Diagnose the "segment still open" case explicitly. Without this the
  -- max(ended_at) below returns NULL and the first-segment branch fires,
  -- producing a correct rejection with a misleading message.
  SELECT id INTO v_open_id
    FROM picker_segment WHERE shift_id = NEW.shift_id AND ended_at IS NULL LIMIT 1;

  IF v_open_id IS NOT NULL THEN
    RAISE EXCEPTION
      'shift % already has an open segment (id %) — close it first; '
      'use picker_transition() rather than a bare INSERT',
      NEW.shift_id, v_open_id
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT max(ended_at), count(*) INTO v_prev_end, v_n
    FROM picker_segment WHERE shift_id = NEW.shift_id;

  IF v_n = 0 THEN
    IF NEW.started_at <> v_shift_start THEN
      RAISE EXCEPTION
        'first segment of shift % must start at check_in_time % (got %)',
        NEW.shift_id, v_shift_start, NEW.started_at
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.started_at <> v_prev_end THEN
    RAISE EXCEPTION
      'timeline gap on shift %: segment starts % but previous ended % (delta %s)',
      NEW.shift_id, NEW.started_at, v_prev_end,
      EXTRACT(EPOCH FROM (NEW.started_at - v_prev_end))
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_segment_no_gap ON picker_segment;
CREATE TRIGGER trg_segment_no_gap
  BEFORE INSERT ON picker_segment
  FOR EACH ROW EXECUTE FUNCTION picker_segment_no_gap();


-- ----------------------------------------------------------------------------
-- 6. Shift start — opens the very first segment. Time is owned from second one.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_shift_open(
  p_shift_id integer
) RETURNS bigint AS $$
DECLARE
  v_pk    varchar;
  v_start timestamp;
  v_id    bigint;
BEGIN
  SELECT picker_username, check_in_time INTO v_pk, v_start
    FROM shifts WHERE id = p_shift_id FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'shift % not found', p_shift_id;
  END IF;

  INSERT INTO picker_segment
    (shift_id, picker_username, state, started_at, opened_by)
  VALUES
    (p_shift_id, v_pk, 'unassigned', v_start, 'system')
  RETURNING id INTO v_id;

  UPDATE shifts SET last_heartbeat_at = v_start WHERE id = p_shift_id;

  RETURN v_id;
END $$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 7. THE transition primitive.
--    Close-then-open, atomically. This is the ONLY sanctioned writer.
--    Because started_at is always derived from the previous ended_at,
--    no caller can produce a gap.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_transition(
  p_shift_id         integer,
  p_new_state        picker_state,
  p_at               timestamp,
  p_opened_by        varchar,
  p_close_reason     varchar,
  p_action_id        uuid,
  p_expected_open_id bigint  DEFAULT NULL,
  p_grace_sec        integer DEFAULT NULL
) RETURNS bigint AS $$
DECLARE
  v_open   picker_segment%ROWTYPE;
  v_pk     varchar;
  v_new_id bigint;
  v_grace  integer;
BEGIN
  ---------------------------------------------------------------------------
  -- Idempotency: a replayed action returns the original segment, creates none.
  ---------------------------------------------------------------------------
  SELECT segment_id INTO v_new_id FROM picker_action WHERE action_id = p_action_id;
  IF FOUND THEN
    RETURN v_new_id;
  END IF;

  SELECT picker_username INTO v_pk FROM shifts WHERE id = p_shift_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'shift % not found', p_shift_id;
  END IF;

  -- Grace window from settings, overridable per call.
  v_grace := COALESCE(
    p_grace_sec,
    NULLIF((SELECT value FROM settings WHERE key = 'activity_mode.grace_sec'), '')::int,
    90
  );

  SELECT * INTO v_open
    FROM picker_segment
   WHERE shift_id = p_shift_id AND ended_at IS NULL
     FOR UPDATE;

  IF FOUND THEN
    -- Optimistic concurrency: reject a client acting on a stale segment
    -- (e.g. the reaper already closed it).
    IF p_expected_open_id IS NOT NULL AND v_open.id <> p_expected_open_id THEN
      RAISE EXCEPTION 'stale_segment: open is %, client expected %',
        v_open.id, p_expected_open_id
        USING ERRCODE = 'serialization_failure';
    END IF;

    IF p_at <= v_open.started_at THEN
      RAISE EXCEPTION 'transition time % is not after open segment start %',
        p_at, v_open.started_at
        USING ERRCODE = 'check_violation';
    END IF;

    -------------------------------------------------------------------------
    -- Grace-window absorption: a prompt declaration RELABELS the unassigned
    -- segment instead of splitting it, so tapping "Break" 8s after finishing
    -- an order yields one clean break, not an 8s crumb + a break.
    -------------------------------------------------------------------------
    IF v_open.state = 'unassigned'
       AND p_opened_by = 'picker'
       AND (p_at - v_open.started_at) <= make_interval(secs => v_grace)
    THEN
      UPDATE picker_segment
         SET state             = p_new_state,
             reclassified_from = 'unassigned',
             reclassified_at   = now(),
             declared_at       = p_at,
             backfill_sec      = (EXTRACT(EPOCH FROM (p_at - v_open.started_at)))::int
       WHERE id = v_open.id;

      INSERT INTO picker_action (action_id, shift_id, segment_id)
      VALUES (p_action_id, p_shift_id, v_open.id);

      RETURN v_open.id;
    END IF;

    UPDATE picker_segment
       SET ended_at = p_at, close_reason = p_close_reason
     WHERE id = v_open.id;
  END IF;

  ---------------------------------------------------------------------------
  -- Open the successor at EXACTLY the instant the predecessor closed.
  ---------------------------------------------------------------------------
  INSERT INTO picker_segment
    (shift_id, picker_username, state, started_at,
     prev_segment_id, opened_by, declared_at)
  VALUES
    (p_shift_id, v_pk, p_new_state, p_at,
     v_open.id, p_opened_by,
     CASE WHEN p_opened_by = 'picker' THEN p_at END)
  RETURNING id INTO v_new_id;

  INSERT INTO picker_action (action_id, shift_id, segment_id)
  VALUES (p_action_id, p_shift_id, v_new_id);

  RETURN v_new_id;
END $$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 8. Shift close — closes whatever is open. No segment is ever left dangling.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_shift_close(
  p_shift_id     integer,
  p_at           timestamp,
  p_close_reason varchar DEFAULT 'check_out'
) RETURNS void AS $$
DECLARE
  v_open picker_segment%ROWTYPE;
  v_end  timestamp := p_at;
BEGIN
  SELECT * INTO v_open
    FROM picker_segment
   WHERE shift_id = p_shift_id AND ended_at IS NULL
     FOR UPDATE;

  IF FOUND THEN
    -- seg_ends_after_start forbids a zero-length segment, so a close time that
    -- is not strictly after the start must be nudged. Nudge by ONE MICROSECOND,
    -- never a whole second: bumping a sub-second final segment by 1s pushes
    -- ended_at past check_out_time, and the timeline then covers MORE than the
    -- shift (uncovered_sec = -1). Fires whenever two taps land in one second.
    IF v_end <= v_open.started_at THEN
      v_end := v_open.started_at + interval '1 microsecond';
    END IF;

    UPDATE picker_segment
       SET ended_at = v_end, close_reason = p_close_reason
     WHERE id = v_open.id;
  END IF;

  -- Use the SAME v_end for the shift, so segment end and check_out_time can
  -- never disagree by construction.
  UPDATE shifts
     SET check_out_time = COALESCE(check_out_time, v_end),
         total_duration_minutes = COALESCE(
           total_duration_minutes,
           (EXTRACT(EPOCH FROM (v_end - check_in_time)) / 60)::int),
         status = CASE WHEN p_close_reason = 'reaper' THEN 'auto_closed'
                       ELSE 'completed' END
   WHERE id = p_shift_id;
END $$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 9. Reaper — crash / never-checked-out recovery.
--    Closes at last_heartbeat, NOT at reaper-run time, so a device that died
--    at 14:30 records ~15 min unknown rather than 4 hours of phantom break.
--    Run every 5 minutes.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_reap_stale_shifts()
RETURNS TABLE (shift_id integer, action text) AS $$
DECLARE
  v_hb_timeout int := COALESCE(
    NULLIF((SELECT value FROM settings WHERE key='activity_mode.heartbeat_timeout_sec'),'')::int,
    300);
  v_max_hours  int := COALESCE(
    NULLIF((SELECT value FROM settings WHERE key='activity_mode.max_shift_hours'),'')::int,
    14);
  r RECORD;
BEGIN
  -- 9a. Shifts past the maximum plausible length: close them out entirely.
  FOR r IN
    SELECT s.id, COALESCE(s.last_heartbeat_at, s.check_in_time) AS cut
      FROM shifts s
     WHERE s.check_out_time IS NULL
       AND now() - s.check_in_time > make_interval(hours => v_max_hours)
  LOOP
    PERFORM picker_shift_close(r.id, r.cut, 'reaper');
    shift_id := r.id; action := 'shift_auto_closed'; RETURN NEXT;
  END LOOP;

  -- 9b. Live shifts whose device went quiet: mark the dark period 'offline'.
  FOR r IN
    SELECT s.id, s.last_heartbeat_at AS cut
      FROM shifts s
      JOIN picker_segment g
        ON g.shift_id = s.id AND g.ended_at IS NULL
     WHERE s.check_out_time IS NULL
       AND s.last_heartbeat_at IS NOT NULL
       AND now() - s.last_heartbeat_at > make_interval(secs => v_hb_timeout)
       AND g.state <> 'offline'
       AND s.last_heartbeat_at > g.started_at
  LOOP
    PERFORM picker_transition(
      r.id, 'offline', r.cut, 'reaper', 'heartbeat_lost', gen_random_uuid());
    shift_id := r.id; action := 'marked_offline'; RETURN NEXT;
  END LOOP;
END $$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 10. INTEGRITY ASSERTION.
--     Should always return ZERO rows. If it returns anything, something
--     upstream is broken. Run daily; alert on non-empty.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_shift_timeline_integrity AS
WITH bounds AS (
  SELECT s.id                AS shift_id,
         s.picker_username,
         s.check_in_time,
         COALESCE(s.check_out_time, now()::timestamp) AS effective_end,
         s.status
    FROM shifts s
),
cover AS (
  SELECT b.shift_id,
         b.picker_username,
         b.status,
         (EXTRACT(EPOCH FROM (b.effective_end - b.check_in_time)))::int AS shift_sec,
         COALESCE(SUM(
           EXTRACT(EPOCH FROM (COALESCE(g.ended_at, b.effective_end) - g.started_at))
         ), 0)::int AS covered_sec,
         COUNT(g.id)::int AS n_segments,
         MIN(g.started_at) AS first_start,
         MAX(COALESCE(g.ended_at, b.effective_end)) AS last_end
    FROM bounds b
    LEFT JOIN picker_segment g ON g.shift_id = b.shift_id
   GROUP BY 1,2,3,4
),
chain AS (
  SELECT g.shift_id,
         COUNT(*) FILTER (
           WHERE g.prev_end IS NOT NULL AND g.started_at <> g.prev_end
         )::int AS broken_links
    FROM (
      SELECT shift_id, started_at,
             LAG(ended_at) OVER (PARTITION BY shift_id ORDER BY started_at) AS prev_end
        FROM picker_segment
    ) g
   GROUP BY 1
)
SELECT c.shift_id,
       c.picker_username,
       c.status,
       c.n_segments,
       c.shift_sec,
       c.covered_sec,
       c.shift_sec - c.covered_sec        AS uncovered_sec,
       COALESCE(ch.broken_links, 0)       AS broken_links,
       c.first_start,
       c.last_end
  FROM cover c
  LEFT JOIN chain ch ON ch.shift_id = c.shift_id
 WHERE c.n_segments > 0                       -- ignore pre-migration shifts
   AND ( c.shift_sec <> c.covered_sec
      OR COALESCE(ch.broken_links, 0) > 0 );


-- ----------------------------------------------------------------------------
-- 11. Reporting: full accounting per picker per day.
--     Every column sums to the shift. 'unassigned' is a managed metric,
--     'offline' is excluded from behavioural judgement.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_picker_day_accounting AS
SELECT
    g.picker_username,
    g.started_at::date                                              AS work_date,
    COUNT(DISTINCT g.shift_id)::int                                 AS shifts,

    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='picking')   /60.0, 1) AS picking_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='break')     /60.0, 1) AS break_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='restock')   /60.0, 1) AS restock_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='assist')    /60.0, 1) AS assist_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='repacking') /60.0, 1) AS repacking_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='unassigned')/60.0, 1) AS unassigned_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='offline')   /60.0, 1) AS offline_min,

    ROUND(SUM(g.duration_sec)/60.0, 1)                             AS total_min,

    -- Occupancy excludes 'offline': device failure is not picker behaviour.
    ROUND(100.0 * SUM(g.duration_sec) FILTER (WHERE g.state='picking')
          / NULLIF(SUM(g.duration_sec) FILTER (WHERE g.state <> 'offline'), 0), 1)
                                                                    AS occupancy_pct,

    -- The health metric to drive down. Replaces "NULL".
    ROUND(100.0 * SUM(g.duration_sec) FILTER (WHERE g.state='unassigned')
          / NULLIF(SUM(g.duration_sec) FILTER (WHERE g.state <> 'offline'), 0), 1)
                                                                    AS unassigned_pct,

    COUNT(*) FILTER (WHERE g.reclassified_at IS NOT NULL)::int      AS n_reclassified
  FROM picker_segment g
 WHERE g.ended_at IS NOT NULL
 GROUP BY 1, 2;


-- ----------------------------------------------------------------------------
-- 12. Unassigned blocks worth asking the picker about at check-out.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_unassigned_to_classify AS
SELECT g.id AS segment_id,
       g.shift_id,
       g.picker_username,
       g.started_at,
       g.ended_at,
       ROUND(g.duration_sec/60.0, 1) AS minutes
  FROM picker_segment g
 WHERE g.state = 'unassigned'
   AND g.ended_at IS NOT NULL
   AND g.duration_sec >= COALESCE(
         NULLIF((SELECT value FROM settings
                  WHERE key='activity_mode.retro_prompt_threshold_min'),'')::int, 3) * 60
 ORDER BY g.started_at;


-- ----------------------------------------------------------------------------
-- 13. Settings — uses the EXISTING key/value settings table.
--     ON CONFLICT DO NOTHING so re-running never clobbers live values.
-- ----------------------------------------------------------------------------
-- NOTE: production `settings` has no unique constraint on `key`, so ON CONFLICT
-- cannot be used. Insert-if-absent instead — safe to re-run, never clobbers.
INSERT INTO settings (key, value)
SELECT v.key, v.value
  FROM (VALUES
    ('activity_mode.enabled',                    'false'),
    ('activity_mode.grace_sec',                  '90'),
    ('activity_mode.heartbeat_timeout_sec',      '300'),
    ('activity_mode.max_shift_hours',            '14'),
    ('activity_mode.retro_prompt_threshold_min', '3'),
    ('activity_mode.occupancy_target_pct',       '65')
  ) AS v(key, value)
 WHERE NOT EXISTS (SELECT 1 FROM settings s WHERE s.key = v.key);

COMMIT;


-- ============================================================================
-- POST-MIGRATION VERIFICATION  (run these; do not skip)
-- ============================================================================

-- A. Integrity assertion must be empty.
--    SELECT * FROM vw_shift_timeline_integrity;

-- B. Prove a gap is actually impossible. Both of these MUST raise:
--
--    BEGIN;
--      -- pick a live shift id
--      SELECT picker_shift_open(<shift_id>);
--      -- deliberate 60s hole -> expect 'timeline gap on shift ...'
--      INSERT INTO picker_segment (shift_id, picker_username, state, started_at, opened_by)
--      SELECT <shift_id>, picker_username, 'break',
--             (SELECT max(ended_at)+interval '60 s' FROM picker_segment WHERE shift_id=<shift_id>),
--             'picker'
--        FROM shifts WHERE id=<shift_id>;
--    ROLLBACK;
--
--    BEGIN;
--      -- deliberate second open segment -> expect exclusion violation
--      INSERT INTO picker_segment (shift_id, picker_username, state, started_at, opened_by)
--      VALUES (<shift_id>, 'Arslan', 'break', now(), 'picker');
--    ROLLBACK;

-- C. Idempotency: same action_id twice returns the same segment id, creates one row.
--    SELECT picker_transition(<shift_id>,'break',now(),'picker','declared',
--                             '11111111-1111-1111-1111-111111111111');
--    SELECT picker_transition(<shift_id>,'break',now(),'picker','declared',
--                             '11111111-1111-1111-1111-111111111111');

-- D. Schedule the reaper every 5 minutes (pg_cron, or an external scheduler):
--    SELECT * FROM picker_reap_stale_shifts();

-- E. Two shifts are currently open with no check-out (ids 491, 492 as of 19 Aug).
--    Decide before enabling: back-fill them, or leave them pre-migration and
--    let vw_shift_timeline_integrity ignore them (n_segments = 0).

-- ============================================================================
-- ROLLBACK
-- ============================================================================
-- BEGIN;
--   DROP VIEW IF EXISTS vw_unassigned_to_classify, vw_picker_day_accounting,
--                       vw_shift_timeline_integrity;
--   DROP FUNCTION IF EXISTS picker_reap_stale_shifts(), picker_shift_close(integer,timestamp,varchar),
--                           picker_transition(integer,picker_state,timestamp,varchar,varchar,uuid,bigint,integer),
--                           picker_shift_open(integer), picker_segment_no_gap();
--   DROP TABLE IF EXISTS picker_action, picker_segment;
--   DROP TYPE IF EXISTS picker_state;
--   ALTER TABLE shifts DROP COLUMN IF EXISTS last_heartbeat_at;
--   -- settings rows and idle_periods are left untouched by design.
-- COMMIT;
