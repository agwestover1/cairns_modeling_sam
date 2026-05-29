# Deploy to GitHub Pages — morning runbook

Goal: put this project on GitHub and turn on **GitHub Pages** so Sam can open a
URL (2D simulator works fully; 3D needs a Mapbox token, see Step 5).

Everything is static (HTML/JS/JSON) — no backend, no build step. Pages just
serves the files. Estimated time: **~15 minutes.**

Prep already done for you:
- `.gitignore` now excludes Sam's **raw** files (`.fit`, result screenshots,
  PDFs) so they won't be published. The viewer doesn't need them.
- A landing page (`index.html` at the repo root) links to the 2D and 3D views,
  so the bare Pages URL is friendly.

---

## ⚠️ Read first: privacy

**GitHub Pages sites are publicly viewable, even from a private repo** (on the
free tier the published site is public regardless). Anything the viewer loads —
the course data **and Sam's projections / FTP / power curve in
`processed/sim_results.json` + `processed/race_projection.json`** — is readable
by anyone who has the URL. The URL is obscure, not secret.

If that's fine → proceed. If Sam's numbers must stay private, options are:
- Send the **single self-contained file** instead (ask me to build it), or
- Host on **Netlify/Cloudflare Pages with password protection** (Access), or
- Strip the projection JSONs and show course-only (defeats the purpose).

Assuming public-Pages is OK, continue.

---

## Step 1 — Initialize the repo

```bash
cd /Users/awestover/Documents/WSG/sam_cairns_project
git init -b main
git add .
git status        # sanity-check: NO .fit / screenshots / PDFs should be listed
git commit -m "Ironman Cairns race simulator — viewer + calibrated model"
```

`git status` should show `web/`, `processed/*.json`, `scripts/`, `index.html`,
the `.md` docs — but **not** `data/sam_workouts/*.fit`, `2025_results/`, or PDFs.
If any sensitive file appears, stop and tell me.

## Step 2 — Create the GitHub repo and push

**Option A — GitHub CLI (fastest):**
```bash
gh auth login            # if not already logged in
gh repo create sam-cairns-sim --public --source=. --remote=origin --push
```
(Use `--private` instead of `--public` if you prefer — Pages still works, but
see the privacy note: the *site* is public either way.)

**Option B — github.com UI:** create an empty repo named `sam-cairns-sim`
(no README), then:
```bash
git remote add origin https://github.com/<your-username>/sam-cairns-sim.git
git push -u origin main
```

## Step 3 — Turn on GitHub Pages

On github.com → your repo → **Settings → Pages**:
- **Source**: "Deploy from a branch"
- **Branch**: `main`, folder `/ (root)` → **Save**

Wait ~1 minute. Pages will show your live URL:
```
https://<your-username>.github.io/sam-cairns-sim/
```

## Step 4 — Send Sam the link

- Landing page: `https://<you>.github.io/sam-cairns-sim/`
- Straight to 2D sim: `https://<you>.github.io/sam-cairns-sim/web/index.html`

The **2D view is fully functional** out of the box (course map, bike simulator,
ride-the-course scenarios, full-day projection + swim slider). Map tiles load
from the internet, so Sam just needs to be online.

## Step 5 — (Optional) Enable the 3D view

3D uses Mapbox and needs a token, which is currently in the **gitignored**
`web/config.local.js` (not published). To make 3D work on the live site:

1. Get a free token at <https://account.mapbox.com/access-tokens/>.
2. **Restrict it** (Mapbox dashboard → token → URL restrictions) to:
   `https://<your-username>.github.io/*` — so it only works on your site.
3. Put the token in a **committed** config the site can read. Easiest:
   edit `web/config.js` (currently committed with an empty token) to:
   ```js
   window.MAPBOX_TOKEN = "pk.your_restricted_token_here";
   ```
   Then `git add web/config.js && git commit -m "Add URL-restricted Mapbox token" && git push`.

A URL-restricted public Mapbox token is safe to commit — it only works from your
github.io domain. If you skip this, the 2D view still works; 3D just shows a
"token needed" message.

---

## Updating later (after new workouts / recalibration)

```bash
# regenerate the data the viewer reads
python3 scripts/analyze_fitness.py
python3 scripts/cda_descent_solve.py
python3 scripts/analyze_climbing.py
python3 scripts/analyze_run.py
python3 scripts/physics_sim.py
# publish
git add processed/*.json web/
git commit -m "Refresh projections"
git push
```
Pages redeploys automatically within ~1 minute.

---

## What gets published (so you know)

**Included:** `index.html`, `web/` (viewer), **all** `processed/*.json` (34 files),
`scripts/`, and the `.md` docs (README/HANDOFF/RESEARCH_WISHLIST).
**Excluded (gitignored):** raw `.fit`/`.FIT` workouts (anywhere), `data/sam_workouts/`
(incl. Sam's verbatim notes + manifest), `2025_results/` screenshots, PDFs/DOCX,
`web/config.local.js`. Keep these locally — scripts still re-run.

### Optional — minimal / less-revealing publish
The interactive viewer needs only **these 8** of the 34 processed files:
`swim_course.json`, `bike_course.json`, `run_course.json`, `climbs.json`,
`course_info.json`, `weather_seed_defaults.json`, `sim_results.json`,
`race_projection.json`. The other 26 (full power curve `sam_fitness.json`, run
paces `run_prediction.json`, 2025 race timings `sam_2025_results.json`, weather
archives, descent CdA samples, sim-splits) are **analysis detail Sam's data** the
site doesn't load. To keep them off a public site, add to `.gitignore`:
```
processed/*.json
!processed/swim_course.json
!processed/bike_course.json
!processed/run_course.json
!processed/climbs.json
!processed/course_info.json
!processed/weather_seed_defaults.json
!processed/sim_results.json
!processed/race_projection.json
```
You can likewise exclude `HANDOFF.md` / `RESEARCH_WISHLIST.md` (internal notes)
if you only want the viewer + README public. Your call — tell me and I'll set it.
