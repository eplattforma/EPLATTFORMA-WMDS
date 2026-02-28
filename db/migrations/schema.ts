import { pgTable, index, unique, serial, varchar, timestamp, jsonb, boolean, integer, numeric, doublePrecision, text, uniqueIndex, foreignKey, check, date, bigserial, bigint, json, primaryKey, pgView, pgMaterializedView } from "drizzle-orm/pg-core"
import { sql } from "drizzle-orm"



export const aiFeedbackCache = pgTable("ai_feedback_cache", {
	id: serial().primaryKey().notNull(),
	payloadHash: varchar("payload_hash", { length: 64 }).notNull(),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	expiresAt: timestamp("expires_at", { withTimezone: true, mode: 'string' }).notNull(),
	responseJson: jsonb("response_json").notNull(),
}, (table) => [
	index("idx_ai_feedback_cache_expires").using("btree", table.expiresAt.asc().nullsLast().op("timestamptz_ops")),
	unique("ai_feedback_cache_payload_hash_key").on(table.payloadHash),
]);

export const discrepancyTypes = pgTable("discrepancy_types", {
	id: serial().primaryKey().notNull(),
	name: varchar({ length: 50 }).notNull(),
	displayName: varchar("display_name", { length: 100 }).notNull(),
	isActive: boolean("is_active").default(true).notNull(),
	sortOrder: integer("sort_order").default(0).notNull(),
	deductsFromCollection: boolean("deducts_from_collection").default(true).notNull(),
	cnRequired: boolean("cn_required").default(true).notNull(),
	returnExpected: boolean("return_expected").default(false).notNull(),
	requiresActualItem: boolean("requires_actual_item").default(false).notNull(),
}, (table) => [
	unique("discrepancy_types_name_key").on(table.name),
]);

