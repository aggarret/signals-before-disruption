#!/usr/bin/env python3
"""healthcheck.py — Verify data integrity after update.

Run after update_data.py to verify the app still loads and data is sane.

Checks (hard failures, exit 1 if any):
  - raw_observations row count > 0
  - daily_entity_metrics row count > 0
  - daily_category_metrics row count > 0
  - no NULLs in critical raw_observations identity columns
    (entity_id, observed_at, metric)
  - value NULL share <= 2%% of raw_observations rows
    (USGS legitimately returns no-data records, e.g. discontinued
    gauges, so small NULL counts are normal; a large share means
    the ingestion wrote garbage)
  - all 3 metrics present across raw_observations
  - app imports successfully (import app)

Checks (soft warnings only, never a failure):
  - max observed_at in raw_observations within last 3 days
    (safe to run before the first update — stale data is warned, not failed)
  - all 52 gauges present in raw_observations

Exit 0 if all hard checks pass; exit 1 if any hard check fails.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import duckdb

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

RAW_GLOB = os.path.join(DATA_DIR, "raw_observations", "metric=*", "year=*.parquet")
EM_GLOB = os.path.join(DATA_DIR, "daily_entity_metrics", "metric=*", "year=*", "data.parquet")
CM_GLOB = os.path.join(DATA_DIR, "daily_category_metrics", "metric=*", "year=*", "data.parquet")
STATIONS_PATH = os.path.join(BASE_DIR, "stations.csv")

EXPECTED_METRICS = {"streamflow", "water_temperature", "gage_height"}
# Identity columns: a single NULL here means corruption -> hard fail.
IDENTITY_COLUMNS = ["entity_id", "observed_at", "metric"]
# value: NULLs are legitimate (USGS no-data records: discontinued gauges,
# provisional gaps). Hard-fail only if the share gets suspiciously large.
VALUE_NULL_TOLERANCE = 0.02
STALE_AFTER_DAYS = 3


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    if not os.path.isdir(DATA_DIR):
        failures.append(f"data directory missing: {DATA_DIR}")
        _report(failures, warnings)
        return 1

    conn = duckdb.connect()  # in-memory; we only read parquet files
    try:
        # ------------------------------------------------------------------
        # Row counts
        # ------------------------------------------------------------------
        for label, path in (
            ("raw_observations", RAW_GLOB),
            ("daily_entity_metrics", EM_GLOB),
            ("daily_category_metrics", CM_GLOB),
        ):
            if not _glob_matches(path):
                failures.append(f"{label}: no parquet files found ({path})")
                continue
            n = int(conn.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0])
            if n <= 0:
                failures.append(f"{label}: row count is {n}, expected > 0")
            else:
                print(f"OK  {label}: {n:,} rows")

        # ------------------------------------------------------------------
        # NULL check on raw_observations columns
        # ------------------------------------------------------------------
        if _glob_matches(RAW_GLOB):
            total = int(
                conn.execute(f"SELECT count(*) FROM read_parquet('{RAW_GLOB}')").fetchone()[0]
            )
            for col in IDENTITY_COLUMNS:
                n_nulls = int(
                    conn.execute(
                        f"SELECT count(*) FROM read_parquet('{RAW_GLOB}') "
                        f"WHERE {col} IS NULL"
                    ).fetchone()[0]
                )
                if n_nulls > 0:
                    failures.append(
                        f"raw_observations: {n_nulls:,} NULLs in critical column '{col}'"
                    )
                else:
                    print(f"OK  raw_observations.{col}: no NULLs")

            n_value_nulls = int(
                conn.execute(
                    f"SELECT count(*) FROM read_parquet('{RAW_GLOB}') WHERE value IS NULL"
                ).fetchone()[0]
            )
            value_null_share = n_value_nulls / total if total else 0.0
            if value_null_share > VALUE_NULL_TOLERANCE:
                failures.append(
                    f"raw_observations: {n_value_nulls:,} NULLs in 'value' "
                    f"({value_null_share:.1%} of rows, tolerance {VALUE_NULL_TOLERANCE:.0%}) — "
                    f"suspiciously high, ingestion may have written garbage"
                )
            else:
                print(
                    f"OK  raw_observations.value: {n_value_nulls:,} NULLs "
                    f"({value_null_share:.2%}, within tolerance — USGS no-data records)"
                )

        # ------------------------------------------------------------------
        # Metric coverage
        # ------------------------------------------------------------------
        if _glob_matches(RAW_GLOB):
            present = {
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT metric FROM read_parquet('{RAW_GLOB}')"
                ).fetchall()
            }
            missing = EXPECTED_METRICS - present
            if missing:
                failures.append(f"raw_observations: missing metrics: {sorted(missing)}")
            else:
                print(f"OK  metrics present: {sorted(EXPECTED_METRICS)}")

        # ------------------------------------------------------------------
        # Freshness (soft — warn only)
        # ------------------------------------------------------------------
        if _glob_matches(RAW_GLOB):
            max_date = conn.execute(
                f"SELECT max(observed_at)::DATE FROM read_parquet('{RAW_GLOB}')"
            ).fetchone()[0]
            if max_date is None:
                warnings.append("raw_observations: no dates found")
            else:
                days_old = (date.today() - max_date).days
                print(f"OK  raw_observations max date: {max_date} ({days_old} day(s) old)")
                if days_old > STALE_AFTER_DAYS:
                    warnings.append(
                        f"raw_observations is {days_old} day(s) old "
                        f"(> {STALE_AFTER_DAYS}); data may not have been updated yet"
                    )

        # ------------------------------------------------------------------
        # Gauge coverage (soft — warn only)
        # ------------------------------------------------------------------
        if os.path.exists(STATIONS_PATH):
            stations = conn.execute(
                f"SELECT entity_id FROM read_csv('{STATIONS_PATH}')"
            ).fetchall()
            expected_entities = {r[0] for r in stations}
            if _glob_matches(RAW_GLOB):
                present_entities = {
                    r[0]
                    for r in conn.execute(
                        f"SELECT DISTINCT entity_id FROM read_parquet('{RAW_GLOB}')"
                    ).fetchall()
                }
                missing_gauges = sorted(expected_entities - present_entities)
                if missing_gauges:
                    warnings.append(
                        f"gauges missing from raw_observations "
                        f"({len(missing_gauges)}/{len(expected_entities)}): "
                        f"{missing_gauges[:10]}{'...' if len(missing_gauges) > 10 else ''}"
                    )
                else:
                    print(
                        f"OK  all {len(expected_entities)} gauges present "
                        f"in raw_observations"
                    )
            else:
                warnings.append(
                    "cannot check gauge coverage: raw_observations parquet not found"
                )

        # ------------------------------------------------------------------
        # App import
        # ------------------------------------------------------------------
        sys.path.insert(0, BASE_DIR)
        try:
            import app  # noqa: F401

            print("OK  import app")
        except Exception as exc:  # noqa: BLE001 — report any import failure
            failures.append(f"import app failed: {exc!r}")

    finally:
        conn.close()

    return _report(failures, warnings)


def _glob_matches(path: str) -> bool:
    import glob

    return len(glob.glob(path)) > 0


def _report(failures: list[str], warnings: list[str]) -> int:
    for w in warnings:
        print(f"WARN {w}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        print(f"\nhealthcheck FAILED ({len(failures)} issue(s))")
        return 1
    print("\nhealthcheck PASSED (warnings above are non-fatal)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
