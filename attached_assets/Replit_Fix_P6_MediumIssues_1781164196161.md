# WMDS Fix — Priority 6: Medium Issues

Five medium-severity bugs in the cooler picking / batch picking subsystem. Each section gives the exact current code to find and the exact replacement. Apply with find/replace — the "Current code" blocks match the files verbatim.

---

## Bug 1 — `cancel_batch` leaves stale `cooler_box_items` rows when cancelling cooler boxes

**File:** `services/batch_picking.py` (approx. lines 570–580, inside `cancel_batch`)

When a cooler-route batch is cancelled, `cancel_batch` flips the session's open boxes to `status='cancelled'` but never deletes the `cooler_box_items` rows inside those boxes. The stale rows inflate item counts in warehouse readiness checks and `check_cooler_picks`, and collide with re-extraction (the pre-plan idempotency check counts `cooler_box_items` per `queue_item_id` and will skip items that still appear boxed). Fix: after cancelling the boxes, delete the `cooler_box_items` rows belonging to this session's cancelled boxes, scoped by `cooler_session_id = :sid` so only boxes of the batch being cancelled are touched.

**Current code (find this exact block):**

```python
        # Cooler-specific teardown: cancel any open boxes.
        if getattr(batch, 'session_type', None) == 'cooler_route':
            db.session.execute(
                text("""
                    UPDATE cooler_boxes
                    SET status = 'cancelled'
                    WHERE cooler_session_id = :sid
                      AND status NOT IN ('closed', 'loaded', 'delivered')
                """),
                {"sid": batch_id},
            )
```

**Replacement:**

```python
        # Cooler-specific teardown: cancel any open boxes.
        if getattr(batch, 'session_type', None) == 'cooler_route':
            db.session.execute(
                text("""
                    UPDATE cooler_boxes
                    SET status = 'cancelled'
                    WHERE cooler_session_id = :sid
                      AND status NOT IN ('closed', 'loaded', 'delivered')
                """),
                {"sid": batch_id},
            )
            # Remove box-item rows for the boxes just cancelled. Without
            # this, stale cooler_box_items inflate readiness counts and
            # block re-boxing after re-extraction. Scoped to this session
            # via cooler_session_id so closed/loaded/delivered boxes (and
            # other sessions' boxes) are untouched.
            db.session.execute(
                text("""
                    DELETE FROM cooler_box_items
                    WHERE cooler_box_id IN (
                        SELECT id FROM cooler_boxes
                        WHERE cooler_session_id = :sid
                          AND status = 'cancelled'
                    )
                """),
                {"sid": batch_id},
            )
```

Note: boxes cancelled earlier (e.g. emptied by `release_cooler_locks_for_invoice`) already have zero `cooler_box_items` rows, so the subquery on `status = 'cancelled'` only deletes rows from boxes cancelled in this operation.

**Testing checklist:**
- Create a cooler-route batch, pre-plan boxes (creates `cooler_box_items`), then cancel the batch via `cancel_batch`. Verify `SELECT COUNT(*) FROM cooler_box_items WHERE cooler_box_id IN (SELECT id FROM cooler_boxes WHERE cooler_session_id = <batch_id>)` returns 0.
- Verify boxes already in `closed`/`loaded`/`delivered` status keep their `cooler_box_items` rows after cancel.
- Verify `cooler_box_items` rows belonging to a different cooler session on the same route are untouched.
- Re-extract the same invoices into a new cooler session and pre-plan again — items must NOT be skipped as "already boxed".

---

## Bug 2 — `queue_move_to_normal` orphans the cooler queue row permanently

**File:** `blueprints/cooler_picking.py` (approx. lines 3260–3306, function `_move_zone`, used by `queue_move_to_normal` and `queue_move_to_cooler`)

Moving a cooler queue row to normal only flips `pick_zone_type = 'normal'`. The row stays attached to the cooler session (`batch_session_id` unchanged), the `invoice_items` row keeps `locked_by_batch_id` pointing at the cooler session, and normal-zone batch queries filter `pick_zone_type='cooler'` so no picker ever sees the row. Its `pending` status then blocks `is_order_ready` and warehouse readiness forever. Fix (cooler→normal direction only): cancel the queue row, release the cooler lock on the matching `invoice_items` row so a normal batch can lock it, and recompute the invoice status with `update_order_status_batch_aware` after commit. The normal→cooler direction (`queue_move_to_cooler`) is unchanged.

