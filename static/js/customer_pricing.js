function fmt(n, d) {
  if (d === undefined) d = 2;
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return Number(n).toLocaleString(undefined, {minimumFractionDigits:d, maximumFractionDigits:d});
}

function fmtSigned(n, d) {
  if (d === undefined) d = 2;
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  var prefix = n > 0 ? "+" : "";
  return prefix + fmt(n, d);
}

function todayISO() {
  return new Date().toISOString().slice(0,10);
}

function ninetyDaysAgoISO() {
  var d = new Date();
  d.setDate(d.getDate() - 89);
  return d.toISOString().slice(0,10);
}

function indexBadge(v) {
  if (v === null || v === undefined) return "-";
  var cls = "at";
  if (v > 1.02) cls = "above";
  else if (v < 0.98) cls = "below";
  return '<span class="pa-index-badge ' + cls + '">' + fmt(v, 3) + '</span>';
}

function deltaClass(v) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "positive" : v < 0 ? "negative" : "";
}

function signClass(n, eps=0.005) {
  const v = Number(n || 0);
  if (Number.isNaN(v) || v === null) return "zero";
  if (Math.abs(v) <= eps) return "zero";
  return v > 0 ? "pos" : "neg";
}
function kpiCardClass(n) {
  const s = signClass(n);
  return s === "pos" ? "kpi-pos" : (s === "neg" ? "kpi-neg" : "kpi-zero");
}

var itemNamesCache = {};
window.PVM_ROWS = [];
window.PVM_FILTER = "all";

function dominantEffectKey(row) {
  const pe = Math.abs(Number(row.price_effect || 0));
  const ve = Math.abs(Number(row.volume_effect || 0));
  const me = Math.abs(Number(row.mix_effect || 0));
  const maxv = Math.max(pe, ve, me);

  const EPS = 0.01;
  if (maxv < EPS) return null;

  if (pe === maxv) return "price";
  if (ve === maxv) return "volume";
  return "mix";
}

function rowMatchesPvmFilter(row) {
  const f = window.PVM_FILTER || "all";
  if (f === "all") return true;

  const pe = Math.abs(Number(row.price_effect || 0));
  const ve = Math.abs(Number(row.volume_effect || 0));
  const me = Math.abs(Number(row.mix_effect || 0));
  const maxv = Math.max(pe, ve, me);

  if (f === "price") return pe === maxv;
  if (f === "volume") return ve === maxv;
  if (f === "mix") return me === maxv;
  return true;
}

function pvmTypeKey(row) {
  const q1 = Number(row.q1 || 0);
  const q0 = Number(row.q0 || 0);
  if (q0 === 0 && q1 > 0) return "new";
  if (q1 === 0 && q0 > 0) return "lost";
  return "common";
}

function typeBadgeHtml(row) {
  const t = pvmTypeKey(row);
  if (t === "new") return `<span class="badge-type type-new">NEW</span>`;
  if (t === "lost") return `<span class="badge-type type-lost">LOST</span>`;
  return `<span class="badge-type type-common">COMMON</span>`;
}

function driverBadgeHtml(row) {
  const key = dominantEffectKey(row);
  if (!key) return `<span class="badge-driver badge-none">NONE</span>`;
  if (key === "price") return `<span class="badge-driver badge-price">PRICE</span>`;
  if (key === "volume") return `<span class="badge-driver badge-volume">VOLUME</span>`;
  return `<span class="badge-driver badge-mix">MIX</span>`;
}

function setPvmFilter(effect) {
  window.PVM_FILTER = effect || "all";
  document.querySelectorAll(".pvm-filter").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.effect === window.PVM_FILTER);
  });
  const label = document.getElementById("pvmFilterLabel");
  if (label) {
    const txt = window.PVM_FILTER === "all"
      ? "Showing: All items"
      : `Showing: ${window.PVM_FILTER.toUpperCase()}-driven items`;
    label.textContent = txt;
  }
  renderPvmTable();
}

