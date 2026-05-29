/* Ironman Cairns 3D viewer (Mapbox GL JS with terrain DEM) */

const COLORS = { swim: "#0ea5e9", bike: "#f97316", run: "#10b981" };
const DISCIPLINES = ["swim", "bike", "run"];
const FREE_TIER_LOADS_PER_MONTH = 50000;
const OVERAGE_USD_PER_1K = 5.0;
const USAGE_KEY = "mapbox-load-log";

const state = {
  courses: {},
  climbs: { swim: [], bike: [], run: [] },
  courseInfo: null,
  weatherSeed: null,
  scrubDiscipline: "bike",
  riderMarker: null,
  layersAdded: false,
  windParticles: null,
  wind: { speedKmh: 17.5, fromDeg: 140, tempC: 23, humidity: 66 },
  rider: { powerW: 320, cda: 0.248, massKg: 75, crr: 0.004, drivetrainEff: 0.975 },
  routeColorMode: "default",
  simCache: { wind: null, calm: null, calmKey: null },
};

/* ---------- local usage tracking (this browser only) ---------- */

function loadLog() {
  try {
    return JSON.parse(localStorage.getItem(USAGE_KEY) || "[]");
  } catch { return []; }
}

function recordMapLoad() {
  const log = loadLog();
  log.push(Date.now());
  // Keep only the most recent 365 days
  const cutoff = Date.now() - 365 * 24 * 3600 * 1000;
  const trimmed = log.filter((t) => t > cutoff);
  localStorage.setItem(USAGE_KEY, JSON.stringify(trimmed));
  return trimmed;
}

function usageStats() {
  const log = loadLog();
  const now = new Date();
  const today = log.filter((t) => now - t < 24 * 3600 * 1000).length;
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).getTime();
  const thisMonth = log.filter((t) => t >= monthStart).length;
  const last7 = log.filter((t) => now - t < 7 * 24 * 3600 * 1000).length;
  const pctOfFree = (thisMonth / FREE_TIER_LOADS_PER_MONTH) * 100;
  const overageRunRate = Math.max(0, thisMonth - FREE_TIER_LOADS_PER_MONTH);
  const overageCostUsd = (overageRunRate / 1000) * OVERAGE_USD_PER_1K;
  return { today, last7, thisMonth, total: log.length, pctOfFree, overageRunRate, overageCostUsd };
}

function renderUsageInBanner() {
  const s = usageStats();
  const el = document.getElementById("usage-readout");
  if (!el) return;
  el.replaceChildren();

  const rows = [
    ["This month (local)", `${s.thisMonth} / ${FREE_TIER_LOADS_PER_MONTH.toLocaleString()}`],
    ["% of free tier",      `${s.pctOfFree.toFixed(3)}%`],
    ["Last 7 days",         `${s.last7}`],
    ["Today",               `${s.today}`],
  ];
  for (const [label, val] of rows) {
    const row = document.createElement("div");
    row.className = "usage-row";
    const l = document.createElement("span");
    l.className = "usage-label";
    l.textContent = label;
    const v = document.createElement("span");
    v.className = "usage-value";
    v.textContent = val;
    row.appendChild(l);
    row.appendChild(v);
    el.appendChild(row);
  }

  // Progress bar
  const bar = document.createElement("div");
  bar.className = "usage-bar";
  const fill = document.createElement("div");
  fill.className = "usage-bar-fill";
  fill.style.width = `${Math.min(100, s.pctOfFree)}%`;
  if (s.thisMonth >= FREE_TIER_LOADS_PER_MONTH) fill.style.background = "#ef4444";
  bar.appendChild(fill);
  el.appendChild(bar);

  if (s.overageCostUsd > 0) {
    const warn = document.createElement("div");
    warn.className = "usage-warn";
    warn.textContent = `Estimated overage this month: $${s.overageCostUsd.toFixed(2)} USD`;
    el.appendChild(warn);
  }
}

function renderUsageRibbon() {
  const s = usageStats();
  let el = document.getElementById("usage-ribbon");
  if (!el) {
    el = document.createElement("div");
    el.id = "usage-ribbon";
    document.body.appendChild(el);
  }
  el.replaceChildren();

  const label = document.createElement("span");
  label.className = "ribbon-label";
  label.textContent = "Mapbox loads (local)";
  const val = document.createElement("span");
  val.className = "ribbon-value";
  val.textContent = `${s.thisMonth} / ${FREE_TIER_LOADS_PER_MONTH.toLocaleString()}  ·  ${s.pctOfFree.toFixed(3)}%`;
  const link = document.createElement("a");
  link.href = "https://account.mapbox.com/";
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "dashboard ↗";
  link.className = "ribbon-link";

  el.appendChild(label);
  el.appendChild(val);
  el.appendChild(link);
}