**Current code (find this exact block inside `_move_zone`):**

```python
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET pick_zone_type = :tgt, wms_zone = :zone, updated_at = :now "
            "WHERE id = :qid"
        ),
        {"tgt": target, "zone": snapshot_zone, "now": now, "qid": queue_item_id},
    )
    _audit(
        f"cooler.move_to_{target}",
        f"Queue #{queue_item_id} invoice={row[0]} item={row[1]} "
        f"moved {expected_from} -> {target} by {_username()} "
        f"(wms_zone snapshot={snapshot_zone})",
        invoice_no=row[0], item_code=row[1],
    )
    db.session.commit()
    return jsonify({
        "queue_item_id": queue_item_id, "pick_zone_type": target,
        "wms_zone": snapshot_zone,
    }), 200
```

**Replacement:**

```python
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET pick_zone_type = :tgt, wms_zone = :zone, updated_at = :now "
            "WHERE id = :qid"
        ),
        {"tgt": target, "zone": snapshot_zone, "now": now, "qid": queue_item_id},
    )
    if target == "normal":
        # The cooler session will never process this row (normal batch
        # queries filter pick_zone_type='cooler'), so: cancel the queue
        # row, release the cooler lock on the invoice item so a normal
        # batch can lock it, and recompute the invoice status below.
        db.session.execute(
            text(
                "UPDATE batch_pick_queue "
                "SET status = 'cancelled', cancelled_at = :now, updated_at = :now "
                "WHERE id = :qid AND status = 'pending'"
            ),
            {"now": now, "qid": queue_item_id},
        )
        db.session.execute(
            text(
                "UPDATE invoice_items "
                "SET locked_by_batch_id = NULL "
                "WHERE invoice_no = :inv "
                "  AND item_code = :ic "
                "  AND is_picked = FALSE "
                "  AND locked_by_batch_id IN ( "
                "    SELECT id FROM batch_picking_sessions "
                "    WHERE session_type = 'cooler_route' "
                "  )"
            ),
            {"inv": row[0], "ic": row[1]},
        )
    _audit(
        f"cooler.move_to_{target}",
        f"Queue #{queue_item_id} invoice={row[0]} item={row[1]} "
        f"moved {expected_from} -> {target} by {_username()} "
        f"(wms_zone snapshot={snapshot_zone})",
        invoice_no=row[0], item_code=row[1],
    )
    db.session.commit()
    if target == "normal":
        try:
            from batch_aware_order_status import update_order_status_batch_aware
            update_order_status_batch_aware(row[0])
        except Exception as _zs_err:
            current_app.logger.warning(
                "_move_zone: status recompute failed for %s: %s",
                row[0], _zs_err,
            )
    return jsonify({
        "queue_item_id": queue_item_id, "pick_zone_type": target,
        "wms_zone": snapshot_zone,
    }), 200
```

Notes: `cancelled_at` already exists on `batch_pick_queue` (set the same way in `cancel_batch`). `update_order_status_batch_aware` commits internally, which is why it is called after the main `db.session.commit()`. `current_app` is already imported at the top of `blueprints/cooler_picking.py`.

**Testing checklist:**
- Move a pending cooler queue row to normal via `POST /cooler/queue/<id>/move-to-normal`. Verify the queue row now has `status='cancelled'` and `pick_zone_type='normal'`.
- Verify the matching `invoice_items` row has `locked_by_batch_id IS NULL` and can be added to a normal-zone batch.
- Verify `is_order_ready(invoice_no)` is no longer blocked by the moved row once all other rows are terminal.
- Verify the invoice status was recomputed (no longer stuck in `awaiting_batch_items` if nothing else is pending).
- Verify `queue_move_to_cooler` (normal→cooler) behaviour is unchanged: row stays `pending`, zone flips to cooler.

---

## Bug 3 — Cooler session never terminates when all its invoices are unassigned

**File:** `services/cooler_route_extraction.py` (function `release_cooler_locks_for_invoice`, approx. lines 258–415)

