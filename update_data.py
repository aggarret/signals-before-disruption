#!/usr/bin/env python3
"""update_data.py — Robust incremental data update for River Personality Monitor.

Fetches new/revised data from USGS, upserts into raw_observations, then
rebuilds all metrics. Safe to run daily (idempotent; a no-op when data is
already current).

Why "robust":
  * The USGS disk cache (data/raw_cache) is BYPASSED for fetches — provisional
    values may have been revised, and the cache is keyed by URL, not content.
  * Requests are batched like ingest_daily.py (10 gauges/request, 2s jittered
    sleep, 429/5xx backoff via ingest_daily.http_get_json). If a batch fails it
    is split in half recursively down to single gauges, so one bad gauge can
    never take down the whole run.
  * Upsert is by (entity_id, parameter_code, observed_at). Only rows whose
    value / approval_status / qualifier / unit / lat / lon actually changed are
    rewritten; identical rows keep their original collected_at.
  * Only year partitions touched by the fetch window are rewritten; every other
    year file is left byte-identical.
  * A full backup of data/ (raw + both metric layers + baselines, ~32 MB) is
    taken to data/backup/YYYY-MM-DD/ before anything is written, and restored
    automatically if a write or metrics rebuild fails.
  * Metrics are fully rebuilt (build_metrics + build_category_metrics, ~3 s)
    rather than incrementally updated — cheaper and safer.
  * USGS revises provisional -> approved on a rolling basis, so the default
    fetch window is the last 30 days (plus 2 days before the last data date so
    late-arriving stragglers are caught).

Design notes
  - build_metrics.py / build_category_metrics.py take a --today flag; they are
    re-run as subprocesses with --today set to the post-upsert max(observed_at)
    so the observation calendar / rollup cap at the newest observed date
    (no fabricated trailing gap days). They are never imported (build_metrics
    parses sys.argv at import) and never modified.
  - The API window is inclusive on both ends, matching ingest_daily.py.
  - approval_status values seen in the data: "Provisional" | "Approved".

Usage:
    python3 update_data.py                # Normal daily update
    python3 update_data.py --dry-run      # Fetch + compare only; write nothing
    python3 update_data.py --since 2026-07-01   # Fetch from a specific date
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import requests

# Make sibling modules importable no matter where the script is run from.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ingest_daily as ing          # API params, HTTP backoff, transform, stations
import build_hydro                  # rebuilds hydro_correlation/ from LIVE sources (cloud mode; import-safe — parses argv only inside main())
# NOTE: build_metrics.py / build_category_metrics.py are invoked as SUBPROCESSES
# (see rebuild_metrics) — never imported, because build_metrics.py parses
# sys.argv at import time, which would swallow update_data's own CLI flags.

# ---------------------------------------------------------------------------
# Paths (absolute, relative to this module so CWD never matters)
# ---------------------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw_observations"
EM_DIR = DATA_DIR / "daily_entity_metrics"
CM_DIR = DATA_DIR / "daily_category_metrics"
SB_PATH = DATA_DIR / "seasonal_baselines.parquet"
BACKUP_ROOT = DATA_DIR / "backup"
UPDATE_LOG = DATA_DIR / "UPDATE_LOG.md"

UA = "river-personality-monitor/0.3 (update_data)"
DEFAULT_REVISION_DAYS = 30   # how far back to fetch for provisional->approved flips
DEFAULT_BATCH_SIZE = 10      # max gauges per request (same as ingest_daily)
DEFAULT_SLEEP = 2.0          # polite delay between requests (same as ingest_daily)
DEFAULT_LIMIT = 50000        # page size (server cap is 50000)
ROW_GROUP_SIZE = 8192

# Canonical column order of raw_observations partitions (matches existing files).
RAW_COLUMNS = [
    "source", "entity_id", "observed_at", "collected_at", "metric",
    "parameter_code", "value", "unit", "latitude", "longitude",
    "approval_status", "qualifier", "raw_payload",
]
RAW_SCHEMA = {
    "source": pl.String,
    "entity_id": pl.String,
    "observed_at": pl.Date,
    "collected_at": pl.Datetime("us"),
    "metric": pl.String,
    "parameter_code": pl.String,
    "value": pl.Float64,
    "unit": pl.String,
    "latitude": pl.Float64,
    "longitude": pl.Float64,
    "approval_status": pl.String,
    "qualifier": pl.List(pl.String),
    "raw_payload": pl.String,
}
KEY = ["entity_id", "parameter_code", "observed_at"]   # upsert key
STABLE = ["source", "entity_id", "observed_at", "metric", "parameter_code"]
# Fields taken from the freshly fetched row when a row is revised.
CONTENT = ["value", "unit", "latitude", "longitude",
           "approval_status", "qualifier", "raw_payload", "collected_at"]


# ---------------------------------------------------------------------------
# Existing data
# ---------------------------------------------------------------------------

def query_last_data_date() -> date | None:
    """max(observed_at) across all raw_observations, or None when empty."""
    if not list(RAW_DIR.rglob("year=*.parquet")):
        return None
    glob = str(RAW_DIR / "metric=*" / "year=*.parquet")
    return pl.scan_parquet(glob).select(pl.col("observed_at").max()).collect().item()


def load_existing(since: date, end: date) -> pl.DataFrame:
    """All existing raw rows whose year falls inside [since, end].

    Only these partitions can be affected by the fetch, so only these are
    loaded (and later rewritten); other years stay untouched on disk.
    """
    years = set(range(since.year, end.year + 1))
    files = [
        p for p in RAW_DIR.rglob("year=*.parquet")
        if int(p.stem.split("=")[1]) in years
    ]
    if not files:
        return pl.DataFrame(schema=RAW_SCHEMA)
    return pl.concat([pl.read_parquet(p) for p in files])


# ---------------------------------------------------------------------------
# Fetching (cache-bypassed, batch-splitting)
# ---------------------------------------------------------------------------

def fetch_batch(gauges: list[str], start: date, end: date,
                session: requests.Session, limit: int, sleep: float,
                save_cache: bool) -> tuple[list[dict], list[str]]:
    """Fetch daily-value features for one gauge batch.

    On request failure the batch is split in half recursively until single
    gauges are isolated, so one bad gauge degrades the run instead of killing
    it. Returns (features, failed_gauge_ids).
    """
    features: list[dict] = []
    failed: list[str] = []
    try:
        url = ing.build_url(gauges, start, end, limit=limit)
        while url:
            page = ing.http_get_json(url, session)      # backoff, no disk cache
            page_features = page.get("features", [])
            features.extend(page_features)
            if save_cache:                              # refresh stale cache entries
                try:
                    ing.save_cached(url, page)
                except Exception:
                    pass                                # cache refresh is best-effort
            nxt = next((l for l in page.get("links", []) if l.get("rel") == "next"), None)
            url = nxt["href"] if nxt else None
        time.sleep(sleep * random.uniform(0.8, 1.2))    # same polite pattern as ingest
        return features, failed
    except Exception:
        if len(gauges) <= 1:
            failed.extend(gauges)
            return features, failed
        time.sleep(1.0)                                 # brief pause before retry
        mid = len(gauges) // 2
        f1, fail1 = fetch_batch(gauges[:mid], start, end, session, limit, sleep, save_cache)
        f2, fail2 = fetch_batch(gauges[mid:], start, end, session, limit, sleep, save_cache)
        return features + f1 + f2, fail1 + fail2


def fetch_all(gauges: list[str], start: date, end: date,
              session: requests.Session, args) -> tuple[list[dict], list[str]]:
    """Fetch over all gauge batches. Returns (features, failed_gauge_ids)."""
    features: list[dict] = []
    failed: list[str] = []
    save_cache = not args.dry_run
    for i in range(0, len(gauges), args.batch_size):
        batch = gauges[i:i + args.batch_size]
        feats, fl = fetch_batch(batch, start, end, session,
                                args.limit, args.sleep, save_cache)
        features.extend(feats)
        failed.extend(fl)
    return features, failed


# ---------------------------------------------------------------------------
# Upsert (pure comparison; no I/O)
# ---------------------------------------------------------------------------

def upsert_compare(existing: pl.DataFrame, fetched: pl.DataFrame):
    """Merge fetched rows into existing; return (merged, counts).

    counts = {"new", "updated", "unchanged", "provisional_to_approved"}.
    A row is "updated" when any of value / approval_status / qualifier / unit /
    latitude / longitude changed; identical rows keep their original
    collected_at. Purely in-memory — callers decide when to write.
    """
    counts = {"new": 0, "updated": 0, "unchanged": 0, "provisional_to_approved": 0}
    if fetched.is_empty():
        return existing, counts

    fetched = fetched.cast(RAW_SCHEMA)
    # Explicit join marker (not a data column) so overlap detection never
    # depends on whether collected_at happens to be null.
    fetched_marked = fetched.with_columns(pl.lit(True).alias("_incoming"))
    merged = existing.join(fetched_marked, on=KEY, how="left", suffix="_new")
    # _incoming exists only on the right side, so it is not suffixed: null for
    # existing rows with no fetched match, True for matched rows.
    overlap = merged.filter(pl.col("_incoming").is_not_null())
    untouched = merged.filter(pl.col("_incoming").is_null()).select(existing.columns)
    new_rows = fetched.join(existing.select(KEY), on=KEY, how="anti")

    if not overlap.is_empty():
        changed = (
            ~pl.col("value").eq_missing(pl.col("value_new"))
            | ~pl.col("approval_status").eq_missing(pl.col("approval_status_new"))
            | ~pl.col("qualifier").eq_missing(pl.col("qualifier_new"))
            | ~pl.col("unit").eq_missing(pl.col("unit_new"))
            | ~pl.col("latitude").eq_missing(pl.col("latitude_new"))
            | ~pl.col("longitude").eq_missing(pl.col("longitude_new"))
        ).fill_null(False)
        overlap = overlap.with_columns(changed.alias("_c"))
        updated = overlap.filter(pl.col("_c")).select(
            STABLE + [pl.col(f"{f}_new").alias(f) for f in CONTENT]
        )
        unchanged = overlap.filter(~pl.col("_c")).select(existing.columns)
        p2a = overlap.filter(
            pl.col("_c")
            & (pl.col("approval_status") != "Approved")
            & (pl.col("approval_status_new") == "Approved")
        ).height
        counts["updated"] = updated.height
        counts["unchanged"] = unchanged.height
        counts["provisional_to_approved"] = p2a
    else:
        updated = pl.DataFrame(schema=existing.schema)
        unchanged = pl.DataFrame(schema=existing.schema)

    counts["new"] = new_rows.height

    # Canonicalize column order on every part (updated/new rows are built in a
    # different order than RAW_COLUMNS) so a plain vertical concat is safe.
    parts = [
        p.select(RAW_COLUMNS)
        for p in (untouched, unchanged, updated, new_rows)
        if not p.is_empty()
    ]
    merged_final = (
        pl.concat(parts, how="vertical").select(RAW_COLUMNS)
        if parts else existing
    )
    return merged_final, counts


# ---------------------------------------------------------------------------
# Writing, backup, restore
# ---------------------------------------------------------------------------

def write_partitions(df: pl.DataFrame) -> dict:
    """Rewrite only the year partitions present in df (already window-filtered).

    Files are sorted by (entity_id, observed_at) and written with
    row_group_size=8192, exactly like ingest_daily.py, so DuckDB row-group
    pruning keeps working. Returns {relative_path: row_count}.
    """
    out: dict = {}
    if df.is_empty():
        return out
    df = df.with_columns(year=pl.col("observed_at").dt.year())
    for (metric, year), grp in df.group_by(["metric", "year"], maintain_order=True):
        part_dir = RAW_DIR / f"metric={metric}"
        part_dir.mkdir(parents=True, exist_ok=True)
        path = part_dir / f"year={year}.parquet"
        # Atomic write: temp file in the same directory (same filesystem), then
        # os.replace — readers never observe a half-written partition.
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=str(path.parent))
        os.close(tmp_fd)
        try:
            grp.select(RAW_COLUMNS).sort(["entity_id", "observed_at"]).write_parquet(
                tmp_path, row_group_size=ROW_GROUP_SIZE
            )
            os.replace(tmp_path, str(path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        out[str(path.relative_to(PROJECT_ROOT))] = grp.height
    return out


def make_backup() -> Path:
    """Copy all parquet layers to data/backup/YYYY-MM-DD/ (skip if present)."""
    stamp = date.today().isoformat()
    bdir = BACKUP_ROOT / stamp
    if (bdir / "raw_observations").exists():
        return bdir                        # already backed up today; don't overwrite
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(RAW_DIR, bdir / "raw_observations")
    shutil.copytree(EM_DIR, bdir / "daily_entity_metrics")
    shutil.copytree(CM_DIR, bdir / "daily_category_metrics")
    shutil.copy2(SB_PATH, bdir / "seasonal_baselines.parquet")
    return bdir


def backup_size_mb(bdir: Path) -> float:
    return sum(p.stat().st_size for p in bdir.rglob("*") if p.is_file()) / 1e6


def restore_from_backup(bdir: Path) -> None:
    """Restore every layer from a backup dir; raises if a layer is missing."""
    for name, target in (
        ("raw_observations", RAW_DIR),
        ("daily_entity_metrics", EM_DIR),
        ("daily_category_metrics", CM_DIR),
    ):
        src = bdir / name
        if not src.exists():
            raise FileNotFoundError(f"backup missing {src}")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(src, target)
    sb = bdir / "seasonal_baselines.parquet"
    if sb.exists():
        shutil.copy2(sb, SB_PATH)


# ---------------------------------------------------------------------------
# Metrics rebuild + logging
# ---------------------------------------------------------------------------

def rebuild_metrics(last_data_date: date) -> tuple[float, float]:
    """Full rebuild via subprocess: baselines + entity metrics, then category.

    build_metrics.py and build_category_metrics.py are invoked with
    --today <last_data_date> so the observation calendar (entity layer) and the
    rollup cap (category layer) end at the newest observed date. check=True
    turns any build failure into CalledProcessError, which the caller maps to
    restore-from-backup + exit 1. Returns (entity_seconds, category_seconds).
    """
    t0 = time.perf_counter()
    subprocess.run(
        [sys.executable, "build_metrics.py", "--today", last_data_date.isoformat()],
        cwd=PROJECT_ROOT, check=True,
    )
    t1 = time.perf_counter()
    subprocess.run(
        [sys.executable, "build_category_metrics.py", "--today", last_data_date.isoformat()],
        cwd=PROJECT_ROOT, check=True,
    )
    t2 = time.perf_counter()
    return t1 - t0, t2 - t1


def append_log(text: str) -> None:
    UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not UPDATE_LOG.exists():
        UPDATE_LOG.write_text("# River Personality Monitor — Update Log\n\n", encoding="utf-8")
    with open(UPDATE_LOG, "a", encoding="utf-8") as fh:
        fh.write(text + "\n---\n")


# ---------------------------------------------------------------------------
# GCS upload (CLOUD mode only)
# ---------------------------------------------------------------------------

# CLOUD (GCS upload) contract
# ----------------------------
# When the env var GCS_BUCKET is set (Cloud Run job), after a SUCCESSFUL local
# update AND metrics rebuild we push the serving artifacts to
# `gs://<GCS_BUCKET>/` so the Dash service picks them up on its next cold
# start. When GCS_BUCKET is unset (local launchd mode) this whole path is
# dead code: nothing here is imported, google-cloud-storage is never loaded,
# and local behavior is byte-identical to before.
#
#   * Objects uploaded (blob name == repo-relative path, e.g. "stations.csv",
#     "data/raw_observations/metric=gage_height/year=2026.parquet"):
#       - data/raw_observations/            (all metric=*/year=*.parquet)
#       - data/daily_entity_metrics/        (all metric=*/year=*/data.parquet)
#       - data/daily_category_metrics/      (all metric=*/year=*/data.parquet)
#       - data/seasonal_baselines.parquet
#       - stations.csv
#       - data/UPDATE_LOG.md
#       - hydro_correlation/          (aligned_pairs.parquet, correlation_final.csv —
#                                      rebuilt from live sources before each upload)
#   * Each object is written in a single GCS upload, which is atomic per
#     object — an inconsistent generation is never visible to readers.
#   * Objects whose remote size AND md5 already match the local file are
#     skipped (blob.reload exists, so we compare both — not size-only).
#   * Uploads are parallelized across a small thread pool (default 8 workers)
#     because there are ~200+ partition files.
#   * If any object still fails after retries, we raise / exit non-zero so the
#     Cloud Run job is marked failed. Local data is already updated and safe;
#     GCS simply stays on its previous generation, which is safe.


def _md5_b64(path: Path) -> str:
    """Base64-encoded MD5 of a file's bytes — the form GCS reports in md5_hash."""
    import base64
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")


