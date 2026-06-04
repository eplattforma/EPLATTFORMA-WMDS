# Cooler Route Picking — 3 targeted changes to `templates/cooler/route_picking.html`

Make these 3 find-and-replace edits to `templates/cooler/route_picking.html`.
No other files need changing.

---

## CHANGE 1 — Replace the step state calculations + step wizard

Find this block (starts right after the page header `</div>` and `</div>`):

```
  {# ── 4-step wizard indicator ...
```

Replace everything from that comment **down to and including** the closing `</div>` of the wizard card (it ends with `</div>` then `</div>`) with:

```jinja2
  {# ── Step state calculations #}
  {% set _is_locked    = cooler_session and cooler_session.is_locked %}
  {% set _boxes_exist  = boxes | length > 0 %}
  {% set _open_boxes   = boxes | selectattr('status', 'equalto', 'open')   | list %}
  {% set _closed_boxes = boxes | selectattr('status', 'equalto', 'closed') | list %}
  {% set _picking_done = picking_phase and picking_phase.complete %}
  {% set _all_closed   = (_open_boxes | length == 0 and _closed_boxes | length > 0) %}
  {% set _any_overfull = boxes | selectattr('estimated_fill_pct') | selectattr('estimated_fill_pct', 'greaterthan', 100) | list | length > 0 %}

  {# 3-step states: 0=future 1=active 2=done #}
  {% set _s1 = 2 if _is_locked else 1 %}
  {% set _s2 = 2 if (_boxes_exist and not _any_overfull) else (1 if _is_locked else 0) %}
  {% set _s3 = 2 if (_picking_done and _all_closed) else (1 if (_s2 == 2) else 0) %}

  {# ── Prominent 3-step progress bar #}
  <div class="mb-4">
    <div class="row g-0" style="border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.12);">

      {% set _1_bg = '#198754' if _s1==2 else '#0d6efd' if _s1==1 else '#adb5bd' %}
      <div class="col-md-4 p-0">
        <div class="h-100 d-flex align-items-center gap-3 px-4 py-3"
             style="background:{{ _1_bg }};color:#fff;min-height:80px;">
          <div class="flex-shrink-0 d-flex align-items-center justify-content-center rounded-circle fw-bold"
               style="width:48px;height:48px;font-size:1.3rem;background:rgba(255,255,255,0.25);">
            {% if _s1==2 %}<i class="fas fa-check"></i>{% else %}1{% endif %}
          </div>
          <div>
            <div class="fw-bold" style="font-size:1rem;">Confirm Cooler Route</div>
            <div style="font-size:0.78rem;opacity:0.85;margin-top:2px;">
              {% if _s1==2 %}✓ Done — sequence locked{% if cooler_session and cooler_session.sequence_locked_by %} by {{ cooler_session.sequence_locked_by }}{% endif %}
              {% else %}Lock delivery sequence for all items{% endif %}
            </div>
          </div>
          {% if _s1==1 %}<div class="ms-auto flex-shrink-0"><span class="badge bg-white text-primary fw-bold px-2 py-1" style="font-size:0.75rem;">← DO THIS NOW</span></div>{% endif %}
        </div>
      </div>
      <div class="col-auto d-none d-md-flex align-items-center" style="background:{{ _1_bg }};width:28px;">
        <div style="width:0;height:0;border-top:40px solid transparent;border-bottom:40px solid transparent;border-left:16px solid {{ _1_bg }};"></div>
      </div>

      {% set _2_bg = '#198754' if _s2==2 else '#0d6efd' if _s2==1 else '#adb5bd' %}
      <div class="col-md-4 p-0">
        <div class="h-100 d-flex align-items-center gap-3 px-4 py-3"
             style="background:{{ _2_bg }};color:#fff;min-height:80px;">
          <div class="flex-shrink-0 d-flex align-items-center justify-content-center rounded-circle fw-bold"
               style="width:48px;height:48px;font-size:1.3rem;background:rgba(255,255,255,0.25);">
            {% if _s2==2 %}<i class="fas fa-check"></i>{% else %}2{% endif %}
          </div>
          <div>
            <div class="fw-bold" style="font-size:1rem;">Plan Cooler Boxes</div>
            <div style="font-size:0.78rem;opacity:0.85;margin-top:2px;">
              {% if _s2==2 %}✓ Done — {{ boxes | length }} box(es) assigned
              {% elif _s2==1 %}Set availability, get recommendation, confirm plan
              {% else %}Complete Step 1 first{% endif %}
            </div>
          </div>
          {% if _s2==1 %}<div class="ms-auto flex-shrink-0"><span class="badge bg-white text-primary fw-bold px-2 py-1" style="font-size:0.75rem;">← DO THIS NOW</span></div>{% endif %}
        </div>
      </div>
      <div class="col-auto d-none d-md-flex align-items-center" style="background:{{ _2_bg }};width:28px;">
        <div style="width:0;height:0;border-top:40px solid transparent;border-bottom:40px solid transparent;border-left:16px solid {{ _2_bg }};"></div>
      </div>

      {% set _3_bg = '#198754' if _s3==2 else '#0d6efd' if _s3==1 else '#adb5bd' %}
      <div class="col-md-4 p-0">
        <div class="h-100 d-flex align-items-center gap-3 px-4 py-3"
             style="background:{{ _3_bg }};color:#fff;min-height:80px;">
          <div class="flex-shrink-0 d-flex align-items-center justify-content-center rounded-circle fw-bold"
               style="width:48px;height:48px;font-size:1.3rem;background:rgba(255,255,255,0.25);">
            {% if _s3==2 %}<i class="fas fa-check"></i>{% else %}3{% endif %}
          </div>
          <div>
            <div class="fw-bold" style="font-size:1rem;">Assign Picker &amp; Pick</div>
            <div style="font-size:0.78rem;opacity:0.85;margin-top:2px;">
              {% if _s3==2 %}✓ Done — all boxes closed
              {% elif _s3==1 %}{% if picking_phase and not picking_phase.empty %}{{ picking_phase.picked_count }} / {{ picking_phase.total_count }} picked{% else %}Assign picker, then start picking{% endif %}
              {% else %}Complete Step 2 first{% endif %}
            </div>
          </div>
          {% if _s3==1 %}<div class="ms-auto flex-shrink-0"><span class="badge bg-white text-primary fw-bold px-2 py-1" style="font-size:0.75rem;">← DO THIS NOW</span></div>{% endif %}
        </div>
      </div>

    </div>
  </div>

  {# ── "What to do now" action banner #}
  {% if _s1 == 1 %}
  <div class="alert alert-primary d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
    <div style="font-size:2rem;">1️⃣</div>
    <div class="flex-grow-1">
      <strong>Start here — Confirm Cooler Route</strong><br>
      <span class="small">Locks the delivery sequence so items can be sorted into boxes in the correct order.</span>
    </div>
    {% if has_permission('cooler.lock_sequencing') %}
    <form method="post" action="{{ url_for('cooler.lock_sequencing', route_id=route_id) }}" class="flex-shrink-0">
      <input type="hidden" name="_html_form" value="1">
      <input type="hidden" name="delivery_date" value="{{ delivery_date }}">
      <button class="btn btn-primary btn-lg text-nowrap" type="submit">
        <i class="fas fa-shield-alt me-2"></i>Confirm Cooler Route
      </button>
    </form>
    {% endif %}
  </div>

  {% elif _s2 == 1 %}
  <div class="alert alert-primary d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
    <div style="font-size:2rem;">2️⃣</div>
    <div class="flex-grow-1">
      <strong>Next — Plan the Cooler Boxes</strong><br>
      <span class="small">Set how many boxes you have available, click "Get Recommendation", adjust if needed, then confirm.</span>
    </div>
    <a href="#boxPlanCard" class="btn btn-primary btn-lg text-nowrap flex-shrink-0">
      <i class="fas fa-layer-group me-2"></i>Plan Boxes ↓
    </a>
  </div>

  {% elif _s3 == 1 %}
    {% if picking_phase and picking_phase.complete %}
    <div class="alert alert-success d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
      <div style="font-size:2rem;">✅</div>
      <div class="flex-grow-1"><strong>All {{ picking_phase.total_count }} items picked — close the open boxes to finish.</strong></div>
      {% if _open_boxes %}<a href="#coolerBoxes" class="btn btn-success btn-lg text-nowrap flex-shrink-0"><i class="fas fa-lock me-2"></i>Close Boxes ↓</a>{% endif %}
    </div>
    {% else %}
    <div class="alert alert-info d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
      <div style="font-size:2rem;">3️⃣</div>
      <div class="flex-grow-1">
        <strong>Assign a picker and start picking</strong><br>
        <span class="small">Each item shows which box it belongs to. The picker places items directly into the correct box.</span>
      </div>
      {% if cooler_session and cooler_session.is_locked %}
        {% if picking_phase and picking_phase.batch_status == 'Created' %}
        <a href="{{ url_for('batch.start_batch_picking', batch_id=cooler_session.id) }}" class="btn btn-info btn-lg text-nowrap flex-shrink-0">
          <i class="fas fa-warehouse me-2"></i>Start Picking
        </a>
        {% else %}
        <a href="{{ url_for('batch.batch_picking_item', batch_id=cooler_session.id) }}" class="btn btn-info btn-lg text-nowrap flex-shrink-0">
          <i class="fas fa-warehouse me-2"></i>Continue Picking
        </a>
        {% endif %}
      {% endif %}
    </div>
    {% endif %}

  {% elif _s3 == 2 %}
  <div class="alert alert-success d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
    <div style="font-size:2rem;">🎉</div>
    <div class="flex-grow-1"><strong>All done — cooler packing complete!</strong></div>
    {% if has_permission('cooler.print_labels') %}
    <a href="{{ url_for('cooler.route_manifest', route_id=route_id, delivery_date=delivery_date) }}"
       target="_blank" class="btn btn-outline-success btn-lg text-nowrap flex-shrink-0">
      <i class="fas fa-file-pdf me-2"></i>Print Route Manifest
    </a>
    {% endif %}
  </div>
  {% endif %}
```

