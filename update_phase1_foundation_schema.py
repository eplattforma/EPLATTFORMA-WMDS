"""Phase 1 Foundation schema migration.

Adds (additive, idempotent):
  - users.display_name VARCHAR(120) NULL  (backfilled with username)
  - job_runs                              (new table, see brief Section 8)
  - user_permissions                      (new table, see brief Section 6)

All operations are guarded by IF NOT EXISTS / IF EXISTS so re-running is safe.
"""
import logging
from sqlalchemy import text, inspect
from app import db

logger = logging.getLogger(__name__)


def update_phase1_foundation_schema():
    try:
        with db.engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(120)"
            ))
            conn.execute(text(
                "UPDATE users SET display_name = username WHERE display_name IS NULL"
            ))
            logger.info("Phase 1: users.display_name ensured + backfilled")

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS job_runs (
                    id BIGSERIAL PRIMARY KEY,
                    job_id VARCHAR(100) NOT NULL,
                    job_name VARCHAR(200),
                    trigger_source VARCHAR(40) NOT NULL DEFAULT 'scheduled',
                    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    finished_at TIMESTAMP WITH TIME ZONE,
                    duration_seconds NUMERIC(12,3),
                    last_heartbeat TIMESTAMP WITH TIME ZONE,
                    status VARCHAR(30) NOT NULL DEFAULT 'RUNNING',
                    current_step VARCHAR(200),
                    progress_current INTEGER,
                    progress_total INTEGER,
                    progress_message TEXT,
                    result_summary JSONB,
                    error_message TEXT,
                    metadata JSONB,
                    created_by VARCHAR(64),
                    parent_run_id BIGINT REFERENCES job_runs(id) ON DELETE SET NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_job_runs_job_started "
                "ON job_runs (job_id, started_at DESC)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_job_runs_status "
                "ON job_runs (status)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_job_runs_running_heartbeat "
                "ON job_runs (last_heartbeat) WHERE status = 'RUNNING'"
            ))
            logger.info("Phase 1: job_runs table + indexes ensured")

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_permissions (
                    id BIGSERIAL PRIMARY KEY,
                    username VARCHAR(64) NOT NULL REFERENCES users(username) ON DELETE CASCADE,
                    permission_key VARCHAR(120) NOT NULL,
                    granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    granted_by VARCHAR(64),
                    UNIQUE (username, permission_key)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_user_permissions_username "
                "ON user_permissions (username)"
            ))
            logger.info("Phase 1: user_permissions table + index ensured")

            conn.commit()

        insp = inspect(db.engine)
        users_cols = {c["name"] for c in insp.get_columns("users")}
        if "display_name" not in users_cols:
            raise RuntimeError("users.display_name not present after migration")
        if "job_runs" not in insp.get_table_names():
            raise RuntimeError("job_runs table not present after migration")
        if "user_permissions" not in insp.get_table_names():
            raise RuntimeError("user_permissions table not present after migration")

        logger.info("Phase 1 foundation schema completed successfully")
    except Exception as e:
        logger.error(f"Phase 1 foundation schema failed: {e}")
        raise
