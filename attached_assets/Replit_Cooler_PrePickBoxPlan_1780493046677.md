# EP SmartGrowth — Cooler Pre-Pick Box Planning (Pick-into-Box Workflow)

## Overview

Changes the cooler workflow so boxes are planned and physically set up **before**
picking starts. The picker knows which box each item goes into while picking and
places it directly. A manager closes each box when done — no separate assignment
step.

**New workflow:**
```
1. Sequencing locked (existing prerequisite)
        ↓
2. Manager clicks "Pre-Plan Boxes" on Route List
   → Boxes created, items pre-assigned (status = planned)
   → Physical boxes are labelled and placed on picker's trolley
        ↓
3. Picker opens picking screen
   → Each cooler item shows "→ Box N" badge
   → Picker picks item, places it into designated box
        ↓
4. When route is picked, manager clicks "Close" per box (existing)
   → Any exception items show as "not placed" on the box panel
   → Manager removes them if needed (existing move/remove routes)
```

**Good news:** Most infrastructure already exists.
- `confirm_box_plan` already creates `cooler_box_items` with `status='planned'`
  for pending items
- The picking screen already builds `assigned_to_box` (queue_item_id → box_no)
  and shows "→ Box #N" — it just needs to be more prominent
- Box close, move, and remove routes are unchanged

---

## CHANGE 1 — `services/cooler_box_planner.py` — include pending items

The planner currently fetches only `status = 'picked'` items. Add an
`include_pending` parameter so pre-planning can run on unstarted routes.

Find the SQL query inside `generate_box_plan` that contains:

```python
"  AND bpq.status = 'picked' "
```

**Replace with:**

```python
"  AND bpq.status IN ('picked', 'pending') " if include_pending else "  AND bpq.status = 'picked' "
```

And update the function signature from:

```python
def generate_box_plan(route_id, delivery_date, box_type_id=None):
```

**To:**

```python
def generate_box_plan(route_id, delivery_date, box_type_id=None, include_pending=False):
```

> When `include_pending=True`, the planner uses `COALESCE(bpq.qty_picked, bpq.qty_required, 1)`
> which already falls back to expected quantity for unstarted items — no other change needed.

---

## CHANGE 2 — `blueprints/cooler_picking.py` — new pre-plan route

Add a new route that triggers box pre-planning from the route list page.
Place it near the existing `confirm_box_plan` route.

