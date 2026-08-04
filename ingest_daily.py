#!/usr/bin/env python3
"""
Signals Before Disruption — Build 2: raw daily-values ingestion pipeline.

Pulls USGS OGC API daily values into the raw_observations schema (parquet,
partitioned by metric/year). Idempotent: raw JSON pages are cached to
data/raw_cache/ keyed by URL hash; parquet partitions are rebuilt in full and
overwritten, never appended.

API behaviors verified empirically (2026-08-02) before coding:
  * Batch queries work: comma-separated `monitoring_location_id` (tested w/ 10
    gauges) and comma-separated `parameter_code` (00060,00065,00010) in one request.
  * Pagination is CURSOR-based: follow links[rel="next"] href until absent.
  * limit cap is 50000 (100000 -> HTTP 400 "Limit of 50000 exceeded").
  * datetime window is INCLUSIVE of both endpoints (midnight-to-midnight returns
    both dates) -> 5-yr windows are requested as [start, start+5yr-1day].
  * Explicit parameter_code + statistic_id filters are REQUIRED: unfiltered
    responses mix statistics (00001/00002/00003/30800) and extra params
    (00095, 80154, 80155).

Usage:
  python3 ingest_daily.py                       # all gauges from stations.csv
  python3 ingest_daily.py --gauges USGS-01184000,USGS-07010000,USGS-14105700
  python3 ingest_daily.py --start 2004-01-01 --end 2026-08-01 --batch-size 10
  python3 ingest_daily.py --since 2026-07-01   # incremental: fetch from that date
                                                # onward, MERGE into existing
                                                # raw_observations (never
                                                # clobbers history), bypass the
                                                # disk cache by default
  python3 ingest_daily.py --since 2026-07-01 --no-cache
  python3 ingest_daily.py --since 2026-07-01 --cache-ttl 3600

Cache: raw JSON pages live in data/raw_cache/ (keyed by URL hash). The cache is
TTL-based (default 3600s) so USGS revisions (provisional -> approved) are
re-fetched once entries age out; pass --no-cache (or --cache-ttl 0) to bypass it
entirely. Incremental (--since) runs bypass the cache by default.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import requests

BASE_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items"
PROJECT_ROOT = Path(__file__).resolve().parent
STATIONS_CSV = PROJECT_ROOT / "stations.csv"
CACHE_DIR = PROJECT_ROOT / "data" / "raw_cache"
OUT_DIR = PROJECT_ROOT / "data" / "raw_observations"

PARAM_CODES = ["00060", "00065", "00010"]
STATISTIC_ID = "00003"  # daily mean
METRIC_MAP = {
    "00060": "streamflow",
    "00065": "gage_height",
    "00010": "water_temperature",
}
DEFAULT_START = date(2004, 1, 1)
UA = "river-personality-monitor/0.2 (build2-ingest)"


# --------------------------------------------------------------------------
# URL + cache helpers
# --------------------------------------------------------------------------

def build_url(
    gauges: list[str],
    start: date,
    end: date,
    limit: int = 50000,
    cursor: str | None = None,
) -> str:
    """Build the request URL. cursor, when given, replaces paging params."""
    params = {
        "f": "json",
        "monitoring_location_id": ",".join(gauges),
        "parameter_code": ",".join(PARAM_CODES),
        "statistic_id": STATISTIC_ID,
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T00:00:00Z",
        "limit": limit,
    }
    if cursor:
        params["cursor"] = cursor
    from urllib.parse import urlencode

    return f"{BASE_URL}?{urlencode(params)}"


def cache_path_for(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"{digest}.json"


def load_cached(url: str, max_age: float | None = None) -> dict | None:
    """Return the cached payload if present and fresh.

    max_age in seconds; None = no expiry check (old behavior). A stale entry
    (or max_age of 0) counts as a miss so USGS revisions (provisional ->
    approved) get re-fetched instead of being frozen forever.
    """
    path = cache_path_for(url)
    if path.exists():
        if max_age is not None:
            age = time.time() - path.stat().st_mtime
            if age > max_age:
                return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def save_cached(url: str, payload: dict) -> None:
    path = cache_path_for(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


# --------------------------------------------------------------------------
# HTTP with backoff
# --------------------------------------------------------------------------

def http_get_json(url: str, session: requests.Session) -> dict:
    """
    GET with retry policy:
      * 429: exponential backoff 15s -> 120s (doubling), +/-20% jitter
      * 5xx: retry up to 3 times, short backoff
      * other 4xx: raise immediately (cached-URL 400s are not transient)
    """
    backoff = 15.0
    for attempt in range(8):
        resp = session.get(url, timeout=120)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            jitter = backoff * random.uniform(0.8, 1.2)
            print(f"    [429] backoff {jitter:.0f}s (attempt {attempt + 1})", flush=True)
            time.sleep(jitter)
            backoff = min(backoff * 2, 120.0)
            continue
        if 500 <= resp.status_code < 600:
            if attempt >= 3:
                resp.raise_for_status()
            wait = 5 * (attempt + 1)
            print(f"    [{resp.status_code}] retry in {wait}s (attempt {attempt + 1})", flush=True)
            time.sleep(wait)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"gave up after backoff for {url}")


def fetch_page(url: str, session: requests.Session, stats: dict, cache_ttl: float = 3600.0) -> dict:
    """Cache-aware page fetch: fresh disk hit first, API second.

    cache_ttl <= 0 bypasses the cache entirely (no read, no write) — used for
    incremental runs where USGS revisions must be picked up.
    """
    if cache_ttl > 0:
        cached = load_cached(url, max_age=cache_ttl)
        if cached is not None:
            stats["cache_hits"] += 1
            return cached
    payload = http_get_json(url, session)
    if cache_ttl > 0:
        save_cached(url, payload)
    stats["api_calls"] += 1
    return payload


def iter_features(first_url: str, session: requests.Session, stats: dict, cache_ttl: float = 3600.0):
    """Follow links[rel=next] from the first page, yielding features."""
    url = first_url
    n_pages = 0
    while url:
        page = fetch_page(url, session, stats, cache_ttl=cache_ttl)
        n_pages += 1
        yield from page.get("features", [])
        nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
        url = nxt["href"] if nxt else None
    print(f"    ({n_pages} page(s))", flush=True)


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def add_years(d: date, n: int) -> date:
    """Add calendar years, clamping Feb-29 to Feb-28."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def iter_windows(start: date, end: date, window_years: int):
    """Yield (chunk_start, chunk_end) inclusive pairs covering [start, end].
    Calendar-accurate: chunk k spans [start+5k yr, start+5(k+1) yr - 1 day]."""
    cur = start
    while cur <= end:
        raw_end = add_years(cur, window_years) - timedelta(days=1)
        chunk_end = min(raw_end, end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


# --------------------------------------------------------------------------
# Transform (polars, lazy)
# --------------------------------------------------------------------------

def transform_features(features: list[dict], collected_at: datetime) -> pl.DataFrame:
    if not features:
        return pl.DataFrame(
            schema={
                "source": pl.String, "entity_id": pl.String, "observed_at": pl.Date,
                "collected_at": pl.Datetime("us"), "metric": pl.String,
                "parameter_code": pl.String, "value": pl.Float64, "unit": pl.String,
                "latitude": pl.Float64, "longitude": pl.Float64,
                "approval_status": pl.String, "qualifier": pl.String,
                "raw_payload": pl.String,
            }
        )
    pc = pl.col("properties").struct.field("parameter_code")
    lf = pl.LazyFrame(features)
    lf = lf.select(
        [
            pl.lit("usgs_daily").alias("source"),
            pl.col("properties").struct.field("monitoring_location_id").alias("entity_id"),
            pl.col("properties").struct.field("time").str.to_date().alias("observed_at"),
            pl.lit(collected_at).cast(pl.Datetime("us")).alias("collected_at"),
            pc.replace_strict(METRIC_MAP, default=pc).alias("metric"),
            pc.alias("parameter_code"),
            pl.col("properties").struct.field("value").cast(pl.Float64, strict=False).alias("value"),
            pl.col("properties").struct.field("unit_of_measure").alias("unit"),
            pl.col("geometry").struct.field("coordinates").list.get(1).cast(pl.Float64).alias("latitude"),
            pl.col("geometry").struct.field("coordinates").list.get(0).cast(pl.Float64).alias("longitude"),
            pl.col("properties").struct.field("approval_status").alias("approval_status"),
            pl.col("properties").struct.field("qualifier").alias("qualifier"),
            pl.col("properties").struct.json_encode().alias("raw_payload"),
        ]
    )
    # guard against any overlapping-chunk duplicates (window edges are inclusive)
    return lf.unique(
        subset=["entity_id", "parameter_code", "observed_at"], keep="first"
    ).collect()


# --------------------------------------------------------------------------
# Incremental merge
# --------------------------------------------------------------------------

def merge_with_existing(new: pl.DataFrame) -> pl.DataFrame:
    """Merge newly fetched rows into the existing raw_observations tree.

    Used by incremental (--since) runs so a partial window never clobbers
    already-ingested history. Existing + new are concatenated and deduped on
    (entity_id, parameter_code, observed_at) keeping the row with the newest
    collected_at — USGS revisions are provisional -> approved, so the latest
    fetch wins. The caller then rewrites every partition from the merged frame
    (still idempotent overwrite, never append).
    """
    existing_files = sorted(OUT_DIR.glob("**/*.parquet"))
    if not existing_files:
        return new
    existing = pl.scan_parquet(existing_files).collect()
    if existing.is_empty():
        return new
    return (
        pl.concat([existing, new], how="vertical_relaxed")
        .sort("collected_at")
        .unique(subset=["entity_id", "parameter_code", "observed_at"], keep="last")
    )


# --------------------------------------------------------------------------
# Partition writer (idempotent)
# --------------------------------------------------------------------------

def write_partitions(df: pl.DataFrame) -> dict:
    """Write data/raw_observations/metric=<metric>/year=<yyyy>.parquet.
    Each partition is built from the complete frame of this run, SORTED BY
    entity_id (then observed_at) so DuckDB can row-group-prune site-filtered
    queries via parquet min/max stats, and written once (overwrite); never
    append. Returns {partition: row_count}."""
    out: dict = {}
    if df.is_empty():
        return out
    df = df.with_columns(pl.col("observed_at").dt.year().alias("_year"))
    for (metric, year), group in df.group_by(["metric", "_year"], maintain_order=True):
        part_dir = OUT_DIR / f"metric={metric}"
        part_dir.mkdir(parents=True, exist_ok=True)
        path = part_dir / f"year={year}.parquet"
        # Sort by entity_id (then observed_at) so consecutive row groups cover
        # contiguous entity ranges -> DuckDB prunes groups via entity_id stats.
        # row_group_size=8192 keeps groups small enough for pruning to matter:
        # polars' default 512K-row groups would make each ~50K-row partition a
        # single row group, nullifying the sort. Compression/statistics/partition
        # layout and idempotent overwrite semantics are otherwise unchanged.
        group.drop("_year").sort(["entity_id", "observed_at"]).write_parquet(
            path, row_group_size=8192
        )
        out[f"metric={metric}/year={year}.parquet"] = group.height
    return out


# --------------------------------------------------------------------------
# Gauge selection
# --------------------------------------------------------------------------

def read_stations() -> dict[str, dict]:
    rows = {}
    with open(STATIONS_CSV, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows[row["entity_id"]] = row
    return rows


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="USGS daily values -> raw_observations parquet")
    ap.add_argument("--gauges", help="comma-separated USGS-xxx list (default: all in stations.csv)")
    ap.add_argument("--since", default=None,
                    help="fetch from this date onward (YYYY-MM-DD); enables incremental mode: "
                         "new rows are merged into the existing raw_observations tree "
                         "(history is never clobbered) and the disk cache is bypassed by default")
    ap.add_argument("--start", default=DEFAULT_START.isoformat(),
                    help="alias for --since (YYYY-MM-DD, default 2004-01-01); do not combine with --since")
    ap.add_argument("--end", default=date.today().isoformat(), help="YYYY-MM-DD (default today)")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the disk cache entirely (read AND write); default in --since mode")
    ap.add_argument("--cache-ttl", type=int, default=None,
                    help="cache freshness in seconds (default 3600; 0 = bypass)")
    ap.add_argument("--window-years", type=int, default=5, help="datetime chunk size (default 5)")
    ap.add_argument("--batch-size", type=int, default=10, help="max gauges per request (default 10)")
    ap.add_argument("--limit", type=int, default=50000, help="page size, server cap is 50000")
    ap.add_argument("--sleep", type=float, default=2.0, help="polite delay between requests (s)")
    args = ap.parse_args()

    since = date.fromisoformat(args.since) if args.since else None
    if since is not None and args.start != DEFAULT_START.isoformat():
        raise SystemExit("use --since OR --start, not both")
    start = since if since is not None else date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        raise SystemExit(f"--start/--since {start.isoformat()} is after --end {end.isoformat()}")

    # Effective cache policy: bypass (ttl 0) by default in incremental mode so
    # USGS revisions (provisional -> approved) are picked up on re-fetch; TTL
    # otherwise applies for interactive/full runs.
    if args.no_cache or (since is not None and args.cache_ttl is None):
        cache_ttl = 0
    else:
        cache_ttl = args.cache_ttl if args.cache_ttl is not None else 3600
    if cache_ttl > 0:
        print(f"Cache: {CACHE_DIR} (ttl={cache_ttl}s)")
    else:
        print(f"Cache: BYPASSED (no read/write; would be {CACHE_DIR})")
    if since is not None:
        print("Incremental mode (--since): new rows will be MERGED into existing raw_observations")
    else:
        print("Full-history mode: raw_observations rebuilt from the fetched window")

    stations = read_stations()
    if args.gauges:
        gauges = [g.strip() for g in args.gauges.split(",") if g.strip()]
    else:
        gauges = list(stations.keys())
    unknown = [g for g in gauges if g not in stations]
    if unknown:
        print(f"  WARNING: gauges not in stations.csv: {unknown} (API may return nothing)")
    print(f"Gauges ({len(gauges)}): {gauges}")

    collected_at = datetime.now(timezone.utc)
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    batches = [gauges[i : i + args.batch_size] for i in range(0, len(gauges), args.batch_size)]
    windows = list(iter_windows(start, end, args.window_years))
    total_requests = len(batches) * len(windows)
    print(f"Plan: {len(batches)} batch(es) x {len(windows)} window(s) = up to {total_requests} requests")
    print(f"Parameters: {PARAM_CODES} | statistic_id={STATISTIC_ID}")

    features: list[dict] = []
    stats = {"api_calls": 0, "cache_hits": 0}
    for bi, batch in enumerate(batches, 1):
        for wi, (wstart, wend) in enumerate(windows, 1):
            url = build_url(batch, wstart, wend, limit=args.limit)
            label = f"batch {bi}/{len(batches)} ({len(batch)} g) window {wstart}..{wend}"
            print(f"[{label}]", flush=True)
            page_features = list(iter_features(url, session, stats, cache_ttl=cache_ttl))
            features.extend(page_features)
            time.sleep(args.sleep * random.uniform(0.8, 1.2))

    print(f"\nFetched {len(features)} features total "
          f"({stats['api_calls']} API call(s), {stats['cache_hits']} cached page(s))")

    df = transform_features(features, collected_at)
    print(f"Transformed: {df.height} rows x {df.width} cols")
    if since is not None:
        before = df.height
        df = merge_with_existing(df)
        print(f"Merged with existing raw_observations: {df.height} rows (fetched {before})")
    print(df.group_by("metric").len().sort("metric"))

    partitions = write_partitions(df)
    total_rows = sum(partitions.values())
    n_bytes = sum(p.stat().st_size for p in OUT_DIR.rglob("*.parquet"))
    print(f"\nWrote {len(partitions)} partitions, {total_rows} rows, {n_bytes/1e6:.1f} MB")
    for part, rows in sorted(partitions.items()):
        print(f"  {part}: {rows} rows")


if __name__ == "__main__":
    main()
