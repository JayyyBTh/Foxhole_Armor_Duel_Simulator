// Loads matrix_cache.json (main), matrix_cache_earlywar.json (early-war) and
// matrix_cache_pushgun.json (push guns) and renders up to six heatmap tables
// that share the armor slider and mode toggle:
//   Main:      Warden×Colonial  +  Colonial×Warden   (data.matrices[mode].wc / .cw)
//   Early-war: same pair, scoped to earlywar.json
//   Push guns: Warden push guns × Colonial tanks  +  Colonial push guns ×
//              Warden tanks (cross-library cache with a `tables` block)
// No live simulation here — the caches are precomputed.

(function () {
  const slider = document.getElementById("armor-slider");
  const readout = document.getElementById("armor-readout");
  const modeRadios = document.querySelectorAll('input[name="matrix-mode"]');
  const thresholdRadios = document.querySelectorAll('input[name="threshold-mode"]');

  const main = {
    wc: document.getElementById("matrix-wc"),
    cw: document.getElementById("matrix-cw"),
    wcTitle: document.getElementById("matrix-wc-title"),
    cwTitle: document.getElementById("matrix-cw-title"),
    cache: null,
  };
  // Optional per-library sections. Each one is hidden when its cache is
  // missing (library absent, empty, or single-faction).
  function optionalGroup(prefix, cachePath) {
    return {
      wc: document.getElementById(`matrix-${prefix}-wc`),
      cw: document.getElementById(`matrix-${prefix}-cw`),
      wcTitle: document.getElementById(`matrix-${prefix}-wc-title`),
      cwTitle: document.getElementById(`matrix-${prefix}-cw-title`),
      section: document.getElementById(`matrix-${prefix}-section`),
      cachePath,
      cache: null,
    };
  }
  const optional = [
    optionalGroup("ew", "assets/matrix_cache_earlywar.json"),
    optionalGroup("pg", "assets/matrix_cache_pushgun.json"),
  ];

  function currentMode() {
    for (const r of modeRadios) if (r.checked) return r.value;
    return "hp";
  }

  function currentThreshold() {
    for (const r of thresholdRadios) if (r.checked) return r.value;
    return "30pct";
  }

  function colorFor(p) {
    const hue = Math.round(120 * p);  // 0=red, 0.5=yellow, 1=green
    return `hsl(${hue}, 70%, 70%)`;
  }

  function pick(matrices, mode, direction, threshold) {
    // v4: { mode: { dm: { wc:[...], cw:[...] } } }   dm in {"30pct","till_death"}
    // v3: { mode: { wc:[...], cw:[...] } }           (assumed dm="30pct")
    // v2: { mode: [...] }                            (assumed wc)
    // v1: [...]                                      (HP-only, assumed wc)
    if (Array.isArray(matrices)) return direction === "wc" ? matrices : null;
    const inner = matrices[mode] || matrices.hp;
    if (Array.isArray(inner)) return direction === "wc" ? inner : null;
    // v4 detection: the inner level has disable-mode keys
    if (inner["30pct"] || inner["till_death"]) {
      const byDm = inner[threshold] || inner["30pct"];
      return byDm ? byDm[direction] : null;
    }
    // v3 fallback (legacy cache, no threshold dimension)
    return inner[direction];
  }

  function renderTable(target, mats, step, rowKeys, rowLabels, colKeys, colLabels, rowFaction, colFaction) {
    if (!mats) {
      target.innerHTML = `<p class="muted">Not available in this cache.</p>`;
      return;
    }
    const m = mats[step];
    const tbl = document.createElement("table");
    tbl.className = "matrix";

    const head = document.createElement("tr");
    const corner = document.createElement("th");
    if (rowFaction && colFaction) {
      corner.innerHTML = `<span class="muted">${rowFaction} \\ ${colFaction}</span>`;
      corner.className = "corner";
    }
    head.appendChild(corner);
    colKeys.forEach((k, j) => {
      const th = document.createElement("th");
      th.textContent = colLabels[j] || k;
      th.title = k;
      head.appendChild(th);
    });
    tbl.appendChild(head);

    for (let i = 0; i < rowKeys.length; i++) {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = rowLabels[i] || rowKeys[i];
      th.title = rowKeys[i];
      th.className = "row-head";
      tr.appendChild(th);
      for (let j = 0; j < colKeys.length; j++) {
        const td = document.createElement("td");
        const p = m[i][j];
        td.textContent = (p * 100).toFixed(0);
        td.style.background = colorFor(p);
        td.title = `${rowKeys[i]} vs ${colKeys[j]}: ${(p * 100).toFixed(1)}%`;
        tr.appendChild(td);
      }
      tbl.appendChild(tr);
    }

    target.innerHTML = "";
    target.appendChild(tbl);
  }

  function renderOne(group) {
    const cache = group.cache;
    if (!cache) return;
    const step = parseInt(slider.value, 10);
    const mode = currentMode();
    const threshold = currentThreshold();

    // Cross-library caches (push guns vs tanks) describe each table
    // separately: tables[dir] = { rows, row_names, cols, col_names,
    // row_label, col_label }. Both tables are row-beats-column.
    if (cache.tables) {
      for (const dir of ["wc", "cw"]) {
        const t = cache.tables[dir];
        const title = dir === "wc" ? group.wcTitle : group.cwTitle;
        title.innerHTML = `${capitalize(t.row_label)} &times; ${capitalize(t.col_label)}`;
        renderTable(dir === "wc" ? group.wc : group.cw,
                    pick(cache.matrices, mode, dir, threshold), step,
                    t.rows, t.row_names, t.cols, t.col_names,
                    capitalize(t.row_label), capitalize(t.col_label));
      }
      return;
    }

    const rowF = cache.row_faction || "row";
    const colF = cache.col_faction || "col";
    const rowKeys = cache.row_tanks || cache.tanks;
    const rowNames = cache.row_names || cache.tank_names || rowKeys;
    const colKeys = cache.col_tanks || cache.tanks;
    const colNames = cache.col_names || cache.tank_names || colKeys;

    group.wcTitle.innerHTML = `${capitalize(rowF)} &times; ${capitalize(colF)}`;
    group.cwTitle.innerHTML = `${capitalize(colF)} &times; ${capitalize(rowF)}`;

    renderTable(group.wc, pick(cache.matrices, mode, "wc", threshold), step,
                rowKeys, rowNames, colKeys, colNames, rowF, colF);
    renderTable(group.cw, pick(cache.matrices, mode, "cw", threshold), step,
                colKeys, colNames, rowKeys, rowNames, colF, rowF);
  }

  function render() {
    const stepSrc = refCache();
    if (!stepSrc) return;
    const step = parseInt(slider.value, 10);
    readout.textContent = `armor = ${stepSrc.armor_steps[step].toFixed(2)}`;
    if (main.cache) renderOne(main);
    for (const g of optional) if (g.cache) renderOne(g);
  }

  function refCache() {
    if (main.cache) return main.cache;
    const g = optional.find(g => g.cache);
    return g ? g.cache : null;
  }

  function capitalize(s) {
    return s ? s[0].toUpperCase() + s.slice(1) : s;
  }

  async function fetchCache(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) return null;
    return await r.json();
  }

  async function boot() {
    // Main cache is required. Early-war / push-gun caches are optional —
    // missing or empty library => hide that section gracefully.
    try {
      main.cache = await fetchCache("assets/matrix_cache.json");
      if (!main.cache) throw new Error("matrix_cache.json missing");
    } catch (e) {
      console.error(e);
      main.wc.innerHTML = `<p class="err">Matrix cache not available (${e.message}). Run <code>python scripts/build_matrix_cache.py</code>.</p>`;
      main.cw.innerHTML = "";
    }

    for (const g of optional) {
      try {
        g.cache = await fetchCache(g.cachePath);
      } catch (e) {
        console.error(e);
      }
      if (!g.cache && g.section) g.section.style.display = "none";
    }

    const ref = refCache();
    if (ref) {
      slider.max = ref.armor_steps.length - 1;
      slider.value = ref.armor_steps.length - 1;
      slider.disabled = false;
      slider.addEventListener("input", render);
      modeRadios.forEach(rd => rd.addEventListener("change", render));
      thresholdRadios.forEach(rd => rd.addEventListener("change", render));
      if (Array.isArray(ref.matrices)) {
        const toggle = document.getElementById("matrix-mode");
        if (toggle) toggle.style.display = "none";
      }
      // Hide the threshold toggle on legacy caches that don't carry the
      // disable-mode dimension (everything stays at the implicit 30% default).
      if (!ref.disable_modes) {
        const thrToggle = document.getElementById("threshold-toggle");
        if (thrToggle) thrToggle.style.display = "none";
      }
      render();
    }
  }

  boot();
})();
