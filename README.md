# Ironman Cairns — Race Strategy Simulator (Sam)

Interactive course explorer + race-day simulator for the 2026 IRONMAN Cairns
bike leg. Built for one athlete (Sam) and one race (Cairns). Designed to be
*forked* — not parameterized — when adapting to another race.

This README is the full playbook so future-us can rebuild from scratch for the
next course.

---

## What this does (current state)

- Parses the official Cairns TCX course files (swim, bike, run) into clean JSON
  with derived metrics (per-segment gradient, bearing, distance markers).
- Auto-detects notable climbs on the bike course (20 found; #3 is Rex Lookout)
  with shape descriptors (straight/winding, front/middle/back-loaded, false
  plateaus).
- Pulls historical race-window weather from Open-Meteo's ERA5 archive for the
  six most recent race days (2018–2024), summarizes prevailing conditions
  (SE trade winds, 17.5 km/h median, 23 °C, 66% RH).
- Runs a forward physics simulation: rider power + CdA + wind + temp → predicted
  bike split, segment-by-segment.
- 2D viewer (Leaflet + ESRI World Imagery) with route overlay, smoothing
  slider, elevation profile, climb cards, weather seed panel, simulator output.
- 3D viewer (Mapbox GL JS + terrain DEM) with pitch/bearing/exaggeration
  controls, live wind controls, animated particle overlay, four route-coloring
  modes (default / wind impact / climbs / climbs + wind), legend, and a flyable
  climb sidebar.

---

## Quick start

```bash
# From the project root
cd /Users/awestover/Documents/WSG/sam_cairns_project

# Rebuild the processed/ JSONs from the raw TCX (idempotent)
python3 scripts/parse_tcx.py
python3 scripts/extract_course_info.py
python3 scripts/detect_climbs.py
python3 scripts/physics_sim.py
# Weather pull is slow and only needs to run once per race
# python3 scripts/pull_weather.py

# Serve the frontend on http://localhost:8765/web/
python3 -m http.server 8765 &
open "http://localhost:8765/web/"          # 2D viewer
open "http://localhost:8765/web/3d.html"   # 3D viewer (needs Mapbox token)
```

**Mapbox token**: `web/config.local.js` (gitignored) must contain
`window.MAPBOX_TOKEN = "pk.…"`. Free tier is 50,000 map loads/month; each visit
to `3d.html` costs one load. See *Operational notes* below.

---

## Project layout

```
sam_cairns_project/
├── README.md                  ← this file
├── .gitignore                 ← excludes web/config.local.js, .env, .DS_Store, etc.
├── data/                      ← raw inputs (TCX files, athlete guide PDF)
│   ├── bike_course.tcx        ←   official IM Cairns bike GPX, ~177 km, 3015 pts
│   ├── run_course.tcx         ←   ~42.9 km, 1101 pts
│   ├── swim_course.tcx        ←   ~3.8 km, 45 pts
│   └── 2026_athlete_guide.pdf ←   67-page IM-published athlete information guide
│
├── processed/                 ← all derived JSON, consumed by the frontend
│   ├── {swim,bike,run}_course.json    ← parsed TCX with derived metrics
│   ├── climbs.json                    ← detected climbs per discipline
│   ├── course_info.json               ← cut-offs, aid stations, wetsuit rules
│   ├── weather_hourly_<year>.json     ← Open-Meteo raw hourly for each race year
│   ├── weather_race_window_summary.json ← race-window stats per year
│   ├── weather_seed_defaults.json     ← median across years; loaded as sim defaults
│   ├── sim_results.json               ← physics_sim scenarios summary
│   ├── sim_splits_<scenario>.json     ← per-segment splits for each scenario
│   ├── athlete_guide.txt              ← extracted text of the PDF (used during dev)
│   └── index.json                     ← summary of which courses exist
│
├── scripts/                   ← Python pipeline (stdlib + defusedxml only)
│   ├── parse_tcx.py           ← TCX → course JSON (with smoothed grades, bearings)
│   ├── extract_course_info.py ← athlete guide knowledge → course_info.json
│   ├── detect_climbs.py       ← climb detection + shape descriptors → climbs.json
│   ├── pull_weather.py        ← Open-Meteo archive → weather_*.json
│   └── physics_sim.py         ← forward simulation across scenarios → sim_*.json
│
└── web/                       ← static frontend (no build step)
    ├── index.html             ← 2D viewer
    ├── app.js                 ← 2D viewer logic
    ├── 3d.html                ← 3D viewer
    ├── app3d.js               ← 3D viewer logic (depends on physics.js, wind.js)
    ├── physics.js             ← JS port of physics_sim.py (live in-browser)
    ├── wind.js                ← particle system + gradient builders + compass
    ├── style.css              ← shared styles (also referenced by 3d.html)
    ├── config.js              ← Mapbox token placeholder (committed, empty)
    └── config.local.js        ← real Mapbox token (gitignored)
```

---

## Architecture & data flow

```
   Raw inputs                Pipeline (Python)              Processed JSON        Frontend
   ─────────                ───────────────────             ──────────────       ──────────

   *.tcx ────────────►  parse_tcx.py ──────────────►  swim/bike/run_course.json ──►  app.js, app3d.js
                                                            │
                                                            ▼
                        detect_climbs.py ───────────►  climbs.json ─────────────────►  app.js, app3d.js
                                                            │
   athlete guide  ────►  pdftotext + manual digest  ──►  course_info.json ──────────►  app.js, app3d.js
                       extract_course_info.py
                                                            │
   (Open-Meteo API) ──►  pull_weather.py ──────────►  weather_*.json ──────────────►  app.js, app3d.js
                                                            │
                                                            ▼
                        physics_sim.py ────────────►  sim_results.json ───────────►  app.js (cards)
                                                            │
                                                            ▼ (matching JS port)
                                                       physics.js + wind.js  ──────►  app3d.js (live sim)
```

**One-way flow**: raw → processed → frontend. The frontend never reads `data/`
directly; everything is mediated by `processed/`. To rebuild any layer, run its
script — they're all idempotent.

---

## The pipeline scripts in detail

### `scripts/parse_tcx.py`

**Input:** `data/*.tcx` (Garmin TCX v2 format)
**Output:** `processed/{slug}_course.json` for each discipline, plus `processed/index.json`

What it does:
- Parses TCX safely (uses `defusedxml`; vanilla `xml.etree` is vulnerable to XXE
  and billion-laughs attacks).
- For each Trackpoint: extracts lat/lng/altitude/cumulative distance.
- Computes per-segment **gradient** (rise / run, as percent), **bearing**
  (great-circle initial bearing in degrees from true north).
- Summary stats: total distance, elevation gain/loss, min/max altitude, bbox.

Output schema (per course):
```json
{
  "name": "Cairns Airport",
  "source_file": "bike_course.tcx",
  "summary": {
    "point_count": 3015,
    "distance_m": 177570.0,
    "distance_km": 177.57,
    "elev_gain_m": 1330.0,
    "elev_loss_m": 1334.0,
    "min_alt_m": 5.0,
    "max_alt_m": 57.8,
    "bbox": { "min_lat": ..., "min_lng": ..., "max_lat": ..., "max_lng": ... }
  },
  "points": [
    { "lat": -16.74107, "lng": 145.67128, "alt": 10.75,
      "dist_m": 0.0, "grade_pct": 0.0, "bearing_deg": 0.0 },
    ...
  ]
}
```

Re-run when: raw TCX files change.

---

### `scripts/detect_climbs.py`

**Input:** `processed/{slug}_course.json`
**Output:** `processed/climbs.json`

What it does:
- Smooths the elevation profile with a rolling-mean window (~150 m) to remove
  GPS jitter.
- Walks the course; opens a climb when smoothed grade ≥ 1.5%, closes it when
  grade < 0.3% for 120 m+.
- Drops climbs shorter than 400 m or with less than 15 m of gain.
- For each climb computes shape descriptors:
  - **bearing_std_deg** — circular std deviation of segment bearings (low = straight, high = winding)
  - **bearing_total_turning_deg** — total angular path; high values indicate switchbacks
  - **gain_thirds_pct** — % of gain in first/middle/last third (e.g. `[50, 30, 20]` = front-loaded)
  - **false_plateaus** — count of mid-climb flats ≥ 50 m
  - **shape_label** — one-line summary like `"winding · back-loaded · 1 false plateau"`
  - **category** — FIETS-style score: HC / 1 / 2 / 3 / 4 / — (Cairns climbs are 3 or 4)

Output schema (excerpt):
```json
{
  "bike": [
    {
      "id": 3, "start_idx": 580, "end_idx": 651,
      "start_km": 17.36, "end_km": 18.43,
      "length_m": 1066.0, "elev_gain_m": 45.2,
      "avg_grade_pct": 4.24, "max_grade_pct": 7.13,
      "start_alt_m": 8.5, "peak_alt_m": 53.7,
      "start": { "lat": -16.6321, "lng": 145.5234 },
      "peak":  { "lat": -16.6238, "lng": 145.5103 },
      "category": "3",
      "bearing_std_deg": 26.8,
      "bearing_total_turning_deg": 145.2,
      "gain_thirds_pct": [34, 29, 38],
      "false_plateaus": 0,
      "shape_label": "winding · consistent grade"
    },
    ...
  ],
  "swim": [], "run": []
}
```

Re-run when: course JSON changes, or you tune the detection thresholds.

---

### `scripts/extract_course_info.py`

**Input:** course JSONs + manually digested facts from the athlete-guide PDF
**Output:** `processed/course_info.json`

What it does:
- Knows the published km marks of every aid station and personal-needs zone
  from the IM athlete guide.
- Looks up each km mark against the parsed course (bisect on cumulative
  distance) to derive map coordinates by linear interpolation.
- Records cut-off times, wetsuit thresholds, lap counts, venue names.

**Important**: aid-station positions and athlete-guide facts are **hardcoded**
in this script for Cairns. For a new race, edit the `bike_stations`,
`run_stations`, etc. arrays in this file (a flat-file replacement of the dict
literals). The interpolation logic is reusable as-is.

Output schema (excerpt):
```json
{
  "race_name": "2026 IRONMAN Cairns",
  "race_date": "2026-06-14",
  "swim": { "distance_km": 3.8, "laps": 2, "cutoff_h": 2.333, ... },
  "bike": {
    "distance_km": 180.0, "laps": 2, "cutoff_h": 10.0,
    "aid_stations": [
      { "name": "B1 — Wangetti Road", "km_marks": [18.0, 91.0],
        "primary_km": 18.0, "lat": ..., "lng": ..., "alt": ... },
      ...
    ],
    "personal_needs": [...],
    "landmarks": [...]
  },
  "run": { ... },
  "wetsuit_rules": { "mandatory_at_or_below_c": 16.0, ... },
  "official_conditions_baseline": { "air_temp_high_c": 28, "air_temp_low_c": 18, "water_temp_avg_c": 23 }
}
```

The PDF was extracted with `pdftotext -layout` once at the start of the
project, into `processed/athlete_guide.txt`. That text is the source the
hardcoded facts came from. For a new race, that step can be re-run on a new
athlete-guide PDF, but the facts then need to be transcribed by hand into the
script.

Re-run when: course JSON changes (to re-derive coords from new distances), or
when athlete-guide facts change.

---

### `scripts/pull_weather.py`

**Input:** Hardcoded Cairns coordinates and race dates
**Output:** `processed/weather_hourly_<year>.json` (6 files), `weather_race_window_summary.json`, `weather_seed_defaults.json`

What it does:
- Calls Open-Meteo's archive API (`archive-api.open-meteo.com/v1/archive`,
  free, no key) for 2018, 2019, 2021, 2022, 2023, 2024 race days at three
  locations along the bike course.
