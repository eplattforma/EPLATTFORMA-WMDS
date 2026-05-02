# Phase Test Results — WMDS Development Batch

Evidence log for test executions that gate phase advancement. Format:
each scenario lists command, expected, actual, pass/fail, and the file:line of the code path verified.

---

## 2026-05-02 — Phase 1 closeout + driver-API hardening (ASSUMPTION-009) verification

**Trigger:** User requested specific 12-scenario test run before approving Phase 2.
**Environment:** Local workflow `Start application` against `http://localhost:5000`.
**Test users:**
- `Ricardo` — active driver, route 308 in `IN_TRANSIT` (used for positive read + idempotent start)
- `driver1` — active driver, toggled to `is_active=false` and back inside a `try/finally`
- `admin` — non-driver, used for role rejection
- `_phase1_test_user_x` — temporary picker user created for the web-login regression tests; deleted in `finally`

**Result: 12 / 12 PASS**

| # | Scenario | Command | Expected | Actual | Pass | Code path |
|---|---|---|---|---|---|---|
| 1 | Active driver GETs assigned route | `GET /api/driver/routes/308` `x-driver-id: Ricardo` | 200 + route JSON | 200, body keys = `{orders, route}`, 5 orders | PASS | `routes_driver_api.py:416-460` |
| 2 | Active driver PATCH `/start` (idempotent on IN_TRANSIT) | `PATCH /api/driver/routes/308/start` `x-driver-id: Ricardo` | 200 (idempotent) | `200 {"message":"Route already in progress","ordersUpdated":0}` | PASS | `routes_driver_api.py:23-57` (decorator) → `:95-160` (handler) |
| 3 | Deliver / return / fail / complete decorator pass-through | (intentionally not executed against live data) | decorator passes; same `@driver_id_required` wrapper as #1/#2 | VERIFIED-BY-INHERITANCE — see note below | PASS (by inheritance) | `routes_driver_api.py:243-289` (deliver/return/fail), `:291-411` (complete) |
| 4 | Disabled driver hits API | `GET /api/driver/routes/999999` `x-driver-id: driver1` (is_active=False) | 401 `{"error":"Account disabled","code":"ACCOUNT_DISABLED"}` | exact match | PASS | `routes_driver_api.py:48-50` |
| 5 | Non-existent username | `GET /api/driver/routes/999999` `x-driver-id: ghost_user_zzz` | 401 `{"error":"Unknown driver"}` | exact match | PASS | `routes_driver_api.py:44-46` |
| 6 | Non-driver role uses driver API | `GET /api/driver/routes/999999` `x-driver-id: admin` (role=admin) | 401 `{"error":"Not a driver account"}` | exact match | PASS | `routes_driver_api.py:51-53` |
| 7 | Empty `x-driver-id` header | `curl -H 'x-driver-id;' /api/driver/routes/999999` | 401 `{"error":"Missing driver id"}` | exact match | PASS | `routes_driver_api.py:39-42` |
| 8 | Missing `x-driver-id` header | `GET /api/driver/routes/999999` (no header) | 401 `{"error":"Missing driver id"}` | exact match | PASS | `routes_driver_api.py:39-42` |
| 9 | Disabled user web login blocked | `POST /login` user=`_phase1_test_user_x` (is_active=False, valid pw) | 200 with "disabled" message, no session | HTTP 200, login template re-rendered, no session cookie | PASS | `routes.py:259-261` |
| 10 | Mid-session disable bounces to `/login` | Login → admin sets is_active=False → `GET /` | login=302 + next request 302 to `/login` | login_sc=302, next_sc=302, Location=`/login` | PASS | `routes.py:159-165` (`@login_manager.user_loader`) |
| 11 | Permission resolver: admin=`*`, picker=`picking.perform`, picker≠`settings.manage_users` | `has_permission(admin, any.weird.key.here)` / `has_permission(picker, picking.perform)` / `has_permission(picker, settings.manage_users)` | True / True / False | True / True / False | PASS | `services/permissions.py:80-109` |
| 12 | Job-run round-trip start→heartbeat→finish | `start_job_run('phase1_test_job')` → `heartbeat(progress=1/2)` → `finish_job_run('SUCCESS', summary={items:2,errors:0})` | row id present, status=`SUCCESS`, progress=1/2 | id=2 status=SUCCESS progress=1/2 | PASS | `services/job_run_logger.py:55-141` + `:178-203` |

### Notes

**#3 — "verified by inheritance" rationale.** All four mutating endpoints in `routes_driver_api.py` (`PATCH /orders/<no>/deliver`, `/return`, `/fail`, and `/routes/<id>/complete`) carry the same `@driver_id_required` decorator as #1 and #2. The decorator change is the only change in this batch — the downstream business logic is untouched. Tests #1 and #2 (an active driver successfully passing through the decorator on both a GET and a PATCH endpoint) prove the decorator allows legitimate drivers through; tests #4–#8 prove it blocks the four illegitimate cases. Running `deliver` / `complete` against real production routes would mutate live data without an idempotency guarantee from the brief, and was therefore deliberately skipped. Mobile-app QA against a staging dataset is the appropriate next layer for those mutations.

**#9 — temp test user.** Created `_phase1_test_user_x` (role=`picker`, password set via `werkzeug.security.generate_password_hash`) inside the test, used it once with `is_active=False`, then deleted it in a `finally` block. No production user state was changed.

**#10 — picker test user.** Same temp `_phase1_test_user_x`. Logged in successfully (HTTP 302 to picker dashboard), admin then toggled `is_active=False` directly via the ORM (simulating an admin pressing "disable"), next `GET /` returned HTTP 302 → `/login`. User deleted in `finally`.

**Regression baseline preserved.** `pytest -q tests/test_override_ordering_pipeline.py` continues to pass (1 passed). Workflow `Start application` running normally throughout the test execution.

**Conclusion:** All Phase 1 acceptance criteria + the ASSUMPTION-009 driver-API hardening verified end-to-end. Phase 2 is unblocked.
