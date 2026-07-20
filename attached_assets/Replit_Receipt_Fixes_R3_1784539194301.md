# Replit Instruction — Receipt Controls Round 3 (small fixes)

Round 2 verified: deferred PS365 commit at print, void unlocks the PaymentEntry, PENDING_RETRY change guard, duplicate-stop guard allows reissue after void, cancellation requests logged and surfaced, migrations updated. Two remaining items.

## FIX 1 (P2) — Receipt lookup cannot find R-numbers

`api_receipt_lookup` (`routes_reconciliation.py`) strips the leading "R" (`q.lstrip('Rr#')`) and then searches `ps365_reference_number == q`. But references are **stored with the R prefix** (`R1000001`, from `next_reference_number`). So typing "R1000001" finds nothing: the stripped "1000001" matches neither a `CODReceipt.id` nor the stored `R1000001`.

**Fix:** try the raw input and the stripped form against `ps365_reference_number`, and the stripped digits against `CODReceipt.id`:

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

**Test:** lookup "R1000001", "1000001", and a plain `receipt.id` all resolve to the same receipt.

## FIX 2 (P3) — Print paths behave differently when PS365 is down

The main mobile path (`print_receipt_png_by_id`) correctly refuses to print an official receipt without a PS365 reference (503). But the HTML print views (`print_receipt`, `print_receipt_80mm`, `print_stop_receipt`) call the sync **non-blocking** and will render a printable receipt even when the sync failed — so the same receipt is blocked on one path and printable on another, and an HTML-printed copy would show `receipt.id` while a later reprint shows the R-number.

**Fix:** apply the same gate in the HTML print views for `doc_type == 'official'` and non-VOIDED receipts: if after `sync_receipt_ps365_at_print` there is still no `ps365_reference_number`, render an error page ("Receipt not registered in Powersoft yet — retry or use the manual book") instead of the receipt. Preview mode (`is_preview`) stays unaffected.

**Test:** with PS365 unreachable, all four print paths refuse to produce an official receipt; online/PDC docs still print (they don't post).