- Pulls: temperature_2m, relative_humidity_2m, wind_speed_10m,
  wind_direction_10m, wind_gusts_10m, precipitation, cloud_cover, dew_point_2m,
  surface_pressure.
- Computes race-window (05:00–17:00 AEST) summary per year: mean/max wind,
  modal cardinal direction, vector-mean direction, temp ranges, etc.
- Writes a `weather_seed_defaults.json` that the frontend loads as the
  "Median" preset.

**Key findings for Cairns:**
- Prevailing wind direction: **SE (vector mean 140°)** — classic trade winds
- Median race-window wind speed: **17.5 km/h**, gust max median **57 km/h**
- Temperature range: **20.7 – 25.4 °C** race-window
- Bimodal across years: 2019/2021/2022 windy (~20 km/h), 2023/2024 calm (~11 km/h)
- The bike course runs N–S along the coast → SE wind = headwind heading north,
  tailwind on the return

**Important**: dates and coordinates are hardcoded for Cairns. For a new race:
edit the year/date list and the coords at the top of the script.

Re-run when: starting a new race (once), or if you want to add another historical year.

---

### `scripts/physics_sim.py`

**Input:** `processed/bike_course.json`, `processed/weather_seed_defaults.json`
**Output:** `processed/sim_results.json`, `processed/sim_splits_<scenario>.json`

