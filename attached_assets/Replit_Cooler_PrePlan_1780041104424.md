# Cooler Boxes — Pre-Planning + LIFO Picking

## What this implements

| Feature | Detail |
|---------|--------|
| Plan boxes before picking | Supervisor auto-assigns all pending items to boxes before any physical picking starts |
| Pick box by box | Picking screen shows one box at a time, not a flat route list |
| LIFO order | Last delivery stop picked first — items stack correctly in the box |
| Move items between boxes | Single action, works for both planned and picked items, any time a box is open |

---

## Overview of all changes

| # | File | What changes |
|---|------|-------------|
| 1 | `update_cooler_schema.py` (new) | Add `planned` status to `cooler_box_items` |
| 2 | `services/cooler_box_planner.py` | `generate_box_plan` includes `pending` items, not just `picked` |
| 3 | `blueprints/cooler_picking.py` | `confirm_box_plan` — allow pre-planning |
| 4 | `blueprints/cooler_picking.py` | `queue_pick` — upgrade `planned` row to `picked` on physical pick |
| 5 | `blueprints/cooler_picking.py` | `_is_cooler_route_pack_complete` — `planned` = not done |
| 6 | `blueprints/cooler_picking.py` | `box_remove_item` — allow removing planned items |
| 7 | `blueprints/cooler_picking.py` | `route_picking` view — pass LIFO data + item statuses |
| 8 | `blueprints/cooler_picking.py` | New route `POST /cooler/box-item/<cbi_id>/move-to-box` |
| 9 | `templates/cooler/route_picking.html` | Plan button, box-grouped LIFO view, move button |
| 10 | `main.py` | Call schema update on startup |

---

## CHANGE 1 — New file `update_cooler_schema.py`

Create this file in the project root:

```python
"""
Adds 'planned' as a valid status in cooler_box_items.
Also adds queue_item_id index if missing.
Run once on startup via main.py.
"""
import logging
logger = logging.getLogger(__name__)

def update_cooler_schema():
    try:
        from app import db
        from sqlalchemy import text

        # Extend any CHECK constraint on cooler_box_items.status to include 'planned'.
        # PostgreSQL: drop the old constraint and add a new one.
        # If no constraint exists this is a no-op.
        try:
            db.session.execute(text(
                "ALTER TABLE cooler_box_items "
                "DROP CONSTRAINT IF EXISTS cooler_box_items_status_check"
            ))
            db.session.execute(text(
                "ALTER TABLE cooler_box_items "
                "ADD CONSTRAINT cooler_box_items_status_check "
                "CHECK (status IN ('planned', 'picked', 'exception'))"
            ))
            db.session.commit()
            logger.info("cooler_box_items status constraint updated to include 'planned'")
        except Exception as e:
            db.session.rollback()
            logger.warning("Could not update cooler_box_items constraint (may not exist): %s", e)

        # Add cbi_id column to cooler_box_items if it doesn't already have a
        # surrogate PK (the move-to-box route needs to address rows by id).
        try:
            db.session.execute(text(
                "ALTER TABLE cooler_box_items "
                "ADD COLUMN IF NOT EXISTS id SERIAL"
            ))
            db.session.commit()
            logger.info("cooler_box_items.id column ensured")
        except Exception as e:
            db.session.rollback()
            logger.warning("cooler_box_items id column: %s", e)

        logger.info("update_cooler_schema complete")
    except Exception as e:
        logger.error("update_cooler_schema failed: %s", e)
```

---

## CHANGE 2 — Register schema update in `main.py`

Find the block in `main.py` where other schema updates are called (near `update_supplier_returns_stock_cache_schema`, etc.) and add:

```python
try:
    from update_cooler_schema import update_cooler_schema
    update_cooler_schema()
except Exception as e:
    logging.error(f"Error updating cooler schema: {e}")
```

---

## CHANGE 3 — `services/cooler_box_planner.py` — include pending items in plan

Find `generate_box_plan`. Inside it there will be a query that fetches items from `batch_pick_queue` with a filter like:

```python
AND bpq.status = 'picked'
```

or

```python
"status": "picked"
```

**Change this filter to include both `pending` and `picked`:**

```python
AND bpq.status IN ('pending', 'picked')
```

This allows the planner to suggest box assignments for items that haven't been physically picked yet.

> **Important:** also check whether `generate_box_plan` filters out items already in `cooler_box_items`. It should — keep that filter. We only want to plan items that are not yet assigned to any box.

