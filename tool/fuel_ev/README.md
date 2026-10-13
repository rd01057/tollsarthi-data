# TollSarthi Fuel/EV data pipeline

The app shows petrol pumps and EV chargers from **static files** — one small
`.json.gz` per map region (Geohash-4 cell, ~40km × 20km), refreshed weekly from
OpenStreetMap. The phone downloads only the regions it needs, so it's fast and
works offline afterwards.

This folder holds everything needed to run that pipeline:

| File | What it does |
|---|---|
| `generate_cells.py` | Downloads OSM fuel/charging POIs for India and writes `output/{cell}.json.gz` |
| `README.md` | This setup guide |
| `../.github/workflows/fuel-ev-weekly.yml` | GitHub Action: runs the script weekly and publishes the files on GitHub Pages |

**You only do this setup once.** After that it runs itself every Sunday.
No card, no secrets, no extra accounts — just GitHub.

---

## Step 1 — Create a PUBLIC repo on GitHub

1. Go to [github.com/new](https://github.com/new).
2. Name it e.g. `tollsarthi-data`. Choose **Public** — the data is public
   OpenStreetMap data anyway, and GitHub Pages needs a public repo on the
   free plan.
3. Don't tick "Add a README". Click **Create repository**.

## Step 2 — Push the project

Push the tollsarthi project folder to this repo (the `.github/workflows/`
folder must be included). If you've never pushed from the terminal:

```bash
cd /path/to/tollsarthi
git init
git add .
git commit -m "TollSarthi with fuel/EV pipeline"
git branch -M main
git remote add origin https://github.com/<your-username>/tollsarthi-data.git
git push -u origin main
```

Replace `<your-username>` with your GitHub username.

## Step 3 — Run the workflow once manually

1. Repo → **Actions** tab → click **Fuel/EV weekly refresh** on the left.
2. Click **Run workflow** → **Run workflow** (green button).
3. Wait ~10–20 minutes (it queries OpenStreetMap for all of India).
4. When it finishes, a new `gh-pages` branch appears in your repo — that
   holds the published files.

## Step 4 — Turn on GitHub Pages

1. Repo → **Settings** → **Pages** (left sidebar).
2. Under **Build and deployment**, Source: **Deploy from a branch**.
3. Branch: **gh-pages** / **/(root)**. Click **Save**.
4. Wait a minute or two. Your files are now live at:

```
https://<your-username>.github.io/tollsarthi-data/v1/
```

5. Open one file to verify, e.g.
   `https://<your-username>.github.io/tollsarthi-data/v1/tdr1.json.gz`
   (it downloads a small file — that's correct).

## Step 5 — Point the app at your site

In `lib/fuel/fuel_service.dart`, replace:

```dart
static const _urlTemplate =
    'https://fuel-ev.tollsarthi.example.com/v1/{cell}.json.gz';
```

with your real address (keep the `{cell}` placeholder exactly as shown):

```dart
static const _urlTemplate =
    'https://<your-username>.github.io/tollsarthi-data/v1/{cell}.json.gz';
```

Replace `<your-username>` with your GitHub username. Rebuild the APK
after this change.

---

## Testing the script locally (optional)

```bash
pip install requests
# small test area (South Delhi) — finishes in under a minute:
python tool/fuel_ev/generate_cells.py --out /tmp/fuel_test \
    --bbox "28.4,76.9,28.9,77.4"
ls /tmp/fuel_test
```

## Notes

- The script is polite to OpenStreetMap: 8 regional queries, 5s pause between them, automatic retry on rate limits. The full India run takes ~10–20 min.
- Files are only re-downloaded by the app when older than 7 days (`_staleAfter`).
- If a region has no fuel/charging POIs in OSM, no file is written — the app treats a 404 as "nothing here" and moves on.
- Old cell files are kept between runs (`keep_files`), so a region that temporarily fails a query never goes blank.