What it does:
- Forward physics simulation: at constant pedal power, segment-by-segment,
  solves for steady-state speed using the cycling power equation.
- Air density derived from temperature + humidity + pressure (Tetens formula).
- Wind: meteorological "from" direction, projected onto rider heading per
  segment (`windAlongHeading`). Positive = tailwind component, negative = headwind.
- Steady-state solver: bisection on speed for each segment given target
  pedal power.
- Inverse mode: bisects on power to hit a target total time.

Scenarios are now **2026, calibrated from recent training data + sourced 2026
equipment** (see *2026 calibration* below). They load CdA/Crr/mass from
`processed/equipment.json` and the IM power band from `processed/sam_fitness.json`,
then run (median Cairns weather unless noted):
1. `2026_conservative` — CdA 0.230 @ 271W (IF 0.72) → **4:52**
2. `2026_target` — CdA 0.230 @ 286W (IF 0.76) → **4:44**  ← primary solo projection
3. `2026_aggressive` — CdA 0.230 @ 293W (IF 0.78) → **4:41**
4. `2026_target_draft` — target + realistic 20m drafting (~3%) → **4:42**
5. `2026_target_aero` — target, optimistic CdA 0.220 → **4:41**
6. `2026_target_calm` — target, 5 km/h wind → **4:32**
7. `2026_target_windy` — target, 28 km/h wind → **5:03**
8/9. `2026_target_mass_hi/lo` — target at 93kg / 88kg → **4:46 / 4:43**

Inverse run: NP required to hit 4:00 / 4:10 / 4:20 / 4:30 (median, CdA 0.230, 90.5kg)
→ **412 / 376 / 346 / 318 W**. Marginal-gain table: at the target operating
point, **+5 W saves ~2:23** and **−0.005 CdA saves ~1:45**.

**Key findings (2026):**
- **Wind variance dominates**: calm→windy swings the split **±15–20 min**, more
  than any realistic aero or power change.
- **2025's 4:27 was heavily draft-aided and is NOT a calibration anchor.**
  Reproducing it at Sam's *measured* 290 W needs CdA 0.18–0.21 (impossible for a
  loaded tri bike) → 2025 drafting (12 m rule + AG packs) was worth ~15–20 %.
  At the 2026 **20 m** rule, legal drafting is ~3 % effective — so the honest
  solo projection is **~4:44**; race-day packs can still pull it toward ~4:35.
- **The fitness gain shows up in the RUN, not a faster solo bike split.** In
  2025, 290 W was ~IF 0.85 of his then-FTP (overcooked → run blow-up). Now
  290 W is IF ~0.77 of a 376 W FTP — same split, far fresher legs → a stronger,
  better-paced marathon.

Re-run when: course JSON changes, rider scenarios change, or weather seed updates.

---

## 2026 calibration (current fitness + new equipment)

As of **2026-05-28**, the model is no longer calibrated against the 2025 race
power (which was draft-contaminated). Per the athlete's direction, the 2026
projection is driven by **recent training data** (Sam's fitness has grown
substantially) plus his **confirmed 2026 equipment**. Two data artifacts feed
the simulator; edit them, not the code:

### `processed/equipment.json` — physics from equipment (now SOURCED; see `_sources`)
| Param | Value | Basis |
|---|---|---|
| **Crr** | 0.0040 (range 0.0036–0.0044) | **Sourced**: bicyclerollingresistance.com measures GP5000 TT TR 28mm at drum Crr **0.00249** (8.3 W/tyre, 72 psi). ×~1.6 real-road asphalt correction + small TPU-tube add → ~0.0040 system on Cairns tarmac. |
| **CdA** | **0.230** (opt 0.220 / cons 0.245) | **Back-solved from real rides**: coasting-in-tuck on the 2025 race (no power-meter dependence) → CdA **0.219** (his proven aero). Sustained pedaling a touch higher → 0.230. Powered-mixed descents read 0.267 but are contaminated. Plasma 6 + Princeton disc/tri-spoke support the low end. |
| **Mass** | **90.5 kg** avg (range 88–93; start 93) | **Confirmed inputs**: rider 78 kg race (80 now) + bike ready ~10 kg (Plasma 6 ~9.6 + Princeton tri-spoke 925 g/disc ~970 g + pedals/cages/computer) + ~2.5 kg time-avg consumables (of 4 L water + 1 kg carb at start). Far heavier than the old 75 kg placeholder. |

### `processed/sam_fitness.json` — current fitness (from `scripts/analyze_fitness.py`)
Mean-maximal power curve across the recent bike files:

| Window | 5 min | 10 min | 20 min | 30 min | 60 min | 4 h |
|---|---|---|---|---|---|---|
| Best (training) | 402 W | 392 W | 382 W | 368 W | 337 W | 293 W |

- **FTP estimate ≈ 376 W** (band 363–388). The 382 W best-20 was set *inside a
  5 h ride*; 10-min reps repeat at ~385 W. Fresh FTP is likely at/above this.
- **Durability is not the limiter**: NP 307 W for 5 h, NP 291 W for 6 h — both
  far above any IM target. Pacing discipline (protecting the marathon) is the
  constraint, not engine size.
- **IM bike target NP band**: 271 W (IF 0.72) / **286 W (IF 0.76, primary)** /
  293 W (IF 0.78). 2025's run blow-up argues for the middle of the band.

