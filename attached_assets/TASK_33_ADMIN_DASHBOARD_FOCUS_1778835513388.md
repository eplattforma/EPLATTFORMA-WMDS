# Task #33 — Admin Dashboard Focus View

## What & Why

The admin dashboard (`/admin/dashboard`) currently has one layout —
a dense, flat table with 14 columns covering all invoices, routes,
batches and pickers in one undifferentiated view ("Classic").

This task adds a second layout ("Focus") that presents the same data
as a **control room** — one card per route, collapsible invoice table
per route, KPI pulse bar at the top, and clear cooler + route batch
status per route card.

Both layouts render from identical backend data. The toggle persists in
`localStorage`. No business logic changes. No risk to existing functionality.

## Done Looks Like

- A **Classic / Focus** toggle appears in the dashboard header
- Classic shows the existing layout byte-for-byte unchanged
- Focus shows:
  - 5 KPI cells (Not Started / In Progress / Ready / Cooler Active / Time Remaining)
  - Active pickers strip
  - One collapsible card per route with inline invoice table
  - Cooler progress + direct cooler screen link on each route card header
  - Route batch status badge on each route card (when Task #32 is active)
  - Unassigned invoices as a separate collapsible card
  - Active batches as a compact table at the bottom
- Toggle choice persists in `localStorage` across page reloads
- All existing modals (assign picker, quick view, assign to route, etc.)
  work from both views without change

## Backend Changes

### Add `routes_data` to `routes.py:admin_dashboard()`

After the existing `sorted_route_ids` loop, add:

```python
# Build enriched per-route data for Focus view
routes_data = []
for route_id in sorted_route_ids:
    route = shipment_cache.get(route_id)
    if not route:
        continue
    route_invoices = route_groups[route_id]

    # Cooler session for this route
    cooler_session = next(
        (s for s in open_batch_sessions
         if getattr(s, 'session_type', None) == 'cooler_route'
         and s.route_id == route_id),
        None
    )
    cooler_counts = batch_session_item_counts.get(
        cooler_session.id, {}
    ) if cooler_session else {}

    # Route batch session (Task #32 — safe to include even if not built yet)
    route_batch = next(
        (s for s in open_batch_sessions
         if getattr(s, 'session_type', None) == 'route_batch'
         and s.route_id == route_id),
        None
    )

    inv_data = []
    for inv in route_invoices:
        inv_data.append({
            'invoice': inv,
            'picked_lines': picked_lines_count.get(inv.invoice_no, 0),
            'total_lines': total_lines_count.get(inv.invoice_no, 0),
            'has_cooler': inv.invoice_no in cooler_invoice_nos,
            'exceptions': invoice_exceptions.get(inv.invoice_no, 0),
            'stop_seq': stop_sequences.get(inv.invoice_no),
        })

    routes_data.append({
        'route': route,
        'invoices': inv_data,
        'total_orders': len(route_invoices),
        'ready_count': sum(1 for i in route_invoices
                           if i.status == 'ready_for_dispatch'),
        'not_started_count': sum(1 for i in route_invoices
                                 if i.status == 'not_started'),
        'in_progress_count': sum(1 for i in route_invoices
                                 if i.status in [
                                    'picking', 'awaiting_batch_items',
                                    'awaiting_packing']),
        'total_weight': sum(i.total_weight or 0 for i in route_invoices),
        'cooler_session': cooler_session,
        'cooler_picked': cooler_counts.get('picked', 0),
        'cooler_total': cooler_counts.get('total', 0),
        'cooler_date': route_date_map.get(route_id),
        'route_batch': route_batch,
    })
```

Add `routes_data=routes_data` to `render_template(...)`.

## Template Changes (`templates/admin_dashboard.html`)

### Step 1 — Toggle in header

Insert at the very top of `{% block content %}`, before any existing HTML:

```html
<div class="d-flex justify-content-between align-items-center mb-3">
  <h4 class="mb-0">
    <i class="fas fa-warehouse me-2 text-primary"></i>Picking Dashboard
  </h4>
  <div class="btn-group" role="group">
    <button type="button" id="btn-view-classic"
            class="btn btn-sm btn-secondary"
            onclick="setDashboardView('classic')">
      <i class="fas fa-table me-1"></i>Classic
    </button>
    <button type="button" id="btn-view-focus"
            class="btn btn-sm btn-outline-primary"
            onclick="setDashboardView('focus')">
      <i class="fas fa-bullseye me-1"></i>Focus
    </button>
  </div>
</div>
```

### Step 2 — Wrap existing content

Wrap everything that currently exists in `{% block content %}` (from
the `<style>` block down to just before `{% endblock %}`) in:

```html
<div id="view-classic">
  <!-- ALL EXISTING CONTENT — NOT ONE LINE CHANGED -->
</div>
```

### Step 3 — Focus view (add immediately after `</div>` of view-classic)

```html
<div id="view-focus" style="display:none;">

  <!-- PULSE BAR -->
  <div class="row g-2 mb-3">
    <div class="col-6 col-md">
      <div class="card text-center h-100 border-secondary">
        <div class="card-body py-2">
          <div class="text-muted small">Not Started</div>
          <div class="display-6 fw-bold text-secondary">
            {{ invoices|selectattr('status','eq','not_started')|list|length }}
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md">
      <div class="card text-center h-100 border-warning">
        <div class="card-body py-2">
          <div class="text-muted small">In Progress</div>
          <div class="display-6 fw-bold text-warning">
            {{ invoices|selectattr('status','in',
               ['picking','awaiting_batch_items','awaiting_packing'])
               |list|length }}
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md">
      <div class="card text-center h-100 border-success">
        <div class="card-body py-2">
          <div class="text-muted small">Ready</div>
          <div class="display-6 fw-bold text-success">
            {{ invoices|selectattr('status','eq','ready_for_dispatch')
               |list|length }}
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md">
      <div class="card text-center h-100 border-info">
        <div class="card-body py-2">
          <div class="text-muted small">❄️ Cooler</div>
          <div class="display-6 fw-bold text-info">
            {{ open_batch_sessions
               |selectattr('session_type','eq','cooler_route')
               |list|length }}
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md">
      <div class="card text-center h-100 border-primary">
        <div class="card-body py-2">
          <div class="text-muted small">Remaining</div>
          <div class="display-6 fw-bold text-primary">
            {{ total_remaining_time|round|int }}m
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ACTIVE PICKERS STRIP -->
  {% if active_pickers_data %}
  <div class="d-flex flex-wrap gap-2 mb-3 align-items-center">
    <span class="text-muted small">
      <i class="fas fa-user-check me-1"></i>Picking now:
    </span>
    {% for p in active_pickers_data %}
    <span class="badge px-2 py-1
          {{ 'bg-warning text-dark' if p.on_break else 'bg-success' }}">
      {{ p.username }}
      {% if p.on_break %}<i class="fas fa-pause ms-1"></i>{% endif %}
      <span class="opacity-75 ms-1 small">{{ p.elapsed_minutes }}m</span>
    </span>
    {% endfor %}
  </div>
  {% endif %}

  <!-- ROUTE CARDS -->
  {% for rd in routes_data %}
  {% set route = rd.route %}
  <div class="card mb-3 shadow-sm"
       style="border-left:4px solid #1a237e;">

    <!-- Route header -->
    <div class="card-header d-flex align-items-center flex-wrap gap-2 py-2"
         style="background:#1a237e;color:#fff;cursor:pointer;"
         data-bs-toggle="collapse"
         data-bs-target="#focus-route-{{ route.id }}">
      <span class="fw-bold">
        <i class="fas fa-truck me-1"></i>#{{ route.id }}
      </span>
      <span>{{ route.driver_name }}</span>
      <span class="opacity-75 small">
        {{ route.delivery_date.strftime('%d/%m/%Y')
           if route.delivery_date }}
      </span>
      {% if route.route_name %}
      <span class="badge bg-light text-dark">{{ route.route_name }}</span>
      {% endif %}
      <span class="badge
            {{ 'bg-success' if route.status == 'PLANNED'
               else 'bg-warning text-dark' }}">
        {{ route.status }}
      </span>

      <!-- Order progress -->
      <div class="d-flex gap-1 ms-auto flex-wrap">
        {% if rd.ready_count %}
        <span class="badge bg-success">{{ rd.ready_count }} ready</span>
        {% endif %}
        {% if rd.in_progress_count %}
        <span class="badge bg-warning text-dark">
          {{ rd.in_progress_count }} picking
        </span>
        {% endif %}
        {% if rd.not_started_count %}
        <span class="badge bg-secondary">
          {{ rd.not_started_count }} pending
        </span>
        {% endif %}
        <span class="badge bg-light text-dark">
          {{ rd.total_weight|round|int }} kg
        </span>
      </div>

      <!-- Cooler pill -->
      {% if rd.cooler_session %}
      <div class="d-flex align-items-center gap-1"
           onclick="event.stopPropagation();">
        <span class="badge bg-info text-dark">
          <i class="fas fa-snowflake me-1"></i>
          {{ rd.cooler_picked }}/{{ rd.cooler_total }}
        </span>
        {% if rd.cooler_date %}
        <a href="{{ url_for('cooler.route_picking',
                             route_id=route.id,
                             delivery_date=rd.cooler_date) }}"
           class="btn btn-sm btn-info py-0 px-2"
           title="Open cooler screen">
          <i class="fas fa-snowflake"></i>
        </a>
        {% endif %}
      </div>
      {% endif %}

      <!-- Route batch pill (Task #32) -->
      {% if rd.route_batch %}
      <span class="badge bg-primary" title="Route batch active">
        <i class="fas fa-layer-group me-1"></i>Batch
        {{ '🔒' if rd.route_batch.sequence_locked_at else '🔓' }}
      </span>
      {% endif %}

      <i class="fas fa-chevron-down text-white-50 ms-1"></i>
    </div>

    <!-- Invoice table -->
    <div class="collapse show" id="focus-route-{{ route.id }}">
      <table class="table table-sm table-hover mb-0">
        <thead class="table-light">
          <tr>
            <th style="width:50px;">Stop</th>
            <th>Invoice</th>
            <th>Customer</th>
            <th>Status</th>
            <th class="text-center">Lines</th>
            <th class="text-center">Wt</th>
            <th>Assigned</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {% for id in rd.invoices %}
          {% set inv = id.invoice %}
          <tr>
            <td class="text-center">
              {% if id.stop_seq %}
              <span class="badge bg-primary">{{ id.stop_seq }}</span>
              {% else %}—{% endif %}
            </td>
            <td>
              <strong>{{ inv.invoice_no }}</strong>
              {% if id.has_cooler %}
              <i class="fas fa-snowflake text-info ms-1"></i>
              {% endif %}
              {% if id.exceptions > 0 %}
              <span class="badge bg-danger ms-1">{{ id.exceptions }}</span>
              {% endif %}
            </td>
            <td>
              <span class="text-truncate d-inline-block"
                    style="max-width:160px;"
                    title="{{ inv.customer_name }}">
                {{ inv.customer_name }}
              </span>
            </td>
            <td>
              {% if inv.status == 'ready_for_dispatch' %}
              <span class="badge bg-success">Ready</span>
              {% elif inv.status == 'picking' %}
              <span class="badge bg-warning text-dark">
                {{ id.picked_lines }}/{{ id.total_lines }}
              </span>
              {% elif inv.status == 'not_started' %}
              <span class="badge bg-secondary">—</span>
              {% elif inv.status in ['awaiting_batch_items','awaiting_packing'] %}
              <span class="badge"
                    style="background:#fd7e14;color:#fff;">
                Awaiting
              </span>
              {% else %}
              <span class="badge bg-secondary">
                {{ inv.status|title }}
              </span>
              {% endif %}
            </td>
            <td class="text-center">
              {{ id.picked_lines }}/{{ id.total_lines }}
            </td>
            <td class="text-center">
              {{ (inv.total_weight or 0)|round(1) }}
            </td>
            <td>
              {% if inv.assigned_to %}
              <span class="badge bg-success">{{ inv.assigned_to }}</span>
              {% else %}—{% endif %}
            </td>
            <td>
              <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-primary"
                        data-bs-toggle="modal"
                        data-bs-target="#assignPickerModal"
                        data-invoice="{{ inv.invoice_no }}"
                        title="Assign picker">
                  <i class="fas fa-user-plus"></i>
                </button>
                <button class="btn btn-outline-secondary"
                        onclick="showSingleOrderQuickView('{{ inv.invoice_no }}')"
                        title="Quick view">
                  <i class="fas fa-eye"></i>
                </button>
              </div>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
  {% endfor %}

  <!-- UNASSIGNED -->
  {% if unassigned_invoices is defined and unassigned_invoices %}
  <div class="card mb-3 border-secondary shadow-sm">
    <div class="card-header d-flex align-items-center gap-2 py-2
                bg-secondary text-white"
         data-bs-toggle="collapse"
         data-bs-target="#focus-unassigned"
         style="cursor:pointer;">
      <i class="fas fa-inbox me-1"></i>
      <strong>Unassigned</strong>
      <span class="badge bg-light text-dark ms-1">
        {{ unassigned_invoices|length }}
      </span>
    </div>
    <div class="collapse show" id="focus-unassigned">
      <div class="card-body p-2 text-muted small">
        Use Classic view or the Route management page to assign
        these invoices to a route.
      </div>
    </div>
  </div>
  {% endif %}

  <!-- ACTIVE BATCHES -->
  {% if open_batch_sessions %}
  <div class="card mb-3 border-dark shadow-sm">
    <div class="card-header d-flex align-items-center gap-2 py-2
                bg-dark text-white">
      <i class="fas fa-layer-group me-1"></i>
      <strong>Active Batches</strong>
      <span class="badge bg-secondary ms-1">
        {{ open_batch_sessions|length }}
      </span>
    </div>
    <table class="table table-sm mb-0">
      <thead class="table-light">
        <tr>
          <th>Batch</th><th>Type</th><th>Progress</th>
          <th>Assigned</th><th>Action</th>
        </tr>
      </thead>
      <tbody>
        {% for session in open_batch_sessions %}
        {% set counts = batch_session_item_counts.get(session.id, {}) %}
        {% set is_cooler = session.session_type == 'cooler_route' %}
        {% set is_route = session.session_type == 'route_batch' %}
        {% set total = counts.get('total', 0) %}
        {% set picked = counts.get('picked', 0) %}
        <tr>
          <td><code class="small">{{ session.name }}</code></td>
          <td>
            {% if is_cooler %}
            <span class="badge bg-info text-dark">❄️ Cooler</span>
            {% elif is_route %}
            <span class="badge bg-primary">🚚 Route</span>
            {% elif session.session_type == 'deferred_route' %}
            <span class="badge"
                  style="background:#6f42c1;color:#fff;">Deferred</span>
            {% else %}
            <span class="badge bg-secondary">
              {{ session.picking_mode }}
            </span>
            {% endif %}
          </td>
          <td style="min-width:80px;">
            {% if total > 0 %}
            <div class="progress mb-1" style="height:5px;">
              <div class="progress-bar bg-success"
                   style="width:{{ (picked/total*100)|int }}%"></div>
            </div>
            <small class="text-muted">{{ picked }}/{{ total }}</small>
            {% else %}—{% endif %}
          </td>
          <td>
            {% if session.assigned_to %}
            <span class="badge bg-success">{{ session.assigned_to }}</span>
            {% else %}
            <button class="btn btn-sm btn-outline-primary py-0"
                    data-bs-toggle="modal"
                    data-bs-target="#dashAssignModal"
                    data-batch-id="{{ session.id }}"
                    data-batch-name="{{ session.name }}">
              <i class="fas fa-user-plus"></i>
            </button>
            {% endif %}
          </td>
          <td>
            {% if is_cooler and session.route_id
               and route_date_map.get(session.route_id) %}
            <a href="{{ url_for('cooler.route_picking',
                                 route_id=session.route_id,
                                 delivery_date=route_date_map[session.route_id]) }}"
               class="btn btn-sm btn-info py-0 px-2">
              <i class="fas fa-snowflake"></i>
            </a>
            {% else %}
            <a href="{{ url_for('batch.batch_picking_manage') }}"
               class="btn btn-sm btn-outline-secondary py-0 px-2">
              <i class="fas fa-tasks"></i>
            </a>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

</div><!-- end #view-focus -->
```

### Step 4 — JavaScript toggle

Add at the bottom of the existing `<script>` block:

```javascript
function setDashboardView(view) {
    const classic = document.getElementById('view-classic');
    const focus   = document.getElementById('view-focus');
    const btnC    = document.getElementById('btn-view-classic');
    const btnF    = document.getElementById('btn-view-focus');
    if (view === 'focus') {
        classic.style.display = 'none';
        focus.style.display   = '';
        btnC.className = 'btn btn-sm btn-outline-secondary';
        btnF.className = 'btn btn-sm btn-primary';
    } else {
        focus.style.display   = 'none';
        classic.style.display = '';
        btnC.className = 'btn btn-sm btn-secondary';
        btnF.className = 'btn btn-sm btn-outline-primary';
    }
    localStorage.setItem('dashboardView', view);
}
(function () {
    setDashboardView(localStorage.getItem('dashboardView') || 'classic');
})();
```

## Notes

- The `assignPickerModal` used by the Focus view must match the actual
  modal ID in the existing Classic view. Do not rename. If it's called
  something different (e.g. `assignModal`), use that ID in
  `data-bs-target`.
- `unassigned_invoices` — check if this variable is already passed from
  the backend. If not, derive it in the template:
  `{% set unassigned_invoices = invoices|selectattr('route_id','none')|list %}`
- The `format_seq` filter used for stop sequence display — if not
  registered, replace with `{{ id.stop_seq|float|round(1) }}` or just
  `{{ id.stop_seq }}`.

## No New Tests Required

This is a pure template addition. The backend data already has tests.
Manual verification is sufficient:

1. Dashboard loads → Classic view shown
2. Click Focus → Focus view shown, KPIs correct
3. Reload → Focus persists
4. Click Classic → Classic shown, all existing features work
5. Assign picker modal opens from Focus view invoice row
6. Cooler screen link on route card navigates correctly
7. Route collapse/expand works per route card