```python
@cooler_bp.route("/route/<route_id>/<delivery_date>/pre-plan", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def pre_plan_boxes(route_id, delivery_date):
    """Pre-plan cooler boxes before picking starts.

    Creates cooler_boxes + cooler_box_items for ALL cooler items on the route
    (pending and picked). Items get status='planned' in cooler_box_items.
    The picker sees box assignments on the picking screen during picking.
    """
    try:
        route_id_int = int(route_id)
    except (TypeError, ValueError):
        flash("Invalid route ID.", "danger")
        return redirect(url_for("cooler.route_list"))

    # Block if boxes already exist for this route
    existing_boxes = db.session.execute(
        text("SELECT COUNT(*) FROM cooler_boxes WHERE route_id = :rid AND delivery_date = :dd"),
        {"rid": route_id_int, "dd": str(delivery_date)},
    ).scalar() or 0

    if existing_boxes > 0:
        flash(
            f"Boxes are already planned for this route ({existing_boxes} box(es) exist). "
            "Open the packing screen to review or re-plan.",
            "warning",
        )
        return redirect(url_for("cooler.route_list"))

    box_type_id = request.form.get("box_type_id") or None

    from services.cooler_box_planner import generate_box_plan
    result = generate_box_plan(
        route_id_int, delivery_date,
        box_type_id=box_type_id,
        include_pending=True,        # ← key: plan against expected quantities
    )

    if isinstance(result, dict) and not result.get("ok", True):
        flash(result.get("message", "Cannot generate box plan."), "warning")
        return redirect(url_for("cooler.route_list"))

    plan = result if isinstance(result, list) else result.get("plan", [])

    if not plan:
        flash("No cooler items found to plan. Make sure sequencing is locked first.", "warning")
        return redirect(url_for("cooler.route_list"))

    now = get_utc_now()
    created_boxes = 0
    skipped_items = 0

    try:
        for idx, box in enumerate(plan, start=1):
            result_row = db.session.execute(
                text(
                    "INSERT INTO cooler_boxes "
                    "(route_id, delivery_date, box_no, status, first_stop_sequence, "
                    " last_stop_sequence, created_by, created_at, box_type_id, "
                    " fill_cm3, fill_weight_kg) "
                    "VALUES (:rid, :dd, :box_no, 'open', :fs, :ls, :who, :now, "
                    "        :btid, :fill, :weight) "
                    "RETURNING id"
                ),
                {
                    "rid": route_id_int, "dd": str(delivery_date),
                    "box_no": idx,
                    "fs": box["stop_min"], "ls": box["stop_max"],
                    "who": _username(), "now": now,
                    "btid": box["box_type_id"],
                    "fill": box["estimated_fill_cm3"],
                    "weight": box["estimated_weight_kg"],
                },
            ).fetchone()
            box_id = result_row[0]
            created_boxes += 1

            for item in box["item_summaries"]:
                qid = item["queue_item_id"]
                qcheck = db.session.execute(
                    text(
                        "SELECT bpq.status, "
                        "       (SELECT COUNT(*) FROM cooler_box_items cbi "
                        "        WHERE cbi.queue_item_id = bpq.id) AS already_boxed "
                        "FROM batch_pick_queue bpq WHERE bpq.id = :qid"
                    ),
                    {"qid": qid},
                ).fetchone()
                if qcheck is None or qcheck[1] > 0:
                    skipped_items += 1
                    continue
                if qcheck[0] not in ("picked", "pending"):
                    skipped_items += 1
                    continue

                item_status = qcheck[0]
                db.session.execute(
                    text(
                        "INSERT INTO cooler_box_items "
                        "(cooler_box_id, invoice_no, customer_code, customer_name, "
                        " route_stop_id, delivery_sequence, item_code, item_name, "
                        " expected_qty, picked_qty, picked_by, picked_at, "
                        " queue_item_id, status, created_at, updated_at) "
                        "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
                        "        :exp, :pq, :who, :now, :qid, :status, :ts, :ts)"
                    ),
                    {
                        "bid": box_id,
                        "inv": item["invoice_no"],
                        "cc": item["customer_code"],
                        "cn": item["customer_name"],
                        "rsid": item["route_stop_id"],
                        "seq": item["delivery_sequence"],
                        "ic": item["item_code"],
                        "iname": item["item_name"],
                        "exp": item["qty"],
                        "pq": item["qty"] if item_status == "picked" else None,
                        "who": _username() if item_status == "picked" else None,
                        "now": now if item_status == "picked" else None,
                        "qid": qid,
                        "status": item_status,
                        "ts": now,
                    },
                )

        db.session.commit()
        _audit("cooler.pre_plan",
               f"Pre-planned {created_boxes} box(es) for route {route_id} "
               f"date={delivery_date} — {skipped_items} item(s) skipped")
        flash(
            f"✓ {created_boxes} box(es) pre-planned. "
            "Label and place them on the picker's trolley, then start picking.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        logger.exception("pre_plan_boxes failed for route %s", route_id)
        flash(f"Pre-planning failed: {e}", "danger")

    return redirect(url_for("cooler.route_list"))
```

---

## CHANGE 3 — `templates/cooler/route_list.html` — Pre-Plan button

Find the route card's action area. It currently has just the "Open Packing Screen"
button. Add a Pre-Plan section **inside** each route card, below the status counters.

Find the block that ends with `Open Packing Screen` and add after it:

```html
{# ── Pre-plan boxes ──────────────────────────────────────────────── #}
{% set preplanned = namespace(count=0) %}
{# Check if boxes already exist — passed from route context #}
{% if route.box_count and route.box_count > 0 %}
  <div class="alert alert-success py-2 px-3 mb-2 d-flex align-items-center gap-2">
    <i class="fas fa-check-circle text-success"></i>
    <span class="small">
      <strong>{{ route.box_count }} box(es) pre-planned.</strong>
      Picker can see box assignments on the picking screen.
    </span>
  </div>
{% else %}
  <div class="border rounded p-3 mb-2" style="background:#f0fff4;">
    <div class="fw-semibold mb-2 text-success">
      <i class="fas fa-box me-1"></i>Pre-Plan Cooler Boxes
    </div>
    <p class="small text-muted mb-2">
      Plan boxes before picking starts. The picker will see which box
      each item goes into on the picking screen.
    </p>
    <form method="POST"
          action="{{ url_for('cooler.pre_plan_boxes', route_id=route.route_id, delivery_date=route.delivery_date) }}">
      <div class="d-flex align-items-center gap-2 flex-wrap">
        <select name="box_type_id" class="form-select form-select-sm" style="max-width:220px;">
          <option value="">— auto: best fit combination —</option>
          {% for bt in box_types %}
          <option value="{{ bt.id }}">{{ bt.name }} ({{ bt.effective_capacity_l }}L usable)</option>
          {% endfor %}
        </select>
        <button type="submit" class="btn btn-success btn-sm">
          <i class="fas fa-magic me-1"></i>Pre-Plan Boxes
        </button>
      </div>
    </form>
  </div>
{% endif %}
```