export const dwAttribute1 = pgTable("dw_attribute1", {
	attribute1Code365: varchar("attribute_1_code_365", { length: 64 }).primaryKey().notNull(),
	attribute1Name: varchar("attribute_1_name", { length: 255 }).notNull(),
	attribute1SecondaryCode: varchar("attribute_1_secondary_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwAttribute2 = pgTable("dw_attribute2", {
	attribute2Code365: varchar("attribute_2_code_365", { length: 64 }).primaryKey().notNull(),
	attribute2Name: varchar("attribute_2_name", { length: 255 }).notNull(),
	attribute2SecondaryCode: varchar("attribute_2_secondary_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwAttribute3 = pgTable("dw_attribute3", {
	attribute3Code365: varchar("attribute_3_code_365", { length: 64 }).primaryKey().notNull(),
	attribute3Name: varchar("attribute_3_name", { length: 255 }).notNull(),
	attribute3SecondaryCode: varchar("attribute_3_secondary_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwAttribute4 = pgTable("dw_attribute4", {
	attribute4Code365: varchar("attribute_4_code_365", { length: 64 }).primaryKey().notNull(),
	attribute4Name: varchar("attribute_4_name", { length: 255 }).notNull(),
	attribute4SecondaryCode: varchar("attribute_4_secondary_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwAttribute5 = pgTable("dw_attribute5", {
	attribute5Code365: varchar("attribute_5_code_365", { length: 64 }).primaryKey().notNull(),
	attribute5Name: varchar("attribute_5_name", { length: 255 }).notNull(),
	attribute5SecondaryCode: varchar("attribute_5_secondary_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwAttribute6 = pgTable("dw_attribute6", {
	attribute6Code365: varchar("attribute_6_code_365", { length: 64 }).primaryKey().notNull(),
	attribute6Name: varchar("attribute_6_name", { length: 255 }).notNull(),
	attribute6SecondaryCode: varchar("attribute_6_secondary_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwBrands = pgTable("dw_brands", {
	brandCode365: varchar("brand_code_365", { length: 64 }).primaryKey().notNull(),
	brandName: varchar("brand_name", { length: 255 }).notNull(),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwCashier = pgTable("dw_cashier", {
	userCode365: varchar("user_code_365", { length: 64 }).primaryKey().notNull(),
	userName: varchar("user_name", { length: 255 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwCategoryPenetration = pgTable("dw_category_penetration", {
	id: serial().primaryKey().notNull(),
	customerCode365: varchar("customer_code_365").notNull(),
	categoryCode: varchar("category_code").notNull(),
	totalSpend: numeric("total_spend", { precision: 12, scale:  2 }).notNull(),
	hasCategory: integer("has_category").notNull(),
}, (table) => [
	index("ix_dw_category_penetration_category_code").using("btree", table.categoryCode.asc().nullsLast().op("text_ops")),
	index("ix_dw_category_penetration_customer_code_365").using("btree", table.customerCode365.asc().nullsLast().op("text_ops")),
]);

export const dwChurnRisk = pgTable("dw_churn_risk", {
	id: serial().primaryKey().notNull(),
	customerCode365: varchar("customer_code_365").notNull(),
	categoryCode: varchar("category_code").notNull(),
	recentSpend: numeric("recent_spend", { precision: 14, scale:  2 }).notNull(),
	prevSpend: numeric("prev_spend", { precision: 14, scale:  2 }).notNull(),
	spendRatio: doublePrecision("spend_ratio").notNull(),
	dropPct: doublePrecision("drop_pct").notNull(),
	churnFlag: integer("churn_flag").notNull(),
}, (table) => [
	index("ix_dw_churn_risk_category_code").using("btree", table.categoryCode.asc().nullsLast().op("text_ops")),
	index("ix_dw_churn_risk_customer_code_365").using("btree", table.customerCode365.asc().nullsLast().op("text_ops")),
]);

export const dwItemCategories = pgTable("dw_item_categories", {
	categoryCode365: varchar("category_code_365", { length: 64 }).primaryKey().notNull(),
	categoryName: varchar("category_name", { length: 255 }).notNull(),
	parentCategoryCode: varchar("parent_category_code", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwRecoBasket = pgTable("dw_reco_basket", {
	id: serial().primaryKey().notNull(),
	fromItemCode: varchar("from_item_code").notNull(),
	toItemCode: varchar("to_item_code").notNull(),
	support: doublePrecision().notNull(),
	confidence: doublePrecision().notNull(),
	lift: doublePrecision(),
}, (table) => [
	index("ix_dw_reco_basket_from_item_code").using("btree", table.fromItemCode.asc().nullsLast().op("text_ops")),
	index("ix_dw_reco_basket_to_item_code").using("btree", table.toItemCode.asc().nullsLast().op("text_ops")),
]);

export const dwSeasons = pgTable("dw_seasons", {
	seasonCode365: varchar("season_code_365", { length: 64 }).primaryKey().notNull(),
	seasonName: varchar("season_name", { length: 255 }).notNull(),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const dwShareOfWallet = pgTable("dw_share_of_wallet", {
	id: serial().primaryKey().notNull(),
	customerCode365: varchar("customer_code_365").notNull(),
	actualSpend: numeric("actual_spend", { precision: 14, scale:  2 }).notNull(),
	avgSpend: numeric("avg_spend", { precision: 14, scale:  2 }).notNull(),
	opportunityGap: numeric("opportunity_gap", { precision: 14, scale:  2 }).notNull(),
}, (table) => [
	unique("dw_share_of_wallet_customer_code_365_key").on(table.customerCode365),
]);

export const dwStore = pgTable("dw_store", {
	storeCode365: varchar("store_code_365", { length: 64 }).primaryKey().notNull(),
	storeName: varchar("store_name", { length: 255 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
});

export const psItemsDw = pgTable("ps_items_dw", {
	itemCode365: varchar("item_code_365", { length: 64 }).primaryKey().notNull(),
	itemName: varchar("item_name", { length: 255 }).notNull(),
	active: boolean().notNull(),
	categoryCode365: varchar("category_code_365", { length: 64 }),
	brandCode365: varchar("brand_code_365", { length: 64 }),
	seasonCode365: varchar("season_code_365", { length: 64 }),
	attribute6Code365: varchar("attribute_6_code_365", { length: 64 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
	attribute1Code365: varchar("attribute_1_code_365", { length: 64 }),
	attribute2Code365: varchar("attribute_2_code_365", { length: 64 }),
	attribute3Code365: varchar("attribute_3_code_365", { length: 64 }),
	attribute4Code365: varchar("attribute_4_code_365", { length: 64 }),
	attribute5Code365: varchar("attribute_5_code_365", { length: 64 }),
	itemLength: numeric("item_length", { precision: 10, scale:  3 }),
	itemWidth: numeric("item_width", { precision: 10, scale:  3 }),
	itemHeight: numeric("item_height", { precision: 10, scale:  3 }),
	itemWeight: numeric("item_weight", { precision: 10, scale:  3 }),
	numberOfPieces: integer("number_of_pieces"),
	sellingQty: numeric("selling_qty", { precision: 10, scale:  3 }),
	wmsZone: varchar("wms_zone", { length: 50 }),
	wmsUnitType: varchar("wms_unit_type", { length: 50 }),
	wmsFragility: varchar("wms_fragility", { length: 20 }),
	wmsStackability: varchar("wms_stackability", { length: 20 }),
	wmsTemperatureSensitivity: varchar("wms_temperature_sensitivity", { length: 30 }),
	wmsPressureSensitivity: varchar("wms_pressure_sensitivity", { length: 20 }),
	wmsShapeType: varchar("wms_shape_type", { length: 30 }),
	wmsSpillRisk: boolean("wms_spill_risk"),
	wmsPickDifficulty: integer("wms_pick_difficulty"),
	wmsShelfHeight: varchar("wms_shelf_height", { length: 20 }),
	wmsBoxFitRule: varchar("wms_box_fit_rule", { length: 30 }),
	wmsClassConfidence: integer("wms_class_confidence"),
	wmsClassSource: varchar("wms_class_source", { length: 30 }),
	wmsClassNotes: text("wms_class_notes"),
	wmsClassifiedAt: timestamp("wms_classified_at", { mode: 'string' }),
	wmsClassEvidence: text("wms_class_evidence"),
	barcode: varchar({ length: 100 }),
	supplierItemCode: varchar("supplier_item_code", { length: 255 }),
	minOrderQty: integer("min_order_qty"),
});

export const postalLookupCache = pgTable("postal_lookup_cache", {
	id: serial().primaryKey().notNull(),
	cacheKey: varchar("cache_key", { length: 256 }),
	requestJson: text("request_json"),
	responseJson: text("response_json"),
	createdAt: timestamp("created_at", { mode: 'string' }),
}, (table) => [
	uniqueIndex("ix_postal_lookup_cache_cache_key").using("btree", table.cacheKey.asc().nullsLast().op("text_ops")),
]);

export const ps365ReservedStock777 = pgTable("ps365_reserved_stock_777", {
	itemCode365: varchar("item_code_365", { length: 64 }).primaryKey().notNull(),
	itemName: varchar("item_name", { length: 255 }).notNull(),
	seasonName: varchar("season_name", { length: 128 }),
	numberOfPieces: integer("number_of_pieces"),
	numberField5Value: integer("number_field_5_value"),
	storeCode365: varchar("store_code_365", { length: 16 }).notNull(),
	stock: numeric({ precision: 18, scale:  4 }).notNull(),
	stockReserved: numeric("stock_reserved", { precision: 18, scale:  4 }).notNull(),
	stockOrdered: numeric("stock_ordered", { precision: 18, scale:  4 }).notNull(),
	availableStock: numeric("available_stock", { precision: 18, scale:  4 }).notNull(),
	syncedAt: timestamp("synced_at", { mode: 'string' }).notNull(),
	supplierItemCode: varchar("supplier_item_code", { length: 255 }),
	barcode: varchar({ length: 100 }),
}, (table) => [
	index("ix_ps365_reserved_stock_777_synced_at").using("btree", table.syncedAt.asc().nullsLast().op("timestamp_ops")),
]);

export const receiptSequence = pgTable("receipt_sequence", {
	id: serial().primaryKey().notNull(),
	lastNumber: integer("last_number").notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }),
});

export const seasonSupplierSettings = pgTable("season_supplier_settings", {
	seasonCode365: varchar("season_code_365", { length: 50 }).primaryKey().notNull(),
	supplierCode: varchar("supplier_code", { length: 50 }),
	emailTo: varchar("email_to", { length: 255 }),
	emailCc: varchar("email_cc", { length: 500 }),
	emailComment: text("email_comment"),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow(),
});

export const settings = pgTable("settings", {
	key: varchar({ length: 100 }).primaryKey().notNull(),
	value: text().notNull(),
});

export const stockPositions = pgTable("stock_positions", {
	id: serial().primaryKey().notNull(),
	itemCode: varchar("item_code", { length: 100 }).notNull(),
	itemDescription: varchar("item_description", { length: 500 }),
	storeCode: varchar("store_code", { length: 50 }).notNull(),
	storeName: varchar("store_name", { length: 200 }).notNull(),
	expiryDate: varchar("expiry_date", { length: 20 }),
	stockQuantity: numeric("stock_quantity", { precision: 12, scale:  4 }).notNull(),
	importedAt: timestamp("imported_at", { mode: 'string' }).notNull(),
}, (table) => [
	index("ix_stock_positions_imported_at").using("btree", table.importedAt.asc().nullsLast().op("timestamp_ops")),
	index("ix_stock_positions_item_code").using("btree", table.itemCode.asc().nullsLast().op("text_ops")),
	index("ix_stock_positions_store_code").using("btree", table.storeCode.asc().nullsLast().op("text_ops")),
	index("ix_stock_positions_store_name").using("btree", table.storeName.asc().nullsLast().op("text_ops")),
]);

export const stockResolutions = pgTable("stock_resolutions", {
	id: serial().primaryKey().notNull(),
	discrepancyType: varchar("discrepancy_type", { length: 50 }).notNull(),
	resolutionName: varchar("resolution_name", { length: 100 }).notNull(),
	isActive: boolean("is_active").default(true).notNull(),
	sortOrder: integer("sort_order").default(0).notNull(),
});

export const syncJobs = pgTable("sync_jobs", {
	id: varchar({ length: 50 }).primaryKey().notNull(),
	jobType: varchar("job_type", { length: 50 }).notNull(),
	params: text(),
	status: varchar({ length: 20 }),
	startedAt: timestamp("started_at", { mode: 'string' }),
	finishedAt: timestamp("finished_at", { mode: 'string' }),
	createdBy: varchar("created_by", { length: 64 }),
	success: boolean(),
	invoicesCreated: integer("invoices_created"),
	invoicesUpdated: integer("invoices_updated"),
	itemsCreated: integer("items_created"),
	itemsUpdated: integer("items_updated"),
	errorCount: integer("error_count"),
	errorMessage: text("error_message"),
	progressCurrent: integer("progress_current"),
	progressTotal: integer("progress_total"),
	progressMessage: varchar("progress_message", { length: 255 }),
});

export const syncState = pgTable("sync_state", {
	key: varchar({ length: 64 }).primaryKey().notNull(),
	value: text().notNull(),
});

export const wmsCategoryDefaults = pgTable("wms_category_defaults", {
	categoryCode365: varchar("category_code_365", { length: 64 }).primaryKey().notNull(),
	defaultZone: varchar("default_zone", { length: 50 }),
	defaultFragility: varchar("default_fragility", { length: 20 }),
	defaultStackability: varchar("default_stackability", { length: 20 }),
	defaultTemperatureSensitivity: varchar("default_temperature_sensitivity", { length: 30 }),
	defaultPressureSensitivity: varchar("default_pressure_sensitivity", { length: 20 }),
	defaultShapeType: varchar("default_shape_type", { length: 30 }),
	defaultSpillRisk: boolean("default_spill_risk"),
	defaultPickDifficulty: integer("default_pick_difficulty"),
	defaultShelfHeight: varchar("default_shelf_height", { length: 20 }),
	defaultBoxFitRule: varchar("default_box_fit_rule", { length: 30 }),
	isActive: boolean("is_active").notNull(),
	notes: text(),
	updatedBy: varchar("updated_by", { length: 100 }),
	updatedAt: timestamp("updated_at", { mode: 'string' }),
	defaultPackMode: varchar("default_pack_mode", { length: 30 }),
});

export const wmsClassificationRuns = pgTable("wms_classification_runs", {
	id: serial().primaryKey().notNull(),
	startedAt: timestamp("started_at", { mode: 'string' }).notNull(),
	finishedAt: timestamp("finished_at", { mode: 'string' }),
	runBy: varchar("run_by", { length: 100 }),
	mode: varchar({ length: 30 }),
	activeItemsScanned: integer("active_items_scanned"),
	itemsUpdated: integer("items_updated"),
	itemsNeedingReview: integer("items_needing_review"),
	notes: text(),
});

export const wmsDynamicRules = pgTable("wms_dynamic_rules", {
	id: serial().primaryKey().notNull(),
	name: varchar({ length: 120 }).notNull(),
	targetAttr: varchar("target_attr", { length: 64 }).notNull(),
	actionValue: varchar("action_value", { length: 100 }).notNull(),
	confidence: integer().notNull(),
	priority: integer().notNull(),
	stopProcessing: boolean("stop_processing").notNull(),
	isActive: boolean("is_active").notNull(),
	conditionJson: text("condition_json").notNull(),
	notes: text(),
	updatedBy: varchar("updated_by", { length: 100 }),
	updatedAt: timestamp("updated_at", { mode: 'string' }),
	actionsJson: text("actions_json"),
}, (table) => [
	index("idx_wms_dynamic_rules_active_target").using("btree", table.isActive.asc().nullsLast().op("int4_ops"), table.targetAttr.asc().nullsLast().op("bool_ops"), table.priority.asc().nullsLast().op("bool_ops")),
]);

export const wmsItemOverrides = pgTable("wms_item_overrides", {
	itemCode365: varchar("item_code_365", { length: 64 }).primaryKey().notNull(),
	zoneOverride: varchar("zone_override", { length: 50 }),
	unitTypeOverride: varchar("unit_type_override", { length: 50 }),
	fragilityOverride: varchar("fragility_override", { length: 20 }),
	stackabilityOverride: varchar("stackability_override", { length: 20 }),
	temperatureSensitivityOverride: varchar("temperature_sensitivity_override", { length: 30 }),
	pressureSensitivityOverride: varchar("pressure_sensitivity_override", { length: 20 }),
	shapeTypeOverride: varchar("shape_type_override", { length: 30 }),
	spillRiskOverride: boolean("spill_risk_override"),
	pickDifficultyOverride: integer("pick_difficulty_override"),
	shelfHeightOverride: varchar("shelf_height_override", { length: 20 }),
	boxFitRuleOverride: varchar("box_fit_rule_override", { length: 30 }),
	overrideReason: text("override_reason"),
	isActive: boolean("is_active").notNull(),
	updatedBy: varchar("updated_by", { length: 100 }),
	updatedAt: timestamp("updated_at", { mode: 'string' }),
	packModeOverride: varchar("pack_mode_override", { length: 30 }),
});

export const wmsPackingProfile = pgTable("wms_packing_profile", {
	itemCode365: varchar("item_code_365", { length: 50 }).primaryKey().notNull(),
	palletRole: varchar("pallet_role", { length: 20 }).notNull(),
	flagsJson: text("flags_json"),
	unitType: varchar("unit_type", { length: 20 }),
	fragility: varchar({ length: 10 }),
	pressureSensitivity: varchar("pressure_sensitivity", { length: 10 }),
	stackability: varchar({ length: 10 }),
	temperatureSensitivity: varchar("temperature_sensitivity", { length: 20 }),
	spillRisk: boolean("spill_risk"),
	boxFitRule: varchar("box_fit_rule", { length: 20 }),
	updatedAt: timestamp("updated_at", { mode: 'string' }).notNull(),
	packMode: varchar("pack_mode", { length: 20 }),
	lossRisk: boolean("loss_risk"),
	cartonTypeHint: varchar("carton_type_hint", { length: 10 }),
	maxCartonWeightKg: numeric("max_carton_weight_kg", { precision: 10, scale:  2 }),
});

export const routeStop = pgTable("route_stop", {
	routeStopId: serial("route_stop_id").primaryKey().notNull(),
	shipmentId: integer("shipment_id").notNull(),
	seqNo: numeric("seq_no", { precision: 10, scale:  2 }).notNull(),
	stopName: text("stop_name"),
	stopAddr: text("stop_addr"),
	stopCity: text("stop_city"),
	stopPostcode: text("stop_postcode"),
	notes: text(),
	windowStart: timestamp("window_start", { mode: 'string' }),
	windowEnd: timestamp("window_end", { mode: 'string' }),
	customerCode: varchar("customer_code", { length: 50 }),
	website: varchar({ length: 500 }),
	phone: varchar({ length: 50 }),
	deliveredAt: timestamp("delivered_at", { mode: 'string' }),
	failedAt: timestamp("failed_at", { mode: 'string' }),
	failureReason: varchar("failure_reason", { length: 100 }),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
}, (table) => [
	index("idx_route_stop_deleted_at").using("btree", table.deletedAt.asc().nullsLast().op("timestamp_ops")),
	index("idx_route_stop_shipment").using("btree", table.shipmentId.asc().nullsLast().op("int4_ops")),
	index("idx_route_stop_shipment_seq").using("btree", table.shipmentId.asc().nullsLast().op("numeric_ops"), table.seqNo.asc().nullsLast().op("numeric_ops")),
	foreignKey({
			columns: [table.shipmentId],
			foreignColumns: [shipments.id],
			name: "route_stop_shipment_id_fkey"
		}).onDelete("cascade"),
	unique("route_stop_shipment_id_seq_no_key").on(table.shipmentId, table.seqNo),
	check("chk_route_stop_completion", sql`NOT ((delivered_at IS NOT NULL) AND (failed_at IS NOT NULL))`),
]);

export const routeStopInvoice = pgTable("route_stop_invoice", {
	routeStopInvoiceId: serial("route_stop_invoice_id").primaryKey().notNull(),
	routeStopId: integer("route_stop_id").notNull(),
	invoiceNo: varchar("invoice_no").notNull(),
	status: varchar(),
	weightKg: doublePrecision("weight_kg"),
	notes: text(),
	isActive: boolean("is_active").default(true).notNull(),
	effectiveFrom: timestamp("effective_from", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	effectiveTo: timestamp("effective_to", { withTimezone: true, mode: 'string' }),
	changedBy: varchar("changed_by", { length: 64 }),
	expectedPaymentMethod: varchar("expected_payment_method", { length: 20 }),
	expectedAmount: numeric("expected_amount", { precision: 12, scale:  2 }),
	manifestLockedAt: timestamp("manifest_locked_at", { mode: 'string' }),
	manifestLockedBy: varchar("manifest_locked_by", { length: 64 }),
	discrepancyValue: numeric("discrepancy_value", { precision: 10, scale:  2 }).default('0'),
}, (table) => [
	index("idx_rsi_invoice").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	index("idx_rsi_status").using("btree", table.status.asc().nullsLast().op("text_ops")),
	index("idx_rsi_stop").using("btree", table.routeStopId.asc().nullsLast().op("int4_ops")),
	index("ix_rsi_active_status").using("btree", table.status.asc().nullsLast().op("text_ops")).where(sql`(is_active = true)`),
	index("ix_rsi_active_stop").using("btree", table.routeStopId.asc().nullsLast().op("int4_ops")).where(sql`(is_active = true)`),
	uniqueIndex("uq_rsi_active_invoice").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")).where(sql`(is_active = true)`),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "route_stop_invoice_invoice_no_fkey"
		}).onDelete("restrict"),
	foreignKey({
			columns: [table.manifestLockedBy],
			foreignColumns: [users.username],
			name: "route_stop_invoice_manifest_locked_by_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "route_stop_invoice_route_stop_id_fkey"
		}).onDelete("cascade"),
]);

export const invoices = pgTable("invoices", {
	invoiceNo: varchar("invoice_no", { length: 50 }).primaryKey().notNull(),
	routing: varchar({ length: 100 }),
	customerName: varchar("customer_name", { length: 200 }),
	uploadDate: varchar("upload_date", { length: 10 }).notNull(),
	assignedTo: varchar("assigned_to", { length: 64 }),
	totalLines: integer("total_lines"),
	totalItems: integer("total_items"),
	totalWeight: doublePrecision("total_weight"),
	totalExpTime: doublePrecision("total_exp_time"),
	status: varchar({ length: 30 }).default('not_started'),
	currentItemIndex: integer("current_item_index"),
	packingCompleteTime: timestamp("packing_complete_time", { mode: 'string' }),
	pickingCompleteTime: timestamp("picking_complete_time", { mode: 'string' }),
	statusUpdatedAt: timestamp("status_updated_at", { mode: 'string' }).default(sql`CURRENT_TIMESTAMP`),
	shippedAt: timestamp("shipped_at", { mode: 'string' }),
	shippedBy: varchar("shipped_by", { length: 64 }),
	deliveredAt: timestamp("delivered_at", { mode: 'string' }),
	undeliveredReason: text("undelivered_reason"),
	customerCode: varchar("customer_code", { length: 50 }),
	routeId: integer("route_id"),
	stopId: integer("stop_id"),
	totalGrand: numeric("total_grand", { precision: 12, scale:  2 }),
	totalSub: numeric("total_sub", { precision: 12, scale:  2 }),
	totalVat: numeric("total_vat", { precision: 12, scale:  2 }),
	ps365SyncedAt: timestamp("ps365_synced_at", { mode: 'string' }),
	customerCode365: varchar("customer_code_365", { length: 50 }),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
}, (table) => [
	index("idx_invoices_assigned_status").using("btree", table.assignedTo.asc().nullsLast().op("text_ops"), table.status.asc().nullsLast().op("text_ops")),
	index("idx_invoices_assigned_to").using("btree", table.assignedTo.asc().nullsLast().op("text_ops")),
	index("idx_invoices_customer_code_365").using("btree", table.customerCode365.asc().nullsLast().op("text_ops")),
	index("idx_invoices_deleted_at").using("btree", table.deletedAt.asc().nullsLast().op("timestamp_ops")),
	index("idx_invoices_route_lookup").using("btree", table.routeId.asc().nullsLast().op("int4_ops"), table.stopId.asc().nullsLast().op("int4_ops"), table.status.asc().nullsLast().op("text_ops")).where(sql`(route_id IS NOT NULL)`),
	index("idx_invoices_route_status").using("btree", table.routeId.asc().nullsLast().op("text_ops"), table.status.asc().nullsLast().op("text_ops"), table.statusUpdatedAt.desc().nullsFirst().op("text_ops")),
	index("idx_invoices_routing").using("btree", table.routing.asc().nullsLast().op("text_ops")),
	index("idx_invoices_status").using("btree", table.status.asc().nullsLast().op("text_ops")),
	index("idx_invoices_status_assigned").using("btree", table.status.asc().nullsLast().op("text_ops"), table.assignedTo.asc().nullsLast().op("text_ops")),
	index("idx_invoices_status_dates").using("btree", table.status.asc().nullsLast().op("timestamp_ops"), table.deliveredAt.asc().nullsLast().op("text_ops"), table.shippedAt.asc().nullsLast().op("timestamp_ops")).where(sql`((status)::text = ANY (ARRAY[('shipped'::character varying)::text, ('delivered'::character varying)::text, ('delivery_failed'::character varying)::text, ('returned_to_warehouse'::character varying)::text, ('cancelled'::character varying)::text]))`),
	index("idx_invoices_status_routing").using("btree", table.status.asc().nullsLast().op("text_ops"), table.routing.asc().nullsLast().op("text_ops")),
	index("idx_invoices_status_updated").using("btree", table.status.asc().nullsLast().op("timestamp_ops"), table.statusUpdatedAt.asc().nullsLast().op("timestamp_ops")),
	foreignKey({
			columns: [table.shippedBy],
			foreignColumns: [users.username],
			name: "fk_invoices_shipped_by"
		}),
	foreignKey({
			columns: [table.assignedTo],
			foreignColumns: [users.username],
			name: "invoices_assigned_to_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "invoices_route_id_fkey"
		}),
	foreignKey({
			columns: [table.stopId],
			foreignColumns: [routeStop.routeStopId],
			name: "invoices_stop_id_fkey"
		}),
]);

export const activityLogs = pgTable("activity_logs", {
	id: serial().primaryKey().notNull(),
	pickerUsername: varchar("picker_username", { length: 64 }),
	timestamp: timestamp({ mode: 'string' }),
	activityType: varchar("activity_type", { length: 50 }),
	invoiceNo: varchar("invoice_no", { length: 50 }),
	itemCode: varchar("item_code", { length: 50 }),
	details: text(),
}, (table) => [
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "activity_logs_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.pickerUsername],
			foreignColumns: [users.username],
			name: "activity_logs_picker_username_fkey"
		}),
]);

export const batchPickingSessions = pgTable("batch_picking_sessions", {
	id: serial().primaryKey().notNull(),
	name: varchar({ length: 100 }).notNull(),
	zones: varchar({ length: 500 }).notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }),
	createdBy: varchar("created_by", { length: 64 }).notNull(),
	assignedTo: varchar("assigned_to", { length: 64 }),
	status: varchar({ length: 20 }),
	currentItemIndex: integer("current_item_index"),
	pickingMode: varchar("picking_mode", { length: 20 }).default('Sequential'),
	currentInvoiceIndex: integer("current_invoice_index").default(0),
	batchNumber: varchar("batch_number", { length: 20 }),
	corridors: varchar({ length: 500 }),
	unitTypes: varchar("unit_types", { length: 500 }).default(sql`NULL`),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
}, (table) => [
	index("idx_batch_sessions_active").using("btree", table.status.asc().nullsLast().op("text_ops"), table.assignedTo.asc().nullsLast().op("text_ops")).where(sql`((status)::text = ANY (ARRAY[('Active'::character varying)::text, ('Paused'::character varying)::text, ('Created'::character varying)::text]))`),
	index("idx_batch_sessions_assigned").using("btree", table.assignedTo.asc().nullsLast().op("text_ops"), table.status.asc().nullsLast().op("text_ops")),
	index("idx_batch_sessions_assigned_to").using("btree", table.assignedTo.asc().nullsLast().op("text_ops")),
	index("idx_batch_sessions_deleted_at").using("btree", table.deletedAt.asc().nullsLast().op("timestamp_ops")),
	index("idx_batch_sessions_status").using("btree", table.status.asc().nullsLast().op("text_ops")),
	index("idx_batch_sessions_status_created").using("btree", table.status.asc().nullsLast().op("text_ops"), table.createdAt.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.assignedTo],
			foreignColumns: [users.username],
			name: "batch_picking_sessions_assigned_to_fkey"
		}),
	foreignKey({
			columns: [table.createdBy],
			foreignColumns: [users.username],
			name: "batch_picking_sessions_created_by_fkey"
		}),
	unique("batch_picking_sessions_batch_number_key").on(table.batchNumber),
]);

export const batchPickedItems = pgTable("batch_picked_items", {
	id: serial().primaryKey().notNull(),
	batchSessionId: integer("batch_session_id").notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	itemCode: varchar("item_code", { length: 50 }).notNull(),
	pickedQty: integer("picked_qty").notNull(),
	timestamp: timestamp({ mode: 'string' }),
}, (table) => [
	index("idx_batch_picked_items_session").using("btree", table.batchSessionId.asc().nullsLast().op("int4_ops")),
	foreignKey({
			columns: [table.batchSessionId],
			foreignColumns: [batchPickingSessions.id],
			name: "batch_picked_items_batch_session_id_fkey"
		}),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "batch_picked_items_invoice_no_fkey"
		}),
	unique("uq_batch_picked_items_unique").on(table.batchSessionId, table.invoiceNo, table.itemCode),
]);

export const codInvoiceAllocations = pgTable("cod_invoice_allocations", {
	id: serial().primaryKey().notNull(),
	codReceiptId: integer("cod_receipt_id"),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	routeId: integer("route_id").notNull(),
	expectedAmount: numeric("expected_amount", { precision: 12, scale:  2 }).default('0').notNull(),
	receivedAmount: numeric("received_amount", { precision: 12, scale:  2 }).default('0').notNull(),
	deductAmount: numeric("deduct_amount", { precision: 12, scale:  2 }).default('0').notNull(),
	paymentMethod: varchar("payment_method", { length: 30 }).default('cash').notNull(),
	isPending: boolean("is_pending").default(false).notNull(),
	chequeNumber: varchar("cheque_number", { length: 50 }),
	chequeDate: date("cheque_date"),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("ix_cod_alloc_invoice").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	index("ix_cod_alloc_pending").using("btree", table.isPending.asc().nullsLast().op("bool_ops")).where(sql`(is_pending = true)`),
	index("ix_cod_alloc_route").using("btree", table.routeId.asc().nullsLast().op("int4_ops")),
	foreignKey({
			columns: [table.codReceiptId],
			foreignColumns: [codReceipts.id],
			name: "cod_invoice_allocations_cod_receipt_id_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "cod_invoice_allocations_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "cod_invoice_allocations_route_id_fkey"
		}),
]);

export const shipments = pgTable("shipments", {
	id: serial().primaryKey().notNull(),
	driverName: varchar("driver_name", { length: 100 }).notNull(),
	routeName: varchar("route_name", { length: 100 }),
	status: varchar({ length: 20 }).notNull(),
	deliveryDate: date("delivery_date").notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }),
	updatedAt: timestamp("updated_at", { mode: 'string' }).defaultNow(),
	startedAt: timestamp("started_at", { mode: 'string' }),
	completedAt: timestamp("completed_at", { mode: 'string' }),
	settlementStatus: varchar("settlement_status", { length: 20 }).default('PENDING'),
	driverSubmittedAt: timestamp("driver_submitted_at", { mode: 'string' }),
	cashExpected: numeric("cash_expected", { precision: 12, scale:  2 }),
	cashHandedIn: numeric("cash_handed_in", { precision: 12, scale:  2 }),
	cashVariance: numeric("cash_variance", { precision: 12, scale:  2 }),
	cashVarianceNote: text("cash_variance_note"),
	returnsCount: integer("returns_count").default(0),
	returnsWeight: doublePrecision("returns_weight"),
	settlementNotes: text("settlement_notes"),
	completionReason: varchar("completion_reason", { length: 50 }),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
	reconciliationStatus: varchar("reconciliation_status", { length: 20 }).default('NOT_READY'),
	reconciledAt: timestamp("reconciled_at", { mode: 'string' }),
	reconciledBy: varchar("reconciled_by", { length: 64 }),
	isArchived: boolean("is_archived").default(false).notNull(),
	archivedAt: timestamp("archived_at", { mode: 'string' }),
	archivedBy: varchar("archived_by", { length: 64 }),
	cashCollected: numeric("cash_collected", { precision: 12, scale:  2 }),
	settlementClearedAt: timestamp("settlement_cleared_at", { mode: 'string' }),
	settlementClearedBy: varchar("settlement_cleared_by", { length: 64 }),
}, (table) => [
	index("idx_shipments_deleted_at").using("btree", table.deletedAt.asc().nullsLast().op("timestamp_ops")),
	index("idx_shipments_driver_status").using("btree", table.driverName.asc().nullsLast().op("text_ops"), table.status.asc().nullsLast().op("timestamp_ops"), table.updatedAt.desc().nullsFirst().op("timestamp_ops")),
]);

export const paymentCustomers = pgTable("payment_customers", {
	id: serial().primaryKey().notNull(),
	code: varchar({ length: 50 }).notNull(),
	name: varchar({ length: 255 }).notNull(),
	group: varchar({ length: 100 }),
}, (table) => [
	index("ix_payment_customers_group").using("btree", table.group.asc().nullsLast().op("text_ops")),
]);

export const creditTerms = pgTable("credit_terms", {
	id: serial().primaryKey().notNull(),
	customerCode: varchar("customer_code", { length: 50 }).notNull(),
	termsCode: varchar("terms_code", { length: 50 }).notNull(),
	dueDays: integer("due_days").notNull(),
	isCredit: boolean("is_credit").notNull(),
	creditLimit: numeric("credit_limit", { precision: 12, scale:  2 }),
	allowCash: boolean("allow_cash"),
	allowCardPos: boolean("allow_card_pos"),
	allowBankTransfer: boolean("allow_bank_transfer"),
	allowCheque: boolean("allow_cheque"),
	chequeDaysAllowed: integer("cheque_days_allowed"),
	minCashAllowed: integer("min_cash_allowed"),
	maxCashAllowed: integer("max_cash_allowed"),
	notesForDriver: text("notes_for_driver"),
	validFrom: date("valid_from"),
	validTo: date("valid_to"),
}, (table) => [
	index("ix_credit_terms_customer_code").using("btree", table.customerCode.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.customerCode],
			foreignColumns: [paymentCustomers.code],
			name: "credit_terms_customer_code_fkey"
		}),
	unique("uniq_terms_version").on(table.customerCode, table.validFrom),
]);

export const customerDeliverySlots = pgTable("customer_delivery_slots", {
	id: serial().primaryKey().notNull(),
	customerCode365: varchar("customer_code_365", { length: 50 }).notNull(),
	dow: integer().notNull(),
	weekCode: integer("week_code").notNull(),
}, (table) => [
	index("ix_delivery_slots_customer").using("btree", table.customerCode365.asc().nullsLast().op("text_ops")),
	index("ix_delivery_slots_dow_week").using("btree", table.dow.asc().nullsLast().op("int4_ops"), table.weekCode.asc().nullsLast().op("int4_ops")),
	foreignKey({
			columns: [table.customerCode365],
			foreignColumns: [psCustomers.customerCode365],
			name: "customer_delivery_slots_customer_code_365_fkey"
		}).onDelete("cascade"),
	unique("customer_delivery_slots_customer_code_365_dow_week_code_key").on(table.customerCode365, table.dow, table.weekCode),
]);

export const deliveryDiscrepancies = pgTable("delivery_discrepancies", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	itemCodeExpected: varchar("item_code_expected", { length: 50 }).notNull(),
	itemName: varchar("item_name", { length: 200 }),
	qtyExpected: integer("qty_expected").notNull(),
	qtyActual: numeric("qty_actual", { precision: 10, scale:  2 }),
	discrepancyType: varchar("discrepancy_type", { length: 50 }).notNull(),
	reportedBy: varchar("reported_by", { length: 64 }).notNull(),
	reportedAt: timestamp("reported_at", { mode: 'string' }).notNull(),
	reportedSource: varchar("reported_source", { length: 50 }),
	status: varchar({ length: 20 }).notNull(),
	validatedBy: varchar("validated_by", { length: 64 }),
	validatedAt: timestamp("validated_at", { mode: 'string' }),
	resolvedBy: varchar("resolved_by", { length: 64 }),
	resolvedAt: timestamp("resolved_at", { mode: 'string' }),
	resolutionAction: varchar("resolution_action", { length: 50 }),
	note: text(),
	photoPaths: text("photo_paths"),
	pickerUsername: varchar("picker_username", { length: 64 }),
	pickedAt: timestamp("picked_at", { mode: 'string' }),
	deliveryDate: date("delivery_date"),
	shelfCode365: varchar("shelf_code_365", { length: 50 }),
	location: varchar({ length: 100 }),
	isValidated: boolean("is_validated").default(false).notNull(),
	isResolved: boolean("is_resolved").default(false).notNull(),
	actualItemId: integer("actual_item_id"),
	actualItemCode: text("actual_item_code"),
	actualItemName: text("actual_item_name"),
	actualQty: numeric("actual_qty", { precision: 12, scale:  3 }),
	actualBarcode: text("actual_barcode"),
	warehouseCheckedBy: varchar("warehouse_checked_by", { length: 64 }),
	warehouseCheckedAt: timestamp("warehouse_checked_at", { mode: 'string' }),
	warehouseResult: varchar("warehouse_result", { length: 30 }),
	warehouseNote: text("warehouse_note"),
	creditNoteRequired: boolean("credit_note_required").default(false),
	creditNoteNo: varchar("credit_note_no", { length: 50 }),
	creditNoteAmount: numeric("credit_note_amount", { precision: 12, scale:  2 }),
	creditNoteCreatedAt: timestamp("credit_note_created_at", { mode: 'string' }),
	reportedValue: numeric("reported_value", { precision: 12, scale:  2 }),
	deductAmount: numeric("deduct_amount", { precision: 12, scale:  2 }).default('0').notNull(),
}, (table) => [
	index("ix_dd_invoice_status").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops"), table.status.asc().nullsLast().op("text_ops"), table.isValidated.asc().nullsLast().op("text_ops"), table.isResolved.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "delivery_discrepancies_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.reportedBy],
			foreignColumns: [users.username],
			name: "delivery_discrepancies_reported_by_fkey"
		}),
	foreignKey({
			columns: [table.resolvedBy],
			foreignColumns: [users.username],
			name: "delivery_discrepancies_resolved_by_fkey"
		}),
	foreignKey({
			columns: [table.validatedBy],
			foreignColumns: [users.username],
			name: "delivery_discrepancies_validated_by_fkey"
		}),
	foreignKey({
			columns: [table.warehouseCheckedBy],
			foreignColumns: [users.username],
			name: "delivery_discrepancies_warehouse_checked_by_fkey"
		}),
]);

export const deliveryDiscrepancyEvents = pgTable("delivery_discrepancy_events", {
	id: serial().primaryKey().notNull(),
	discrepancyId: integer("discrepancy_id").notNull(),
	eventType: varchar("event_type", { length: 50 }).notNull(),
	actor: varchar({ length: 64 }).notNull(),
	timestamp: timestamp({ mode: 'string' }).notNull(),
	note: text(),
	oldValue: text("old_value"),
	newValue: text("new_value"),
}, (table) => [
	foreignKey({
			columns: [table.actor],
			foreignColumns: [users.username],
			name: "delivery_discrepancy_events_actor_fkey"
		}),
	foreignKey({
			columns: [table.discrepancyId],
			foreignColumns: [deliveryDiscrepancies.id],
			name: "delivery_discrepancy_events_discrepancy_id_fkey"
		}),
]);

export const deliveryEvents = pgTable("delivery_events", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	action: varchar({ length: 30 }).notNull(),
	actor: varchar({ length: 64 }).notNull(),
	timestamp: timestamp({ mode: 'string' }).notNull(),
	reason: text(),
}, (table) => [
	foreignKey({
			columns: [table.actor],
			foreignColumns: [users.username],
			name: "delivery_events_actor_fkey"
		}),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "delivery_events_invoice_no_fkey"
		}),
]);

