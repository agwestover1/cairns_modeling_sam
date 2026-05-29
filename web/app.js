/* Ironman Cairns Course Explorer
 * Phase 1 + Phase 2: course viewer, climbs, aid stations, weather seed
 */

const COLORS = {
  swim: "#0ea5e9",
  bike: "#f97316",
  run:  "#10b981",
};

const DISCIPLINES = ["swim", "bike", "run"];

const state = {
  courses: {},           // slug -> parsed course JSON
  polylines: {},         // slug -> L.Polyline
  scrubMarker: null,
  scrubDiscipline: "bike",
  climbs: { swim: [], bike: [], run: [] },
  climbPolylines: [],    // overlay polylines for climbs on the map
  aidMarkers: [],
  needsMarkers: [],
  courseInfo: null,
  weatherSeed: null,
  smoothCache: {},       // slug -> smoothed elevation array (cache per window)
  smoothWindowPts: 6,    // current smoothing window (in points; ~15m per point on bike)
  activeClimbHighlight: null,
  show: { aid: true, needs: true, climbs: true },
};

const map = L.map("map", { zoomControl: true, attributionControl: true });

L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    attribution:
      "Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, GIS User Community",
    maxZoom: 19,
  }
).addTo(map);

L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 19, opacity: 0.7 }
).addTo(map);

/* ---------- loaders ---------- */

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function loadAll() {
  const [swim, bike, run, climbs, info, weather, sim, race] = await Promise.all([
    fetchJSON("../processed/swim_course.json"),
    fetchJSON("../processed/bike_course.json"),
    fetchJSON("../processed/run_course.json"),
    fetchJSON("../processed/climbs.json"),
    fetchJSON("../processed/course_info.json"),
    fetchJSON("../processed/weather_seed_defaults.json").catch(() => null),
    fetchJSON("../processed/sim_results.json").catch(() => null),
    fetchJSON("../processed/race_projection.json").catch(() => null),
  ]);
  state.courses.swim = swim;
  state.courses.bike = bike;
  state.courses.run = run;
  state.climbs = climbs;
  state.courseInfo = info;
  state.weatherSeed = weather;
  state.simResults = sim;
  state.raceProjection = race;
}

/* ---------- time helpers ---------- */

function hmsToSec(hms) {
  const parts = String(hms).split(":").map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return Number(hms) || 0;
}

