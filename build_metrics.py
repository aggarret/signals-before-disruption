#!/usr/bin/env python3
"""Build 3: seasonal_baselines + daily_entity_metrics.

Input : data/raw_observations/metric=*/year=*.parquet   (633,257 rows, 52 gauges x 3 params)
Output:
  - data/seasonal_baselines.parquet
    per (entity_id, metric, day_of_year 1..366): mu, sigma, n_years
    Fixed 20-year baseline 2004-01-01 .. 2023-12-31, +-7-day circular DOY window.
  - data/daily_entity_metrics/metric=<metric>/year=<yyyy>.parquet
    per (entity_id, metric, observed_at): daily stats, daily_change, rise_rate_3d,
    flow_percentile, anomaly_score, completeness_score, record_proximity.
    Calendar-reindexed (gaps are explicit null rows, completeness_score = 0).

Design notes
  - Null-value raw rows are treated as gaps: a day whose only raw row has a null
    value becomes a completeness_score = 0 row after reindexing (verified: no day
    mixes null and non-null rows).
  - Baselines are fixed and precomputed; the daily layer is a cheap join.
  - flow_percentile = count(historical values <= v) / total * 100 over ALL data
    (2004-2026), ties share the same percentile (rank method "max").
  - sigma = 0 or null is stored as null -> anomaly_score null (no signal).
  - Idempotent: output trees are wiped and rewritten on every run.

Usage: python3 build_metrics.py [--today YYYY-MM-DD]

  --today overrides the end of the observation calendar (default: system date).
  The 2004-2023 baseline window is FIXED by design; all other metrics are
  recomputed over ALL raw data present under data/raw_observations/.
"""
from __future__ import annotations

import argparse
import shutil
import time
from datetime import date
from pathlib import Path

import polars as pl

RAW_GLOB = "data/raw_observations/**/*.parquet"
BASELINE_START = date(2004, 1, 1)
BASELINE_END = date(2023, 12, 31)
WINDOW = 7                # +-7-day seasonal window

# End of the observation calendar (inclusive). No longer hardcoded: defaults to
# the system date so re-running the pipeline advances with the real clock.
parser = argparse.ArgumentParser(
    description="Build 3: seasonal_baselines + daily_entity_metrics from raw_observations."
)
parser.add_argument(
    "--today", type=str, default=None,
    help="Override 'today' date (YYYY-MM-DD), the inclusive end of the observation "
         "calendar. Defaults to the system date.",
)
args = parser.parse_args()
TODAY = date.fromisoformat(args.today) if args.today else date.today()

OUT_BASELINES = Path("data/seasonal_baselines.parquet")
OUT_DAILY = Path("data/daily_entity_metrics")
ROW_GROUP_SIZE = 8192

DAILY_COLUMNS = [
    "entity_id", "metric", "observed_at", "observation_count",
    "minimum", "maximum", "average", "daily_change", "rise_rate_3d",
    "flow_percentile", "anomaly_score", "completeness_score", "record_proximity",
]


def load_raw() -> pl.LazyFrame:
    """Raw observations, null-value rows dropped (they are gaps, see module doc)."""
    return (
        pl.scan_parquet(RAW_GLOB)
        .select(["entity_id", "metric", "observed_at", "value"])
        .filter(pl.col("value").is_not_null())
    )


def build_seasonal_baselines() -> pl.DataFrame:
    """mu/sigma per (entity_id, metric, day_of_year) over the fixed 20-yr baseline.

    Each observation with DOY d contributes to every target DOY in [d-7, d+7] in a
    circular 1..366 DOY space ((d-1+k) mod 366 + 1), so DOY 1 sees late December of
    the prior year and DOY 365/366 see early January of the next year.
    """
    t0 = time.perf_counter()
    base = (
        load_raw()
        .filter(
            (pl.col("observed_at") >= pl.lit(BASELINE_START))
            & (pl.col("observed_at") <= pl.lit(BASELINE_END))
        )
        .with_columns(
            day_of_year=pl.col("observed_at").dt.ordinal_day(),
            year=pl.col("observed_at").dt.year(),
        )
        .select(["entity_id", "metric", "value", "day_of_year", "year"])
        .collect()
    )
    offsets = pl.DataFrame({"offset": range(-WINDOW, WINDOW + 1)})
    stats = (
        base.join(offsets, how="cross")
        .with_columns(
            target_doy=((pl.col("day_of_year") - 1 + pl.col("offset")) % 366 + 1).cast(pl.Int32)
        )
        .group_by(["entity_id", "metric", "target_doy"])
        .agg(
            mu=pl.col("value").mean(),
            sigma=pl.col("value").std(),          # sample std, ddof=1
            n_years=pl.col("year").n_unique().cast(pl.Int32),
        )
    )

    # Full 1..366 grid per combo so downstream joins always match; mu/sigma stay
    # null for DOYs with no baseline coverage.
    grid = (
        stats.select(["entity_id", "metric"]).unique()
        .join(pl.DataFrame({"day_of_year": range(1, 367)}), how="cross")
    )
    out = (
        grid.join(stats, left_on=["entity_id", "metric", "day_of_year"],
                  right_on=["entity_id", "metric", "target_doy"], how="left")
        .with_columns(
            sigma=pl.when(pl.col("sigma").is_null() | (pl.col("sigma") == 0.0))
            .then(None)
            .otherwise(pl.col("sigma"))
        )
        .sort(["entity_id", "metric", "day_of_year"])
    )
    out.write_parquet(OUT_BASELINES, row_group_size=ROW_GROUP_SIZE)
    print(f"seasonal_baselines: {out.height} rows ({out['n_years'].null_count()} DOYs without baseline data) -> {OUT_BASELINES}  [{time.perf_counter()-t0:.1f}s]")
    return out