### Drafting (20 m rule) — sourced
IRONMAN's pro draft zone expanded **12 m → 20 m** (effective 2026-03-01, the
RaceRanger/Graveline study). CFD (Swiss Side) puts trailing-rider drag savings
at ~13 % at 10 m tapering to **~9 % at 20 m in calm air**, and *negligible* in
crosswind. Applied only to the aero term, only when a legal rider is ahead, and
cut by Cairns SE trades → **~3 % effective race-average**, modelled as a CdA
×0.97 scenario (`2026_target_draft`). Reconciliation: reproducing the actual
2025 4:27 at Sam's measured 290 W needs CdA 0.18–0.21 (impossible), so 2025 was
**~15–20 % draft-aided** under the looser 12 m rule + AG packs — which is why
2025 is *not* a calibration anchor.

### "Ride the course" — variable power (`processed/climbing_profile.json`)
Sam's recent hilly rides show a clean course-riding signature, quantified into
a **power-by-grade curve** (median % of NP): ~**114 %** on >8 % climbs down to
~**28 %** soft-pedalling on >6 % descents, with **HR held ~flat (150–155)**
across the whole power range and 25+ bpm recovery when he eases on descents.
`scripts/physics_sim.py` now has a `simulate_ride_the_course` mode that
distributes power by gradient (Sam's curve), **scaled to the same Normalized
Power** as constant pacing. Result: **~3 min free** at equal physiological cost
(`2026_target_rtc` 4:41 / `2026_aggressive_rtc` **4:38**). Gain is modest only
because Cairns is fairly flat; on a hillier course it's larger. Constant-power
pacing leaves this on the table.

### Run leg & sub-3 goal — `processed/run_prediction.json` (`scripts/analyze_run.py`)
Paced from physiology (separate from bike physics). Recent files: **LT2 threshold
≈ 3:33/km**, **LT1 ≈ 4:13/km**; Cairns run is flat, historically warm (~29 °C).
- **Durability calibration (May 24 datapoint)**: he held **LT1 pace on legs
  fatigued by the prior day's 6 hr ride at only +3 bpm** (4:12/km @ HR 158 vs
  fresh 4:13/km @ HR 155) — strong fatigue resistance. This justifies modelling
  the IM-off-bike penalty at **20 s/km** (down from a generic 22). *Caveat: 1 hr
  run off an aerobic ride, not a marathon off a race bike — not extrapolated to
  the optimistic extreme.*
- **Current-fitness projection**: best **3:08** / target **3:12** / conservative
  **3:23** (vs 2025 actual 3:23:32).
- **Sub-3 goal** = **4:16/km even**. A *goal needing a fitness lift, not a
  current-fitness outcome*: 4:16/km ≈ his current **fresh** LT1, model ceiling
  ~3:08. **Gap: LT1 must move 4:13 → ~3:56/km (~17 s/km)** (narrowed from 19 by
  the durability evidence). He has the speed (3:33 threshold); the work is
  aerobic durability + holding it off the bike in heat. Closers: tempo/sweet-spot
  to lift LT1, brick runs at 4:16 off a hard bike, heat acclimation, gut training.

### Full-day projection — `processed/race_projection.json`
Bike rows = **ride-the-course** execution (NP-matched); run rows = durability-calibrated.
| | Bike | Run | **Total** |
|---|---|---|---|
| best (current fitness) | 4:32 | 3:08 | **8:32:50** |
| **target** | 4:38 | 3:12 | **8:43:36** |
| **stretch (sub-3 run)** | 4:36 | **3:00** | **8:29:05** |
| conservative | 5:00 | 3:23 | **9:18:45** |
| *2025 actual* | *4:27 (draft)* | *3:23* | *8:45:38* |

Note the **target (8:43:36) now edges under 2025's 8:45:38** — the durability
calibration pulled the run projection forward.

The **stretch line (8:29)** is the prize: a strong ridden-to-course bike + the
run development to hold sub-3 → clear of 2025 and the AG. Research that would
firm up these numbers is listed in `RESEARCH_WISHLIST.md`.

With ride-the-course pacing and the durability-calibrated run, the **target now
projects 8:43:36 — edging under 2025's draft-aided 8:45:38**, with a stronger,
better-paced run and lower bike intensity. Race-day packs (even at 20 m) and a
faster swim push it down further; the sub-3-run stretch reaches ~8:29.

### To retune
1. New bike files → `python3 scripts/analyze_fitness.py`; new run files → `python3 scripts/analyze_run.py`.
2. Edit `processed/equipment.json` if kit/position/mass changes.
3. `python3 scripts/cda_descent_solve.py` to re-back-solve CdA from new high-speed data.
4. `python3 scripts/physics_sim.py` → regenerates `processed/sim_results.json` (viewers read this).

---

## Frontend

Both viewers are plain HTML + JS (no build step). Serve any way you like; a
local Python server works:

```bash
python3 -m http.server 8765
```

Then visit `http://localhost:8765/web/` (2D) or `http://localhost:8765/web/3d.html` (3D).

### `web/index.html` + `web/app.js` (2D viewer)

- **Map**: Leaflet 1.9.4 with ESRI World Imagery (no API key required).
  Includes a labels overlay so place names stay legible on satellite.
- **Sidebar**:
  - Course summary table (distance, elev gain/loss, alt range per discipline)
  - "Walk the course" scrubber: pick a discipline, drag the slider, marker
    walks along the route, live readouts of distance/alt/grade/heading
  - Elevation profile (Canvas 2D) with a **smoothing slider** that applies a
    moving-average filter to the displayed elevation
  - Notable Climbs cards (read from `climbs.json`); click to fly to climb
  - Race-day weather seed panel (median across 2018–2024)
  - Bike Time Simulator panel showing the six static scenarios + inverse table
- **Header**: discipline toggles, view tabs (2D Map / 3D View)

### `web/3d.html` + `web/app3d.js` (3D viewer)

Depends on `physics.js` and `wind.js` (which themselves use `physics.js`).

- **Map**: Mapbox GL JS 3.7.0 with `satellite-streets-v12` style + terrain DEM
  (`mapbox.mapbox-terrain-dem-v1`). Pitch, bearing, exaggeration sliders.
