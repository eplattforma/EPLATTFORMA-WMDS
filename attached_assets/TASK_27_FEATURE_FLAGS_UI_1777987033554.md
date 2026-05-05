# Task #27 — Feature Flags Admin UI

## What & Why

Every feature flag introduced by the WMDS Development Batch (Phases 1–5 + Cockpit) is
currently managed only via the Python shell or direct SQL. There is no UI page for them.
This means operators cannot flip flags in production without developer access, cannot see
the current state of all flags at a glance, and have no audit trail of who changed what
and when.

This task adds a dedicated **Feature Flags** section to the existing Admin Settings page
(`/admin/settings`). It is a pure UI convenience layer — no business logic changes, no
new tables, no new service modules. All flags already exist in the `settings` table and
are already read by the application. This task only adds a way to view and change them
from the browser.

---

## Done Looks Like

- A new **"Feature Flags"** card/section appears at the bottom of the existing Admin
  Settings page (`templates/admin_settings.html`), visible to `admin` role only (not
  `warehouse_manager`).
- The section lists every flag from `PHASE1_DEFAULTS` in `services/settings_defaults.py`
  that is not an operational setting (i.e. not company name, bank details, skip reasons,
  etc.) — specifically the flags listed in **Scope** below.
- Each flag row shows: **flag name** (human-readable label), **key** (monospace, for
  reference), **current value** (green ON / red OFF badge), **description** (one sentence),
  and a **toggle button** (Activate / Deactivate).
- Toggling a flag writes the new value to the `settings` table via the existing
  `Setting.set()` / `save_setting()` pattern already used in `routes.py:2027-2035`.
- Each flag change writes an `activity_log` row (`activity_type = 'feature_flag_change'`,
  `picker_username = current_user.username`, `details = JSON: {key, old_value, new_value}`).
- After any toggle, the page reloads and shows a flash message:
  `"Flag '<human label>' set to <ON/OFF> by <username>"`.
- Three flags are marked **RED / HIGH RISK** with a warning tooltip and a confirmation
  modal before toggling: `permissions_enforcement_enabled`,
  `use_db_backed_picking_queue`, `summer_cooler_mode_enabled`. The modal text is
  specific per flag (see **High-Risk Warnings** below).
- Three flags are marked **DISABLED** (rendered but not toggleable) with a tooltip
  explaining why: `batch_claim_required`, `enable_consolidated_batch_picking`,
  `cooler_picking_enabled` — these depend on Phase 5 cooler code that is not yet
  built. They are shown for visibility only.
- `permissions_auto_seed_done` is shown as **read-only** (not toggleable). It is
  informational only — it tells the operator whether the permission seeder has run.
- The page passes all existing automated tests. No regressions.

---

## Scope — Flags To Manage

Group the flags into visual sections matching their operational area.

### Section 1 — Permissions

| Key | Human Label | Default | Risk |
|-----|-------------|---------|------|
| `permissions_enforcement_enabled` | Permission Enforcement | false | 🔴 HIGH |
| `permissions_menu_filtering_enabled` | Menu Filtering | true | YELLOW |
| `permissions_role_fallback_enabled` | Role Fallback Safety Net | true | YELLOW |
| `permissions_auto_seed_done` | Seeder Has Run | false | READ-ONLY |

### Section 2 — Job Runs & Logging

| Key | Human Label | Default | Risk |
|-----|-------------|---------|------|
| `forecast_watchdog_enabled` | Forecast Watchdog (5-min cadence) | false | YELLOW |
| `job_log_cleanup_enabled` | Daily Log Cleanup | false | GREEN |
| `job_runs_retention_days` | Log Retention Days | 90 | NUMERIC (see below) |

### Section 3 — Batch Picking

