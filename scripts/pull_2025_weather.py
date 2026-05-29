"""
Pull race-day weather for the 2025 IRONMAN Cairns and compare against the
2018-2024 historical median.

Goal: answer "was 2025 a normal/easy/hard day?" so we can contextualize Sam's
actual 4:27:14 bike split against the model's prediction.

Open-Meteo archive API (free, no key).
"""

import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "processed"

RACE_DATE = "2025-06-15"  # Sunday, mid-June (IM Cairns 2025)
WINDOW_DAYS = 2            # pull race day ± 2 days for context

# Coordinate set matching the existing pull_weather.py output schema.
LOCATIONS = {
    "swim_bike_start": (-16.7466, 145.6713),  # Palm Cove / Yorkeys Knob area
    "north_turnaround": (-16.55, 145.46),     # Captain Cook Hwy / Craiglie
    "run_cbd":          (-16.92, 145.78),     # Cairns CBD esplanade
}

VARS = [
    "temperature_2m", "relative_humidity_2m",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
    "precipitation", "cloud_cover", "dew_point_2m", "surface_pressure",
]

# Race-window in local time. Sam's race ran 07:50 → 16:36; round to 05:00-17:00.
RACE_WINDOW_HOURS = list(range(5, 18))

# Cardinal-bin mapping
CARDINALS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def fetch_open_meteo(lat, lng, start_date, end_date):
    qs = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lng,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(VARS),
        "timezone": "Australia/Brisbane",
        "windspeed_unit": "kmh",
    })
    url = f"https://archive-api.open-meteo.com/v1/archive?{qs}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def race_window_summary(hourly, race_date):
    """Compute race-day-only statistics (05:00-17:00 AEST)."""
    times = hourly["time"]
    rows = []
    for i, t in enumerate(times):
        # t looks like "2025-06-15T07:00"
        date_part, time_part = t.split("T")
        hour = int(time_part.split(":")[0])
        if date_part != race_date or hour not in RACE_WINDOW_HOURS:
            continue
        rows.append({k: hourly[k][i] for k in VARS if k in hourly})
    if not rows:
        return None

    speeds = [r["wind_speed_10m"] for r in rows if r["wind_speed_10m"] is not None]
    gusts  = [r["wind_gusts_10m"] for r in rows if r["wind_gusts_10m"] is not None]
    temps  = [r["temperature_2m"] for r in rows if r["temperature_2m"] is not None]
    humid  = [r["relative_humidity_2m"] for r in rows if r["relative_humidity_2m"] is not None]
    precip = [r["precipitation"] for r in rows if r["precipitation"] is not None]
    dirs   = [r["wind_direction_10m"] for r in rows if r["wind_direction_10m"] is not None]

    # Vector mean of wind direction (weight by speed)
    sx = sy = 0.0
    for r in rows:
        d = r["wind_direction_10m"]; s = r["wind_speed_10m"]
        if d is None or s is None:
            continue
        rad = math.radians(d)
        sx += s * math.sin(rad)
        sy += s * math.cos(rad)
    vec_mean_deg = (math.degrees(math.atan2(sx, sy)) + 360) % 360 if (sx or sy) else None

    # Modal cardinal bin
    bin_counts = [0] * 16
    for d in dirs:
        if d is None:
            continue
        idx = int(round(d / 22.5)) % 16
        bin_counts[idx] += 1
    modal_cardinal = CARDINALS[bin_counts.index(max(bin_counts))] if dirs else None

    return {
        "hour_count": len(rows),
        "wind_speed_kmh_mean": round(sum(speeds) / len(speeds), 2) if speeds else None,
        "wind_speed_kmh_max": round(max(speeds), 2) if speeds else None,
        "wind_gust_kmh_max": round(max(gusts), 2) if gusts else None,
        "wind_dir_vector_mean_deg": round(vec_mean_deg, 1) if vec_mean_deg is not None else None,
        "wind_dir_modal_cardinal": modal_cardinal,
        "temp_c_min": round(min(temps), 2) if temps else None,
        "temp_c_max": round(max(temps), 2) if temps else None,
        "temp_c_mean": round(sum(temps) / len(temps), 2) if temps else None,
        "humidity_pct_mean": round(sum(humid) / len(humid), 1) if humid else None,
        "precip_mm_total": round(sum(precip), 2) if precip else None,
        "precip_hours": sum(1 for p in precip if p and p > 0.05),
    }


