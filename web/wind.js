/* Wind visualization: animated particles + per-segment wind-impact coloring.
 * Depends on physics.js and Mapbox GL JS.
 */

class WindParticles {
  constructor(map, _legacyParentEl, opts = {}) {
    this.map = map;
    this.canvas = document.createElement("canvas");
    this.canvas.id = "wind-canvas";
    this.canvas.style.cssText =
      "position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2;";
    // Mount inside Mapbox's canvas container — its size is authoritative
    // and stays in sync through zoom, pitch, DPR changes, and pinch.
    map.getCanvasContainer().appendChild(this.canvas);
    this.ctx = this.canvas.getContext("2d");

    this.windSpeedKmh = 0;
    this.windFromDeg = 140;
    this.enabled = true;
    this.particles = [];
    this.lastTime = performance.now();
    this.cssW = 0;
    this.cssH = 0;
    this.dpr = window.devicePixelRatio || 1;

    this.resize = this.resize.bind(this);
    this.tick = this.tick.bind(this);

    // Resize on Mapbox's own resize event (covers DPR + container reflow).
    map.on("resize", this.resize);
    // Defensive: also watch for DPR changes from OS/browser zoom or monitor switch.
    if (window.matchMedia) {
      this._dprMql = window.matchMedia(`(resolution: ${this.dpr}dppx)`);
      try {
        this._dprMql.addEventListener("change", this.resize);
      } catch {
        // Older Safari: addListener is the legacy API
        this._dprMql.addListener && this._dprMql.addListener(this.resize);
      }
    }
    this.resize();
    requestAnimationFrame(this.tick);
  }

