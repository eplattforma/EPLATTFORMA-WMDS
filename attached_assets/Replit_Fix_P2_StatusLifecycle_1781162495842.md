# WMDS Fix — Priority 2: Status Lifecycle Deadlocks

Four related bugs that leave invoices stuck at `awaiting_batch_items`, wrongly demote `ready_for_dispatch` invoices, or leave routes looking ready when they are not. All code blocks below are exact copies of the current source — use them as find/replace targets.

Files touched:

1. `routes_routes.py` — Bug 1
2. `batch_aware_order_status.py` — Bugs 2a and 4
3. `services/batch_picking.py` — Bug 2b
4. `blueprints/cooler_picking.py` — Bug 3

---

## Bug 1 — No invoice status recompute after unassign from route

**File:** `routes_routes.py`, function `unassign_from_route()` (route `/unassign-from-route`), approx. lines 1120–1188.

When invoices are unassigned from a route, the handler deletes `RouteStopInvoice` rows, clears `route_id`/`stop_id`, and releases cooler locks via `release_cooler_locks_for_invoice()` — but it never recomputes the invoice status. Invoices stay parked at `awaiting_batch_items` or `ready_for_dispatch` even though their cooler queue rows and locks were just reset. Fix: after the commit, run `update_order_status_batch_aware()` for every unassigned invoice.

### Current code (end of `unassign_from_route`, find this exactly)

```python
    db.session.commit()
    
    # Clean up any empty stops after unassigning invoices
    empty_stops = db.session.execute(
        db.select(RouteStop.route_stop_id).outerjoin(
            RouteStopInvoice
        ).group_by(RouteStop.route_stop_id).having(
            db.func.count(RouteStopInvoice.invoice_no) == 0
        )
    ).scalars().all()
    
    for stop_id in empty_stops:
        from services import delete_stop
        delete_stop(stop_id)
    
    return jsonify({
        "ok": True,
        "total_invoices": len(invoices),
        "message": f"{len(invoices)} invoice(s) removed from route"
    }), 200
```

### Replacement

```python
    db.session.commit()

    # Recompute invoice statuses now that route assignment and cooler
    # locks/queue rows have changed. Without this, invoices stay at
    # awaiting_batch_items / ready_for_dispatch with stale status.
    from batch_aware_order_status import update_order_status_batch_aware
    for invoice in invoices:
        try:
            update_order_status_batch_aware(invoice.invoice_no)
        except Exception as _sre:
            current_app.logger.warning(
                "unassign_from_route: status recompute failed for %s: %s",
                invoice.invoice_no, _sre,
            )

    # Clean up any empty stops after unassigning invoices
    empty_stops = db.session.execute(
        db.select(RouteStop.route_stop_id).outerjoin(
            RouteStopInvoice
        ).group_by(RouteStop.route_stop_id).having(
            db.func.count(RouteStopInvoice.invoice_no) == 0
        )
    ).scalars().all()
    
    for stop_id in empty_stops:
        from services import delete_stop
        delete_stop(stop_id)
    
    return jsonify({
        "ok": True,
        "total_invoices": len(invoices),
        "message": f"{len(invoices)} invoice(s) removed from route"
    }), 200
```

Note: the import is placed inside the function (matching the existing local-import style of this file, e.g. `from app import db` at the top of the same function). `update_order_status_batch_aware` commits internally, so calling it after the first `db.session.commit()` is safe. If `current_app` is not already imported at the top of `routes_routes.py` (check the existing `from flask import ...` line), add `current_app` to that import.

### Testing checklist

- Assign an invoice with cooler items to a route, pick + close its cooler flow so it reaches `ready_for_dispatch`, then POST `/unassign-from-route` with that invoice — verify `SELECT status FROM invoices WHERE invoice_no = '<INV>'` is no longer `ready_for_dispatch` (it should recompute to `awaiting_batch_items`, `picking`, `awaiting_packing`, or `not_started` per its item state).
- Unassign an invoice stuck at `awaiting_batch_items` whose cooler locks get released by `release_cooler_locks_for_invoice` — verify status recomputes instead of staying `awaiting_batch_items`.
- Unassign multiple invoices in one call — all of them get recomputed (check log lines `📦 Order <inv> status: ... → ...`).
- Verify the endpoint still returns `{"ok": true}` and empty stops are still deleted.

---

## Bug 2 — Cancelled-batch locks permanently deadlock invoices at `awaiting_batch_items`

