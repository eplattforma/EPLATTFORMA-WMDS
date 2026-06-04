# Cooler Box Plan — Smart Recommender + Interactive Editor

Three files to update. No database changes needed.

---

## FILE 1 — Replace `services/cooler_box_planner.py` entirely

Replace the **entire file** with the version saved at:
`services/cooler_box_planner.py` in this project folder.

Key changes in this file:
- **Two-phase algorithm**: Phase 1 groups stops LIFO (consecutive stops only). Phase 2 right-sizes each box type to hit ≥ 80% fill.
- **Oversized stops** are split across max 2 sub-boxes automatically.
- **`available_type_counts`** parameter: `{type_id: max_count}` limits which box types/counts can be used.
- **`target_fill_pct`** parameter (default 0.80).
- Each item carries `has_dimensions`, `queue_status` in `item_summaries`.
- Each box dict carries `usable_capacity_cm3`, `max_weight_kg` (needed by JS editor).

---

## FILE 2 — Two edits to `blueprints/cooler_picking.py`

### Edit A — Replace `box_plan_preview` endpoint

Find:
```python
@cooler_bp.route("/route/<route_id>/<delivery_date>/box-plan", methods=["GET"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_plan_preview(route_id, delivery_date):
    box_type_id = request.args.get("box_type_id") or None
    result = generate_box_plan(route_id, delivery_date, box_type_id)
    if isinstance(result, dict) and not result.get("ok", True):
        return jsonify(result)
    plan = result if isinstance(result, list) else result.get("plan", [])
    if not plan:
        return jsonify({
            "ok": True,
            "plan": [],
            "message": "No picked unboxed cooler items found.",
        })
    return jsonify({"ok": True, "plan": plan})
```

Replace with:
```python
@cooler_bp.route("/route/<route_id>/<delivery_date>/box-plan", methods=["GET"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def box_plan_preview(route_id, delivery_date):
    """Return the recommended box plan plus all active box types for the UI."""
    box_type_id = request.args.get("box_type_id") or None
    target_fill = float(request.args.get("target_fill", "0.80"))

    # Parse availability: ?avail=typeId:count,typeId:count
    avail_raw = request.args.get("avail") or ""
    available_type_counts = None
    if avail_raw:
        try:
            available_type_counts = {}
            for part in avail_raw.split(","):
                tid, cnt = part.strip().split(":")
                available_type_counts[int(tid)] = int(cnt)
        except Exception:
            available_type_counts = None

    result = generate_box_plan(
        route_id, delivery_date,
        box_type_id=box_type_id,
        available_type_counts=available_type_counts,
        target_fill_pct=target_fill,
    )
    if isinstance(result, dict) and not result.get("ok", True):
        return jsonify(result)
    plan = result if isinstance(result, list) else result.get("plan", [])
    if not plan:
        return jsonify({
            "ok": True,
            "plan": [],
            "message": "No cooler items found to plan.",
        })

    from services.cooler_box_planner import _load_box_types
    box_types = _load_box_types()

    return jsonify({
        "ok": True,
        "plan": plan,
        "box_types": box_types,
        "target_fill_pct": target_fill,
    })
```

### Edit B — Update `confirm_box_plan` to accept user-edited plan JSON

Inside `confirm_box_plan`, find the lines that call `generate_box_plan`:

```python
    box_type_id = request.form.get("box_type_id") or None
    result = generate_box_plan(route_id_int, delivery_date, box_type_id)

    # Planner may return a dict with ok=False (e.g. missing delivery sequence)
    if isinstance(result, dict):
        if not result.get("ok", True):
            flash(result.get("message", "Cannot generate box plan."), "warning")
            return _redirect_back()
        plan = result.get("plan", [])
    else:
        plan = result

    if not plan:
        flash("No picked unboxed cooler items found.", "warning")
        return _redirect_back()
```

Replace with:
```python
    import json as _json
    plan_data_raw = request.form.get("plan_data") or ""
    plan = None

    if plan_data_raw:
        try:
            plan = _json.loads(plan_data_raw)
            if not isinstance(plan, list):
                plan = None
        except Exception:
            plan = None

    if plan is None:
        box_type_id = request.form.get("box_type_id") or None
        result = generate_box_plan(route_id_int, delivery_date, box_type_id)
        if isinstance(result, dict):
            if not result.get("ok", True):
                flash(result.get("message", "Cannot generate box plan."), "warning")
                return _redirect_back()
            plan = result.get("plan", [])
        else:
            plan = result

    if not plan:
        flash("No cooler items found to plan.", "warning")
        return _redirect_back()
```

---

## FILE 3 — `templates/cooler/route_picking.html`

The file has been fully rewritten and saved. Make sure Replit uses the saved version.

The key new features in this file:
- **Availability panel**: input boxes per box type (e.g., "Large: 3, Medium: 5, Small: 10"). Set to 0 to exclude a size. This feeds into the recommendation.
- **Min fill target selector**: 70% / 80% / 85% / 90%.
- **"Get Recommendation" button**: calls the server with availability + fill target, returns the optimised plan.
- **Interactive box cards**: each box shows fill %, warnings, item list.
- **Box type selector per card**: change a box type (e.g., Large → Medium). Items that no longer fit automatically cascade to the next box.
- **Move item (↔)**: each item row has a dropdown — select destination box and the item moves. If the destination is full, the earliest-delivery item in that box cascades to the next box.
- **Add Box / Remove Box buttons**.
- **"Re-recommend" button**: re-fetches a fresh recommendation while keeping the current availability settings.
- **Confirm Box Plan**: sends the full edited plan as JSON — no server re-generation, exactly what the manager built gets saved.

---

## How the new workflow works

```
1. Manager opens the cooler picking page
2. Sets availability: "today I have 2 Large, 4 Medium, 6 Small"
3. Clicks "Get Recommendation"
   → Server runs two-phase algorithm:
     Phase 1: group stops LIFO (last stop first, consecutive only)
     Phase 2: pick smallest box type where fill ≥ 80%
     Split any stop > largest box across 2 sub-boxes
4. Manager sees recommended boxes with fill % bars
5. Manager can:
   - Change a box from Large to 2x Medium (change type → cascade)
   - Move individual items between boxes (cascade if full)
   - Add/remove boxes
   - Change fill target and re-recommend
6. Clicks "Confirm Box Plan" → saves exactly what is shown
7. Pickers see "Pick → Box #N" on every item
```

No database schema changes required.
