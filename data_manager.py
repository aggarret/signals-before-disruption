"""data_manager.py — Stale-data safety net for River Personality Monitor.

When the app starts and the served dataset has not been updated since the
staleness cutoff set by ``UPDATE_HOUR``/``UPDATE_MINUTE`` (currently 07:00), this
module kicks off a background refresh: a daemon thread runs ``update_data.py`` as a subprocess so
the app keeps serving the *old* data for the whole duration of the update — no
callback is ever blocked and no partial data is ever visible. Only after a
successful update are the module-level query caches in ``queries.py``
invalidated, so the very next callback reads the freshly written parquet.

This is a safety net, not the scheduler: the actual scheduler is the launchd
plist (currently 14:00 and currently NOT loaded). ``UPDATE_HOUR``/``UPDATE_MINUTE``
only define the staleness cutoff. This exists for the cases where the app comes
up after the cutoff but the job hasn't run yet (or failed, or isn't loaded) —
the app then refreshes itself in the background.

Guarantees
  * Never blocks: every public function returns immediately.
  * Never raises: every public function swallows its own errors and logs them.
  * At most one update at a time: a process-local ``threading.Lock`` plus a
    cross-process ``flock`` on ``data/.update.lock``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from typing import Optional

import queries  # noqa: F401  (used by invalidate_caches / _CACHE_LOCK)

try:  # POSIX (macOS/Linux); non-POSIX falls back to in-process locking only
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
UPDATE_LOG_PATH = os.path.join(DATA_DIR, "UPDATE_LOG.md")
METRICS_DIR = os.path.join(DATA_DIR, "daily_entity_metrics")
AUTO_UPDATE_LOG = os.path.join(DATA_DIR, "auto-update.log")
UPDATE_LOCK_FILE = os.path.join(DATA_DIR, ".update.lock")

# Scheduled (launchd) update time each day, local time.
UPDATE_HOUR = 7
UPDATE_MINUTE = 0

_UPDATE_LOCK = threading.Lock()  # process-local: at most one in-flight update


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    """Append a timestamped line to ``data/auto-update.log``; never raises."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(AUTO_UPDATE_LOG, "a", encoding="utf-8") as fh:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            fh.write(f"[{stamp}] {msg}\n")
    except Exception as exc:
        print(f"data_manager: cannot write {AUTO_UPDATE_LOG}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------
def _today_update_cutoff() -> float:
    """Epoch seconds of today's scheduled update time (07:00 local)."""
    now = datetime.now().astimezone()
    return (
        now.replace(hour=UPDATE_HOUR, minute=UPDATE_MINUTE, second=0, microsecond=0)
        .timestamp()
    )


def _last_update_mtime() -> Optional[float]:
    """Epoch mtime of the last completed data update, or None if unknown.

    ``data/UPDATE_LOG.md`` is appended only by *successful* runs of
    ``update_data.py`` (both the "already up to date" and the "updated" exit
    paths), so its mtime is the most faithful "last successful update"
    timestamp. If it is missing (pre-update-log installs), fall back to the
    newest metrics parquet file, whose mtime directly reflects when the
    dataset was last written.
    """
    if os.path.isfile(UPDATE_LOG_PATH):
        return os.path.getmtime(UPDATE_LOG_PATH)
    latest: Optional[float] = None
    if os.path.isdir(METRICS_DIR):
        for metric_dir in os.listdir(METRICS_DIR):
            metric_path = os.path.join(METRICS_DIR, metric_dir)
            if not (metric_dir.startswith("metric=") and os.path.isdir(metric_path)):
                continue
            for year_dir in os.listdir(metric_path):
                year_path = os.path.join(metric_path, year_dir)
                if not (year_dir.startswith("year=") and os.path.isdir(year_path)):
                    continue
                fp = os.path.join(year_path, "data.parquet")
                if os.path.isfile(fp):
                    mt = os.path.getmtime(fp)
                    if latest is None or mt > latest:
                        latest = mt
    return latest


def is_data_stale() -> bool:
    """True when the dataset was last updated before today's 07:00 local time.

    On any error (missing files, unreadable stat, ...) it conservatively
    returns True so the safety net still fires — a background update is
    idempotent and safe to run.
    """
    try:
        last = _last_update_mtime()
    except Exception as exc:
        _log(f"is_data_stale: error ({exc!r}) — treating data as stale")
        return True
    if last is None:
        _log(
            "is_data_stale: no update log and no metrics parquet found — "
            "treating data as stale"
        )
        return True
    stale = last < _today_update_cutoff()
    _log(
        "is_data_stale: last update "
        f"{datetime.fromtimestamp(last).astimezone().isoformat(timespec='seconds')} "
        f"— {'STALE' if stale else 'fresh'}"
    )
    return stale


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------
def invalidate_caches() -> None:
    """Drop every computed-result cache in ``queries.py``.

    DuckDB reads parquet per query (it does not keep the parquet files open
    between queries), so the existing thread-local connections see the newly
    written files on their next ``read_parquet()``. The only thing that must
    go are the in-memory caches holding results computed from the *old* data.
    Runs under ``queries._CACHE_LOCK`` to stay safe against concurrent
    callbacks mutating the LRU slice cache.
    """
    try:
        with queries._CACHE_LOCK:
            queries._STATS_CACHE.clear()
            queries._HIST_MAX_CACHE.clear()
            queries._SLICE_CACHE.clear()
        _log("invalidate_caches: cleared _STATS_CACHE, _HIST_MAX_CACHE, _SLICE_CACHE")
    except Exception as exc:
        _log(f"invalidate_caches: failed ({exc!r})")


# ---------------------------------------------------------------------------
# Background update
# ---------------------------------------------------------------------------
def _acquire_update_flock() -> Optional[object]:
    """Try to take the cross-process update lock.

    Returns an open file handle when the lock was acquired, or None when
    another process currently holds it (e.g. the launchd job). On
    platforms without ``fcntl`` returns a sentinel meaning "acquired" so the
    in-process lock still applies.
    """
    if fcntl is None:
        return object()
    try:
        fh = open(UPDATE_LOCK_FILE, "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except (BlockingIOError, OSError):
        try:
            fh.close()
        except Exception:
            pass
        return None


def _run_update(lock: threading.Lock) -> None:
    """Daemon-thread body: run ``update_data.py``, invalidate caches on success."""
    lock_file = None
    try:
        lock_file = _acquire_update_flock()
        if lock_file is None:
            _log("auto-update: another update process is already running — skipping")
            return

        _log("auto-update: starting update_data.py subprocess")
        proc = subprocess.Popen(
            [sys.executable, "update_data.py"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            out, err = proc.communicate(timeout=600)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            try:
                proc.communicate()
            except Exception:
                pass
            partial_out = (exc.output or "").strip()
            partial_err = (exc.stderr or "").strip()
            _log(
                "auto-update: TIMED OUT (rc=-1) — killed stuck update"
                f"\n--- stdout ---\n{partial_out}"
                f"\n--- stderr ---\n{partial_err}"
            )
            return
        out = (out or "").strip()
        err = (err or "").strip()

        if proc.returncode == 0:
            _log(f"auto-update: SUCCESS (rc=0)\n--- stdout ---\n{out}")
            if err:
                _log(f"auto-update: stderr (non-fatal):\n{err}")
            invalidate_caches()
            _log("auto-update: caches invalidated — app now serving fresh data")
        else:
            _log(
                f"auto-update: FAILED (rc={proc.returncode})"
                f"\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
            )
    except Exception as exc:
        _log(f"auto-update: unexpected error ({exc!r})")
    finally:
        if lock_file is not None and fcntl is not None:
            try:
                lock_file.close()  # closes fd -> flock released
            except Exception:
                pass
        lock.release()


def trigger_background_update() -> Optional[threading.Thread]:
    """Spawn a daemon thread that runs ``update_data.py`` in a subprocess.

    Never blocks. Returns the started thread, or None if an update is already
    in flight (the process-local lock guarantees at most one concurrent
    update from this process).
    """
    if not _UPDATE_LOCK.acquire(blocking=False):
        _log("trigger_background_update: an update is already in flight — skipping")
        return None
    thread = threading.Thread(
        target=_run_update,
        args=(_UPDATE_LOCK,),
        name="auto-data-update",
        daemon=True,
    )
    thread.start()
    _log("trigger_background_update: background update thread started")
    return thread


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------
def ensure_fresh_data() -> Optional[threading.Thread]:
    """App-startup hook: trigger a background update only if data is stale.

    Never blocks, never raises. Returns the update thread, or None.
    """
    # Cloud Run: a separate Cloud Run job handles updates; the serving
    # container must NOT run its own background update thread.
    if os.environ.get("GCS_BUCKET"):
        return None
    try:
        if is_data_stale():
            _log("ensure_fresh_data: data is stale — triggering background update")
            return trigger_background_update()
        return None
    except Exception as exc:
        _log(f"ensure_fresh_data: unexpected error ({exc!r})")
        return None


if __name__ == "__main__":
    # Debug entry: report staleness and fire the background update if stale.
    print(f"is_data_stale: {is_data_stale()}")
    print(f"trigger_background_update: {trigger_background_update()}")