"""
Expiry dates export and FTP upload service.
Exports ExpiryDates.csv from the database and uploads it to the FTP server atomically.
"""

import logging
import os
from ftplib import FTP, FTP_TLS
from datetime import datetime
from app import db
from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)


def validate_export_file(path: str) -> None:
    """Validate that the export file exists, is not empty, and has correct header."""
    if not os.path.exists(path):
        raise RuntimeError(f"Export file not found: {path}")

    if os.path.getsize(path) == 0:
        raise RuntimeError(f"Export file is empty: {path}")

    with open(path, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()

    if first_line != "SKU,expiry_date":
        raise RuntimeError(f"Unexpected CSV header: {first_line}")


def export_expiry_dates_for_magento(local_dir: str) -> str:
    """
    Build ExpiryDates.csv from database and return the full local file path.
    
    Exports SKU and expiry_date from stock_positions table.
    Keeps duplicates, sorts by SKU ASC, expiry_date DESC.
    This ensures earliest expiry date appears last for each SKU (first imported by Magento).
    
    Args:
        local_dir: Directory to save the CSV file to
        
    Returns:
        Full path to the exported CSV file
        
    Raises:
        RuntimeError: If export fails or returns no data
    """
    os.makedirs(local_dir, exist_ok=True)
    
    # Query stock_positions for SKU and expiry_date
    try:
        result = db.session.execute(
            sa_text("""
                SELECT item_code AS SKU, expiry_date
                FROM stock_positions
                WHERE expiry_date IS NOT NULL AND expiry_date != ''
                ORDER BY SKU ASC, expiry_date DESC
            """)
        )
        rows = result.fetchall()
    except Exception as e:
        logger.error("Failed to query stock_positions: %s", e)
        raise RuntimeError(f"Failed to query expiry dates from database: {e}")

    if not rows:
        logger.warning("No expiry dates found in stock_positions table")
        raise RuntimeError("No expiry dates found in database")

    # Write CSV file
    csv_path = os.path.join(local_dir, "ExpiryDates.csv")
    try:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("SKU,expiry_date\n")
            for row in rows:
                sku = str(row[0]).strip() if row[0] else ""
                exp_date = str(row[1]).strip() if row[1] else ""
                if sku and exp_date:
                    f.write(f"{sku},{exp_date}\n")
    except Exception as e:
        logger.error("Failed to write CSV file: %s", e)
        raise RuntimeError(f"Failed to write export file: {e}")

    total_rows = len(rows)
    unique_skus = len(set(row[0] for row in rows))
    duplicate_rows = total_rows - unique_skus
    file_size = os.path.getsize(csv_path)

    logger.info(
        "Expiry export created: path=%s, total_rows=%d, unique_skus=%d, "
        "duplicate_rows=%d, file_size=%d bytes",
        csv_path, total_rows, unique_skus, duplicate_rows, file_size
    )

    return csv_path


def upload_file_to_expiry_ftp(local_file_path: str, max_retries: int = 3) -> None:
    """
    Upload ExpiryDates.csv to FTP server atomically.
    
    Uploads to a temporary file (.tmp) first, then renames on the server.
    This prevents Magento from reading a half-uploaded file.
    
    Args:
        local_file_path: Full path to the local CSV file
        max_retries: Number of retry attempts for upload
        
    Raises:
        RuntimeError: If upload fails after all retries or FTP operation fails
    """
    host = os.environ.get("EXPIRY_FTP_HOST")
    port = int(os.environ.get("EXPIRY_FTP_PORT", "21"))
    username = os.environ.get("EXPIRY_FTP_USERNAME")
    password = os.environ.get("EXPIRY_FTP_PASSWORD")
    remote_dir = os.environ.get("EXPIRY_FTP_REMOTE_DIR", "/")
    use_tls = os.environ.get("EXPIRY_FTP_USE_TLS", "false").lower() == "true"
    timeout = int(os.environ.get("EXPIRY_FTP_TIMEOUT", "30"))

    if not all([host, username, password]):
        raise RuntimeError("FTP credentials not configured (EXPIRY_FTP_HOST, EXPIRY_FTP_USERNAME, EXPIRY_FTP_PASSWORD)")

    final_name = "ExpiryDates.csv"
    temp_name = final_name + ".tmp"

    for attempt in range(1, max_retries + 1):
        ftp = None
        try:
            logger.info(
                "FTP upload attempt %d/%d: host=%s, port=%d, remote_dir=%s, use_tls=%s",
                attempt, max_retries, host, port, remote_dir, use_tls
            )

            # Connect to FTP
            if use_tls:
                ftp = FTP_TLS()
                ftp.connect(host, port, timeout=timeout)
                ftp.login(username, password)
                ftp.prot_p()
            else:
                ftp = FTP()
                ftp.connect(host, port, timeout=timeout)
                ftp.login(username, password)

            ftp.set_pasv(True)

            # Change to remote directory
            if remote_dir and remote_dir != "/":
                try:
                    ftp.cwd(remote_dir)
                except Exception as e:
                    logger.error("Failed to change to remote directory %s: %s", remote_dir, e)
                    raise

            # Upload to temporary file
            with open(local_file_path, "rb") as f:
                ftp.storbinary(f"STOR {temp_name}", f)
            logger.info("Uploaded to temporary file: %s", temp_name)

            # Delete old live file if it exists
            try:
                ftp.delete(final_name)
                logger.info("Deleted old file: %s", final_name)
            except Exception:
                logger.debug("Old file %s did not exist or could not be deleted", final_name)

            # Rename temp file to final name
            ftp.rename(temp_name, final_name)
            logger.info("Renamed %s to %s", temp_name, final_name)

            logger.info("FTP upload succeeded on attempt %d", attempt)
            return  # Success

        except Exception as e:
            logger.warning(
                "FTP upload failed on attempt %d/%d: %s",
                attempt, max_retries, e
            )

            # Wait before retry (except on last attempt)
            if attempt < max_retries:
                wait_time = 2 if attempt == 1 else 5
                logger.info("Waiting %d seconds before retry...", wait_time)
                import time
                time.sleep(wait_time)
            else:
                # Last attempt failed
                raise RuntimeError(f"FTP upload failed after {max_retries} attempts: {e}")

        finally:
            # Always close connection
            if ftp is not None:
                try:
                    ftp.quit()
                except Exception:
                    try:
                        ftp.close()
                    except Exception:
                        pass


def run_expiry_dates_export_and_upload() -> dict:
    """
    Main function: export expiry dates from DB and upload to FTP.
    
    Returns:
        dict with keys: csv_export (success/fail), ftp_upload (success/fail), details
    """
    result = {
        "csv_export": "fail",
        "ftp_upload": "fail",
        "details": {}
    }

    try:
        # Step 1: Export to CSV
        logger.info("Starting expiry dates export...")
        local_dir = os.environ.get("EXPIRY_EXPORT_DIR", "/tmp")
        csv_path = export_expiry_dates_for_magento(local_dir)
        
        # Step 2: Validate export
        logger.info("Validating export file...")
        validate_export_file(csv_path)
        result["csv_export"] = "success"
        result["details"]["csv_path"] = csv_path
        result["details"]["csv_size"] = os.path.getsize(csv_path)

    except Exception as e:
        logger.error("CSV export failed: %s", e)
        result["details"]["csv_error"] = str(e)
        return result

    # Step 3: Upload to FTP (only if CSV export succeeded)
    try:
        logger.info("Starting FTP upload...")
        upload_file_to_expiry_ftp(csv_path)
        result["ftp_upload"] = "success"
        logger.info("Expiry dates export and FTP upload completed successfully")

    except Exception as e:
        logger.error("FTP upload failed: %s", e)
        result["details"]["ftp_error"] = str(e)

    return result
