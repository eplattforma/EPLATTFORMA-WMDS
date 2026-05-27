# Receipt Duplicate Reference Fix

## What happened

The driver sent a cash payment. PS365 received it and created receipt R1001199 — but the network dropped before the confirmation came back. The system never saw a success response, so it marked the payment FAILED and kept the sequence counter stuck at the same number. Every retry generates R1001199 again, and PS365 correctly rejects it: "already exists".

Two fixes below:
1. **Immediate** — unblock the stuck driver right now (30 seconds, no code change)
2. **Permanent** — code guard so this never blocks a driver again

---

## FIX 1 — Immediate: unblock the driver (run now)

Run this SQL in the Replit database console (Shell tab → `psql $DATABASE_URL`):

```sql
UPDATE receipt_sequence SET last_number = 1001199 WHERE id = 1;
```

This advances the counter so the next generation produces **R1001200**, which PS365 has never seen.

**Then:** have the driver tap **Retry Receipt Sync**. It will generate R1001200, PS365 will accept it, and the stop will close normally.

**Verify it worked:**
```sql
SELECT last_number FROM receipt_sequence WHERE id = 1;
-- Should return 1001199
```

---

## FIX 2 — Permanent: auto-recover in `routes_receipts.py`

**Root cause:** `next_reference_number()` increments the counter inside the same database transaction as the PS365 call. If PS365 succeeds but the response is lost (timeout, network drop), the whole transaction rolls back — including the counter increment. The next retry gets the same number, which PS365 already has.

**Fix:** when PS365 returns "already exists" for a reference number, that IS success — the receipt is in PS365. Instead of raising an error, log a warning and continue as if it worked.

### In `routes_receipts.py` — find:

```python
            # CRITICAL: If no valid transaction number, fail completely
            if not ok or not response_id:
                error_msg = api_response.get("response_msg", "Unknown error from Powersoft365")
                raise Exception(f"Powersoft365 receipt creation failed: {error_msg}")
```

### Replace with:

```python
            # CRITICAL: If no valid transaction number, fail completely
            if not ok or not response_id:
                error_msg = api_response.get("response_msg", "Unknown error from Powersoft365")

                # Special case: PS365 says this reference number already exists.
                # This means the receipt WAS created on a previous attempt but the
                # network dropped before we received the confirmation. The receipt
                # is in PS365 — treat this as success so the driver can proceed.
                if ("already exists" in error_msg.lower()
                        and "reference_number" in error_msg.lower()):
                    logger.warning(
                        "[Receipts] PS365 reports %s already exists — "
                        "treating as SUCCESS (response was lost on prior attempt). "
                        "Error was: %s", reference_number, error_msg
                    )
                    ok = True
                    # response_id unknown — use our reference number as the identifier
                    if not response_id:
                        response_id = reference_number
                else:
                    raise Exception(f"Powersoft365 receipt creation failed: {error_msg}")
```

---

## FIX 3 — Permanent: same guard in `services/payments.py`

The driver's **Retry Receipt Sync** button goes through `commit_to_ps365()` in `services/payments.py`. Apply the same pattern there.

Find the section in `commit_to_ps365()` that checks the PS365 response and raises an error on failure. It will look something like:

```python
if response_code != "1" or not response_id:
    error_msg = api_response.get("response_msg") or "Unknown PS365 error"
    pe.ps_status = 'FAILED'
    pe.ps_error = error_msg
    ...
    raise Exception(...)   # or return without raising
```

Add the same "already exists" check **before** setting `ps_status = 'FAILED'`:

```python
if response_code != "1" or not response_id:
    error_msg = api_response.get("response_msg") or "Unknown PS365 error"

    # If PS365 says this reference already exists, the payment went through
    # on a prior attempt and we just lost the confirmation. Mark as SUCCESS.
    if ("already exists" in error_msg.lower()
            and "reference_number" in error_msg.lower()):
        logger.warning(
            "[Payments] PS365 reports reference already exists for PaymentEntry %s — "
            "treating as SUCCESS. Error: %s", pe.id, error_msg
        )
        pe.ps_status = 'SUCCESS'
        pe.ps_error = None
        pe.attempt_count = (pe.attempt_count or 0) + 1
        pe.last_attempt_at = datetime.utcnow()
        # ps_reference already set earlier in the function — if not, set it now
        if not pe.ps_reference:
            pe.ps_reference = reference_number  # use whatever ref was generated
        return pe

    # Genuine failure
    pe.ps_status = 'FAILED'
    pe.ps_error = error_msg
    ...
```

> Since `services/payments.py` is not in the downloaded zip, Replit should find the equivalent error-handling block by searching for `"FAILED"` and `ps_error` in that file and applying the same logic.

---

## Why this works

| Scenario | Before fix | After fix |
|----------|-----------|-----------|
| PS365 times out, receipt NOT created | FAILED → retry → new ref number → works | Same — unaffected |
| PS365 succeeds, network drops before response | FAILED → retry → same ref → blocked forever | FAILED → retry → "already exists" detected → marked SUCCESS → driver proceeds |

The fix only triggers when PS365 explicitly says the reference number already exists — not on any other error. All genuine failures still go through the normal error path.
