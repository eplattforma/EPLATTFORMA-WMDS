
(function() {
  const DEFAULT_PARAMS = {
  "version": "v1",
  "store_units": {
    "line_exp_time": "minutes",
    "invoice_total_exp_time": "minutes"
  },
  "location": {
    "regex": "^(?P<corridor>\\d{2})-(?P<bay>\\d{2})-(?P<level>[A-Z])(?P<pos>\\d{2})$",
    "upper_corridors": [
      "70",
      "80",
      "90"
    ],
    "ladder_levels": [
      "C",
      "D"
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
    }
  },
  "pack": {
    "base_seconds": 45,
    "per_line_seconds": 3,
    "special_group_seconds": 20
  }
};
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

  function populateForm(params) {
    document.querySelectorAll('[data-path]').forEach((el) => {
      const path = el.getAttribute('data-path');
      let val = getByPath(params, path);

      // List fields stored as arrays, shown as comma-separated
      if (path === 'location.upper_corridors' || path === 'location.ladder_levels') {
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
    if (adv) adv.value = JSON.stringify(params, null, 2);
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

      if (path === 'location.upper_corridors' || path === 'location.ladder_levels') {
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

  // Initialize
  populateForm(parseInitial());
})();
