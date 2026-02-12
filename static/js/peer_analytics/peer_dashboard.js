document.addEventListener('DOMContentLoaded', function() {
    const btnRun = document.getElementById('btnRunPeer');
    const tabs = document.querySelectorAll('.peer-tab');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.peer-content').forEach(c => c.classList.add('d-none'));
            document.getElementById(tab.dataset.target).classList.remove('d-none');
        });
    });

    let proposal = new Set();

    async function runAnalysis() {
        const preset = document.getElementById('peerPreset').value;
        const group = document.getElementById('peerGroup').value;
        const base = `?customer_code=${CUSTOMER_CODE}&preset=${preset}&peer_group=${group}`;

        setLoading('tblMissing');
        setLoading('tblCategory');
        setLoading('tblBrand');
        document.getElementById('catGapsList').innerHTML = '<div class="p-4 text-center"><i class="fas fa-spinner fa-spin"></i></div>';

        try {
            const [missing, catMix, brandMix, catGaps] = await Promise.all([
                fetch(`/analytics/peer/api/missing-items${base}`).then(r => r.json()),
                fetch(`/analytics/peer/api/category-mix${base}`).then(r => r.json()),
                fetch(`/analytics/peer/api/brand-mix${base}`).then(r => r.json()),
                fetch(`/analytics/category-manager/api/category-gaps${base}`).then(r => r.json())
            ]);

            renderMissing(missing);
            renderMix('tblCategory', catMix.items, 'category');
            renderMix('tblBrand', brandMix.items, 'brand');
            renderCatGaps(catGaps.items, base);
        } catch (e) {
            console.error(e);
        }
    }

    function renderCatGaps(items, queryParams) {
        const list = document.getElementById('catGapsList');
        if (!items || items.length === 0) {
            list.innerHTML = '<div class="p-4 text-center text-muted">No categories found</div>';
            return;
        }
        list.innerHTML = items.map(it => {
            const gap = it.share_gap * 100;
            const gapClass = gap < -2 ? 'text-danger' : (gap > 2 ? 'text-success' : '');
            return `
                <div class="list-group-item cat-gap-item p-3" data-cat="${it.category}">
                    <div class="d-flex justify-content-between align-items-center">
                        <div class="fw-bold small text-truncate" style="max-width: 180px;">${it.category}</div>
                        <span class="badge rounded-pill bg-danger" style="font-size: 10px;">${it.missing_items} items</span>
                    </div>
                    <div class="d-flex justify-content-between mt-1 tiny text-muted" style="font-size: 11px;">
                        <span>Share: ${(it.cust_share*100).toFixed(1)}%</span>
                        <span class="${gapClass} fw-bold">Gap: ${gap > 0 ? '+' : ''}${gap.toFixed(1)}%</span>
                    </div>
                </div>
            `;
        }).join('');

        list.querySelectorAll('.cat-gap-item').forEach(el => {
            el.addEventListener('click', () => {
                list.querySelectorAll('.cat-gap-item').forEach(x => x.classList.remove('active'));
                el.classList.add('active');
                loadSuggestions(el.dataset.cat, queryParams);
            });
        });

        // Auto select first
        if (items.length > 0) list.querySelector('.cat-gap-item').click();
    }

    async function loadSuggestions(cat, queryParams) {
        document.getElementById('selectedCatTitle').innerText = cat;
        document.getElementById('catSuggestions').innerHTML = '<div class="p-5 text-center"><i class="fas fa-spinner fa-spin fa-2x"></i></div>';
        
        const res = await fetch(`/analytics/category-manager/api/category-suggestions${queryParams}&category=${encodeURIComponent(cat)}`);
        const data = await res.json();
        
        const blocks = data.blocks || {};
        let html = '';

        const renderBlock = (title, items, cls) => {
            if (!items || items.length === 0) return '';
            return `
                <div class="mb-3 p-2 rounded ${cls}">
                    <div class="d-flex justify-content-between align-items-center mb-1">
                        <strong class="small text-uppercase">${title}</strong>
                        <span class="tiny text-muted">${items.length} items</span>
                    </div>
                    ${items.map(it => `
                        <div class="suggestion-row d-flex justify-content-between align-items-center">
                            <div style="max-width: 70%;">
                                <div class="fw-bold">${it.item_code} <span class="badge ${it.tag === 'NEW' ? 'bg-success' : 'bg-info'} tiny" style="font-size: 9px;">${it.tag}</span></div>
                                <div class="text-truncate">${it.item_name}</div>
                                <div class="tiny text-muted">${it.brand} &middot; ${(it.penetration*100).toFixed(0)}% pen</div>
                            </div>
                            <div class="text-end">
                                <div class="fw-bold">€${it.peer_avg_sales.toFixed(2)}</div>
                                <button class="btn btn-xs btn-primary btn-add-prop" data-code="${it.item_code}" data-name="${it.item_name}">Add</button>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        };

        html += renderBlock('Must Stock (60%+)', blocks.must, 'block-must');
        html += renderBlock('Should Stock (30-60%)', blocks.should, 'block-should');
        html += renderBlock('Variety (15-30%)', blocks.variety, 'block-variety');

        document.getElementById('catSuggestions').innerHTML = html || '<div class="p-5 text-center text-muted">No suggestions for this category</div>';
        
        document.querySelectorAll('.btn-add-prop').forEach(btn => {
            btn.addEventListener('click', () => {
                addToProposal(btn.dataset.code, btn.dataset.name);
            });
        });
    }

    function addToProposal(code, name) {
        proposal.add(JSON.stringify({code, name}));
        renderProposal();
    }

    function renderProposal() {
        const list = document.getElementById('proposalItems');
        if (proposal.size === 0) {
            list.innerHTML = '<div class="text-center text-muted small p-4">Add items from blocks</div>';
            return;
        }
        const arr = Array.from(proposal).map(x => JSON.parse(x));
        list.innerHTML = arr.map(it => `
            <div class="d-flex justify-content-between align-items-center p-2 mb-1 border-bottom border-secondary tiny">
                <div class="text-truncate" style="max-width: 80%;"><strong>${it.code}</strong> ${it.name}</div>
                <button class="btn btn-xs btn-link text-danger p-0" onclick="removeFromProposal('${it.code}')"><i class="fas fa-times"></i></button>
            </div>
        `).join('');
    }

    window.removeFromProposal = function(code) {
        const arr = Array.from(proposal).map(x => JSON.parse(x));
        const filtered = arr.filter(x => x.code !== code);
        proposal = new Set(filtered.map(x => JSON.stringify(x)));
        renderProposal();
    };

    document.getElementById('btnExportProposal').addEventListener('click', async () => {
        if (proposal.size === 0) return alert('Proposal is empty');
        const codes = Array.from(proposal).map(x => JSON.parse(x).code);
        const res = await fetch('/analytics/category-manager/export/proposal.csv', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ item_codes: codes })
        });
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `proposal_${CUSTOMER_CODE}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
    });

    function setLoading(id) {
        document.querySelector(`#${id} tbody`).innerHTML = '<tr><td colspan="10" class="text-center p-4">Loading...</td></tr>';
    }

    function renderMissing(data) {
        const items = data.items || [];
        const meta = data.meta || {};
        const tbody = document.querySelector('#tblMissing tbody');
        
        if (items.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="10" class="text-center p-4">
                        <div class="mb-2">No opportunities found</div>
                        <div class="alert alert-info d-inline-block text-start" style="font-size:0.85rem; background:rgba(13,202,240,0.05); border-color:rgba(13,202,240,0.2); color:#0dcaf0; max-width:400px;">
                            <i class="fas fa-info-circle me-2"></i>
                            <strong>Why no results?</strong><br>
                            • Peers in Segment: ${meta.peer_customers || 0}<br>
                            • Active Peers (Bought in Period): ${meta.active_peers || 0}<br>
                            • Requirement: At least 5 active peers are needed for a valid analysis.
                        </div>
                    </td>
                </tr>`;
            return;
        }

        tbody.innerHTML = items.map(it => `
            <tr>
                <td>${it.item_code}</td>
                <td>${it.item_name}</td>
                <td>${it.category}</td>
                <td>${(it.penetration * 100).toFixed(1)}%</td>
                <td>€${it.peer_avg_sales.toFixed(2)}</td>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <span class="badge ${it.last_bought ? 'bg-warning' : 'bg-danger'}" style="font-size: 10px;">${it.last_bought ? 'STALE ('+it.last_bought+')' : 'NEVER'}</span>
                        <button class="btn btn-xs btn-outline-primary btn-add-prop" data-code="${it.item_code}" data-name="${it.item_name}">Add to Proposal</button>
                    </div>
                </td>
            </tr>
        `).join('');

        tbody.querySelectorAll('.btn-add-prop').forEach(btn => {
            btn.addEventListener('click', () => {
                addToProposal(btn.dataset.code, btn.dataset.name);
            });
        });
    }

    function renderMix(id, items, key) {
        const tbody = document.querySelector(`#${id} tbody`);
        tbody.innerHTML = items.map(it => {
            const gap = it.share_gap * 100;
            const gapClass = gap < -2 ? 'gap-neg' : (gap > 2 ? 'gap-pos' : '');
            return `
                <tr>
                    <td>${it[key]}</td>
                    <td>€${it.cust_sales.toLocaleString()}</td>
                    <td>${(it.cust_share * 100).toFixed(1)}%</td>
                    <td>${(it.peer_share * 100).toFixed(1)}%</td>
                    <td class="${gapClass}">${gap > 0 ? '+' : ''}${gap.toFixed(1)}%</td>
                </tr>
            `;
        }).join('') || '<tr><td colspan="10" class="text-center p-4">No data</td></tr>';
    }

    btnRun.addEventListener('click', runAnalysis);
    runAnalysis();
});