// Defer Mapbox init until user explicitly confirms — each Map() call = 1 paid load.
window.addEventListener("DOMContentLoaded", () => {
  // Give config.local.js a tick to load (it's appended async via DOM)
  setTimeout(initGate, 100);
});

function initGate() {
  if (!window.MAPBOX_TOKEN) {
    document.getElementById("token-banner").hidden = false;
    return;
  }
  const autoLoad = sessionStorage.getItem("mapbox-auto-load") === "1";
  if (autoLoad) {
    boot();
    return;
  }
  const banner = document.getElementById("confirm-banner");
  banner.hidden = false;
  renderUsageInBanner();
  document.getElementById("confirm-load").addEventListener("click", () => {
    if (document.getElementById("auto-load").checked) {
      sessionStorage.setItem("mapbox-auto-load", "1");
    }
    banner.hidden = true;
    boot();
  });
}

async function boot() {
  document.getElementById("pitch-controls").hidden = false;
  document.getElementById("scrubber-3d").hidden = false;

  await loadAll();

  mapboxgl.accessToken = window.MAPBOX_TOKEN;
  recordMapLoad();          // <-- one load counted, this is the only place
  renderUsageRibbon();
  const map = new mapboxgl.Map({
    container: "map3d",
    style: "mapbox://styles/mapbox/satellite-streets-v12",
    center: [145.55, -16.78],
    zoom: 11,
    pitch: 60,
    bearing: 0,
    antialias: true,
  });
  state.map = map;
  // WindParticles owns its own map.on("resize") + matchMedia subscription;
  // no extra listener needed here.

  // Guard against the WebGL canvas being created at 0x0 when the map inits
  // before the container's layout is flushed (blank map, attribution still
  // shows, no console error). Re-measure once layout settles. Harmless if the
  // canvas was already sized correctly.
  const forceResize = () => { try { map.resize(); } catch (_) {} };
  requestAnimationFrame(forceResize);
  setTimeout(forceResize, 250);
  window.addEventListener("load", forceResize);

  map.on("load", () => {
    forceResize();
    // Add terrain DEM
    map.addSource("dem", {
      type: "raster-dem",
      url: "mapbox://mapbox.mapbox-terrain-dem-v1",
      tileSize: 512,
      maxzoom: 14,
    });
    map.setTerrain({ source: "dem", exaggeration: 1.5 });

    // Sky
    map.addLayer({
      id: "sky",
      type: "sky",
      paint: {
        "sky-type": "atmosphere",
        "sky-atmosphere-sun-intensity": 6,
        "sky-atmosphere-sun": [0, 0],
      },
    });

    addCourseLayers(map);
    addAidStationLayers(map);
    fitToCourses(map);
    setupControls(map);
    setupScrubber(map);
    initWindAndSim(map);
    state.layersAdded = true;

    // Terrain gets silently disabled by Safari's fingerprinting/tracking
    // protection (and private browsing) — it blocks the Canvas2D readback
    // Mapbox needs to decode DEM elevation. Detect that and tell the user
    // instead of leaving them staring at a flat map.
    setTimeout(() => { if (!map.getTerrain()) showTerrainDisabledBanner(); }, 1500);
  });
}

function showTerrainDisabledBanner() {
  if (document.getElementById("terrain-warn")) return;
  const b = document.createElement("div");
  b.id = "terrain-warn";
  b.style.cssText =
    "position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:1000;" +
    "background:#3a2a00;color:#ffd479;border:1px solid #6b5200;padding:10px 14px;" +
    "border-radius:8px;font:13px/1.45 -apple-system,BlinkMacSystemFont,sans-serif;" +
    "max-width:540px;box-shadow:0 2px 12px rgba(0,0,0,.45)";
  const msg = document.createElement("span");
  msg.textContent =
    "3D terrain is disabled by this browser's fingerprinting/tracking protection " +
    "(common in Safari and private windows). For the 3D view, open this page in " +
    "Chrome or Firefox — or turn off Safari → Settings → Privacy → “Advanced " +
    "Tracking and Fingerprinting Protection” and reload.";
  const x = document.createElement("button");
  x.textContent = "×";
  x.setAttribute("aria-label", "Dismiss");
  x.style.cssText =
    "margin-left:10px;background:none;border:none;color:#ffd479;font-size:18px;" +
    "line-height:1;cursor:pointer;vertical-align:top";
  x.onclick = () => b.remove();
  b.append(msg, x);
  (document.querySelector("main") || document.body).appendChild(b);
}

