"""cloud_boot.py — GCS data sync at container startup (Cloud Run).

The container image does NOT bake in data/. On startup (MODE=service) the
entrypoint runs this module to fetch the parquet/CSV dataset from Cloud
Storage into ``./data/`` using Application Default Credentials (the Cloud Run
service account). A separate Cloud Run *job* (running update_data.py, which
writes back to GCS) is the source of truth; this boot only mirrors the latest
published blobs so the serving container shows fresh data.

Design goals
  * Idempotent: re-running is safe; already-fetched blobs with a matching
    size are skipped (size is a cheap, reliable freshness gate for the
    once-per-boost startup sync).
  * Never crashes the boot: if GCS is unreachable but local ``./data/``
    already exists, warn and continue so the app still starts.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, List, Tuple

from google.cloud import storage

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")

# Directory prefixes to mirror (hive-partitioned: metric=*/year=*). Synced
# into ./data/<prefix>.
PREFIXES: List[str] = [
    "raw_observations/",
    "daily_entity_metrics/",
    "daily_category_metrics/",
]

# Top-level single blobs to sync into ./data/.
BLOBS: List[str] = [
    "seasonal_baselines.parquet",
    "stations.csv",
    "UPDATE_LOG.md",
]


def _log(msg: str) -> None:
    print(f"cloud_boot: {msg}", flush=True)


def _blob_destination(blob_name: str) -> str:
    """Map a GCS blob name to the local path under ./data/.

    Prefixes map 1:1 (``raw_observations/...`` -> ``data/raw_observations/...``);
    top-level blobs (``seasonal_baselines.parquet``, ``stations.csv``,
    ``UPDATE_LOG.md``) land directly in ``data/``.
    """
    for prefix in PREFIXES:
        if blob_name.startswith(prefix):
            return os.path.join(DATA_DIR, blob_name)
    return os.path.join(DATA_DIR, os.path.basename(blob_name))


def _iter_blob_names(bucket: storage.Bucket) -> Iterable[str]:
    """Yield all relevant blob names in the GCS bucket."""
    for prefix in PREFIXES:
        for blob in bucket.list_blobs(prefix=prefix):
            if not blob.name.endswith("/"):  # skip pseudo-directories
                yield blob.name
    for name in BLOBS:
        yield name


def _fetch_blob(bucket: storage.Bucket, blob_name: str) -> bool:
    """Download one blob if changed; returns True when (re)downloaded."""
    dest = _blob_destination(blob_name)
    remote_size = bucket.blob(blob_name).size  # may be None for empty blobs

    # Idempotent skip: existing local file with the same byte size is assumed
    # to be the same published blob. (Sizes are authoritative from the bucket;
    # a size mismatch triggers a fresh download.)
    if os.path.isfile(dest) and remote_size is not None:
        try:
            if os.path.getsize(dest) == remote_size:
                _log(f"skip (same size) {blob_name}")
                return False
        except OSError:
            pass  # stat failed — just re-download

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    bucket.blob(blob_name).download_to_filename(dest)
    _log(f"downloaded {blob_name} -> {dest} (size={remote_size})")
    return True


def sync_from_gcs(bucket_name: str) -> int:
    """Mirror all dataset blobs from ``gs://<bucket_name>`` into ./data/.

    Returns the number of blobs (re)downloaded. Raises on auth/transport
    errors so the caller can decide whether local data is sufficient.
    """
    client = storage.Client()  # ADC default credentials
    bucket = client.bucket(bucket_name)

    os.makedirs(DATA_DIR, exist_ok=True)
    blobs = list(_iter_blob_names(bucket))
    _log(f"found {len(blobs)} blob(s) in gs://{bucket_name}")
    count = 0
    for blob_name in blobs:
        try:
            if _fetch_blob(bucket, blob_name):
                count += 1
        except Exception as exc:
            _log(f"WARNING: failed to fetch {blob_name}: {exc!r}")
            _log(f"continuing with remaining blobs; local data may be stale for {blob_name}")
    return count


def main() -> int:
    bucket_name = os.environ.get("GCS_BUCKET")
    if not bucket_name:
        _log("GCS_BUCKET not set — skipping GCS sync (using baked/local data)")
        return 0

    _log(f"GCS_BUCKET={bucket_name} — syncing dataset into {DATA_DIR}")
    try:
        downloaded = sync_from_gcs(bucket_name)
        _log(f"GCS sync complete: {downloaded} blob(s) downloaded")
        return 0
    except Exception as exc:
        # Never crash the boot: if we already have local data, warn and
        # continue so the app can still start and serve it.
        if os.path.isdir(DATA_DIR) and any(
            f for f in os.listdir(DATA_DIR) if not f.startswith(".")
        ):
            _log(
                f"ERROR: GCS sync failed but local {DATA_DIR} exists — "
                f"continuing with local data: {exc!r}"
            )
            return 0
        _log(f"FATAL: GCS sync failed and no local data present: {exc!r}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
