-- ============================================================================
-- Migration 011  —  Per-USER activity tracking (replaces per-ROLE gating)
--                 + awaiting_order state
--                 + unassigned resolution state machine
--
-- PostgreSQL 16 (Neon).  Run AFTER 010_picker_timeline_postgres.sql
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


-- ----------------------------------------------------------------------------
-- 0. Enum additions must land before any transaction that uses them.
-- ----------------------------------------------------------------------------
ALTER TYPE picker_state ADD VALUE IF NOT EXISTS 'awaiting_order';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'segment_resolution') THEN
    CREATE TYPE segment_resolution AS ENUM (
      'not_required',      -- a declared state; nothing to resolve
      'pending',           -- unassigned, nobody has addressed it yet
      'classified_live',   -- picker declared it in the moment
      'classified_retro',  -- picker labelled it at check-out
      'declined',          -- picker could not recall -> escalated
      'supervisor_set',    -- supervisor classified it
      'written_off'        -- supervisor (or the 7-day reaper) accepted it unknown
    );
  END IF;
END $$;


-- ============================================================================
-- This file contains ONLY type changes, and is deliberately separate from 012.
--
-- PostgreSQL will not allow a new enum value to be USED in the same transaction
-- that ADDED it. 012 creates vw_picker_day_accounting, which references
-- 'awaiting_order' in a FILTER clause. psql splits a file into per-statement
-- transactions so a combined file happens to work there, but any runner that
-- sends the file in one execute() (psycopg2, SQLAlchemy) fails with
-- 'unsafe use of new value'. Splitting removes the trap for every runner.
-- ============================================================================