| Key | Human Label | Default | Risk |
|-----|-------------|---------|------|
| `use_db_backed_picking_queue` | DB-Backed Picking Queue | false | 🔴 HIGH |
| `allow_legacy_session_picking_fallback` | Legacy Session Fallback | true | YELLOW |
| `batch_claim_required` | Claim Required Before Picking | false | DISABLED |
| `enable_consolidated_batch_picking` | Consolidated Batch Picking | false | DISABLED |

### Section 4 — Cooler Picking (not yet built)

| Key | Human Label | Default | Risk |
|-----|-------------|---------|------|
| `summer_cooler_mode_enabled` | Cooler Mode (SENSITIVE items) | false | 🔴 HIGH |
| `cooler_picking_enabled` | Cooler Picking UI | false | DISABLED |
| `cooler_labels_enabled` | Cooler Label Printing | false | GREEN |
| `cooler_driver_view_enabled` | Driver Cooler Loading View | false | GREEN |

### Section 5 — Cockpit

| Key | Human Label | Default | Risk |
|-----|-------------|---------|------|
| `cockpit_enabled` | Account Manager Cockpit | false | YELLOW |

### `job_runs_retention_days` — Numeric Field

This is not a boolean flag. Render it as a small numeric input (integer, min 0, max 365)
with a Save button separate from the toggles. Value `0` means "cron runs but deletes
nothing." Current value loaded from `settings` table.

---

## High-Risk Warnings

When a user clicks the toggle for a HIGH RISK flag, show a Bootstrap modal (not a
browser `confirm()`) before saving. Each modal has specific text:

### `permissions_enforcement_enabled`

```
Title: Activate Permission Enforcement

Before activating, confirm:
• The permission seeder has run (permissions_auto_seed_done = true above)
• You have tested in development with the verification guide
• All active users have been seeded with their role permissions

Once active, users without the correct permission key will receive a 403
error. Admins are unaffected (wildcard * covers everything).

To check readiness, run: SELECT key, value FROM settings
WHERE key = 'permissions_auto_seed_done';

[ Cancel ]  [ I confirm — Activate ]
```

### `use_db_backed_picking_queue`

```
Title: Activate DB-Backed Picking Queue

This is the highest-risk flag in the system. Before activating:
• Complete the drain workflow — no active batches should be in progress
• Confirm allow_legacy_session_picking_fallback = true (safety net)
• This has not been activated in production before — test on a quiet day

Existing batches created before this flip will continue on the legacy path.
New batches will use the DB-backed queue.

[ Cancel ]  [ I confirm — Activate ]
```

### `summer_cooler_mode_enabled`

```
Title: Activate Cooler Mode

This separates SENSITIVE items from the normal picking queue. Before activating:
• Confirm cooler_picking_enabled is also ON (pickers need the cooler UI)
• Coordinate with the warehouse team — cooler picking workflow changes today
• Activate on a pilot route first, not all routes simultaneously

[ Cancel ]  [ I confirm — Activate ]
```

---

## Out of Scope

- No new database tables or migrations.
- No changes to `services/settings_defaults.py`.
- No changes to any flag-reading code elsewhere in the application.
- No changes to the existing Admin Settings form fields (picking behaviour, company
  details, forecast parameters, OOS exclusions, etc.) — the Feature Flags section is
  purely additive at the bottom of the same page.
- No separate page — this is a new section on the existing `/admin/settings` page.
- No role other than `admin` can see or interact with the Feature Flags section.
  `warehouse_manager` can use the rest of the settings page as today but the Feature
  Flags section must be gated with `{% if current_user.role == 'admin' %}`.
- No REST API for flag management — the existing POST handler is extended, not replaced.
- No email notifications on flag changes.

---

## Implementation Notes

### Where to add the route logic

Extend the existing `admin_settings` route in `routes.py:2004`. The POST handler already
saves individual settings via `save_setting()` (lines 2027-2035). Add flag toggle
handling to the same POST handler — detect a `form.get('flag_key')` and
`form.get('flag_value')` pair and call `save_setting(flag_key, flag_value)` after
validating the key is in the allowed list below.

