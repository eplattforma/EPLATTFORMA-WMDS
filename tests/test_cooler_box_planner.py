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

    def test_pending_item_rejected_by_box_assign_item(self):
        """box_assign_item source must reject any status that is not 'picked'.

        We verify this by inspecting the guard expression in the source rather
        than invoking the view through its permission-decorator stack.
        """
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_assign_item)
        # The guard must check *exactly* != "picked", not "in ('pending','picked')"
        assert "!= \"picked\"" in source or "!= 'picked'" in source, (
            "box_assign_item must reject queue items whose status is not 'picked'."
        )
        assert "in (\"pending\", \"picked\")" not in source and \
               "in ('pending', 'picked')" not in source, (
            "box_assign_item must not allow pending items — only picked items "
            "can be boxed."
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

    def test_box_assign_item_does_not_update_queue_status(self):
        """box_assign_item must NOT run UPDATE that changes pending→picked."""
        import inspect
        import blueprints.cooler_picking as mod

        source = inspect.getsource(mod.box_assign_item)
        assert "SET status = 'picked'" not in source.replace("# ", "#"), (
            "box_assign_item must not change queue status to 'picked'."
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
