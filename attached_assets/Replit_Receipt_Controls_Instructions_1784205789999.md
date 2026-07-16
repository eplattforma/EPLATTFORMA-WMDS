# Replit Instruction — Driver Receipt Controls (Issuing, Cancelling, Validation)

**Goal:** Close three gaps in the driver receipt flow — (1) no amount-vs-owed validation gate, (2) a printed receipt can still be silently edited, (3) cancel/void is local-only and drifts from PS365 — while keeping the driver UI fast (happy path = same number of taps as today).

**Guiding UI rule:** *Friction only on the exception.* A correct, exact payment must stay one confirm tap. Warnings, reasons, and locks appear **only** when something is off. Never add a field the driver has to touch when everything is normal.

---

## Files in scope (existing code — do not rebuild, extend)

| Concern | File / symbol |
|---|---|
| Driver payment wizard UI | `templates/driver/deliver_wizard.html` (`pwShowConfirm`, `submitPaymentWizard`, `getCollectDue`, `pwUpdateDisplay`, `pwValidateStep2`) |
| Payment API | `routes_payments.py` → `create_payment` (`/api/route-stops/<id>/payment`) |
| PS365 commit | `services/payments.py` → `commit_to_ps365`, `upsert_active_payment` |
| Receipt → PS365 | `routes_receipts.py` → `create_receipt_core`, `next_reference_number` |
| Admin void / reissue | `routes_reconciliation.py` → `api_void_receipt`, `api_reissue_receipt` (both `@admin_or_warehouse_required`) |
| Receipt record | `models.py` → `CODReceipt` (has `print_count`, `first_printed_at`, `last_printed_at`, `variance`, `note`, `status`, `voided_at/by`, `void_reason`, `replaced_by_cod_receipt_id`, `ps365_reference_number`) |
| Printed customer receipt | `templates/driver/receipt_80mm.html`, `receipt_58mm.html` (prints `receipt.id` as the customer-facing number) |

The customer-facing number is `receipt.id`, which exists at DRAFT creation — **no change to numbering is required.** PS365 stores our number; it never mints one.

---

## Change 1 — Amount-owed validation gate

**What's there today:** `getCollectDue()` = invoice totals − approved returns/credits (driver cannot edit it). The confirm screen already shows a coloured variance line. **What's missing:** nothing stops the driver or captures *why* it differs.

**Behaviour to build (confirm step only — `pwShowConfirm`):**

1. Compute `variance = entered − owed`. Apply **only** to cash/cheque collected now. Skip for `online` and post-dated cheque (collected-now is €0 by design — do **not** flag these as short).
2. **Exact match** (`|variance| ≤ €0.01`): unchanged. Green tick, single **Confirm** button. One tap.
3. **Short** (`entered < owed`): show an amber panel and a **required reason chip row** — `Customer paying part`, `Dispute on item`, `No change`, `Other` (Other reveals a one-line note). Confirm button stays disabled until a reason is picked. Remaining balance stays due on the account (do not zero it).
4. **Over** (`entered > owed`): stronger red panel — headline *"Customer is paying €X MORE than the €Y owed — is this correct?"* Require the same reason chip + explicit confirm. Overpayment is almost always a typo; make the driver look twice here.
5. Persist `variance` (already exists) **and a new `variance_reason`** on `CODReceipt`.

**Show the breakdown at confirm** so the number is meaningful (and disputes die at the door):
```
Invoices        € 500.00
Credits/returns −€  50.00
─────────────────────────
Owed            € 450.00
Entered         € 450.00   ✓ matches
```

**Data model:** add `CODReceipt.variance_reason` (String, nullable). Migration only — no backfill.

---

## Change 2 — Freeze on print (the real point of no return)

Once a numbered slip is in a customer's hand, its number + amount are a claim against us. The lock is **first print**, not PS365 sync.

1. On the first print, `first_printed_at` is set (already happens ~`routes_driver.py:1362`). Treat **`first_printed_at IS NOT NULL` as LOCKED.**
2. When locked, the driver UI **replaces** "Change Payment" with a disabled state + a single action **"Request cancellation"** (see Change 3). No keypad, no edit, no silent reprint-at-different-amount.
3. Backend hard stop: `create_payment` and any receipt-mutating endpoint must reject changes to a receipt whose `first_printed_at IS NOT NULL` unless it is being voided by an admin. Return `409` with a clear message: *"This receipt was printed and given to the customer. It can only be cancelled by an admin."*
4. **Reprints of the same unchanged receipt are allowed** and just increment `print_count` / `last_printed_at`. Editing is what's blocked, not reprinting.

---

## Change 3 — Admin cancel / void hardening

Today `api_void_receipt` flips status locally and does **nothing** in PS365; `api_reissue_receipt` posts a *second* PS365 receipt → double count. Fix both.

**Void (admin only — keep `@admin_or_warehouse_required`):**

