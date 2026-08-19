# Replit — pack screen (ready-to-pack) fixes

Fixes for the `ready_for_packing` screen based on the live screenshot: unreadable customer name, weak/confusable print button, unclear print feedback, accidental reprints, tiny secondary links. Plus optional duplex printing.

## 1. Customer name unreadable (white on light green)
The header background is light green but the text renders white in the phone's dark mode. Force explicit dark colors in the header — don't use theme text tokens there.
```css
.hdr        { background:#EAF3DE; border-radius:12px; padding:14px; }
.hdr .ok    { color:#2f6b2f; font-weight:700; }
.hdr .cust  { color:#14311c; font-size:20px; font-weight:800; line-height:1.12; }
.hdr .meta  { color:#3d5c45; font-size:12px; }
```
(Apply to the customer-name and route lines specifically so dark mode can't invert them.)

## 2. Make Print and Mark-as-packed clearly different
Print = blue outlined (a distinct action); Mark as packed = green solid primary (the final step). Keep the sent-banner between them.
```css
.btn-print  { width:100%; height:48px; border:2px solid #2f6bd8; background:#fff;
              color:#1c4fa3; border-radius:10px; font-size:15px; font-weight:700; }
.btn-packed { width:100%; height:56px; border:none; background:#1f9e6f; color:#fff;
              border-radius:10px; font-size:17px; font-weight:800; }
```

## 3. Clear print feedback (+ real "Printed" confirmation)
Replace the small "Slip sent to printer" text with a green banner, and confirm actual printing by polling the job status until the office-PC agent acks it.

Enqueue endpoint: return the job id.
```python
# in POST /print/delivery-slip/<invoice_no>
job_id = db.session.execute(text(
  "INSERT INTO print_jobs (invoice_no, doc_type, status) VALUES (:n,'slip','queued') RETURNING id"),
  {'n': invoice_no}).scalar()
db.session.commit()
return jsonify({'ok': True, 'job_id': job_id})
```
Status endpoint:
```python
@app.route('/print/job/<int:job_id>/status')
@login_required
def print_job_status(job_id):
    row = db.session.execute(text("SELECT status FROM print_jobs WHERE id=:i"), {'i': job_id}).first()
    return jsonify({'status': row.status if row else 'unknown'})
```
Client:
```javascript
async function printSlip(invoiceNo, btn){
  if (btn.dataset.busy) return;
  btn.dataset.busy="1"; btn.disabled=true; const orig=btn.innerHTML; btn.textContent="Sending…";
  const banner = document.getElementById('printMsg');
  try{
    const r = await fetch(`/print/delivery-slip/${invoiceNo}`, {method:'POST'});
    const j = await r.json();
    banner.className='banner sending'; banner.textContent='Sent — printing…';
    // poll up to ~15s for the agent to confirm
    let printed=false;
    for(let i=0;i<10 && !printed;i++){
      await new Promise(s=>setTimeout(s,1500));
      const s = await (await fetch(`/print/job/${j.job_id}/status`)).json();
      if(s.status==='printed'){ printed=true; }
      if(s.status==='failed'){ break; }
    }
    banner.className = printed ? 'banner ok' : 'banner sending';
    banner.textContent = printed ? '✓ Printed on office Konica' : 'Sent to printer';
  }catch(e){ banner.className='banner fail'; banner.textContent='Failed — try again'; }
  btn.innerHTML=orig; btn.disabled=false; delete btn.dataset.busy;
}
```
```css
.banner{border-radius:8px;padding:9px 12px;font-size:13px;font-weight:600;margin:8px 0 12px}
.banner.sending{background:#fff7e6;color:#8a5a00;border:1px solid #e0b050}
.banner.ok{background:#12351f;color:#bfe9c8;border:1px solid #2f6b2f}
.banner.fail{background:#3a1414;color:#f3b0b0;border:1px solid #a33}
```

## 4. Reprint behaviour + "already printed?" confirmation
The lock (`dataset.busy` + disable) stops accidental double-prints while a job is in flight. On top of that, if the slip has **already been printed**, ask before reprinting.

Server — when rendering the pack screen, flag whether a slip was already printed:
```python
already_printed = db.session.execute(text(
  "SELECT 1 FROM print_jobs WHERE invoice_no=:n AND doc_type='slip' "
  "AND status IN ('printed','sending','queued') LIMIT 1"),
  {'n': invoice_no}).first() is not None
# pass already_printed to the template; render the button with data-printed="1" when true:
#   <button class="btn-print" data-printed="{{ '1' if already_printed else '' }}"
#           onclick="printSlip('{{ invoice.invoice_no }}', this)"> ... </button>
```
Client — confirm if it was already printed, and set the flag after a successful print:
```javascript
async function printSlip(invoiceNo, btn){
  if (btn.dataset.busy) return;
  if (btn.dataset.printed === "1"){
    if (!confirm("This delivery slip has already been printed.\nDo you want to print it again?")) return;
  }
  // ... existing send + status-poll code from section 3 ...
  // after a successful/queued print:
  btn.dataset.printed = "1";
}
```
So the first print goes straight through; any later press pops "This delivery slip has already been printed. Do you want to print it again?" — Cancel does nothing, OK reprints. (Same pattern works for the box label on the dashboard if you want it there too.)

## 5. Bigger secondary actions
Turn the tiny links into tappable buttons, side by side, ≥44px tall.
```html
<div class="secondary">
  <a class="lnk" href="{{ url_for('pick_item', invoice_no=invoice.invoice_no) }}">Back to picking</a>
  <a class="lnk" href="{{ url_for('print_invoice', invoice_no=invoice.invoice_no) }}" target="_blank">View slip on screen</a>
</div>
```
```css
.secondary{display:flex;gap:10px;margin-top:14px}
.lnk{flex:1;text-align:center;padding:12px;border:1px solid #3a4759;border-radius:10px;
     color:#c7d2e0;font-size:14px;text-decoration:none}
```

## 6. Print both sides (duplex) — optional
Only matters when a slip runs to a second page. Two options:
- **Simplest:** on the office PC, set the Konica's driver default to **two-sided (long edge)**. Applies to all slips automatically.
- **Per-document in the agent:** duplex slips, single-side labels — in `print_agent.ps1`, when printing a slip add print settings:
  ```powershell
  if ($job.doc_type -eq "label") { & $sumatra -print-to $deli -silent $tmp }
  else { & $sumatra -print-to $konica -print-settings "duplexlong" -silent $tmp }
  ```

## Paste to the Replit Agent
> Fix the ready_for_packing screen: (1) the customer name is white on the light-green header in dark mode — force dark text colors (#14311c name, #2f6b2f status, #3d5c45 meta) on that header so it's always readable. (2) Style the Print button as a blue outlined button and Mark-as-packed as the green solid primary so they're clearly different. (3) Replace the small "sent" text with a status banner; make the enqueue endpoint return the job id, add `GET /print/job/<id>/status`, and have the button poll it to show "Sent — printing…" then "✓ Printed on office Konica" (or "Sent to printer" if not confirmed in ~15s). (4) Lock the print button while a job is in flight to prevent accidental double-prints, AND if the slip was already printed (a slip print_job for this invoice is queued/sending/printed), pop a confirm "This delivery slip has already been printed. Do you want to print it again?" before reprinting — pass an `already_printed` flag to the template and set it after a successful print. (5) Turn "Back to picking" and "View slip on screen" into full-width bordered buttons, ≥44px tall, side by side. Keep the same routes and print bridge.

(Duplex is a printer-driver/agent setting, handled on the office PC — see section 6.)
