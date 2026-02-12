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

    async function runAnalysis() {
        const preset = document.getElementById('peerPreset').value;
        const group = document.getElementById('peerGroup').value;
        const base = `?customer_code=${CUSTOMER_CODE}&preset=${preset}&peer_group=${group}`;

        setLoading('tblMissing');
        setLoading('tblCategory');
        setLoading('tblBrand');

        try {
            const [missing, catMix, brandMix] = await Promise.all([
                fetch(`/analytics/peer/api/missing-items${base}`).then(r => r.json()),
                fetch(`/analytics/peer/api/category-mix${base}`).then(r => r.json()),
                fetch(`/analytics/peer/api/brand-mix${base}`).then(r => r.json())
            ]);

            renderMissing(missing.items);
            renderMix('tblCategory', catMix.items, 'category');
            renderMix('tblBrand', brandMix.items, 'brand');
        } catch (e) {
            console.error(e);
        }
    }

    function setLoading(id) {
        document.querySelector(`#${id} tbody`).innerHTML = '<tr><td colspan="10" class="text-center p-4">Loading...</td></tr>';
    }

    function renderMissing(items) {
        const tbody = document.querySelector('#tblMissing tbody');
        tbody.innerHTML = items.map(it => `
            <tr>
                <td>${it.item_code}</td>
                <td>${it.item_name}</td>
                <td>${it.category}</td>
                <td>${(it.penetration * 100).toFixed(1)}%</td>
                <td>€${it.peer_avg_sales.toFixed(2)}</td>
                <td><span class="badge ${it.last_bought ? 'bg-warning' : 'bg-danger'}">${it.last_bought ? 'STALE ('+it.last_bought+')' : 'NEVER'}</span></td>
            </tr>
        `).join('') || '<tr><td colspan="10" class="text-center p-4">No opportunities found</td></tr>';
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
