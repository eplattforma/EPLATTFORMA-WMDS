# Replit — fix the reset / edit status bug

Two admin actions leave an order in a status the picking dashboard can't show. Both bypass the canonical `update_order_status_batch_aware()` (in `batch_aware_order_status.py`), which already computes the correct status from item states. Fix = route both through it. Example stuck order in production: **IN10057277**.

## Root cause
1. **Reset progress** (`admin_reset_invoice_progress`, routes.py ~3277) sets `invoice.status = 'In Progress'` — a legacy value not in the 8-status set, so the dashboard (filters `not_started` / `picking` / `awaiting_packing`) can't see it.
2. **Edit items → mark all not picked** (`admin_update_invoice_items`, routes.py ~3226) only handles `not_started`, `picking`, `ready_for_dispatch`. "Ready to pack" is `awaiting_packing`, which no branch handles — so un-picking leaves it stuck at `awaiting_packing`.

## Fix 1 — `admin_reset_invoice_progress`
Remove the hardcoded status; clear completion timestamps; recompute canonically.
```python
    # Reset invoice progress
    invoice.current_item_index = 0
    invoice.picking_complete_time = None
    invoice.packing_complete_time = None
    # (delete the line:  invoice.status = 'In Progress')

    recalculate_invoice_totals(db.session, invoice_no)
    db.session.commit()

    from batch_aware_order_status import update_order_status_batch_aware
    update_order_status_batch_aware(invoice_no)   # -> not_started (all items reset)

    flash('Invoice progress has been reset. All items are now marked as Not Picked.', 'success')
    return redirect(url_for('admin_view_invoice', invoice_no=invoice_no))
```

## Fix 2 — `admin_update_invoice_items`
Replace the whole ad-hoc status block (from `all_picked = is_order_ready(...)` down through the `elif invoice.status == 'ready_for_dispatch'` chain) with a guarded call to the canonical function, so `awaiting_packing` and `awaiting_batch_items` are handled and completion timestamps are cleared when items are un-picked:
```python
    recalculate_invoice_totals(db.session, invoice_no)

    # Only recompute for non-terminal orders; never touch shipped/delivered.
    NON_TERMINAL = ('not_started', 'picking', 'awaiting_batch_items',
                    'awaiting_packing', 'ready_for_dispatch')
    if invoice.status in NON_TERMINAL:
        from services.order_readiness import is_order_ready
        if not any(it.is_picked for it in invoice.items):
            invoice.picking_complete_time = None
        if not is_order_ready(invoice_no):
            invoice.packing_complete_time = None
        db.session.commit()
        from batch_aware_order_status import update_order_status_batch_aware
        update_order_status_batch_aware(invoice_no)
    else:
        db.session.commit()

    flash('Invoice items updated successfully', 'success')
    return redirect(url_for('admin_view_invoice', invoice_no=invoice_no))
```

## Fix 3 — `admin_reset_item` (cleanup, same file ~3289)
It already sets canonical `not_started` / `picking`, but has dead legacy branches. Replace its manual status block (the `if invoice.status == 'In Progress'` and `if invoice.status == 'Completed': invoice.status = 'In Progress'` lines and the picked-count if/elif) with the same canonical call after the item reset:
```python
    recalculate_invoice_totals(db.session, invoice_no)
    if not is_order_ready(invoice_no):
        invoice.packing_complete_time = None
    db.session.commit()
    from batch_aware_order_status import update_order_status_batch_aware
    update_order_status_batch_aware(invoice_no)
```

## Fix the stuck order IN10057277 (after deploying)
Its items were reset to not-picked, so it should be `not_started`. Either click **Reset progress** on it (now works), or run once:
```sql
UPDATE invoices
SET status='not_started', picking_complete_time=NULL, packing_complete_time=NULL, status_updated_at=now()
WHERE invoice_no='IN10057277';
```
(It will then reappear on the picking dashboard.)

## Paste to the Replit Agent
> Two admin actions leave orders in a status the picking dashboard can't show, because they bypass `update_order_status_batch_aware()`. Fix both by routing through it.
> 1. In `admin_reset_invoice_progress`: delete `invoice.status = 'In Progress'`; set `current_item_index=0`, clear `picking_complete_time` and `packing_complete_time`, commit, then call `update_order_status_batch_aware(invoice_no)`.
> 2. In `admin_update_invoice_items`: replace the ad-hoc status if/elif block with — only when `invoice.status` is in (not_started, picking, awaiting_batch_items, awaiting_packing, ready_for_dispatch): clear `picking_complete_time` if no items picked, clear `packing_complete_time` if `not is_order_ready(invoice_no)`, commit, then call `update_order_status_batch_aware(invoice_no)`; otherwise just commit (never change shipped/delivered).
> 3. In `admin_reset_item`: remove the legacy `'In Progress'`/`'Completed'` branches and the manual picked-count status block; after the item reset, call `update_order_status_batch_aware(invoice_no)`.
> Then correct the stuck order: set IN10057277 to `not_started` with `picking_complete_time`/`packing_complete_time` cleared (or click Reset on it once deployed).

## Follow-up (optional tech debt)
Legacy `'In Progress'` / `'Completed'` strings also appear at routes.py ~496, ~628, ~3482. They don't match the canonical set and can cause similar silent mismatches — worth migrating to the 8-status constants in a later cleanup.