Cooler session completion only fires inside `box_close` (which marks the session `'Completed'` when all boxes are packed). If every invoice on the route is unassigned — which deletes the queue rows — there are no boxes to close, so the session stays `'Created'` / `'In Progress'` forever. Invoices later re-added to the route then get a NEW sibling session, and warehouse readiness blocks the route because the stale old session is non-terminal. Fix: capture the affected cooler session ids before deleting the queue rows, and at the end of the function mark any of those sessions `'Completed'` if zero non-cancelled queue rows remain. The terminal string `'Completed'` matches `TERMINAL_COOLER_STATUSES = ("Completed", "Cancelled", "Archived")` defined at the top of this same file and the string used by `box_close`.

**Edit 3a — capture session ids at the top of the function. Find this exact block:**

```python
    # 1) Drop pending cooler queue rows (always)
    res = db.session.execute(
        text(
            "DELETE FROM batch_pick_queue "
            "WHERE invoice_no = :inv "
            "  AND pick_zone_type = 'cooler' "
            "  AND status = 'pending'"
        ),
        {"inv": invoice_no},
    )
    queue_deleted = res.rowcount or 0
```

**Replacement:**

```python
    # 0) Capture the cooler session(s) holding queue rows for this invoice
    # BEFORE deleting anything, so we can check for session completion at
    # the end (Bug fix: sessions whose every invoice is unassigned never
    # reach box_close and would otherwise stay non-terminal forever).
    affected_session_ids = [
        r[0] for r in db.session.execute(
            text(
                "SELECT DISTINCT batch_session_id FROM batch_pick_queue "
                "WHERE invoice_no = :inv "
                "  AND pick_zone_type = 'cooler' "
                "  AND batch_session_id IS NOT NULL"
            ),
            {"inv": invoice_no},
        ).fetchall()
    ]

    # 1) Drop pending cooler queue rows (always)
    res = db.session.execute(
        text(
            "DELETE FROM batch_pick_queue "
            "WHERE invoice_no = :inv "
            "  AND pick_zone_type = 'cooler' "
            "  AND status = 'pending'"
        ),
        {"inv": invoice_no},
    )
    queue_deleted = res.rowcount or 0
```

**Edit 3b — completion check at the END of the function, immediately before the return. Find this exact block:**

```python
    return {
        "queue_deleted": queue_deleted,
        "items_unlocked": items_unlocked,
        "box_items_removed": box_items_removed,
        "picked_queue_deleted": picked_queue_deleted,
        "items_unpicked": items_unpicked,
        "boxes_cancelled": boxes_cancelled,
    }
```

**Replacement:**

```python
    # 8) If a cooler session that held this invoice now has zero
    # non-cancelled queue rows left, mark it Completed so it cannot
    # block warehouse readiness forever (box_close never fires when
    # there are no boxes left to close).
    for _sid in affected_session_ids:
        try:
            remaining = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM batch_pick_queue "
                    "WHERE batch_session_id = :sid "
                    "  AND status != 'cancelled'"
                ),
                {"sid": _sid},
            ).scalar() or 0
            if remaining == 0:
                db.session.execute(
                    text(
                        "UPDATE batch_picking_sessions "
                        "SET status = 'Completed', last_activity_at = :now "
                        "WHERE id = :sid "
                        "  AND session_type = 'cooler_route' "
                        "  AND status NOT IN ('Completed', 'Cancelled', 'Archived')"
                    ),
                    {"sid": _sid, "now": get_utc_now()},
                )
                logger.info(
                    "release_cooler_locks_for_invoice: cooler session %s "
                    "marked Completed (no remaining queue rows) after "
                    "invoice %s released", _sid, invoice_no,
                )
        except Exception as _done_err:
            logger.warning(
                "release_cooler_locks_for_invoice: completion check failed "
                "for session %s: %s", _sid, _done_err,
            )

    return {
        "queue_deleted": queue_deleted,
        "items_unlocked": items_unlocked,
        "box_items_removed": box_items_removed,
        "picked_queue_deleted": picked_queue_deleted,
        "items_unpicked": items_unpicked,
        "boxes_cancelled": boxes_cancelled,
    }
```

Notes: `get_utc_now` is already imported at the top of `services/cooler_route_extraction.py` (`from timezone_utils import get_utc_now`). The function does not commit — the caller commits, same as the rest of this function's statements.

