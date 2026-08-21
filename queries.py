"""queries.py — DuckDB data-access layer for the River Personality Monitor Dash app.

All functions take a DuckDB connection (see `get_connection()`) and return pandas
DataFrames or plain dicts. No Dash code lives here; these queries serve every
callback in the frontend.

Performance notes:
- `daily_entity_metrics` / `daily_category_metrics` are hive-partitioned by
  metric=*/year=*; the `year` partition column is used for predicate pushdown.
- `raw_observations` stores per-year files (`metric=<m>/year=<YYYY>.parquet`), so
  payload lookups read a single file directly.
- Stations (52 rows) are loaded once at import and registered on the connection as
  a table — avoids ~8ms CSV re-parsing on every query.
- Static dataset stats (total rows, last date, gap rate) and per-gauge historical
  maxima are cached after first computation; the underlying parquet is immutable
  during serving, so caching is safe.
- A small LRU cache backs the daily date-slice (map/KPI) query.
"""

from __future__ import annotations

import os
import tempfile
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import duckdb
import pandas as pd

# ---------------------------------------------------------------------------
# Paths (absolute, relative to this module so CWD never matters)
# ---------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_EM_GLOB = os.path.join(_BASE_DIR, "data", "daily_entity_metrics", "metric=*", "year=*", "data.parquet")
_CM_GLOB = os.path.join(_BASE_DIR, "data", "daily_category_metrics", "metric=*", "year=*", "data.parquet")
_RAW_DIR = os.path.join(_BASE_DIR, "data", "raw_observations")
_RAW_GLOB = os.path.join(_RAW_DIR, "metric=*", "year=*.parquet")
_SB_PATH = os.path.join(_BASE_DIR, "data", "seasonal_baselines.parquet")
_STATIONS_PATH = os.path.join(_BASE_DIR, "stations.csv")

# Read-only connections require an existing DuckDB database file; DuckDB does not
# allow in-memory read-only. We keep one tiny throwaway db file in the OS temp dir
# (never inside the project) purely to satisfy the read-only connection mode.
_DB_PATH = os.path.join(tempfile.gettempdir(), "signals-before-disruption.duckdb")

# ---------------------------------------------------------------------------
# Module-level singletons / caches
# ---------------------------------------------------------------------------
_thread_local = threading.local()          # one read-only connection per thread
_DB_FILE_LOCK = threading.Lock()           # first-writer lock for the temp db file
_CACHE_LOCK = threading.Lock()             # guards the LRU slice cache
_STATIONS: Optional[pd.DataFrame] = None
_STATS_CACHE: Dict[str, Dict[str, Any]] = {}          # metric -> {total_rows, last_date, gap_rate}
_HIST_MAX_CACHE: Dict[tuple, float] = {}              # (entity_id, metric) -> max average
_SLICE_CACHE: "OrderedDict[tuple, pd.DataFrame]" = OrderedDict()   # (metric, date) -> date slice
_SLICE_CACHE_MAX = 8


def _load_stations() -> pd.DataFrame:
    """Load stations.csv once (kept in memory for the whole process)."""
    global _STATIONS
    if _STATIONS is None:
        _STATIONS = pd.read_csv(_STATIONS_PATH)
    return _STATIONS


