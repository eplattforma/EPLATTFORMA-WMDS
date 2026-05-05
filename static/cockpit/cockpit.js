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
  let rows = [];
  try {
    rows = JSON.parse(c.dataset.trend || '[]');
  } catch (e) {
    console.error('Invalid cockpit trend data', e);
    return;
  }
  if (!Array.isArray(rows) || !rows.length) return;
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

// Live Cart tile is a plain anchor — no client-side toggle.
// Cart line items aren't stored in our warehouse (ASSUMPTION-045);
// the tile links out to Magento (or /abandoned-carts as fallback).

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

// ─── Claude advice (Ticket 3, cockpit-brief §12) ────────────────────────
//
// Greek output. The page-level "all" advice auto-loads into the Recommended
// Actions panel on DOMContentLoaded. Section buttons open a Bootstrap modal.

const SECTION_TITLES = {
  all: '✦ Ask Claude',
  offers: '✦ Ask Claude about offers',
  opportunities: '✦ Ask Claude about opportunities',
  pricing: '✦ Ask Claude about pricing',
  risk: '✦ Ask Claude about risk',
};

const RA_NOT_CONFIGURED = 'Συμβουλές μη διαθέσιμες — επικοινώνησε με admin.';
const RA_GENERIC_ERROR  = 'Σφάλμα κατά τη δημιουργία συμβουλής. Δοκίμασε ξανά.';

function _esc(s) {
  // Minimal HTML escape for AI-returned strings.
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function _fetchAdvice(section) {
  const url = `/cockpit/api/${encodeURIComponent(CC)}/advice`;
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ section: section || 'all' }),
  });
  let body = {};
  try { body = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, body };
}

function _renderAdviceHTML(data) {
  const parts = [];
  if (data.summary) {
    parts.push(`<p class="mb-2"><strong>${_esc(data.summary)}</strong></p>`);
  }
  if (data.peer_context) {
    parts.push(`<p class="text-muted small mb-3">${_esc(data.peer_context)}</p>`);
  }
  if ((data.next_actions || []).length) {
    parts.push('<h6 class="mt-3 mb-2">Next Actions</h6><ol class="mb-3">');
    data.next_actions.forEach(a => {
      const pri = _esc(a.priority || '');
      const act = _esc(a.action || '');
      const hint = a.script_hint
        ? `<div class="small text-muted"><em>${_esc(a.script_hint)}</em></div>` : '';
      parts.push(`<li><span class="badge bg-secondary me-2">${pri}</span>${act}${hint}</li>`);
    });
    parts.push('</ol>');
  }
  if ((data.key_findings || []).length) {
    parts.push('<h6 class="mt-3 mb-2">Key Findings</h6><ul class="mb-3">');
    data.key_findings.forEach(f => parts.push(`<li>${_esc(f)}</li>`));
    parts.push('</ul>');
  }
  if ((data.opportunities || []).length) {
    parts.push('<h6 class="mt-3 mb-2">Opportunities</h6><ul class="mb-3">');
    data.opportunities.forEach(o => {
      const conf = (o.confidence != null) ? ` <span class="badge bg-light text-dark">${(o.confidence*100).toFixed(0)}%</span>` : '';
      parts.push(`<li><strong>${_esc(o.title)}</strong>${conf}<div class="small">${_esc(o.why)}</div><div class="small text-success">${_esc(o.expected_impact)}</div></li>`);
    });
    parts.push('</ul>');
  }
  if ((data.risks || []).length) {
    parts.push('<h6 class="mt-3 mb-2">Risks</h6><ul class="mb-0">');
    data.risks.forEach(r => parts.push(`<li>${_esc(r)}</li>`));
    parts.push('</ul>');
  }
  return parts.join('');
}

function _renderRecommendedActions(data) {
  const top = (data.next_actions || []).slice(0, 4);
  if (!top.length && !data.summary) {
    return `<div class="text-muted small">${_esc(RA_GENERIC_ERROR)}</div>`;
  }
  const parts = [];
  if (data.summary) parts.push(`<p class="mb-2">${_esc(data.summary)}</p>`);
  if (top.length) {
    parts.push('<ol class="mb-0">');
    top.forEach(a => {
      const pri = _esc(a.priority || '');
      const act = _esc(a.action || '');
      const hint = a.script_hint
        ? ` <i class="fas fa-circle-info text-muted ms-1" data-bs-toggle="tooltip" title="${_esc(a.script_hint)}" aria-label="${_esc(a.script_hint)}"></i>`
        : '';
      parts.push(`<li><span class="badge bg-secondary me-2">${pri}</span>${act}${hint}</li>`);
    });
    parts.push('</ol>');
  }
  return parts.join('');
}

function _activateTooltipsIn(container) {
  if (!container || !window.bootstrap || !window.bootstrap.Tooltip) return;
  container.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
    window.bootstrap.Tooltip.getOrCreateInstance(el);
  });
}

async function _loadRecommendedActions() {
  const card = document.getElementById('recommendedActionsCard');
  if (!card) return;
  // Server-side cache-hit render (cockpit-brief §12.5) — skip async fetch.
  if (card.dataset.prerendered === '1') {
    _activateTooltipsIn(card);
    return;
  }
  const loading = document.getElementById('raLoading');
  const content = document.getElementById('raContent');
  const err = document.getElementById('raError');
  const result = await _fetchAdvice('all');
  if (loading) loading.classList.add('d-none');
  if (result.ok) {
    content.innerHTML = _renderRecommendedActions(result.body);
    content.classList.remove('d-none');
    _activateTooltipsIn(content);
  } else if (result.status === 503 && result.body && result.body.cached_html) {
    content.innerHTML = result.body.cached_html;
    content.classList.remove('d-none');
  } else if (result.status === 503) {
    err.textContent = (result.body && result.body.message) || RA_NOT_CONFIGURED;
    err.classList.remove('d-none');
  } else {
    err.textContent = (result.body && result.body.message) || RA_GENERIC_ERROR;
    err.classList.remove('d-none');
  }
}

async function askClaude(section) {
  const modalEl = document.getElementById('claudeAdviceModal');
  if (!modalEl) return;
  document.getElementById('claudeAdviceTitle').textContent =
    SECTION_TITLES[section] || SECTION_TITLES.all;
  const body = document.getElementById('claudeAdviceBody');
  body.innerHTML = '<div class="text-muted small d-flex align-items-center">'
    + '<div class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></div>'
    + '<span>Δημιουργία συμβουλών…</span></div>';
  let modal;
  if (window.bootstrap && window.bootstrap.Modal) {
    modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();
  } else {
    modalEl.style.display = 'block';
    modalEl.classList.add('show');
  }
  const result = await _fetchAdvice(section);
  if (result.ok) {
    body.innerHTML = _renderAdviceHTML(result.body);
  } else if (result.status === 503) {
    const msg = (result.body && result.body.message) || RA_NOT_CONFIGURED;
    body.innerHTML = `<div class="alert alert-warning small mb-0">${_esc(msg)}</div>`;
  } else {
    const msg = (result.body && result.body.message) || RA_GENERIC_ERROR;
    body.innerHTML = `<div class="alert alert-warning small mb-0">${_esc(msg)}</div>`;
  }
}

// ─── boot ──────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('canvas[data-spark]').forEach(drawSparkline);
  drawTrendChart();
  _loadRecommendedActions();
});
