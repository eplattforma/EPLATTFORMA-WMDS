# WMDS Fix — Priority 4: Box State Consistency

All four bugs are in **`blueprints/cooler_picking.py`**. Each fix below gives the exact current code to find (copy-paste searchable) and the exact replacement. Apply them with find/replace — no other files need to change.

Background on the two tables involved:

- `batch_pick_queue` (bpq) — the master pick list. Statuses: `pending`, `picked`, `exception`.
- `cooler_box_items` (cbi) — items assigned to a physical cooler box. Statuses: `planned`, `picked`, `exception`. (`'pending'` is **not** a valid cbi status — it is a queue status.)
- `_is_cooler_route_pack_complete()` (line ~178) blocks route completion if any bpq row for the route is `pending` (check #2) or any cbi row is `planned` (check #2b).

---

## Bug 1 — Force-closing a box leaves `batch_pick_queue` rows `pending`, permanently blocking route completion

**File:** `blueprints/cooler_picking.py`, function `box_close()` (starts line ~2143), force branch at lines ~2205–2226.

When a manager closes a box with `force=1`, the code marks the box's unpicked `cooler_box_items` rows as `exception` — but the matching `batch_pick_queue` rows stay `pending`. `_is_cooler_route_pack_complete()` check #2 counts pending bpq rows and returns False, so the cooler session is never marked `Completed` and the route never reaches WAREHOUSE_READY. There is no UI path that fixes those orphaned pending rows afterwards.

**Find this exact code (lines ~2205–2217):**

```python
    if unpicked > 0 and force:
        # Mark planned (unphysically-picked) items as exception so the route
        # completion check no longer treats them as blocking planned rows.
        now_f = get_utc_now()
        affected = db.session.execute(
            text(
                "UPDATE cooler_box_items "
                "SET status = 'exception', updated_at = :now "
                "WHERE cooler_box_id = :bid "
                "  AND status = 'planned'"
            ),
            {"bid": box_id, "now": now_f},
        ).rowcount
```

**Replace with:**

```python
    if unpicked > 0 and force:
        # Mark planned (unphysically-picked) items as exception so the route
        # completion check no longer treats them as blocking planned rows.
        now_f = get_utc_now()
        # FIRST: mark the matching batch_pick_queue rows as exception too,
        # otherwise they stay 'pending' forever and
        # _is_cooler_route_pack_complete() check #2 permanently blocks the
        # route from completing. Must run BEFORE the cbi update below because
        # it joins on cbi.status = 'planned'.
        db.session.execute(
            text(
                "UPDATE batch_pick_queue bpq "
                "SET status = 'exception', updated_at = :now "
                "FROM cooler_box_items cbi "
                "WHERE cbi.queue_item_id = bpq.id "
                "  AND cbi.cooler_box_id = :bid "
                "  AND cbi.status = 'planned' "
                "  AND bpq.status = 'pending'"
            ),
            {"bid": box_id, "now": now_f},
        )
        affected = db.session.execute(
            text(
                "UPDATE cooler_box_items "
                "SET status = 'exception', updated_at = :now "
                "WHERE cooler_box_id = :bid "
                "  AND status = 'planned'"
            ),
            {"bid": box_id, "now": now_f},
        ).rowcount
```

Note the order: the new `batch_pick_queue` UPDATE must come **before** the existing `cooler_box_items` UPDATE, because it joins on `cbi.status = 'planned'` (which the second statement changes to `'exception'`). The `UPDATE ... FROM` syntax matches the PostgreSQL pattern already used earlier in the same function (the auto-reconcile block at line ~2160).

**Testing checklist:**

- Pre-plan or confirm a box plan so a box has at least one `planned` cbi row whose bpq row is `pending`.
- POST `/cooler/box/<box_id>/close` with `force=1`.
- Verify in DB: the cbi rows are `exception` **and** the corresponding `batch_pick_queue` rows are `exception` (not `pending`).
- Verify that once all other boxes are closed, the `batch_picking_sessions` row for the route flips to `Completed` and warehouse readiness recalculates.
- Regression: force-close a box where some items were genuinely picked — picked bpq rows must remain `picked` (only `pending` rows joined to that box's `planned` cbi rows are touched).

---

## Bug 2 — `pre_plan_boxes` inserts `cooler_box_items` with `status='pending'` instead of `'planned'`

**File:** `blueprints/cooler_picking.py`, function `pre_plan_boxes()` (starts line ~1645), item INSERT at lines ~1744–1772.

`confirm_box_plan()` (line ~1538–1539) deliberately maps queue status `pending` → cbi status `'planned'`, with a comment warning never to pass `'pending'` into `cooler_box_items`. But `pre_plan_boxes()` copies the raw queue status verbatim into the cbi INSERT, producing cbi rows with `status='pending'`. Every guard in the system filters on `status='planned'`: the `queue_pick` promotion, the `box_close` auto-reconcile (`AND cbi.status = 'planned'`), the force-close exception update, and `_is_cooler_route_pack_complete()` check #2b. Rows stuck at `'pending'` are invisible to all of them — they never get promoted to picked, never reconcile, and the `box_close` unpicked guard (`status = 'planned' OR picked_qty = 0`) counts them forever via `picked_qty = 0`/NULL semantics, while completion check #3 can also misbehave.

**Find this exact code (lines ~1744 and ~1769 — the variable assignment and the INSERT parameter):**

```python
                item_status = qcheck[0]
```

**Replace with:**

```python
                # bpq status is 'picked' or 'pending'; cooler_box_items only
                # allows ('planned','picked','exception') — map pending->planned
                # exactly like confirm_box_plan does.
                item_status = qcheck[0]
                cbi_status = "picked" if item_status == "picked" else "planned"
```

Then, in the parameter dict of the `INSERT INTO cooler_box_items` immediately below it, **find:**

```python
                        "qid": qid,
                        "status": item_status,
                        "ts": now,
```

**Replace with:**

```python
                        "qid": qid,
                        "status": cbi_status,
                        "ts": now,
```

(The other uses of `item_status` in that dict — `"pq"`, `"who"`, `"now"`, all comparing `item_status == "picked"` — are correct and must stay unchanged.)

**Testing checklist:**

- On a route with unpicked cooler items, POST `/cooler/route/<route_id>/<delivery_date>/pre-plan`.
- Verify in DB: every new `cooler_box_items` row has `status` of `'planned'` (for unpicked) or `'picked'` (for already-picked) — never `'pending'`.
- Pick one of the pre-planned items via the picking screen and confirm it is promoted (`planned` → `picked`) in `cooler_box_items`.
- Close the box normally after picking everything — the unpicked guard must not fire.
- Confirm the route completes (`_is_cooler_route_pack_complete` returns True) after all boxes are closed.

---

## Bug 3 — `queue_skip` and `queue_exception` leave planned `cooler_box_items` rows unchanged

**File:** `blueprints/cooler_picking.py`, functions `queue_skip()` (line ~2953) and `queue_exception()` (line ~3198). They are separate handlers — apply the fix in **both**.

When a picker skips an item or marks it as an exception, the `batch_pick_queue` row becomes `exception`, but any `cooler_box_items` row pre-assigned as `'planned'` for that queue item is left untouched. The box then permanently shows a planned item that will never be picked: the `box_close` unpicked guard counts it, and `_is_cooler_route_pack_complete()` check #2b blocks completion (forcing managers into force-close, which until Bug 1 was itself broken).

### 3a — `queue_skip`

**Find this exact code (lines ~2969–2977):**

```python
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'exception', updated_at = :now "
            "WHERE id = :qid AND status = 'pending'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_skipped",
```

**Replace with:**

```python
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'exception', updated_at = :now "
            "WHERE id = :qid AND status = 'pending'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    # Keep any pre-planned box assignment in sync — otherwise the box shows a
    # 'planned' item that will never be picked and can never be closed cleanly.
    db.session.execute(
        text(
            "UPDATE cooler_box_items "
            "SET status = 'exception', updated_at = :now "
            "WHERE queue_item_id = :qid AND status = 'planned'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_skipped",
```

### 3b — `queue_exception`

**Find this exact code (lines ~3220–3228):**

```python
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'exception', updated_at = :now "
            "WHERE id = :qid"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.queue_exception",
```

**Replace with:**

```python
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'exception', updated_at = :now "
            "WHERE id = :qid"
        ),
        {"now": now, "qid": queue_item_id},
    )
    # Keep any pre-planned box assignment in sync — otherwise the box shows a
    # 'planned' item that will never be picked and can never be closed cleanly.
    db.session.execute(
        text(
            "UPDATE cooler_box_items "
            "SET status = 'exception', updated_at = :now "
            "WHERE queue_item_id = :qid AND status = 'planned'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.queue_exception",
```

### 3c — Companion fix in `queue_resume` (line ~2999, recommended)

`queue_resume()` resets an exception bpq row back to `pending`. After 3a/3b, the cbi row will be `exception`; resuming should restore it to `planned` so the box assignment becomes active again.

**Find (lines ~3015–3023):**

```python
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'pending', updated_at = :now "
            "WHERE id = :qid AND status = 'exception'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_resumed",
```

**Replace with:**

```python
    now = get_utc_now()
    db.session.execute(
        text(
            "UPDATE batch_pick_queue "
            "SET status = 'pending', updated_at = :now "
            "WHERE id = :qid AND status = 'exception'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    # Mirror of queue_skip/queue_exception: restore the pre-planned box
    # assignment (exception -> planned) so the item is pickable into its box.
    db.session.execute(
        text(
            "UPDATE cooler_box_items cbi "
            "SET status = 'planned', updated_at = :now "
            "FROM cooler_boxes cb "
            "WHERE cbi.queue_item_id = :qid "
            "  AND cbi.status = 'exception' "
            "  AND cb.id = cbi.cooler_box_id "
            "  AND cb.status = 'open'"
        ),
        {"now": now, "qid": queue_item_id},
    )
    _audit(
        "cooler.item_resumed",
```

(The join on `cb.status = 'open'` ensures we never resurrect a planned row inside a box that was already force-closed or cancelled.)

**Testing checklist:**

- Pre-plan boxes (after Bug 2 fix) so a pending item has a `planned` cbi row, then POST `/cooler/queue/<id>/skip` — verify both bpq and cbi rows become `exception`.
- Same setup, POST `/cooler/queue/<id>/exception` with a reason — same verification.
- POST `/cooler/queue/<id>/resume` on the skipped item — bpq returns to `pending` and the cbi row returns to `planned` (box still open).
- Close the box containing a skipped item without `force` — the unpicked guard must not count the exception row as planned (note: it may still count via `picked_qty = 0`; force-close then works correctly per Bug 1).
- Skip an item with **no** box assignment — handler must still work (the cbi UPDATE simply matches 0 rows).

---

## Bug 4 — `pre_plan_boxes` counts cancelled boxes as existing, blocking re-pre-planning

**File:** `blueprints/cooler_picking.py`, function `pre_plan_boxes()`, `existing_boxes` check at lines ~1658–1672.

The guard counts **all** `cooler_boxes` rows for the route/date with no status filter. Boxes cancelled individually via `box_cancel` (line ~2388, sets `status = 'cancelled'`; cancelled is terminal — they can never be reopened) still count. So after every box on a route is cancelled, the guard still fires with "Boxes are already planned" and the manager can never pre-plan again. (The "Cancel Pre-Plan" button at line ~1798 *deletes* open boxes, but any box cancelled through the box-level cancel endpoint, or a session cancel that sets `status='cancelled'` at line ~622, leaves a row behind.)

**Find this exact code (lines ~1658–1664):**

```python
    existing_boxes = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = :dd"
        ),
        {"rid": route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0
```

**Replace with:**

```python
    existing_boxes = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_boxes "
            "WHERE route_id = :rid AND delivery_date = :dd "
            "  AND status != 'cancelled'"
        ),
        {"rid": route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0
```

**Required companion change in the same function** — box numbering. `pre_plan_boxes` currently assigns `box_no = idx` (1, 2, 3 …). If cancelled boxes with box numbers 1..N still exist for the route/date, re-planning would create duplicate box numbers (and violate a unique constraint on `(route_id, delivery_date, box_no)` if one exists). Offset from the existing max, exactly as `confirm_box_plan` does.

**Find (line ~1697–1699):**

```python
    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0
```

**Replace with:**

```python
    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0

    # Cancelled boxes may still occupy box numbers — continue numbering after
    # them so re-planning never produces duplicate box_no values.
    max_box_no = db.session.execute(
        text("SELECT COALESCE(MAX(box_no), 0) FROM cooler_boxes "
             "WHERE route_id = :rid AND delivery_date = :dd"),
        {"rid": route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0
```

**Then find (line ~1714–1716, inside the cooler_boxes INSERT parameter dict):**

```python
                    "rid": route_id_int, "dd": str(delivery_date),
                    "box_no": idx,
```

**Replace with:**

```python
                    "rid": route_id_int, "dd": str(delivery_date),
                    "box_no": int(max_box_no) + idx,
```

**Testing checklist:**

- Pre-plan a route, then cancel every box via POST `/cooler/box/<id>/cancel` (the box-level cancel, which sets `status='cancelled'`).
- POST `/cooler/route/<route_id>/<delivery_date>/pre-plan` again — it must succeed instead of flashing "Boxes are already planned".
- Verify the new boxes get box numbers continuing after the cancelled ones (no duplicate `box_no` for the route/date).
- Regression: with an **open** or **closed** (non-cancelled) box present, pre-plan must still be blocked by the guard.
- Regression: the normal "Cancel Pre-Plan" button (which deletes open boxes) followed by re-pre-plan still works.