export const deliveryLines = pgTable("delivery_lines", {
	id: serial().primaryKey().notNull(),
	routeId: integer("route_id").notNull(),
	routeStopId: integer("route_stop_id").notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	itemCode: varchar("item_code", { length: 50 }).notNull(),
	qtyOrdered: numeric("qty_ordered", { precision: 10, scale:  2 }).notNull(),
	qtyDelivered: numeric("qty_delivered", { precision: 10, scale:  2 }).notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
}, (table) => [
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "delivery_lines_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "delivery_lines_route_id_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "delivery_lines_route_stop_id_fkey"
		}),
]);

export const dwInvoiceHeader = pgTable("dw_invoice_header", {
	invoiceNo365: varchar("invoice_no_365", { length: 64 }).primaryKey().notNull(),
	invoiceType: varchar("invoice_type", { length: 64 }).notNull(),
	invoiceDateUtc0: date("invoice_date_utc0").notNull(),
	customerCode365: varchar("customer_code_365", { length: 64 }),
	storeCode365: varchar("store_code_365", { length: 64 }),
	userCode365: varchar("user_code_365", { length: 64 }),
	totalSub: numeric("total_sub", { precision: 18, scale:  4 }),
	totalDiscount: numeric("total_discount", { precision: 18, scale:  4 }),
	totalVat: numeric("total_vat", { precision: 18, scale:  4 }),
	totalGrand: numeric("total_grand", { precision: 18, scale:  4 }),
	pointsEarned: numeric("points_earned", { precision: 18, scale:  2 }),
	pointsRedeemed: numeric("points_redeemed", { precision: 18, scale:  2 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
	totalNet: numeric("total_net", { precision: 18, scale:  4 }),
}, (table) => [
	index("idx_dwh_hdr_date_customer").using("btree", table.invoiceDateUtc0.asc().nullsLast().op("date_ops"), table.customerCode365.asc().nullsLast().op("date_ops")),
	index("idx_dwh_hdr_invoice").using("btree", table.invoiceNo365.asc().nullsLast().op("text_ops")),
	index("ix_dw_invoice_header_customer_code_365").using("btree", table.customerCode365.asc().nullsLast().op("text_ops")),
	index("ix_dw_invoice_header_store_code_365").using("btree", table.storeCode365.asc().nullsLast().op("text_ops")),
	index("ix_dwih_customer_date").using("btree", table.customerCode365.asc().nullsLast().op("date_ops"), table.invoiceDateUtc0.asc().nullsLast().op("date_ops")),
	index("ix_dwih_customer_invoice").using("btree", table.customerCode365.asc().nullsLast().op("text_ops"), table.invoiceNo365.asc().nullsLast().op("text_ops")),
	index("ix_dwih_date").using("btree", table.invoiceDateUtc0.asc().nullsLast().op("date_ops")),
]);

export const dwInvoiceLine = pgTable("dw_invoice_line", {
	id: serial().primaryKey().notNull(),
	invoiceNo365: varchar("invoice_no_365", { length: 64 }).notNull(),
	lineNumber: integer("line_number").notNull(),
	itemCode365: varchar("item_code_365", { length: 64 }),
	quantity: numeric({ precision: 18, scale:  4 }),
	priceExcl: numeric("price_excl", { precision: 18, scale:  4 }),
	priceIncl: numeric("price_incl", { precision: 18, scale:  4 }),
	discountPercent: numeric("discount_percent", { precision: 18, scale:  4 }),
	vatCode365: varchar("vat_code_365", { length: 20 }),
	vatPercent: numeric("vat_percent", { precision: 6, scale:  4 }),
	lineTotalExcl: numeric("line_total_excl", { precision: 18, scale:  4 }),
	lineTotalDiscount: numeric("line_total_discount", { precision: 18, scale:  4 }),
	lineTotalVat: numeric("line_total_vat", { precision: 18, scale:  4 }),
	lineTotalIncl: numeric("line_total_incl", { precision: 18, scale:  4 }),
	attrHash: varchar("attr_hash", { length: 32 }).notNull(),
	lastSyncAt: timestamp("last_sync_at", { mode: 'string' }).notNull(),
	lineNetValue: numeric("line_net_value", { precision: 18, scale:  4 }),
}, (table) => [
	index("idx_dwl_invoice_item").using("btree", table.invoiceNo365.asc().nullsLast().op("text_ops"), table.itemCode365.asc().nullsLast().op("text_ops")),
	index("idx_dwl_item").using("btree", table.itemCode365.asc().nullsLast().op("text_ops")),
	index("ix_dw_invoice_line_invoice_no_365").using("btree", table.invoiceNo365.asc().nullsLast().op("text_ops")),
	index("ix_dw_invoice_line_item_code_365").using("btree", table.itemCode365.asc().nullsLast().op("text_ops")),
	index("ix_dw_invoice_line_line_number").using("btree", table.lineNumber.asc().nullsLast().op("int4_ops")),
	index("ix_dwil_invoice_item").using("btree", table.invoiceNo365.asc().nullsLast().op("text_ops"), table.itemCode365.asc().nullsLast().op("text_ops")),
	index("ix_dwil_item_qty").using("btree", table.itemCode365.asc().nullsLast().op("text_ops"), table.quantity.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.invoiceNo365],
			foreignColumns: [dwInvoiceHeader.invoiceNo365],
			name: "dw_invoice_line_invoice_no_365_fkey"
		}),
	unique("unique_invoice_line").on(table.invoiceNo365, table.lineNumber),
]);

