"""
pull_weather.py
================

Pull historical hourly weather for Ironman Cairns race days and a +/-3 day
window around each race, then derive race-window summary statistics and a
single "seed defaults" scenario used to populate the race-day simulator.

API
---
Source: Open-Meteo Historical Weather (ERA5-based reanalysis).
  https://archive-api.open-meteo.com/v1/archive
Free, no key. Hourly variables documented at:
  https://open-meteo.com/en/docs/historical-weather-api

Example call (GET):
  https://archive-api.open-meteo.com/v1/archive
    ?latitude=-16.7466
    &longitude=145.6713
    &start_date=2024-06-13
    &end_date=2024-06-19
    &hourly=temperature_2m,relative_humidity_2m,dew_point_2m,
            wind_speed_10m,wind_direction_10m,wind_gusts_10m,
            precipitation,cloud_cover,surface_pressure
    &timezone=Australia/Brisbane
    &wind_speed_unit=kmh
    &temperature_unit=celsius
    &precipitation_unit=mm

Locations sampled
-----------------
  swim_bike_start  : -16.7466, 145.6713  (Cairns Esplanade / Yorkeys Knob)
  bike_north_turn  : -16.5500, 145.4600  (Captain Cook Hwy turnaround)
  run_cbd          : -16.9200, 145.7800  (Cairns CBD run course)

Race dates (historical Sunday in mid-June)
------------------------------------------
  2018-06-10, 2019-06-09, 2021-06-06, 2022-06-19, 2023-06-18, 2024-06-16
  (2020 cancelled, COVID. 2026 expected ~2026-06-14.)

Outputs (written to ../processed/)
----------------------------------
  weather_hourly_<year>.json          one per year; all three locations, full window
  weather_race_window_summary.json    per-year + cross-year stats during the race
                                       window (05:00-17:00 local AEST/UTC+10)
  weather_seed_defaults.json          single best-guess scenario for simulator
                                       initial sliders, plus IM-published values

Dependencies: stdlib only (urllib, json, math, datetime, pathlib).
"""

from __future__ import annotations

import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

LOCATIONS = {
    "swim_bike_start": (-16.7466, 145.6713),
    "bike_north_turn": (-16.5500, 145.4600),
    "run_cbd":         (-16.9200, 145.7800),
}

# Race dates: 2018, 2019, 2021, 2022, 2023, 2024 (Sundays mid-June).
RACE_DATES = {
    2018: date(2018, 6, 10),
    2019: date(2019, 6,  9),
    2021: date(2021, 6,  6),
    2022: date(2022, 6, 19),
    2023: date(2023, 6, 18),
    2024: date(2024, 6, 16),
}

HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "precipitation",
    "cloud_cover",
    "surface_pressure",
]

# Race window in local AEST (UTC+10, no DST in Queensland).
RACE_HOUR_START = 5   # 05:00 local
RACE_HOUR_END   = 17  # 17:00 local (inclusive of 16:00, exclusive of 17:00)
LOCAL_TZ        = "Australia/Brisbane"

WINDOW_DAYS = 3  # +/- days

# IM-published "official" environmental values from the race page.
IM_PUBLISHED = {
    "air_temp_high_c": 28.0,
    "air_temp_low_c":  18.0,
    "water_temp_avg_c": 23.0,
    "source_note": "Ironman Cairns athlete guide / event page",
}

OUT_DIR = Path(__file__).resolve().parent.parent / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str, retries: int = 3, sleep_s: float = 1.5) -> dict:
    """GET a URL and parse JSON. Light retry on transient errors."""
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "im-cairns-sim/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(sleep_s * (attempt + 1))
    raise RuntimeError(f"Failed after {retries} attempts: {url} :: {last_err}")


def fetch_archive(lat: float, lon: float, start: date, end: date) -> dict:
    params = {
        "latitude": f"{lat}",
        "longitude": f"{lon}",
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "hourly":     ",".join(HOURLY_VARS),
        "timezone":   LOCAL_TZ,
        "wind_speed_unit":   "kmh",
        "temperature_unit":  "celsius",
        "precipitation_unit": "mm",
    }
    return fetch_json(f"{BASE_URL}?{urllib.parse.urlencode(params)}")


