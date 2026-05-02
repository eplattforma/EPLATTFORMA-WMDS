# Assumptions Log — WMDS Development Batch

Format defined in Section 3 of the brief.

---

## ASSUMPTION-001: Existing 10-min forecast watchdog stays; brief 5-min watchdog gated by flag

**Date:** 2026-04-30
**Phase:** Phase 1 (Foundation)
**Files affected:** `SCHEDULING.md`, `ROLLBACK_AND_FLAGS.md`
**Decision made:** Keep the existing `forecast_watchdog` (every 10 min, UTC) running as today. The brief asks for a 5-min watchdog (Section 8) — this is treated as a Phase-2 cadence change gated behind `forecast_watchdog_enabled`. Until that flag is turned on in Phase 2, the current 10-min watchdog continues.
**Reason:** The current watchdog is operationally proven and the brief's "Decision Rule 6" says keep backward compatibility unless explicitly told to remove. Changing cron cadence is a production behaviour change that belongs in Phase 2 per Section 4.
**Safer alternative considered:** Switch immediately to 5-min cadence. Rejected — would couple Phase 1 infrastructure to a Phase 2 behaviour change.
**Feature flag / rollback:** `forecast_watchdog_enabled` (default `false`). Setting it true in Phase 2 will swap cadence; setting back to false reverts.
**Reversibility:** High
**Recommendation if user disagrees:** Toggle `forecast_watchdog_enabled = true` and update `forecast_watchdog_interval_minutes = 5`; the watchdog rescheduler in `scheduler.py` will re-register on next boot.

---

## ASSUMPTION-002: `display_name` defaults to `username` via additive backfill

**Date:** 2026-04-30
**Phase:** Phase 1 (Foundation)
**Files affected:** `update_phase1_foundation_schema.py`, `models.py`
**Decision made:** Add `users.display_name VARCHAR(120) NULL` and backfill existing rows with `username` value once on first migration. Future inserts may leave it NULL; UI helpers `display_name_or_username(user)` resolve the fallback.
**Reason:** Brief Section 6 says "Add `display_name` to users and use it in UI/reports where a human-readable name is needed." Nullable + backfill is the safest additive approach. NOT NULL would require touching every insert path.
**Safer alternative considered:** NOT NULL with default = `username`. Rejected — Postgres default cannot reference another column at insert time.
**Feature flag / rollback:** None — column is additive. To revert, `ALTER TABLE users DROP COLUMN display_name`.
**Reversibility:** High

---

## ASSUMPTION-003: Permissions service ships disabled (decorator no-ops)

**Date:** 2026-04-30
**Phase:** Phase 1 (Foundation)
**Files affected:** `services/permissions.py`
**Decision made:** `@require_permission(key)` is added now but only logs missing permissions until `permissions_enforcement_enabled = true` (default `false`). When the flag is off, the decorator unconditionally allows the request and writes a debug-level log line.
**Reason:** Brief Section 4 Phase 1 DoD: "Permission decorator/helper exists but enforcement can remain disabled." This lets us add the decorator to many routes safely in Phase 1 ahead of Phase 3 enforcement.
**Safer alternative considered:** Don't ship decorator yet. Rejected — Phase 3 would then require touching every protected route at once, increasing risk.
**Feature flag / rollback:** `permissions_enforcement_enabled` (default `false`). Toggling true activates 403 responses; toggling false reverts.
**Reversibility:** High

---

## ASSUMPTION-004: Job-run logger failures are swallowed

**Date:** 2026-04-30
**Phase:** Phase 1 (Foundation)
**Files affected:** `services/job_run_logger.py`
**Decision made:** All public functions in `job_run_logger.py` catch every exception internally, log at WARN level, and return `None` instead of raising. Caller code paths must never crash because the logging side-channel failed.
**Reason:** Brief Section 14: "Logging failures must not stop scheduled jobs from running." This is non-negotiable per the brief.
**Safer alternative considered:** Re-raise critical errors (e.g., DB connection lost). Rejected — the brief explicitly forbids it.
**Feature flag / rollback:** `job_runs_write_enabled` short-circuits the writes entirely.
**Reversibility:** High

---

## ASSUMPTION-005: `job_runs` is a fresh table, not a rename of any existing table

