# Cooler Picking — Group pick list by box

One change only, in `templates/cooler/route_picking.html`.

The current pick list shows all items in one flat table. Replace it so items are
**grouped by box** — the picker completes Box 1 entirely, then moves to Box 2, etc.
Each box gets its own card with a progress bar and a "Close Box" button that appears
automatically when all items in that box are picked.

---

## Find this exact block (lines ~500–627)

```
  {# ── Ready to pick ─────────────────────────────────────────────────────── #}
  {% set _sequenced_pending  = sequenced | selectattr('status','equalto','pending')   | list %}
  {% set _sequenced_picked   = sequenced | selectattr('status','equalto','picked')    | list %}
  {% set _sequenced_excepted = sequenced | selectattr('status','equalto','exception') | list %}

  <h5 class="mt-3">
    <i class="fas fa-list-ul text-primary me-1"></i>Pick List
    <span class="badge bg-secondary ms-1">{{ sequenced|length }}</span>
    {% if _sequenced_pending  %}<span class="badge bg-warning text-dark ms-1">{{ _sequenced_pending|length }} pending</span>{% endif %}
    {% if _sequenced_picked   %}<span class="badge bg-success ms-1">{{ _sequenced_picked|length }} picked</span>{% endif %}
    {% if _sequenced_excepted %}<span class="badge bg-danger ms-1">{{ _sequenced_excepted|length }} skipped/exception</span>{% endif %}
  </h5>

  {% if not sequenced %}
    <div class="alert alert-info">
      No items ready yet — click <strong>Confirm Cooler Route</strong> above to load the pick list.
    </div>
  {% else %}
    <div class="table-responsive mb-4">
    <table class="table table-sm align-middle">
      <thead class="table-light">
        <tr>
          <th>Stop</th>
          <th>Customer</th>
          <th>Invoice</th>
          <th>Item</th>
          <th class="text-end">Qty</th>
          <th class="text-center">
            <i class="fas fa-box me-1 text-primary"></i>Box
          </th>
          <th>Status</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {# ── Pending items ── #}
        {% for q in sequenced if q.status == 'pending' %}
          {% set _planned_box_no = planned_box.get(q.queue_item_id) if planned_box else none %}
          <tr class="{{ 'table-info' if _planned_box_no else '' }}">
            <td class="fw-semibold">{{ q.delivery_sequence|int }}</td>
            <td class="small">{{ q.customer_name or q.customer_code or '-' }}</td>
            <td class="small">{{ q.invoice_no }}</td>
            <td>
              <strong>{{ q.item_code }}</strong>
              <br><span class="text-muted small">{{ q.item_name or '' }}</span>
            </td>
            <td class="text-end">{{ q.expected_qty|int }}</td>
            <td class="text-center">
              {% if _planned_box_no %}
                <span class="badge fs-6 px-3 py-2"
                      style="background:#0d6efd;color:#fff;min-width:52px;">
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
            <td>
              {% if has_permission('cooler.pick') and not batch_in_progress %}
              <div class="d-flex gap-1 flex-nowrap">
                <form method="post" action="{{ url_for('cooler.queue_pick', queue_item_id=q.queue_item_id) }}" class="d-inline">
                  <input type="hidden" name="delivery_date" value="{{ delivery_date }}">
                  {% if _planned_box_no %}
                  <button type="submit" class="btn btn-sm btn-success text-nowrap">
                    <i class="fas fa-hand-rock me-1"></i>Pick → Box #{{ _planned_box_no }}
                  </button>
                  {% else %}
                  <button type="submit" class="btn btn-sm btn-primary">
                    <i class="fas fa-hand-rock me-1"></i>Pick
                  </button>
                  {% endif %}
                </form>
                <form method="post" action="{{ url_for('cooler.queue_skip', queue_item_id=q.queue_item_id) }}" class="d-inline">
                  <input type="hidden" name="_html_form" value="1">
                  <button type="submit" class="btn btn-sm btn-outline-secondary" title="Skip for now">
                    <i class="fas fa-forward"></i>
                  </button>
                </form>
              </div>
              {% elif batch_in_progress %}
                <span class="text-muted small"><i class="fas fa-boxes me-1"></i>via batch</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}

        {# ── Picked items ── #}
        {% for q in sequenced if q.status == 'picked' %}
          {% set _box_no = assigned_to_box.get(q.queue_item_id) if assigned_to_box else none %}
          <tr class="table-success">
            <td class="fw-semibold">{{ q.delivery_sequence|int }}</td>
            <td class="small">{{ q.customer_name or q.customer_code or '-' }}</td>
            <td class="small">{{ q.invoice_no }}</td>
            <td>
              <strong>{{ q.item_code }}</strong>
              <br><span class="text-muted small">{{ q.item_name or '' }}</span>
            </td>
            <td class="text-end">{{ q.expected_qty|int }}</td>
            <td class="text-center">
              {% if _box_no %}
                <span class="badge fs-6 px-3 py-2"
                      style="background:#198754;color:#fff;min-width:52px;">
                  📦 #{{ _box_no }}
                </span>
              {% else %}
                <span class="badge bg-warning text-dark">Unboxed</span>
              {% endif %}
            </td>
            <td><span class="badge bg-success">✓ Picked</span></td>
            <td>
              {% if _box_no %}
                <span class="text-success small"><i class="fas fa-check-circle me-1"></i>In box</span>
              {% else %}
                <span class="text-warning small"><i class="fas fa-exclamation-triangle me-1"></i>No box</span>
              {% endif %}
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    </div>
```

