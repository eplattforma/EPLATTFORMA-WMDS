"""Phase 1 Foundation: seed safe defaults for every flag introduced by the brief.

Idempotent and concurrency-safe — uses INSERT ... ON CONFLICT DO NOTHING so that
multiple gunicorn workers booting in parallel cannot race each other into a
UniqueViolation, and never overwrites a value the operator has set.
"""
import logging

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


PHASE1_DEFAULTS = {
    # Global safety
    "wmds_development_batch_enabled": "true",
    "maintenance_mode": "normal",

    # Permissions. Per the WMDS Verification & Closeout brief Section 1.2
    # (Option A), enforcement ships OFF — admins manually flip
    # ``permissions_enforcement_enabled`` to ``true`` from the Settings UI
    # when production is ready. While off, ``@require_permission`` only
    # logs missing keys (see ``services/permissions.py``). Role fallback
    # stays ON so the eventual flip cannot lock out admin / warehouse
    # manager / crm_admin users who have no explicit grants yet. The
    # auto-seeder still runs once on first boot so explicit rows exist
    # by the time enforcement is flipped on.
    "permissions_enforcement_enabled": "false",
    "permissions_menu_filtering_enabled": "true",
    "permissions_role_fallback_enabled": "true",
    "permissions_auto_seed_done": "false",

    # Job Runs & Logging
    "job_runs_enabled": "true",
    "new_logging_enabled": "true",
    "job_runs_write_enabled": "true",
    "job_runs_ui_enabled": "true",
    "forecast_watchdog_enabled": "false",
    "job_log_cleanup_enabled": "false",
    "job_log_retention_days": "90",
    # Phase 4 — canonical retention key consumed by
    # ``services.maintenance.log_cleanup.delete_old_job_runs``.
    # ``job_log_retention_days`` is preserved above as a legacy alias
    # (already documented in ROLLBACK_AND_FLAGS.md / referenced by
    # historical scripts) but Phase 4 code reads only this key.
    "job_runs_retention_days": "90",
    "forecast_heartbeat_timeout_seconds": "2700",
    "forecast_watchdog_interval_minutes": "5",
    "forecast_max_duration_seconds": "3600",

    # Replenishment
    "legacy_replenishment_enabled": "false",

    # Batch Picking (defaults from brief Section 14)
    "use_db_backed_picking_queue": "false",
    "allow_legacy_session_picking_fallback": "true",
    "enable_consolidated_batch_picking": "false",
    "batch_claim_required": "false",

    # Cooler Picking (all OFF by default; turned on in Phase 5)
    "summer_cooler_mode_enabled": "false",
    "cooler_picking_enabled": "false",
    "cooler_labels_enabled": "false",
    "cooler_driver_view_enabled": "false",

    # Cockpit (Account-Manager Cockpit, default OFF — see ROLLBACK_AND_FLAGS.md).
    # When ``false`` the entire ``/cockpit/...`` URL space returns HTTP 404 and
    # the menu entry is hidden; permission keys are registered but unassigned.
    "cockpit_enabled": "false",

    # Forecast Week Rollover (Task #29 — Configurable Week Cutoff).
    # Weekday 0=Mon … 6=Sun; default Friday. Time is Athens wall-clock (HH:MM).
    # When Athens local time reaches this weekday+time, the in-progress week is
    # treated as "complete enough" and included in the forecast.
    "forecast_week_rollover_weekday": "4",
    "forecast_week_rollover_time": "10:00",
}


def ensure_phase1_settings_defaults():
    """Insert default Phase 1 settings if missing.

    Uses an isolated engine connection (not the request/session scoped
    `db.session`) and a single INSERT ... ON CONFLICT DO NOTHING per row so
    parallel workers can run this safely on boot without colliding on the
    `settings.key` primary key.
    """
    inserted = 0
    skipped = 0
    try:
        with db.engine.connect() as conn:
            for key, default in PHASE1_DEFAULTS.items():
                result = conn.execute(
                    text(
                        "INSERT INTO settings (key, value) "
                        "VALUES (:key, :value) "
                        "ON CONFLICT (key) DO NOTHING"
                    ),
                    {"key": key, "value": default},
                )
                if (result.rowcount or 0) > 0:
                    inserted += 1
                else:
                    skipped += 1
            conn.commit()
        logger.info(
            f"Phase 1 settings: inserted {inserted} new default(s), "
            f"kept {skipped} existing"
        )
    except Exception as e:
        logger.error(f"ensure_phase1_settings_defaults failed: {e}")
