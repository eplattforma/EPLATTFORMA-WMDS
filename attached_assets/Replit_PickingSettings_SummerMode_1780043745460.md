# Picking Settings — Summer Mode + Rename

Two changes:
1. Rename the Picking Sort Settings page to **Picking Settings** throughout
2. Add a **Summer Mode** section at the top of that page with friendly toggles for the four cooler flags

No new routes, no new DB tables, no new setting keys. The four flags already exist — they're just being surfaced in a better place.

---

## CHANGE 1 — `routes.py` — handle flag toggles on the sorting page

### Step 1a — Add a `next` parameter to `_process_feature_flag_post`

Find `_process_feature_flag_post()`. At the very end, find the two redirect lines:

```python
    return redirect(url_for('admin_settings'))
```

There are two of these (one for the flash, one for the return). **Replace both** with:

```python
    next_url = request.form.get('next') or url_for('admin_settings')
    return redirect(next_url)
```

This allows any page that calls the flag toggle to redirect back to itself instead of always landing on admin_settings.

### Step 1b — Call `_process_feature_flag_post` from `admin_sorting_settings`

Find `admin_sorting_settings`. In the `if request.method == 'POST':` block, at the very top (before the existing form handling), add:

```python
    if request.method == 'POST':
        # Handle Summer Mode / cooler flag toggles
        if request.form.get('flag_key'):
            flag_response = _process_feature_flag_post()
            if flag_response is not None:
                return flag_response
```

> The existing code already starts with `if request.method == 'POST':` — just insert the flag check as the first thing inside it, before the `new_sorting = {}` block.

### Step 1c — Read cooler flag values in the GET handler

In `admin_sorting_settings`, find the `return render_template(...)` call at the bottom. Before it, add:

```python
    # Read current Summer Mode / cooler flag values for display
    summer_mode_on       = Setting.get(db.session, 'summer_cooler_mode_enabled',  'false') == 'true'
    cooler_picking_on    = Setting.get(db.session, 'cooler_picking_enabled',       'false') == 'true'
    cooler_labels_on     = Setting.get(db.session, 'cooler_labels_enabled',        'false') == 'true'
    cooler_driver_on     = Setting.get(db.session, 'cooler_driver_view_enabled',   'false') == 'true'
```

Then pass them to the template:

```python
    return render_template('admin_sorting.html',
                          sorting_config=sorting_config,
                          manual_priority_str=manual_priority_str,
                          summer_mode_on=summer_mode_on,
                          cooler_picking_on=cooler_picking_on,
                          cooler_labels_on=cooler_labels_on,
                          cooler_driver_on=cooler_driver_on)
```

---

## CHANGE 2 — `templates/admin_sorting.html`

### Step 2a — Rename the page title

Find any occurrence of "Picking Sort Settings" in the template (page title, `<h1>`, `<h2>`, breadcrumb, `<title>` tag). **Replace all with "Picking Settings".**

### Step 2b — Add Summer Mode section at the top of the page

Insert this block **before** the existing sorting configuration section (i.e., before the zone/corridor/shelf/level/bin fields):

