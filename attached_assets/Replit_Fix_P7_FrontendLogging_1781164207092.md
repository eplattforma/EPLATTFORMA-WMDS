# WMDS Fix — Priority 7: Frontend & Logging

Three bugs: two in the cooler route-picking template's JavaScript, one missing logger in the cooler picking blueprint. Every "FIND" block below is exact current code copied from the files — use it as a find/replace target. Do not paraphrase or reformat; match it exactly (including indentation and Unicode escapes like `—`).

---

## Bug 1 — AJAX calls assume JSON; HTML error pages silently become "Network error"

**File:** `templates/cooler/route_picking.html`

The template contains exactly **two** `fetch()` calls, and both parse the response with `.then(r => r.json())` with no `r.ok` check and no content-type check. If the server returns an HTML error page (500, 403) or a session-expired redirect to the HTML login page, `r.json()` throws a parse error, the generic `.catch` fires ("Network error — please try again"), and the user gets no clue what actually happened. The page does not reload, and the operation is silently not applied. (The third AJAX-style action, "Move ALL items", builds and submits a real HTML form, so it is not affected.)

### 1a. Box-plan recommendation fetch (`fetchRec`)

**Location:** inside the `{# ── Box plan JavaScript ─...─ #}` script block, approx. lines 1286–1301.

**FIND (exact current code):**

```javascript
    fetch(url, {credentials: 'same-origin'})
      .then(function(r){ return r.json(); })
      .then(function(data) {
        btn.disabled = false; btn.innerHTML = origHtml;
        if (!data.ok) { showError(data.message || 'Error generating plan.'); return; }
        if (!data.plan || data.plan.length === 0) { showError(data.message || 'No items found to plan.'); return; }
        if (data.box_types && data.box_types.length) boxTypes = data.box_types;
        plan = data.plan.map(function(box) {
          if (!box.usable_capacity_cm3) { var bt = getBoxType(box.box_type_id); if (bt) box.usable_capacity_cm3 = bt.usable_capacity; }
          if (!box.max_weight_kg) box.max_weight_kg = 0;
          box._server_warnings = (box.warnings || []).slice();
          return box;
        });
        editorArea.classList.remove('d-none'); btnReRec.classList.remove('d-none'); renderPlan();
      })
      .catch(function() { btn.disabled = false; btn.innerHTML = origHtml; showError('Network error — please try again.'); });
```

**REPLACE WITH:**

```javascript
    fetch(url, {credentials: 'same-origin'})
      .then(function(r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok || ct.indexOf('application/json') === -1) {
          if (r.status === 401 || r.status === 403 || ct.indexOf('text/html') !== -1) {
            throw new Error('Server returned an unexpected response (HTTP ' + r.status + '). Your session may have expired — please refresh the page and try again.');
          }
          throw new Error('Server error (HTTP ' + r.status + ') — please refresh the page and try again.');
        }
        return r.json();
      })
      .then(function(data) {
        btn.disabled = false; btn.innerHTML = origHtml;
        if (!data.ok) { showError(data.message || 'Error generating plan.'); return; }
        if (!data.plan || data.plan.length === 0) { showError(data.message || 'No items found to plan.'); return; }
        if (data.box_types && data.box_types.length) boxTypes = data.box_types;
        plan = data.plan.map(function(box) {
          if (!box.usable_capacity_cm3) { var bt = getBoxType(box.box_type_id); if (bt) box.usable_capacity_cm3 = bt.usable_capacity; }
          if (!box.max_weight_kg) box.max_weight_kg = 0;
          box._server_warnings = (box.warnings || []).slice();
          return box;
        });
        editorArea.classList.remove('d-none'); btnReRec.classList.remove('d-none'); renderPlan();
      })
      .catch(function(err) { btn.disabled = false; btn.innerHTML = origHtml; showError((err && err.message) || 'Network error — please try again.'); });
```

### 1b. Move-item-between-boxes fetch

**Location:** the `{# ── Move item between boxes (fetch) ─...─ #}` script block near the bottom of the file, approx. lines 1353–1368.

**FIND (exact current code):**

```javascript
  fetch('/cooler/box-item/' + cbiId + '/move-to-box', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({destination_box_id: parseInt(destId)})
  })
  .then(function(r){ return r.json(); })
  .then(function(data){ if (data.error) { alert('Could not move: ' + data.error); } else { window.location.reload(); } })
  .catch(function(){ alert('Network error — please try again.'); });
```

**REPLACE WITH:**

```javascript
  fetch('/cooler/box-item/' + cbiId + '/move-to-box', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({destination_box_id: parseInt(destId)})
  })
  .then(function(r) {
    var ct = r.headers.get('content-type') || '';
    if (!r.ok || ct.indexOf('application/json') === -1) {
      if (r.status === 401 || r.status === 403 || ct.indexOf('text/html') !== -1) {
        throw new Error('Server returned an unexpected response (HTTP ' + r.status + '). Your session may have expired — please refresh the page and try again.');
      }
      throw new Error('Server error (HTTP ' + r.status + ') — please refresh the page and try again.');
    }
    return r.json();
  })
  .then(function(data){ if (data.error) { alert('Could not move: ' + data.error); } else { window.location.reload(); } })
  .catch(function(err){ alert((err && err.message) || 'Network error — please try again.'); });
```