---

## CHANGE 4 — `blueprints/cooler_picking.py` — `confirm_box_plan`

### Step 4a — Allow pending items through the pre-flight check

Find inside `confirm_box_plan` this block:

```python
                if qcheck[0] != "picked":
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} status={qcheck[0]} (not picked) — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue
```

**Replace with:**

```python
                if qcheck[0] not in ("picked", "pending"):
                    _audit("cooler.confirm_plan_skip",
                           f"queue #{qid} status={qcheck[0]} (not plannable) — skipped",
                           invoice_no=item.get("invoice_no"))
                    skipped += 1
                    continue
```

### Step 4b — Insert planned items with correct status

Find the `INSERT INTO cooler_box_items` statement inside `confirm_box_plan`. It currently inserts with `status = 'picked'` and sets `picked_qty`, `picked_by`, `picked_at`. 

**Replace the entire INSERT block with one that handles both cases:**

```python
                # Items already picked keep status='picked'.
                # Items not yet picked are pre-assigned as status='planned'.
                item_status = qcheck[0]   # 'picked' or 'pending'
                _now_or_none = now if item_status == "picked" else None
                _who_or_none = _username() if item_status == "picked" else None
                _qty = item["qty"] if item_status == "picked" else None

                db.session.execute(
                    text(
                        "INSERT INTO cooler_box_items "
                        "(cooler_box_id, invoice_no, customer_code, customer_name, "
                        " route_stop_id, delivery_sequence, item_code, item_name, "
                        " expected_qty, picked_qty, picked_by, picked_at, "
                        " queue_item_id, status, created_at, updated_at) "
                        "VALUES (:bid, :inv, :cc, :cn, :rsid, :seq, :ic, :iname, "
                        "        :exp, :pq, :who, :now, :qid, :status, :created, :created)"
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
                        "pq": _qty,
                        "who": _who_or_none,
                        "now": _now_or_none,
                        "qid": qid,
                        "status": item_status,
                        "created": now,
                    },
                )
```

### Step 4c — Update the `picked_unboxed_count` query in `route_picking`

Find in `route_picking`:

```python
    picked_unboxed_count = db.session.execute(
        text(
            "SELECT COUNT(*) FROM batch_pick_queue bpq "
            ...
            "  AND bpq.status = 'picked' "
            ...
        ),
```

**Change `bpq.status = 'picked'` to `bpq.status IN ('picked', 'pending')`** so the "Plan Boxes" button shows when there are unplanned items of either status.

---

## CHANGE 5 — `queue_pick` — upgrade planned row to picked on physical pick

Find `queue_pick`. After the block that updates `batch_pick_queue` to `status = 'picked'` (the `UPDATE batch_pick_queue SET status = 'picked' ...` call), add this block **before** `db.session.commit()`:

```python
        # If this item was pre-assigned to a cooler box as 'planned',
        # upgrade it to 'picked' now that it has been physically collected.
        try:
            db.session.execute(
                text(
                    "UPDATE cooler_box_items "
                    "SET status = 'picked', "
                    "    picked_qty = qty_required_val, "
                    "    picked_by  = :who, "
                    "    picked_at  = :now, "
                    "    updated_at = :now "
                    "FROM (SELECT qty_required FROM batch_pick_queue WHERE id = :qid) AS q(qty_required_val) "
                    "WHERE cooler_box_items.queue_item_id = :qid "
                    "  AND cooler_box_items.status = 'planned'"
                ),
                {"qid": queue_item_id, "who": _username(), "now": now},
            )
        except Exception as _upgrade_err:
            current_app.logger.warning(
                "cooler.queue_pick: could not upgrade planned box row for queue %s: %s",
                queue_item_id, _upgrade_err,
            )
```

> **Note:** The subquery `FROM (...) AS q` is PostgreSQL syntax. If needed simplify: fetch `qty_required` first with a separate SELECT, then use the value directly in the UPDATE.

---

## CHANGE 6 — `_is_cooler_route_pack_complete` — planned items block completion

Find the check for pending items (condition 2). After it, add a check for planned items:

```python
    # 2b. Planned rows (pre-assigned but not yet physically picked)
    planned = db.session.execute(
        text(
            "SELECT COUNT(*) FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cb.route_id = :rid AND cb.delivery_date = :dd "
            "  AND cbi.status = 'planned'"
        ),
        {"rid": rid, "dd": dd},
    ).scalar() or 0
    if planned > 0:
        return False
```

