# WMDS Fix — Priority 5: Fill & Capacity Recalculation

## Context (read first)

The `cooler_boxes` table has two stored fill columns:

- `fill_cm3` — `NUMERIC(12,2)`, total estimated item volume in the box
- `fill_weight_kg` — `NUMERIC(10,3)`, total estimated item weight in the box

**Important schema fact:** there is NO stored `fill_pct` column on `cooler_boxes`. The fill percentage is always derived at read time in SQL, e.g. in `route_picking` (blueprints/cooler_picking.py, ~line 772) and `get_cooler_route_status_data` (~line 3414):

```sql
CASE WHEN cbt.internal_volume_cm3 > 0 AND cbt.fill_efficiency > 0
     THEN ROUND(cb.fill_cm3 / (cbt.internal_volume_cm3 * cbt.fill_efficiency) * 100)
     ELSE NULL END AS fill_pct
```

This means: **recalculating `fill_cm3` and `fill_weight_kg` automatically fixes every fill % shown in the UI** — no separate `fill_pct` update is needed.

The bug: `fill_cm3` / `fill_weight_kg` are only ever written once, at plan time (`confirm_box_plan` and `pre_plan_boxes` insert planner estimates). They are NEVER updated after any item mutation (assign, remove, move, consolidate, cancel-then-reassign). The volume/weight KPIs on the route status report (`total_volume_l`, `total_weight_kg` at ~line 3556) and all displayed fill % values therefore operate on stale data. Boxes created manually via `box_create` never get a fill value at all.

Reference formulas (from `services/cooler_box_planner.py`, `generate_box_plan`, ~lines 213–281):

- Dimensions come from `ps_items_dw` with columns `item_code_365, item_length, item_width, item_height, item_weight` (cm and kg)
- Per-item volume: `item_length * item_width * item_height * qty` — `0` when any dimension is NULL
- Per-item weight: `item_weight * qty` — `0` when weight is NULL
- Box capacity: `cooler_box_types.internal_volume_cm3 * cooler_box_types.fill_efficiency`

`cooler_box_items` stores `item_code` (same code space as `ps_items_dw.item_code_365`), `expected_qty`, and `picked_qty` (NULL until physically picked).

All edits below are in **`blueprints/cooler_picking.py`** (the file is ~3750 lines).

---

## Step 1 — Add the shared `_recalculate_box_fill` helper

**File:** `blueprints/cooler_picking.py`, approx. line 340–350 (helpers section, right before the `# Routes` banner comment).

**FIND this exact block:**

```python
def _audit(activity_type, details, invoice_no=None, item_code=None):
    db.session.add(ActivityLog(
        picker_username=_username(),
        activity_type=activity_type,
        invoice_no=invoice_no,
        item_code=item_code,
        details=details,
    ))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
```

**REPLACE with:**

