#!/usr/bin/env python3
"""
Derive Sam's CURRENT fitness profile from his recent workout files.

Per the user's directive (2026-05-28): Sam's power has grown substantially
since the 2025 race, so the 2026 projection must be calibrated from RECENT
training data — not the draft-contaminated 2025 race power. This script builds
the mean-maximal power curve across the bike files, estimates FTP, and derives
a defensible IRONMAN race-target power band.

Reads decompressed .fit files from data/sam_workouts/.
Writes processed/sam_fitness.json.

Usage:
    python3 scripts/analyze_fitness.py
"""
import json
from pathlib import Path

import numpy as np
import fitparse

ROOT = Path(__file__).resolve().parent.parent
WK = ROOT / "data" / "sam_workouts"
OUT = ROOT / "processed" / "sam_fitness.json"

# Bike files only (run files have no power). Label -> filename.
BIKE_FILES = {
    "2025_cairns_race": "2025-06-14-224112-ELEMNT BOLT 7ECA-79-0.fit",
    "2026-04-19_5hr_aerobic": "2026-04-19-001548-ELEMNT_BOLT_7ECA-294-0.fit",
    "2026-04-26_4x10_lt2_indoor": "2026-04-26-014214-ELEMNT_BOLT_7ECA-299-0.fit",
    "2026-05-16_recovery_indoor": "2026-05-16-074755-ELEMNT_BOLT_7ECA-311-0.fit",
    "2026-05-23_6hr_ride": "2026-05-23-213119-ELEMNT_BOLT_7ECA-317-0.fit",
}

# Mean-maximal durations (seconds) we report.
DURATIONS = [5, 15, 30, 60, 300, 600, 1200, 1800, 3600, 7200, 14400]


def power_series(path):
    ff = fitparse.FitFile(str(path))
    p = [r.get_value("power") or 0 for r in ff.get_messages("record")]
    return np.array(p, dtype=float)


def normalized_power(p):
    if len(p) < 30:
        return None
    roll = np.convolve(p, np.ones(30) / 30, mode="valid")
    return float((np.mean(roll ** 4)) ** 0.25)


def mean_max(p, d):
    if len(p) < d:
        return None
    csum = np.concatenate([[0.0], np.cumsum(p)])
    windows = csum[d:] - csum[:-d]
    return float(windows.max() / d)


def main():
    per_file = {}
    for label, fn in BIKE_FILES.items():
        p = power_series(WK / fn)
        if len(p) == 0 or p.max() == 0:
            continue
        npw = normalized_power(p)
        per_file[label] = {
            "samples_s": len(p),
            "avg_power_w": round(float(p.mean()), 1),
            "normalized_power_w": round(npw, 1) if npw else None,
            "variability_index": round(npw / p.mean(), 3) if npw and p.mean() else None,
            "mean_max_w": {f"{d}s": (round(mean_max(p, d), 1) if mean_max(p, d) else None)
                           for d in DURATIONS},
            "is_race": label == "2025_cairns_race",
        }

    # Composite recent-training mean-max curve (best across TRAINING files only;
    # the 2025 race is excluded so the curve reflects *current* fitness).
    training = {k: v for k, v in per_file.items() if not v["is_race"]}
    best_curve = {}
    for d in DURATIONS:
        key = f"{d}s"
        vals = [v["mean_max_w"][key] for v in training.values()
                if v["mean_max_w"][key] is not None]
        best_curve[key] = round(max(vals), 1) if vals else None

    # --- FTP estimate -------------------------------------------------------
    # Best 20-min (382 W) was set INSIDE a 5 h ride (fatigued), and 10-min reps
    # are repeatable at ~385 W. Fresh 20-min would be higher; 0.95 * best-20
    # is therefore a conservative-to-fair FTP floor. We report a band.
    p20 = best_curve.get("1200s")
    p10 = best_curve.get("600s")
    ftp_low = round(0.95 * p20) if p20 else None       # classic 95% of 20-min
    ftp_high = round(p10 * 0.99) if p10 else None       # ~10-min repeatable proxy
    ftp_estimate = round((ftp_low + ftp_high) / 2) if ftp_low and ftp_high else None

    # --- IRONMAN target band ------------------------------------------------
    # IM bike intensity factor (IF = NP/FTP) for a strong athlete who must still
    # run a marathon: 0.72 (cautious) .. 0.78 (aggressive). Sam's demonstrated
    # durability (NP 307 W for 5 h, NP 291 W for 6 h) comfortably supports the
    # upper half of this band for a ~4.5 h IM split.
    im_targets = None
    if ftp_estimate:
        im_targets = {
            "conservative_IF_0.72": round(0.72 * ftp_estimate),
            "target_IF_0.76": round(0.76 * ftp_estimate),
            "aggressive_IF_0.78": round(0.78 * ftp_estimate),
        }

    out = {
        "_generated": "scripts/analyze_fitness.py",
        "_basis": "Recent training files drive current fitness; 2025 race excluded "
                  "from the fitness curve (draft-aided, lower fitness).",
        "per_file": per_file,
        "recent_training_mean_max_w": best_curve,
        "ftp": {
            "estimate_w": ftp_estimate,
            "band_w": [ftp_low, ftp_high],
            "method": "0.95*best-20min (fatigued, in 5h ride) to ~10min repeatable proxy; "
                      "best-20min=382W set during the Apr19 KOM effort.",
        },
        "ironman_bike_target_np_w": im_targets,
        "durability_notes": {
            "np_5h_w": per_file.get("2026-04-19_5hr_aerobic", {}).get("normalized_power_w"),
            "np_6h_w": per_file.get("2026-05-23_6hr_ride", {}).get("normalized_power_w"),
            "comment": "Sustained 5-6h normalized powers far exceed any plausible IM "
                       "target, confirming durability is not the limiter — pacing "
                       "discipline (to protect the run) is.",
        },
    }

    OUT.write_text(json.dumps(out, indent=2))
    print(f"Wrote {OUT}")
    print(f"\nRecent-training mean-max power curve:")
    for d in DURATIONS:
        v = best_curve[f"{d}s"]
        lbl = f"{d}s" if d < 60 else f"{d//60}min"
        if v:
            print(f"  {lbl:>6}: {v:6.0f} W")
    print(f"\nFTP estimate: {ftp_estimate} W (band {ftp_low}-{ftp_high})")
    if im_targets:
        print("IM bike NP target band:")
        for k, v in im_targets.items():
            print(f"  {k}: {v} W")


if __name__ == "__main__":
    main()
