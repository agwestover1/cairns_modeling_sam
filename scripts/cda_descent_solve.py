#!/usr/bin/env python3
"""
Back-solve Sam's effective CdA from high-speed segments of real rides.

At 45-60 km/h, aerodynamic drag is the dominant resistive force, so the power
balance can be inverted for CdA with low sensitivity to Crr/grade error:

    P_pedal*eta / v  =  m*g*sin(theta)         (gravity, signed by grade)
                      + m*g*cos(theta)*Crr      (rolling)
                      + 0.5*rho*CdA*v_app*v_rel (aero)
                      + m*a                      (inertia, dv/dt)

  =>  CdA = (P_wheel/v - F_grav - F_roll - m*a) / (0.5*rho*v_app*v_rel)

We compute this per-second on fast samples, using that day's measured wind
(decomposed onto the actual GPS heading), and take the robust median.

Primary target: the 2025 IM Cairns race file (we know that day's weather).
Cross-checked on the recent outdoor training rides.

Writes processed/cda_descent_solve.json.

Usage: python3 scripts/cda_descent_solve.py
"""
import json
import math
from pathlib import Path

import numpy as np
import fitparse

ROOT = Path(__file__).resolve().parent.parent
WK = ROOT / "data" / "sam_workouts"
PROCESSED = ROOT / "processed"
G = 9.80665
SEMI = 180.0 / 2 ** 31  # Garmin semicircles -> degrees

# Equipment (kept in sync with equipment.json)
EQUIP = json.loads((PROCESSED / "equipment.json").read_text())
CRR = EQUIP["tires"]["crr"]["value"]
MASS = EQUIP["mass"]["total_race_kg"]
ETA = EQUIP["drivetrain_eff"]
WIND_HEIGHT_FACTOR = (1.2 / 10) ** 0.11  # match physics_sim

# Weather per ride (10m wind). 2025 race values come from the ERA5 pull used by
# calibrate_against_2025.py; training rides use rough local defaults (their CdA
# estimate is only a sanity cross-check, not the primary calibration).
RIDES = {
    "2025_cairns_race": {
        "file": "2025-06-14-224112-ELEMNT BOLT 7ECA-79-0.fit",
        "wind_kmh_10m": 19.35, "wind_from_deg": 145.1, "temp_c": 21.61, "rh": 61.4,
        "primary": True,
    },
    "2026-04-19_5hr": {
        "file": "2026-04-19-001548-ELEMNT_BOLT_7ECA-294-0.fit",
        "wind_kmh_10m": 10.0, "wind_from_deg": 140.0, "temp_c": 10.0, "rh": 60.0,
        "primary": False,
    },
    "2026-05-23_6hr": {
        "file": "2026-05-23-213119-ELEMNT_BOLT_7ECA-317-0.fit",
        "wind_kmh_10m": 12.0, "wind_from_deg": 140.0, "temp_c": 10.0, "rh": 60.0,
        "primary": False,
    },
}

# Sample filters for a robust aero-dominant solve.
MIN_SPEED_MS = 13.0     # 46.8 km/h — aero is clearly dominant
MAX_ACCEL_MS2 = 0.30    # near-steady (inertia term small & well-estimated)
ALT_SMOOTH_S = 5        # rolling window for altitude/grade smoothing


def air_density(temp_c, rh, pressure_hpa=1013.25):
    T = temp_c + 273.15
    Psat = 6.1078 * math.exp(17.27 * temp_c / (temp_c + 237.3)) * 100.0
    Pv = Psat * (rh / 100.0)
    Pd = pressure_hpa * 100.0 - Pv
    return Pd / (287.058 * T) + Pv / (461.495 * T)


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def load_records(path):
    ff = fitparse.FitFile(str(path))
    rows = []
    for r in ff.get_messages("record"):
        d = {f.name: f.value for f in r}
        spd = d.get("enhanced_speed", d.get("speed"))
        alt = d.get("enhanced_altitude", d.get("altitude"))
        lat = d.get("position_lat")
        lon = d.get("position_long")
        rows.append({
            "t": d.get("timestamp"),
            "v": spd,
            "p": d.get("power"),
            "alt": alt,
            "dist": d.get("distance"),
            "lat": lat * SEMI if lat is not None else None,
            "lon": lon * SEMI if lon is not None else None,
        })
    return rows


