---
name: Cooler planner test mock pattern
description: How to correctly mock DB calls in test_cooler_box_planner.py for auto vs manual box-type modes.
---

In `services/cooler_box_planner.py`, `generate_box_plan`:
- **Auto mode** (`box_type_id=None`): calls `db.session.execute(...).fetchall()` on `cooler_box_types` query — returns list of (id, name, volume, efficiency, max_weight) tuples, sorted largest→smallest.
- **Manual mode** (`box_type_id=<int>`): calls `.fetchone()` for the single matching box type.

**Why:** Discovered when 9 TestPlannerLogic tests all failed silently — mock only set `fetchone`, so `fetchall()` returned a default MagicMock, iteration yielded nothing, `all_box_types = []`, early return `[]`.

**How to apply:** In `_plan_with_rows()` test helper, branch on `box_type_id is None`:
```python
if box_type_id is None:
    mock_result.fetchall.return_value = auto_rows  # list of tuples
else:
    mock_result.fetchone.return_value = single_row
```
Use `extra_box_types` param to pass multiple box type rows for smart-allocation tests.

**Also:** `_is_cooler_route_pack_complete` has **6** scalar queries (order: unsequenced, pending, planned, unboxed, open_with_items, duplicates). The `planned` check (cooler_box_items.status='planned') was added at position 2. Test mock must supply 6 counts.

**Row-tuple schema drift:** The picked-queue SELECT in `generate_box_plan` is read positionally; the planner now reads `r[9]` (`queue_status`). The `_make_row`/`_flat` helpers must build a tuple with **all** columns the planner indexes — when a column is added to the query, update both helpers in lockstep or every TestPlannerLogic test dies with `IndexError: tuple index out of range`. **Why:** all 11 TestPlannerLogic tests failed this way because `_flat()` stopped one column short of `r[9]`.