export const shifts = pgTable("shifts", {
	id: serial().primaryKey().notNull(),
	pickerUsername: varchar("picker_username", { length: 64 }).notNull(),
	checkInTime: timestamp("check_in_time", { mode: 'string' }).notNull(),
	checkOutTime: timestamp("check_out_time", { mode: 'string' }),
	checkInCoordinates: varchar("check_in_coordinates", { length: 100 }),
	checkOutCoordinates: varchar("check_out_coordinates", { length: 100 }),
	totalDurationMinutes: integer("total_duration_minutes"),
	status: varchar({ length: 20 }),
	adminAdjusted: boolean("admin_adjusted"),
	adjustmentNote: text("adjustment_note"),
	adjustmentBy: varchar("adjustment_by", { length: 64 }),
	adjustmentTime: timestamp("adjustment_time", { mode: 'string' }),
}, (table) => [
	foreignKey({
			columns: [table.adjustmentBy],
			foreignColumns: [users.username],
			name: "shifts_adjustment_by_fkey"
		}),
	foreignKey({
			columns: [table.pickerUsername],
			foreignColumns: [users.username],
			name: "shifts_picker_username_fkey"
		}),
]);

export const idlePeriods = pgTable("idle_periods", {
	id: serial().primaryKey().notNull(),
	shiftId: integer("shift_id").notNull(),
	startTime: timestamp("start_time", { mode: 'string' }).notNull(),
	endTime: timestamp("end_time", { mode: 'string' }),
	durationMinutes: integer("duration_minutes"),
	isBreak: boolean("is_break"),
	breakReason: varchar("break_reason", { length: 200 }),
}, (table) => [
	foreignKey({
			columns: [table.shiftId],
			foreignColumns: [shifts.id],
			name: "idle_periods_shift_id_fkey"
		}),
]);

export const psCustomers = pgTable("ps_customers", {
	customerCode365: varchar("customer_code_365", { length: 50 }).primaryKey().notNull(),
	customerCodeSecondary: text("customer_code_secondary"),
	isCompany: boolean("is_company"),
	companyName: text("company_name"),
	storeCode365: text("store_code_365"),
	active: boolean().notNull(),
	tel1: text("tel_1"),
	mobile: text(),
	sms: text(),
	website: text(),
	categoryCode1365: text("category_code_1_365"),
	category1Name: text("category_1_name"),
	categoryCode2365: text("category_code_2_365"),
	category2Name: text("category_2_name"),
	companyActivityCode365: text("company_activity_code_365"),
	companyActivityName: text("company_activity_name"),
	creditLimitAmount: doublePrecision("credit_limit_amount"),
	vatRegistrationNumber: text("vat_registration_number"),
	addressLine1: text("address_line_1"),
	addressLine2: text("address_line_2"),
	addressLine3: text("address_line_3"),
	postalCode: text("postal_code"),
	town: text(),
	contactLastName: text("contact_last_name"),
	contactFirstName: text("contact_first_name"),
	agentCode365: text("agent_code_365"),
	agentName: text("agent_name"),
	lastSyncedAt: timestamp("last_synced_at", { mode: 'string' }),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
	isActive: boolean("is_active").default(true).notNull(),
	disabledAt: timestamp("disabled_at", { mode: 'string' }),
	disabledReason: varchar("disabled_reason", { length: 255 }),
	latitude: doublePrecision(),
	longitude: doublePrecision(),
	reportingGroup: text("reporting_group"),
	deliveryDays: text("delivery_days"),
	deliveryDaysStatus: varchar("delivery_days_status", { length: 20 }).default('EMPTY'),
	deliveryDaysInvalidTokens: text("delivery_days_invalid_tokens"),
	deliveryDaysParsedAt: timestamp("delivery_days_parsed_at", { withTimezone: true, mode: 'string' }),
	email: text(),
}, (table) => [
	index("idx_ps_cust_reporting_group").using("btree", table.reportingGroup.asc().nullsLast().op("text_ops")),
	index("idx_ps_customers_deleted_at").using("btree", table.deletedAt.asc().nullsLast().op("timestamp_ops")),
	index("idx_ps_customers_is_active").using("btree", table.isActive.asc().nullsLast().op("bool_ops")),
]);

export const users = pgTable("users", {
	username: varchar({ length: 64 }).primaryKey().notNull(),
	password: varchar({ length: 256 }).notNull(),
	role: varchar({ length: 20 }).notNull(),
	paymentTypeCode365: varchar("payment_type_code_365", { length: 50 }),
	requireGpsCheck: boolean("require_gps_check").default(true),
	disabledAt: timestamp("disabled_at", { mode: 'string' }),
	disabledReason: varchar("disabled_reason", { length: 255 }),
	isActive: boolean("is_active").default(true).notNull(),
	chequePaymentTypeCode365: varchar("cheque_payment_type_code_365", { length: 50 }),
}, (table) => [
	index("idx_users_is_active").using("btree", table.isActive.asc().nullsLast().op("bool_ops")),
]);

export const invoiceDeliveryEvents = pgTable("invoice_delivery_events", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	action: varchar({ length: 30 }).notNull(),
	actor: varchar({ length: 64 }).notNull(),
	timestamp: timestamp({ mode: 'string' }).notNull(),
	reason: text(),
}, (table) => [
	foreignKey({
			columns: [table.actor],
			foreignColumns: [users.username],
			name: "invoice_delivery_events_actor_fkey"
		}),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "invoice_delivery_events_invoice_no_fkey"
		}),
]);

export const invoicePaymentExpectations = pgTable("invoice_payment_expectations", {
	invoiceNo: varchar("invoice_no", { length: 50 }).primaryKey().notNull(),
	expectedPaymentMethod: varchar("expected_payment_method", { length: 20 }).notNull(),
	isCod: boolean("is_cod").notNull(),
	expectedAmount: numeric("expected_amount", { precision: 12, scale:  2 }),
	customerCode365: varchar("customer_code_365", { length: 50 }),
	termsCode: varchar("terms_code", { length: 50 }),
	dueDays: integer("due_days"),
	capturedAt: timestamp("captured_at", { mode: 'string' }).notNull(),
}, (table) => [
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "invoice_payment_expectations_invoice_no_fkey"
		}).onDelete("cascade"),
]);

export const invoicePostDeliveryCases = pgTable("invoice_post_delivery_cases", {
	id: bigserial({ mode: "bigint" }).primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	routeId: bigint("route_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	routeStopId: bigint("route_stop_id", { mode: "number" }),
	status: varchar({ length: 50 }).default('OPEN').notNull(),
	reason: text(),
	notes: text(),
	createdBy: varchar("created_by", { length: 100 }),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	updatedAt: timestamp("updated_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	creditNoteRequired: boolean("credit_note_required").default(false),
	creditNoteExpectedAmount: numeric("credit_note_expected_amount", { precision: 12, scale:  2 }).default('0'),
	creditNoteNo: varchar("credit_note_no", { length: 64 }),
	creditNoteIssuedAt: timestamp("credit_note_issued_at", { mode: 'string' }),
	creditNoteIssuedBy: varchar("credit_note_issued_by", { length: 64 }),
}, (table) => [
	index("idx_ipdc_status").using("btree", table.status.asc().nullsLast().op("text_ops")),
	uniqueIndex("uq_ipdc_invoice_open").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")).where(sql`((status)::text = ANY (ARRAY[('OPEN'::character varying)::text, ('INTAKE_RECEIVED'::character varying)::text, ('REROUTE_QUEUED'::character varying)::text]))`),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "invoice_post_delivery_cases_invoice_no_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "invoice_post_delivery_cases_route_id_fkey"
		}).onDelete("set null"),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "invoice_post_delivery_cases_route_stop_id_fkey"
		}).onDelete("set null"),
]);

export const invoiceRouteHistory = pgTable("invoice_route_history", {
	id: bigserial({ mode: "bigint" }).primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	routeId: bigint("route_id", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	routeStopId: bigint("route_stop_id", { mode: "number" }),
	action: varchar({ length: 100 }).notNull(),
	reason: text(),
	notes: text(),
	actorUsername: varchar("actor_username", { length: 100 }),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("idx_irh_invoice").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops"), table.createdAt.desc().nullsFirst().op("text_ops")),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "invoice_route_history_invoice_no_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "invoice_route_history_route_id_fkey"
		}).onDelete("set null"),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "invoice_route_history_route_stop_id_fkey"
		}).onDelete("set null"),
]);

