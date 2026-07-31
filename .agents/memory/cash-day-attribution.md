---
name: Cash-day attribution for COD receipts
description: Rerouted invoices delivered early book cash to the future route; settlement counts must reattribute by collection day.
---
Rule: a non-VOIDED COD receipt whose local (Asia/Nicosia) collection date is before its own route's delivery_date, when the same driver has a route dated the collection day, counts in that day-route's cash totals and is excluded from its own route's (shown as "early collection" on both).

**Why:** Rerouting a failed invoice onto tomorrow's route lets the driver deliver it today; the receipt is booked to tomorrow's route, so the driver's daily cash hand-in mismatches both days' settlements (real incident: cash collected a day early appeared "missing" from that day's route).

**How to apply:** All cash-total surfaces (driver settlement form + submit, reconciliation summary/page/print, lifecycle summary) must consume the single attribution helper in the reconciliation service — never sum cod_receipts by route_id alone. Receipt created_at is naive UTC; convert to Cyprus local date before comparing to delivery_date.
