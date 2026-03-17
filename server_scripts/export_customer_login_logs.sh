#!/bin/bash
set -euo pipefail

source /home/eplattforma/.db_export_env

EXPORT_DIR="/home/eplattforma/public_html/eplattforma.com.cy/ReplitConnect"
FINAL_FILE="${EXPORT_DIR}/customer_login_logs.csv"
TMP_FILE="${EXPORT_DIR}/.customer_login_logs_tmp_$$.csv"
LOG_FILE="/home/eplattforma/logs/export_login_logs.log"

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$EXPORT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "START export_customer_login_logs"

mysql -h localhost -u eplattforma_readonlymag -p"${DB_PASSWORD}" eplattforma_mage \
  --batch --raw -e "
SELECT
    'log_id','customer_id','email','first_name','last_name','ps365_code','last_login_at','last_logout_at'
UNION ALL
SELECT
    log_id,
    customer_id,
    email,
    first_name,
    last_name,
    ps365_code,
    last_login_at,
    IFNULL(last_logout_at, '')
FROM bss_customer_login_logs
WHERE last_login_at >= NOW() - INTERVAL 10 DAY
   OR last_logout_at >= NOW() - INTERVAL 10 DAY
ORDER BY log_id;
" | sed 's/\t/,/g' > "$TMP_FILE" 2>> "$LOG_FILE"

ROW_COUNT=$(wc -l < "$TMP_FILE")
ROW_COUNT=$((ROW_COUNT - 1))

if [ "$ROW_COUNT" -lt 1 ]; then
    log "ERROR: Query returned 0 data rows — keeping previous file"
    rm -f "$TMP_FILE"
    exit 1
fi

mv "$TMP_FILE" "$FINAL_FILE"
chmod 644 "$FINAL_FILE"

log "OK: ${ROW_COUNT} rows written to ${FINAL_FILE}"
log "END export_customer_login_logs"