/* ---------- wind & simulator integration ---------- */

function initWindAndSim(map) {
  const panel = document.getElementById("wind-panel");
  panel.hidden = false;

  state.windParticles = new window.Wind.WindParticles(map, document.getElementById("map3d"));

  // Wire sliders to defaults from weather seed
  const speed = document.getElementById("wind-speed-range");
  const dir = document.getElementById("wind-dir-range");
  const temp = document.getElementById("temp-range");
  const power = document.getElementById("power-range");
  const cda = document.getElementById("cda-range");
  const mass = document.getElementById("mass-range");

  speed.value = state.wind.speedKmh.toFixed(1);
  dir.value = Math.round(state.wind.fromDeg);
  temp.value = state.wind.tempC.toFixed(1);
  power.value = state.rider.powerW;
  cda.value = state.rider.cda;
  mass.value = state.rider.massKg;

  const onWindChange = () => {
    state.wind.speedKmh = Number(speed.value);
    state.wind.fromDeg = Number(dir.value);
    state.wind.tempC = Number(temp.value);
    refreshAll();
  };
  speed.addEventListener("input", onWindChange);
  dir.addEventListener("input", onWindChange);
  temp.addEventListener("input", onWindChange);

  const onRiderChange = () => {
    state.rider.powerW = Number(power.value);
    state.rider.cda = Number(cda.value);
    state.rider.massKg = Number(mass.value);
    refreshAll();
  };
  power.addEventListener("input", onRiderChange);
  cda.addEventListener("input", onRiderChange);
  mass.addEventListener("input", onRiderChange);

  // Presets
  document.querySelectorAll(".preset-btn[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
  });

  // Color mode toggle
  document.querySelectorAll(".preset-btn[data-color-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".preset-btn[data-color-mode]").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      state.routeColorMode = btn.dataset.colorMode;
      applyRouteColor(map);
      renderLegend();
      updateClimbsPanelVisibility();
      if (["climbs", "climbs-wind"].includes(state.routeColorMode)) {
        renderClimbsPanel();
      }
    });
  });

  // Climbs panel scaffold
  setupClimbsPanel();
  renderClimbsPanel();

  // Panel collapse
  document.getElementById("wind-panel-toggle").addEventListener("click", () => {
    panel.classList.toggle("collapsed");
    document.getElementById("wind-panel-chevron").textContent =
      panel.classList.contains("collapsed") ? "▸" : "▾";
  });

  // Initial render
  refreshAll();
}

function applyPreset(name) {
  const w = state.weatherSeed || {};
  switch (name) {
    case "median":
      state.wind.speedKmh = w.wind_speed_kmh_median ?? 17.5;
      state.wind.fromDeg = w.wind_direction_vector_mean_deg ?? 140;
      state.wind.tempC = w.temp_c_mean_median ?? 23;
      break;
    case "calm":
      state.wind.speedKmh = 5; state.wind.fromDeg = 140; state.wind.tempC = 24;
      break;
    case "windy":
      state.wind.speedKmh = 28; state.wind.fromDeg = 140; state.wind.tempC = 22;
      break;
    case "north-tail":
      // Bike course heads N out, S back. "Tail N" means tailwind going north (wind from S = 180°)
      state.wind.speedKmh = 20; state.wind.fromDeg = 180; state.wind.tempC = 23;
      break;
    case "north-head":
      // Headwind going north (wind from N = 0°)
      state.wind.speedKmh = 20; state.wind.fromDeg = 0; state.wind.tempC = 23;
      break;
    case "zero":
      state.wind.speedKmh = 0; state.wind.tempC = 23;
      break;
  }
  // Reflect into sliders
  document.getElementById("wind-speed-range").value = state.wind.speedKmh.toFixed(1);
  document.getElementById("wind-dir-range").value = Math.round(state.wind.fromDeg);
  document.getElementById("temp-range").value = state.wind.tempC.toFixed(1);
  document.querySelectorAll(".preset-btn[data-preset]").forEach((b) =>
    b.classList.toggle("active", b.dataset.preset === name)
  );
  refreshAll();
}

