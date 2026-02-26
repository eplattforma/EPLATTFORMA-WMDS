  upload_date CHARACTER VARYING(10),
  customer_code CHARACTER VARYING(50),
  picking_duration_minutes NUMERIC
);



-- ========== pbi_fact_route_deliveries ==========
CREATE TABLE pbi_fact_route_deliveries (
  delivery_id INTEGER,
  route_id INTEGER,
  route_name CHARACTER VARYING(100),
  driver_name CHARACTER VARYING(100),
  delivery_date DATE,
  stop_id INTEGER,
  stop_sequence NUMERIC,
  stop_name TEXT,
  stop_city TEXT,
  customer_code CHARACTER VARYING(50),
  invoice_no CHARACTER VARYING,
  delivery_status CHARACTER VARYING,
  expected_payment_method CHARACTER VARYING(20),
  expected_amount NUMERIC,
  discrepancy_value NUMERIC,
  weight_kg DOUBLE PRECISION,
  delivered_at TIMESTAMP WITHOUT TIME ZONE,
  failed_at TIMESTAMP WITHOUT TIME ZONE,
  failure_reason CHARACTER VARYING(100)
);



-- ========== pbi_fact_routes ==========
CREATE TABLE pbi_fact_routes (
  route_id INTEGER,
  route_name CHARACTER VARYING(100),
  driver_name CHARACTER VARYING(100),
  route_status CHARACTER VARYING(20),
  delivery_date DATE,
  reconciliation_status CHARACTER VARYING(20),
  is_archived BOOLEAN,
  created_at TIMESTAMP WITHOUT TIME ZONE,
  started_at TIMESTAMP WITHOUT TIME ZONE,
  completed_at TIMESTAMP WITHOUT TIME ZONE,
  cash_expected NUMERIC,
  cash_collected NUMERIC,
  cash_handed_in NUMERIC,
  cash_variance NUMERIC,
  returns_count INTEGER,
  duration_minutes NUMERIC,
  stop_count BIGINT,
  invoice_count BIGINT,
  delivered_count BIGINT,
  failed_count BIGINT
);



-- ========== pbi_fact_sales ==========
CREATE TABLE pbi_fact_sales (
  line_id INTEGER,
  invoice_no CHARACTER VARYING(64),
  invoice_type CHARACTER VARYING(64),
  invoice_date DATE,
  customer_code CHARACTER VARYING(64),
  store_code CHARACTER VARYING(64),
  salesperson_code CHARACTER VARYING(64),
  item_code CHARACTER VARYING(64),
  line_number INTEGER,
  quantity NUMERIC,
  price_excl NUMERIC,
  price_incl NUMERIC,
  discount_percent NUMERIC,
  vat_percent NUMERIC,
  line_total_excl NUMERIC,
  line_total_discount NUMERIC,
  line_total_vat NUMERIC,
  line_total_incl NUMERIC,
  line_net_value NUMERIC,
  year NUMERIC,
  month NUMERIC,
  quarter NUMERIC,
  year_month TEXT,
  day_of_week TEXT,
  day_of_week_no NUMERIC
);



-- ========== picking_exceptions ==========
CREATE TABLE picking_exceptions (
  id INTEGER NOT NULL DEFAULT nextval('picking_exceptions_id_seq'::regclass),
  invoice_no CHARACTER VARYING(50) NOT NULL,
  item_code CHARACTER VARYING(50) NOT NULL,
  expected_qty INTEGER NOT NULL,
  picked_qty INTEGER NOT NULL,
  picker_username CHARACTER VARYING(64) NOT NULL,
  timestamp TIMESTAMP WITHOUT TIME ZONE,
  reason CHARACTER VARYING(500)
);

CREATE UNIQUE INDEX picking_exceptions_pkey ON public.picking_exceptions USING btree (id);
CREATE INDEX idx_picking_exceptions_invoice ON public.picking_exceptions USING btree (invoice_no);
CREATE INDEX idx_picking_exceptions_invoice_no ON public.picking_exceptions USING btree (invoice_no);

ALTER TABLE picking_exceptions ADD CONSTRAINT picking_exceptions_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);
ALTER TABLE picking_exceptions ADD CONSTRAINT picking_exceptions_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES users(username);

-- ========== pod_records ==========
CREATE TABLE pod_records (
  id INTEGER NOT NULL DEFAULT nextval('pod_records_id_seq'::regclass),
  route_id INTEGER NOT NULL,
  route_stop_id INTEGER NOT NULL,
  invoice_nos JSON NOT NULL,
  has_physical_signed_invoice BOOLEAN,
  receiver_name CHARACTER VARYING(200),
  receiver_relationship CHARACTER VARYING(100),
  photo_paths JSON,
  gps_lat NUMERIC,
  gps_lng NUMERIC,
  collected_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  collected_by CHARACTER VARYING(64) NOT NULL,
  notes TEXT
);

CREATE UNIQUE INDEX pod_records_pkey ON public.pod_records USING btree (id);

ALTER TABLE pod_records ADD CONSTRAINT pod_records_collected_by_fkey FOREIGN KEY (collected_by) REFERENCES users(username);
ALTER TABLE pod_records ADD CONSTRAINT pod_records_route_id_fkey FOREIGN KEY (route_id) REFERENCES shipments(id);
ALTER TABLE pod_records ADD CONSTRAINT pod_records_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES route_stop(route_stop_id);

