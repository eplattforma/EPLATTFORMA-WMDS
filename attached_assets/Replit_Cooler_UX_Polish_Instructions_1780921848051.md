# Cooler Packing Screen — Final UX Polish

**File to edit:** `templates/cooler/route_picking.html`

This is a small, low-risk wording/label cleanup pass on the cooler packing screen
(`/cooler/route/<route_id>/<delivery_date>`). No backend, route, or permission
changes are needed — these are template-only text and label edits.

---

## 1. Rename two KPI card labels (lines ~76 and ~85)

The KPI cards currently use internal jargon that's unclear to warehouse staff.

Find:
```html
<div class="small opacity-75 mt-1">Awaiting Prep</div>
```
Replace with:
```html
<div class="small opacity-75 mt-1">Not Sequenced</div>
```

Find:
```html
<div class="small opacity-75 mt-1">Unboxed</div>
```
Replace with:
```html
<div class="small opacity-75 mt-1">Needs Boxing</div>
```

(These two cards count items that haven't been route-sequenced yet, and picked
items not yet assigned to a box, respectively — the new labels describe the
state in plain terms instead of internal shorthand.)

---

## 2. Reword the three "scroll to section" buttons (lines ~172, ~177, ~194)

These buttons just scroll down to an accordion section that's already
auto-expanded at that stage — the bare down-arrow makes them look like they
navigate somewhere new. Drop the arrow and use an action-oriented label so it's
clear it's a same-page jump.

Find:
```html
<i class="fas fa-lock me-2"></i>Close Boxes ↓
```
Replace with:
```html
<i class="fas fa-lock me-2"></i>Go to Boxes
```

Find:
```html
<i class="fas fa-layer-group me-2"></i>Plan Remaining ↓
```
Replace with:
```html
<i class="fas fa-layer-group me-2"></i>Go to Box Planning
```

Find:
```html
<i class="fas fa-layer-group me-2"></i>Plan Boxes ↓
```
Replace with:
```html
<i class="fas fa-layer-group me-2"></i>Go to Box Planning
```

(The `onclick` scroll behavior on these buttons stays exactly as-is — only the
visible button text changes.)

---

## 3. Soften "recovery tools" wording in the Box Planning alert (line ~448)

Find:
```html
Box changes should be done before confirming the plan. After confirmation, use recovery tools only if a physical packing mistake happened.
```
Replace with:
```html
Box changes should be done before confirming the plan. After confirmation, use the Manager Tools below only if a physical packing mistake happened.
```

---

## 4. Rename the "Manager Recovery Tools" section to match (lines ~723, ~730)

To stay consistent with the wording change in step 3, rename the section
heading. This is a label-only change — the accordion ID, permission check, and
all the tools inside (reopen box, force close, cancel, move item, emergency
assignment) stay exactly the same.

Find:
```html
{# E. Manager Recovery Tools                                                  #}
```
Replace with:
```html
{# E. Manager Tools                                                           #}
```

Find:
```html
<i class="fas fa-tools me-2 text-warning"></i>Manager Recovery Tools
```
Replace with:
```html
<i class="fas fa-tools me-2 text-warning"></i>Manager Tools
```

---

## Testing checklist after applying

- [ ] Open a cooler route before sequencing — confirm the KPI card now reads
      "Not Sequenced" instead of "Awaiting Prep".
- [ ] Open a route with picked-but-unboxed items — confirm the KPI card reads
      "Needs Boxing" instead of "Unboxed".
- [ ] At each workflow stage (locked but no boxes, picking complete with open
      boxes, picking complete with unboxed items) — confirm the Next Action
      button now reads "Go to Box Planning" / "Go to Boxes" and still scrolls
      to and expands the correct accordion section on click.
- [ ] Expand Box Planning — confirm the alert text reads "...use the Manager
      Tools below only if a physical packing mistake happened."
- [ ] Scroll to the bottom accordion section — confirm it now reads
      "Manager Tools" and all five tools inside (reopen, force close, cancel,
      move item, emergency assignment) still work unchanged.