---

## CHANGE 2 — Add Box column to the pick list table

Find the table header inside the "Ready to pick" section:

```html
      <thead class="table-light">
        <tr>
          <th>Stop</th><th>Customer</th><th>Invoice</th><th>Item</th>
          <th class="text-end">Qty</th><th>Status</th><th>Action</th>
        </tr>
      </thead>
```

Replace with:

```html
      <thead class="table-light">
        <tr>
          <th>Stop</th>
          <th>Customer</th>
          <th>Invoice</th>
          <th>Item</th>
          <th class="text-end">Qty</th>
          <th class="text-center"><i class="fas fa-box me-1 text-primary"></i>Box</th>
          <th>Status</th>
          <th>Action</th>
        </tr>
      </thead>
```

---

## CHANGE 3 — Add Box column to pending rows

Find the pending item row inside the same table (it starts with `{% for q in sequenced if q.status == 'pending' %}`).

Find this specific block inside that loop:

```jinja2
            <td class="text-end">{{ q.expected_qty|int }}</td>
            <td>
              {% if _planned_box_no %}
                <span class="badge bg-info text-dark"><i class="fas fa-box me-1"></i>Pre-assigned Box #{{ _planned_box_no }}</span>
              {% else %}
                <span class="badge bg-secondary">Pending</span>
              {% endif %}
            </td>
```