-- ========== postal_lookup_cache ==========
CREATE TABLE postal_lookup_cache (
  id INTEGER NOT NULL DEFAULT nextval('postal_lookup_cache_id_seq'::regclass),
  cache_key CHARACTER VARYING(256),
  request_json TEXT,
  response_json TEXT,
  created_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE UNIQUE INDEX postal_lookup_cache_pkey ON public.postal_lookup_cache USING btree (id);
CREATE UNIQUE INDEX ix_postal_lookup_cache_cache_key ON public.postal_lookup_cache USING btree (cache_key);


-- ========== ps365_reserved_stock_777 ==========
CREATE TABLE ps365_reserved_stock_777 (
  item_code_365 CHARACTER VARYING(64) NOT NULL,
  item_name CHARACTER VARYING(255) NOT NULL,
  season_name CHARACTER VARYING(128),
  number_of_pieces INTEGER,
  number_field_5_value INTEGER,
  store_code_365 CHARACTER VARYING(16) NOT NULL,
  stock NUMERIC NOT NULL,
  stock_reserved NUMERIC NOT NULL,
  stock_ordered NUMERIC NOT NULL,
  available_stock NUMERIC NOT NULL,
  synced_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  supplier_item_code CHARACTER VARYING(255),
  barcode CHARACTER VARYING(100)
);

CREATE UNIQUE INDEX ps365_reserved_stock_777_pkey ON public.ps365_reserved_stock_777 USING btree (item_code_365);
CREATE INDEX ix_ps365_reserved_stock_777_synced_at ON public.ps365_reserved_stock_777 USING btree (synced_at);


-- ========== ps_customers ==========
CREATE TABLE ps_customers (
  customer_code_365 CHARACTER VARYING(50) NOT NULL,
  customer_code_secondary TEXT,
  is_company BOOLEAN,
  company_name TEXT,
  store_code_365 TEXT,
  active BOOLEAN NOT NULL,
  tel_1 TEXT,
  mobile TEXT,
  sms TEXT,
  website TEXT,
  category_code_1_365 TEXT,
  category_1_name TEXT,
  category_code_2_365 TEXT,
  category_2_name TEXT,
  company_activity_code_365 TEXT,
  company_activity_name TEXT,
  credit_limit_amount DOUBLE PRECISION,
  vat_registration_number TEXT,
  address_line_1 TEXT,
  address_line_2 TEXT,
  address_line_3 TEXT,
  postal_code TEXT,
  town TEXT,
  contact_last_name TEXT,
  contact_first_name TEXT,
  agent_code_365 TEXT,
  agent_name TEXT,
  last_synced_at TIMESTAMP WITHOUT TIME ZONE,
  deleted_at TIMESTAMP WITHOUT TIME ZONE,
  deleted_by CHARACTER VARYING(64),
  delete_reason CHARACTER VARYING(255),
  is_active BOOLEAN NOT NULL DEFAULT true,
  disabled_at TIMESTAMP WITHOUT TIME ZONE,
  disabled_reason CHARACTER VARYING(255),
  latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION,
  reporting_group TEXT,
  delivery_days TEXT,
  delivery_days_status CHARACTER VARYING(20) DEFAULT 'EMPTY'::character varying,
  delivery_days_invalid_tokens TEXT,
  delivery_days_parsed_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX ps_customers_pkey ON public.ps_customers USING btree (customer_code_365);
CREATE INDEX idx_ps_cust_reporting_group ON public.ps_customers USING btree (reporting_group);
CREATE INDEX idx_ps_customers_deleted_at ON public.ps_customers USING btree (deleted_at);
CREATE INDEX idx_ps_customers_is_active ON public.ps_customers USING btree (is_active);


-- ========== ps_items_dw ==========
CREATE TABLE ps_items_dw (
  item_code_365 CHARACTER VARYING(64) NOT NULL,
  item_name CHARACTER VARYING(255) NOT NULL,
  active BOOLEAN NOT NULL,
  category_code_365 CHARACTER VARYING(64),
  brand_code_365 CHARACTER VARYING(64),
  season_code_365 CHARACTER VARYING(64),
  attribute_6_code_365 CHARACTER VARYING(64),
  attr_hash CHARACTER VARYING(32) NOT NULL,
  last_sync_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  attribute_1_code_365 CHARACTER VARYING(64),
  attribute_2_code_365 CHARACTER VARYING(64),
  attribute_3_code_365 CHARACTER VARYING(64),
  attribute_4_code_365 CHARACTER VARYING(64),
  attribute_5_code_365 CHARACTER VARYING(64),
  item_length NUMERIC,
  item_width NUMERIC,
  item_height NUMERIC,
  item_weight NUMERIC,
  number_of_pieces INTEGER,
  selling_qty NUMERIC,
  wms_zone CHARACTER VARYING(50),
  wms_unit_type CHARACTER VARYING(50),
  wms_fragility CHARACTER VARYING(20),
  wms_stackability CHARACTER VARYING(20),
  wms_temperature_sensitivity CHARACTER VARYING(30),
  wms_pressure_sensitivity CHARACTER VARYING(20),
  wms_shape_type CHARACTER VARYING(30),
  wms_spill_risk BOOLEAN,
  wms_pick_difficulty INTEGER,
  wms_shelf_height CHARACTER VARYING(20),
  wms_box_fit_rule CHARACTER VARYING(30),
  wms_class_confidence INTEGER,
  wms_class_source CHARACTER VARYING(30),
  wms_class_notes TEXT,
  wms_classified_at TIMESTAMP WITHOUT TIME ZONE,
  wms_class_evidence TEXT,
  barcode CHARACTER VARYING(100),
  supplier_item_code CHARACTER VARYING(255),
  min_order_qty INTEGER
);

CREATE UNIQUE INDEX ps_items_dw_pkey ON public.ps_items_dw USING btree (item_code_365);


-- ========== purchase_order_lines ==========
CREATE TABLE purchase_order_lines (
  id INTEGER NOT NULL DEFAULT nextval('purchase_order_lines_id_seq'::regclass),
  purchase_order_id INTEGER NOT NULL,
  line_number INTEGER NOT NULL,
  item_code_365 CHARACTER VARYING(100) NOT NULL,
  item_name CHARACTER VARYING(500),
  line_quantity NUMERIC,
  line_price_excl_vat NUMERIC,
  line_total_sub NUMERIC,
  line_total_discount NUMERIC,
  line_total_discount_percentage NUMERIC,
  line_vat_code_365 CHARACTER VARYING(50),
  line_total_vat NUMERIC,
  line_total_vat_percentage NUMERIC,
  line_total_grand NUMERIC,
  shelf_locations TEXT,
  item_has_expiration_date BOOLEAN NOT NULL DEFAULT false,
  item_has_lot_number BOOLEAN NOT NULL DEFAULT false,
  item_has_serial_number BOOLEAN NOT NULL DEFAULT false,
  line_id_365 CHARACTER VARYING(100),
  item_barcode CHARACTER VARYING(100),
  unit_type CHARACTER VARYING(50),
  pieces_per_unit INTEGER,
  supplier_item_code CHARACTER VARYING(255),
  stock_qty NUMERIC,
  stock_reserved_qty NUMERIC,
  stock_ordered_qty NUMERIC,
  available_qty NUMERIC,
  stock_synced_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX purchase_order_lines_pkey ON public.purchase_order_lines USING btree (id);
CREATE INDEX idx_purchase_order_lines_line_id_365 ON public.purchase_order_lines USING btree (line_id_365);
CREATE INDEX ix_purchase_order_lines_item_code_365 ON public.purchase_order_lines USING btree (item_code_365);

ALTER TABLE purchase_order_lines ADD CONSTRAINT purchase_order_lines_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id);

-- ========== purchase_orders ==========
CREATE TABLE purchase_orders (
  id INTEGER NOT NULL DEFAULT nextval('purchase_orders_id_seq'::regclass),
  code_365 CHARACTER VARYING(100),
  shopping_cart_code CHARACTER VARYING(100),
  supplier_code CHARACTER VARYING(100),
  status_code CHARACTER VARYING(50),
  status_name CHARACTER VARYING(100),
  order_date_local CHARACTER VARYING(50),
  order_date_utc0 CHARACTER VARYING(50),
  comments TEXT,
  total_sub NUMERIC,
  total_discount NUMERIC,
  total_vat NUMERIC,
  total_grand NUMERIC,
  downloaded_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  downloaded_by CHARACTER VARYING(64),
  supplier_name CHARACTER VARYING(200),
  deleted_at TIMESTAMP WITHOUT TIME ZONE,
  deleted_by CHARACTER VARYING(64),
  delete_reason CHARACTER VARYING(255),
  is_archived BOOLEAN NOT NULL DEFAULT false,
  archived_at TIMESTAMP WITHOUT TIME ZONE,
  archived_by CHARACTER VARYING(64),
  description TEXT
);

CREATE UNIQUE INDEX purchase_orders_pkey ON public.purchase_orders USING btree (id);
CREATE INDEX idx_purchase_orders_deleted_at ON public.purchase_orders USING btree (deleted_at);
CREATE INDEX idx_purchase_orders_is_archived ON public.purchase_orders USING btree (is_archived);
CREATE INDEX ix_purchase_orders_code_365 ON public.purchase_orders USING btree (code_365);
CREATE INDEX ix_purchase_orders_shopping_cart_code ON public.purchase_orders USING btree (shopping_cart_code);

ALTER TABLE purchase_orders ADD CONSTRAINT purchase_orders_archived_by_fkey FOREIGN KEY (archived_by) REFERENCES users(username);
ALTER TABLE purchase_orders ADD CONSTRAINT purchase_orders_downloaded_by_fkey FOREIGN KEY (downloaded_by) REFERENCES users(username);

-- ========== receipt_log ==========
CREATE TABLE receipt_log (
  id INTEGER NOT NULL DEFAULT nextval('receipt_log_id_seq'::regclass),
  reference_number CHARACTER VARYING(32) NOT NULL,
  customer_code_365 CHARACTER VARYING(32) NOT NULL,
  amount NUMERIC NOT NULL,
  comments CHARACTER VARYING(1000),
  response_id CHARACTER VARYING(128),
  success INTEGER,
  request_json TEXT,
  response_json TEXT,
  created_at TIMESTAMP WITHOUT TIME ZONE,
  invoice_no CHARACTER VARYING(500),
  driver_username CHARACTER VARYING(64),
  route_stop_id INTEGER
);

CREATE UNIQUE INDEX receipt_log_pkey ON public.receipt_log USING btree (id);
CREATE UNIQUE INDEX receipt_log_reference_number_key ON public.receipt_log USING btree (reference_number);
CREATE INDEX ix_receipt_log_customer_code_365 ON public.receipt_log USING btree (customer_code_365);
CREATE INDEX ix_receipt_log_reference_number ON public.receipt_log USING btree (reference_number);

ALTER TABLE receipt_log ADD CONSTRAINT receipt_log_driver_username_fkey FOREIGN KEY (driver_username) REFERENCES users(username);
ALTER TABLE receipt_log ADD CONSTRAINT receipt_log_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES route_stop(route_stop_id);

-- ========== receipt_sequence ==========
CREATE TABLE receipt_sequence (
  id INTEGER NOT NULL DEFAULT nextval('receipt_sequence_id_seq'::regclass),
  last_number INTEGER NOT NULL,
  updated_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE UNIQUE INDEX receipt_sequence_pkey ON public.receipt_sequence USING btree (id);


-- ========== receiving_lines ==========
CREATE TABLE receiving_lines (
  id INTEGER NOT NULL DEFAULT nextval('receiving_lines_id_seq'::regclass),
  session_id INTEGER NOT NULL,
  po_line_id INTEGER NOT NULL,
  barcode_scanned CHARACTER VARYING(200),
  item_code_365 CHARACTER VARYING(100) NOT NULL,
  qty_received NUMERIC NOT NULL,
  expiry_date DATE,
  lot_note TEXT,
  received_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

CREATE UNIQUE INDEX receiving_lines_pkey ON public.receiving_lines USING btree (id);

ALTER TABLE receiving_lines ADD CONSTRAINT receiving_lines_po_line_id_fkey FOREIGN KEY (po_line_id) REFERENCES purchase_order_lines(id);
ALTER TABLE receiving_lines ADD CONSTRAINT receiving_lines_session_id_fkey FOREIGN KEY (session_id) REFERENCES receiving_sessions(id);

-- ========== receiving_sessions ==========
CREATE TABLE receiving_sessions (
  id INTEGER NOT NULL DEFAULT nextval('receiving_sessions_id_seq'::regclass),
  purchase_order_id INTEGER NOT NULL,
  receipt_code CHARACTER VARYING(50) NOT NULL,
  operator CHARACTER VARYING(64),
  started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  finished_at TIMESTAMP WITHOUT TIME ZONE,
  comments TEXT
);

CREATE UNIQUE INDEX receiving_sessions_pkey ON public.receiving_sessions USING btree (id);
CREATE UNIQUE INDEX ix_receiving_sessions_receipt_code ON public.receiving_sessions USING btree (receipt_code);

ALTER TABLE receiving_sessions ADD CONSTRAINT receiving_sessions_operator_fkey FOREIGN KEY (operator) REFERENCES users(username);
ALTER TABLE receiving_sessions ADD CONSTRAINT receiving_sessions_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id);

-- ========== reroute_requests ==========
CREATE TABLE reroute_requests (
  id BIGINT NOT NULL DEFAULT nextval('reroute_requests_id_seq'::regclass),
  invoice_no CHARACTER VARYING(50) NOT NULL,
  requested_by CHARACTER VARYING(100),
  status CHARACTER VARYING(50) NOT NULL DEFAULT 'OPEN'::character varying,
  notes TEXT,
  assigned_route_id BIGINT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  completed_at TIMESTAMP WITH TIME ZONE
);

CREATE UNIQUE INDEX reroute_requests_pkey ON public.reroute_requests USING btree (id);
CREATE INDEX idx_rr_status ON public.reroute_requests USING btree (status);

ALTER TABLE reroute_requests ADD CONSTRAINT reroute_requests_assigned_route_id_fkey FOREIGN KEY (assigned_route_id) REFERENCES shipments(id);
ALTER TABLE reroute_requests ADD CONSTRAINT reroute_requests_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);

-- ========== route_delivery_events ==========
CREATE TABLE route_delivery_events (
  id INTEGER NOT NULL DEFAULT nextval('route_delivery_events_id_seq'::regclass),
  route_id INTEGER NOT NULL,
  route_stop_id INTEGER,
  event_type CHARACTER VARYING(50) NOT NULL,
  payload JSON,
  gps_lat NUMERIC,
  gps_lng NUMERIC,
  created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  actor_username CHARACTER VARYING(64) NOT NULL
);

CREATE UNIQUE INDEX route_delivery_events_pkey ON public.route_delivery_events USING btree (id);

ALTER TABLE route_delivery_events ADD CONSTRAINT route_delivery_events_actor_username_fkey FOREIGN KEY (actor_username) REFERENCES users(username);
ALTER TABLE route_delivery_events ADD CONSTRAINT route_delivery_events_route_id_fkey FOREIGN KEY (route_id) REFERENCES shipments(id);
ALTER TABLE route_delivery_events ADD CONSTRAINT route_delivery_events_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES route_stop(route_stop_id);

-- ========== route_return_handover ==========
CREATE TABLE route_return_handover (
  id INTEGER NOT NULL DEFAULT nextval('route_return_handover_id_seq'::regclass),
  route_id INTEGER NOT NULL,
  route_stop_id INTEGER,
  invoice_no CHARACTER VARYING(50) NOT NULL,
  driver_confirmed_at TIMESTAMP WITHOUT TIME ZONE,
  driver_username CHARACTER VARYING(64),
  warehouse_received_at TIMESTAMP WITHOUT TIME ZONE,
  received_by CHARACTER VARYING(64),
  packages_count INTEGER,
  notes TEXT,
  photo_paths JSONB,
  created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX route_return_handover_pkey ON public.route_return_handover USING btree (id);
CREATE INDEX ix_return_handover_driver_pending ON public.route_return_handover USING btree (route_id, driver_confirmed_at, warehouse_received_at);
CREATE UNIQUE INDEX ux_return_handover_route_invoice ON public.route_return_handover USING btree (route_id, invoice_no);

ALTER TABLE route_return_handover ADD CONSTRAINT route_return_handover_driver_username_fkey FOREIGN KEY (driver_username) REFERENCES users(username);
ALTER TABLE route_return_handover ADD CONSTRAINT route_return_handover_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);
ALTER TABLE route_return_handover ADD CONSTRAINT route_return_handover_received_by_fkey FOREIGN KEY (received_by) REFERENCES users(username);
ALTER TABLE route_return_handover ADD CONSTRAINT route_return_handover_route_id_fkey FOREIGN KEY (route_id) REFERENCES shipments(id);
ALTER TABLE route_return_handover ADD CONSTRAINT route_return_handover_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES route_stop(route_stop_id);

-- ========== route_stop ==========
CREATE TABLE route_stop (
  route_stop_id INTEGER NOT NULL DEFAULT nextval('route_stop_route_stop_id_seq'::regclass),
  shipment_id INTEGER NOT NULL,
  seq_no NUMERIC NOT NULL,
  stop_name TEXT,
  stop_addr TEXT,
  stop_city TEXT,
  stop_postcode TEXT,
  notes TEXT,
  window_start TIMESTAMP WITHOUT TIME ZONE,
  window_end TIMESTAMP WITHOUT TIME ZONE,
  customer_code CHARACTER VARYING(50),
  website CHARACTER VARYING(500),
  phone CHARACTER VARYING(50),
  delivered_at TIMESTAMP WITHOUT TIME ZONE,
  failed_at TIMESTAMP WITHOUT TIME ZONE,
  failure_reason CHARACTER VARYING(100),
  deleted_at TIMESTAMP WITHOUT TIME ZONE,
  deleted_by CHARACTER VARYING(64),
  delete_reason CHARACTER VARYING(255)
);

CREATE UNIQUE INDEX route_stop_pkey ON public.route_stop USING btree (route_stop_id);
CREATE UNIQUE INDEX route_stop_shipment_id_seq_no_key ON public.route_stop USING btree (shipment_id, seq_no);
CREATE INDEX idx_route_stop_deleted_at ON public.route_stop USING btree (deleted_at);
CREATE INDEX idx_route_stop_shipment ON public.route_stop USING btree (shipment_id);
CREATE INDEX idx_route_stop_shipment_seq ON public.route_stop USING btree (shipment_id, seq_no);

ALTER TABLE route_stop ADD CONSTRAINT route_stop_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES shipments(id);

-- ========== route_stop_invoice ==========
CREATE TABLE route_stop_invoice (
  route_stop_invoice_id INTEGER NOT NULL DEFAULT nextval('route_stop_invoice_route_stop_invoice_id_seq'::regclass),
  route_stop_id INTEGER NOT NULL,
  invoice_no CHARACTER VARYING NOT NULL,
  status CHARACTER VARYING,
  weight_kg DOUBLE PRECISION,
  notes TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true,
  effective_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  effective_to TIMESTAMP WITH TIME ZONE,
  changed_by CHARACTER VARYING(64),
  expected_payment_method CHARACTER VARYING(20),
  expected_amount NUMERIC,
  manifest_locked_at TIMESTAMP WITHOUT TIME ZONE,
  manifest_locked_by CHARACTER VARYING(64),
  discrepancy_value NUMERIC DEFAULT 0
);

CREATE UNIQUE INDEX route_stop_invoice_pkey ON public.route_stop_invoice USING btree (route_stop_invoice_id);
CREATE INDEX idx_rsi_invoice ON public.route_stop_invoice USING btree (invoice_no);
CREATE INDEX idx_rsi_status ON public.route_stop_invoice USING btree (status);
CREATE INDEX idx_rsi_stop ON public.route_stop_invoice USING btree (route_stop_id);
CREATE INDEX ix_rsi_active_status ON public.route_stop_invoice USING btree (status) WHERE (is_active = true);
CREATE INDEX ix_rsi_active_stop ON public.route_stop_invoice USING btree (route_stop_id) WHERE (is_active = true);
CREATE UNIQUE INDEX uq_rsi_active_invoice ON public.route_stop_invoice USING btree (invoice_no) WHERE (is_active = true);

ALTER TABLE route_stop_invoice ADD CONSTRAINT route_stop_invoice_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);
ALTER TABLE route_stop_invoice ADD CONSTRAINT route_stop_invoice_manifest_locked_by_fkey FOREIGN KEY (manifest_locked_by) REFERENCES users(username);
ALTER TABLE route_stop_invoice ADD CONSTRAINT route_stop_invoice_route_stop_id_fkey FOREIGN KEY (route_stop_id) REFERENCES route_stop(route_stop_id);

-- ========== season_supplier_settings ==========
CREATE TABLE season_supplier_settings (
  season_code_365 CHARACTER VARYING(50) NOT NULL,
  supplier_code CHARACTER VARYING(50),
  email_to CHARACTER VARYING(255),
  email_cc CHARACTER VARYING(500),
  email_comment TEXT,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

CREATE UNIQUE INDEX season_supplier_settings_pkey ON public.season_supplier_settings USING btree (season_code_365);


-- ========== settings ==========
CREATE TABLE settings (
  key CHARACTER VARYING(100) NOT NULL,
  value TEXT NOT NULL
);

CREATE UNIQUE INDEX settings_pkey ON public.settings USING btree (key);


-- ========== shifts ==========
CREATE TABLE shifts (
  id INTEGER NOT NULL DEFAULT nextval('shifts_id_seq'::regclass),
  picker_username CHARACTER VARYING(64) NOT NULL,
  check_in_time TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  check_out_time TIMESTAMP WITHOUT TIME ZONE,
  check_in_coordinates CHARACTER VARYING(100),
  check_out_coordinates CHARACTER VARYING(100),
  total_duration_minutes INTEGER,
  status CHARACTER VARYING(20),
  admin_adjusted BOOLEAN,
  adjustment_note TEXT,
  adjustment_by CHARACTER VARYING(64),
  adjustment_time TIMESTAMP WITHOUT TIME ZONE
);

CREATE UNIQUE INDEX shifts_pkey ON public.shifts USING btree (id);

ALTER TABLE shifts ADD CONSTRAINT shifts_adjustment_by_fkey FOREIGN KEY (adjustment_by) REFERENCES users(username);
ALTER TABLE shifts ADD CONSTRAINT shifts_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES users(username);

-- ========== shipment_orders ==========
CREATE TABLE shipment_orders (
  id INTEGER NOT NULL DEFAULT nextval('shipment_orders_id_seq'::regclass),
  shipment_id INTEGER NOT NULL,
  invoice_no CHARACTER VARYING(20) NOT NULL
);

CREATE UNIQUE INDEX shipment_orders_pkey ON public.shipment_orders USING btree (id);

ALTER TABLE shipment_orders ADD CONSTRAINT shipment_orders_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);
ALTER TABLE shipment_orders ADD CONSTRAINT shipment_orders_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES shipments(id);

-- ========== shipments ==========
CREATE TABLE shipments (
  id INTEGER NOT NULL DEFAULT nextval('shipments_id_seq'::regclass),
  driver_name CHARACTER VARYING(100) NOT NULL,
  route_name CHARACTER VARYING(100),
  status CHARACTER VARYING(20) NOT NULL,
  delivery_date DATE NOT NULL,
  created_at TIMESTAMP WITHOUT TIME ZONE,
  updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
  started_at TIMESTAMP WITHOUT TIME ZONE,
  completed_at TIMESTAMP WITHOUT TIME ZONE,
  settlement_status CHARACTER VARYING(20) DEFAULT 'PENDING'::character varying,
  driver_submitted_at TIMESTAMP WITHOUT TIME ZONE,
  cash_expected NUMERIC,
  cash_handed_in NUMERIC,
  cash_variance NUMERIC,
  cash_variance_note TEXT,
  returns_count INTEGER DEFAULT 0,
  returns_weight DOUBLE PRECISION,
  settlement_notes TEXT,
  completion_reason CHARACTER VARYING(50),
  deleted_at TIMESTAMP WITHOUT TIME ZONE,
  deleted_by CHARACTER VARYING(64),
  delete_reason CHARACTER VARYING(255),
  reconciliation_status CHARACTER VARYING(20) DEFAULT 'NOT_READY'::character varying,
  reconciled_at TIMESTAMP WITHOUT TIME ZONE,
  reconciled_by CHARACTER VARYING(64),
  is_archived BOOLEAN NOT NULL DEFAULT false,
  archived_at TIMESTAMP WITHOUT TIME ZONE,
  archived_by CHARACTER VARYING(64),
  cash_collected NUMERIC,
  settlement_cleared_at TIMESTAMP WITHOUT TIME ZONE,
  settlement_cleared_by CHARACTER VARYING(64)
);

CREATE UNIQUE INDEX shipments_pkey ON public.shipments USING btree (id);
CREATE INDEX idx_shipments_deleted_at ON public.shipments USING btree (deleted_at);
CREATE INDEX idx_shipments_driver_status ON public.shipments USING btree (driver_name, status, updated_at DESC);


-- ========== shipping_events ==========
CREATE TABLE shipping_events (
  id INTEGER NOT NULL DEFAULT nextval('shipping_events_id_seq'::regclass),
  invoice_no CHARACTER VARYING(50) NOT NULL,
  action CHARACTER VARYING(20) NOT NULL,
  actor CHARACTER VARYING(64) NOT NULL,
  timestamp TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  note TEXT
);

CREATE UNIQUE INDEX shipping_events_pkey ON public.shipping_events USING btree (id);

ALTER TABLE shipping_events ADD CONSTRAINT shipping_events_actor_fkey FOREIGN KEY (actor) REFERENCES users(username);
ALTER TABLE shipping_events ADD CONSTRAINT shipping_events_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);

