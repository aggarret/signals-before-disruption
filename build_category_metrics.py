#!/usr/bin/env python3
"""Build 4: daily_category_metrics — the regional rollup layer.

Input :
  - data/daily_entity_metrics/metric=*/year=*/data.parquet  (687,166 rows,
    per (entity_id, metric, observed_at) with anomaly_score, rise_rate_3d,
    completeness_score)
  - stations.csv (52 gauges, 8 regions)

Output:
  - data/daily_category_metrics/metric=<metric>/year=<yyyy>.parquet
    One row per (date, region, metric) — the national/regional pulse:
      date                Date
      region              from stations.csv (8 regions)
      metric              streamflow | gage_height | water_temperature
      entity_count        gauges reporting that day (completeness_score > 0)
                          — a data-health signal: drops mean gauges going offline
      event_count         gauges with |anomaly_score| >= 2.5 that day
      average_anomaly     mean anomaly_score across reporting gauges (nulls excluded)
      extreme_entity_count  same value as event_count (kept as its own column)
      fastest_risers      top-5 gauges by rise_rate_3d as a polars list[struct]:
                          [{entity_id, station_name, rise_rate_3d, value}]
                          (completeness_score > 0 and rise_rate_3d non-null only)

Partitioned by metric/year (3 x 23 = 69 partitions); sorted by (date, region)
within each partition. Idempotent: the output tree is wiped and rewritten on
every run.

Usage: python3 build_category_metrics.py [--today YYYY-MM-DD]

  --today optionally caps the rollup at a date (default: use every row present
  in daily_entity_metrics). Rebuilds from whatever daily_entity_metrics
  contains, so it automatically picks up incremental builds from build_metrics.py.
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path

import polars as pl

DAILY_GLOB = "data/daily_entity_metrics/metric=*/year=*/data.parquet"
STATIONS_CSV = Path("stations.csv")
OUT_DIR = Path("data/daily_category_metrics")
ROW_GROUP_SIZE = 8192

# Columns of the final table (order matters for the write + readers).
OUT_COLUMNS = [
    "date", "region", "metric",
    "entity_count", "event_count", "average_anomaly",
    "extreme_entity_count", "fastest_risers", "year",
]

# Struct fields inside fastest_risers.
RISER_FIELDS = ["entity_id", "station_name", "rise_rate_3d", "value"]


def load_stations() -> pl.DataFrame:
    """region + station_name per gauge from stations.csv."""
    return (
        pl.read_csv(STATIONS_CSV)
        .select(["entity_id", "region", "station_name"])
    )


def build_category_metrics(today: date | None = None) -> pl.DataFrame:
    """Regional rollup from data/daily_entity_metrics.

    today, when given, caps the rollup at that date (rows after it are dropped).
    When None (default) every row in daily_entity_metrics is used.
    """
    t0 = time.perf_counter()

    daily = pl.scan_parquet(DAILY_GLOB).select(
        ["entity_id", "metric", "observed_at", "average",
         "rise_rate_3d", "anomaly_score", "completeness_score"]
    )
    if today is not None:
        daily = daily.filter(pl.col("observed_at") <= pl.lit(today))
    stations = load_stations()

    # ---- 1. Core regional aggregates: counts + mean anomaly. ----------------
    # Boolean sums ignore nulls, so event_count is safe around null anomaly
    # scores; mean() drops nulls by default (anomaly null => gauge not reporting
    # or outside baseline, so excluding is exactly "reporting gauges").
    core = (
        daily.join(stations.lazy(), on="entity_id", how="left")
        .group_by(["observed_at", "region", "metric"])
        .agg(
            # Count every gauge that has a row for the day — this matches
            # the map, which renders all rows (offline gauges as grey dots).
            entity_count=pl.len().cast(pl.Int32),
            event_count=(
                pl.col("anomaly_score").abs().ge(2.5).sum().cast(pl.Int32)
            ),
            average_anomaly=pl.col("anomaly_score").mean(),
            # same signal, separate column per locked design
            extreme_entity_count=(
                pl.col("anomaly_score").abs().ge(2.5).sum().cast(pl.Int32)
            ),
        )
        .collect()
    )

    # ---- 2. fastest_risers: top-5 by rise_rate_3d per region-metric-day. ----
    # Filter first (completeness > 0 AND non-null rise_rate), then aggregate to
    # a list[struct], sort the list by rise_rate_3d desc, keep head(5).
    risers = (
        daily.join(stations.lazy(), on="entity_id", how="left")
        .filter(
            pl.col("completeness_score").gt(0)
            & pl.col("rise_rate_3d").is_not_null()
        )
        .group_by(["observed_at", "region", "metric"])
        .agg(
            fastest_risers=pl.struct(
                [
                    pl.col("entity_id"),
                    pl.col("station_name"),
                    pl.col("rise_rate_3d"),
                    pl.col("average").alias("value"),
                ]
            )
            .sort_by("rise_rate_3d", descending=True)
            .head(5)
        )
        .collect()
    )

    # ---- 3. Merge, shape, partition, write. ---------------------------------
    out = (
        core.join(
            risers, on=["observed_at", "region", "metric"], how="left"
        )
        .rename({"observed_at": "date"})
        .with_columns(year=pl.col("date").dt.year())
        .select(OUT_COLUMNS)
    )

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    total = 0
    n_parts = 0
    for (metric, year), grp in out.group_by(["metric", "year"], maintain_order=True):
        part_dir = OUT_DIR / f"metric={metric}" / f"year={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file in the same directory (same filesystem), then
        # os.replace — readers never observe a half-written partition.
        path = part_dir / "data.parquet"
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
        os.close(tmp_fd)
        try:
            grp.sort(["date", "region"]).write_parquet(
                tmp_path, row_group_size=ROW_GROUP_SIZE
            )
            os.replace(tmp_path, str(path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        total += grp.height
        n_parts += 1

    n_regions = out["region"].n_unique()
    print(f"daily_category_metrics: {total:,} rows, {n_regions} regions, "
          f"{n_parts} partitions -> {OUT_DIR}  [{time.perf_counter()-t0:.1f}s]")
    print(f"  coverage: {out['date'].min()} .. {out['date'].max()}"
          + (f" (capped at --today {today.isoformat()})" if today else ""))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Build 4: regional rollup from daily_entity_metrics."
    )
    ap.add_argument(
        "--today", type=str, default=None,
        help="Cap the rollup at this date (YYYY-MM-DD). Default: use all rows "
             "present in daily_entity_metrics.",
    )
    a = ap.parse_args()
    today = date.fromisoformat(a.today) if a.today else None
    t_start = time.perf_counter()
    df = build_category_metrics(today=today)
    print(f"done in {time.perf_counter()-t_start:.1f}s")
