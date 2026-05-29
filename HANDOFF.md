# Session Handoff — sam_cairns_project

**Date of handoff:** 2026-05-28 (updated same day)
**State:** Sam's workout files received + model recalibrated to 2026 current
fitness + new equipment. Two open confirms with Sam (mass, target intensity).

If you're picking this up in a new session, read this file first, then skim
`README.md` (esp. *2026 calibration*) for the full architecture and decision log.

---

## UPDATE 2026-05-28 — files arrived, model recalibrated

Sam's workouts landed (9 files, in `data/sam_workouts/` with per-workout
`*_context.md` + `manifest.json`). Includes the **2025 Cairns race bike** (the
old ground-truth target) AND a rich set of recent training (tempo/aerobic/
interval/climbing). Per the user, the 2026 projection is now calibrated from
**recent fitness**, not the 2025 race (which was draft-aided).

**What changed (full recalibration, sourced):**
- `scripts/analyze_fitness.py` → `processed/sam_fitness.json`: mean-max power
  curve, **FTP ≈ 376 W**, IM target NP band 271/286/293 W.
- `processed/equipment.json` (now SOURCED, see `_sources`): **Crr 0.0040** (GP5000
  TT TR 28mm drum 0.00249 ×real-road), **CdA 0.230** (back-solved from coasting
  tuck 0.219), **mass 90.5 kg** (rider 78 + bike ~10 + 2.5 avg consumables).
- `scripts/cda_descent_solve.py` → `processed/cda_descent_solve.json`: back-solves
  CdA from real >46.8 km/h samples + that-day wind. Coasting solve = 0.219.
- `scripts/analyze_climbing.py` → `processed/climbing_profile.json`: Sam's
  power-by-grade signature (114% NP on >8% climbs → 28% on descents, HR flat).
- `scripts/physics_sim.py` `simulate_ride_the_course`: variable power by gradient,
  NP-matched → **~3 min free** vs constant (`*_rtc` scenarios).
- `scripts/analyze_run.py` → `processed/run_prediction.json`: run model recentred
  on the **sub-3 goal** + gap analysis.
- `processed/race_projection.json`: full-day estimate.
- `scripts/physics_sim.py` + `web/physics.js`: defaults synced (mass 90.5, Crr
  0.0040); added 20 m-drafting + ride-the-course scenarios.
- **Bike (ridden to course): ~4:38 @ NP 293** (solo constant 4:44; 4:32 calm /
  5:03 windy). **Run: durability-calibrated off the May-24 datapoint (+3 bpm HR
  cost fatigued → IM penalty 20 s/km): current ceiling 3:08, target 3:12; sub-3
  is a GOAL needing LT1 4:13→~3:56 (gap ~17 s/km).** **Full day: target ~8:44,
  stretch (sub-3 run) ~8:29, best 8:33, vs 2025 8:45 — target now edges under 2025.**
- The **May-24 run file** (was "missing") was found loose in the repo root, filed
  into `data/sam_workouts/` + manifest, and folded into the run model.
- **2025 4:27 is NOT an anchor**: reproducing it at his measured 290 W needs
  CdA 0.18–0.21 (impossible) → it was ~15–20 % draft-aided (old 12 m rule).
- **Key insight**: the bike-fitness gain shows up in the RUN (290 W is now IF
  0.77 not 0.85); the sub-3 run is the lever, gated by an LT1 lift.
- `RESEARCH_WISHLIST.md`: Tier-1 papers to firm up draft %, IM-run penalty, heat.

**Open confirms with Sam (lower stakes now, but worth nailing):**
1. **Power-meter calibration** — powered descent solve (0.267) > coasting (0.219)
   hints the meter may read a few % high (would lower FTP + race NP). Static test.
2. **Carried-fluid strategy** — is "4 L water" carried (heavy) or total consumed?
   Affects avg mass (90.5 assumes ~2.5 kg avg carried).
3. **Target intensity** — IF 0.72/0.76/0.78.
4. **Chung VE solve** on a flat sustained-aero segment to confirm CdA 0.230.

Also: the **May 24** run still has context but **no file** (`manifest.json` →
`pending_no_file`).

---

## Status snapshot

The simulator is built, working, and **already well-calibrated** for Sam at Cairns:

