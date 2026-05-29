#!/usr/bin/env python3
"""
Run fitness profile + IM Cairns marathon projection & pacing plan for Sam.

The bike-leg simulator is physics-based; the run leg is paced from physiology,
not aerodynamics, so this is a separate model. It reads recent run files,
extracts threshold (LT2) and LT1 paces + HR, and projects the IM marathon-off-
the-bike pace band, calibrated against the actual 2025 race run.

Reads decompressed run .FIT files from data/sam_workouts/.
Writes processed/run_prediction.json.

Usage: python3 scripts/analyze_run.py
"""
import json
from pathlib import Path

import numpy as np
import fitparse

ROOT = Path(__file__).resolve().parent.parent
WK = ROOT / "data" / "sam_workouts"
PROCESSED = ROOT / "processed"

RUN_FILES = {
    "2025_cairns_race": "tp-4966123.2025-06-15-08-02-14-349Z.GarminPing.AAAAAGhOfgYGTkvM.FIT",
    "2026-04-08_3x2km_lt2": "tp-4966123.2026-04-08-07-35-40-546Z.GarminPing.AAAAAGnWBUxtFFD5.FIT",
    "2026-04-20_10x1km_track": "tp-4966123.2026-04-20-08-12-35-894Z.GarminPing.AAAAAGnl3_Opc__i.FIT",
    "2026-05-22_3x30_lt1": "tp-4966123.2026-05-22-07-40-42-382Z.GarminPing.AAAAAGoQCHqp3fAg.FIT",
    # Day-AFTER the 6hr ride: 3x10min LT1 on fatigued legs — our best brick-like
    # durability datapoint (see processed/run_prediction.json -> durability_check).
    "2026-05-24_3x10_lt1_post6hr": "tp-4966123.2026-05-24-07-15-27-834Z.GarminPing.AAAAAGoSpY8uFY25.FIT",
}


def pace_str(s_per_km):
    return f"{int(s_per_km // 60)}:{int(round(s_per_km % 60)):02d}"


def lap_summary(path):
    ff = fitparse.FitFile(str(path))
    laps = []
    for m in ff.get_messages("lap"):
        d = {f.name: f.value for f in m}
        dist = (d.get("total_distance") or 0) / 1000.0
        dur = d.get("total_timer_time") or 0
        if dist > 0.3:  # ignore micro recovery laps for rep paces
            laps.append({"km": round(dist, 2), "s": round(dur, 1),
                         "pace_s_km": dur / dist if dist else None,
                         "hr": d.get("avg_heart_rate")})
    return laps


