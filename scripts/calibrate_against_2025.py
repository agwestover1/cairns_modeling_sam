"""
Calibration pass: use Sam's actual 2025 bike split (4:27:14) plus the actual
2025 race-day weather to back-solve his effective race-day average power.

This is the precursor to full Chung-method calibration with his TrainingPeaks
power data; here we only have time + weather, so we hold mass/Crr/CdA at
placeholder values and let power flex.

Usage:
  python3 scripts/calibrate_against_2025.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import physics_sim  # noqa: E402

PROCESSED = ROOT / "processed"


def main():
    # ---- Load ground truth ----
    with (PROCESSED / "sam_2025_results.json").open() as f:
        sam = json.load(f)
    actual_bike_time_s = sam["summary"]["bike_time_s"]
    actual_bike_hms = sam["summary"]["bike_time_hms"]

    # ---- Load 2025 weather (race-window @ swim/bike start) ----
    with (PROCESSED / "weather_hourly_2025.json").open() as f:
        wx = json.load(f)
    s = wx["by_location"]["swim_bike_start"]
    wind_kmh = s["wind_speed_kmh_mean"]
    wind_dir = s["wind_dir_vector_mean_deg"]
    temp_c = s["temp_c_mean"]
    humid = s["humidity_pct_mean"]

    # ---- Load bike course ----
    with (PROCESSED / "bike_course.json").open() as f:
        bike = json.load(f)
    points = bike["points"]

    print(f"Sam's actual 2025 bike split: {actual_bike_hms} ({actual_bike_time_s} s)")
    print(f"2025 race-day inputs: wind {wind_kmh} km/h @ 10m from {wind_dir}°, "
          f"{temp_c}°C, {humid}% RH")
    print()

    # ---- Pre-compute corner caps (course-dependent only) ----
    corners = physics_sim.precompute_corner_max_speeds(points)

    common = dict(
        crr=0.004,
        mass_kg=75.0,
        drivetrain_eff=0.975,
        wind_speed_kmh=wind_kmh,
        wind_from_deg=wind_dir,
        temp_c=temp_c,
        pressure_hpa=1013.25,
        humidity_pct=humid,
        corner_max_speeds=corners,
    )

    # ---- Forward sim: a few candidate (power, CdA) combos ----
    print("Forward predictions at actual 2025 conditions:")
    print(f"{'Power':>6}  {'CdA':>6}  {'Predicted':>11}  {'Δ vs actual':>13}")
    print("-" * 50)
    for power_w in (340, 330, 320, 310):
        for cda in (0.258, 0.248, 0.238):
            r = physics_sim.simulate_forward(
                points, power_w=power_w, cda=cda, **common
            )
            delta_s = r["total_time_s"] - actual_bike_time_s
            sign = "+" if delta_s >= 0 else "-"
            mins = int(abs(delta_s) // 60); secs = int(abs(delta_s) % 60)
            print(f"{power_w:>5}W  {cda:>6.3f}  {r['total_time_hms']:>11}  "
                  f"{sign}{mins:>2d}:{secs:02d}")
    print()

    # ---- Inverse: what POWER does Sam need at each candidate CdA to hit 4:27:14 ----
    print("Back-solve: power required to ride 4:27:14 at 2025 conditions:")
    out = {}
    for cda in (0.258, 0.248, 0.238, 0.228):
        p = physics_sim.simulate_inverse(
            points, target_time_s=actual_bike_time_s, cda=cda, **common
        )
        out[f"cda_{cda:.3f}"] = round(p, 1)
        print(f"  CdA {cda:.3f}  →  {p:.0f} W effective race-day pedal power")
    print()
    print("Interpretation: these are Sam's effective avg power values IF the other")
    print("placeholders are correct (mass 75 kg, Crr 0.004, drivetrain 0.975). Once")
    print("we have his TrainingPeaks file, compare to his measured average power.")
    print("Mismatch tells us how much our placeholder mass+Crr need to shift.")

    # Persist for record
    result = {
        "_meta": {
            "purpose": "Estimate Sam's effective 2025 race-day power from his observed bike split + actual weather",
            "method": "Inverse bisection on power, holding CdA/mass/Crr at placeholders",
            "next_step": "Compare estimated power vs measured TrainingPeaks power; adjust placeholders accordingly"
        },
        "ground_truth": {
            "athlete": sam["athlete"]["name"],
            "race": "2025 IRONMAN Cairns",
            "actual_bike_time_hms": actual_bike_hms,
            "actual_bike_time_s": actual_bike_time_s,
            "actual_bike_avg_kmh": sam["summary"]["bike_avg_speed_kmh"],
        },
        "weather_2025_actual": {
            "wind_speed_kmh_10m_mean": wind_kmh,
            "wind_direction_vector_mean_deg": wind_dir,
            "temp_c_race_window_mean": temp_c,
            "humidity_pct_mean": humid,
            "verdict_vs_historical_median": "windier by 1.9 km/h, cooler by 1.4 °C, otherwise typical",
        },
        "model_assumptions": {
            "physics_version": "v2",
            "mass_kg_placeholder": 75.0,
            "crr_placeholder": 0.004,
            "drivetrain_eff": 0.975,
            "sitting_cda_mult": physics_sim.DEFAULT_SITTING_CDA_MULT,
            "aero_threshold_kmh": physics_sim.DEFAULT_AERO_THRESHOLD_KMH,
            "wind_height_factor": round(physics_sim.DEFAULT_WIND_HEIGHT_FACTOR, 4),
        },
        "estimated_effective_power_w_by_cda": out,
    }
    out_path = PROCESSED / "calibration_2025.json"
    with out_path.open("w") as f:
        json.dump(result, f, indent=2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
