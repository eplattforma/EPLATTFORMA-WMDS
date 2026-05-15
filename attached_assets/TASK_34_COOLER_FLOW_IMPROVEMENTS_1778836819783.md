# Task #34 — Cooler Flow Improvements

## Prerequisites
- FIX-001 must be deployed and verified (box assignment working)
- FIX-002 must be deployed (lock model solid)
- End-to-end cooler route test must have passed at least once

## What This Task Builds

Four improvements to the cooler picking flow that make it operationally
coherent end-to-end:

1. **Mode selection at lock time** — Location Order vs Sequential Stop
2. **Capacity-based auto-boxing** — items fill boxes automatically during
   sequential picking; box seals when full, new box opens
3. **Pack by Stop panel** — for Location Order mode, one-click box
   creation per stop after picking is complete
4. **Auto-redirect to cooler screen** — when cooler batch is completed,
   picker is automatically sent to the cooler screen to finish packing

---

## Schema Changes (additive)

```sql
-- On batch_picking_sessions
ALTER TABLE batch_picking_sessions
  ADD COLUMN IF NOT EXISTS cooler_pack_mode VARCHAR(20) DEFAULT 'location_order',
  ADD COLUMN IF NOT EXISTS cooler_box_type_id INTEGER REFERENCES cooler_box_types(id);

-- On cooler_boxes
ALTER TABLE cooler_boxes
  ADD COLUMN IF NOT EXISTS fill_cm3 NUMERIC(12,2) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS fill_weight_kg NUMERIC(10,3) DEFAULT 0,
  ADD COLUMN IF NOT EXISTS cooler_session_id INTEGER,
  ADD COLUMN IF NOT EXISTS box_type_id INTEGER REFERENCES cooler_box_types(id);
```

Add to `update_phase6_cooler_integration_schema.py` using the existing
additive `_add_column_if_missing` helper pattern.

---

## Change 1 — Mode selection UI at lock time

### Template (`templates/cooler/route_picking.html`)

Replace the existing plain "Lock cooler sequencing" button with a form
that includes mode selection. Show this form ONLY when sequencing is
not yet locked (`not cooler_session or not cooler_session.sequence_locked_at`):

```html
{% if not (cooler_session and cooler_session.sequence_locked_at) %}
<form method="post"
      action="{{ url_for('cooler.lock_sequencing',
                         route_id=route_id) }}">

  <div class="row g-3 mb-3">
    <!-- Mode selection -->
    <div class="col-12 col-md-6">
      <label class="form-label fw-bold small">Picking Mode</label>
      <div class="d-flex flex-column gap-2">

        <div class="form-check border rounded p-2">
          <input class="form-check-input" type="radio"
                 name="cooler_pack_mode" value="location_order"
                 id="mode_loc" checked>
          <label class="form-check-label" for="mode_loc">
            <strong>Location Order</strong>
            <div class="text-muted small">
              Picker walks the warehouse efficiently.
              Assign items to boxes after picking completes.
            </div>
          </label>
        </div>

        <div class="form-check border rounded p-2">
          <input class="form-check-input" type="radio"
                 name="cooler_pack_mode" value="sequential_stop"
                 id="mode_seq">
          <label class="form-check-label" for="mode_seq">
            <strong>Sequential Stop</strong>
            <div class="text-muted small">
              Last stop first. Items auto-assigned to boxes as
              picked. Boxes fill by capacity then seal automatically.
            </div>
          </label>
        </div>

      </div>
    </div>

    <!-- Box type — only for sequential_stop -->
    <div class="col-12 col-md-6" id="box_type_section" style="display:none;">
      <label class="form-label fw-bold small">
        Box Type
        <span class="text-muted fw-normal">
          (boxes created automatically)
        </span>
      </label>
      <select name="cooler_box_type_id" class="form-select">
        <option value="">— choose box size —</option>
        {% for bt in box_types %}
        <option value="{{ bt.id }}">
          {{ bt.name }}
          ({{ (bt.internal_volume_cm3 * bt.fill_efficiency)|round|int }} cm³ usable)
        </option>
        {% endfor %}
      </select>
      {% if estimate and estimate.box_estimates %}
      <div class="text-muted small mt-1">
        Estimator recommends:
        <strong>{{ estimate.box_estimates[0].box_type_name }}</strong>
      </div>
      {% endif %}
    </div>
  </div>

  <button type="submit" class="btn btn-success">
    <i class="fas fa-lock me-1"></i>Lock Cooler Sequencing
  </button>
</form>

<script>
document.querySelectorAll('[name="cooler_pack_mode"]').forEach(el => {
  el.addEventListener('change', function() {
    document.getElementById('box_type_section').style.display =
      (this.value === 'sequential_stop') ? '' : 'none';
  });
});
</script>
{% endif %}
```