**Date:** 2026-04-30
**Phase:** Phase 1 (Foundation)
**Files affected:** `update_phase1_foundation_schema.py`
**Decision made:** Create a brand-new `job_runs` table with the schema in brief Section 8. Do NOT migrate or rename any existing log table (`PS365SyncLog`, `forecast_runs`, etc.).
**Reason:** Brief Section 8 says "Create/extend central Job Runs & Sync Logs". The simplest additive route is a new table. Existing tables continue to receive their domain-specific logs; the new logger writes to `job_runs` in parallel.
**Safer alternative considered:** Reuse `forecast_runs` as the canonical table. Rejected — `forecast_runs` has forecast-specific columns and existing query consumers.
**Feature flag / rollback:** `job_runs_enabled`, `job_runs_write_enabled`. Both default true; turn off to disable.
**Reversibility:** High

---

## ASSUMPTION-007: Job-run logger and settings seeder use isolated connections, not `db.session`

**Date:** 2026-05-02
**Phase:** Phase 1 (Foundation) — post-review hardening
**Files affected:** `services/job_run_logger.py`, `services/settings_defaults.py`
**Decision made:** Both modules now open their own short-lived `db.engine.connect()` connections and commit inside them. They never call `db.session.commit()`.
**Reason:** Code review surfaced that `db.session.commit()` inside the logger could commit half-finished business work of the caller (e.g., an in-flight forecast or sync transaction). Settings seeding had a parallel race risk on multi-worker boot. Both are now isolated. Settings seeding additionally uses `INSERT ... ON CONFLICT (key) DO NOTHING` for safe parallel boot.
**Safer alternative considered:** Use a separate scoped session. Rejected — engine-level connection is simpler and equally safe.
**Feature flag / rollback:** None — internal implementation detail; behaviour unchanged from caller perspective.
**Reversibility:** High

---

## ASSUMPTION-006: User-permission rows keyed by username string

**Date:** 2026-04-30
**Phase:** Phase 1 (Foundation)
**Files affected:** `update_phase1_foundation_schema.py`
**Decision made:** `user_permissions.username VARCHAR(64)` with FK to `users.username`. Brief Ground Rule 2 forbids the `username` PK migration in this batch.
**Reason:** Until/unless the username PK migration is approved, all FK references must use `users.username`. This keeps Phase 1 fully reversible.
**Safer alternative considered:** Use a surrogate `user_id` integer. Rejected — would require username PK migration first, which is explicitly out of scope.
**Feature flag / rollback:** None — table is additive.
**Reversibility:** High

---

## ASSUMPTION-008: Web login + mid-session `is_active` enforcement is already in place

**Date:** 2026-05-02
**Phase:** Phase 1 (Foundation) — verification only
**Files affected:** None (read-only audit)
**Decision made:** Confirmed via code trace that the two web-side gates required by Section 6 item 4 are already enforced and need no Phase 1/3 work:

- **Login path:** `routes.py:259-261` — after `check_password_hash` succeeds, `if not user.is_active` flashes "Your account has been disabled. Please contact an administrator." and returns to login without calling `login_user`.
- **Mid-session enforcement:** `routes.py:159-165` — Flask-Login `@login_manager.user_loader` returns `None` whenever `user.is_active` is False. Because `load_user` runs on every authenticated request, an admin disabling a user mid-session causes that user to be treated as anonymous on their very next request and bounced by `@login_required`.

**Reason:** Logging confirmed verification so Phase 3 does not re-trace these paths.
**Safer alternative considered:** Re-verify in Phase 3. Rejected — wastes effort; write down findings now.
**Feature flag / rollback:** None — describes pre-existing behaviour.
**Reversibility:** N/A
**Recommendation if user disagrees:** Run a manual smoke test by disabling a non-admin user in the Users page and attempting both a fresh login and a mid-session navigation; both should be rejected.

---

## ASSUMPTION-009: Driver-API `is_active` gate added; header-only auth scheme unchanged

