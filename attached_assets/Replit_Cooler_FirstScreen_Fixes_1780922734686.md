# Cooler Packing — First-Screen Fixes (Route Detail Page)

**File(s):** `templates/cooler/route_picking.html` and
`blueprints/cooler_picking.py`

These are the issues spotted on the very first screen a manager sees when
opening a cooler route (`/cooler/route/<route_id>/<delivery_date>`), in
addition to the wording cleanup already covered in
`Replit_Cooler_UX_Polish_Instructions.md`. One of these (#1) is a real data/
logic bug, not just wording — flag it as the priority item.

---

## 1. PRIORITY — "Needs Boxing" KPI shows a count before anything is picked (likely bug)

**What's happening:** On a brand-new route where 0 items have been picked yet
(`Picked: 0/9`), the KPI card still shows `9` for "Needs Boxing"
(currently labelled "Unboxed"). That's confusing — the label implies these are
*picked* items waiting to be put in a box, but the count appears before any
picking has started.

**Root cause — `blueprints/cooler_picking.py`, around line 692:**

```python
picked_unboxed_count = db.session.execute(
    text(
        "SELECT COUNT(*) FROM batch_pick_queue bpq "
        "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
        "JOIN shipments s ON s.id = i.route_id "
        "WHERE bpq.pick_zone_type = 'cooler' "
        "  AND bpq.status IN ('picked', 'pending') "      # <-- includes 'pending'
        "  AND i.route_id = :rid "
        "  AND s.delivery_date = :dd "
        "  AND NOT EXISTS ("
        "        SELECT 1 FROM cooler_box_items cbi "
        "        WHERE cbi.queue_item_id = bpq.id"
        "  )"
    ),
    ...
)
```

The query counts items with status **'picked' OR 'pending'** that aren't yet
in a box — so it counts every not-yet-boxed item on the route, picked or not,
which is effectively the whole route before picking starts. The variable name
(`picked_unboxed_count`) and the on-screen label ("Unboxed" / "Needs Boxing")
both promise a count of *picked* items only.

**Recommended fix — pick ONE of these, please confirm with the team which is intended:**

- **Option A (most likely correct):** If the KPI is meant to show "picked
  items that still need a box," remove `'pending'` from the status filter so
  it only counts `'picked'`:
  ```python
  "  AND bpq.status = 'picked' "
  ```
  This makes the card read `0` until items are actually picked, which matches
  what "Needs Boxing" implies.

- **Option B:** If the intent is genuinely "all items on this route not yet
  assigned to a box regardless of pick status" (e.g. for box-planning purposes
  before picking begins), then keep the query as-is but change the on-screen
  label to something that doesn't imply "picked," e.g. "Not Yet Boxed" — and
  ideally split it into two separate counts (planned-but-unpicked vs.
  picked-but-unboxed) so a manager can tell which situation they're in.

Please check with whoever defined this KPI which behaviour was intended, then
apply the corresponding fix above.

---

## 2. Duplicate snowflake icon in the page title

**Where:** `templates/cooler/route_picking.html`, line ~47-48

```html
<i class="fas fa-snowflake me-2 text-info"></i>
❄️ Cooler Packing — Route {{ route_id }}{% if route_name %} / {{ route_name }}{% endif %}
```

This renders **two** snowflakes side by side — a Font Awesome icon and an
emoji. Pick one. Recommended: drop the emoji and keep the FA icon (it matches
the rest of the icon styling on the page):

Find:
```html
<i class="fas fa-snowflake me-2 text-info"></i>
❄️ Cooler Packing — Route {{ route_id }}{% if route_name %} / {{ route_name }}{% endif %}
```
Replace with:
```html
<i class="fas fa-snowflake me-2 text-info"></i>
Cooler Packing — Route {{ route_id }}{% if route_name %} / {{ route_name }}{% endif %}
```

(There's a second occurrence of the `route_name` pattern around line 891 in
the print section — that one does NOT have the duplicate icon issue, leave it
as-is.)

---

## 3. Route name displaying as a partial word ("eview")

**What's happening:** The page title showed "Prepare Cooler Route 433 / eview"
— "eview" looks like a truncated word (possibly "Review" or a longer name with
the first characters cut off).

**This is a data issue, not a template bug** — `route_name` is read directly
from the `shipments.route_name` column:

```python
_sinfo = db.session.execute(
    text("SELECT driver_name, route_name FROM shipments WHERE id = :rid"),
    {"rid": _route_id_int},
).fetchone()
route_driver = _sinfo[0] if _sinfo else None
route_name_val = _sinfo[1] if _sinfo else None
```

**Action:** Please check the `shipments` table for route id 433 — and ideally
scan for other rows where `route_name` looks unusually short or starts mid-word
— and confirm whether this is how the name was entered/imported, or whether an
upstream import/sync process is truncating it. No code change is needed here
unless the data confirms a systemic truncation bug in the import.

---

## 4. Same instruction shown twice on the same screen

**What's happening:** When a route hasn't been confirmed yet, the page shows
**two** separate messages telling the manager to do the same thing:

