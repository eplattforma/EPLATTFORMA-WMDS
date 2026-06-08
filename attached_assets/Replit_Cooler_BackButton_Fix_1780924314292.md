# Cooler Route Screen — "Back" Button Goes to the Wrong Place

**Screen:** `/cooler/route/<route_id>/<delivery_date>`
**Element:** The "← Cooler Packing" button in the top-right of the page header

## The problem

This button is meant to act as a "back" link, but it's wired to a **fixed
destination** — the Cooler Packing route list — rather than returning the user
to wherever they actually came from.

In practice: a user can land on this screen from several different places —
the picking dashboard, the cooler route list, a route's own detail page, etc.
No matter which one they came from, clicking "← Cooler Packing" always sends
them to the cooler route list. If they came from the picking dashboard (as a
picker would, most commonly), this is confusing — it looks like the button
took them somewhere unrelated to where they started.

## What it should do

The button should behave like a real "back" action: return the user to the
page they were on immediately before arriving here, not to one fixed page.

## Recommended approaches (pick whichever fits the existing routing pattern best)

**Option A — use the HTTP referrer**
In the route handler that renders this template, capture `request.referrer`
and pass it through to the template as a `back_url`. Fall back to the cooler
route list only if there's no referrer (e.g. direct link/bookmark):

```python
back_url = request.referrer or url_for('cooler.route_list')
```

Then in the template, use `back_url` for the link's `href` and adjust the
label so it isn't tied to a specific destination name (e.g. just "← Back"):

```html
<a href="{{ back_url }}" class="btn btn-outline-secondary btn-sm align-self-start">
  <i class="fas fa-arrow-left me-1"></i>Back
</a>
```

**Option B — pass an explicit `from` parameter**
If `request.referrer` proves unreliable (e.g. with redirects or POST-redirect-
GET flows), have each page that links into this screen pass its own identity
as a query parameter, e.g.:

```
/cooler/route/433/2026-06-02?from=picking_dashboard
/cooler/route/433/2026-06-02?from=cooler_list
```

Then map known `from` values to their corresponding URLs and labels in the
route handler, defaulting to the cooler route list if `from` is missing or
unrecognized. This is more code to maintain but more predictable than relying
on the browser referrer.

## Why this matters

A "back" control that doesn't actually go back erodes trust in the navigation
— users start avoiding it and using the browser's back button instead, which
can be unreliable on this app after form submissions. Fixing this makes the
in-app navigation consistent and predictable across all the dashboards that
link into the cooler route screen.

## Testing checklist

- [ ] Navigate to this screen from the **picking dashboard** — clicking the
      back button returns to the picking dashboard.
- [ ] Navigate to this screen from the **Cooler Packing route list** —
      clicking the back button returns to that list.
- [ ] Navigate to this screen from any other entry point currently in use
      (e.g. route list, search results) — clicking back returns to that page.
- [ ] Open the screen via a direct link/bookmark with no referrer — clicking
      back falls back gracefully to the Cooler Packing route list (no error).