  resize() {
    // Authoritative source: Mapbox's own canvas. Avoids the layout-race that
    // briefly produces zero-sized parent rects during zoom transitions.
    const mapCanvas = this.map.getCanvas();
    const rect = mapCanvas.getBoundingClientRect();
    // Bail if we'd produce a degenerate canvas (size 0). Keep prior good dims.
    if (rect.width < 1 || rect.height < 1) return;
    const dpr = window.devicePixelRatio || 1;
    this.dpr = dpr;
    this.cssW = rect.width;
    this.cssH = rect.height;
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  set(windSpeedKmh, windFromDeg, enabled = true) {
    this.windSpeedKmh = windSpeedKmh;
    this.windFromDeg = windFromDeg;
    this.enabled = enabled;
    // Always keep a healthy baseline so low-wind days remain visually informative.
    // 60 at 0 km/h → ~460 at 50 km/h.
    const target = Math.min(460, Math.round(60 + windSpeedKmh * 8));
    while (this.particles.length < target) this.particles.push(this.spawn(true));
    while (this.particles.length > target) this.particles.pop();
  }

  spawn(random = false) {
    return {
      x: random ? Math.random() * this.cssW : -10,
      y: random ? Math.random() * this.cssH : -10,
      age: Math.random() * 1.2,
      maxAge: 2.0 + Math.random() * 2.5,
      thickness: 0.8 + Math.random() * 1.2,
      trail: 4 + Math.random() * 10,
    };
  }

  // Respawn routes by *cause*, not by random probability:
  //   - "aged" (lifespan expired): respawn anywhere visible. Keeps the canvas
  //      uniformly populated regardless of wind speed.
  //   - "exited" (drifted off canvas): respawn at the upwind edge. Preserves the
  //      "wind streaming across" feel at higher wind speeds, since fast-moving
  //      particles drift off-screen before they can age out.
  respawn(p, reason, dx, dy) {
    p.age = 0;
    p.maxAge = 2.0 + Math.random() * 2.5;
    p.thickness = 0.8 + Math.random() * 1.2;
    p.trail = 4 + Math.random() * 10;
    if (reason === "exited") {
      if (Math.abs(dx) > Math.abs(dy)) {
        p.x = dx > 0 ? -10 : this.cssW + 10;
        p.y = Math.random() * this.cssH;
      } else {
        p.y = dy > 0 ? -10 : this.cssH + 10;
        p.x = Math.random() * this.cssW;
      }
    } else {
      p.x = Math.random() * this.cssW;
      p.y = Math.random() * this.cssH;
    }
  }

  // Unit screen-direction vector for the current "wind to" direction, accounting
  // for both map bearing AND zoom level (the offset projects further at low zooms
  // so we always get a sufficient pixel delta to normalize cleanly).
  screenDirection() {
    const center = this.map.getCenter();
    const toDeg = (this.windFromDeg + 180) % 360;
    // Scale offset distance up at low zooms so projected delta is always ≥ many pixels.
    const zoom = this.map.getZoom();
    const distM = 200 * Math.pow(2, Math.max(0, 12 - zoom));
    const toRad = (toDeg * Math.PI) / 180;
    const latCos = Math.max(1e-6, Math.cos((center.lat * Math.PI) / 180));
    const dLat = (distM * Math.cos(toRad)) / 111000;
    const dLng = (distM * Math.sin(toRad)) / (111000 * latCos);
    const a = this.map.project(center);
    const b = this.map.project([center.lng + dLng, center.lat + dLat]);
    const sx = b.x - a.x;
    const sy = b.y - a.y;
    const len = Math.sqrt(sx * sx + sy * sy);
    if (len === 0) return { dx: 0, dy: 1 };
    return { dx: sx / len, dy: sy / len };
  }

  // Drift speed in px/sec — clamped to a small ambient floor so low-wind scenes
  // still look alive instead of frozen.
  driftSpeedPxPerSec() {
    return Math.max(8, this.windSpeedKmh * 5);
  }

  tick(now) {
    const dt = Math.min(0.05, (now - this.lastTime) / 1000);
    this.lastTime = now;
    // Cheap polling fallback: if DPR changed without firing matchMedia, fix it.
    if ((window.devicePixelRatio || 1) !== this.dpr) this.resize();
    if (this.cssW < 1 || this.cssH < 1) {
      // Canvas is degenerate; try once to recover from a transient bad layout.
      this.resize();
      requestAnimationFrame(this.tick);
      return;
    }
    const ctx = this.ctx;
    // Fade for comet-trail
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = "rgba(0,0,0,0.18)";
    ctx.fillRect(0, 0, this.cssW, this.cssH);
    ctx.globalCompositeOperation = "source-over";

    if (this.enabled) {
      const { dx, dy } = this.screenDirection();
      const speed = this.driftSpeedPxPerSec();
      const opacity = 0.35 + Math.min(0.45, this.windSpeedKmh * 0.02);

      ctx.strokeStyle = `rgba(170,210,255,${opacity.toFixed(2)})`;
      ctx.lineCap = "round";

      for (const p of this.particles) {
        p.x += dx * speed * dt;
        p.y += dy * speed * dt;
        p.age += dt;
        const exited =
          p.x < -20 || p.x > this.cssW + 20 ||
          p.y < -20 || p.y > this.cssH + 20;
        if (exited) {
          this.respawn(p, "exited", dx, dy);
        } else if (p.age > p.maxAge) {
          this.respawn(p, "aged", dx, dy);
        }
        ctx.lineWidth = p.thickness;
        // Trail length proportional to actual movement speed, clamped
        const trail = Math.max(2, Math.min(20, p.trail * (speed / 60)));
        ctx.beginPath();
        ctx.moveTo(p.x - dx * trail, p.y - dy * trail);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
      }
    }
    requestAnimationFrame(this.tick);
  }
}

/* ---------- Per-segment wind-impact route colorization ---------- */

function buildWindImpactGradient(points, simWind, simCalm) {
  const total = points[points.length - 1].dist_m;
  if (total <= 0) return null;
  const N = points.length;
  const STOPS = 200;
  const stride = Math.max(1, Math.floor(N / STOPS));
  const stops = [];
  for (let i = 1; i < N; i += stride) {
    const progress = points[i].dist_m / total;
    const vWind = simWind.segSpeeds[i];
    const vCalm = simCalm.segSpeeds[i];
    if (vWind === 0 || vCalm === 0) continue;
    const delta = (vWind - vCalm) / vCalm;
    stops.push([Math.min(1, Math.max(0, progress)), windDeltaToColor(delta)]);
  }
  return finalizeGradientStops(stops);
}

// Mapbox `interpolate` requires STRICTLY ascending progress values.
// Dedupe colocated stops (a duplicate first/last point in the TCX would otherwise
// produce two stops at progress=0, which Mapbox silently rejects via try/catch).
function finalizeGradientStops(stops) {
  if (stops.length === 0) return null;
  if (stops[0][0] > 0) stops.unshift([0, stops[0][1]]);
  if (stops[stops.length - 1][0] < 1) stops.push([1, stops[stops.length - 1][1]]);
  const EPS = 1e-6;
  const dedup = [stops[0]];
  for (let i = 1; i < stops.length; i++) {
    if (stops[i][0] > dedup[dedup.length - 1][0] + EPS) dedup.push(stops[i]);
  }
  if (dedup.length < 2) return null;
  const expr = ["interpolate", ["linear"], ["line-progress"]];
  for (const [p, c] of dedup) expr.push(p, c);
  return expr;
}

function windDeltaToColor(delta) {
  // delta in [-0.25, +0.25] mapped to red→gray→blue
  const clamped = Math.max(-0.25, Math.min(0.25, delta));
  const t = (clamped + 0.25) / 0.5;  // 0..1
  // Interpolate red → gray → blue through HSL or just RGB.
  // Use a 3-color ramp:
  const stops = [
    [0.0,  [239, 68, 68]],   // red (worst headwind)
    [0.5,  [120, 120, 130]], // neutral
    [1.0,  [56, 189, 248]],  // blue (best tailwind)
  ];
  let lo = stops[0], hi = stops[stops.length - 1];
  for (let i = 0; i < stops.length - 1; i++) {
    if (t >= stops[i][0] && t <= stops[i + 1][0]) { lo = stops[i]; hi = stops[i + 1]; break; }
  }
  const span = hi[0] - lo[0] || 1;
  const f = (t - lo[0]) / span;
  const rgb = lo[1].map((c, k) => Math.round(c + (hi[1][k] - c) * f));
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

/* ---------- Compass dial widget ---------- */

function drawCompass(canvas, fromDeg, speedKmh) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const size = 96;
  canvas.width = size * dpr;
  canvas.height = size * dpr;
  canvas.style.width = size + "px";
  canvas.style.height = size + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);
  const cx = size / 2, cy = size / 2, r = size / 2 - 4;