---

## CHANGE 7 — `box_remove_item` — allow removing planned items

Find in `box_remove_item` the check that reads:

```python
    if box["status"] != "open":
        return jsonify({...}), 400
```

This is fine — keep it. But also find if there's any check that restricts removal to `status = 'picked'` items only. If there is, remove that restriction so `planned` items can also be removed/moved.

---

## CHANGE 8 — New route `POST /cooler/box-item/<int:cbi_id>/move-to-box`

Add this new route anywhere after `box_remove_item` in `blueprints/cooler_picking.py`:

```python
@cooler_bp.route("/box-item/<int:cbi_id>/move-to-box", methods=["POST"])
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_manage
@_require_picking_flag
def move_box_item(cbi_id):
    """Move a cooler_box_items row from its current box to a different open box.

    Works for both 'planned' and 'picked' items.
    Both the source and destination boxes must be open.
    """
    data = request.get_json(silent=True) or request.form
    try:
        dest_box_id = int(data.get("destination_box_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "destination_box_id is required and must be int"}), 400

    # Fetch the item row
    cbi = db.session.execute(
        text(
            "SELECT cbi.id, cbi.cooler_box_id, cbi.invoice_no, cbi.item_code, "
            "       cbi.status, cb.route_id, cb.delivery_date, cb.status AS box_status "
            "FROM cooler_box_items cbi "
            "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
            "WHERE cbi.id = :id"
        ),
        {"id": cbi_id},
    ).fetchone()
    if cbi is None:
        return jsonify({"error": "Item not found"}), 404
    if cbi[7] != "open":
        return jsonify({"error": f"Source box is {cbi[7]}; can only move items from open boxes."}), 400

    # Fetch destination box
    dest = db.session.execute(
        text(
            "SELECT id, route_id, delivery_date, status "
            "FROM cooler_boxes WHERE id = :id"
        ),
        {"id": dest_box_id},
    ).fetchone()
    if dest is None:
        return jsonify({"error": "Destination box not found"}), 404
    if dest[3] != "open":
        return jsonify({"error": f"Destination box is {dest[3]}; can only move to open boxes."}), 400

    # Cross-route / cross-date guard
    if int(dest[1]) != int(cbi[5]) or str(dest[2]) != str(cbi[6]):
        return jsonify({"error": "Cannot move items between routes or dates."}), 400

    if dest[0] == cbi[1]:
        return jsonify({"error": "Item is already in that box."}), 400

    source_box_id = cbi[1]
    now = get_utc_now()

    db.session.execute(
        text(
            "UPDATE cooler_box_items "
            "SET cooler_box_id = :dest, updated_at = :now "
            "WHERE id = :cbi_id"
        ),
        {"dest": dest_box_id, "now": now, "cbi_id": cbi_id},
    )

    # Recalculate stop range on both affected boxes
    for box_id_to_update in (source_box_id, dest_box_id):
        recalc = db.session.execute(
            text(
                "SELECT MIN(delivery_sequence), MAX(delivery_sequence) "
                "FROM cooler_box_items WHERE cooler_box_id = :bid"
            ),
            {"bid": box_id_to_update},
        ).fetchone()
        db.session.execute(
            text(
                "UPDATE cooler_boxes "
                "SET first_stop_sequence = :fs, last_stop_sequence = :ls "
                "WHERE id = :bid"
            ),
            {"fs": recalc[0], "ls": recalc[1], "bid": box_id_to_update},
        )

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

---

## CHANGE 9 — `route_picking` view — pass LIFO data and item statuses to template

### Step 9a — Sort boxes LIFO in the view

Find where `boxes` is built from `_boxes_raw`. The current query ends with `ORDER BY cb.box_no`. **This is fine for creation order** — keep it. But add a sorted version for the template:

After building the `boxes` list, add:

```python
    # LIFO display order: boxes covering later stops are shown first
    # so the picker packs last-delivery items into the box first.
    boxes_lifo = sorted(
        boxes,
        key=lambda b: (b["last_stop_sequence"] or 0),
        reverse=True,
    )
