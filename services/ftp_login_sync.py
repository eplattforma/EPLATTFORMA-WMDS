import csv
import ftplib
import io
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

FTP_HOST = "195.201.199.118"
FTP_PORT = 21
REMOTE_FILE = "customer_login_logs.csv"
SOURCE_TAG = "ftp_login_sync"


def sync_login_logs_from_ftp():
    from app import app, db
    from sqlalchemy import text

    ftp_user = os.environ.get("FTP_USERNAME", "")
    ftp_pass = os.environ.get("FTP_PASSWORD", "")

    if not ftp_user or not ftp_pass:
        logger.error("FTP credentials not configured (FTP_USERNAME / FTP_PASSWORD)")
        return {"success": False, "error": "FTP credentials missing"}

    try:
        ftp = ftplib.FTP(FTP_HOST, timeout=30)
        ftp.login(ftp_user, ftp_pass)
        logger.info(f"FTP connected to {FTP_HOST}")

        buf = io.BytesIO()
        ftp.retrbinary(f"RETR {REMOTE_FILE}", buf.write)
        ftp.quit()

        buf.seek(0)
        text_data = buf.read().decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text_data))
        rows = list(reader)
        logger.info(f"FTP login CSV: {len(rows)} rows downloaded")

        now = datetime.now(timezone.utc)
        inserted = 0
        updated = 0

        with app.app_context():
            for row in rows:
                try:
                    log_id = int(row["log_id"])
                except (ValueError, KeyError):
                    continue

                try:
                    magento_id = int(row["customer_id"])
                except (ValueError, KeyError):
                    magento_id = 0

                ps365_code = row.get("ps365_code", "").strip()
                email = row.get("email", "").strip()
                first_name = row.get("first_name", "").strip()
                last_name = row.get("last_name", "").strip()
                last_login = row.get("last_login_at", "").strip()
                last_logout = row.get("last_logout_at", "").strip() or None

                if not last_login:
                    continue

                existing = db.session.execute(
                    text("SELECT 1 FROM magento_customer_login_log WHERE log_id = :lid"),
                    {"lid": log_id},
                ).fetchone()

                if existing:
                    if last_logout:
                        result = db.session.execute(
                            text(
                                "UPDATE magento_customer_login_log "
                                "SET last_logout_at = :lo "
                                "WHERE log_id = :lid AND (last_logout_at IS NULL OR last_logout_at < :lo)"
                            ),
                            {"lid": log_id, "lo": last_logout},
                        )
                        if result.rowcount > 0:
                            updated += 1
                    continue

                db.session.execute(
                    text(
                        "INSERT INTO magento_customer_login_log "
                        "(log_id, magento_customer_id, customer_code_365, email, "
                        "first_name, last_name, last_login_at, last_logout_at, "
                        "imported_at, source_filename) "
                        "VALUES (:lid, :mid, :cc, :em, :fn, :ln, :li, :lo, :ia, :sf)"
                    ),
                    {
                        "lid": log_id, "mid": magento_id, "cc": ps365_code,
                        "em": email, "fn": first_name, "ln": last_name,
                        "li": last_login, "lo": last_logout,
                        "ia": now, "sf": SOURCE_TAG,
                    },
                )
                inserted += 1

            db.session.commit()

            db.session.execute(text("TRUNCATE TABLE magento_customer_last_login_current"))
            db.session.execute(
                text(
                    "INSERT INTO magento_customer_last_login_current "
                    "(customer_code_365, magento_customer_id, last_login_at, last_logout_at, "
                    "email, first_name, last_name, imported_at, source_filename) "
                    "SELECT DISTINCT ON (customer_code_365) "
                    "customer_code_365, magento_customer_id, last_login_at, last_logout_at, "
                    "email, first_name, last_name, imported_at, source_filename "
                    "FROM magento_customer_login_log "
                    "WHERE customer_code_365 IS NOT NULL AND customer_code_365 != '' "
                    "ORDER BY customer_code_365, last_login_at DESC"
                )
            )
            db.session.commit()

            current_count = db.session.execute(
                text("SELECT count(*) FROM magento_customer_last_login_current")
            ).scalar()

        logger.info(
            f"FTP login sync complete: {inserted} inserted, {updated} updated, "
            f"{current_count} customers in last_login_current"
        )
        return {
            "success": True,
            "inserted": inserted,
            "updated": updated,
            "total_csv_rows": len(rows),
            "current_count": current_count,
        }

    except Exception as e:
        logger.error(f"FTP login sync failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
