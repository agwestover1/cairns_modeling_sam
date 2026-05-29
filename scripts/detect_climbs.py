"""
Identify notable climbs on each course.

Algorithm:
1. Smooth elevation with a rolling-mean window (~150m) to remove GPS jitter.
2. Walk the course; track active climb segments where smoothed grade ≥ start_threshold.
3. End a climb when grade stays below end_threshold for at least flat_tolerance_m.
4. Apply minimum length and minimum gain filters to drop nonsense.
5. Score climbs by FIETS index = (length_km * avg_grade^2 / 10) — roughly Tour-de-France style category.

Output: processed/climbs.json — keyed by discipline.
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "processed"


def smooth_elevation(points, window_m=150.0):
    """Rolling-mean smoothing over a fixed *distance* window (not point count)."""
    smoothed = []
    n = len(points)
    j_lo = 0
    j_hi = 0
    half = window_m / 2
    for i in range(n):
        d = points[i]["dist_m"]
        while j_lo < n and points[j_lo]["dist_m"] < d - half:
            j_lo += 1
        while j_hi < n and points[j_hi]["dist_m"] < d + half:
            j_hi += 1
        window = [points[k]["alt"] for k in range(j_lo, j_hi) if points[k]["alt"] is not None]
        smoothed.append(sum(window) / len(window) if window else (points[i]["alt"] or 0))
    return smoothed


def fiets_category(length_km, avg_grade_pct, peak_alt_m):
    """Loose Tour-de-France-style climb categorization. Cairns courses are flat to rolling,
    so most climbs will land Cat 4 or below ("uncategorized")."""
    if length_km < 0.3:
        return "—"
    score = (length_km * (avg_grade_pct ** 2)) / 10
    if score >= 8: return "HC"
    if score >= 5: return "1"
    if score >= 3: return "2"
    if score >= 1.5: return "3"
    if score >= 0.4: return "4"
    return "—"


def detect_climbs(
    course,
    *,
    start_grade_pct=1.5,
    end_grade_pct=0.3,
    flat_tolerance_m=120,
    min_length_m=400,
    min_gain_m=15,
    smooth_window_m=150,
):
    points = course["points"]
    if len(points) < 2:
        return []

    smoothed = smooth_elevation(points, smooth_window_m)

    # Per-segment smoothed grade
    seg_grade = [0.0]
    for i in range(1, len(points)):
        seg_d = points[i]["dist_m"] - points[i - 1]["dist_m"]
        if seg_d <= 0.1:
            seg_grade.append(0.0)
            continue
        d_alt = smoothed[i] - smoothed[i - 1]
        seg_grade.append(100 * d_alt / seg_d)

    climbs = []
    in_climb = False
    climb_start = 0
    flat_run = 0.0

    for i in range(1, len(points)):
        g = seg_grade[i]
        seg_d = points[i]["dist_m"] - points[i - 1]["dist_m"]
        if not in_climb:
            if g >= start_grade_pct:
                in_climb = True
                climb_start = i - 1
                flat_run = 0
        else:
            if g < end_grade_pct:
                flat_run += seg_d
                if flat_run >= flat_tolerance_m:
                    end_idx = _find_peak(points, smoothed, climb_start, i)
                    climbs.append(_summarize(points, smoothed, seg_grade, climb_start, end_idx))
                    in_climb = False
            else:
                flat_run = 0

    if in_climb:
        end_idx = _find_peak(points, smoothed, climb_start, len(points) - 1)
        climbs.append(_summarize(points, smoothed, seg_grade, climb_start, end_idx))

    # Filter & sort
    climbs = [c for c in climbs if c["length_m"] >= min_length_m and c["elev_gain_m"] >= min_gain_m]
    climbs.sort(key=lambda c: c["start_km"])

    # Number them
    for i, c in enumerate(climbs, 1):
        c["id"] = i

    return climbs


def _find_peak(points, smoothed, start_idx, end_idx):
    """Within [start_idx, end_idx], return the index of the highest smoothed elevation."""
    peak = start_idx
    for k in range(start_idx, end_idx + 1):
        if smoothed[k] > smoothed[peak]:
            peak = k
    return peak


def _summarize(points, smoothed, seg_grade, start_idx, end_idx):
    s = points[start_idx]
    e = points[end_idx]
    length_m = e["dist_m"] - s["dist_m"]
    elev_gain = smoothed[end_idx] - smoothed[start_idx]
    avg_grade = 100 * elev_gain / length_m if length_m > 1 else 0
    max_grade_50m = _max_window_grade(points, smoothed, start_idx, end_idx, window_m=50)

    shape = _shape_metrics(points, smoothed, seg_grade, start_idx, end_idx, elev_gain)

    return {
        "start_idx": start_idx,
        "end_idx": end_idx,
        "start_km": round(s["dist_m"] / 1000, 3),
        "end_km": round(e["dist_m"] / 1000, 3),
        "length_m": round(length_m, 1),
        "elev_gain_m": round(elev_gain, 1),
        "start_alt_m": round(smoothed[start_idx], 1),
        "peak_alt_m": round(smoothed[end_idx], 1),
        "avg_grade_pct": round(avg_grade, 2),
        "max_grade_pct": round(max_grade_50m, 2),
        "start": {"lat": s["lat"], "lng": s["lng"]},
        "peak": {"lat": e["lat"], "lng": e["lng"]},
        "category": fiets_category(length_m / 1000, avg_grade, smoothed[end_idx]),
        **shape,
    }


def _shape_metrics(points, smoothed, seg_grade, start_idx, end_idx, elev_gain):
    """Heuristics for whether a climb is straight vs. winding, has a false plateau,
    and how gain is distributed across thirds."""
    bearings = [points[k]["bearing_deg"] for k in range(start_idx + 1, end_idx + 1)
                if points[k].get("bearing_deg") is not None]

    bearing_std = _circular_std_deg(bearings) if bearings else 0.0
    bearing_turning = _bearing_total_turning_deg(bearings) if bearings else 0.0

    # Gain by thirds of total length
    length = points[end_idx]["dist_m"] - points[start_idx]["dist_m"]
    t1 = points[start_idx]["dist_m"] + length / 3
    t2 = points[start_idx]["dist_m"] + 2 * length / 3
    gain_thirds = [0.0, 0.0, 0.0]
    for k in range(start_idx + 1, end_idx + 1):
        d_alt = smoothed[k] - smoothed[k - 1]
        if d_alt <= 0:
            continue
        dist = points[k]["dist_m"]
        idx = 0 if dist <= t1 else (1 if dist <= t2 else 2)
        gain_thirds[idx] += d_alt
    total_pos = sum(gain_thirds) or 1.0
    gain_thirds_pct = [round(100 * g / total_pos, 0) for g in gain_thirds]

    # False plateau: locations within the climb where segment grade drops below 0.5%
    # for at least 50m of cumulative distance.
    fp_count = 0
    flat_run = 0.0
    in_fp = False
    for k in range(start_idx + 1, end_idx + 1):
        seg_d = points[k]["dist_m"] - points[k - 1]["dist_m"]
        g = seg_grade[k] if k < len(seg_grade) else 0
        if g < 0.5:
            flat_run += seg_d
            if flat_run >= 50 and not in_fp:
                fp_count += 1
                in_fp = True
        else:
            flat_run = 0
            in_fp = False

    # Label
    if bearing_std < 5:
        curvature_label = "straight"
    elif bearing_std < 15:
        curvature_label = "slightly curving"
    else:
        curvature_label = "winding"

    front, mid, back = gain_thirds_pct
    if max(gain_thirds_pct) - min(gain_thirds_pct) < 12:
        loading_label = "consistent grade"
    elif front == max(gain_thirds_pct):
        loading_label = "front-loaded (steepest at start)"
    elif back == max(gain_thirds_pct):
        loading_label = "back-loaded (steepest at top)"
    else:
        loading_label = "middle-pinch"

    parts = [curvature_label, loading_label]
    if fp_count > 0:
        parts.append(f"{fp_count} false plateau{'s' if fp_count != 1 else ''}")

    return {
        "bearing_std_deg": round(bearing_std, 1),
        "bearing_total_turning_deg": round(bearing_turning, 1),
        "gain_thirds_pct": gain_thirds_pct,
        "false_plateaus": fp_count,
        "shape_label": " · ".join(parts),
    }


def _circular_std_deg(bearings):
    """Circular standard deviation of a list of bearings in degrees."""
    if not bearings:
        return 0.0
    sin_sum = sum(math.sin(math.radians(b)) for b in bearings)
    cos_sum = sum(math.cos(math.radians(b)) for b in bearings)
    n = len(bearings)
    r = math.sqrt(sin_sum * sin_sum + cos_sum * cos_sum) / n
    # Clamp to avoid math domain errors when bearings are essentially identical
    r = min(max(r, 1e-9), 1.0)
    return math.degrees(math.sqrt(-2 * math.log(r)))


def _bearing_total_turning_deg(bearings):
    """Total angular turning across the climb — sum of consecutive absolute
    bearing changes (each wrap-corrected to ±180°). High value = winding road."""
    if len(bearings) < 2:
        return 0.0
    total = 0.0
    for i in range(len(bearings) - 1):
        d = ((bearings[i + 1] - bearings[i] + 540) % 360) - 180
        total += abs(d)
    return total


def _max_window_grade(points, smoothed, start_idx, end_idx, window_m=50):
    """Two-pointer sliding window. j only advances forward; O(N) per climb."""
    best = 0.0
    j = start_idx + 1
    for i in range(start_idx, end_idx + 1):
        if j <= i:
            j = i + 1
        while j <= end_idx and points[j]["dist_m"] - points[i]["dist_m"] < window_m:
            j += 1
        if j > end_idx:
            break
        d = points[j]["dist_m"] - points[i]["dist_m"]
        if d > 0:
            grade = 100 * (smoothed[j] - smoothed[i]) / d
            if grade > best:
                best = grade
    return best


def main():
    out = {}
    for slug in ("swim", "bike", "run"):
        with (PROCESSED / f"{slug}_course.json").open() as f:
            course = json.load(f)
        climbs = detect_climbs(course)
        out[slug] = climbs
        print(f"{slug:>5s}: {len(climbs)} climb(s)")
        for c in climbs[:8]:
            print(
                f"  #{c['id']:>2d}  km {c['start_km']:>6.2f} → {c['end_km']:>6.2f}  "
                f"len {c['length_m']:>6.0f}m  gain {c['elev_gain_m']:>5.1f}m  "
                f"avg {c['avg_grade_pct']:>4.1f}%  max {c['max_grade_pct']:>4.1f}%  cat {c['category']}"
            )

    with (PROCESSED / "climbs.json").open("w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {PROCESSED / 'climbs.json'}")


if __name__ == "__main__":
    main()
