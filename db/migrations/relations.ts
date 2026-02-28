import { relations } from "drizzle-orm/relations";
import { shipments, routeStop, invoices, routeStopInvoice, users, activityLogs, batchPickingSessions, batchPickedItems, codReceipts, codInvoiceAllocations, paymentCustomers, creditTerms, psCustomers, customerDeliverySlots, deliveryDiscrepancies, deliveryDiscrepancyEvents, deliveryEvents, deliveryLines, dwInvoiceHeader, dwInvoiceLine, shifts, idlePeriods, invoiceDeliveryEvents, invoicePaymentExpectations, invoicePostDeliveryCases, invoiceRouteHistory, itemTimeTracking, oiEstimateRuns, oiEstimateLines, orderTimeBreakdown, pickingExceptions, podRecords, purchaseOrders, purchaseOrderLines, receiptLog, receivingLines, receivingSessions, rerouteRequests, routeDeliveryEvents, routeReturnHandover, shipmentOrders, shippingEvents, timeTrackingAlerts, wmsPallet, wmsPalletOrder, paymentEntries, bankTransactions, batchSessionInvoices, invoiceItems } from "./schema";

export const routeStopRelations = relations(routeStop, ({one, many}) => ({
	shipment: one(shipments, {
		fields: [routeStop.shipmentId],
		references: [shipments.id]
	}),
	routeStopInvoices: many(routeStopInvoice),
	invoices: many(invoices),
	deliveryLines: many(deliveryLines),
	invoicePostDeliveryCases: many(invoicePostDeliveryCases),
	invoiceRouteHistories: many(invoiceRouteHistory),
	podRecords: many(podRecords),
	receiptLogs: many(receiptLog),
	routeDeliveryEvents: many(routeDeliveryEvents),
	routeReturnHandovers: many(routeReturnHandover),
	paymentEntries: many(paymentEntries),
	codReceipts: many(codReceipts),
}));

export const shipmentsRelations = relations(shipments, ({many}) => ({
	routeStops: many(routeStop),
	invoices: many(invoices),
	codInvoiceAllocations: many(codInvoiceAllocations),
	deliveryLines: many(deliveryLines),
	invoicePostDeliveryCases: many(invoicePostDeliveryCases),
	invoiceRouteHistories: many(invoiceRouteHistory),
	podRecords: many(podRecords),
	rerouteRequests: many(rerouteRequests),
	routeDeliveryEvents: many(routeDeliveryEvents),
	routeReturnHandovers: many(routeReturnHandover),
	shipmentOrders: many(shipmentOrders),
	wmsPallets: many(wmsPallet),
	codReceipts: many(codReceipts),
}));