- The blue "Next Action" banner: *"Start here — Confirm Cooler Route... Locks
  the delivery sequence so items can be sorted into boxes in the correct
  order."*
- A second cream-colored alert further down inside "Picker / Picking Control":
  *"Prepare the cooler route (step 1) before starting picking."*
  (`templates/cooler/route_picking.html`, line ~714)

Both are correct, but showing the same guidance twice in two different visual
styles on first load adds noise rather than clarity. Recommended: keep the
prominent blue Next Action banner (it has the actual "Confirm Cooler Route"
button) and remove the second alert, since the Picker section is already
collapsed/disabled-looking at this stage.

Find (around line 712-715):
```html
{% else %}
<div class="alert alert-warning py-2 small mb-0">
  <i class="fas fa-lock-open me-1"></i>Prepare the cooler route (step 1) before starting picking.
</div>
{% endif %}
```
Replace with:
```html
{% else %}
<div class="text-muted small">
  <i class="fas fa-lock-open me-1"></i>Picking will be available once the cooler route is confirmed (see banner above).
</div>
{% endif %}
```
This keeps a lightweight pointer in context without repeating the full
instruction in a second alert box.

---

## 5. The two orange KPI cards are visually identical

**Where:** `templates/cooler/route_picking.html`, lines ~70 and ~79

Both the "Not Sequenced" and "Needs Boxing" cards use the same orange
(`#fd7e14`) when their count is greater than zero, making them indistinguishable
at a glance — the manager has to read the small label text under each number.

Recommended: give them distinct colors so the row is scannable without reading
every label. For example, keep "Not Sequenced" as orange (it's the earliest-
stage flag) and make "Needs Boxing" amber/yellow (`#ffc107`) to signal a
later-stage, less urgent flag:

Find (line ~79):
```html
style="background:{{ '#fd7e14' if picked_unboxed_count > 0 else '#6c757d' }};color:#fff;">
```
Replace with:
```html
style="background:{{ '#ffc107' if picked_unboxed_count > 0 else '#6c757d' }};color:#000;">
```
(Note the `color` also changes to `#000` for readability against the lighter
amber background.)

---

## 6. "Picker / Picking Control" section is fully expanded with nothing actionable yet

**Where:** `templates/cooler/route_picking.html`, line ~643

```jinja
{% set _picker_open = (not cooler_session.assigned_to) or (picking_phase and not picking_phase.complete) or (batch_in_progress and not _picking_done) %}
```

Before the route is confirmed, this section is open by default (because
`cooler_session.assigned_to` is empty) even though picking can't start yet —
adding scroll length to a screen that's already telling the manager to do
something else first (confirm the route).

Recommended: also require the route to be locked before auto-expanding this
section:

Find:
```jinja
{% set _picker_open = (not cooler_session.assigned_to) or (picking_phase and not picking_phase.complete) or (batch_in_progress and not _picking_done) %}
```
Replace with:
```jinja
{% set _picker_open = _is_locked and ((not cooler_session.assigned_to) or (picking_phase and not picking_phase.complete) or (batch_in_progress and not _picking_done)) %}
```
This keeps the section collapsed (but still visible/reachable) until the route
is actually confirmed, then expands automatically once it's relevant.

---

## 7. "9 awaiting" badge — align wording with the renamed KPI card

**Where:** `templates/cooler/route_picking.html`, line ~221

```html
{% if unsequenced %}<span class="badge bg-warning text-dark ms-1">{{ unsequenced|length }} awaiting</span>{% endif %}
```

If you're applying the KPI rename from `Replit_Cooler_UX_Polish_Instructions.md`
("Awaiting Prep" → "Not Sequenced"), update this badge to use the same term so
a manager doesn't have to mentally map "awaiting" and "not sequenced" as the
same thing:

Replace with:
```html
{% if unsequenced %}<span class="badge bg-warning text-dark ms-1">{{ unsequenced|length }} not sequenced</span>{% endif %}
```

---

## Suggested order of work

1. **Item 1 first** — confirm the intended KPI behaviour with the team, then
   fix the query or the label (this is the only one that could mislead a
   manager about real warehouse state).
2. Items 2, 4, 5, 6, 7 — straightforward template edits, can be done together.
3. Item 3 — a data check, not a deploy; raise it separately with whoever
   manages route/shipment data entry or the import pipeline.

## Testing checklist

- [ ] Open a freshly-confirmed route with 0 items picked — "Needs Boxing"
      (or whatever it ends up labelled) reads `0`, not the total item count.
- [ ] Page title shows a single snowflake icon, no duplicate emoji.
- [ ] Before route confirmation: only ONE instruction to confirm the route is
      visible (the blue banner); the Picker section shows a brief muted note
      instead of a second alert box.
- [ ] "Not Sequenced" and "Needs Boxing" KPI cards are visually distinguishable
      by color when both have counts > 0.
- [ ] Picker / Picking Control section stays collapsed until the route is
      confirmed, then expands automatically once relevant.
- [ ] Badge on Route Item Status reads "N not sequenced" consistent with the
      KPI card label.
