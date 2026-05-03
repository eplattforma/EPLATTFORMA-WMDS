# Known Gaps — WMDS Development Batch

Tracks pre-existing weaknesses discovered during this batch that are **not** being fixed in this batch but should be addressed in a future scoped piece of work.

Format: each gap has a short ID, severity, where it was discovered, the current behaviour, the risk, and the recommended future fix.

---

## GAP-001: Driver-API authentication is header-only

**Severity:** Medium
**Discovered:** 2026-05-02 (Phase 1 verification trace, Q3 of `is_active` audit)
**Files:** `routes_driver_api.py` (`driver_id_required` decorator)
**Current behaviour:** Driver mobile API endpoints under `/api/driver/*` identify the caller via the `x-driver-id` HTTP header. After the Phase 1 hardening (ASSUMPTION-009), the decorator now also verifies that the user exists, is active, and has the `driver` role — but it still does **not** verify any cryptographic token, signed request, password, or session cookie.
**Risk:** Any party who knows a valid driver username can call `/api/driver/*` from any client by setting that header. The Phase 1 fix prevents disabled drivers from bypassing the gate, but does not prevent username impersonation. In practice, the mobile app is the only known caller and routes are bound to the named driver, so the blast radius is limited to actions performed against that specific driver's own routes.
**Recommended future fix (separate "Driver Auth Hardening" batch):**
  - Replace `x-driver-id` with either:
    - A device-bound bearer token issued at first login and rotated on each session, OR
    - A short-lived signed JWT minted by the existing `/login` flow with `role=driver` claim.
  - Keep the user-existence + `is_active` + role checks already added in ASSUMPTION-009.
  - Add a small admin "Driver Devices" UI to revoke device tokens (used when a phone is lost/stolen).
  - Rolling backward-compatible flag (`driver_api_token_required`, default false) so the mobile app can be updated independently of the server.
**Why not now:** The brief's Section 7 explicitly lists "Driver authentication method" as out-of-scope for this batch. The Phase 1 fix was the smallest possible enforcement bump that does not change the workflow for legitimate drivers.

---

## RR-001: Admin role has no decorator-level DENY case (residual risk)

**Severity:** Documentation only — no security impact.
**Discovered:** 2026-05-02 (Phase 3 closeout, Task #16 second-pass validation).
**Files:** `services/permissions.py` (`ROLE_PERMISSIONS["admin"] = ["*"]`), `tests/test_phase3_closeout_matrix.py` (admin row of the captured matrix).
**Current behaviour:** The `admin` role holds the `*` wildcard, which the matcher in `services.permissions._matches` always treats as a match for any key. As a consequence, no `@require_permission(...)` decorator can deny an `admin` user, and admin satisfies every role-string body check we ship today. The 7×5 captured matrix therefore shows 7× ALLOW for admin and 0× DENY.
**Why this is by design:** Admin is the system's authority role; the brief's Section 4 role table grants admin universal access deliberately. Synthesising an admin DENY by either (a) removing `*` from the admin grant for the duration of a test or (b) inventing a key not in admin's grant would prove a hypothetical that does not exist in production.
**Compensating evidence already captured:**
  - The 7-key × admin row in `PHASE3_CLOSEOUT_MATRIX.txt` proves admin reaches the 200 body of every gated route under enforcement — i.e. admin is **not accidentally locked out** by a missed key, which is the property that actually matters for go-live.
  - The wildcard-matcher behaviour itself is unit-tested in `tests/test_permissions.py::test_matches_unit` (positive + negative cases).
  - The auto-seeder grants admin `*` and is idempotent (`tests/test_permissions.py::test_seeder_grants_admin_star_and_is_idempotent`).
**Recommended future fix:** None. This is the system working as designed; the residual is a documentation artefact, not a code or test gap. If a future operator ever wants per-domain admin separation (e.g. a "warehouse super-admin" who is *not* allowed into Settings), that would require a new role + a removal of `*` from that role, at which point this RR is automatically retired by the new role's allow/deny matrix.
**Why not now:** This is not a fix in waiting — there is nothing to fix. The entry exists only so that the closeout's "1 ALLOW + 1 DENY per role" literal wording has an explicit, signed-off carve-out for `admin`.

---

## GAP-P5-01: Cooler-box auto-close on flag flip (deferred)

**Date raised:** 2026-05-03
**Phase:** Phase 5 (Cooler Picking, Reduced Scope)
**Severity:** Low (operational, not safety)
**Description:** When the operator flips `cooler_picking_enabled` from `true` back to `false` mid-shift, any cooler boxes that were opened while the flag was on remain in the `Open` state. They do not auto-close, and they do not appear in the operator UI any more (the blueprint endpoints become unreachable from the dashboard) until the operator manually re-enables the flag or closes them via direct URL `/cooler/box/<id>/close`.
**Why deferred:** Auto-closing on flag flip would require a watcher / scheduler hook and a "close reason" column on `cooler_boxes`. The reduced-scope phase explicitly excluded background workers (see SCHEDULING.md — no new jobs in Phase 5). The current behaviour is safe (orphan boxes do not block dispatch — they only show in the orphan-locks reconciliation UI).
**Workaround:** Operator visits `/cooler/box/<id>/close` directly, or re-enables the flag long enough to close from the dashboard, then disables again.

---

## GAP-P5-02: PDF Polish — barcode font fallback

**Date raised:** 2026-05-03
**Phase:** Phase 5 (Cooler Picking, Reduced Scope)
**Severity:** Cosmetic
**Description:** `services/cooler_pdf.py` uses ReportLab's default Helvetica family. The QR code is reliable, but the human-readable label below long invoice numbers can wrap awkwardly. A monospace font would render the wrapped lines more cleanly.
**Why deferred:** Aesthetic polish only; QR code is the operator's actual scan target. Out of reduced scope.

---

## GAP-P5-03: Cross-file pytest run leaks locked-rows from Phase 5 into Phase 4

**Date raised:** 2026-05-03
**Phase:** Phase 5 (Cooler Picking, Reduced Scope)
**Severity:** Test-tooling only (no production impact)
**Description:** When `tests/test_phase5_cooler_picking.py` and `tests/test_phase4_batch_picking.py` are run together in the **same** pytest process, the in-memory SQLite database is shared across the two suites' fixtures (SQLAlchemy connection-pool reuse), so item locks created during Phase 5 batch-creation tests carry over and trip the conflict guard in Phase 4 cancel/audit tests. Running each suite in its own pytest process (which is what the configured `override-pipeline` workflow and the documented release-test command do) shows them all green.
**Why deferred:** Fixing requires a session-scoped fixture rewrite in the shared `tests/conftest.py` (engine disposal between files) — a test-infrastructure change that is out of reduced-scope for the picking phase and would touch every other test suite in the repo.
**Workaround:** Run the suites in separate invocations:
```
pytest tests/test_phase5_cooler_picking.py
pytest tests/test_phase4_batch_picking.py
pytest tests/test_override_ordering_pipeline.py
```
