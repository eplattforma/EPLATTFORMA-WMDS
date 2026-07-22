# Replit Master Instruction — Driver Receipt Controls (do in this order)

This is the single, ordered work list. **Do the phases in sequence — Phase 0 first and separately, because it may change what the later phases need.** Do not start Phase 1 until Phase 0's report is reviewed.

**Background (already built and verified — do not redo):** variance gate with reason chips, freeze-on-print, admin void hardening (customer-copy count + mandatory PS365 cancellation reference), two-copy printing, manual receipt log, receipt lookup, exception report, deferred PS365 commit at print (posting happens when the receipt prints; route close is only a fallback for never-printed receipts), void unlocks the stop's PaymentEntry, PENDING_RETRY change guard.

**Priority order:** Phase 2 (reissue-amount bug — blocking a live correction) → Phase 1 (correctness) → Phase 3 (driver/reconciliation UX). Phase 0 is complete; see findings below.

---

# PHASE 0 — COMPLETE. Diagnosis done, no action required.

Investigated: "printed receipts are not reaching PS365". **Conclusion: the PS365 sync is not broken.**

- 27 official receipts synced in the last 7 days; PS365 answering HTTP 200 consistently.
- **Zero** official receipts are printed-but-unsynced (prod and dev).
- All 258 printed-without-reference receipts are SKIP-mode documents **by design**: 234 online notices (€0.00) and 24 post-dated cheque acknowledgements. `decide_commit_and_doc` deliberately marks `online` and future-dated `cheque` as SKIP.
- Payment entries: 1,157 SUCCESS, 276 SKIPPED, 0 FAILED, 0 PENDING_RETRY, no error text anywhere.
- All four PS365 env vars set; `create_receipt_core` reads `POWERSOFT_TOKEN`/`POWERSOFT_BASE` — correct, no mismatch.
- Customers and driver payment-type codes valid (only the unused "driver1" test account lacks one).
- The single genuine posting failure ever seen (reference-number collision, PS365 error 312) was fixed in an earlier session.

## DECISION — Deferred payments are never posted by the app. Leave exactly as is.

**The rule:** money in hand now (cash, card, same-day cheque) → the app posts to PS365 at print. Money arriving later (**online transfer and post-dated cheque**) → the app prints a notice/acknowledgement only and **never** posts. The accountant records these in PS365 manually when the money actually arrives. Post-dated cheques are treated exactly like online payments.

**Do not** build a maturity job, a PDC worklist, a "chase unposted online payments" feature, or any automatic posting for either type. Note that `send_cod_receipt` (`routes_receipts.py:455`) *would* post a PDC once past its cheque date — it is currently unreachable (no template calls it, no scheduler job). **Leave it unreachable.** Wiring it up would double-count against the accountant's manual entries (PS365 already tracks these — the customer-balances aging reads a `postdated_payments` field).

If anyone later reports "online/PDC receipts never reach PS365" — that is by design, not a bug. Confirm with the accountant before touching it.

**Consequence for reporting:** online notices and PDC acknowledgements will always show as printed-without-a-PS365-reference. Any current or future report that flags "printed but not synced" must exclude `doc_type` of `online_notice` and `pdc_ack`, or it will raise 258+ false alarms.

---

# PHASE 1 — Correctness fixes (was R3)

## 1.1 (P2) — Receipt lookup cannot find R-numbers
`api_receipt_lookup` (`routes_reconciliation.py`) strips the leading "R" then matches `ps365_reference_number == q`, but references are stored **with** the R prefix (`R1000001`). So "R1000001" finds nothing.

```python
raw = (request.args.get('q') or '').strip()
stripped = raw.lstrip('Rr#')
receipt = None
if stripped.isdigit():
    receipt = db.session.get(CODReceipt, int(stripped))
if not receipt:
    receipt = CODReceipt.query.filter(
        CODReceipt.ps365_reference_number.in_([raw, stripped, f'R{stripped}'])
    ).first()
```
**Test:** "R1000001", "1000001", and a plain `receipt.id` all resolve to the same receipt.

