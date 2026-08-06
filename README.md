# River Personality Monitor

Characterizes river behavior across 52 USGS gauges using seasonal z-score anomalies, flashiness (Richards-Baker Index), and record proximity.

## Overview

- **52 USGS gauges** across 8 regions, 3 metrics (streamflow, gage height, water temperature)
- **20-year baseline** (2004–2023) with ±7-day circular day-of-year window
- **Anomaly detection**: z-scores (observed − seasonal mean) / seasonal std, |z| ≥ 2.5 threshold
- **Data pipeline**: USGS OGC Water Data API → polars → parquet (hive-partitioned) → DuckDB (serving) → Plotly Dash (frontend)

## Two-Page Dashboard

- **Dashboard** (`/`): Anomaly scorecards, monthly bar chart, national map, regional rollup, fastest risers, hydrograph drill-down, personality cards, raw data drawer
- **Guide** (`/guide`): Full app guide — purpose, use cases, component reference, color scale, methodology, tech stack

## Quick Start

```bash
cd signals-before-disruption
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 app.py
# → http://localhost:8050
```

## Data Update

### Manual
```bash
# Manual update (fetches new + revised data from USGS, rebuilds metrics)
.venv/bin/python3 update_data.py

# Dry run (fetch + compare, no writes)
.venv/bin/python3 update_data.py --dry-run

# Health check
.venv/bin/python3 scripts/healthcheck.py
```

### Scheduled (launchd)
A launchd job runs `update_data.py` daily at 7:00 AM PT. Plist: `~/Library/LaunchAgents/ai.openclaw.river-data-update.plist`.

### Auto-Fetch Safety Net
When the app starts and data hasn't been updated since 7 AM today, `data_manager.py` automatically triggers a background update via a daemon thread. The app continues serving from the old data until the update completes, then invalidates query caches to pick up the new data. This is a safety net — the launchd job is the primary scheduler.

### Atomic Writes
All parquet writes in `update_data.py`, `build_metrics.py`, and `build_category_metrics.py` use temp-file + `os.replace()` for atomic replacement. The app never reads a partially-written file during updates.

## Cloud Run Deployment

The app is deployed on Google Cloud Run with parquet data served from Google Cloud Storage (not baked into the image).

**Live URL:** https://signals-ox6jzm56ga-uc.a.run.app

### Architecture
- **Cloud Run service** (`signals`, us-central1): gunicorn `app:server`, 1 worker / 8 threads, 300s timeout, **2 CPU + 2Gi memory, min-instances: 1** (always-on, no cold starts). Image: `us-central1-docker.pkg.dev/river-personality-monitor/signals/signals:v3`
- **GCS bucket** (`river-personality-monitor-data`): holds all parquet layers + stations.csv + UPDATE_LOG.md. The container fetches them at startup via `cloud_boot.py` (Application Default Credentials).
- **Cloud Run job** (`daily-usgs-update`): runs `update_data.py` with `MODE=job` — fetches fresh data from USGS, rebuilds metrics, uploads serving artifacts back to GCS. 2Gi memory, 900s timeout.
- **Cloud Scheduler** (`daily-usgs-7am`): cron `0 7 * * *` (7 AM PT) triggers the Cloud Run job via HTTP POST.
- **Artifact Registry** (`signals`, us-central1): stores the container image.

### How it works
1. Container starts → `cloud_boot.py` syncs `gs://river-personality-monitor-data/` → local `data/` (idempotent, size-based skip).
2. `app.py` detects `GCS_BUCKET` env var → skips `data_manager.ensure_fresh_data()` (cloud mode: the job handles updates, not the serving container).
3. gunicorn serves the Dash app on port 8080.
4. Daily at 7 AM PT, Cloud Scheduler triggers the `daily-usgs-update` job → `update_data.py` fetches from USGS, rebuilds metrics, uploads to GCS. Next cold start picks up the fresh data.

### Local vs Cloud
| | Local (dev) | Cloud Run |
|---|---|---|
| Data source | `data/` on disk | GCS bucket → `data/` at startup |
| Updates | launchd 7 AM PT + `data_manager.py` safety net | Cloud Run job 7 AM PT |
| Writeback | Local parquet writes | GCS upload after update |
| `GCS_BUCKET` | unset | `river-personality-monitor-data` |
| `MODE` | unset | `service` (serving) or `job` (update) |

### Deploying
```bash
# Build + push image (from repo root)
gcloud builds submit --tag us-central1-docker.pkg.dev/river-personality-monitor/signals/signals:v4

# Deploy service
gcloud run deploy signals --image us-central1-docker.pkg.dev/river-personality-monitor/signals/signals:v4 \
  --region us-central1 --concurrency 8 \
  --set-env-vars GCS_BUCKET=river-personality-monitor-data,MODE=service

# Run update job manually
gcloud run jobs execute daily-usgs-update --region us-central1
```

## Tech Stack

- Python 3.12 (Cloud Run) / 3.10 (local), Dash 4.4.1, Plotly 6.9.0
- dash-bootstrap-components 2.0.4, dash-mantine-components 2.8.0
- DuckDB 1.5.5 (serving), polars 1.43.2 (pipeline)
- gunicorn 23.0.0 (Cloud Run), google-cloud-storage 2.18.2 (GCS sync)
- USGS Water Data OGC API (`https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items`)

## Project Structure

```
app.py                  # Dash app, MantineProvider, navbar, page container
pages/
    dashboard.py        # Dashboard page (layout + integration callbacks)
    guide.py            # Guide page (styled informational content)
components/             # 9 dashboard component modules
queries.py             # DuckDB query layer (11 functions)
build_metrics.py       # Seasonal baselines + entity metrics builder
build_category_metrics.py  # Regional rollup builder
data_manager.py        # Auto-fetch staleness check + background update trigger
cloud_boot.py          # GCS data sync at container startup (Cloud Run)
ingest_daily.py        # USGS API fetcher (CLI, cache-aware)
update_data.py         # Incremental data update orchestrator (atomic writes + GCS writeback)
Dockerfile             # Cloud Run container image (Python 3.12-slim + gunicorn)
.dockerignore          # Excludes data/, .venv, etc. from image
stations.csv           # 52 verified USGS gauges
data/                  # Parquet layers (~32 MB, not in image — fetched from GCS)
```

## License

Data source: USGS Water Data (public domain). Code: MIT.
