#!/usr/bin/env python3
"""
Characterize Sam's "ride the course" signature: how he distributes power by
gradient, how HR responds, and how power + HR recover after climbs.

This feeds the variable-power bike model (scripts/physics_sim.py "ride the
course" mode) — Sam surges climbs above NP and eases descents, holding HR
roughly flat, which is faster than constant power at equal physiological cost.

Reads decompressed bike .fit files. Writes processed/climbing_profile.json.
Usage: python3 scripts/analyze_climbing.py
"""
import json
from pathlib import Path

import numpy as np
import fitparse

ROOT = Path(__file__).resolve().parent.parent
WK = ROOT / "data" / "sam_workouts"
PROCESSED = ROOT / "processed"

RIDES = {
    "2026-04-19_5hr": "2026-04-19-001548-ELEMNT_BOLT_7ECA-294-0.fit",
    "2026-05-23_6hr": "2026-05-23-213119-ELEMNT_BOLT_7ECA-317-0.fit",
    "2025_race": "2025-06-14-224112-ELEMNT BOLT 7ECA-79-0.fit",
}
GRADE_BINS = [(-99, -6), (-6, -4), (-4, -2), (-2, 0), (0, 2), (2, 4), (4, 6), (6, 8), (8, 99)]


def bin_label(b):
    lo, hi = b
    if lo == -99: return f"<{hi}"
    if hi == 99:  return f">{lo}"
    return f"{lo}..{hi}"


def load(fn):
    ff = fitparse.FitFile(str(WK / fn))
    P, HR, CAD, ALT, DIST, SPD = [], [], [], [], [], []
    for r in ff.get_messages("record"):
        d = {f.name: f.value for f in r}
        P.append(d.get("power") or 0)
        HR.append(d.get("heart_rate") or np.nan)
        CAD.append(d.get("cadence") or 0)
        ALT.append(d.get("enhanced_altitude", d.get("altitude")))
        DIST.append(d.get("distance"))
        SPD.append(d.get("enhanced_speed", d.get("speed")))
    arr = lambda x: np.array([v if v is not None else np.nan for v in x], float)
    P = np.array(P, float)
    n = len(P)
    ALT, DIST, SPD = arr(ALT), arr(DIST), arr(SPD)
    alt_s = np.convolve(np.nan_to_num(ALT), np.ones(10) / 10, mode="same")
    grade = np.zeros(n)
    for i in range(5, n - 5):
        dd = DIST[i + 5] - DIST[i - 5]
        if dd and dd > 3:
            grade[i] = 100 * (alt_s[i + 5] - alt_s[i - 5]) / dd
    grade = np.clip(grade, -25, 25)
    return P, arr(HR), arr(CAD), SPD, grade, n


def normalized_power(P):
    return float((np.mean(np.convolve(P, np.ones(30) / 30, mode="valid") ** 4)) ** 0.25)


def grade_table(P, HR, CAD, SPD, grade, n, npw):
    rows = {}
    for b in GRADE_BINS:
        m = (grade >= b[0]) & (grade < b[1]) & (SPD > 2)
        if m.sum() < 20:
            continue
        rows[bin_label(b)] = {
            "secs": int(m.sum()),
            "pct_time": round(100 * m.sum() / n, 1),
            "avg_power": round(float(np.nanmean(P[m]))),
            "pct_of_np": round(100 * np.nanmean(P[m]) / npw),
            "avg_hr": round(float(np.nanmean(HR[m]))) if not np.isnan(np.nanmean(HR[m])) else None,
            "avg_cad": round(float(np.nanmean(CAD[m]))),
            "avg_kmh": round(float(np.nanmean(SPD[m]) * 3.6), 1),
        }
    return rows


def detect_climbs(P, HR, grade, SPD, n, min_grade=3.0, min_dur=90):
    """Find sustained climbs; measure climb power/HR and post-climb recovery."""
    climbs = []
    i = 0
    while i < n - 1:
        if grade[i] >= min_grade and SPD[i] > 2:
            j = i
            while j < n - 1 and grade[j] >= min_grade - 1.0:
                j += 1
            if j - i >= min_dur:
                cseg = slice(i, j)
                # recovery window: 90s after the top
                r0, r1 = j, min(j + 90, n)
                hr_top = np.nanmean(HR[max(j - 10, i):j])
                hr_end_rec = np.nanmean(HR[max(r1 - 10, r0):r1]) if r1 > r0 else np.nan
                climbs.append({
                    "dur_s": j - i,
                    "avg_grade": round(float(np.mean(grade[cseg])), 1),
                    "climb_avg_power": round(float(np.nanmean(P[cseg]))),
                    "climb_avg_hr": round(float(np.nanmean(HR[cseg]))) if not np.isnan(np.nanmean(HR[cseg])) else None,
                    "hr_at_top": round(float(hr_top)) if not np.isnan(hr_top) else None,
                    "recovery_avg_power": round(float(np.nanmean(P[r0:r1]))) if r1 > r0 else None,
                    "hr_after_90s": round(float(hr_end_rec)) if not np.isnan(hr_end_rec) else None,
                    "hr_drop_90s": round(float(hr_top - hr_end_rec)) if not np.isnan(hr_top) and not np.isnan(hr_end_rec) else None,
                })
            i = j
        else:
            i += 1
    return climbs


def main():
    out = {"_note": "Sam's gradient power/HR distribution + climb-recovery dynamics. "
                    "Feeds the variable-power 'ride the course' bike model.",
           "rides": {}}
    grade_power_mult = {}  # accumulate pct_of_np by grade bin across hilly training
    for name, fn in RIDES.items():
        P, HR, CAD, SPD, grade, n = load(fn)
        npw = normalized_power(P)
        table = grade_table(P, HR, CAD, SPD, grade, n, npw)
        climbs = detect_climbs(P, HR, grade, SPD, n)
        # average recovery stats
        drops = [c["hr_drop_90s"] for c in climbs if c["hr_drop_90s"] is not None]
        out["rides"][name] = {
            "np_w": round(npw),
            "grade_table": table,
            "n_climbs_detected": len(climbs),
            "median_hr_drop_90s_after_climb": round(float(np.median(drops))) if drops else None,
            "climbs": climbs[:15],
        }
        if name != "2025_race":  # build the power-by-grade curve from training
            for k, v in table.items():
                grade_power_mult.setdefault(k, []).append(v["pct_of_np"])

    # Sam's observed power-by-grade multipliers (% of NP), median across training.
    out["power_by_grade_pct_of_np"] = {
        k: round(float(np.median(v))) for k, v in grade_power_mult.items()
    }
    PROCESSED.joinpath("climbing_profile.json").write_text(json.dumps(out, indent=2))
    print(f"Wrote {PROCESSED/'climbing_profile.json'}\n")
    print("Sam's power-by-grade signature (median % of NP, training):")
    order = ["<-6", "-6..-4", "-4..-2", "-2..0", "0..2", "2..4", "4..6", "6..8", ">8"]
    for k in order:
        if k in out["power_by_grade_pct_of_np"]:
            print(f"  grade {k:>7}% : {out['power_by_grade_pct_of_np'][k]:>4}% of NP")
    for name in RIDES:
        r = out["rides"][name]
        print(f"\n{name}: NP {r['np_w']}W, {r['n_climbs_detected']} climbs, "
              f"median HR drop 90s after top = {r['median_hr_drop_90s_after_climb']} bpm")


if __name__ == "__main__":
    main()