- **v2 physics** is live in both Python (`scripts/physics_sim.py`) and JS
  (`web/physics.js`). Includes wind-gradient correction, yaw-dependent CdA,
  aero/sitting posture switching at 19.3 km/h, and slow-corner caps.
- **Model accuracy vs Sam's 2025 actual bike split** (4:27:14):
  prediction = 4:26:57 at his stated 340W / CdA 0.258 / actual 2025 weather.
  Delta = **17 seconds (0.1%)**. This is Best-Bike-Split-grade accuracy.
- **2D viewer** (`web/index.html`) and **3D viewer** (`web/3d.html`) both work.
  3D needs Mapbox token in `web/config.local.js` (already set, gitignored).
- **README.md** is the canonical project doc (~815 lines).

---

## What's pending: Sam's TrainingPeaks workout files

The user (Alex Westover) is uploading Sam's TP workout files in the next few
hours. These are the gating item for pinning the model's last remaining
placeholder parameters.

**Likely formats:** `.fit`, `.tcx`, or `.pwx` exports. All three are parseable.

**Where to put them:** create `data/sam_workouts/` and drop them in. Don't
mix with race-course TCX files in `data/`.

---

## The plan when TP files arrive

### Step 1 — Parse and inventory

Write `scripts/parse_workouts.py` that loads each TP file and extracts:
- Power (watts) per timestamp
- Heart rate per timestamp
- Cadence per timestamp
- Speed (m/s) per timestamp
- Altitude per timestamp
- GPS lat/lng (if outdoor)
- Workout metadata (date, duration, type, "training" vs "race")

Output a clean per-workout JSON to `processed/workouts/<workout_id>.json`.

### Step 2 — Identify Sam's 2025 race file specifically

The 2025 IM Cairns race itself (June 15, 2025, ~8:45 total time) is the
single highest-value file. If present, flag it and use it as the
**ground-truth calibration target** since we already have his observed times
in `processed/sam_2025_results.json`.

### Step 3 — Extract Sam's actual race-day parameters

From the 2025 race workout, compute:

| Parameter | How to extract |
|---|---|
| **Avg power (bike leg)** | Mean of power channel during the bike portion |
| **Normalized power** | 30s rolling avg, 4th-power mean, 4th-root — TrainingPeaks formula |
| **Avg cadence** | Mean of cadence channel |
| **Total race mass** | Best estimate from workout metadata, or ask user |
| **Aero-position dwell** | Cluster speed vs cadence patterns; high cad + high speed = aero |

Compare measured average power to our model's back-solved estimate of **339W**
(at CdA 0.258 / mass 75kg / Crr 0.004). Any gap tells us how much mass and Crr
need to shift.

### Step 4 — Chung indirect-CdA calibration

Use Robert Chung's virtual-elevation method (described in
`README.md` → "Lessons learned" → research links) to back-solve Sam's
actual CdA on a known segment:

1. Pick a clean stretch (long, well-defined start/end, ideally flat-to-rolling)
2. Given measured power + speed + measured altitude profile, solve for the CdA
   that makes simulated altitude match measured altitude
3. Repeat across multiple workouts — consistency of the result validates the method
4. Update Sam's "true" CdA in the model

### Step 5 — Recalibrate and re-validate

With pinned mass + Crr + CdA from steps 3-4:
1. Re-run `scripts/calibrate_against_2025.py` and verify the model predicts
   Sam's 4:27:14 within ±15 seconds with NO power adjustment needed.
2. If yes, the model is now genuinely calibrated. Update `README.md`
   accordingly (replace the "75 kg / 0.004 / 0.258" placeholders with
   measured values).
3. If no, identify which parameter is still off and iterate.

### Step 6 — 2026 predictive forecast

With a calibrated model, use it to answer Sam's actual planning questions:
- "What bike split should I target for 2026 at my current fitness + CdA 0.238?"
- "What's the variance across plausible 2026 weather scenarios?"
- "How much does a 5W power gain buy me vs a 0.005 CdA gain?"

These become trustworthy quantitative answers, not just educated guesses.

---

## Active conventions / decisions (don't re-litigate)

These were settled this session — flag in conversation only if there's
new evidence to revisit:

