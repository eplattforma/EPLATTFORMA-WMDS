# Replit — verify or implement (consolidated)

Hand this to the Replit Agent. For each item: confirm it's already implemented; if not, implement it as described. Nothing here should remove existing working behaviour (printing, order status, delivery screens).

## Paste to the Replit Agent
> Go through the checklist below. For each point, tell me whether it's already implemented; where it isn't, implement it exactly as described. Don't break existing printing, order-status, or delivery functionality.

---

## A. Label PDF orientation  (`services/label_pdf.py`)
- **Verify:** page size is `pagesize=(105*mm, 70*mm)` (landscape) with **no** `c.rotate()` / `c.translate()` anywhere.
- Correct as-is per our test (STOP 101 label). Leave it.

## B. Label printing UX
- **Verify:** the box-label / tag button appears **only on the Picking Dashboard**, and has been removed from picker-facing screens (ready-for-packing / picker order views).
- **Verify:** tapping it disables the button, shows "Sending…" → "Sent ✓", and ignores repeat taps while a request is in flight.
- **Verify server dedupe:** the box-label enqueue endpoint skips inserting if a `doc_type='label'` job for that invoice is already `queued` or `sending`:
```python
exists = db.session.execute(text(
  "SELECT 1 FROM print_jobs WHERE invoice_no=:n AND doc_type='label' AND status IN ('queued','sending') LIMIT 1"),
  {'n': invoice_no}).first()
if not exists:
    db.session.execute(text("INSERT INTO print_jobs (invoice_no, doc_type, status) VALUES (:n,'label','queued')"),
                       {'n': invoice_no}); db.session.commit()
return jsonify({'ok': True})
```

## C. Label + slip content: stop grouping & cooler
- **Verify:** label and slip show **"Order X of Y"** for the stop, computed from invoices sharing `stop_id`.
- **Verify:** a bold **COOLER BOX** marker shows when the order has any rows in `cooler_box_items` (by `invoice_no`).
```sql
SELECT (SELECT count(*) FROM invoices x WHERE x.stop_id=i.stop_id) AS stop_total,
       (SELECT count(*) FROM invoices x WHERE x.stop_id=i.stop_id AND x.invoice_no<=i.invoice_no) AS stop_index,
       EXISTS(SELECT 1 FROM cooler_box_items c WHERE c.invoice_no=i.invoice_no) AS has_cooler
FROM invoices i WHERE i.invoice_no=:inv;
```

## D. Pack screen (ready_for_packing)
- **Verify readable name:** customer name uses dark text on the green header (not theme tokens that go white in dark mode) — e.g. `#14311c` name, `#2f6b2f` status, `#3d5c45` meta.
- **Verify buttons:** "Print delivery slip" is a blue outlined button; "Mark as packed" is the green solid primary.
- **Verify print feedback:** a status banner shows "Sent — printing…", and the button polls job status to show "✓ Printed on office Konica" (or "Sent to printer" if unconfirmed in ~15s). Needs: enqueue returns `job_id`, and `GET /print/job/<id>/status` returns the job status.
- **Verify already-printed guard:** if the slip was already printed for this invoice, tapping Print pops a confirm "This delivery slip has already been printed. Do you want to print it again?" (pass an `already_printed` flag; set it after a successful print).
- **Verify secondary links:** "Back to picking" and "View slip on screen" are full-width bordered buttons ≥44px tall, side by side.

## E. Reset / edit status bug (routes.py)
- **Verify `admin_reset_invoice_progress`:** it does NOT set `invoice.status = 'In Progress'`. Instead it resets items, sets `current_item_index=0`, clears `picking_complete_time`/`packing_complete_time`, commits, then calls `update_order_status_batch_aware(invoice_no)`.
- **Verify `admin_update_invoice_items`:** after updating items it recomputes via `update_order_status_batch_aware` (not an ad-hoc if/elif that ignores `awaiting_packing`), and never changes shipped/delivered orders.
- **Verify `admin_reset_item`:** no legacy `'In Progress'`/`'Completed'` branches; it calls `update_order_status_batch_aware` after the reset.
- **Fix data:** ensure IN10057277 is a valid status (should be `not_started` now). If still `In Progress`, run:
```sql
UPDATE invoices SET status='not_started', picking_complete_time=NULL, packing_complete_time=NULL, status_updated_at=now() WHERE invoice_no='IN10057277';
```

## F. Print bridge (should already exist — verify)
- **Verify** `print_jobs` table has a `doc_type` column ('slip'|'label').
- **Verify** endpoints exist and require the `X-Print-Agent-Token` header: `GET /print/agent/poll`, `POST /print/agent/ack`, `POST /print/delivery-slip/<inv>`, `POST /print/box-label/<inv>`, and `GET /print/job/<id>/status`.
- **Verify** poll builds the slip PDF for `doc_type='slip'` and the label PDF for `doc_type='label'`, and returns `doc_type` + `pdf_base64`.

---

## Not Replit — office PC / print agent (for reference, don't ask the Agent to do these)
- `print_agent.ps1`: label branch uses `-print-settings "noscale"`; Konica/slip branch uses `-print-settings "duplexlong"` for double-sided.
- Deli driver: paper 105×70 mm, Portrait, no 180, 100% scale.
- Konica driver: default 2-sided (long edge) if you prefer duplex there instead of the agent flag.