export const itemTimeTracking = pgTable("item_time_tracking", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	itemCode: varchar("item_code", { length: 50 }).notNull(),
	pickerUsername: varchar("picker_username", { length: 64 }).notNull(),
	itemStarted: timestamp("item_started", { mode: 'string' }),
	itemCompleted: timestamp("item_completed", { mode: 'string' }),
	walkingToLocation: doublePrecision("walking_to_location"),
	timeAtLocation: doublePrecision("time_at_location"),
	location: varchar({ length: 100 }),
	zone: varchar({ length: 50 }),
	quantityPicked: integer("quantity_picked"),
	createdAt: timestamp("created_at", { mode: 'string' }),
	walkingTime: doublePrecision("walking_time").default(0),
	pickingTime: doublePrecision("picking_time").default(0),
	confirmationTime: doublePrecision("confirmation_time").default(0),
	totalItemTime: doublePrecision("total_item_time").default(0),
	corridor: varchar({ length: 50 }),
	shelf: varchar({ length: 50 }),
	level: varchar({ length: 50 }),
	binLocation: varchar("bin_location", { length: 50 }),
	quantityExpected: integer("quantity_expected").default(0),
	itemWeight: doublePrecision("item_weight"),
	itemName: varchar("item_name", { length: 200 }),
	unitType: varchar("unit_type", { length: 50 }),
	expectedTime: doublePrecision("expected_time").default(0),
	efficiencyRatio: doublePrecision("efficiency_ratio").default(0),
	previousLocation: varchar("previous_location", { length: 100 }),
	orderSequence: integer("order_sequence").default(0),
	timeOfDay: varchar("time_of_day", { length: 10 }),
	dayOfWeek: varchar("day_of_week", { length: 10 }),
	pickedCorrectly: boolean("picked_correctly").default(true),
	wasSkipped: boolean("was_skipped").default(false),
	skipReason: varchar("skip_reason", { length: 200 }),
	peakHours: boolean("peak_hours").default(false),
	concurrentPickers: integer("concurrent_pickers").default(1),
	updatedAt: timestamp("updated_at", { mode: 'string' }).default(sql`CURRENT_TIMESTAMP`),
}, (table) => [
	index("idx_item_time_tracking_completed").using("btree", table.itemCompleted.asc().nullsLast().op("timestamp_ops")).where(sql`(item_completed IS NOT NULL)`),
	index("idx_item_time_tracking_invoice_started").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops"), table.itemStarted.asc().nullsLast().op("text_ops")),
	index("idx_time_tracking_reporting").using("btree", table.invoiceNo.asc().nullsLast().op("timestamp_ops"), table.itemStarted.asc().nullsLast().op("text_ops"), table.pickerUsername.asc().nullsLast().op("timestamp_ops")).where(sql`(item_completed IS NOT NULL)`),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "item_time_tracking_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.pickerUsername],
			foreignColumns: [users.username],
			name: "item_time_tracking_picker_username_fkey"
		}),
]);

export const oiEstimateRuns = pgTable("oi_estimate_runs", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	estimatorVersion: varchar("estimator_version", { length: 50 }).notNull(),
	paramsRevision: integer("params_revision").notNull(),
	paramsSnapshotJson: text("params_snapshot_json"),
	estimatedTotalSeconds: doublePrecision("estimated_total_seconds"),
	estimatedPickSeconds: doublePrecision("estimated_pick_seconds"),
	estimatedTravelSeconds: doublePrecision("estimated_travel_seconds"),
	breakdownJson: text("breakdown_json"),
	reason: varchar({ length: 100 }),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
}, (table) => [
	index("ix_oi_estimate_runs_invoice_no").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "oi_estimate_runs_invoice_no_fkey"
		}),
]);

export const oiEstimateLines = pgTable("oi_estimate_lines", {
	id: serial().primaryKey().notNull(),
	runId: integer("run_id").notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	invoiceItemId: integer("invoice_item_id"),
	itemCode: varchar("item_code", { length: 100 }),
	location: varchar({ length: 100 }),
	unitTypeNormalized: varchar("unit_type_normalized", { length: 50 }),
	qty: doublePrecision(),
	estimatedPickSeconds: doublePrecision("estimated_pick_seconds"),
	estimatedWalkSeconds: doublePrecision("estimated_walk_seconds"),
	estimatedTotalSeconds: doublePrecision("estimated_total_seconds"),
	breakdownJson: text("breakdown_json"),
}, (table) => [
	index("ix_oi_estimate_lines_invoice_no").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	index("ix_oi_estimate_lines_item_code").using("btree", table.itemCode.asc().nullsLast().op("text_ops")),
	index("ix_oi_estimate_lines_run_id").using("btree", table.runId.asc().nullsLast().op("int4_ops")),
	foreignKey({
			columns: [table.runId],
			foreignColumns: [oiEstimateRuns.id],
			name: "oi_estimate_lines_run_id_fkey"
		}).onDelete("cascade"),
]);

export const orderTimeBreakdown = pgTable("order_time_breakdown", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	pickerUsername: varchar("picker_username", { length: 64 }).notNull(),
	pickingStarted: timestamp("picking_started", { mode: 'string' }),
	pickingCompleted: timestamp("picking_completed", { mode: 'string' }),
	packingStarted: timestamp("packing_started", { mode: 'string' }),
	packingCompleted: timestamp("packing_completed", { mode: 'string' }),
	totalWalkingTime: doublePrecision("total_walking_time"),
	totalPickingTime: doublePrecision("total_picking_time"),
	totalPackingTime: doublePrecision("total_packing_time"),
	totalItemsPicked: integer("total_items_picked"),
	totalLocationsVisited: integer("total_locations_visited"),
	averageTimePerItem: doublePrecision("average_time_per_item"),
	createdAt: timestamp("created_at", { mode: 'string' }),
	updatedAt: timestamp("updated_at", { mode: 'string' }),
}, (table) => [
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "order_time_breakdown_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.pickerUsername],
			foreignColumns: [users.username],
			name: "order_time_breakdown_picker_username_fkey"
		}),
]);

export const pickingExceptions = pgTable("picking_exceptions", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	itemCode: varchar("item_code", { length: 50 }).notNull(),
	expectedQty: integer("expected_qty").notNull(),
	pickedQty: integer("picked_qty").notNull(),
	pickerUsername: varchar("picker_username", { length: 64 }).notNull(),
	timestamp: timestamp({ mode: 'string' }),
	reason: varchar({ length: 500 }),
}, (table) => [
	index("idx_picking_exceptions_invoice").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	index("idx_picking_exceptions_invoice_no").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "picking_exceptions_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.pickerUsername],
			foreignColumns: [users.username],
			name: "picking_exceptions_picker_username_fkey"
		}),
]);

export const podRecords = pgTable("pod_records", {
	id: serial().primaryKey().notNull(),
	routeId: integer("route_id").notNull(),
	routeStopId: integer("route_stop_id").notNull(),
	invoiceNos: json("invoice_nos").notNull(),
	hasPhysicalSignedInvoice: boolean("has_physical_signed_invoice"),
	receiverName: varchar("receiver_name", { length: 200 }),
	receiverRelationship: varchar("receiver_relationship", { length: 100 }),
	photoPaths: json("photo_paths"),
	gpsLat: numeric("gps_lat", { precision: 10, scale:  8 }),
	gpsLng: numeric("gps_lng", { precision: 11, scale:  8 }),
	collectedAt: timestamp("collected_at", { mode: 'string' }).notNull(),
	collectedBy: varchar("collected_by", { length: 64 }).notNull(),
	notes: text(),
}, (table) => [
	foreignKey({
			columns: [table.collectedBy],
			foreignColumns: [users.username],
			name: "pod_records_collected_by_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "pod_records_route_id_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "pod_records_route_stop_id_fkey"
		}),
]);

export const purchaseOrders = pgTable("purchase_orders", {
	id: serial().primaryKey().notNull(),
	code365: varchar("code_365", { length: 100 }),
	shoppingCartCode: varchar("shopping_cart_code", { length: 100 }),
	supplierCode: varchar("supplier_code", { length: 100 }),
	statusCode: varchar("status_code", { length: 50 }),
	statusName: varchar("status_name", { length: 100 }),
	orderDateLocal: varchar("order_date_local", { length: 50 }),
	orderDateUtc0: varchar("order_date_utc0", { length: 50 }),
	comments: text(),
	totalSub: numeric("total_sub", { precision: 12, scale:  2 }),
	totalDiscount: numeric("total_discount", { precision: 12, scale:  2 }),
	totalVat: numeric("total_vat", { precision: 12, scale:  2 }),
	totalGrand: numeric("total_grand", { precision: 12, scale:  2 }),
	downloadedAt: timestamp("downloaded_at", { mode: 'string' }).notNull(),
	downloadedBy: varchar("downloaded_by", { length: 64 }),
	supplierName: varchar("supplier_name", { length: 200 }),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
	isArchived: boolean("is_archived").default(false).notNull(),
	archivedAt: timestamp("archived_at", { mode: 'string' }),
	archivedBy: varchar("archived_by", { length: 64 }),
	description: text(),
}, (table) => [
	index("idx_purchase_orders_deleted_at").using("btree", table.deletedAt.asc().nullsLast().op("timestamp_ops")),
	index("idx_purchase_orders_is_archived").using("btree", table.isArchived.asc().nullsLast().op("bool_ops")),
	index("ix_purchase_orders_code_365").using("btree", table.code365.asc().nullsLast().op("text_ops")),
	index("ix_purchase_orders_shopping_cart_code").using("btree", table.shoppingCartCode.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.archivedBy],
			foreignColumns: [users.username],
			name: "purchase_orders_archived_by_fkey"
		}),
	foreignKey({
			columns: [table.downloadedBy],
			foreignColumns: [users.username],
			name: "purchase_orders_downloaded_by_fkey"
		}),
]);

export const purchaseOrderLines = pgTable("purchase_order_lines", {
	id: serial().primaryKey().notNull(),
	purchaseOrderId: integer("purchase_order_id").notNull(),
	lineNumber: integer("line_number").notNull(),
	itemCode365: varchar("item_code_365", { length: 100 }).notNull(),
	itemName: varchar("item_name", { length: 500 }),
	lineQuantity: numeric("line_quantity", { precision: 12, scale:  4 }),
	linePriceExclVat: numeric("line_price_excl_vat", { precision: 12, scale:  2 }),
	lineTotalSub: numeric("line_total_sub", { precision: 12, scale:  2 }),
	lineTotalDiscount: numeric("line_total_discount", { precision: 12, scale:  2 }),
	lineTotalDiscountPercentage: numeric("line_total_discount_percentage", { precision: 5, scale:  2 }),
	lineVatCode365: varchar("line_vat_code_365", { length: 50 }),
	lineTotalVat: numeric("line_total_vat", { precision: 12, scale:  2 }),
	lineTotalVatPercentage: numeric("line_total_vat_percentage", { precision: 5, scale:  2 }),
	lineTotalGrand: numeric("line_total_grand", { precision: 12, scale:  2 }),
	shelfLocations: text("shelf_locations"),
	itemHasExpirationDate: boolean("item_has_expiration_date").default(false).notNull(),
	itemHasLotNumber: boolean("item_has_lot_number").default(false).notNull(),
	itemHasSerialNumber: boolean("item_has_serial_number").default(false).notNull(),
	lineId365: varchar("line_id_365", { length: 100 }),
	itemBarcode: varchar("item_barcode", { length: 100 }),
	unitType: varchar("unit_type", { length: 50 }),
	piecesPerUnit: integer("pieces_per_unit"),
	supplierItemCode: varchar("supplier_item_code", { length: 255 }),
	stockQty: numeric("stock_qty", { precision: 12, scale:  4 }),
	stockReservedQty: numeric("stock_reserved_qty", { precision: 12, scale:  4 }),
	stockOrderedQty: numeric("stock_ordered_qty", { precision: 12, scale:  4 }),
	availableQty: numeric("available_qty", { precision: 12, scale:  4 }),
	stockSyncedAt: timestamp("stock_synced_at", { withTimezone: true, mode: 'string' }),
}, (table) => [
	index("idx_purchase_order_lines_line_id_365").using("btree", table.lineId365.asc().nullsLast().op("text_ops")),
	index("ix_purchase_order_lines_item_code_365").using("btree", table.itemCode365.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.purchaseOrderId],
			foreignColumns: [purchaseOrders.id],
			name: "purchase_order_lines_purchase_order_id_fkey"
		}).onDelete("cascade"),
]);

export const receiptLog = pgTable("receipt_log", {
	id: serial().primaryKey().notNull(),
	referenceNumber: varchar("reference_number", { length: 32 }).notNull(),
	customerCode365: varchar("customer_code_365", { length: 32 }).notNull(),
	amount: numeric({ precision: 12, scale:  2 }).notNull(),
	comments: varchar({ length: 1000 }),
	responseId: varchar("response_id", { length: 128 }),
	success: integer(),
	requestJson: text("request_json"),
	responseJson: text("response_json"),
	createdAt: timestamp("created_at", { mode: 'string' }),
	invoiceNo: varchar("invoice_no", { length: 500 }),
	driverUsername: varchar("driver_username", { length: 64 }),
	routeStopId: integer("route_stop_id"),
}, (table) => [
	index("ix_receipt_log_customer_code_365").using("btree", table.customerCode365.asc().nullsLast().op("text_ops")),
	index("ix_receipt_log_reference_number").using("btree", table.referenceNumber.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.driverUsername],
			foreignColumns: [users.username],
			name: "receipt_log_driver_username_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "receipt_log_route_stop_id_fkey"
		}),
	unique("receipt_log_reference_number_key").on(table.referenceNumber),
]);