function secToHms(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

/* ---------- map: courses, aid stations, climbs ---------- */

function drawCourse(slug) {
  const data = state.courses[slug];
  const latlngs = data.points.map((p) => [p.lat, p.lng]);
  const line = L.polyline(latlngs, {
    color: COLORS[slug],
    weight: slug === "swim" ? 4 : 3,
    opacity: 0.9,
    lineJoin: "round",
  }).addTo(map);
  state.polylines[slug] = line;

  L.circleMarker(latlngs[0], {
    radius: 5, color: "#fff", weight: 2,
    fillColor: COLORS[slug], fillOpacity: 1,
  }).addTo(map).bindTooltip(`${slug} start`);
  L.circleMarker(latlngs[latlngs.length - 1], {
    radius: 5, color: COLORS[slug], weight: 2,
    fillColor: "#fff", fillOpacity: 1,
  }).addTo(map).bindTooltip(`${slug} finish`);
}

function drawAidStations() {
  const info = state.courseInfo;
  if (!info) return;

  const all = [
    ...info.bike.aid_stations.map((a) => ({ ...a, discipline: "bike", kind: "aid" })),
    ...info.run.aid_stations.map((a) => ({ ...a, discipline: "run",  kind: "aid" })),
  ];
  for (const a of all) {
    const m = L.marker([a.lat, a.lng], {
      icon: L.divIcon({
        className: "aid-marker",
        html: `<div class="aid-pin aid-${a.discipline}" title="${a.name}">A</div>`,
        iconSize: [18, 18], iconAnchor: [9, 9],
      }),
    }).addTo(map).bindTooltip(
      `<strong>${a.name}</strong><br/>at km ${a.km_marks.join(', ')}`,
      { direction: "top" }
    );
    state.aidMarkers.push(m);
  }

  const allNeeds = [
    ...info.bike.personal_needs.map((a) => ({ ...a, discipline: "bike" })),
    ...info.run.personal_needs.map((a) => ({ ...a, discipline: "run" })),
  ];
  for (const a of allNeeds) {
    const m = L.marker([a.lat, a.lng], {
      icon: L.divIcon({
        className: "needs-marker",
        html: `<div class="needs-pin needs-${a.discipline}" title="${a.name}">PN</div>`,
        iconSize: [22, 18], iconAnchor: [11, 9],
      }),
    }).addTo(map).bindTooltip(
      `<strong>${a.name}</strong><br/>at km ${a.km_marks.join(', ')}`,
      { direction: "top" }
    );
    state.needsMarkers.push(m);
  }
}

function drawClimbsOnMap() {
  for (const slug of DISCIPLINES) {
    const climbs = state.climbs[slug] || [];
    if (climbs.length === 0) continue;
    const points = state.courses[slug].points;
    for (const c of climbs) {
      const slice = points.slice(c.start_idx, c.end_idx + 1).map((p) => [p.lat, p.lng]);
      const pl = L.polyline(slice, {
        color: "#fbbf24",
        weight: 6,
        opacity: 0.55,
        lineCap: "round",
        interactive: false,
      });
      // Don't add by default; toggled via overlay control. But add so toggle works.
      pl.addTo(map);
      pl._climbId = `${slug}-${c.id}`;
      state.climbPolylines.push(pl);
    }
  }
}

/* ---------- overlay toggles ---------- */

function setOverlayVisibility() {
  for (const m of state.aidMarkers) {
    if (state.show.aid) m.addTo(map); else map.removeLayer(m);
  }
  for (const m of state.needsMarkers) {
    if (state.show.needs) m.addTo(map); else map.removeLayer(m);
  }
  for (const p of state.climbPolylines) {
    if (state.show.climbs) p.addTo(map); else map.removeLayer(p);
  }
}

/* ---------- summary table ---------- */

function renderSummary() {
  const t = document.getElementById("summary-table");
  t.replaceChildren();
  const fmt = (n, d = 1) => (n == null ? "—" : Number(n).toFixed(d));

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["", "Dist", "Elev +/-", "Alt range"].forEach((label) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  t.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const slug of DISCIPLINES) {
    const c = state.courses[slug];
    if (!c) continue;
    const s = c.summary;
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    const sw = document.createElement("span");
    sw.className = `swatch ${slug}`;
    tdName.appendChild(sw);
    tdName.appendChild(document.createTextNode(` ${slug}`));
    tr.appendChild(tdName);

    [
      `${fmt(s.distance_km, 2)} km`,
      `+${fmt(s.elev_gain_m, 0)} / -${fmt(s.elev_loss_m, 0)} m`,
      `${fmt(s.min_alt_m, 1)} – ${fmt(s.max_alt_m, 1)} m`,
    ].forEach((txt) => {
      const td = document.createElement("td");
      td.textContent = txt;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
  t.appendChild(tbody);
}

/* ---------- smoothing ---------- */

function smoothedAltitudes(slug, windowPts) {
  const cacheKey = `${slug}:${windowPts}`;
  if (state.smoothCache[cacheKey]) return state.smoothCache[cacheKey];
  const pts = state.courses[slug].points;
  const out = new Array(pts.length);
  const w = Math.max(1, Math.floor(windowPts));
  if (w <= 1) {
    for (let i = 0; i < pts.length; i++) out[i] = pts[i].alt;
  } else {
    // Symmetric moving average over (2w+1) points
    for (let i = 0; i < pts.length; i++) {
      const lo = Math.max(0, i - w);
      const hi = Math.min(pts.length - 1, i + w);
      let s = 0, n = 0;
      for (let j = lo; j <= hi; j++) { s += pts[j].alt ?? 0; n++; }
      out[i] = n ? s / n : pts[i].alt;
    }
  }
  state.smoothCache[cacheKey] = out;
  return out;
}

function approxWindowMeters(slug, windowPts) {
  // Estimate average inter-point spacing
  const pts = state.courses[slug].points;
  if (pts.length < 2) return 0;
  const totalDist = pts[pts.length - 1].dist_m;
  const avgSpacing = totalDist / (pts.length - 1);
  return Math.round(avgSpacing * (2 * windowPts + 1));
}

/* ---------- elevation profile ---------- */

function drawProfile() {
  const canvas = document.getElementById("profile");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const slug = state.scrubDiscipline;
  const course = state.courses[slug];
  if (!course) return;

  const pts = course.points;
  const padL = 36, padR = 8, padT = 8, padB = 22;
  const w = cssW - padL - padR;
  const h = cssH - padT - padB;
  const totalDist = course.summary.distance_m;

  const smooth = smoothedAltitudes(slug, state.smoothWindowPts);
  let minAlt = Infinity, maxAlt = -Infinity;
  for (const a of smooth) { if (a < minAlt) minAlt = a; if (a > maxAlt) maxAlt = a; }
  const altRange = Math.max(maxAlt - minAlt, 1);

  // Climb bands
  if (state.show.climbs) {
    const climbs = state.climbs[slug] || [];
    for (const c of climbs) {
      const x1 = padL + (pts[c.start_idx].dist_m / totalDist) * w;
      const x2 = padL + (pts[c.end_idx].dist_m / totalDist) * w;
      ctx.fillStyle =
        c.id === state.activeClimbHighlight ? "rgba(251,191,36,0.35)" : "rgba(251,191,36,0.13)";
      ctx.fillRect(x1, padT, Math.max(1, x2 - x1), h);
    }
  }

  // Axes
  ctx.strokeStyle = "rgba(255,255,255,0.15)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padL, padT); ctx.lineTo(padL, padT + h);
  ctx.lineTo(padL + w, padT + h);
  ctx.stroke();

  // Filled profile
  ctx.beginPath();
  ctx.moveTo(padL, padT + h);
  pts.forEach((p, i) => {
    const x = padL + (p.dist_m / totalDist) * w;
    const y = padT + h - ((smooth[i] - minAlt) / altRange) * h;
    ctx.lineTo(x, y);
  });
  ctx.lineTo(padL + w, padT + h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, padT, 0, padT + h);
  grad.addColorStop(0, COLORS[slug] + "cc");
  grad.addColorStop(1, COLORS[slug] + "11");
  ctx.fillStyle = grad;
  ctx.fill();

  // Outline
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = padL + (p.dist_m / totalDist) * w;
    const y = padT + h - ((smooth[i] - minAlt) / altRange) * h;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = COLORS[slug];
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Labels
  ctx.fillStyle = "rgba(230,236,245,0.7)";
  ctx.font = "10px -apple-system, sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(`${maxAlt.toFixed(0)}m`, padL - 4, padT + 8);
  ctx.fillText(`${minAlt.toFixed(0)}m`, padL - 4, padT + h);
  ctx.textAlign = "center";
  ctx.fillText("0", padL, padT + h + 12);
  ctx.fillText(`${(totalDist / 1000).toFixed(1)} km`, padL + w, padT + h + 12);

  drawScrubIndicator(smooth, minAlt, altRange);
}

function drawScrubIndicator(smooth, minAlt, altRange) {
  const slug = state.scrubDiscipline;
  const course = state.courses[slug];
  if (!course) return;
  const canvas = document.getElementById("profile");
  const ctx = canvas.getContext("2d");
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  const padL = 36, padR = 8, padT = 8, padB = 22;
  const w = cssW - padL - padR;
  const h = cssH - padT - padB;

  const idx = currentScrubIndex();
  const p = course.points[idx];
  if (!p) return;
  const x = padL + (p.dist_m / course.summary.distance_m) * w;
  const y = padT + h - ((smooth[idx] - minAlt) / altRange) * h;

  ctx.strokeStyle = "rgba(255,255,255,0.4)";
  ctx.setLineDash([2, 3]);
  ctx.beginPath();
  ctx.moveTo(x, padT); ctx.lineTo(x, padT + h);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = "#fff";
  ctx.strokeStyle = COLORS[slug];
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
}

/* ---------- scrubber ---------- */

function currentScrubIndex() {
  const slug = state.scrubDiscipline;
  const course = state.courses[slug];
  if (!course) return 0;
  const range = document.getElementById("scrub-range");
  const frac = Number(range.value) / Number(range.max);
  return Math.max(0, Math.min(course.points.length - 1, Math.floor(frac * (course.points.length - 1))));
}

function updateScrub() {
  const slug = state.scrubDiscipline;
  const course = state.courses[slug];
  if (!course) return;
  const p = course.points[currentScrubIndex()];

  document.getElementById("r-dist").textContent = `${(p.dist_m / 1000).toFixed(2)} km`;
  document.getElementById("r-alt").textContent = p.alt == null ? "— m" : `${p.alt.toFixed(1)} m`;
  document.getElementById("r-grade").textContent = `${p.grade_pct.toFixed(1)} %`;
  document.getElementById("r-bearing").textContent = `${p.bearing_deg.toFixed(0)}°`;

  if (state.scrubMarker) map.removeLayer(state.scrubMarker);
  state.scrubMarker = L.circleMarker([p.lat, p.lng], {
    radius: 7, color: "#fff", weight: 2,
    fillColor: COLORS[slug], fillOpacity: 1,
  }).addTo(map);

  drawProfile();
}

/* ---------- climbs sidebar ---------- */

function renderClimbs() {
  const slug = state.scrubDiscipline;
  const climbs = state.climbs[slug] || [];
  const root = document.getElementById("climbs-list");
  root.replaceChildren();

  document.getElementById("climbs-title").textContent =
    `Notable Climbs — ${slug} (${climbs.length})`;

  if (climbs.length === 0) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No notable climbs detected (flat profile).";
    root.appendChild(p);
    return;
  }

  for (const c of climbs) {
    const card = document.createElement("div");
    card.className = "climb-card";
    card.dataset.climbId = c.id;

    const head = document.createElement("div");
    head.className = "climb-head";
    const tag = document.createElement("span");
    tag.className = `climb-cat cat-${c.category}`;
    tag.textContent = c.category === "—" ? "—" : `Cat ${c.category}`;
    const title = document.createElement("span");
    title.className = "climb-title";
    title.textContent = `#${c.id}  km ${c.start_km.toFixed(1)} → ${c.end_km.toFixed(1)}`;
    head.appendChild(tag);
    head.appendChild(title);

    const stats = document.createElement("div");
    stats.className = "climb-stats";
    const fmts = [
      ["Length", `${c.length_m.toFixed(0)} m`],
      ["Gain",   `${c.elev_gain_m.toFixed(0)} m`],
      ["Avg",    `${c.avg_grade_pct.toFixed(1)}%`],
      ["Max",    `${c.max_grade_pct.toFixed(1)}%`],
    ];
    for (const [label, val] of fmts) {
      const cell = document.createElement("div");
      const dt = document.createElement("div");
      dt.className = "stat-label";
      dt.textContent = label;
      const dd = document.createElement("div");
      dd.className = "stat-value";
      dd.textContent = val;
      cell.appendChild(dt);
      cell.appendChild(dd);
      stats.appendChild(cell);
    }

    card.appendChild(head);
    card.appendChild(stats);

    if (c.shape_label) {
      const shape = document.createElement("div");
      shape.className = "climb-shape";
      shape.textContent = c.shape_label;
      card.appendChild(shape);
    }
    if (c.gain_thirds_pct) {
      const bar = document.createElement("div");
      bar.className = "gain-bar";
      bar.title = `Gain by thirds: ${c.gain_thirds_pct[0]}% / ${c.gain_thirds_pct[1]}% / ${c.gain_thirds_pct[2]}%`;
      const labels = ["start", "middle", "top"];
      c.gain_thirds_pct.forEach((pct, i) => {
        const seg = document.createElement("div");
        seg.className = "gain-bar-seg";
        seg.style.flexBasis = `${pct}%`;
        seg.title = `${labels[i]}: ${pct}% of gain`;
        bar.appendChild(seg);
      });
      card.appendChild(bar);
    }

    card.addEventListener("click", () => focusClimb(c));
    root.appendChild(card);
  }
}

function focusClimb(c) {
  state.activeClimbHighlight = c.id;
  const slug = state.scrubDiscipline;
  const points = state.courses[slug].points;
  const slice = points.slice(c.start_idx, c.end_idx + 1).map((p) => [p.lat, p.lng]);
  if (slice.length === 0) return;
  const bounds = L.latLngBounds(slice);
  map.fitBounds(bounds, { padding: [80, 80], maxZoom: 16 });

  // Move the scrubber to the start of this climb
  const range = document.getElementById("scrub-range");
  const frac = c.start_idx / (points.length - 1);
  range.value = Math.round(frac * Number(range.max));
  updateScrub();

  // Visual highlight on the card
  document.querySelectorAll(".climb-card").forEach((el) => {
    el.classList.toggle("active", Number(el.dataset.climbId) === c.id);
  });
}

/* ---------- weather snapshot ---------- */

function renderWeather() {
  if (!state.weatherSeed) return;
  const w = state.weatherSeed;
  const root = document.getElementById("weather-readout");
  if (!root) return;
  const rows = [
    ["Wind (median)", `${w.wind_speed_kmh_median.toFixed(1)} km/h from ${w.wind_direction_modal_cardinal} (vector ${w.wind_direction_vector_mean_deg.toFixed(0)}°)`],
    ["Peak gusts",     `${w.wind_gust_kmh_median_of_max.toFixed(0)} km/h median-of-max`],
    ["Air temp",       `${w.temp_c_low_median.toFixed(0)} – ${w.temp_c_high_median.toFixed(0)} °C  (race-window mean ${w.temp_c_mean_median.toFixed(1)} °C)`],
    ["Humidity",       `${w.humidity_pct_mean_median.toFixed(0)} %`],
    ["Rain",           `${w.precip_mm_total_median.toFixed(2)} mm median`],
    ["IM published",   `air ${w.im_published.air_temp_low_c}–${w.im_published.air_temp_high_c} °C · water ${w.im_published.water_temp_avg_c} °C`],
  ];
  root.replaceChildren();
  for (const [label, val] of rows) {
    const row = document.createElement("div");
    row.className = "weather-row";
    const lbl = document.createElement("span");
    lbl.className = "weather-label";
    lbl.textContent = label;
    const v = document.createElement("span");
    v.className = "weather-value";
    v.textContent = val;
    row.appendChild(lbl);
    row.appendChild(v);
    root.appendChild(row);
  }
}

/* ---------- simulator results ---------- */

function renderSimulator() {
  const root = document.getElementById("sim-results");
  if (!root) return;
  root.replaceChildren();
  const sim = state.simResults;
  if (!sim) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Run `python3 scripts/physics_sim.py` to populate simulator results.";
    root.appendChild(p);
    return;
  }

  const wx = document.createElement("div");
  wx.className = "sim-wx";
  const wi = sim.weather_inputs;
  const windKmh = wi.wind_speed_kmh_10m ?? wi.wind_speed_kmh ?? 0;
  wx.textContent =
    `Inputs: wind ${windKmh.toFixed(1)} km/h from ` +
    `${Math.round(wi.wind_from_deg)}°  ·  ` +
    `${wi.temp_c.toFixed(1)}°C  ·  ${Math.round(wi.humidity_pct)}% RH`;
  root.appendChild(wx);

  for (const [key, s] of Object.entries(sim.scenarios)) {
    const card = document.createElement("div");
    card.className = "sim-card";

    const head = document.createElement("div");
    head.className = "sim-head";
    const label = document.createElement("span");
    label.className = "sim-label";
    label.textContent = s.label;
    const time = document.createElement("span");
    time.className = "sim-time";
    time.textContent = s.total_time_hms;
    head.appendChild(label);
    head.appendChild(time);

    const detail = document.createElement("div");
    detail.className = "sim-detail";
    detail.textContent = `${s.power_w}W  ·  CdA ${s.cda}  ·  avg ${s.avg_speed_kmh.toFixed(1)} km/h`;

    card.appendChild(head);
    card.appendChild(detail);
    root.appendChild(card);
  }

  if (sim.inverse_targets) {
    const inv = document.createElement("div");
    inv.className = "sim-inverse";
    const title = document.createElement("div");
    title.className = "sim-inverse-title";
    title.textContent = "NP required to hit split (median conditions)";
    inv.appendChild(title);
    for (const [k, v] of Object.entries(sim.inverse_targets)) {
      const row = document.createElement("div");
      row.className = "sim-inverse-row";
      const lbl = document.createElement("span");
      lbl.textContent = k;
      const val = document.createElement("span");
      val.textContent = `${v.toFixed(0)} W`;
      row.appendChild(lbl);
      row.appendChild(val);
      inv.appendChild(row);
    }
    root.appendChild(inv);
  }
}

/* ---------- full-day race projection ---------- */

// Friendly labels + ordering for race_projection.json scenario keys.
const RACE_SCENARIOS = [
  ["best", "Best case (current fitness)"],
  ["target", "Target"],
  ["stretch_sub3run", "Stretch — sub-3 run"],
  ["conservative", "Conservative"],
];

function renderRaceDay() {
  const root = document.getElementById("raceday-results");
  if (!root) return;
  const race = state.raceProjection;
  if (!race || !race.scenarios) {
    root.textContent = "Race projection unavailable (run scripts/physics_sim.py + analyze_run.py).";
    return;
  }

  // Precompute each scenario's non-swim seconds (T1 + bike + T2 + run) once,
  // so the swim slider can recompute totals live.
  const rows = [];
  for (const [key, label] of RACE_SCENARIOS) {
    const s = race.scenarios[key];
    if (!s) continue;
    const nonSwim = hmsToSec(s.total) - hmsToSec(s.swim);
    rows.push({ key, label, s, nonSwim });
  }
  state.raceRows = rows;

  root.replaceChildren();
  for (const r of rows) {
    const card = document.createElement("div");
    card.className = "sim-card";
    card.dataset.key = r.key;

    const head = document.createElement("div");
    head.className = "sim-head";
    const lab = document.createElement("span");
    lab.className = "sim-label";
    lab.textContent = r.label;
    const time = document.createElement("span");
    time.className = "sim-time race-total";
    head.append(lab, time);

    const detail = document.createElement("div");
    detail.className = "sim-detail race-detail";

    card.append(head, detail);
    root.appendChild(card);
  }

  // 2025 actual reference row
  if (race["2025_actual"]) {
    const a = race["2025_actual"];
    const ref = document.createElement("div");
    ref.className = "sim-inverse";
    const title = document.createElement("div");
    title.className = "sim-inverse-title";
    title.textContent = "2025 actual (reference)";
    const row = document.createElement("div");
    row.className = "sim-inverse-row";
    const lbl = document.createElement("span");
    lbl.textContent = "Total — bike was draft-aided";
    const val = document.createElement("span");
    val.textContent = a.total;
    row.append(lbl, val);
    ref.append(title, row);
    root.appendChild(ref);
  }

  updateRaceDay();
}

function updateRaceDay() {
  const rows = state.raceRows;
  if (!rows) return;
  const swimSec = Number(document.getElementById("swim-range").value);
  document.getElementById("swim-readout").textContent = secToHms(swimSec);
  for (const r of rows) {
    const card = document.querySelector(`#raceday-results .sim-card[data-key="${r.key}"]`);
    if (!card) continue;
    const total = swimSec + r.nonSwim;
    card.querySelector(".race-total").textContent = secToHms(total);
    card.querySelector(".race-detail").textContent =
      `swim ${secToHms(swimSec)} · bike ${r.s.bike} · run ${r.s.run}`;
  }
}

function setupSwimSlider() {
  const el = document.getElementById("swim-range");
  if (el) el.addEventListener("input", updateRaceDay);
}

/* ---------- event wiring ---------- */

function setupToggles() {
  document.querySelectorAll(".discipline-toggles input").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      const slug = e.target.dataset.discipline;
      const line = state.polylines[slug];
      if (!line) return;
      if (e.target.checked) line.addTo(map);
      else map.removeLayer(line);
    });
  });

  document.getElementById("show-aid").addEventListener("change", (e) => {
    state.show.aid = e.target.checked;
    setOverlayVisibility();
  });
  document.getElementById("show-needs").addEventListener("change", (e) => {
    state.show.needs = e.target.checked;
    setOverlayVisibility();
  });
  document.getElementById("show-climbs").addEventListener("change", (e) => {
    state.show.climbs = e.target.checked;
    setOverlayVisibility();
    drawProfile();
  });
}