### Backend — pass `box_types` to template

In `blueprints/cooler_picking.py:route_picking()`, add to the query:

```python
from sqlalchemy import text
box_types = db.session.execute(text(
    "SELECT id, name, internal_volume_cm3, fill_efficiency, "
    "       max_weight_kg "
    "FROM cooler_box_types WHERE is_active = true "
    "ORDER BY sort_order, name"
)).fetchall()
# Convert to dicts for template
box_types = [dict(zip(
    ['id','name','internal_volume_cm3','fill_efficiency','max_weight_kg'],
    r
)) for r in box_types]
```

Pass `box_types=box_types` to `render_template(...)`.

### Backend — save mode in lock endpoint

In `blueprints/cooler_picking.py:lock_sequencing()`, after stamping
`sequence_locked_at`, also persist the mode:

```python
pack_mode = request.form.get("cooler_pack_mode", "location_order")
box_type_id = request.form.get("cooler_box_type_id") or None
if box_type_id:
    try:
        box_type_id = int(box_type_id)
    except ValueError:
        box_type_id = None

# Validate: sequential_stop requires a box_type_id
if pack_mode == "sequential_stop" and not box_type_id:
    flash("Please select a box type for Sequential Stop mode.", "danger")
    return redirect(url_for("cooler.route_picking",
                            route_id=route_id,
                            delivery_date=delivery_date))

db.session.execute(text("""
    UPDATE batch_picking_sessions
    SET cooler_pack_mode = :mode,
        cooler_box_type_id = :btid
    WHERE id = :sid
"""), {"mode": pack_mode, "btid": box_type_id, "sid": session_id})
```

---

## Change 2 — Auto-redirect after cooler batch completes

In `routes_batch.py`, find where batch status is set to 'Completed'.
Add the cooler-specific redirect immediately after:

```python
# After batch.status = 'Completed' and db.session.commit():
if getattr(batch_session, 'session_type', None) == 'cooler_route' \
        and batch_session.route_id:
    from models import Shipment
    route = Shipment.query.get(batch_session.route_id)
    if route and route.delivery_date:
        pack_mode = getattr(
            batch_session, 'cooler_pack_mode', 'location_order'
        )
        if pack_mode == 'sequential_stop':
            msg = ("✅ Cooler picking complete. "
                   "All items have been auto-assigned to boxes. "
                   "Seal each box and print labels below.")
        else:
            msg = ("✅ Cooler picking complete. "
                   "Now use Pack by Stop below to assign items to boxes.")
        flash(msg, "success")
        return redirect(url_for(
            "cooler.route_picking",
            route_id=batch_session.route_id,
            delivery_date=route.delivery_date.strftime("%Y-%m-%d"),
        ))
```

---

## Change 3 — Auto-boxing in sequential_stop mode

### New helper: `_cooler_auto_assign()`

Add to `services/cooler_route_extraction.py`:

