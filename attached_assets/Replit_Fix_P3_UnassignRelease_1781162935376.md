# WMDS Fix — Priority 3: Unassign & Release Integrity

Four data-integrity bugs in the invoice unassign / cooler-lock release flow. All four touch the same function (`release_cooler_locks_for_invoice` in `services/cooler_route_extraction.py`), plus one endpoint in `routes_routes.py` and one check in `services/route_warehouse_readiness.py`.

**IMPORTANT: All edits in this document must be applied, in the order written.** The "Shared changes" section adds counters and return-dict keys that the per-bug edits rely on — apply it first. Every "FIND" block is the exact current code; every "REPLACE" block is the exact new code. Use exact string matching (whitespace included).

For context, the DB is PostgreSQL. Item dimensions live in `ps_items_dw` (columns `item_code_365`, `item_length`, `item_width`, `item_height`, `item_weight`); item weight fallback lives in `invoice_items.item_weight`. `cooler_box_items.status` allows `('planned','picked','exception')` and has a unique index on `queue_item_id`. `get_utc_now` is already imported at the top of `services/cooler_route_extraction.py` (`from timezone_utils import get_utc_now`).

---

## Shared changes (apply FIRST)

**File:** `services/cooler_route_extraction.py`, inside `release_cooler_locks_for_invoice` (function starts around line 258).

### Shared change A — counter initialization (around line 304)

FIND:

```python
    box_items_removed = 0
    picked_queue_deleted = 0
    items_unpicked = 0
    boxes_cancelled = 0
```

REPLACE WITH:

```python
    box_items_removed = 0
    picked_queue_deleted = 0
    items_unpicked = 0
    boxes_cancelled = 0
    closed_boxes_voided = 0
    picked_flagged_for_return = 0
```

### Shared change B — return dict (around line 410, end of the function)

FIND:

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

REPLACE WITH:

```python
    return {
        "queue_deleted": queue_deleted,
        "items_unlocked": items_unlocked,
        "box_items_removed": box_items_removed,
        "picked_queue_deleted": picked_queue_deleted,
        "items_unpicked": items_unpicked,
        "boxes_cancelled": boxes_cancelled,
        "closed_boxes_voided": closed_boxes_voided,
        "planned_box_items_removed": planned_box_items_removed,
        "picked_flagged_for_return": picked_flagged_for_return,
    }
```

(`planned_box_items_removed` is assigned unconditionally near the top of the function by the Bug 2 edit below — that is why Bug 2 is mandatory before this code runs.)

---

## Bug 1 — full_reset: box cancellation counter wrong + fill not recalculated

**File:** `services/cooler_route_extraction.py`, Step 7 of the `full_reset=True` block, approx. lines 372–408.

**What the bug is:** In the full-reset path, step 7 cancels boxes that became empty with `UPDATE cooler_boxes SET status = 'cancelled' WHERE id = :bid AND status = 'open'`. Because of the `AND status = 'open'` guard, the UPDATE silently no-ops on boxes that were already `closed` — but `boxes_cancelled += 1` runs regardless, so the returned counter overstates what happened, and a closed box that is now completely empty stays `closed` and will still appear on manifests and labels. Additionally, the function's docstring promises "Recalculates fill on any boxes that lost items", but the non-empty branch only updates `first_stop_sequence`/`last_stop_sequence` — `fill_cm3` and `fill_weight_kg` are never recalculated, so box fill numbers on the picking UI remain inflated after items are removed.

### Exact current code (FIND)

```python
        # 7) Recalculate fill on affected boxes; cancel any that are now empty
        for box_id in affected_box_ids:
            remaining = db.session.execute(
                text(
                    "SELECT COUNT(*), "
                    "       MIN(delivery_sequence), "
                    "       MAX(delivery_sequence) "
                    "FROM cooler_box_items "
                    "WHERE cooler_box_id = :bid"
                ),
                {"bid": box_id},
            ).fetchone()

            if not remaining or remaining[0] == 0:
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET status = 'cancelled' "
                        "WHERE id = :bid AND status = 'open'"
                    ),
                    {"bid": box_id},
                )
                boxes_cancelled += 1
            else:
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, "
                        "    last_stop_sequence  = :ls "
                        "WHERE id = :bid"
                    ),
                    {
                        "fs": remaining[1],
                        "ls": remaining[2],
                        "bid": box_id,
                    },
                )
```