```html
{# ── Summer Mode ─────────────────────────────────────────────────────── #}
<div class="card mb-4 {% if summer_mode_on %}border-warning{% else %}border-secondary{% endif %}">
  <div class="card-header d-flex align-items-center justify-content-between
              {% if summer_mode_on %}bg-warning bg-opacity-10{% endif %}">
    <div>
      <h5 class="mb-0">
        <i class="fas fa-sun me-2 {% if summer_mode_on %}text-warning{% else %}text-muted{% endif %}"></i>
        Summer Mode
        {% if summer_mode_on %}
          <span class="badge bg-warning text-dark ms-2">ON</span>
        {% else %}
          <span class="badge bg-secondary ms-2">OFF</span>
        {% endif %}
      </h5>
      <div class="text-muted small mt-1">
        When ON, items tagged as <strong>SENSITIVE</strong> are automatically 
        routed to a separate cooler picking queue and handled with cold-chain care.
        Turn this on before summer. Turn it off when no longer needed.
      </div>
    </div>
  </div>

  <div class="card-body">

    {# Main toggle — Summer Mode #}
    <div class="d-flex align-items-center justify-content-between py-2 border-bottom">
      <div>
        <div class="fw-bold">Summer Mode (SENSITIVE item routing)</div>
        <div class="text-muted small">
          Splits SENSITIVE items into a dedicated cooler picking queue.
        </div>
      </div>
      <form method="POST" action="{{ url_for('admin_sorting_settings') }}" class="ms-3">
        <input type="hidden" name="flag_key"   value="summer_cooler_mode_enabled">
        <input type="hidden" name="flag_value" value="{{ 'false' if summer_mode_on else 'true' }}">
        <input type="hidden" name="next"       value="{{ url_for('admin_sorting_settings') }}">
        <button type="submit"
                class="btn btn-sm {% if summer_mode_on %}btn-warning{% else %}btn-outline-secondary{% endif %}">
          <i class="fas {% if summer_mode_on %}fa-toggle-on{% else %}fa-toggle-off{% endif %} me-1"></i>
          {{ 'Turn OFF' if summer_mode_on else 'Turn ON' }}
        </button>
      </form>
    </div>

    {# Secondary cooler flags — shown as a supporting group #}
    <div class="mt-3">
      <div class="text-muted small fw-bold mb-2 text-uppercase" style="letter-spacing:.5px">
        Related settings
      </div>

      {# Cooler Picking UI #}
      <div class="d-flex align-items-center justify-content-between py-2 border-bottom">
        <div>
          <div class="small fw-bold">Cooler Picking UI</div>
          <div class="text-muted" style="font-size:.8rem">
            Shows the dedicated cooler picking screen for warehouse staff.
          </div>
        </div>
        <form method="POST" action="{{ url_for('admin_sorting_settings') }}">
          <input type="hidden" name="flag_key"   value="cooler_picking_enabled">
          <input type="hidden" name="flag_value" value="{{ 'false' if cooler_picking_on else 'true' }}">
          <input type="hidden" name="next"       value="{{ url_for('admin_sorting_settings') }}">
          <button type="submit"
                  class="btn btn-sm {% if cooler_picking_on %}btn-success{% else %}btn-outline-secondary{% endif %}">
            {{ 'ON' if cooler_picking_on else 'OFF' }}
          </button>
        </form>
      </div>

      {# Cooler Label Printing #}
      <div class="d-flex align-items-center justify-content-between py-2 border-bottom">
        <div>
          <div class="small fw-bold">Cooler Label Printing</div>
          <div class="text-muted" style="font-size:.8rem">
            Enables printing of dedicated cooler box labels at pack time.
          </div>
        </div>
        <form method="POST" action="{{ url_for('admin_sorting_settings') }}">
          <input type="hidden" name="flag_key"   value="cooler_labels_enabled">
          <input type="hidden" name="flag_value" value="{{ 'false' if cooler_labels_on else 'true' }}">
          <input type="hidden" name="next"       value="{{ url_for('admin_sorting_settings') }}">
          <button type="submit"
                  class="btn btn-sm {% if cooler_labels_on %}btn-success{% else %}btn-outline-secondary{% endif %}">
            {{ 'ON' if cooler_labels_on else 'OFF' }}
          </button>
        </form>
      </div>

      {# Driver Cooler Loading View #}
      <div class="d-flex align-items-center justify-content-between py-2">
        <div>
          <div class="small fw-bold">Driver Cooler Loading View</div>
          <div class="text-muted" style="font-size:.8rem">
            Highlights cooler items on the driver's loading screen.
          </div>
        </div>
        <form method="POST" action="{{ url_for('admin_sorting_settings') }}">
          <input type="hidden" name="flag_key"   value="cooler_driver_view_enabled">
          <input type="hidden" name="flag_value" value="{{ 'false' if cooler_driver_on else 'true' }}">
          <input type="hidden" name="next"       value="{{ url_for('admin_sorting_settings') }}">
          <button type="submit"
                  class="btn btn-sm {% if cooler_driver_on %}btn-success{% else %}btn-outline-secondary{% endif %}">
            {{ 'ON' if cooler_driver_on else 'OFF' }}
          </button>
        </form>
      </div>

    </div>{# /related settings #}
  </div>{# /card-body #}
</div>{# /Summer Mode card #}
```

---

## CHANGE 3 — Update nav link label

Find wherever the nav link to `admin_sorting_settings` is defined (in the base nav template). It will say something like "Picking Sort Settings". **Change the label to "Picking Settings".**

---

## Result

The Picking Settings page now has two sections:

**Section 1 — Summer Mode** (new, at the top)
- Large card with a sun icon, turns amber/warning colour when ON
- One main "Turn ON / Turn OFF" button for Summer Mode
- Three supporting toggles for the related cooler flags grouped below
- Plain-language descriptions — no technical flag names visible to the admin

**Section 2 — Picking Sort Order** (existing, unchanged)
- Zone, corridor, shelf, level, bin sort configuration — exactly as before

The four cooler flags remain on the General Settings page unchanged — nothing is removed from there. The Picking Settings page is an easier alternative for day-to-day use.