```python
def cooler_auto_assign_item(batch_session_id: int, invoice_no: str,
                             item_code: str, qty_picked: float,
                             delivery_sequence) -> dict:
    """
    Called after each item is confirmed picked in a cooler batch
    running in sequential_stop mode.

    Assigns the item to the current open box for this route session.
    If the box is full (fill_cm3 + item_volume > effective_capacity),
    seals the current box and opens a new one.

    Returns: {'box_id': int, 'box_no': int, 'new_box_created': bool}
    """
    from app import db
    from sqlalchemy import text

    # Get session info
    session = db.session.execute(text(
        "SELECT route_id, cooler_box_type_id "
        "FROM batch_picking_sessions WHERE id = :sid"
    ), {"sid": batch_session_id}).fetchone()
    if not session:
        return {}

    route_id = session[0]
    box_type_id = session[1]
    if not route_id or not box_type_id:
        return {}

    # Get delivery date from route
    dd_row = db.session.execute(text(
        "SELECT delivery_date FROM shipments WHERE id = :rid"
    ), {"rid": route_id}).fetchone()
    if not dd_row:
        return {}
    delivery_date = str(dd_row[0])

    # Get box type capacity
    bt = db.session.execute(text(
        "SELECT internal_volume_cm3 * fill_efficiency AS cap, "
        "       max_weight_kg "
        "FROM cooler_box_types WHERE id = :btid"
    ), {"btid": box_type_id}).fetchone()
    if not bt:
        return {}
    effective_capacity = float(bt[0])
    max_weight = float(bt[1]) if bt[1] else None

    # Get item volume and weight
    item = db.session.execute(text(
        "SELECT item_length, item_width, item_height, item_weight "
        "FROM ps_items_dw WHERE item_code_365 = :code LIMIT 1"
    ), {"code": item_code}).fetchone()
    item_volume = 0.0
    item_weight = 0.0
    if item and all(item[i] is not None for i in range(3)):
        item_volume = float(item[0]) * float(item[1]) * float(item[2])
        item_weight = float(item[3] or 0) * qty_picked
    total_volume = item_volume * qty_picked

    # Find current open box
    open_box = db.session.execute(text("""
        SELECT id, box_no, fill_cm3, fill_weight_kg
        FROM cooler_boxes
        WHERE route_id = :rid
          AND delivery_date = :dd
          AND status = 'open'
          AND cooler_session_id = :sid
        ORDER BY box_no DESC LIMIT 1
    """), {"rid": route_id, "dd": delivery_date,
           "sid": batch_session_id}).fetchone()

    new_box_created = False

    def _new_box(after_no):
        next_no = after_no + 1
        db.session.execute(text("""
            INSERT INTO cooler_boxes
              (route_id, delivery_date, box_no, status,
               fill_cm3, fill_weight_kg,
               box_type_id, cooler_session_id,
               first_stop_sequence, last_stop_sequence,
               created_by, created_at)
            VALUES
              (:rid, :dd, :no, 'open', 0, 0,
               :btid, :sid, NULL, NULL, 'system', NOW())
        """), {
            "rid": route_id, "dd": delivery_date, "no": next_no,
            "btid": box_type_id, "sid": batch_session_id,
        })
        row = db.session.execute(text(
            "SELECT id FROM cooler_boxes "
            "WHERE route_id=:rid AND delivery_date=:dd AND box_no=:no"
        ), {"rid": route_id, "dd": delivery_date, "no": next_no}).fetchone()
        return row[0], next_no

    if open_box is None:
        box_id, box_no = _new_box(0)
        cur_fill = 0.0
        cur_weight = 0.0
        new_box_created = True
    else:
        box_id = open_box[0]
        box_no = open_box[1]
        cur_fill = float(open_box[2] or 0)
        cur_weight = float(open_box[3] or 0)

        # Seal and open new if over capacity
        over_volume = (total_volume > 0 and
                       cur_fill + total_volume > effective_capacity)
        over_weight = (max_weight and
                       cur_weight + item_weight > max_weight)
        if over_volume or over_weight:
            # Seal current box
            db.session.execute(text("""
                UPDATE cooler_boxes
                SET status='closed', closed_at=NOW(), closed_by='system'
                WHERE id=:bid
            """), {"bid": box_id})
            box_id, box_no = _new_box(box_no)
            cur_fill = 0.0
            cur_weight = 0.0
            new_box_created = True

    # Get queue_item_id for this item
    qrow = db.session.execute(text("""
        SELECT id FROM batch_pick_queue
        WHERE invoice_no=:inv AND item_code=:item
          AND batch_session_id=:sid LIMIT 1
    """), {"inv": invoice_no, "item": item_code,
           "sid": batch_session_id}).fetchone()
    queue_item_id = qrow[0] if qrow else None

    # Insert into cooler_box_items
    db.session.execute(text("""
        INSERT INTO cooler_box_items
          (cooler_box_id, invoice_no, item_code,
           qty_assigned, delivery_sequence, queue_item_id)
        VALUES (:bid, :inv, :item, :qty, :seq, :qid)
        ON CONFLICT DO NOTHING
    """), {
        "bid": box_id, "inv": invoice_no, "item": item_code,
        "qty": qty_picked, "seq": float(delivery_sequence or 0),
        "qid": queue_item_id,
    })

    # Update box fill level and stop range
    db.session.execute(text("""
        UPDATE cooler_boxes SET
          fill_cm3 = :fill,
          fill_weight_kg = :wt,
          first_stop_sequence = LEAST(
              COALESCE(first_stop_sequence, :seq), :seq),
          last_stop_sequence = GREATEST(
              COALESCE(last_stop_sequence, :seq), :seq)
        WHERE id = :bid
    """), {
        "fill": cur_fill + total_volume,
        "wt": cur_weight + item_weight,
        "seq": float(delivery_sequence or 0),
        "bid": box_id,
    })

    return {
        "box_id": box_id,
        "box_no": box_no,
        "new_box_created": new_box_created,
    }
```