**Testing checklist:**
- Create a route with one cooler invoice (session created, queue rows written). Unassign the invoice with `full_reset=True` and commit. Verify the cooler session status is now `'Completed'`.
- With two cooler invoices on the route, unassign only one. Verify the session stays non-terminal (the other invoice's queue rows remain).
- After completing the stale session, re-add an invoice to the route — verify `get_or_create_cooler_session` creates a new sibling session (`COOLER-ROUTE-<id>-2`).
- Verify warehouse readiness condition 2 no longer blocks a route whose only cooler session was emptied by unassignment.
- Verify `full_reset=False` path (pending rows deleted, picked rows kept): session is NOT completed if picked rows remain.

---

## Bug 4 — `queue_pick` can promote an invoice to `ready_for_dispatch` before any box exists

**File:** `blueprints/cooler_picking.py` (approx. lines 2534–2561, inside `queue_pick`)

After a pick, `queue_pick` calls `is_order_ready()` and promotes the invoice to `ready_for_dispatch`. But the cooler-box sub-check in `services/order_readiness.py` counts open boxes (`SELECT COUNT(DISTINCT cb.id) FROM cooler_boxes cb JOIN cooler_box_items cbi ... WHERE cb.status NOT IN ('closed','loaded','delivered')`) — when ZERO boxes exist for the invoice, the count is 0 and the check passes vacuously. So an invoice whose cooler items are all picked but never boxed gets marked ready for dispatch prematurely. Fix: guard the promotion — only promote if at least one non-cancelled `cooler_boxes` row exists for the invoice's route + delivery date. `row[6]` is `i.route_id` and `row[7]` is `s.delivery_date` from the query at the top of `queue_pick`; `cooler_boxes.delivery_date` is stored as `str(delivery_date)` everywhere in this file, hence the `str(row[7])` comparison.

**Current code (find this exact block):**

```python
        # Promotion check: if this invoice was waiting on cooler items and is
        # now fully ready, advance it to ready_for_dispatch immediately.
        # This is the safety net for items that are picked but never boxed
        # (location_order mode, or any case where box_close won't fire).
        # The same logic also lives in box_close; having it here ensures the
        # status moves forward even when no box is ever created for the item.
        _invoice_no = row[1]
        try:
            from services.order_readiness import is_order_ready
            from models import Invoice as _Invoice
            _inv = _Invoice.query.filter_by(invoice_no=_invoice_no).first()
            if _inv is not None \
                    and _inv.status in ("awaiting_batch_items", "awaiting_packing") \
                    and is_order_ready(_invoice_no):
                _prev_status = _inv.status
                _inv.status = "ready_for_dispatch"
                _audit(
                    "cooler.order_ready_for_dispatch",
                    f"Invoice {_invoice_no} promoted "
                    f"{_prev_status} -> ready_for_dispatch "
                    f"after cooler queue item #{queue_item_id} picked",
                    invoice_no=_invoice_no,
                )
        except Exception as _exc:
            current_app.logger.warning(
                "cooler.queue_pick: promotion check failed for %s: %s",
                _invoice_no, _exc,
            )
```

**Replacement:**

```python
        # Promotion check: if this invoice was waiting on cooler items and is
        # now fully ready, advance it to ready_for_dispatch immediately.
        # GUARD: is_order_ready()'s cooler-box sub-check passes vacuously
        # when ZERO boxes exist for the invoice (zero boxes -> "all boxes
        # closed"). Require at least one non-cancelled cooler box on this
        # invoice's route before promoting, so a picked-but-never-boxed
        # invoice cannot be marked ready_for_dispatch prematurely.
        _invoice_no = row[1]
        try:
            from services.order_readiness import is_order_ready
            from models import Invoice as _Invoice
            _inv = _Invoice.query.filter_by(invoice_no=_invoice_no).first()
            _box_count = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM cooler_boxes "
                    "WHERE route_id = :rid "
                    "  AND delivery_date = :dd "
                    "  AND status != 'cancelled'"
                ),
                {"rid": row[6], "dd": str(row[7])},
            ).scalar() or 0
            if _inv is not None \
                    and _box_count > 0 \
                    and _inv.status in ("awaiting_batch_items", "awaiting_packing") \
                    and is_order_ready(_invoice_no):
                _prev_status = _inv.status
                _inv.status = "ready_for_dispatch"
                _audit(
                    "cooler.order_ready_for_dispatch",
                    f"Invoice {_invoice_no} promoted "
                    f"{_prev_status} -> ready_for_dispatch "
                    f"after cooler queue item #{queue_item_id} picked",
                    invoice_no=_invoice_no,
                )
        except Exception as _exc:
            current_app.logger.warning(
                "cooler.queue_pick: promotion check failed for %s: %s",
                _invoice_no, _exc,
            )
```

Notes: if `row[7]` (delivery_date) is NULL the count is 0 and promotion is skipped — correct, since no boxes can exist for a route with no delivery date. The promotion in `box_close` is unaffected (a box necessarily exists there) and still promotes the invoice once its boxes close.

**Testing checklist:**
- Route in box-pack flow with zero boxes created: pick all cooler items for an invoice in `awaiting_batch_items`. Verify the invoice is NOT promoted to `ready_for_dispatch`.
- Then pre-plan/create a box, assign the items, close the box. Verify the invoice IS promoted (via `box_close`).
- With a non-cancelled box already existing for the route, pick the last cooler item — verify promotion still works from `queue_pick` once `is_order_ready` is True.
- Verify a route whose only box is `status='cancelled'` does not allow promotion from `queue_pick`.

---

## Bug 5 — `_audit()` called after `db.session.commit()` in `pre_plan_boxes` and `cancel_pre_plan` — audit rows never persisted

**File:** `blueprints/cooler_picking.py` (approx. lines 1773–1786 in `pre_plan_boxes`; approx. lines 1815–1830 in `cancel_pre_plan`)

`_audit()` (line ~340) only does `db.session.add(ActivityLog(...))` — it does not commit. In both functions the call happens AFTER the main `db.session.commit()` with no further commit before the redirect, so the `ActivityLog` row sits uncommitted in the session and is discarded at request teardown. The `cooler.pre_plan` and `cooler.cancel_preplan` audit events are never persisted. Fix: move `_audit(...)` to before `db.session.commit()` in both functions.

**Edit 5a — `pre_plan_boxes`. Current code (find this exact block):**

```python
        db.session.commit()
        _audit(
            "cooler.pre_plan",
            f"Pre-planned {created_boxes} box(es) for route {route_id} "
            f"date={delivery_date} — {skipped_items} item(s) skipped",
        )
        flash(
```

**Replacement:**

```python
        _audit(
            "cooler.pre_plan",
            f"Pre-planned {created_boxes} box(es) for route {route_id} "
            f"date={delivery_date} — {skipped_items} item(s) skipped",
        )
        db.session.commit()
        flash(
```

(The `—` in the f-string is a literal em dash — keep it exactly as-is so the find matches.)

**Edit 5b — `cancel_pre_plan`. Current code (find this exact block):**

```python
    db.session.commit()
    _audit(
        "cooler.cancel_preplan",
        f"Cancelled pre-plan for route {route_id} date={delivery_date}",
    )
    flash("Pre-plan cancelled. You can now generate a new plan.", "info")
```

**Replacement:**

```python
    _audit(
        "cooler.cancel_preplan",
        f"Cancelled pre-plan for route {route_id} date={delivery_date}",
    )
    db.session.commit()
    flash("Pre-plan cancelled. You can now generate a new plan.", "info")
```

**Testing checklist:**
- Run pre-plan for a route, then check `SELECT * FROM activity_log WHERE activity_type = 'cooler.pre_plan' ORDER BY id DESC LIMIT 1` — a row must now exist with the route id and box count.
- Run cancel-preplan and verify a `cooler.cancel_preplan` row is persisted in `activity_log`.
- Verify the boxes/box-items themselves are still committed correctly (pre-plan creates them, cancel removes open ones).
- Force an exception inside the pre-plan loop (e.g. invalid box_type) and verify the rollback path still works and no partial audit row is committed.

---

## Apply order

The five fixes are independent — apply in any order. Bugs 2, 4, 5 all live in `blueprints/cooler_picking.py`; the find targets are unique within the file, so plain find/replace is safe.