## 1.2 (P2) — Print paths behave differently when PS365 is down
`print_receipt_png_by_id` correctly refuses (503) to print an official receipt with no PS365 reference. But `print_receipt`, `print_receipt_80mm`, `print_stop_receipt` call the sync non-blocking and print anyway — so a receipt is blocked on one path, printable on another, and shows `receipt.id` on one vs the R-number on another.

**Fix:** in those HTML print views, for `doc_type == 'official'` and non-VOIDED receipts, after `sync_receipt_ps365_at_print`, if still no `ps365_reference_number`, render an error page ("Receipt not registered in Powersoft yet — retry or use the manual book") instead of the receipt. **Do not set `first_printed_at` / bump `print_count` when the post failed** — no lock without a successful post. Preview mode unaffected.

**Test:** with PS365 unreachable, all four print paths refuse an official receipt; online/PDC docs still print.

---

# PHASE 2 — Reissue must accept the corrected amount (BUG — was R4 Part 4)

`doReissue()` in `templates/reconciliation/receipt_lookup.html` posts an **empty body** to `/api/receipts/<id>/reissue`. The endpoint falls back to `old_receipt.received_amount`, so reissue **clones the wrong amount** — no way to correct it from the UI. This is the common case ("posted €96.16, should be €92").

**Fix (UI only — the API already reads `received_amount` / `expected_amount` / `payment_method` / `note` from the body):**
1. Reissue action reveals a small form pre-filled from the voided receipt: **Corrected amount** (`received_amount`, editable, required), method (editable), optional note; show the original beside it ("was €96.16").
2. Send those fields in the POST body instead of `{}`.
3. Validate amount > 0; if unchanged, warn "amount is the same as the voided receipt — continue?".
4. Reissued receipt is DRAFT/unprinted/unsynced; posts to PS365 at print with the corrected amount, exactly once (the void's recorded PS365 reversal ref satisfies the double-post guard).

**Test:** void €96.16 (with PS365 reversal ref) → Reissue → enter €92 → new receipt is €92, prints "Replaces R‹old›", posts €92 once.

---

# PHASE 3 — Driver & reconciliation UX (was R4 Parts 1–3)

## 3.1 — "Edit Payment" until print (edit window must survive stop close)

**Symptom:** driver confirms cash, closes delivery without printing, then can't change the amount. Two causes: the deliver wizard route (`routes_driver.py` ~line 488) redirects any closed stop away; and `closeStop` copies the payment into the `CODReceipt` + `CODInvoiceAllocation`, which is what later prints/posts — not the `PaymentEntry`.

**Eligibility (enforce server-side):** stop's live CODReceipt has `first_printed_at IS NULL` AND `ps365_reference_number IS NULL` AND status not VOIDED, and route not yet submitted. Applies **at any point in the route** — the driver can edit an earlier stop's unprinted payment from the stops list. Print is the only lock; stop order/closure irrelevant. Printed or posted → existing 409 lock.

**Backend** — extend `create_payment` (`routes_payments.py`): after `upsert_active_payment`, if a live unprinted/unsynced CODReceipt exists for the stop, update it in the same transaction: `received_amount`, `payment_method`, cheque fields, `variance`, `variance_reason`, `doc_type` (recompute via `decide_commit_and_doc`); rebuild `CODInvoiceAllocation` rows using the same allocation logic as `closeStop` (smallest-due-first; single invoice gets full amount). Do NOT touch `expected_amount`, invoice list, or discrepancies. One endpoint stays the single writer.

**UI** (`templates/driver/stops_list.html`): on each eligible closed stop, show **"Edit Payment"** next to Reprint; it opens the existing payment wizard scoped to the stop, posting to the same `/api/route-stops/<id>/payment`. Not eligible → lock chip + Request Cancellation, never a dead button.

**Regression guards:** freeze-on-print 409 and synced-SUCCESS 409 still fire first; PENDING_RETRY guard unchanged; route-submit fallback unchanged.

## 3.2 — Close the cancellation-request loop + status visibility (`stops_list.html`)

**A. Cancellation request feedback.**
- Office: count badge of open cancellation requests (`cancellation_requested_at` set, not VOIDED) on the reconciliation nav, linking to the exception report.
- Driver: on the stop card, persistent state driven by the receipt — `cancellation_requested_at` set → amber "Cancellation requested — waiting for office"; VOIDED with no live replacement → red "Voided by office"; replacement DRAFT exists (`replaced_by` chain) → green **"New receipt ready — Print"** that prints the replacement. Refresh/poll is enough.

**B. PS365 status on the card.** On the "Locked (printed)" line show `R‹number› · SYNCED`; when `print_count > 1` append `· printed ×N`. Unprinted/unsynced receipt shows "Edit Payment" instead (absence of the R-number is the "not sent" signal).

**C. Reprint button.** `btn-outline-secondary` on the green card reads as disabled — make it solid (`btn-secondary`/`btn-dark`), same size as Request Cancellation. On `print.png` error, surface the server's message text (PS365 sync failed vs real error), not the generic "Could not load receipt".

**D. No indication when a receipt was NOT printed** (customer left with no paper).
1. Loud amber chip **"⚠ NOT PRINTED — customer has no receipt"** on any delivered non-credit stop whose live receipt has `first_printed_at IS NULL`. Button label **"Print Receipt"** on first print; "Reprint" only when `print_count > 0`. This is about the paper only — it applies to online notices and PDC acknowledgements too (the customer must still leave with a document), and must NOT be worded or coloured as a PS365 sync problem, since those two types never post by design.
2. Route submit screen lists all unprinted receipts and requires an explicit confirm to proceed (print / log manual book receipt / knowingly confirm). Confirmed-unprinted appear on the exception report.
3. Route-close PS365 fallback stays (money must be booked) — this is about the customer copy, not the posting.

## 3.3 — Show voided receipts on route reconciliation

`routes_routes.py` (~line 568) excludes `status = 'VOIDED'` from the reconciliation screen. Correct for totals, wrong for display — reconciliation is where the driver hands back cancelled customer copies, so the reconciler must see the voids.
1. Include voided receipts; render as a distinct struck-through **VOIDED** row: reason, voided by/at, copies expected (`print_count`) vs recovered (`slips_recovered`), PS365 cancellation ref, link to replacement.
2. Keep them out of all cash/variance totals (math unchanged).
3. If `slips_recovered` null or `< print_count`, flag: "copies outstanding — collect from driver."

---

# Acceptance tests (run after Phases 1–3)

1. Confirm cash → close stop (no print) → stops list shows "Edit Payment" → change €500→€50 with reason → prints €50, posts €50 once; allocations sum to €50.
2. Same but print first → no "Edit Payment", lock chip shown; API returns 409.
3. Edit cash→cheque (number+date) → doc_type/cheque fields correct on print.
4. After route submit → no Edit Payment anywhere.
5. Void/reissue still works for printed receipts (regression).
6. Request cancellation → amber chip + office count badge; after office void+reissue → driver card shows "New receipt ready — Print"; printing completes the loop.
7. Printed card shows `R‹number› · SYNCED`; unprinted eligible stop shows "Edit Payment".
8. Reprint button visibly active; server error messages pass through to the alert.
9. Delivered stop never printed → "NOT PRINTED" chip + "Print Receipt"; after first print → `R‹number› · SYNCED` and label "Reprint".
10. Route submit with unprinted receipts → blocking confirm listing the stops; confirmed appear on exception report.
11. Reconciliation shows voided receipt as struck-through row with reason and copies expected/recovered; totals unchanged; outstanding copies flagged.
12. Void €96.16 (with PS365 reversal ref) → Reissue → enter €92 → new receipt €92, prints "Replaces R‹old›", posts €92 once.
13. Lookup "R1000001", "1000001", plain id all resolve to the same receipt.
14. PS365 unreachable → all four print paths refuse an official receipt and do NOT lock it; online/PDC still print.
