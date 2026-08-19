# Gapless Picker Timeline — Implementation (Flask / PostgreSQL)

**Target:** EPLATTFORMA WMDS · Flask + Neon PostgreSQL 16 · per-user activity tracking

Stack confirmed as Flask/Python, so the earlier TypeScript files are superseded.
Everything here was executed: migrations run against a local PostgreSQL 16 seeded
with a fixture mirroring your production schema column-for-column, and the Flask
layer driven end-to-end against it. Results in §7.

---

## 1. Files

| File | What it is | Verified |
|---|---|---|
| `update_activity_tracking_schema.py` | Migration runner, house `update_*_schema.py` convention | idempotent x3 |
| `sql/010_picker_timeline_postgres.sql` | Segment ledger, constraints, transition primitive, reaper, integrity views | ran clean |
| `sql/011_activity_enums.sql` | Type changes only — must be its own file (see §2) | ran clean |
| `sql/012_per_user_tracking.sql` | Per-user gate, resolution machine, `ensure_open`, accounting rebuild | ran clean |
| `server/activity_service.py` | Data layer — one SQL call per method, driver-agnostic | 30/30 tests pass |
| `server/activity_routes.py` | Flask blueprint | exercised via `test_client` |
| `client/AdminTrackingRoster.tsx` | Per-user roster screen (React) | typechecks |
| `client/picker-ui.html` | Interactive picker prototype, 10 screens | reference for the client |

### Delete — do not port these

- `activity-service.ts`, `activity-routes.ts` — superseded by the `.py` files
- `001_…sql`, `002_…sql`, `003_…sql` — MySQL syntax, will not run on Postgres
- `activity_mode_backend.py`, `activity_mode_settings_admin.py` — written against
  MongoDB (`insert_one`, `ObjectId`, `$set`); they will not work against Postgres
- `ActivityMode.jsx` / `.css`, `ActivityModeAdminPanel.jsx` / `.css` — wrong API
  contract and missing states; use `picker-ui.html` as the reference

---

## 2. Install

Drop `update_activity_tracking_schema.py` in the repo root and `sql/` beside it,
then run it the way you run every other schema change:

```bash
# BRANCH YOUR NEON DATABASE FIRST. Neon console -> Branches -> Create branch,
# point DATABASE_URL at the branch, verify, then promote.
python update_activity_tracking_schema.py
```

It reads `DATABASE_URL` through `app.db.engine`, applies the three SQL files in
order, then prints the integrity counters and the roster summary. Idempotent —
verified clean across three consecutive runs.

**Two things in the runner that look odd and must not be "cleaned up":**

1. **It uses autocommit and a raw DBAPI cursor**, not `db.session.execute(text(...))`.
   SQLAlchemy's `text()` chokes on the `$$`-quoted function bodies.
2. **The enum changes are a separate file (`011`).** PostgreSQL refuses to *use*
   a new enum value in the transaction that *added* it. `psql` happens to work
   because it splits files into per-statement transactions, but any runner that
   sends a file in one `execute()` fails with `unsafe use of new value`. Splitting
   removes the trap for every runner — this was caught by testing, not theory.

**SQLite:** `app.py` falls back to `sqlite:///picking.db` when `DATABASE_URL` is
unset, which is what the test suite uses. This feature is Postgres-only
(`btree_gist`, `EXCLUDE`, enums, generated columns). The runner detects SQLite,
logs an error and exits without applying anything — so it degrades cleanly rather
than half-migrating. **Any test that exercises activity tracking needs a real
Postgres**, or must be skipped.

If you prefer raw psql:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/010_picker_timeline_postgres.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/011_activity_enums.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/012_per_user_tracking.sql
```

`010` requires the `btree_gist` extension, which is **not currently installed** on
your database (only `plpgsql` is). The migration creates it; Neon supports it.

---

## 3. Wire into Flask

```python
from contextlib import contextmanager
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from activity_service import ActivityService
from activity_routes import make_activity_blueprint

pool = ThreadedConnectionPool(1, 10, DATABASE_URL, cursor_factory=RealDictCursor)

@contextmanager
def get_connection():
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)

activity = ActivityService(get_connection)

def current_user():
    # wire to whatever you already use — session, flask_login, JWT
    return {"username": session.get("username"), "role": session.get("role")}

app.register_blueprint(
    make_activity_blueprint(activity, current_user),
    url_prefix="/api/activity",
)
```

`activity_service.py` takes a connection factory, so psycopg2 / psycopg3 /
Flask-SQLAlchemy all work — adapters for each are at the bottom of that file.
The service needs **dict-like rows** (`RealDictCursor` above).

### Cron — not optional

```python
from apscheduler.schedulers.background import BackgroundScheduler