**Date:** 2026-05-02
**Phase:** Phase 1 (Foundation) — out-of-batch fix authorised by user
**Files affected:** `routes_driver_api.py`
**Decision made:** Hardened `driver_id_required` to also look up the user, return 401 if the username is unknown, the account is disabled (`{'error': 'Account disabled', 'code': 'ACCOUNT_DISABLED'}`), or the role is not `'driver'`. The header scheme itself is unchanged — still `x-driver-id` only, no token, no signature, no session cookie.
**Reason:** Discovery during the Section 6 audit (Q3 follow-up) showed the previous decorator only checked header presence — a disabled driver who knew their own username could continue to hit `/api/driver/*`. The fix is bounded to four extra `if` checks; active drivers see no behaviour change. User explicitly authorised the scoped fix despite Section 7's "Driver Mode unchanged" rule, on the rationale that adding an enforcement check does not change the workflow for legitimate users.
**Safer alternative considered:**
  1. Defer to a dedicated Driver Auth Hardening batch. Rejected by user — disabled drivers are a current latent risk.
  2. Replace header with a token / signed JWT. Rejected — out of scope; tracked as a future batch in `KNOWN_GAPS.md`.
**Feature flag / rollback:** None — the new check is unconditional. To revert, restore the previous 6-line `driver_id_required` body.
**Reversibility:** High (single function, ~30 LOC)
**Recommendation if user disagrees:** Revert by reapplying the original decorator body and remove `User` from the `from models import …` line; behaviour returns to header-only.
**Code-review tightening (2026-05-02 same-day):** Architect review flagged two robustness items, both applied:
  1. Normalise the header — `driver_id = (request.headers.get('x-driver-id') or '').strip()` — so trailing whitespace from the mobile client is not misclassified as "Unknown driver".
  2. Add WARN-level logs for every rejected auth attempt (missing/blank header, unknown driver, disabled account, non-driver role), each tagged with `request.remote_addr`. No secrets are logged.

## ASSUMPTION-014: Phase 3 enforcement ships OFF; admin manually flips it ON when ready (Option A)

**Date:** 2026-05-02 (revised same day during Phase 3 closeout)
**Phase:** Phase 3 (Permission Enforcement) — closeout reconciliation
**Files affected:** `services/settings_defaults.py`, `services/permissions.py`, `ROLLBACK_AND_FLAGS.md`, `replit.md`
**Decision made:** Seed `permissions_enforcement_enabled = "false"` by default. Admins flip it to `"true"` manually from the Settings UI when production is ready. While the flag is `false`, `@require_permission` decorators only log missing keys (so accidental key/decorator drift surfaces in logs without breaking users). `permissions_role_fallback_enabled = "true"` and the `admin: ["*"]` wildcard remain in place so the eventual flip cannot lock out admin / warehouse_manager / crm_admin users without explicit grants. The Phase 3 auto-seeder still runs once on first boot, so by the time an admin flips enforcement on, every active user already has explicit `user_permissions` rows derived from their role.
**Reason:** Verification & Closeout brief Section 1.2 (Option A): match the seeded value to the per-phase rollout discipline of the rest of the batch — every high-risk flag ships OFF and is flipped manually after operational sign-off. An interim Phase 3 commit briefly seeded `"true"` while interpreting "Phase 3 turns enforcement ON" literally; that conflicted with the rollback doc, the assumptions log, and `replit.md`, all of which assume manual flip. Reconciling to `false` keeps every source of truth aligned and matches Production Safety Rule #2 ("enable one module/flag at a time").
**Safer alternative considered:** Option B — keep `"true"` and update the docs to match. Rejected by the project owner during closeout: ships a high-risk behaviour change without operational sign-off, and contradicts the otherwise-uniform pattern of "all high-risk flags default OFF."
**Feature flag / rollback:** Manual flip from the Settings UI, or `Setting.set(db.session, 'permissions_enforcement_enabled', 'true')`. Setting it back to `'false'` reverts decorators to log-only mode without code changes.
**Reversibility:** High
**Recommendation if user disagrees:** Switch to Option B by editing `services/settings_defaults.py` to seed `"true"` and reverting the doc updates above.

---

## ASSUMPTION-015: Phase 3 auto-seeder writes role-default rows once, marker-gated

