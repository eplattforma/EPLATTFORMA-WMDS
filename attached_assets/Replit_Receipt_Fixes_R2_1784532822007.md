# Replit Instruction — Receipt Controls Round 2: Fix Payment-Processing Bugs

Context: Round 1 (variance gate, freeze-on-print, void hardening, manual receipts, lookup, two-copy printing) is implemented and reviewed. Testing shows payments still lock up "as before." Root causes below, in priority order.

---

## BUG 1 (P1) — Void does not unlock the payment. The cancel → re-enter flow is dead.

**Symptom:** Office voids a receipt, driver tries to re-enter payment → still gets *"Payment already synced to PS365. Cannot change a committed receipt."* (409). Exactly the pre-change behaviour.

**Root cause:** `api_void_receipt` (`routes_reconciliation.py`) only updates the `CODReceipt`. It never touches the stop's `PaymentEntry`, which remains `is_active=True, ps_status='SUCCESS'`. `create_payment` (`routes_payments.py` line ~60) checks exactly that and returns 409. The freeze-on-print 409 is correctly skipped for VOIDED receipts, but the SUCCESS-sync 409 fires first.

**Fix — in `api_void_receipt`, after setting the receipt to VOIDED:**

```python
# Unlock the stop's payment so the driver can re-enter after reissue
if receipt.route_stop_id:
    active_pe = PaymentEntry.query.filter_by(
        route_stop_id=receipt.route_stop_id, is_active=True).first()
    if active_pe:
        active_pe.is_active = False
        active_pe.updated_at = datetime.utcnow()
```

**Also required — duplicate-stop guard in `create_receipt_core` (`routes_receipts.py`):** the check `ReceiptLog.query.filter_by(route_stop_id=...)` raises "Receipt already exists for this customer" even when that receipt's CODReceipt is voided. When the matching `CODReceipt` for the stop is `VOIDED` (and its PS365 reversal is recorded), the guard must allow the new receipt through. Without this, the re-entered payment fails at the PS365 posting step instead.

**Test:** issue cash payment (synced) → print → office void (with slips + PS365 reversal ref) → driver taps Add Payment → new amount → syncs → prints with "Replaces R‹old›". Must succeed end-to-end.

---

## BUG 2 (P1, design) — No edit window at all for cash/card: PS365 commit still happens at Confirm.

**Symptom:** Driver confirms cash €500 (typo for €50). One second later it is SUCCESS-synced and locked — before any receipt is printed. The freeze-on-print gate never matters for cash/card/same-day cheque because the sync lock always fires first. This is the original complaint, unresolved.

**Fix — defer the PS365 commit to the print moment.** The customer-facing number is `receipt.id` (local); PS365 accepts our `reference_number` whenever we send it. So:

1. In `create_payment` / `services/payments.py`: for cash/card/same-day cheque, create the `PaymentEntry` with `ps_status='NEW'` and **do not call `commit_to_ps365`**. Driver sees status chip "READY" (not SYNCED). Driver can change the payment freely — the existing upsert already handles deactivating the old entry.
2. Trigger `commit_to_ps365` when the receipt is **printed** (the same moment `first_printed_at` is set — this is already the natural lock point) or, as a fallback, at **stop close** if somehow never printed.
3. Keep the existing retry machinery (`PENDING_RETRY`, auto-retry, Retry button) unchanged — it just starts at print time instead of confirm time.
4. `refreshHeaderAndSteps` blockers: accept `NEW/READY` as valid for proceeding to signature/print; the blocker becomes "receipt not printed" rather than "not synced".

**Result:** edit window = from confirm until print. Lock point = print (one lock, one rule, matches the SOP the drivers were given). Nothing changes for online/PDC (already SKIPPED).

**Test:** confirm cash €500 → change to €50 (allowed, no office call) → print → PS365 receives €50 once → further change blocked with the lock message.

---

## BUG 3 (P2) — Changing a payment while PENDING_RETRY can double-post to PS365.

**Scenario:** cash €100 → PS365 timeout → PENDING_RETRY. Driver changes to €90 → new PaymentEntry, **new reference number** → posts €90. But the €100 attempt may have actually landed in PS365 (timeout ≠ failure). PS365 now holds €100 + €90; nothing detects it, because the new reference doesn't collide.

**Fix:** in `create_payment`, if the active entry is `PENDING_RETRY`, reject the change: *"Previous attempt is still being confirmed — retry or wait, then change."* The retry logic already resolves the attempt to SUCCESS or FAILED (including the reference-already-exists reconciliation); FAILED entries can then be changed safely. Note: implementing Bug 2 shrinks this window dramatically but does not remove it — keep this guard.

---

## Polish (P3)

1. **"Request Cancellation" is a dead-end alert.** It only tells the driver to phone. Log it: POST to a small endpoint that stamps `cancellation_requested_at/by` on the receipt and surfaces a badge on the office lookup/exception screens, so requests are visible without a phone call. Keep the alert text as the driver-facing confirmation.
2. **Void error wording:** "printed slips must be recovered" → "customer copies must be recovered" (office copy stays with the driver per SOP; per print event one customer copy exists, so the count check `slips_recovered == print_count` is unchanged).

---

## Verified working in this build (no action)

Variance gate with reason chips + online/PDC exemption; freeze-on-print (UI lock row, API 409, GET lock info); void hardening (slip count + mandatory PS365 reversal ref; reissue blocked until reversal recorded); two-copy printing with cut line and "Replaces R‹old›"; ManualReceiptLog + match endpoint + duplicate book-number guard; receipt lookup page/API; exception report incl. slip-count mismatches; per-stop driver authorization on payment endpoints; startup migrations for all new columns/tables.

## Acceptance tests (round 2)

1. Void → driver re-enters payment at the same stop → succeeds; new receipt prints "Replaces R‹old›". *(Bug 1)*
2. Void of a synced receipt without PS365 reversal ref → still blocked (regression check).
3. Cash confirm → amount editable until print; print posts to PS365 exactly once; edit after print → 409 lock message. *(Bug 2)*
4. Online/PDC → unchanged (SKIPPED, no PS365 post, no variance nag).
5. PENDING_RETRY → "Change Payment" rejected until resolved. *(Bug 3)*
6. PS365 export for a test day shows exactly one receipt per stop across void/reissue and retry scenarios.