### Wire into batch confirm handler

In `routes_batch.py`, in the item confirmation handler, after marking
`InvoiceItem.is_picked = True`, add:

```python
# Auto-box for sequential_stop cooler batches
if (getattr(batch_session, 'session_type', None) == 'cooler_route'
        and getattr(batch_session, 'cooler_pack_mode', None)
        == 'sequential_stop'):
    from services.cooler_route_extraction import cooler_auto_assign_item
    from sqlalchemy import text as _text
    # Get delivery_sequence for this item
    seq_row = db.session.execute(_text("""
        SELECT delivery_sequence FROM batch_pick_queue
        WHERE invoice_no=:inv AND item_code=:item
          AND batch_session_id=:sid LIMIT 1
    """), {"inv": invoice_no, "item": item_code,
           "sid": batch_session.id}).fetchone()
    delivery_sequence = seq_row[0] if seq_row else None

    result = cooler_auto_assign_item(
        batch_session_id=batch_session.id,
        invoice_no=invoice_no,
        item_code=item_code,
        qty_picked=float(picked_qty or 1),
        delivery_sequence=delivery_sequence,
    )
    if result.get("new_box_created"):
        logger.info(
            f"Auto-boxing: new Box #{result['box_no']} "
            f"opened for session {batch_session.id}"
        )
```

---

## Change 4 — Pack by Stop panel (Location Order mode)

### Backend — add `pack_by_stop` data to `route_picking()`

In `blueprints/cooler_picking.py:route_picking()`, when
`picking_phase["complete"]` is True AND
`cooler_pack_mode == "location_order"`, build per-stop packing data:

