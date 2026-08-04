#!/usr/bin/env python3
"""Build 3 verification: DuckDB queries against the new parquet layers.

Checks row counts, spot values, anomaly distribution, completeness gaps,
flow-percentile sanity, query latency, and baseline sanity.

Usage: python3 verify_metrics.py
"""
from __future__ import annotations

import glob
import time

import duckdb

RAW_FILES = sorted(glob.glob("data/raw_observations/metric=*/year=*.parquet"))
DAILY_FILES = sorted(glob.glob("data/daily_entity_metrics/metric=*/year=*/*.parquet"))
BASELINE_FILE = "data/seasonal_baselines.parquet"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW daily AS SELECT * FROM read_parquet({DAILY_FILES!r}, hive_partitioning=true)"
    )
    con.execute(
        f"CREATE VIEW raw AS SELECT * FROM read_parquet({RAW_FILES!r}, hive_partitioning=true)"
    )
    con.execute(f"CREATE VIEW baselines AS SELECT * FROM read_parquet('{BASELINE_FILE}')")
    return con


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    con = connect()

    # ------------------------------------------------------------------ 1. counts
    section("1. Row counts")
    total = con.execute("SELECT count(*) FROM daily").fetchone()[0]
    per_metric = con.execute(
        "SELECT metric, count(*) AS n FROM daily GROUP BY metric ORDER BY n DESC"
    ).fetchall()
    raw_total = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    print(f"daily_entity_metrics total: {total:,} rows  (raw_observations: {raw_total:,})")
    for m, n in per_metric:
        print(f"  {m:18s} {n:>10,}")
    expected = 52 * 3 * 8270  # naive max: 52 gauges x 3 metrics x full calendar
    print(f"naive expectation (52 gauges x 3 metrics x 8270 days): {expected:,}")
    print(f"actual / naive = {total / expected:.1%}  "
          f"(62 empty metric-gauge combos + sparse series + calendar start dates)")
    base_total = con.execute("SELECT count(*) FROM baselines").fetchone()[0]
    combos = con.execute("SELECT count(DISTINCT entity_id || '|' || metric) FROM baselines").fetchone()[0]
    print(f"seasonal_baselines: {base_total:,} rows, {combos} gauge-metric combos")

    # ------------------------------------------------------------------ 2. spot check
    section("2. Spot check: USGS-01184000 streamflow 2026-07-04")
    rows = con.execute(
        """
        SELECT entity_id, metric, observed_at, observation_count, minimum, maximum,
               average, daily_change, rise_rate_3d, flow_percentile, anomaly_score,
               completeness_score, record_proximity
        FROM daily
        WHERE entity_id = 'USGS-01184000' AND metric = 'streamflow'
          AND observed_at = DATE '2026-07-04'
        """
    ).fetchall()
    cols = [d[0] for d in con.description]
    for r in rows:
        for c, v in zip(cols, r):
            print(f"  {c:20s} {v}")
    if rows:
        avg, z = rows[0][6], rows[0][10]
        # cross-check against the fixed baseline for the exact DOY (2026-07-04 -> DOY 185)
        doy = con.execute(
            "SELECT day_of_year FROM (SELECT date_diff('day', DATE '2026-01-01', DATE '2026-07-04') + 1 AS day_of_year)"
        ).fetchone()[0]
        mu, sigma, ny = con.execute(
            "SELECT mu, sigma, n_years FROM baselines WHERE entity_id='USGS-01184000' "
            "AND metric='streamflow' AND day_of_year=?",
            [doy],
        ).fetchone()
        print(f"  -> DOY {doy}: mu={mu:,.1f} sigma={sigma:,.1f} n_years={ny}; "
              f"z=(avg-mu)/sigma=({avg:,.0f}-{mu:,.0f})/{sigma:,.0f}={z:.3f}")
        print(f"  -> anomaly_score {z:.3f} : {'ABOVE normal (positive z)' if z and z > 0 else 'BELOW normal (negative z)'}")

    # ------------------------------------------------------------------ 3. anomaly dist
    section("3. Anomaly score distribution (non-null)")
    dist = con.execute(
        """
        SELECT count(*) AS n, min(anomaly_score) AS mn,
               quantile_cont(anomaly_score, 0.25) AS q1,
               quantile_cont(anomaly_score, 0.5) AS med,
               quantile_cont(anomaly_score, 0.75) AS q3,
               max(anomaly_score) AS mx,
               avg(anomaly_score) AS mean
        FROM daily WHERE anomaly_score IS NOT NULL
        """
    ).fetchone()
    print(f"  n={dist[0]:,}  min={dist[1]:.2f}  q1={dist[2]:.2f}  median={dist[3]:.2f}  "
          f"q3={dist[4]:.2f}  max={dist[5]:.2f}  mean={dist[6]:.3f}")
    extreme = con.execute(
        "SELECT count(*) FILTER (WHERE anomaly_score >= 2.5) AS hi, "
        "       count(*) FILTER (WHERE anomaly_score <= -2.5) AS lo FROM daily"
    ).fetchone()
    print(f"  |z|>=2.5: {extreme[0]:,} high / {extreme[1]:,} low")

    # ------------------------------------------------------------------ 4. completeness
    section("4. Completeness gaps")
    gaps = con.execute("SELECT count(*) FROM daily WHERE completeness_score = 0").fetchone()[0]
    obs = con.execute("SELECT count(*) FROM daily WHERE completeness_score = 1").fetchone()[0]
    print(f"  rows with completeness_score = 0 (explicit gaps): {gaps:,} ({gaps/(gaps+obs):.1%})")
    print("  gauges with the most gap days:")
    for eid, metric, n in con.execute(
        """
        SELECT entity_id, metric, count(*) AS gaps
        FROM daily WHERE completeness_score = 0
        GROUP BY entity_id, metric ORDER BY gaps DESC LIMIT 8
        """
    ).fetchall():
        print(f"    {eid}  {metric:18s} {n:,} gap days")

    # ------------------------------------------------------------------ 5. flow percentile
    section("5. Flow percentile spot check (most recent day per gauge-metric)")
    for eid, metric in [("USGS-01184000", "streamflow"), ("USGS-14105700", "streamflow")]:
        row = con.execute(
            """
            SELECT observed_at, average, flow_percentile, record_proximity
            FROM daily
            WHERE entity_id = ? AND metric = ?
              AND completeness_score = 1
            ORDER BY observed_at DESC LIMIT 1
            """,
            [eid, metric],
        ).fetchone()
        print(f"  {eid} {metric}: {row[0]}  value={row[1]:,.1f}  "
              f"percentile={row[2]:.1f}  record_proximity={row[3]:.3f}")

    # ------------------------------------------------------------------ 6. latency
    section("6. Query latency: USGS-14105700 streamflow >= 2026-01-01")
    for label, sql in [
        ("count only", "SELECT count(*) FROM daily WHERE entity_id='USGS-14105700' AND metric='streamflow' AND observed_at >= DATE '2026-01-01'"),
        ("full rows", "SELECT * FROM daily WHERE entity_id='USGS-14105700' AND metric='streamflow' AND observed_at >= DATE '2026-01-01' ORDER BY observed_at"),
    ]:
        t0 = time.perf_counter()
        res = con.execute(sql).fetchall()
        dt = (time.perf_counter() - t0) * 1000
        print(f"  {label:12s} -> {len(res):,} rows in {dt:.1f} ms")

    # ------------------------------------------------------------------ 7. baseline sanity
    section("7. seasonal_baselines spot check: USGS-01184000 streamflow DOY 200")
    b = con.execute(
        """
        SELECT day_of_year, mu, sigma, n_years
        FROM baselines
        WHERE entity_id = 'USGS-01184000' AND metric = 'streamflow' AND day_of_year = 200
        """
    ).fetchone()
    print(f"  DOY={b[0]}  mu={b[1]:,.1f} ft3/s  sigma={b[2]:,.1f}  n_years={b[3]}")
    # context: neighbors + how the +-7 day window behaves
    neigh = con.execute(
        """
        SELECT min(mu), max(mu), avg(n_years) FROM baselines
        WHERE entity_id = 'USGS-01184000' AND metric = 'streamflow' AND day_of_year BETWEEN 193 AND 207
        """
    ).fetchone()
    print(f"  DOY 193-207 window: mu range [{neigh[0]:,.1f}, {neigh[1]:,.1f}], avg n_years {neigh[2]:.1f}")
    leap = con.execute(
        """
        SELECT day_of_year, n_years FROM baselines
        WHERE entity_id = 'USGS-01184000' AND metric = 'streamflow' AND day_of_year IN (1, 366)
        """
    ).fetchall()
    print(f"  boundary DOYs: {leap}  (DOY 366 only fed by leap years + Jan wrap)")

    # ------------------------------------------------------------------ 8. partition ordering
    section("8. Partition sort-order check (entity_id, observed_at within each file)")
    viol = con.execute(
        """
        WITH base AS (
            SELECT metric, year, entity_id, observed_at, row_number() OVER () AS rn
            FROM daily
        ), pairs AS (
            SELECT a.entity_id, a.observed_at, b.entity_id AS pe, b.observed_at AS pd
            FROM base a JOIN base b
              ON a.metric = b.metric AND a.year = b.year AND a.rn = b.rn + 1
        )
        SELECT count(*) FROM pairs
        WHERE entity_id < pe OR (entity_id = pe AND observed_at < pd)
        """
    ).fetchone()[0]
    print(f"  out-of-order rows across all partitions: {viol}  (0 = each file sorted)")

    con.close()


if __name__ == "__main__":
    main()
