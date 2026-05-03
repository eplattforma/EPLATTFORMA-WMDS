// Cockpit page client logic. CC is set in the template.

function cockpitApplyControls(e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const params = new URLSearchParams();
  for (const [k, v] of fd.entries()) {
    if (v !== '' && v != null) params.set(k, v);
  }
  window.location.search = '?' + params.toString();
  return false;
}

// ─── Sparklines + trend chart ───────────────────────────────────────────
function drawSparkline(canvas) {
  const data = JSON.parse(canvas.dataset.spark || '[]');
  if (!data.length || typeof Chart === 'undefined') return;
  new Chart(canvas, {
    type: 'line',
    data: {
      labels: data.map((_, i) => i + 1),
      datasets: [{
        data, borderColor: '#0d6efd', borderWidth: 1.5,
        pointRadius: 0, tension: 0.3, fill: false
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } }
    }
  });
}

function drawTrendChart() {
  const c = document.getElementById('trendChart');
  if (!c || typeof Chart === 'undefined') return;
  const rows = JSON.parse(c.dataset.trend || '[]');
  const labels = rows.map(r => r.month);
  const sales = rows.map(r => r.sales);
  const peer = rows.map(r => r.peer_avg_sales);
  const target = rows.map(r => r.target_monthly);
  const datasets = [
    { label: 'Sales', data: sales, borderColor: '#0d6efd',
      backgroundColor: 'rgba(13,110,253,.15)', tension: .25, fill: true },
    { label: 'Peer avg', data: peer, borderColor: '#6c757d',
      borderDash: [4, 3], tension: .25, fill: false }
  ];
  if (target.some(v => v != null)) {
    datasets.push({ label: 'Monthly target', data: target,
      borderColor: '#198754', borderDash: [2, 2], tension: 0, fill: false });
  }
  new Chart(c, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: { y: { beginAtZero: true, ticks: { callback: v => '€' + v.toLocaleString() } } }
    }
  });
}

// ─── Top items toggle ───────────────────────────────────────────────────
document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-toggle]');
  if (!btn) return;
  const target = btn.dataset.toggle;
  document.querySelectorAll('[data-toggle]').forEach(b => b.classList.toggle('active', b === btn));
  ['topGP', 'topRev'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('d-none', id !== target);
  });
});

// ─── Live-cart inline panel (§11.9) ─────────────────────────────────────
// Server-rendered: just toggle visibility. Cart line items are not stored
// in our warehouse (ASSUMPTION-045) so there's nothing to fetch.
function toggleLiveCart() {
  const panel = document.getElementById('liveCartPanel');
  const tile = document.getElementById('liveCartTile');
  if (!panel || !tile) return;
  const opening = panel.classList.contains('d-none');
  panel.classList.toggle('d-none', !opening);
  tile.setAttribute('aria-expanded', opening ? 'true' : 'false');
}

// ─── Target endpoints (unchanged from Ticket 1) ────────────────────────
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return [r.ok, await r.json().catch(() => ({}))];
}

function readForm(form) {
  const out = {};
  new FormData(form).forEach((v, k) => { if (v !== '') out[k] = v; });
  return out;
}

async function targetSubmit(e) {
  e.preventDefault();
  const action = document.activeElement.dataset.action || 'propose';
  const body = readForm(e.target);
  const url = `/cockpit/api/${encodeURIComponent(CC)}/target/${action}`;
  const [ok, data] = await postJSON(url, body);
  if (!ok) { alert('Failed: ' + (data.error || 'unknown')); return false; }
  location.reload();
  return false;
}
async function targetAct(action) {
  const url = `/cockpit/api/${encodeURIComponent(CC)}/target/${action}`;
  const [ok, data] = await postJSON(url, {});
  if (!ok) { alert('Failed: ' + (data.error || 'unknown')); return; }
  location.reload();
}
async function targetReject() {
  const reason = prompt('Reason for rejection?');
  if (reason === null) return;
  const url = `/cockpit/api/${encodeURIComponent(CC)}/target/reject`;
  const [ok, data] = await postJSON(url, { reason });
  if (!ok) { alert('Failed: ' + (data.error || 'unknown')); return; }
  location.reload();
}

// ─── boot ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('canvas[data-spark]').forEach(drawSparkline);
  drawTrendChart();
});