1. Require a **reason** (already required) **plus** a **customer-copy recovery confirmation**: the admin confirms the **customer copies** have been collected back. Each print event produces one customer copy, so expected copies to recover = `print_count` (see Change 6). If fewer are returned, the void does **not** complete — flag for investigation. (A returned-copy count field on the void payload; store it as `slips_recovered`.) The office copy stays the company's record and is reconciled separately.
2. If the receipt was already synced (`ps365_reference_number` set), the admin **cancels it manually in the PS365 back office** (PS365 has no API reversal — this is by design). The void **must** capture that manual action: who cancelled it in PS365, when, and the PS365 reference. Store on the receipt. Do not mark `VOIDED` as "clean" until this is recorded.
3. Add a nightly reconciliation check: any `VOIDED` receipt whose `ps365_reference_number` still appears in a PS365 receipt export → flag. This is the safety net for a forgotten manual delete.

**Reissue:**

1. New receipt gets a **new number** (never reuse the old).
2. It **prints "Replaces R‹old› (voided)"** on `receipt_80mm.html` / `receipt_58mm.html` so the paper trail self-links.
3. Block the reissue from posting to PS365 until the original's **manual PS365 cancellation** (step 2 above) is recorded — prevents the double count.

**Corrections to a receipt the customer already holds:** do **not** reduce-and-reissue. Issue a **linked credit** the customer also receives, so their file always nets out (€500 receipt + €450 credit = €50). Reduce-and-reissue is only valid *before* `first_printed_at` (misprint / wrong customer).

---

## Change 4 — Manual (emergency) receipt logging

The paper fallback (customer already gone / no signal / printer dead) is a fraud vector unless controlled.

1. Add a **manual receipt entry** at reconciliation: pre-numbered book number + amount + customer + linked digital receipt.
2. Every manual number must be logged and matched to a digital entry before the route reconciles.
3. Surface **per-driver manual-receipt usage** on the exception report — heavy use is a flag.

---

## Change 6 — Two-part receipt printing (customer copy + office copy)

Every issue prints **two slips in one action**:

1. **CUSTOMER COPY** — handed to the customer. This is the controlled document / claim against us.
2. **OFFICE COPY** — kept by the driver and handed in at reconciliation as the company's record.

**Implementation:**
- One print action emits both slips back to back (`receipt_80mm.html` / `receipt_58mm.html`), each with a clear header band: `CUSTOMER COPY` and `OFFICE COPY`. Same number (`receipt.id`), same amount — only the label differs.
- Keep `print_count` counting **print events** (each event = 1 customer copy + 1 office copy). Cancellation control is driven by customer copies = `print_count` (Change 3).
- A reprint re-emits both copies and bumps `print_count` — allowed only while the receipt is **not** locked-and-being-edited (reprint of the same unchanged receipt is fine; see Change 2).
- Thermal printers: emit as a single job with a cut between the two copies so the driver tears them apart cleanly.

---

## Change 5 — Receipt lookup + exception report

1. **Receipt lookup** (office): enter a receipt number → live status (`ISSUED` / `VOIDED` / `REISSUED`), amount, driver, reversal/replacement links. This resolves a customer dispute in seconds.
2. **Per-driver exception report** aggregating: voids (with reasons), variances (with reasons), overpayments, manual receipts, slip-count mismatches. This is the detective control behind every policy above.

---

## UI principles applied (efficient + effective)

- **Mobile-first, thumb-reachable.** All exception actions (reason chips, confirm, request-cancel) sit in the lower third, same zone as the existing keypad.
- **One happy-path tap preserved.** Exact payment → green tick → Confirm. No new taps for the 90% case.
- **Chips over dropdowns.** Reasons are big tap targets, not a select menu.
- **Colour = state, consistently.** Green = matches/synced, Amber = short/attention, Red = over/blocked. Reuse the classes already in `deliver_wizard.html`.
- **Locked ≠ hidden.** A printed receipt shows plainly as LOCKED with one clear next action ("Request cancellation"), never a dead greyed control with no explanation.
- **Every block states the reason and the way out** in one line — no dead ends.

---

## Data model summary (migrations)

- `CODReceipt.variance_reason` — String, nullable.
- `CODReceipt.slips_recovered` — Integer, nullable (set at void).
- `CODReceipt.ps365_reversed_by` / `ps365_reversed_at` / `ps365_reversal_ref` — for synced voids.
- Manual receipt log — reuse/extend the reconciliation model with `manual_receipt_no`, `manual_receipt_amount`, `linked_cod_receipt_id`.

---

## Acceptance tests

1. Exact payment → one Confirm tap, no reason prompt, receipt prints. *(happy path unchanged)*
2. Short payment with no reason selected → Confirm stays disabled; picking a reason enables it; `variance_reason` saved; balance remains due.
3. Over payment → red gate, reason required, saved.
4. Online / post-dated cheque → no variance flag.
5. Printed receipt → edit blocked in UI and via API (`409`); reprint of same receipt still works and bumps `print_count`.
6. One print action produces exactly two labelled slips (CUSTOMER COPY + OFFICE COPY), same number and amount.
7. Admin void with customer copies recovered `< print_count` → void does not complete.
8. Admin void of a synced receipt without the manual PS365 cancellation recorded → cannot be marked clean; reissue cannot post.
9. Reissued receipt → new number, prints "Replaces R‹old›".
10. Nightly check flags a VOIDED receipt still live in PS365.
11. Receipt lookup returns correct live status and links for issued, voided, and reissued cases.