### Testing checklist (Bug 1)

- With a valid session, click "Get Recommendation" in Box Planning — the plan editor still appears normally.
- With a valid session, use Manager Tools > "Move item between boxes" > "Move to… Box #N" — the move succeeds and the page reloads.
- Log out in a second tab (or let the session expire), then click "Get Recommendation" — the warning area shows "Your session may have expired — please refresh the page and try again" instead of "Network error".
- Temporarily make the move endpoint raise an exception (or use devtools to block/override the response with an HTML 500 page) — the move-item alert shows "Server error (HTTP 500) — please refresh the page and try again" instead of "Network error".
- Disconnect the network and retry — the original "Network error — please try again." message still appears (the `.catch` fallback).

---

## Bug 2 — `cascadeOverflow` recurses infinitely when one item is larger than the largest box

**File:** `templates/cooler/route_picking.html`, inside the `{# ── Box plan JavaScript ─...─ #}` script block, approx. lines 1157–1176.

`cascadeOverflow` bumps the earliest-stop item out of an overfull box into the next box, appending a new box if needed, then recurses. If a single item's volume exceeds the usable capacity of every box type, every newly appended box is immediately overfull too, so the function recurses until "Maximum call stack size exceeded" and the plan editor dies silently. The fix adds (a) a check that the bumped item actually fits in the largest available box type — if not, it is put back, an alert names the item, and the cascade stops — and (b) a hard recursion depth cap of 20 as a safety net. A `bpCascadeWarned` flag ensures the alert fires only once per cascade. Callers (`bpChangeType`, `bpMoveItem`, `bpRemoveBox`) need **no changes** — the new `depth` parameter defaults to 0 when omitted.

**FIND (exact current code — the variable declarations plus the whole function):**

```javascript
  var plan     = [];
  var boxTypes = [];
```

**REPLACE WITH:**

```javascript
  var plan     = [];
  var boxTypes = [];
  var BP_MAX_CASCADE_DEPTH = 20;
  var bpCascadeWarned = false;
```

Then:

**FIND (exact current code — the full `cascadeOverflow` function):**

```javascript
  function cascadeOverflow(fromIdx) {
    var box = plan[fromIdx];
    if (!box || !box.usable_capacity_cm3 || box.estimated_fill_cm3 <= box.usable_capacity_cm3) return;
    var earliest = 0;
    for (var i = 1; i < box.item_summaries.length; i++) {
      if ((box.item_summaries[i].delivery_sequence || 0) < (box.item_summaries[earliest].delivery_sequence || 0)) earliest = i;
    }
    var bumped = box.item_summaries.splice(earliest, 1)[0];
    recalcBox(box);
    if (fromIdx + 1 >= plan.length) {
      var bt = smallestFitting(bumped.estimated_volume_cm3 || 0, bumped.estimated_weight_kg || 0);
      plan.push({ box_no: plan.length + 1, box_type_id: bt.id, box_type_name: bt.name,
        usable_capacity_cm3: bt.usable_capacity, max_weight_kg: bt.max_weight_kg || 0,
        item_summaries: [], estimated_fill_cm3: 0, estimated_fill_pct: 0, estimated_weight_kg: 0, warnings: [] });
    }
    plan[fromIdx + 1].item_summaries.push(bumped);
    recalcBox(plan[fromIdx + 1]);
    cascadeOverflow(fromIdx + 1);
    cascadeOverflow(fromIdx);
  }
```

**REPLACE WITH (full fixed function):**

