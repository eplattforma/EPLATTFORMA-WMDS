-- ============================================================================
-- Migration 012  —  Per-USER activity tracking (replaces per-ROLE gating)
--                 + unassigned resolution state machine
--
-- PostgreSQL 16 (Neon).  Run AFTER 010 and 011_activity_enums.sql
--
-- WHY PER-USER — the production data, not a preference:
--
--   9 users already record shifts, across THREE roles:
--     picker            Arslan, Khaled, Thierno, picker1, picker2
--     admin             Eleonora, Polis
--     warehouse_manager Andreas, Dennis
--
--   Only 5 have role='picker'. Gating on role would silently exclude 4 people
--   who are already being timed — including Polis, whose 286 idle minutes
--   started this whole investigation.
--
--   user_permissions was considered and rejected: it is a *capability* grant
--   with '*' and 'ns.*' wildcards in live use (4 users hold bare '*'). Activity
--   tracking is an OBLIGATION, not a capability — granting an admin superuser
--   must not conscript them into shift check-in. The correct precedent is
--   users.require_gps_check: a per-user obligation flag on the user record.
-- ============================================================================


BEGIN;

-- ----------------------------------------------------------------------------
-- 1. THE GATE.  Per-user, mirroring users.require_gps_check.
-- ----------------------------------------------------------------------------
ALTER TABLE users ADD COLUMN IF NOT EXISTS track_activity        boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS track_activity_set_by varchar;
ALTER TABLE users ADD COLUMN IF NOT EXISTS track_activity_set_at timestamp;

COMMENT ON COLUMN users.track_activity IS
  'Per-user obligation flag. When true the user gets shift check-in/out, the '
  'activity modal and segment tracking. Independent of role and of '
  'user_permissions. Global master switch: settings[activity_mode.enabled].';

CREATE INDEX IF NOT EXISTS idx_users_track_activity
  ON users (username) WHERE track_activity;

-- Audit of who turned tracking on/off for whom.
CREATE TABLE IF NOT EXISTS user_tracking_audit (
    id         bigserial PRIMARY KEY,
    username   varchar   NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    enabled    boolean   NOT NULL,
    changed_by varchar   NOT NULL,
    changed_at timestamp NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    reason     text
);
CREATE INDEX IF NOT EXISTS idx_tracking_audit_user ON user_tracking_audit (username, changed_at DESC);

CREATE OR REPLACE FUNCTION users_track_activity_audit() RETURNS trigger AS $$
BEGIN
  IF NEW.track_activity IS DISTINCT FROM OLD.track_activity THEN
    INSERT INTO user_tracking_audit (username, enabled, changed_by)
    VALUES (NEW.username, NEW.track_activity, COALESCE(NEW.track_activity_set_by,'unknown'));
    NEW.track_activity_set_at := (now() AT TIME ZONE 'utc');
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_track_activity_audit ON users;
CREATE TRIGGER trg_users_track_activity_audit
  BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION users_track_activity_audit();


-- ----------------------------------------------------------------------------
-- 2. Single resolver. Every gate in the app calls THIS — never role, never
--    a hardcoded username list.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_tracking_enabled(p_username varchar)
RETURNS boolean AS $$
  SELECT
    -- global master switch
    COALESCE(NULLIF((SELECT value FROM settings WHERE key='activity_mode.enabled'),''), 'false')::boolean
    AND EXISTS (
      SELECT 1 FROM users u
       WHERE u.username = p_username
         AND u.track_activity
         AND u.is_active            -- a disabled account is never tracked
    );
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION picker_tracking_enabled(varchar) IS
  'The ONLY sanctioned activity-tracking gate. Role is not consulted.';


-- ----------------------------------------------------------------------------
-- 3. Mid-shift flag changes must not strand an open timeline.
--    Turning tracking OFF closes the shift cleanly; turning it ON takes
--    effect at the NEXT check-in (time already elapsed cannot be covered).
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION users_track_activity_close_shift() RETURNS trigger AS $$
DECLARE r RECORD;
BEGIN
  IF OLD.track_activity AND NOT NEW.track_activity THEN
    FOR r IN SELECT id FROM shifts
              WHERE picker_username = NEW.username AND check_out_time IS NULL
    LOOP
      PERFORM picker_shift_close(r.id, (now() AT TIME ZONE 'utc'), 'admin');
    END LOOP;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_track_close_shift ON users;
CREATE TRIGGER trg_users_track_close_shift
  AFTER UPDATE ON users FOR EACH ROW EXECUTE FUNCTION users_track_activity_close_shift();


