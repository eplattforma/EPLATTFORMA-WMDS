-- =====================================================
-- Power BI Views for Warehouse Management System
-- Run against PostgreSQL production database
-- =====================================================

-- ===== DIMENSION: Customers =====
CREATE OR REPLACE VIEW pbi_dim_customers AS
SELECT
    c.customer_code_365 AS customer_code,
    c.company_name AS customer_name,
    c.is_company,
    c.category_1_name AS customer_category,
    c.company_activity_name AS business_activity,
    c.agent_name AS sales_agent,
    c.town,
    c.postal_code,
    c.address_line_1,
    c.address_line_2,
    c.address_line_3,
    c.tel_1 AS phone,
    c.mobile,
    c.vat_registration_number AS vat_no,
    c.credit_limit_amount AS credit_limit,
    c.latitude,
    c.longitude,
    COALESCE(c.is_active, true) AS is_active
FROM ps_customers c
WHERE c.deleted_at IS NULL;

-- ===== DIMENSION: Products =====
CREATE OR REPLACE VIEW pbi_dim_products AS
SELECT
    i.item_code_365 AS item_code,
    i.item_name,
    COALESCE(i.active, true) AS is_active,
    i.barcode,
    i.supplier_item_code,
    cat.category_name AS category,
    b.brand_name AS brand,
    a3.attribute_3_name AS zone_name,
    i.attribute_1_code_365 AS attribute_1,
    i.attribute_2_code_365 AS attribute_2,
    i.attribute_3_code_365 AS zone_code,
    i.attribute_4_code_365 AS attribute_4,
    i.attribute_5_code_365 AS attribute_5,
    i.attribute_6_code_365 AS attribute_6,
    i.item_weight,
    i.selling_qty,
    i.number_of_pieces,
    i.wms_zone,
    i.wms_unit_type,
    i.wms_fragility,
    i.wms_temperature_sensitivity
FROM ps_items_dw i
LEFT JOIN dw_item_categories cat ON cat.category_code_365 = i.category_code_365
LEFT JOIN dw_brands b ON b.brand_code_365 = i.brand_code_365
LEFT JOIN dw_attribute3 a3 ON a3.attribute_3_code_365 = i.attribute_3_code_365;

-- ===== DIMENSION: Stores =====
CREATE OR REPLACE VIEW pbi_dim_stores AS
SELECT
    s.store_code_365 AS store_code,
    s.store_name
FROM dw_store s;

-- ===== DIMENSION: Date Calendar =====
CREATE OR REPLACE VIEW pbi_dim_dates AS
SELECT
    d::date AS date_key,
    EXTRACT(YEAR FROM d)::int AS year,
    EXTRACT(QUARTER FROM d)::int AS quarter,
    EXTRACT(MONTH FROM d)::int AS month_no,
    TO_CHAR(d, 'Month') AS month_name,
    TO_CHAR(d, 'Mon') AS month_short,
    EXTRACT(WEEK FROM d)::int AS week_no,
    EXTRACT(DOW FROM d)::int AS day_of_week_no,
    TO_CHAR(d, 'Day') AS day_name,
    TO_CHAR(d, 'YYYY-MM') AS year_month,
    TO_CHAR(d, 'YYYY') || '-Q' || EXTRACT(QUARTER FROM d) AS year_quarter,
    CASE WHEN EXTRACT(DOW FROM d) IN (0,6) THEN false ELSE true END AS is_weekday
FROM generate_series('2023-01-01'::date, '2027-12-31'::date, '1 day'::interval) d;