def cardinal_bin(deg: float) -> str:
    bins = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    # Center each bin on its compass point; 22.5 deg half-width.
    idx = int(((deg % 360) + 22.5) // 45) % 8
    return bins[idx]


def vector_mean_direction(speeds: list[float], dirs_deg: list[float]) -> tuple[float, float]:
    """Return (mean_speed, vector_mean_direction_deg) using sine/cosine averaging."""
    sx = sy = 0.0
    n = 0
    for s, d in zip(speeds, dirs_deg):
        if s is None or d is None:
            continue
        rad = math.radians(d)
        sx += s * math.sin(rad)
        sy += s * math.cos(rad)
        n += 1
    if n == 0:
        return (float("nan"), float("nan"))
    mean_s = math.hypot(sx, sy) / n
    mean_dir = (math.degrees(math.atan2(sx, sy)) + 360.0) % 360.0
    raw_mean_s = statistics.fmean([s for s in speeds if s is not None])
    return (raw_mean_s, mean_dir)


def safe_stats(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0, "min": None, "max": None, "mean": None, "median": None}
    return {
        "n": len(xs),
        "min": min(xs),
        "max": max(xs),
        "mean": statistics.fmean(xs),
        "median": statistics.median(xs),
    }


def race_window_indices(times: list[str], race_day_iso: str) -> list[int]:
    """Return hourly indices whose local-time stamp falls inside the race window."""
    idxs = []
    for i, t in enumerate(times):
        # Open-Meteo returns "YYYY-MM-DDTHH:MM" in the requested timezone.
        d, hh = t[:10], int(t[11:13])
        if d == race_day_iso and RACE_HOUR_START <= hh < RACE_HOUR_END:
            idxs.append(i)
    return idxs


def pick(series: list, idxs: list[int]) -> list:
    return [series[i] for i in idxs] if series else []


# ---------------------------------------------------------------------------
# Pull
# ---------------------------------------------------------------------------

def pull_all() -> dict:
    """Pull per-year hourly data for all three locations; write per-year files."""
    by_year: dict[int, dict] = {}
    for year, rd in RACE_DATES.items():
        start = rd - timedelta(days=WINDOW_DAYS)
        end   = rd + timedelta(days=WINDOW_DAYS)
        print(f"[{year}] race={rd}  window={start}..{end}")

        per_loc = {}
        for name, (lat, lon) in LOCATIONS.items():
            print(f"  fetch {name} ({lat},{lon}) ...", end=" ", flush=True)
            data = fetch_archive(lat, lon, start, end)
            per_loc[name] = data
            print("ok")
            time.sleep(0.4)  # polite pacing

        rec = {
            "year": year,
            "race_date": rd.isoformat(),
            "window_start": start.isoformat(),
            "window_end":   end.isoformat(),
            "timezone": LOCAL_TZ,
            "locations": per_loc,
        }
        out_path = OUT_DIR / f"weather_hourly_{year}.json"
        out_path.write_text(json.dumps(rec, indent=2))
        print(f"  wrote {out_path}")
        by_year[year] = rec
    return by_year


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarize_race_day(year: int, rec: dict) -> dict:
    rd_iso = rec["race_date"]
    summary_locs = {}

    for loc_name, blob in rec["locations"].items():
        hourly = blob.get("hourly", {})
        times = hourly.get("time", [])
        idxs = race_window_indices(times, rd_iso)

        temp = pick(hourly.get("temperature_2m", []), idxs)
        rh   = pick(hourly.get("relative_humidity_2m", []), idxs)
        dp   = pick(hourly.get("dew_point_2m", []), idxs)
        ws   = pick(hourly.get("wind_speed_10m", []), idxs)
        wd   = pick(hourly.get("wind_direction_10m", []), idxs)
        wg   = pick(hourly.get("wind_gusts_10m", []), idxs)
        pr   = pick(hourly.get("precipitation", []), idxs)
        cc   = pick(hourly.get("cloud_cover", []), idxs)
        sp   = pick(hourly.get("surface_pressure", []), idxs)

        mean_speed, vec_dir = vector_mean_direction(ws, wd)
        # Cardinal histogram (count hours weighted by 1).
        bin_counts: dict[str, int] = {}
        for d in wd:
            if d is None:
                continue
            bin_counts[cardinal_bin(d)] = bin_counts.get(cardinal_bin(d), 0) + 1
        modal_card = max(bin_counts.items(), key=lambda kv: kv[1])[0] if bin_counts else None

        rain_hours = sum(1 for v in pr if v is not None and v >= 0.1)
        precip_total = sum(v for v in pr if v is not None)

        summary_locs[loc_name] = {
            "race_window_local": f"{RACE_HOUR_START:02d}:00-{RACE_HOUR_END:02d}:00 {LOCAL_TZ}",
            "n_hours": len(idxs),
            "temperature_c":      safe_stats(temp),
            "humidity_pct":       safe_stats(rh),
            "dew_point_c":        safe_stats(dp),
            "wind_speed_kmh":     safe_stats(ws),
            "wind_gust_kmh":      safe_stats(wg),
            "wind_vector_mean_speed_kmh": mean_speed,
            "wind_vector_mean_direction_deg": vec_dir,
            "wind_vector_mean_cardinal": cardinal_bin(vec_dir) if not math.isnan(vec_dir) else None,
            "wind_cardinal_hours": bin_counts,
            "wind_modal_cardinal": modal_card,
            "cloud_cover_pct":    safe_stats(cc),
            "surface_pressure_hpa": safe_stats(sp),
            "precip_mm_total":    precip_total,
            "precip_rain_hours":  rain_hours,
        }

    return {"year": year, "race_date": rd_iso, "locations": summary_locs}


def cross_year_aggregate(per_year: list[dict]) -> dict:
    """Aggregate across years using the swim_bike_start location as primary."""
    primary = "swim_bike_start"

    speeds, gusts, temps_min, temps_max, temps_mean = [], [], [], [], []
    rh_min, rh_max, rh_mean = [], [], []
    dirs_for_vec_speed, dirs_for_vec_dir = [], []
    precip_totals, rain_hours_list = [], []
    cardinal_hours: dict[str, int] = {}

    for y in per_year:
        loc = y["locations"][primary]
        if loc["wind_speed_kmh"]["mean"] is not None:
            speeds.append(loc["wind_speed_kmh"]["mean"])
        if loc["wind_gust_kmh"]["max"] is not None:
            gusts.append(loc["wind_gust_kmh"]["max"])
        if loc["temperature_c"]["min"] is not None:
            temps_min.append(loc["temperature_c"]["min"])
        if loc["temperature_c"]["max"] is not None:
            temps_max.append(loc["temperature_c"]["max"])
        if loc["temperature_c"]["mean"] is not None:
            temps_mean.append(loc["temperature_c"]["mean"])
        if loc["humidity_pct"]["min"] is not None:
            rh_min.append(loc["humidity_pct"]["min"])
        if loc["humidity_pct"]["max"] is not None:
            rh_max.append(loc["humidity_pct"]["max"])
        if loc["humidity_pct"]["mean"] is not None:
            rh_mean.append(loc["humidity_pct"]["mean"])
        if not math.isnan(loc["wind_vector_mean_speed_kmh"]) and not math.isnan(loc["wind_vector_mean_direction_deg"]):
            dirs_for_vec_speed.append(loc["wind_vector_mean_speed_kmh"])
            dirs_for_vec_dir.append(loc["wind_vector_mean_direction_deg"])
        precip_totals.append(loc["precip_mm_total"])
        rain_hours_list.append(loc["precip_rain_hours"])
        for k, v in loc["wind_cardinal_hours"].items():
            cardinal_hours[k] = cardinal_hours.get(k, 0) + v

    overall_mean_speed, overall_vec_dir = vector_mean_direction(dirs_for_vec_speed, dirs_for_vec_dir)
    modal_cardinal = max(cardinal_hours.items(), key=lambda kv: kv[1])[0] if cardinal_hours else None

    return {
        "primary_location": primary,
        "n_years": len(per_year),
        "wind_speed_kmh_mean_of_means": safe_stats(speeds),
        "wind_gust_kmh_max_of_max":     safe_stats(gusts),
        "temp_c_low":  safe_stats(temps_min),
        "temp_c_high": safe_stats(temps_max),
        "temp_c_mean": safe_stats(temps_mean),
        "humidity_pct_low":  safe_stats(rh_min),
        "humidity_pct_high": safe_stats(rh_max),
        "humidity_pct_mean": safe_stats(rh_mean),
        "precip_mm_total_per_year": precip_totals,
        "rain_hours_per_year":      rain_hours_list,
        "wind_overall_vector_mean_speed_kmh":    overall_mean_speed,
        "wind_overall_vector_mean_direction_deg": overall_vec_dir,
        "wind_overall_vector_mean_cardinal":      cardinal_bin(overall_vec_dir) if not math.isnan(overall_vec_dir) else None,
        "wind_cardinal_hours_all_years": cardinal_hours,
        "wind_modal_cardinal": modal_cardinal,
    }


def build_seed_defaults(cross: dict, per_year: list[dict]) -> dict:
    """Single best-guess scenario for simulator initial sliders."""
    # Median across the per-year race-window means/maxes from primary location.
    primary = cross["primary_location"]
    ws_means = [y["locations"][primary]["wind_speed_kmh"]["mean"] for y in per_year if y["locations"][primary]["wind_speed_kmh"]["mean"] is not None]
    gust_max = [y["locations"][primary]["wind_gust_kmh"]["max"] for y in per_year if y["locations"][primary]["wind_gust_kmh"]["max"] is not None]
    t_low  = [y["locations"][primary]["temperature_c"]["min"] for y in per_year if y["locations"][primary]["temperature_c"]["min"] is not None]
    t_high = [y["locations"][primary]["temperature_c"]["max"] for y in per_year if y["locations"][primary]["temperature_c"]["max"] is not None]
    t_mean = [y["locations"][primary]["temperature_c"]["mean"] for y in per_year if y["locations"][primary]["temperature_c"]["mean"] is not None]
    rh_mean = [y["locations"][primary]["humidity_pct"]["mean"] for y in per_year if y["locations"][primary]["humidity_pct"]["mean"] is not None]
    precip_totals = [y["locations"][primary]["precip_mm_total"] for y in per_year]

    return {
        "scenario": "median_race_day",
        "source": "Open-Meteo archive ERA5 reanalysis; race-window 05:00-17:00 AEST",
        "wind_speed_kmh_median":      statistics.median(ws_means) if ws_means else None,
        "wind_gust_kmh_median_of_max": statistics.median(gust_max) if gust_max else None,
        "wind_direction_modal_cardinal": cross["wind_modal_cardinal"],
        "wind_direction_vector_mean_deg": cross["wind_overall_vector_mean_direction_deg"],
        "wind_direction_vector_mean_cardinal": cross["wind_overall_vector_mean_cardinal"],
        "temp_c_low_median":    statistics.median(t_low)  if t_low  else None,
        "temp_c_high_median":   statistics.median(t_high) if t_high else None,
        "temp_c_mean_median":   statistics.median(t_mean) if t_mean else None,
        "humidity_pct_mean_median": statistics.median(rh_mean) if rh_mean else None,
        "precip_mm_total_median":   statistics.median(precip_totals) if precip_totals else None,
        "im_published": IM_PUBLISHED,
        "notes": (
            "Use vector-mean direction for prevailing wind on the coastal "
            "north-south bike course. Modal cardinal bin captures the most "
            "common hourly direction. Cairns has no DST (Australia/Brisbane)."
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    by_year = pull_all()

    per_year_summaries = []
    for year in sorted(by_year):
        per_year_summaries.append(summarize_race_day(year, by_year[year]))

    cross = cross_year_aggregate(per_year_summaries)

    summary_payload = {
        "race_window_local_hours": f"{RACE_HOUR_START:02d}:00-{RACE_HOUR_END:02d}:00 {LOCAL_TZ}",
        "primary_location": "swim_bike_start",
        "locations": LOCATIONS,
        "per_year": per_year_summaries,
        "cross_year": cross,
        "im_published": IM_PUBLISHED,
    }

    summary_path = OUT_DIR / "weather_race_window_summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2))
    print(f"wrote {summary_path}")

    seed = build_seed_defaults(cross, per_year_summaries)
    seed_path = OUT_DIR / "weather_seed_defaults.json"
    seed_path.write_text(json.dumps(seed, indent=2))
    print(f"wrote {seed_path}")


if __name__ == "__main__":
    main()