```python
def _audit(activity_type, details, invoice_no=None, item_code=None):
    db.session.add(ActivityLog(
        picker_username=_username(),
        activity_type=activity_type,
        invoice_no=invoice_no,
        item_code=item_code,
        details=details,
    ))


def _recalculate_box_fill(box_ids):
    """Recalculate fill_cm3 and fill_weight_kg for the given cooler box id(s)
    by re-summing the box's CURRENT cooler_box_items contents against
    ps_items_dw dimensions (same formula as services/cooler_box_planner.py:
    volume = item_length * item_width * item_height * qty, 0 when any
    dimension is NULL; weight = item_weight * qty, 0 when weight is NULL).

    Note: cooler_boxes has no stored fill_pct column — fill % is derived at
    read time as fill_cm3 / (cooler_box_types.internal_volume_cm3 *
    cooler_box_types.fill_efficiency), so refreshing fill_cm3 corrects every
    fill % shown in the UI automatically.

    Accepts a single int or an iterable of ints. Does NOT commit — the
    caller owns the transaction. Never raises: a failed recalc is logged
    and must not block the item mutation that triggered it.
    """
    if box_ids is None:
        return
    if isinstance(box_ids, int):
        box_ids = [box_ids]
    try:
        ids = {int(b) for b in box_ids if b is not None}
    except (TypeError, ValueError):
        return
    for bid in ids:
        try:
            row = db.session.execute(
                text(
                    "SELECT "
                    "  COALESCE(SUM("
                    "    CASE WHEN d.item_length IS NOT NULL "
                    "          AND d.item_width  IS NOT NULL "
                    "          AND d.item_height IS NOT NULL "
                    "         THEN d.item_length * d.item_width * d.item_height "
                    "              * COALESCE(cbi.picked_qty, cbi.expected_qty, 1) "
                    "         ELSE 0 END), 0) AS vol_cm3, "
                    "  COALESCE(SUM("
                    "    COALESCE(d.item_weight, 0) "
                    "    * COALESCE(cbi.picked_qty, cbi.expected_qty, 1)"
                    "  ), 0) AS weight_kg "
                    "FROM cooler_box_items cbi "
                    "LEFT JOIN ps_items_dw d ON d.item_code_365 = cbi.item_code "
                    "WHERE cbi.cooler_box_id = :bid"
                ),
                {"bid": bid},
            ).fetchone()
            new_vol = float(row[0] or 0.0) if row else 0.0
            new_wt = float(row[1] or 0.0) if row else 0.0
            db.session.execute(
                text(
                    "UPDATE cooler_boxes "
                    "SET fill_cm3 = :vol, fill_weight_kg = :wt "
                    "WHERE id = :bid"
                ),
                {"vol": new_vol, "wt": new_wt, "bid": bid},
            )
        except Exception as _fill_err:
            current_app.logger.warning(
                "_recalculate_box_fill failed for box %s: %s", bid, _fill_err
            )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
```

Notes on the helper:

- It empties to `0` correctly — a box with no remaining items gets `fill_cm3 = 0, fill_weight_kg = 0` (the `COALESCE(SUM(...), 0)` handles the no-rows case).
- Quantity uses `COALESCE(picked_qty, expected_qty, 1)`: planned (not yet picked) items have NULL `picked_qty`, so the planned quantity is used until pick time.
- `text` and `current_app` are already imported at the top of this file — no new imports are needed.

---

## Step 2 — Call `_recalculate_box_fill` after every item mutation

All five sub-steps below are in `blueprints/cooler_picking.py`. Each shows the exact current code just before `db.session.commit()` and the replacement. After Step 1 is applied, line numbers shift down by roughly 70 lines; the approximate locations below refer to the ORIGINAL file.

### 2a — `box_assign_item` (route `/box/<int:box_id>/assign-item`), approx. line 1983–1991

The target box is `box_id`.

**FIND:**

```python
    _audit(
        "cooler.item_assigned",
        f"Cooler box #{box_id} <- queue #{queue_item_id} "
        f"invoice={invoice_no} item={item_code} qty={picked_qty} by {_username()}",
        invoice_no=invoice_no, item_code=item_code,
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "picked"}), 200
```

(Beware: a very similar `_audit("cooler.item_assigned", ...)` block exists in `queue_assign_box` around line 2743, but that one is followed by `flash(...)` instead of `return jsonify(...)`. Use the one followed by `return jsonify(...)` here.)

**REPLACE with:**

```python
    _audit(
        "cooler.item_assigned",
        f"Cooler box #{box_id} <- queue #{queue_item_id} "
        f"invoice={invoice_no} item={item_code} qty={picked_qty} by {_username()}",
        invoice_no=invoice_no, item_code=item_code,
    )
    _recalculate_box_fill([box_id])
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "picked"}), 200
```

### 2b — `box_remove_item` (route `/box/<int:box_id>/remove-item`), approx. line 2034–2042

The affected box is `box_id` (the box the item was removed from).

**FIND:**

```python
    _audit(
        "cooler.item_removed",
        f"Cooler box #{box_id} -> unboxed queue #{queue_item_id} "
        f"invoice={cb_row[1]} item={cb_row[2]} (remains picked) by {_username()}",
        invoice_no=cb_row[1], item_code=cb_row[2],
    )
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "picked"}), 200
```

**REPLACE with:**

