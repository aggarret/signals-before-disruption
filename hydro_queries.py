"""hydro_queries.py — DuckDB data-access layer for the HYDRO COUPLING page.

Exposes the hydro-coupling analysis (monthly streamflow vs. hydro generation)
filtered to TIGHT-tier gauges. Mirrors the conventions of ``queries.py``:
every public function takes an optional DuckDB connection and returns a pandas
DataFrame; connections are read-only and cached per-thread.

Data sources (all under hydro_correlation/):
  - correlation_final.csv  : per-gauge coupling stats (spearman/pearson, best_lag, tier)
  - gauge_geo.csv          : gauge metadata incl. lat/long, station_name, state, region
  - gauge_location.csv     : gauge -> EIA location mapping (+ eia_location_name)
  - aligned_pairs.parquet  : matched monthly series 2004-01 .. 2026-05
                             (period, mean_flow_cfs, generation_thousand_mwh)

TIGHT tier = 14 gauges, all with |spearman_anom| >= 0.5.
"""

from __future__ import annotations

import os
import tempfile
import threading
from typing import Optional

import duckdb
import pandas as pd

# ---------------------------------------------------------------------------
# Paths (absolute, relative to this module so CWD never matters)
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
_HYDRO_DIR = os.path.join(ROOT, "hydro_correlation")

_CORR_FINAL = os.path.join(_HYDRO_DIR, "correlation_final.csv")
_GAUGE_GEO = os.path.join(_HYDRO_DIR, "gauge_geo.csv")
_GAUGE_LOC = os.path.join(_HYDRO_DIR, "gauge_location.csv")
_ALIGNED_PAIRS = os.path.join(_HYDRO_DIR, "aligned_pairs.parquet")

# Read-only DuckDB requires an existing empty db file; keep it in the OS temp
# dir (never inside the project), matching queries.py's approach.
_DB_PATH = os.path.join(tempfile.gettempdir(), "signals-before-disruption-hydro.duckdb")

_thread_local = threading.local()
_DB_FILE_LOCK = threading.Lock()
_TIGHT_CACHE: Optional[pd.DataFrame] = None


def _ensure_db_file() -> None:
    if os.path.exists(_DB_PATH):
        return
    with _DB_FILE_LOCK:
        if not os.path.exists(_DB_PATH):
            c = duckdb.connect(_DB_PATH)
            c.close()


def _register(conn: duckdb.DuckDBPyConnection) -> None:
    """Register all hydro sources as DuckDB relations on this connection.

    Uses ``conn.register`` with scanner relations (not ``CREATE VIEW``) so it
    works on the persistent read-only connection that queries.py-style serving
    relies on.
    """
    conn.register("corr_final", conn.from_csv_auto(_CORR_FINAL))
    conn.register("gauge_geo", conn.from_csv_auto(_GAUGE_GEO))
    conn.register("gauge_loc", conn.from_csv_auto(_GAUGE_LOC))
    conn.register("aligned_pairs", conn.from_parquet(_ALIGNED_PAIRS))


def _ensure_registered(conn: duckdb.DuckDBPyConnection) -> None:
    """Register the hydro sources on an arbitrary connection if not already."""
    try:
        conn.execute("SELECT count(*) FROM gauge_geo WHERE 1=0").fetchone()
    except Exception:
        _register(conn)


def _conn_impl() -> duckdb.DuckDBPyConnection:
    """Per-thread read-only DuckDB connection with hydro views registered."""
    conn = getattr(_thread_local, "hydro_conn", None)
    if conn is None:
        _ensure_db_file()
        conn = duckdb.connect(_DB_PATH, read_only=True)
        _register(conn)
        _thread_local.hydro_conn = conn
    return conn


def _conn(conn: Optional[duckdb.DuckDBPyConnection] = None) -> duckdb.DuckDBPyConnection:
    """Resolve the caller-supplied connection, or fall back to the module one."""
    if conn is not None:
        # Ensure sources are registered even if the caller passed a bare conn.
        _ensure_registered(conn)
        return conn
    return _conn_impl()


# ---------------------------------------------------------------------------
# Public queries
# ---------------------------------------------------------------------------
def get_tight_gauges(
    conn: Optional[duckdb.DuckDBPyConnection] = None,
) -> pd.DataFrame:
    """The 14 TIGHT-tier gauges with geo metadata.

    Columns: entity_id, eia_location, eia_location_name, state, region,
             station_name, latitude, longitude, spearman_anom, pearson_anom,
             best_lag, tier, n_months.
    Sorted by |spearman_anom| DESC (strongest coupling first).
    """
    c = _conn(conn)
    df = c.execute(
        """
        SELECT cf.entity_id,
               cf.eia_location,
               gl.eia_location_name,
               gg.state,
               gg.region,
               gg.station_name,
               gg.latitude,
               gg.longitude,
               cf.spearman_anom,
               cf.pearson_anom,
               cf.best_lag,
               cf.tier,
               cf.n_months
        FROM corr_final cf
        LEFT JOIN gauge_geo gg ON cf.entity_id = gg.entity_id
        LEFT JOIN gauge_loc  gl ON cf.entity_id = gl.entity_id
        WHERE cf.tier = 'tight'
        ORDER BY abs(cf.spearman_anom) DESC
        """
    ).df()

    float_cols = ["latitude", "longitude", "spearman_anom", "pearson_anom"]
    for col in float_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["best_lag"] = pd.to_numeric(df["best_lag"], errors="coerce").astype("Int64")
    df["n_months"] = pd.to_numeric(df["n_months"], errors="coerce").astype("Int64")
    return df.reset_index(drop=True)


