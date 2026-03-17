#!/bin/bash
set -euo pipefail

source /home/eplattforma/.db_export_env

EXPORT_DIR="/home/eplattforma/public_html/eplattforma.com.cy/ReplitConnect"
FINAL_FILE="${EXPORT_DIR}/customer_price_master.csv"
TMP_FILE="${EXPORT_DIR}/.customer_price_master_tmp_$$.csv"
LOG_FILE="/home/eplattforma/logs/export_price_master.log"

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$EXPORT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "START export_customer_price_master"

mysql -h localhost -u eplattforma_readonlymag -p"${DB_PASSWORD}" eplattforma_mage \
  --batch --raw -e "
SELECT
    'snapshot_at','customer_id','customer_email','sku','product_name','rule_code','rule_name','rule_description','origin_price','customer_final_price'
UNION ALL
SELECT
    snapshot_at,
    customer_id,
    customer_email,
    sku,
    REPLACE(REPLACE(product_name, ',', ' '), '\n', ' '),
    rule_code,
    REPLACE(REPLACE(rule_name, ',', ' '), '\n', ' '),
    REPLACE(REPLACE(IFNULL(rule_description,''), ',', ' '), '\n', ' '),
    origin_price,
    customer_final_price
FROM (
    SELECT
        NOW() AS snapshot_at,
        ce.entity_id AS customer_id,
        ce.email AS customer_email,
        cpe.sku,
        pp.name AS product_name,
        pr.id AS rule_code,
        pr.name AS rule_name,
        pr.description AS rule_description,
        pp.origin_price,
        idx.final_price AS customer_final_price,
        ROW_NUMBER() OVER (
            PARTITION BY ce.entity_id, cpe.entity_id, idx.website_id
            ORDER BY
                CASE WHEN pr.priority IS NULL THEN 999999 ELSE pr.priority END ASC,
                pr.id ASC
        ) AS rn
    FROM bss_applied_customers ac
    JOIN customer_entity ce
        ON ce.entity_id = ac.customer_id
    JOIN bss_price_rules pr
        ON pr.id = ac.rule_id
    JOIN bss_custom_pricing_index idx
        ON idx.rule_id = ac.rule_id
       AND idx.customer_group_id = ce.group_id
    JOIN catalog_product_entity cpe
        ON cpe.entity_id = idx.product_id
    LEFT JOIN bss_product_price pp
        ON pp.rule_id = ac.rule_id
       AND pp.product_id = idx.product_id
    WHERE COALESCE(pr.status, 0) = 1
      AND COALESCE(ac.applied_rule, 1) = 1
) ranked
WHERE rn = 1
ORDER BY customer_id, sku;
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
log "END export_customer_price_master"