```python
pack_by_stop = []
if (picking_phase.get("complete")
        and cooler_pack_mode == "location_order"):

    # All picked items not yet in a box, grouped by stop
    unboxed = db.session.execute(text("""
        SELECT bpq.id, bpq.invoice_no, bpq.item_code,
               bpq.qty_required, bpq.delivery_sequence,
               i.customer_name,
               psi.item_name
        FROM batch_pick_queue bpq
        JOIN invoices i ON i.invoice_no = bpq.invoice_no
        LEFT JOIN ps_items_dw psi ON psi.item_code_365 = bpq.item_code
        WHERE bpq.batch_session_id = :sid
          AND bpq.pick_zone_type = 'cooler'
          AND bpq.status = 'picked'
          AND bpq.id NOT IN (
              SELECT queue_item_id FROM cooler_box_items
              WHERE queue_item_id IS NOT NULL
          )
        ORDER BY bpq.delivery_sequence, bpq.item_code
    """), {"sid": cooler_session_id}).fetchall()

    stop_groups = {}
    for r in unboxed:
        seq = float(r[4] or 0)
        if seq not in stop_groups:
            stop_groups[seq] = {
                "stop_seq": seq,
                "customer_name": r[5] or "—",
                "items": [],
            }
        stop_groups[seq]["items"].append({
            "queue_item_id": r[0],
            "invoice_no": r[1],
            "item_code": r[2],
            "qty": float(r[3] or 0),
            "item_name": r[6] or r[2],
        })

    # Sort descending (last stop first = pack deepest in truck first)
    pack_by_stop = sorted(
        stop_groups.values(),
        key=lambda x: x["stop_seq"],
        reverse=True,
    )
```

Pass `pack_by_stop=pack_by_stop` and
`cooler_pack_mode=cooler_pack_mode` to `render_template(...)`.

### New endpoint: `pack_stop`

```python
@cooler_bp.route(
    "/route/<int:route_id>/<delivery_date>/pack-stop",
    methods=["POST"],
)
@login_required
@require_permission("cooler.manage_boxes")
@_require_cooler_pick
@_require_picking_flag
def pack_stop(route_id, delivery_date):
    """
    Create a box for one stop and auto-assign all unboxed picked
    items for that stop to it.
    """
    stop_seq = request.form.get("stop_seq", type=float)
    box_type_id = request.form.get("box_type_id", type=int)
    if stop_seq is None:
        flash("Stop sequence required.", "danger")
        return redirect(url_for("cooler.route_picking",
                                route_id=route_id,
                                delivery_date=delivery_date))

    # Count existing boxes for box_no
    existing = db.session.execute(text(
        "SELECT COUNT(*) FROM cooler_boxes "
        "WHERE route_id=:rid AND delivery_date=:dd"
    ), {"rid": route_id, "dd": str(delivery_date)}).scalar() or 0
    next_no = existing + 1

    db.session.execute(text("""
        INSERT INTO cooler_boxes
          (route_id, delivery_date, box_no, status,
           first_stop_sequence, last_stop_sequence,
           box_type_id, created_by, created_at)
        VALUES
          (:rid, :dd, :no, 'open', :seq, :seq,
           :btid, :user, NOW())
    """), {
        "rid": route_id, "dd": str(delivery_date),
        "no": next_no, "seq": stop_seq,
        "btid": box_type_id, "user": current_user.username,
    })
    box_id = db.session.execute(text(
        "SELECT id FROM cooler_boxes "
        "WHERE route_id=:rid AND delivery_date=:dd AND box_no=:no"
    ), {"rid": route_id, "dd": str(delivery_date),
        "no": next_no}).scalar()

    # Assign unboxed picked items for this stop
    items = db.session.execute(text("""
        SELECT bpq.id, bpq.invoice_no, bpq.item_code, bpq.qty_required
        FROM batch_pick_queue bpq
        JOIN invoices i ON i.invoice_no = bpq.invoice_no
        WHERE bpq.pick_zone_type = 'cooler'
          AND i.route_id = :rid
          AND bpq.status = 'picked'
          AND ROUND(bpq.delivery_sequence::NUMERIC, 2) =
              ROUND(:seq::NUMERIC, 2)
          AND bpq.id NOT IN (
              SELECT queue_item_id FROM cooler_box_items
              WHERE queue_item_id IS NOT NULL
          )
    """), {"rid": route_id, "seq": stop_seq}).fetchall()

    for item in items:
        db.session.execute(text("""
            INSERT INTO cooler_box_items
              (cooler_box_id, invoice_no, item_code,
               qty_assigned, delivery_sequence, queue_item_id)
            VALUES (:bid, :inv, :item, :qty, :seq, :qid)
            ON CONFLICT DO NOTHING
        """), {
            "bid": box_id, "inv": item[1], "item": item[2],
            "qty": float(item[3] or 0), "seq": stop_seq,
            "qid": item[0],
        })

    db.session.commit()
    flash(
        f"📦 Box #{next_no} created for Stop {stop_seq} "
        f"— {len(items)} item(s) assigned.",
        "success",
    )
    return redirect(url_for("cooler.route_picking",
                            route_id=route_id,
                            delivery_date=delivery_date))
```

