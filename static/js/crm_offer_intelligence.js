window.addEventListener('load', function() {
    var offerDrawer = document.getElementById('offerDrawer');
    var offerOverlay = document.getElementById('offerDrawerOverlay');
    if (!offerDrawer) return;

    var _offerDrawerState = {
        customerCode: '',
        customerName: '',
        customerMobile: '',
        selectedOffers: {},
        selectedOpportunities: {}
    };

    document.querySelectorAll('.offer-chip[data-customer]').forEach(function(chip) {
        chip.addEventListener('click', function(e) {
            e.stopPropagation();
            var code = this.getAttribute('data-customer');
            var name = this.getAttribute('data-name') || code;
            openOfferDrawer(code, name);
        });
    });

    window.openOfferDrawer = function(customerCode, customerName) {
        _offerDrawerState.customerCode = customerCode;
        _offerDrawerState.customerName = customerName;
        _offerDrawerState.selectedOffers = {};
        _offerDrawerState.selectedOpportunities = {};

        document.getElementById('offer-drawer-cust-name').textContent = customerName;
        document.getElementById('offer-drawer-cust-code').textContent = customerCode;

        var body = document.getElementById('offer-drawer-body');
        body.innerHTML = '<div class="offer-empty-state"><i class="fas fa-spinner fa-spin"></i>Loading offer intelligence...</div>';

        offerDrawer.classList.add('open');
        if (offerOverlay) offerOverlay.classList.add('show');

        fetch('/crm/customer/' + customerCode + '/offer-intelligence')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                _offerDrawerState.customerMobile = data.customer_mobile || '';
                _offerDrawerState.customerName = data.customer_name || customerName;
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
        var totalSales = summary.total_customer_sales_4w || 0;
        html += '<div class="offer-kpi-row">';
        html += kpiCard(summary.active_offer_skus || 0, 'SKUs', '#6ea8fe');
        html += kpiCard(fmtPct(usagePct), 'Usage', usagePct >= 50 ? '#22c55e' : usagePct >= 25 ? '#ffc107' : '#ef4444');
        html += kpiCard(fmtEur(summary.offer_sales_4w), 'Sales 4W', '#17a2b8');
        html += kpiCard(fmtEur(totalSales), 'Total Sales', '#a78bfa');
        html += kpiCard(fmtPct(sharePct), 'Offer Share', sharePct >= 50 ? '#fd7e14' : '#22c55e');
        html += '</div>';

        html += '<div class="offer-tabs">';
        html += '<button class="offer-tab-btn active" onclick="switchOfferTab(this, \'summary\')">Offer Summary</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'opportunities\')">Unused Offers (' + opps.length + ')</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'margin\')">Offer-Driven Sales (' + marginWatch.length + ')</button>';
        html += '<button class="offer-tab-btn" onclick="switchOfferTab(this, \'all\')">All Active Offers (' + allOffers.length + ')</button>';
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

    function fmtPct(v) { return v != null ? parseFloat(v).toFixed(1) + '%' : '\u2014'; }
    function fmtEur(v) { return v != null ? '\u20AC' + parseFloat(v).toLocaleString('en', {maximumFractionDigits:0}) : '\u2014'; }
    function fmtEur2(v) { return v != null ? '\u20AC' + parseFloat(v).toFixed(2) : '\u2014'; }

    function renderSummaryTab(s, rulesBreakdown, sentence) {
        var h = '';

        if (sentence) {
            h += '<div class="offer-generated-sentence">' + esc(sentence) + '</div>';
        }

        if (rulesBreakdown && rulesBreakdown.length > 0) {
            h += '<div class="offer-rules-section">';
            h += '<div class="rules-title">Rules Breakdown</div>';
            h += '<table class="rules-table"><thead><tr><th>Rule</th><th class="text-end">SKUs</th><th class="text-end">Avg Disc</th></tr></thead><tbody>';
            rulesBreakdown.forEach(function(rule) {
                var isUnused = (rule.count || 0) === 0;
                h += '<tr' + (isUnused ? ' style="background-color:#fee2e2;color:#991b1b;"' : '') + '>';
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
        
        _offerDrawerState.selectedOpportunities = {};
        
        var h = '<div class="offer-tab4-toolbar">';
        h += '<div class="offer-search-bar"><input type="text" id="oppSearchInput" placeholder="Search product..." oninput="filterOppRows(this.value)"></div>';
        h += '<span class="offer-selected-count" id="oppSelectedCount"></span>';
        h += '<button class="offer-sms-btn" id="oppSmsBtn" disabled onclick="openOppSmsModal()"><i class="fas fa-sms"></i> Send SMS</button>';
        h += '</div>';
        
        h += '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th style="width:30px;"><input type="checkbox" class="opp-select-cb" id="oppSelectAll" onchange="toggleAllOppRows(this.checked)"></th>';
        h += '<th>Product</th><th class="text-end">Offer</th>';
        h += '</tr></thead><tbody id="oppBody">';
        opps.forEach(function(o, idx) {
            var rowId = (o.sku || '') + '_' + idx;
            h += '<tr class="opp-row" data-row-id="' + esc(rowId) + '" data-search="' + esc((o.sku || '') + ' ' + (o.product_name || '')).toLowerCase() + '"';
            h += ' data-product="' + esc(o.product_name || o.sku || '') + '" data-price="' + (o.offer_price != null ? o.offer_price : '') + '">';
            h += '<td><input type="checkbox" class="opp-select-cb" data-row-id="' + esc(rowId) + '" onchange="toggleOppRow(this)"></td>';
            h += '<td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(o.product_name || '') + '">' + esc(o.product_name || '') + '</td>';
            h += '<td class="text-end">' + fmtEur2(o.offer_price) + '</td>';
            h += '</tr>';
        });
        h += '</tbody></table></div>';
        return h;
    }

    function renderMarginTab(items) {
        if (!items.length) return '<div class="offer-empty-state"><i class="fas fa-chart-pie" style="color:#22c55e"></i>No offer-driven sales detected</div>';

        var sorted = items.slice().sort(function(a, b) {
            return (b.sold_value_4w || 0) - (a.sold_value_4w || 0);
        });

        var h = '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th>Product</th><th class="text-end">Offer</th><th class="text-end">Sold 4W</th><th class="text-end">Value</th>';
        h += '</tr></thead><tbody>';
        sorted.forEach(function(m) {
            h += '<tr>';
            h += '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(m.product_name || '') + '">' + esc(m.product_name || '') + '</td>';
            h += '<td class="text-end">' + fmtEur2(m.offer_price) + '</td>';
            h += '<td class="text-end">' + (m.sold_qty_4w != null ? m.sold_qty_4w : '\u2014') + '</td>';
            h += '<td class="text-end">' + (m.sold_value_4w != null ? fmtEur(m.sold_value_4w) : '\u2014') + '</td>';
            h += '</tr>';
        });
        h += '</tbody></table></div>';
        return h;
    }

    function renderAllOffersTab(offers) {
        var activeOffers = offers.filter(function(o) { return o.line_status !== 'unknown'; });
        if (!activeOffers.length) return '<div class="offer-empty-state"><i class="fas fa-tag"></i>No offers found</div>';

        _offerDrawerState.selectedOffers = {};

        var h = '<div class="offer-tab4-toolbar">';
        h += '<div class="offer-search-bar"><input type="text" id="offerSearchInput" placeholder="Search product..." oninput="filterOfferRows(this.value)"></div>';
        h += '<span class="offer-selected-count" id="offerSelectedCount"></span>';
        h += '<button class="offer-sms-btn" id="offerSmsBtn" disabled onclick="openOfferSmsModal()"><i class="fas fa-sms"></i> Send SMS</button>';
        h += '</div>';

        h += '<div style="overflow-x:auto;">';
        h += '<table class="all-offers-table"><thead><tr>';
        h += '<th style="width:30px;"><input type="checkbox" class="offer-select-cb" id="offerSelectAll" onchange="toggleAllOfferRows(this.checked)"></th>';
        h += '<th>Product</th><th class="text-end">Offer Price</th>';
        h += '</tr></thead><tbody id="allOffersBody">';
        activeOffers.forEach(function(o, idx) {
            var rowId = (o.sku || '') + '_' + idx;
            var isUnused = o.line_status === 'unused' || o.line_status === 'high_discount_unused';
            var rowClass = isUnused ? 'offer-row offer-row-unused' : 'offer-row';
            h += '<tr class="' + rowClass + '" data-row-id="' + esc(rowId) + '" data-search="' + esc((o.sku || '') + ' ' + (o.product_name || '')).toLowerCase() + '"';
            h += ' data-product="' + esc(o.product_name || o.sku || '') + '" data-price="' + (o.offer_price != null ? o.offer_price : '') + '">';
            h += '<td><input type="checkbox" class="offer-select-cb" data-row-id="' + esc(rowId) + '" onchange="toggleOfferRow(this)"></td>';
            h += '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + esc(o.product_name || '') + '">' + esc(o.product_name || '') + '</td>';
            h += '<td class="text-end">' + fmtEur2(o.offer_price) + '</td>';
            h += '</tr>';
        });
        h += '</tbody></table></div>';
        return h;
    }

    window.toggleOfferRow = function(cb) {
        var rowId = cb.getAttribute('data-row-id');
        var tr = cb.closest('tr');
        if (cb.checked) {
            _offerDrawerState.selectedOffers[rowId] = {
                product: tr.getAttribute('data-product'),
                price: tr.getAttribute('data-price')
            };
        } else {
            delete _offerDrawerState.selectedOffers[rowId];
        }
        updateSmsButtonState();
    };

    window.toggleAllOfferRows = function(checked) {
        var rows = document.querySelectorAll('#allOffersBody .offer-row');
        rows.forEach(function(tr) {
            if (tr.style.display === 'none') return;
            var cb = tr.querySelector('.offer-select-cb');
            if (cb) {
                cb.checked = checked;
                var rowId = cb.getAttribute('data-row-id');
                if (checked) {
                    _offerDrawerState.selectedOffers[rowId] = {
                        product: tr.getAttribute('data-product'),
                        price: tr.getAttribute('data-price')
                    };
                } else {
                    delete _offerDrawerState.selectedOffers[rowId];
                }
            }
        });
        updateSmsButtonState();
    };

    function updateSmsButtonState() {
        var count = Object.keys(_offerDrawerState.selectedOffers).length;
        var btn = document.getElementById('offerSmsBtn');
        var countEl = document.getElementById('offerSelectedCount');
        if (btn) btn.disabled = count === 0;
        if (countEl) countEl.textContent = count > 0 ? count + ' selected' : '';
    }

    function buildSmsMessage(selected) {
        var lines = ['Special offers for you:'];
        Object.keys(selected).forEach(function(key) {
            var item = selected[key];
            var priceStr = item.price ? '\u20AC' + parseFloat(item.price).toFixed(2) : '';
            lines.push(item.product + (priceStr ? ' - ' + priceStr : ''));
        });
        lines.push('');
        lines.push('EPLATTFORMA 70000394 PLACE YOUR ORDER');
        return lines.join('\n');
    }

    window.filterOfferRows = function(q) {
        q = q.toLowerCase().trim();
        document.querySelectorAll('#allOffersBody .offer-row').forEach(function(tr) {
            tr.style.display = tr.getAttribute('data-search').indexOf(q) >= 0 ? '' : 'none';
        });
    };

    window.openOfferSmsModal = function() {
        var selected = _offerDrawerState.selectedOffers;
        var count = Object.keys(selected).length;
        if (count === 0) return;

        var mobile = _offerDrawerState.customerMobile || '';
        var custName = _offerDrawerState.customerName || '';

        var lines = ['Special offers for you:'];
        Object.keys(selected).forEach(function(key) {
            var item = selected[key];
            var priceStr = item.price ? '\u20AC' + parseFloat(item.price).toFixed(2) : '';
            lines.push(item.product + (priceStr ? ' - ' + priceStr : ''));
        });
        lines.push('');
        lines.push('Reply or contact us to place your order.');
        var msgText = lines.join('\n');

        var overlay = document.getElementById('offerSmsModalOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'offerSmsModalOverlay';
            overlay.className = 'offer-sms-modal-overlay';
            overlay.innerHTML = buildSmsModalHtml();
            document.body.appendChild(overlay);
            overlay.addEventListener('click', function(e) {
                if (e.target === overlay) closeOfferSmsModal();
            });
        }

        document.getElementById('smsModalCustName').textContent = custName;
        document.getElementById('smsModalProductCount').textContent = count + ' product' + (count > 1 ? 's' : '') + ' selected';
        document.getElementById('smsModalMobile').value = mobile;
        document.getElementById('smsModalMessage').value = msgText;
        document.getElementById('smsModalSendBtn').disabled = false;
        document.getElementById('smsModalSendBtn').innerHTML = '<i class="fas fa-paper-plane"></i> Send';

        if (!mobile) {
            document.getElementById('smsModalMobileError').style.display = 'block';
            document.getElementById('smsModalMobileError').textContent = 'No mobile number on file for this customer';
        } else {
            document.getElementById('smsModalMobileError').style.display = 'none';
        }

        updateSmsCharCount();
        overlay.classList.add('show');

        document.getElementById('smsModalMessage').addEventListener('input', updateSmsCharCount);
    };

    function buildSmsModalHtml() {
        return '<div class="offer-sms-modal">'
            + '<div class="offer-sms-modal-header">'
            + '<h5><i class="fas fa-sms" style="margin-right:6px;color:#6ea8fe;"></i>Send Offer SMS</h5>'
            + '<div class="sms-modal-subtitle"><span id="smsModalCustName"></span> &middot; <span id="smsModalProductCount"></span></div>'
            + '</div>'
            + '<div class="offer-sms-modal-body">'
            + '<div class="sms-field-group">'
            + '<label>Mobile Number</label>'
            + '<input type="text" id="smsModalMobile" readonly>'
            + '<div id="smsModalMobileError" style="display:none;color:#ef4444;font-size:0.72rem;margin-top:3px;"></div>'
            + '</div>'
            + '<div class="sms-field-group">'
            + '<label>Message</label>'
            + '<textarea id="smsModalMessage" rows="6"></textarea>'
            + '<div class="sms-meta-row">'
            + '<span id="smsCharCount">0 characters</span>'
            + '<span id="smsSegmentCount">1 SMS</span>'
            + '</div>'
            + '<div id="smsLengthWarn" style="display:none;color:#ffc107;font-size:0.72rem;margin-top:3px;font-weight:600;"></div>'
            + '</div>'
            + '</div>'
            + '<div class="offer-sms-modal-footer">'
            + '<button class="btn-cancel" onclick="closeOfferSmsModal()">Cancel</button>'
            + '<button class="btn-send" id="smsModalSendBtn" onclick="sendOfferSms()"><i class="fas fa-paper-plane"></i> Send</button>'
            + '</div>'
            + '</div>';
    }

    function updateSmsCharCount() {
        var ta = document.getElementById('smsModalMessage');
        if (!ta) return;
        var len = ta.value.length;
        var segments = len <= 160 ? 1 : Math.ceil(len / 153);

        var charEl = document.getElementById('smsCharCount');
        var segEl = document.getElementById('smsSegmentCount');
        var warnEl = document.getElementById('smsLengthWarn');
        if (charEl) charEl.textContent = len + ' characters';
        if (segEl) {
            segEl.textContent = segments + ' SMS' + (segments > 1 ? ' parts' : '');
            if (segments > 1) segEl.classList.add('sms-warn');
            else segEl.classList.remove('sms-warn');
        }
        if (warnEl) {
            if (segments > 3) {
                warnEl.style.display = 'block';
                warnEl.textContent = 'Long message \u2014 ' + segments + ' SMS parts will be sent';
            } else {
                warnEl.style.display = 'none';
            }
        }
    }

    window.closeOfferSmsModal = function() {
        var overlay = document.getElementById('offerSmsModalOverlay');
        if (overlay) overlay.classList.remove('show');
    };

    window.sendOfferSms = function() {
        var mobile = (document.getElementById('smsModalMobile').value || '').trim();
        var message = (document.getElementById('smsModalMessage').value || '').trim();
        var sendBtn = document.getElementById('smsModalSendBtn');

        if (!mobile) {
            document.getElementById('smsModalMobileError').style.display = 'block';
            document.getElementById('smsModalMobileError').textContent = 'No mobile number available';
            return;
        }
        if (!message) return;

        sendBtn.disabled = true;
        sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';

        var selectedCount = Object.keys(_offerDrawerState.selectedOffers).length;

        fetch('/crm/customer/' + _offerDrawerState.customerCode + '/offer-sms', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                mobile: mobile,
                message: message,
                customer_name: _offerDrawerState.customerName,
                selected_count: selectedCount
            })
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                closeOfferSmsModal();
                showSmsToast('SMS sent successfully', 'success');
                _offerDrawerState.selectedOffers = {};
                document.querySelectorAll('#allOffersBody .offer-select-cb').forEach(function(cb) { cb.checked = false; });
                var selectAll = document.getElementById('offerSelectAll');
                if (selectAll) selectAll.checked = false;
                updateSmsButtonState();
            } else {
                sendBtn.disabled = false;
                sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send';
                showSmsToast(data.error || 'Failed to send SMS', 'error');
            }
        })
        .catch(function(err) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send';
            showSmsToast('Network error', 'error');
        });
    };

    window.toggleOppRow = function(cb) {
        var rowId = cb.getAttribute('data-row-id');
        var tr = cb.closest('tr');
        if (cb.checked) {
            _offerDrawerState.selectedOpportunities[rowId] = {
                product: tr.getAttribute('data-product'),
                price: tr.getAttribute('data-price')
            };
        } else {
            delete _offerDrawerState.selectedOpportunities[rowId];
        }
        updateOppSmsButtonState();
    };

    window.toggleAllOppRows = function(checked) {
        var rows = document.querySelectorAll('#oppBody .opp-row');
        rows.forEach(function(tr) {
            if (tr.style.display === 'none') return;
            var cb = tr.querySelector('.opp-select-cb');
            if (cb) {
                cb.checked = checked;
                var rowId = cb.getAttribute('data-row-id');
                if (checked) {
                    _offerDrawerState.selectedOpportunities[rowId] = {
                        product: tr.getAttribute('data-product'),
                        price: tr.getAttribute('data-price')
                    };
                } else {
                    delete _offerDrawerState.selectedOpportunities[rowId];
                }
            }
        });
        updateOppSmsButtonState();
    };

    function updateOppSmsButtonState() {
        var count = Object.keys(_offerDrawerState.selectedOpportunities).length;
        var btn = document.getElementById('oppSmsBtn');
        var countEl = document.getElementById('oppSelectedCount');
        if (btn) btn.disabled = count === 0;
        if (countEl) countEl.textContent = count > 0 ? count + ' selected' : '';
    }

    window.filterOppRows = function(q) {
        q = q.toLowerCase().trim();
        document.querySelectorAll('#oppBody .opp-row').forEach(function(tr) {
            tr.style.display = tr.getAttribute('data-search').indexOf(q) >= 0 ? '' : 'none';
        });
        document.getElementById('oppSelectAll').checked = false;
        _offerDrawerState.selectedOpportunities = {};
        updateOppSmsButtonState();
    };

    window.openOppSmsModal = function() {
        var selected = _offerDrawerState.selectedOpportunities;
        var count = Object.keys(selected).length;
        if (count === 0) return;

        var mobile = _offerDrawerState.customerMobile || '';
        var custName = _offerDrawerState.customerName || '';

        var lines = ['Special offers for you:'];
        Object.keys(selected).forEach(function(key) {
            var item = selected[key];
            var priceStr = item.price ? '\u20AC' + parseFloat(item.price).toFixed(2) : '';
            lines.push(item.product + (priceStr ? ' - ' + priceStr : ''));
        });
        lines.push('');
        lines.push('Reply or contact us to place your order.');
        var msgText = lines.join('\n');

        var overlay = document.getElementById('oppSmsModalOverlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'oppSmsModalOverlay';
            overlay.className = 'offer-sms-modal-overlay';
            overlay.innerHTML = buildOppSmsModalHtml();
            document.body.appendChild(overlay);
            overlay.addEventListener('click', function(e) {
                if (e.target === overlay) closeOppSmsModal();
            });
        }

        document.getElementById('oppSmsModalCustName').textContent = custName;
        document.getElementById('oppSmsModalProductCount').textContent = count + ' product' + (count > 1 ? 's' : '') + ' selected';
        document.getElementById('oppSmsModalMobile').value = mobile;
        document.getElementById('oppSmsModalMessage').value = msgText;
        document.getElementById('oppSmsModalSendBtn').disabled = false;
        document.getElementById('oppSmsModalSendBtn').innerHTML = '<i class="fas fa-paper-plane"></i> Send';

        if (!mobile) {
            document.getElementById('oppSmsModalMobileError').style.display = 'block';
            document.getElementById('oppSmsModalMobileError').textContent = 'No mobile number on file for this customer';
        } else {
            document.getElementById('oppSmsModalMobileError').style.display = 'none';
        }

        updateOppSmsCharCount();
        overlay.classList.add('show');

        document.getElementById('oppSmsModalMessage').addEventListener('input', updateOppSmsCharCount);
    };

    function buildOppSmsModalHtml() {
        return '<div class="offer-sms-modal">'
            + '<div class="offer-sms-modal-header">'
            + '<h5><i class="fas fa-sms" style="margin-right:6px;color:#6ea8fe;"></i>Send Offer SMS</h5>'
            + '<div class="sms-modal-subtitle"><span id="oppSmsModalCustName"></span> &middot; <span id="oppSmsModalProductCount"></span></div>'
            + '</div>'
            + '<div class="offer-sms-modal-body">'
            + '<div class="sms-field-group">'
            + '<label>Mobile Number</label>'
            + '<input type="text" id="oppSmsModalMobile" readonly>'
            + '<div id="oppSmsModalMobileError" style="display:none;color:#ef4444;font-size:0.85rem;margin-top:4px;"></div>'
            + '</div>'
            + '<div class="sms-field-group">'
            + '<label>Message</label>'
            + '<textarea id="oppSmsModalMessage" rows="6"></textarea>'
            + '<div class="sms-meta-row">'
            + '<span id="oppSmsCharCount">0 characters</span>'
            + '<span id="oppSmsSegmentCount">1 SMS</span>'
            + '</div>'
            + '</div>'
            + '</div>'
            + '<div class="offer-sms-modal-footer">'
            + '<button class="btn-cancel" onclick="closeOppSmsModal()">Cancel</button>'
            + '<button class="btn-send" id="oppSmsModalSendBtn" onclick="sendOppSms()"><i class="fas fa-paper-plane"></i> Send</button>'
            + '</div>'
            + '</div>';
    }

    window.closeOppSmsModal = function() {
        var overlay = document.getElementById('oppSmsModalOverlay');
        if (overlay) overlay.classList.remove('show');
    };

    window.sendOppSms = function() {
        var mobile = (document.getElementById('oppSmsModalMobile').value || '').trim();
        var message = (document.getElementById('oppSmsModalMessage').value || '').trim();
        var sendBtn = document.getElementById('oppSmsModalSendBtn');

        if (!mobile) {
            document.getElementById('oppSmsModalMobileError').style.display = 'block';
            document.getElementById('oppSmsModalMobileError').textContent = 'No mobile number available';
            return;
        }
        if (!message) return;

        sendBtn.disabled = true;
        sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';

        var selectedCount = Object.keys(_offerDrawerState.selectedOpportunities).length;

        fetch('/crm/customer/' + _offerDrawerState.customerCode + '/offer-sms', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                mobile: mobile,
                message: message,
                customer_name: _offerDrawerState.customerName,
                selected_count: selectedCount
            })
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                closeOppSmsModal();
                showSmsToast('SMS sent successfully', 'success');
                _offerDrawerState.selectedOpportunities = {};
                document.querySelectorAll('#oppBody .opp-select-cb').forEach(function(cb) { cb.checked = false; });
                var selectAll = document.getElementById('oppSelectAll');
                if (selectAll) selectAll.checked = false;
                updateOppSmsButtonState();
            } else {
                sendBtn.disabled = false;
                sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send';
                showSmsToast(data.error || 'Failed to send SMS', 'error');
            }
        })
        .catch(function(err) {
            sendBtn.disabled = false;
            sendBtn.innerHTML = '<i class="fas fa-paper-plane"></i> Send';
            showSmsToast('Network error', 'error');
        });
    };

    function updateOppSmsCharCount() {
        var ta = document.getElementById('oppSmsModalMessage');
        if (!ta) return;
        var len = ta.value.length;
        var segments = len <= 160 ? 1 : Math.ceil(len / 153);

        var charEl = document.getElementById('oppSmsCharCount');
        var segEl = document.getElementById('oppSmsSegmentCount');
        if (charEl) charEl.textContent = len + ' characters';
        if (segEl) {
            segEl.textContent = segments + ' SMS' + (segments > 1 ? ' parts' : '');
        }
    }

    function showSmsToast(msg, type) {
        var toast = document.createElement('div');
        toast.className = 'sms-toast ' + type;
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(function() {
            toast.classList.add('fade-out');
            setTimeout(function() { toast.remove(); }, 400);
        }, 3000);
    }

    function esc(s) {
        var d = document.createElement('div');
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }
});
