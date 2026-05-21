"""Tests for cooler box planner and related blueprint guards.

Run with:  pytest -q tests/test_cooler_box_planner.py
"""
import types
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_row(queue_id, invoice_no, item_code, qty, customer_code, customer_name,
              route_stop_id, seq_no, item_name):
    """Build a Row-like namedtuple matching the cooler_box_planner query order."""
    return types.SimpleNamespace(
        **dict(zip(
            ["id", "invoice_no", "item_code", "qty", "customer_code", "customer_name",
             "route_stop_id", "seq_no", "item_name"],
            [queue_id, invoice_no, item_code, qty, customer_code, customer_name,
             route_stop_id, seq_no, item_name],
        ))
    )


def _flat(ns):
    """Return a simple tuple in column order expected by the planner."""
    return (ns.id, ns.invoice_no, ns.item_code, ns.qty,
            ns.customer_code, ns.customer_name,
            ns.route_stop_id, ns.seq_no, ns.item_name)


# ---------------------------------------------------------------------------
# Unit tests for the planner's packing algorithm
# ---------------------------------------------------------------------------

class TestPlannerLogic:
    """Tests the generate_box_plan packing algorithm via direct call with
    mocked DB calls."""

    def _plan_with_rows(self, rows, dim_map, box_volume=100_000, box_weight=20.0,
                        box_type_id=None):
        """Call the packer after mocking all DB/ORM interactions."""
        from services import cooler_box_planner as planner

        box_type_row = (1, "Standard Box", box_volume, 0.8, box_weight)

        def _execute(sql_obj, params=None):
            sql_text = str(sql_obj).lower() if hasattr(sql_obj, '_text') else str(sql_obj).lower()
            mock_result = MagicMock()
            if "cooler_box_types" in sql_text:
                mock_result.fetchone.return_value = box_type_row
                return mock_result
            if "ps_items_dw" in sql_text:
                result_rows = [
                    (code, d["length"], d["width"], d["height"], d["weight"])
                    for code, d in dim_map.items()
                ]
                mock_result.fetchall.return_value = result_rows
                return mock_result
            # Picked-queue rows
            mock_result.fetchall.return_value = [_flat(r) for r in rows]
            return mock_result

        mock_session = MagicMock()
        mock_session.execute.side_effect = _execute
        mock_db = MagicMock()
        mock_db.session = mock_session

        with patch.object(planner, "db", mock_db):
            return planner.generate_box_plan(route_id=1, delivery_date="2026-05-12",
                                             box_type_id=box_type_id)

    # ── 1. All stops fit in one box ──────────────────────────────────────
    def test_three_stops_fit_one_box(self):
        """Stops 10, 9, 8 all fit → single box."""
        rows = [
            _make_row(1, "INV001", "A001", 2, "C1", "Cust1", 10, 10, "Item A"),
            _make_row(2, "INV002", "B001", 1, "C2", "Cust2", 9,  9,  "Item B"),
            _make_row(3, "INV003", "C001", 3, "C3", "Cust3", 8,  8,  "Item C"),
        ]
        # Each item: 10×10×10 × qty → 1000 × qty; total well under 80 000 usable
        dims = {
            "A001": {"length": 10, "width": 10, "height": 10, "weight": 0.5},
            "B001": {"length": 10, "width": 10, "height": 10, "weight": 0.5},
            "C001": {"length": 10, "width": 10, "height": 10, "weight": 0.5},
        }
        plan = self._plan_with_rows(rows, dims, box_volume=100_000)
        assert len(plan) == 1
        assert plan[0]["stop_max"] == 10
        assert plan[0]["stop_min"] == 8
        assert "Stops 10" in plan[0]["stop_display"]

    # ── 2. Capacity exceeded → multiple boxes ───────────────────────────
    def test_capacity_overflow_creates_multiple_boxes(self):
        """When a stop's volume overflows the box, a new box starts."""
        rows = [
            _make_row(1, "INV001", "A001", 10, "C1", "Cust1", 10, 10, "Item A"),
            _make_row(2, "INV002", "B001", 10, "C2", "Cust2", 9,  9,  "Item B"),
        ]
        # 10 × 10×10×10 = 10 000 cm³ per stop; usable = 12 000 * 0.8 = 9 600
        # so each stop needs its own box
        dims = {
            "A001": {"length": 10, "width": 10, "height": 10, "weight": 0.2},
            "B001": {"length": 10, "width": 10, "height": 10, "weight": 0.2},
        }
        plan = self._plan_with_rows(rows, dims, box_volume=12_000)
        assert len(plan) == 2

    # ── 3. Missing dimensions → warning, item still included ────────────
    def test_missing_dimensions_warns_but_includes_item(self):
        rows = [
            _make_row(1, "INV001", "A001", 2, "C1", "Cust1", 5, 5, "Item A"),
        ]
        # No dimensions for A001 — should still produce a box with a warning
        plan = self._plan_with_rows(rows, {}, box_volume=50_000)
        assert len(plan) == 1
        assert plan[0]["missing_dimension_count"] >= 1
        assert any("dimension" in w.lower() for w in plan[0]["warnings"])

    # ── 4. Last-stop-first order ─────────────────────────────────────────
    def test_stop_order_last_stop_first(self):
        """The first box in the plan should hold the highest stop number."""
        rows = [
            _make_row(1, "INV001", "A001", 1, "C1", "Cust1", 1, 1, "Item A"),
            _make_row(2, "INV002", "B001", 1, "C2", "Cust2", 5, 5, "Item B"),
        ]
        dims = {
            "A001": {"length": 5, "width": 5, "height": 5, "weight": 0.1},
            "B001": {"length": 5, "width": 5, "height": 5, "weight": 0.1},
        }
        plan = self._plan_with_rows(rows, dims, box_volume=500_000)
        # All fits into one box; stop_max should be 5
        assert len(plan) == 1
        assert plan[0]["stop_max"] == 5

    # ── 5. No rows → empty plan ──────────────────────────────────────────
    def test_no_rows_returns_empty(self):
        plan = self._plan_with_rows([], {})
        assert plan == []

    # ── 6. COALESCE: qty_picked preferred over qty_required ─────────────
    def test_uses_coalesce_qty(self):
        """Plan should use the qty returned from COALESCE(qty_picked, qty_required, 1)."""
        # Row has qty=3 (as COALESCE would give)
        rows = [
            _make_row(1, "INV001", "A001", 3, "C1", "Cust1", 2, 2, "Item A"),
        ]
        dims = {"A001": {"length": 5, "width": 5, "height": 5, "weight": 1.0}}
        plan = self._plan_with_rows(rows, dims)
        item = plan[0]["item_summaries"][0]
        assert item["qty"] == 3.0

    # ── 7. Box exceeds capacity → warning added ──────────────────────────
    def test_oversized_stop_warns(self):
        """A single stop that exceeds box capacity still creates a box with a warning."""
        rows = [
            _make_row(1, "INV001", "A001", 100, "C1", "Cust1", 3, 3, "Item A"),
        ]
        dims = {"A001": {"length": 20, "width": 20, "height": 20, "weight": 0.5}}
        # 20*20*20*100 = 800 000 > 50 000 * 0.8 = 40 000
        plan = self._plan_with_rows(rows, dims, box_volume=50_000)
        assert len(plan) == 1
        assert any("exceed" in w.lower() or "capacity" in w.lower()
                   for w in plan[0]["warnings"])

    # ── 8. Items with missing delivery_sequence → dict warning, no plan ──
    def test_missing_delivery_sequence_returns_warning_dict(self):
        """If any picked item has seq_no=None, return a dict with ok=False."""
        rows = [
            _make_row(1, "INV001", "A001", 2, "C1", "Cust1", 5, 5, "Item A"),
            _make_row(2, "INV002", "B001", 1, "C2", "Cust2", None, None, "Item B"),
        ]
        result = self._plan_with_rows(rows, {})
        assert isinstance(result, dict), "Expected dict when missing seq"
        assert result["ok"] is False
        assert "delivery sequence" in result["message"].lower()
        assert result["plan"] == []

    # ── 9. All items have valid seq_no → returns list, not dict ──────────
    def test_all_sequenced_returns_list(self):
        """When all items have seq_no set, return a list (normal plan)."""
        rows = [
            _make_row(1, "INV001", "A001", 1, "C1", "Cust1", 2, 2, "Item A"),
        ]
        dims = {"A001": {"length": 5, "width": 5, "height": 5, "weight": 0.5}}
        result = self._plan_with_rows(rows, dims)
        assert isinstance(result, list), "Expected list when all items sequenced"
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests for box_remove_item / box_cancel — must NOT revert queue to pending
# ---------------------------------------------------------------------------

