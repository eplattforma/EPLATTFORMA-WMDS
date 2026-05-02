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
