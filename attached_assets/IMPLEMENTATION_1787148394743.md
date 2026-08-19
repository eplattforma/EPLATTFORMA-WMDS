# Gapless Picker Timeline — Implementation

**Target:** EPLATTFORMA WMDS · Neon PostgreSQL 16 · per-user activity tracking

Everything here was executed against a local PostgreSQL 16 instance seeded with
a fixture mirroring your production schema column-for-column. The SQL ran, the
adversarial tests passed, and the TypeScript typechecks under `strict`. Results
in §6.

---

## 1. Files

| File | What it is | Verified |
|---|---|---|
| `010_picker_timeline_postgres.sql` | Segment ledger, constraints, transition primitive, reaper, integrity views | ran clean |
| `011_per_user_tracking.sql` | Per-user gate, `awaiting_order`, resolution state machine, accounting rebuild | ran clean |
| `activity-service.ts` | Data layer — one function per SQL call | `tsc --strict` clean |
| `activity-routes.ts` | Express HTTP layer | `tsc --strict` clean |
| `AdminTrackingRoster.tsx` | Per-user roster admin screen | `tsc --strict` clean |
| `picker-ui.html` | Interactive picker prototype (10 screens) | reference for the client |

### Delete when adopting

- `001_activity_mode_schema_migration.sql`, `002_…`, `003_…` — MySQL syntax, will not run
- `activity_mode_backend.py`, `activity_mode_settings_admin.py` — written against MongoDB
- `ActivityMode.jsx` / `.css`, `ActivityModeAdminPanel.jsx` / `.css` — wrong API contract and missing states; use `picker-ui.html` as the reference instead

---

## 2. Order of operations

```bash
# 1. Branch your Neon database first — this is DDL against production.
#    Neon console → Branches → Create branch. Run steps 2–3 there, verify, then promote.

# 2. Migrations, in order
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 010_picker_timeline_postgres.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 011_per_user_tracking.sql

# 3. Confirm the roster seeded and nothing is live yet
psql "$DATABASE_URL" -c "SELECT username, role, is_active, track_activity, effective,
                                shifts_recorded FROM vw_tracking_roster WHERE track_activity;"
#    Expect 7 users across picker / admin / warehouse_manager, effective = false everywhere.
```

`010` requires `btree_gist`, which is **not currently installed** on your database
(only `plpgsql` is). The migration creates it; Neon supports it.

### Backend

```ts
import { makeActivityRoutes } from './activity-routes.js';
app.use('/api/activity', makeActivityRoutes(pool));
```

`activity-routes.ts` expects `req.user = { username, role }` from your existing
auth middleware. `role` is used **only** to gate supervisor endpoints, never to
decide who gets tracked.

### Cron

```ts
setInterval(() => svc.reapStaleShifts(), 5 * 60_000);   // crash / no-checkout recovery
setInterval(() => svc.autoWriteOff(),   24 * 3600_000); // 7-day resolution backstop
```

### Monitoring

`GET /api/activity/health` returns 500 when an invariant breaks. Page on it.
It should be boring forever.

---

## 3. The one code change that matters

```diff
- if (user.role === 'picker') { ...activity tracking... }
- if (DEDICATED_PICKERS.includes(username)) { ... }
+ if (await svc.trackingEnabled(username)) { ...activity tracking... }
```

Grep for `'picker'` and for `DEDICATED_PICKERS` and remove every hit. Role must
not appear in any tracking decision.

**Why:** 9 users already record shifts across 3 roles. Only 5 have
`role='picker'`. Role gating silently excludes 4 people who are already being
timed — including Polis, whose 286 idle minutes started this investigation.

---

## 4. Client contract

| Step | Call | Note |
|---|---|---|
| App load | `GET /session` | `tracking_enabled` decides whether shift controls render at all |
| Check in | `POST /check-in` | Returns `prompt_activity: true` — **show the activity picker immediately** |
| Declare | `POST /transition` | `action_id` UUID required; send `expected_open_segment_id` |
| Order done | `POST /packing-complete` | Server already switched to `unassigned`; the modal only labels it |
| No orders | `POST /awaiting-order` | Attributed to Planning, never scored against the picker |
| Every 30s | `POST /heartbeat` | Drives crash detection |
| Check out | `POST /check-out` | Returns `unresolved[]` — show the review screen |
| Review | `POST /segment/:id/classify` or `/decline` | Decline **escalates**, does not close |