-- ----------------------------------------------------------------------------
-- 3b. Safe "make sure this shift has an open segment" primitive.
--
--     Needed for shifts that already exist WITHOUT segments — you have two right
--     now (491, 492: open, no check-out, pre-migration). Calling picker_shift_open
--     on a shift whose timeline was already partially built, or calling a bare
--     transition on a shift with no segments at all, both trip the no-gap trigger.
--     This resolves all three cases and is idempotent, so it is safe to call on
--     every check-in.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION picker_shift_ensure_open(p_shift_id integer)
RETURNS bigint AS $$
DECLARE
  v_open  bigint;
  v_pk    varchar;
  v_start timestamp;
  v_last  timestamp;
  v_n     int;
BEGIN
  SELECT picker_username, check_in_time INTO v_pk, v_start
    FROM shifts WHERE id = p_shift_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'shift % not found', p_shift_id;
  END IF;

  -- Case 1: already has an open segment -> nothing to do.
  SELECT id INTO v_open
    FROM picker_segment WHERE shift_id = p_shift_id AND ended_at IS NULL;
  IF FOUND THEN
    RETURN v_open;
  END IF;

  SELECT count(*), max(ended_at) INTO v_n, v_last
    FROM picker_segment WHERE shift_id = p_shift_id;

  -- Case 2: no segments at all (pre-migration shift) -> start at check_in_time,
  --         so elapsed time is honestly recorded as unassigned rather than lost.
  -- Case 3: segments exist but none open -> resume at the end of the last one.
  INSERT INTO picker_segment
    (shift_id, picker_username, state, started_at, opened_by)
  VALUES
    (p_shift_id, v_pk, 'unassigned',
     CASE WHEN v_n = 0 THEN v_start ELSE v_last END, 'system')
  RETURNING id INTO v_open;

  UPDATE shifts
     SET last_heartbeat_at = COALESCE(last_heartbeat_at, (now() AT TIME ZONE 'utc'))
   WHERE id = p_shift_id;

  RETURN v_open;
END $$ LANGUAGE plpgsql;

COMMENT ON FUNCTION picker_shift_ensure_open(integer) IS
  'Idempotent. Use on every check-in — handles new shifts, pre-migration shifts '
  'with no segments, and shifts whose timeline was closed without the shift.';


-- ----------------------------------------------------------------------------
-- 4. Resolution state machine — nothing silently vanishes.
-- ----------------------------------------------------------------------------
ALTER TABLE picker_segment
  ADD COLUMN IF NOT EXISTS resolution      segment_resolution NOT NULL DEFAULT 'not_required',
  ADD COLUMN IF NOT EXISTS resolved_by     varchar,
  ADD COLUMN IF NOT EXISTS resolved_at     timestamp,
  ADD COLUMN IF NOT EXISTS resolution_note text;

CREATE INDEX IF NOT EXISTS idx_seg_resolution
  ON picker_segment (resolution) WHERE resolution IN ('pending','declined');

-- Closing a segment as 'unassigned' automatically makes it someone's problem.
CREATE OR REPLACE FUNCTION picker_segment_mark_pending() RETURNS trigger AS $$
BEGIN
  IF NEW.ended_at IS NOT NULL AND OLD.ended_at IS NULL
     AND NEW.state = 'unassigned' AND NEW.resolution = 'not_required' THEN
    NEW.resolution := 'pending';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_segment_mark_pending ON picker_segment;
CREATE TRIGGER trg_segment_mark_pending
  BEFORE UPDATE ON picker_segment FOR EACH ROW EXECUTE FUNCTION picker_segment_mark_pending();


-- 4a. Picker labels a block at check-out.
CREATE OR REPLACE FUNCTION picker_classify_segment(
  p_segment_id bigint, p_state picker_state, p_by varchar
) RETURNS void AS $$
BEGIN
  UPDATE picker_segment
     SET state             = p_state,
         reclassified_from = state,
         reclassified_at   = (now() AT TIME ZONE 'utc'),
         reclassified_by   = p_by,
         resolution        = 'classified_retro',
         resolved_by       = p_by,
         resolved_at       = (now() AT TIME ZONE 'utc')
   WHERE id = p_segment_id AND resolution IN ('pending','declined');
END $$ LANGUAGE plpgsql;

-- 4b. Picker cannot recall -> escalate. Honest, and it does NOT close the block.
CREATE OR REPLACE FUNCTION picker_decline_segment(
  p_segment_id bigint, p_by varchar
) RETURNS void AS $$
BEGIN
  UPDATE picker_segment
     SET resolution = 'declined', resolved_by = p_by, resolved_at = (now() AT TIME ZONE 'utc')
   WHERE id = p_segment_id AND resolution = 'pending';
END $$ LANGUAGE plpgsql;