  // Ring
  ctx.strokeStyle = "rgba(255,255,255,0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.stroke();

  // Cardinal labels
  ctx.fillStyle = "rgba(230,236,245,0.6)";
  ctx.font = "10px -apple-system, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("N", cx, cy - r + 6);
  ctx.fillText("S", cx, cy + r - 6);
  ctx.fillText("E", cx + r - 6, cy);
  ctx.fillText("W", cx - r + 6, cy);

  // Wind arrow: shows wind direction as "blowing TOWARD"
  const toDeg = (fromDeg + 180) % 360;
  const a = ((toDeg - 90) * Math.PI) / 180;
  const aLen = r - 12;
  const tipX = cx + Math.cos(a) * aLen;
  const tipY = cy + Math.sin(a) * aLen;
  const tailX = cx - Math.cos(a) * (aLen - 8);
  const tailY = cy - Math.sin(a) * (aLen - 8);

  const speedIntensity = Math.min(1, speedKmh / 30);
  const arrowColor = `rgba(56, 189, 248, ${0.5 + speedIntensity * 0.5})`;
  ctx.strokeStyle = arrowColor;
  ctx.fillStyle = arrowColor;
  ctx.lineWidth = 2 + speedIntensity * 2;
  ctx.lineCap = "round";

  ctx.beginPath();
  ctx.moveTo(tailX, tailY);
  ctx.lineTo(tipX, tipY);
  ctx.stroke();

  // Arrow head
  const headLen = 9;
  const headAngle = 0.5;
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(
    tipX - headLen * Math.cos(a - headAngle),
    tipY - headLen * Math.sin(a - headAngle)
  );
  ctx.lineTo(
    tipX - headLen * Math.cos(a + headAngle),
    tipY - headLen * Math.sin(a + headAngle)
  );
  ctx.closePath();
  ctx.fill();

  // Center marker
  ctx.fillStyle = "rgba(230,236,245,0.7)";
  ctx.beginPath();
  ctx.arc(cx, cy, 2, 0, Math.PI * 2);
  ctx.fill();

  // From-cardinal text
  ctx.fillStyle = "rgba(230,236,245,0.85)";
  ctx.font = "600 11px -apple-system, sans-serif";
  ctx.fillText(`${Math.round(fromDeg)}°`, cx, cy + 22);
}

/* ---------- Smoothed grade per point (rolling window over altitude) ---------- */

