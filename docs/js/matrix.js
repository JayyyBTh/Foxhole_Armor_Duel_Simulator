// Loads matrix_cache.json and renders two side-by-side heatmap tables:
//   - Warden rows × Colonial cols (data.matrices[mode].wc)
//   - Colonial rows × Warden cols (data.matrices[mode].cw)
// Both update from the shared armor slider and the HP / weapon-disable
// toggle. No live simulation here — the cache is precomputed.

(function () {
  const slider = document.getElementById("armor-slider");
  const readout = document.getElementById("armor-readout");
  const wcContainer = document.getElementById("matrix-wc");
  const cwContainer = document.getElementById("matrix-cw");
  const wcTitle = document.getElementById("matrix-wc-title");
  const cwTitle = document.getElementById("matrix-cw-title");
  const modeRadios = document.querySelectorAll('input[name="matrix-mode"]');

  let cache = null;

  function currentMode() {
    for (const r of modeRadios) if (r.checked) return r.value;
    return "hp";
  }

  function colorFor(p) {
    const hue = Math.round(120 * p);  // 0=red, 0.5=yellow, 1=green
    return `hsl(${hue}, 70%, 70%)`;
  }

  function pick(matrices, mode, direction) {
    // v3: { mode: { wc:[...], cw:[...] } }
    // v2: { mode: [...] }              (assumed wc)
    // v1: [...]                        (HP-only, assumed wc)
    if (Array.isArray(matrices)) return direction === "wc" ? matrices : null;
    const inner = matrices[mode] || matrices.hp;
    if (Array.isArray(inner)) return direction === "wc" ? inner : null;
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

  function render() {
    const step = parseInt(slider.value, 10);
    readout.textContent = `armor = ${cache.armor_steps[step].toFixed(2)}`;
    const mode = currentMode();
    const rowF = cache.row_faction || "row";
    const colF = cache.col_faction || "col";
    const rowKeys = cache.row_tanks || cache.tanks;
    const rowNames = cache.row_names || cache.tank_names || rowKeys;
    const colKeys = cache.col_tanks || cache.tanks;
    const colNames = cache.col_names || cache.tank_names || colKeys;

    wcTitle.innerHTML = `${capitalize(rowF)} &times; ${capitalize(colF)}`;
    cwTitle.innerHTML = `${capitalize(colF)} &times; ${capitalize(rowF)}`;

    renderTable(wcContainer, pick(cache.matrices, mode, "wc"), step,
                rowKeys, rowNames, colKeys, colNames, rowF, colF);
    renderTable(cwContainer, pick(cache.matrices, mode, "cw"), step,
                colKeys, colNames, rowKeys, rowNames, colF, rowF);
  }

  function capitalize(s) {
    return s ? s[0].toUpperCase() + s.slice(1) : s;
  }

  async function boot() {
    try {
      const r = await fetch("assets/matrix_cache.json", { cache: "no-cache" });
      if (!r.ok) throw new Error(`matrix_cache.json: ${r.status}`);
      cache = await r.json();
      slider.max = cache.armor_steps.length - 1;
      slider.value = cache.armor_steps.length - 1;
      slider.disabled = false;
      slider.addEventListener("input", render);
      modeRadios.forEach(rd => rd.addEventListener("change", render));
      if (Array.isArray(cache.matrices)) {
        const toggle = document.getElementById("matrix-mode");
        if (toggle) toggle.style.display = "none";
      }
      render();
    } catch (e) {
      console.error(e);
      wcContainer.innerHTML = `<p class="err">Matrix cache not available (${e.message}). Run <code>python scripts/build_matrix_cache.py</code>.</p>`;
      cwContainer.innerHTML = "";
    }
  }

  boot();
})();