### Template — Pack by Stop section

In `templates/cooler/route_picking.html`, add after the Sequenced
section and before the Cooler Boxes section:

```html
{% if cooler_pack_mode == 'location_order'
      and picking_phase and picking_phase.complete
      and pack_by_stop %}
<div class="card mb-4 border-success">
  <div class="card-header bg-success text-white d-flex align-items-center">
    <i class="fas fa-box me-2"></i>
    <strong>Pack by Stop</strong>
    <span class="ms-2 opacity-75 small">
      — last stop first (loads deepest in truck)
    </span>
  </div>
  <div class="card-body p-3">

    {% for stop_data in pack_by_stop %}
    <div class="card mb-3 border-secondary">
      <div class="card-header d-flex align-items-center justify-content-between py-2">
        <div>
          <span class="badge bg-primary me-2">
            Stop {{ stop_data.stop_seq }}
          </span>
          <strong>{{ stop_data.customer_name }}</strong>
          <span class="text-muted ms-2 small">
            {{ stop_data.items|length }} item(s)
          </span>
        </div>
      </div>
      <div class="card-body py-2">
        <!-- Item photo grid -->
        <div class="d-flex flex-wrap gap-2 mb-3">
          {% for item in stop_data.items %}
          <div class="text-center" style="width:80px;">
            <img src="{{ url_for('static',
                         filename='images/' + item.item_code + '.webp') }}"
                 onerror="this.src='{{ url_for('static',
                          filename='images/image-not-found.png') }}'"
                 class="rounded mb-1"
                 style="width:70px;height:70px;object-fit:contain;
                        border:1px solid #dee2e6;">
            <div class="small fw-bold">{{ item.item_code }}</div>
            <div class="badge bg-dark">× {{ item.qty|int }}</div>
          </div>
          {% endfor %}
        </div>
        <!-- Create box form -->
        <form method="post"
              action="{{ url_for('cooler.pack_stop',
                                 route_id=route_id,
                                 delivery_date=delivery_date) }}">
          <input type="hidden" name="stop_seq"
                 value="{{ stop_data.stop_seq }}">
          <div class="d-flex align-items-center gap-2">
            {% if box_types %}
            <select name="box_type_id"
                    class="form-select form-select-sm"
                    style="max-width:160px;">
              <option value="">— box size —</option>
              {% for bt in box_types %}
              <option value="{{ bt.id }}">{{ bt.name }}</option>
              {% endfor %}
            </select>
            {% endif %}
            <button type="submit" class="btn btn-success btn-sm">
              <i class="fas fa-box me-1"></i>
              Create Box & Assign All
            </button>
          </div>
        </form>
      </div>
    </div>
    {% endfor %}

    <!-- All packed indicator -->
    {% if pack_by_stop|length == 0 %}
    <div class="alert alert-success mb-0">
      <i class="fas fa-check-circle me-2"></i>
      All items assigned to boxes.
      <a href="#cooler-boxes-section" class="ms-2">
        Seal boxes and print labels ↓
      </a>
    </div>
    {% endif %}
  </div>
</div>
{% endif %}
```