def _collect_serving_files() -> list[Path]:
    """All local files that make up the serving artifact set (relative paths)."""
    files: list[Path] = []
    if RAW_DIR.exists():
        files += [p for p in RAW_DIR.rglob("metric=*/year=*.parquet") if p.is_file()]
    if EM_DIR.exists():
        files += [p for p in EM_DIR.rglob("metric=*/year=*/data.parquet") if p.is_file()]
    if CM_DIR.exists():
        files += [p for p in CM_DIR.rglob("metric=*/year=*/data.parquet") if p.is_file()]
    for f in (SB_PATH, PROJECT_ROOT / "stations.csv", UPDATE_LOG):
        if f.is_file():
            files.append(f)
    # hydro_correlation/ serving files (rebuilt by build_hydro.py before the
    # upload). Blob names fall back to project-root-relative, so they land in
    # gs://<bucket>/hydro_correlation/ — exactly the prefix cloud_boot.py maps
    # back to ROOT.
    hydro_dir = PROJECT_ROOT / "hydro_correlation"
    for f in ("aligned_pairs.parquet", "correlation_final.csv"):
        p = hydro_dir / f
        if p.is_file():
            files.append(p)
    return files


def _upload_one(bucket, blob_name: str, local_path: Path,
                max_attempts: int = 3) -> tuple[str, str | None]:
    """Upload a single object; returns (status, error_or_None).

    status is "uploaded" or "skipped". On failure after retries, status is
    "failed" with the last error returned for the caller to raise.
    """
    blob = bucket.blob(blob_name)
    local_size = local_path.stat().st_size
    # Skip unchanged objects: same remote size AND same remote md5.
    if blob.exists():
        try:
            blob.reload()
            if blob.size == local_size and blob.md5_hash == _md5_b64(local_path):
                return "skipped", None
        except Exception:
            pass  # can't cheaply verify -> fall through and (re)upload
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            blob.upload_from_filename(str(local_path))
            return "uploaded", None
        except Exception as exc:  # noqa: BLE001 - surface all retryable failures
            last_err = exc
            if attempt < max_attempts:
                time.sleep(min(2 ** attempt, 8))
    return "failed", str(last_err) if last_err else "unknown error"