export const receivingLines = pgTable("receiving_lines", {
	id: serial().primaryKey().notNull(),
	sessionId: integer("session_id").notNull(),
	poLineId: integer("po_line_id").notNull(),
	barcodeScanned: varchar("barcode_scanned", { length: 200 }),
	itemCode365: varchar("item_code_365", { length: 100 }).notNull(),
	qtyReceived: numeric("qty_received", { precision: 12, scale:  4 }).notNull(),
	expiryDate: date("expiry_date"),
	lotNote: text("lot_note"),
	receivedAt: timestamp("received_at", { mode: 'string' }).notNull(),
}, (table) => [
	foreignKey({
			columns: [table.poLineId],
			foreignColumns: [purchaseOrderLines.id],
			name: "receiving_lines_po_line_id_fkey"
		}).onDelete("cascade"),
	foreignKey({
			columns: [table.sessionId],
			foreignColumns: [receivingSessions.id],
			name: "receiving_lines_session_id_fkey"
		}).onDelete("cascade"),
]);

export const receivingSessions = pgTable("receiving_sessions", {
	id: serial().primaryKey().notNull(),
	purchaseOrderId: integer("purchase_order_id").notNull(),
	receiptCode: varchar("receipt_code", { length: 50 }).notNull(),
	operator: varchar({ length: 64 }),
	startedAt: timestamp("started_at", { mode: 'string' }).notNull(),
	finishedAt: timestamp("finished_at", { mode: 'string' }),
	comments: text(),
}, (table) => [
	uniqueIndex("ix_receiving_sessions_receipt_code").using("btree", table.receiptCode.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.operator],
			foreignColumns: [users.username],
			name: "receiving_sessions_operator_fkey"
		}),
	foreignKey({
			columns: [table.purchaseOrderId],
			foreignColumns: [purchaseOrders.id],
			name: "receiving_sessions_purchase_order_id_fkey"
		}).onDelete("cascade"),
]);

export const rerouteRequests = pgTable("reroute_requests", {
	id: bigserial({ mode: "bigint" }).primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	requestedBy: varchar("requested_by", { length: 100 }),
	status: varchar({ length: 50 }).default('OPEN').notNull(),
	notes: text(),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	assignedRouteId: bigint("assigned_route_id", { mode: "number" }),
	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
	completedAt: timestamp("completed_at", { withTimezone: true, mode: 'string' }),
}, (table) => [
	index("idx_rr_status").using("btree", table.status.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.assignedRouteId],
			foreignColumns: [shipments.id],
			name: "reroute_requests_assigned_route_id_fkey"
		}).onDelete("set null"),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "reroute_requests_invoice_no_fkey"
		}).onDelete("cascade"),
]);

export const routeDeliveryEvents = pgTable("route_delivery_events", {
	id: serial().primaryKey().notNull(),
	routeId: integer("route_id").notNull(),
	routeStopId: integer("route_stop_id"),
	eventType: varchar("event_type", { length: 50 }).notNull(),
	payload: json(),
	gpsLat: numeric("gps_lat", { precision: 10, scale:  8 }),
	gpsLng: numeric("gps_lng", { precision: 11, scale:  8 }),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
	actorUsername: varchar("actor_username", { length: 64 }).notNull(),
}, (table) => [
	foreignKey({
			columns: [table.actorUsername],
			foreignColumns: [users.username],
			name: "route_delivery_events_actor_username_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "route_delivery_events_route_id_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "route_delivery_events_route_stop_id_fkey"
		}),
]);

export const routeReturnHandover = pgTable("route_return_handover", {
	id: serial().primaryKey().notNull(),
	routeId: integer("route_id").notNull(),
	routeStopId: integer("route_stop_id"),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	driverConfirmedAt: timestamp("driver_confirmed_at", { mode: 'string' }),
	driverUsername: varchar("driver_username", { length: 64 }),
	warehouseReceivedAt: timestamp("warehouse_received_at", { mode: 'string' }),
	receivedBy: varchar("received_by", { length: 64 }),
	packagesCount: integer("packages_count"),
	notes: text(),
	photoPaths: jsonb("photo_paths"),
	createdAt: timestamp("created_at", { mode: 'string' }).defaultNow().notNull(),
}, (table) => [
	index("ix_return_handover_driver_pending").using("btree", table.routeId.asc().nullsLast().op("int4_ops"), table.driverConfirmedAt.asc().nullsLast().op("int4_ops"), table.warehouseReceivedAt.asc().nullsLast().op("timestamp_ops")),
	uniqueIndex("ux_return_handover_route_invoice").using("btree", table.routeId.asc().nullsLast().op("int4_ops"), table.invoiceNo.asc().nullsLast().op("int4_ops")),
	foreignKey({
			columns: [table.driverUsername],
			foreignColumns: [users.username],
			name: "route_return_handover_driver_username_fkey"
		}),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "route_return_handover_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.receivedBy],
			foreignColumns: [users.username],
			name: "route_return_handover_received_by_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "route_return_handover_route_id_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "route_return_handover_route_stop_id_fkey"
		}),
]);

export const shipmentOrders = pgTable("shipment_orders", {
	id: serial().primaryKey().notNull(),
	shipmentId: integer("shipment_id").notNull(),
	invoiceNo: varchar("invoice_no", { length: 20 }).notNull(),
}, (table) => [
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "shipment_orders_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.shipmentId],
			foreignColumns: [shipments.id],
			name: "shipment_orders_shipment_id_fkey"
		}),
]);

export const shippingEvents = pgTable("shipping_events", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	action: varchar({ length: 20 }).notNull(),
	actor: varchar({ length: 64 }).notNull(),
	timestamp: timestamp({ mode: 'string' }).notNull(),
	note: text(),
}, (table) => [
	foreignKey({
			columns: [table.actor],
			foreignColumns: [users.username],
			name: "shipping_events_actor_fkey"
		}),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "shipping_events_invoice_no_fkey"
		}),
]);

export const timeTrackingAlerts = pgTable("time_tracking_alerts", {
	id: serial().primaryKey().notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	pickerUsername: varchar("picker_username", { length: 64 }).notNull(),
	alertType: varchar("alert_type", { length: 50 }).notNull(),
	expectedDuration: doublePrecision("expected_duration").notNull(),
	actualDuration: doublePrecision("actual_duration").notNull(),
	thresholdPercentage: doublePrecision("threshold_percentage").notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }),
	isResolved: boolean("is_resolved"),
	resolvedAt: timestamp("resolved_at", { mode: 'string' }),
	resolvedBy: varchar("resolved_by", { length: 64 }),
	notes: text(),
}, (table) => [
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "time_tracking_alerts_invoice_no_fkey"
		}),
	foreignKey({
			columns: [table.pickerUsername],
			foreignColumns: [users.username],
			name: "time_tracking_alerts_picker_username_fkey"
		}),
	foreignKey({
			columns: [table.resolvedBy],
			foreignColumns: [users.username],
			name: "time_tracking_alerts_resolved_by_fkey"
		}),
]);

export const wmsPallet = pgTable("wms_pallet", {
	palletId: serial("pallet_id").primaryKey().notNull(),
	shipmentId: integer("shipment_id").notNull(),
	label: varchar({ length: 50 }).notNull(),
	laneCode: varchar("lane_code", { length: 10 }),
	laneSlot: integer("lane_slot"),
	status: varchar({ length: 20 }).notNull(),
	maxWeightKg: numeric("max_weight_kg", { precision: 10, scale:  2 }).notNull(),
	maxHeightM: numeric("max_height_m", { precision: 10, scale:  2 }).notNull(),
	usedMask: integer("used_mask").notNull(),
	usedWeightKg: numeric("used_weight_kg", { precision: 10, scale:  2 }).notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).notNull(),
	deletedAt: timestamp("deleted_at", { mode: 'string' }),
	deletedBy: varchar("deleted_by", { length: 64 }),
	deleteReason: varchar("delete_reason", { length: 255 }),
}, (table) => [
	foreignKey({
			columns: [table.shipmentId],
			foreignColumns: [shipments.id],
			name: "wms_pallet_shipment_id_fkey"
		}).onDelete("cascade"),
]);

export const wmsPalletOrder = pgTable("wms_pallet_order", {
	id: serial().primaryKey().notNull(),
	palletId: integer("pallet_id").notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	blocksRequested: integer("blocks_requested").notNull(),
	blocksMask: integer("blocks_mask").notNull(),
	estWeightKg: numeric("est_weight_kg", { precision: 10, scale:  2 }),
	stopSeqNo: numeric("stop_seq_no", { precision: 10, scale:  2 }),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
}, (table) => [
	index("ix_pallet_order_pallet_id").using("btree", table.palletId.asc().nullsLast().op("int4_ops")),
	foreignKey({
			columns: [table.palletId],
			foreignColumns: [wmsPallet.palletId],
			name: "wms_pallet_order_pallet_id_fkey"
		}).onDelete("cascade"),
	unique("uq_pallet_order_invoice_no").on(table.invoiceNo),
]);

export const paymentEntries = pgTable("payment_entries", {
	id: serial().primaryKey().notNull(),
	routeStopId: integer("route_stop_id").notNull(),
	method: varchar({ length: 20 }).notNull(),
	amount: numeric({ precision: 18, scale:  2 }).notNull(),
	chequeNo: varchar("cheque_no", { length: 64 }),
	chequeDate: date("cheque_date"),
	commitMode: varchar("commit_mode", { length: 20 }).notNull(),
	docType: varchar("doc_type", { length: 20 }).notNull(),
	psStatus: varchar("ps_status", { length: 20 }).notNull(),
	psReference: varchar("ps_reference", { length: 64 }),
	psError: text("ps_error"),
	attemptCount: integer("attempt_count").notNull(),
	lastAttemptAt: timestamp("last_attempt_at", { mode: 'string' }),
	isActive: boolean("is_active").notNull(),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
	updatedAt: timestamp("updated_at", { mode: 'string' }).notNull(),
}, (table) => [
	index("ix_payment_entries_route_stop_id").using("btree", table.routeStopId.asc().nullsLast().op("int4_ops")),
	index("ix_payment_entries_stop").using("btree", table.routeStopId.asc().nullsLast().op("int4_ops")),
	uniqueIndex("uq_payment_entries_active").using("btree", table.routeStopId.asc().nullsLast().op("int4_ops")).where(sql`(is_active = true)`),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "payment_entries_route_stop_id_fkey"
		}).onDelete("cascade"),
]);

export const codReceipts = pgTable("cod_receipts", {
	id: serial().primaryKey().notNull(),
	routeId: integer("route_id").notNull(),
	routeStopId: integer("route_stop_id").notNull(),
	driverUsername: varchar("driver_username", { length: 64 }).notNull(),
	invoiceNos: json("invoice_nos").notNull(),
	expectedAmount: numeric("expected_amount", { precision: 12, scale:  2 }).notNull(),
	receivedAmount: numeric("received_amount", { precision: 12, scale:  2 }).notNull(),
	variance: numeric({ precision: 12, scale:  2 }),
	paymentMethod: varchar("payment_method", { length: 20 }).notNull(),
	note: text(),
	ps365ReceiptId: varchar("ps365_receipt_id", { length: 128 }),
	ps365SyncedAt: timestamp("ps365_synced_at", { mode: 'string' }),
	createdAt: timestamp("created_at", { mode: 'string' }).notNull(),
	chequeNumber: varchar("cheque_number", { length: 50 }),
	chequeDate: date("cheque_date"),
	docType: varchar("doc_type", { length: 30 }).default('official').notNull(),
	status: varchar({ length: 20 }).default('DRAFT').notNull(),
	lockedAt: timestamp("locked_at", { withTimezone: true, mode: 'string' }),
	lockedBy: varchar("locked_by", { length: 64 }),
	printCount: integer("print_count").default(0).notNull(),
	firstPrintedAt: timestamp("first_printed_at", { withTimezone: true, mode: 'string' }),
	lastPrintedAt: timestamp("last_printed_at", { withTimezone: true, mode: 'string' }),
	voidedAt: timestamp("voided_at", { withTimezone: true, mode: 'string' }),
	voidedBy: varchar("voided_by", { length: 64 }),
	voidReason: text("void_reason"),
	replacedByCodReceiptId: integer("replaced_by_cod_receipt_id"),
	clientRequestId: varchar("client_request_id", { length: 128 }),
	ps365ReferenceNumber: varchar("ps365_reference_number", { length: 128 }),
}, (table) => [
	index("idx_cod_receipts_client_request_id").using("btree", table.clientRequestId.asc().nullsLast().op("text_ops")),
	index("idx_cod_receipts_doc_type").using("btree", table.docType.asc().nullsLast().op("text_ops")),
	index("idx_cod_receipts_status").using("btree", table.status.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.driverUsername],
			foreignColumns: [users.username],
			name: "cod_receipts_driver_username_fkey"
		}),
	foreignKey({
			columns: [table.lockedBy],
			foreignColumns: [users.username],
			name: "cod_receipts_locked_by_fkey"
		}),
	foreignKey({
			columns: [table.replacedByCodReceiptId],
			foreignColumns: [table.id],
			name: "cod_receipts_replaced_by_cod_receipt_id_fkey"
		}),
	foreignKey({
			columns: [table.routeId],
			foreignColumns: [shipments.id],
			name: "cod_receipts_route_id_fkey"
		}),
	foreignKey({
			columns: [table.routeStopId],
			foreignColumns: [routeStop.routeStopId],
			name: "cod_receipts_route_stop_id_fkey"
		}),
	foreignKey({
			columns: [table.voidedBy],
			foreignColumns: [users.username],
			name: "cod_receipts_voided_by_fkey"
		}),
]);

export const bankTransactions = pgTable("bank_transactions", {
	id: serial().primaryKey().notNull(),
	batchId: varchar("batch_id", { length: 36 }).notNull(),
	txnDate: date("txn_date"),
	description: text(),
	reference: varchar({ length: 200 }),
	credit: numeric({ precision: 12, scale:  2 }),
	debit: numeric({ precision: 12, scale:  2 }),
	balance: numeric({ precision: 14, scale:  2 }),
	rawRow: text("raw_row"),
	matchedAllocationId: integer("matched_allocation_id"),
	matchStatus: varchar("match_status", { length: 20 }).notNull(),
	matchConfidence: varchar("match_confidence", { length: 20 }),
	matchReason: varchar("match_reason", { length: 200 }),
	dismissed: boolean().notNull(),
	uploadedBy: varchar("uploaded_by", { length: 64 }),
	uploadedAt: timestamp("uploaded_at", { mode: 'string' }).notNull(),
}, (table) => [
	index("ix_bank_transactions_batch_id").using("btree", table.batchId.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.matchedAllocationId],
			foreignColumns: [codInvoiceAllocations.id],
			name: "bank_transactions_matched_allocation_id_fkey"
		}),
]);