**Date:** 2026-05-02
**Phase:** Phase 3 (Permission Enforcement)
**Files affected:** `services/permission_seeding.py`, `services/settings_defaults.py`, `main.py`
**Decision made:** On first boot after Phase 3 ships, walk every active user and insert their role's permission keys into `user_permissions` (literal wildcards preserved — the matcher already handles them). Gate with a one-time `permissions_auto_seed_done` marker so subsequent boots are idempotent. Provide a manual "Re-seed Permissions" button on Manage Users that calls the seeder with `force=True`.
**Reason:** Without seeded rows, `permissions_role_fallback_enabled` is the only source of truth — and the editor UI shows nothing in checkboxes for users with role fallback only. Seeding once gives the editor a concrete starting point and lets operators diverge per user.
**Safer alternative considered:** Run the seeder on every boot. Rejected — would re-grant permissions an operator deliberately revoked.
**Feature flag / rollback:** Set `permissions_auto_seed_done = "false"` and restart, or click the "Re-seed Permissions" button. To wipe a single user back to defaults, use "Reset to role defaults" on their permission editor.
**Reversibility:** High

---

## ASSUMPTION-016: `crm_admin` role added to `ROLE_PERMISSIONS` map (was string-only role)

**Date:** 2026-05-02
**Phase:** Phase 3 (Permission Enforcement)
**Files affected:** `services/permissions.py`
**Decision made:** Add a `crm_admin` entry to `ROLE_PERMISSIONS` with `menu.dashboard`, `menu.crm`, `menu.communications`, and the `comms.*` wildcard. The role exists in production (used by `_role_ok()` in `blueprints/communications.py` and `blueprints/sms.py`) but was previously string-checked only — Phase 3 enforcement would have locked them out of comms otherwise.
**Reason:** Brief Section 6 lists comms templates and customer messaging as a CRM admin's daily workflow. Migrating those blueprints to `@require_permission` without a corresponding role-fallback entry would have been a regression.
**Safer alternative considered:** Leave `_role_ok()` in place as the only guard and skip comms migration this phase. Rejected — leaves a string-role check in production code that contradicts Phase 3 DoD.
**Feature flag / rollback:** Remove the `crm_admin` entry; existing explicit grants in `user_permissions` still apply.
**Reversibility:** High

---

## ASSUMPTION-017: `routes_routes.py:admin_required` widened to honour `routes.manage` permission

**Date:** 2026-05-02
**Phase:** Phase 3 (Permission Enforcement)
**Files affected:** `routes_routes.py`
**Decision made:** The local `admin_required` decorator (used on every state-changing route in `routes_routes.py`) now passes if the current user is admin/warehouse_manager **or** holds the `routes.manage` permission. Before Phase 3 it was a strict role string check.
**Reason:** Phase 3 DoD requires migrating role-string checks to permission keys without breaking existing flows. Widening (admin/WM/perm) preserves the legacy behaviour as defense in depth while letting an operator grant `routes.manage` to a custom-role user from the editor UI.
**Safer alternative considered:** Replace the role check entirely with `@require_permission('routes.manage')`. Rejected — would couple route management to the master enforcement flag; admins would lose access if `permissions_enforcement_enabled = true` and the seeder hasn't run for them. The OR keeps behaviour identical for admin/WM regardless of flag state.
**Feature flag / rollback:** Toggle `permissions_enforcement_enabled = false` to revert; or reapply the previous 6-line decorator from git.
**Reversibility:** High

## ASSUMPTION-018: comms/sms blueprints keep coarse `menu.communications` key (no per-action fan-out)