-- ===== FACT: Sales (line level) =====
CREATE OR REPLACE VIEW pbi_fact_sales AS
SELECT
    l.id AS line_id,
    h.invoice_no_365 AS invoice_no,
    h.invoice_type,
    h.invoice_date_utc0 AS invoice_date,
    h.customer_code_365 AS customer_code,
    h.store_code_365 AS store_code,
    h.user_code_365 AS salesperson_code,
    l.item_code_365 AS item_code,
    l.line_number,
    l.quantity,
    l.price_excl,
    l.price_incl,
    l.discount_percent,
    l.vat_percent,
    l.line_total_excl,
    l.line_total_discount,
    l.line_total_vat,
    l.line_total_incl,
    EXTRACT(YEAR FROM h.invoice_date_utc0) AS year,
    EXTRACT(MONTH FROM h.invoice_date_utc0) AS month,
    EXTRACT(QUARTER FROM h.invoice_date_utc0) AS quarter,
    TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS year_month,
    TO_CHAR(h.invoice_date_utc0, 'Day') AS day_of_week,
    EXTRACT(DOW FROM h.invoice_date_utc0) AS day_of_week_no
FROM dw_invoice_line l
JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365;

-- ===== FACT: Invoices (header level) =====
CREATE OR REPLACE VIEW pbi_fact_invoices AS
SELECT
    h.invoice_no_365 AS invoice_no,
    h.invoice_type,
    h.invoice_date_utc0 AS invoice_date,
    h.customer_code_365 AS customer_code,
    h.store_code_365 AS store_code,
    h.user_code_365 AS salesperson_code,
    h.total_sub AS total_excl_vat,
    h.total_discount,
    h.total_vat,
    h.total_grand AS total_incl_vat,
    h.points_earned,
    h.points_redeemed,
    COUNT(l.id) AS line_count,
    SUM(l.quantity) AS total_qty,
    EXTRACT(YEAR FROM h.invoice_date_utc0) AS year,
    EXTRACT(MONTH FROM h.invoice_date_utc0) AS month,
    EXTRACT(QUARTER FROM h.invoice_date_utc0) AS quarter,
    TO_CHAR(h.invoice_date_utc0, 'YYYY-MM') AS year_month
FROM dw_invoice_header h
LEFT JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365
GROUP BY h.invoice_no_365, h.invoice_type, h.invoice_date_utc0,
         h.customer_code_365, h.store_code_365, h.user_code_365,
         h.total_sub, h.total_discount, h.total_vat, h.total_grand,
         h.points_earned, h.points_redeemed;

-- ===== FACT: Routes =====
CREATE OR REPLACE VIEW pbi_fact_routes AS
WITH route_counts AS (
    SELECT
        rs.shipment_id,
        COUNT(*) FILTER (WHERE rsi.is_active = true) AS invoice_count,
        COUNT(*) FILTER (WHERE rsi.is_active = true AND rsi.status = 'DELIVERED') AS delivered_count,
        COUNT(*) FILTER (WHERE rsi.is_active = true AND rsi.status = 'FAILED') AS failed_count
    FROM route_stop rs
    JOIN route_stop_invoice rsi ON rsi.route_stop_id = rs.route_stop_id
    WHERE rs.deleted_at IS NULL
    GROUP BY rs.shipment_id
),
stop_counts AS (
    SELECT shipment_id, COUNT(*) AS stop_count
    FROM route_stop
    WHERE deleted_at IS NULL
    GROUP BY shipment_id
)
SELECT
    s.id AS route_id,
    s.route_name,
    s.driver_name,
    s.status AS route_status,
    s.delivery_date,
    s.reconciliation_status,
    s.is_archived,
    s.created_at,
    s.started_at,
    s.completed_at,
    s.cash_expected,
    s.cash_collected,
    s.cash_handed_in,
    s.cash_variance,
    s.returns_count,
    CASE WHEN s.completed_at IS NOT NULL AND s.started_at IS NOT NULL
         THEN EXTRACT(EPOCH FROM (s.completed_at - s.started_at)) / 60.0
         ELSE NULL END AS duration_minutes,
    COALESCE(sc.stop_count, 0) AS stop_count,
    COALESCE(rc.invoice_count, 0) AS invoice_count,
    COALESCE(rc.delivered_count, 0) AS delivered_count,
    COALESCE(rc.failed_count, 0) AS failed_count
