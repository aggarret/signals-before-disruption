#!/usr/bin/env python3
"""
Signals Before Disruption — Build 2: serving-layer verification.

Validates the low-latency serverless serving pattern: DuckDB queries the
raw_observations parquet hive-partition directly (no database server), plus a
PyArrow memory-mapped read of a single partition file.

Usage:
  python3 verify_serving.py [--partitions data/raw_observations]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

GLOB = "data/raw_observations/**/*.parquet"


def timed(label: str, fn):
    t0 = time.perf_counter()
    result = fn()
    dt = (time.perf_counter() - t0) * 1000
    print(f"  {label}: {dt:7.1f} ms")
    return result, dt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partitions", default="data/raw_observations")
    args = ap.parse_args()
    root = Path(args.partitions)
    glob = str(root / "**" / "*.parquet")

    con = duckdb.connect()
    con.execute("SET threads=4;")
    parquet_files = sorted(str(p) for p in root.rglob("*.parquet"))
    print(f"Partition files: {len(parquet_files)}")
    total_bytes = sum(Path(p).stat().st_size for p in parquet_files)
    print(f"Total parquet size: {total_bytes/1e6:.2f} MB\n")

    # --- 1. Row counts per metric -------------------------------------------
    print("[1] Row counts per metric")
    (res, dt) = timed("count per metric", lambda: con.execute(
        f"SELECT metric, COUNT(*) AS n FROM read_parquet('{glob}', "
        f"hive_partitioning=true) GROUP BY metric ORDER BY metric"
    ).fetchall())
    for metric, n in res:
        print(f"      {metric}: {n:,}")
    total_rows = sum(n for _, n in res)
    print(f"      TOTAL: {total_rows:,}")

    # --- 2. Min/max observed_at per gauge -----------------------------------
    print("\n[2] Min/max observed_at per gauge")
    (res, dt) = timed("min/max per gauge", lambda: con.execute(
        f"SELECT entity_id, MIN(observed_at) AS first, MAX(observed_at) AS last, "
        f"COUNT(*) AS n FROM read_parquet('{glob}', hive_partitioning=true) "
        f"GROUP BY entity_id ORDER BY entity_id"
    ).fetchall())
    for e, f, l, n in res:
        print(f"      {e}: {f} .. {l}  ({n:,} rows)")

    # --- 3. Analytical query: yearly average streamflow, one gauge ----------
    print("\n[3] Yearly average streamflow — USGS-01184000 (Connecticut River)")
    (res, dt) = timed("yearly avg streamflow", lambda: con.execute(
        f"SELECT year(observed_at) AS yr, ROUND(AVG(value), 1) AS avg_cfs, "
        f"COUNT(*) AS n FROM read_parquet('{glob}', hive_partitioning=true) "
        f"WHERE entity_id = 'USGS-01184000' AND metric = 'streamflow' "
        f"GROUP BY yr ORDER BY yr"
    ).fetchall())
    for yr, avg, n in res:
        print(f"      {yr}: {avg} cfs ({n} days)")

    # --- 4. Aggregate analytical query (regional pulse prototype) -----------
    print("\n[4] Hot streak check: days where flow > 3x gauge's 20-yr median")
    (res, dt) = timed("flow > 3x median days", lambda: con.execute(
        f"WITH med AS (SELECT entity_id, median(value) AS m FROM "
        f"read_parquet('{glob}', hive_partitioning=true) "
        f"WHERE metric='streamflow' GROUP BY entity_id) "
        f"SELECT r.entity_id, COUNT(*) AS hot_days FROM "
        f"read_parquet('{glob}', hive_partitioning=true) r JOIN med "
        f"ON r.entity_id = med.entity_id WHERE r.metric='streamflow' "
        f"AND r.value > 3 * med.m GROUP BY r.entity_id ORDER BY hot_days DESC"
    ).fetchall())
    for e, n in res:
        print(f"      {e}: {n} hot days")

    # --- 5. PyArrow memory-mapped read ---------------------------------------
    print("\n[5] PyArrow memory-mapped read (single partition file)")
    sample = parquet_files[len(parquet_files) // 2]
    print(f"      file: {sample} ({Path(sample).stat().st_size/1e3:.1f} KB)")
    t0 = time.perf_counter()
    with pa.memory_map(sample) as mm:
        pf = pq.ParquetFile(mm)  # physical-file read: no hive-partition inference
        table = pf.read()
    dt = (time.perf_counter() - t0) * 1000
    print(f"      ParquetFile(memory_map) read: {dt:7.1f} ms -> "
          f"{table.num_rows:,} rows x {table.num_columns} cols")
    print(f"      schema: {[f.name for f in table.schema]}")
    print(f"      observed_at range: {table.column('observed_at')[0].as_py()} "
          f".. {table.column('observed_at')[-1].as_py()}")
    print(f"      metric values: {sorted(set(table.column('metric').to_pylist()))}")

    # --- 6. Site-filtered query timing (cold then warm) ----------------------
    print("\n[6] Site-filtered query timing — USGS-01184000 streamflow, Jan 2004")
    q = f"""
    SELECT COUNT(*) AS n, MIN(observed_at), MAX(observed_at)
    FROM read_parquet('{glob}', hive_partitioning=true)
    WHERE entity_id = 'USGS-01184000' AND metric = 'streamflow'
      AND observed_at BETWEEN '2004-01-01' AND '2004-01-31'
    """
    for label in ("cold (1st)", "warm (2nd)", "warm (3rd)"):
        t0 = time.perf_counter()
        res = con.execute(q).fetchall()
        dt = (time.perf_counter() - t0) * 1000
        print(f"      {label}: {dt:7.1f} ms -> {res}")

    # --- 7. Per-gauge x metric coverage ---------------------------------------
    print("\n[7] Per-gauge x metric coverage (0 streamflow rows = UNEXPECTED)")
    (res, _) = timed("entity x metric counts", lambda: con.execute(
        f"SELECT entity_id, metric, COUNT(*) AS n FROM "
        f"read_parquet('{glob}', hive_partitioning=true) "
        f"GROUP BY entity_id, metric"
    ).fetchall())
    by_metric: dict[str, dict[str, int]] = {}
    for e, m, n in res:
        by_metric.setdefault(e, {})[m] = n
    no_flow = [e for e, c in by_metric.items() if c.get("streamflow", 0) == 0]
    print(f"      gauges with 0 streamflow rows: {no_flow if no_flow else 'NONE — all 52 report flow'}")
    empty_combos = []
    for e in sorted(by_metric):
        for m in ("streamflow", "gage_height", "water_temperature"):
            if m not in by_metric[e]:
                empty_combos.append((e, m))
    print(f"      empty metric-gauge combos ({len(empty_combos)}):")
    for e, m in empty_combos:
        print(f"        {e} / {m}")
    print(f"      gauges reporting at least one metric: {len(by_metric)}")

    # --- 8. Overall observed_at coverage --------------------------------------
    print("\n[8] Overall observed_at coverage")
    (res, _) = timed("min/max observed_at", lambda: con.execute(
        f"SELECT MIN(observed_at), MAX(observed_at), COUNT(DISTINCT entity_id) "
        f"FROM read_parquet('{glob}', hive_partitioning=true)"
    ).fetchall())
    print(f"      min={res[0][0]}  max={res[0][1]}  distinct gauges={res[0][2]}")

    con.close()


if __name__ == "__main__":
    main()