## Replace with this

```
  {# ── Pick List — grouped by box ──────────────────────────────────────── #}
  {% set _sequenced_pending  = sequenced | selectattr('status','equalto','pending')   | list %}
  {% set _sequenced_picked   = sequenced | selectattr('status','equalto','picked')    | list %}
  {% set _sequenced_excepted = sequenced | selectattr('status','equalto','exception') | list %}

  <div class="d-flex align-items-center justify-content-between mb-3 mt-3">
    <h5 class="mb-0">
      <i class="fas fa-list-ul text-primary me-1"></i>Pick List
      <span class="badge bg-secondary ms-1">{{ sequenced|length }}</span>
      {% if _sequenced_pending %}<span class="badge bg-warning text-dark ms-1">{{ _sequenced_pending|length }} to pick</span>{% endif %}
      {% if _sequenced_picked  %}<span class="badge bg-success ms-1">{{ _sequenced_picked|length }} picked</span>{% endif %}
      {% if _sequenced_excepted%}<span class="badge bg-danger ms-1">{{ _sequenced_excepted|length }} skipped</span>{% endif %}
    </h5>
  </div>

  {% if not sequenced %}
    <div class="alert alert-info">
      No items ready yet — click <strong>Confirm Cooler Route</strong> above to load the pick list.
    </div>
  {% else %}

  {# Collect distinct box numbers in ascending order #}
  {% set _bx = namespace(nos=[]) %}
  {% for q in sequenced %}
    {% set _bn = (planned_box.get(q.queue_item_id) if planned_box else none) or (assigned_to_box.get(q.queue_item_id) if assigned_to_box else none) %}
    {% if _bn is not none and _bn not in _bx.nos %}
      {% set _bx.nos = _bx.nos + [_bn] %}
    {% endif %}
  {% endfor %}

  {# One card per box #}
  {% for box_no in _bx.nos | sort %}
    {% set _bd = boxes | selectattr('box_no','equalto',box_no) | first | default(none) %}

    {# Count pending / picked for this box #}
    {% set _bc = namespace(pending=0, picked=0, total=0) %}
    {% for q in sequenced %}
      {% set _bn = (planned_box.get(q.queue_item_id) if planned_box else none) or (assigned_to_box.get(q.queue_item_id) if assigned_to_box else none) %}
      {% if _bn == box_no and q.status in ('pending','picked') %}
        {% set _bc.total   = _bc.total + 1 %}
        {% if q.status == 'pending' %}{% set _bc.pending = _bc.pending + 1 %}{% endif %}
        {% if q.status == 'picked'  %}{% set _bc.picked  = _bc.picked  + 1 %}{% endif %}
      {% endif %}
    {% endfor %}
    {% set _box_all_picked = (_bc.total > 0 and _bc.pending == 0) %}
    {% set _box_status = _bd.status if _bd else 'planned' %}

    <div class="card mb-4 shadow-sm"
         style="border:2px solid {{ '#198754' if _box_all_picked else '#0d6efd' }};">

      {# Box header #}
      <div class="card-header d-flex align-items-center justify-content-between flex-wrap gap-2 py-2"
           style="background:{{ '#198754' if _box_all_picked else '#0d6efd' }};color:#fff;">
        <div class="d-flex align-items-center gap-3 flex-wrap">
          <span class="fw-bold" style="font-size:1.1rem;">📦 Box #{{ box_no }}</span>
          {% if _bd and _bd.box_type_name %}
            <span class="badge bg-white {{ 'text-success' if _box_all_picked else 'text-primary' }}">{{ _bd.box_type_name }}</span>
          {% endif %}
          {% if _bd and _bd.first_stop_sequence is not none and _bd.last_stop_sequence is not none %}
            <span class="opacity-75 small">
              {% if _bd.first_stop_sequence == _bd.last_stop_sequence %}Stop {{ _bd.last_stop_sequence|int }}
              {% else %}Stops {{ _bd.last_stop_sequence|int }} → {{ _bd.first_stop_sequence|int }}{% endif %}
            </span>
          {% endif %}
        </div>
        <div class="d-flex align-items-center gap-2 flex-wrap">
          <span class="badge bg-white {{ 'text-success fw-bold' if _box_all_picked else 'text-primary' }}">
            {% if _box_all_picked %}✓ All {{ _bc.total }} picked
            {% else %}{{ _bc.picked }} / {{ _bc.total }} picked{% endif %}
          </span>
          {% if _bc.total > 0 %}
          <div class="rounded" style="width:90px;height:8px;background:rgba(255,255,255,0.3);overflow:hidden;">
            <div style="height:100%;width:{{ ((_bc.picked / _bc.total) * 100)|int }}%;background:#fff;transition:width 0.4s;"></div>
          </div>
          {% endif %}
          {% if _bd and has_permission('cooler.print_labels') %}
          <a href="{{ url_for('cooler.box_manifest', box_id=_bd.id) }}" target="_blank"
             class="btn btn-sm btn-light {{ 'text-success' if _box_all_picked else 'text-primary' }}">
            <i class="fas fa-print me-1"></i>Manifest
          </a>
          {% endif %}
          {% if _box_all_picked and _bd and _box_status == 'open' and has_permission('cooler.manage_boxes') %}
          <form method="post" action="{{ url_for('cooler.box_close', box_id=_bd.id) }}" class="d-inline">
            <input type="hidden" name="_html_form" value="1">
            <button type="submit" class="btn btn-sm btn-light text-success fw-bold">
              <i class="fas fa-lock me-1"></i>Close Box #{{ box_no }}
            </button>
          </form>
          {% elif _bd and _box_status == 'closed' %}
          <span class="badge bg-white text-success fs-6 px-2"><i class="fas fa-lock me-1"></i>Closed</span>
          {% endif %}
        </div>
      </div>

      {# Items for this box #}
      <div class="table-responsive">
        <table class="table table-sm align-middle mb-0">
          <thead class="table-light">
            <tr>
              <th>Stop</th><th>Customer</th><th>Invoice</th>
              <th>Item</th><th class="text-end">Qty</th>
              <th>Status</th><th>Action</th>
            </tr>
          </thead>
          <tbody>
            {% for q in sequenced %}
              {% set _bn = (planned_box.get(q.queue_item_id) if planned_box else none) or (assigned_to_box.get(q.queue_item_id) if assigned_to_box else none) %}
              {% if _bn == box_no and q.status in ('pending','picked') %}
              <tr class="{{ 'table-success' if q.status == 'picked' else '' }}">
                <td class="fw-semibold">{{ q.delivery_sequence|int if q.delivery_sequence else '—' }}</td>
                <td class="small">{{ q.customer_name or q.customer_code or '-' }}</td>
                <td class="small">{{ q.invoice_no }}</td>
                <td>
                  <strong>{{ q.item_code }}</strong>
                  <br><span class="text-muted small">{{ q.item_name or '' }}</span>
                </td>
                <td class="text-end">{{ q.expected_qty|int }}</td>
                <td>
                  {% if q.status == 'picked' %}<span class="badge bg-success">✓ Picked</span>
                  {% else %}<span class="badge bg-secondary">Pending</span>{% endif %}
                </td>
                <td>
                  {% if q.status == 'pending' %}
                    {% if has_permission('cooler.pick') and not batch_in_progress %}
                    <div class="d-flex gap-1 flex-nowrap">
                      <form method="post" action="{{ url_for('cooler.queue_pick', queue_item_id=q.queue_item_id) }}" class="d-inline">
                        <input type="hidden" name="delivery_date" value="{{ delivery_date }}">
                        <button type="submit" class="btn btn-sm btn-success text-nowrap">
                          <i class="fas fa-hand-rock me-1"></i>Pick → Box #{{ box_no }}
                        </button>
                      </form>
                      <form method="post" action="{{ url_for('cooler.queue_skip', queue_item_id=q.queue_item_id) }}" class="d-inline">
                        <input type="hidden" name="_html_form" value="1">
                        <button type="submit" class="btn btn-sm btn-outline-secondary" title="Skip for now"><i class="fas fa-forward"></i></button>
                      </form>
                    </div>
                    {% elif batch_in_progress %}
                      <span class="text-muted small"><i class="fas fa-boxes me-1"></i>via batch</span>
                    {% endif %}
                  {% else %}
                    <span class="text-success small"><i class="fas fa-check-circle me-1"></i>Done</span>
                  {% endif %}
                </td>
              </tr>
              {% endif %}
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
  {% endfor %}

  {# Items with no box assigned #}
  {% set _ub = namespace(found=false) %}
  {% for q in sequenced if q.status in ('pending','picked') %}
    {% set _bn = (planned_box.get(q.queue_item_id) if planned_box else none) or (assigned_to_box.get(q.queue_item_id) if assigned_to_box else none) %}
    {% if not _bn %}{% set _ub.found = true %}{% endif %}
  {% endfor %}
  {% if _ub.found %}
  <div class="card mb-4 border-warning shadow-sm">
    <div class="card-header bg-warning text-dark fw-bold py-2">
      <i class="fas fa-exclamation-triangle me-2"></i>Items not yet assigned to a box
      <span class="fw-normal small ms-2">— complete Step 2 (Plan Boxes) to assign these</span>
    </div>
    <div class="table-responsive">
      <table class="table table-sm align-middle mb-0">
        <thead class="table-light">
          <tr><th>Stop</th><th>Customer</th><th>Invoice</th><th>Item</th><th class="text-end">Qty</th><th>Status</th><th>Action</th></tr>
        </thead>
        <tbody>
          {% for q in sequenced if q.status in ('pending','picked') %}
            {% set _bn = (planned_box.get(q.queue_item_id) if planned_box else none) or (assigned_to_box.get(q.queue_item_id) if assigned_to_box else none) %}
            {% if not _bn %}
            <tr>
              <td>{{ q.delivery_sequence|int if q.delivery_sequence else '—' }}</td>
              <td class="small">{{ q.customer_name or q.customer_code or '-' }}</td>
              <td class="small">{{ q.invoice_no }}</td>
              <td><strong>{{ q.item_code }}</strong><br><span class="text-muted small">{{ q.item_name or '' }}</span></td>
              <td class="text-end">{{ q.expected_qty|int }}</td>
              <td>{% if q.status=='picked' %}<span class="badge bg-success">Picked</span>{% else %}<span class="badge bg-secondary">Pending</span>{% endif %}</td>
              <td>
                {% if q.status == 'pending' and has_permission('cooler.pick') and not batch_in_progress %}
                <form method="post" action="{{ url_for('cooler.queue_pick', queue_item_id=q.queue_item_id) }}" class="d-inline">
                  <input type="hidden" name="delivery_date" value="{{ delivery_date }}">
                  <button type="submit" class="btn btn-sm btn-primary"><i class="fas fa-hand-rock me-1"></i>Pick</button>
                </form>
                {% elif batch_in_progress %}<span class="text-muted small">via batch</span>
                {% endif %}
              </td>
            </tr>
            {% endif %}
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endif %}
```