function refreshAll() {
  updateSliderReadouts();
  updateCompassAndDisplay();
  if (state.windParticles) {
    state.windParticles.set(state.wind.speedKmh, state.wind.fromDeg, true);
  }
  runSimulationAndRender();
}

function updateSliderReadouts() {
  document.getElementById("wind-speed-readout").textContent = `${state.wind.speedKmh.toFixed(1)} km/h`;
  document.getElementById("wind-dir-readout").textContent = `${Math.round(state.wind.fromDeg)}°`;
  document.getElementById("temp-readout").textContent = `${state.wind.tempC.toFixed(1)}°C`;
  document.getElementById("power-readout").textContent = `${state.rider.powerW} W`;
  document.getElementById("cda-readout").textContent = state.rider.cda.toFixed(3);
  document.getElementById("mass-readout").textContent = `${state.rider.massKg.toFixed(1)} kg`;
}

function updateCompassAndDisplay() {
  const canvas = document.getElementById("compass-dial");
  window.Wind.drawCompass(canvas, state.wind.fromDeg, state.wind.speedKmh);

  document.getElementById("wind-speed-display").textContent =
    `${state.wind.speedKmh.toFixed(1)} km/h`;
  document.getElementById("wind-dir-display").textContent =
    `${cardinal(state.wind.fromDeg)} (${Math.round(state.wind.fromDeg)}°)`;

  const rho = window.Physics.airDensity(state.wind.tempC, 1013.25, state.wind.humidity);
  document.getElementById("rho-display").textContent = `${rho.toFixed(3)} kg/m³`;
}

function cardinal(deg) {
  const dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
  return dirs[Math.round(((deg % 360) / 22.5)) % 16];
}

function runSimulationAndRender() {
  const points = state.courses.bike.points;
  const baseParams = {
    powerW: state.rider.powerW,
    cda: state.rider.cda,
    crr: state.rider.crr,
    massKg: state.rider.massKg,
    drivetrainEff: state.rider.drivetrainEff,
    tempC: state.wind.tempC,
    humidityPct: state.wind.humidity,
  };

  const withWind = window.Physics.simulateForward(points, {
    ...baseParams,
    windSpeedKmh: state.wind.speedKmh,
    windFromDeg: state.wind.fromDeg,
  });
  // Calm baseline depends only on rider params + temp/humidity — cache it.
  const calmKey = [
    baseParams.powerW, baseParams.cda, baseParams.crr,
    baseParams.massKg, baseParams.drivetrainEff,
    baseParams.tempC, baseParams.humidityPct,
  ].join("|");
  let noWind = state.simCache.calm;
  if (state.simCache.calmKey !== calmKey || !noWind) {
    noWind = window.Physics.simulateForward(points, {
      ...baseParams, windSpeedKmh: 0, windFromDeg: 0,
    });
    state.simCache.calm = noWind;
    state.simCache.calmKey = calmKey;
  }
  state.simCache.wind = withWind;

  // Output
  document.getElementById("sim-time").textContent = withWind.totalTimeHms;
  document.getElementById("sim-baseline").textContent = noWind.totalTimeHms;
  const delta = withWind.totalTimeS - noWind.totalTimeS;
  const deltaEl = document.getElementById("sim-delta");
  deltaEl.textContent = window.Physics.formatHMSDelta(delta);
  const row = document.getElementById("sim-baseline-row");
  row.classList.toggle("delta-positive", delta > 0);
  row.classList.toggle("delta-negative", delta < 0);
  document.getElementById("sim-speed").textContent = `${withWind.avgSpeedKmh.toFixed(2)} km/h`;

  if (["wind", "climbs-wind"].includes(state.routeColorMode) && state.map) {
    applyRouteColor(state.map);
  }
  // Only the wind annotations need to update on a slider tick — full rebuild
  // (with stats + shape + tags) only when the discipline or climb list changes.
  if (["climbs", "climbs-wind"].includes(state.routeColorMode)) {
    updateClimbWindRows();
  }
}