**Date:** 2026-05-02
**Phase:** Phase 3 (Permission Enforcement) — closeout
**Files affected:** `blueprints/communications.py`, `blueprints/sms.py`
**Decision made:** The `_role_ok()` helpers in `blueprints/communications.py:25` and `blueprints/sms.py:48` are kept as-is. Both already delegate to `has_permission(current_user, "menu.communications")` (the migration from raw role-string checks landed in Phase 3) and gate every state-changing endpoint in those blueprints — 17 sites in `communications.py` (compose / preview / send-microsms / send-finalized / push send / bulk send / templates CRUD / logs view / launch URL / etc.) and 9 sites in `sms.py` (compose / preview / send / templates CRUD / logs / balance / etc.). The function name is the only legacy artefact; the body is the new model. We are deliberately **not** fanning out into per-action keys (`comms.send_sms`, `comms.send_bulk`, `comms.manage_templates`, `comms.view_logs`, etc.) at this time.
**Reason:** (1) The product owner's existing operational model treats "communications access" as a single binary capability — anyone allowed in the comms area can send, view, and manage templates; there is no current request to split these. (2) `crm_admin`, `warehouse_manager`, and `admin` already get `menu.communications` via `ROLE_PERMISSIONS`, plus `crm_admin` carries the broader `comms.*` wildcard, so role fallback covers every legitimate user today. (3) Adding per-action keys without an operational consumer would mean editing the permission editor grid (`PERMISSION_EDITOR_GROUPS` in `routes.py:1681`), the role table, the seeder coverage tests, and 26 call sites — pure churn for no behavioural change. (4) The keys are reserved namespace: any future request to split (e.g. "let template editors view but not send") can flip the helper to `has_permission(current_user, "comms.send_sms")` etc. without touching call sites because every call site goes through the helper.
**Safer alternative considered:** Migrate every call site to `@require_permission(...)` decorators with a fan-out of fine-grained keys. Rejected — see (3) above; also moves the gate from a single helper to 26 decorator lines and requires editor-grid + role-table + seeder updates that would each need their own ASSUMPTION entry.
**Feature flag / rollback:** None needed — behaviour is unchanged from the merged Phase 3 work. If the fan-out is ever wanted, it is a one-file edit to each helper.
**Reversibility:** High

---

## ASSUMPTION-019: customer_analytics / category_manager / peer_analytics keep raw role-string checks (out of scope for Task #16)

**Date:** 2026-05-02
**Phase:** Phase 3 (Permission Enforcement) — closeout
**Files affected:** `routes_customer_analytics.py`, `blueprints/category_manager.py`, `blueprints/peer_analytics.py`
**Decision made:** The `_role_ok()` helpers in these three analytics blueprints (11 + 3 + 4 = 18 call sites total) keep their raw `current_user.role in ('admin', 'warehouse_manager')` checks for now and are explicitly listed as known gaps in `KNOWN_GAPS.md`. Task #16's scope (per the task plan's "Done looks like") is the comms/sms migration plus closeout verification; widening to these three blueprints would be a separate task with its own role-table + seeder-coverage audit.
**Reason:** Production safety — touching access control on three more blueprints inside a closeout/verification task widens the blast radius beyond what the brief calls for ("either complete the migration or document the deliberate dual-track"). Documenting the dual-track here is the explicit option chosen by the brief. Both `admin` and `warehouse_manager` are still gated via the role string, so behaviour is unchanged from pre-Phase-3; the migration is purely a defense-in-depth widening that can wait.
**Safer alternative considered:** Migrate all three in this task. Rejected — three more files, three more sets of call-site audits, and no operational driver requesting it.
**Feature flag / rollback:** None — these helpers are already strict role checks. The migration, when undertaken, will follow the same pattern as comms/sms (replace body with `has_permission(current_user, "menu.<area>")`) and be guarded by `permissions_enforcement_enabled` like every other Phase 3 decorator.
**Reversibility:** High

---

## ASSUMPTION-020: Forecast workbench "On Hand" / "Case Qty" UI removed without forecast-math change (commit 7f75e73)

**Date:** 2026-05-02
**Phase:** Phase 3 (Permission Enforcement) — closeout (UI tidy reconciliation)
**Files affected:** `blueprints/forecast_workbench.py`, `templates/supplier_detail.html` (commit `7f75e73`)
**Decision made:** Per commit `7f75e73` ("Remove 'On Hand' and 'Case Qty' from forecast display and calculations"), both columns were dropped from the supplier-detail forecast table and from the per-row payload the workbench builds for the template. Forecast totals, suggested order quantity, and override pipeline are unchanged — `on_hand` is no longer surfaced, but the underlying values are still pulled from the warehouse for any code path that needs them (e.g. picking, transfers).
**Reason:** Operational sign-off from the project owner: the two columns were leftover noise from an earlier iteration and were confusing the supplier-review workflow. Removing display surface area only (not data) means no migration, no rollback flag needed, and no impact on the `tests/test_override_ordering_pipeline.py` regression baseline (still passing).
**Safer alternative considered:** Hide the columns behind a flag rather than remove them. Rejected — no operational consumer asked for the toggle, and the columns were cluttering the display for every supplier review session.
**Feature flag / rollback:** Pure UI removal — revert commit `7f75e73` to restore.
**Reversibility:** High