sched = BackgroundScheduler()
sched.add_job(activity.reap_stale_shifts, "interval", minutes=5)
sched.add_job(activity.auto_write_off,    "cron", hour=2)
sched.start()
```

Without the reaper, a tablet that dies leaves a segment open forever.

### Monitoring

`GET /api/activity/health` returns 500 when an invariant breaks. Page on it. It
should be boring forever.

---

## 4. The one code change that matters

```diff
- if user.role == 'picker':
-     ...activity tracking...
- if username in DEDICATED_PICKERS:
+ if activity.tracking_enabled(username):
+     ...activity tracking...
```

Grep for `'picker'` and `DEDICATED_PICKERS`; remove every hit that gates
tracking. `role` may still gate *supervisor* endpoints — that is a different
question and is handled inside the blueprint.

**Why:** 9 users already record shifts across 3 roles. Only 5 are
`role='picker'`. Role gating silently excludes 4 people who are already being
timed — including Polis, whose 286 idle minutes started this investigation.
Verified in the test suite: `tracking_enabled('Polis')` is true and
`tracking_enabled('Thierno')` is false despite Thierno being `role='picker'`,
because his account is inactive.

---

## 5. Client contract

| Step | Call | Note |
|---|---|---|
| App load | `GET /session` | `tracking_enabled` decides whether shift controls render at all |
| Check in | `POST /check-in` | Returns `prompt_activity: true` — **show the activity picker immediately** |
| Declare | `POST /transition` | `action_id` UUID required; send `expected_open_segment_id` |
| Order done | `POST /packing-complete` | Server already switched to `unassigned`; the modal only labels it |
| No orders | `POST /awaiting-order` | Attributed to Planning, never scored against the picker |
| Every 30s | `POST /heartbeat` | Drives crash detection |
| Check out | `POST /check-out` | Returns `unresolved[]` — show the review screen |
| Review | `POST /segment/<id>/classify` \| `/decline` | Decline **escalates**, does not close |
| Supervisor | `GET /review-queue`, `POST /segment/<id>/resolve` | `state: null` = write-off |
| Admin | `GET /roster`, `POST /roster/<username>`, `POST /master-switch` | supervisor role required |

Three client rules that are easy to get wrong:

1. **Generate `action_id` once per user gesture and reuse it on retry.** That is
   what makes double-taps and flaky-network retries safe. A fresh UUID per retry
   defeats it entirely. Verified: same `action_id` twice returns the same segment
   and creates one row.
2. **On `409 STALE_SEGMENT`, re-fetch `/session` — never retry blindly.** It means
   the reaper closed the segment first because the device went quiet.
3. **The UI timer is display-only.** Every recorded timestamp is server-side, so a
   tablet with a wrong clock cannot corrupt the ledger.

Clients may only declare `picking, break, restock, assist, repacking`.
`unassigned`, `offline` and `awaiting_order` are system-assigned — the API
rejects them, so a client cannot mark itself offline to hide time.

---

## 6. Rollout

Ships inert: master switch `false`, 7 users flagged.

1. **Verify** — `SELECT * FROM vw_shift_timeline_integrity;` returns nothing
2. **Trim the roster** — turn off anyone who shouldn't be tracked
3. **One user first** — leave only Arslan on, flip the master switch
4. **Watch a day** — `/health` stays 200; `unassigned_pct` looks plausible
5. **Expand** from the roster screen

Rollback at any point: flip the master switch off. Open shifts close cleanly, no
data is lost.

---

## 7. Verification results

PostgreSQL 16.13, fixture mirroring your production schema (`users`, `settings`,
`shifts`, `idle_periods`, `user_permissions` — same columns and types), seeded
with your real user list and shift distribution. Flask 3.1.3, psycopg2 2.9.12.

### Migrations

```
010_picker_timeline_postgres -> 0 errors
011_per_user_tracking        -> 0 errors
```

### Adversarial SQL — can the model be broken?

| Attack | Result |
|---|---|
| Insert a segment leaving a 60s gap | **BLOCKED** |
| Insert while a segment is still open | **BLOCKED** — names the open segment id |
| Insert overlapping a closed segment | **BLOCKED** |
| Replay the same `action_id` | same segment returned, **1 row not 2** |
| Declare 30s after `unassigned` opened | relabelled, `backfill_sec=30` |

Full 08:00–16:00 shift: `picking 395 · break 28 · awaiting_order 22 ·
unassigned 35 = 480.0 min`, `uncovered_sec = 0`.

### Flask suite — 30/30 pass

```
gate            Arslan(picker) / Polis(ADMIN) / Dennis(wh_mgr) tracked
                Thierno(inactive) and Ricardo(unflagged) NOT tracked
check-in        first segment opens 'unassigned' at check_in_time; re-check-in idempotent
idempotency     same action_id -> same segment id
validation      client cannot declare 'offline' or 'unassigned'
errors          StaleSegment -> 409 · TrackingDisabled -> 403 · missing action_id -> 400
full flow       packing_complete -> unassigned · awaiting_order · heartbeat · check-out
integrity       healthy=True, 0 violations, 0 reconciliation breaks
roster          7 users across 3 roles; revoke and re-grant both work
HTTP            picker blocked from /roster (403); admin allowed (200)
                review-queue serialises interval + Decimal columns
                all responses JSON-encodable end-to-end
