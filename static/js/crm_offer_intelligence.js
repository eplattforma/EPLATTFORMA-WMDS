window.addEventListener('load', function() {
    var offerDrawer = document.getElementById('offerDrawer');
    var offerOverlay = document.getElementById('offerDrawerOverlay');
    if (!offerDrawer) return;

    document.querySelectorAll('.offer-chip[data-customer]').forEach(function(chip) {
        chip.addEventListener('click', function(e) {
            e.stopPropagation();
            var code = this.getAttribute('data-customer');
            var name = this.getAttribute('data-name') || code;
            openOfferDrawer(code, name);
        });
    });

    window.openOfferDrawer = function(customerCode, customerName) {
        document.getElementById('offer-drawer-cust-name').textContent = customerName;
        document.getElementById('offer-drawer-cust-code').textContent = customerCode;

        var body = document.getElementById('offer-drawer-body');
        body.innerHTML = '<div class="offer-empty-state"><i class="fas fa-spinner fa-spin"></i>Loading offer intelligence...</div>';

        offerDrawer.classList.add('open');
        if (offerOverlay) offerOverlay.classList.add('show');

        fetch('/crm/customer/' + customerCode + '/offer-intelligence')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                renderOfferDrawerContent(data, customerCode);
            })
            .catch(function(err) {
                body.innerHTML = '<div class="offer-empty-state"><i class="fas fa-exclamation-triangle"></i>Failed to load offer data</div>';
            });
    };

    window.closeOfferDrawer = function() {
        offerDrawer.classList.remove('open');
        if (offerOverlay) offerOverlay.classList.remove('show');
    };

    if (offerOverlay) {
        offerOverlay.addEventListener('click', function() { closeOfferDrawer(); });
    }

    function renderOfferDrawerContent(data, customerCode) {
        var body = document.getElementById('offer-drawer-body');
        var summary = data.summary || {};
        var opps = data.opportunities || [];
        var marginWatch = data.margin_risks || [];
        var allOffers = data.all_offers || [];

        if (!summary.has_special_pricing) {
            body.innerHTML = '<div class="offer-empty-state"><i class="fas fa-tag"></i>No special pricing for this customer</div>';
            return;
        }

        var html = '';

        html += '<div class="offer-kpi-row">';
        html += kpiCard(summary.active_offer_skus || 0, 'SKUs', '#6ea8fe');
        html += kpiCard(fmtPct(summary.avg_discount_percent), 'Avg Disc', '#ffc107');
        html += kpiCard(fmtPct(summary.offer_utilisation_pct), 'Util %', summary.offer_utilisation_pct >= 50 ? '#22c55e' : '#ef4444');
        html += kpiCard(summary.margin_risk_skus || 0, 'Margin Risk', summary.margin_risk_skus > 0 ? '#ef4444' : '#22c55e');
        html += '</div>';

        html += '<div class="offer-tabs">';
        html += '<button class="offer-tab-btn active" onclick="switchOfferTab(this, \'summary\')">Summary</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'opportunities\')">Opportunities (' + opps.length + ')</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'margin\')">Margin Watch (' + marginWatch.length + ')</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'all\')">All Offers (' + allOffers.length + ')</button>';
        html += '</div>';

        html += '<div id="offer-tab-summary" class="offer-tab-content active">';
        html += renderSummaryTab(summary);
        html += '</div>';

        html += '<div id="offer-tab-opportunities" class="offer-tab-content">';
        html += renderOpportunitiesTab(opps);
        html += '</div>';

        html += '<div id="offer-tab-margin" class="offer-tab-content">';
        html += renderMarginTab(marginWatch);
        html += '</div>';

        html += '<div id="offer-tab-all" class="offer-tab-content">';
        html += renderAllOffersTab(allOffers);
        html += '</div>';

        body.innerHTML = html;
    }

    window.switchOfferTab = function(btn, tabId) {
        btn.closest('.offer-tabs').querySelectorAll('.offer-tab-btn').forEach(function(b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var parent = btn.closest('.offer-tabs').parentElement;
        parent.querySelectorAll('.offer-tab-content').forEach(function(tc) { tc.classList.remove('active'); });
        document.getElementById('offer-tab-' + tabId).classList.add('active');
    };

    function kpiCard(value, label, color) {
        return '<div class="offer-kpi-card"><div class="kpi-num" style="color:' + color + '">' + value + '</div><div class="kpi-lbl">' + label + '</div></div>';
    }

    function fmtPct(v) { return v != null ? parseFloat(v).toFixed(1) + '%' : '—'; }
    function fmtEur(v) { return v != null ? '€' + parseFloat(v).toLocaleString('en', {maximumFractionDigits:0}) : '—'; }

    function renderSummaryTab(s) {
        var h = '<div class="offer-summary-section">';
        h += sumRow('Active offer SKUs', s.active_offer_skus || 0);
        h += sumRow('Average discount', fmtPct(s.avg_discount_percent));
        h += sumRow('Offer sales (4w)', fmtEur(s.offer_sales_4w));
        h += sumRow('Offer sales (90d)', fmtEur(s.offer_sales_90d));
        h += sumRow('SKUs bought (4w)', s.offered_skus_bought_4w || 0);
        h += sumRow('SKUs not bought', '<span style="color:' + ((s.offered_skus_not_bought || 0) > 0 ? '#ffc107' : '#22c55e') + '">' + (s.offered_skus_not_bought || 0) + '</span>');
        h += sumRow('Utilisation %', fmtPct(s.offer_utilisation_pct));
        h += sumRow('Margin risk SKUs', '<span style="color:' + ((s.margin_risk_skus || 0) > 0 ? '#ef4444' : '#22c55e') + '">' + (s.margin_risk_skus || 0) + '</span>');
        h += sumRow('High discount unused', '<span style="color:' + ((s.high_discount_unused_skus || 0) > 0 ? '#ffc107' : '#22c55e') + '">' + (s.high_discount_unused_skus || 0) + '</span>');
        h += '</div>';
        return h;
    }

    function sumRow(label, val) {
        return '<div class="sum-row"><span class="sum-label">' + label + '</span><span class="sum-val">' + val + '</span></div>';
    }

    function renderOpportunitiesTab(opps) {
        if (!opps.length) return '<div class="offer-empty-state"><i class="fas fa-check-circle"></i>No unused opportunities found</div>';
        var h = '<div style="padding:4px 0;">';
        opps.forEach(function(o) {
            h += '<div class="opp-card">';
            h += '<div class="opp-sku">' + esc(o.sku || '') + '</div>';
            h += '<div class="opp-name">' + esc(o.product_name || '') + '</div>';
            h += '<div class="opp-detail">';
            h += '<strong>Discount:</strong> ' + fmtPct(o.discount_percent) + ' · ';
            h += '<strong>Rule:</strong> ' + esc(o.rule_name || o.supplier_name || '—') + ' · ';
            h += '<strong>Price:</strong> ' + fmtEur(o.offer_price);
            if (o.cost != null) h += ' · <strong>Cost:</strong> ' + fmtEur(o.cost);
            h += '</div>';
            h += '</div>';
        });
        h += '</div>';
        return h;
    }

    function renderMarginTab(items) {
        if (!items.length) return '<div class="offer-empty-state"><i class="fas fa-shield-alt" style="color:#22c55e"></i>No margin risks detected</div>';
        var h = '<div style="padding:4px 0;">';
        items.forEach(function(m) {
            var isRisk = m.gross_margin_percent != null && m.gross_margin_percent < 15;
            h += '<div class="margin-card ' + (isRisk ? 'risk' : 'ok') + '">';
            h += '<div class="d-flex justify-content-between align-items-center">';
            h += '<div>';
            h += '<div class="opp-sku">' + esc(m.sku || '') + '</div>';
            h += '<div class="opp-name">' + esc(m.product_name || '') + '</div>';
            h += '</div>';
            var mClass = m.gross_margin_percent != null ? (m.gross_margin_percent < 10 ? 'margin-pct-bad' : m.gross_margin_percent < 20 ? 'margin-pct-warn' : 'margin-pct-good') : '';
            h += '<div class="text-end">';
            h += '<div class="' + mClass + '" style="font-size:1rem;font-weight:700;">' + fmtPct(m.gross_margin_percent) + '</div>';
            h += '<div style="font-size:0.68rem;color:rgba(255,255,255,0.4);">margin</div>';
            h += '</div></div>';
            h += '<div class="opp-detail mt-1">';
            h += '<strong>Cost:</strong> ' + fmtEur(m.cost) + ' · <strong>Offer:</strong> ' + fmtEur(m.offer_price) + ' · <strong>Disc:</strong> ' + fmtPct(m.discount_percent);
            h += '</div>';
            h += '</div>';
        });
        h += '</div>';
        return h;
    }

    function renderAllOffersTab(offers) {
        if (!offers.length) return '<div class="offer-empty-state"><i class="fas fa-tag"></i>No offers found</div>';
        var h = '<div class="offer-search-bar"><input type="text" id="offerSearchInput" placeholder="Search SKU or product..." oninput="filterOfferRows(this.value)"></div>';
        h += '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th>SKU</th><th>Product</th><th class="text-end">Origin €</th><th class="text-end">Final €</th><th class="text-end">Disc %</th><th class="text-end">Margin</th><th>Rule</th>';
        h += '</tr></thead><tbody id="allOffersBody">';
        offers.forEach(function(o) {
            h += offerRow(o);
        });
        h += '</tbody></table></div>';
        return h;
    }

    function offerRow(o) {
        var mClass = '';
        var gm = o.gross_margin_percent;
        if (gm != null) {
            mClass = gm < 10 ? 'margin-pct-bad' : gm < 20 ? 'margin-pct-warn' : 'margin-pct-good';
        }
        var r = '<tr class="offer-row" data-search="' + esc((o.sku || '') + ' ' + (o.product_name || '')).toLowerCase() + '">';
        r += '<td style="font-weight:600;">' + esc(o.sku || '') + '</td>';
        r += '<td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(o.product_name || '') + '">' + esc(o.product_name || '') + '</td>';
        r += '<td class="text-end">' + (o.origin_price != null ? '€' + parseFloat(o.origin_price).toFixed(2) : '—') + '</td>';
        r += '<td class="text-end">' + (o.offer_price != null ? '€' + parseFloat(o.offer_price).toFixed(2) : '—') + '</td>';
        r += '<td class="text-end">' + fmtPct(o.discount_percent) + '</td>';
        r += '<td class="text-end ' + mClass + '">' + (gm != null ? fmtPct(gm) : '—') + '</td>';
        r += '<td style="font-size:0.72rem;max-width:90px;overflow:hidden;text-overflow:ellipsis;">' + esc(o.rule_name || '') + '</td>';
        r += '</tr>';
        return r;
    }

    window.filterOfferRows = function(q) {
        q = q.toLowerCase().trim();
        document.querySelectorAll('#allOffersBody .offer-row').forEach(function(tr) {
            tr.style.display = tr.getAttribute('data-search').indexOf(q) >= 0 ? '' : 'none';
        });
    };

    function esc(s) {
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }
});
