# Cooler Route Screen — Simplify the KPI Row

**File:** `templates/cooler/route_picking.html`
**Section:** KPI cards row (around lines 60-115)

After reviewing the six KPI cards on the cooler route screen with the team,
three of them add more clutter than value in normal day-to-day use. This brief
proposes consolidating from 6 cards down to 4.

---

## 1. Remove (or conditionally hide) "Not Sequenced"

Per the team: invoices cannot land on a route without the system automatically
assigning them a sequence number, so this count should always read 0 in normal
operation. As a permanent fixture it just adds a card nobody needs to read.

**Recommended:** Remove the card from the KPI row entirely. If there's any
concern about a rare edge case (e.g. a data correction that bypasses normal
sequencing), keep the underlying count available but only surface it as a
warning banner when it's actually greater than zero — not as a permanent KPI
card that always shows the same number.

Find (around line 67-76):
```html
<div class="col-6 col-sm-4 col-xl-2">
  <div class="card border-0 shadow-sm text-center h-100"
       style="background:{{ '#fd7e14' if unsequenced else '#6c757d' }};color:#fff;">
    <div class="card-body py-3">
      <div class="fs-4 fw-bold">{{ unsequenced | length }}</div>
      <div class="small opacity-75 mt-1">Not Sequenced</div>
    </div>
  </div>
</div>
```
Delete this block. (Optional safeguard — add this just above the KPI row so an
anomaly still surfaces if it ever occurs:)
```html
{% if unsequenced %}
<div class="alert alert-warning py-2 small mb-2">
  <i class="fas fa-exclamation-triangle me-1"></i>
  {{ unsequenced|length }} item(s) on this route are missing a delivery sequence — this shouldn't normally happen. Please check before confirming the route.
</div>
{% endif %}
```

---

## 2. Remove "Needs Boxing"

This card duplicates information that's already visible elsewhere in context
(the Box Planning section badge already shows "{{ picked_unboxed_count }}
unboxed" when relevant, and the Next Action banner calls out "Plan Remaining
Unboxed Items" at the appropriate stage). As a standing KPI it's one more
number to scan that rarely changes the manager's next action.

Find (around line 79-88):
```html
<div class="col-6 col-sm-4 col-xl-2">
  <div class="card border-0 shadow-sm text-center h-100"
       style="background:{{ '#ffc107' if picked_unboxed_count > 0 else '#6c757d' }};color:#000;">
    <div class="card-body py-3">
      <div class="fs-4 fw-bold">{{ picked_unboxed_count }}</div>
      <div class="small opacity-75 mt-1">Needs Boxing</div>
    </div>
  </div>
</div>
```
Delete this block.

---

## 3. Merge "Open Boxes" and "Closed Boxes" into a single "Boxes Closed" card

Instead of two separate cards each showing one number, combine them into one
card showing progress as `closed / total` (e.g. `0/2`, `1/2`, `2/2`) — this
communicates the same information (how many boxes exist, how many are sealed)
in a single glance, and naturally reads as a completion indicator.

Find (around lines 90-108, the two existing cards):
```html
<div class="col-6 col-sm-4 col-xl-2">
  <div class="card border-0 shadow-sm text-center h-100"
       style="background:{{ '#0d6efd' if _open_boxes else '#6c757d' }};color:#fff;">
    <div class="card-body py-3">
      <div class="fs-4 fw-bold">{{ _open_boxes | length }}</div>
      <div class="small opacity-75 mt-1">Open Boxes</div>
    </div>
  </div>
</div>
<div class="col-6 col-sm-4 col-xl-2">
  <div class="card border-0 shadow-sm text-center h-100"
       style="background:{{ '#198754' if _closed_boxes else '#6c757d' }};color:#fff;">
    <div class="card-body py-3">
      <div class="fs-4 fw-bold">{{ _closed_boxes | length }}</div>
      <div class="small opacity-75 mt-1">Closed Boxes</div>
    </div>
  </div>
</div>
```
Replace with a single combined card:
```html
<div class="col-6 col-sm-4 col-xl-3">
  <div class="card border-0 shadow-sm text-center h-100"
       style="background:{{ '#198754' if (boxes and _closed_boxes|length == boxes|length) else '#0d6efd' if _open_boxes else '#6c757d' }};color:#fff;">
    <div class="card-body py-3">
      <div class="fs-4 fw-bold">{{ _closed_boxes | length }} / {{ boxes | length }}</div>
      <div class="small opacity-75 mt-1">Boxes Closed</div>
    </div>
  </div>
</div>
```
This card turns blue while boxes are open/in progress, green once every box on
the route is closed, and stays neutral grey if no boxes have been planned yet
— giving an at-a-glance read of box-packing progress without needing two
separate numbers.

---

## 4. Adjust column widths for the new 4-card layout

Since we're going from 6 cards to 4, widen the remaining cards so the row
still fills nicely. Change the Bootstrap column classes from `col-xl-2` to
`col-xl-3` on the **Picked**, **Boxes Closed** (already shown above), and
**Volume** cards so all four sit evenly across the row on large screens:

Find (Picked card, ~line 65):
```html
<div class="col-6 col-sm-4 col-xl-2">
```
Replace with:
```html
<div class="col-6 col-sm-4 col-xl-3">
```
(Do the same for the Volume card around line 100.)

---

## Result

The KPI row goes from six cards (Picked / Not Sequenced / Needs Boxing / Open
Boxes / Closed Boxes / Volume) down to four (**Picked**, **Boxes Closed**,
**Volume**, plus whichever of the removed ones the team wants kept as a
conditional warning banner instead). This keeps every number that actually
drives a decision, and drops the ones that either never change in normal use
or duplicate information shown elsewhere on the same screen.

## Testing checklist

- [ ] KPI row shows 4 cards on desktop, evenly spaced, no awkward gaps.
- [ ] "Boxes Closed" reads `0/0` with no boxes planned, `0/2` with 2 open
      boxes, `1/2` with one closed and one open, `2/2` (green) once all
      boxes are sealed.
- [ ] If keeping the optional "unsequenced" warning banner — confirm it only
      appears when `unsequenced` is non-empty, and stays hidden in normal use.
- [ ] Confirm no other part of the page (JS, filters, print view) references
      the removed KPI cards by ID/class — only the visual cards are removed,
      underlying counts (`unsequenced`, `picked_unboxed_count`, `_open_boxes`,
      `_closed_boxes`) should remain available for other sections that use them.