def get_ranked_coupling(
    conn: Optional[duckdb.DuckDBPyConnection] = None,
) -> pd.DataFrame:
    """Compact ranked coupling strip for the 14 TIGHT-tier gauges.

    Columns: entity_id, station_name, eia_location, spearman_anom, pearson_anom,
             best_lag, tier.
    Sorted by |spearman_anom| DESC.
    """
    c = _conn(conn)
    df = c.execute(
        """
        SELECT cf.entity_id,
               gg.station_name,
               cf.eia_location,
               cf.spearman_anom,
               cf.pearson_anom,
               cf.best_lag,
               cf.tier
        FROM corr_final cf
        LEFT JOIN gauge_geo gg ON cf.entity_id = gg.entity_id
        WHERE cf.tier = 'tight'
        ORDER BY abs(cf.spearman_anom) DESC
        """
    ).df()

    for col in ("spearman_anom", "pearson_anom"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["best_lag"] = pd.to_numeric(df["best_lag"], errors="coerce").astype("Int64")
    return df.reset_index(drop=True)


def get_gauge_series(
    conn: Optional[duckdb.DuckDBPyConnection] = None,
    entity_id: Optional[str] = None,
) -> pd.DataFrame:
    """Matched monthly series for ONE gauge (drill-down source).

    Columns: entity_id, period, mean_flow_cfs, generation_thousand_mwh, confidence.
    Sorted by period ascending. Returns an empty frame when `entity_id` is None
    or has no aligned pairs.
    """
    if entity_id is None:
        return pd.DataFrame(
            columns=["entity_id", "period", "mean_flow_cfs",
                     "generation_thousand_mwh", "confidence"]
        )
    c = _conn(conn)
    df = c.execute(
        """
        SELECT entity_id, period, mean_flow_cfs, generation_thousand_mwh, confidence
        FROM aligned_pairs
        WHERE entity_id = ?
        ORDER BY period
        """,
        [entity_id],
    ).df()

    df["period"] = df["period"].astype(str)
    df["mean_flow_cfs"] = pd.to_numeric(df["mean_flow_cfs"], errors="coerce").astype("float64")
    df["generation_thousand_mwh"] = pd.to_numeric(
        df["generation_thousand_mwh"], errors="coerce"
    ).astype("float64")
    return df.reset_index(drop=True)


def get_eia_hydro(
    conn: Optional[duckdb.DuckDBPyConnection] = None,
    eia_location: Optional[str] = None,
) -> pd.DataFrame:
    """Monthly hydro generation for an EIA location (context / shared axis).

    Columns: entity_id, eia_location, period, generation_thousand_mwh.
    Aggregated across gauges mapped to that location (a single location may back
    several gauges). Sorted by period ascending.
    """
    if eia_location is None:
        return pd.DataFrame(
            columns=["entity_id", "eia_location", "period", "generation_thousand_mwh"]
        )
    c = _conn(conn)
    df = c.execute(
        """
        SELECT ap.entity_id, gl.eia_location, ap.period,
               ap.generation_thousand_mwh
        FROM aligned_pairs ap
        LEFT JOIN gauge_loc gl ON ap.entity_id = gl.entity_id
        WHERE gl.eia_location = ?
        ORDER BY ap.period, ap.entity_id
        """,
        [eia_location],
    ).df()

    df["period"] = df["period"].astype(str)
    df["generation_thousand_mwh"] = pd.to_numeric(
        df["generation_thousand_mwh"], errors="coerce"
    ).astype("float64")
    return df.reset_index(drop=True)


def set_tight_cache(df: Optional[pd.DataFrame]) -> None:
    """(Optional) warm the tight-gauge cache; not required for correctness."""
    global _TIGHT_CACHE
    _TIGHT_CACHE = df


def invalidate_caches() -> None:
    """Clear per-thread DuckDB connections so the next query re-registers from fresh files.

    Called by app.refresh_data_if_stale() after cloud_boot.sync_from_gcs() re-downloads
    the hydro_correlation/ files. Safe to call repeatedly; no-op when no connection exists.
    """
    global _TIGHT_CACHE
    conn = getattr(_thread_local, "hydro_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _thread_local.hydro_conn = None
    _TIGHT_CACHE = None


if __name__ == "__main__":
    tight = get_tight_gauges()
    print("get_tight_gauges rows:", len(tight))
    g = tight.iloc[0]["entity_id"]
    s = get_gauge_series(entity_id=str(g))
    print(f"sample gauge {g}: period {s['period'].min()}..{s['period'].max()}, rows {len(s)}")