def main():
    profile = {}
    for name, fn in RUN_FILES.items():
        laps = lap_summary(WK / fn)
        profile[name] = laps

    # --- derive key paces (the "work" laps, not warmup/cooldown) -------------
    # LT2 / threshold: the 1km and 2km reps (fast, pace 3:30-3:45)
    rep_paces = []
    for name in ("2026-04-08_3x2km_lt2", "2026-04-20_10x1km_track"):
        for lp in profile[name]:
            if lp["pace_s_km"] and 195 <= lp["pace_s_km"] <= 240 and 0.8 <= lp["km"] <= 2.2:
                rep_paces.append(lp["pace_s_km"])
    lt2_pace = float(np.median(rep_paces)) if rep_paces else None

    # LT1 (FRESH): the 25-30 min sustained efforts on May 22 (~4:10/km)
    lt1_paces = [lp["pace_s_km"] for lp in profile["2026-05-22_3x30_lt1"]
                 if lp["pace_s_km"] and lp["km"] >= 5.5]
    lt1_pace = float(np.median(lt1_paces)) if lt1_paces else None
    lt1_hr = float(np.median([lp["hr"] for lp in profile["2026-05-22_3x30_lt1"]
                              if lp["hr"] and lp["km"] >= 5.5])) if lt1_paces else None

    # LT1 (FATIGUED): the 3x10min efforts on May 24, the day AFTER the 6hr ride.
    # The ~10min efforts are the 2.2-2.7km laps; recoveries are shorter.
    fat_laps = [lp for lp in profile.get("2026-05-24_3x10_lt1_post6hr", [])
                if lp["pace_s_km"] and lp["km"] >= 2.0 and 540 <= lp["s"] <= 660]
    fat_lt1_pace = float(np.median([lp["pace_s_km"] for lp in fat_laps])) if fat_laps else None
    fat_lt1_hr = float(np.median([lp["hr"] for lp in fat_laps if lp["hr"]])) if fat_laps else None

    # Durability = HR cost of accumulated fatigue at the SAME LT1 pace.
    # Fresh (May22) vs fatigued (May24, post-6hr). Small cost = strong durability.
    hr_cost_of_fatigue = (round(fat_lt1_hr - lt1_hr, 1)
                          if (fat_lt1_hr and lt1_hr) else None)

    # 2025 actual IM run pace (ground truth) = 4:49/km over 42.2km.
    im_2025_pace = 4 * 60 + 49

    def marathon(pace_s_km):
        s = pace_s_km * 42.2
        return {"pace_per_km": pace_str(pace_s_km),
                "time_hms": f"{int(s//3600)}:{int((s%3600)//60):02d}:{int(s%60):02d}"}

    # --- GOAL: sub-3:00 marathon = 4:16/km even -----------------------------
    SUB3_PACE = 180 * 60 / 42.195   # 255.95 s/km = 4:15.6/km
    # IM-off-bike pace sits some seconds/km slower than FRESH LT1 (fatigue +
    # heat + duration). Typical range ~18-30 s/km. We now have athlete-specific
    # durability evidence: the May24 session held LT1 PACE on legs fatigued by
    # the prior day's 6hr ride at only ~+2 bpm HR cost (see durability_check) —
    # strong fatigue resistance — so we model the LOWER-MIDDLE of the range.
    # CAVEAT: that was a 1hr run off an AEROBIC ride, not a 3h+ marathon off a
    # race-intensity bike, so we don't go to the optimistic extreme.
    IM_PENALTY = 20 if (hr_cost_of_fatigue is not None and hr_cost_of_fatigue <= 4) else 24
    lt1_needed_for_sub3 = SUB3_PACE - IM_PENALTY
    lt1_gap = lt1_pace - lt1_needed_for_sub3   # +ve = LT1 still too slow

    # Current-fitness IM-run projection band (off a ~286W IF0.76 bike).
    target_pace = lt1_pace + IM_PENALTY        # well-paced, current fitness
    best_pace = lt1_pace + (IM_PENALTY - 5)    # great day, fuel dialled, cooler
    cons_pace = im_2025_pace                   # 2025 repeat (overpace, heat, GI)

    out = {
        "_note": "Run leg paced from physiology (separate from bike physics). Cairns run "
                 "is 3 laps, FLAT (~322m / 42.9km), historically warm (~29C in 2025). "
                 "GOAL is a sub-3:00 marathon.",
        "lap_detail": profile,
        "fitness": {
            "lt2_threshold_pace_per_km": pace_str(lt2_pace),
            "lt2_note": "1km/2km reps ~" + pace_str(lt2_pace) + "/km, HR 160-176. Strong leg speed.",
            "lt1_pace_per_km": pace_str(lt1_pace),
            "lt1_note": f"Fresh 3x25-30min efforts ~{pace_str(lt1_pace)}/km at HR ~{round(lt1_hr) if lt1_hr else '?'}.",
            "2025_im_run_pace_per_km": pace_str(im_2025_pace),
        },
        "durability_check": {
            "_what": "Fresh LT1 (May22) vs FATIGUED LT1 (May24, day after the 6hr ride) at the same pace.",
            "fresh_lt1_pace_per_km": pace_str(lt1_pace) if lt1_pace else None,
            "fresh_lt1_hr": round(lt1_hr) if lt1_hr else None,
            "fatigued_lt1_pace_per_km": pace_str(fat_lt1_pace) if fat_lt1_pace else None,
            "fatigued_lt1_hr": round(fat_lt1_hr) if fat_lt1_hr else None,
            "hr_cost_of_fatigue_bpm": hr_cost_of_fatigue,
            "reading": (f"Held LT1 pace (~{pace_str(fat_lt1_pace)}/km) on legs fatigued by a 6hr "
                        f"ride at only +{hr_cost_of_fatigue} bpm — strong fatigue resistance. "
                        f"Supports modelling the IM-off-bike penalty at the lower-middle "
                        f"({IM_PENALTY}s/km). CAVEAT: 1hr run off an AEROBIC ride, not 3h+ off a "
                        f"race bike, so not extrapolated to the optimistic extreme."
                        if hr_cost_of_fatigue is not None else "insufficient data"),
        },
        "sub3_goal": {
            "required_even_pace_per_km": pace_str(SUB3_PACE),
            "verdict": (f"GOAL requiring a fitness lift, NOT a current-fitness outcome. At current "
                        f"run fitness the model ceiling is ~{marathon(best_pace)['time_hms']} (best "
                        f"case); sub-3 is ~{round((SUB3_PACE - best_pace) * 42.2 / 60)}min beyond it. "
                        f"4:16/km equals his current FRESH LT1 ({pace_str(lt1_pace)}); the new "
                        f"durability evidence (small HR cost when fatigued) is encouraging but the "
                        f"3h+ duration + heat gap remains. His threshold ({pace_str(lt2_pace)}) shows "
                        f"the speed ceiling is there to build it."),
            "gap_analysis": {
                "lt1_now_per_km": pace_str(lt1_pace),
                "lt1_needed_for_sub3_per_km": pace_str(lt1_needed_for_sub3),
                "lt1_gap_s_per_km": round(lt1_gap, 0),
                "reading": f"LT1 must drop ~{round(lt1_gap)}s/km (to ~{pace_str(lt1_needed_for_sub3)}) so "
                           f"4:16/km sits below threshold and is holdable when fatigued. With the "
                           f"durability evidence the penalty is modelled at {IM_PENALTY}s/km (was 22). "
                           f"He has the speed ({pace_str(lt2_pace)} threshold); the gap is aerobic "
                           f"durability + executing it off the bike in heat.",
                "vs_2025": f"Sub-3 is {round((im_2025_pace - SUB3_PACE))}s/km faster than his 2025 "
                           "IM run (4:49) = ~23 min. Big jump, but his threshold/LT1 have clearly improved.",
            },
            "what_closes_the_gap": [
                "Push LT1 from 4:13 toward ~3:58/km (long tempo / sweet-spot run blocks).",
                "Brick runs at 4:16/km OFF a hard bike, so goal pace feels controlled when fatigued.",
                "Don't overbike: every extra bike IF point taxes the run; protect ~IF 0.76-0.78.",
                "Heat acclimation (Cairns ~29C) — heat alone can cost 15-25 s/km if unprepared.",
                "Fuel/gut training to prevent the 2025 GI blow-up.",
            ],
        },
        "marathon_projection": {
            "sub3_goal": marathon(SUB3_PACE),
            "best_case": marathon(best_pace),
            "target": marathon(target_pace),
            "conservative": marathon(cons_pace),
            "assumptions": "Off a ~286W/IF0.76 bike. best_case ~ the sub-3 neighbourhood on a "
                           "great day; target = solid current-fitness execution; conservative = "
                           "2025 repeat. Heat is the biggest swing.",
        },
        "sub3_pacing_plan": {
            "the_2025_mistake": "Went out 4:19-4:22/km (~threshold) for 5km, blew to 5:00+/km "
                                "with vomits; lost 59 places. Overpace + GI + heat.",
            "even_split_target": pace_str(SUB3_PACE) + "/km the whole way (NOT a fast start)",
            "segments": [
                "0-10km: 4:18-4:20/km, HR <=150. Feel easy. This banks the back half.",
                "10-32km: lock 4:16/km, HR 150-156. Metronome. This is the race.",
                "32-42km: hold 4:16; if HR<158 and gut OK, ease toward 4:12. Negative split wins.",
            ],
            "grade_note": "Cairns run is flat; grade-adjustment is minor. On the gentle rises "
                          "hold EFFORT/HR (let pace slip ~5-8s/km) and take it back on the falls "
                          "(~3-5s/km quicker) — never surge a rise.",
            "hr_cap": "Keep HR <=156 through 32km; his LT1 work sits at 155-158, so >158 early = overcooked.",
        },
    }
    PROCESSED.joinpath("run_prediction.json").write_text(json.dumps(out, indent=2))
    print(f"Wrote {PROCESSED/'run_prediction.json'}\n")
    print(f"LT2 threshold pace : {pace_str(lt2_pace)}/km")
    print(f"LT1 pace (fresh)   : {pace_str(lt1_pace)}/km @ HR {round(lt1_hr) if lt1_hr else '?'}")
    print(f"LT1 (fatigued/May24): {pace_str(fat_lt1_pace)}/km @ HR {round(fat_lt1_hr) if fat_lt1_hr else '?'}"
          f"  -> HR cost of fatigue = +{hr_cost_of_fatigue} bpm  => IM penalty {IM_PENALTY}s/km")
    print(f"2025 actual IM run : {pace_str(im_2025_pace)}/km (3:23:32)\n")
    print(f"SUB-3 GOAL         : {pace_str(SUB3_PACE)}/km even -> 3:00:00")
    print(f"  LT1 needed ~{pace_str(lt1_needed_for_sub3)}/km  (now {pace_str(lt1_pace)} -> gap {round(lt1_gap)}s/km)\n")
    print(f"2026 marathon projection (off ~286W bike):")
    print(f"  best case    {marathon(best_pace)['pace_per_km']}/km -> {marathon(best_pace)['time_hms']}")
    print(f"  TARGET       {marathon(target_pace)['pace_per_km']}/km -> {marathon(target_pace)['time_hms']}")
    print(f"  conservative {marathon(cons_pace)['pace_per_km']}/km -> {marathon(cons_pace)['time_hms']}")


if __name__ == "__main__":
    main()