- **Fork-per-race when scaling, not parameterize.** When the next race
  (post-Cairns 2026) comes up, copy this whole folder to
  `sam_<race>_project/` and adapt. See `README.md` → "Playbook" for steps.
- **Mapbox token** stays in `web/config.local.js` (gitignored). URL-restrict
  in dashboard to `http://localhost:*` for blast-radius safety.
- **No multi-race UI selector.** Decided against URL params (`?race=...`)
  because each race has unique physics quirks that don't generalize well.
- **2025 data is reference only, not wired into the UI.** User explicitly
  declined adding a "2025 Actual" preset to the 3D viewer.
- **Riding-line offset (left of travel in AU)** was analyzed and
  rejected — net delta is 10 sec / 0.065%, below model noise floor.
  See conversation log if needed; no files saved.
- ~~**Run physics is out of scope**~~ — SUPERSEDED 2026-05-28: a physiology-based
  run pacing model now exists (`scripts/analyze_run.py` → `processed/run_prediction.json`).
  The bike *physics sim* is still bike-leg only; the run is paced, not simulated.

---

## Files added this session that future-you should know about

| Path | What it is |
|---|---|
| `processed/sam_2025_results.json` | Sam's full 2025 race timings (swim/T1/bike/T2/run, every split). Extracted from screenshots in `2025_results/`. |
| `processed/weather_hourly_2025.json` | Open-Meteo archive pull for 2025-06-15 race day at all three race locations. |
| `processed/calibration_2025.json` | Back-solve output: estimated effective race-day power per CdA assumption. |
| `scripts/pull_2025_weather.py` | Re-runnable 2025 weather pull. |
| `scripts/calibrate_against_2025.py` | Re-runnable inverse calibration; matches inline analysis in conversation. |
| `README.md` | Full project documentation, ~815 lines. |
| `HANDOFF.md` | This file. |

Everything else is from earlier in the session and documented in the README.

---

## Quick-orient commands for the new session

```bash
cd /Users/awestover/Documents/WSG/sam_cairns_project

# Server (if not already running)
python3 -m http.server 8765 > /tmp/cairns_server.log 2>&1 &
echo $! > /tmp/cairns_server.pid

# Verify current sim still runs cleanly
python3 scripts/physics_sim.py | head -20

# Verify 2025 calibration still passes
python3 scripts/calibrate_against_2025.py | head -10
```

If `physics_sim.py` outputs the v2 scenarios block and `calibrate_against_2025.py`
shows the model predicting 4:26:57 at 340W/0.258 (vs Sam's actual 4:27:14),
you're picked up where we left off.

---

## Open questions to verify with the user when they come back

1. **TP file location/format** — `data/sam_workouts/`? `.fit` files okay or do
   they prefer something else?
2. **Sam's race-day mass** — get this from the TP file metadata or directly
   from him? Total mass including bottles + nutrition matters.
3. **Calibration scope** — calibrate against only the 2025 IM Cairns workout,
   or use multiple workouts (training rides too) for a more robust fit?
4. **Confidence level** — how much accuracy is "enough"? We're at 0.1%
   already; pushing to 0.05% requires more parameter pinning but adds
   diminishing returns.

---

## Recent bug saga summary (so it doesn't recur)

The two big bugs we hit this session, both in the 3D viewer's particle system:

1. **Canvas zero-size on Mapbox layout disturbances** → fixed by mounting the
   wind canvas inside `map.getCanvasContainer()` instead of `#map3d`, plus
   defensive guards in `resize()` and a `matchMedia` DPR-change listener.
2. **Upwind respawn bias** → at default 17.5 km/h wind, 97% of particle
   respawns were going to the upwind edge (`upwindBias = wind/18 → 0.97`),
   slowly emptying the canvas interior. Fixed by splitting respawn by *cause*:
   aged-out particles respawn at random interior, drift-off-canvas particles
   respawn at upwind edge.

Both are documented in `README.md` → "Lessons learned" → "Bugs we hit."

---

## Closing note

The model is in genuinely good shape. The remaining work is calibration
polish, not architectural lift. Once Sam's TP files land, plan on a focused
2-3 hour session to do steps 1-4 above, then we can confidently use this for
race-day planning.

— end of handoff —