FROM shipments s
LEFT JOIN route_counts rc ON rc.shipment_id = s.id
LEFT JOIN stop_counts sc ON sc.shipment_id = s.id
WHERE s.deleted_at IS NULL;

-- ===== FACT: Route Deliveries (stop/invoice level) =====
CREATE OR REPLACE VIEW pbi_fact_route_deliveries AS
SELECT
    rsi.route_stop_invoice_id AS delivery_id,
    s.id AS route_id,
    s.route_name,
    s.driver_name,
    s.delivery_date,
    rs.route_stop_id AS stop_id,
    rs.seq_no AS stop_sequence,
    rs.stop_name,
    rs.stop_city,
    rs.customer_code,
    rsi.invoice_no,
    rsi.status AS delivery_status,
    rsi.expected_payment_method,
    rsi.expected_amount,
    rsi.discrepancy_value,
    rsi.weight_kg,
    rs.delivered_at,
    rs.failed_at,
    rs.failure_reason
FROM route_stop_invoice rsi
JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id
JOIN shipments s ON s.id = rs.shipment_id
WHERE rs.deleted_at IS NULL
  AND s.deleted_at IS NULL
  AND rsi.is_active = true;

-- ===== FACT: Picking Performance =====
CREATE OR REPLACE VIEW pbi_fact_picking AS
SELECT
    inv.invoice_no,
    inv.customer_name,
    inv.assigned_to AS picker,
    inv.status AS order_status,
    inv.total_lines,
    inv.total_items,
    inv.total_weight,
    inv.picking_complete_time,
    inv.packing_complete_time,
    inv.shipped_at,
    inv.delivered_at,
    inv.upload_date,
    inv.customer_code_365 AS customer_code,
    CASE WHEN inv.picking_complete_time IS NOT NULL AND inv.status_updated_at IS NOT NULL
         THEN EXTRACT(EPOCH FROM (inv.picking_complete_time - inv.status_updated_at)) / 60.0
         ELSE NULL END AS picking_duration_minutes
FROM invoices inv
WHERE inv.deleted_at IS NULL;

-- ===== FACT: Delivery Discrepancies =====
CREATE OR REPLACE VIEW pbi_fact_discrepancies AS
SELECT
    dd.id AS discrepancy_id,
    dd.invoice_no,
    dd.item_code_expected AS item_code,
    dd.item_name,
    dd.qty_expected,
    dd.qty_actual,
    dd.discrepancy_type,
    dd.status AS discrepancy_status,
    dd.reported_by,
    dd.reported_at,
    dd.reported_source,
    dd.delivery_date,
    dd.reported_value,
    dd.warehouse_result,
    dd.credit_note_required,
    dd.credit_note_amount,
    dd.resolution_action,
    dd.is_validated,
    dd.is_resolved
FROM delivery_discrepancies dd;

-- ===== MATERIALIZED VIEW: Sales Lines (for Pricing Analytics) =====
-- This MV replaces the regular view dw_sales_lines_v for pricing queries
-- Refresh after each data warehouse sync
DROP MATERIALIZED VIEW IF EXISTS dw_sales_lines_mv;
CREATE MATERIALIZED VIEW dw_sales_lines_mv AS
SELECT
  h.invoice_date_utc0 AS sale_date,
  h.customer_code_365,
  l.item_code_365,
  l.quantity AS qty,
  l.line_total_excl AS net_excl
FROM dw_invoice_header h
JOIN dw_invoice_line l ON l.invoice_no_365 = h.invoice_no_365;

CREATE INDEX IF NOT EXISTS idx_sales_mv_customer_date
  ON dw_sales_lines_mv (customer_code_365, sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_mv_item_date
  ON dw_sales_lines_mv (item_code_365, sale_date);
CREATE INDEX IF NOT EXISTS idx_sales_mv_customer_item_date
  ON dw_sales_lines_mv (customer_code_365, item_code_365, sale_date);