```python
    _audit(
        "cooler.item_removed",
        f"Cooler box #{box_id} -> unboxed queue #{queue_item_id} "
        f"invoice={cb_row[1]} item={cb_row[2]} (remains picked) by {_username()}",
        invoice_no=cb_row[1], item_code=cb_row[2],
    )
    _recalculate_box_fill([box_id])
    db.session.commit()
    return jsonify({"cooler_box_id": box_id, "queue_item_id": queue_item_id,
                    "status": "picked"}), 200
```

### 2c — `move_box_item` (route `/box-item/<int:cbi_id>/move-to-box`), approx. line 2124–2135

Single-item move by `cooler_box_items.id`. Both `source_box_id` and `dest_box_id` are affected. (This function already re-stamps `first_stop_sequence` / `last_stop_sequence` for both boxes just above this block — fill must be re-stamped the same way.)

**FIND:**

```python
    _audit(
        "cooler.item_moved",
        f"cooler_box_items #{cbi_id} moved from box #{source_box_id} "
        f"to box #{dest_box_id} ({cbi[4]}) by {_username()}",
        invoice_no=cbi[2], item_code=cbi[3],
    )
    db.session.commit()
    return jsonify({
        "cbi_id": cbi_id,
        "from_box_id": source_box_id,
        "to_box_id": dest_box_id,
    }), 200
```

**REPLACE with:**

```python
    _audit(
        "cooler.item_moved",
        f"cooler_box_items #{cbi_id} moved from box #{source_box_id} "
        f"to box #{dest_box_id} ({cbi[4]}) by {_username()}",
        invoice_no=cbi[2], item_code=cbi[3],
    )
    _recalculate_box_fill([source_box_id, dest_box_id])
    db.session.commit()
    return jsonify({
        "cbi_id": cbi_id,
        "from_box_id": source_box_id,
        "to_box_id": dest_box_id,
    }), 200
```

### 2d — `box_move_item` (route `/box/<int:from_box_id>/move-item`), approx. line 2838–2844

This is the SECOND single-item-move endpoint (keyed by `queue_item_id` instead of `cbi_id`) — it must be fixed too. Both `from_box_id` and `to_box_id` are affected.

**FIND:**

```python
    _audit(
        "cooler.item_moved",
        f"Queue #{queue_item_id} invoice={src_row[1]} item={src_row[2]} "
        f"moved from box #{from_box_id} → box #{to_box_id} by {_username()}",
        invoice_no=src_row[1], item_code=src_row[2],
    )
    db.session.commit()
```

**REPLACE with:**

```python
    _audit(
        "cooler.item_moved",
        f"Queue #{queue_item_id} invoice={src_row[1]} item={src_row[2]} "
        f"moved from box #{from_box_id} → box #{to_box_id} by {_username()}",
        invoice_no=src_row[1], item_code=src_row[2],
    )
    _recalculate_box_fill([from_box_id, to_box_id])
    db.session.commit()
```

### 2e — `box_move_all_to` (route `/box/<int:source_box_id>/move_all_to/<int:dest_box_id>`), approx. line 2936–2940

Box consolidation — both `source_box_id` (now empty or near-empty) and `dest_box_id` are affected.

**FIND:**

```python
    _audit(
        "cooler.box_consolidation",
        f"Consolidated {moved} item(s) from box #{source_box_id} → box #{dest_box_id} by {_username()}",
    )
    db.session.commit()
```

**REPLACE with:**

```python
    _audit(
        "cooler.box_consolidation",
        f"Consolidated {moved} item(s) from box #{source_box_id} → box #{dest_box_id} by {_username()}",
    )
    _recalculate_box_fill([source_box_id, dest_box_id])
    db.session.commit()
```

### 2f — `queue_assign_box` (route `/queue/<int:queue_item_id>/assign-box`), approx. line 2743–2751

Picker assigns a picked queue item to a box from the picking screen. The affected box is `box_id`.

**FIND:**

```python
    _audit(
        "cooler.item_assigned",
        f"Cooler box #{box_id} <- queue #{queue_item_id} "
        f"invoice={invoice_no} item={item_code} qty={picked_qty} by {_username()}",
        invoice_no=invoice_no, item_code=item_code,
    )
    db.session.commit()
    flash(f"Assigned {item_code} to Box #{box['box_no']}.", "success")
    return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)
```