```

Pass `boxes_lifo` to `render_template` alongside the existing `boxes`.

### Step 9b — Add `id` and `status` to `box_items_by_box`

Find the `box_items_by_box` query:

```python
        _item_rows = db.session.execute(
            text(
                "SELECT cbi.cooler_box_id, cbi.invoice_no, cbi.item_code, cbi.item_name, "
                "       cbi.expected_qty, cbi.customer_name, cbi.delivery_sequence "
                "FROM cooler_box_items cbi "
                "WHERE cbi.cooler_box_id = ANY(:bids) "
                "ORDER BY cbi.cooler_box_id, cbi.delivery_sequence NULLS LAST, cbi.invoice_no"
            ),
```

**Replace with** (adds `cbi.id`, `cbi.status`, flips delivery_sequence to DESC for LIFO):

```python
        _item_rows = db.session.execute(
            text(
                "SELECT cbi.cooler_box_id, cbi.invoice_no, cbi.item_code, cbi.item_name, "
                "       cbi.expected_qty, cbi.customer_name, cbi.delivery_sequence, "
                "       cbi.id, cbi.status "
                "FROM cooler_box_items cbi "
                "WHERE cbi.cooler_box_id = ANY(:bids) "
                "ORDER BY cbi.cooler_box_id, cbi.delivery_sequence DESC NULLS LAST, cbi.invoice_no"
            ),
```

And update the dict that builds `box_items_by_box` to include the new fields:

```python
        for _r in _item_rows:
            box_items_by_box.setdefault(int(_r[0]), []).append({
                "invoice_no":        _r[1],
                "item_code":         _r[2],
                "item_name":         _r[3] or "",
                "qty":               float(_r[4]) if _r[4] is not None else 0,
                "customer_name":     _r[5] or "",
                "delivery_sequence": _r[6],
                "cbi_id":            _r[7],    # needed for move-to-box
                "status":            _r[8] or "planned",
            })
```

Also pass `boxes_lifo` to `render_template`.

---

## CHANGE 10 — `templates/cooler/route_picking.html` — new planning UI

### Step 10a — "Plan Boxes" button

Find the existing "Generate Box Plan" button/form (the one that POSTs to `confirm-box-plan`). It is currently gated on `picked_unboxed_count > 0`. 

**Change the label and gate condition:**

- Old label: "Generate Box Plan" (or similar)
- New label: **"Plan Cooler Boxes"**
- Old gate: show only when `picked_unboxed_count > 0`
- New gate: show when `picked_unboxed_count > 0` (unchanged — now counts both pending and picked unplanned items)

No other change to this button — it already POSTs to `confirm-box-plan` which now handles pending items.

### Step 10b — Box-grouped LIFO picking view

The template currently renders a flat list of queue items. Add a **box-by-box section** that renders `boxes_lifo` with their items.

Add this section **above** the existing flat queue list. Wrap the flat list in a collapsed section so experienced pickers who know the old flow can still use it, but the new box view is primary:

```html
{# ── Box-by-box picking (LIFO order) ── #}
{% if boxes_lifo %}
<div class="mb-4">
  <h5 class="mb-3">
    <i class="fas fa-boxes me-2"></i>Cooler Boxes
    <span class="badge bg-secondary ms-2">{{ boxes_lifo|length }} box(es)</span>
    <small class="text-muted ms-2" style="font-size:0.75rem">
      Last delivery first — pick in this order
    </small>
  </h5>

  {% for box in boxes_lifo %}
  {% set b_items = box_items_by_box.get(box.id, []) %}
  <div class="card mb-3 border-{% if box.status == 'closed' %}success{% else %}secondary{% endif %}">

    {# Box header #}
    <div class="card-header d-flex justify-content-between align-items-center
                {% if box.status == 'closed' %}bg-success bg-opacity-10{% endif %}">
      <div>
        <strong>Box #{{ box.box_no }}</strong>
        {% if box.box_type_name %}
          <span class="badge bg-secondary ms-1">{{ box.box_type_name }}</span>
        {% endif %}
        {% if box.first_stop_sequence is not none %}
          <span class="text-muted ms-2" style="font-size:0.8rem">
            Stops {{ box.first_stop_sequence|int }}–{{ box.last_stop_sequence|int }}
          </span>
        {% endif %}
      </div>
      <div class="d-flex align-items-center gap-2">
        <span class="badge {% if box.status == 'closed' %}bg-success{% elif box.status == 'open' %}bg-primary{% else %}bg-secondary{% endif %}">
          {{ box.status|upper }}
        </span>
        <span class="text-muted small">{{ b_items|length }} item(s)</span>
      </div>
    </div>

    {# Box items #}
    {% if b_items %}
    <div class="card-body p-0">
      <table class="table table-sm table-hover mb-0 align-middle">
        <thead class="table-secondary">
          <tr>
            <th>Stop</th>
            <th>Customer</th>
            <th>Item</th>
            <th class="text-end">Qty</th>
            <th>Status</th>
            {% if box.status == 'open' %}
            <th>Actions</th>
            {% endif %}
          </tr>
        </thead>
        <tbody>
          {% for item in b_items %}
          <tr class="{% if item.status == 'picked' %}opacity-75{% endif %}">
            <td class="text-muted small">
              {% if item.delivery_sequence is not none %}{{ item.delivery_sequence|int }}{% else %}—{% endif %}
            </td>
            <td class="small">{{ item.customer_name }}</td>
            <td>
              <div class="fw-bold small">{{ item.item_code }}</div>
              <div class="text-muted" style="font-size:0.75rem">{{ item.item_name }}</div>
            </td>
            <td class="text-end">{{ item.qty }}</td>
            <td>
              {% if item.status == 'picked' %}
                <span class="badge bg-success">
                  <i class="fas fa-check me-1"></i>Picked
                </span>
              {% else %}
                <span class="badge bg-warning text-dark">
                  <i class="fas fa-clock me-1"></i>Planned
                </span>
              {% endif %}
            </td>
            {% if box.status == 'open' %}
            <td>
              {# Move to box dropdown #}
              {% set other_boxes = boxes_lifo | selectattr('status', 'equalto', 'open') | selectattr('id', 'ne', box.id) | list %}
              {% if other_boxes %}
              <div class="dropdown">
                <button class="btn btn-outline-secondary btn-sm dropdown-toggle"
                        type="button" data-bs-toggle="dropdown">
                  Move
                </button>
                <ul class="dropdown-menu">
                  {% for ob in other_boxes %}
                  <li>
                    <button class="dropdown-item btn-move-item"
                            data-cbi-id="{{ item.cbi_id }}"
                            data-dest-box-id="{{ ob.id }}"
                            data-dest-box-no="{{ ob.box_no }}">
                      → Box #{{ ob.box_no }}
                      {% if ob.first_stop_sequence is not none %}
                        (stops {{ ob.first_stop_sequence|int }}–{{ ob.last_stop_sequence|int }})
                      {% endif %}
                    </button>
                  </li>
                  {% endfor %}
                </ul>
              </div>
              {% endif %}
            </td>
            {% endif %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% else %}
    <div class="card-body text-muted small">No items assigned to this box yet.</div>
    {% endif %}

  </div>
  {% endfor %}
</div>
{% endif %}
```

### Step 10c — Move-to-box JavaScript

Add this script inside the `{% block scripts %}` section (or equivalent):

```javascript
// ── Move item between cooler boxes ──────────────────────────────────────────
document.addEventListener("click", function(e) {
  var btn = e.target.closest(".btn-move-item");
  if (!btn) return;

  var cbiId    = btn.dataset.cbiId;
  var destId   = btn.dataset.destBoxId;
  var destNo   = btn.dataset.destBoxNo;

  if (!confirm("Move this item to Box #" + destNo + "?")) return;

  fetch("/cooler/box-item/" + cbiId + "/move-to-box", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({destination_box_id: parseInt(destId)})
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.error) {
      alert("Could not move: " + data.error);
    } else {
      window.location.reload();
    }
  })
  .catch(function() {
    alert("Network error — please try again.");
  });
});
```

---

## End-to-end flow after applying

1. Supervisor opens route → clicks **"Plan Cooler Boxes"**
2. System auto-assigns all pending + picked items to boxes (LIFO-aware, volume-based — same algorithm as today)
3. Supervisor sees boxes with their planned items. Moves any item with the **Move** dropdown — one click, instant
4. Picker opens the same screen — sees **Box #3 (stops 8–10)** first, then **Box #2 (stops 5–7)**, then **Box #1 (stops 1–4)**
5. Within each box, items are listed last-stop-first: stop 10 before stop 9 before stop 8
6. Picker picks each item physically — badge changes from **Planned** (yellow) to **Picked** (green)
7. Picker can still move a picked item to another open box during picking using the same Move dropdown
8. Once all items in a box show green → close the box
9. Completion check (`_is_cooler_route_pack_complete`) passes only when all `planned` rows are gone (all physically picked and all boxes closed)