// Partial update: replace just the climb-wind row in each existing card.
function updateClimbWindRows() {
  const cards = document.querySelectorAll(".climb3d-card");
  if (cards.length === 0) return;
  const climbs = state.climbs.bike || [];
  const byId = new Map(climbs.map((c) => [String(c.id), c]));
  for (const card of cards) {
    const c = byId.get(card.dataset.climbId);
    if (!c) continue;
    const existing = card.querySelector(".climb3d-wind");
    if (existing) existing.remove();
    const row = renderClimbWindRow(c);
    if (row) card.appendChild(row);
  }
}

function applyRouteColor(map) {
  const layerId = "route-bike";
  const points = state.courses.bike.points;
  try {
    let grad = null;
    let width = 3.5;
    if (state.routeColorMode === "wind") {
      if (state.simCache.wind && state.simCache.calm) {
        grad = window.Wind.buildWindImpactGradient(points, state.simCache.wind, state.simCache.calm);
        width = 5;
      }
    } else if (state.routeColorMode === "climbs") {
      grad = window.Wind.buildClimbsGradient(points);
      width = 5;
    } else if (state.routeColorMode === "climbs-wind") {
      grad = window.Wind.buildClimbsWindGradient(points, {
        windSpeedKmh: state.wind.speedKmh,
        windFromDeg: state.wind.fromDeg,
        massKg: state.rider.massKg,
        cda: state.rider.cda,
        tempC: state.wind.tempC,
        humidityPct: state.wind.humidity,
      });
      width = 5;
    }
    if (!grad) {
      grad = ["interpolate", ["linear"], ["line-progress"], 0, COLORS.bike, 1, COLORS.bike];
      width = 3.5;
    }
    map.setPaintProperty(layerId, "line-gradient", grad);
    map.setPaintProperty(layerId, "line-width", width);
  } catch (e) {
    console.warn("route color apply failed:", e);
  }
}

/* ---------- Color legend ---------- */

