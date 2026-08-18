# Replit — collapse the post-pick flow into one "ready to pack" screen

Today, after the last item: `ready-for-packing` page → print → pack → a separate "mark as packed". Replace it with **one focused screen** (`ready_for_packing.html`, route `/picker/invoice/<no>/ready-for-packing`) that is the packing hub: summary at the top, one tap to print, one big button to finish. Fewer taps, no navigating between print and completion, and the single prominent action reinforces tapping "packed" only when packing is actually done (accurate `packing_complete_time`).

## What the one screen shows (top to bottom)
1. **Status + customer.** Green "Picked — ready to pack", then `customer_name` large, then `Route · Stop · Driver`.
2. **Totals.** Three tiles: lines, pieces (`total_items`), weight (`total_weight`).
3. **Exceptions / short-picks banner** (amber) — only if present. List short-picked items (`qty_picked < qty`) and any `PickingException`. The packer must see this before sealing.
4. **Print delivery slip** — secondary button, opens the slip (`print_invoice`). One tap, no auto-print dialog.
5. **Mark as packed** — the primary action: full-width, tall, bottom of screen (thumb reach), submits `POST /picker/invoice/<no>/mark-as-packed`.
6. **Back to picking** — small text link for corrections.

## Design rules (handheld-first)
- One primary button only (Mark as packed). Everything else is secondary/text.
- Big touch targets: primary ≥ 52px tall, full width; secondary ≥ 44px.
- No auto-print on load — print is a deliberate tap.
- Plain language: "Ready to pack", "Mark as packed" — no status codes.

## Route change (`ready_for_packing` in routes.py)
Pass the data the banner needs:
```python
short_picks = [it for it in invoice.items if (it.qty_picked or 0) < (it.qty or 0)]
return render_template('ready_for_packing.html',
                       invoice=invoice,
                       exceptions=exceptions,
                       short_picks=short_picks,
                       now=datetime.now())
```

## Template (`ready_for_packing.html`)
```html
<div class="pack-screen">
  <div class="hdr">
    <div class="ok"><i class="ti ti-circle-check"></i> Picked — ready to pack</div>
    <div class="cust">{{ invoice.customer_name }}</div>
    <div class="meta">Route {{ route_name }} · Stop {{ stop_number or '—' }} · {{ driver_name }}</div>
  </div>

  <div class="totals">
    <div><b>{{ invoice.total_lines }}</b><span>lines</span></div>
    <div><b>{{ invoice.total_items }}</b><span>pieces</span></div>
    <div><b>{{ invoice.total_weight|round(0)|int }}</b><span>kg</span></div>
  </div>

  {% if short_picks or exceptions %}
  <div class="alert">
    <i class="ti ti-alert-triangle"></i>
    <div>
      {% for it in short_picks %}<div><b>Short:</b> {{ it.item_name }} — {{ it.qty_picked }} of {{ it.qty }}</div>{% endfor %}
      {% for ex in exceptions %}<div><b>Issue:</b> {{ ex.item_name or ex.item_code }} — {{ ex.reason }}</div>{% endfor %}
      <div class="hint">Check before sealing.</div>
    </div>
  </div>
  {% endif %}

  <a class="btn-secondary" href="{{ url_for('print_invoice', invoice_no=invoice.invoice_no) }}" target="_blank">
    <i class="ti ti-printer"></i> Print delivery slip</a>

  <form method="POST" action="{{ url_for('mark_as_packed', invoice_no=invoice.invoice_no) }}">
    <button type="submit" class="btn-primary"><i class="ti ti-package"></i> Mark as packed</button>
  </form>
  <a class="back" href="{{ url_for('pick_item', invoice_no=invoice.invoice_no) }}">Back to picking</a>
</div>

<style>
  .pack-screen{max-width:460px;margin:0 auto;font-family:Arial,sans-serif}
  .hdr{background:#EAF3DE;border-radius:12px;padding:14px 16px}
  .ok{color:#3B6D11;font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px}
  .cust{font-size:24px;font-weight:700;margin-top:6px}
  .meta{color:#555;font-size:13px;margin-top:2px}
  .totals{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}
  .totals div{background:#f4f2ec;border-radius:8px;text-align:center;padding:12px 4px}
  .totals b{display:block;font-size:22px}.totals span{font-size:12px;color:#555}
  .alert{background:#FAEEDA;border-radius:8px;padding:12px;display:flex;gap:8px;color:#854F0B;font-size:13px;margin-bottom:12px}
  .alert .hint{color:#7a6a3a;margin-top:4px}
  .btn-secondary{display:flex;align-items:center;justify-content:center;gap:8px;height:46px;border:1px solid #bbb;border-radius:10px;text-decoration:none;color:#222;font-size:15px;margin-bottom:10px}
  .btn-primary{width:100%;height:54px;border:none;border-radius:10px;background:#1D9E75;color:#fff;font-size:17px;font-weight:600;display:flex;align-items:center;justify-content:center;gap:8px}
  .back{display:block;text-align:center;margin-top:12px;color:#666;font-size:13px;text-decoration:none}
</style>
```
(`route_name`, `stop_number`, `driver_name` reuse the bindings the current page already has. Inline CSS shown for clarity; move to your stylesheet if preferred. Colors are literal here because this is a picker-facing print/native screen, not a CDS surface.)

## Paste to the Replit Agent
> Redesign `ready_for_packing.html` into one handheld-friendly screen and stop the multi-step flow. Top: a green "Picked — ready to pack" header with `customer_name` large and Route/Stop/Driver under it. Then three tiles (lines, pieces, weight). Then an amber banner listing short-picks (`qty_picked < qty`) and any PickingException — only if present. Then a secondary "Print delivery slip" button (opens `print_invoice`, no auto-print). Then a full-width, 54px, green primary "Mark as packed" button submitting `mark_as_packed`, with a small "Back to picking" link under it. In the `ready_for_packing` route, also pass `short_picks`. One primary button only; large touch targets; plain language, no status codes.

## Why this is better
- Removes the hop between the print page and a separate mark-packed step — it's one screen.
- One obvious next action → no hesitation, faster per order.
- Short-picks/exceptions are seen before sealing, cutting delivery errors.
- The single prominent "Mark as packed" is the accurate packing-time signal we want (ties into the occupancy reporting).