```javascript
  function cascadeOverflow(fromIdx, depth) {
    depth = depth || 0;
    if (depth === 0) bpCascadeWarned = false;
    if (depth > BP_MAX_CASCADE_DEPTH) {
      if (!bpCascadeWarned) {
        bpCascadeWarned = true;
        alert('Box planning stopped after too many overflow moves — please review box sizes and item assignments manually.');
      }
      return;
    }
    var box = plan[fromIdx];
    if (!box || !box.usable_capacity_cm3 || box.estimated_fill_cm3 <= box.usable_capacity_cm3) return;
    var earliest = 0;
    for (var i = 1; i < box.item_summaries.length; i++) {
      if ((box.item_summaries[i].delivery_sequence || 0) < (box.item_summaries[earliest].delivery_sequence || 0)) earliest = i;
    }
    var bumped = box.item_summaries.splice(earliest, 1)[0];
    recalcBox(box);
    var largestBt = boxTypes.slice().sort(function(a,b){ return b.usable_capacity - a.usable_capacity; })[0];
    if (largestBt && largestBt.usable_capacity > 0 &&
        (bumped.estimated_volume_cm3 || 0) > largestBt.usable_capacity) {
      box.item_summaries.push(bumped);
      recalcBox(box);
      if (!bpCascadeWarned) {
        bpCascadeWarned = true;
        alert('Item ' + (bumped.item_code || '?') + ' is too large for any available box type — please adjust box sizes or item dimensions.');
      }
      return;
    }
    if (fromIdx + 1 >= plan.length) {
      var bt = smallestFitting(bumped.estimated_volume_cm3 || 0, bumped.estimated_weight_kg || 0);
      plan.push({ box_no: plan.length + 1, box_type_id: bt.id, box_type_name: bt.name,
        usable_capacity_cm3: bt.usable_capacity, max_weight_kg: bt.max_weight_kg || 0,
        item_summaries: [], estimated_fill_cm3: 0, estimated_fill_pct: 0, estimated_weight_kg: 0, warnings: [] });
    }
    plan[fromIdx + 1].item_summaries.push(bumped);
    recalcBox(plan[fromIdx + 1]);
    cascadeOverflow(fromIdx + 1, depth + 1);
    cascadeOverflow(fromIdx, depth + 1);
  }
```

Notes:
- The oversized item is left in its original box (overfull), where `recalcBox` already adds the visible per-box warning "Box exceeds capacity (...)", so the user can see exactly which box is affected after the alert.
- `bpChangeType`, `bpMoveItem`, and `bpRemoveBox` call `cascadeOverflow(bi)` / `cascadeOverflow(toBi)` / `cascadeOverflow(plan.length - 1)` with one argument — these calls remain valid and reset `bpCascadeWarned` because `depth` defaults to 0.

### Testing checklist (Bug 2)

- Normal flow: generate a recommendation, move an item into a box so it overflows — overflow still cascades into the next/new box and the editor re-renders.
- Oversized-item case: change a box's type (via the type dropdown on a box card) to the smallest type while it contains an item bigger than the largest box type's usable capacity — a single alert appears naming the item code, the editor stays alive (no "Maximum call stack size exceeded" in the console), and the box shows the "Box exceeds capacity" warning.
- Confirm only ONE alert appears per user action, even though `cascadeOverflow` recurses on both `fromIdx + 1` and `fromIdx`.
- Use `bpRemoveBox` to delete a box whose items overflow the last box — items still cascade correctly.
- Check the browser console for errors during the above — there should be none.

---

## Bug 3 — `NameError: logger` swallows the real exception in `pre_plan_boxes`

**File:** `blueprints/cooler_picking.py`

The module never defines a module-level `logger` and never imports `logging` at module level (the only logging imports are function-local aliases: `import logging as _l` around line 842 and `import logging as _log` around line 1624; everywhere else the file uses `current_app.logger`). But the `except` handler in `pre_plan_boxes` (approx. line 1785–1788) calls `logger.exception(...)`. When any DB error occurs during pre-planning, the handler itself raises `NameError: name 'logger' is not defined` after the rollback — the original exception is lost, the user gets an undiagnosable HTTP 500, and nothing is logged. The fix is a two-line module-level addition; the except handler then works as written and needs no change.

**Current except handler for reference (do NOT change it):** approx. lines 1785–1788:

```python
    except Exception as e:
        db.session.rollback()
        logger.exception("pre_plan_boxes failed for route %s", route_id)
        flash(f"Pre-planning failed: {e}", "danger")
```

**Edit 1 — add the `logging` import.** At the very top of the file (the import block starts at approx. line 21, right after the module docstring):

**FIND (exact current code):**

```python
from datetime import datetime, date
from io import BytesIO
```

**REPLACE WITH:**

```python
import logging
from datetime import datetime, date
from io import BytesIO
```

**Edit 2 — define the module-level logger.** Immediately after the last import (approx. lines 38–39, before the `# Per-permission role allow-lists...` comment block):

**FIND (exact current code):**

```python
from services.cooler_box_planner import generate_box_plan
from timezone_utils import get_utc_now
```

**REPLACE WITH:**

```python
from services.cooler_box_planner import generate_box_plan
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)
```

### Testing checklist (Bug 3)

- `python -c "import blueprints.cooler_picking"` (or start the app) — module imports cleanly with no errors.
- Trigger a DB failure inside `pre_plan_boxes` (e.g. temporarily rename the `cooler_box_items` table in a dev DB, or raise inside the `try` block), then POST to `/cooler/route/<route_id>/<delivery_date>/pre-plan` as a warehouse manager.
- Confirm the response is a redirect with the flash "Pre-planning failed: ..." (not an HTTP 500).
- Confirm the application log now contains "pre_plan_boxes failed for route ..." with the full traceback of the ORIGINAL exception (no `NameError: name 'logger' is not defined`).
- Run a successful pre-plan on a clean route to confirm the happy path (boxes created, success flash) is unaffected.
