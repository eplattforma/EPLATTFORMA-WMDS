# Replit Instruction — DIAGNOSE: printed receipts are not reaching PS365

**Do not change any code yet.** This is a diagnostic task. Report findings first.

**Symptom:** driver receipts show "Locked (printed)" in the app, but no receipt appears in PS365. Reprint via `/driver/receipts/<id>/print.png` fails with "Could not load receipt" (503).

**Known context (already confirmed by code review — do not re-investigate):**
- `print_receipt` (58mm) and `print_receipt_80mm` in `routes_driver.py` lock the receipt even when `sync_receipt_ps365_at_print` fails, because that function swallows all exceptions. The fix for this is separate (R3). 
- The question THIS task answers: **why is the PS365 sync itself failing?**

## Step 1 — Find the failing receipts

Run against the database:

```sql
SELECT id, route_stop_id, received_amount, payment_method, status,
       print_count, first_printed_at, ps365_reference_number, ps365_synced_at
FROM cod_receipts
WHERE first_printed_at IS NOT NULL
  AND ps365_reference_number IS NULL
ORDER BY first_printed_at DESC
LIMIT 20;
```

Report the rows. These are the printed-but-never-posted receipts.

## Step 2 — Get the stored sync error

```sql
SELECT pe.id, pe.route_stop_id, pe.method, pe.amount, pe.ps_status,
       pe.attempt_count, pe.ps_error, pe.last_attempt_at
FROM payment_entries pe
WHERE pe.route_stop_id IN (SELECT route_stop_id FROM cod_receipts
                           WHERE first_printed_at IS NOT NULL
                             AND ps365_reference_number IS NULL)
  AND pe.is_active = TRUE;
```

Report `ps_status`, `attempt_count`, and the full `ps_error` text for each. Also search the application logs for `PS365 sync at print failed` and `PS365 CLIENT` and report the last 20 matching lines.

## Step 3 — Check PS365 configuration in this environment

Without printing secret values, report for each: SET or NOT SET, and the length.

- `PS365_TOKEN`
- `PS365_BASE_URL`
- `POWERSOFT_TOKEN`
- `POWERSOFT_BASE`

Note: `routes_receipts.py` uses `POWERSOFT_TOKEN` / `POWERSOFT_BASE` while `ps365_client.py` uses `PS365_TOKEN` / `PS365_BASE_URL`. Report which ones the receipt path (`create_receipt_core`) actually reads and whether those specific ones are set in this environment.

## Step 4 — Test the PS365 connection directly

Write a throwaway script (do not commit it) that calls the `customer_receipt` endpoint the same way `create_receipt_core` does, using: the same base URL + token the app reads, a known-good `customer_code_365` from `ps_customers`, amount `0.01`, a test reference like `RTEST0001`, and the same `payment_type_code_365` logic (default `DRVR1`). Report:

- HTTP status and full response body (redact the token)
- If it fails: is it auth (bad/missing token), unknown customer code, unknown payment type code, or network?

If it succeeds, immediately cancel/delete that €0.01 receipt in the PS365 back office and say so in the report.

## Step 5 — Check the failing receipts' inputs

For the receipts from Step 1, verify the data that `create_receipt_core` would send:

- Does the stop's `customer_code` exist in `ps_customers` with `customer_code_365` set and `active = TRUE`?
- Does the driver user have `payment_type_code_365` set, and does that code exist in PS365's payment types?
- Any of `received_amount <= 0`?

Report a table: receipt id → customer code found? → payment type used → amount.

## Deliverable

A short report answering ONE question: **what exactly is making the PS365 post fail in this environment** — missing/wrong token, wrong env var name, unknown customer, unknown payment type, network, or something else. Include the evidence (error text, HTTP responses). Propose the fix but DO NOT implement it until confirmed.