### Pass `box_count` and `box_types` from the route

**File:** `blueprints/cooler_picking.py`, in the `route_list` view.

After building the `routes` list, add:

```python
# Box type options for pre-plan dropdown
box_types = db.session.execute(text(
    "SELECT id, name, internal_volume_cm3, fill_efficiency, "
    "       ROUND((internal_volume_cm3 * fill_efficiency / 1000)::numeric, 1) AS effective_capacity_l "
    "FROM cooler_box_types WHERE is_active = true ORDER BY sort_order, name"
)).fetchall()
box_types = [{"id": r[0], "name": r[1], "effective_capacity_l": r[4]} for r in box_types]

# Count existing boxes per route so template can show "pre-planned" badge
box_counts = {}
if routes:
    bc_rows = db.session.execute(text(
        "SELECT route_id, delivery_date::text, COUNT(*) "
        "FROM cooler_boxes GROUP BY route_id, delivery_date"
    )).fetchall()
    for r in bc_rows:
        box_counts[(r[0], r[1])] = r[2]

for route in routes:
    route["box_count"] = box_counts.get(
        (route["route_id"], str(route["delivery_date"])), 0
    )
```

Pass to template:
```python
return render_template("cooler/route_list.html",
                       routes=routes, estimates=estimates,
                       box_types=box_types)
```

---

## CHANGE 4 — `templates/cooler/route_picking.html` — prominent box badge

The picking screen already builds `assigned_to_box` and uses it to hide the
assign form. Upgrade the box assignment display so it's visually prominent
during picking.

Find where the picking screen renders each queue item row. Look for where
`assigned_to_box` is used — it currently shows something like "→ Box #N".
Replace with a more prominent badge:

```html
{% if item.queue_item_id in assigned_to_box %}
<span class="badge bg-primary fs-6 px-3 py-2">
  <i class="fas fa-box me-1"></i>Box {{ assigned_to_box[item.queue_item_id] }}
</span>
{% else %}
{# Show assign form only if not pre-planned #}
... existing assign form ...
{% endif %}
```

> The badge should be large enough to read at a glance from the trolley.
> `fs-6` (Bootstrap font-size-6) gives a reasonable size; adjust to taste.

---

## CHANGE 5 — Box panel: show planned-but-not-picked items at close time

In the box detail panel on the picking screen, add a visual flag for items
that were pre-planned but not yet picked (status = 'pending' in cooler_box_items).

Find where box items are listed in the box panel. For each item, add:

```html
{% if item.status == 'pending' %}
  <span class="badge bg-warning text-dark ms-1">Not yet picked</span>
{% elif item.status == 'exception' or item.picked_qty == 0 %}
  <span class="badge bg-danger ms-1">Exception — not placed</span>
{% endif %}
```

This lets the manager see at a glance which items didn't make it into a box
before clicking Close.

---

## What does NOT change

- Box close route — unchanged
- Move item between boxes — unchanged (existing route)
- Remove item from box — unchanged (existing route)
- `_is_cooler_route_pack_complete` — unchanged (pre-planned items satisfy
  condition 3 "every picked row has a cooler_box_items entry" automatically)
- Label and manifest printing — unchanged

---

## Sequencing dependency

Pre-planning requires delivery sequence to be set (`delivery_sequence IS NOT NULL`).
The `generate_box_plan` function already guards this and returns an error message.
The Pre-Plan button should only be enabled (or show a tooltip) after sequencing
is locked. Consider checking `route.pending > 0` vs `route.total` to decide
whether to show the button or a "Sequence not locked" note.

---

## Rollback / re-plan

If a manager pre-plans and then needs to change the plan (e.g. a customer is
removed from the route), add a simple "Cancel Pre-Plan" button that deletes
all open boxes for the route:

```python
@cooler_bp.route("/route/<route_id>/<delivery_date>/cancel-preplan", methods=["POST"])
@login_required
@_require_cooler_manage
def cancel_pre_plan(route_id, delivery_date):
    """Remove all open/planned boxes so a fresh pre-plan can be generated."""
    db.session.execute(text(
        "DELETE FROM cooler_box_items WHERE cooler_box_id IN "
        "(SELECT id FROM cooler_boxes WHERE route_id = :rid AND delivery_date = :dd "
        " AND status = 'open')"
    ), {"rid": int(route_id), "dd": str(delivery_date)})
    db.session.execute(text(
        "DELETE FROM cooler_boxes WHERE route_id = :rid AND delivery_date = :dd "
        "AND status = 'open'"
    ), {"rid": int(route_id), "dd": str(delivery_date)})
    db.session.commit()
    flash("Pre-plan cancelled. You can now generate a new plan.", "info")
    return redirect(url_for("cooler.route_list"))
```