Two client rules that are easy to get wrong:

1. **`action_id` must be generated once per user gesture and reused on retry.**
   That is what makes double-taps and flaky-network retries safe. Generating a
   fresh UUID on retry defeats it.
2. **On `409 STALE_SEGMENT`, re-fetch `/session` — never retry blindly.** It means
   the reaper closed the segment first (device went quiet), so the client's view
   is stale.

The timer in the UI is **display only**. Every recorded timestamp is server-side,
so a tablet with a wrong clock cannot corrupt the ledger.

---

## 5. Rollout

The system ships inert: master switch `false`, 7 users flagged.

1. **Verify** — `SELECT * FROM vw_shift_timeline_integrity;` returns nothing
2. **Trim the roster** — turn off anyone who shouldn't be tracked
3. **One user first** — turn everyone off except Arslan, flip the master switch
4. **Watch for a day** — `/health` stays 200; check `unassigned_pct` looks plausible
5. **Expand** — re-enable the rest from the roster screen

Rollback at any point: flip the master switch off. Open shifts close cleanly;
no data is lost.

---

## 6. Verification results

Run against PostgreSQL 16.13 with a fixture mirroring your production schema
(`users`, `settings`, `shifts`, `idle_periods`, `user_permissions` — same columns
and types), seeded with your real user list and shift distribution.

### Migrations

```
010_picker_timeline_postgres: 0 errors
011_per_user_tracking:        0 errors
```

### Adversarial tests — can the model be broken?

| Test | Result |
|---|---|
| Insert a segment leaving a 60s gap | **BLOCKED** — `timeline gap on shift 10` |
| Insert while a segment is still open | **BLOCKED** — `already has an open segment (id 1)` |
| Insert overlapping a closed segment | **BLOCKED** |
| Replay the same `action_id` twice | returned the same segment id; **1 row created, not 2** |
| Declare 30s after `unassigned` opened | relabelled to `break`, `backfill_sec=30`, `reclassified_from=unassigned` |

### Full shift, 08:00–16:00, mixed states

```
picking 395  break 28  awaiting_order 22  unassigned 35   total 480.0
occupancy 86.2%   unassigned 7.6%

integrity_violations = 0
reconciliation_breaks = 0
uncovered_sec = 0          ← 480 min shift, 480 min covered, exactly
```

### Resolution machine

Picker classified one block, declined another → both surfaced in
`vw_supervisor_review_queue` → `vw_shift_closure_blockers` showed 1 pending,
1 declined, 33 min → supervisor wrote one off and classified the other →
blockers dropped to 0.

### Per-user gate

| Case | Result |
|---|---|
| Revoke tracking mid-shift | open shift **and** open segment both closed cleanly (0 remaining) |
| `is_active=false` user flagged `track_activity=true` | `picker_tracking_enabled` returns **false** |
| Every flag change | written to `user_tracking_audit` |

---

## 7. Two defects this testing caught

Worth recording, because both would have been silent in production:

1. **`ON CONFLICT (key)` on `settings`** — your `settings` table has no unique
   constraint on `key`, so both migrations aborted at the settings insert.
   Rewritten as insert-if-absent, which needs no constraint.

2. **`vw_picker_day_accounting` had no `awaiting_order` column** — `010` created
   the view before `011` added the state, so that time would have been counted in
   `total_min` but missing from the breakdown. The columns would have quietly
   stopped summing to the total: exactly the class of discrepancy this design
   exists to prevent. `011` now rebuilds the view and adds
   `vw_accounting_reconciliation` as a standing guard.

---

## 8. Open items

- **Backend stack assumption.** `activity-service.ts` and `activity-routes.ts`
  are Node + `node-postgres`, inferred from Replit + Neon. If you are on Python
  or Go, the port is roughly an hour — every method is a single SQL call, and all
  invariants live in Postgres, so a port cannot weaken them. Tell me the stack
  and I will rewrite these two files.
- **`idle_periods`** (3,574 rows, 100% `break_reason IS NULL`) is left untouched.
  Once `picker_segment` has coverage, migrate reporting off it and retire it.
- **Shifts 491 and 492** are currently open with no check-out. Decide before
  enabling: back-fill them, or leave them pre-migration — the integrity view
  ignores shifts with no segments, so they will not raise false alarms.
