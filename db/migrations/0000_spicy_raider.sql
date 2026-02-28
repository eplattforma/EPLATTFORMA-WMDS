-- Current sql file was generated after introspecting the database
-- If you want to run this migration please uncomment this code before executing migrations
/*
CREATE TABLE "ai_feedback_cache" (
	"id" serial PRIMARY KEY NOT NULL,
	"payload_hash" varchar(64) NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"response_json" jsonb NOT NULL,
	CONSTRAINT "ai_feedback_cache_payload_hash_key" UNIQUE("payload_hash")
);
--> statement-breakpoint
CREATE TABLE "discrepancy_types" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" varchar(50) NOT NULL,
	"display_name" varchar(100) NOT NULL,
	"is_active" boolean DEFAULT true NOT NULL,
	"sort_order" integer DEFAULT 0 NOT NULL,
	"deducts_from_collection" boolean DEFAULT true NOT NULL,
	"cn_required" boolean DEFAULT true NOT NULL,
	"return_expected" boolean DEFAULT false NOT NULL,
	"requires_actual_item" boolean DEFAULT false NOT NULL,
	CONSTRAINT "discrepancy_types_name_key" UNIQUE("name")
);
--> statement-breakpoint
CREATE TABLE "dw_attribute1" (
	"attribute_1_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"attribute_1_name" varchar(255) NOT NULL,
	"attribute_1_secondary_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_attribute2" (
	"attribute_2_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"attribute_2_name" varchar(255) NOT NULL,
	"attribute_2_secondary_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_attribute3" (
	"attribute_3_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"attribute_3_name" varchar(255) NOT NULL,
	"attribute_3_secondary_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_attribute4" (
	"attribute_4_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"attribute_4_name" varchar(255) NOT NULL,
	"attribute_4_secondary_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_attribute5" (
	"attribute_5_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"attribute_5_name" varchar(255) NOT NULL,
	"attribute_5_secondary_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_attribute6" (
	"attribute_6_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"attribute_6_name" varchar(255) NOT NULL,
	"attribute_6_secondary_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_brands" (
	"brand_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"brand_name" varchar(255) NOT NULL,
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_cashier" (
	"user_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"user_name" varchar(255),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_category_penetration" (
	"id" serial PRIMARY KEY NOT NULL,
	"customer_code_365" varchar NOT NULL,
	"category_code" varchar NOT NULL,
	"total_spend" numeric(12, 2) NOT NULL,
	"has_category" integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_churn_risk" (
	"id" serial PRIMARY KEY NOT NULL,
	"customer_code_365" varchar NOT NULL,
	"category_code" varchar NOT NULL,
	"recent_spend" numeric(14, 2) NOT NULL,
	"prev_spend" numeric(14, 2) NOT NULL,
	"spend_ratio" double precision NOT NULL,
	"drop_pct" double precision NOT NULL,
	"churn_flag" integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_item_categories" (
	"category_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"category_name" varchar(255) NOT NULL,
	"parent_category_code" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_reco_basket" (
	"id" serial PRIMARY KEY NOT NULL,
	"from_item_code" varchar NOT NULL,
	"to_item_code" varchar NOT NULL,
	"support" double precision NOT NULL,
	"confidence" double precision NOT NULL,
	"lift" double precision
);
--> statement-breakpoint
CREATE TABLE "dw_seasons" (
	"season_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"season_name" varchar(255) NOT NULL,
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_share_of_wallet" (
	"id" serial PRIMARY KEY NOT NULL,
	"customer_code_365" varchar NOT NULL,
	"actual_spend" numeric(14, 2) NOT NULL,
	"avg_spend" numeric(14, 2) NOT NULL,
	"opportunity_gap" numeric(14, 2) NOT NULL,
	CONSTRAINT "dw_share_of_wallet_customer_code_365_key" UNIQUE("customer_code_365")
);
--> statement-breakpoint
CREATE TABLE "dw_store" (
	"store_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"store_name" varchar(255),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "ps_items_dw" (
	"item_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"item_name" varchar(255) NOT NULL,
	"active" boolean NOT NULL,
	"category_code_365" varchar(64),
	"brand_code_365" varchar(64),
	"season_code_365" varchar(64),
	"attribute_6_code_365" varchar(64),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL,
	"attribute_1_code_365" varchar(64),
	"attribute_2_code_365" varchar(64),
	"attribute_3_code_365" varchar(64),
	"attribute_4_code_365" varchar(64),
	"attribute_5_code_365" varchar(64),
	"item_length" numeric(10, 3),
	"item_width" numeric(10, 3),
	"item_height" numeric(10, 3),
	"item_weight" numeric(10, 3),
	"number_of_pieces" integer,
	"selling_qty" numeric(10, 3),
	"wms_zone" varchar(50),
	"wms_unit_type" varchar(50),
	"wms_fragility" varchar(20),
	"wms_stackability" varchar(20),
	"wms_temperature_sensitivity" varchar(30),
	"wms_pressure_sensitivity" varchar(20),
	"wms_shape_type" varchar(30),
	"wms_spill_risk" boolean,
	"wms_pick_difficulty" integer,
	"wms_shelf_height" varchar(20),
	"wms_box_fit_rule" varchar(30),
	"wms_class_confidence" integer,
	"wms_class_source" varchar(30),
	"wms_class_notes" text,
	"wms_classified_at" timestamp,
	"wms_class_evidence" text,
	"barcode" varchar(100),
	"supplier_item_code" varchar(255),
	"min_order_qty" integer
);
--> statement-breakpoint
CREATE TABLE "postal_lookup_cache" (
	"id" serial PRIMARY KEY NOT NULL,
	"cache_key" varchar(256),
	"request_json" text,
	"response_json" text,
	"created_at" timestamp
);
--> statement-breakpoint
CREATE TABLE "ps365_reserved_stock_777" (
	"item_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"item_name" varchar(255) NOT NULL,
	"season_name" varchar(128),
	"number_of_pieces" integer,
	"number_field_5_value" integer,
	"store_code_365" varchar(16) NOT NULL,
	"stock" numeric(18, 4) NOT NULL,
	"stock_reserved" numeric(18, 4) NOT NULL,
	"stock_ordered" numeric(18, 4) NOT NULL,
	"available_stock" numeric(18, 4) NOT NULL,
	"synced_at" timestamp NOT NULL,
	"supplier_item_code" varchar(255),
	"barcode" varchar(100)
);
--> statement-breakpoint
CREATE TABLE "receipt_sequence" (
	"id" serial PRIMARY KEY NOT NULL,
	"last_number" integer NOT NULL,
	"updated_at" timestamp
);
--> statement-breakpoint
CREATE TABLE "season_supplier_settings" (
	"season_code_365" varchar(50) PRIMARY KEY NOT NULL,
	"supplier_code" varchar(50),
	"email_to" varchar(255),
	"email_cc" varchar(500),
	"email_comment" text,
	"updated_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "settings" (
	"key" varchar(100) PRIMARY KEY NOT NULL,
	"value" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "stock_positions" (
	"id" serial PRIMARY KEY NOT NULL,
	"item_code" varchar(100) NOT NULL,
	"item_description" varchar(500),
	"store_code" varchar(50) NOT NULL,
	"store_name" varchar(200) NOT NULL,
	"expiry_date" varchar(20),
	"stock_quantity" numeric(12, 4) NOT NULL,
	"imported_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "stock_resolutions" (
	"id" serial PRIMARY KEY NOT NULL,
	"discrepancy_type" varchar(50) NOT NULL,
	"resolution_name" varchar(100) NOT NULL,
	"is_active" boolean DEFAULT true NOT NULL,
	"sort_order" integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE "sync_jobs" (
	"id" varchar(50) PRIMARY KEY NOT NULL,
	"job_type" varchar(50) NOT NULL,
	"params" text,
	"status" varchar(20),
	"started_at" timestamp,
	"finished_at" timestamp,
	"created_by" varchar(64),
	"success" boolean,
	"invoices_created" integer,
	"invoices_updated" integer,
	"items_created" integer,
	"items_updated" integer,
	"error_count" integer,
	"error_message" text,
	"progress_current" integer,
	"progress_total" integer,
	"progress_message" varchar(255)
);
--> statement-breakpoint
CREATE TABLE "sync_state" (
	"key" varchar(64) PRIMARY KEY NOT NULL,
	"value" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "wms_category_defaults" (
	"category_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"default_zone" varchar(50),
	"default_fragility" varchar(20),
	"default_stackability" varchar(20),
	"default_temperature_sensitivity" varchar(30),
	"default_pressure_sensitivity" varchar(20),
	"default_shape_type" varchar(30),
	"default_spill_risk" boolean,
	"default_pick_difficulty" integer,
	"default_shelf_height" varchar(20),
	"default_box_fit_rule" varchar(30),
	"is_active" boolean NOT NULL,
	"notes" text,
	"updated_by" varchar(100),
	"updated_at" timestamp,
	"default_pack_mode" varchar(30)
);
--> statement-breakpoint
CREATE TABLE "wms_classification_runs" (
	"id" serial PRIMARY KEY NOT NULL,
	"started_at" timestamp NOT NULL,
	"finished_at" timestamp,
	"run_by" varchar(100),
	"mode" varchar(30),
	"active_items_scanned" integer,
	"items_updated" integer,
	"items_needing_review" integer,
	"notes" text
);
--> statement-breakpoint
CREATE TABLE "wms_dynamic_rules" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" varchar(120) NOT NULL,
	"target_attr" varchar(64) NOT NULL,
	"action_value" varchar(100) NOT NULL,
	"confidence" integer NOT NULL,
	"priority" integer NOT NULL,
	"stop_processing" boolean NOT NULL,
	"is_active" boolean NOT NULL,
	"condition_json" text NOT NULL,
	"notes" text,
	"updated_by" varchar(100),
	"updated_at" timestamp,
	"actions_json" text
);
--> statement-breakpoint
CREATE TABLE "wms_item_overrides" (
	"item_code_365" varchar(64) PRIMARY KEY NOT NULL,
	"zone_override" varchar(50),
	"unit_type_override" varchar(50),
	"fragility_override" varchar(20),
	"stackability_override" varchar(20),
	"temperature_sensitivity_override" varchar(30),
	"pressure_sensitivity_override" varchar(20),
	"shape_type_override" varchar(30),
	"spill_risk_override" boolean,
	"pick_difficulty_override" integer,
	"shelf_height_override" varchar(20),
	"box_fit_rule_override" varchar(30),
	"override_reason" text,
	"is_active" boolean NOT NULL,
	"updated_by" varchar(100),
	"updated_at" timestamp,
	"pack_mode_override" varchar(30)
);
--> statement-breakpoint
CREATE TABLE "wms_packing_profile" (
	"item_code_365" varchar(50) PRIMARY KEY NOT NULL,
	"pallet_role" varchar(20) NOT NULL,
	"flags_json" text,
	"unit_type" varchar(20),
	"fragility" varchar(10),
	"pressure_sensitivity" varchar(10),
	"stackability" varchar(10),
	"temperature_sensitivity" varchar(20),
	"spill_risk" boolean,
	"box_fit_rule" varchar(20),
	"updated_at" timestamp NOT NULL,
	"pack_mode" varchar(20),
	"loss_risk" boolean,
	"carton_type_hint" varchar(10),
	"max_carton_weight_kg" numeric(10, 2)
);
--> statement-breakpoint
CREATE TABLE "route_stop" (
	"route_stop_id" serial PRIMARY KEY NOT NULL,
	"shipment_id" integer NOT NULL,
	"seq_no" numeric(10, 2) NOT NULL,
	"stop_name" text,
	"stop_addr" text,
	"stop_city" text,
	"stop_postcode" text,
	"notes" text,
	"window_start" timestamp,
	"window_end" timestamp,
	"customer_code" varchar(50),
	"website" varchar(500),
	"phone" varchar(50),
	"delivered_at" timestamp,
	"failed_at" timestamp,
	"failure_reason" varchar(100),
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255),
	CONSTRAINT "route_stop_shipment_id_seq_no_key" UNIQUE("shipment_id","seq_no"),
	CONSTRAINT "chk_route_stop_completion" CHECK (NOT ((delivered_at IS NOT NULL) AND (failed_at IS NOT NULL)))
);
--> statement-breakpoint
CREATE TABLE "route_stop_invoice" (
	"route_stop_invoice_id" serial PRIMARY KEY NOT NULL,
	"route_stop_id" integer NOT NULL,
	"invoice_no" varchar NOT NULL,
	"status" varchar,
	"weight_kg" double precision,
	"notes" text,
	"is_active" boolean DEFAULT true NOT NULL,
	"effective_from" timestamp with time zone DEFAULT now() NOT NULL,
	"effective_to" timestamp with time zone,
	"changed_by" varchar(64),
	"expected_payment_method" varchar(20),
	"expected_amount" numeric(12, 2),
	"manifest_locked_at" timestamp,
	"manifest_locked_by" varchar(64),
	"discrepancy_value" numeric(10, 2) DEFAULT '0'
);
--> statement-breakpoint
CREATE TABLE "invoices" (
	"invoice_no" varchar(50) PRIMARY KEY NOT NULL,
	"routing" varchar(100),
	"customer_name" varchar(200),
	"upload_date" varchar(10) NOT NULL,
	"assigned_to" varchar(64),
	"total_lines" integer,
	"total_items" integer,
	"total_weight" double precision,
	"total_exp_time" double precision,
	"status" varchar(30) DEFAULT 'not_started',
	"current_item_index" integer,
	"packing_complete_time" timestamp,
	"picking_complete_time" timestamp,
	"status_updated_at" timestamp DEFAULT CURRENT_TIMESTAMP,
	"shipped_at" timestamp,
	"shipped_by" varchar(64),
	"delivered_at" timestamp,
	"undelivered_reason" text,
	"customer_code" varchar(50),
	"route_id" integer,
	"stop_id" integer,
	"total_grand" numeric(12, 2),
	"total_sub" numeric(12, 2),
	"total_vat" numeric(12, 2),
	"ps365_synced_at" timestamp,
	"customer_code_365" varchar(50),
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255)
);
--> statement-breakpoint
CREATE TABLE "activity_logs" (
	"id" serial PRIMARY KEY NOT NULL,
	"picker_username" varchar(64),
	"timestamp" timestamp,
	"activity_type" varchar(50),
	"invoice_no" varchar(50),
	"item_code" varchar(50),
	"details" text
);
--> statement-breakpoint
CREATE TABLE "batch_picking_sessions" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" varchar(100) NOT NULL,
	"zones" varchar(500) NOT NULL,
	"created_at" timestamp,
	"created_by" varchar(64) NOT NULL,
	"assigned_to" varchar(64),
	"status" varchar(20),
	"current_item_index" integer,
	"picking_mode" varchar(20) DEFAULT 'Sequential',
	"current_invoice_index" integer DEFAULT 0,
	"batch_number" varchar(20),
	"corridors" varchar(500),
	"unit_types" varchar(500) DEFAULT NULL,
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255),
	CONSTRAINT "batch_picking_sessions_batch_number_key" UNIQUE("batch_number")
);
--> statement-breakpoint
CREATE TABLE "batch_picked_items" (
	"id" serial PRIMARY KEY NOT NULL,
	"batch_session_id" integer NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"item_code" varchar(50) NOT NULL,
	"picked_qty" integer NOT NULL,
	"timestamp" timestamp,
	CONSTRAINT "uq_batch_picked_items_unique" UNIQUE("batch_session_id","invoice_no","item_code")
);
--> statement-breakpoint
CREATE TABLE "cod_invoice_allocations" (
	"id" serial PRIMARY KEY NOT NULL,
	"cod_receipt_id" integer,
	"invoice_no" varchar(50) NOT NULL,
	"route_id" integer NOT NULL,
	"expected_amount" numeric(12, 2) DEFAULT '0' NOT NULL,
	"received_amount" numeric(12, 2) DEFAULT '0' NOT NULL,
	"deduct_amount" numeric(12, 2) DEFAULT '0' NOT NULL,
	"payment_method" varchar(30) DEFAULT 'cash' NOT NULL,
	"is_pending" boolean DEFAULT false NOT NULL,
	"cheque_number" varchar(50),
	"cheque_date" date,
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "shipments" (
	"id" serial PRIMARY KEY NOT NULL,
	"driver_name" varchar(100) NOT NULL,
	"route_name" varchar(100),
	"status" varchar(20) NOT NULL,
	"delivery_date" date NOT NULL,
	"created_at" timestamp,
	"updated_at" timestamp DEFAULT now(),
	"started_at" timestamp,
	"completed_at" timestamp,
	"settlement_status" varchar(20) DEFAULT 'PENDING',
	"driver_submitted_at" timestamp,
	"cash_expected" numeric(12, 2),
	"cash_handed_in" numeric(12, 2),
	"cash_variance" numeric(12, 2),
	"cash_variance_note" text,
	"returns_count" integer DEFAULT 0,
	"returns_weight" double precision,
	"settlement_notes" text,
	"completion_reason" varchar(50),
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255),
	"reconciliation_status" varchar(20) DEFAULT 'NOT_READY',
	"reconciled_at" timestamp,
	"reconciled_by" varchar(64),
	"is_archived" boolean DEFAULT false NOT NULL,
	"archived_at" timestamp,
	"archived_by" varchar(64),
	"cash_collected" numeric(12, 2),
	"settlement_cleared_at" timestamp,
	"settlement_cleared_by" varchar(64)
);
--> statement-breakpoint
CREATE TABLE "payment_customers" (
	"id" serial PRIMARY KEY NOT NULL,
	"code" varchar(50) NOT NULL,
	"name" varchar(255) NOT NULL,
	"group" varchar(100)
);
--> statement-breakpoint
CREATE TABLE "credit_terms" (
	"id" serial PRIMARY KEY NOT NULL,
	"customer_code" varchar(50) NOT NULL,
	"terms_code" varchar(50) NOT NULL,
	"due_days" integer NOT NULL,
	"is_credit" boolean NOT NULL,
	"credit_limit" numeric(12, 2),
	"allow_cash" boolean,
	"allow_card_pos" boolean,
	"allow_bank_transfer" boolean,
	"allow_cheque" boolean,
	"cheque_days_allowed" integer,
	"min_cash_allowed" integer,
	"max_cash_allowed" integer,
	"notes_for_driver" text,
	"valid_from" date,
	"valid_to" date,
	CONSTRAINT "uniq_terms_version" UNIQUE("customer_code","valid_from")
);
--> statement-breakpoint
CREATE TABLE "customer_delivery_slots" (
	"id" serial PRIMARY KEY NOT NULL,
	"customer_code_365" varchar(50) NOT NULL,
	"dow" integer NOT NULL,
	"week_code" integer NOT NULL,
	CONSTRAINT "customer_delivery_slots_customer_code_365_dow_week_code_key" UNIQUE("customer_code_365","dow","week_code")
);
--> statement-breakpoint
CREATE TABLE "delivery_discrepancies" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"item_code_expected" varchar(50) NOT NULL,
	"item_name" varchar(200),
	"qty_expected" integer NOT NULL,
	"qty_actual" numeric(10, 2),
	"discrepancy_type" varchar(50) NOT NULL,
	"reported_by" varchar(64) NOT NULL,
	"reported_at" timestamp NOT NULL,
	"reported_source" varchar(50),
	"status" varchar(20) NOT NULL,
	"validated_by" varchar(64),
	"validated_at" timestamp,
	"resolved_by" varchar(64),
	"resolved_at" timestamp,
	"resolution_action" varchar(50),
	"note" text,
	"photo_paths" text,
	"picker_username" varchar(64),
	"picked_at" timestamp,
	"delivery_date" date,
	"shelf_code_365" varchar(50),
	"location" varchar(100),
	"is_validated" boolean DEFAULT false NOT NULL,
	"is_resolved" boolean DEFAULT false NOT NULL,
	"actual_item_id" integer,
	"actual_item_code" text,
	"actual_item_name" text,
	"actual_qty" numeric(12, 3),
	"actual_barcode" text,
	"warehouse_checked_by" varchar(64),
	"warehouse_checked_at" timestamp,
	"warehouse_result" varchar(30),
	"warehouse_note" text,
	"credit_note_required" boolean DEFAULT false,
	"credit_note_no" varchar(50),
	"credit_note_amount" numeric(12, 2),
	"credit_note_created_at" timestamp,
	"reported_value" numeric(12, 2),
	"deduct_amount" numeric(12, 2) DEFAULT '0' NOT NULL
);
--> statement-breakpoint
CREATE TABLE "delivery_discrepancy_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"discrepancy_id" integer NOT NULL,
	"event_type" varchar(50) NOT NULL,
	"actor" varchar(64) NOT NULL,
	"timestamp" timestamp NOT NULL,
	"note" text,
	"old_value" text,
	"new_value" text
);
--> statement-breakpoint
CREATE TABLE "delivery_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"action" varchar(30) NOT NULL,
	"actor" varchar(64) NOT NULL,
	"timestamp" timestamp NOT NULL,
	"reason" text
);
--> statement-breakpoint
CREATE TABLE "delivery_lines" (
	"id" serial PRIMARY KEY NOT NULL,
	"route_id" integer NOT NULL,
	"route_stop_id" integer NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"item_code" varchar(50) NOT NULL,
	"qty_ordered" numeric(10, 2) NOT NULL,
	"qty_delivered" numeric(10, 2) NOT NULL,
	"created_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "dw_invoice_header" (
	"invoice_no_365" varchar(64) PRIMARY KEY NOT NULL,
	"invoice_type" varchar(64) NOT NULL,
	"invoice_date_utc0" date NOT NULL,
	"customer_code_365" varchar(64),
	"store_code_365" varchar(64),
	"user_code_365" varchar(64),
	"total_sub" numeric(18, 4),
	"total_discount" numeric(18, 4),
	"total_vat" numeric(18, 4),
	"total_grand" numeric(18, 4),
	"points_earned" numeric(18, 2),
	"points_redeemed" numeric(18, 2),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL,
	"total_net" numeric(18, 4)
);
--> statement-breakpoint
CREATE TABLE "dw_invoice_line" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no_365" varchar(64) NOT NULL,
	"line_number" integer NOT NULL,
	"item_code_365" varchar(64),
	"quantity" numeric(18, 4),
	"price_excl" numeric(18, 4),
	"price_incl" numeric(18, 4),
	"discount_percent" numeric(18, 4),
	"vat_code_365" varchar(20),
	"vat_percent" numeric(6, 4),
	"line_total_excl" numeric(18, 4),
	"line_total_discount" numeric(18, 4),
	"line_total_vat" numeric(18, 4),
	"line_total_incl" numeric(18, 4),
	"attr_hash" varchar(32) NOT NULL,
	"last_sync_at" timestamp NOT NULL,
	"line_net_value" numeric(18, 4),
	CONSTRAINT "unique_invoice_line" UNIQUE("invoice_no_365","line_number")
);
--> statement-breakpoint
CREATE TABLE "shifts" (
	"id" serial PRIMARY KEY NOT NULL,
	"picker_username" varchar(64) NOT NULL,
	"check_in_time" timestamp NOT NULL,
	"check_out_time" timestamp,
	"check_in_coordinates" varchar(100),
	"check_out_coordinates" varchar(100),
	"total_duration_minutes" integer,
	"status" varchar(20),
	"admin_adjusted" boolean,
	"adjustment_note" text,
	"adjustment_by" varchar(64),
	"adjustment_time" timestamp
);
--> statement-breakpoint
CREATE TABLE "idle_periods" (
	"id" serial PRIMARY KEY NOT NULL,
	"shift_id" integer NOT NULL,
	"start_time" timestamp NOT NULL,
	"end_time" timestamp,
	"duration_minutes" integer,
	"is_break" boolean,
	"break_reason" varchar(200)
);
--> statement-breakpoint
CREATE TABLE "ps_customers" (
	"customer_code_365" varchar(50) PRIMARY KEY NOT NULL,
	"customer_code_secondary" text,
	"is_company" boolean,
	"company_name" text,
	"store_code_365" text,
	"active" boolean NOT NULL,
	"tel_1" text,
	"mobile" text,
	"sms" text,
	"website" text,
	"category_code_1_365" text,
	"category_1_name" text,
	"category_code_2_365" text,
	"category_2_name" text,
	"company_activity_code_365" text,
	"company_activity_name" text,
	"credit_limit_amount" double precision,
	"vat_registration_number" text,
	"address_line_1" text,
	"address_line_2" text,
	"address_line_3" text,
	"postal_code" text,
	"town" text,
	"contact_last_name" text,
	"contact_first_name" text,
	"agent_code_365" text,
	"agent_name" text,
	"last_synced_at" timestamp,
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255),
	"is_active" boolean DEFAULT true NOT NULL,
	"disabled_at" timestamp,
	"disabled_reason" varchar(255),
	"latitude" double precision,
	"longitude" double precision,
	"reporting_group" text,
	"delivery_days" text,
	"delivery_days_status" varchar(20) DEFAULT 'EMPTY',
	"delivery_days_invalid_tokens" text,
	"delivery_days_parsed_at" timestamp with time zone,
	"email" text
);
--> statement-breakpoint
CREATE TABLE "users" (
	"username" varchar(64) PRIMARY KEY NOT NULL,
	"password" varchar(256) NOT NULL,
	"role" varchar(20) NOT NULL,
	"payment_type_code_365" varchar(50),
	"require_gps_check" boolean DEFAULT true,
	"disabled_at" timestamp,
	"disabled_reason" varchar(255),
	"is_active" boolean DEFAULT true NOT NULL,
	"cheque_payment_type_code_365" varchar(50)
);
--> statement-breakpoint
CREATE TABLE "invoice_delivery_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"action" varchar(30) NOT NULL,
	"actor" varchar(64) NOT NULL,
	"timestamp" timestamp NOT NULL,
	"reason" text
);
--> statement-breakpoint
CREATE TABLE "invoice_payment_expectations" (
	"invoice_no" varchar(50) PRIMARY KEY NOT NULL,
	"expected_payment_method" varchar(20) NOT NULL,
	"is_cod" boolean NOT NULL,
	"expected_amount" numeric(12, 2),
	"customer_code_365" varchar(50),
	"terms_code" varchar(50),
	"due_days" integer,
	"captured_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "invoice_post_delivery_cases" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"route_id" bigint,
	"route_stop_id" bigint,
	"status" varchar(50) DEFAULT 'OPEN' NOT NULL,
	"reason" text,
	"notes" text,
	"created_by" varchar(100),
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"credit_note_required" boolean DEFAULT false,
	"credit_note_expected_amount" numeric(12, 2) DEFAULT '0',
	"credit_note_no" varchar(64),
	"credit_note_issued_at" timestamp,
	"credit_note_issued_by" varchar(64)
);
--> statement-breakpoint
CREATE TABLE "invoice_route_history" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"route_id" bigint,
	"route_stop_id" bigint,
	"action" varchar(100) NOT NULL,
	"reason" text,
	"notes" text,
	"actor_username" varchar(100),
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "item_time_tracking" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"item_code" varchar(50) NOT NULL,
	"picker_username" varchar(64) NOT NULL,
	"item_started" timestamp,
	"item_completed" timestamp,
	"walking_to_location" double precision,
	"time_at_location" double precision,
	"location" varchar(100),
	"zone" varchar(50),
	"quantity_picked" integer,
	"created_at" timestamp,
	"walking_time" double precision DEFAULT 0,
	"picking_time" double precision DEFAULT 0,
	"confirmation_time" double precision DEFAULT 0,
	"total_item_time" double precision DEFAULT 0,
	"corridor" varchar(50),
	"shelf" varchar(50),
	"level" varchar(50),
	"bin_location" varchar(50),
	"quantity_expected" integer DEFAULT 0,
	"item_weight" double precision,
	"item_name" varchar(200),
	"unit_type" varchar(50),
	"expected_time" double precision DEFAULT 0,
	"efficiency_ratio" double precision DEFAULT 0,
	"previous_location" varchar(100),
	"order_sequence" integer DEFAULT 0,
	"time_of_day" varchar(10),
	"day_of_week" varchar(10),
	"picked_correctly" boolean DEFAULT true,
	"was_skipped" boolean DEFAULT false,
	"skip_reason" varchar(200),
	"peak_hours" boolean DEFAULT false,
	"concurrent_pickers" integer DEFAULT 1,
	"updated_at" timestamp DEFAULT CURRENT_TIMESTAMP
);
--> statement-breakpoint
CREATE TABLE "oi_estimate_runs" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"estimator_version" varchar(50) NOT NULL,
	"params_revision" integer NOT NULL,
	"params_snapshot_json" text,
	"estimated_total_seconds" double precision,
	"estimated_pick_seconds" double precision,
	"estimated_travel_seconds" double precision,
	"breakdown_json" text,
	"reason" varchar(100),
	"created_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "oi_estimate_lines" (
	"id" serial PRIMARY KEY NOT NULL,
	"run_id" integer NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"invoice_item_id" integer,
	"item_code" varchar(100),
	"location" varchar(100),
	"unit_type_normalized" varchar(50),
	"qty" double precision,
	"estimated_pick_seconds" double precision,
	"estimated_walk_seconds" double precision,
	"estimated_total_seconds" double precision,
	"breakdown_json" text
);
--> statement-breakpoint
CREATE TABLE "order_time_breakdown" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"picker_username" varchar(64) NOT NULL,
	"picking_started" timestamp,
	"picking_completed" timestamp,
	"packing_started" timestamp,
	"packing_completed" timestamp,
	"total_walking_time" double precision,
	"total_picking_time" double precision,
	"total_packing_time" double precision,
	"total_items_picked" integer,
	"total_locations_visited" integer,
	"average_time_per_item" double precision,
	"created_at" timestamp,
	"updated_at" timestamp
);
--> statement-breakpoint
CREATE TABLE "picking_exceptions" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"item_code" varchar(50) NOT NULL,
	"expected_qty" integer NOT NULL,
	"picked_qty" integer NOT NULL,
	"picker_username" varchar(64) NOT NULL,
	"timestamp" timestamp,
	"reason" varchar(500)
);
--> statement-breakpoint
CREATE TABLE "pod_records" (
	"id" serial PRIMARY KEY NOT NULL,
	"route_id" integer NOT NULL,
	"route_stop_id" integer NOT NULL,
	"invoice_nos" json NOT NULL,
	"has_physical_signed_invoice" boolean,
	"receiver_name" varchar(200),
	"receiver_relationship" varchar(100),
	"photo_paths" json,
	"gps_lat" numeric(10, 8),
	"gps_lng" numeric(11, 8),
	"collected_at" timestamp NOT NULL,
	"collected_by" varchar(64) NOT NULL,
	"notes" text
);
--> statement-breakpoint
CREATE TABLE "purchase_orders" (
	"id" serial PRIMARY KEY NOT NULL,
	"code_365" varchar(100),
	"shopping_cart_code" varchar(100),
	"supplier_code" varchar(100),
	"status_code" varchar(50),
	"status_name" varchar(100),
	"order_date_local" varchar(50),
	"order_date_utc0" varchar(50),
	"comments" text,
	"total_sub" numeric(12, 2),
	"total_discount" numeric(12, 2),
	"total_vat" numeric(12, 2),
	"total_grand" numeric(12, 2),
	"downloaded_at" timestamp NOT NULL,
	"downloaded_by" varchar(64),
	"supplier_name" varchar(200),
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255),
	"is_archived" boolean DEFAULT false NOT NULL,
	"archived_at" timestamp,
	"archived_by" varchar(64),
	"description" text
);
--> statement-breakpoint
CREATE TABLE "purchase_order_lines" (
	"id" serial PRIMARY KEY NOT NULL,
	"purchase_order_id" integer NOT NULL,
	"line_number" integer NOT NULL,
	"item_code_365" varchar(100) NOT NULL,
	"item_name" varchar(500),
	"line_quantity" numeric(12, 4),
	"line_price_excl_vat" numeric(12, 2),
	"line_total_sub" numeric(12, 2),
	"line_total_discount" numeric(12, 2),
	"line_total_discount_percentage" numeric(5, 2),
	"line_vat_code_365" varchar(50),
	"line_total_vat" numeric(12, 2),
	"line_total_vat_percentage" numeric(5, 2),
	"line_total_grand" numeric(12, 2),
	"shelf_locations" text,
	"item_has_expiration_date" boolean DEFAULT false NOT NULL,
	"item_has_lot_number" boolean DEFAULT false NOT NULL,
	"item_has_serial_number" boolean DEFAULT false NOT NULL,
	"line_id_365" varchar(100),
	"item_barcode" varchar(100),
	"unit_type" varchar(50),
	"pieces_per_unit" integer,
	"supplier_item_code" varchar(255),
	"stock_qty" numeric(12, 4),
	"stock_reserved_qty" numeric(12, 4),
	"stock_ordered_qty" numeric(12, 4),
	"available_qty" numeric(12, 4),
	"stock_synced_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "receipt_log" (
	"id" serial PRIMARY KEY NOT NULL,
	"reference_number" varchar(32) NOT NULL,
	"customer_code_365" varchar(32) NOT NULL,
	"amount" numeric(12, 2) NOT NULL,
	"comments" varchar(1000),
	"response_id" varchar(128),
	"success" integer,
	"request_json" text,
	"response_json" text,
	"created_at" timestamp,
	"invoice_no" varchar(500),
	"driver_username" varchar(64),
	"route_stop_id" integer,
	CONSTRAINT "receipt_log_reference_number_key" UNIQUE("reference_number")
);
--> statement-breakpoint
CREATE TABLE "receiving_lines" (
	"id" serial PRIMARY KEY NOT NULL,
	"session_id" integer NOT NULL,
	"po_line_id" integer NOT NULL,
	"barcode_scanned" varchar(200),
	"item_code_365" varchar(100) NOT NULL,
	"qty_received" numeric(12, 4) NOT NULL,
	"expiry_date" date,
	"lot_note" text,
	"received_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "receiving_sessions" (
	"id" serial PRIMARY KEY NOT NULL,
	"purchase_order_id" integer NOT NULL,
	"receipt_code" varchar(50) NOT NULL,
	"operator" varchar(64),
	"started_at" timestamp NOT NULL,
	"finished_at" timestamp,
	"comments" text
);
--> statement-breakpoint
CREATE TABLE "reroute_requests" (
	"id" bigserial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"requested_by" varchar(100),
	"status" varchar(50) DEFAULT 'OPEN' NOT NULL,
	"notes" text,
	"assigned_route_id" bigint,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"completed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "route_delivery_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"route_id" integer NOT NULL,
	"route_stop_id" integer,
	"event_type" varchar(50) NOT NULL,
	"payload" json,
	"gps_lat" numeric(10, 8),
	"gps_lng" numeric(11, 8),
	"created_at" timestamp NOT NULL,
	"actor_username" varchar(64) NOT NULL
);
--> statement-breakpoint
CREATE TABLE "route_return_handover" (
	"id" serial PRIMARY KEY NOT NULL,
	"route_id" integer NOT NULL,
	"route_stop_id" integer,
	"invoice_no" varchar(50) NOT NULL,
	"driver_confirmed_at" timestamp,
	"driver_username" varchar(64),
	"warehouse_received_at" timestamp,
	"received_by" varchar(64),
	"packages_count" integer,
	"notes" text,
	"photo_paths" jsonb,
	"created_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "shipment_orders" (
	"id" serial PRIMARY KEY NOT NULL,
	"shipment_id" integer NOT NULL,
	"invoice_no" varchar(20) NOT NULL
);
--> statement-breakpoint
CREATE TABLE "shipping_events" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"action" varchar(20) NOT NULL,
	"actor" varchar(64) NOT NULL,
	"timestamp" timestamp NOT NULL,
	"note" text
);
--> statement-breakpoint
CREATE TABLE "time_tracking_alerts" (
	"id" serial PRIMARY KEY NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"picker_username" varchar(64) NOT NULL,
	"alert_type" varchar(50) NOT NULL,
	"expected_duration" double precision NOT NULL,
	"actual_duration" double precision NOT NULL,
	"threshold_percentage" double precision NOT NULL,
	"created_at" timestamp,
	"is_resolved" boolean,
	"resolved_at" timestamp,
	"resolved_by" varchar(64),
	"notes" text
);
--> statement-breakpoint
CREATE TABLE "wms_pallet" (
	"pallet_id" serial PRIMARY KEY NOT NULL,
	"shipment_id" integer NOT NULL,
	"label" varchar(50) NOT NULL,
	"lane_code" varchar(10),
	"lane_slot" integer,
	"status" varchar(20) NOT NULL,
	"max_weight_kg" numeric(10, 2) NOT NULL,
	"max_height_m" numeric(10, 2) NOT NULL,
	"used_mask" integer NOT NULL,
	"used_weight_kg" numeric(10, 2) NOT NULL,
	"created_at" timestamp NOT NULL,
	"updated_at" timestamp NOT NULL,
	"deleted_at" timestamp,
	"deleted_by" varchar(64),
	"delete_reason" varchar(255)
);
--> statement-breakpoint
CREATE TABLE "wms_pallet_order" (
	"id" serial PRIMARY KEY NOT NULL,
	"pallet_id" integer NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"blocks_requested" integer NOT NULL,
	"blocks_mask" integer NOT NULL,
	"est_weight_kg" numeric(10, 2),
	"stop_seq_no" numeric(10, 2),
	"created_at" timestamp NOT NULL,
	CONSTRAINT "uq_pallet_order_invoice_no" UNIQUE("invoice_no")
);
--> statement-breakpoint
CREATE TABLE "payment_entries" (
	"id" serial PRIMARY KEY NOT NULL,
	"route_stop_id" integer NOT NULL,
	"method" varchar(20) NOT NULL,
	"amount" numeric(18, 2) NOT NULL,
	"cheque_no" varchar(64),
	"cheque_date" date,
	"commit_mode" varchar(20) NOT NULL,
	"doc_type" varchar(20) NOT NULL,
	"ps_status" varchar(20) NOT NULL,
	"ps_reference" varchar(64),
	"ps_error" text,
	"attempt_count" integer NOT NULL,
	"last_attempt_at" timestamp,
	"is_active" boolean NOT NULL,
	"created_at" timestamp NOT NULL,
	"updated_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "cod_receipts" (
	"id" serial PRIMARY KEY NOT NULL,
	"route_id" integer NOT NULL,
	"route_stop_id" integer NOT NULL,
	"driver_username" varchar(64) NOT NULL,
	"invoice_nos" json NOT NULL,
	"expected_amount" numeric(12, 2) NOT NULL,
	"received_amount" numeric(12, 2) NOT NULL,
	"variance" numeric(12, 2),
	"payment_method" varchar(20) NOT NULL,
	"note" text,
	"ps365_receipt_id" varchar(128),
	"ps365_synced_at" timestamp,
	"created_at" timestamp NOT NULL,
	"cheque_number" varchar(50),
	"cheque_date" date,
	"doc_type" varchar(30) DEFAULT 'official' NOT NULL,
	"status" varchar(20) DEFAULT 'DRAFT' NOT NULL,
	"locked_at" timestamp with time zone,
	"locked_by" varchar(64),
	"print_count" integer DEFAULT 0 NOT NULL,
	"first_printed_at" timestamp with time zone,
	"last_printed_at" timestamp with time zone,
	"voided_at" timestamp with time zone,
	"voided_by" varchar(64),
	"void_reason" text,
	"replaced_by_cod_receipt_id" integer,
	"client_request_id" varchar(128),
	"ps365_reference_number" varchar(128)
);
--> statement-breakpoint
CREATE TABLE "bank_transactions" (
	"id" serial PRIMARY KEY NOT NULL,
	"batch_id" varchar(36) NOT NULL,
	"txn_date" date,
	"description" text,
	"reference" varchar(200),
	"credit" numeric(12, 2),
	"debit" numeric(12, 2),
	"balance" numeric(14, 2),
	"raw_row" text,
	"matched_allocation_id" integer,
	"match_status" varchar(20) NOT NULL,
	"match_confidence" varchar(20),
	"match_reason" varchar(200),
	"dismissed" boolean NOT NULL,
	"uploaded_by" varchar(64),
	"uploaded_at" timestamp NOT NULL
);
--> statement-breakpoint
CREATE TABLE "batch_session_invoices" (
	"batch_session_id" integer NOT NULL,
	"invoice_no" varchar(50) NOT NULL,
	"is_completed" boolean DEFAULT false,
	CONSTRAINT "batch_session_invoices_pkey" PRIMARY KEY("batch_session_id","invoice_no")
);
--> statement-breakpoint
CREATE TABLE "invoice_items" (
	"invoice_no" varchar(50) NOT NULL,
	"item_code" varchar(50) NOT NULL,
	"location" varchar(100),
	"barcode" varchar(100),
	"zone" varchar(50),
	"item_weight" double precision,
	"item_name" varchar(200),
	"unit_type" varchar(50),
	"pack" varchar(50),
	"qty" integer,
	"line_weight" double precision,
	"exp_time" double precision,
	"picked_qty" integer,
	"is_picked" boolean,
	"pick_status" varchar(20) DEFAULT 'not_picked',
	"reset_by" varchar(64),
	"reset_timestamp" timestamp,
	"reset_note" varchar(500),
	"skip_reason" text,
	"skip_timestamp" timestamp,
	"skip_count" integer DEFAULT 0,
	"corridor" varchar(10),
	"locked_by_batch_id" integer,
	"pieces_per_unit_snapshot" integer,
	"expected_pick_pieces" integer,
	CONSTRAINT "invoice_items_pkey" PRIMARY KEY("invoice_no","item_code")
);
--> statement-breakpoint
ALTER TABLE "route_stop" ADD CONSTRAINT "route_stop_shipment_id_fkey" FOREIGN KEY ("shipment_id") REFERENCES "public"."shipments"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_stop_invoice" ADD CONSTRAINT "route_stop_invoice_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_stop_invoice" ADD CONSTRAINT "route_stop_invoice_manifest_locked_by_fkey" FOREIGN KEY ("manifest_locked_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_stop_invoice" ADD CONSTRAINT "route_stop_invoice_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoices" ADD CONSTRAINT "fk_invoices_shipped_by" FOREIGN KEY ("shipped_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoices" ADD CONSTRAINT "invoices_assigned_to_fkey" FOREIGN KEY ("assigned_to") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoices" ADD CONSTRAINT "invoices_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoices" ADD CONSTRAINT "invoices_stop_id_fkey" FOREIGN KEY ("stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "activity_logs" ADD CONSTRAINT "activity_logs_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "activity_logs" ADD CONSTRAINT "activity_logs_picker_username_fkey" FOREIGN KEY ("picker_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "batch_picking_sessions" ADD CONSTRAINT "batch_picking_sessions_assigned_to_fkey" FOREIGN KEY ("assigned_to") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "batch_picking_sessions" ADD CONSTRAINT "batch_picking_sessions_created_by_fkey" FOREIGN KEY ("created_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "batch_picked_items" ADD CONSTRAINT "batch_picked_items_batch_session_id_fkey" FOREIGN KEY ("batch_session_id") REFERENCES "public"."batch_picking_sessions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "batch_picked_items" ADD CONSTRAINT "batch_picked_items_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_invoice_allocations" ADD CONSTRAINT "cod_invoice_allocations_cod_receipt_id_fkey" FOREIGN KEY ("cod_receipt_id") REFERENCES "public"."cod_receipts"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_invoice_allocations" ADD CONSTRAINT "cod_invoice_allocations_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_invoice_allocations" ADD CONSTRAINT "cod_invoice_allocations_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "credit_terms" ADD CONSTRAINT "credit_terms_customer_code_fkey" FOREIGN KEY ("customer_code") REFERENCES "public"."payment_customers"("code") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "customer_delivery_slots" ADD CONSTRAINT "customer_delivery_slots_customer_code_365_fkey" FOREIGN KEY ("customer_code_365") REFERENCES "public"."ps_customers"("customer_code_365") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancies" ADD CONSTRAINT "delivery_discrepancies_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancies" ADD CONSTRAINT "delivery_discrepancies_reported_by_fkey" FOREIGN KEY ("reported_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancies" ADD CONSTRAINT "delivery_discrepancies_resolved_by_fkey" FOREIGN KEY ("resolved_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancies" ADD CONSTRAINT "delivery_discrepancies_validated_by_fkey" FOREIGN KEY ("validated_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancies" ADD CONSTRAINT "delivery_discrepancies_warehouse_checked_by_fkey" FOREIGN KEY ("warehouse_checked_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancy_events" ADD CONSTRAINT "delivery_discrepancy_events_actor_fkey" FOREIGN KEY ("actor") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_discrepancy_events" ADD CONSTRAINT "delivery_discrepancy_events_discrepancy_id_fkey" FOREIGN KEY ("discrepancy_id") REFERENCES "public"."delivery_discrepancies"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_events" ADD CONSTRAINT "delivery_events_actor_fkey" FOREIGN KEY ("actor") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_events" ADD CONSTRAINT "delivery_events_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_lines" ADD CONSTRAINT "delivery_lines_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_lines" ADD CONSTRAINT "delivery_lines_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "delivery_lines" ADD CONSTRAINT "delivery_lines_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "dw_invoice_line" ADD CONSTRAINT "dw_invoice_line_invoice_no_365_fkey" FOREIGN KEY ("invoice_no_365") REFERENCES "public"."dw_invoice_header"("invoice_no_365") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shifts" ADD CONSTRAINT "shifts_adjustment_by_fkey" FOREIGN KEY ("adjustment_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shifts" ADD CONSTRAINT "shifts_picker_username_fkey" FOREIGN KEY ("picker_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "idle_periods" ADD CONSTRAINT "idle_periods_shift_id_fkey" FOREIGN KEY ("shift_id") REFERENCES "public"."shifts"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_delivery_events" ADD CONSTRAINT "invoice_delivery_events_actor_fkey" FOREIGN KEY ("actor") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_delivery_events" ADD CONSTRAINT "invoice_delivery_events_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_payment_expectations" ADD CONSTRAINT "invoice_payment_expectations_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_post_delivery_cases" ADD CONSTRAINT "invoice_post_delivery_cases_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_post_delivery_cases" ADD CONSTRAINT "invoice_post_delivery_cases_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_post_delivery_cases" ADD CONSTRAINT "invoice_post_delivery_cases_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_route_history" ADD CONSTRAINT "invoice_route_history_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_route_history" ADD CONSTRAINT "invoice_route_history_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_route_history" ADD CONSTRAINT "invoice_route_history_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "item_time_tracking" ADD CONSTRAINT "item_time_tracking_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "item_time_tracking" ADD CONSTRAINT "item_time_tracking_picker_username_fkey" FOREIGN KEY ("picker_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "oi_estimate_runs" ADD CONSTRAINT "oi_estimate_runs_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "oi_estimate_lines" ADD CONSTRAINT "oi_estimate_lines_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "public"."oi_estimate_runs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "order_time_breakdown" ADD CONSTRAINT "order_time_breakdown_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "order_time_breakdown" ADD CONSTRAINT "order_time_breakdown_picker_username_fkey" FOREIGN KEY ("picker_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "picking_exceptions" ADD CONSTRAINT "picking_exceptions_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "picking_exceptions" ADD CONSTRAINT "picking_exceptions_picker_username_fkey" FOREIGN KEY ("picker_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pod_records" ADD CONSTRAINT "pod_records_collected_by_fkey" FOREIGN KEY ("collected_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pod_records" ADD CONSTRAINT "pod_records_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "pod_records" ADD CONSTRAINT "pod_records_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "purchase_orders" ADD CONSTRAINT "purchase_orders_archived_by_fkey" FOREIGN KEY ("archived_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "purchase_orders" ADD CONSTRAINT "purchase_orders_downloaded_by_fkey" FOREIGN KEY ("downloaded_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "purchase_order_lines" ADD CONSTRAINT "purchase_order_lines_purchase_order_id_fkey" FOREIGN KEY ("purchase_order_id") REFERENCES "public"."purchase_orders"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "receipt_log" ADD CONSTRAINT "receipt_log_driver_username_fkey" FOREIGN KEY ("driver_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "receipt_log" ADD CONSTRAINT "receipt_log_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "receiving_lines" ADD CONSTRAINT "receiving_lines_po_line_id_fkey" FOREIGN KEY ("po_line_id") REFERENCES "public"."purchase_order_lines"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "receiving_lines" ADD CONSTRAINT "receiving_lines_session_id_fkey" FOREIGN KEY ("session_id") REFERENCES "public"."receiving_sessions"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "receiving_sessions" ADD CONSTRAINT "receiving_sessions_operator_fkey" FOREIGN KEY ("operator") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "receiving_sessions" ADD CONSTRAINT "receiving_sessions_purchase_order_id_fkey" FOREIGN KEY ("purchase_order_id") REFERENCES "public"."purchase_orders"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "reroute_requests" ADD CONSTRAINT "reroute_requests_assigned_route_id_fkey" FOREIGN KEY ("assigned_route_id") REFERENCES "public"."shipments"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "reroute_requests" ADD CONSTRAINT "reroute_requests_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_delivery_events" ADD CONSTRAINT "route_delivery_events_actor_username_fkey" FOREIGN KEY ("actor_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_delivery_events" ADD CONSTRAINT "route_delivery_events_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_delivery_events" ADD CONSTRAINT "route_delivery_events_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_return_handover" ADD CONSTRAINT "route_return_handover_driver_username_fkey" FOREIGN KEY ("driver_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_return_handover" ADD CONSTRAINT "route_return_handover_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_return_handover" ADD CONSTRAINT "route_return_handover_received_by_fkey" FOREIGN KEY ("received_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_return_handover" ADD CONSTRAINT "route_return_handover_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "route_return_handover" ADD CONSTRAINT "route_return_handover_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shipment_orders" ADD CONSTRAINT "shipment_orders_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shipment_orders" ADD CONSTRAINT "shipment_orders_shipment_id_fkey" FOREIGN KEY ("shipment_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shipping_events" ADD CONSTRAINT "shipping_events_actor_fkey" FOREIGN KEY ("actor") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "shipping_events" ADD CONSTRAINT "shipping_events_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "time_tracking_alerts" ADD CONSTRAINT "time_tracking_alerts_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "time_tracking_alerts" ADD CONSTRAINT "time_tracking_alerts_picker_username_fkey" FOREIGN KEY ("picker_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "time_tracking_alerts" ADD CONSTRAINT "time_tracking_alerts_resolved_by_fkey" FOREIGN KEY ("resolved_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "wms_pallet" ADD CONSTRAINT "wms_pallet_shipment_id_fkey" FOREIGN KEY ("shipment_id") REFERENCES "public"."shipments"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "wms_pallet_order" ADD CONSTRAINT "wms_pallet_order_pallet_id_fkey" FOREIGN KEY ("pallet_id") REFERENCES "public"."wms_pallet"("pallet_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "payment_entries" ADD CONSTRAINT "payment_entries_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_receipts" ADD CONSTRAINT "cod_receipts_driver_username_fkey" FOREIGN KEY ("driver_username") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_receipts" ADD CONSTRAINT "cod_receipts_locked_by_fkey" FOREIGN KEY ("locked_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_receipts" ADD CONSTRAINT "cod_receipts_replaced_by_cod_receipt_id_fkey" FOREIGN KEY ("replaced_by_cod_receipt_id") REFERENCES "public"."cod_receipts"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_receipts" ADD CONSTRAINT "cod_receipts_route_id_fkey" FOREIGN KEY ("route_id") REFERENCES "public"."shipments"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_receipts" ADD CONSTRAINT "cod_receipts_route_stop_id_fkey" FOREIGN KEY ("route_stop_id") REFERENCES "public"."route_stop"("route_stop_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "cod_receipts" ADD CONSTRAINT "cod_receipts_voided_by_fkey" FOREIGN KEY ("voided_by") REFERENCES "public"."users"("username") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "bank_transactions" ADD CONSTRAINT "bank_transactions_matched_allocation_id_fkey" FOREIGN KEY ("matched_allocation_id") REFERENCES "public"."cod_invoice_allocations"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "batch_session_invoices" ADD CONSTRAINT "batch_session_invoices_batch_session_id_fkey" FOREIGN KEY ("batch_session_id") REFERENCES "public"."batch_picking_sessions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "batch_session_invoices" ADD CONSTRAINT "batch_session_invoices_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_items" ADD CONSTRAINT "fk_locked_by_batch_id" FOREIGN KEY ("locked_by_batch_id") REFERENCES "public"."batch_picking_sessions"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "invoice_items" ADD CONSTRAINT "invoice_items_invoice_no_fkey" FOREIGN KEY ("invoice_no") REFERENCES "public"."invoices"("invoice_no") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "idx_ai_feedback_cache_expires" ON "ai_feedback_cache" USING btree ("expires_at" timestamptz_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_category_penetration_category_code" ON "dw_category_penetration" USING btree ("category_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_category_penetration_customer_code_365" ON "dw_category_penetration" USING btree ("customer_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_churn_risk_category_code" ON "dw_churn_risk" USING btree ("category_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_churn_risk_customer_code_365" ON "dw_churn_risk" USING btree ("customer_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_reco_basket_from_item_code" ON "dw_reco_basket" USING btree ("from_item_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_reco_basket_to_item_code" ON "dw_reco_basket" USING btree ("to_item_code" text_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "ix_postal_lookup_cache_cache_key" ON "postal_lookup_cache" USING btree ("cache_key" text_ops);--> statement-breakpoint
CREATE INDEX "ix_ps365_reserved_stock_777_synced_at" ON "ps365_reserved_stock_777" USING btree ("synced_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "ix_stock_positions_imported_at" ON "stock_positions" USING btree ("imported_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "ix_stock_positions_item_code" ON "stock_positions" USING btree ("item_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_stock_positions_store_code" ON "stock_positions" USING btree ("store_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_stock_positions_store_name" ON "stock_positions" USING btree ("store_name" text_ops);--> statement-breakpoint
CREATE INDEX "idx_wms_dynamic_rules_active_target" ON "wms_dynamic_rules" USING btree ("is_active" int4_ops,"target_attr" bool_ops,"priority" bool_ops);--> statement-breakpoint
CREATE INDEX "idx_route_stop_deleted_at" ON "route_stop" USING btree ("deleted_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_route_stop_shipment" ON "route_stop" USING btree ("shipment_id" int4_ops);--> statement-breakpoint
CREATE INDEX "idx_route_stop_shipment_seq" ON "route_stop" USING btree ("shipment_id" numeric_ops,"seq_no" numeric_ops);--> statement-breakpoint
CREATE INDEX "idx_rsi_invoice" ON "route_stop_invoice" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "idx_rsi_status" ON "route_stop_invoice" USING btree ("status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_rsi_stop" ON "route_stop_invoice" USING btree ("route_stop_id" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_rsi_active_status" ON "route_stop_invoice" USING btree ("status" text_ops) WHERE (is_active = true);--> statement-breakpoint
CREATE INDEX "ix_rsi_active_stop" ON "route_stop_invoice" USING btree ("route_stop_id" int4_ops) WHERE (is_active = true);--> statement-breakpoint
CREATE UNIQUE INDEX "uq_rsi_active_invoice" ON "route_stop_invoice" USING btree ("invoice_no" text_ops) WHERE (is_active = true);--> statement-breakpoint
CREATE INDEX "idx_invoices_assigned_status" ON "invoices" USING btree ("assigned_to" text_ops,"status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_assigned_to" ON "invoices" USING btree ("assigned_to" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_customer_code_365" ON "invoices" USING btree ("customer_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_deleted_at" ON "invoices" USING btree ("deleted_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_route_lookup" ON "invoices" USING btree ("route_id" int4_ops,"stop_id" int4_ops,"status" text_ops) WHERE (route_id IS NOT NULL);--> statement-breakpoint
CREATE INDEX "idx_invoices_route_status" ON "invoices" USING btree ("route_id" text_ops,"status" text_ops,"status_updated_at" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_routing" ON "invoices" USING btree ("routing" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_status" ON "invoices" USING btree ("status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_status_assigned" ON "invoices" USING btree ("status" text_ops,"assigned_to" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_status_dates" ON "invoices" USING btree ("status" timestamp_ops,"delivered_at" text_ops,"shipped_at" timestamp_ops) WHERE ((status)::text = ANY (ARRAY[('shipped'::character varying)::text, ('delivered'::character varying)::text, ('delivery_failed'::character varying)::text, ('returned_to_warehouse'::character varying)::text, ('cancelled'::character varying)::text]));--> statement-breakpoint
CREATE INDEX "idx_invoices_status_routing" ON "invoices" USING btree ("status" text_ops,"routing" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoices_status_updated" ON "invoices" USING btree ("status" timestamp_ops,"status_updated_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_batch_sessions_active" ON "batch_picking_sessions" USING btree ("status" text_ops,"assigned_to" text_ops) WHERE ((status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Paused'::character varying)::text, ('Created'::character varying)::text]));--> statement-breakpoint
CREATE INDEX "idx_batch_sessions_assigned" ON "batch_picking_sessions" USING btree ("assigned_to" text_ops,"status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_batch_sessions_assigned_to" ON "batch_picking_sessions" USING btree ("assigned_to" text_ops);--> statement-breakpoint
CREATE INDEX "idx_batch_sessions_deleted_at" ON "batch_picking_sessions" USING btree ("deleted_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_batch_sessions_status" ON "batch_picking_sessions" USING btree ("status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_batch_sessions_status_created" ON "batch_picking_sessions" USING btree ("status" text_ops,"created_at" text_ops);--> statement-breakpoint
CREATE INDEX "idx_batch_picked_items_session" ON "batch_picked_items" USING btree ("batch_session_id" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_cod_alloc_invoice" ON "cod_invoice_allocations" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "ix_cod_alloc_pending" ON "cod_invoice_allocations" USING btree ("is_pending" bool_ops) WHERE (is_pending = true);--> statement-breakpoint
CREATE INDEX "ix_cod_alloc_route" ON "cod_invoice_allocations" USING btree ("route_id" int4_ops);--> statement-breakpoint
CREATE INDEX "idx_shipments_deleted_at" ON "shipments" USING btree ("deleted_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_shipments_driver_status" ON "shipments" USING btree ("driver_name" text_ops,"status" timestamp_ops,"updated_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "ix_payment_customers_group" ON "payment_customers" USING btree ("group" text_ops);--> statement-breakpoint
CREATE INDEX "ix_credit_terms_customer_code" ON "credit_terms" USING btree ("customer_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_delivery_slots_customer" ON "customer_delivery_slots" USING btree ("customer_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_delivery_slots_dow_week" ON "customer_delivery_slots" USING btree ("dow" int4_ops,"week_code" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_dd_invoice_status" ON "delivery_discrepancies" USING btree ("invoice_no" text_ops,"status" text_ops,"is_validated" text_ops,"is_resolved" text_ops);--> statement-breakpoint
CREATE INDEX "idx_dwh_hdr_date_customer" ON "dw_invoice_header" USING btree ("invoice_date_utc0" date_ops,"customer_code_365" date_ops);--> statement-breakpoint
CREATE INDEX "idx_dwh_hdr_invoice" ON "dw_invoice_header" USING btree ("invoice_no_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_invoice_header_customer_code_365" ON "dw_invoice_header" USING btree ("customer_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_invoice_header_store_code_365" ON "dw_invoice_header" USING btree ("store_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dwih_customer_date" ON "dw_invoice_header" USING btree ("customer_code_365" date_ops,"invoice_date_utc0" date_ops);--> statement-breakpoint
CREATE INDEX "ix_dwih_customer_invoice" ON "dw_invoice_header" USING btree ("customer_code_365" text_ops,"invoice_no_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dwih_date" ON "dw_invoice_header" USING btree ("invoice_date_utc0" date_ops);--> statement-breakpoint
CREATE INDEX "idx_dwl_invoice_item" ON "dw_invoice_line" USING btree ("invoice_no_365" text_ops,"item_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "idx_dwl_item" ON "dw_invoice_line" USING btree ("item_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_invoice_line_invoice_no_365" ON "dw_invoice_line" USING btree ("invoice_no_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_invoice_line_item_code_365" ON "dw_invoice_line" USING btree ("item_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dw_invoice_line_line_number" ON "dw_invoice_line" USING btree ("line_number" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_dwil_invoice_item" ON "dw_invoice_line" USING btree ("invoice_no_365" text_ops,"item_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_dwil_item_qty" ON "dw_invoice_line" USING btree ("item_code_365" text_ops,"quantity" text_ops);--> statement-breakpoint
CREATE INDEX "idx_ps_cust_reporting_group" ON "ps_customers" USING btree ("reporting_group" text_ops);--> statement-breakpoint
CREATE INDEX "idx_ps_customers_deleted_at" ON "ps_customers" USING btree ("deleted_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_ps_customers_is_active" ON "ps_customers" USING btree ("is_active" bool_ops);--> statement-breakpoint
CREATE INDEX "idx_users_is_active" ON "users" USING btree ("is_active" bool_ops);--> statement-breakpoint
CREATE INDEX "idx_ipdc_status" ON "invoice_post_delivery_cases" USING btree ("status" text_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "uq_ipdc_invoice_open" ON "invoice_post_delivery_cases" USING btree ("invoice_no" text_ops) WHERE ((status)::text = ANY (ARRAY[('OPEN'::character varying)::text, ('INTAKE_RECEIVED'::character varying)::text, ('REROUTE_QUEUED'::character varying)::text]));--> statement-breakpoint
CREATE INDEX "idx_irh_invoice" ON "invoice_route_history" USING btree ("invoice_no" text_ops,"created_at" text_ops);--> statement-breakpoint
CREATE INDEX "idx_item_time_tracking_completed" ON "item_time_tracking" USING btree ("item_completed" timestamp_ops) WHERE (item_completed IS NOT NULL);--> statement-breakpoint
CREATE INDEX "idx_item_time_tracking_invoice_started" ON "item_time_tracking" USING btree ("invoice_no" text_ops,"item_started" text_ops);--> statement-breakpoint
CREATE INDEX "idx_time_tracking_reporting" ON "item_time_tracking" USING btree ("invoice_no" timestamp_ops,"item_started" text_ops,"picker_username" timestamp_ops) WHERE (item_completed IS NOT NULL);--> statement-breakpoint
CREATE INDEX "ix_oi_estimate_runs_invoice_no" ON "oi_estimate_runs" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "ix_oi_estimate_lines_invoice_no" ON "oi_estimate_lines" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "ix_oi_estimate_lines_item_code" ON "oi_estimate_lines" USING btree ("item_code" text_ops);--> statement-breakpoint
CREATE INDEX "ix_oi_estimate_lines_run_id" ON "oi_estimate_lines" USING btree ("run_id" int4_ops);--> statement-breakpoint
CREATE INDEX "idx_picking_exceptions_invoice" ON "picking_exceptions" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "idx_picking_exceptions_invoice_no" ON "picking_exceptions" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "idx_purchase_orders_deleted_at" ON "purchase_orders" USING btree ("deleted_at" timestamp_ops);--> statement-breakpoint
CREATE INDEX "idx_purchase_orders_is_archived" ON "purchase_orders" USING btree ("is_archived" bool_ops);--> statement-breakpoint
CREATE INDEX "ix_purchase_orders_code_365" ON "purchase_orders" USING btree ("code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_purchase_orders_shopping_cart_code" ON "purchase_orders" USING btree ("shopping_cart_code" text_ops);--> statement-breakpoint
CREATE INDEX "idx_purchase_order_lines_line_id_365" ON "purchase_order_lines" USING btree ("line_id_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_purchase_order_lines_item_code_365" ON "purchase_order_lines" USING btree ("item_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_receipt_log_customer_code_365" ON "receipt_log" USING btree ("customer_code_365" text_ops);--> statement-breakpoint
CREATE INDEX "ix_receipt_log_reference_number" ON "receipt_log" USING btree ("reference_number" text_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "ix_receiving_sessions_receipt_code" ON "receiving_sessions" USING btree ("receipt_code" text_ops);--> statement-breakpoint
CREATE INDEX "idx_rr_status" ON "reroute_requests" USING btree ("status" text_ops);--> statement-breakpoint
CREATE INDEX "ix_return_handover_driver_pending" ON "route_return_handover" USING btree ("route_id" int4_ops,"driver_confirmed_at" int4_ops,"warehouse_received_at" timestamp_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "ux_return_handover_route_invoice" ON "route_return_handover" USING btree ("route_id" int4_ops,"invoice_no" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_pallet_order_pallet_id" ON "wms_pallet_order" USING btree ("pallet_id" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_payment_entries_route_stop_id" ON "payment_entries" USING btree ("route_stop_id" int4_ops);--> statement-breakpoint
CREATE INDEX "ix_payment_entries_stop" ON "payment_entries" USING btree ("route_stop_id" int4_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "uq_payment_entries_active" ON "payment_entries" USING btree ("route_stop_id" int4_ops) WHERE (is_active = true);--> statement-breakpoint
CREATE INDEX "idx_cod_receipts_client_request_id" ON "cod_receipts" USING btree ("client_request_id" text_ops);--> statement-breakpoint
CREATE INDEX "idx_cod_receipts_doc_type" ON "cod_receipts" USING btree ("doc_type" text_ops);--> statement-breakpoint
CREATE INDEX "idx_cod_receipts_status" ON "cod_receipts" USING btree ("status" text_ops);--> statement-breakpoint
CREATE INDEX "ix_bank_transactions_batch_id" ON "bank_transactions" USING btree ("batch_id" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_batch_lock" ON "invoice_items" USING btree ("locked_by_batch_id" int4_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_batch_zone" ON "invoice_items" USING btree ("zone" int4_ops,"corridor" int4_ops,"locked_by_batch_id" int4_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_corridor" ON "invoice_items" USING btree ("corridor" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_invoice_no" ON "invoice_items" USING btree ("invoice_no" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_invoice_picked" ON "invoice_items" USING btree ("invoice_no" bool_ops,"is_picked" bool_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_invoice_status" ON "invoice_items" USING btree ("invoice_no" text_ops,"pick_status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_is_picked" ON "invoice_items" USING btree ("is_picked" bool_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_location" ON "invoice_items" USING btree ("zone" text_ops,"corridor" text_ops,"location" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_location_sort" ON "invoice_items" USING btree ("invoice_no" text_ops,"zone" text_ops,"corridor" text_ops,"location" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_pick_status" ON "invoice_items" USING btree ("pick_status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_picked" ON "invoice_items" USING btree ("is_picked" bool_ops,"picked_qty" int4_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_picking_performance" ON "invoice_items" USING btree ("invoice_no" int4_ops,"is_picked" int4_ops,"pick_status" int4_ops,"locked_by_batch_id" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_zone" ON "invoice_items" USING btree ("zone" text_ops);--> statement-breakpoint
CREATE INDEX "idx_invoice_items_zone_corridor" ON "invoice_items" USING btree ("zone" text_ops,"corridor" text_ops);--> statement-breakpoint
CREATE INDEX "idx_items_batch_eligible" ON "invoice_items" USING btree ("zone" text_ops,"corridor" text_ops,"is_picked" bool_ops,"pick_status" text_ops) WHERE ((is_picked = false) AND ((pick_status)::text = ANY (ARRAY[('not_picked'::character varying)::text, ('reset'::character varying)::text, ('skipped_pending'::character varying)::text])));--> statement-breakpoint
CREATE INDEX "idx_items_batch_locking" ON "invoice_items" USING btree ("zone" int4_ops,"corridor" int4_ops,"is_picked" int4_ops,"pick_status" bool_ops,"locked_by_batch_id" text_ops,"unit_type" bool_ops) WHERE ((is_picked = false) AND ((pick_status)::text = ANY (ARRAY[('not_picked'::character varying)::text, ('reset'::character varying)::text, ('skipped_pending'::character varying)::text])));--> statement-breakpoint
CREATE INDEX "idx_items_corridor_zone" ON "invoice_items" USING btree ("corridor" text_ops,"zone" text_ops);--> statement-breakpoint
CREATE INDEX "idx_items_invoice_picked_status" ON "invoice_items" USING btree ("invoice_no" bool_ops,"is_picked" bool_ops,"pick_status" text_ops);--> statement-breakpoint
CREATE INDEX "idx_items_zone_status_picked" ON "invoice_items" USING btree ("zone" bool_ops,"pick_status" text_ops,"is_picked" bool_ops);--> statement-breakpoint
CREATE UNIQUE INDEX "uq_invoice_items_invoice_no_item_code" ON "invoice_items" USING btree ("invoice_no" text_ops,"item_code" text_ops);--> statement-breakpoint
CREATE VIEW "public"."dw_sales_lines_v" AS (SELECT h.invoice_date_utc0 AS sale_date, h.customer_code_365, l.item_code_365, l.quantity AS qty, l.line_total_excl AS net_excl FROM dw_invoice_header h JOIN dw_invoice_line l ON l.invoice_no_365::text = h.invoice_no_365::text);--> statement-breakpoint
CREATE VIEW "public"."pbi_dim_customers" AS (SELECT customer_code_365 AS customer_code, company_name AS customer_name, is_company, category_1_name AS customer_category, company_activity_name AS business_activity, agent_name AS sales_agent, town, postal_code, address_line_1, address_line_2, address_line_3, tel_1 AS phone, mobile, vat_registration_number AS vat_no, credit_limit_amount AS credit_limit, latitude, longitude, COALESCE(is_active, true) AS is_active FROM ps_customers c WHERE deleted_at IS NULL);--> statement-breakpoint
CREATE VIEW "public"."pbi_dim_dates" AS (SELECT d::date AS date_key, EXTRACT(year FROM d)::integer AS year, EXTRACT(quarter FROM d)::integer AS quarter, EXTRACT(month FROM d)::integer AS month_no, to_char(d, 'Month'::text) AS month_name, to_char(d, 'Mon'::text) AS month_short, EXTRACT(week FROM d)::integer AS week_no, EXTRACT(dow FROM d)::integer AS day_of_week_no, to_char(d, 'Day'::text) AS day_name, to_char(d, 'YYYY-MM'::text) AS year_month, (to_char(d, 'YYYY'::text) || '-Q'::text) || EXTRACT(quarter FROM d) AS year_quarter, CASE WHEN EXTRACT(dow FROM d) = ANY (ARRAY[0::numeric, 6::numeric]) THEN false ELSE true END AS is_weekday FROM generate_series('2023-01-01'::date::timestamp with time zone, '2027-12-31'::date::timestamp with time zone, '1 day'::interval) d(d));--> statement-breakpoint
CREATE VIEW "public"."pbi_dim_products" AS (SELECT i.item_code_365 AS item_code, i.item_name, COALESCE(i.active, true) AS is_active, i.barcode, i.supplier_item_code, cat.category_name AS category, b.brand_name AS brand, a3.attribute_3_name AS zone_name, i.attribute_1_code_365 AS attribute_1, i.attribute_2_code_365 AS attribute_2, i.attribute_3_code_365 AS zone_code, i.attribute_4_code_365 AS attribute_4, i.attribute_5_code_365 AS attribute_5, i.attribute_6_code_365 AS attribute_6, i.item_weight, i.selling_qty, i.number_of_pieces, i.wms_zone, i.wms_unit_type, i.wms_fragility, i.wms_temperature_sensitivity FROM ps_items_dw i LEFT JOIN dw_item_categories cat ON cat.category_code_365::text = i.category_code_365::text LEFT JOIN dw_brands b ON b.brand_code_365::text = i.brand_code_365::text LEFT JOIN dw_attribute3 a3 ON a3.attribute_3_code_365::text = i.attribute_3_code_365::text);--> statement-breakpoint
CREATE VIEW "public"."pbi_dim_stores" AS (SELECT store_code_365 AS store_code, store_name FROM dw_store s);--> statement-breakpoint
CREATE VIEW "public"."pbi_fact_discrepancies" AS (SELECT id AS discrepancy_id, invoice_no, item_code_expected AS item_code, item_name, qty_expected, qty_actual, discrepancy_type, status AS discrepancy_status, reported_by, reported_at, reported_source, delivery_date, reported_value, warehouse_result, credit_note_required, credit_note_amount, resolution_action, is_validated, is_resolved FROM delivery_discrepancies dd);--> statement-breakpoint
CREATE VIEW "public"."pbi_fact_invoices" AS (SELECT h.invoice_no_365 AS invoice_no, h.invoice_type, h.invoice_date_utc0 AS invoice_date, h.customer_code_365 AS customer_code, h.store_code_365 AS store_code, h.user_code_365 AS salesperson_code, h.total_sub AS total_excl_vat, h.total_discount, COALESCE(h.total_sub, 0::numeric) - COALESCE(h.total_discount, 0::numeric) AS total_net, h.total_vat, h.total_grand AS total_incl_vat, h.points_earned, h.points_redeemed, count(l.id) AS line_count, sum(l.quantity) AS total_qty, EXTRACT(year FROM h.invoice_date_utc0) AS year, EXTRACT(month FROM h.invoice_date_utc0) AS month, EXTRACT(quarter FROM h.invoice_date_utc0) AS quarter, to_char(h.invoice_date_utc0::timestamp with time zone, 'YYYY-MM'::text) AS year_month FROM dw_invoice_header h LEFT JOIN dw_invoice_line l ON l.invoice_no_365::text = h.invoice_no_365::text GROUP BY h.invoice_no_365, h.invoice_type, h.invoice_date_utc0, h.customer_code_365, h.store_code_365, h.user_code_365, h.total_sub, h.total_discount, h.total_vat, h.total_grand, h.points_earned, h.points_redeemed);--> statement-breakpoint
CREATE VIEW "public"."pbi_fact_picking" AS (SELECT invoice_no, customer_name, assigned_to AS picker, status AS order_status, total_lines, total_items, total_weight, picking_complete_time, packing_complete_time, shipped_at, delivered_at, upload_date, customer_code_365 AS customer_code, CASE WHEN picking_complete_time IS NOT NULL AND status_updated_at IS NOT NULL THEN EXTRACT(epoch FROM picking_complete_time - status_updated_at) / 60.0 ELSE NULL::numeric END AS picking_duration_minutes FROM invoices inv WHERE deleted_at IS NULL);--> statement-breakpoint
CREATE VIEW "public"."pbi_fact_route_deliveries" AS (SELECT rsi.route_stop_invoice_id AS delivery_id, s.id AS route_id, s.route_name, s.driver_name, s.delivery_date, rs.route_stop_id AS stop_id, rs.seq_no AS stop_sequence, rs.stop_name, rs.stop_city, rs.customer_code, rsi.invoice_no, rsi.status AS delivery_status, rsi.expected_payment_method, rsi.expected_amount, rsi.discrepancy_value, rsi.weight_kg, rs.delivered_at, rs.failed_at, rs.failure_reason FROM route_stop_invoice rsi JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id JOIN shipments s ON s.id = rs.shipment_id WHERE rs.deleted_at IS NULL AND s.deleted_at IS NULL AND rsi.is_active = true);--> statement-breakpoint
CREATE VIEW "public"."pbi_fact_routes" AS (WITH route_counts AS ( SELECT rs.shipment_id, count(*) FILTER (WHERE rsi.is_active = true) AS invoice_count, count(*) FILTER (WHERE rsi.is_active = true AND rsi.status::text = 'DELIVERED'::text) AS delivered_count, count(*) FILTER (WHERE rsi.is_active = true AND rsi.status::text = 'FAILED'::text) AS failed_count FROM route_stop rs JOIN route_stop_invoice rsi ON rsi.route_stop_id = rs.route_stop_id WHERE rs.deleted_at IS NULL GROUP BY rs.shipment_id ), stop_counts AS ( SELECT route_stop.shipment_id, count(*) AS stop_count FROM route_stop WHERE route_stop.deleted_at IS NULL GROUP BY route_stop.shipment_id ) SELECT s.id AS route_id, s.route_name, s.driver_name, s.status AS route_status, s.delivery_date, s.reconciliation_status, s.is_archived, s.created_at, s.started_at, s.completed_at, s.cash_expected, s.cash_collected, s.cash_handed_in, s.cash_variance, s.returns_count, CASE WHEN s.completed_at IS NOT NULL AND s.started_at IS NOT NULL THEN EXTRACT(epoch FROM s.completed_at - s.started_at) / 60.0 ELSE NULL::numeric END AS duration_minutes, COALESCE(sc.stop_count, 0::bigint) AS stop_count, COALESCE(rc.invoice_count, 0::bigint) AS invoice_count, COALESCE(rc.delivered_count, 0::bigint) AS delivered_count, COALESCE(rc.failed_count, 0::bigint) AS failed_count FROM shipments s LEFT JOIN route_counts rc ON rc.shipment_id = s.id LEFT JOIN stop_counts sc ON sc.shipment_id = s.id WHERE s.deleted_at IS NULL);--> statement-breakpoint
CREATE VIEW "public"."pbi_fact_sales" AS (SELECT l.id AS line_id, h.invoice_no_365 AS invoice_no, h.invoice_type, h.invoice_date_utc0 AS invoice_date, h.customer_code_365 AS customer_code, h.store_code_365 AS store_code, h.user_code_365 AS salesperson_code, l.item_code_365 AS item_code, l.line_number, l.quantity, l.price_excl, l.price_incl, l.discount_percent, l.vat_percent, l.line_total_excl, l.line_total_discount, l.line_total_vat, l.line_total_incl, COALESCE(l.line_total_incl, 0::numeric) - COALESCE(l.line_total_vat, 0::numeric) AS line_net_value, EXTRACT(year FROM h.invoice_date_utc0) AS year, EXTRACT(month FROM h.invoice_date_utc0) AS month, EXTRACT(quarter FROM h.invoice_date_utc0) AS quarter, to_char(h.invoice_date_utc0::timestamp with time zone, 'YYYY-MM'::text) AS year_month, to_char(h.invoice_date_utc0::timestamp with time zone, 'Day'::text) AS day_of_week, EXTRACT(dow FROM h.invoice_date_utc0) AS day_of_week_no FROM dw_invoice_line l JOIN dw_invoice_header h ON h.invoice_no_365::text = l.invoice_no_365::text);--> statement-breakpoint
CREATE VIEW "public"."v_route_stop_invoice_active" AS (SELECT route_stop_invoice_id, route_stop_id, invoice_no, status, weight_kg, notes, is_active, effective_from, effective_to, changed_by FROM route_stop_invoice WHERE is_active = true);--> statement-breakpoint
CREATE VIEW "public"."v_shipment_orders" AS (SELECT rs.shipment_id, rsi.invoice_no FROM route_stop rs JOIN route_stop_invoice rsi ON rsi.route_stop_id = rs.route_stop_id WHERE rsi.is_active = true);--> statement-breakpoint
CREATE MATERIALIZED VIEW "public"."dw_sales_lines_mv" AS (SELECT h.invoice_date_utc0 AS sale_date, h.customer_code_365::text AS customer_code_365, l.item_code_365::text AS item_code_365, l.quantity::numeric AS qty, COALESCE(l.line_total_incl, 0::numeric) - COALESCE(l.line_total_vat, 0::numeric) AS net_excl FROM dw_invoice_header h JOIN dw_invoice_line l ON l.invoice_no_365::text = h.invoice_no_365::text);
*/