# EP SmartGrowth — Cooler Box Smart Allocation

## What this changes

Currently the planner uses **one box type** for the entire route — always the largest.
This wastes space on stops with few items and gives no opportunity to use smaller boxes.

The new behaviour (when **auto** is selected):
- Groups stops last-first (unchanged — Box 1 = last stops, LIFO truck loading)
- Accumulates stops using the **largest box** as the boundary
- At flush time, assigns the **smallest box type that actually fits** the accumulated volume
- Result: last stops (many items, heavy) → Large box goes in first/bottom of truck;
  first stops (few items, light) → Small/Medium box goes in last/top of truck
- When a specific box type is manually selected, behaviour is unchanged

---

## CHANGE 1 — `services/cooler_box_planner.py` — replace `generate_box_plan`

Replace the **entire** `generate_box_plan` function with the version below.
Do not change anything above it (the module docstring, imports, `STOP_ORDER`, `_num`).

```python
def generate_box_plan(route_id, delivery_date, box_type_id=None):
    """Generate a box plan for all picked-but-unboxed cooler items on a route.

    Auto mode (box_type_id=None):
      - Loads all active box types.
      - Uses the largest box capacity as the overflow boundary.
      - At flush time assigns the SMALLEST box type that fits the accumulated
        volume and weight — so last stops (many items) get large boxes and
        early stops (few items) get smaller boxes.
      - Truck loading: Box 1 (large, last stops) loaded first/bottom;
        last box (small, first stops) loaded last/top.

    Manual mode (box_type_id supplied):
      - Uses only the specified box type (existing behaviour).

    Returns [] when there are no eligible items or no active box types.
    Returns {"ok": False, "plan": [], "message": "..."} when sequencing is missing.
    """
    route_id = int(route_id)
    delivery_date = str(delivery_date)

    # ── Load box types ──────────────────────────────────────────────────────
    auto_mode = (box_type_id is None)

    if not auto_mode:
        # Manual: single box type (existing behaviour)
        try:
            row = db.session.execute(
                text(
                    "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                    "FROM cooler_box_types WHERE id = :id AND is_active = true"
                ),
                {"id": int(box_type_id)},
            ).fetchone()
        except Exception:
            row = None
        if row is None:
            logger.warning("generate_box_plan: specified box type %s not found", box_type_id)
            return []
        all_box_types = [_make_box_type(row)]
    else:
        # Auto: all active box types, sorted largest → smallest by usable capacity
        try:
            rows = db.session.execute(
                text(
                    "SELECT id, name, internal_volume_cm3, fill_efficiency, max_weight_kg "
                    "FROM cooler_box_types WHERE is_active = true "
                    "ORDER BY (internal_volume_cm3 * COALESCE(fill_efficiency, 1)) DESC, "
                    "         sort_order, name"
                )
            ).fetchall()
        except Exception:
            rows = []
        if not rows:
            logger.warning("generate_box_plan: no active box types found")
            return []
        all_box_types = [_make_box_type(r) for r in rows]

    # Largest box defines the accumulation boundary in auto mode
    largest = all_box_types[0]
    boundary_capacity = largest["usable_capacity"]
    boundary_weight   = largest["max_weight"] if largest["max_weight"] else None

    # ── Fetch picked-but-unboxed cooler queue rows ───────────────────────────
    rows = db.session.execute(
        text(
            "SELECT bpq.id, bpq.invoice_no, bpq.item_code, "
            "       COALESCE(bpq.qty_picked, bpq.qty_required, 1) AS qty, "
            "       i.customer_code, i.customer_name, "
            "       rs.route_stop_id, rs.seq_no, ii.item_name "
            "FROM batch_pick_queue bpq "
            "JOIN invoices i ON i.invoice_no = bpq.invoice_no "
            "JOIN shipments s ON s.id = i.route_id "
            "LEFT JOIN route_stop_invoice rsi "
            "       ON rsi.invoice_no = bpq.invoice_no AND rsi.is_active = :truthy "
            "LEFT JOIN route_stop rs "
            "       ON rs.route_stop_id = rsi.route_stop_id "
            "LEFT JOIN invoice_items ii "
            "       ON ii.invoice_no = bpq.invoice_no AND ii.item_code = bpq.item_code "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND bpq.status = 'picked' "
            "  AND i.route_id = :rid "
            "  AND s.delivery_date = :dd "
            "  AND NOT EXISTS ("
            "        SELECT 1 FROM cooler_box_items cbi "
            "        WHERE cbi.queue_item_id = bpq.id"
            "  ) "
            "ORDER BY COALESCE(rs.seq_no, 0) {order}, bpq.invoice_no, bpq.item_code".format(
                order="DESC" if STOP_ORDER == "last_first" else "ASC"
            )
        ),
        {"rid": route_id, "dd": delivery_date, "truthy": True},
    ).fetchall()

    if not rows:
        return []

    # Guard: all items must have a delivery sequence
    missing_seq = [r for r in rows if r[7] is None]
    if missing_seq:
        invoice_samples = list({r[1] for r in missing_seq})[:5]
        return {
            "ok": False,
            "plan": [],
            "message": (
                f"{len(missing_seq)} picked item(s) have no delivery sequence "
                f"(e.g. invoice(s): {', '.join(invoice_samples)}). "
                "Please run Refresh Route Order / Prepare Cooler Route first, "
                "then generate the box plan again."
            ),
        }

    # ── Bulk-fetch item dimensions ──────────────────────────────────────────
    item_codes = list({r[2] for r in rows})
    dim_map = {}
    if item_codes:
        try:
            dim_rows = db.session.execute(
                text(
                    "SELECT item_code_365, item_length, item_width, item_height, item_weight "
                    "FROM ps_items_dw WHERE item_code_365 = ANY(:codes)"
                ),
                {"codes": item_codes},
            ).fetchall()
            for dr in dim_rows:
                dim_map[dr[0]] = {
                    "length": _num(dr[1]),
                    "width":  _num(dr[2]),
                    "height": _num(dr[3]),
                    "weight": _num(dr[4]),
                }
        except Exception as exc:
            logger.warning("generate_box_plan: dimension lookup failed: %s", exc)

    # ── Group rows by stop sequence ─────────────────────────────────────────
    by_stop = defaultdict(list)
    for r in rows:
        by_stop[_num(r[7])].append(r)

    stops = sorted(
        by_stop.keys(),
        key=lambda x: x if x is not None else -1,
        reverse=(STOP_ORDER == "last_first"),
    )

    # ── Pack stops into boxes ───────────────────────────────────────────────
    plan     = []
    box_no   = 1
    current  = None   # accumulator dict

    def _stop_int(s):
        return int(s) if s is not None else 0

    def _choose_box_type(vol_cm3, weight_kg):
        """Return smallest active box type that fits; fall back to largest."""
        if not auto_mode:
            return all_box_types[0]
        fitting = [
            bt for bt in reversed(all_box_types)   # smallest first
            if (bt["usable_capacity"] <= 0 or bt["usable_capacity"] >= vol_cm3)
            and (bt["max_weight"] is None or bt["max_weight"] >= weight_kg)
        ]
        return fitting[0] if fitting else all_box_types[0]

    def flush_box():
        nonlocal current, box_no
        if not current:
            return
        vol  = current["estimated_fill_cm3"]
        wt   = current["estimated_weight_kg"]
        bt   = _choose_box_type(vol, wt)
        uc   = bt["usable_capacity"]
        mw   = bt["max_weight"]

        current["box_type_id"]   = bt["id"]
        current["box_type_name"] = bt["name"]
        current["estimated_fill_pct"] = round((vol / uc) * 100, 1) if uc else 0

        if uc and vol > uc:
            current["warnings"].append(
                f"This box exceeds capacity ({vol:.0f} cm³ > {uc:.0f} cm³ usable)."
            )
        if mw and wt > mw:
            current["warnings"].append(
                f"This box exceeds weight limit ({wt:.1f} kg > {mw:.1f} kg)."
            )
        plan.append(current)
        box_no += 1
        current = None

    for stop_seq in stops:
        stop_rows   = by_stop[stop_seq]
        stop_volume = 0.0
        stop_weight = 0.0
        missing_count = 0
        item_summaries  = []
        queue_item_ids  = []
        stop_no = _stop_int(stop_seq)

        for r in stop_rows:
            qty   = _num(r[3]) or 1.0
            dims  = dim_map.get(r[2], {})
            l, w, h = dims.get("length"), dims.get("width"), dims.get("height")
            wgt   = dims.get("weight")
            est_vol = 0.0
            est_wt  = 0.0
            if l is None or w is None or h is None:
                missing_count += 1
            else:
                est_vol = l * w * h * qty
            if wgt is not None:
                est_wt = wgt * qty
            stop_volume += est_vol
            stop_weight += est_wt
            queue_item_ids.append(int(r[0]))
            item_summaries.append({
                "queue_item_id":      int(r[0]),
                "invoice_no":         r[1],
                "customer_code":      r[4],
                "customer_name":      r[5],
                "route_stop_id":      r[6],
                "delivery_sequence":  stop_seq,
                "item_code":          r[2],
                "item_name":          r[8],
                "qty":                qty,
                "estimated_volume_cm3":  est_vol,
                "estimated_weight_kg":   est_wt,
            })

        # Check if stop fits in current accumulation (against boundary = largest box)
        acc_vol = (current["estimated_fill_cm3"] if current else 0.0) + stop_volume
        acc_wt  = (current["estimated_weight_kg"] if current else 0.0) + stop_weight
        fits_vol = boundary_capacity <= 0 or acc_vol <= boundary_capacity
        fits_wt  = boundary_weight is None or acc_wt <= boundary_weight

        if current is not None and not (fits_vol and fits_wt):
            flush_box()

        if current is None:
            current = {
                "box_no":                  box_no,
                "box_type_id":             None,      # assigned at flush
                "box_type_name":           None,
                "stop_min":                stop_no,
                "stop_max":                stop_no,
                "stop_display":            f"Stop {stop_no}" if stop_seq is not None else "No stop",
                "stops":                   [stop_no],
                "queue_item_ids":          list(queue_item_ids),
                "item_summaries":          list(item_summaries),
                "estimated_fill_cm3":      stop_volume,
                "estimated_fill_pct":      0,
                "estimated_weight_kg":     stop_weight,
                "missing_dimension_count": missing_count,
                "warnings":                [],
            }
        else:
            current["stops"] = sorted(set(current["stops"] + [stop_no]), reverse=True)
            current["stop_min"] = min(current["stops"])
            current["stop_max"] = max(current["stops"])
            current["stop_display"] = (
                f"Stops {current['stop_max']} → {current['stop_min']}"
                if current["stop_min"] != current["stop_max"]
                else f"Stop {current['stop_max']}"
            )
            current["queue_item_ids"].extend(queue_item_ids)
            current["item_summaries"].extend(item_summaries)
            current["estimated_fill_cm3"]      += stop_volume
            current["estimated_weight_kg"]     += stop_weight
            current["missing_dimension_count"] += missing_count

        if missing_count:
            warn = "Some items are missing dimensions — fill estimate may be low."
            if warn not in current["warnings"]:
                current["warnings"].append(warn)

    flush_box()
    return plan
```

