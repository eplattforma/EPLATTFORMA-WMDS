# Replit Instruction — Receipt Controls Round 4: Edit window must survive stop close

**Symptom:** driver confirms a cash payment, closes the delivery WITHOUT printing, then cannot change the amount. The deferred-commit design (R2) says the edit window lasts until print — but two things end it early at stop close:

1. The deliver wizard route (`routes_driver.py` ~line 488) redirects any closed stop to the stops list ("This stop has already been closed"), so there is **no UI path** to the payment wizard after close.
2. Even via API, changing the `PaymentEntry` after close would be insufficient: `closeStop` copies the payment into the `CODReceipt` (`received_amount`, `payment_method`, cheque fields, `variance`, `variance_reason`) and creates `CODInvoiceAllocation` rows. The receipt that later prints/posts reads from `CODReceipt`, not `PaymentEntry`.

## Fix — "Edit Payment" for closed, unprinted, unsynced stops

**Eligibility (hard rule, enforce server-side):** the stop's live CODReceipt has `first_printed_at IS NULL` **and** `ps365_reference_number IS NULL` **and** status not VOIDED, and the route is not yet submitted/reconciled. Printed or posted → existing lock applies (409 with the lock message).

This applies at **any point during the route**: the driver can be several stops further along and still edit an earlier stop's unprinted payment from the stops list. Print is the only lock; stop order and stop closure are irrelevant.

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

## Part 2 — Close the cancellation-request loop + status visibility (stops list)

Testing showed three visibility gaps on `templates/driver/stops_list.html`:

**A. Cancellation request goes nowhere visible, and the driver gets no feedback.**
1. Office side: add a badge with the count of open cancellation requests on the reconciliation nav/header (receipts with `cancellation_requested_at` set, status not VOIDED), linking to the exception report. No email/SMS — just make it impossible to miss on screens the office already uses.
2. Driver side, on the stop card, replace the one-shot alert with a persistent state driven by the receipt: `cancellation_requested_at` set → amber chip "Cancellation requested — waiting for office"; receipt VOIDED with no live replacement → red chip "Voided by office"; replacement DRAFT exists (`replaced_by` chain) → green button **"New receipt ready — Print"** that prints the replacement. The stops list already re-renders on refresh; no push needed, refresh/poll is enough.

**B. PS365 status invisible on the stop card.** Printed = posted in this build, but show it: on the "Locked (printed)" chip line, display the receipt number and reference — e.g. `R1000123 · SYNCED`. Data is already on `cod_receipts[0].ps365_reference_number`. When `print_count > 1`, append the count: `R1000123 · SYNCED · printed ×3` — reprints multiply customer copies in the wild, so the card should show it; a single print shows no count. For an unprinted/unsynced receipt (after R4 Part 1), the card shows "Edit Payment" instead — the absence of the R-number IS the "not sent yet" signal, so no extra chip needed.

**D. No indication when a receipt was NOT printed.** A delivered stop with an unprinted receipt (`first_printed_at IS NULL`) looks nearly identical to a printed one — and the button even says "Reprint". The customer has no paper and nobody is told. Fix:
1. Stop card: loud amber chip **"⚠ NOT PRINTED — customer has no receipt"** on any delivered, non-credit stop whose live receipt has `first_printed_at IS NULL`. Button label becomes **"Print Receipt"** (first print) — "Reprint" only when `print_count > 0`.
2. Route settlement/submit screen: list all unprinted receipts ("2 receipts not printed: stops 3, 7") and require an explicit confirm to proceed — the driver should either print, log a manual book receipt, or knowingly confirm. Confirmed-unprinted receipts appear on the office exception report per driver.
3. The route-close PS365 fallback stays (money must be booked) — this item is about the customer copy, not the posting.

**C. Reprint button looks disabled.** `btn-outline-secondary` on the light-green delivered card reads as inactive. Make it solid (`btn-secondary` or `btn-dark`), same size as Request Cancellation. If `print.png` returns an error, surface the server's message text in the alert instead of the generic "Could not load receipt" — the driver needs to know "PS365 sync failed, retry" vs a real error.

## Acceptance tests

1. Confirm cash → close stop (no print) → stops list shows "Edit Payment" → change €500→€50 with variance reason → receipt prints €50, posts €50 to PS365 once; allocations sum to €50.
2. Same but print first → "Edit Payment" absent, lock chip shown; API returns 409.
3. Edit changes cash→cheque (number+date) → doc_type/cheque fields correct on print.
4. After route submit → no Edit Payment on any stop.
5. Void/reissue path still works for printed receipts (regression).
6. Request cancellation → amber chip on stop card + count badge on office nav; after office void+reissue, driver's card shows "New receipt ready — Print"; printing it completes the loop.
7. Printed stop card shows `R‹number› · SYNCED`; unprinted eligible stop shows "Edit Payment" instead.
8. Reprint button visibly active; server error messages pass through to the driver alert.
9. Delivered stop, receipt never printed → "NOT PRINTED" chip + "Print Receipt" label; after first print, chip replaced by `R‹number› · SYNCED` and label becomes "Reprint".
10. Route submit with unprinted receipts → blocking confirm listing the stops; confirmed ones appear on the exception report.