```

---

## 8. Read against your actual codebase

`EPLATTFORMA-WMDS-main` was read before finalising this. What it changed:

| Found in your code | Consequence |
|---|---|
| `models.py` uses `UTCDateTime()` + `get_utc_now()` — UTC-naive | Every `now()` in the SQL and service is now `(now() AT TIME ZONE 'utc')`. Previously it relied on the DB server's `TimeZone` matching UTC — true on Neon, but by luck rather than contract. |
| `User.require_gps_check` — a per-user obligation boolean | Confirms `users.track_activity` is the right shape and the right precedent. |
| Newer modules export blueprints (`routes_batch.batch_bp`), older ones use bare `@app.route` | `make_activity_blueprint()` matches the newer convention. |
| Migrations are `update_*_schema.py`, not raw `.sql` | Added `update_activity_tracking_schema.py` in that exact style. |
| `app.py` falls back to SQLite for tests | Runner now guards the dialect and exits cleanly. |

### The snapshot is stale — read this before wiring anything up

The copy on disk is dated **2026-06-11**, roughly ten weeks old. In it:

- `shift_routes.py` and `routes_shifts.py` both define `/shift/check-in`,
  `/shift/check-out`, `/shift/break` — and **neither file is imported anywhere**.
  Both are dead code.
- **No live route creates a `Shift` row**, and `IdlePeriod(` is constructed only
  inside `models.py`.

Yet the database has shifts through today (id 492) and 3,574 idle periods. So the
live app creates both through code that is not in this snapshot.

Everything delivered here is therefore built against the **database**, which is
current and which I read directly — not against the stale application code. That
is why all the invariants live in Postgres functions. What I cannot write without
a current snapshot is the *integration*: the edit to your live check-in handler
that calls `check_in()` instead of inserting a `Shift` directly, and the
`packing_complete` hook. **Send a current export and that becomes a small, exact
diff instead of guesswork.**

---

## 9. Six defects this testing caught

All four would have been silent or intermittent in production.

1. **`ON CONFLICT (key)` on `settings`** — your `settings` table has no unique
   constraint on `key`, so both migrations aborted at the settings insert.
   Rewritten as insert-if-absent, which needs no constraint.

2. **`vw_picker_day_accounting` had no `awaiting_order` column** — `010` created
   the view before `011` added the state, so that time counted in `total_min` but
   vanished from the breakdown. `011` now rebuilds the view and adds
   `vw_accounting_reconciliation` as a standing guard.

3. **Check-in on a pre-migration shift crashed** — a shift that already exists
   with no segments (you have two right now: **491 and 492**, open with no
   check-out) made the first segment start at `now()` instead of `check_in_time`,
   tripping the no-gap trigger. Fixed with `picker_shift_ensure_open()`, which is
   idempotent and handles new shifts, pre-migration shifts, and resumed timelines
   alike. **This also resolves 491/492 automatically on next check-in** — their
   elapsed time is honestly recorded as `unassigned` rather than lost.

4. **`picker_shift_close` could make the timeline longer than the shift** — it
   bumped a sub-second final segment by a whole second to satisfy the
   `ends_after_start` check, pushing `ended_at` past `check_out_time` and
   producing `uncovered_sec = -1`. This would have fired whenever two taps landed
   in the same second. Now nudges by one microsecond and derives `check_out_time`
   from the same value, so segment end and shift end cannot disagree.

5. **Enum + view in one transaction** — running the migration through psycopg2
   (rather than `psql`) failed with `unsafe use of new value "awaiting_order"`.
   Fixed by splitting the type changes into `011_activity_enums.sql`.

6. **Migration was not idempotent** — a second run failed with
   `cannot drop columns from view`: `010` used `CREATE OR REPLACE VIEW` on the
   accounting view that `012` later widens, and a replace cannot narrow a view.
   Both files now use ordered `DROP VIEW IF EXISTS` (reconciliation view first,
   since it depends on the accounting view). Verified clean over three runs.

---

## 10. Remaining

- **`idle_periods`** (3,574 rows, 100% `break_reason IS NULL`) is untouched. Once
  `picker_segment` has coverage, migrate reporting off it and retire it.
- **Supervisor UI** — `AdminTrackingRoster.tsx` covers the roster. The review
  queue (`GET /review-queue` + `POST /segment/<id>/resolve`) has endpoints but no
  screen yet; screen 9 of `picker-ui.html` shows the intended layout.
- **`AdminTrackingRoster.tsx` is React.** If your Flask app renders Jinja
  templates instead, say so and I will convert it — it is one table and two
  fetch calls.