def build_daily_entity_metrics() -> pl.DataFrame:
    t0 = time.perf_counter()

    # 1. Daily stats + flow_percentile over ALL observed days (2004-2026).
    daily = (
        load_raw()
        .group_by(["entity_id", "metric", "observed_at"])
        .agg(
            observation_count=pl.len().cast(pl.Int32),
            minimum=pl.col("value").min(),
            maximum=pl.col("value").max(),
            average=pl.col("value").mean(),
        )
        .with_columns(
            flow_percentile=(
                pl.col("average").rank("max").over(["entity_id", "metric"])
                / pl.len().over(["entity_id", "metric"])
                * 100.0
            )
        )
        .collect()
    )

    # 2. Calendar reindex: complete date range per combo, first observed day -> TODAY.
    starts = daily.group_by(["entity_id", "metric"]).agg(start=pl.col("observed_at").min())
    if starts.is_empty():
        raise SystemExit(
            "no raw observations found under data/raw_observations/ — "
            "run ingest_daily.py first"
        )
    earliest = starts["start"].min()
    if TODAY < earliest:
        raise SystemExit(
            f"--today {TODAY.isoformat()} is before the earliest observation "
            f"({earliest.isoformat()}); nothing to build"
        )
    # Complete calendar per combo: cross-join day offsets, keep days <= TODAY.
    max_days = (TODAY - starts["start"].min()).days + 1
    offsets = pl.DataFrame({"off": pl.arange(0, max_days, eager=True)})
    calendar = (
        starts.join(offsets, how="cross")
        .filter(pl.col("start") + pl.duration(days=pl.col("off")) <= pl.lit(TODAY))
        .with_columns(observed_at=pl.col("start") + pl.duration(days=pl.col("off")))
        .drop(["start", "off"])
        .sort(["entity_id", "metric", "observed_at"])
    )
    full = (
        calendar.join(daily, on=["entity_id", "metric", "observed_at"], how="left")
        .with_columns(
            observation_count=pl.col("observation_count").fill_null(0),
            completeness_score=pl.when(pl.col("observation_count") > 0).then(1.0).otherwise(0.0),
        )
        .sort(["entity_id", "metric", "observed_at"])
    )

    # 3. Per gauge-metric series features (computed on the reindexed calendar, so a
    #    shift across a gap is null rather than a silently wrong delta).
    full = full.with_columns(
        daily_change=pl.col("average") - pl.col("average").shift(1).over(["entity_id", "metric"]),
        rise_rate_3d=(pl.col("average") - pl.col("average").shift(3).over(["entity_id", "metric"])) / 3.0,
        record_proximity=pl.col("average") / pl.col("average").max().over(["entity_id", "metric"]),
        doy=pl.col("observed_at").dt.ordinal_day(),
    )

    # 4. Seasonal z-score: join fixed baselines on (entity_id, metric, day_of_year).
    base = (
        pl.scan_parquet(OUT_BASELINES)
        .select(["entity_id", "metric", "day_of_year", "mu", "sigma"])
        .collect()
    )
    full = (
        full.join(base, left_on=["entity_id", "metric", "doy"],
                  right_on=["entity_id", "metric", "day_of_year"], how="left")
        .with_columns(
            anomaly_score=pl.when(pl.col("sigma").is_not_null() & pl.col("average").is_not_null())
            .then((pl.col("average") - pl.col("mu")) / pl.col("sigma"))
            .otherwise(None)
        )
        .with_columns(year=pl.col("observed_at").dt.year())
        .select(DAILY_COLUMNS + ["year"])
    )

    # 5. Hive-partitioned, idempotent write.
    if OUT_DAILY.exists():
        shutil.rmtree(OUT_DAILY)
    OUT_DAILY.mkdir(parents=True)
    total = 0
    for (metric, year), grp in full.group_by(["metric", "year"], maintain_order=True):
        part_dir = OUT_DAILY / f"metric={metric}" / f"year={year}"
        part_dir.mkdir(parents=True, exist_ok=True)
        grp.sort(["entity_id", "observed_at"]).write_parquet(
            part_dir / "data.parquet", row_group_size=ROW_GROUP_SIZE
        )
        total += grp.height
    n_parts = len(list(OUT_DAILY.glob("metric=*")))
    print(f"daily_entity_metrics: {total} rows across {n_parts} metric dirs -> {OUT_DAILY}  [{time.perf_counter()-t0:.1f}s]")
    return full


if __name__ == "__main__":
    t_start = time.perf_counter()
    build_seasonal_baselines()
    build_daily_entity_metrics()
    print(f"done in {time.perf_counter()-t_start:.1f}s")