export const batchSessionInvoices = pgTable("batch_session_invoices", {
	batchSessionId: integer("batch_session_id").notNull(),
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	isCompleted: boolean("is_completed").default(false),
}, (table) => [
	foreignKey({
			columns: [table.batchSessionId],
			foreignColumns: [batchPickingSessions.id],
			name: "batch_session_invoices_batch_session_id_fkey"
		}),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "batch_session_invoices_invoice_no_fkey"
		}),
	primaryKey({ columns: [table.batchSessionId, table.invoiceNo], name: "batch_session_invoices_pkey"}),
]);

export const invoiceItems = pgTable("invoice_items", {
	invoiceNo: varchar("invoice_no", { length: 50 }).notNull(),
	itemCode: varchar("item_code", { length: 50 }).notNull(),
	location: varchar({ length: 100 }),
	barcode: varchar({ length: 100 }),
	zone: varchar({ length: 50 }),
	itemWeight: doublePrecision("item_weight"),
	itemName: varchar("item_name", { length: 200 }),
	unitType: varchar("unit_type", { length: 50 }),
	pack: varchar({ length: 50 }),
	qty: integer(),
	lineWeight: doublePrecision("line_weight"),
	expTime: doublePrecision("exp_time"),
	pickedQty: integer("picked_qty"),
	isPicked: boolean("is_picked"),
	pickStatus: varchar("pick_status", { length: 20 }).default('not_picked'),
	resetBy: varchar("reset_by", { length: 64 }),
	resetTimestamp: timestamp("reset_timestamp", { mode: 'string' }),
	resetNote: varchar("reset_note", { length: 500 }),
	skipReason: text("skip_reason"),
	skipTimestamp: timestamp("skip_timestamp", { mode: 'string' }),
	skipCount: integer("skip_count").default(0),
	corridor: varchar({ length: 10 }),
	lockedByBatchId: integer("locked_by_batch_id"),
	piecesPerUnitSnapshot: integer("pieces_per_unit_snapshot"),
	expectedPickPieces: integer("expected_pick_pieces"),
}, (table) => [
	index("idx_invoice_items_batch_lock").using("btree", table.lockedByBatchId.asc().nullsLast().op("int4_ops")),
	index("idx_invoice_items_batch_zone").using("btree", table.zone.asc().nullsLast().op("int4_ops"), table.corridor.asc().nullsLast().op("int4_ops"), table.lockedByBatchId.asc().nullsLast().op("int4_ops")),
	index("idx_invoice_items_corridor").using("btree", table.corridor.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_invoice_no").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_invoice_picked").using("btree", table.invoiceNo.asc().nullsLast().op("bool_ops"), table.isPicked.asc().nullsLast().op("bool_ops")),
	index("idx_invoice_items_invoice_status").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops"), table.pickStatus.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_is_picked").using("btree", table.isPicked.asc().nullsLast().op("bool_ops")),
	index("idx_invoice_items_location").using("btree", table.zone.asc().nullsLast().op("text_ops"), table.corridor.asc().nullsLast().op("text_ops"), table.location.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_location_sort").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops"), table.zone.asc().nullsLast().op("text_ops"), table.corridor.asc().nullsLast().op("text_ops"), table.location.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_pick_status").using("btree", table.pickStatus.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_picked").using("btree", table.isPicked.asc().nullsLast().op("bool_ops"), table.pickedQty.asc().nullsLast().op("int4_ops")),
	index("idx_invoice_items_picking_performance").using("btree", table.invoiceNo.asc().nullsLast().op("int4_ops"), table.isPicked.asc().nullsLast().op("int4_ops"), table.pickStatus.asc().nullsLast().op("int4_ops"), table.lockedByBatchId.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_zone").using("btree", table.zone.asc().nullsLast().op("text_ops")),
	index("idx_invoice_items_zone_corridor").using("btree", table.zone.asc().nullsLast().op("text_ops"), table.corridor.asc().nullsLast().op("text_ops")),
	index("idx_items_batch_eligible").using("btree", table.zone.asc().nullsLast().op("text_ops"), table.corridor.asc().nullsLast().op("text_ops"), table.isPicked.asc().nullsLast().op("bool_ops"), table.pickStatus.asc().nullsLast().op("text_ops")).where(sql`((is_picked = false) AND ((pick_status)::text = ANY (ARRAY[('not_picked'::character varying)::text, ('reset'::character varying)::text, ('skipped_pending'::character varying)::text])))`),
	index("idx_items_batch_locking").using("btree", table.zone.asc().nullsLast().op("int4_ops"), table.corridor.asc().nullsLast().op("int4_ops"), table.isPicked.asc().nullsLast().op("int4_ops"), table.pickStatus.asc().nullsLast().op("bool_ops"), table.lockedByBatchId.asc().nullsLast().op("text_ops"), table.unitType.asc().nullsLast().op("bool_ops")).where(sql`((is_picked = false) AND ((pick_status)::text = ANY (ARRAY[('not_picked'::character varying)::text, ('reset'::character varying)::text, ('skipped_pending'::character varying)::text])))`),
	index("idx_items_corridor_zone").using("btree", table.corridor.asc().nullsLast().op("text_ops"), table.zone.asc().nullsLast().op("text_ops")),
	index("idx_items_invoice_picked_status").using("btree", table.invoiceNo.asc().nullsLast().op("bool_ops"), table.isPicked.asc().nullsLast().op("bool_ops"), table.pickStatus.asc().nullsLast().op("text_ops")),
	index("idx_items_zone_status_picked").using("btree", table.zone.asc().nullsLast().op("bool_ops"), table.pickStatus.asc().nullsLast().op("text_ops"), table.isPicked.asc().nullsLast().op("bool_ops")),
	uniqueIndex("uq_invoice_items_invoice_no_item_code").using("btree", table.invoiceNo.asc().nullsLast().op("text_ops"), table.itemCode.asc().nullsLast().op("text_ops")),
	foreignKey({
			columns: [table.lockedByBatchId],
			foreignColumns: [batchPickingSessions.id],
			name: "fk_locked_by_batch_id"
		}).onDelete("set null"),
	foreignKey({
			columns: [table.invoiceNo],
			foreignColumns: [invoices.invoiceNo],
			name: "invoice_items_invoice_no_fkey"
		}),
	primaryKey({ columns: [table.invoiceNo, table.itemCode], name: "invoice_items_pkey"}),
]);
export const dwSalesLinesV = pgView("dw_sales_lines_v", {	saleDate: date("sale_date"),
	customerCode365: varchar("customer_code_365", { length: 64 }),
	itemCode365: varchar("item_code_365", { length: 64 }),
	qty: numeric({ precision: 18, scale:  4 }),
	netExcl: numeric("net_excl", { precision: 18, scale:  4 }),
}).as(sql`SELECT h.invoice_date_utc0 AS sale_date, h.customer_code_365, l.item_code_365, l.quantity AS qty, l.line_total_excl AS net_excl FROM dw_invoice_header h JOIN dw_invoice_line l ON l.invoice_no_365::text = h.invoice_no_365::text`);

export const pbiDimCustomers = pgView("pbi_dim_customers", {	customerCode: varchar("customer_code", { length: 50 }),
	customerName: text("customer_name"),
	isCompany: boolean("is_company"),
	customerCategory: text("customer_category"),
	businessActivity: text("business_activity"),
	salesAgent: text("sales_agent"),
	town: text(),
	postalCode: text("postal_code"),
	addressLine1: text("address_line_1"),
	addressLine2: text("address_line_2"),
	addressLine3: text("address_line_3"),
	phone: text(),
	mobile: text(),
	vatNo: text("vat_no"),
	creditLimit: doublePrecision("credit_limit"),
	latitude: doublePrecision(),
	longitude: doublePrecision(),
	isActive: boolean("is_active"),
}).as(sql`SELECT customer_code_365 AS customer_code, company_name AS customer_name, is_company, category_1_name AS customer_category, company_activity_name AS business_activity, agent_name AS sales_agent, town, postal_code, address_line_1, address_line_2, address_line_3, tel_1 AS phone, mobile, vat_registration_number AS vat_no, credit_limit_amount AS credit_limit, latitude, longitude, COALESCE(is_active, true) AS is_active FROM ps_customers c WHERE deleted_at IS NULL`);

export const pbiDimDates = pgView("pbi_dim_dates", {	dateKey: date("date_key"),
	year: integer(),
	quarter: integer(),
	monthNo: integer("month_no"),
	monthName: text("month_name"),
	monthShort: text("month_short"),
	weekNo: integer("week_no"),
	dayOfWeekNo: integer("day_of_week_no"),
	dayName: text("day_name"),
	yearMonth: text("year_month"),
	yearQuarter: text("year_quarter"),
	isWeekday: boolean("is_weekday"),
}).as(sql`SELECT d::date AS date_key, EXTRACT(year FROM d)::integer AS year, EXTRACT(quarter FROM d)::integer AS quarter, EXTRACT(month FROM d)::integer AS month_no, to_char(d, 'Month'::text) AS month_name, to_char(d, 'Mon'::text) AS month_short, EXTRACT(week FROM d)::integer AS week_no, EXTRACT(dow FROM d)::integer AS day_of_week_no, to_char(d, 'Day'::text) AS day_name, to_char(d, 'YYYY-MM'::text) AS year_month, (to_char(d, 'YYYY'::text) || '-Q'::text) || EXTRACT(quarter FROM d) AS year_quarter, CASE WHEN EXTRACT(dow FROM d) = ANY (ARRAY[0::numeric, 6::numeric]) THEN false ELSE true END AS is_weekday FROM generate_series('2023-01-01'::date::timestamp with time zone, '2027-12-31'::date::timestamp with time zone, '1 day'::interval) d(d)`);

export const pbiDimProducts = pgView("pbi_dim_products", {	itemCode: varchar("item_code", { length: 64 }),
	itemName: varchar("item_name", { length: 255 }),
	isActive: boolean("is_active"),
	barcode: varchar({ length: 100 }),
	supplierItemCode: varchar("supplier_item_code", { length: 255 }),
	category: varchar({ length: 255 }),
	brand: varchar({ length: 255 }),
	zoneName: varchar("zone_name", { length: 255 }),
	attribute1: varchar("attribute_1", { length: 64 }),
	attribute2: varchar("attribute_2", { length: 64 }),
	zoneCode: varchar("zone_code", { length: 64 }),
	attribute4: varchar("attribute_4", { length: 64 }),
	attribute5: varchar("attribute_5", { length: 64 }),
	attribute6: varchar("attribute_6", { length: 64 }),
	itemWeight: numeric("item_weight", { precision: 10, scale:  3 }),
	sellingQty: numeric("selling_qty", { precision: 10, scale:  3 }),
	numberOfPieces: integer("number_of_pieces"),
	wmsZone: varchar("wms_zone", { length: 50 }),
	wmsUnitType: varchar("wms_unit_type", { length: 50 }),
	wmsFragility: varchar("wms_fragility", { length: 20 }),
	wmsTemperatureSensitivity: varchar("wms_temperature_sensitivity", { length: 30 }),
}).as(sql`SELECT i.item_code_365 AS item_code, i.item_name, COALESCE(i.active, true) AS is_active, i.barcode, i.supplier_item_code, cat.category_name AS category, b.brand_name AS brand, a3.attribute_3_name AS zone_name, i.attribute_1_code_365 AS attribute_1, i.attribute_2_code_365 AS attribute_2, i.attribute_3_code_365 AS zone_code, i.attribute_4_code_365 AS attribute_4, i.attribute_5_code_365 AS attribute_5, i.attribute_6_code_365 AS attribute_6, i.item_weight, i.selling_qty, i.number_of_pieces, i.wms_zone, i.wms_unit_type, i.wms_fragility, i.wms_temperature_sensitivity FROM ps_items_dw i LEFT JOIN dw_item_categories cat ON cat.category_code_365::text = i.category_code_365::text LEFT JOIN dw_brands b ON b.brand_code_365::text = i.brand_code_365::text LEFT JOIN dw_attribute3 a3 ON a3.attribute_3_code_365::text = i.attribute_3_code_365::text`);

export const pbiDimStores = pgView("pbi_dim_stores", {	storeCode: varchar("store_code", { length: 64 }),
	storeName: varchar("store_name", { length: 255 }),
}).as(sql`SELECT store_code_365 AS store_code, store_name FROM dw_store s`);

export const pbiFactDiscrepancies = pgView("pbi_fact_discrepancies", {	discrepancyId: integer("discrepancy_id"),
	invoiceNo: varchar("invoice_no", { length: 50 }),
	itemCode: varchar("item_code", { length: 50 }),
	itemName: varchar("item_name", { length: 200 }),
	qtyExpected: integer("qty_expected"),
	qtyActual: numeric("qty_actual", { precision: 10, scale:  2 }),
	discrepancyType: varchar("discrepancy_type", { length: 50 }),
	discrepancyStatus: varchar("discrepancy_status", { length: 20 }),
	reportedBy: varchar("reported_by", { length: 64 }),
	reportedAt: timestamp("reported_at", { mode: 'string' }),
	reportedSource: varchar("reported_source", { length: 50 }),
	deliveryDate: date("delivery_date"),
	reportedValue: numeric("reported_value", { precision: 12, scale:  2 }),
	warehouseResult: varchar("warehouse_result", { length: 30 }),
	creditNoteRequired: boolean("credit_note_required"),
	creditNoteAmount: numeric("credit_note_amount", { precision: 12, scale:  2 }),
	resolutionAction: varchar("resolution_action", { length: 50 }),
	isValidated: boolean("is_validated"),
	isResolved: boolean("is_resolved"),
}).as(sql`SELECT id AS discrepancy_id, invoice_no, item_code_expected AS item_code, item_name, qty_expected, qty_actual, discrepancy_type, status AS discrepancy_status, reported_by, reported_at, reported_source, delivery_date, reported_value, warehouse_result, credit_note_required, credit_note_amount, resolution_action, is_validated, is_resolved FROM delivery_discrepancies dd`);