def main():
    PROCESSED.mkdir(exist_ok=True)

    # Pull all locations, ±2 days
    from datetime import datetime, timedelta
    race_dt = datetime.fromisoformat(RACE_DATE)
    start_date = (race_dt - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    end_date   = (race_dt + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")

    raw = {}
    summary = {}
    for loc, (lat, lng) in LOCATIONS.items():
        print(f"Pulling {loc} ({lat}, {lng}) for {start_date} → {end_date}...")
        data = fetch_open_meteo(lat, lng, start_date, end_date)
        raw[loc] = data
        summary[loc] = race_window_summary(data["hourly"], RACE_DATE)

    out = {
        "race_date": RACE_DATE,
        "source": "Open-Meteo archive (ERA5 reanalysis)",
        "race_window_hours_local": [RACE_WINDOW_HOURS[0], RACE_WINDOW_HOURS[-1] + 1],
        "by_location": summary,
        "raw_hourly_swim_bike_start": raw["swim_bike_start"]["hourly"],
    }
    out_path = PROCESSED / "weather_hourly_2025.json"
    with out_path.open("w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {out_path}")

    # Comparison vs existing median (from weather_seed_defaults.json)
    try:
        with (PROCESSED / "weather_seed_defaults.json").open() as f:
            median = json.load(f)
    except FileNotFoundError:
        median = None

    s = summary["swim_bike_start"]
    print()
    print("=" * 70)
    print(f"2025 race-day conditions @ swim/bike start (05:00–17:00 AEST):")
    print("=" * 70)
    print(f"  Wind mean:      {s['wind_speed_kmh_mean']:5.1f} km/h")
    print(f"  Wind max:       {s['wind_speed_kmh_max']:5.1f} km/h")
    print(f"  Gust max:       {s['wind_gust_kmh_max']:5.1f} km/h")
    print(f"  Wind direction: {s['wind_dir_modal_cardinal']} (vector mean {s['wind_dir_vector_mean_deg']}°)")
    print(f"  Temperature:    {s['temp_c_min']:.1f} – {s['temp_c_max']:.1f} °C "
          f"(mean {s['temp_c_mean']:.1f})")
    print(f"  Humidity:       {s['humidity_pct_mean']:.0f}%")
    print(f"  Precipitation:  {s['precip_mm_total']:.2f} mm  ({s['precip_hours']} hours with rain)")

    if median:
        print()
        print("Compared to 2018-2024 historical median:")
        print(f"  Wind:        2025 {s['wind_speed_kmh_mean']:5.1f}  vs  median {median['wind_speed_kmh_median']:5.1f} km/h")
        print(f"  Gusts:       2025 {s['wind_gust_kmh_max']:5.1f}  vs  median-of-max {median['wind_gust_kmh_median_of_max']:5.1f} km/h")
        print(f"  Temp high:   2025 {s['temp_c_max']:5.1f}  vs  median {median['temp_c_high_median']:5.1f} °C")
        print(f"  Temp low:    2025 {s['temp_c_min']:5.1f}  vs  median {median['temp_c_low_median']:5.1f} °C")
        print(f"  Direction:   2025 {s['wind_dir_vector_mean_deg']:5.0f}° vs median {median['wind_direction_vector_mean_deg']:5.0f}°")

        # Verdict
        wind_delta = s["wind_speed_kmh_mean"] - median["wind_speed_kmh_median"]
        if abs(wind_delta) < 2.5:
            verdict = "TYPICAL day — within ±2.5 km/h of median wind"
        elif wind_delta < 0:
            verdict = f"CALMER than typical ({abs(wind_delta):.1f} km/h less wind)"
        else:
            verdict = f"WINDIER than typical (+{wind_delta:.1f} km/h above median)"
        print()
        print(f"Verdict: {verdict}")


if __name__ == "__main__":
    main()