def solve_ride(cfg):
    rows = load_records(WK / cfg["file"])
    n = len(rows)
    v = np.array([r["v"] if r["v"] is not None else np.nan for r in rows])
    p = np.array([r["p"] if r["p"] is not None else 0.0 for r in rows])
    alt = np.array([r["alt"] if r["alt"] is not None else np.nan for r in rows])
    dist = np.array([r["dist"] if r["dist"] is not None else np.nan for r in rows])

    # Smooth altitude, derive grade vs distance.
    k = ALT_SMOOTH_S
    alt_s = np.convolve(np.nan_to_num(alt), np.ones(k) / k, mode="same")
    grade = np.zeros(n)
    for i in range(1, n - 1):
        dd = dist[i + 1] - dist[i - 1]
        if dd and dd > 1:
            grade[i] = (alt_s[i + 1] - alt_s[i - 1]) / dd
    grade = np.clip(grade, -0.20, 0.20)

    # Acceleration (central difference on speed).
    a = np.zeros(n)
    a[1:-1] = (v[2:] - v[:-2]) / 2.0

    # Heading from GPS.
    head = np.full(n, np.nan)
    for i in range(n - 1):
        if None not in (rows[i]["lat"], rows[i]["lon"], rows[i + 1]["lat"], rows[i + 1]["lon"]):
            head[i] = bearing(rows[i]["lat"], rows[i]["lon"], rows[i + 1]["lat"], rows[i + 1]["lon"])

    rho = air_density(cfg["temp_c"], cfg["rh"])
    wind_ms = cfg["wind_kmh_10m"] * WIND_HEIGHT_FACTOR / 3.6
    wind_to = (cfg["wind_from_deg"] + 180.0) % 360.0

    cdas = []
    for i in range(2, n - 2):
        vi = v[i]
        if np.isnan(vi) or vi < MIN_SPEED_MS:
            continue
        if abs(a[i]) > MAX_ACCEL_MS2:
            continue
        if np.isnan(head[i]):
            continue
        ang = math.radians(wind_to - head[i])
        v_along = wind_ms * math.cos(ang)
        v_cross = wind_ms * math.sin(ang)
        v_rel = vi - v_along
        if v_rel <= 1.0:
            continue
        v_app = math.sqrt(v_rel * v_rel + v_cross * v_cross)
        theta = math.atan(grade[i])
        f_grav = MASS * G * math.sin(theta)
        f_roll = MASS * G * math.cos(theta) * CRR
        p_wheel = (p[i] or 0.0) * ETA
        f_aero = p_wheel / vi - f_grav - f_roll - MASS * a[i]
        if f_aero <= 0:
            continue
        cda = f_aero / (0.5 * rho * v_app * v_rel)
        if 0.10 < cda < 0.45:   # sane window; reject GPS/baro glitches
            cdas.append(cda)

    cdas = np.array(cdas)
    if len(cdas) < 30:
        return {"file": cfg["file"], "rho": round(rho, 4), "n_samples": len(cdas),
                "note": "too few high-speed samples for a reliable solve"}
    return {
        "file": cfg["file"],
        "rho_kg_m3": round(rho, 4),
        "wind_kmh_rider": round(cfg["wind_kmh_10m"] * WIND_HEIGHT_FACTOR, 2),
        "mass_kg": MASS, "crr": CRR,
        "n_samples": int(len(cdas)),
        "cda_median": round(float(np.median(cdas)), 4),
        "cda_p25": round(float(np.percentile(cdas, 25)), 4),
        "cda_p75": round(float(np.percentile(cdas, 75)), 4),
        "cda_mean": round(float(np.mean(cdas)), 4),
    }


def main():
    out = {"_method": "Aero-dominant power-balance inversion on >46.8km/h near-steady "
                      "samples; wind decomposed onto GPS heading at rider height.",
           "_inputs": {"crr": CRR, "mass_kg": MASS, "eta": ETA},
           "rides": {}}
    print(f"Back-solving CdA  (Crr={CRR}, mass={MASS}kg)\n")
    for name, cfg in RIDES.items():
        res = solve_ride(cfg)
        out["rides"][name] = res
        if "cda_median" in res:
            print(f"{name:22} rho={res['rho_kg_m3']}  n={res['n_samples']:5}  "
                  f"CdA median={res['cda_median']}  (IQR {res['cda_p25']}-{res['cda_p75']})"
                  + ("   <-- PRIMARY" if cfg["primary"] else ""))
        else:
            print(f"{name:22} {res.get('note')}")

    prim = out["rides"]["2025_cairns_race"]
    out["recommended_cda_zero_yaw"] = prim.get("cda_median")
    PROCESSED.joinpath("cda_descent_solve.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote {PROCESSED/'cda_descent_solve.json'}")
    if "cda_median" in prim:
        print(f"\n2025-race-derived effective CdA: {prim['cda_median']} "
              f"(vs assumed 0.238 / old 0.258)")


if __name__ == "__main__":
    main()
