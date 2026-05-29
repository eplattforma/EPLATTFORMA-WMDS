# EP SmartGrowth CRM — Remove "Need Action" from Dashboard

Remove two elements from `templates/crm/dashboard.html`. No backend changes needed.

---

## CHANGE 1 — Remove the "Need Action" KPI card

Find the Need Action card in the KPI strip. It will look like:

```html
<div class="card ... ">
  <div class="small text-muted">Need Action</div>
  <div class="fw-bold fs-5 ...">{{ kpi_action_count }}</div>
</div>
```

**Delete the entire card `<div>` block.**

---

## CHANGE 2 — Remove the "Action needed only" checkbox filter

Find the checkbox in the filter bar:

```html
<input type="checkbox" id="actionOnly" ...>
<label ... for="actionOnly">Action needed only</label>
```

**Delete the entire checkbox + label block.**

Also find and remove any JavaScript that reads this checkbox when building the filter URL, e.g.:

```javascript
if (document.getElementById("actionOnly").checked) params.set("action_only", "1");
```

---

## That's it

The tier classification (Champion / Active / At Risk / Dormant / Potential) now covers the same ground in a clearer way. The "Need Action" count added noise without explaining why action was needed. The tier badges and trend arrows give the agent more useful signals.
