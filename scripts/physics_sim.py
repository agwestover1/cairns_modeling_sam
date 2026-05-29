"""
Cycling physics simulator for the IM Cairns bike course — v2.

v2 refinements over v1:
  - Wind gradient correction (Hellmann power law, 10m → ~1.2m rider height).
  - Yaw-dependent CdA via a generic high-end TT/disc/tri-spoke quadratic curve.
  - Aero / sitting-up posture switching at 12 mph (= 19.31 km/h) threshold,
    with a 1.6× sitting-up CdA multiplier (per published 0.28 → 0.45 data).
  - Slow corner speed caps from local bearing-change curvature, μ·g·r physics
    with a sane minimum corner speed.

Mirror of web/physics.js. Both implementations must stay numerically aligned.

Usage:
  python3 scripts/physics_sim.py
    (runs the scenarios defined in main(); writes processed/sim_results.json)
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "processed"

G = 9.80665

# Hellmann power law: 10m anemometer wind → rider-height (~1.2m). α = 0.11
# for open coastal terrain (Cairns).
DEFAULT_WIND_HEIGHT_FACTOR = (1.2 / 10) ** 0.11  # ≈ 0.7766

# Sam sits up below 12 mph = 19.31 km/h.
DEFAULT_AERO_THRESHOLD_KMH = 19.31

# Sitting-up CdA multiplier (≈61% increase per published 0.28→0.45 data).
DEFAULT_SITTING_CDA_MULT = 1.6

# Yaw-dependent CdA curve: factor = 1 + a*yaw² - b*|yaw|. Yaw in degrees.
# Calibrated against published TT bike + disc rear + tri-spoke front data
# (Princeton CarbonWorks, Specialized, Trek public yaw sweeps).
DEFAULT_YAW_CURVE_A = 0.00055
DEFAULT_YAW_CURVE_B = 0.005
YAW_CURVE_FLOOR = 0.92  # never reduce below 92% of zero-yaw CdA

# Cornering: μ = 0.45 conservative for race conditions on dry tarmac.
# Min speed = 4 m/s (= 14.4 km/h) — riders don't fully stop, just scrub heavily.
DEFAULT_CORNER_FRICTION_MU = 0.45
DEFAULT_CORNER_MIN_MS = 4.0
CORNER_LOOK_AHEAD_M = 30


def air_density(temp_c, pressure_hpa=1013.25, humidity_pct=60):
    T = temp_c + 273.15
    P = pressure_hpa * 100.0
    Psat_hpa = 6.1078 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    Pv = Psat_hpa * 100.0 * (humidity_pct / 100.0)
    Pd = P - Pv
    return (Pd / (287.058 * T)) + (Pv / (461.495 * T))


def wind_decompose(wind_speed_kmh, wind_from_deg, heading_deg):
    """Decompose wind into along-heading (signed: + tailwind) and cross-heading components in m/s."""
    wind_to_deg = (wind_from_deg + 180.0) % 360.0
    angle = math.radians(wind_to_deg - heading_deg)
    speed_ms = wind_speed_kmh / 3.6
    return speed_ms * math.cos(angle), speed_ms * math.sin(angle)


def wind_along_heading(wind_speed_kmh, wind_from_deg, heading_deg):
    """Legacy single-component accessor (still used by some callers)."""
    along, _ = wind_decompose(wind_speed_kmh, wind_from_deg, heading_deg)
    return along


def yaw_deg(v_rider_ms, v_along_ms, v_cross_ms):
    """Apparent-wind yaw angle magnitude in degrees."""
    head_on = v_rider_ms - v_along_ms
    if head_on <= 1e-3 and abs(v_cross_ms) <= 1e-3:
        return 0.0
    return abs(math.degrees(math.atan2(abs(v_cross_ms), head_on)))


def cda_at_yaw(cda_zero_yaw, yaw_deg_mag, a=DEFAULT_YAW_CURVE_A, b=DEFAULT_YAW_CURVE_B):
    y = abs(yaw_deg_mag)
    factor = max(YAW_CURVE_FLOOR, 1 + a * y * y - b * y)
    return cda_zero_yaw * factor


def solve_steady_speed(p_wheel, grade, mass_kg, crr, cda, rho, v_along_ms, v_cross_ms=0.0):
    """Bisection on rider speed (m/s) for steady-state given pedal power.
    Generalized to include crosswind in the apparent wind magnitude."""
    if p_wheel <= 0:
        return 0.5

    slope = math.atan(grade)
    f_grav = mass_kg * G * math.sin(slope)
    f_roll = mass_kg * G * math.cos(slope) * crr
    v_cross_sq = v_cross_ms * v_cross_ms

    def f(v):
        v_rel = v - v_along_ms
        v_app = math.sqrt(v_rel * v_rel + v_cross_sq)
        f_aero = 0.5 * rho * cda * v_app * v_rel
        return v * (f_grav + f_roll + f_aero) - p_wheel

    lo, hi = 0.05, 50.0
    if f(hi) < 0:
        return hi
    if f(lo) > 0:
        return 0.05
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def precompute_corner_max_speeds(
    points, mu=DEFAULT_CORNER_FRICTION_MU, min_ms=DEFAULT_CORNER_MIN_MS,
    look_ahead_m=CORNER_LOOK_AHEAD_M,
):
    """Per-point max safe cornering speed (m/s). Inf where no significant corner."""
    n = len(points)
    out = [float("inf")] * n
    g_mu = mu * G
    for i in range(n - 1):
        min_radius = float("inf")
        start_dist = points[i].get("dist_m") or 0
        for j in range(i + 1, n - 1):
            d = (points[j].get("dist_m") or 0) - start_dist
            if d > look_ahead_m:
                break
            b1 = points[j].get("bearing_deg") or 0
            b2 = points[j + 1].get("bearing_deg") or 0
            seg_d = (points[j + 1].get("dist_m") or 0) - (points[j].get("dist_m") or 0)
            if seg_d < 1:
                continue
            b_change = abs(((b2 - b1 + 540) % 360) - 180)
            if b_change < 0.5:
                continue
            local_radius = seg_d / math.radians(b_change)
            if local_radius < min_radius:
                min_radius = local_radius
        if min_radius != float("inf"):
            out[i] = max(min_ms, math.sqrt(g_mu * min_radius))
    return out


def solve_segment(
    *, p_wheel, grade, mass_kg, crr, cda_zero_yaw, rho,
    v_along_ms, v_cross_ms,
    sitting_cda_mult, aero_threshold_ms,
    yaw_a, yaw_b,
    corner_max_ms,
):
    v = 12.0
    aero = True
    for _ in range(4):
        yaw = yaw_deg(v, v_along_ms, v_cross_ms)
        base_cda = cda_zero_yaw if aero else cda_zero_yaw * sitting_cda_mult
        cda = cda_at_yaw(base_cda, yaw, yaw_a, yaw_b)
        new_v = solve_steady_speed(
            p_wheel, grade, mass_kg, crr, cda, rho, v_along_ms, v_cross_ms
        )
        if corner_max_ms is not None and corner_max_ms < float("inf"):
            new_v = min(new_v, corner_max_ms)
        new_aero = new_v >= aero_threshold_ms
        if abs(new_v - v) < 0.02 and new_aero == aero:
            return new_v, new_aero, yaw
        v = 0.7 * new_v + 0.3 * v
        aero = new_aero
    return v, aero, yaw_deg(v, v_along_ms, v_cross_ms)


def simulate_forward(
    points,
    *,
    power_w, cda,
    power_fn=None,
    crr=0.0040,
    mass_kg=90.5,
    drivetrain_eff=0.975,
    wind_speed_kmh=0.0,
    wind_from_deg=0.0,
    temp_c=23.0,
    pressure_hpa=1013.25,
    humidity_pct=66.0,
    sitting_cda_mult=DEFAULT_SITTING_CDA_MULT,
    aero_threshold_kmh=DEFAULT_AERO_THRESHOLD_KMH,
    wind_height_factor=DEFAULT_WIND_HEIGHT_FACTOR,
    yaw_curve_a=DEFAULT_YAW_CURVE_A,
    yaw_curve_b=DEFAULT_YAW_CURVE_B,
    enable_cornering=True,
    corner_friction_mu=DEFAULT_CORNER_FRICTION_MU,
    corner_min_ms=DEFAULT_CORNER_MIN_MS,
    corner_max_speeds=None,
):
    rho = air_density(temp_c, pressure_hpa, humidity_pct)
    p_wheel = power_w * drivetrain_eff
    aero_threshold_ms = aero_threshold_kmh / 3.6
    effective_wind_kmh = wind_speed_kmh * wind_height_factor

    corners = None
    if enable_cornering:
        corners = corner_max_speeds or precompute_corner_max_speeds(
            points, corner_friction_mu, corner_min_ms
        )

    total_time = 0.0
    cum_dist = 0.0
    splits = []
    aero_segments = 0
    sitting_segments = 0
    # For Normalized Power of a variable-power ride (time-weighted 4th-power mean;
    # course gradients last minutes so 30s-smoothing is ~negligible here).
    np_time_p4 = 0.0
    np_time = 0.0

    for i in range(1, len(points)):
        prev = points[i - 1]
        cur = points[i]
        seg_d = (cur.get("dist_m") or 0) - (prev.get("dist_m") or 0)
        if seg_d <= 0:
            continue
        grade = (cur.get("grade_pct") or 0) / 100.0
        bearing = cur.get("bearing_deg") or 0

        # Variable "ride the course" power: power_fn(grade_pct) -> watts.
        seg_power = power_fn(grade * 100.0) if power_fn is not None else power_w
        seg_p_wheel = seg_power * drivetrain_eff if power_fn is not None else p_wheel

        v_along, v_cross = wind_decompose(effective_wind_kmh, wind_from_deg, bearing)
        corner_max = corners[i] if corners is not None else float("inf")

        v, aero, yaw = solve_segment(
            p_wheel=seg_p_wheel, grade=grade, mass_kg=mass_kg, crr=crr,
            cda_zero_yaw=cda, rho=rho,
            v_along_ms=v_along, v_cross_ms=v_cross,
            sitting_cda_mult=sitting_cda_mult,
            aero_threshold_ms=aero_threshold_ms,
            yaw_a=yaw_curve_a, yaw_b=yaw_curve_b,
            corner_max_ms=corner_max,
        )

        seg_time = seg_d / max(v, 0.1)
        total_time += seg_time
        cum_dist += seg_d
        np_time += seg_time
        np_time_p4 += seg_time * (seg_power ** 4)
        if aero:
            aero_segments += 1
        else:
            sitting_segments += 1
        splits.append({
            "km": round(cum_dist / 1000.0, 3),
            "v_kmh": round(v * 3.6, 2),
            "grade_pct": round(grade * 100, 2),
            "bearing": round(bearing, 1),
            "yaw_deg": round(yaw, 1),
            "aero": aero,
            "wind_along_ms": round(v_along, 2),
            "wind_cross_ms": round(v_cross, 2),
            "seg_time_s": round(seg_time, 2),
            "cum_time_s": round(total_time, 1),
        })

    norm_power = (np_time_p4 / np_time) ** 0.25 if np_time > 0 else power_w
    return {
        "total_time_s": round(total_time, 1),
        "total_time_hms": fmt_hms(total_time),
        "total_dist_km": round(cum_dist / 1000.0, 3),
        "avg_speed_kmh": round((cum_dist / total_time) * 3.6, 2) if total_time > 0 else 0,
        "normalized_power_w": round(norm_power, 1),
        "rho_kg_m3": round(rho, 4),
        "effective_wind_kmh": round(effective_wind_kmh, 2),
        "aero_pct": round(100 * aero_segments / (aero_segments + sitting_segments), 1)
                    if (aero_segments + sitting_segments) > 0 else 0,
        "splits": splits,
    }


def make_power_fn(base_np, grade_curve):
    """Return power_fn(grade_pct)->watts from Sam's %-of-NP-by-grade signature.
    `base_np` scales the whole curve; `grade_curve` is a list of
    (grade_upper_pct, pct_of_np) breakpoints (interpolated)."""
    xs = [g for g, _ in grade_curve]
    ys = [m / 100.0 for _, m in grade_curve]

    def fn(grade_pct):
        if grade_pct <= xs[0]:
            mult = ys[0]
        elif grade_pct >= xs[-1]:
            mult = ys[-1]
        else:
            for k in range(1, len(xs)):
                if grade_pct <= xs[k]:
                    t = (grade_pct - xs[k - 1]) / (xs[k] - xs[k - 1])
                    mult = ys[k - 1] + t * (ys[k] - ys[k - 1])
                    break
        return base_np * mult

    return fn


def simulate_ride_the_course(points, *, target_np, grade_curve, **kwargs):
    """Variable-power sim that distributes power by gradient (Sam's signature),
    scaled so the achieved Normalized Power equals target_np. Returns the sim
    result dict (with the matched base power)."""
    base = target_np
    result = None
    for _ in range(25):  # fixed-point: scale base until achieved NP == target
        fn = make_power_fn(base, grade_curve)
        result = simulate_forward(points, power_w=target_np, power_fn=fn, **kwargs)
        achieved = result["normalized_power_w"]
        if abs(achieved - target_np) < 0.3:
            break
        base *= target_np / achieved
    result["base_power_w"] = round(base, 1)
    return result


def simulate_inverse(points, *, target_time_s, **kwargs):
    """Bisect on pedal power to hit a target time."""
    lo, hi = 50.0, 600.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        t = simulate_forward(points, power_w=mid, **kwargs)["total_time_s"]
        if t > target_time_s:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def fmt_hms(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}"


def load_course(slug):
    with (PROCESSED / f"{slug}_course.json").open() as f:
        return json.load(f)


def load_weather_seed():
    try:
        with (PROCESSED / "weather_seed_defaults.json").open() as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def load_json(name, default=None):
    try:
        with (PROCESSED / name).open() as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main():
    bike = load_course("bike")
    points = bike["points"]
    weather = load_weather_seed() or {}

    # Equipment-derived physics (processed/equipment.json) and current fitness
    # (processed/sam_fitness.json) drive the 2026 calibration. Edit those
    # artifacts, not this code, to retune.
    equip = load_json("equipment.json", {})
    fitness = load_json("sam_fitness.json", {})

    cda_central = equip.get("rider_position", {}).get("cda_m2", {}).get("central", 0.238)
    cda_opt = equip.get("rider_position", {}).get("cda_m2", {}).get("optimistic", 0.228)
    crr = equip.get("tires", {}).get("crr", {}).get("value", 0.0040)
    mass = equip.get("mass", {}).get("total_race_kg", 82)
    mass_lo, mass_hi = equip.get("mass", {}).get("range_kg", [78, 86])
    dt_eff = equip.get("drivetrain_eff", 0.975)

    im = fitness.get("ironman_bike_target_np_w", {})
    p_cons = im.get("conservative_IF_0.72", 271)
    p_tgt = im.get("target_IF_0.76", 286)
    p_agg = im.get("aggressive_IF_0.78", 293)

    wind_kmh = weather.get("wind_speed_kmh_median", 17.5)
    wind_from_deg = weather.get("wind_direction_vector_mean_deg", 140.0)
    temp_c = weather.get("temp_c_mean_median", 23.0)
    humidity = weather.get("humidity_pct_mean_median", 66.0)

    common = dict(
        mass_kg=mass,
        crr=crr,
        drivetrain_eff=dt_eff,
        wind_speed_kmh=wind_kmh,
        wind_from_deg=wind_from_deg,
        temp_c=temp_c,
        pressure_hpa=1013.25,
        humidity_pct=humidity,
    )

    print(f"Equipment: CdA {cda_central} (Plasma 6), Crr {crr} (GP5000 TT TR 28mm "
          f"+ TPU @ 80-82psi), mass {mass}kg")
    print(f"Current fitness IM target band: {p_cons}/{p_tgt}/{p_agg} W "
          f"(cons/target/aggr); FTP est {fitness.get('ftp',{}).get('estimate_w','?')} W")

    # Pre-compute corners once (independent of weather/rider)
    print("Pre-computing corner max speeds...")
    corner_speeds = precompute_corner_max_speeds(points)
    corner_count = sum(1 for v in corner_speeds if v < float("inf") and v < 15)
    print(f"  {corner_count} points flagged with corner cap < 15 m/s (54 km/h)")
    common["corner_max_speeds"] = corner_speeds

    # Effective time-averaged drag reduction from legal 20m drafting.
    # Calm-air benefit at 20m is ~3-9% (Swiss Side CFD), but it (a) applies only
    # to the aero term, (b) only when a legal rider is actually ~20m ahead, and
    # (c) is cut sharply by Cairns crosswinds. Net race-average ~3%, plus minor
    # slingshotting. Modelled as a CdA multiplier.
    draft = equip.get("drafting", {})
    draft_factor = 1.0 - draft.get("effective_avg_drag_reduction_pct", 3.0) / 100.0
    cda_draft = round(cda_central * draft_factor, 4)

    # 2026 scenarios: current fitness + new equipment physics, median weather.
    scenarios = {
        "2026_conservative":  {"cda": cda_central, "power_w": p_cons,
                               "label": f"2026 conservative (IF 0.72, {p_cons}W)"},
        "2026_target":        {"cda": cda_central, "power_w": p_tgt,
                               "label": f"2026 TARGET solo (IF 0.76, {p_tgt}W)"},
        "2026_aggressive":    {"cda": cda_central, "power_w": p_agg,
                               "label": f"2026 aggressive (IF 0.78, {p_agg}W)"},
        "2026_target_draft":  {"cda": cda_draft, "power_w": p_tgt,
                               "label": f"2026 target + 20m drafting (~3%, CdA {cda_draft})"},
        "2026_target_aero":   {"cda": cda_opt, "power_w": p_tgt,
                               "label": f"2026 target + optimized aero (CdA {cda_opt})"},
        "2026_target_calm":   {"cda": cda_central, "power_w": p_tgt,
                               "label": "2026 target, calm day (5 km/h)",
                               "_override": {"wind_speed_kmh": 5.0}},
        "2026_target_windy":  {"cda": cda_central, "power_w": p_tgt,
                               "label": "2026 target, windy day (28 km/h)",
                               "_override": {"wind_speed_kmh": 28.0}},
        "2026_target_mass_hi": {"cda": cda_central, "power_w": p_tgt,
                                "label": f"2026 target, heavy ({mass_hi}kg)",
                                "_override": {"mass_kg": mass_hi}},
        "2026_target_mass_lo": {"cda": cda_central, "power_w": p_tgt,
                                "label": f"2026 target, light ({mass_lo}kg)",
                                "_override": {"mass_kg": mass_lo}},
    }

    out = {
        "physics_version": "v2",
        "course": "bike",
        "weather_inputs": {
            "wind_speed_kmh_10m": wind_kmh,
            "wind_speed_kmh_rider_height": round(wind_kmh * DEFAULT_WIND_HEIGHT_FACTOR, 2),
            "wind_height_factor": round(DEFAULT_WIND_HEIGHT_FACTOR, 4),
            "wind_from_deg": wind_from_deg,
            "temp_c": temp_c,
            "humidity_pct": humidity,
        },
        "model_params": {
            "aero_threshold_kmh": DEFAULT_AERO_THRESHOLD_KMH,
            "sitting_cda_mult": DEFAULT_SITTING_CDA_MULT,
            "yaw_curve_a": DEFAULT_YAW_CURVE_A,
            "yaw_curve_b": DEFAULT_YAW_CURVE_B,
            "corner_friction_mu": DEFAULT_CORNER_FRICTION_MU,
            "corner_min_ms": DEFAULT_CORNER_MIN_MS,
        },
        "scenarios": {},
    }

    print(f"\nMedian Cairns conditions: wind {wind_kmh:.1f} km/h @ 10m "
          f"({wind_kmh * DEFAULT_WIND_HEIGHT_FACTOR:.1f} at rider height), "
          f"from {wind_from_deg:.0f}°, {temp_c:.1f}°C, {humidity:.0f}% RH")
    print(f"Air density: {air_density(temp_c, 1013.25, humidity):.4f} kg/m³")
    print()
    print(f"{'Scenario':<42}  {'Power':>6}  {'CdA':>6}  {'Time':>10}  {'Avg':>11}  {'Aero%':>6}")
    print("-" * 100)

    for key, scen in scenarios.items():
        params = dict(common)
        if "_override" in scen:
            params.update(scen["_override"])
        result = simulate_forward(points, power_w=scen["power_w"], cda=scen["cda"], **params)
        out["scenarios"][key] = {
            "label": scen["label"],
            "power_w": scen["power_w"],
            "cda": scen["cda"],
            "total_time_s": result["total_time_s"],
            "total_time_hms": result["total_time_hms"],
            "avg_speed_kmh": result["avg_speed_kmh"],
            "rho_kg_m3": result["rho_kg_m3"],
            "effective_wind_kmh": result["effective_wind_kmh"],
            "aero_pct": result["aero_pct"],
        }
        print(f"{scen['label']:<42}  {scen['power_w']:>5}W  {scen['cda']:>6.3f}  "
              f"{result['total_time_hms']:>10}  {result['avg_speed_kmh']:>7.2f} km/h  "
              f"{result['aero_pct']:>5.1f}%")
        splits_path = PROCESSED / f"sim_splits_{key}.json"
        with splits_path.open("w") as f:
            json.dump({
                "label": scen["label"],
                "splits": result["splits"],
                "total_time_hms": result["total_time_hms"],
            }, f)

    # --- "Ride the course": variable power by gradient, matched to target NP ---
    # Sam's observed power-by-grade signature (processed/climbing_profile.json).
    cl0 = load_json("climbing_profile.json", {})
    sig = cl0.get("power_by_grade_pct_of_np", {})
    # map bin labels -> representative grade %
    bin_grade = {"<-6": -8, "-6..-4": -5, "-4..-2": -3, "-2..0": -1,
                 "0..2": 1, "2..4": 3, "4..6": 5, "6..8": 7, ">8": 10}
    grade_curve = sorted((bin_grade[k], v) for k, v in sig.items() if k in bin_grade)
    if grade_curve:
        print("\n'Ride the course' (variable power by gradient, SAME NP as constant):")
        print(f"{'Scenario':<42}  {'NP':>4}  {'base':>5}  {'Time':>10}  {'vs const':>9}")
        print("-" * 80)
        for key, np_target, cda_s in [("2026_target_rtc", p_tgt, cda_central),
                                       ("2026_aggressive_rtc", p_agg, cda_central)]:
            res = simulate_ride_the_course(points, target_np=np_target,
                                           grade_curve=grade_curve, cda=cda_s, **common)
            # matching constant-power split at the same NP
            const = simulate_forward(points, power_w=np_target, cda=cda_s, **common)
            saved = const["total_time_s"] - res["total_time_s"]
            label = f"Ride-the-course @ NP {np_target}W"
            out["scenarios"][key] = {
                "label": label, "power_w": np_target, "cda": cda_s,
                "mode": "variable_power_by_grade",
                "base_power_w": res["base_power_w"],
                "achieved_np_w": res["normalized_power_w"],
                "total_time_s": res["total_time_s"],
                "total_time_hms": res["total_time_hms"],
                "avg_speed_kmh": res["avg_speed_kmh"],
                "saved_vs_constant_s": round(saved, 1),
                "rho_kg_m3": res["rho_kg_m3"],
                "effective_wind_kmh": res["effective_wind_kmh"],
                "aero_pct": res["aero_pct"],
            }
            print(f"{label:<42}  {res['normalized_power_w']:>4.0f}  "
                  f"{res['base_power_w']:>5.0f}  {res['total_time_hms']:>10}  "
                  f"{'-'+fmt_hms(saved)[2:]:>9}")
        out["ride_the_course_grade_curve"] = grade_curve

    print()
    print("Inverse: NP required to hit target splits (median conditions, "
          f"CdA {cda_central}, {mass}kg)")
    for hh, mm in [(4, 0), (4, 10), (4, 20), (4, 30)]:
        target_s = hh * 3600 + mm * 60
        p = simulate_inverse(points, target_time_s=target_s, cda=cda_central, **common)
        label = f"{hh}:{mm:02d}:00"
        print(f"  {label}  →  {p:.0f} W")
        out.setdefault("inverse_targets", {})[label] = round(p, 1)

    # Marginal-gain table: what a 5W power gain vs a 0.005 CdA gain each buy,
    # at the 2026 target operating point (median weather).
    base = simulate_forward(points, power_w=p_tgt, cda=cda_central, **common)["total_time_s"]
    plus5 = simulate_forward(points, power_w=p_tgt + 5, cda=cda_central, **common)["total_time_s"]
    cda5 = simulate_forward(points, power_w=p_tgt, cda=cda_central - 0.005, **common)["total_time_s"]
    out["marginal_gains_s"] = {
        "base_time_s": round(base, 1),
        "plus_5W_saves_s": round(base - plus5, 1),
        "minus_0.005_cda_saves_s": round(base - cda5, 1),
    }
    print(f"\nMarginal gains at 2026 target ({p_tgt}W / CdA {cda_central}):")
    print(f"  +5 W       saves {base - plus5:5.1f} s")
    print(f"  -0.005 CdA saves {base - cda5:5.1f} s")

    with (PROCESSED / "sim_results.json").open("w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {PROCESSED / 'sim_results.json'}")


if __name__ == "__main__":
    main()
