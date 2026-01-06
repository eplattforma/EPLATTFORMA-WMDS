
(function() {
  const DEFAULT_PARAMS = {
  "version": "v1",
  "store_units": {
    "line_exp_time": "minutes",
    "invoice_total_exp_time": "minutes"
  },
  "location": {
    "upper_corridors": [
      "70",
      "80",
      "90"
    ]
  },
  "overhead": {
    "start_seconds": 45,
    "end_seconds": 45
  },
  "travel": {
    "sec_align_per_stop": 2,
    "sec_per_corridor_change": 14,
    "sec_per_corridor_step": 4,
    "sec_per_bay_step": 2.5,
    "sec_per_pos_step": 0.6,
    "sec_stairs_up": 25,
    "sec_stairs_down": 20,
    "upper_walk_multiplier": 1.05,
    "zone_switch_seconds": 4
  },
  "pick": {
    "base_by_unit_type": {
      "item": 6,
      "pack": 8,
      "box": 10,
      "case": 13,
      "virtual_pack": 6
    },
    "per_qty_by_unit_type": {
      "item": 1.1,
      "pack": 1.6,
      "box": 2.0,
      "case": 3.0,
      "virtual_pack": 1.1
    },
    "level_seconds": {
      "A": 0,
      "B": 2,
      "C": 12,
      "D": 14
    },
    "difficulty_seconds": {
      "1": 0,
      "2": 2,
      "3": 6,
      "4": 12,
      "5": 20
    },
    "handling_seconds": {
      "fragility_yes": 6,
      "fragility_semi": 3,
      "spill_true": 5,
      "pressure_high": 4,
      "heat_sensitive_summer": 8
    },
    "ladder_rules": [
      {
        "corridors": ["11", "13"],
        "levels": ["C"],
        "ladder_seconds": 15
      }
    ]
  },
  "pack": {
    "base_seconds": 45,
    "per_line_seconds": 3,
    "special_group_seconds": 20
  }
};
  // --- Info Modal Handling ---
  const infoIcons = document.querySelectorAll('.info-icon');
  
  infoIcons.forEach(icon => {
    icon.style.cursor = 'pointer';
    icon.addEventListener('click', () => {
      const title = icon.getAttribute('data-title');
      const content = icon.getAttribute('data-content');
      
      const infoModalTitle = document.getElementById('infoModalTitle');
      const infoModalBody = document.getElementById('infoModalBody');
      const infoModalElement = document.getElementById('infoModal');
      
      if (infoModalTitle && infoModalBody && infoModalElement) {
        infoModalTitle.textContent = title;
        infoModalBody.textContent = content;
        
        // Use window.bootstrap to ensure we access the global library
        if (window.bootstrap && window.bootstrap.Modal) {
          const infoModal = new window.bootstrap.Modal(infoModalElement);
          infoModal.show();
        } else {
          console.error("Bootstrap JS library not found");
          alert(content); // Fallback if modal fails
        }
      }
    });
  });

  const form = document.getElementById('paramsForm');
  const hidden = document.getElementById('params_json');
  const adv = document.getElementById('advanced_json');

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function getByPath(obj, path) {
    const parts = path.split('.');
    let cur = obj;
    for (const p of parts) {
      if (cur == null) return undefined;
      cur = cur[p];
    }
    return cur;
  }

  function setByPath(obj, path, value) {
    const parts = path.split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      const p = parts[i];
      if (!(p in cur) || typeof cur[p] !== 'object' || cur[p] === null) {
        cur[p] = {};
      }
      cur = cur[p];
    }
    cur[parts[parts.length - 1]] = value;
  }

  function parseInitial() {
    try {
      const txt = (hidden && hidden.value) ? hidden.value : '';
      if (!txt.trim()) return deepClone(DEFAULT_PARAMS);
      const parsed = JSON.parse(txt);
      if (typeof parsed !== 'object' || parsed === null) return deepClone(DEFAULT_PARAMS);
      return parsed;
    } catch (e) {
      return deepClone(DEFAULT_PARAMS);
    }
  }

  function normalizeListInput(v) {
    if (typeof v !== 'string') return [];
    return v.split(',').map(s => s.trim()).filter(Boolean);
  }

  function renderLadderRules(rules) {
    const container = document.getElementById('ladderRulesContainer');
    if (!container) return;
    container.innerHTML = '';
    
    (rules || []).forEach((rule, idx) => {
      const div = document.createElement('div');
      div.className = 'card card-sm mb-2 p-3';
      div.innerHTML = `
        <div class="row g-2 align-items-end">
          <div class="col-md-4">
            <label class="form-label small">Corridors (comma-separated)</label>
            <input type="text" class="form-control form-control-sm ladder-corridors" value="${(rule.corridors || []).join(',')}">
          </div>
          <div class="col-md-3">
            <label class="form-label small">Levels (comma-separated)</label>
            <input type="text" class="form-control form-control-sm ladder-levels" value="${(rule.levels || []).join(',')}">
          </div>
          <div class="col-md-3">
            <label class="form-label small">Ladder Seconds</label>
            <input type="number" class="form-control form-control-sm ladder-seconds" value="${rule.ladder_seconds || 0}" min="0" step="0.1">
          </div>
          <div class="col-md-2">
            <button type="button" class="btn btn-sm btn-outline-danger ladder-remove-btn">Remove</button>
          </div>
        </div>
      `;
      container.appendChild(div);
      div.querySelector('.ladder-remove-btn').addEventListener('click', () => {
        div.remove();
      });
    });
  }

  function populateForm(params) {
    document.querySelectorAll('[data-path]').forEach((el) => {
      const path = el.getAttribute('data-path');
      let val = getByPath(params, path);

      // List fields stored as arrays, shown as comma-separated
      if (path === 'location.upper_corridors') {
        if (Array.isArray(val)) val = val.join(',');
        if (val == null) val = '';
        el.value = String(val);
        return;
      }

      if (val == null) {
        el.value = '';
        return;
      }

      // numeric inputs should show as number
      el.value = String(val);
    });
    
    // Render ladder rules UI
    const ladderRules = getByPath(params, 'pick.ladder_rules') || [];
    renderLadderRules(ladderRules);
    
    if (adv) adv.value = JSON.stringify(params, null, 2);
  }

  function buildLadderRulesFromUI() {
    const container = document.getElementById('ladderRulesContainer');
    if (!container) return [];
    
    const rules = [];
    container.querySelectorAll('.card').forEach((card) => {
      const corridorsRaw = card.querySelector('.ladder-corridors').value.trim();
      const levelsRaw = card.querySelector('.ladder-levels').value.trim();
      const secondsRaw = card.querySelector('.ladder-seconds').value.trim();
      
      const corridors = normalizeListInput(corridorsRaw);
      const levels = normalizeListInput(levelsRaw);
      const seconds = secondsRaw === '' ? 0 : Number(secondsRaw);
      
      if (corridors.length > 0 && levels.length > 0 && !isNaN(seconds)) {
        rules.push({
          corridors: corridors,
          levels: levels,
          ladder_seconds: seconds
        });
      }
    });
    return rules;
  }

  function buildParamsFromForm(baseParams) {
    const params = deepClone(baseParams || DEFAULT_PARAMS);

    // Ensure required top-level keys exist
    if (!params.version) params.version = 'v1';
    if (!params.location) params.location = {};
    if (!params.overhead) params.overhead = {};
    if (!params.travel) params.travel = {};
    if (!params.pick) params.pick = {};
    if (!params.pack) params.pack = {};

    document.querySelectorAll('[data-path]').forEach((el) => {
      const path = el.getAttribute('data-path');
      let raw = (el.value ?? '').trim();

      if (path === 'location.upper_corridors') {
        setByPath(params, path, normalizeListInput(raw));
        return;
      }

      // Try numeric conversion when input type is number
      if (el.type === 'number') {
        const num = raw === '' ? null : Number(raw);
        if (num === null || Number.isNaN(num)) {
          return;
        }
        setByPath(params, path, num);
        return;
      }

      // string
      setByPath(params, path, raw);
    });

    // Build ladder rules from UI
    params.pick.ladder_rules = buildLadderRulesFromUI();

    return params;
  }

  // Buttons
  document.getElementById('btnSaveParams')?.addEventListener('click', () => {
    const current = buildParamsFromForm(parseInitial());
    hidden.value = JSON.stringify(current, null, 2);
    form.submit();
  });

  document.getElementById('btnResetDefaults')?.addEventListener('click', () => {
    populateForm(deepClone(DEFAULT_PARAMS));
  });

  document.getElementById('btnAddLadderRule')?.addEventListener('click', () => {
    const container = document.getElementById('ladderRulesContainer');
    if (!container) return;
    const newRule = { corridors: [], levels: [], ladder_seconds: 0 };
    const rules = buildLadderRulesFromUI();
    rules.push(newRule);
    renderLadderRules(rules);
  });

  document.getElementById('btnApplyAdvanced')?.addEventListener('click', () => {
    try {
      const parsed = JSON.parse(adv.value);
      if (typeof parsed !== 'object' || parsed === null) throw new Error('JSON must be an object');
      populateForm(parsed);
    } catch (e) {
      alert('Invalid JSON: ' + e.message);
    }
  });

  document.getElementById('btnCopyAdvanced')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(adv.value);
    } catch (e) {
      // ignore
    }
  });

  document.getElementById('btnDownloadJson')?.addEventListener('click', () => {
    const params = buildParamsFromForm(parseInitial());
    const blob = new Blob([JSON.stringify(params, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'oi_time_params_v1.json';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  document.getElementById('fileImportJson')?.addEventListener('change', (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || ''));
        populateForm(parsed);
      } catch (e) {
        alert('Invalid JSON file');
      }
    };
    reader.readAsText(file);
  });

  // --- Test Estimates Form ---
  const checkForm = document.getElementById('checkForm');
  const checkResult = document.getElementById('checkResult');
  const resMinutes = document.getElementById('resMinutes');
  const resOver = document.getElementById('resOver');
  const resTrav = document.getElementById('resTrav');
  const resPick = document.getElementById('resPick');
  const resPack = document.getElementById('resPack');
  const resTravTotal = document.getElementById('resTrav'); // Mapping to the existing UI element

  checkForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const invoiceNo = document.getElementById('test_invoice_no').value.trim();
    if (!invoiceNo) return;

    try {
      const resp = await fetch('/admin/oi/api/estimate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoice_no: invoiceNo })
      });
      const data = await resp.json();

      if (data.success) {
        checkResult.classList.remove('d-none');
        resMinutes.textContent = data.total_minutes.toFixed(2);
        
        // Update print button link
        const printBtn = document.getElementById('printAnalysisBtn');
        if (printBtn) {
            printBtn.href = `/admin/oi/invoice/${invoiceNo}/motion-study`;
        }

        const b = data.breakdown;
        resOver.textContent = b.overhead_seconds.toFixed(0);
        resTravTotal.textContent = b.travel_seconds.toFixed(0);
        resPick.textContent = b.pick_seconds.toFixed(0);
        resPack.textContent = b.pack_seconds.toFixed(0);
      } else {
        alert('Estimate failed: ' + data.error);
      }
    } catch (err) {
      alert('Error: ' + err.message);
    }
  });

  // Initialize
  populateForm(parseInitial());
})();