function renderLegend() {
  const el = document.getElementById("color-legend");
  if (!el) return;
  el.replaceChildren();
  const mode = state.routeColorMode;
  if (mode === "default") {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  if (mode === "wind") {
    const title = document.createElement("div");
    title.className = "color-legend-title";
    title.textContent = "Wind impact on speed";
    el.appendChild(title);

    const bar = document.createElement("div");
    bar.className = "legend-bar";
    bar.style.background = "linear-gradient(to right, rgb(239,68,68), rgb(120,120,130), rgb(56,189,248))";
    bar.style.height = "10px";
    el.appendChild(bar);

    const labels = document.createElement("div");
    labels.className = "legend-labels";
    [["Headwind", "−20%"], ["Neutral", "0"], ["Tailwind", "+20%"]].forEach(([t, p]) => {
      const span = document.createElement("span");
      span.textContent = `${t} (${p})`;
      labels.appendChild(span);
    });
    el.appendChild(labels);

    const note = document.createElement("p");
    note.textContent = "Per-segment speed change vs. a calm-day baseline at the same power.";
    el.appendChild(note);
  } else if (mode === "climbs" || mode === "climbs-wind") {
    const title = document.createElement("div");
    title.className = "color-legend-title";
    title.textContent = mode === "climbs" ? "Gradient severity" : "Effective gradient (incl. wind)";
    el.appendChild(title);

    const grid = document.createElement("div");
    grid.className = "legend-grid";
    const rows = [
      ["rgb(16,185,129)",  "Flat or descent",  "≤ 0%"],
      ["rgb(251,191,36)",  "Mild climb",       "0–2%"],
      ["rgb(249,115,22)",  "Moderate",         "2–4%"],
      ["rgb(239,68,68)",   "Hard",             "4–7%"],
      ["rgb(124,58,237)",  "Very hard",        "≥ 10%"],
    ];
    for (const [color, label, val] of rows) {
      const sw = document.createElement("span");
      sw.className = "legend-swatch";
      sw.style.background = color;
      const lbl = document.createElement("span");
      lbl.className = "label";
      lbl.textContent = label;
      const v = document.createElement("span");
      v.className = "value";
      v.textContent = val;
      grid.appendChild(sw);
      grid.appendChild(lbl);
      grid.appendChild(v);
    }
    el.appendChild(grid);

    if (mode === "climbs-wind") {
      const note = document.createElement("p");
      note.textContent = "Wind converted to virtual grade at 41 km/h reference. Headwind steepens the climb feel; tailwind flattens it.";
      el.appendChild(note);
    }
  }
}

/* ---------- Climbs sidebar (3D port) ---------- */

function setupClimbsPanel() {
  const panel = document.getElementById("climbs-panel");
  const toggle = document.getElementById("climbs-panel-toggle");
  toggle.addEventListener("click", () => {
    panel.classList.toggle("collapsed");
    document.getElementById("climbs-panel-chevron").textContent =
      panel.classList.contains("collapsed") ? "▸" : "▾";
  });
}

function updateClimbsPanelVisibility() {
  const panel = document.getElementById("climbs-panel");
  const showInModes = ["climbs", "climbs-wind"];
  panel.hidden = !showInModes.includes(state.routeColorMode);
}

function renderClimbsPanel() {
  const root = document.getElementById("climbs-3d-list");
  if (!root) return;
  root.replaceChildren();
  const climbs = state.climbs.bike || [];
  if (climbs.length === 0) {
    const p = document.createElement("p");
    p.style.cssText = "color: var(--muted); font-size: 12px; text-align: center;";
    p.textContent = "No notable climbs detected.";
    root.appendChild(p);
    return;
  }
  document.getElementById("climbs-panel-title").textContent =
    `Notable Climbs — bike (${climbs.length})`;

  for (const c of climbs) {
    const card = document.createElement("div");
    card.className = "climb3d-card";
    card.dataset.climbId = c.id;

    const head = document.createElement("div");
    head.className = "climb3d-head";
    const tag = document.createElement("span");
    tag.className = "climb3d-cat";
    tag.textContent = c.category === "—" ? "—" : `Cat ${c.category}`;
    const title = document.createElement("span");
    title.className = "climb3d-title";
    title.textContent = `#${c.id}  km ${c.start_km.toFixed(1)} → ${c.end_km.toFixed(1)}`;
    head.appendChild(tag); head.appendChild(title);
    card.appendChild(head);

    const stats = document.createElement("div");
    stats.className = "climb3d-stats";
    [
      ["Length", `${c.length_m.toFixed(0)} m`],
      ["Gain",   `${c.elev_gain_m.toFixed(0)} m`],
      ["Avg",    `${c.avg_grade_pct.toFixed(1)}%`],
      ["Max",    `${c.max_grade_pct.toFixed(1)}%`],
    ].forEach(([l, v]) => {
      const cell = document.createElement("div");
      const lbl = document.createElement("div");
      lbl.className = "lbl"; lbl.textContent = l;
      const val = document.createElement("div");
      val.className = "val"; val.textContent = v;
      cell.appendChild(lbl); cell.appendChild(val);
      stats.appendChild(cell);
    });
    card.appendChild(stats);

    if (c.shape_label) {
      const s = document.createElement("div");
      s.className = "climb3d-shape";
      s.textContent = c.shape_label;
      card.appendChild(s);
    }

    // Wind impact at this climb's midpoint
    const windRow = renderClimbWindRow(c);
    if (windRow) card.appendChild(windRow);

    card.addEventListener("click", () => flyToClimb(c));
    root.appendChild(card);
  }
}

function renderClimbWindRow(climb) {
  if (state.wind.speedKmh <= 0.5) return null;
  const points = state.courses.bike.points;
  const mid = points[Math.floor((climb.start_idx + climb.end_idx) / 2)];
  if (!mid) return null;
  const bearing = mid.bearing_deg || 0;
  const windAlong = window.Physics.windAlongHeading(state.wind.speedKmh, state.wind.fromDeg, bearing);
  const windAlongKmh = windAlong * 3.6;
  const tag = Math.abs(windAlongKmh) < 1.5 ? "cross"
            : (windAlongKmh > 0 ? "tailwind" : "headwind");
  const row = document.createElement("div");
  row.className = `climb3d-wind ${tag}`;
  const lbl = document.createElement("span");
  lbl.textContent = `Heading ${Math.round(bearing)}°`;
  const eff = document.createElement("span");
  eff.className = "wind-eff";
  const label =
    tag === "tailwind" ? `↓ tailwind +${windAlongKmh.toFixed(1)} km/h` :
    tag === "headwind" ? `↑ headwind ${windAlongKmh.toFixed(1)} km/h` :
    `~ crosswind`;
  eff.textContent = label;
  row.appendChild(lbl); row.appendChild(eff);
  return row;
}

function flyToClimb(climb) {
  const points = state.courses.bike.points;
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
  for (let i = climb.start_idx; i <= climb.end_idx && i < points.length; i++) {
    const p = points[i];
    if (p.lng < minLng) minLng = p.lng;
    if (p.lng > maxLng) maxLng = p.lng;
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
  }
  if (minLng === Infinity) return;
  state.map.fitBounds([[minLng, minLat], [maxLng, maxLat]], { padding: 80, pitch: 65, duration: 1400 });
  document.querySelectorAll(".climb3d-card").forEach((el) =>
    el.classList.toggle("active", Number(el.dataset.climbId) === climb.id)
  );
}

async function loadAll() {
  const fetchJSON = async (path) => {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  };
  const fetchJSONOptional = async (path) => {
    try { return await fetchJSON(path); } catch { return null; }
  };
  const [swim, bike, run, climbs, info, weather] = await Promise.all([
    fetchJSON("../processed/swim_course.json"),
    fetchJSON("../processed/bike_course.json"),
    fetchJSON("../processed/run_course.json"),
    fetchJSON("../processed/climbs.json"),
    fetchJSON("../processed/course_info.json"),
    fetchJSONOptional("../processed/weather_seed_defaults.json"),
  ]);
  state.courses = { swim, bike, run };
  state.climbs = climbs;
  state.courseInfo = info;
  state.weatherSeed = weather;
  if (weather) {
    state.wind.speedKmh = weather.wind_speed_kmh_median;
    state.wind.fromDeg = weather.wind_direction_vector_mean_deg;
    state.wind.tempC = weather.temp_c_mean_median;
    state.wind.humidity = weather.humidity_pct_mean_median;
  }
}

function addCourseLayers(map) {
  for (const slug of DISCIPLINES) {
    const coords = state.courses[slug].points.map((p) => [p.lng, p.lat]);
    map.addSource(`route-${slug}`, {
      type: "geojson",
      lineMetrics: true,
      data: { type: "Feature", geometry: { type: "LineString", coordinates: coords } },
    });
    map.addLayer({
      id: `route-${slug}-glow`,
      type: "line",
      source: `route-${slug}`,
      paint: {
        "line-color": COLORS[slug],
        "line-width": 8,
        "line-opacity": 0.25,
        "line-blur": 4,
      },
    });
    map.addLayer({
      id: `route-${slug}`,
      type: "line",
      source: `route-${slug}`,
      paint: {
        "line-color": COLORS[slug],
        "line-width": slug === "swim" ? 5 : 3.5,
      },
    });
  }

  // Climb highlight overlay on the bike route
  const climbFeatures = [];
  for (const c of state.climbs.bike || []) {
    const pts = state.courses.bike.points.slice(c.start_idx, c.end_idx + 1);
    climbFeatures.push({
      type: "Feature",
      properties: { id: c.id, label: c.shape_label || "" },
      geometry: { type: "LineString", coordinates: pts.map((p) => [p.lng, p.lat]) },
    });
  }
  map.addSource("climbs", {
    type: "geojson",
    data: { type: "FeatureCollection", features: climbFeatures },
  });
  map.addLayer({
    id: "climbs-highlight",
    type: "line",
    source: "climbs",
    paint: { "line-color": "#fbbf24", "line-width": 8, "line-opacity": 0.45 },
  });
}

function addAidStationLayers(map) {
  if (!state.courseInfo) return;
  const features = [];
  const push = (a, discipline) => {
    features.push({
      type: "Feature",
      properties: { name: a.name, discipline, km: a.km_marks.join(", ") },
      geometry: { type: "Point", coordinates: [a.lng, a.lat] },
    });
  };
  state.courseInfo.bike.aid_stations.forEach((a) => push(a, "bike"));
  state.courseInfo.run.aid_stations.forEach((a) => push(a, "run"));

  map.addSource("aid-stations", {
    type: "geojson",
    data: { type: "FeatureCollection", features },
  });
  map.addLayer({
    id: "aid-circles",
    type: "circle",
    source: "aid-stations",
    paint: {
      "circle-radius": 6,
      "circle-color": [
        "match",
        ["get", "discipline"],
        "bike", COLORS.bike,
        "run", COLORS.run,
        "#fff",
      ],
      "circle-stroke-color": "#fff",
      "circle-stroke-width": 1.5,
    },
  });

  map.on("click", "aid-circles", (e) => {
    const p = e.features[0].properties;
    new mapboxgl.Popup({ offset: 12 })
      .setLngLat(e.features[0].geometry.coordinates)
      .setHTML(`<strong>${p.name}</strong><br/>at km ${p.km}`)
      .addTo(map);
  });
  map.on("mouseenter", "aid-circles", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "aid-circles", () => (map.getCanvas().style.cursor = ""));
}

function fitToCourses(map) {
  // Avoid Math.min/max spread on long arrays (Safari arg-count limit ~64k).
  let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
  for (const slug of DISCIPLINES) {
    for (const p of state.courses[slug].points) {
      if (p.lng < minLng) minLng = p.lng;
      if (p.lng > maxLng) maxLng = p.lng;
      if (p.lat < minLat) minLat = p.lat;
      if (p.lat > maxLat) maxLat = p.lat;
    }
  }
  map.fitBounds([[minLng, minLat], [maxLng, maxLat]], { padding: 50, pitch: 60, duration: 800 });
}

/* discipline & view controls */
function setupControls(map) {
  document.querySelectorAll(".discipline-toggles input").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      const slug = e.target.dataset.discipline;
      const vis = e.target.checked ? "visible" : "none";
      map.setLayoutProperty(`route-${slug}`, "visibility", vis);
      map.setLayoutProperty(`route-${slug}-glow`, "visibility", vis);
    });
  });

  const pitch = document.getElementById("pitch-range");
  const bearing = document.getElementById("bearing-range");
  const exag = document.getElementById("exag-range");
  pitch.addEventListener("input", () => map.setPitch(Number(pitch.value)));
  bearing.addEventListener("input", () => map.setBearing(Number(bearing.value)));
  exag.addEventListener("input", () => map.setTerrain({ source: "dem", exaggeration: Number(exag.value) }));

  // Sync sliders back when user drags on the map
  map.on("pitch", () => (pitch.value = Math.round(map.getPitch())));
  map.on("rotate", () => (bearing.value = Math.round(map.getBearing())));
}