(This is the `_audit("cooler.item_assigned", ...)` block followed by `flash(...)` — distinct from 2a, which is followed by `return jsonify(...)`.)

**REPLACE with:**

```python
    _audit(
        "cooler.item_assigned",
        f"Cooler box #{box_id} <- queue #{queue_item_id} "
        f"invoice={invoice_no} item={item_code} qty={picked_qty} by {_username()}",
        invoice_no=invoice_no, item_code=item_code,
    )
    _recalculate_box_fill([box_id])
    db.session.commit()
    flash(f"Assigned {item_code} to Box #{box['box_no']}.", "success")
    return _redirect_to_picking_from_queue(queue_item_id, delivery_date_str)
```

---

## Bug 2 — `confirm_box_plan` keeps planner-estimated fill even when rows are skipped

**File:** `blueprints/cooler_picking.py`, function `confirm_box_plan` (route `/route/<route_id>/<delivery_date>/confirm-box-plan`), approx. lines 1416–1637.

Current behaviour: each box is INSERTed with `fill_cm3 = box["estimated_fill_cm3"]` and `fill_weight_kg = box["estimated_weight_kg"]` from the planner (the INSERT at ~line 1473). Items are then inserted one by one, and any item that no longer exists, has a non-plannable status, or is already boxed is **skipped** — but the box keeps the full planner-estimated fill. The existing "recalculate box header fields" block (~line 1586) only corrects `first_stop_sequence` / `last_stop_sequence`, NOT fill.

Two edits inside `confirm_box_plan`:

### Edit 1 — track confirmed box ids (approx. line 1467–1469)

**FIND:**

```python
    now = get_utc_now()
    created = 0
    skipped = 0
```

(Beware: the nearby `pre_plan_boxes` function uses `created_boxes = 0` / `skipped_items = 0`, so this snippet is unique to `confirm_box_plan`.)

**REPLACE with:**

```python
    now = get_utc_now()
    created = 0
    skipped = 0
    confirmed_box_ids = []
```

### Edit 2 — recalculate fill from actual contents before committing (approx. line 1586–1610)

**FIND:**

```python
            else:
                # Some items were actually inserted — recalculate box header
                # fields based only on what was really placed inside, not the
                # original planner estimates (which may include skipped items).
                recalc = db.session.execute(
                    text(
                        "SELECT MIN(delivery_sequence), MAX(delivery_sequence) "
                        "FROM cooler_box_items "
                        "WHERE cooler_box_id = :bid"
                    ),
                    {"bid": box_id},
                ).fetchone()
                actual_first = recalc[0] if recalc else None
                actual_last = recalc[1] if recalc else None
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, last_stop_sequence = :ls "
                        "WHERE id = :bid"
                    ),
                    {"fs": actual_first, "ls": actual_last, "bid": box_id},
                )
                created += 1

        db.session.commit()
```

**REPLACE with:**

```python
            else:
                # Some items were actually inserted — recalculate box header
                # fields based only on what was really placed inside, not the
                # original planner estimates (which may include skipped items).
                recalc = db.session.execute(
                    text(
                        "SELECT MIN(delivery_sequence), MAX(delivery_sequence) "
                        "FROM cooler_box_items "
                        "WHERE cooler_box_id = :bid"
                    ),
                    {"bid": box_id},
                ).fetchone()
                actual_first = recalc[0] if recalc else None
                actual_last = recalc[1] if recalc else None
                db.session.execute(
                    text(
                        "UPDATE cooler_boxes "
                        "SET first_stop_sequence = :fs, last_stop_sequence = :ls "
                        "WHERE id = :bid"
                    ),
                    {"fs": actual_first, "ls": actual_last, "bid": box_id},
                )
                created += 1
                confirmed_box_ids.append(box_id)

        # Replace planner-estimated fill with fill computed from the items
        # that were ACTUALLY inserted (skipped items no longer inflate it).
        _recalculate_box_fill(confirmed_box_ids)

        db.session.commit()
```

### Bug 2b (same defect, second site) — `pre_plan_boxes` (approx. lines 1640–1790)