function setupScrubber() {
  document.getElementById("scrub-discipline").addEventListener("change", (e) => {
    state.scrubDiscipline = e.target.value;
    state.activeClimbHighlight = null;
    document.getElementById("profile-title").textContent =
      `Elevation Profile — ${state.scrubDiscipline}`;
    renderClimbs();
    updateScrub();
  });
  document.getElementById("scrub-range").addEventListener("input", updateScrub);
}

function setupSmoothing() {
  const range = document.getElementById("smooth-range");
  const readout = document.getElementById("smooth-readout");
  const update = () => {
    state.smoothWindowPts = Number(range.value);
    state.smoothCache = {};  // invalidate cache
    const meters = approxWindowMeters(state.scrubDiscipline, state.smoothWindowPts);
    readout.textContent = state.smoothWindowPts === 0 ? "raw (no smoothing)" : `~${meters}m window`;
    drawProfile();
  };
  range.addEventListener("input", update);
  update();
}

function fitToAll() {
  const lines = Object.values(state.polylines);
  if (lines.length === 0) return;
  const group = L.featureGroup(lines);
  map.fitBounds(group.getBounds(), { padding: [30, 30] });
}

/* ---------- boot ---------- */

(async function init() {
  try {
    await loadAll();
    DISCIPLINES.forEach(drawCourse);
    drawAidStations();
    drawClimbsOnMap();
    setOverlayVisibility();

    renderSummary();
    fitToAll();
    setupToggles();
    setupScrubber();
    setupSmoothing();
    renderWeather();
    renderSimulator();
    renderRaceDay();
    setupSwimSlider();
    document.getElementById("profile-title").textContent =
      `Elevation Profile — ${state.scrubDiscipline}`;
    renderClimbs();
    updateScrub();
  } catch (e) {
    console.error(e);
    const mapEl = document.getElementById("map");
    mapEl.replaceChildren();
    const box = document.createElement("div");
    box.style.cssText = "padding:20px;color:#f88;font-family:sans-serif;line-height:1.5";
    const line1 = document.createElement("div");
    line1.textContent = `Failed to load course data: ${e.message}`;
    const line2 = document.createElement("div");
    line2.textContent =
      "Make sure you're serving via a local server (file:// won't fetch JSON). " +
      "Run `python3 -m http.server 8765` from the project root, then visit " +
      "http://localhost:8765/web/";
    box.append(line1, line2);
    mapEl.appendChild(box);
  }
})();
