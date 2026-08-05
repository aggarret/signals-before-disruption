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

## Tech Stack

- Python 3.10, Dash 4.4.1, Plotly 6.9.0
- dash-bootstrap-components 2.0.4, dash-mantine-components 2.8.0
- DuckDB 1.5.5 (serving), polars 1.43.2 (pipeline)
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
ingest_daily.py        # USGS API fetcher (CLI, cache-aware)
update_data.py         # Incremental data update orchestrator (atomic writes)
stations.csv           # 52 verified USGS gauges
data/                  # Parquet layers (~32 MB)
```

## License

Data source: USGS Water Data (public domain). Code: MIT.