`pre_plan_boxes` has the identical pattern: boxes are INSERTed with `fill_cm3 = box["estimated_fill_cm3"]`, then items are individually skipped (`skipped_items += 1`) with no fill correction. Apply the same treatment.

**FIND (approx. line 1697–1699):**

```python
    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0
```

**REPLACE with:**

```python
    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0
    preplan_box_ids = []
```

**FIND (approx. line 1722–1724, inside the `for idx, box in enumerate(plan, start=1):` loop):**

```python
            box_id = result_row[0]
            created_boxes += 1
```

(This exact two-line pairing appears only in `pre_plan_boxes`; in `confirm_box_plan` the line after `box_id = result_row[0]` is `items_inserted = 0`.)

**REPLACE with:**

```python
            box_id = result_row[0]
            created_boxes += 1
            preplan_box_ids.append(box_id)
```

**FIND (approx. line 1774, end of the try block in `pre_plan_boxes`):**

```python
        db.session.commit()
        _audit(
            "cooler.pre_plan",
            f"Pre-planned {created_boxes} box(es) for route {route_id} "
            f"date={delivery_date} — {skipped_items} item(s) skipped",
        )
```

**REPLACE with:**

```python
        _recalculate_box_fill(preplan_box_ids)
        db.session.commit()
        _audit(
            "cooler.pre_plan",
            f"Pre-planned {created_boxes} box(es) for route {route_id} "
            f"date={delivery_date} — {skipped_items} item(s) skipped",
        )
```

---

## Testing checklist

Setup: enable the `cooler_picking_enabled` setting, use a route with SENSITIVE (cooler) items where at least some item codes have dimensions in `ps_items_dw` (`item_length/item_width/item_height/item_weight` on `item_code_365`) and at least one item code has NULL dimensions.

1. **Confirm box plan (Bug 2):** generate and confirm a box plan where at least one item gets skipped (e.g. pre-assign one queue item to a box manually first so the confirm-time "already boxed" guard skips it). After confirmation, check `SELECT id, fill_cm3, fill_weight_kg FROM cooler_boxes` — the box that lost the skipped item must show fill LOWER than the planner estimate, matching the sum over its actual `cooler_box_items`.
2. **Pre-plan (Bug 2b):** run pre-plan on a fresh route where some items get skipped; verify fill matches actual inserted contents.
3. **Assign (2a / 2f):** assign a picked queue item to an open box via the picking screen form (`queue_assign_box`) and via the JSON endpoint (`box_assign_item`). The box's `fill_cm3`/`fill_weight_kg` must increase by `l*w*h*qty` / `weight*qty` for that item, and the fill % badge on the packing screen must update after page reload.
4. **Remove (2b):** remove that item from the box; fill must drop back to the previous value. Remove the LAST item from a box; fill must go to exactly 0 (not stay stale).
5. **Single move, both endpoints (2c / 2d):** move an item between two open boxes using `/box-item/<cbi_id>/move-to-box` and again (another item) using `/box/<from_box_id>/move-item`. Source box fill decreases, destination box fill increases, totals conserved.
6. **Consolidation (2e):** use "move all" from box A to box B. Box A ends at `fill_cm3 = 0, fill_weight_kg = 0`; box B equals the sum of both previous fills (for items with dimensions).
7. **NULL dimensions:** assign an item whose code has no `ps_items_dw` row (or NULL length/width/height). The operation must succeed and contribute 0 volume; if `item_weight` exists it still contributes weight.
8. **Planned vs picked qty:** confirm a plan that includes a pending (not yet picked) item — fill must use `expected_qty`. Then pick it via the queue (which sets `picked_qty`) and trigger any mutation on that box; fill should now reflect `picked_qty`.
9. **Derived fill %:** on the route packing screen and on the Route Status print report, verify the fill % values and the `total_volume_l` / `total_weight_kg` KPIs change after each mutation above (these read `cb.fill_cm3` / `cb.fill_weight_kg` directly).
10. **No commit side effects:** verify a failed mutation (e.g. assigning a duplicate queue item, which returns HTTP 409 and rolls back) leaves fill values unchanged.
11. **Regression:** close a box, reopen it, print a label/manifest — all still work (none of these paths were modified).
