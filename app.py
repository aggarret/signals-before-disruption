"""app.py — River Personality Monitor: multipage Dash application (Pass E).

Two pages (Dash ``register_page`` system, ``use_pages`` enabled):

    /        Dashboard — the full seven-panel dark-slate monitor:
             header → KPI row → (map | region table + fastest risers)
             → hydrograph → personality cards → raw-data drawer → footer
    /guide   Guide — placeholder (being implemented by another agent)

Page modules live in ``pages/`` (imported by ``app.enable_pages()`` at the
bottom of this module, after every symbol they import is defined) and own all
callbacks: each component's own ``register_callbacks`` handles its internal
interactivity (map clicks, range buttons, drawer open/close, CSV export,
region-row selection), and the dashboard page's integration callbacks keep the
state-store mirrors (``selected-metric``, ``selected-date``,
``selected-station``, ``selected-region``, ``date-range``) in sync with the
panels' controls and re-render the sections that have no internal callbacks
(KPI cards, region table, personality cards, raw-drawer contents) whenever
their inputs change.

Data access: one shared read-only DuckDB connection (queries.get_connection)
created at import; the parquet dataset is immutable during serving, so this is
thread-safe for every callback.

Run locally:   python3 app.py            -> http://localhost:8050
Deploy:        gunicorn app:server       (WSGI entry for Cloud Run)
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Dict, Optional

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# When run as a script (`python3 app.py`) this module is `__main__`, but the
# page modules do `from app import ...`. Without this alias that import would
# re-execute this file as a SECOND module named "app" — a second Dash app, a
# second DuckDB connection, and callbacks registered on the wrong instance.
# Aliasing __main__ as "app" makes `from app import ...` resolve to THIS
# module in every run mode (script, gunicorn app:server, `import app`).
if __name__ == "__main__":
    sys.modules.setdefault("app", sys.modules["__main__"])

import pandas as pd
import dash
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
from dash import Dash, dcc, html

import queries
import cloud_boot
import data_manager
from components import map_panel

# ---------------------------------------------------------------------------
# Startup data (queried once — the parquet is immutable during serving)
# ---------------------------------------------------------------------------
conn = queries.get_connection()

DEFAULT_METRIC = "streamflow"
# Last date with broad gauge coverage (map / KPI / region default view). The
# ingest's final day is usually partial, so this scans back for a date where
# >= 50% of streamflow gauges report.
DEFAULT_DATE = str(map_panel.get_default_date(conn))

# Dataset-wide facts for the header / footer.
_METRICS = ("streamflow", "gage_height", "water_temperature")
TOTAL_ROWS = sum(
    int(queries._dataset_stats(conn, m)["total_rows"]) for m in _METRICS
)
N_GAUGES = int(conn.execute("SELECT count(*) FROM stations").fetchone()[0])

_latest = conn.execute(
    f"SELECT MAX(observed_at) FROM read_parquet('{queries._EM_GLOB}')"
).fetchone()[0]
LATEST_DATA_DATE = str(pd.Timestamp(_latest).date())

# entity_id -> station_name, loaded once (used for header / drill-downs).
STATION_NAMES: Dict[str, str] = dict(
    pd.read_csv(os.path.join(_ROOT, "stations.csv"))
    .set_index("entity_id")["station_name"]
    .to_dict()
)

# ---------------------------------------------------------------------------
# Stale-data safety net: if the dataset has not been updated since the
# scheduled 07:00 local update time, kick off a background refresh now. The
# app keeps serving the current (old) data while update_data.py runs; the
# query caches are invalidated only after the update completes, so no callback
# ever sees a partial dataset. Never blocks, never raises.
# ---------------------------------------------------------------------------
# Cloud Run (GCS_BUCKET set): the background safety-net update is disabled — a
# separate Cloud Run job refreshes the data and writes back to GCS, and
# cloud_boot.py already synced the current dataset into ./data/ at startup.
if os.environ.get("GCS_BUCKET"):
    print("app: GCS_BUCKET set — skipping data_manager.ensure_fresh_data() (cloud mode)")
    if not cloud_boot.wait_until_ready(conn):
        print("app: WARNING — cloud boot readiness probe timed out; serving anyway")
    else:
        print("app: cloud boot readiness confirmed — data queryable")
else:
    data_manager.ensure_fresh_data()

# ---------------------------------------------------------------------------
# Cloud-mode freshness poll: the daily Cloud Run job republishes data to GCS,
# but cloud_boot only mirrors it at cold start. A warm instance would otherwise
# keep serving its boot-time dataset forever. This lets the serving process
# detect a new publication (via UPDATE_LOG.md's GCS generation) and re-sync
# without a redeploy. Driven by a dcc.Interval in the dashboard page.
# ---------------------------------------------------------------------------
_GCS_GEN: Optional[int] = None  # last-seen UPDATE_LOG.md generation
_REFRESH_LOCK = threading.Lock()  # prevents overlapping concurrent GCS re-syncs


def refresh_data_if_stale() -> Optional[str]:
    """Poll GCS for a new dataset publication; re-sync if one is found.

    Cloud-mode only (no-op without GCS_BUCKET). Returns the new
    LATEST_DATA_DATE (str) when the dataset changed, else None. On the first
    call after a cold start it just records the baseline generation (cloud_boot
    already mirrored the current dataset at boot), so no redundant sync runs.
    """
    global LATEST_DATA_DATE, DEFAULT_DATE, TOTAL_ROWS, _GCS_GEN
    bucket = os.environ.get("GCS_BUCKET")
    if not bucket:
        return None

    gen = cloud_boot.get_update_generation(bucket)
    if gen is None:
        return None
    if _GCS_GEN is None:
        _GCS_GEN = gen  # baseline after boot sync
        return None
    if gen == _GCS_GEN:
        return None

    if not _REFRESH_LOCK.acquire(blocking=False):
        return None  # a re-sync is already in flight for this 60s tick; skip
    try:
        _GCS_GEN = gen
        print(f"app: detected new GCS dataset (generation {gen}) — re-syncing")
        cloud_boot.sync_from_gcs(bucket)
        queries.invalidate_caches()
        DEFAULT_DATE = str(map_panel.get_default_date(conn))
        _latest = conn.execute(
            f"SELECT MAX(observed_at) FROM read_parquet('{queries._EM_GLOB}')"
        ).fetchone()[0]
        LATEST_DATA_DATE = str(pd.Timestamp(_latest).date())
        TOTAL_ROWS = sum(
            int(queries._dataset_stats(conn, m)["total_rows"]) for m in _METRICS
        )
        print(f"app: re-sync complete — LATEST_DATA_DATE={LATEST_DATA_DATE}")
        return LATEST_DATA_DATE
    finally:
        _REFRESH_LOCK.release()

# App-level store ids (mirrors of the panels' own controls / stores). Kept at
# app level (as in the original single-page app) and imported by
# pages/dashboard.py.
_ID_METRIC_STORE = "selected-metric"
_ID_DATE_STORE = "selected-date"
_ID_STATION_STORE = "selected-station"
_ID_REGION_STORE = "selected-region"
_ID_RANGE_STORE = "date-range"

# Section containers re-rendered by the integration callbacks.
_ID_KPI_CONTAINER = "kpi-cards-container"
_ID_REGION_CONTAINER = "region-table-container"
_ID_PERSONALITY_CONTAINER = "personality-cards-container"
_ID_DRAWER_CONTAINER = "raw-drawer-container"

# Muted text shades (match assets/style.css palette).
TEXT_MUTED = "#94a3b8"
TEXT_FAINT = "#64748b"

# ---------------------------------------------------------------------------
# App + server
# ---------------------------------------------------------------------------
# NOTE: `use_pages` is intentionally NOT passed to the constructor. Dash 4.4.1
# imports page modules eagerly inside Dash.__init__ (init_app -> enable_pages),
# i.e. before the module-level `app` name below is bound, so a page doing
# `from app import app` would hit a partially-initialized module. Instead we
# flip `app.use_pages` on and call `app.enable_pages()` at the bottom of this
# module, once `app` and every symbol the pages import already exist.
app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    assets_folder="assets",
    # Panels (map, strip, small-multiples, drill-down) are rendered INTO their
    # containers by callbacks, so their inner graph ids (hydro-map-graph,
    # hydro-strip-graph, ...) are NOT present in the initial layout. Allow
    # callbacks to reference those late-created components.
    suppress_callback_exceptions=True,
)
server = app.server  # WSGI entry for gunicorn / Cloud Run

# Mantine dark theme (matches the existing slate-900 palette).
_MANTINE_THEME = {
    "primaryColor": "teal",
    "colorScheme": "dark",
    "fontFamily": "Inter, -apple-system, Segoe UI, Roboto, sans-serif",
    "defaultRadius": "md",
}

# App-level store ids (mirrors of the panels' own controls / stores).
_ID_METRIC_STORE = "selected-metric"
_ID_DATE_STORE = "selected-date"
_ID_STATION_STORE = "selected-station"
_ID_REGION_STORE = "selected-region"
_ID_RANGE_STORE = "date-range"

# Section containers re-rendered by the dashboard page's integration callbacks.
_ID_KPI_CONTAINER = "kpi-cards-container"
_ID_REGION_CONTAINER = "region-table-container"
_ID_PERSONALITY_CONTAINER = "personality-cards-container"
_ID_DRAWER_CONTAINER = "raw-drawer-container"

# Muted text shades (match assets/style.css palette).
TEXT_MUTED = "#94a3b8"
TEXT_FAINT = "#64748b"


def _navbar() -> dbc.NavbarSimple:
    """Top navigation shared by all pages."""
    return dbc.NavbarSimple(
        children=[
            dbc.NavItem(dbc.NavLink("Dashboard", href="/", active="exact")),
            dbc.NavItem(dbc.NavLink("Hydro Coupling", href="/hydro", active="exact")),
            dbc.NavItem(dbc.NavLink("Guide", href="/guide", active="exact")),
        ],
        brand="🌊 River Personality Monitor",
        brand_href="/",
        color="#0f172a",
        dark=True,
        style={"borderBottom": "1px solid #334155"},
    )


# ---------------------------------------------------------------------------
# Page container: shared navbar + routed page content
# ---------------------------------------------------------------------------
app.layout = dmc.MantineProvider(
    theme=_MANTINE_THEME,
    children=[
        dcc.Location(id="url", refresh=False),
        _navbar(),
        dash.page_container,
    ],
)

# ---------------------------------------------------------------------------
# Multipage routing: import pages/ and register the page router.
# (See the NOTE at the Dash construction above for why this happens here.)
# ---------------------------------------------------------------------------
app.use_pages = True
app.enable_pages()


if __name__ == "__main__":
    app.run(debug=True, port=8050)