def _ensure_db_file() -> None:
    """Create the empty db file used for the read-only connection, if missing."""
    if os.path.exists(_DB_PATH):
        return
    with _DB_FILE_LOCK:
        if not os.path.exists(_DB_PATH):
            c = duckdb.connect(_DB_PATH)
            c.close()


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a persistent, read-only DuckDB connection for the calling thread.

    One connection per thread, cached in thread-local storage. The DuckDB
    Python client is **not** safe for concurrent queries on a single
    connection (a racing ``execute`` can invalidate another thread's query
    result, making ``.df()`` return None). Dash serves callbacks from a thread
    pool, so a single shared connection would race and poison the caches with
    None. Read-only connections to the same tiny db file are cheap and safe;
    the parquet dataset is immutable during serving.
    """
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        _ensure_db_file()
        conn = duckdb.connect(_DB_PATH, read_only=True)
        conn.register("stations", _load_stations())
        _thread_local.conn = conn
        # One-time warm-up per thread (stats + slice caches are module-level,
        # so only the first thread actually computes anything).
        try:
            for _m in ("streamflow", "water_temperature", "gage_height"):
                _dataset_stats(conn, _m)
            _last = _STATS_CACHE.get("streamflow", {}).get("last_date")
            if _last is not None:
                _cached_slice(conn, "streamflow", str(_last))
        except Exception:
            pass  # warm-up is best-effort; queries still work on demand
    return conn


def _cached_slice(conn: duckdb.DuckDBPyConnection, metric: str, date: str) -> pd.DataFrame:
    """LRU-cached per-date slice of every gauge (joined to stations)."""
    key = (metric, str(date))
    with _CACHE_LOCK:
        if key in _SLICE_CACHE:
            _SLICE_CACHE.move_to_end(key)
            return _SLICE_CACHE[key]

    year = int(str(date)[:4])
    df = conn.execute(
        f"""
        SELECT e.entity_id, s.station_name, s.region, s.state,
               s.latitude, s.longitude,
               e.average, e.anomaly_score, e.flow_percentile, e.rise_rate_3d,
               e.daily_change, e.completeness_score, e.record_proximity
        FROM read_parquet('{_EM_GLOB}') e
        JOIN stations s ON e.entity_id = s.entity_id
        WHERE e.metric = ? AND e.year = ? AND e.observed_at = ?
        """,
        [metric, year, str(date)],
    ).df()

    with _CACHE_LOCK:
        _SLICE_CACHE[key] = df
        _SLICE_CACHE.move_to_end(key)
        while len(_SLICE_CACHE) > _SLICE_CACHE_MAX:
            _SLICE_CACHE.popitem(last=False)
    return df


def _dataset_stats(conn: duckdb.DuckDBPyConnection, metric: str) -> Dict[str, Any]:
    """Static dataset stats for a metric, computed once then cached."""
    if metric not in _STATS_CACHE:
        row = conn.execute(
            f"""
            SELECT count(*)                                  AS total_rows,
                   max(observed_at)                         AS last_date,
                   avg(CASE WHEN completeness_score > 0
                            THEN 0.0 ELSE 1.0 END)          AS gap_rate
            FROM read_parquet('{_EM_GLOB}')
            WHERE metric = ?
            """,
            [metric],
        ).fetchone()
        _STATS_CACHE[metric] = {
            "total_rows": int(row[0]),
            "last_date": pd.Timestamp(row[1]).date(),
            "gap_rate": float(row[2]) if row[2] is not None else 0.0,
        }
    return _STATS_CACHE[metric]


def _hist_max(conn: duckdb.DuckDBPyConnection, entity_id: str, metric: str) -> float:
    """All-time max average for a gauge-metric, computed once then cached."""
    key = (entity_id, metric)
    if key not in _HIST_MAX_CACHE:
        row = conn.execute(
            f"""
            SELECT max(average) FROM read_parquet('{_EM_GLOB}')
            WHERE entity_id = ? AND metric = ? AND average IS NOT NULL
            """,
            [entity_id, metric],
        ).fetchone()
        _HIST_MAX_CACHE[key] = float(row[0]) if row[0] is not None else float("nan")
    return _HIST_MAX_CACHE[key]


def invalidate_caches() -> None:
    """Drop every serving cache after the underlying parquet is re-synced
    (cloud freshness poll). Subsequent queries recompute from the new files.

    The slice cache is cleared under its lock; the stats/hist-max dicts are
    cleared here too — worst case is a benign recompute, never corruption.
    """
    with _CACHE_LOCK:
        _SLICE_CACHE.clear()
    _STATS_CACHE.clear()
    _HIST_MAX_CACHE.clear()


def _risers_to_dicts(val: Any) -> List[Dict[str, Any]]:
    """Normalize DuckDB struct[] / ROW objects to plain dicts.

    DuckDB returns the struct array as a numpy object array of ROWs (sometimes
    with a 1-D matrix wrapper); this handles all shapes safely.
    """
    if val is None or (isinstance(val, float) and val != val) or (hasattr(val, '__class__') and val.__class__.__name__ == 'NAType'):
        return []
    if hasattr(val, "tolist"):
        val = val.tolist()
    if isinstance(val, (list, tuple)):
        items = val
    else:
        # unexpected scalar / single struct
        items = [val]
    out: List[Dict[str, Any]] = []
    for item in items:
        if item is None:
            continue
        out.append(dict(item) if not isinstance(item, dict) else item)
    return out


# ---------------------------------------------------------------------------
# 1. KPI cards
# ---------------------------------------------------------------------------
def get_kpi_cards(
    conn: duckdb.DuckDBPyConnection,
    metric: str = "streamflow",
    date: str = "2026-08-01",
) -> Dict[str, Any]:
    """Top-of-page KPI cards for one metric on one date.

    Returns dict with:
      extreme_events_today : # gauges with |anomaly_score| >= 2.5
      fastest_riser        : {entity_id, station_name, rise_rate_3d, value}
      most_below_normal    : region with lowest avg anomaly (daily mean)
      data_health          : {gauges_reporting, total_gauges, gap_rate,
                            total_rows, last_date}
    """
    df = _cached_slice(conn, metric, date)
    if df.empty:
        return {
            "extreme_events_today": 0,
            "fastest_riser": None,
            "most_below_normal": None,
            "data_health": None,
        }

    extreme = int((df["anomaly_score"].abs() >= 2.5).sum())

    top = df.sort_values("rise_rate_3d", ascending=False, na_position="last").iloc[0]
    fastest_riser = {
        "entity_id": top["entity_id"],
        "station_name": top["station_name"],
        "rise_rate_3d": None if pd.isna(top["rise_rate_3d"]) else float(top["rise_rate_3d"]),
        "value": None if pd.isna(top["average"]) else float(top["average"]),
    }

    grp = (
        df.dropna(subset=["anomaly_score"])
        .groupby("region")["anomaly_score"]
        .mean()
    )
    most_below = str(grp.idxmin()) if not grp.empty else None

    stats = _dataset_stats(conn, metric)
    # gauges_reporting = % of gauges actively reporting (completeness > 0) = colored
    #          dots on the map. total_gauges = every gauge with a row this day =
    #          ALL dots on the map (including grey completeness==0 gauges). The KPI
    #          card renders "X/Y gauges reporting" where X = gauges_reporting and
    #          Y = total_gauges, so the count always matches the map render.
    health = {
        "gauges_reporting": int((df["completeness_score"] > 0).sum()),
        "total_gauges": int(len(df)),
        "gap_rate": stats["gap_rate"],       # overall share of explicit gap rows
        "total_rows": stats["total_rows"],   # rows in daily_entity_metrics for metric
        "last_date": stats["last_date"],
    }

    return {
        "extreme_events_today": extreme,
        "fastest_riser": fastest_riser,
        "most_below_normal": most_below,
        "data_health": health,
    }


# ---------------------------------------------------------------------------
# 1b. Global anomaly scorecards + monthly summary (not filter-scoped)
# ---------------------------------------------------------------------------
def get_top_anomaly_dates(
    conn: duckdb.DuckDBPyConnection,
    n: int = 2,
) -> List[Dict[str, Any]]:
    """Top N dates with the most anomalous events across ALL metrics and regions.

    Returns list of dicts: [{date, total_events, total_extreme, metrics_breakdown}]
    where metrics_breakdown is {metric: event_count} for that date.
    """
    # Sum event_count across all regions and metrics per date
    df = conn.execute(f"""
        SELECT date,
               sum(event_count)      AS total_events,
               sum(extreme_entity_count) AS total_extreme
        FROM read_parquet('{_CM_GLOB}')
        GROUP BY date
        ORDER BY total_events DESC
        LIMIT ?
    """, [n]).df()

    if df.empty:
        return []

    results = []
    for _, row in df.iterrows():
        date_str = str(pd.Timestamp(row["date"]).date())
        # Get per-metric breakdown for this date
        metric_df = conn.execute(f"""
            SELECT metric, sum(event_count) AS events
            FROM read_parquet('{_CM_GLOB}')
            WHERE date = ?
            GROUP BY metric
            ORDER BY events DESC
        """, [date_str]).df()
        metrics_breakdown = dict(zip(metric_df["metric"], metric_df["events"]))
        results.append({
            "date": date_str,
            "total_events": int(row["total_events"]),
            "total_extreme": int(row["total_extreme"]),
            "metrics_breakdown": metrics_breakdown,
        })
    return results


def get_anomaly_date_breakdown(
    conn: duckdb.DuckDBPyConnection,
    date: str,
) -> List[Dict[str, Any]]:
    """Per-region breakdown of anomalous gauges for a specific date.

    Returns list of dicts: [{region, metric, entity_id, station_name, anomaly_score, average}]
    for all gauges with |anomaly_score| >= 2.5 on that date.
    """
    df = conn.execute(f"""
        SELECT e.entity_id, s.station_name, s.region, e.metric,
               e.anomaly_score, e.average
        FROM read_parquet('{_EM_GLOB}') e
        JOIN stations s ON e.entity_id = s.entity_id
        WHERE e.observed_at = ?
          AND abs(e.anomaly_score) >= 2.5
        ORDER BY abs(e.anomaly_score) DESC
    """, [str(date)]).df()

    if df.empty:
        return []
    return df.to_dict("records")


def get_monthly_anomaly_counts(
    conn: duckdb.DuckDBPyConnection,
) -> pd.DataFrame:
    """Monthly total anomalous events across ALL metrics and regions.

    Returns DataFrame with columns: year_month (datetime), year (int), month (int),
    total_events (int), total_extreme (int).
    Sorted chronologically.
    """
    df = conn.execute(f"""
        SELECT
            date_trunc('month', date)::DATE AS year_month,
            year(date)   AS year,
            month(date)  AS month,
            sum(event_count)          AS total_events,
            sum(extreme_entity_count) AS total_extreme
        FROM read_parquet('{_CM_GLOB}')
        GROUP BY date_trunc('month', date), year(date), month(date)
        ORDER BY year_month
    """).df()
    return df


# ---------------------------------------------------------------------------
# 2. Map data
# ---------------------------------------------------------------------------
def get_map_data(
    conn: duckdb.DuckDBPyConnection,
    metric: str = "streamflow",
    date: str = "2026-08-01",
) -> pd.DataFrame:
    """One row per gauge for the choropleth + scatter_geo markers.

    Columns: entity_id, station_name, latitude, longitude, value (average),
             anomaly_score, flow_percentile, region, state.
    """
    df = _cached_slice(conn, metric, date).copy()
    cols = ["entity_id", "station_name", "latitude", "longitude",
            "value", "anomaly_score", "flow_percentile", "region", "state"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = df.rename(columns={"average": "value"})
    return df[cols]


# ---------------------------------------------------------------------------
# 3. Region table
# ---------------------------------------------------------------------------
def get_region_table(
    conn: duckdb.DuckDBPyConnection,
    metric: str = "streamflow",
    date: str = "2026-08-01",
    region: Optional[str] = None,
) -> pd.DataFrame:
    """Regional rollup table for a metric-date.

    Columns: region, entity_count, event_count, average_anomaly, fastest_risers
    (the latter parsed to a list of dicts). Sorted by average_anomaly DESC.
    Pass `region` to filter to a single region (e.g. for the drill-down panel).
    """
    sql = f"""
        SELECT region, entity_count, event_count, average_anomaly, fastest_risers
        FROM read_parquet('{_CM_GLOB}')
        WHERE metric = ? AND year = ? AND date = ?
    """
    year = int(str(date)[:4])
    params: List[Any] = [metric, year, str(date)]
    if region is not None:
        sql += " AND region = ?"
        params.append(region)
    sql += " ORDER BY average_anomaly DESC NULLS LAST"

    df = conn.execute(sql, params).df()
    if not df.empty:
        df["fastest_risers"] = df["fastest_risers"].map(_risers_to_dicts)
    return df


# ---------------------------------------------------------------------------
# 4. Fastest risers (top-5 struct extraction)
# ---------------------------------------------------------------------------
def get_fastest_risers(
    conn: duckdb.DuckDBPyConnection,
    region: str,
    metric: str = "streamflow",
    date: str = "2026-08-01",
) -> List[Dict[str, Any]]:
    """Top-5 risers for a region-metric-date, from the list[struct] column.

    Returns [{entity_id, station_name, rise_rate_3d, value}], sorted by
    rise_rate_3d DESC, capped at 5.
    """
    row = conn.execute(
        f"""
        SELECT fastest_risers FROM read_parquet('{_CM_GLOB}')
        WHERE metric = ? AND year = ? AND date = ? AND region = ?
        """,
        [metric, int(str(date)[:4]), str(date), region],
    ).fetchone()
    if row is None or row[0] is None:
        return []
    risers = _risers_to_dicts(row[0])
    risers = [
        {
            "entity_id": r["entity_id"],
            "station_name": r["station_name"],
            "rise_rate_3d": r["rise_rate_3d"],
            "value": r["value"],
        }
        for r in risers
        if r.get("rise_rate_3d") is not None
    ]
    risers.sort(key=lambda r: r["rise_rate_3d"], reverse=True)
    return risers[:5]


# ---------------------------------------------------------------------------
# 5. Hydrograph data (with water-temperature overlay + calendar reindex)
# ---------------------------------------------------------------------------
def get_hydrograph_data(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str = "streamflow",
    start_date: str = "2026-07-01",
    end_date: str = "2026-08-01",
) -> pd.DataFrame:
    """Daily series for one gauge over a date range, reindexed to the full calendar.

    Columns: observed_at, average (flow), water_temp, anomaly_score, daily_change,
             rise_rate_3d, flow_percentile, completeness_score.
    Missing days appear as rows with null metrics and completeness_score = 0.
    water_temp joins daily 'water_temperature' averages for the same gauge and may
    be sparse/null — handled gracefully, never errors.
    """
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)

    raw = conn.execute(
        f"""
        SELECT observed_at, metric, average, anomaly_score, daily_change,
               rise_rate_3d, flow_percentile, completeness_score
        FROM read_parquet('{_EM_GLOB}')
        WHERE entity_id = ?
          AND metric IN (?, 'water_temperature')
          AND year BETWEEN ? AND ?
          AND observed_at BETWEEN ? AND ?
        """,
        [entity_id, metric, start.year, end.year, str(start.date()), str(end.date())],
    ).df()

    if raw.empty:
        flow = pd.DataFrame({"observed_at": pd.Series(dtype="datetime64[ns]"),
                             "average": pd.Series(dtype="float64")})
    else:
        flow = raw[raw["metric"] == metric].drop(columns=["metric"])

    if raw.empty:
        temp = pd.DataFrame({"observed_at": pd.Series(dtype="datetime64[ns]"),
                             "water_temp": pd.Series(dtype="float64")})
    else:
        temp = (
            raw[raw["metric"] == "water_temperature"]
            .drop(columns=["metric"])
            .rename(columns={"average": "water_temp"})[["observed_at", "water_temp"]]
        )

    merged = flow.merge(temp, on="observed_at", how="left")
    merged["observed_at"] = pd.to_datetime(merged["observed_at"])

    idx = pd.date_range(start, end, freq="D")
    out = (
        merged.set_index("observed_at")
        .reindex(idx)
        .reset_index()
        .rename(columns={"index": "observed_at"})
    )
    # Empty-input safety (production 500, signals:v6): when no rows matched
    # (e.g. a gauge with no data in the requested window) the reindexed
    # calendar only carries the merge columns. Restore the documented output
    # schema instead of raising KeyError: missing days get null metrics and
    # completeness_score = 0, as the docstring promises.
    for _col in ("anomaly_score", "daily_change", "rise_rate_3d",
                 "flow_percentile", "completeness_score"):
        if _col not in out.columns:
            out[_col] = 0.0 if _col == "completeness_score" else float("nan")
    out["completeness_score"] = out["completeness_score"].fillna(0.0)

    cols = ["observed_at", "average", "water_temp", "anomaly_score",
            "daily_change", "rise_rate_3d", "flow_percentile", "completeness_score"]
    return out[cols]


# ---------------------------------------------------------------------------
# 6. Baseline band (±1σ / ±2σ confidence band support)
# ---------------------------------------------------------------------------
def get_baseline_band(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str = "streamflow",
    start_date: str = "2026-07-01",
    end_date: str = "2026-08-01",
) -> pd.DataFrame:
    """Seasonal baseline mu/sigma by day-of-year for one gauge-metric.

    Returns all ~366 DOY rows (filtered to the requested range when it does not
    cross the year boundary); the Dash hydrograph joins on
    observed_at.dayofyear == day_of_year. Columns: day_of_year, mu, sigma.
    """
    df = conn.execute(
        f"""
        SELECT day_of_year, mu, sigma
        FROM read_parquet('{_SB_PATH}')
        WHERE entity_id = ? AND metric = ?
        """,
        [entity_id, metric],
    ).df()

    if df.empty:
        return df

    start_doy, end_doy = (
        int(pd.Timestamp(start_date).dayofyear),
        int(pd.Timestamp(end_date).dayofyear),
    )
    if start_doy <= end_doy:
        df = df[(df["day_of_year"] >= start_doy) & (df["day_of_year"] <= end_doy)]
    # range straddles the year boundary (e.g. Dec→Jan): keep the wrap-around band
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# 7. Raw payload (audit / drill-down)
# ---------------------------------------------------------------------------
def get_raw_payload(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str = "streamflow",
    date: str = "2026-08-01",
) -> Optional[str]:
    """Raw USGS API JSON payload (string) for one gauge-metric-day.

    Reads the exact per-year file directly (fast path); falls back to the full
    glob if that file is missing. Returns None when no record exists.
    """
    date_str = str(date)
    year = date_str[:4]
    fast_path = os.path.join(_RAW_DIR, f"metric={metric}", f"year={year}.parquet")
    sources = [fast_path] if os.path.exists(fast_path) else [_RAW_GLOB]

    for src in sources:
        row = conn.execute(
            f"""
            SELECT raw_payload FROM read_parquet('{src}')
            WHERE entity_id = ? AND observed_at = ?
            """,
            [entity_id, date_str],
        ).fetchone()
        if row is not None:
            return row[0]
    return None


# ---------------------------------------------------------------------------
# 8. Flashiness index (Richards-Baker) + regional rank
# ---------------------------------------------------------------------------
def get_flashiness_index(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str = "streamflow",
    year: int = 2026,
) -> Dict[str, Any]:
    """Richards-Baker Flashiness Index over a calendar year.

    index = sum(|daily_change|) / sum(average), computed only over days with
    completeness_score > 0 (actual observations, not gap rows).

    Returns {flashiness_index, region_rank (1 = flashiest in region), region,
             n_gauges, n_days} — or None-valued fields when the gauge has no
    qualifying observations that year.
    """
    sql = f"""
        WITH reg AS (SELECT region FROM stations WHERE entity_id = ?),
        f AS (
            SELECT s.entity_id,
                   sum(abs(e.daily_change)) / sum(e.average) AS fi,
                   count(*)                                 AS n_days
            FROM read_parquet('{_EM_GLOB}') e
            JOIN stations s USING (entity_id)
            WHERE e.metric = ?
              AND e.year = ?
              AND e.completeness_score > 0
              AND e.daily_change IS NOT NULL
              AND e.average IS NOT NULL
              AND e.average > 0
              AND s.region = (SELECT region FROM reg)
            GROUP BY s.entity_id
        )
        SELECT (SELECT fi FROM f WHERE entity_id = ?)                        AS fi,
               (SELECT n_days FROM f WHERE entity_id = ?)                    AS n_days,
               (SELECT count(*) FROM f WHERE fi > (SELECT fi FROM f WHERE entity_id = ?)) + 1 AS region_rank,
               (SELECT count(*) FROM f)                                      AS n_gauges,
               (SELECT region FROM reg)                                      AS region
    """
    res = conn.execute(sql, [entity_id, metric, int(year), entity_id, entity_id, entity_id]).fetchone()

    fi = float(res[0]) if res[0] is not None else None
    rank = int(res[2]) if res[2] is not None and pd.notna(res[2]) else None
    return {
        "flashiness_index": fi,
        "region_rank": rank,
        "region": res[4],
        "n_gauges": int(res[3]) if res[3] is not None else 0,
        "n_days": int(res[1]) if res[1] is not None else 0,
        "year": int(year),
    }


# ---------------------------------------------------------------------------
# 9. Personality cards
# ---------------------------------------------------------------------------
def get_personality_cards(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str = "streamflow",
    date: str = "2026-08-01",
) -> Dict[str, Any]:
    """Gauge 'personality' summary at a point in time.

    Returns {flashiness_index, flashiness_rank (1 = flashiest in region),
             flow_percentile, record_proximity, historical_max, average,
             region, n_gauges, n_days}.
    """
    f = _flashiness_rank(conn, entity_id, metric, int(str(date)[:4]))

    slice_ = _cached_slice(conn, metric, date)
    row = slice_[slice_["entity_id"] == entity_id]
    if row.empty:
        percentile = proximity = average = None
    else:
        row = row.iloc[0]
        percentile = None if pd.isna(row["flow_percentile"]) else float(row["flow_percentile"])
        proximity = None if pd.isna(row["record_proximity"]) else float(row["record_proximity"])
        average = None if pd.isna(row["average"]) else float(row["average"])

    hist_max = _hist_max(conn, entity_id, metric)

    return {
        "flashiness_index": f["flashiness_index"],
        "flashiness_rank": f["region_rank"],
        "flow_percentile": percentile,
        "record_proximity": proximity,
        "historical_max": hist_max,
        "average": average,
        "region": f["region"],
        "n_gauges": f["n_gauges"],
        "n_days": f["n_days"],
        "date": str(date),
    }


def _flashiness_rank(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str,
    year: int,
) -> Dict[str, Any]:
    """Shared implementation: flashiness + regional rank for one gauge-year."""
    return get_flashiness_index(conn, entity_id, metric, year)


# ---------------------------------------------------------------------------
# 10. Previous-year overlay (ghost line on the hydrograph)
# ---------------------------------------------------------------------------
def get_previous_year_flow(
    conn: duckdb.DuckDBPyConnection,
    entity_id: str,
    metric: str = "streamflow",
    year: int = 2026,
) -> pd.DataFrame:
    """Previous calendar year's daily averages, with observed_at shifted +1 year
    so it overlays directly on the current-year hydrograph.

    Columns: observed_at (shifted), average.
    """
    prev = int(year) - 1
    df = conn.execute(
        f"""
        SELECT (observed_at + INTERVAL 1 YEAR)::DATE AS observed_at, average
        FROM read_parquet('{_EM_GLOB}')
        WHERE entity_id = ? AND metric = ? AND year = ?
          AND average IS NOT NULL
        ORDER BY observed_at
        """,
        [entity_id, metric, prev],
    ).df()
    return df.reset_index(drop=True)
