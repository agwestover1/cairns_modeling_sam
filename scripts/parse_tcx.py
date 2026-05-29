"""
Parse the three Ironman Cairns TCX course files into clean JSON
with derived per-segment metrics (gradient, bearing) and summary stats.

Usage:
    python3 scripts/parse_tcx.py
"""

import json
import math
from pathlib import Path

import defusedxml.ElementTree as ET

NS = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "processed"

EARTH_R_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, degrees from true north [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def parse_course(tcx_path: Path) -> dict:
    tree = ET.parse(tcx_path)
    root = tree.getroot()
    course = root.find(".//tcx:Course", NS)
    name = course.findtext("tcx:Name", default=tcx_path.stem, namespaces=NS).strip()

    raw_pts = []
    for tp in course.findall(".//tcx:Trackpoint", NS):
        pos = tp.find("tcx:Position", NS)
        if pos is None:
            continue
        lat = float(pos.findtext("tcx:LatitudeDegrees", namespaces=NS))
        lon = float(pos.findtext("tcx:LongitudeDegrees", namespaces=NS))
        alt = tp.findtext("tcx:AltitudeMeters", namespaces=NS)
        dist = tp.findtext("tcx:DistanceMeters", namespaces=NS)
        raw_pts.append(
            {
                "lat": round(lat, 7),
                "lng": round(lon, 7),
                "alt": round(float(alt), 2) if alt is not None else None,
                "dist_m": round(float(dist), 2) if dist is not None else None,
            }
        )

    # Derived per-segment metrics
    pts = []
    elev_gain = 0.0
    elev_loss = 0.0
    min_alt = math.inf
    max_alt = -math.inf
    min_lat = min_lng = math.inf
    max_lat = max_lng = -math.inf

    for i, p in enumerate(raw_pts):
        # Use file-provided cumulative distance if present, else compute
        if p["dist_m"] is None and i > 0:
            prev = raw_pts[i - 1]
            seg = haversine_m(prev["lat"], prev["lng"], p["lat"], p["lng"])
            p["dist_m"] = round((raw_pts[i - 1].get("dist_m") or 0) + seg, 2)
        if i == 0:
            grade = 0.0
            bearing = 0.0
        else:
            prev = raw_pts[i - 1]
            seg_d = (p["dist_m"] or 0) - (prev["dist_m"] or 0)
            d_alt = (p["alt"] or 0) - (prev["alt"] or 0)
            grade = round(100 * d_alt / seg_d, 3) if seg_d > 1 else 0.0
            bearing = round(bearing_deg(prev["lat"], prev["lng"], p["lat"], p["lng"]), 1)
            if d_alt > 0:
                elev_gain += d_alt
            else:
                elev_loss += -d_alt

        if p["alt"] is not None:
            min_alt = min(min_alt, p["alt"])
            max_alt = max(max_alt, p["alt"])
        min_lat = min(min_lat, p["lat"])
        max_lat = max(max_lat, p["lat"])
        min_lng = min(min_lng, p["lng"])
        max_lng = max(max_lng, p["lng"])

        pts.append(
            {
                "lat": p["lat"],
                "lng": p["lng"],
                "alt": p["alt"],
                "dist_m": p["dist_m"],
                "grade_pct": grade,
                "bearing_deg": bearing,
            }
        )

    total_dist = pts[-1]["dist_m"] if pts else 0

    return {
        "name": name,
        "source_file": tcx_path.name,
        "summary": {
            "point_count": len(pts),
            "distance_m": round(total_dist, 2),
            "distance_km": round(total_dist / 1000, 3),
            "elev_gain_m": round(elev_gain, 1),
            "elev_loss_m": round(elev_loss, 1),
            "min_alt_m": round(min_alt, 2) if min_alt != math.inf else None,
            "max_alt_m": round(max_alt, 2) if max_alt != -math.inf else None,
            "bbox": {
                "min_lat": round(min_lat, 7),
                "min_lng": round(min_lng, 7),
                "max_lat": round(max_lat, 7),
                "max_lng": round(max_lng, 7),
            },
        },
        "points": pts,
    }


def main():
    OUT_DIR.mkdir(exist_ok=True)
    courses = {}
    for tcx in sorted(DATA_DIR.glob("*.tcx")):
        slug = tcx.stem  # bike_course, run_course, swim_course
        data = parse_course(tcx)
        out_path = OUT_DIR / f"{slug}.json"
        with out_path.open("w") as f:
            json.dump(data, f, separators=(",", ":"))
        courses[slug] = data["summary"]
        s = data["summary"]
        print(
            f"{slug:14s}  {s['distance_km']:>7.2f} km  "
            f"{s['point_count']:>5d} pts  "
            f"+{s['elev_gain_m']:>6.0f} / -{s['elev_loss_m']:>6.0f} m  "
            f"alt [{s['min_alt_m']:.1f}, {s['max_alt_m']:.1f}]"
        )

    # Combined index
    with (OUT_DIR / "index.json").open("w") as f:
        json.dump(
            {slug: {"summary": s, "file": f"{slug}.json"} for slug, s in courses.items()},
            f,
            indent=2,
        )
    print(f"\nWrote {len(courses)} course JSON files + index.json to {OUT_DIR}/")


if __name__ == "__main__":
    main()
