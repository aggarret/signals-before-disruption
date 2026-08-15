"""Gated GridStatus enrichment for BPA hydro (Bonneville Power Administration).

Built by BUILDER B4 for the River Personality Monitor (/hydro page).

Purpose
-------
This module OPTIONALLY enriches the validated monthly EIA HYC hydro series with a
daily-resolution, per-Balancing-Authority BPA hydro series via the `gridstatus`
library. It is intentionally GATED so that it can NEVER block the /hydro page:

* If no ``GRIDSTATUS_TOKEN`` env var is set  -> fully degraded (returns None / False).
* If `gridstatus` cannot be imported (e.g. Python 3.10 vs its 3.11+ ``StrEnum``
  dependency) -> fully degraded (returns None / False).
* If the token IS set and import succeeds but a runtime fetch fails -> catches and
  returns None instead of crashing the page.

The gridstatus import is DELIBERATELY lazy (inside functions, behind the guard) so
that importing this module is always safe even when gridstatus is absent.

Reference
---------
See ``hydro_correlation/research/R5_proposal.md`` for the documented API surface.
gridstatus 0.36.0: ``EIA.get_grid_monitor(area_id="BPAT")`` (hourly, hydro column
``NG: WAT``) or ``EIA.get_dataset(dataset="electricity/rto/fuel-type-data",
facets={"respondent": ["BPAT"]})`` (hourly, ``Hydro`` column). Daily is a pandas
resample of the hourly series.

This module is consumed by BUILDER B3, which calls ``gridstatus_status_text()`` to
render the note and ``is_gridstatus_available()`` to decide whether to show a
(future) daily view. It does not modify app.py / pages/.
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

# Name of the BPA (Bonneville Power Administration) balancing authority.
_BPA_AREA_ID = "BPAT"

# Name of this feature's token. Presence of this env var is part of the gate.
_TOKEN_ENV = "GRIDSTATUS_TOKEN"


def _gridstatus_importable() -> bool:
    """Return True only if ``import gridstatus`` succeeds.

    Import is done lazily here (NOT at module top level) so this module imports
    cleanly even when gridstatus is absent or unimportable on this interpreter.
    Never raises.
    """
    try:
        import gridstatus  # noqa: F401  (lazy, guarded)
        return True
    except Exception:  # ImportError or anything else at import time
        return False


def is_gridstatus_available() -> bool:
    """Return True only when BOTH a token is set AND gridstatus imports.

    This is the single gate for all enrichment. Never raises.
    """
    try:
        token_set = bool(os.environ.get(_TOKEN_ENV))
    except Exception:
        token_set = False
    return token_set and _gridstatus_importable()


def get_bpa_daily_hydro() -> Optional[pd.DataFrame]:
    """Fetch BPA (BPAT) daily hydro via gridstatus, or return None when gated.

    Returns
    -------
    Optional[pd.DataFrame]
        A DataFrame with a daily hydro series when fully available; ``None`` when
        degraded (no token, gridstatus unimportable, or a runtime fetch failure).

    The fetch is ONLY attempted when ``is_gridstatus_available()`` is True. If it
    is not, this returns None immediately (no import, no network call). If it is
    available but the fetch fails at runtime, the exception is caught and None is
    returned so the page never crashes.
    """
    if not is_gridstatus_available():
        return None

    if not _fetch_is_run():
        # Guard hook: see note below. Defensive; should not normally trigger.
        return None

    try:
        # Lazy import -- only reached when the availability gate already passed,
        # so gridstatus is known importable here.
        from gridstatus.eia import EIA

        from gridstatus import __version__  # noqa: F401  (existence check)

        token = os.environ.get(_TOKEN_ENV)
        eia = EIA(api_key=token)

        # Option A (preferred): EIA-930 fuel mix for BPA via the Grid Monitor.
        # Hydro column is "NG: WAT" (water/hydro), hourly, indexed on a time col.
        bpa_hourly = eia.get_grid_monitor(area_id=_BPA_AREA_ID)

        # Option B fallback: per-BA fuel-type-data dataset with a "Hydro" column.
        if "NG: WAT" not in bpa_hourly.columns:
            bpa_hourly = eia.get_dataset(
                dataset="electricity/rto/fuel-type-data",
                frequency="hourly",
                facets={"respondent": [_BPA_AREA_ID]},
            )

        # Locate the time column and the hydro column after normalization.
        ts_col = _find_time_column(bpa_hourly)
        if ts_col is None:
            return None

        hydro_col = None
        if "NG: WAT" in bpa_hourly.columns:
            hydro_col = "NG: WAT"
        elif "Hydro" in bpa_hourly.columns:
            hydro_col = "Hydro"
        if hydro_col is None:
            return None

        series = (
            bpa_hourly.set_index(ts_col)[hydro_col]
            .astype(float)
            .resample("1D")
            .mean()
        )
        series.name = "bpa_hydro_mw_daily"
        return series.dropna().to_frame()

    except Exception:
        # Runtime fetch failure (network, auth, schema drift, gremlins) — degrade
        # gracefully rather than break the /hydro page.
        return None


def _find_time_column(df: pd.DataFrame) -> Optional[str]:
    """Best-effort location of a time-ish column in a gridstatus frame."""
    for candidate in ("Interval Start", "Time", "Interval End", "timestamp"):
        if candidate in df.columns:
            return candidate
    # Fall back to any datetime64 dtype column.
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    return None


def _fetch_is_run() -> bool:
    """Defensive runtime-permission hook.

    Always True in the normal, local-only flow. Kept as a separate seam so a
    future periodic-ingest path (not the page request path) could be gated
    independently without changing the public API. Never raises.
    """
    return True


def gridstatus_status_text() -> str:
    """Return a user-facing note describing the current enrichment state."""
    if is_gridstatus_available():
        return (
            "Daily-resolution BPA hydro enabled (GridStatus token detected)."
        )
    return (
        "Daily-resolution BPA hydro (Bonneville Power Administration) is "
        "available with a GridStatus API token — currently showing monthly EIA data."
    )