### Replacement (REPLACE WITH)

```python
        # 7) Recalculate fill on affected boxes; cancel any that are now empty
        for box_id in affected_box_ids:
            remaining = db.session.execute(
                text(
                    "SELECT COUNT(*), "
                    "       MIN(delivery_sequence), "
                    "       MAX(delivery_sequence) "
                    "FROM cooler_box_items "
                    "WHERE cooler_box_id = :bid"
                ),
                {"bid": box_id},
            ).fetchone()

            if not remaining or remaining[0] == 0:
                # Cancel open boxes; only count when a row actually changed.
                res_open = db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET status = 'cancelled' "
                        "WHERE id = :bid AND status = 'open'"
                    ),
                    {"bid": box_id},
                )
                if (res_open.rowcount or 0) > 0:
                    boxes_cancelled += 1
                else:
                    # The box was already 'closed'. It cannot be un-closed,
                    # but it is now EMPTY — mark it 'cancelled' so it stops
                    # appearing on manifests/labels, and count separately.
                    res_closed = db.session.execute(
                        text(
                            "UPDATE cooler_boxes "
                            "SET status = 'cancelled' "
                            "WHERE id = :bid AND status = 'closed'"
                        ),
                        {"bid": box_id},
                    )
                    closed_boxes_voided += res_closed.rowcount or 0
            else:
                # Box still has items: re-sum fill volume/weight from the
                # remaining cooler_box_items (dimensions from ps_items_dw,
                # weight fallback from invoice_items) and refresh the stop
                # sequence window. Uses picked_qty when set, else expected_qty
                # — mirroring the planner's estimation formula.
                fill_row = db.session.execute(
                    text(
                        "SELECT "
                        "  COALESCE(SUM("
                        "    COALESCE(dw.item_length, 0) * "
                        "    COALESCE(dw.item_width, 0) * "
                        "    COALESCE(dw.item_height, 0) * "
                        "    COALESCE(NULLIF(cbi.picked_qty, 0), cbi.expected_qty, 0)"
                        "  ), 0) AS vol_cm3, "
                        "  COALESCE(SUM("
                        "    COALESCE(dw.item_weight, ii.item_weight, 0) * "
                        "    COALESCE(NULLIF(cbi.picked_qty, 0), cbi.expected_qty, 0)"
                        "  ), 0) AS weight_kg "
                        "FROM cooler_box_items cbi "
                        "LEFT JOIN ps_items_dw dw "
                        "  ON dw.item_code_365 = cbi.item_code "
                        "LEFT JOIN invoice_items ii "
                        "  ON ii.invoice_no = cbi.invoice_no "
                        " AND ii.item_code = cbi.item_code "
                        "WHERE cbi.cooler_box_id = :bid"
                    ),
                    {"bid": box_id},
                ).fetchone()

                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, "
                        "    last_stop_sequence  = :ls, "
                        "    fill_cm3            = :fc, "
                        "    fill_weight_kg      = :fw "
                        "WHERE id = :bid"
                    ),
                    {
                        "fs": remaining[1],
                        "ls": remaining[2],
                        "fc": float(fill_row[0] or 0),
                        "fw": float(fill_row[1] or 0),
                        "bid": box_id,
                    },
                )
```

### Testing checklist

- Full-reset an invoice whose items sit in an **open** box that becomes empty → box status becomes `cancelled`, result has `boxes_cancelled == 1`, `closed_boxes_voided == 0`.
- Full-reset an invoice whose items sit in a **closed** box that becomes empty → box status becomes `cancelled` (not left `closed`), result has `boxes_cancelled == 0`, `closed_boxes_voided == 1`, and the box no longer appears on the box manifest/labels page.
- Full-reset an invoice sharing a box with another invoice → box stays `open`/`closed`, `fill_cm3` and `fill_weight_kg` shrink to the sum of the remaining items only (verify against `SELECT` of remaining `cooler_box_items` joined to `ps_items_dw`).
- Box with remaining items where some item codes have no `ps_items_dw` row → no crash, those items contribute 0 volume and fall back to `invoice_items.item_weight` for weight.
- Re-run the same full reset twice → idempotent, counters all 0 the second time.