- **Confirm-load gate**: shows local usage stats + a button before
  instantiating the map (each instantiation is one paid map load against the
  free tier).
- **Usage ribbon (top-right)**: persistent counter of map loads made from
  this browser, with a link to the authoritative Mapbox dashboard.
- **Wind & Race Simulator panel (top-left)**, collapsible:
  - Compass dial showing wind direction
  - Speed / direction / temperature sliders
  - Scenario presets (Median, Calm, Windy, Tail N, Head N, No wind)
  - Rider params (Power, CdA, Mass)
  - Big predicted-bike-split readout + `vs no wind` delta
  - **Route color toggle** (4 modes):
    - **Default** — orange line
    - **Wind** — per-segment speed delta (red = headwind penalty, blue = tailwind boost)
    - **Climbs** — gradient severity (green flat → yellow → orange → red → dark purple)
    - **Climbs + Wind** — effective grade (real grade + virtual grade from wind force)
  - **Color legend** that updates per mode
- **Animated wind particles** overlay on the map, mounted inside Mapbox's own
  canvas container. Particles drift in the true world wind direction (accounts
  for map rotation). Density and speed scale with wind speed.
- **Climbs panel (right side)**, shows automatically when Climbs or Climbs+Wind
  mode is selected. Same climb cards as 2D, plus a live "wind at this climb"
  annotation that updates as you drag the wind sliders.
- **3D scrubber (bottom-right)**: pick discipline, walk the course, click
  "Fly to" to drop the camera into a first-person angle along the rider's heading.
- **Pitch/Bearing/Exag controls (bottom-left)** for camera tweaks.

### Shared modules

- **`web/physics.js`** — JS port of `physics_sim.py`. Functions: `airDensity`,
  `windAlongHeading`, `solveSteadySpeed`, `simulateForward`. Mathematically
  identical to the Python; verified outputs match.
- **`web/wind.js`** — particle animation + per-segment color gradient builders
  for the three non-default coloring modes. Uses `physics.js` for the
  effective-grade calculation.

---

## The physics model (v2)

