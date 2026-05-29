/* Cycling physics simulator v2.
 *
 * Refinements over v1:
 *   - Wind gradient correction (Hellmann power law, 10m → ~1.2m rider height).
 *     Open-Meteo gives 10m anemometer values; without this, we over-count wind
 *     impact by ~22% over coastal terrain.
 *   - Yaw-dependent CdA via a generic high-end TT/disc/tri-spoke quadratic curve.
 *     Based on published Princeton CarbonWorks Mach TSV2 yaw-sweep behavior and
 *     industry-typical disc-rear / deep-front yaw profiles.
 *   - Aero / sitting-up posture switching. Below an athlete-specific threshold
 *     (default 19.3 km/h = 12 mph for Sam), the rider sits up — CdA jumps by
 *     a configurable multiplier (default 1.6×, per published 0.28→0.45 data).
 *   - Slow corner speed caps. Per-point max safe speed from bearing-change rate;
 *     riders can't pull race speed through sharp corners. Applies a friction-
 *     limited circular-motion cap with a realistic minimum (default 4 m/s).
 *
 * Source citations live in README.md (see "Physics model" and "Lessons learned").
 *
 * Conventions:
 *   - windFromDeg is meteorological: degrees the wind is coming FROM.
 *   - bearingDeg is the direction the rider is going TOWARD.
 *   - All distances meters, speeds m/s internally, kmh at API boundaries.
 */

const G = 9.80665;

// Hellmann power law: factor to convert 10m wind speed to rider-height (~1.2m).
// Exponent α = 0.11 for open coastal terrain (Cairns).
//   v_rider = v_10m * (1.2 / 10)^0.11  ≈ v_10m * 0.7766
const DEFAULT_WIND_HEIGHT_FACTOR = Math.pow(1.2 / 10, 0.11); // ≈ 0.7766

// Below this speed, the rider sits up (out of aero). Sam = 12 mph = 19.31 km/h.
const DEFAULT_AERO_THRESHOLD_KMH = 19.31;

// Sitting-up CdA multiplier (61% increase per published 0.28→0.45 data).
const DEFAULT_SITTING_CDA_MULT = 1.6;

// Yaw-dependent CdA curve: factor = 1 + a*yaw² - b*|yaw|. Yaw in degrees.
// Gives a slight sail-effect dip at low yaw, increasing past ~10°. Calibrated
// against typical TT bike + disc rear + tri-spoke front published curves
// (Princeton CarbonWorks, Specialized, Trek wind-tunnel public data).
const DEFAULT_YAW_CURVE_A = 0.00055;
const DEFAULT_YAW_CURVE_B = 0.005;
const YAW_CURVE_FLOOR = 0.92; // never reduce CdA below 92% of zero-yaw

// Cornering: max safe speed = sqrt(μ · g · r). μ = 0.45 is conservative for
// race conditions on dry tarmac (raw tire grip is ~0.7 but riders don't ride
// at the limit). Minimum corner speed = 4 m/s (= 14.4 km/h) — a rider doesn't
// fully stop for a U-turn but does scrub heavily.
const DEFAULT_CORNER_FRICTION_MU = 0.45;
const DEFAULT_CORNER_MIN_MS = 4.0;
const CORNER_LOOK_AHEAD_M = 30;

function airDensity(tempC, pressureHpa = 1013.25, humidityPct = 60) {
  const T = tempC + 273.15;
  const P = pressureHpa * 100;
  const PsatHpa = 6.1078 * Math.exp((17.27 * tempC) / (tempC + 237.3));
  const Pv = PsatHpa * 100 * (humidityPct / 100);
  const Pd = P - Pv;
  return Pd / (287.058 * T) + Pv / (461.495 * T);
}

// Decompose wind into along-heading (signed: + tailwind) and cross-heading.
// Both in m/s.
function windDecompose(windSpeedKmh, windFromDeg, headingDeg) {
  const windToDeg = (windFromDeg + 180) % 360;
  const angleRad = ((windToDeg - headingDeg) * Math.PI) / 180;
  const speedMs = windSpeedKmh / 3.6;
  return {
    along: speedMs * Math.cos(angleRad),
    cross: speedMs * Math.sin(angleRad),
  };
}

// Legacy single-component (still used in some places); kept for backward compat.
function windAlongHeading(windSpeedKmh, windFromDeg, headingDeg) {
  return windDecompose(windSpeedKmh, windFromDeg, headingDeg).along;
}

// Yaw angle at this rider velocity (m/s) given decomposed wind components.
// Returns degrees, magnitude only (CdA is symmetric in yaw sign).
function yawDeg(vRiderMs, vAlongMs, vCrossMs) {
  const headOnComponent = vRiderMs - vAlongMs;
  if (headOnComponent <= 1e-3 && Math.abs(vCrossMs) <= 1e-3) return 0;
  return Math.abs((Math.atan2(Math.abs(vCrossMs), headOnComponent) * 180) / Math.PI);
}

