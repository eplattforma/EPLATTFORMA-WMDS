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

## GAP-002: Three analytics blueprints still use raw role-string `_role_ok()` checks

**Severity:** Low
**Discovered:** 2026-05-02 (Phase 3 closeout `_role_ok` migration audit, Task #16)
**Files:**
  - `routes_customer_analytics.py` (`customer_analytics_bp`, 11 call sites)
  - `blueprints/category_manager.py` (`catmgr_bp`, 3 call sites)
  - `blueprints/peer_analytics.py` (`peer_bp`, 4 call sites)
**Current behaviour:** Each blueprint defines a local `_role_ok()` helper that returns `current_user.role in ("admin", "warehouse_manager")` and short-circuits every analytics endpoint. Unlike the comms/sms helpers — which were migrated to `has_permission(current_user, "menu.communications")` during Phase 3 — these three were intentionally left on the legacy role-string check.
**Risk:** None today. Both `admin` and `warehouse_manager` are still the only legitimate consumers of these analytics surfaces, so the gate produces the same answer as a permission lookup would. The risk is purely future: if an operator ever wants to grant a custom-role user (e.g. a category buyer) access to category-manager analytics without making them a warehouse manager, they cannot do so from the per-user permission editor — the role-string check would still block them.
**Recommended future fix (separate "Analytics permission migration" task):**
  - Replace each helper body with `has_permission(current_user, "menu.crm")` (customer analytics) / `has_permission(current_user, "menu.warehouse")` (category manager + peer) — pick the menu key that matches each blueprint's natural section.
  - Add the corresponding key to each role's grant in `services.permissions.ROLE_PERMISSIONS` if it is not already covered (admin has `*`, warehouse_manager already has `menu.warehouse`).
  - Extend `tests/test_permission_enforcement.py` parametrised matrix with one allow + one deny cell per blueprint.
  - Optional: rename the helpers (or replace them with `@require_permission(...)` decorators on each route) to remove the misleading `_role_ok` name.
**Why not now:** Out-of-scope for Task #16 per the closeout brief ("either complete the migration or document the deliberate dual-track"). Touching access control on three more blueprints inside a closeout/verification task widens the blast radius beyond what the brief calls for. Documented as the deliberate dual-track in `ASSUMPTIONS_LOG.md` (ASSUMPTION-019).
