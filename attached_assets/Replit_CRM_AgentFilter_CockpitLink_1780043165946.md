# CRM Dashboard — Agent Filter + Cockpit Link

Three changes, two files:

| # | File | What |
|---|------|------|
| 1 | `routes_crm_dashboard.py` | Agent filter — query, data, template context |
| 2 | `templates/crm/dashboard.html` | Agent dropdown in filter bar, agent column in table, Cockpit button per row |
| 3 | `blueprints/cockpit.py` + `services/cockpit_data.py` | Show agent name in Cockpit header |

---

## CHANGE 1 — `routes_crm_dashboard.py`

### Step 1a — Read the agent filter param

In `customer_slot_dashboard()`, find the block where all filter params are read from `request.args`. It starts with:

```python
    slot = request.args.get("slot")
    classification = request.args.getlist("classification")
    ...
    search_q = request.args.get("q", "").strip()
```

Add this line in the same block:

```python
    agent_code = request.args.get("agent", "").strip()
```

### Step 1b — Add agent to the main query

Find the section where optional filters are applied to `q`. It contains blocks like:

```python
    if classification:
        ...
    if district:
        ...
    if area:
        q = q.filter(CrmCustomerProfile.area == area)
```

Add after the `area` filter:

```python
    if agent_code:
        q = q.filter(PSCustomer.agent_code_365 == agent_code)
```

### Step 1c — Fetch distinct agent list for the dropdown

Find where `all_districts` is built (a query that fetches distinct values for the filter bar). Add immediately after it:

```python
    all_agents = db.session.query(
        PSCustomer.agent_code_365,
        PSCustomer.agent_name,
    ).filter(
        PSCustomer.active.is_(True),
        PSCustomer.deleted_at.is_(None),
        PSCustomer.agent_code_365.isnot(None),
        PSCustomer.agent_code_365 != "",
    ).distinct().order_by(PSCustomer.agent_name).all()

    # Deduplicate and format as list of dicts
    all_agents = [
        {"code": r.agent_code_365, "name": r.agent_name or r.agent_code_365}
        for r in all_agents
    ]
```

### Step 1d — Add agent fields to each `dashboard_rows` entry

Find inside the `for r in rows:` loop where `dashboard_rows.append({...})` is called. Add these two fields to the dict:

```python
            "agent_code": getattr(r, "agent_code_365", "") or "",
            "agent_name": getattr(r, "agent_name", "") or "",
```

> `agent_code_365` and `agent_name` are columns on `PSCustomer` which is already in the query — no join needed.

### Step 1e — Add agent to the `filters` dict and template context

Find the `return render_template(...)` call at the end of `customer_slot_dashboard`. 

In the `filters={...}` dict, add:
```python
            "agent": agent_code,
```

In the `render_template(...)` call arguments, add:
```python
        all_agents=all_agents,
```

---

## CHANGE 2 — `templates/crm/dashboard.html`

### Step 2a — Agent filter dropdown in the filter bar

Find the filter bar section — it contains the existing filter inputs (Search, Classification, District, Login ≤ days, Delivery Slot). Add the Agent dropdown alongside them:

```html
<!-- Agent filter -->
<div>
  <label class="form-label small text-muted mb-1">Agent</label>
  <select class="form-select form-select-sm" id="filterAgent" style="min-width:160px">
    <option value="">All Agents</option>
    {% for ag in all_agents %}
    <option value="{{ ag.code }}"
      {% if filters.agent == ag.code %}selected{% endif %}>
      {{ ag.name }}
    </option>
    {% endfor %}
  </select>
</div>
```

Then wire it into the URL filter JS. Find where the other filter controls trigger a page reload (there will be a JS function that builds the query string and does `window.location.href = ...`). Add `agent` to that function the same way `district`, `slot`, etc. are handled:

```javascript
params.set("agent", document.getElementById("filterAgent").value);
```

### Step 2b — Agent column in the customer table

Find the table header row (`<thead>`). It currently has columns: Customer, Classification, Orders, Cart, Last Login, Last Invoice, Value 6m, Value 4w, Action, Offers, Comm.

Add an **Agent** column header after Classification:

```html
<th>Agent</th>
```

Then in the table body, find the `{% for row in rows %}` loop and the corresponding `<td>` cells. After the Classification cell, add:

```html
<td class="small">
  {% if row.agent_name %}
    <a href="?{{ filters_as_querystring }}&agent={{ row.agent_code }}"
       class="text-decoration-none text-muted"
       title="Filter by {{ row.agent_name }}">
      {{ row.agent_name }}
    </a>
  {% else %}
    <span class="text-muted">—</span>
  {% endif %}
</td>
```

> The agent name is clickable — clicking it filters the whole dashboard to that agent's customers. Use whatever query-string helper the template already uses for other filter links (check how the Classification badge links filter the list).

### Step 2c — Cockpit button per customer row

Find where the action buttons are rendered per row (the area with ORDER REMINDER / CART NUDGE buttons). Add a **Cockpit** link button next to the existing action button:

```html
{% if cockpit_enabled %}
<a href="{{ url_for('cockpit.cockpit', customer_code=row.customer_code_365) }}"
   class="btn btn-outline-info btn-sm ms-1"
   title="Open AM Cockpit for {{ row.customer_name }}"
   target="_blank">
  <i class="fas fa-chart-line"></i>
</a>
{% endif %}
```

`cockpit_enabled` is already injected into every template via the context processor in `main.py` — no extra work needed.

---

## CHANGE 3 — Show agent name in the Cockpit header

### Step 3a — Fetch agent name in `services/cockpit_data.py`

Find `get_cockpit_data()`. Near the top where it fetches basic customer info (company name, classification, etc.), add a fetch for agent name:

```python
    agent_row = db.session.execute(
        text(
            "SELECT agent_code_365, agent_name "
            "FROM ps_customers WHERE customer_code_365 = :c LIMIT 1"
        ),
        {"c": customer_code},
    ).fetchone()
    agent_name = agent_row.agent_name if agent_row else None
    agent_code = agent_row.agent_code_365 if agent_row else None
```

Then include these in the dict that `get_cockpit_data()` returns:

```python
    "agent_name": agent_name,
    "agent_code": agent_code,
```

### Step 3b — Show agent name in the Cockpit template

In `templates/cockpit/cockpit.html`, find the customer header section (where the company name and customer code are displayed). Add the agent name underneath:

```html
{% if data and data.agent_name %}
<div class="text-muted small mt-1">
  <i class="fas fa-user-tie me-1"></i>Agent: <strong>{{ data.agent_name }}</strong>
  <a href="{{ url_for('crm_dashboard.customer_slot_dashboard', agent=data.agent_code) }}"
     class="ms-2 text-muted small"
     title="View all customers for this agent">
    View portfolio
  </a>
</div>
{% endif %}
```

The "View portfolio" link takes the manager straight back to the CRM dashboard pre-filtered to that agent's customers — closing the loop between the list view and the individual cockpit.

---

## Workflow after applying

1. Open CRM Dashboard
2. Select an agent from the new **Agent** dropdown → list filters to their customers only
3. Sort by Value 6m to rank by spend
4. Click the **📈 Cockpit** button on any customer → full performance view opens in new tab
5. Cockpit header shows the agent name + "View portfolio" link
6. Review 90/180/365 day trend, peer benchmark, category gaps, AI recommendations
7. Set or adjust target directly from the Cockpit
8. Click "View portfolio" to return to the filtered agent list and move to the next customer
