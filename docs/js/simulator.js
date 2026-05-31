// Boots Pyodide, loads tank_duel.py + tanks.json into its virtual FS,
// and wires the single-duel form to simulate_duel / print_* via stdout
// capture. The print_* functions are unchanged in the source — we just
// redirect sys.stdout per call.

(function () {
  const PYODIDE_VERSION = "v0.26.2";
  const PYODIDE_URL = `https://cdn.jsdelivr.net/pyodide/${PYODIDE_VERSION}/full/`;

  const bootLabel = document.getElementById("boot-label");
  const bootDetail = document.getElementById("boot-detail");
  const bootStatus = document.getElementById("boot-status");
  const tank1Sel = document.getElementById("tank1");
  const tank2Sel = document.getElementById("tank2");
  const runBtn = document.getElementById("run");
  const form = document.getElementById("duel-form");
  const output = document.getElementById("output");

  let pyodide = null;
  let tankKeys = [];

  function setStatus(label, detail, klass) {
    bootLabel.textContent = label;
    bootDetail.textContent = detail || "";
    bootStatus.className = "card " + (klass || "");
  }

  async function fetchText(path) {
    const r = await fetch(path, { cache: "no-cache" });
    if (!r.ok) throw new Error(`fetch ${path}: ${r.status}`);
    return await r.text();
  }

  function populateSelect(sel, keys, names) {
    sel.innerHTML = "";
    keys.forEach((k, i) => {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = names[i] ? `${names[i]} (${k})` : k;
      sel.appendChild(o);
    });
    sel.disabled = false;
  }

  async function boot() {
    try {
      setStatus("Booting Pyodide…", " loading runtime");
      pyodide = await loadPyodide({ indexURL: PYODIDE_URL });

      setStatus("Loading tank_duel.py…", "");
      // earlywar.json is optional — if the fetch fails (e.g. the asset
      // wasn't synced), the picker still works with the main library only.
      let earlywarSrc = null;
      try {
        earlywarSrc = await fetchText("assets/earlywar.json");
      } catch (e) {
        console.warn("earlywar.json not loaded:", e.message);
      }
      const [pySrc, tanksSrc] = await Promise.all([
        fetchText("assets/tank_duel.py"),
        fetchText("assets/tanks.json"),
      ]);
      pyodide.FS.writeFile("tank_duel.py", pySrc);
      pyodide.FS.writeFile("tanks.json", tanksSrc);
      if (earlywarSrc !== null) {
        pyodide.FS.writeFile("earlywar.json", earlywarSrc);
      }

      await pyodide.runPythonAsync(`
import sys, importlib
from pathlib import Path
sys.path.insert(0, '.')
import tank_duel
importlib.reload(tank_duel)
_paths = [Path("tanks.json")]
if Path("earlywar.json").exists():
    _paths.append(Path("earlywar.json"))
_LIBRARY = tank_duel.load_libraries(_paths)
_TANK_KEYS = sorted(_LIBRARY.keys())
_TANK_NAMES = [_LIBRARY[k].name for k in _TANK_KEYS]
      `);

      tankKeys = pyodide.globals.get("_TANK_KEYS").toJs();
      const tankNames = pyodide.globals.get("_TANK_NAMES").toJs();
      populateSelect(tank1Sel, tankKeys, tankNames);
      populateSelect(tank2Sel, tankKeys, tankNames);
      if (tankKeys.length > 1) tank2Sel.selectedIndex = 1;

      runBtn.disabled = false;
      setStatus("Ready.", " " + tankKeys.length + " tanks loaded.", "ok");
    } catch (e) {
      console.error(e);
      setStatus("Boot failed.", " " + e.message, "err");
    }
  }

  async function runSimulation(ev) {
    ev.preventDefault();
    if (!pyodide) return;
    runBtn.disabled = true;
    output.textContent = "(running…)";

    const t1 = tank1Sel.value;
    const t2 = tank2Sel.value;
    const a1 = parseFloat(document.getElementById("armor1").value);
    const a2 = parseFloat(document.getElementById("armor2").value);
    const rng = parseFloat(document.getElementById("range_m").value);
    const shots = parseInt(document.getElementById("shots").value, 10);
    const mode = document.getElementById("mode").value;

    try {
      pyodide.globals.set("_t1k", t1);
      pyodide.globals.set("_t2k", t2);
      pyodide.globals.set("_a1", a1);
      pyodide.globals.set("_a2", a2);
      pyodide.globals.set("_rng", rng);
      pyodide.globals.set("_shots", shots);
      pyodide.globals.set("_mode", mode);

      const text = await pyodide.runPythonAsync(`
import io, sys
_t1 = _LIBRARY[_t1k]
_t2 = _LIBRARY[_t2k]
_result = tank_duel.simulate_duel(
    tank1=_t1, tank2=_t2,
    initial_armor_frac1=_a1, initial_armor_frac2=_a2,
    range_m=_rng, mode=_mode,
)
_buf = io.StringIO()
_saved = sys.stdout
sys.stdout = _buf
try:
    tank_duel.print_duel_summary(_t1, _t2, _result)
    tank_duel.print_shot_log(_t1, _t2, _result, n=_shots)
finally:
    sys.stdout = _saved
_buf.getvalue()
      `);
      output.textContent = text;
    } catch (e) {
      console.error(e);
      output.textContent = "Error: " + e.message;
    } finally {
      runBtn.disabled = false;
    }
  }

  form.addEventListener("submit", runSimulation);
  boot();
})();