Replace with:

```jinja2
            <td class="text-end">{{ q.expected_qty|int }}</td>
            <td class="text-center">
              {% if _planned_box_no %}
                <span class="badge fs-6 px-3 py-2" style="background:#0d6efd;color:#fff;min-width:52px;">
                  📦 #{{ _planned_box_no }}
                </span>
              {% else %}
                <span class="text-muted small">—</span>
              {% endif %}
            </td>
            <td>
              {% if _planned_box_no %}
                <span class="badge bg-info text-dark">Pre-assigned</span>
              {% else %}
                <span class="badge bg-secondary">Pending</span>
              {% endif %}
            </td>
```

---

## CHANGE 4 — Add Box column to picked rows

Find the picked item row (starts with `{% for q in sequenced if q.status == 'picked' %}`).

Find:

```jinja2
            <td class="text-end">{{ q.expected_qty|int }}</td>
            <td><span class="badge bg-success">Picked</span></td>
            <td>
              {% if assigned_to_box and assigned_to_box.get(q.queue_item_id) %}
                <span class="badge bg-success"><i class="fas fa-box me-1"></i>Box #{{ assigned_to_box[q.queue_item_id] }}</span>
              {% else %}
                <span class="text-muted small"><i class="fas fa-clock me-1"></i>Unboxed</span>
              {% endif %}
            </td>
```

Replace with:

```jinja2
            <td class="text-end">{{ q.expected_qty|int }}</td>
            <td class="text-center">
              {% set _box_no = assigned_to_box.get(q.queue_item_id) if assigned_to_box else none %}
              {% if _box_no %}
                <span class="badge fs-6 px-3 py-2" style="background:#198754;color:#fff;min-width:52px;">
                  📦 #{{ _box_no }}
                </span>
              {% else %}
                <span class="badge bg-warning text-dark">Unboxed</span>
              {% endif %}
            </td>
            <td><span class="badge bg-success">✓ Picked</span></td>
            <td>
              {% set _box_no = assigned_to_box.get(q.queue_item_id) if assigned_to_box else none %}
              {% if _box_no %}
                <span class="text-success small"><i class="fas fa-check-circle me-1"></i>In box</span>
              {% else %}
                <span class="text-warning small"><i class="fas fa-exclamation-triangle me-1"></i>No box</span>
              {% endif %}
            </td>
```

---

## Summary of what these changes do

| Change | What it does |
|--------|-------------|
| 1 | Replaces tiny step circles with a full-width 3-colour progress bar (green=done, blue=active, grey=future) + a large "DO THIS NOW" action banner with one-click buttons |
| 2 | Adds a Box column header to the pick list |
| 3 | Shows a large blue **📦 #2** badge in the Box column for pending items pre-assigned to a box |
| 4 | Shows a large green **📦 #2** badge for items already picked into a box |