-- 4c. Supervisor resolves: classify, or write off with a reason.
CREATE OR REPLACE FUNCTION picker_supervisor_resolve(
  p_segment_id bigint, p_state picker_state, p_by varchar, p_note text DEFAULT NULL
) RETURNS void AS $$
BEGIN
  IF p_state IS NULL THEN
    UPDATE picker_segment
       SET resolution='written_off', resolved_by=p_by, resolved_at=(now() AT TIME ZONE 'utc'), resolution_note=p_note
     WHERE id = p_segment_id;
  ELSE
    UPDATE picker_segment
       SET state=p_state, reclassified_from=state, reclassified_at=(now() AT TIME ZONE 'utc'), reclassified_by=p_by,
           resolution='supervisor_set', resolved_by=p_by, resolved_at=(now() AT TIME ZONE 'utc'), resolution_note=p_note
     WHERE id = p_segment_id;
  END IF;
END $$ LANGUAGE plpgsql;

-- 4d. Auto write-off so reporting is never blocked indefinitely.
--     Written-off time STILL counts as unassigned — resolved, not erased.
CREATE OR REPLACE FUNCTION picker_auto_writeoff_stale()
RETURNS integer AS $$
DECLARE
  v_days int := COALESCE(
    NULLIF((SELECT value FROM settings WHERE key='activity_mode.autowriteoff_days'),'')::int, 7);
  v_n int;
BEGIN
  UPDATE picker_segment
     SET resolution='written_off', resolved_by='system',
         resolved_at=(now() AT TIME ZONE 'utc'), resolution_note='auto write-off after '||v_days||' days'
   WHERE resolution IN ('pending','declined')
     AND ended_at < (now() AT TIME ZONE 'utc') - make_interval(days => v_days);
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END $$ LANGUAGE plpgsql;


-- ----------------------------------------------------------------------------
-- 5. Supervisor queue + what is blocking each shift from closing.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_supervisor_review_queue AS
SELECT g.id AS segment_id, g.shift_id, g.picker_username,
       COALESCE(u.display_name, g.picker_username) AS display_name,
       g.started_at, g.ended_at,
       ROUND(g.duration_sec/60.0, 1) AS minutes,
       g.resolution,
       ((now() AT TIME ZONE 'utc') - g.ended_at) AS age
  FROM picker_segment g
  JOIN users u ON u.username = g.picker_username
 WHERE g.resolution IN ('pending','declined')
 ORDER BY g.ended_at;

CREATE OR REPLACE VIEW vw_shift_closure_blockers AS
SELECT s.id AS shift_id, s.picker_username, s.check_in_time, s.check_out_time,
       COUNT(*) FILTER (WHERE g.resolution = 'pending')::int  AS pending_blocks,
       COUNT(*) FILTER (WHERE g.resolution = 'declined')::int AS declined_blocks,
       ROUND(SUM(g.duration_sec) FILTER (
         WHERE g.resolution IN ('pending','declined'))/60.0, 1) AS unresolved_min
  FROM shifts s
  JOIN picker_segment g ON g.shift_id = s.id
 WHERE g.resolution IN ('pending','declined')
 GROUP BY 1,2,3,4
 ORDER BY s.check_in_time DESC;


-- ----------------------------------------------------------------------------
-- 6. Who is tracked — the roster screen's backing view.
--    Note how little role predicts.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_tracking_roster AS
SELECT u.username,
       COALESCE(u.display_name, u.username) AS display_name,
       u.role,
       u.is_active,
       u.track_activity,
       picker_tracking_enabled(u.username)  AS effective,
       u.track_activity_set_by,
       u.track_activity_set_at,
       (SELECT count(*) FROM shifts s WHERE s.picker_username = u.username)::int AS shifts_recorded,
       (SELECT max(s.check_in_time) FROM shifts s WHERE s.picker_username = u.username) AS last_shift
  FROM users u
 ORDER BY u.track_activity DESC, u.role, u.username;