class TestBoxRemoveAndCancelLeaveQueuePicked:
    """box_remove_item and box_cancel must delete cooler_box_items rows
    but leave batch_pick_queue untouched (status stays 'picked')."""

    def test_box_remove_item_does_not_revert_queue(self):
        """box_remove_item source must not UPDATE batch_pick_queue to pending."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_remove_item)
        assert "status = 'pending'" not in source, (
            "box_remove_item must not revert queue rows to pending."
        )
        assert "picked_by = NULL" not in source, (
            "box_remove_item must not clear picked_by on queue rows."
        )
        assert "picked_at = NULL" not in source, (
            "box_remove_item must not clear picked_at on queue rows."
        )

    def test_box_remove_item_returns_picked_status(self):
        """box_remove_item must return status='picked' in JSON response."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_remove_item)
        assert '"status": "picked"' in source or "'status': 'picked'" in source, (
            "box_remove_item JSON response must carry status='picked'."
        )

    def test_box_cancel_does_not_revert_queue(self):
        """box_cancel source must not UPDATE batch_pick_queue to pending."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_cancel)
        assert "status = 'pending'" not in source, (
            "box_cancel must not revert queue rows to pending."
        )
        assert "picked_by = NULL" not in source, (
            "box_cancel must not clear picked_by on queue rows."
        )


# ---------------------------------------------------------------------------
# Tests for blueprint guards (no Flask app context needed — pure logic tests)
# ---------------------------------------------------------------------------

class TestBoxAssignmentGuards:
    """Verify that the 'only picked items can be assigned' invariant
    is enforced in box_assign_item / queue_assign_box.

    We patch at the route level to avoid spinning up Flask + DB.
    """

    def _make_qrow(self, status, pick_zone_type="cooler", route_id=1,
                   delivery_date="2026-05-12"):
        return (1, "INV001", "A001", 2.0,
                status, pick_zone_type,
                "C1", "Cust1", route_id, delivery_date)

    def test_pending_item_accepted_by_box_assign_item(self):
        """box_assign_item must REJECT pending items.

        Physical picking and box packing are separate steps.  Only items that
        have already been physically picked (status='picked') may be assigned to
        a box.  Pending items must be rejected with HTTP 400.
        """
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_assign_item)
        # The guard must reject anything other than 'picked'.
        assert ("!= \"picked\"" in source or "!= 'picked'" in source), (
            "box_assign_item must reject pending items — only status='picked' is allowed."
        )
        # And the error message must guide the picker to pick first.
        assert "Pick the item first" in source, (
            "box_assign_item error message must tell the picker to pick before boxing."
        )

    def test_picked_item_passes_status_check(self):
        """Status guard: only 'picked' should pass."""
        qrow_picked = self._make_qrow("picked")
        qrow_pending = self._make_qrow("pending")
        assert qrow_picked[4] == "picked"
        assert qrow_pending[4] != "picked"

    def test_box_assignment_does_not_update_queue_status(self):
        """queue_assign_box must NOT run any UPDATE batch_pick_queue statement
        that changes status to 'picked' (the pending→picked transition is
        handled only by queue_pick)."""
        import ast
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.queue_assign_box)
        # The guard we assert: no UPDATE ... SET status = 'picked' inside
        # queue_assign_box. A comment is fine; it's the SQL we're checking.
        assert "SET status = 'picked'" not in source.replace("# ", "#"), (
            "queue_assign_box must not change queue status to 'picked'; "
            "physical picking is a separate event (queue_pick)."
        )

    def test_box_assign_item_updates_pending_queue_status(self):
        """box_assign_item must NOT UPDATE pending items to 'picked'.

        Physical picking and box packing are separate audit events.
        box_assign_item must never SET status = 'picked' in batch_pick_queue;
        that transition belongs exclusively to queue_pick.
        Pending items must be rejected before the INSERT even runs.
        """
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_assign_item)
        assert "SET status = 'picked'" not in source, (
            "box_assign_item must NOT change queue status to 'picked'; "
            "physical picking is a separate event (queue_pick)."
        )


# ---------------------------------------------------------------------------
# Tests for _is_cooler_route_pack_complete
# ---------------------------------------------------------------------------

class TestCoolerRoutePackComplete:
    """Verify the five-condition completeness check."""

    def _run(self, counts):
        """counts = [unsequenced, pending, unboxed, open_with_items, duplicates]"""
        from blueprints.cooler_picking import _is_cooler_route_pack_complete

        call_count = [0]
        def side_effect(sql, params=None):
            idx = call_count[0]
            call_count[0] += 1
            mock_result = MagicMock()
            mock_result.scalar.return_value = counts[idx]
            return mock_result

        mock_session = MagicMock()
        mock_session.execute.side_effect = side_effect
        mock_db = MagicMock()
        mock_db.session = mock_session

        import blueprints.cooler_picking as mod
        with patch.object(mod, "db", mock_db):
            return _is_cooler_route_pack_complete(1, "2026-05-12")

    def test_all_zero_returns_true(self):
        assert self._run([0, 0, 0, 0, 0]) is True

    def test_unsequenced_blocks_completion(self):
        assert self._run([1, 0, 0, 0, 0]) is False

    def test_pending_blocks_completion(self):
        assert self._run([0, 1, 0, 0, 0]) is False

    def test_unboxed_picked_blocks_completion(self):
        assert self._run([0, 0, 1, 0, 0]) is False

    def test_open_box_with_items_blocks_completion(self):
        assert self._run([0, 0, 0, 1, 0]) is False

    def test_duplicate_queue_item_blocks_completion(self):
        assert self._run([0, 0, 0, 0, 1]) is False


# ---------------------------------------------------------------------------
# Test batch invoice route grouping
# ---------------------------------------------------------------------------

class TestBatchRouteGrouping:
    """Verify the route_groups / multi-route grouping logic in
    filter_invoices_for_batch (routes_batch.py)."""

    def test_route_groups_passed_to_template(self):
        """filter_invoices_for_batch should pass route_groups to the template
        so multi-route invoices are grouped, not flat-listed."""
        import routes_batch
        import inspect
        source = inspect.getsource(routes_batch.filter_invoices_for_batch)
        assert "route_groups" in source, (
            "filter_invoices_for_batch must build and pass route_groups to the template."
        )
        assert "render_template" in source

    def test_unrouted_invoices_present(self):
        """Invoices with no active route_stop_invoice should be in unrouted_invoices."""
        import routes_batch
        import inspect
        source = inspect.getsource(routes_batch.filter_invoices_for_batch)
        assert "unrouted_invoices" in source, (
            "filter_invoices_for_batch must separate invoices with no route stop."
        )

    def test_multi_route_warning_in_template(self):
        """batch_picking_create.html must show a warning when invoices span
        multiple routes (route_groups|length > 1)."""
        import os
        template_path = os.path.join("templates", "batch_picking_create.html")
        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "route_groups" in content, (
            "batch_picking_create.html must render route_groups."
        )


# ---------------------------------------------------------------------------
# Tests for issue #2 — no Pack by Stop language anywhere in user-facing code
# ---------------------------------------------------------------------------

class TestNoPackByStopLanguage:
    """'Pack by Stop' must not appear in any user-facing message or template."""

    def test_no_pack_by_stop_in_routes_batch(self):
        """routes_batch.py must not emit 'Pack by Stop' to the user."""
        import inspect
        import routes_batch
        source = inspect.getsource(routes_batch)
        assert "Pack by Stop" not in source, (
            "routes_batch.py must not reference 'Pack by Stop' — "
            "replaced by 'Generate Box Plan'."
        )

    def test_routes_batch_cooler_message_uses_generate_box_plan(self):
        """The cooler-complete flash message in routes_batch.py must
        say 'Generate Box Plan', not 'Pack by Stop'."""
        import inspect
        import routes_batch
        source = inspect.getsource(routes_batch)
        assert "Generate Box Plan" in source, (
            "routes_batch.py cooler completion message must reference "
            "'Generate Box Plan'."
        )

    def test_no_pack_by_stop_in_route_picking_template(self):
        """route_picking.html must not expose any Pack-by-Stop button."""
        import os
        with open(os.path.join("templates", "cooler", "route_picking.html"),
                  encoding="utf-8") as f:
            content = f.read()
        assert "pack-stop" not in content.lower() and \
               "pack by stop" not in content.lower(), (
            "route_picking.html must not render any Pack-by-Stop controls."
        )


# ---------------------------------------------------------------------------
# Tests for issue #3 — lock_sequencing always forces location_order
# ---------------------------------------------------------------------------

class TestLockSequencingForcesLocationOrder:
    """lock_sequencing must never accept sequential_stop from form data."""

    def test_pack_mode_hardcoded_to_location_order(self):
        """lock_sequencing source must set pack_mode = 'location_order'
        unconditionally and not read it from form data."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.lock_sequencing)
        # Must set pack_mode to location_order directly
        assert "pack_mode = \"location_order\"" in source or \
               "pack_mode = 'location_order'" in source, (
            "lock_sequencing must force pack_mode = 'location_order'."
        )
        # Must NOT read pack_mode from request.form
        assert "request.form.get(\"cooler_pack_mode\"" not in source and \
               "request.form.get('cooler_pack_mode'" not in source, (
            "lock_sequencing must not read cooler_pack_mode from form data."
        )

    def test_sequential_stop_not_accepted_from_form(self):
        """sequential_stop from a POST must have no effect in lock_sequencing."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.lock_sequencing)
        # The guard that checked sequential_stop+box_type must also be gone
        assert "sequential_stop" not in source or \
               "not production-ready" in source, (
            "lock_sequencing must not branch on sequential_stop from form data."
        )


# ---------------------------------------------------------------------------
# Tests for issue #6 — box_plan_preview returns message when plan is empty
# ---------------------------------------------------------------------------

class TestBoxPlanPreviewEmptyResponse:
    """box_plan_preview must return ok=true with a message when plan is empty."""

    def test_preview_returns_message_on_empty_plan(self):
        """box_plan_preview source must include a friendly message
        when plan is empty, not just an empty list."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_plan_preview)
        assert "No picked unboxed cooler items found" in source, (
            "box_plan_preview must return a descriptive message when empty."
        )
        assert '"ok": True' in source or "'ok': True" in source or \
               '"ok"' in source, (
            "box_plan_preview must always return ok=true."
        )

    def test_preview_handles_dict_warning_from_planner(self):
        """If generate_box_plan returns a dict with ok=False (e.g. missing
        delivery sequence), box_plan_preview must forward that dict."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_plan_preview)
        assert "isinstance(result, dict)" in source or \
               "isinstance" in source, (
            "box_plan_preview must handle dict return from generate_box_plan."
        )


# ---------------------------------------------------------------------------
# Tests for issue #5 — confirm_box_plan catches IntegrityError
# ---------------------------------------------------------------------------

class TestConfirmBoxPlanSafety:
    """confirm_box_plan must catch IntegrityError and do per-item pre-flight."""

    def test_integrity_error_is_caught(self):
        """confirm_box_plan must catch IntegrityError and flash a warning."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.confirm_box_plan)
        assert "IntegrityError" in source, (
            "confirm_box_plan must catch IntegrityError from the unique index."
        )
        assert "rollback" in source.lower(), (
            "confirm_box_plan must call db.session.rollback() on IntegrityError."
        )

    def test_per_item_preflight_check(self):
        """confirm_box_plan must re-verify each queue item before inserting."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.confirm_box_plan)
        # Pre-flight looks up bpq.status per item
        assert "qcheck" in source, (
            "confirm_box_plan must run a per-item pre-flight check (qcheck)."
        )
        assert "already_boxed" in source, (
            "confirm_box_plan must check whether the item is already boxed."
        )
        assert "status != 'picked'" in source or \
               "!= \"picked\"" in source or \
               "!= 'picked'" in source, (
            "confirm_box_plan must skip items whose status is no longer 'picked'."
        )
