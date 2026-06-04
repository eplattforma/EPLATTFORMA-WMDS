# Cooler Route — Remove workflow cards, add status badge + volume KPI

Three replacements in `templates/cooler/route_picking.html`. Do them in order.

---

## Replacement 1 — Add status badge to page header

### Find this exact block (lines ~8–27)

```
  {# ── Page header ───────────────────────────────────────────────────────── #}
  <div class="d-flex align-items-start justify-content-between mb-3 flex-wrap gap-2">
    <div>
      <h2 class="mb-1">
        <i class="fas fa-snowflake me-2 text-info"></i>
        {% if picking_phase and picking_phase.complete %}
          ❄️ Cooler Packing — Route {{ route_id }}{% if route_name %} / {{ route_name }}{% endif %}
        {% else %}
          Cooler Route {{ route_id }}{% if route_name %} — {{ route_name }}{% endif %}
        {% endif %}
      </h2>
      <p class="text-muted mb-0 small">
        {% if route_driver %}<i class="fas fa-user me-1"></i><strong>Driver:</strong> {{ route_driver }}{% if delivery_date %} &nbsp;|&nbsp; {% endif %}{% endif %}
        {% if delivery_date %}<i class="fas fa-calendar me-1"></i><strong>Delivery date:</strong> {{ delivery_date }}{% endif %}
      </p>
    </div>
    <a href="{{ url_for('admin_dashboard') }}" class="btn btn-outline-secondary btn-sm align-self-start">
      <i class="fas fa-arrow-left me-1"></i>Dashboard
    </a>
  </div>
```

### Replace with this

```
  {# ── Page header ───────────────────────────────────────────────────────── #}
  <div class="d-flex align-items-start justify-content-between mb-3 flex-wrap gap-2">
    <div>
      <h2 class="mb-1">
        <i class="fas fa-snowflake me-2 text-info"></i>
        ❄️ Cooler Packing — Route {{ route_id }}{% if route_name %} / {{ route_name }}{% endif %}
      </h2>
      <p class="text-muted mb-0 small">
        {% if route_driver %}<i class="fas fa-user me-1"></i><strong>Driver:</strong> {{ route_driver }}{% if delivery_date %} &nbsp;|&nbsp; {% endif %}{% endif %}
        {% if delivery_date %}<i class="fas fa-calendar me-1"></i><strong>Delivery date:</strong> {{ delivery_date }}{% endif %}
      </p>
    </div>
    <a href="{{ url_for('admin_dashboard') }}" class="btn btn-outline-secondary btn-sm align-self-start">
      <i class="fas fa-arrow-left me-1"></i>Dashboard
    </a>
  </div>
```

*(No change yet — the status badge is added in Replacement 2 below, after the step calculations are in scope.)*

---

## Replacement 2 — Remove 3-step workflow cards, add status badge + volume calc

### Find this exact block (lines ~29–287)

Find from:
```
  {# ── Step state calculations ─────────────────────────────────────────── #}
  {% set _is_locked    = cooler_session and cooler_session.is_locked %}
```

All the way through to the end of the summary stat cards section:
```
  {% endif %}

  {# ── Contextual next-action banner ────────────────────────────────────── #}
```

This covers:
- The step state calculations (`_is_locked`, `_boxes_exist`, etc.)
- The `_s1`/`_s2`/`_s3` variables
- The entire 3-step progress bar `<div class="mb-4">…</div>`
- The "What to do now" action banners (`{% if _s1 == 1 %}` … `{% endif %}`)
- The summary stat cards (`{# ── Summary stat cards … #}` … `{% endif %}`)

### Replace with this entire block