**Allowed keys whitelist** (only these keys may be written via this UI — prevents
arbitrary settings manipulation):

```python
FEATURE_FLAG_KEYS = {
    'permissions_enforcement_enabled',
    'permissions_menu_filtering_enabled',
    'permissions_role_fallback_enabled',
    'forecast_watchdog_enabled',
    'job_log_cleanup_enabled',
    'job_runs_retention_days',
    'use_db_backed_picking_queue',
    'allow_legacy_session_picking_fallback',
    'batch_claim_required',
    'enable_consolidated_batch_picking',
    'summer_cooler_mode_enabled',
    'cooler_picking_enabled',
    'cooler_labels_enabled',
    'cooler_driver_view_enabled',
    'cockpit_enabled',
}
```

Any POST with a `flag_key` not in this set must be rejected with a 400 response.

### Audit log

Use the existing `ActivityLog` model (`models.py:710`). Write one row per flag change:

```python
import json
log = ActivityLog(
    picker_username=current_user.username,
    activity_type='feature_flag_change',
    details=json.dumps({
        'key': flag_key,
        'old_value': old_value,
        'new_value': flag_value,
    })
)
db.session.add(log)
```

### Template structure

Add the Feature Flags section to `templates/admin_settings.html` after all existing
cards, inside an `{% if current_user.role == 'admin' %}` block. Use the same Bootstrap
card pattern as the existing sections. Each flag row should be a table row inside a
`<table class="table table-sm">` with columns: Label | Key | Status | Description |
Action.

Flag toggle buttons POST to the same `/admin/settings` URL with two hidden fields:
`flag_key` and `flag_value` (the new value, opposite of current). The Bootstrap
confirmation modal intercepts the submit event in JavaScript for HIGH RISK flags before
allowing the POST to proceed.

---

## Required Tests

Add to `tests/test_feature_flags_ui.py`:

| # | Scenario | Expected |
|---|----------|----------|
| T1 | Admin GET `/admin/settings` | Feature Flags section visible |
| T2 | Warehouse manager GET `/admin/settings` | Feature Flags section NOT visible |
| T3 | Admin POST valid flag key + value | Setting updated; flash message; activity_log row written |
| T4 | Admin POST flag key NOT in whitelist | 400 response; setting not changed |
| T5 | Admin POST `permissions_auto_seed_done` (read-only) | Rejected — not in whitelist |
| T6 | `job_runs_retention_days` set to 0 | Value saved as `'0'` |
| T7 | `job_runs_retention_days` set to -1 | 400 response; not saved |
| T8 | `job_runs_retention_days` set to 366 | 400 response; not saved |
| T9 | Toggle any flag | ActivityLog row written with correct details JSON |
| T10 | All 15 flags in FEATURE_FLAG_KEYS load their current value from DB | Verified for each |

---

## Closeout

When complete, provide:

1. Screenshot or description of the rendered Feature Flags section showing all 5 groups
2. All T1–T10 tests passing: `pytest -q tests/test_feature_flags_ui.py`
3. Manual confirmation: flip `forecast_watchdog_enabled` to `true` and back via the UI
   in development; confirm ActivityLog has 2 entries for the test
4. Confirm no regression: `pytest -q tests/test_override_ordering_pipeline.py
   tests/test_permissions.py tests/test_phase3_closeout_matrix.py`
5. Append assumption entries to `ASSUMPTIONS_LOG.md` for any autonomous decisions
   (UI layout choices, modal trigger mechanism, etc.)

---

## Critical Constraints

- Do NOT modify any flag-reading code. The UI reads and writes the `settings` table only.
- Do NOT add any new scheduled jobs.
- Do NOT change any seeded defaults in `services/settings_defaults.py`.
- The whitelist check is mandatory — a missing whitelist check is a security issue.
- The Feature Flags section must be completely invisible to `warehouse_manager` and
  all other non-admin roles.
- Production flag values must NOT be changed as part of this development task. Build
  and test in development only.
