# Cooler Routes — Remove from Nav, Add to Route Detail Page

One item removed from the Warehouse menu. Access moves to the route detail page instead — staff always start from a specific route, so a top-level menu link is an unnecessary detour.

---

## CHANGE 1 — Remove from the Warehouse dropdown

In the base nav template (`templates/base.html` or equivalent), find and **delete** the Manage Cooler Routes item from the Warehouse dropdown:

```html
<a class="dropdown-item" href="...">
  ...Manage Cooler Routes...
</a>
```

Delete the entire `<li>` containing it. If there is a `<li><hr class="dropdown-divider"></li>` immediately before or after it that becomes orphaned, delete that too.

---

## CHANGE 2 — Add Cooler Boxes button to the route detail page

In the route detail template (the page showing a single delivery route with its stops/invoices), find the action buttons in the route header — the area that has buttons like "Lock Route", "Print Manifest", etc.

Add this button in that group:

```html
{% if cooler_picking_enabled %}
<a href="{{ url_for('cooler.route_box_plan', route_id=route.id, delivery_date=route.delivery_date) }}"
   class="btn btn-outline-info btn-sm">
  <i class="fas fa-snowflake me-1"></i>Cooler Boxes
</a>
{% endif %}
```

**Notes:**
- `cooler_picking_enabled` is already injected into every template via the context processor in `main.py` — no extra wiring needed
- The button only appears when Cooler Mode is active, so it is invisible in normal operation
- Verify the exact `url_for` endpoint name by checking `blueprints/cooler_picking.py` — find the route that renders the cooler box plan page for a specific route and date, and use its endpoint name
- `route.id` and `route.delivery_date` should match whatever the route object is called on that template page

---

## Result

- Warehouse dropdown loses one item (tidier)
- Cooler packing is reached in one click from the route you are already working on
- No new routes, no new permissions, no backend changes
