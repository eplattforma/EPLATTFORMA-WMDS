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
        var rulesBreakdown = data.rules_breakdown || [];
        var generatedSentence = data.generated_sentence || '';

        if (!summary.has_special_pricing) {
            body.innerHTML = '<div class="offer-empty-state"><i class="fas fa-tag"></i>No special pricing for this customer</div>';
            return;
        }

        var html = '';

        var usagePct = summary.offer_usage_pct != null ? summary.offer_usage_pct : summary.offer_utilisation_pct;
        var sharePct = summary.offer_sales_share_pct || 0;
        html += '<div class="offer-kpi-row">';
        html += kpiCard(summary.active_offer_skus || 0, 'SKUs', '#6ea8fe');
        html += kpiCard(fmtPct(usagePct), 'Usage', usagePct >= 50 ? '#22c55e' : usagePct >= 25 ? '#ffc107' : '#ef4444');
        html += kpiCard(fmtEur(summary.offer_sales_4w), 'Sales 4w', '#17a2b8');
        html += kpiCard(fmtPct(sharePct), 'Sales Share', sharePct >= 50 ? '#fd7e14' : '#22c55e');
        html += '</div>';

        html += '<div class="offer-tabs">';
        html += '<button class="offer-tab-btn active" onclick="switchOfferTab(this, \'summary\')">Summary</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'opportunities\')">Unused (' + opps.length + ')</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'margin\')">Sales Dep. (' + marginWatch.length + ')</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'all\')">All Offers (' + allOffers.length + ')</button>';
        html += '</div>';

        html += '<div id="offer-tab-summary" class="offer-tab-content active">';
        html += renderSummaryTab(summary, rulesBreakdown, generatedSentence);
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

    function renderSummaryTab(s, rulesBreakdown, sentence) {
        var h = '';
        var usagePct = s.offer_usage_pct != null ? s.offer_usage_pct : s.offer_utilisation_pct;
        var sharePct = s.offer_sales_share_pct || 0;

        if (sentence) {
            h += '<div class="offer-generated-sentence">' + esc(sentence) + '</div>';
        }

        h += '<div class="offer-section-title">Sales Dependency</div>';
        h += '<div class="offer-summary-section">';
        h += sumRow('Offer sales (4w)', fmtEur(s.offer_sales_4w));
        h += sumRow('Total customer sales (4w)', fmtEur(s.total_customer_sales_4w));
        h += sumRow('Sales share %', '<span style="color:' + (sharePct >= 50 ? '#fd7e14' : '#22c55e') + '">' + fmtPct(sharePct) + '</span>');
        h += sumRow('Offer sales (90d)', fmtEur(s.offer_sales_90d));
        h += '</div>';

        h += '<div class="offer-section-title">Pricing</div>';
        h += '<div class="offer-summary-section">';
        h += sumRow('Average discount', fmtPct(s.avg_discount_percent));
        h += sumRow('Max discount', fmtPct(s.max_discount_percent));
        if (s.top_rule_name) {
            h += sumRow('Top rule', esc(s.top_rule_name));
        }
        h += sumRow('Margin risk SKUs', '<span style="color:' + ((s.margin_risk_skus || 0) > 0 ? '#ef4444' : '#22c55e') + '">' + (s.margin_risk_skus || 0) + '</span>');
        h += '</div>';

        if (rulesBreakdown && rulesBreakdown.length > 0) {
            h += '<div class="offer-rules-section">';
            h += '<div class="rules-title">Rules Breakdown</div>';
            h += '<table class="rules-table"><thead><tr><th>Rule</th><th class="text-end">SKUs</th><th class="text-end">Avg Disc</th></tr></thead><tbody>';
            rulesBreakdown.forEach(function(rule) {
                h += '<tr>';
                h += '<td>' + esc(rule.rule_name || rule.rule_code || '') + '</td>';
                h += '<td class="text-end">' + rule.count + '</td>';
                h += '<td class="text-end">' + fmtPct(rule.avg_discount) + '</td>';
                h += '</tr>';
            });
            h += '</tbody></table></div>';
        }

        return h;
    }

    function sumRow(label, val) {
        return '<div class="sum-row"><span class="sum-label">' + label + '</span><span class="sum-val">' + val + '</span></div>';
    }

    function renderOpportunitiesTab(opps) {
        if (!opps.length) return '<div class="offer-empty-state"><i class="fas fa-check-circle"></i>No unused opportunities found</div>';
        var h = '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th>SKU</th><th>Product</th><th class="text-end">Offer €</th><th class="text-end">Disc %</th><th class="text-end">Margin %</th><th>Supplier / Brand</th><th>Mention</th>';
        h += '</tr></thead><tbody>';
        opps.forEach(function(o) {
            h += '<tr>';
            h += '<td style="font-weight:600;">' + esc(o.sku || '') + '</td>';
            h += '<td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(o.product_name || '') + '">' + esc(o.product_name || '') + '</td>';
            h += '<td class="text-end">' + (o.offer_price != null ? '€' + parseFloat(o.offer_price).toFixed(2) : '—') + '</td>';
            h += '<td class="text-end">' + fmtPct(o.discount_percent) + '</td>';
            h += '<td class="text-end">' + (o.gross_margin_percent != null ? fmtPct(o.gross_margin_percent) : '—') + '</td>';
            h += '<td style="font-size:0.72rem;">' + esc(o.supplier_name || o.brand_name || '—') + '</td>';
            h += '<td><span class="badge-mention">Mention</span></td>';
            h += '</tr>';
        });
        h += '</tbody></table></div>';
        return h;
    }

    function renderMarginTab(items) {
        if (!items.length) return '<div class="offer-empty-state"><i class="fas fa-chart-pie" style="color:#22c55e"></i>No high-dependency lines detected</div>';
        var h = '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th>SKU</th><th>Product</th><th class="text-end">Offer €</th><th class="text-end">Sold 4w</th><th class="text-end">Value 4w</th><th class="text-end">Disc %</th><th>Rule</th><th>Status</th>';
        h += '</tr></thead><tbody>';
        items.forEach(function(m) {
            var statusBadge = m.margin_status === 'negative' ? '<span class="badge-risk-neg">Neg Margin</span>' : m.margin_status === 'low' ? '<span class="badge-risk-low">Low Margin</span>' : '<span class="badge-status-selling">OK</span>';
            h += '<tr>';
            h += '<td style="font-weight:600;">' + esc(m.sku || '') + '</td>';
            h += '<td style="max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(m.product_name || '') + '">' + esc(m.product_name || '') + '</td>';
            h += '<td class="text-end">' + (m.offer_price != null ? '€' + parseFloat(m.offer_price).toFixed(2) : '—') + '</td>';
            h += '<td class="text-end">' + (m.sold_qty_4w != null ? m.sold_qty_4w : '—') + '</td>';
            h += '<td class="text-end">' + (m.sold_value_4w != null ? fmtEur(m.sold_value_4w) : '—') + '</td>';
            h += '<td class="text-end">' + (m.discount_percent != null ? fmtPct(m.discount_percent) : '—') + '</td>';
            h += '<td style="font-size:0.72rem;">' + esc(m.rule_name || '—') + '</td>';
            h += '<td>' + statusBadge + '</td>';
            h += '</tr>';
        });
        h += '</tbody></table></div>';
        return h;
    }

    function renderAllOffersTab(offers) {
        if (!offers.length) return '<div class="offer-empty-state"><i class="fas fa-tag"></i>No offers found</div>';
        var h = '<div class="offer-search-bar"><input type="text" id="offerSearchInput" placeholder="Search SKU or product..." oninput="filterOfferRows(this.value)"></div>';
        h += '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th>SKU</th><th>Product</th><th>Rule</th><th class="text-end">Normal €</th><th class="text-end">Offer €</th><th class="text-end">Disc %</th><th class="text-end">Cost €</th><th class="text-end">GP €</th><th class="text-end">Margin</th><th class="text-end">Sold 4w</th><th>Last Sold</th><th>Status</th>';
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
            mClass = gm < 0 ? 'margin-pct-bad' : gm < 12 ? 'margin-pct-warn' : 'margin-pct-good';
        }
        var statusMap = {
            'selling': '<span class="badge-status-selling">Selling</span>',
            'unused': '<span class="badge-status-unused">Unused</span>',
            'high_discount_unused': '<span class="badge-status-hdu">High Disc</span>',
            'margin_risk': '<span class="badge-status-risk">Risk</span>',
            'unknown': '<span class="badge-status-unknown">—</span>'
        };
        var statusBadge = statusMap[o.line_status] || '<span class="badge-status-unknown">' + esc(o.line_status || '—') + '</span>';

        var r = '<tr class="offer-row" data-search="' + esc((o.sku || '') + ' ' + (o.product_name || '')).toLowerCase() + '">';
        r += '<td style="font-weight:600;">' + esc(o.sku || '') + '</td>';
        r += '<td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(o.product_name || '') + '">' + esc(o.product_name || '') + '</td>';
        r += '<td style="font-size:0.72rem;max-width:80px;overflow:hidden;text-overflow:ellipsis;" title="' + esc(o.rule_name || '') + '">' + esc(o.rule_name || '') + '</td>';
        r += '<td class="text-end">' + (o.origin_price != null ? '€' + parseFloat(o.origin_price).toFixed(2) : '—') + '</td>';
        r += '<td class="text-end">' + (o.offer_price != null ? '€' + parseFloat(o.offer_price).toFixed(2) : '—') + '</td>';
        r += '<td class="text-end">' + fmtPct(o.discount_percent) + '</td>';
        r += '<td class="text-end">' + (o.cost != null ? '€' + parseFloat(o.cost).toFixed(2) : '—') + '</td>';
        r += '<td class="text-end">' + (o.gross_profit != null ? '€' + parseFloat(o.gross_profit).toFixed(2) : '—') + '</td>';
        r += '<td class="text-end ' + mClass + '">' + (gm != null ? fmtPct(gm) : '—') + '</td>';
        r += '<td class="text-end">' + (o.sold_qty_4w || 0) + '</td>';
        r += '<td style="font-size:0.72rem;">' + (o.last_sold_at ? o.last_sold_at.substring(0, 10) : '—') + '</td>';
        r += '<td>' + statusBadge + '</td>';
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
