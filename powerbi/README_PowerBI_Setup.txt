POWER BI CONNECTION SETUP
=========================

HOW TO CONNECT:

1. Open Power BI Desktop
2. Click "Get Data" > "PostgreSQL database"
3. Enter your production database server and database name
   (check your Replit Secrets or ask your admin for connection details)
   - Data Connectivity mode: Import (recommended)
4. When prompted for credentials:
   - Select "Database" authentication
   - Enter your database username and password
5. In Navigator, select ONLY the views starting with "pbi_"
   (do not load base tables - they contain raw operational data)

ALTERNATIVE: Edit WMS_Report.pbids to fill in your server/database,
then double-click to auto-open Power BI with the connection pre-configured.


AVAILABLE VIEWS (Tables in Power BI):

  DIMENSIONS (link to facts via keys):
  ------------------------------------
  pbi_dim_customers    - Customer master data (link: customer_code)
  pbi_dim_products     - Product/item master data (link: item_code)
  pbi_dim_stores       - Store lookup (link: store_code)
  pbi_dim_dates        - Date calendar 2023-2027 (link: date_key)
                         Fields: year, quarter, month_no, month_name, week_no,
                         day_of_week_no, day_name, year_month, year_quarter, is_weekday

  FACTS (main data tables):
  -------------------------
  pbi_fact_sales           - Sales at line level (86K+ rows)
                             Keys: customer_code, item_code, store_code, invoice_date
                             Measures: quantity, line_total_excl, line_total_incl, discount_percent
                             Time: invoice_date, year, month, quarter, year_month, day_of_week

  pbi_fact_invoices        - Sales at invoice level (aggregated)
                             Keys: customer_code, store_code, invoice_date
                             Measures: total_excl_vat, total_incl_vat, total_discount, total_qty, line_count

  pbi_fact_routes          - Delivery route summary
                             Keys: delivery_date
                             Measures: stop_count, invoice_count, delivered_count, failed_count,
                             cash_expected, cash_collected, cash_variance, duration_minutes

  pbi_fact_route_deliveries - Per-invoice delivery detail
                             Keys: customer_code, invoice_no, delivery_date
                             Measures: expected_amount, discrepancy_value, weight_kg

  pbi_fact_picking         - Order picking performance
                             Keys: customer_code
                             Measures: total_lines, total_items, total_weight, picking_duration_minutes

  pbi_fact_discrepancies   - Delivery issues/discrepancies
                             Keys: item_code, invoice_no, delivery_date
                             Measures: qty_expected, qty_actual, reported_value, credit_note_amount


SUGGESTED RELATIONSHIPS IN POWER BI:

  Dates:
  pbi_fact_sales[invoice_date]             -> pbi_dim_dates[date_key]
  pbi_fact_invoices[invoice_date]          -> pbi_dim_dates[date_key]
  pbi_fact_routes[delivery_date]           -> pbi_dim_dates[date_key]
  pbi_fact_route_deliveries[delivery_date] -> pbi_dim_dates[date_key]

  Customers:
  pbi_fact_sales[customer_code]            -> pbi_dim_customers[customer_code]
  pbi_fact_invoices[customer_code]         -> pbi_dim_customers[customer_code]
  pbi_fact_route_deliveries[customer_code] -> pbi_dim_customers[customer_code]
  pbi_fact_picking[customer_code]          -> pbi_dim_customers[customer_code]

  Products:
  pbi_fact_sales[item_code]                -> pbi_dim_products[item_code]
  pbi_fact_discrepancies[item_code]        -> pbi_dim_products[item_code]

  Stores:
  pbi_fact_sales[store_code]               -> pbi_dim_stores[store_code]


SUGGESTED REPORT PAGES:

  1. Sales Overview      - Total sales, trends by month, top customers, top products
  2. Customer Analysis   - Sales by customer category, agent, town, credit utilization
  3. Product Performance - Sales by category, brand, zone, top/bottom items
  4. Delivery Operations - Routes per day, delivery success rate, duration, cash variance
  5. Quality & Issues    - Discrepancy types, resolution rates, credit notes issued
  6. Picking Efficiency  - Pick times by picker, lines/items per order, weight distribution
