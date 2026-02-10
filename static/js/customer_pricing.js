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

var itemNamesCache = {};

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

async function loadAll() {
  var customer = window.CUSTOMER_CODE_365;
  var from = document.getElementById("dFrom").value;
  var to = document.getElementById("dTo").value;
  var compare = document.getElementById("compare").value;
  var includeCredits = document.getElementById("includeCredits").checked ? "1" : "0";

  var benchmark = document.getElementById("benchmark").value;
  var base = "customer_code_365=" + encodeURIComponent(customer) + "&from=" + from + "&to=" + to + "&compare=" + compare + "&include_credits=" + includeCredits + "&benchmark=" + benchmark;

  setLoading("tblIndex");
  setLoading("tblDisp");
  setLoading("tblPvm");
  setLoading("tblSens");
  document.getElementById("indexSummary").innerHTML = "";
  document.getElementById("pvmSummary").innerHTML = "";

  await Promise.all([
    loadIndex("/pricing/api/price-index?" + base + "&top_n=50"),
    loadDisp("/pricing/api/price-dispersion?" + base),
    compare !== "none" ? loadPvm("/pricing/api/pvm?" + base) : showPvmNotice(),
    loadSens("/pricing/api/price-sensitivity?customer_code_365=" + encodeURIComponent(customer) + "&months=18")
  ]);
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

    var s = j.summary || {};
    var bm = s.benchmark === "max" ? "max" : "median";
    var bmLabel = bm === "max" ? "Market Max" : "Market Median";
    var overpayClass = (s.estimated_overpay || 0) > 0 ? "negative" : "positive";
    document.getElementById("indexSummary").innerHTML =
      '<div class="pa-kpi-grid">' +
        '<div class="pa-kpi"><div class="label">Revenue (excl VAT)</div><div class="value">' + fmt(s.total_revenue) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Market cost (basket, ' + bm + ')</div><div class="value">' + fmt(s.total_market_cost) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Overall Index vs ' + bmLabel + '</div><div class="value">' + indexBadge(s.overall_index) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Est. over/under pay vs ' + bm + '</div><div class="value ' + overpayClass + '">' + fmtSigned(s.estimated_overpay) + '</div></div>' +
      '</div>';

    var tb = document.querySelector("#tblIndex tbody");
    tb.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="font-weight:600;font-size:12px">' + it.item_code_365 + '</td>' +
        '<td style="font-size:12px">' + lookupItemName(it.item_code_365) + '</td>' +
        '<td class="text-end">' + fmt(it.qty, 0) + '</td>' +
        '<td class="text-end">' + fmt(it.revenue) + '</td>' +
        '<td class="text-end">' + fmt(it.cust_price, 4) + '</td>' +
        '<td class="text-end">' + fmt(it.market_median_price, 4) + '</td>' +
        '<td class="text-end">' + fmt(it.market_max_price, 4) + '</td>' +
        '<td class="text-end">' + indexBadge(it.index) + '</td>' +
        '<td class="text-end">' + fmtSigned(it.delta_per_unit, 4) + '</td>' +
        '<td class="text-end ' + deltaClass(it.delta_total) + '">' + fmtSigned(it.delta_total) + '</td>';
      tb.appendChild(tr);
    }
  } catch(e) {
    setEmpty("tblIndex", e.message || "Error loading data");
  }
}

async function loadDisp(url) {
  try {
    var j = await safeFetch(url);

    var items = j.items || [];
    if (items.length === 0) {
      setEmpty("tblDisp", "No dispersion data");
      return;
    }

    var codes = items.map(function(it) { return it.item_code_365; });
    await fetchItemNames(codes);

    var tb = document.querySelector("#tblDisp tbody");
    tb.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var dispPct = it.dispersion_pct !== null ? (it.dispersion_pct * 100) : null;
      var dispClass = dispPct !== null && dispPct > 20 ? "negative" : "";
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="font-weight:600;font-size:12px">' + it.item_code_365 + '</td>' +
        '<td style="font-size:12px">' + lookupItemName(it.item_code_365) + '</td>' +
        '<td class="text-end">' + fmt(it.line_count, 0) + '</td>' +
        '<td class="text-end">' + fmt(it.min_price, 4) + '</td>' +
        '<td class="text-end">' + fmt(it.median_price, 4) + '</td>' +
        '<td class="text-end">' + fmt(it.max_price, 4) + '</td>' +
        '<td class="text-end ' + dispClass + '">' + fmt(dispPct, 1) + '%</td>' +
        '<td class="text-end">' + fmt(it.cv, 3) + '</td>';
      tb.appendChild(tr);
    }
  } catch(e) {
    setEmpty("tblDisp", e.message || "Error loading data");
  }
}

async function loadPvm(url) {
  try {
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

    var s = j.summary || {};
    document.getElementById("pvmSummary").innerHTML =
      '<div class="pa-kpi-grid">' +
        '<div class="pa-kpi"><div class="label">Delta Revenue</div><div class="value ' + deltaClass(s.delta_revenue) + '">' + fmtSigned(s.delta_revenue) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Price Effect</div><div class="value ' + deltaClass(s.price_effect) + '">' + fmtSigned(s.price_effect) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Volume Effect</div><div class="value ' + deltaClass(s.volume_effect) + '">' + fmtSigned(s.volume_effect) + '</div></div>' +
        '<div class="pa-kpi"><div class="label">Mix Effect</div><div class="value ' + deltaClass(s.mix_effect) + '">' + fmtSigned(s.mix_effect) + '</div></div>' +
      '</div>' +
      '<div class="pa-note" style="margin-top:8px">Baseline: ' + s.baseline_from + ' to ' + s.baseline_to + ' (' + s.compare + ')</div>';

    var tb = document.querySelector("#tblPvm tbody");
    tb.innerHTML = "";
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var tr = document.createElement("tr");
      tr.innerHTML =
        '<td style="font-weight:600;font-size:12px">' + it.item_code_365 + '</td>' +
        '<td style="font-size:12px">' + lookupItemName(it.item_code_365) + '</td>' +
        '<td class="text-end ' + deltaClass(it.delta_revenue) + '">' + fmtSigned(it.delta_revenue) + '</td>' +
        '<td class="text-end ' + deltaClass(it.price_effect) + '">' + fmtSigned(it.price_effect) + '</td>' +
        '<td class="text-end ' + deltaClass(it.volume_effect) + '">' + fmtSigned(it.volume_effect) + '</td>' +
        '<td class="text-end ' + deltaClass(it.mix_effect) + '">' + fmtSigned(it.mix_effect) + '</td>' +
        '<td class="text-end">' + fmt(it.q1, 0) + '</td>' +
        '<td class="text-end">' + fmt(it.p1, 4) + '</td>' +
        '<td class="text-end">' + fmt(it.q0, 0) + '</td>' +
        '<td class="text-end">' + fmt(it.p0, 4) + '</td>';
      tb.appendChild(tr);
    }
  } catch(e) {
    setEmpty("tblPvm", e.message || "Error loading data");
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

  document.getElementById("btnLoad").addEventListener("click", loadAll);

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