Two coordinated fixes. (a) `update_order_status_batch_aware` counts any lock whose batch is not `Completed` as blocking — so a lock pointing at a `Cancelled` or `Archived` batch blocks forever. (b) `cancel_batch` only releases `locked_by_batch_id` for `pending` queue rows; rows in `exception` or `skipped` status keep their lock permanently. Combined effect: skip an item in a batch, cancel the batch, and the invoice is deadlocked at `awaiting_batch_items` with no UI path out.

### Fix 2a

**File:** `batch_aware_order_status.py`, inside `update_order_status_batch_aware()`, approx. lines 54–67.

#### Current code (find this exactly)

```python
    # Now iterate items with cached batch statuses
    for item in all_items:
        if item.is_picked and item.pick_status in TERMINAL_PICK_STATUSES:
            picked_items += 1
        else:
            unpicked_items += 1
            # Check if item is locked by an ACTIVE batch
            if item.locked_by_batch_id is not None:
                batch_status = batch_status_map.get(item.locked_by_batch_id)
                if batch_status and batch_status != 'Completed':
                    batch_locked_items += 1
                elif batch_status == 'Completed':
                    # Unlock item from completed batch
                    item.locked_by_batch_id = None
```

#### Replacement

```python
    # Batches in these statuses no longer hold their locks. Cancelled and
    # Archived batches must NOT count as blocking, otherwise stale locks
    # (e.g. exception/skipped rows from a later-cancelled batch) park the
    # invoice at awaiting_batch_items forever.
    RELEASED_BATCH_STATUSES = ('Completed', 'Cancelled', 'Archived')

    # Now iterate items with cached batch statuses
    for item in all_items:
        if item.is_picked and item.pick_status in TERMINAL_PICK_STATUSES:
            picked_items += 1
        else:
            unpicked_items += 1
            # Check if item is locked by an ACTIVE batch
            if item.locked_by_batch_id is not None:
                batch_status = batch_status_map.get(item.locked_by_batch_id)
                if batch_status and batch_status not in RELEASED_BATCH_STATUSES:
                    batch_locked_items += 1
                elif batch_status in RELEASED_BATCH_STATUSES:
                    # Unlock item from a completed/cancelled/archived batch
                    item.locked_by_batch_id = None
```

(Behaviour when `batch_status` is `None` — batch row deleted — is unchanged: not counted as blocking, lock left as-is.)

### Fix 2b

**File:** `services/batch_picking.py`, inside `cancel_batch()`, approx. lines 518–544 (the DB-backed branch).

#### Current code (find this exactly)

```python
        if db_backed:
            # DB-backed batches: cancel ONLY pending rows. picked/skipped/
            # exception rows must remain untouched for audit. Lock release
            # is keyed to the actually-cancelled (invoice_no, item_code)
            # tuples so queue state and lock state stay in sync.
            pending = db.session.execute(
                text("SELECT invoice_no, item_code FROM batch_pick_queue "
                     "WHERE batch_session_id = :sid AND status = 'pending'"),
                {"sid": batch_id},
            ).fetchall()
            db.session.execute(
                text("UPDATE batch_pick_queue "
                     "SET status = 'cancelled', cancelled_at = :now, updated_at = :now "
                     "WHERE batch_session_id = :sid AND status = 'pending'"),
                {"sid": batch_id, "now": _now},
            )
            released = 0
            for row in pending:
                released += db.session.query(InvoiceItem).filter(
                    InvoiceItem.invoice_no == row.invoice_no,
                    InvoiceItem.item_code == row.item_code,
                    InvoiceItem.locked_by_batch_id == batch_id,
                    InvoiceItem.is_picked.is_(False),
                ).update(
                    {InvoiceItem.locked_by_batch_id: None},
                    synchronize_session=False,
                )
```

#### Replacement

