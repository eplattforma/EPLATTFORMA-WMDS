# Replit Instruction — Receipt Controls Round 4: Edit window must survive stop close

**Symptom:** driver confirms a cash payment, closes the delivery WITHOUT printing, then cannot change the amount. The deferred-commit design (R2) says the edit window lasts until print — but two things end it early at stop close:

1. The deliver wizard route (`routes_driver.py` ~line 488) redirects any closed stop to the stops list ("This stop has already been closed"), so there is **no UI path** to the payment wizard after close.
2. Even via API, changing the `PaymentEntry` after close would be insufficient: `closeStop` copies the payment into the `CODReceipt` (`received_amount`, `payment_method`, cheque fields, `variance`, `variance_reason`) and creates `CODInvoiceAllocation` rows. The receipt that later prints/posts reads from `CODReceipt`, not `PaymentEntry`.

## Fix — "Edit Payment" for closed, unprinted, unsynced stops

**Eligibility (hard rule, enforce server-side):** the stop's live CODReceipt has `first_printed_at IS NULL` **and** `ps365_reference_number IS NULL` **and** status not VOIDED, and the route is not yet submitted/reconciled. Printed or posted → existing lock applies (409 with the lock message).

### Backend

Extend `create_payment` (`routes_payments.py`): after `upsert_active_payment`, if a live unprinted/unsynced `CODReceipt` exists for the stop, update it in the same transaction:

- `received_amount` = new amount, `payment_method` = new method, `cheque_number`/`cheque_date`, `variance` = received − expected, `variance_reason` from payload, `doc_type` recomputed via `decide_commit_and_doc`
- Rebuild the `CODInvoiceAllocation` rows for the receipt using the same allocation logic as `closeStop` (smallest-due-first; single invoice gets full amount)
- Do NOT touch `expected_amount`, invoice list, or discrepancies — this is a payment correction, not a redelivery

This keeps one endpoint as the single writer for payment changes; no new endpoint needed.

### UI (stops list — `templates/driver/stops_list.html`)

On each closed stop that is eligible (expose the flags in the stops list data): next to "Reprint", show **"Edit Payment"**. It opens the existing payment wizard modal (method → keypad → variance gate → confirm) scoped to that stop, posting to the same `/api/route-stops/<id>/payment`. On success, show the updated amount inline.

Not eligible (printed or posted): show the lock chip instead — "Locked (printed)" with the Request Cancellation action, same as in the wizard. Never a dead disabled button.

### Guards to keep intact (regression)

- Freeze-on-print 409 and synced-SUCCESS 409 in `create_payment` still fire first — eligibility above is a *narrower* path, not a bypass.
- PENDING_RETRY change guard unchanged.
- Route submit fallback (`sync_receipt_ps365_at_print` on route close) unchanged — after route submit, no edits.

## Acceptance tests

1. Confirm cash → close stop (no print) → stops list shows "Edit Payment" → change €500→€50 with variance reason → receipt prints €50, posts €50 to PS365 once; allocations sum to €50.
2. Same but print first → "Edit Payment" absent, lock chip shown; API returns 409.
3. Edit changes cash→cheque (number+date) → doc_type/cheque fields correct on print.
4. After route submit → no Edit Payment on any stop.
5. Void/reissue path still works for printed receipts (regression).