### Cooler boxes: show fill bar

In the existing Cooler Boxes table, add a capacity bar when
`box.fill_cm3` and `box.effective_cap` are available:

```html
{% if box.effective_cap and box.effective_cap > 0 %}
{% set fill_pct = ((box.fill_cm3 or 0) / box.effective_cap * 100)|round|int %}
<div class="progress mt-1" style="height:5px;min-width:80px;">
  <div class="progress-bar
       {{ 'bg-danger' if fill_pct > 90
          else 'bg-warning' if fill_pct > 70
          else 'bg-success' }}"
       style="width:{{ fill_pct }}%"></div>
</div>
<small class="text-muted">
  {{ fill_pct }}% full
</small>
{% endif %}
```

Update the boxes query in `route_picking()` to include fill data:

```python
boxes = db.session.execute(text("""
    SELECT cb.id, cb.route_id, cb.delivery_date, cb.box_no,
           cb.status, cb.first_stop_sequence, cb.last_stop_sequence,
           COUNT(cbi.id) AS item_count,
           COALESCE(cb.fill_cm3, 0) AS fill_cm3,
           COALESCE(bt.internal_volume_cm3 * bt.fill_efficiency, 0)
             AS effective_cap,
           COALESCE(bt.name, '—') AS box_type_name
    FROM cooler_boxes cb
    LEFT JOIN cooler_box_items cbi ON cbi.cooler_box_id = cb.id
    LEFT JOIN cooler_box_types bt ON bt.id = cb.box_type_id
    WHERE cb.delivery_date = :dd AND cb.route_id = :rid
    GROUP BY cb.id, cb.route_id, cb.delivery_date, cb.box_no,
             cb.status, cb.first_stop_sequence, cb.last_stop_sequence,
             cb.fill_cm3, bt.internal_volume_cm3, bt.fill_efficiency,
             bt.name
    ORDER BY cb.box_no
"""), {"dd": str(delivery_date), "rid": route_id_int}).fetchall()
```

---

## Tests Required

Create `tests/test_cooler_flow_improvements.py`:

| # | Scenario | Expected |
|---|----------|----------|
| C1 | Lock with location_order mode | `cooler_pack_mode='location_order'`, no box_type required |
| C2 | Lock with sequential_stop + no box_type | Validation error, lock rejected |
| C3 | Lock with sequential_stop + box_type | Saved correctly |
| C4 | Confirm item in sequential_stop batch | `cooler_box_items` row created, `fill_cm3` updated |
| C5 | Item pushes box over capacity | Previous box sealed, new box opened, item in new box |
| C6 | `pack_stop` endpoint creates box + assigns items | Box created, items assigned, redirect |
| C7 | `pack_stop` with no unboxed items for stop | Box created, 0 items, no error |
| C8 | Batch complete in cooler_route session | Redirect to cooler screen (not generic complete page) |
| C9 | Batch complete — sequential_stop flash message differs from location_order | ✓ |
| C10 | `pack_by_stop` sorted descending (last stop first) | ✓ |

## Verification

1. Lock sequencing with Sequential Stop + Medium box
2. Assign to picker — picker confirms items via batch interface
3. After each confirm: verify `cooler_box_items` row exists
4. When box capacity exceeded: verify old box status='closed', new box created
5. After batch complete: picker redirected to cooler screen
6. Cooler screen shows all items "Picked", boxes show fill bar
7. In Location Order mode: verify Pack by Stop panel appears after batch complete
8. Click "Create Box & Assign All" for one stop → box created, items assigned
9. Close box → print label → label shows correct stop range