/* scrubber */
function setupScrubber(map) {
  document.getElementById("scrub-discipline-3d").addEventListener("change", (e) => {
    state.scrubDiscipline = e.target.value;
    updateScrub(map);
  });
  document.getElementById("scrub-range-3d").addEventListener("input", () => updateScrub(map));
  document.getElementById("fly-btn").addEventListener("click", () => flyToScrub(map));
}

function updateScrub(map) {
  const slug = state.scrubDiscipline;
  const course = state.courses[slug];
  if (!course) return;
  const range = document.getElementById("scrub-range-3d");
  const frac = Number(range.value) / Number(range.max);
  const idx = Math.max(0, Math.min(course.points.length - 1, Math.floor(frac * (course.points.length - 1))));
  const p = course.points[idx];

  document.getElementById("r3d-dist").textContent = `${(p.dist_m / 1000).toFixed(2)} km`;
  document.getElementById("r3d-alt").textContent = p.alt == null ? "— m" : `${p.alt.toFixed(1)} m`;
  document.getElementById("r3d-grade").textContent = `${p.grade_pct.toFixed(1)}%`;
  document.getElementById("r3d-bearing").textContent = `${p.bearing_deg.toFixed(0)}°`;

  // Reuse the marker; recreating per input event thrashes DOM nodes.
  if (!state.riderMarker) {
    const el = document.createElement("div");
    el.className = "rider-marker";
    state.riderMarker = new mapboxgl.Marker({ element: el }).setLngLat([p.lng, p.lat]).addTo(map);
    state.riderMarkerEl = el;
    state.riderMarkerSlug = null;
  } else {
    state.riderMarker.setLngLat([p.lng, p.lat]);
  }
  if (state.riderMarkerSlug !== slug) {
    state.riderMarkerEl.style.cssText =
      `width:14px;height:14px;border-radius:50%;background:#fff;` +
      `border:3px solid ${COLORS[slug]};box-shadow:0 0 12px ${COLORS[slug]};`;
    state.riderMarkerSlug = slug;
  }
}

function flyToScrub(map) {
  const slug = state.scrubDiscipline;
  const course = state.courses[slug];
  const range = document.getElementById("scrub-range-3d");
  const frac = Number(range.value) / Number(range.max);
  const idx = Math.max(0, Math.min(course.points.length - 1, Math.floor(frac * (course.points.length - 1))));
  const p = course.points[idx];
  // Orient the camera along the rider's heading for a "first-person" feel.
  map.flyTo({
    center: [p.lng, p.lat],
    zoom: 15.5,
    pitch: 70,
    bearing: p.bearing_deg,
    duration: 1600,
    essential: true,
  });
}