---

## What this does

The picker now sees one card per box instead of one flat table:

```
┌─────────────────────────────────────────────────────────────┐
│ 📦 Box #1  Large  Stops 10→8        0/3 picked  [Manifest] │  ← blue header
├─────────────────────────────────────────────────────────────┤
│ Stop │ Customer    │ Item   │ Qty │ Status  │ Action        │
│  10  │ Coffee Shop │ CHO-14 │  1  │ Pending │ Pick→Box #1  │
│  10  │ Coffee Shop │ CHO-25 │  2  │ Pending │ Pick→Box #1  │
│   8  │ Petrolina   │ CHO-66 │  1  │ Pending │ Pick→Box #1  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 📦 Box #2  Medium  Stops 5→1        0/4 picked  [Manifest] │
├─────────────────────────────────────────────────────────────┤
│   5  │ Annie Belle │ CHO-27 │  1  │ Pending │ Pick→Box #2  │
│   3  │ GMK Kiosk   │ CHO-11 │  1  │ Pending │ Pick→Box #2  │
│   1  │ CoffeeSense │ CHO-14 │  1  │ Pending │ Pick→Box #2  │
└─────────────────────────────────────────────────────────────┘
```

When all items in a box are picked, the header turns **green** and a **"Close Box #1"** button appears automatically in the header. The picker closes it and moves to the next box.

Each box header also has a **Print Manifest** button for on-the-spot label printing.