The base equations are from [Martin et al. 1998](https://journals.humankinetics.com/view/journals/jab/14/3/article-p276.xml)
— validated against road measurements at R² = 0.97, SE = 2.7 W. The current
implementation includes four refinements over the bare Martin model, documented
inline in `web/physics.js` and `scripts/physics_sim.py`.

### Cycling power equation

```
P_pedal · η = v · (F_gravity + F_rolling + F_aero)

  F_gravity     = m · g · sin(atan(grade))
  F_rolling     = m · g · cos(atan(grade)) · Crr
  F_aero,motion = 0.5 · ρ · CdA(yaw) · v_apparent · (v − v_wind_along)
  v_apparent    = sqrt((v − v_wind_along)² + v_wind_cross²)
  yaw           = atan2(|v_wind_cross|, v − v_wind_along)
```

- `P_pedal` — rider's pedal power, W
- `η` — drivetrain efficiency (default 0.975)
- `v` — ground speed, m/s
- `m` — total mass (rider + bike + gear), kg (default **82**; from
  `processed/equipment.json`. ⚠️ Sam's body mass is unconfirmed — see *2026 calibration*)
- `g` — 9.80665 m/s²
- `grade` — rise/run (e.g. 0.04 for 4%)
- `Crr` — rolling resistance coefficient (default **0.0040**; from
  `processed/equipment.json` — Continental GP5000 TT TR 28mm + Pirelli TPU tube
  @ 80–82 psi on Cairns tarmac)
- `ρ` — air density, kg/m³
- `CdA(yaw)` — yaw-dependent drag area, m² (see *Yaw-dependent CdA* below)

### v2 refinement #1: Wind gradient correction (Hellmann power law)

Open-Meteo wind is reported at 10 m anemometer height. A rider is at ~1.2 m.
Per [standard meteorology](https://en.wikipedia.org/wiki/Wind_gradient):

```
v_rider = v_10m · (1.2 / 10)^α
```

Where α is the Hellmann exponent: **0.10 offshore**, **0.143 onshore over open
terrain**, 0.20+ over rough terrain. Cairns is coastal/rural → α = 0.11 →
factor ≈ **0.7766**. Without this correction we over-counted wind impact by ~22%.

Configurable via `windHeightFactor` parameter.

### v2 refinement #2: Yaw-dependent CdA

CdA varies with the apparent wind angle (yaw). A typical TT setup with disc
rear + tri-spoke front (Sam's: Scott Plasma + full disc + Princeton CarbonWorks
tri-spoke) has a slight "sail effect" benefit at low yaw and increases past
~10°. We use a quadratic fit:

```
CdA(yaw) = CdA(0) · max(0.92, 1 + a · yaw² − b · |yaw|)
```

With defaults `a = 0.00055`, `b = 0.005`. This produces:
- 0° yaw: 1.00× CdA(0)
- 5° yaw: 0.989× (slight dip)
- 10° yaw: 1.005×
- 15° yaw: 1.04×
- 20° yaw: 1.12×

Calibrated against published high-end TT wind-tunnel data (Princeton
CarbonWorks' Mach TSV2 yaw sweep, Specialized/Trek public curves). When Sam's
actual wind-tunnel curve becomes available, swap `a` and `b`.

### v2 refinement #3: Aero / sitting-up posture switching

Below a configurable speed threshold (default **19.31 km/h = 12 mph** for Sam),
the rider sits up — CdA jumps by a multiplier (default **1.6×**, per published
0.28 → 0.45 sitting-up data). Implemented as an iteration inside `solveSegment`
that converges on a self-consistent (aero state, yaw, speed) triple per segment.

On the Cairns bike course at Sam's target power: ~97% of segments held aero,
~3% sitting (slow climbs and sharp corners).

### v2 refinement #4: Slow corner speed caps

For each course point, we pre-compute the tightest local curvature within
a 30 m look-ahead window. Implied radius `r = distance / Δbearing(rad)`. Max
safe speed:

```
v_corner_max = max(4.0, sqrt(μ · g · r))
```

With μ = 0.45 (conservative for race conditions on dry tarmac) and a floor of
4 m/s (= 14.4 km/h, the slowest realistic cornering pace). On the Cairns bike
course, 99 of 3015 points (3.3%) hit a cornering cap below 54 km/h, concentrated
at the Craiglie U-turn and the Yorkeys Knob junction.

Configurable via `cornerFrictionMu`, `cornerMinMs`, and `enableCornering`.

### Air density (Tetens / ideal gas)

```
ρ = (P_dry / (R_d · T)) + (P_vapor / (R_v · T))
  R_d = 287.058 J/(kg·K)     R_v = 461.495 J/(kg·K)
  P_vapor = humidity · 6.1078 · exp(17.27·T_c / (T_c + 237.3)) · 100  (Pa)
  P_dry = P_total - P_vapor
```

For Cairns at 23 °C, 1013 hPa, 66% RH: **ρ ≈ 1.184 kg/m³**

### Wind direction conventions

- **Meteorological convention** — `wind_from_deg` = the direction the wind is
  *coming from*. 0° = north wind (blowing toward south), 140° = SE wind,
  270° = west wind.
- **Bearing convention** — `bearing_deg` = the direction the rider is
  *going toward*. 0° = north, 90° = east.
- **Wind decomposition into along + cross components**:
  ```
  wind_to_deg   = (wind_from_deg + 180) % 360
  angle         = wind_to_deg − heading_deg
  wind_along_ms = (wind_speed_kmh / 3.6) · cos(angle)   (+ = tailwind)
  wind_cross_ms = (wind_speed_kmh / 3.6) · sin(angle)   (lateral; sign irrelevant for CdA)
  ```
  `wind_speed_kmh` here is the **rider-height** value (after Hellmann correction).

### Solver

Bisection on speed `v` (range `[0.05, 50] m/s`) for each segment, ~80 iterations
to floating-point convergence. Wrapped in an outer iteration that converges on
the (aero/sitting state, yaw, speed) triple per segment — typically 2–3
iterations. The JS (`web/physics.js`) and Python (`scripts/physics_sim.py`)
implementations are kept numerically identical; both expose the same defaults
via a `DEFAULTS` block.

### Inverse mode

To find the power required to hit a target total time: bisection on power
(`[50, 600] W`), running the full forward sim each iteration. Used only at
build time; the live frontend only does forward sims.

---

## The wind system

### Particle animation (`web/wind.js`, `class WindParticles`)

- Canvas mounted inside Mapbox's `getCanvasContainer()` — same parent as
  Mapbox's GL canvas, so it inherits the authoritative size and survives
  zoom/pitch/DPR transitions.
- Particle count = `60 + 8 · wind_speed_kmh` (capped 460).
- Drift speed in pixels/sec = `max(8, wind_speed_kmh · 5)` — floor keeps low-wind
  particles visibly moving.
- **Respawn by cause**:
  - Aged out (lifespan expired) → respawn at **random visible position**
    (keeps interior populated at all wind speeds)
  - Drifted off canvas → respawn at **upwind edge** (preserves streaming feel)
- Direction vector recomputed each frame via `map.project()` of two real-world
  points 200m apart, so it tracks map bearing.
- DPR change detection: re-check `window.devicePixelRatio` each frame; if it
  differs from cached, call `resize()`. Also subscribes to
  `matchMedia('(resolution: Xdppx)')` for OS/browser zoom events.

### Route color gradients

All three non-default modes produce a Mapbox `line-gradient` expression along
`line-progress` (requires `lineMetrics: true` on the source). Stops are
de-duped to strictly ascending progress (Mapbox silently rejects duplicates).

- **Wind impact** — color from per-segment speed delta vs zero-wind baseline.
  Red (-20%) → gray (neutral) → blue (+20%).
- **Climbs** — color from smoothed segment grade. Green (≤0%) → yellow (2%) →
  orange (4%) → red (7%) → dark purple (≥10%).
- **Climbs + Wind** — color from "effective grade" = real grade +
  wind-equivalent grade. Equivalent grade is `(F_wind_delta / (m·g)) · 100`
  using a 41 km/h reference speed.

### Compass dial

`drawCompass(canvas, fromDeg, speedKmh)` — small canvas in the panel. Arrow
points in the direction the wind is *going to* (the "downstream" direction),
intensity scales with speed. Cardinal labels at N/E/S/W.

---

## Operational notes

### Mapbox setup and cost

- **Free tier**: 50,000 GL JS Map Loads per month.
- **Overage**: $5 per 1,000 loads (50,001 – 100,000 tier), in USD.
- **What counts as a load**: each `new mapboxgl.Map(...)` call. Pan/zoom/pitch
  *within* a session is free. Refreshing the page = +1 load.
- **Session length**: 12 hours, then a new load is counted.
- **Tiles within a session**: terrain DEM, raster, vector tiles — all *unlimited*
  during the session, no extra billing.
- **Realistic project usage**: 50–500 loads/month → **$0**.

### Token handling

- Token lives in `web/config.local.js` — gitignored.
- `web/config.js` is a committed empty stub; `3d.html` loads both, with
  `config.local.js` overriding.
- The token in current use is a `pk.*` (public token). Restrict it to
  `http://localhost:*` via the Mapbox dashboard for blast-radius safety.
- For programmatic usage tracking (cross-device), Mapbox's Statistics API
  requires a `sk.*` (secret token) — not implemented; we rely on a local
  `localStorage` counter that's per-browser only, with a link to the official
  dashboard for the canonical numbers.

### Confirm-load gate

`3d.html` shows a "Load map?" gate the first time you visit per tab session,
displaying current local usage. Session-storage `mapbox-auto-load=1` flag
skips the gate for the remainder of the tab.

### Local web server

The frontend fetches JSON, which doesn't work over `file://`. Always serve via
HTTP:

```bash
python3 -m http.server 8765 > /tmp/cairns_server.log 2>&1 &
echo $! > /tmp/cairns_server.pid
# To stop:
kill $(cat /tmp/cairns_server.pid)
```

### Python environment

All scripts use **Python stdlib only** plus `defusedxml`. Python 3.9+.

```bash
pip install defusedxml      # only external dep
```

No virtualenv strictly required, but recommended for isolation.

---

## Hardcoded race-specific assumptions

These will all need to change for a new race:

| Where | What's race-specific |
|---|---|
| `data/*.tcx` | The course files themselves |
| `data/2026_athlete_guide.pdf` | Race info document |
| `scripts/extract_course_info.py` | Aid station km marks, cut-off times, lap counts, wetsuit thresholds, venue names — all literal in the script |
| `scripts/pull_weather.py` | Lat/lng of swim/bike/run start, race date list, AEST timezone offset |
| `scripts/physics_sim.py` | The six scenario definitions (CdA, power values for Sam) — race-and-athlete-specific |
| `web/app3d.js` | Default map center (currently `[145.55, -16.78]` — Cairns area) |
| `web/index.html` + `web/3d.html` | Page titles |

---

## Lessons learned & key decisions

### Bugs we hit (in order of debugging difficulty)

1. **TCX security**: vanilla `xml.etree.ElementTree` is vulnerable to XXE and
   billion-laughs attacks. Use `defusedxml.ElementTree`.

2. **Particle disappearance on zoom (two interleaved bugs)**:
   - First bug: canvas mounted on `#map3d` which sometimes had transient
     zero-size during Mapbox layout adjustments → `cssW=0` → particles
     stacked at origin. Fix: mount inside `map.getCanvasContainer()` instead,
     use that as authoritative size, also subscribe to `matchMedia` for DPR changes.
   - Second bug: `respawnInPlace` used `Math.random() > upwindBias` with
     `upwindBias = wind_speed / 18`. At default 17.5 km/h, 97% of respawns
     went to the upwind edge → interior emptied over time. Fix: split respawn
     by *cause* — aged-out particles always respawn at random interior position,
     drift-off-canvas particles respawn at upwind edge.

3. **Mapbox `line-gradient` requires strictly ascending progress values**, not
   just non-decreasing. Duplicate stops (e.g. when two adjacent TCX points
   share `dist_m=0`) cause silent rejection inside `try/catch`. Fix: dedupe
   with epsilon before building the expression.

4. **JS/Python physics divergence**: original JS `solveSteadySpeed` returned
   `0.05` for zero power, Python returned `0.5`. Aligned to `0.5` (a slow
   crawl rather than near-zero).

5. **Per-slider-tick simulation cost**: every input event ran the forward sim
   twice (with-wind + calm). The calm baseline only depends on rider+temp,
   not wind. Now memoized by parameter key, only recomputes when those change.

### The two-agent audit

After heavy build-out, we ran two independent code-review agents in parallel
(no shared context) and compared findings:

- **Mutual high-confidence findings** were fixed immediately.
- **Divergent findings** (one agent only) were evaluated individually — some
  were real bugs the other missed, some were stylistic preferences.
- **Disagreements about mechanism** (the particle bug) led to a defensive fix
  that addresses both theories.

This pattern is worth repeating for the next race build-out — independent
reviews catch each other's blind spots.

### Decisions made along the way

| Decision | Why |
|---|---|
| Leaflet (2D) + Mapbox GL JS (3D) — not just one | Leaflet's free ESRI tiles for the always-on 2D view, Mapbox for the premium 3D experience only. Splits cost exposure. |
| Python stdlib only (+ `defusedxml`) | No virtualenv hassle, every Mac has Python 3 |
| JSON output between pipeline + frontend | Easy to inspect, easy to load via `fetch` |
| Physics in both Python and JS | Python for build-time scenarios, JS for live interactive sim |
| Single HTML+JS files per viewer, no bundler | Fast iteration, no build step, easy to share |
| Wind defaults from historical median | A "best guess" baseline that's data-driven, not arbitrary |
| Climbs detected at build time, not live | The detection algorithm has many tuning knobs; freezing them in `climbs.json` keeps the frontend cheap |
| Athlete params hardcoded in `physics_sim.py` | Single-athlete project; reasonable shortcut. **First refactor target if/when we add Sam's TrainingPeaks data.** |

### Mapbox cost confidence-building

Before opening the 3D view, we explicitly modeled the cost exposure given the
50k free-tier ceiling and $5/1k overage rate. Realistic project usage is
50–500 loads/month → **$0**. The token is URL-restrictable in the Mapbox
dashboard for additional safety. Local usage counter in `app3d.js` tracks
loads-from-this-browser in localStorage with a 365-day rolling window.

---

## Known limitations & deferred work

### Not yet built / pending

- **Sam's TrainingPeaks workout files** — the original starting point of the
  project. The TrainingPeaks export button on individual workouts would give
  us `.fit` or `.tcx` files with HR/power/cadence/speed. Once we have these,
  we can:
  - Back-calibrate Sam's actual mass + Crr + CdA via Robert Chung's "virtual
    elevation" method ([raceyourtrack.com PDF](https://www.raceyourtrack.com/static/docs/indirect-cda.pdf))
  - Replace the *generic* yaw curve with field-derived coefficients for Sam's
    specific Scott Plasma + disc + tri-spoke setup
  - Overlay his lived power/HR/speed on the course
  - Run "what if" scenarios with Sam's actual variability instead of constant power

- **Previous-race data + transition times** — once historical race files
  (Sam's own past IM Cairns or similar events) are available, the model can be
  validated against actual lived bike splits, and the swim/bike/run transitions
  can be incorporated as fixed-time penalties for full-race-day predictions.

- **Athlete profile JSON** — currently rider params (mass, CdA, power) are
  inline in `physics_sim.py` and as sliders in the 3D viewer. A
  `data/athletes/sam/profile.json` would let us swap athletes more easily.
  Skipped per the "fork-not-parameterize" decision.

- **Real-time weather forecast pull** — `pull_weather.py` only does historical.
  For race-week prep, a forecast fetch (different Open-Meteo endpoint) would
  show "predicted conditions for this Sunday."

- **Run physics model** — we only simulate the bike leg. Running physics
  (different energy cost, no aero gain from drafting, heat dissipation matters
  more) would require a separate model.

- **Climb-by-climb time deltas** — the climbs sidebar shows current
  wind direction at each climb, but doesn't yet show "this climb will cost
  you +0:45 with current conditions vs. no-wind baseline." Would be useful
  for race-day strategy.

- **Aid station strategy overlay** — fueling/hydration plan tied to predicted
  effort and heat exposure between aid stations.

- **Sun position / time-of-day overlay** — heat exposure depends on when the
  rider is on which segment.

### Active limitations

- The bike course TCX is 177.6 km but the published race is 180 km. Small
  discrepancy from the TCX file's start/finish trimming.
- Aid station positions are derived from interpolation along the TCX's
  cumulative distance, not from GPS-tagged aid station locations. Accurate
  enough for visualization but not for "did I run a red light at the aid station."
- The "effective grade" calculation in the wind+climbs route coloring uses a
  fixed 41 km/h reference speed. When Sam's data arrives, swap for per-section average.
- Particle visualization is purely artistic — particles don't represent real
  air parcels, just the direction and intensity of the modeled wind.
- The yaw-dependent CdA curve is a **generic** TT-bike fit, not Sam-specific.
  Without his wind-tunnel data, this is the best available approximation;
  expect ±2-3% error in any single segment. Aggregates over the full bike leg
  largely cancel out.
- Slow-corner caps use a single conservative friction coefficient (μ = 0.45);
  a confident descender may corner faster, a wet day may demand slower. Worth
  per-rider tuning eventually.
- Cornering is modeled as a *cap* on segment speed, not as a deceleration +
  apex + acceleration profile. Lost time is approximately right; lost watts
  during the brake/coast aren't recovered (so the constant-power assumption
  slightly under-predicts time saved by smooth corner entry).

---

## Playbook: building this for the next race

1. **Create a new project folder** by copying this one:
   ```bash
   cp -r sam_cairns_project sam_<race>_project
   cd sam_<race>_project
   rm -rf data/ processed/  # we'll repopulate
   mkdir -p data processed
   ```

2. **Drop in the new race's TCX files** into `data/`:
   - `swim_course.tcx`
   - `bike_course.tcx`
   - `run_course.tcx`
   Source: the race's official course page on the IRONMAN website, athlete
   guide, or a Strava segment.

3. **Drop in the athlete guide PDF** if available, then extract text:
   ```bash
   pdftotext -layout data/<athlete_guide>.pdf processed/athlete_guide.txt
   ```

4. **Edit `scripts/extract_course_info.py`** with the new race's facts:
   - Aid station km marks (search the athlete guide text)
   - Cut-off times
   - Lap counts
   - Wetsuit thresholds (usually the same; tropical races may waive)
   - Venue names
   - Race name + date

5. **Edit `scripts/pull_weather.py`** with:
   - Race-day coordinates (lat/lng of swim/bike/run start)
   - Historical race date list (the last 5–6 years' dates)
   - Local timezone offset
   - Save to the same `weather_*.json` filenames.

6. **Run the pipeline**:
   ```bash
   python3 scripts/parse_tcx.py
   python3 scripts/extract_course_info.py
   python3 scripts/detect_climbs.py
   python3 scripts/pull_weather.py        # slow, run once
   python3 scripts/physics_sim.py
   ```

7. **Sanity-check the outputs**:
   - Open `processed/{slug}_course.json` — distances should match the race's
     official total (within ~2% — small trimming is expected).
   - Open `processed/climbs.json` — climb count should match what you'd
     expect (Cairns has 20; flat races have 0; Lanzarote has many more).
   - Open `processed/weather_seed_defaults.json` — wind direction should be
     consistent with the local prevailing winds for that month.
   - Run `python3 scripts/physics_sim.py` and compare predicted bike split
     to what a top age-group time looks like for this race.

8. **Edit `scripts/physics_sim.py`** with the athlete's parameters:
   - The six scenario CdA/power combos
   - The inverse-mode target time

9. **Update `web/app3d.js`** map center to the new race's bbox center.

10. **Update `web/index.html`** and `web/3d.html`** page titles.

11. **Reset the Mapbox usage counter** (optional; LocalStorage is per-browser
    anyway, but for a clean slate):
    ```js
    localStorage.removeItem("mapbox-load-log");
    ```

12. **Serve the frontend** and visit `http://localhost:8765/web/`:
    ```bash
    python3 -m http.server 8765
    ```

13. **Test all four route-color modes** in the 3D viewer; verify particles
    animate correctly; verify the climbs panel shows reasonable climbs.

14. **Document deltas from this race** in a new section at the top of the
    project's README. Especially: the prevailing wind regime, the time-of-day
    profile, and any course quirks (e.g., "the descent down X is faster than
    the model predicts because the road camber favors aero tuck").

---

## Glossary

- **TCX** — Training Center XML (Garmin). Format for course/activity data
  including GPS, altitude, time, distance.
- **CdA** — coefficient of drag × frontal area, m². Lower = more aero.
  Triathlon TT: 0.20–0.27. Sam's 2026 value: **0.230** (back-solved; coasting
  tuck 0.219) on the Scott Plasma Gen 6 + Princeton disc/tri-spoke.
- **Crr** — coefficient of rolling resistance, dimensionless. Race tires on
  smooth tarmac: ~0.003–0.005. Sam's 2026 setup (GP5000 TT TR 28mm + TPU @
  80–82 psi): **0.0040**.
- **IF / NP** — Intensity Factor = NP/FTP; Normalized Power. IM bike is
  typically ridden at IF 0.72–0.78. Sam's FTP ≈ 376 W (2026), IM target NP ≈ 286 W.
- **FIETS index** — a Tour-de-France-style climb categorization based on
  length × grade². HC > 1 > 2 > 3 > 4. Cairns climbs are all 3 or 4.
- **AEST** — Australian Eastern Standard Time (UTC+10, no DST in QLD).
- **ERA5** — ECMWF Reanalysis v5. The historical weather product Open-Meteo
  serves via its archive API. Hourly resolution back to 1940.
- **DPR** — Device Pixel Ratio. The browser's `window.devicePixelRatio`.
  Retina displays have DPR=2, regular displays DPR=1.
- **Map load** (Mapbox) — one `new mapboxgl.Map()` instantiation. The unit
  of GL JS billing.

---

## Contact / context

- Built by Alex Westover (Hard Rock Digital).
- Designed for: 2026 IRONMAN Cairns, June 14, 2026.
- Target athlete: Sam.
- Source data: official IM Cairns course TCX exports, the 2026 athlete guide
  PDF, Open-Meteo archive ERA5 reanalysis.