```python
        if db_backed:
            # DB-backed batches: cancel ONLY pending rows. picked/skipped/
            # exception rows must remain untouched for audit. Lock release
            # is keyed to the actually-cancelled (invoice_no, item_code)
            # tuples so queue state and lock state stay in sync.
            pending = db.session.execute(
                text("SELECT invoice_no, item_code FROM batch_pick_queue "
                     "WHERE batch_session_id = :sid AND status = 'pending'"),
                {"sid": batch_id},
            ).fetchall()
            db.session.execute(
                text("UPDATE batch_pick_queue "
                     "SET status = 'cancelled', cancelled_at = :now, updated_at = :now "
                     "WHERE batch_session_id = :sid AND status = 'pending'"),
                {"sid": batch_id, "now": _now},
            )
            released = 0
            for row in pending:
                released += db.session.query(InvoiceItem).filter(
                    InvoiceItem.invoice_no == row.invoice_no,
                    InvoiceItem.item_code == row.item_code,
                    InvoiceItem.locked_by_batch_id == batch_id,
                    InvoiceItem.is_picked.is_(False),
                ).update(
                    {InvoiceItem.locked_by_batch_id: None},
                    synchronize_session=False,
                )
            # Also release locks held by exception/skipped queue rows of
            # this batch. Their queue rows stay untouched for audit, but
            # the InvoiceItem lock must not survive cancellation or the
            # invoice deadlocks at awaiting_batch_items forever.
            stuck = db.session.execute(
                text("SELECT invoice_no, item_code FROM batch_pick_queue "
                     "WHERE batch_session_id = :sid "
                     "  AND status IN ('exception', 'skipped')"),
                {"sid": batch_id},
            ).fetchall()
            for row in stuck:
                released += db.session.query(InvoiceItem).filter(
                    InvoiceItem.invoice_no == row.invoice_no,
                    InvoiceItem.item_code == row.item_code,
                    InvoiceItem.locked_by_batch_id == batch_id,
                ).update(
                    {InvoiceItem.locked_by_batch_id: None},
                    synchronize_session=False,
                )
```

Note: the `stuck` release deliberately omits the `is_picked.is_(False)` filter — an exception row may have `is_picked = True` with `pick_status = 'exception'`, and the stale lock should be cleared in that case too. `cancel_batch` already runs `update_order_status_batch_aware` for all batch invoices afterwards (the `affected_invoices` loop further down the function), so once the locks are gone the statuses self-correct.

### Testing checklist

- Create a batch with a 2-item invoice, skip one item (queue row → `skipped`), then cancel the batch. Verify `SELECT locked_by_batch_id FROM invoice_items WHERE invoice_no = '<INV>'` returns all NULLs and `SELECT status FROM invoices WHERE invoice_no = '<INV>'` is NOT `awaiting_batch_items`.
- Repeat with an `exception` queue row — same expectations; the queue row itself must still read `exception` (audit preserved).
- Pre-existing deadlocked invoice (lock points at an already-`Cancelled` batch): run `update_order_status_batch_aware('<INV>')` — fix 2a must clear the lock and move the invoice out of `awaiting_batch_items`.
- Cancel a batch with only pending rows — behaviour unchanged: rows flip to `cancelled`, locks released, `released` count in the activity log is correct.
- Verify a lock held by an `In Progress` batch still counts as blocking (invoice stays `awaiting_batch_items`).

---

## Bug 3 — `box_reopen` reopens a box but does not reverse invoice/session/readiness statuses

**File:** `blueprints/cooler_picking.py`, function `box_reopen()` (route `/box/<int:box_id>/reopen`), approx. lines 2340–2373.

`box_close` promotes invoices to `ready_for_dispatch`, marks the route's cooler session `Completed` when packing is done, and recalculates route warehouse readiness. `box_reopen` reverses none of that — it only flips `cooler_boxes.status` back to `open`. After a reopen, invoices stay `ready_for_dispatch`, the cooler session stays `Completed`, and the route still looks ready for dispatch. Fix: revert the cooler session to `In Progress`, demote affected invoices that are no longer actually ready, and recalculate warehouse readiness.

### Current code (the full function — find this exactly)

```python
@cooler_bp.route("/box/<int:box_id>/reopen", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_reopen(box_id):
    """Re-open a previously closed cooler box.

    Only ``closed`` boxes can be re-opened (cancelled boxes are terminal —
    their items have already been reverted to pending). The stop-range
    stamps are intentionally left in place for audit; ``box_close`` will
    overwrite them on the next close.
    """
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "closed":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; only closed "
                     f"boxes can be re-opened."
        }), 400
    db.session.execute(
        text("UPDATE cooler_boxes SET status = 'open' WHERE id = :bid"),
        {"bid": box_id},
    )
    _audit(
        "cooler.box_reopened",
        f"Cooler box #{box_id} re-opened by {_username()}",
    )
    db.session.commit()
    flash(f"Box #{box_id} re-opened.", "success")
    return redirect(url_for("cooler.route_picking",
                            route_id=box["route_id"],
                            delivery_date=box["delivery_date"]))
```

### Replacement