```
  {# ── Step state calculations ─────────────────────────────────────────── #}
  {% set _is_locked    = cooler_session and cooler_session.is_locked %}
  {% set _boxes_exist  = boxes | length > 0 %}
  {% set _open_boxes   = boxes | selectattr('status', 'equalto', 'open')   | list %}
  {% set _closed_boxes = boxes | selectattr('status', 'equalto', 'closed') | list %}
  {% set _picking_done = picking_phase and picking_phase.complete %}
  {% set _all_closed   = (_open_boxes | length == 0 and _closed_boxes | length > 0) %}
  {% set _any_overfull = boxes | selectattr('estimated_fill_pct') | selectattr('estimated_fill_pct', 'greaterthan', 100) | list | length > 0 %}

  {# Step states (kept for action banners below) #}
  {% set _s1 = 2 if _is_locked else 1 %}
  {% set _s2 = 2 if (_boxes_exist and not _any_overfull) else (1 if _is_locked else 0) %}
  {% set _s3 = 2 if (_picking_done and _all_closed) else (1 if (_s2 == 2) else 0) %}

  {# ── Status badge ─────────────────────────────────────────────────────── #}
  {# Derive a single stage label from step states #}
  {% if _s3 == 2 %}
    {% set _stage_label = 'Complete' %}
    {% set _stage_color = '#198754' %}
    {% set _stage_icon  = 'fa-check-circle' %}
  {% elif _picking_done and _open_boxes %}
    {% set _stage_label = 'Packing' %}
    {% set _stage_color = '#0d6efd' %}
    {% set _stage_icon  = 'fa-box' %}
  {% elif _s2 == 2 %}
    {% set _stage_label = 'Picking' %}
    {% set _stage_color = '#0d6efd' %}
    {% set _stage_icon  = 'fa-hand-rock' %}
  {% elif _s1 == 2 %}
    {% set _stage_label = 'Planning' %}
    {% set _stage_color = '#fd7e14' %}
    {% set _stage_icon  = 'fa-layer-group' %}
  {% else %}
    {% set _stage_label = 'Sequencing' %}
    {% set _stage_color = '#fd7e14' %}
    {% set _stage_icon  = 'fa-shield-alt' %}
  {% endif %}

  <div class="mb-3">
    <span class="badge px-3 py-2 fs-6"
          style="background:{{ _stage_color }};color:#fff;border-radius:20px;">
      <i class="fas {{ _stage_icon }} me-2"></i>{{ _stage_label }}
    </span>
  </div>

  {# ── Volume totals across all boxes (falls back to pre-pick estimate) ─── #}
  {% set _vt = namespace(l=0.0, kg=0.0, has_data=false) %}
  {% for b in boxes %}
    {% if b.fill_cm3 and b.fill_cm3 > 0 %}
      {% set _vt.l = _vt.l + b.fill_cm3 / 1000 %}
      {% set _vt.has_data = true %}
    {% endif %}
    {% if b.fill_weight_kg and b.fill_weight_kg > 0 %}
      {% set _vt.kg = _vt.kg + b.fill_weight_kg %}
    {% endif %}
  {% endfor %}
  {# Fall back to the pre-pick estimate when boxes have no fill data yet #}
  {% if not _vt.has_data and estimate %}
    {% set _vol_l  = estimate.total_volume_l %}
    {% set _vol_kg = estimate.total_weight_kg %}
    {% set _vol_est = true %}
  {% else %}
    {% set _vol_l  = (_vt.l  | round(1)) %}
    {% set _vol_kg = (_vt.kg | round(1)) %}
    {% set _vol_est = false %}
  {% endif %}

  {# ── KPI cards ────────────────────────────────────────────────────────── #}
  <div class="row g-3 mb-4">

    {# Picked #}
    <div class="col-6 col-md">
      <div class="card border-0 shadow-sm text-center h-100"
           style="background:{% if picking_phase and picking_phase.picked_count > 0 %}#198754{% else %}#6c757d{% endif %};color:#fff;">
        <div class="card-body py-3">
          <div class="fs-3 fw-bold">
            {% if picking_phase %}{{ picking_phase.picked_count }} / {{ picking_phase.total_count }}{% else %}—{% endif %}
          </div>
          <div class="small opacity-75 mt-1">Picked</div>
        </div>
      </div>
    </div>

    {# Unboxed #}
    <div class="col-6 col-md">
      <div class="card border-0 shadow-sm text-center h-100"
           style="background:{{ '#fd7e14' if picked_unboxed_count > 0 else '#6c757d' }};color:#fff;">
        <div class="card-body py-3">
          <div class="fs-3 fw-bold">{{ picked_unboxed_count }}</div>
          <div class="small opacity-75 mt-1">Unboxed</div>
        </div>
      </div>
    </div>

    {# Open Boxes #}
    <div class="col-6 col-md">
      <div class="card border-0 shadow-sm text-center h-100"
           style="background:{{ '#0d6efd' if _open_boxes else '#6c757d' }};color:#fff;">
        <div class="card-body py-3">
          <div class="fs-3 fw-bold">{{ _open_boxes | length }}</div>
          <div class="small opacity-75 mt-1">Open Boxes</div>
        </div>
      </div>
    </div>

    {# Closed Boxes #}
    <div class="col-6 col-md">
      <div class="card border-0 shadow-sm text-center h-100"
           style="background:{{ '#198754' if _closed_boxes else '#6c757d' }};color:#fff;">
        <div class="card-body py-3">
          <div class="fs-3 fw-bold">{{ _closed_boxes | length }}</div>
          <div class="small opacity-75 mt-1">Closed Boxes</div>
        </div>
      </div>
    </div>

    {# Volume #}
    <div class="col-12 col-md">
      <div class="card border-0 shadow-sm text-center h-100"
           style="background:#0dcaf0;color:#000;">
        <div class="card-body py-3">
          {% if _vol_l > 0 or _vol_kg > 0 %}
            <div class="fs-3 fw-bold">{{ _vol_l }}L</div>
            <div class="small mt-1">
              {{ _vol_kg }} kg
              {% if _vol_est %}<span class="opacity-60 ms-1">(est.)</span>{% endif %}
            </div>
          {% else %}
            <div class="fs-3 fw-bold text-muted">—</div>
            <div class="small mt-1 text-muted">No data yet</div>
          {% endif %}
          <div class="small opacity-75 mt-1">Volume</div>
        </div>
      </div>
    </div>

  </div>

  {# ── "What to do now" action banner ───────────────────────────────────── #}
  {% if _s1 == 1 %}
  <div class="alert alert-primary d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
    <div style="font-size:2rem;">1️⃣</div>
    <div class="flex-grow-1">
      <strong>Start here — Confirm Cooler Route</strong><br>
      <span class="small">This locks the delivery sequence so items can be sorted into boxes in the correct order.</span>
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
      <span class="small">Set how many boxes you have available, then click "Get Recommendation". The system will suggest the best box arrangement. You can adjust before confirming.</span>
    </div>
    <a href="#boxPlanCard" class="btn btn-primary btn-lg text-nowrap flex-shrink-0">
      <i class="fas fa-layer-group me-2"></i>Plan Boxes ↓
    </a>
  </div>

  {% elif _s3 == 1 %}
    {% if picking_phase and picking_phase.complete %}
    <div class="alert alert-success d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
      <div style="font-size:2rem;">✅</div>
      <div class="flex-grow-1">
        <strong>All {{ picking_phase.total_count }} items picked — close the open boxes to finish.</strong>
      </div>
      {% if _open_boxes %}
      <a href="#coolerBoxes" class="btn btn-success btn-lg text-nowrap flex-shrink-0">
        <i class="fas fa-lock me-2"></i>Close Boxes ↓
      </a>
      {% endif %}
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
        <a href="{{ url_for('batch.start_batch_picking', batch_id=cooler_session.id) }}"
           class="btn btn-info btn-lg text-nowrap flex-shrink-0">
          <i class="fas fa-warehouse me-2"></i>Start Picking
        </a>
        {% elif cooler_session %}
        <a href="{{ url_for('batch.batch_picking_item', batch_id=cooler_session.id) }}"
           class="btn btn-info btn-lg text-nowrap flex-shrink-0">
          <i class="fas fa-warehouse me-2"></i>Continue Picking
        </a>
        {% endif %}
      {% endif %}
    </div>
    {% endif %}

  {% elif _s3 == 2 %}
  <div class="alert alert-success d-flex align-items-center gap-3 mb-4 border-0 shadow-sm">
    <div style="font-size:2rem;">🎉</div>
    <div class="flex-grow-1">
      <strong>All done — cooler packing complete!</strong>
      All items picked and all boxes closed.
    </div>
    {% if has_permission('cooler.print_labels') %}
    <a href="{{ url_for('cooler.route_manifest', route_id=route_id, delivery_date=delivery_date) }}"
       target="_blank" class="btn btn-outline-success btn-lg text-nowrap flex-shrink-0">
      <i class="fas fa-file-pdf me-2"></i>Print Route Manifest
    </a>
    {% endif %}
  </div>
  {% endif %}

  {# ── Contextual next-action banner ────────────────────────────────────── #}
```

---

## What this does

**Removed:** The 3-step green/blue workflow card strip (Confirm Route → Plan Boxes → Assign Picker).

**Added above the action banner:**

1. **Status badge** — single pill showing current stage:
   - 🟠 Sequencing — route not yet confirmed
   - 🟠 Planning — route confirmed, no boxes yet
   - 🔵 Picking — boxes planned, items being picked
   - 🔵 Packing — all picked, closing boxes
   - 🟢 Complete — all done

2. **5 KPI cards** always visible:
   - Picked (e.g. 23/24)
   - Unboxed
   - Open Boxes
   - Closed Boxes
   - Volume — shows total litres + kg summed across all boxes; falls back to pre-pick estimate (labelled "est.") when boxes have no fill data yet

The action banners (Confirm Route / Plan Boxes / Start Picking / etc.) are **unchanged** — they still drive the buttons at each stage.