function renderPvmTable() {
  const tb = document.querySelector("#tblPvm tbody");
  if (!tb) return;
  tb.innerHTML = "";
  const rows = (window.PVM_ROWS || []).filter(rowMatchesPvmFilter);

  for (const it of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="font-weight:600;font-size:12px">${it.item_code_365}</td>
      <td style="font-size:12px">${lookupItemName(it.item_code_365)}</td>
      <td>${typeBadgeHtml(it)}</td>
      <td>
        <a href="#" class="pvm-driver-click" data-effect="${dominantEffectKey(it) || ""}" style="text-decoration:none">
          ${driverBadgeHtml(it)}
        </a>
      </td>
      <td class="text-end ${signClass(it.delta_revenue)}">${fmt(it.delta_revenue, 2)}</td>
      <td class="text-end ${signClass(it.price_effect)}">${fmt(it.price_effect, 2)}</td>
      <td class="text-end ${signClass(it.volume_effect)}">${fmt(it.volume_effect, 2)}</td>
      <td class="text-end ${signClass(it.mix_effect)}">${fmt(it.mix_effect, 2)}</td>
      <td class="text-end">${fmt(it.q1, 0)}</td>
      <td class="text-end">${fmt(it.p1, 4)}</td>
      <td class="text-end">${fmt(it.q0, 0)}</td>
      <td class="text-end">${fmt(it.p0, 4)}</td>
    `;
    tb.appendChild(tr);
  }
}

var indexSelectedBrand = "";
var indexBrands = [];
var indexBrandNameMap = {};

function lookupItemName(code) {
  return itemNamesCache[code] || "";
}

function fetchItemNames(codes) {
  var missing = codes.filter(function(c) { return !itemNamesCache[c]; });
  if (missing.length === 0) return Promise.resolve();
  return fetch("/analytics/customers/api/item-names?codes=" + encodeURIComponent(missing.join(",")))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data && data.names) {
        for (var k in data.names) {
          itemNamesCache[k] = data.names[k];
        }
      }
    })
    .catch(function() {});
}

async function safeFetch(url) {
  var r = await fetch(url);
  if (r.redirected || r.status === 401 || r.status === 302) {
    throw new Error("Session expired - please refresh the page");
  }
  if (r.status === 403) {
    throw new Error("Access denied");
  }
  if (!r.ok) {
    throw new Error("Server error (" + r.status + ")");
  }
  var ct = r.headers.get("content-type") || "";
  if (ct.indexOf("application/json") === -1) {
    throw new Error("Session expired - please refresh the page");
  }
  return r.json();
}

function setLoading(tabId) {
  var el = document.querySelector("#" + tabId + " tbody");
  if (el) el.innerHTML = '<tr><td colspan="12" class="pa-loading">Loading...</td></tr>';
}

function setEmpty(tabId, msg) {
  var el = document.querySelector("#" + tabId + " tbody");
  if (el) el.innerHTML = '<tr><td colspan="12" class="pa-empty">' + (msg || "No data") + '</td></tr>';
}

function buildBaseParams() {
  var customer = window.CUSTOMER_CODE_365;
  var preset = document.getElementById("preset").value;
  var compare = document.getElementById("compare").value;
  var includeCredits = document.getElementById("includeCredits").checked ? "1" : "0";
  var benchmark = document.getElementById("benchmark").value;
  var p = new URLSearchParams();
  p.set("customer_code_365", customer);
  if (preset) {
    p.set("preset", preset);
  } else {
    p.set("from", document.getElementById("dFrom").value);
    p.set("to", document.getElementById("dTo").value);
  }
  p.set("compare", compare);
  p.set("include_credits", includeCredits);
  p.set("benchmark", benchmark);
  return p.toString();
}

function resolveCurrentDates() {
  var preset = document.getElementById("preset").value;
  var today = new Date();
  var d_from, d_to;
  if (preset === "last90") {
    d_to = new Date(today);
    d_from = new Date(today);
    d_from.setDate(d_from.getDate() - 89);
  } else if (preset === "last30") {
    d_to = new Date(today);
    d_from = new Date(today);
    d_from.setDate(d_from.getDate() - 29);
  } else if (preset === "mtd") {
    d_to = new Date(today);
    d_from = new Date(today.getFullYear(), today.getMonth(), 1);
  } else if (preset === "qtd") {
    d_to = new Date(today);
    var q = Math.floor(today.getMonth() / 3);
    d_from = new Date(today.getFullYear(), q * 3, 1);
  } else if (preset === "ytd") {
    d_to = new Date(today);
    d_from = new Date(today.getFullYear(), 0, 1);
  } else {
    var fv = document.getElementById("dFrom").value;
    var tv = document.getElementById("dTo").value;
    d_from = fv ? new Date(fv + "T00:00:00") : new Date(today.getTime() - 89 * 86400000);
    d_to = tv ? new Date(tv + "T00:00:00") : new Date(today);
  }
  return { from: d_from, to: d_to };
}

function resolveCompareDates(d_from, d_to, compare) {
  if (compare === "prev") {
    var days = Math.round((d_to - d_from) / 86400000);
    var prev_end = new Date(d_from.getTime() - 86400000);
    var prev_start = new Date(prev_end.getTime() - days * 86400000);
    return { from: prev_start, to: prev_end };
  } else if (compare === "py") {
    var pyFrom = new Date(d_from);
    pyFrom.setFullYear(pyFrom.getFullYear() - 1);
    var pyTo = new Date(d_to);
    pyTo.setFullYear(pyTo.getFullYear() - 1);
    return { from: pyFrom, to: pyTo };
  }
  return null;
}

function fmtDateDMY(d) {
  var dd = String(d.getDate()).padStart(2, '0');
  var mm = String(d.getMonth() + 1).padStart(2, '0');
  var yyyy = d.getFullYear();
  return dd + '/' + mm + '/' + yyyy;
}

function updateDateRangeDisplay() {
  var drEl = document.getElementById("dateRangeInfo");
  if (!drEl) return;
  var cur = resolveCurrentDates();
  var compare = document.getElementById("compare").value;
  var html = '<i class="fas fa-calendar-alt me-1"></i> <strong>Period:</strong> ' + fmtDateDMY(cur.from) + ' \u2013 ' + fmtDateDMY(cur.to);
  var comp = resolveCompareDates(cur.from, cur.to, compare);
  if (comp) {
    html += '&nbsp;&nbsp;|&nbsp;&nbsp;<i class="fas fa-exchange-alt me-1"></i> <strong>vs:</strong> ' + fmtDateDMY(comp.from) + ' \u2013 ' + fmtDateDMY(comp.to);
  }
  drEl.innerHTML = html;
  drEl.style.display = 'block';
}

async function loadAll() {
  var customer = window.CUSTOMER_CODE_365;
  var base = buildBaseParams();
  var compare = document.getElementById("compare").value;

  indexSelectedBrand = "";

  updateDateRangeDisplay();

  setLoading("tblIndex");
  setLoading("tblPvm");
  setLoading("tblSens");
  setLoading("tblStale");
  document.getElementById("indexSummary").innerHTML = "";
  document.getElementById("pvmSummary").innerHTML = "";

  await Promise.all([
    loadIndex("/pricing/api/price-index?" + base + "&top_n=50"),
    compare !== "none" ? loadPvm("/pricing/api/pvm?" + base) : showPvmNotice(),
    loadSens("/pricing/api/price-sensitivity?customer_code_365=" + encodeURIComponent(customer) + "&months=18"),
    loadStale(buildStaleUrl())
  ]);
}

function reloadIndex(brand) {
  if (brand !== undefined) indexSelectedBrand = brand;
  var base = buildBaseParams();
  var url = "/pricing/api/price-index?" + base + "&top_n=200";
  if (indexSelectedBrand) url += "&brand=" + encodeURIComponent(indexSelectedBrand);
  setLoading("tblIndex");
  document.getElementById("indexSummary").innerHTML = "";
  loadIndex(url);
}

function showPvmNotice() {
  document.getElementById("pvmSummary").innerHTML = '<div class="pa-note">Set Compare to "Previous period" or "Prior Year" to run PVM analysis.</div>';
  document.querySelector("#tblPvm tbody").innerHTML = "";
  return Promise.resolve();
}

async function loadIndex(url) {
  try {
    var j = await safeFetch(url);

    var items = j.items || [];
    if (items.length === 0) {
      setEmpty("tblIndex", "No sales data for this customer in selected period");
      document.getElementById("indexSummary").innerHTML = "";
      return;
    }

    var codes = items.map(function(it) { return it.item_code_365; });
    await fetchItemNames(codes);

    if (j.brands && j.brands.length > 0) {
      indexBrands = j.brands;
      indexBrandNameMap = {};
      j.brands.forEach(function(b) { indexBrandNameMap[b.code] = b.name || ""; });
    }

    var brandOpts = indexBrands.map(function(b) {
      var label = b.name ? b.code + " - " + b.name : b.code;
      return '<option value="' + b.code + '"' + (b.code === indexSelectedBrand ? ' selected' : '') + '>' + label + '</option>';
    }).join("");
    var clearBtn = indexSelectedBrand
      ? ' <button class="btn btn-outline-secondary btn-sm" onclick="reloadIndex(\'\')" title="Show all brands" style="font-size:11px;padding:2px 8px"><i class="fas fa-times me-1"></i>Clear</button>'
      : "";
    var brandSelect = '<select class="form-select form-select-sm" style="width:auto;display:inline-block;min-width:200px;background:rgba(30,41,59,0.8);color:#e2e8f0;border-color:rgba(100,116,139,0.3)" onchange="reloadIndex(this.value)">' +
      '<option value="">All Brands</option>' + brandOpts + '</select>' + clearBtn;

    var s = j.summary || {};
    var cov = (s.coverage_pct !== null && s.coverage_pct !== undefined) ? (s.coverage_pct * 100) : null;
    var bm = s.benchmark === "max" ? "max" : "median";
    var bmLabel = bm === "max" ? "Market Max" : "Market Median";
    var overpayClass = (s.estimated_overpay || 0) > 0 ? "negative" : "positive";
    
    document.getElementById("indexSummary").innerHTML =
      '<div class="d-flex align-items-center gap-2 mb-2">' + brandSelect + '</div>' +
      '<div class="row g-2">' +
        '<div class="col-md-3"><div class="pa-kpi"><div class="label">Benchmarked revenue (Top ' + (s.top_n || 50) + ')</div><div class="value">' + fmt(s.benchmarked_sales_top_n ?? s.total_revenue) + '</div></div></div>' +
        '<div class="col-md-3"><div class="pa-kpi"><div class="label">Gross positive sales (all items)</div><div class="value">' + fmt(s.gross_positive_sales_all_items) + '</div></div></div>' +
        '<div class="col-md-3"><div class="pa-kpi"><div class="label">Coverage</div><div class="value">' + (cov === null ? "-" : fmt(cov, 1) + "%") + '</div></div></div>' +
        '<div class="col-md-3"><div class="pa-kpi"><div class="label">Net sales (incl credits)</div><div class="value">' + fmt(s.net_sales_all_lines) + '</div></div></div>' +
      '</div>' +
      '<div class="pa-kpi-grid mt-2">' +
        '<div class="pa-kpi"><div class="label">Market cost (basket, ' + bm + ')</div><div class="value">' + fmt(s.total_market_cost) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Overall Index vs ' + bmLabel + '</div><div class="value">' + indexBadge(s.overall_index) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Est. over/under pay vs ' + bm + '</div><div class="value ' + overpayClass + '">' + fmtSigned(s.estimated_overpay) + '</div></div>' +
      '</div>';

    var tb = document.querySelector("#tblIndex tbody");
    tb.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var brandBadge = "";
      if (it.brand) {
        var bName = indexBrandNameMap[it.brand] || "";
        var badgeLabel = bName ? it.brand + " - " + bName : it.brand;
        brandBadge = ' <span class="badge text-bg-secondary" style="cursor:pointer;font-size:10px;vertical-align:middle;margin-left:6px" onclick="reloadIndex(\'' + it.brand.replace(/'/g, "\\'") + '\')">' + badgeLabel + '</span>';
      }
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="font-weight:600;font-size:12px">' + it.item_code_365 + '</td>' +
        '<td style="font-size:12px">' + lookupItemName(it.item_code_365) + brandBadge + '</td>' +
        '<td class="text-end">' + fmt(it.qty, 0) + '</td>' +
        '<td class="text-end">' + fmt(it.revenue) + '</td>' +
        '<td class="text-end">' + fmt(it.cust_price, 2) + '</td>' +
        '<td class="text-end">' + fmt(it.market_median_price, 2) + '</td>' +
        '<td class="text-end">' + fmt(it.market_max_price, 2) + '</td>' +
        '<td class="text-end">' + indexBadge(it.index) + '</td>' +
        '<td class="text-end">' + fmtSigned(it.delta_per_unit, 2) + '</td>' +
        '<td class="text-end ' + deltaClass(it.delta_total) + '">' + fmtSigned(it.delta_total) + '</td>';
      tb.appendChild(tr);
    }
  } catch(e) {
    setEmpty("tblIndex", e.message || "Error loading data");
  }
}

async function loadPvm(url) {
  try {
    const ignoreSmall = document.getElementById("ignoreSmallPrice")?.checked ? "1" : "0";
    let priceAbsThr = "0.10";
    let pricePctThr = "0.005";
    if (ignoreSmall !== "1") {
      priceAbsThr = "0";
      pricePctThr = "0";
    }
    url += `&price_abs_thr=${priceAbsThr}&price_pct_thr=${pricePctThr}`;

    var j = await safeFetch(url);

    if (j.error) {
      showPvmNotice();
      return;
    }

    var items = j.items || [];
    if (items.length === 0) {
      setEmpty("tblPvm", "No PVM data for comparison period");
      document.getElementById("pvmSummary").innerHTML = "";
      return;
    }

    var codes = items.map(function(it) { return it.item_code_365; });
    await fetchItemNames(codes);

    window.PVM_ROWS = items;
    if (!window.PVM_FILTER) window.PVM_FILTER = "all";

    var s = j.summary || {};
    const net = s.delta_net_revenue ?? s.delta_revenue ?? 0;
    const pe = s.price_effect ?? 0;
    const ve = s.volume_effect ?? 0;
    const me = s.mix_effect ?? s.new_items_effect ?? 0;

    document.getElementById("pvmSummary").innerHTML = `
      <div class="kpi-grid">
        <div class="kpi-card p-2 ${kpiCardClass(net)}">
          <div class="lbl">Δ Net Revenue</div>
          <div class="val ${signClass(net)}">${fmtSigned(net)}</div>
        </div>

        <div class="kpi-card p-2 ${kpiCardClass(pe)}">
          <div class="lbl">Price effect</div>
          <div class="val ${signClass(pe)}">${fmtSigned(pe)}</div>
        </div>

        <div class="kpi-card p-2 ${kpiCardClass(ve)}">
          <div class="lbl">Volume effect</div>
          <div class="val ${signClass(ve)}">${fmtSigned(ve)}</div>
        </div>

        <div class="kpi-card p-2 ${kpiCardClass(me)}">
          <div class="lbl">New items effect</div>
          <div class="val ${signClass(me)}">${fmtSigned(me)}</div>
        </div>
      </div>

      <div class="pa-note" style="margin-top:8px">Baseline: ${s.baseline_from} to ${s.baseline_to} (${s.compare})</div>
      <div class="row g-2 mt-2">
        <div class="col-md-4"><div class="card p-2 kpi">
          <div class="kpi-label">New items (Curr only)</div>
          <div class="kpi-value pos">${fmt(s.new_items_revenue_current,2)}</div>
          <div class="text-muted small">${s.new_items_count || 0} items</div>
        </div></div>

        <div class="col-md-4"><div class="card p-2 kpi">
          <div class="kpi-label">Lost items (Base only)</div>
          <div class="kpi-value neg">${fmt(s.lost_items_revenue_baseline,2)}</div>
          <div class="text-muted small">${s.lost_items_count || 0} items</div>
        </div></div>

        <div class="col-md-4"><div class="card p-2 kpi">
          <div class="kpi-label">Common items PVM</div>
          <div class="kpi-value ${deltaClass(s.common_items_delta_revenue)}">${fmt(s.common_items_delta_revenue,2)}</div>
          <div class="text-muted small">
            Price ${fmt(s.common_price_effect,2)} · Volume ${fmt(s.common_volume_effect,2)} · ${s.common_items_count || 0} items
          </div>
        </div></div>
      </div>
    `;

    if (!window.__pvmFilterBound) {
      window.__pvmFilterBound = true;
      document.addEventListener("click", (e) => {
        const btn = e.target.closest(".pvm-filter");
        const card = e.target.closest(".pvm-card-click");
        const badge = e.target.closest(".pvm-driver-click");
        
        if (btn) {
          e.preventDefault();
          setPvmFilter(btn.dataset.effect);
        } else if (card) {
          e.preventDefault();
          setPvmFilter(card.dataset.effect);
        } else if (badge) {
          e.preventDefault();
          const eff = badge.dataset.effect;
          if (eff) setPvmFilter(eff);
        }
      });

      const allBtn = document.getElementById("pvmFilterAll");
      if (allBtn) {
        allBtn.addEventListener("click", () => setPvmFilter("all"));
      }
    }

    setPvmFilter(window.PVM_FILTER || "all");
  } catch(e) {
    setEmpty("tblPvm", e.message || "Error loading data");
  }
}

function buildStaleUrl() {
  var customer = window.CUSTOMER_CODE_365;
  var benchmark = document.getElementById("benchmark").value;
  var staleMin = document.getElementById("staleMin") ? document.getElementById("staleMin").value : 300;
  var staleMax = document.getElementById("staleMax") ? document.getElementById("staleMax").value : 400;
  var marketDays = document.getElementById("marketDays") ? document.getElementById("marketDays").value : 90;
  return "/pricing/api/stale-pricing?customer_code_365=" + encodeURIComponent(customer) +
    "&stale_min=" + staleMin + "&stale_max=" + staleMax +
    "&market_days=" + marketDays + "&benchmark=" + benchmark;
}

async function loadStale(url) {
  try {
    var j = await safeFetch(url);
    var items = j.items || [];

    if (items.length === 0) {
      setEmpty("tblStale", "No stale items found in the specified day range");
      return;
    }

    var tb = document.querySelector("#tblStale tbody");
    tb.innerHTML = "";

    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="font-weight:600;font-size:12px">' + (it.item_code_365 || "") + '</td>' +
        '<td style="font-size:12px">' + (it.item_name || "") + '</td>' +
        '<td class="text-end">' + (it.last_purchase_date || "") + '</td>' +
        '<td class="text-end">' + fmt(it.recency_days, 0) + '</td>' +
        '<td class="text-end">' + fmt(it.last_unit_price, 2) + '</td>' +
        '<td class="text-end">' + fmt(it.ref_at_last, 2) + '</td>' +
        '<td class="text-end ' + deltaClass(it.delta_vs_ref_at_last) + '">' + fmtSigned(it.delta_vs_ref_at_last, 2) + '</td>' +
        '<td class="text-end">' + fmt(it.ref_current, 2) + '</td>' +
        '<td class="text-end ' + deltaClass(it.delta_vs_ref_current) + '">' + fmtSigned(it.delta_vs_ref_current, 2) + '</td>' +
        '<td class="text-end">' + fmt(it.suggested_winback_price, 2) + '</td>';
      tb.appendChild(tr);
    }
  } catch(e) {
    setEmpty("tblStale", e.message || "Error loading data");
  }
}

async function loadSens(url) {
  try {
    var j = await safeFetch(url);

    var items = j.items || [];
    if (items.length === 0) {
      setEmpty("tblSens", "No items with enough data (6+ month-pairs required)");
      return;
    }

    var codes = items.map(function(it) { return it.item_code_365; });
    await fetchItemNames(codes);

    var tb = document.querySelector("#tblSens tbody");
    tb.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var corrVal = it.sensitivity_corr;
      var corrClass = "";
      if (corrVal !== null && corrVal < -0.3) corrClass = "negative";
      else if (corrVal !== null && corrVal > 0.3) corrClass = "positive";
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="font-weight:600;font-size:12px">' + it.item_code_365 + '</td>' +
        '<td style="font-size:12px">' + lookupItemName(it.item_code_365) + '</td>' +
        '<td class="text-end">' + fmt(it.pairs, 0) + '</td>' +
        '<td class="text-end ' + corrClass + '">' + fmt(corrVal, 3) + '</td>' +
        '<td class="text-end">' + fmt(it.dropouts_after_rise, 0) + '</td>';
      tb.appendChild(tr);
    }
  } catch(e) {
    setEmpty("tblSens", e.message || "Error loading data");
  }
}

document.addEventListener("DOMContentLoaded", function() {
  document.getElementById("dFrom").value = ninetyDaysAgoISO();
  document.getElementById("dTo").value = todayISO();

  document.getElementById("preset").addEventListener("change", function() {
    var custom = this.value === "";
    document.getElementById("dFrom").disabled = !custom;
    document.getElementById("dTo").disabled = !custom;
  });
  document.getElementById("dFrom").disabled = true;
  document.getElementById("dTo").disabled = true;

  document.getElementById("btnLoad").addEventListener("click", loadAll);

  var btnStale = document.getElementById("btnLoadStale");
  if (btnStale) {
    btnStale.addEventListener("click", function() {
      setLoading("tblStale");
      loadStale(buildStaleUrl());
    });
  }

  var tabs = document.querySelectorAll(".pa-tab");
  tabs.forEach(function(tab) {
    tab.addEventListener("click", function() {
      tabs.forEach(function(t) { t.classList.remove("active"); });
      tab.classList.add("active");
      var target = tab.getAttribute("data-tab");
      document.querySelectorAll(".pa-tab-content").forEach(function(tc) {
        tc.classList.toggle("active", tc.id === target);
      });
    });
  });

  loadAll();
});
