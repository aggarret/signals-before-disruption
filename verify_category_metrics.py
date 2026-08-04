#!/usr/bin/env python3
"""Build 4 verification: DuckDB queries against daily_category_metrics.

Checks row counts (total / per metric / per region), the Pacific Northwest
streamflow spot check, region ranking on 2026-08-01, query latency on a
region+metric+date filter, fastest_risers list-of-struct structure (with an
independent cross-check against daily_entity_metrics), and null
average_anomaly rows.

Usage: python3 verify_category_metrics.py
"""
from __future__ import annotations

import glob
import time

import duckdb

CAT_FILES = sorted(glob.glob("data/daily_category_metrics/metric=*/year=*/*.parquet"))
DAILY_FILES = sorted(glob.glob("data/daily_entity_metrics/metric=*/year=*/*.parquet"))
STATIONS_CSV = "stations.csv"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW cat AS SELECT * FROM read_parquet({CAT_FILES!r}, hive_partitioning=true)"
    )
    con.execute(
        f"CREATE VIEW daily AS SELECT * FROM read_parquet({DAILY_FILES!r}, hive_partitioning=true)"
    )
    con.execute(
        f"CREATE VIEW stations AS SELECT entity_id, region, station_name FROM read_csv('{STATIONS_CSV}')"
    )
    return con


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    con = connect()

    # ---------------------------------------------------------------- 1. counts
    section("1. Row counts")
    total = con.execute("SELECT count(*) FROM cat").fetchone()[0]
    print(f"daily_category_metrics total: {total:,} rows")
    per_metric = con.execute(
        "SELECT metric, count(*) AS n FROM cat GROUP BY metric ORDER BY n DESC"
    ).fetchall()
    for m, n in per_metric:
        print(f"  {m:18s} {n:>10,}")
    n_dates = con.execute("SELECT count(DISTINCT date) FROM cat").fetchone()[0]
    naive = 8 * 3 * 8270
    print(f"distinct dates: {n_dates} (2004-01-01 .. 2026-08-02)")
    print(f"naive max (8 regions x 3 metrics x ~8270 days): {naive:,}")
    print(f"actual / naive = {total / naive:.1%}  "
          f"(regions with fewer gauges and sparse gage_height/temp have fewer date combos)")

    # -------------------------------------------------------- 2. per region
    section("2. Per-region row counts (should be roughly equal, x3 metrics)")
    per_region = con.execute(
        "SELECT region, count(*) AS n FROM cat GROUP BY region ORDER BY n DESC"
    ).fetchall()
    for r, n in per_region:
        print(f"  {r:26s} {n:>8,}")

    # --------------------------------------------- 3. PNW streamflow spot check
    section("3. Spot check: Pacific Northwest / streamflow / 2026-08-01")
    row = con.execute(
        """
        SELECT date, region, metric, entity_count, event_count, average_anomaly,
               extreme_entity_count, fastest_risers
        FROM cat
        WHERE region = 'Pacific Northwest' AND metric = 'streamflow'
          AND date = DATE '2026-08-01'
        """
    ).fetchone()
    print(f"  date={row[0]} region={row[1]} metric={row[2]}")
    print(f"  entity_count={row[3]}  event_count={row[4]}  "
          f"average_anomaly={row[5]:.3f}  extreme_entity_count={row[6]}")
    print(f"  fastest_risers (n={len(row[7])}):")
    for s in row[7]:
        print(f"    {s['entity_id']}  {s['station_name'][:45]:47s} "
              f"rise={s['rise_rate_3d']:9.1f}  value={s['value']:12,.1f}")

    # independent cross-check: expected entity_count straight from the entity layer
    exp_count = con.execute(
        """
        SELECT count(DISTINCT d.entity_id)
        FROM daily d JOIN stations s ON d.entity_id = s.entity_id
        WHERE s.region = 'Pacific Northwest' AND d.metric = 'streamflow'
          AND d.observed_at = DATE '2026-08-01' AND d.completeness_score > 0
        """
    ).fetchone()[0]
    print(f"  cross-check: PNW gauges reporting 2026-08-01 per entity layer = {exp_count} "
          f"({'OK' if exp_count == row[3] else 'MISMATCH'})")

    # independent cross-check: top riser from the entity layer
    top = con.execute(
        """
        SELECT d.entity_id, s.station_name, d.rise_rate_3d, d.average
        FROM daily d JOIN stations s ON d.entity_id = s.entity_id
        WHERE s.region = 'Pacific Northwest' AND d.metric = 'streamflow'
          AND d.observed_at = DATE '2026-08-01' AND d.completeness_score > 0
          AND d.rise_rate_3d IS NOT NULL
        ORDER BY d.rise_rate_3d DESC LIMIT 1
        """
    ).fetchone()
    if row[7]:
        f = row[7][0]
        ok = (f['entity_id'] == top[0] and f['station_name'] == top[1]
              and abs(f['rise_rate_3d'] - top[2]) < 1e-6 and abs(f['value'] - top[3]) < 1e-6)
        print(f"  cross-check: entity-layer top riser = {top[0]} {top[1][:35]:37s} "
              f"rise={top[2]:9.1f} value={top[3]:12,.1f}  ({'MATCHES' if ok else 'MISMATCH'})")

    # -------------------------------------------------- 4. top region 08-01
    section("4. Highest average_anomaly region on 2026-08-01 (streamflow)")
    top_region = con.execute(
        """
        SELECT region, entity_count, event_count, round(average_anomaly, 3) AS avg_anom
        FROM cat
        WHERE date = DATE '2026-08-01' AND metric = 'streamflow'
        ORDER BY average_anomaly DESC
        """
    ).fetchall()
    for r in top_region:
        print(f"  {r[0]:26s} entities={r[1]} events={r[2]}  avg_anomaly={r[3]}")
    print(f"  -> highest: {top_region[0][0]} (avg_anomaly={top_region[0][3]})")

    # -------------------------------------------------------- 5. latency
    section("5. Query latency: Colorado River basin / streamflow / >= 2026-01-01")
    for i in range(3):
        t0 = time.perf_counter()
        res = con.execute(
            """
            SELECT count(*), count(DISTINCT date)
            FROM cat
            WHERE region = 'Colorado River basin' AND metric = 'streamflow'
              AND date >= DATE '2026-01-01'
            """
        ).fetchone()
        dt = (time.perf_counter() - t0) * 1000
        label = "cold" if i == 0 else "warm"
        print(f"  run {i + 1} ({label:4s}): {res[0]:,} rows over {res[1]} distinct dates in {dt:.1f} ms")
    t0 = time.perf_counter()
    res = con.execute(
        """
        SELECT date, entity_count, event_count, average_anomaly, fastest_risers
        FROM cat
        WHERE region = 'Colorado River basin' AND metric = 'streamflow'
          AND date >= DATE '2026-01-01' ORDER BY date DESC LIMIT 5
        """
    ).fetchall()
    dt = (time.perf_counter() - t0) * 1000
    print(f"  full-row materialization (limit 5, ordered): {dt:.1f} ms")
    for r in res:
        n = len(r[4]) if r[4] else 0
        a = f"{r[3]:+.2f}" if r[3] is not None else "null"
        print(f"    {r[0]}  entities={r[1]}  events={r[2]}  avg_anom={a}  risers={n}")

    # ------------------------------------------- 6. fastest_risers structure
    section("6. fastest_risers list-of-struct verification")
    row = con.execute(
        """
        SELECT date, region, fastest_risers
        FROM cat
        WHERE fastest_risers IS NOT NULL AND len(fastest_risers) = 5
        ORDER BY date DESC, region LIMIT 1
        """
    ).fetchone()
    print(f"  sample row: date={row[0]} region={row[1]}")
    print(f"  type: list of structs, fields = "
          f"{[k for k in row[2][0].keys()]}, element type sample: {row[2][0]}")
    for i, s in enumerate(row[2], 1):
        print(f"    [{i}] {s['entity_id']}  {s['station_name'][:45]:47s} "
              f"rise={s['rise_rate_3d']:9.1f}  value={s['value']:12,.1f}")
    # global: are all risers sorted desc and capped at 5?
    # simpler: count lists longer than 5 + lists with null riser elements
    bad_len = con.execute(
        "SELECT count(*) FROM cat WHERE fastest_risers IS NOT NULL AND len(fastest_risers) > 5"
    ).fetchone()[0]
    null_el = con.execute(
        """
        SELECT count(*) FROM (
            SELECT unnest(fastest_risers) AS s FROM cat WHERE fastest_risers IS NOT NULL
        ) t WHERE s.rise_rate_3d IS NULL
        """
    ).fetchone()[0]
    unsorted = con.execute(
        """
        WITH base AS (
            SELECT date, region, metric, fastest_risers,
                   list_transform(fastest_risers, x -> x.rise_rate_3d) AS r
            FROM cat WHERE fastest_risers IS NOT NULL
        )
        SELECT count(*) FROM base
        WHERE r != list_sort(r, 'DESC')
        """
    ).fetchone()[0]
    print(f"  global checks: lists longer than 5 = {bad_len}; "
          f"struct elements with null rise_rate_3d = {null_el}; "
          f"lists not sorted desc = {unsorted}  "
          f"({'ALL CLEAN' if bad_len == null_el == unsorted == 0 else 'ISSUES FOUND'})")
    n_null_risers = con.execute(
        "SELECT count(*) FROM cat WHERE fastest_risers IS NULL"
    ).fetchone()[0]
    print(f"  rows with fastest_risers NULL (no valid rise_rate that day): {n_null_risers:,} "
          f"({n_null_risers / 164241:.1%} of table)")

    # -------------------------------------------------- 7. null avg anomaly
    section("7. Null average_anomaly rows")
    n_null = con.execute(
        "SELECT count(*) FROM cat WHERE average_anomaly IS NULL"
    ).fetchone()[0]
    print(f"  rows with null average_anomaly: {n_null:,} ({n_null / 164241:.1%} of table)")
    by_metric = con.execute(
        """
        SELECT metric, count(*) AS n
        FROM cat WHERE average_anomaly IS NULL
        GROUP BY metric ORDER BY n DESC
        """
    ).fetchall()
    for m, n in by_metric:
        print(f"    {m:18s} {n:>8,}")
    by_region = con.execute(
        """
        SELECT region, count(*) AS n
        FROM cat WHERE average_anomaly IS NULL
        GROUP BY region ORDER BY n DESC LIMIT 5
        """
    ).fetchall()
    print("  top regions with null average_anomaly:")
    for r, n in by_region:
        print(f"    {r:26s} {n:>8,}")
    sample = con.execute(
        """
        SELECT date, region, metric, entity_count
        FROM cat WHERE average_anomaly IS NULL
        ORDER BY date DESC LIMIT 6
        """
    ).fetchall()
    print("  most recent null rows:")
    for d, r, m, ec in sample:
        print(f"    {d}  {r:26s} {m:18s} entity_count={ec}")
    # how many nulls have entity_count = 0 (pure gap days) vs reporting gauges
    null_ec0 = con.execute(
        "SELECT count(*) FROM cat WHERE average_anomaly IS NULL AND entity_count = 0"
    ).fetchone()[0]
    print(f"  of those: {null_ec0:,} have entity_count = 0 (no reporting gauges), "
          f"{n_null - null_ec0:,} have gauges reporting but null anomaly (e.g. outside baseline)")

    # ------------------------------------------ 8. global consistency checks
    section("8. Global consistency vs daily_entity_metrics")
    sum_events = con.execute("SELECT sum(event_count) FROM cat").fetchone()[0]
    exp_events = con.execute(
        "SELECT count(*) FROM daily WHERE abs(anomaly_score) >= 2.5"
    ).fetchone()[0]
    print(f"  sum(event_count) over all regions = {sum_events:,} vs "
          f"|z|>=2.5 rows in entity layer = {exp_events:,} "
          f"({'OK' if sum_events == exp_events else 'MISMATCH'})")
    sum_entities = con.execute("SELECT sum(entity_count) FROM cat").fetchone()[0]
    exp_entities = con.execute(
        "SELECT count(*) FROM daily WHERE completeness_score > 0"
    ).fetchone()[0]
    print(f"  sum(entity_count) = {sum_entities:,} vs reporting rows in entity layer = "
          f"{exp_entities:,} ({'OK' if sum_entities == exp_entities else 'MISMATCH'})")
    riser_total = con.execute(
        "SELECT sum(len(fastest_risers)) FROM cat WHERE fastest_risers IS NOT NULL"
    ).fetchone()[0]
    exp_risers = con.execute(
        """
        SELECT count(*) FROM daily
        WHERE completeness_score > 0 AND rise_rate_3d IS NOT NULL
        """
    ).fetchone()[0]
    # expected: min over region-metric-days of (rows, but capped at 5 per group)
    print(f"  sum(len(fastest_risers)) = {riser_total:,} (capped 5/group; "
          f"entity-layer rows w/ rise_rate = {exp_risers:,})")

    con.close()


if __name__ == "__main__":
    main()
