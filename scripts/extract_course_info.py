"""
Derive structured race-day info from the 2026 IRONMAN Cairns athlete guide:
aid stations (with map coords looked up from course distance), cut-off times,
wetsuit thresholds, and key landmarks.

The athlete guide gives aid station positions by km (cumulative course distance).
We use the parsed TCX courses to convert km → lat/lng coordinates.

Output: processed/course_info.json
"""

import bisect
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "processed"


def load_course(slug: str) -> dict:
    with (PROCESSED / f"{slug}_course.json").open() as f:
        return json.load(f)


def coords_at_distance(points, target_m):
    """Linear interpolation along the course to find (lat, lng, alt) at a given cumulative distance."""
    dists = [p["dist_m"] for p in points]
    idx = bisect.bisect_left(dists, target_m)
    if idx <= 0:
        p = points[0]
        return p["lat"], p["lng"], p["alt"]
    if idx >= len(points):
        p = points[-1]
        return p["lat"], p["lng"], p["alt"]
    a = points[idx - 1]
    b = points[idx]
    span = b["dist_m"] - a["dist_m"]
    if span <= 0:
        return a["lat"], a["lng"], a["alt"]
    t = (target_m - a["dist_m"]) / span
    lat = a["lat"] + t * (b["lat"] - a["lat"])
    lng = a["lng"] + t * (b["lng"] - a["lng"])
    alt = None
    if a["alt"] is not None and b["alt"] is not None:
        alt = a["alt"] + t * (b["alt"] - a["alt"])
    return round(lat, 7), round(lng, 7), (round(alt, 1) if alt is not None else None)


def main():
    bike = load_course("bike")
    run = load_course("run")

    # --- Bike aid stations (from athlete guide) ---
    # Course is 2 laps; the same physical station is hit twice on different km marks.
    # We group by "name" so the frontend can render one marker but show "passed at km X and km Y".
    bike_stations = [
        {"name": "B1 — Wangetti Road",     "km_marks": [18.0, 91.0]},
        {"name": "B2 — Mowbray River Rd",  "km_marks": [41.2, 110.5]},
        {"name": "B3 — Thala Beach",       "km_marks": [49.6, 123.9]},
        {"name": "B4 — Ellis Beach",       "km_marks": [71.9, 146.2]},
        {"name": "B5 — Smithfield Village","km_marks": [160.0]},
    ]
    bike_personal_needs = [
        {"name": "Personal Needs — Buchan St", "km_marks": [75.0, 150.0]},
    ]

    # --- Run aid stations ---
    run_stations = [
        {"name": "R1", "km_marks": [0.4, 2.8, 10.9, 13.3, 21.3, 23.8, 31.8, 34.3]},
        {"name": "R2", "km_marks": [1.2, 11.7, 22.2, 32.7]},
        {"name": "R3 (full station)", "km_marks": [4.3, 9.1, 14.8, 19.6, 25.3, 30.1, 35.7, 40.5]},
        {"name": "R4", "km_marks": [7.4, 17.8, 28.4, 38.4]},
    ]
    run_personal_needs = [
        {"name": "Personal Needs — Lily St", "km_marks": [5.8, 16.3, 26.8, 37.3]},
    ]

    def hydrate(stations, points):
        out = []
        for s in stations:
            primary_km = s["km_marks"][0]
            lat, lng, alt = coords_at_distance(points, primary_km * 1000)
            out.append({
                "name": s["name"],
                "km_marks": s["km_marks"],
                "primary_km": primary_km,
                "lat": lat,
                "lng": lng,
                "alt": alt,
            })
        return out

    course_info = {
        "race_name": "2026 IRONMAN Cairns",
        "race_date": "2026-06-14",  # approximate, mid-June Sunday
        "host_location": "Palm Cove → Port Douglas → Cairns CBD",
        "swim": {
            "distance_km": 3.8,
            "laps": 2,
            "venue": "Palm Cove, Williams Esplanade",
            "direction": "anti-clockwise",
            "cutoff_h": 2.333,
            "cutoff_label": "2h 20m from individual swim start",
        },
        "bike": {
            "distance_km": 180.0,
            "laps": 2,
            "course": "Palm Cove → Captain Cook Highway → Rex Lookout → Craiglie (Trezise Rd / Spring Creek Rd) → return → Yorkeys Knob → Cairns CBD",
            "cutoff_h": 10.0,
            "cutoff_label": "10h cumulative from swim start",
            "aid_stations": hydrate(bike_stations, bike["points"]),
            "personal_needs": hydrate(bike_personal_needs, bike["points"]),
            "landmarks": [
                {"name": "Rex Lookout", "note": "Iconic climb on Captain Cook Highway"},
                {"name": "Craiglie", "note": "Northern turnaround"},
            ],
        },
        "run": {
            "distance_km": 42.2,
            "laps": 4,
            "venue": "Cairns Esplanade",
            "direction": "anti-clockwise",
            "cutoff_h": 16.5,
            "cutoff_label": "16h 30m total from swim start",
            "aid_stations": hydrate(run_stations, run["points"]),
            "personal_needs": hydrate(run_personal_needs, run["points"]),
        },
        "wetsuit_rules": {
            "mandatory_at_or_below_c": 16.0,
            "optional_range_c": [16.0, 26.0],
            "likely_banned_above_c": 26.1,
            "note": "Aus Triathlon granted higher cutoff for this race; official ruling posted race morning.",
        },
        "official_conditions_baseline": {
            "air_temp_high_c": 28,
            "air_temp_low_c": 18,
            "water_temp_avg_c": 23,
            "source": "IM Cairns event website",
        },
    }

    out_path = PROCESSED / "course_info.json"
    with out_path.open("w") as f:
        json.dump(course_info, f, indent=2)
    print(f"Wrote {out_path}")
    print(f"  Bike aid stations: {len(course_info['bike']['aid_stations'])}")
    print(f"  Run aid stations:  {len(course_info['run']['aid_stations'])}")


if __name__ == "__main__":
    main()