---

## Bug 2 — `check_cooler_picks` misses pre-assigned (planned) items; default unassign leaves stale planned `cooler_box_items` rows

**Files:** `routes_routes.py` (approx. lines 1098–1117) and `services/cooler_route_extraction.py` (approx. lines 276–286).

**What the bug is:** The pre-unassign warning endpoint `check_cooler_picks` only looks at queue rows with `bpq.status = 'picked'`. When a box plan has been confirmed before picking, pending queue rows (`batch_pick_queue.status = 'pending'`) already have a `cooler_box_items` row with `status='planned'` pointing at them via `queue_item_id`. Those invoices produce **no warning**, so the frontend runs the default unassign (`force_reset=False`), which deletes the pending queue rows — leaving the planned `cooler_box_items` rows behind with dangling `queue_item_id` references to deleted queue rows. Those orphans corrupt box manifests and the pick-to-box flow.

### Part 2a — extend the warning SQL in `check_cooler_picks`

**File:** `routes_routes.py`

#### Exact current code (FIND)

```python
    rows = db.session.execute(
        text(
            "SELECT bpq.invoice_no, "
            "       COUNT(*) AS picked_count, "
            "       COUNT(cbi.id) AS boxed_count "
            "FROM batch_pick_queue bpq "
            "LEFT JOIN cooler_box_items cbi ON cbi.queue_item_id = bpq.id "
            "WHERE bpq.invoice_no = ANY(:inv) "
            "  AND bpq.pick_zone_type = 'cooler' "
            "  AND bpq.status = 'picked' "
            "GROUP BY bpq.invoice_no"
        ),
        {"inv": invoice_nos},
    ).fetchall()

    affected = [
        {"invoice_no": r[0], "picked_count": int(r[1]), "boxed_count": int(r[2])}
        for r in rows if r[1] > 0
    ]
    return jsonify({"ok": True, "affected": affected})
```

#### Replacement (REPLACE WITH)

```python
    rows = db.session.execute(
        text(
            "SELECT bpq.invoice_no, "
            "       COUNT(*) FILTER (WHERE bpq.status = 'picked') AS picked_count, "
            "       COUNT(cbi.id) FILTER (WHERE bpq.status = 'picked') AS boxed_count, "
            "       COUNT(cbi.id) FILTER (WHERE bpq.status = 'pending' "
            "                               AND cbi.status = 'planned') AS planned_count "
            "FROM batch_pick_queue bpq "
            "LEFT JOIN cooler_box_items cbi ON cbi.queue_item_id = bpq.id "
            "WHERE bpq.invoice_no = ANY(:inv) "
            "  AND bpq.pick_zone_type = 'cooler' "
            "  AND bpq.status IN ('picked', 'pending') "
            "GROUP BY bpq.invoice_no"
        ),
        {"inv": invoice_nos},
    ).fetchall()

    affected = [
        {
            "invoice_no": r[0],
            "picked_count": int(r[1]),
            "boxed_count": int(r[2]),
            "planned_count": int(r[3]),
        }
        for r in rows if r[1] > 0 or r[3] > 0
    ]
    return jsonify({"ok": True, "affected": affected})
```

(`COUNT(...) FILTER (WHERE ...)` is standard PostgreSQL conditional aggregation. The response shape gains a `planned_count` key per affected invoice; existing keys are unchanged, so existing frontend code keeps working.)

### Part 2b — clean up planned box rows in the default unassign path

**File:** `services/cooler_route_extraction.py`, very start of `release_cooler_locks_for_invoice`'s body (the "Step 1" pending delete, approx. lines 276–286).

#### Exact current code (FIND)

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

#### Replacement (REPLACE WITH)