```python
@cooler_bp.route("/box/<int:box_id>/reopen", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_reopen(box_id):
    """Re-open a previously closed cooler box.

    Only ``closed`` boxes can be re-opened (cancelled boxes are terminal —
    their items have already been reverted to pending). The stop-range
    stamps are intentionally left in place for audit; ``box_close`` will
    overwrite them on the next close.

    Reopening reverses the status side-effects of ``box_close``:
    the route's cooler session goes back to 'In Progress' if it was
    auto-completed, invoices that were promoted to ready_for_dispatch
    are demoted while their box is open again, and the route warehouse
    readiness is recalculated.
    """
    box = _fetch_box(box_id)
    if box is None:
        abort(404)
    if box["status"] != "closed":
        return jsonify({
            "error": f"Box #{box_id} is {box['status']}; only closed "
                     f"boxes can be re-opened."
        }), 400
    now = get_utc_now()
    db.session.execute(
        text("UPDATE cooler_boxes SET status = 'open' WHERE id = :bid"),
        {"bid": box_id},
    )

    # (a) Reverse the auto-completion done by box_close: the route's
    # cooler session is no longer complete while a box is open.
    db.session.execute(
        text(
            "UPDATE batch_picking_sessions "
            "SET status = 'In Progress', last_activity_at = :now "
            "WHERE session_type = 'cooler_route' "
            "  AND route_id = :rid "
            "  AND status = 'Completed'"
        ),
        {"rid": box["route_id"], "now": now},
    )

    # (b) Demote invoices that were promoted to ready_for_dispatch when
    # this box closed. is_order_ready() consults cooler box statuses, so
    # with the box open again it returns False for affected invoices.
    demoted = []
    try:
        from services.order_readiness import is_order_ready
        from models import Invoice
        inv_rows = db.session.execute(
            text(
                "SELECT DISTINCT invoice_no FROM cooler_box_items "
                "WHERE cooler_box_id = :bid"
            ),
            {"bid": box_id},
        ).fetchall()
        for (inv_no,) in inv_rows:
            inv = Invoice.query.filter_by(invoice_no=inv_no).first()
            if inv is None:
                continue
            if inv.status == 'ready_for_dispatch' and not is_order_ready(inv_no):
                inv.status = 'awaiting_batch_items'
                demoted.append(inv_no)
                _audit(
                    "cooler.order_demoted_on_reopen",
                    f"Invoice {inv_no} demoted "
                    f"ready_for_dispatch -> awaiting_batch_items "
                    f"after cooler box #{box_id} reopened",
                    invoice_no=inv_no,
                )
    except Exception as exc:  # never block box reopen on demotion failure
        current_app.logger.warning(
            "cooler.box_reopen: demotion check failed for box %s: %s",
            box_id, exc,
        )

    _audit(
        "cooler.box_reopened",
        f"Cooler box #{box_id} re-opened by {_username()}",
    )
    db.session.commit()

    # (c) Recalculate warehouse readiness — the route is no longer ready
    # while this box is open.
    try:
        from services.route_warehouse_readiness import recalculate_route_warehouse_status
        recalculate_route_warehouse_status(box["route_id"])
    except Exception as _wre:
        current_app.logger.warning(
            "warehouse readiness recalc failed after box_reopen %s: %s", box_id, _wre
        )

    flash(f"Box #{box_id} re-opened.", "success")
    return redirect(url_for("cooler.route_picking",
                            route_id=box["route_id"],
                            delivery_date=box["delivery_date"]))
```

Notes for Replit:

- `get_utc_now`, `current_app`, `text`, `db`, `_audit`, `_username`, and `_fetch_box` are already imported/defined in `blueprints/cooler_picking.py` — do not add duplicate imports for them.
- The demotion uses `is_order_ready()` directly rather than `update_order_status_batch_aware()`, because the latter cannot see the reopened box (the cooler queue rows are still `picked`, so it would leave the invoice at `ready_for_dispatch`). The explicit demotion is the exact mirror of the promotion block in `box_close` (which promotes `awaiting_batch_items`/`awaiting_packing` → `ready_for_dispatch` when `is_order_ready()` is true).
- The session revert mirrors the `box_close` auto-complete UPDATE (`session_type = 'cooler_route' AND route_id = :rid`); it only touches sessions currently at `Completed`, so cancelled/archived sessions are untouched.

### Testing checklist