-- ----------------------------------------------------------------------------
-- 6b. Rebuild the accounting view.
--     010 created it before 'awaiting_order' existed, so that state had no
--     column and the breakdown silently stopped summing to total_min — exactly
--     the class of discrepancy this design exists to prevent.
--     DROP+CREATE, not CREATE OR REPLACE: a replace cannot insert a column.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_accounting_reconciliation;   -- depends on the view below
DROP VIEW IF EXISTS vw_picker_day_accounting;
CREATE VIEW vw_picker_day_accounting AS
SELECT
    g.picker_username,
    g.started_at::date                                              AS work_date,
    COUNT(DISTINCT g.shift_id)::int                                 AS shifts,

    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='picking')       /60.0,1) AS picking_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='break')         /60.0,1) AS break_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='restock')       /60.0,1) AS restock_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='assist')        /60.0,1) AS assist_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='repacking')     /60.0,1) AS repacking_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='awaiting_order')/60.0,1) AS awaiting_order_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='unassigned')    /60.0,1) AS unassigned_min,
    ROUND(SUM(g.duration_sec) FILTER (WHERE g.state='offline')       /60.0,1) AS offline_min,

    ROUND(SUM(g.duration_sec)/60.0, 1)                              AS total_min,

    -- Occupancy excludes offline (device failure is not picker behaviour) and
    -- awaiting_order (no work available is not picker behaviour either).
    ROUND(100.0 * SUM(g.duration_sec) FILTER (WHERE g.state='picking')
          / NULLIF(SUM(g.duration_sec) FILTER (
              WHERE g.state NOT IN ('offline','awaiting_order')), 0), 1) AS occupancy_pct,

    ROUND(100.0 * SUM(g.duration_sec) FILTER (WHERE g.state='unassigned')
          / NULLIF(SUM(g.duration_sec) FILTER (
              WHERE g.state NOT IN ('offline','awaiting_order')), 0), 1) AS unassigned_pct,

    COUNT(*) FILTER (WHERE g.reclassified_at IS NOT NULL)::int       AS n_reclassified,
    COUNT(*) FILTER (WHERE g.resolution IN ('pending','declined'))::int AS n_unresolved
  FROM picker_segment g
 WHERE g.ended_at IS NOT NULL
 GROUP BY 1, 2;

-- Guard: the breakdown must always reconcile to total_min. Should be EMPTY.
CREATE OR REPLACE VIEW vw_accounting_reconciliation AS
SELECT picker_username, work_date, total_min,
       (picking_min+break_min+restock_min+assist_min+repacking_min
        +awaiting_order_min+unassigned_min+offline_min) AS sum_of_parts
  FROM vw_picker_day_accounting
 WHERE abs(total_min - (picking_min+break_min+restock_min+assist_min+repacking_min
        +awaiting_order_min+unassigned_min+offline_min)) > 0.05;


-- ----------------------------------------------------------------------------
-- 7. Settings. The per-picker JSON allowlist from 010 is now obsolete —
--    the flag lives on users. Master switch stays OFF until you flip it.
-- ----------------------------------------------------------------------------
DELETE FROM settings WHERE key = 'activity_mode.enabled_pickers';

-- `settings` has no unique constraint on `key` in production, so ON CONFLICT
-- is unavailable. Insert-if-absent — safe to re-run.
INSERT INTO settings (key, value)
SELECT 'activity_mode.autowriteoff_days', '7'
 WHERE NOT EXISTS (SELECT 1 FROM settings s WHERE s.key = 'activity_mode.autowriteoff_days');


-- ----------------------------------------------------------------------------
-- 8. Seed the roster from reality: everyone active who already records shifts.
--    Gives you 7 users across 3 roles. Trim before enabling the master switch.
--
--    Deliberately EXCLUDED: Thierno and Andreas (have shifts, is_active=false).
-- ----------------------------------------------------------------------------
UPDATE users u
   SET track_activity = true,
       track_activity_set_by = 'migration_011'
 WHERE u.is_active
   AND EXISTS (SELECT 1 FROM shifts s WHERE s.picker_username = u.username);

COMMIT;


-- ============================================================================
-- VERIFY
-- ============================================================================

-- A. The roster. Expect 7 tracked across picker / admin / warehouse_manager,
--    and effective=false everywhere because the master switch is still off.
--    SELECT username, role, is_active, track_activity, effective, shifts_recorded
--      FROM vw_tracking_roster;

-- B. Prove role is NOT the gate:
--    SELECT role, count(*) FILTER (WHERE track_activity) tracked, count(*) total
--      FROM users GROUP BY role;

-- C. Turn one user on/off (this is what the admin screen calls):
--    UPDATE users SET track_activity=true, track_activity_set_by='Polis'
--     WHERE username='Dennis';
--    SELECT * FROM user_tracking_audit ORDER BY changed_at DESC LIMIT 5;

-- D. Flip the master switch when ready:
--    UPDATE settings SET value='true' WHERE key='activity_mode.enabled';

-- E. Schedule daily:  SELECT picker_auto_writeoff_stale();
--    Schedule 5-minutely: SELECT * FROM picker_reap_stale_shifts();

-- F. Integrity assertion must stay empty:
--    SELECT * FROM vw_shift_timeline_integrity;


-- ============================================================================
-- APPLICATION CHANGE — the only gate that may appear in code
-- ============================================================================
--   BEFORE:  if user.role == 'picker':            ...
--   AFTER:   if picker_tracking_enabled(username): ...
--
-- Replace every role check in check-in, check-out, packing-complete and the
-- activity endpoints. No hardcoded username list (DEDICATED_PICKERS is gone).
-- ============================================================================