```python
    # 0) Remove planned cooler_box_items rows whose backing queue row is a
    #    PENDING row for this invoice — those queue rows are deleted in
    #    step 1 below, and leaving the planned box assignments behind would
    #    create dangling queue_item_id references (orphans on manifests and
    #    in the pick-to-box flow). Runs in BOTH the default and full_reset
    #    paths; in full_reset the broader delete later is a harmless no-op
    #    for these rows.
    res0 = db.session.execute(
        text(
            "DELETE FROM cooler_box_items "
            "WHERE queue_item_id IN ( "
            "    SELECT id FROM batch_pick_queue "
            "    WHERE invoice_no = :inv "
            "      AND pick_zone_type = 'cooler' "
            "      AND status = 'pending' "
            ")"
        ),
        {"inv": invoice_no},
    )
    planned_box_items_removed = res0.rowcount or 0

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

Note: the cleanup DELETE must run **before** the step-1 queue delete, because it identifies victims via the still-existing pending queue rows. The order above is mandatory.

### Testing checklist

- Confirm a box plan (creates `cooler_box_items` with `status='planned'`), do NOT pick, call `POST /check-cooler-picks` for that invoice → response includes the invoice with `planned_count > 0` and `picked_count == 0`.
- Invoice with only unplanned pending queue rows (no box plan) → still NOT in `affected` (no false warning).
- Default unassign (`force_cooler_reset=false`) an invoice with planned box assignments → `SELECT COUNT(*) FROM cooler_box_items cbi LEFT JOIN batch_pick_queue bpq ON bpq.id = cbi.queue_item_id WHERE cbi.queue_item_id IS NOT NULL AND bpq.id IS NULL` returns 0 (no dangling refs); return dict shows `planned_box_items_removed` equal to the number of planned rows removed.
- Default unassign an invoice with PICKED+boxed items → the picked `cooler_box_items` rows (status `'picked'`) are still preserved (audit trail unchanged).
- Full reset still works: `box_items_removed + planned_box_items_removed` accounts for every `cooler_box_items` row the invoice had.

---

## Bug 3 — full reset leaves `invoice_items.is_picked = TRUE` when the lock was already cleared

**File:** `services/cooler_route_extraction.py`, Step 6 of the `full_reset` block, approx. lines 355–370.

**What the bug is:** Step 6 resets `is_picked` only for rows whose `locked_by_batch_id` currently points at a cooler session. But once picking completes, the batch-session auto-clear / orphan-lock cleanup sets `locked_by_batch_id = NULL` on those rows. So by the time a manager runs a full reset, the `WHERE ... locked_by_batch_id IN (...)` clause matches nothing: step 5 has already deleted the picked queue rows, yet `invoice_items.is_picked` stays `TRUE`. The item has vanished from every queue/box, but the DB still claims it is physically picked — it can never be re-extracted or re-picked correctly.

### Exact current code (FIND)

```python
        # 6) Reset is_picked on invoice_items that were picked in cooler context
        res5 = db.session.execute(
            text(
                "UPDATE invoice_items "
                "SET is_picked = FALSE, "
                "    locked_by_batch_id = NULL "
                "WHERE invoice_no = :inv "
                "  AND is_picked = TRUE "
                "  AND locked_by_batch_id IN ( "
                "        SELECT id FROM batch_picking_sessions "
                "        WHERE session_type = 'cooler_route' "
                "  )"
            ),
            {"inv": invoice_no},
        )
        items_unpicked = res5.rowcount or 0
```

### Replacement (REPLACE WITH)

```python
        # 6) Reset is_picked on invoice_items that were picked in cooler context.
        #
        # WHY THE BROAD CONDITION: we cannot filter on the current
        # locked_by_batch_id — once picking completes, the batch auto-clear /
        # orphan-lock cleanup sets locked_by_batch_id = NULL, so a filter on
        # the lock matches nothing by the time a manager runs a full reset
        # (leaving is_picked stuck TRUE forever). We also cannot join through
        # the queue rows, because step 5 above already deleted them. Resetting
        # ALL is_picked = TRUE rows for this invoice is safe HERE because
        # this function only runs for invoices in the cooler unassign flow,
        # and full_reset means the user explicitly confirmed a complete
        # return-to-warehouse: every physically picked item for this invoice
        # (cooler or otherwise) is coming back to stock and must be re-picked
        # when the invoice is re-routed. Normal-zone items use a different
        # is_picked lifecycle that does not pass through this function, and
        # after a full reset they too must be re-picked from scratch.
        res5 = db.session.execute(
            text(
                "UPDATE invoice_items "
                "SET is_picked = FALSE, "
                "    locked_by_batch_id = NULL "
                "WHERE invoice_no = :inv "
                "  AND is_picked = TRUE"
            ),
            {"inv": invoice_no},
        )
        items_unpicked = res5.rowcount or 0