function cdaAtYaw(cdaZeroYaw, yawDegMag, a = DEFAULT_YAW_CURVE_A, b = DEFAULT_YAW_CURVE_B) {
  const y = Math.abs(yawDegMag);
  const factor = Math.max(YAW_CURVE_FLOOR, 1 + a * y * y - b * y);
  return cdaZeroYaw * factor;
}

// Solve for steady-state rider speed v (m/s) given pedal power and wind components.
// Includes crosswind contribution to apparent wind magnitude (not just along).
function solveSteadySpeed(pWheel, grade, massKg, crr, cda, rho, vAlongMs, vCrossMs = 0) {
  if (pWheel <= 0) return 0.5;
  const slope = Math.atan(grade);
  const fGrav = massKg * G * Math.sin(slope);
  const fRoll = massKg * G * Math.cos(slope) * crr;
  const vCrossSq = vCrossMs * vCrossMs;

  const f = (v) => {
    const vRel = v - vAlongMs;
    const vApp = Math.sqrt(vRel * vRel + vCrossSq);
    const fAero = 0.5 * rho * cda * vApp * vRel;
    return v * (fGrav + fRoll + fAero) - pWheel;
  };

  let lo = 0.05, hi = 50.0;
  if (f(hi) < 0) return hi;
  if (f(lo) > 0) return 0.05;
  for (let i = 0; i < 80; i++) {
    const mid = 0.5 * (lo + hi);
    if (f(mid) > 0) hi = mid;
    else lo = mid;
  }
  return 0.5 * (lo + hi);
}

// Precompute per-point cornering max speed by walking ahead and finding the
// tightest local curvature within CORNER_LOOK_AHEAD_M. Cached after first build.
function precomputeCornerMaxSpeeds(
  points,
  mu = DEFAULT_CORNER_FRICTION_MU,
  minMs = DEFAULT_CORNER_MIN_MS,
  lookAheadM = CORNER_LOOK_AHEAD_M,
) {
  const n = points.length;
  const out = new Float32Array(n).fill(Infinity);
  const gMu = mu * G;
  for (let i = 0; i < n - 1; i++) {
    let minRadius = Infinity;
    const startDist = points[i].dist_m ?? 0;
    for (let j = i + 1; j < n - 1; j++) {
      const d = (points[j].dist_m ?? 0) - startDist;
      if (d > lookAheadM) break;
      const b1 = points[j].bearing_deg ?? 0;
      const b2 = points[j + 1].bearing_deg ?? 0;
      const segD = (points[j + 1].dist_m ?? 0) - (points[j].dist_m ?? 0);
      if (segD < 1) continue;
      // Wrap-corrected absolute bearing change
      const bChange = Math.abs(((b2 - b1 + 540) % 360) - 180);
      if (bChange < 0.5) continue; // noise floor
      const localRadius = segD / ((bChange * Math.PI) / 180);
      if (localRadius < minRadius) minRadius = localRadius;
    }
    if (minRadius === Infinity) {
      out[i] = Infinity;
    } else {
      out[i] = Math.max(minMs, Math.sqrt(gMu * minRadius));
    }
  }
  return out;
}

// Solve a single segment: iterates aero/sitting state + yaw-dependent CdA
// until both converge. Applies cornering cap if present.
function solveSegment({
  pWheel, grade, massKg, crr, cdaZeroYaw, rho,
  vAlongMs, vCrossMs,
  sittingCdaMult, aeroThresholdMs,
  yawA, yawB,
  cornerMaxMs,
}) {
  // Initial guess: aero, mid-pace
  let v = 12.0;
  let aero = true;
  for (let iter = 0; iter < 4; iter++) {
    const yaw = yawDeg(v, vAlongMs, vCrossMs);
    const baseCdA = aero ? cdaZeroYaw : cdaZeroYaw * sittingCdaMult;
    const cda = cdaAtYaw(baseCdA, yaw, yawA, yawB);
    let newV = solveSteadySpeed(pWheel, grade, massKg, crr, cda, rho, vAlongMs, vCrossMs);
    if (cornerMaxMs !== undefined && cornerMaxMs < Infinity) {
      newV = Math.min(newV, cornerMaxMs);
    }
    const newAero = newV >= aeroThresholdMs;
    if (Math.abs(newV - v) < 0.02 && newAero === aero) {
      return { v: newV, aero: newAero, yaw };
    }
    // Damp slightly to prevent oscillation
    v = 0.7 * newV + 0.3 * v;
    aero = newAero;
  }
  return { v, aero, yaw: yawDeg(v, vAlongMs, vCrossMs) };
}