export const routeStopInvoiceRelations = relations(routeStopInvoice, ({one}) => ({
	invoice: one(invoices, {
		fields: [routeStopInvoice.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user: one(users, {
		fields: [routeStopInvoice.manifestLockedBy],
		references: [users.username]
	}),
	routeStop: one(routeStop, {
		fields: [routeStopInvoice.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const invoicesRelations = relations(invoices, ({one, many}) => ({
	routeStopInvoices: many(routeStopInvoice),
	user_shippedBy: one(users, {
		fields: [invoices.shippedBy],
		references: [users.username],
		relationName: "invoices_shippedBy_users_username"
	}),
	user_assignedTo: one(users, {
		fields: [invoices.assignedTo],
		references: [users.username],
		relationName: "invoices_assignedTo_users_username"
	}),
	shipment: one(shipments, {
		fields: [invoices.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [invoices.stopId],
		references: [routeStop.routeStopId]
	}),
	activityLogs: many(activityLogs),
	batchPickedItems: many(batchPickedItems),
	codInvoiceAllocations: many(codInvoiceAllocations),
	deliveryDiscrepancies: many(deliveryDiscrepancies),
	deliveryEvents: many(deliveryEvents),
	deliveryLines: many(deliveryLines),
	invoiceDeliveryEvents: many(invoiceDeliveryEvents),
	invoicePaymentExpectations: many(invoicePaymentExpectations),
	invoicePostDeliveryCases: many(invoicePostDeliveryCases),
	invoiceRouteHistories: many(invoiceRouteHistory),
	itemTimeTrackings: many(itemTimeTracking),
	oiEstimateRuns: many(oiEstimateRuns),
	orderTimeBreakdowns: many(orderTimeBreakdown),
	pickingExceptions: many(pickingExceptions),
	rerouteRequests: many(rerouteRequests),
	routeReturnHandovers: many(routeReturnHandover),
	shipmentOrders: many(shipmentOrders),
	shippingEvents: many(shippingEvents),
	timeTrackingAlerts: many(timeTrackingAlerts),
	batchSessionInvoices: many(batchSessionInvoices),
	invoiceItems: many(invoiceItems),
}));

export const usersRelations = relations(users, ({many}) => ({
	routeStopInvoices: many(routeStopInvoice),
	invoices_shippedBy: many(invoices, {
		relationName: "invoices_shippedBy_users_username"
	}),
	invoices_assignedTo: many(invoices, {
		relationName: "invoices_assignedTo_users_username"
	}),
	activityLogs: many(activityLogs),
	batchPickingSessions_assignedTo: many(batchPickingSessions, {
		relationName: "batchPickingSessions_assignedTo_users_username"
	}),
	batchPickingSessions_createdBy: many(batchPickingSessions, {
		relationName: "batchPickingSessions_createdBy_users_username"
	}),
	deliveryDiscrepancies_reportedBy: many(deliveryDiscrepancies, {
		relationName: "deliveryDiscrepancies_reportedBy_users_username"
	}),
	deliveryDiscrepancies_resolvedBy: many(deliveryDiscrepancies, {
		relationName: "deliveryDiscrepancies_resolvedBy_users_username"
	}),
	deliveryDiscrepancies_validatedBy: many(deliveryDiscrepancies, {
		relationName: "deliveryDiscrepancies_validatedBy_users_username"
	}),
	deliveryDiscrepancies_warehouseCheckedBy: many(deliveryDiscrepancies, {
		relationName: "deliveryDiscrepancies_warehouseCheckedBy_users_username"
	}),
	deliveryDiscrepancyEvents: many(deliveryDiscrepancyEvents),
	deliveryEvents: many(deliveryEvents),
	shifts_adjustmentBy: many(shifts, {
		relationName: "shifts_adjustmentBy_users_username"
	}),
	shifts_pickerUsername: many(shifts, {
		relationName: "shifts_pickerUsername_users_username"
	}),
	invoiceDeliveryEvents: many(invoiceDeliveryEvents),
	itemTimeTrackings: many(itemTimeTracking),
	orderTimeBreakdowns: many(orderTimeBreakdown),
	pickingExceptions: many(pickingExceptions),
	podRecords: many(podRecords),
	purchaseOrders_archivedBy: many(purchaseOrders, {
		relationName: "purchaseOrders_archivedBy_users_username"
	}),
	purchaseOrders_downloadedBy: many(purchaseOrders, {
		relationName: "purchaseOrders_downloadedBy_users_username"
	}),
	receiptLogs: many(receiptLog),
	receivingSessions: many(receivingSessions),
	routeDeliveryEvents: many(routeDeliveryEvents),
	routeReturnHandovers_driverUsername: many(routeReturnHandover, {
		relationName: "routeReturnHandover_driverUsername_users_username"
	}),
	routeReturnHandovers_receivedBy: many(routeReturnHandover, {
		relationName: "routeReturnHandover_receivedBy_users_username"
	}),
	shippingEvents: many(shippingEvents),
	timeTrackingAlerts_pickerUsername: many(timeTrackingAlerts, {
		relationName: "timeTrackingAlerts_pickerUsername_users_username"
	}),
	timeTrackingAlerts_resolvedBy: many(timeTrackingAlerts, {
		relationName: "timeTrackingAlerts_resolvedBy_users_username"
	}),
	codReceipts_driverUsername: many(codReceipts, {
		relationName: "codReceipts_driverUsername_users_username"
	}),
	codReceipts_lockedBy: many(codReceipts, {
		relationName: "codReceipts_lockedBy_users_username"
	}),
	codReceipts_voidedBy: many(codReceipts, {
		relationName: "codReceipts_voidedBy_users_username"
	}),
}));

export const activityLogsRelations = relations(activityLogs, ({one}) => ({
	invoice: one(invoices, {
		fields: [activityLogs.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user: one(users, {
		fields: [activityLogs.pickerUsername],
		references: [users.username]
	}),
}));

export const batchPickingSessionsRelations = relations(batchPickingSessions, ({one, many}) => ({
	user_assignedTo: one(users, {
		fields: [batchPickingSessions.assignedTo],
		references: [users.username],
		relationName: "batchPickingSessions_assignedTo_users_username"
	}),
	user_createdBy: one(users, {
		fields: [batchPickingSessions.createdBy],
		references: [users.username],
		relationName: "batchPickingSessions_createdBy_users_username"
	}),
	batchPickedItems: many(batchPickedItems),
	batchSessionInvoices: many(batchSessionInvoices),
	invoiceItems: many(invoiceItems),
}));

export const batchPickedItemsRelations = relations(batchPickedItems, ({one}) => ({
	batchPickingSession: one(batchPickingSessions, {
		fields: [batchPickedItems.batchSessionId],
		references: [batchPickingSessions.id]
	}),
	invoice: one(invoices, {
		fields: [batchPickedItems.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const codInvoiceAllocationsRelations = relations(codInvoiceAllocations, ({one, many}) => ({
	codReceipt: one(codReceipts, {
		fields: [codInvoiceAllocations.codReceiptId],
		references: [codReceipts.id]
	}),
	invoice: one(invoices, {
		fields: [codInvoiceAllocations.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	shipment: one(shipments, {
		fields: [codInvoiceAllocations.routeId],
		references: [shipments.id]
	}),
	bankTransactions: many(bankTransactions),
}));

export const codReceiptsRelations = relations(codReceipts, ({one, many}) => ({
	codInvoiceAllocations: many(codInvoiceAllocations),
	user_driverUsername: one(users, {
		fields: [codReceipts.driverUsername],
		references: [users.username],
		relationName: "codReceipts_driverUsername_users_username"
	}),
	user_lockedBy: one(users, {
		fields: [codReceipts.lockedBy],
		references: [users.username],
		relationName: "codReceipts_lockedBy_users_username"
	}),
	codReceipt: one(codReceipts, {
		fields: [codReceipts.replacedByCodReceiptId],
		references: [codReceipts.id],
		relationName: "codReceipts_replacedByCodReceiptId_codReceipts_id"
	}),
	codReceipts: many(codReceipts, {
		relationName: "codReceipts_replacedByCodReceiptId_codReceipts_id"
	}),
	shipment: one(shipments, {
		fields: [codReceipts.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [codReceipts.routeStopId],
		references: [routeStop.routeStopId]
	}),
	user_voidedBy: one(users, {
		fields: [codReceipts.voidedBy],
		references: [users.username],
		relationName: "codReceipts_voidedBy_users_username"
	}),
}));

export const creditTermsRelations = relations(creditTerms, ({one}) => ({
	paymentCustomer: one(paymentCustomers, {
		fields: [creditTerms.customerCode],
		references: [paymentCustomers.code]
	}),
}));

export const paymentCustomersRelations = relations(paymentCustomers, ({many}) => ({
	creditTerms: many(creditTerms),
}));

export const customerDeliverySlotsRelations = relations(customerDeliverySlots, ({one}) => ({
	psCustomer: one(psCustomers, {
		fields: [customerDeliverySlots.customerCode365],
		references: [psCustomers.customerCode365]
	}),
}));

export const psCustomersRelations = relations(psCustomers, ({many}) => ({
	customerDeliverySlots: many(customerDeliverySlots),
}));

export const deliveryDiscrepanciesRelations = relations(deliveryDiscrepancies, ({one, many}) => ({
	invoice: one(invoices, {
		fields: [deliveryDiscrepancies.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user_reportedBy: one(users, {
		fields: [deliveryDiscrepancies.reportedBy],
		references: [users.username],
		relationName: "deliveryDiscrepancies_reportedBy_users_username"
	}),
	user_resolvedBy: one(users, {
		fields: [deliveryDiscrepancies.resolvedBy],
		references: [users.username],
		relationName: "deliveryDiscrepancies_resolvedBy_users_username"
	}),
	user_validatedBy: one(users, {
		fields: [deliveryDiscrepancies.validatedBy],
		references: [users.username],
		relationName: "deliveryDiscrepancies_validatedBy_users_username"
	}),
	user_warehouseCheckedBy: one(users, {
		fields: [deliveryDiscrepancies.warehouseCheckedBy],
		references: [users.username],
		relationName: "deliveryDiscrepancies_warehouseCheckedBy_users_username"
	}),
	deliveryDiscrepancyEvents: many(deliveryDiscrepancyEvents),
}));

export const deliveryDiscrepancyEventsRelations = relations(deliveryDiscrepancyEvents, ({one}) => ({
	user: one(users, {
		fields: [deliveryDiscrepancyEvents.actor],
		references: [users.username]
	}),
	deliveryDiscrepancy: one(deliveryDiscrepancies, {
		fields: [deliveryDiscrepancyEvents.discrepancyId],
		references: [deliveryDiscrepancies.id]
	}),
}));

export const deliveryEventsRelations = relations(deliveryEvents, ({one}) => ({
	user: one(users, {
		fields: [deliveryEvents.actor],
		references: [users.username]
	}),
	invoice: one(invoices, {
		fields: [deliveryEvents.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const deliveryLinesRelations = relations(deliveryLines, ({one}) => ({
	invoice: one(invoices, {
		fields: [deliveryLines.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	shipment: one(shipments, {
		fields: [deliveryLines.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [deliveryLines.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const dwInvoiceLineRelations = relations(dwInvoiceLine, ({one}) => ({
	dwInvoiceHeader: one(dwInvoiceHeader, {
		fields: [dwInvoiceLine.invoiceNo365],
		references: [dwInvoiceHeader.invoiceNo365]
	}),
}));

export const dwInvoiceHeaderRelations = relations(dwInvoiceHeader, ({many}) => ({
	dwInvoiceLines: many(dwInvoiceLine),
}));

export const shiftsRelations = relations(shifts, ({one, many}) => ({
	user_adjustmentBy: one(users, {
		fields: [shifts.adjustmentBy],
		references: [users.username],
		relationName: "shifts_adjustmentBy_users_username"
	}),
	user_pickerUsername: one(users, {
		fields: [shifts.pickerUsername],
		references: [users.username],
		relationName: "shifts_pickerUsername_users_username"
	}),
	idlePeriods: many(idlePeriods),
}));

export const idlePeriodsRelations = relations(idlePeriods, ({one}) => ({
	shift: one(shifts, {
		fields: [idlePeriods.shiftId],
		references: [shifts.id]
	}),
}));

export const invoiceDeliveryEventsRelations = relations(invoiceDeliveryEvents, ({one}) => ({
	user: one(users, {
		fields: [invoiceDeliveryEvents.actor],
		references: [users.username]
	}),
	invoice: one(invoices, {
		fields: [invoiceDeliveryEvents.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const invoicePaymentExpectationsRelations = relations(invoicePaymentExpectations, ({one}) => ({
	invoice: one(invoices, {
		fields: [invoicePaymentExpectations.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const invoicePostDeliveryCasesRelations = relations(invoicePostDeliveryCases, ({one}) => ({
	invoice: one(invoices, {
		fields: [invoicePostDeliveryCases.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	shipment: one(shipments, {
		fields: [invoicePostDeliveryCases.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [invoicePostDeliveryCases.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const invoiceRouteHistoryRelations = relations(invoiceRouteHistory, ({one}) => ({
	invoice: one(invoices, {
		fields: [invoiceRouteHistory.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	shipment: one(shipments, {
		fields: [invoiceRouteHistory.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [invoiceRouteHistory.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const itemTimeTrackingRelations = relations(itemTimeTracking, ({one}) => ({
	invoice: one(invoices, {
		fields: [itemTimeTracking.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user: one(users, {
		fields: [itemTimeTracking.pickerUsername],
		references: [users.username]
	}),
}));

export const oiEstimateRunsRelations = relations(oiEstimateRuns, ({one, many}) => ({
	invoice: one(invoices, {
		fields: [oiEstimateRuns.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	oiEstimateLines: many(oiEstimateLines),
}));

export const oiEstimateLinesRelations = relations(oiEstimateLines, ({one}) => ({
	oiEstimateRun: one(oiEstimateRuns, {
		fields: [oiEstimateLines.runId],
		references: [oiEstimateRuns.id]
	}),
}));

export const orderTimeBreakdownRelations = relations(orderTimeBreakdown, ({one}) => ({
	invoice: one(invoices, {
		fields: [orderTimeBreakdown.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user: one(users, {
		fields: [orderTimeBreakdown.pickerUsername],
		references: [users.username]
	}),
}));

export const pickingExceptionsRelations = relations(pickingExceptions, ({one}) => ({
	invoice: one(invoices, {
		fields: [pickingExceptions.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user: one(users, {
		fields: [pickingExceptions.pickerUsername],
		references: [users.username]
	}),
}));

export const podRecordsRelations = relations(podRecords, ({one}) => ({
	user: one(users, {
		fields: [podRecords.collectedBy],
		references: [users.username]
	}),
	shipment: one(shipments, {
		fields: [podRecords.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [podRecords.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const purchaseOrdersRelations = relations(purchaseOrders, ({one, many}) => ({
	user_archivedBy: one(users, {
		fields: [purchaseOrders.archivedBy],
		references: [users.username],
		relationName: "purchaseOrders_archivedBy_users_username"
	}),
	user_downloadedBy: one(users, {
		fields: [purchaseOrders.downloadedBy],
		references: [users.username],
		relationName: "purchaseOrders_downloadedBy_users_username"
	}),
	purchaseOrderLines: many(purchaseOrderLines),
	receivingSessions: many(receivingSessions),
}));

export const purchaseOrderLinesRelations = relations(purchaseOrderLines, ({one, many}) => ({
	purchaseOrder: one(purchaseOrders, {
		fields: [purchaseOrderLines.purchaseOrderId],
		references: [purchaseOrders.id]
	}),
	receivingLines: many(receivingLines),
}));

export const receiptLogRelations = relations(receiptLog, ({one}) => ({
	user: one(users, {
		fields: [receiptLog.driverUsername],
		references: [users.username]
	}),
	routeStop: one(routeStop, {
		fields: [receiptLog.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const receivingLinesRelations = relations(receivingLines, ({one}) => ({
	purchaseOrderLine: one(purchaseOrderLines, {
		fields: [receivingLines.poLineId],
		references: [purchaseOrderLines.id]
	}),
	receivingSession: one(receivingSessions, {
		fields: [receivingLines.sessionId],
		references: [receivingSessions.id]
	}),
}));

export const receivingSessionsRelations = relations(receivingSessions, ({one, many}) => ({
	receivingLines: many(receivingLines),
	user: one(users, {
		fields: [receivingSessions.operator],
		references: [users.username]
	}),
	purchaseOrder: one(purchaseOrders, {
		fields: [receivingSessions.purchaseOrderId],
		references: [purchaseOrders.id]
	}),
}));

export const rerouteRequestsRelations = relations(rerouteRequests, ({one}) => ({
	shipment: one(shipments, {
		fields: [rerouteRequests.assignedRouteId],
		references: [shipments.id]
	}),
	invoice: one(invoices, {
		fields: [rerouteRequests.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const routeDeliveryEventsRelations = relations(routeDeliveryEvents, ({one}) => ({
	user: one(users, {
		fields: [routeDeliveryEvents.actorUsername],
		references: [users.username]
	}),
	shipment: one(shipments, {
		fields: [routeDeliveryEvents.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [routeDeliveryEvents.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const routeReturnHandoverRelations = relations(routeReturnHandover, ({one}) => ({
	user_driverUsername: one(users, {
		fields: [routeReturnHandover.driverUsername],
		references: [users.username],
		relationName: "routeReturnHandover_driverUsername_users_username"
	}),
	invoice: one(invoices, {
		fields: [routeReturnHandover.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user_receivedBy: one(users, {
		fields: [routeReturnHandover.receivedBy],
		references: [users.username],
		relationName: "routeReturnHandover_receivedBy_users_username"
	}),
	shipment: one(shipments, {
		fields: [routeReturnHandover.routeId],
		references: [shipments.id]
	}),
	routeStop: one(routeStop, {
		fields: [routeReturnHandover.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const shipmentOrdersRelations = relations(shipmentOrders, ({one}) => ({
	invoice: one(invoices, {
		fields: [shipmentOrders.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	shipment: one(shipments, {
		fields: [shipmentOrders.shipmentId],
		references: [shipments.id]
	}),
}));

export const shippingEventsRelations = relations(shippingEvents, ({one}) => ({
	user: one(users, {
		fields: [shippingEvents.actor],
		references: [users.username]
	}),
	invoice: one(invoices, {
		fields: [shippingEvents.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const timeTrackingAlertsRelations = relations(timeTrackingAlerts, ({one}) => ({
	invoice: one(invoices, {
		fields: [timeTrackingAlerts.invoiceNo],
		references: [invoices.invoiceNo]
	}),
	user_pickerUsername: one(users, {
		fields: [timeTrackingAlerts.pickerUsername],
		references: [users.username],
		relationName: "timeTrackingAlerts_pickerUsername_users_username"
	}),
	user_resolvedBy: one(users, {
		fields: [timeTrackingAlerts.resolvedBy],
		references: [users.username],
		relationName: "timeTrackingAlerts_resolvedBy_users_username"
	}),
}));

export const wmsPalletRelations = relations(wmsPallet, ({one, many}) => ({
	shipment: one(shipments, {
		fields: [wmsPallet.shipmentId],
		references: [shipments.id]
	}),
	wmsPalletOrders: many(wmsPalletOrder),
}));

export const wmsPalletOrderRelations = relations(wmsPalletOrder, ({one}) => ({
	wmsPallet: one(wmsPallet, {
		fields: [wmsPalletOrder.palletId],
		references: [wmsPallet.palletId]
	}),
}));

export const paymentEntriesRelations = relations(paymentEntries, ({one}) => ({
	routeStop: one(routeStop, {
		fields: [paymentEntries.routeStopId],
		references: [routeStop.routeStopId]
	}),
}));

export const bankTransactionsRelations = relations(bankTransactions, ({one}) => ({
	codInvoiceAllocation: one(codInvoiceAllocations, {
		fields: [bankTransactions.matchedAllocationId],
		references: [codInvoiceAllocations.id]
	}),
}));

export const batchSessionInvoicesRelations = relations(batchSessionInvoices, ({one}) => ({
	batchPickingSession: one(batchPickingSessions, {
		fields: [batchSessionInvoices.batchSessionId],
		references: [batchPickingSessions.id]
	}),
	invoice: one(invoices, {
		fields: [batchSessionInvoices.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));

export const invoiceItemsRelations = relations(invoiceItems, ({one}) => ({
	batchPickingSession: one(batchPickingSessions, {
		fields: [invoiceItems.lockedByBatchId],
		references: [batchPickingSessions.id]
	}),
	invoice: one(invoices, {
		fields: [invoiceItems.invoiceNo],
		references: [invoices.invoiceNo]
	}),
}));