```

### Testing checklist

- Pick a cooler item to completion, let the session auto-clear (`locked_by_batch_id` becomes NULL), then full-reset the invoice → `invoice_items.is_picked` is `FALSE` and `items_unpicked >= 1` in the returned dict.
- Full-reset an invoice whose cooler items are still locked (`locked_by_batch_id` set) → still works exactly as before; lock cleared and `is_picked = FALSE`.
- After a full reset, re-attach the invoice to a route → cooler extraction re-queues the items normally (no `picked_warning` / `already_picked` data-quality entries from the stale flag).
- Default unassign (`force_reset=False`) → step 6 does NOT run; picked items keep `is_picked = TRUE` (audit-preserving behaviour unchanged).
- Verify no `batch_pick_queue` or `cooler_box_items` rows remain for the invoice after full reset while `SELECT COUNT(*) FROM invoice_items WHERE invoice_no = :inv AND is_picked = TRUE` returns 0.

---

## Bug 4 — picked-but-unboxed rows after a default unassign permanently block WAREHOUSE_READY on the old route

**Files:** `services/cooler_route_extraction.py` (non-full_reset path) and `services/route_warehouse_readiness.py` (condition 3, approx. lines 72–91).

**What the bug is:** The default unassign (`force_reset=False`) deletes pending queue rows but deliberately preserves `status='picked'` queue rows for audit. Those rows stay linked to the OLD route's cooler session via `batch_pick_queue.batch_session_id`. Readiness condition 3 counts every queue row on the route's cooler sessions with `qty_picked > 0` that has no `cooler_box_items` row — so a picked-but-unboxed item from a since-unassigned invoice keeps "Cooler items unboxed (N…)" in the blockers forever, and the old route can never reach `WAREHOUSE_READY` even after all remaining invoices are finished. The fix flags those rows `status='needs_return'` (not deleted — the picker may physically hold the items, and the audit trail is preserved) and makes condition 3 count only rows still in `status='picked'`.

### Part 4a — flag picked rows as `needs_return` in the non-full_reset path

**File:** `services/cooler_route_extraction.py`. The "non-full_reset block that handles picked rows" is implicit today — picked rows are simply left untouched after Step 2. Insert an explicit handler immediately after Step 2.

#### Exact current code (FIND)

```python
    res2 = db.session.execute(
        text(
            "UPDATE invoice_items "
            "SET locked_by_batch_id = NULL "
            "WHERE invoice_no = :inv "
            "  AND is_picked = FALSE "
            "  AND locked_by_batch_id IN ( "
            "    SELECT id FROM batch_picking_sessions "
            "    WHERE session_type = 'cooler_route' "
            "  )"
        ),
        {"inv": invoice_no},
    )
    items_unlocked = res2.rowcount or 0
```

#### Replacement (REPLACE WITH)

```python
    res2 = db.session.execute(
        text(
            "UPDATE invoice_items "
            "SET locked_by_batch_id = NULL "
            "WHERE invoice_no = :inv "
            "  AND is_picked = FALSE "
            "  AND locked_by_batch_id IN ( "
            "    SELECT id FROM batch_picking_sessions "
            "    WHERE session_type = 'cooler_route' "
            "  )"
        ),
        {"inv": invoice_no},
    )
    items_unlocked = res2.rowcount or 0

    # 2b) Default (non-full_reset) path: picked queue rows are preserved
    #     for audit, but they must not keep blocking the OLD route's
    #     WAREHOUSE_READY check forever (readiness condition 3 counts
    #     picked-but-unboxed rows via bps.route_id). Flag them
    #     'needs_return' — not deleted, because the picker may physically
    #     have these items in hand and they need follow-up (return to
    #     cooler stock). Readiness only counts status = 'picked'.
    if not full_reset:
        res_nr = db.session.execute(
            text(
                "UPDATE batch_pick_queue "
                "SET status = 'needs_return', "
                "    updated_at = :now "
                "WHERE invoice_no = :inv "
                "  AND pick_zone_type = 'cooler' "
                "  AND status = 'picked'"
            ),
            {"inv": invoice_no, "now": get_utc_now()},
        )
        picked_flagged_for_return = res_nr.rowcount or 0