- Close the last box of a route (session auto-flips to `Completed`, invoices promoted to `ready_for_dispatch`), then reopen that box. Verify: `SELECT status FROM batch_picking_sessions WHERE session_type='cooler_route' AND route_id=<RID>` is `In Progress`; affected invoices are back at `awaiting_batch_items`; the route's warehouse readiness flag is no longer "ready".
- Reopen a box on a route that still has other open boxes (session never auto-completed) — session UPDATE is a no-op, no errors.
- Reopen a box whose invoices were not yet `ready_for_dispatch` (e.g. still `awaiting_batch_items` because of other open boxes) — no invoice statuses change, no spurious audit rows.
- Re-close the reopened box — invoices get re-promoted by `box_close` and the session re-completes (round trip works).
- Attempt to reopen an `open` or `cancelled` box — still returns the 400 error.

---

## Bug 4 — `update_order_status_batch_aware` demotes ready invoices to `awaiting_packing`

**File:** `batch_aware_order_status.py`, inside `update_order_status_batch_aware()`, approx. lines 83–98.

When all items are picked and no cooler queue rows are pending, the function only preserves dispatch-readiness if the old status was `awaiting_batch_items`. An invoice already at `ready_for_dispatch` falls into the `else` branch and is rewritten to `awaiting_packing`. This regression fires whenever the recompute is run on an already-ready invoice — e.g. `cancel_batch`'s `affected_invoices` loop (an invoice that was in two batches, one completed and one later cancelled) or `update_all_orders_after_batch_completion`, and the new recompute loop added in Bug 1.

### Current code (find this exactly)

```python
    if picked_items == total_items and cooler_pending == 0:
        # All items picked. If the order had already been packed earlier
        # and was sitting in 'awaiting_batch_items' waiting on this batch
        # (Phase-5 cooler/batch integration), promote it straight to
        # ready_for_dispatch — packing is already done; the only thing
        # that was holding it back was the batch queue. Otherwise fall
        # back to the legacy behaviour of routing through awaiting_packing.
        try:
            from services.order_readiness import is_order_ready
            ready = is_order_ready(invoice_no)
        except Exception:
            ready = False
        if old_status == 'awaiting_batch_items' and ready:
            invoice.status = 'ready_for_dispatch'
        else:
            invoice.status = 'awaiting_packing'
```

### Replacement

```python
    if picked_items == total_items and cooler_pending == 0:
        # All items picked. If the order had already been packed earlier
        # and was sitting in 'awaiting_batch_items' waiting on this batch
        # (Phase-5 cooler/batch integration), promote it straight to
        # ready_for_dispatch — packing is already done; the only thing
        # that was holding it back was the batch queue. An invoice that
        # is ALREADY ready_for_dispatch must keep that status — a
        # recompute (e.g. from cancel_batch or batch completion) must
        # never demote a ready order back to awaiting_packing. Otherwise
        # fall back to the legacy behaviour of routing through
        # awaiting_packing.
        try:
            from services.order_readiness import is_order_ready
            ready = is_order_ready(invoice_no)
        except Exception:
            ready = False
        if old_status in ('awaiting_batch_items', 'ready_for_dispatch') and ready:
            invoice.status = 'ready_for_dispatch'
        else:
            invoice.status = 'awaiting_packing'
```

Note: the `and ready` guard is kept for the `ready_for_dispatch` case on purpose — if the invoice is no longer actually ready (e.g. a cooler box for it was reopened, Bug 3), the recompute is allowed to demote it to `awaiting_packing`.

### Testing checklist

- Set an invoice to `ready_for_dispatch` with all items picked and all queue rows/boxes terminal, then call `update_order_status_batch_aware('<INV>')` — status must remain `ready_for_dispatch` (and `status_changed` in the returned summary must be `False`).
- Cancel a batch whose `batch_session_invoices` includes an already-`ready_for_dispatch` invoice — verify the cancel's recompute loop does not demote it.
- Complete a batch (`update_all_orders_after_batch_completion`) containing a `ready_for_dispatch` invoice — status preserved.
- Verify the original promotion still works: invoice at `awaiting_batch_items` with all items picked and `is_order_ready()` true → `ready_for_dispatch`.
- Verify the legacy path still works: invoice at `picking` with all items picked → `awaiting_packing`.

---

## Suggested apply order

1. Bug 4 (one-line condition change, prerequisite for Bug 1's recompute loop not demoting ready invoices).
2. Bug 2a then 2b (lock semantics + lock release).
3. Bug 1 (recompute after unassign).
4. Bug 3 (box_reopen reversal).