function simulateForward(points, params) {
  const {
    powerW, cda,
    crr = 0.0040,
    massKg = 90.5,
    drivetrainEff = 0.975,
    windSpeedKmh = 0,
    windFromDeg = 0,
    tempC = 23,
    pressureHpa = 1013.25,
    humidityPct = 66,
    // v2 knobs (defaults match Sam / Cairns assumptions)
    sittingCdaMult = DEFAULT_SITTING_CDA_MULT,
    aeroThresholdKmh = DEFAULT_AERO_THRESHOLD_KMH,
    windHeightFactor = DEFAULT_WIND_HEIGHT_FACTOR,
    yawCurveA = DEFAULT_YAW_CURVE_A,
    yawCurveB = DEFAULT_YAW_CURVE_B,
    enableCornering = true,
    cornerFrictionMu = DEFAULT_CORNER_FRICTION_MU,
    cornerMinMs = DEFAULT_CORNER_MIN_MS,
    cornerMaxSpeeds = null, // optional pre-computed cache
  } = params;

  const rho = airDensity(tempC, pressureHpa, humidityPct);
  const pWheel = powerW * drivetrainEff;
  const aeroThresholdMs = aeroThresholdKmh / 3.6;
  const effectiveWindKmh = windSpeedKmh * windHeightFactor;

  const corners = enableCornering
    ? (cornerMaxSpeeds || precomputeCornerMaxSpeeds(points, cornerFrictionMu, cornerMinMs))
    : null;

  let totalTime = 0;
  let cumDist = 0;
  const segSpeeds = new Float32Array(points.length);
  const segTimes = new Float32Array(points.length);
  const segWindAlong = new Float32Array(points.length);
  const segAero = new Uint8Array(points.length);
  const segYaw = new Float32Array(points.length);

  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const cur = points[i];
    const segD = (cur.dist_m ?? 0) - (prev.dist_m ?? 0);
    if (segD <= 0) continue;
    const grade = (cur.grade_pct ?? 0) / 100;
    const bearing = cur.bearing_deg ?? 0;

    const w = windDecompose(effectiveWindKmh, windFromDeg, bearing);
    const cornerMax = corners ? corners[i] : Infinity;

    const { v, aero, yaw } = solveSegment({
      pWheel, grade, massKg, crr, cdaZeroYaw: cda, rho,
      vAlongMs: w.along, vCrossMs: w.cross,
      sittingCdaMult, aeroThresholdMs,
      yawA: yawCurveA, yawB: yawCurveB,
      cornerMaxMs: cornerMax,
    });

    const segTime = segD / Math.max(v, 0.1);
    totalTime += segTime;
    cumDist += segD;
    segSpeeds[i] = v;
    segTimes[i] = segTime;
    segWindAlong[i] = w.along;
    segAero[i] = aero ? 1 : 0;
    segYaw[i] = yaw;
  }

  return {
    totalTimeS: totalTime,
    totalTimeHms: formatHMS(totalTime),
    totalDistKm: cumDist / 1000,
    avgSpeedKmh: totalTime > 0 ? (cumDist / totalTime) * 3.6 : 0,
    rho,
    effectiveWindKmh,
    segSpeeds,
    segTimes,
    segWindAlong,
    segAero,
    segYaw,
  };
}

function formatHMS(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function formatHMSDelta(seconds) {
  const sign = seconds >= 0 ? "+" : "-";
  const abs = Math.abs(seconds);
  const m = Math.floor(abs / 60);
  const s = Math.floor(abs % 60);
  return `${sign}${m}:${s.toString().padStart(2, "0")}`;
}

window.Physics = {
  airDensity,
  windDecompose, windAlongHeading,
  yawDeg, cdaAtYaw,
  solveSteadySpeed, solveSegment,
  precomputeCornerMaxSpeeds,
  simulateForward,
  formatHMS, formatHMSDelta,
  // Expose defaults so the UI can show them and the README can stay accurate
  DEFAULTS: {
    WIND_HEIGHT_FACTOR: DEFAULT_WIND_HEIGHT_FACTOR,
    AERO_THRESHOLD_KMH: DEFAULT_AERO_THRESHOLD_KMH,
    SITTING_CDA_MULT: DEFAULT_SITTING_CDA_MULT,
    YAW_CURVE_A: DEFAULT_YAW_CURVE_A,
    YAW_CURVE_B: DEFAULT_YAW_CURVE_B,
    CORNER_FRICTION_MU: DEFAULT_CORNER_FRICTION_MU,
    CORNER_MIN_MS: DEFAULT_CORNER_MIN_MS,
  },
};