```

### Part 4b — make readiness condition 3 ignore `needs_return` rows

**File:** `services/route_warehouse_readiness.py`, condition "3 & 6" inside `check_route_warehouse_ready`.

#### Exact current code (FIND)

```python
            # 3 & 6. No picked cooler items unassigned to a box
            unboxed = conn.execute(
                db.text(
                    "SELECT COUNT(*) "
                    "FROM batch_pick_queue bpq "
                    "JOIN batch_picking_sessions bps ON bps.id = bpq.batch_session_id "
                    "WHERE bps.route_id = :rid "
                    "  AND bps.session_type = 'cooler_route' "
                    "  AND bpq.qty_picked > 0 "
                    "  AND NOT EXISTS ("
                    "      SELECT 1 FROM cooler_box_items cbi "
                    "      WHERE cbi.queue_item_id = bpq.id"
                    "  )"
                ),
                {"rid": route_id},
            ).scalar() or 0
```

#### Replacement (REPLACE WITH)

```python
            # 3 & 6. No picked cooler items unassigned to a box.
            # Only rows still in status='picked' count — rows flagged
            # 'needs_return' belong to invoices unassigned from this route
            # and are handled by the picker-return follow-up flow, so they
            # must not block WAREHOUSE_READY here.
            unboxed = conn.execute(
                db.text(
                    "SELECT COUNT(*) "
                    "FROM batch_pick_queue bpq "
                    "JOIN batch_picking_sessions bps ON bps.id = bpq.batch_session_id "
                    "WHERE bps.route_id = :rid "
                    "  AND bps.session_type = 'cooler_route' "
                    "  AND bpq.status = 'picked' "
                    "  AND bpq.qty_picked > 0 "
                    "  AND NOT EXISTS ("
                    "      SELECT 1 FROM cooler_box_items cbi "
                    "      WHERE cbi.queue_item_id = bpq.id"
                    "  )"
                ),
                {"rid": route_id},
            ).scalar() or 0
```

### Testing checklist

- Pick a cooler item (no box assignment), default-unassign its invoice → the `batch_pick_queue` row still exists with `status = 'needs_return'`, `qty_picked` unchanged, `updated_at` refreshed; return dict shows `picked_flagged_for_return == 1`.
- After that unassign, finish all remaining work on the old route and run `recalculate_route_warehouse_status(route_id)` → route reaches `WAREHOUSE_READY`; `check_route_warehouse_ready` returns no "Cooler items unboxed" blocker.
- A genuinely picked-but-unboxed item on a still-assigned invoice (status `'picked'`) → still blocks readiness exactly as before (condition 3 not weakened for live work).
- `needs_return` rows do not reappear in the picker's queue (picker queue queries filter `status = 'pending'`) and do not trip readiness condition 7 (which filters `status = 'pending'`).
- Full reset (`force_cooler_reset=true`) on the same invoice → picked rows are deleted as before (step 5 runs, the `needs_return` flagging is skipped because of `if not full_reset`).

---

## Apply order summary

1. Shared change A (counter init) — `services/cooler_route_extraction.py`
2. Shared change B (return dict) — `services/cooler_route_extraction.py`
3. Bug 2 Part 2b (planned cbi cleanup before step 1) — `services/cooler_route_extraction.py`
4. Bug 4 Part 4a (needs_return flagging after step 2) — `services/cooler_route_extraction.py`
5. Bug 3 (step 6 broad is_picked reset) — `services/cooler_route_extraction.py`
6. Bug 1 (step 7 rewrite) — `services/cooler_route_extraction.py`
7. Bug 2 Part 2a (`check_cooler_picks` SQL) — `routes_routes.py`
8. Bug 4 Part 4b (readiness condition 3) — `services/route_warehouse_readiness.py`

After applying everything, restart the app and run the per-bug testing checklists above against a staging database.