---

## CHANGE 2 — Add helper `_make_box_type` above `generate_box_plan`

Add this small helper function **immediately above** `generate_box_plan`:

```python
def _make_box_type(row):
    """Materialise a cooler_box_types DB row into a dict."""
    vol = _num(row[2]) or 0.0
    fe  = _num(row[3]) or 1.0
    mw  = _num(row[4])
    return {
        "id":              row[0],
        "name":            row[1],
        "internal_volume_cm3": vol,
        "fill_efficiency": fe,
        "usable_capacity": vol * fe,
        "max_weight":      mw if mw and mw > 0 else None,
    }
```

---

## CHANGE 3 — Update the dropdown label in the template

**File:** `templates/cooler/route_picking.html`

The dropdown currently says "— auto choose largest —". Update it to reflect the new behaviour:

Find:
```html
<option value="">— auto choose largest —</option>
```

**Replace with:**
```html
<option value="">— auto: best fit combination —</option>
```

---

## How the result changes

**Example: route with 6 stops, 3 box types (Small 15L, Medium 37L, Large 61L)**

| Box | Stops | Content | Volume | Assigned type | Truck position |
|---|---|---|---|---|---|
| Box 1 | Stops 6→4 | Last 3 stops (busy) | 45 L | **Large** | Bottom (loaded first) |
| Box 2 | Stops 3→2 | Middle stops | 25 L | **Medium** | Middle |
| Box 3 | Stop 1 | First stop (light) | 8 L | **Small** | Top (loaded last) |

Driver delivers Stop 1 first → opens top box. Stop 6 last → opens bottom box. ✓

---

## No schema or migration changes needed

Only `services/cooler_box_planner.py` and the template change. The existing
`pre_pick_estimate` function in the same file is not affected.