def upload_serving_artifacts(bucket_name: str, max_workers: int = 8,
                             max_attempts: int = 3) -> dict:
    """Upload all serving artifacts to `gs://<bucket_name>/` (cloud mode only).

    Raises a RuntimeError if any object remains failed after retries, so the
    caller can exit non-zero. Returns {"uploaded": n, "skipped": n}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    # Lazy import: only pulled in on the cloud path so local installs without
    # google-cloud-storage still work. Uses ADC (Application Default Credentials),
    # which works automatically on Cloud Run.
    from google.cloud import storage

    files = _collect_serving_files()
    if not files:
        print(f"  [gcs] no serving files found to upload for gs://{bucket_name}/", flush=True)
        return {"uploaded": 0, "skipped": 0}

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    def _blob_name(path: Path) -> str:
        # Bucket layout contract (cloud_boot.py + initial seed): bucket root is
        # the data dir — e.g. "raw_observations/metric=.../data.parquet",
        # "seasonal_baselines.parquet", "UPDATE_LOG.md". Non-data files like
        # stations.csv stay project-root relative.
        try:
            return str(path.relative_to(PROJECT_ROOT / "data"))
        except ValueError:
            return str(path.relative_to(PROJECT_ROOT))

    jobs = [(_blob_name(path), path) for path in files]
    uploaded = 0
    skipped = 0
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_upload_one, bucket, blob_name, local_path, max_attempts): blob_name
            for blob_name, local_path in jobs
        }
        for fut in as_completed(futures):
            blob_name = futures[fut]
            status, err = fut.result()
            if status == "uploaded":
                uploaded += 1
            elif status == "skipped":
                skipped += 1
            else:
                failures.append(f"{blob_name}: {err}")

    print(f"  [gcs] uploaded {uploaded} / skipped (unchanged) {skipped} "
          f"objects to gs://{bucket_name}/", flush=True)
    if failures:
        for f in failures[:10]:
            print(f"  [gcs] FAILED: {f}", flush=True)
        if len(failures) > 10:
            print(f"  [gcs] ... and {len(failures) - 10} more failed objects", flush=True)
        raise RuntimeError(f"GCS upload: {len(failures)} object(s) failed after "
                           f"{max_attempts} attempts")
    return {"uploaded": uploaded, "skipped": skipped}


def _rebuild_hydro_serving_files() -> bool:
    """Rebuild hydro_correlation/aligned_pairs.parquet + correlation_final.csv
    from LIVE sources via build_hydro.main() (no --dry-run, so it atomically
    rewrites the files in place).

    Returns True on success. FAILURE ISOLATION: any exception or non-zero
    return is logged as a WARNING and returns False — it never propagates, so
    a hydro rebuild problem can never fail the job nor the upload of the
    remaining artifacts (the USGS half must still succeed and upload on its
    own).
    """
    try:
        # build_hydro.main() parses sys.argv for its own flags; update_data's
        # argv (e.g. --since) would make argparse exit 2. Swap in a clean argv
        # for the duration of the call (we are single-threaded here) — same
        # rationale as rebuild_metrics running argv-parsing builders as
        # subprocesses.
        saved_argv = sys.argv
        sys.argv = ["build_hydro.py"]
        try:
            rc = build_hydro.main()
        finally:
            sys.argv = saved_argv
        if rc != 0:
            print(f"  WARNING: build_hydro.py exited {rc} — hydro serving files "
                  "NOT refreshed; continuing without them.", flush=True)
            return False
        print("  Hydro rebuild OK: aligned_pairs.parquet + correlation_final.csv "
              "refreshed.", flush=True)
        return True
    except SystemExit as exc:      # argparse/exit — treat as failure, keep going
        print(f"  WARNING: build_hydro.py aborted (exit {exc.code}) — hydro "
              "serving files NOT refreshed; continuing without them.", flush=True)
        return False
    except Exception as exc:
        print(f"  WARNING: hydro rebuild failed ({type(exc).__name__}: {exc}) — "
              "hydro serving files NOT refreshed; continuing without them.", flush=True)
        return False


def _upload_cloud_artifacts(bucket_name: str) -> bool:
    """Rebuild hydro serving files, then push ALL serving artifacts to GCS.

    Returns True on success; False only when the GCS upload itself failed
    after retries (caller then exits non-zero so the Cloud Run job is marked
    failed — local data is safe and GCS stays on the previous generation). A
    hydro rebuild failure NEVER fails the upload or the job: it logs a
    warning, the (previous) hydro files are still uploaded if present, and
    everything else uploads normally.
    """
    _rebuild_hydro_serving_files()
    print(f"\nCloud mode: uploading serving artifacts to gs://{bucket_name}/...", flush=True)
    try:
        upload_serving_artifacts(bucket_name)
        return True
    except Exception as exc:
        print(f"ERROR: GCS upload failed: {exc}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Entry point. Any unexpected failure before/outside the guarded write and
    rebuild blocks is caught here: nothing has been written yet, so there is
    nothing to restore — we just report cleanly and exit 1."""
    try:
        return _run()
    except KeyboardInterrupt:
        print("\nInterrupted — no changes were written.", flush=True)
        return 130
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ERROR: unexpected failure: {type(exc).__name__}: {exc}", flush=True)
        print("No changes were written before this point.", flush=True)
        import traceback
        traceback.print_exc()
        return 1


