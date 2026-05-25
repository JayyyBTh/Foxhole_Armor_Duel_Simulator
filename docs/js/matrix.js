// Loads matrix_cache.json and renders a heatmap table that updates as
// the user drags the shared-armor slider. The slider snaps to the
// cached steps (no live simulation here).

(function () {
  const slider = document.getElementById("armor-slider");
  const readout = document.getElementById("armor-readout");
  const container = document.getElementById("matrix-container");

  let cache = null;

  function colorFor(p) {
    // 0 = red, 0.5 = yellow, 1 = green.
    const hue = Math.round(120 * p);
    return `hsl(${hue}, 70%, 70%)`;
  }

  function renderTable(step) {
    const m = cache.matrices[step];
    const keys = cache.tanks;
    const names = cache.tank_names || keys;
    const tbl = document.createElement("table");
    tbl.className = "matrix";

    const head = document.createElement("tr");
    head.appendChild(document.createElement("th"));
    keys.forEach((k, j) => {
      const th = document.createElement("th");
      th.textContent = names[j] || k;
      th.title = k;
      head.appendChild(th);
    });
    tbl.appendChild(head);

    for (let i = 0; i < keys.length; i++) {
      const row = document.createElement("tr");
      const th = document.createElement("th");
      th.textContent = names[i] || keys[i];
      th.title = keys[i];
      th.className = "row-head";
      row.appendChild(th);
      for (let j = 0; j < keys.length; j++) {
        const td = document.createElement("td");
        const p = m[i][j];
        td.textContent = (p * 100).toFixed(0);
        td.style.background = colorFor(p);
        td.title = `${keys[i]} vs ${keys[j]}: ${(p * 100).toFixed(1)}%`;
        row.appendChild(td);
      }
      tbl.appendChild(row);
    }

    container.innerHTML = "";
    container.appendChild(tbl);
  }

  function onSlide() {
    const step = parseInt(slider.value, 10);
    readout.textContent = `armor = ${cache.armor_steps[step].toFixed(2)}`;
    renderTable(step);
  }

  async function boot() {
    try {
      const r = await fetch("assets/matrix_cache.json", { cache: "no-cache" });
      if (!r.ok) throw new Error(`matrix_cache.json: ${r.status}`);
      cache = await r.json();
      slider.max = cache.armor_steps.length - 1;
      slider.value = cache.armor_steps.length - 1; // default: full armor
      slider.disabled = false;
      slider.addEventListener("input", onSlide);
      onSlide();
    } catch (e) {
      console.error(e);
      container.innerHTML = `<p class="err">Matrix cache not available (${e.message}). Run <code>python scripts/build_matrix_cache.py</code> or wait for the CI build.</p>`;
    }
  }

  boot();
})();