-- ========== stock_positions ==========
CREATE TABLE stock_positions (
  id INTEGER NOT NULL DEFAULT nextval('stock_positions_id_seq'::regclass),
  item_code CHARACTER VARYING(100) NOT NULL,
  item_description CHARACTER VARYING(500),
  store_code CHARACTER VARYING(50) NOT NULL,
  store_name CHARACTER VARYING(200) NOT NULL,
  expiry_date CHARACTER VARYING(20),
  stock_quantity NUMERIC NOT NULL,
  imported_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

CREATE UNIQUE INDEX stock_positions_pkey ON public.stock_positions USING btree (id);
CREATE INDEX ix_stock_positions_imported_at ON public.stock_positions USING btree (imported_at);
CREATE INDEX ix_stock_positions_item_code ON public.stock_positions USING btree (item_code);
CREATE INDEX ix_stock_positions_store_code ON public.stock_positions USING btree (store_code);
CREATE INDEX ix_stock_positions_store_name ON public.stock_positions USING btree (store_name);


-- ========== stock_resolutions ==========
CREATE TABLE stock_resolutions (
  id INTEGER NOT NULL DEFAULT nextval('stock_resolutions_id_seq'::regclass),
  discrepancy_type CHARACTER VARYING(50) NOT NULL,
  resolution_name CHARACTER VARYING(100) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX stock_resolutions_pkey ON public.stock_resolutions USING btree (id);


-- ========== sync_jobs ==========
CREATE TABLE sync_jobs (
  id CHARACTER VARYING(50) NOT NULL,
  job_type CHARACTER VARYING(50) NOT NULL,
  params TEXT,
  status CHARACTER VARYING(20),
  started_at TIMESTAMP WITHOUT TIME ZONE,
  finished_at TIMESTAMP WITHOUT TIME ZONE,
  created_by CHARACTER VARYING(64),
  success BOOLEAN,
  invoices_created INTEGER,
  invoices_updated INTEGER,
  items_created INTEGER,
  items_updated INTEGER,
  error_count INTEGER,
  error_message TEXT,
  progress_current INTEGER,
  progress_total INTEGER,
  progress_message CHARACTER VARYING(255)
);

CREATE UNIQUE INDEX sync_jobs_pkey ON public.sync_jobs USING btree (id);


-- ========== sync_state ==========
CREATE TABLE sync_state (
  key CHARACTER VARYING(64) NOT NULL,
  value TEXT NOT NULL
);

CREATE UNIQUE INDEX sync_state_pkey ON public.sync_state USING btree (key);


-- ========== time_tracking_alerts ==========
CREATE TABLE time_tracking_alerts (
  id INTEGER NOT NULL DEFAULT nextval('time_tracking_alerts_id_seq'::regclass),
  invoice_no CHARACTER VARYING(50) NOT NULL,
  picker_username CHARACTER VARYING(64) NOT NULL,
  alert_type CHARACTER VARYING(50) NOT NULL,
  expected_duration DOUBLE PRECISION NOT NULL,
  actual_duration DOUBLE PRECISION NOT NULL,
  threshold_percentage DOUBLE PRECISION NOT NULL,
  created_at TIMESTAMP WITHOUT TIME ZONE,
  is_resolved BOOLEAN,
  resolved_at TIMESTAMP WITHOUT TIME ZONE,
  resolved_by CHARACTER VARYING(64),
  notes TEXT
);

CREATE UNIQUE INDEX time_tracking_alerts_pkey ON public.time_tracking_alerts USING btree (id);

ALTER TABLE time_tracking_alerts ADD CONSTRAINT time_tracking_alerts_invoice_no_fkey FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no);
ALTER TABLE time_tracking_alerts ADD CONSTRAINT time_tracking_alerts_picker_username_fkey FOREIGN KEY (picker_username) REFERENCES users(username);
ALTER TABLE time_tracking_alerts ADD CONSTRAINT time_tracking_alerts_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES users(username);

-- ========== users ==========
CREATE TABLE users (
  username CHARACTER VARYING(64) NOT NULL,
  password CHARACTER VARYING(256) NOT NULL,
  role CHARACTER VARYING(20) NOT NULL,
  payment_type_code_365 CHARACTER VARYING(50),
  require_gps_check BOOLEAN DEFAULT true,
  disabled_at TIMESTAMP WITHOUT TIME ZONE,
  disabled_reason CHARACTER VARYING(255),
  is_active BOOLEAN NOT NULL DEFAULT true
);

CREATE UNIQUE INDEX users_pkey ON public.users USING btree (username);
CREATE INDEX idx_users_is_active ON public.users USING btree (is_active);


-- ========== v_route_stop_invoice_active ==========
CREATE TABLE v_route_stop_invoice_active (
  route_stop_invoice_id INTEGER,
  route_stop_id INTEGER,
  invoice_no CHARACTER VARYING,
  status CHARACTER VARYING,
  weight_kg DOUBLE PRECISION,
  notes TEXT,
  is_active BOOLEAN,
  effective_from TIMESTAMP WITH TIME ZONE,
  effective_to TIMESTAMP WITH TIME ZONE,
  changed_by CHARACTER VARYING(64)
);



-- ========== v_shipment_orders ==========
CREATE TABLE v_shipment_orders (
  shipment_id INTEGER,
  invoice_no CHARACTER VARYING
);



-- ========== wms_category_defaults ==========
CREATE TABLE wms_category_defaults (
  category_code_365 CHARACTER VARYING(64) NOT NULL,
  default_zone CHARACTER VARYING(50),
  default_fragility CHARACTER VARYING(20),
  default_stackability CHARACTER VARYING(20),
  default_temperature_sensitivity CHARACTER VARYING(30),
  default_pressure_sensitivity CHARACTER VARYING(20),
  default_shape_type CHARACTER VARYING(30),
  default_spill_risk BOOLEAN,
  default_pick_difficulty INTEGER,
  default_shelf_height CHARACTER VARYING(20),
  default_box_fit_rule CHARACTER VARYING(30),
  is_active BOOLEAN NOT NULL,
  notes TEXT,
  updated_by CHARACTER VARYING(100),
  updated_at TIMESTAMP WITHOUT TIME ZONE,
  default_pack_mode CHARACTER VARYING(30)
);

CREATE UNIQUE INDEX wms_category_defaults_pkey ON public.wms_category_defaults USING btree (category_code_365);


-- ========== wms_classification_runs ==========
CREATE TABLE wms_classification_runs (
  id INTEGER NOT NULL DEFAULT nextval('wms_classification_runs_id_seq'::regclass),
  started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  finished_at TIMESTAMP WITHOUT TIME ZONE,
  run_by CHARACTER VARYING(100),
  mode CHARACTER VARYING(30),
  active_items_scanned INTEGER,
  items_updated INTEGER,
  items_needing_review INTEGER,
  notes TEXT
);

CREATE UNIQUE INDEX wms_classification_runs_pkey ON public.wms_classification_runs USING btree (id);


-- ========== wms_dynamic_rules ==========
CREATE TABLE wms_dynamic_rules (
  id INTEGER NOT NULL DEFAULT nextval('wms_dynamic_rules_id_seq'::regclass),
  name CHARACTER VARYING(120) NOT NULL,
  target_attr CHARACTER VARYING(64) NOT NULL,
  action_value CHARACTER VARYING(100) NOT NULL,
  confidence INTEGER NOT NULL,
  priority INTEGER NOT NULL,
  stop_processing BOOLEAN NOT NULL,
  is_active BOOLEAN NOT NULL,
  condition_json TEXT NOT NULL,
  notes TEXT,
  updated_by CHARACTER VARYING(100),
  updated_at TIMESTAMP WITHOUT TIME ZONE,
  actions_json TEXT
);

CREATE UNIQUE INDEX wms_dynamic_rules_pkey ON public.wms_dynamic_rules USING btree (id);
CREATE INDEX idx_wms_dynamic_rules_active_target ON public.wms_dynamic_rules USING btree (is_active, target_attr, priority);


-- ========== wms_item_overrides ==========
CREATE TABLE wms_item_overrides (
  item_code_365 CHARACTER VARYING(64) NOT NULL,
  zone_override CHARACTER VARYING(50),
  unit_type_override CHARACTER VARYING(50),
  fragility_override CHARACTER VARYING(20),
  stackability_override CHARACTER VARYING(20),
  temperature_sensitivity_override CHARACTER VARYING(30),
  pressure_sensitivity_override CHARACTER VARYING(20),
  shape_type_override CHARACTER VARYING(30),
  spill_risk_override BOOLEAN,
  pick_difficulty_override INTEGER,
  shelf_height_override CHARACTER VARYING(20),
  box_fit_rule_override CHARACTER VARYING(30),
  override_reason TEXT,
  is_active BOOLEAN NOT NULL,
  updated_by CHARACTER VARYING(100),
  updated_at TIMESTAMP WITHOUT TIME ZONE,
  pack_mode_override CHARACTER VARYING(30)
);

CREATE UNIQUE INDEX wms_item_overrides_pkey ON public.wms_item_overrides USING btree (item_code_365);


-- ========== wms_packing_profile ==========
CREATE TABLE wms_packing_profile (
  item_code_365 CHARACTER VARYING(50) NOT NULL,
  pallet_role CHARACTER VARYING(20) NOT NULL,
  flags_json TEXT,
  unit_type CHARACTER VARYING(20),
  fragility CHARACTER VARYING(10),
  pressure_sensitivity CHARACTER VARYING(10),
  stackability CHARACTER VARYING(10),
  temperature_sensitivity CHARACTER VARYING(20),
  spill_risk BOOLEAN,
  box_fit_rule CHARACTER VARYING(20),
  updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  pack_mode CHARACTER VARYING(20),
  loss_risk BOOLEAN,
  carton_type_hint CHARACTER VARYING(10),
  max_carton_weight_kg NUMERIC
);

CREATE UNIQUE INDEX wms_packing_profile_pkey ON public.wms_packing_profile USING btree (item_code_365);


-- ========== wms_pallet ==========
CREATE TABLE wms_pallet (
  pallet_id INTEGER NOT NULL DEFAULT nextval('wms_pallet_pallet_id_seq'::regclass),
  shipment_id INTEGER NOT NULL,
  label CHARACTER VARYING(50) NOT NULL,
  lane_code CHARACTER VARYING(10),
  lane_slot INTEGER,
  status CHARACTER VARYING(20) NOT NULL,
  max_weight_kg NUMERIC NOT NULL,
  max_height_m NUMERIC NOT NULL,
  used_mask INTEGER NOT NULL,
  used_weight_kg NUMERIC NOT NULL,
  created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  deleted_at TIMESTAMP WITHOUT TIME ZONE,
  deleted_by CHARACTER VARYING(64),
  delete_reason CHARACTER VARYING(255)
);

CREATE UNIQUE INDEX wms_pallet_pkey ON public.wms_pallet USING btree (pallet_id);

ALTER TABLE wms_pallet ADD CONSTRAINT wms_pallet_shipment_id_fkey FOREIGN KEY (shipment_id) REFERENCES shipments(id);

-- ========== wms_pallet_order ==========
CREATE TABLE wms_pallet_order (
  id INTEGER NOT NULL DEFAULT nextval('wms_pallet_order_id_seq'::regclass),
  pallet_id INTEGER NOT NULL,
  invoice_no CHARACTER VARYING(50) NOT NULL,
  blocks_requested INTEGER NOT NULL,
  blocks_mask INTEGER NOT NULL,
  est_weight_kg NUMERIC,
  stop_seq_no NUMERIC,
  created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

CREATE UNIQUE INDEX uq_pallet_order_invoice_no ON public.wms_pallet_order USING btree (invoice_no);
CREATE UNIQUE INDEX wms_pallet_order_pkey ON public.wms_pallet_order USING btree (id);
CREATE INDEX ix_pallet_order_pallet_id ON public.wms_pallet_order USING btree (pallet_id);

ALTER TABLE wms_pallet_order ADD CONSTRAINT wms_pallet_order_pallet_id_fkey FOREIGN KEY (pallet_id) REFERENCES wms_pallet(pallet_id);

