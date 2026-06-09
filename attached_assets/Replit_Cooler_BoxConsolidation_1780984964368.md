# Cooler Route Screen — Box Consolidation During Packing

**File:** `templates/cooler/route_picking.html`

## Background / why this is needed

The system proposes a box plan before picking starts — but physical reality at
packing time often differs from the recommendation. A common example: the
system suggests 1 large box + 1 medium box, but when items are collected the
team finds everything fits in the large box alone. The second box is then
unnecessary and should be cancelled.

The system already has all the tools to do this (cancel a box, move items
between boxes) — they just live in the "Manager Tools" accordion with a
warning label that frames them as "mistake correction." That framing is wrong:
**consolidating boxes based on real items is a normal, expected packing step.**

This brief makes two changes:
1. Add a low-fill warning directly on each box card to prompt the manager when
   consolidation makes sense.
2. Surface the "Move Items" and "Cancel Box" actions directly on each box card
   (during active packing) — not hidden in a separate admin section.

---

## Change 1 — Add a fill-level consolidation prompt on each box card

**Where:** In the Cooler Boxes table, in the `<td>` that renders the fill
progress bar for each box (around line ~590). After the fill bar, add a
conditional warning row that only appears when:
- picking is complete (`_picking_done` is true), AND
- the box is still open (not closed), AND
- the box fill is below 50%

In the Actions `<td>` of the box table row (around line ~625), immediately
**before** the existing Close button, add:

```html
{% if _picking_done and b.status == 'open' and b.fill_pct is not none and b.fill_pct < 50 %}
<span class="badge bg-warning text-dark text-wrap text-start" style="max-width:160px;white-space:normal;">
  <i class="fas fa-exclamation-triangle me-1"></i>
  Only {{ b.fill_pct }}% full — consider moving items to another box before sealing.
</span>
{% endif %}
```

This makes the fill warning appear inline in the box row at exactly the right
moment — after picking, before closing — without cluttering the screen earlier
in the workflow.

---

## Change 2 — Add "Move Items" and "Cancel Box" directly on each open box card

Right now these actions are only accessible in the collapsed Manager Tools
section. They should also appear directly in the box row's Actions column when
the box is open and picking is done — this is the natural moment a manager
would want them.

In the Actions `<td>` of the box table (around line ~625), **after** the
existing Close button block, add:

```html
{% if has_permission('cooler.manage_boxes') and b.status == 'open' and _picking_done %}

  {# Cancel this box (only if it's empty or all items moved away) #}
  <form method="post" action="{{ url_for('cooler.box_cancel', box_id=b.id) }}" class="d-inline"
        onsubmit="return confirm('Cancel Box {{ b.box_no }}? All assigned items will be returned to unboxed.')">
    <input type="hidden" name="_html_form" value="1">
    <button type="submit" class="btn btn-sm btn-outline-danger text-nowrap">
      <i class="fas fa-times me-1"></i>Cancel Box
    </button>
  </form>

  {# Move items from this box to another open box (only if other open boxes exist) #}
  {% set _other_open_boxes = _open_boxes | selectattr('id', 'ne', b.id) | list %}
  {% if _other_open_boxes and box_items_by_box.get(b.id) %}
  <div class="dropdown d-inline">
    <button class="btn btn-sm btn-outline-secondary dropdown-toggle text-nowrap"
            type="button" data-bs-toggle="dropdown">
      <i class="fas fa-arrows-alt me-1"></i>Move Items
    </button>
    <ul class="dropdown-menu">
      <li><h6 class="dropdown-header">Move ALL items from Box {{ b.box_no }} to:</h6></li>
      {% for ob in _other_open_boxes %}
      <li>
        <button class="dropdown-item btn-move-all-items"
                data-source-box-id="{{ b.id }}"
                data-source-box-no="{{ b.box_no }}"
                data-dest-box-id="{{ ob.id }}"
                data-dest-box-no="{{ ob.box_no }}">
          → Box {{ ob.box_no }}
          {% if ob.box_type_name %}({{ ob.box_type_name }}){% endif %}
          {% if ob.fill_pct is not none %} — {{ ob.fill_pct }}% full{% endif %}
        </button>
      </li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}

{% endif %}
```

**Note on "Move ALL items":** The existing Manager Tools section already has
a per-item "Move to..." dropdown. The new button above moves ALL items from
one box to another in a single action — which is what you need for the
consolidation scenario (empty Box 2 into Box 1, then cancel Box 2). This
requires a new backend route:

```
POST /cooler/box/<source_box_id>/move_all_to/<dest_box_id>
```

Replit should implement this route to:
1. Look up all `cooler_box_items` where `box_id = source_box_id`
2. Update each one: set `box_id = dest_box_id`
3. Recalculate fill percentages for both boxes
4. Audit-log the move: `"cooler.box_consolidation"` with source/dest box IDs
5. Return a redirect back to the route picking page (same pattern as existing
   `box_cancel`, `box_close` routes)

---

## Change 3 — Update the Manager Tools warning text

Now that the consolidation tools are surfaced directly on the box cards in
normal workflow, update the warning at the top of the Manager Tools accordion
so it no longer implies every action there is a "mistake correction." Some are
routine adjustments, others are genuine last-resort fixes.

Find (around line ~789):
```html
<strong>Use only if a physical packing mistake happened after the plan was confirmed.</strong>
Box changes should be done in Box Planning before confirming the plan.
```
Replace with:
```html
<strong>Use these tools to adjust boxes during or after packing.</strong>
For routine consolidation (moving items between boxes, cancelling an empty box),
use the controls directly on each box card above. The tools here cover additional
scenarios such as force-closing a box with unpicked items or reopening a
sealed box.
```

---

## Recommended workflow (for the ops team to follow)

Once this is implemented, the consolidation flow for the scenario described
(system plans 2 boxes, everything fits in 1) is:

1. Picking completes normally.
2. Screen shows the Cooler Boxes section — any box under 50% full will show
   an orange "Only X% full" warning automatically.
3. Manager clicks **Move Items → Box 1** on the low-fill box card.
4. All items move to Box 1. The low-fill box is now empty.
5. Manager clicks **Cancel Box** on the now-empty box.
6. Manager clicks **Close** on Box 1 to seal it.
7. Route is ready for dispatch.

No admin tools, no buried sections — this is a three-click operation
directly on the box cards.

---

## Testing checklist

- [ ] With picking complete and a box below 50% full: orange warning appears
      on that box card in the Cooler Boxes section.
- [ ] With picking complete and two open boxes: "Move Items" dropdown appears
      on each open box card listing the other open box(es) as destinations,
      showing their fill % for reference.
- [ ] Clicking "Move ALL items → Box X" moves every item from the source box
      to the destination box, recalculates fill % on both boxes, and reloads
      the page correctly.
- [ ] After moving all items out, "Cancel Box" on the now-empty box removes it
      cleanly and the page reloads without error.
- [ ] Before picking is complete: "Move Items" and "Cancel Box" buttons do NOT
      appear on box cards (these are packing-time actions only). They remain
      accessible in Manager Tools if needed earlier.
- [ ] All moves are recorded in the audit log with source box, dest box, and
      user.
- [ ] Existing per-item "Move to..." in Manager Tools still works (this change
      adds a new "move all" action, it does not replace the per-item one).