def _run() -> int:
    ap = argparse.ArgumentParser(
        description="Incremental data update: fetch new/revised USGS daily "
                    "values, upsert into raw_observations, rebuild all metrics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch + compare only; write nothing (no backup, no rebuild)")
    ap.add_argument("--since", metavar="YYYY-MM-DD",
                    help="fetch from this date (default: auto — max of the "
                         "revision window and 2 days before the last data date)")
    ap.add_argument("--revision-days", type=int, default=DEFAULT_REVISION_DAYS,
                    help="how far back to fetch for provisional->approved "
                         f"revisions (default {DEFAULT_REVISION_DAYS})")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                    help=f"max gauges per request (default {DEFAULT_BATCH_SIZE})")
    ap.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                    help=f"polite delay between requests (default {DEFAULT_SLEEP}s)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"page size (default {DEFAULT_LIMIT})")
    args = ap.parse_args()

    today = date.today()
    if args.since:
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            ap.error(f"--since must be YYYY-MM-DD, got {args.since!r}")

    stations = ing.read_stations()
    gauges = sorted(stations.keys())
    if not gauges:
        print("ERROR: no gauges found in stations.csv — aborting.", flush=True)
        return 1

    summary: list[str] = []

    # ---- a. Determine the update window -----------------------------------
    last_data_date = query_last_data_date()
    if args.since:
        since = date.fromisoformat(args.since)
    else:
        revision_start = today - timedelta(days=args.revision_days)
        straggler_start = (last_data_date - timedelta(days=2)) if last_data_date else (today - timedelta(days=2))
        since = min(revision_start, straggler_start)
    end = today
    if since > end:
        ap.error(f"--since {since} is after today ({end})")
    if last_data_date is None:
        print("WARNING: no existing raw_observations data; treating the whole "
              "window as new.", flush=True)

    summary += [
        "=== River Personality Monitor — Data Update ===",
        f"Last data date: {last_data_date or 'none'}",
        f"Fetching from USGS: {since} to {end} ({len(gauges)} gauges × {len(ing.PARAM_CODES)} metrics)",
    ]
    print("\n".join(summary), flush=True)

    # ---- b. Fetch from USGS (cache bypassed) ------------------------------
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    t_fetch = time.perf_counter()
    features, failed = fetch_all(gauges, since, end, session, args)
    dt_fetch = time.perf_counter() - t_fetch

    summary.append(f"Fetched: {len(features)} rows")
    print(f"Fetched: {len(features)} rows  [{dt_fetch:.0f}s]", flush=True)

    # If every request failed, the API is unreachable — never touch the data.
    if len(failed) >= len(gauges):
        print("ERROR: every USGS request failed — API appears unreachable. "
              "No changes made.", flush=True)
        return 1

    # ---- c. Upsert comparison (in memory) ----------------------------------
    fetched_df = (
        ing.transform_features(features, datetime.now(timezone.utc))
        if features else pl.DataFrame(schema=RAW_SCHEMA)
    )
    existing = load_existing(since, end)
    merged, counts = upsert_compare(existing, fetched_df)

    summary += [
        f"New rows: {counts['new']}",
        f"Updated rows: {counts['updated']} ({counts['provisional_to_approved']} provisional → approved)",
        f"Unchanged: {counts['unchanged']}",
        f"Errors: {len(failed)}",
    ]
    if failed:
        summary.append(f"  (failed gauges: {', '.join(failed)})")
    print("\n".join(summary[4:]), flush=True)   # rows 4.. are New/Updated/Unchanged/Errors

    now_str = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    # ---- d. Dry-run: stop here, write nothing ------------------------------
    if args.dry_run:
        summary += [
            "",
            "[dry-run] nothing written — no backup, no metrics rebuild. "
            "Run without --dry-run to apply.",
            f"Update complete (dry-run): {now_str}",
        ]
        print("\n".join(summary[-3:]), flush=True)
        return 0

    # ---- No-op: data already current ---------------------------------------
    if counts["new"] == 0 and counts["updated"] == 0:
        summary += [
            "",
            "Already up to date — no new or revised rows. Nothing written.",
            f"Update complete: {now_str}",
        ]
        print("\n".join(summary[-3:]), flush=True)
        append_log("\n".join(summary))
        # Cloud mode: STILL rebuild + upload hydro on no-op days. Live grid
        # sources (the independently refreshed EIA monthly file, the BPA
        # gridstatus extension) can move even when USGS didn't, and the /hydro
        # page should reflect the newest months/stats. Local mode (GCS_BUCKET
        # unset) returns here exactly as before: nothing written, no upload,
        # no hydro build.
        gcs_bucket = os.environ.get("GCS_BUCKET")
        if gcs_bucket:
            print("\nCloud mode: USGS was a no-op; rebuilding + uploading hydro "
                  "serving files only.", flush=True)
            if not _upload_cloud_artifacts(gcs_bucket):
                return 1
        return 0

    # ---- e. Backup before any change ---------------------------------------
    bdir = make_backup()
    backup_line = f"Backup: data/backup/{date.today().isoformat()}/ ({backup_size_mb(bdir):.1f} MB)"

    # ---- f. Upsert: rewrite affected year partitions ------------------------
    try:
        partitions = write_partitions(merged)
    except Exception as exc:
        print(f"ERROR: parquet write failed: {exc}", flush=True)
        try:
            restore_from_backup(bdir)
            print("Restored data from backup.", flush=True)
        except Exception as rexc:
            print(f"CRITICAL: restore failed: {rexc} — manual recovery needed.", flush=True)
            return 2
        return 1

    last_data_after = merged.select(pl.col("observed_at").max()).item()

    # ---- g. Full metrics rebuild ---------------------------------------------
    print("\nRebuilding metrics...", flush=True)
    try:
        t_em, t_cm = rebuild_metrics(last_data_after)
    except Exception as exc:
        print(f"ERROR: metrics rebuild failed: {exc}", flush=True)
        try:
            restore_from_backup(bdir)
            print("Restored data from backup.", flush=True)
        except Exception as rexc:
            print(f"CRITICAL: restore failed: {rexc} — manual recovery needed.", flush=True)
            return 2
        return 1

    # ---- h. Summary + log ----------------------------------------------------
    summary += [
        "",
        "Rebuilding metrics...",
        f"build_metrics: {t_em:.1f}s",
        f"build_category_metrics: {t_cm:.1f}s",
        "",
        backup_line,
        f"Update complete: {now_str}",
    ]
    print("\n".join(summary[-6:]), flush=True)
    if partitions:
        print(f"Rewrote {len(partitions)} partition(s): "
              + ", ".join(sorted(partitions)), flush=True)
    append_log("\n".join(summary))

    # ---- i. CLOUD: upload serving artifacts to GCS --------------------------
    # Only reached after a successful local update AND metrics rebuild (never
    # in dry-run, never on the no-op path, never after a failed rebuild). If
    # GCS_BUCKET is unset (local launchd mode) nothing happens here at all and
    # behavior stays byte-identical to before.
    gcs_bucket = os.environ.get("GCS_BUCKET")
    if gcs_bucket:
        print(f"\nCloud mode: rebuilding hydro_correlation from live sources...", flush=True)
        if not _upload_cloud_artifacts(gcs_bucket):
            # Local data is already updated + rebuilt and remains usable; GCS
            # stays on the previous generation (safe). Exit non-zero so the
            # Cloud Run job is marked failed. (A hydro rebuild failure by
            # itself never reaches here — see _rebuild_hydro_serving_files.)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