export const pbiFactInvoices = pgView("pbi_fact_invoices", {	invoiceNo: varchar("invoice_no", { length: 64 }),
	invoiceType: varchar("invoice_type", { length: 64 }),
	invoiceDate: date("invoice_date"),
	customerCode: varchar("customer_code", { length: 64 }),
	storeCode: varchar("store_code", { length: 64 }),
	salespersonCode: varchar("salesperson_code", { length: 64 }),
	totalExclVat: numeric("total_excl_vat", { precision: 18, scale:  4 }),
	totalDiscount: numeric("total_discount", { precision: 18, scale:  4 }),
	totalNet: numeric("total_net"),
	totalVat: numeric("total_vat", { precision: 18, scale:  4 }),
	totalInclVat: numeric("total_incl_vat", { precision: 18, scale:  4 }),
	pointsEarned: numeric("points_earned", { precision: 18, scale:  2 }),
	pointsRedeemed: numeric("points_redeemed", { precision: 18, scale:  2 }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	lineCount: bigint("line_count", { mode: "number" }),
	totalQty: numeric("total_qty"),
	year: numeric(),
	month: numeric(),
	quarter: numeric(),
	yearMonth: text("year_month"),
}).as(sql`SELECT h.invoice_no_365 AS invoice_no, h.invoice_type, h.invoice_date_utc0 AS invoice_date, h.customer_code_365 AS customer_code, h.store_code_365 AS store_code, h.user_code_365 AS salesperson_code, h.total_sub AS total_excl_vat, h.total_discount, COALESCE(h.total_sub, 0::numeric) - COALESCE(h.total_discount, 0::numeric) AS total_net, h.total_vat, h.total_grand AS total_incl_vat, h.points_earned, h.points_redeemed, count(l.id) AS line_count, sum(l.quantity) AS total_qty, EXTRACT(year FROM h.invoice_date_utc0) AS year, EXTRACT(month FROM h.invoice_date_utc0) AS month, EXTRACT(quarter FROM h.invoice_date_utc0) AS quarter, to_char(h.invoice_date_utc0::timestamp with time zone, 'YYYY-MM'::text) AS year_month FROM dw_invoice_header h LEFT JOIN dw_invoice_line l ON l.invoice_no_365::text = h.invoice_no_365::text GROUP BY h.invoice_no_365, h.invoice_type, h.invoice_date_utc0, h.customer_code_365, h.store_code_365, h.user_code_365, h.total_sub, h.total_discount, h.total_vat, h.total_grand, h.points_earned, h.points_redeemed`);

export const pbiFactPicking = pgView("pbi_fact_picking", {	invoiceNo: varchar("invoice_no", { length: 50 }),
	customerName: varchar("customer_name", { length: 200 }),
	picker: varchar({ length: 64 }),
	orderStatus: varchar("order_status", { length: 30 }),
	totalLines: integer("total_lines"),
	totalItems: integer("total_items"),
	totalWeight: doublePrecision("total_weight"),
	pickingCompleteTime: timestamp("picking_complete_time", { mode: 'string' }),
	packingCompleteTime: timestamp("packing_complete_time", { mode: 'string' }),
	shippedAt: timestamp("shipped_at", { mode: 'string' }),
	deliveredAt: timestamp("delivered_at", { mode: 'string' }),
	uploadDate: varchar("upload_date", { length: 10 }),
	customerCode: varchar("customer_code", { length: 50 }),
	pickingDurationMinutes: numeric("picking_duration_minutes"),
}).as(sql`SELECT invoice_no, customer_name, assigned_to AS picker, status AS order_status, total_lines, total_items, total_weight, picking_complete_time, packing_complete_time, shipped_at, delivered_at, upload_date, customer_code_365 AS customer_code, CASE WHEN picking_complete_time IS NOT NULL AND status_updated_at IS NOT NULL THEN EXTRACT(epoch FROM picking_complete_time - status_updated_at) / 60.0 ELSE NULL::numeric END AS picking_duration_minutes FROM invoices inv WHERE deleted_at IS NULL`);

export const pbiFactRouteDeliveries = pgView("pbi_fact_route_deliveries", {	deliveryId: integer("delivery_id"),
	routeId: integer("route_id"),
	routeName: varchar("route_name", { length: 100 }),
	driverName: varchar("driver_name", { length: 100 }),
	deliveryDate: date("delivery_date"),
	stopId: integer("stop_id"),
	stopSequence: numeric("stop_sequence", { precision: 10, scale:  2 }),
	stopName: text("stop_name"),
	stopCity: text("stop_city"),
	customerCode: varchar("customer_code", { length: 50 }),
	invoiceNo: varchar("invoice_no"),
	deliveryStatus: varchar("delivery_status"),
	expectedPaymentMethod: varchar("expected_payment_method", { length: 20 }),
	expectedAmount: numeric("expected_amount", { precision: 12, scale:  2 }),
	discrepancyValue: numeric("discrepancy_value", { precision: 10, scale:  2 }),
	weightKg: doublePrecision("weight_kg"),
	deliveredAt: timestamp("delivered_at", { mode: 'string' }),
	failedAt: timestamp("failed_at", { mode: 'string' }),
	failureReason: varchar("failure_reason", { length: 100 }),
}).as(sql`SELECT rsi.route_stop_invoice_id AS delivery_id, s.id AS route_id, s.route_name, s.driver_name, s.delivery_date, rs.route_stop_id AS stop_id, rs.seq_no AS stop_sequence, rs.stop_name, rs.stop_city, rs.customer_code, rsi.invoice_no, rsi.status AS delivery_status, rsi.expected_payment_method, rsi.expected_amount, rsi.discrepancy_value, rsi.weight_kg, rs.delivered_at, rs.failed_at, rs.failure_reason FROM route_stop_invoice rsi JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id JOIN shipments s ON s.id = rs.shipment_id WHERE rs.deleted_at IS NULL AND s.deleted_at IS NULL AND rsi.is_active = true`);

export const pbiFactRoutes = pgView("pbi_fact_routes", {	routeId: integer("route_id"),
	routeName: varchar("route_name", { length: 100 }),
	driverName: varchar("driver_name", { length: 100 }),
	routeStatus: varchar("route_status", { length: 20 }),
	deliveryDate: date("delivery_date"),
	reconciliationStatus: varchar("reconciliation_status", { length: 20 }),
	isArchived: boolean("is_archived"),
	createdAt: timestamp("created_at", { mode: 'string' }),
	startedAt: timestamp("started_at", { mode: 'string' }),
	completedAt: timestamp("completed_at", { mode: 'string' }),
	cashExpected: numeric("cash_expected", { precision: 12, scale:  2 }),
	cashCollected: numeric("cash_collected", { precision: 12, scale:  2 }),
	cashHandedIn: numeric("cash_handed_in", { precision: 12, scale:  2 }),
	cashVariance: numeric("cash_variance", { precision: 12, scale:  2 }),
	returnsCount: integer("returns_count"),
	durationMinutes: numeric("duration_minutes"),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	stopCount: bigint("stop_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	invoiceCount: bigint("invoice_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	deliveredCount: bigint("delivered_count", { mode: "number" }),
	// You can use { mode: "bigint" } if numbers are exceeding js number limitations
	failedCount: bigint("failed_count", { mode: "number" }),
}).as(sql`WITH route_counts AS ( SELECT rs.shipment_id, count(*) FILTER (WHERE rsi.is_active = true) AS invoice_count, count(*) FILTER (WHERE rsi.is_active = true AND rsi.status::text = 'DELIVERED'::text) AS delivered_count, count(*) FILTER (WHERE rsi.is_active = true AND rsi.status::text = 'FAILED'::text) AS failed_count FROM route_stop rs JOIN route_stop_invoice rsi ON rsi.route_stop_id = rs.route_stop_id WHERE rs.deleted_at IS NULL GROUP BY rs.shipment_id ), stop_counts AS ( SELECT route_stop.shipment_id, count(*) AS stop_count FROM route_stop WHERE route_stop.deleted_at IS NULL GROUP BY route_stop.shipment_id ) SELECT s.id AS route_id, s.route_name, s.driver_name, s.status AS route_status, s.delivery_date, s.reconciliation_status, s.is_archived, s.created_at, s.started_at, s.completed_at, s.cash_expected, s.cash_collected, s.cash_handed_in, s.cash_variance, s.returns_count, CASE WHEN s.completed_at IS NOT NULL AND s.started_at IS NOT NULL THEN EXTRACT(epoch FROM s.completed_at - s.started_at) / 60.0 ELSE NULL::numeric END AS duration_minutes, COALESCE(sc.stop_count, 0::bigint) AS stop_count, COALESCE(rc.invoice_count, 0::bigint) AS invoice_count, COALESCE(rc.delivered_count, 0::bigint) AS delivered_count, COALESCE(rc.failed_count, 0::bigint) AS failed_count FROM shipments s LEFT JOIN route_counts rc ON rc.shipment_id = s.id LEFT JOIN stop_counts sc ON sc.shipment_id = s.id WHERE s.deleted_at IS NULL`);

export const pbiFactSales = pgView("pbi_fact_sales", {	lineId: integer("line_id"),
	invoiceNo: varchar("invoice_no", { length: 64 }),
	invoiceType: varchar("invoice_type", { length: 64 }),
	invoiceDate: date("invoice_date"),
	customerCode: varchar("customer_code", { length: 64 }),
	storeCode: varchar("store_code", { length: 64 }),
	salespersonCode: varchar("salesperson_code", { length: 64 }),
	itemCode: varchar("item_code", { length: 64 }),
	lineNumber: integer("line_number"),
	quantity: numeric({ precision: 18, scale:  4 }),
	priceExcl: numeric("price_excl", { precision: 18, scale:  4 }),
	priceIncl: numeric("price_incl", { precision: 18, scale:  4 }),
	discountPercent: numeric("discount_percent", { precision: 18, scale:  4 }),
	vatPercent: numeric("vat_percent", { precision: 6, scale:  4 }),
	lineTotalExcl: numeric("line_total_excl", { precision: 18, scale:  4 }),
	lineTotalDiscount: numeric("line_total_discount", { precision: 18, scale:  4 }),
	lineTotalVat: numeric("line_total_vat", { precision: 18, scale:  4 }),
	lineTotalIncl: numeric("line_total_incl", { precision: 18, scale:  4 }),
	lineNetValue: numeric("line_net_value"),
	year: numeric(),
	month: numeric(),
	quarter: numeric(),
	yearMonth: text("year_month"),
	dayOfWeek: text("day_of_week"),
	dayOfWeekNo: numeric("day_of_week_no"),
}).as(sql`SELECT l.id AS line_id, h.invoice_no_365 AS invoice_no, h.invoice_type, h.invoice_date_utc0 AS invoice_date, h.customer_code_365 AS customer_code, h.store_code_365 AS store_code, h.user_code_365 AS salesperson_code, l.item_code_365 AS item_code, l.line_number, l.quantity, l.price_excl, l.price_incl, l.discount_percent, l.vat_percent, l.line_total_excl, l.line_total_discount, l.line_total_vat, l.line_total_incl, COALESCE(l.line_total_incl, 0::numeric) - COALESCE(l.line_total_vat, 0::numeric) AS line_net_value, EXTRACT(year FROM h.invoice_date_utc0) AS year, EXTRACT(month FROM h.invoice_date_utc0) AS month, EXTRACT(quarter FROM h.invoice_date_utc0) AS quarter, to_char(h.invoice_date_utc0::timestamp with time zone, 'YYYY-MM'::text) AS year_month, to_char(h.invoice_date_utc0::timestamp with time zone, 'Day'::text) AS day_of_week, EXTRACT(dow FROM h.invoice_date_utc0) AS day_of_week_no FROM dw_invoice_line l JOIN dw_invoice_header h ON h.invoice_no_365::text = l.invoice_no_365::text`);

export const vRouteStopInvoiceActive = pgView("v_route_stop_invoice_active", {	routeStopInvoiceId: integer("route_stop_invoice_id"),
	routeStopId: integer("route_stop_id"),
	invoiceNo: varchar("invoice_no"),
	status: varchar(),
	weightKg: doublePrecision("weight_kg"),
	notes: text(),
	isActive: boolean("is_active"),
	effectiveFrom: timestamp("effective_from", { withTimezone: true, mode: 'string' }),
	effectiveTo: timestamp("effective_to", { withTimezone: true, mode: 'string' }),
	changedBy: varchar("changed_by", { length: 64 }),
}).as(sql`SELECT route_stop_invoice_id, route_stop_id, invoice_no, status, weight_kg, notes, is_active, effective_from, effective_to, changed_by FROM route_stop_invoice WHERE is_active = true`);

export const vShipmentOrders = pgView("v_shipment_orders", {	shipmentId: integer("shipment_id"),
	invoiceNo: varchar("invoice_no"),
}).as(sql`SELECT rs.shipment_id, rsi.invoice_no FROM route_stop rs JOIN route_stop_invoice rsi ON rsi.route_stop_id = rs.route_stop_id WHERE rsi.is_active = true`);

export const dwSalesLinesMv = pgMaterializedView("dw_sales_lines_mv", {	saleDate: date("sale_date"),
	customerCode365: text("customer_code_365"),
	itemCode365: text("item_code_365"),
	qty: numeric(),
	netExcl: numeric("net_excl"),
}).as(sql`SELECT h.invoice_date_utc0 AS sale_date, h.customer_code_365::text AS customer_code_365, l.item_code_365::text AS item_code_365, l.quantity::numeric AS qty, COALESCE(l.line_total_incl, 0::numeric) - COALESCE(l.line_total_vat, 0::numeric) AS net_excl FROM dw_invoice_header h JOIN dw_invoice_line l ON l.invoice_no_365::text = h.invoice_no_365::text`);