function smoothedGrades(points, windowPts = 12) {
  const N = points.length;
  const smoothAlt = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const lo = Math.max(0, i - windowPts);
    const hi = Math.min(N - 1, i + windowPts);
    let sum = 0, count = 0;
    for (let j = lo; j <= hi; j++) {
      if (points[j].alt != null) { sum += points[j].alt; count++; }
    }
    smoothAlt[i] = count ? sum / count : 0;
  }
  const grades = new Float32Array(N);
  for (let i = 1; i < N; i++) {
    const segD = points[i].dist_m - points[i - 1].dist_m;
    if (segD > 1) grades[i] = (100 * (smoothAlt[i] - smoothAlt[i - 1])) / segD;
  }
  return grades;
}

/* ---------- Climbs (grade-only) gradient ---------- */
// Color ramp: ≤0% green → 2% yellow → 4% orange → 7% red → ≥10% dark purple.

const CLIMB_STOPS = [
  [-1, [16, 185, 129]],   // strong descent — light green (still easy)
  [0,  [16, 185, 129]],   // flat — green
  [2,  [251, 191, 36]],   // mild climb — yellow
  [4,  [249, 115, 22]],   // moderate — orange
  [7,  [239, 68, 68]],    // hard — red
  [12, [124, 58, 237]],   // very hard — purple
];

function gradeToColor(gradePct) {
  const g = Math.max(-1, Math.min(15, gradePct));
  let lo = CLIMB_STOPS[0], hi = CLIMB_STOPS[CLIMB_STOPS.length - 1];
  for (let i = 0; i < CLIMB_STOPS.length - 1; i++) {
    if (g >= CLIMB_STOPS[i][0] && g <= CLIMB_STOPS[i + 1][0]) {
      lo = CLIMB_STOPS[i]; hi = CLIMB_STOPS[i + 1]; break;
    }
  }
  const span = hi[0] - lo[0] || 1;
  const t = (g - lo[0]) / span;
  const rgb = lo[1].map((c, k) => Math.round(c + (hi[1][k] - c) * t));
  return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;
}

function buildClimbsGradient(points) {
  const grades = smoothedGrades(points, 12);
  const total = points[points.length - 1].dist_m;
  if (total <= 0) return null;
  const STOPS = 240;
  const stride = Math.max(1, Math.floor(points.length / STOPS));
  const stops = [];
  for (let i = 1; i < points.length; i += stride) {
    const progress = points[i].dist_m / total;
    stops.push([Math.min(1, Math.max(0, progress)), gradeToColor(grades[i])]);
  }
  return finalizeGradientStops(stops);
}

/* ---------- Climbs + Wind (effective grade) gradient ---------- */
// Effective grade = actual grade + (wind force / weight) × 100, where wind force
// is the headwind/tailwind contribution to aero drag at a reference speed.
// Headwind raises effective grade; tailwind lowers it (can even flatten a climb).

function buildClimbsWindGradient(points, params) {
  const {
    windSpeedKmh, windFromDeg,
    massKg = 75, cda = 0.25,
    tempC = 23, humidityPct = 66,
    refSpeedMs = 11.5,        // ~41 km/h, typical bike-leg pace
  } = params;
  const grades = smoothedGrades(points, 12);
  const rho = window.Physics.airDensity(tempC, 1013.25, humidityPct);
  const G = 9.80665;
  const f_aero_calm = 0.5 * rho * cda * refSpeedMs * refSpeedMs;

  const total = points[points.length - 1].dist_m;
  if (total <= 0) return null;
  const STOPS = 240;
  const stride = Math.max(1, Math.floor(points.length / STOPS));
  const stops = [];
  for (let i = 1; i < points.length; i += stride) {
    const progress = points[i].dist_m / total;
    const grade = grades[i];
    const bearing = points[i].bearing_deg || 0;
    const windAlong = window.Physics.windAlongHeading(windSpeedKmh, windFromDeg, bearing);
    const vRel = refSpeedMs - windAlong;
    const f_aero = 0.5 * rho * cda * vRel * Math.abs(vRel);
    const f_wind_delta = f_aero - f_aero_calm;
    const equivGradePct = (f_wind_delta / (massKg * G)) * 100;
    const effectiveGrade = grade + equivGradePct;
    stops.push([Math.min(1, Math.max(0, progress)), gradeToColor(effectiveGrade)]);
  }
  return finalizeGradientStops(stops);
}

window.Wind = {
  WindParticles,
  buildWindImpactGradient,
  buildClimbsGradient,
  buildClimbsWindGradient,
  windDeltaToColor,
  gradeToColor,
  smoothedGrades,
  drawCompass,
  CLIMB_STOPS,
};
