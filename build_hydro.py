#!/usr/bin/env python3
"""build_hydro.py — Rebuild hydro_correlation serving files from LIVE sources.

Rebuilds ``aligned_pairs.parquet`` (monthly streamflow × hydro generation for
52 gauges) and ``correlation_final.csv`` (spearman/pearson/tier/lag) from
current data sources, then optionally extends the generation series with the
freshest BPA daily hydro data (via the gridstatus library).

Intended to be wired into the daily 7am job (update_data.py's pipeline) so
the /hydro page always reflects the newest available months and stats.

LIVE sources (no baked-in files for streamflow):
  - Streamflow: ``daily_entity_metrics`` (metric='streamflow'), the SAME source
    the dashboard queries use. Monthly mean = mean of daily *average* (cfs)
    per (entity_id, year-month).
  - Generation base: ``eia_hydro_monthly_clean.parquet`` (EIA HYC state-level
    hydro, refreshed independently). Joined via ``gauge_location.csv``.
  - Generation extension: ``hydro_gridstatus.get_bpa_daily_hydro()`` — when
    GRIDSTATUS_TOKEN is set, fetches BPA (BPAT) daily hydro MW from the
    gridstatus library, converts to monthly thousand MWh, and appends to
    BPA-footprint gauges (WA/OR/ID/MT) for months beyond the last
    EIA-published month.
  - Gauge metadata: ``gauge_location.csv``, ``gauge_geo.csv``,
    ``correlation_final.csv`` (existing — used to seed the TIGHT-tier set).

Graceful degradation:
  - If gridstatus / token is unavailable: streamflow + EIA-only generation
    (no extension beyond the last EIA month). A warning is logged; the
    parquet is still valid and matches the current schema.
  - If any input file is missing: explicit error with a clear message.
  - Atomic write: temp file + os.replace for both outputs.

Usage:
    python3 build_hydro.py                     # rebuild + extend
    python3 build_hydro.py --dry-run            # build + print summary, write
                                                # nothing
    python3 build_hydro.py --no-extend          # skip BPA extension
    python3 build_hydro.py --dry-run --no-extend

Design choice: TIGHT-tier gauge set is loaded from the *existing*
``correlation_final.csv`` (tier == 'tight') rather than re-derived. This
preserves the stable 14-gauge set that the /hydro page's map and strip panels
depend on. The correlation stats are still recomputed for all 52 gauges; the
new correlation_final.csv may shift tiers slightly if new months change the
statistics, which is the intended behavior of "rebuild from live sources."

Design choice: The BPA generation extension (when available) is applied ONLY to
BPA-footprint gauges — those whose eia_location is in _BPA_STATES = {WA, OR, ID,
MT} (the core BPAT balancing-area states). All other gauges' generation series
ends at the last EIA-published month, exactly as the current file does (2004-01
.. 2026-05). Extending ALL gauges with BPA-scale values would inject extreme
outliers into small-state series (e.g. CT hydro ~30 thousand MWh vs BPA ~7,000)
and corrupt the pearson-based significance gate that tiers depend on.

``_EXTEND_ALL_GAUGES`` exists as an explicit opt-in for maintainers who want the
uniform extension anyway; it defaults to False for the reason above.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl
from scipy import stats

# ---------------------------------------------------------------------------
# Module root — always resolve relative to this file, so CWD never matters.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_DATA_DIR = _PROJECT_ROOT / "data"
_HYDRO_DIR = _PROJECT_ROOT / "hydro_correlation"

_EM_GLOB = str(_DATA_DIR / "daily_entity_metrics" / "metric=*" / "year=*" / "data.parquet")
_EIA_CLEAN = _HYDRO_DIR / "eia_hydro_monthly_clean.parquet"
_GAUGE_LOC = _HYDRO_DIR / "gauge_location.csv"
_GAUGE_GEO = _HYDRO_DIR / "gauge_geo.csv"
_CORR_FINAL = _HYDRO_DIR / "correlation_final.csv"
_ALIGNED_OUT = _HYDRO_DIR / "aligned_pairs.parquet"
_CORR_RAW_OUT = _HYDRO_DIR / "correlation_raw.csv"
_CORR_FINAL_OUT = _HYDRO_DIR / "correlation_final.csv"
_SB_PATH = _DATA_DIR / "seasonal_baselines.parquet"

# ---------------------------------------------------------------------------
# BPA footprint: states whose EIA location is within the BPAT balancing area.
# Gauges in these states get BPA generation extension. This is a simplifying
# assumption — BPAT (Bonneville Power Administration) covers WA, OR, ID, MT
# plus parts of other states. The list is kept conservative (core states only).
# ---------------------------------------------------------------------------
_BPA_STATES: set[str] = {"WA", "OR", "ID", "MT"}

# Default: extend ONLY BPA-footprint gauges (see docstring). Set to True to
# apply the BPA extension uniformly to every gauge.
_EXTEND_ALL_GAUGES: bool = False


# ===================================================================
#  1. Load gauge universe and TIGHT set
# ===================================================================

def load_gauge_metadata() -> tuple[pl.DataFrame, list[str]]:
    """Load gauge_location.csv and correlation_final.csv.

    Returns
    -------
    (loc_df, tight_ids)
        loc_df: all 52 gauges with entity_id, eia_location, confidence.
        tight_ids: list of TIGHT-tier entity_ids (from correlation_final).
    """
    loc = pl.read_csv(_GAUGE_LOC)
    tight_ids: list[str] = []
    if _CORR_FINAL.exists():
        corr = pl.read_csv(_CORR_FINAL)
        tight_ids = (
            corr.filter(pl.col("tier") == "tight")
            .select("entity_id")
            .to_series()
            .to_list()
        )
    else:
        warnings.warn(
            f"{_CORR_FINAL} not found — no TIGHT-tier seed. "
            "All 52 gauges will be rebuilt; tiers will be recomputed from scratch."
        )
    return loc, tight_ids


# ===================================================================
#  2. Build monthly streamflow from daily_entity_metrics (LIVE)
# ===================================================================

def build_streamflow_monthly(entity_ids: list[str] | None = None) -> pl.DataFrame:
    """Monthly mean streamflow (cfs) per gauge from daily_entity_metrics.

    Derives the mean of daily *average* (cfs) per (entity_id, YYYY-MM),
    matching the exact semantics of the existing ``gauge_streamflow_monthly``
    and ``aligned_pairs.parquet``.

    Parameters
    ----------
    entity_ids : list[str] | None
        If given, restrict to these entity_ids. Otherwise all available.

    Returns
    -------
    pl.DataFrame
        Columns: entity_id, period, mean_flow_cfs (Float64), n_days (UInt32).
        Sorted by (entity_id, period).
    """
    lf = pl.scan_parquet(_EM_GLOB)
    lf = lf.filter(pl.col("metric") == "streamflow")
    if entity_ids:
        lf = lf.filter(pl.col("entity_id").is_in(entity_ids))
    df = (
        lf.select(["entity_id", "observed_at", "average"])
        .with_columns(pl.col("observed_at").dt.strftime("%Y-%m").alias("period"))
        .group_by(["entity_id", "period"])
        .agg(
            pl.mean("average").alias("mean_flow_cfs"),
            pl.len().alias("n_days"),
        )
        .sort(["entity_id", "period"])
        .collect()
    )
    return df


# ===================================================================
#  3. Build generation series from EIA + BPA extension
# ===================================================================

def build_generation_base() -> pl.DataFrame:
    """Monthly hydro generation (thousand MWh) per gauge from EIA.

    Joins ``eia_hydro_monthly_clean.parquet`` with ``gauge_location.csv`` on
    eia_location, producing per-gauge monthly generation. Null generation
    values are dropped (matching the existing aligned_pairs semantics).

    Returns
    -------
    pl.DataFrame
        Columns: entity_id, period, generation_thousand_mwh (Float64).
    """
    eia = pl.read_parquet(_EIA_CLEAN)
    loc = pl.read_csv(_GAUGE_LOC)
    gen = eia.join(
        loc.select(["entity_id", "eia_location"]),
        left_on="location",
        right_on="eia_location",
        how="inner",
    )
    gen = gen.select(["entity_id", "period", "generation_thousand_mwh"]).drop_nulls()
    return gen.sort(["entity_id", "period"])


def get_bpa_monthly_from_gridstatus() -> pl.DataFrame | None:
    """Fetch BPA daily hydro via gridstatus, resample to monthly thousand MWh.

    Returns
    -------
    pl.DataFrame | None
        Columns: period (str, YYYY-MM), generation_thousand_mwh (Float64).
        None when gridstatus is unavailable or the fetch fails.
    """
    try:
        from hydro_gridstatus import get_bpa_daily_hydro
    except ImportError:
        return None

    daily = get_bpa_daily_hydro()  # Optional[pd.DataFrame]; may return None
    if daily is None or daily.empty:
        return None

    # daily DataFrame: index = datetime (tz-aware, named e.g. 'Interval Start'),
    # column = 'bpa_hydro_mw_daily' (mean MW). gridstatus's get_grid_monitor
    # fetches ALL available hourly data, which includes the IN-PROGRESS current
    # month — a partial month must never be treated as a monthly total.
    grp = daily["bpa_hydro_mw_daily"].resample("ME")  # month-end frequency
    monthly = grp.sum().mul(24.0).div(1000.0)  # Σ(daily mean MW) × 24h / 1000
    n_days = grp.count()

    out = pd.DataFrame({
        "period": monthly.index.strftime("%Y-%m"),
        "generation_thousand_mwh": monthly.to_numpy(),
        "n_days": n_days.to_numpy(),
    })
    # Completeness gate: keep only months with ~full daily coverage (≥25 days).
    # This drops the partial current month and any month with big data gaps.
    out = out[out["n_days"] >= 25].drop(columns=["n_days"])
    out = out[out["generation_thousand_mwh"].notna()]
    if out.empty:
        return None
    return pl.from_pandas(out.reset_index(drop=True))


def extend_generation(
    gen_base: pl.DataFrame,
    bpa_monthly: pl.DataFrame | None,
    target_ids: list[str],
) -> pl.DataFrame:
    """Extend the per-gauge generation series with BPA monthly values.

    For each target gauge, months beyond the last EIA-published month are
    filled with the BPA monthly generation (if available). Non-target gauges
    are left untouched — their series ends at the last EIA month, exactly like
    the current aligned_pairs.parquet.

    Parameters
    ----------
    gen_base : pl.DataFrame
        Per-gauge EIA generation (entity_id, period, generation_thousand_mwh).
    bpa_monthly : pl.DataFrame | None
        BPA monthly generation (period, generation_thousand_mwh), or None.
    target_ids : list[str]
        Gauge entity_ids to extend (BPA footprint, or all when
        _EXTEND_ALL_GAUGES is set).

    Returns
    -------
    pl.DataFrame
        Extended per-gauge generation, sorted by (entity_id, period).
    """
    if bpa_monthly is None or bpa_monthly.is_empty():
        return gen_base

    # BPA months to append: those after the global last EIA month.
    global_last = gen_base.select("period").max().item()
    bpa_new = bpa_monthly.filter(pl.col("period") > global_last)
    if bpa_new.is_empty():
        return gen_base

    # Build extension rows: cross-join targets × new BPA months.
    ext_rows = []
    for eid in target_ids:
        for row in bpa_new.to_dicts():
            ext_rows.append({
                "entity_id": eid,
                "period": row["period"],
                "generation_thousand_mwh": row["generation_thousand_mwh"],
            })
    if not ext_rows:
        return gen_base

    ext_df = pl.DataFrame(ext_rows, schema=gen_base.schema)
    # Deduplicate: if a month already exists in gen_base (e.g. EIA just caught
    # up), keep the EIA version.
    ext_df = ext_df.join(
        gen_base.select(["entity_id", "period"]),
        on=["entity_id", "period"],
        how="anti",
    )
    result = pl.concat([gen_base, ext_df], how="vertical").sort(
        ["entity_id", "period"]
    )
    return result


# ===================================================================
#  4. Join streamflow × generation into aligned_pairs schema
# ===================================================================

def build_aligned_pairs(
    flow: pl.DataFrame,
    gen: pl.DataFrame,
    loc: pl.DataFrame,
) -> pl.DataFrame:
    """Inner-join streamflow and generation on (entity_id, period).

    Adds the ``confidence`` column from gauge_location.csv.

    Returns
    -------
    pl.DataFrame
        Columns: entity_id, period, mean_flow_cfs, generation_thousand_mwh,
        confidence. Sorted by (entity_id, period). This is the exact schema
        expected by hydro_queries.py / aligned_pairs.parquet.
    """
    pairs = flow.join(gen, on=["entity_id", "period"], how="inner").select([
        "entity_id", "period", "mean_flow_cfs", "generation_thousand_mwh",
    ])
    conf = dict(zip(loc["entity_id"], loc["confidence"]))
    pairs = pairs.with_columns(
        pl.col("entity_id").replace_strict(conf).alias("confidence")
    )
    return pairs.sort(["entity_id", "period"])


# ===================================================================
#  5. Recompute correlation stats (fully automated)
# ===================================================================

def compute_correlation_stats(
    pairs: pl.DataFrame,
    loc: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Recompute correlation stats exactly matching run_correlation.py +
    _final_analysis.py.

    Produces two tables:
      1. correlation_raw.csv — per-gauge correlation coefficients
         (spearman, pearson, on raw and anomaly, with p-values + best_lag)
      2. correlation_final.csv — derived tiers, significance, decoupled flags

    The anomaly flow computation uses seasonal_baselines.parquet (streamflow
    mu/sigma per calendar day, aggregated to monthly). The anomaly gen
    computation uses per-gauge z-score (centered by gauge's own mean/std).

    This is fully automated — no manual flags needed. Every stat in the
    current correlation_final.csv is recomputed here.

    Returns
    -------
    (corr_raw_df, corr_final_df)
        Both as Polars DataFrames, matching the CSV schemas.
    """
    # Convert to pandas for the scipy-based logic (matching original code).
    pdf = pairs.to_pandas()
    pdf["period"] = pdf["period"].astype(str)
    pdf["year"] = pdf["period"].str[:4].astype(int)
    pdf["month"] = pdf["period"].str[5:7].astype(int)

    # ---- Anomaly flow (seasonal climatology from seasonal_baselines) ----
    base = pl.read_parquet(_SB_PATH)
    base = base.filter(pl.col("metric") == "streamflow").to_pandas()
    # day_of_year → month mapping
    ref = datetime(2001, 1, 1)  # non-leap year
    doy_to_month = {}
    for doy in range(1, 367):
        doy_to_month[doy] = (ref + timedelta(days=doy - 1)).month
    base["month"] = base["day_of_year"].map(doy_to_month)
    monthly_base = base.groupby(["entity_id", "month"], as_index=False).agg(
        mu_month=("mu", "mean"), sigma_month=("sigma", "mean")
    )

    df = pdf.merge(monthly_base, on=["entity_id", "month"], how="left")
    valid_sigma = df["sigma_month"].fillna(0) > 1e-9
    df["anomaly_flow"] = np.where(
        valid_sigma & df["sigma_month"].notna(),
        (df["mean_flow_cfs"] - df["mu_month"]) / df["sigma_month"].replace(0, np.nan),
        np.nan,
    )

    # ---- Anomaly gen (simple per-gauge z-score) ----
    g_stats = df.groupby("entity_id")["generation_thousand_mwh"].agg(["mean", "std"])
    g_stats.columns = ["g_mean", "g_std"]
    df = df.merge(g_stats, left_on="entity_id", right_index=True, how="left")
    df["anomaly_gen"] = np.where(
        df["g_std"].fillna(0) > 1e-9,
        (df["generation_thousand_mwh"] - df["g_mean"]) / df["g_std"].replace(0, np.nan),
        np.nan,
    )

    # ---- Per-gauge correlation ----
    eia_map = dict(zip(loc["entity_id"], loc["eia_location"]))
    results = []
    entities = sorted(df["entity_id"].unique())
    for ent in entities:
        sub = df[df["entity_id"] == ent].dropna(
            subset=["mean_flow_cfs", "generation_thousand_mwh"]
        )
        n_months = len(sub)
        if n_months < 5:
            results.append({
                "entity_id": ent, "n_months": n_months,
                "eia_location": eia_map.get(ent, ""),
                "confidence": conf_of(ent, loc),
            })
            continue

        # Raw
        pr_raw = stats.pearsonr(sub["mean_flow_cfs"], sub["generation_thousand_mwh"])
        sr_raw = stats.spearmanr(sub["mean_flow_cfs"], sub["generation_thousand_mwh"])

        # Anomaly
        anom = sub.dropna(subset=["anomaly_flow", "anomaly_gen"])
        n_anom = len(anom)
        if n_anom >= 5:
            pr_an = stats.pearsonr(anom["anomaly_flow"], anom["anomaly_gen"])
            sr_an = stats.spearmanr(anom["anomaly_flow"], anom["anomaly_gen"])
        else:
            pr_an = (np.nan, np.nan)
            sr_an = (np.nan, np.nan)

        # Lag scan on anomaly: flow at t vs gen at t+lag (flow leads by lag)
        best_lag: int | float = 0
        best_corr = sr_an[0] if not np.isnan(sr_an[0]) else 0.0
        avail_lags = []
        for lag in [0, 1, 2]:
            a = anom.sort_values(["year", "month"]).reset_index(drop=True)
            flow_a = a["anomaly_flow"]
            gen_shift = a["anomaly_gen"].shift(-lag)
            m = pd.concat([flow_a, gen_shift], axis=1).dropna()
            if len(m) >= 5:
                c = stats.spearmanr(m.iloc[:, 0], m.iloc[:, 1])[0]
                avail_lags.append((lag, c))
                if abs(c) > abs(best_corr):
                    best_corr = c
                    best_lag = lag
        if not avail_lags:
            best_lag = np.nan
            best_corr = np.nan

        results.append({
            "entity_id": ent,
            "eia_location": eia_map.get(ent, ""),
            "confidence": conf_of(ent, loc),
            "n_months": n_months,
            "pearson_raw": pr_raw[0],
            "pearson_raw_p": pr_raw[1],
            "spearman_raw": sr_raw[0],
            "spearman_raw_p": sr_raw[1],
            "pearson_anom": pr_an[0],
            "pearson_anom_p": pr_an[1],
            "spearman_anom": sr_an[0],
            "spearman_anom_p": sr_an[1],
            "best_lag": best_lag,
            "best_lag_corr": best_corr,
        })

    corr_raw = pl.from_pandas(
        pd.DataFrame(results).sort_values("entity_id").reset_index(drop=True)
    )

    # ---- Derive tiers (matching _final_analysis.py) ----
    corr_raw_pd = corr_raw.to_pandas()
    corr_raw_pd["significant"] = (
        corr_raw_pd["pearson_anom_p"] < 0.05
    ) & (
        corr_raw_pd["spearman_anom_p"] < 0.05
    )

    def assign_tier(row) -> str:
        a = abs(row["spearman_anom"])
        sig = row["significant"]
        if not sig:
            return "not_significant"
        if a >= 0.5:
            return "tight"
        if a >= 0.3:
            return "moderate"
        return "weak"

    corr_raw_pd["tier"] = corr_raw_pd.apply(assign_tier, axis=1)
    corr_raw_pd["decoupled"] = corr_raw_pd["spearman_anom"].abs() < 0.2

    final = corr_raw_pd[
        [
            "entity_id", "eia_location", "confidence", "n_months",
            "spearman_anom", "spearman_anom_p", "pearson_anom",
            "best_lag", "tier", "decoupled", "significant",
        ]
    ].copy()
    # Sort by |spearman_anom| DESC, exactly matching _final_analysis.py
    final = final.sort_values(
        "spearman_anom", key=lambda s: s.abs(), ascending=False
    ).reset_index(drop=True)
    corr_final = pl.from_pandas(final)

    return corr_raw, corr_final


def conf_of(entity_id: str, loc: pl.DataFrame) -> str:
    """Lookup confidence from gauge_location.csv for an entity_id."""
    row = loc.filter(pl.col("entity_id") == entity_id)
    if row.height:
        return row[0, "confidence"]
    return "low"


# ===================================================================
#  6. Atomic write + dry-run
# ===================================================================

def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """Write a Polars DataFrame to parquet via temp file + os.replace.

    Readers never see a half-written file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".parquet", dir=str(path.parent))
    os.close(tmp_fd)
    try:
        df.write_parquet(tmp_path)
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def atomic_write_csv(df: pl.DataFrame, path: Path) -> None:
    """Write CSV via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(path.parent))
    os.close(tmp_fd)
    try:
        df.write_csv(tmp_path)
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ===================================================================
#  7. Main
# ===================================================================

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rebuild hydro_correlation serving files from live sources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Build + print summary; write nothing to disk.",
    )
    ap.add_argument(
        "--no-extend", action="store_true",
        help="Skip BPA generation extension (use EIA base only).",
    )
    args = ap.parse_args()

    print("=== Build Hydro — Rebuild aligned_pairs + correlation_final ===")
    print(f"Dry-run: {args.dry_run}  |  No-extend: {args.no_extend}", flush=True)

    # ---- 1. Load gauge metadata -------------------------------------------
    print("\n[1/5] Loading gauge metadata...", flush=True)
    loc, tight_ids = load_gauge_metadata()
    all_ids = loc["entity_id"].to_list()
    print(f"  Total gauges: {len(all_ids)}  |  TIGHT: {len(tight_ids)}", flush=True)

    # ---- 2. Build monthly streamflow from daily_entity_metrics ------------
    print("\n[2/5] Building monthly streamflow from daily_entity_metrics...", flush=True)
    flow = build_streamflow_monthly()
    flow_period_min = flow["period"].min()
    flow_period_max = flow["period"].max()
    print(f"  Flow periods: {flow_period_min} .. {flow_period_max} ({flow.height} rows)", flush=True)

    # ---- 3. Build generation (EIA base + BPA extension) -------------------
    print("\n[3/5] Building generation series...", flush=True)
    gen_base = build_generation_base()
    gen_period_min = gen_base["period"].min()
    gen_period_max = gen_base["period"].max()
    print(f"  EIA generation: {gen_period_min} .. {gen_period_max} ({gen_base.height} rows)", flush=True)

    bpa_monthly = None
    if not args.no_extend:
        print("  Attempting BPA gridstatus extension...", flush=True)
        bpa_monthly = get_bpa_monthly_from_gridstatus()
        if bpa_monthly is not None:
            bpa_min = bpa_monthly["period"].min()
            bpa_max = bpa_monthly["period"].max()
            bpa_new = bpa_monthly.filter(pl.col("period") > gen_period_max)
            print(
                f"  BPA monthly available: {bpa_min} .. {bpa_max} "
                f"({bpa_new.height} new months beyond {gen_period_max})",
                flush=True,
            )
        else:
            print(
                "  WARNING: gridstatus unavailable or token not set. "
                "Generation will NOT be extended beyond EIA.",
                flush=True,
            )
    else:
        print("  BPA extension skipped (--no-extend).", flush=True)

    # Choose extension targets: BPA-footprint gauges by default (all when
    # _EXTEND_ALL_GAUGES is set).
    if _EXTEND_ALL_GAUGES:
        target_ids = list(all_ids)
    else:
        loc_bpa = pl.read_csv(_GAUGE_LOC)
        target_ids = (
            loc_bpa.filter(pl.col("eia_location").is_in(_BPA_STATES))
            .select("entity_id")
            .to_series()
            .to_list()
        )

    gen = extend_generation(gen_base, bpa_monthly, target_ids)
    gen_extended_max = gen["period"].max()
    if gen_extended_max > gen_period_max:
        print(
            f"  Generation extended to {gen_extended_max} "
            f"({gen.height - gen_base.height} new rows over "
            f"{len(target_ids)} BPA-footprint gauge(s))",
            flush=True,
        )
    else:
        print(f"  Generation: {gen_period_min} .. {gen_period_max} (no extension)", flush=True)

    # ---- 4. Join into aligned pairs ---------------------------------------
    print("\n[4/5] Joining streamflow × generation...", flush=True)
    pairs = build_aligned_pairs(flow, gen, loc)
    pair_min = pairs["period"].min()
    pair_max = pairs["period"].max()
    print(f"  Aligned pairs: {pairs.height} rows, {pairs['entity_id'].n_unique()} entities", flush=True)
    print(f"  Period range: {pair_min} .. {pair_max}", flush=True)
    # Per-gauge coverage: max period for extended vs non-extended gauges.
    cov = (
        pairs.group_by("entity_id")
        .agg(pl.col("period").max().alias("max_period"))
        .sort("max_period", descending=True)
    )
    top = cov.head(6)
    print("  Gauge coverage (top max_period):",
          [f"{r['entity_id']}→{r['max_period']}" for r in top.to_dicts()], flush=True)

    # ---- 5. Recompute correlation stats -----------------------------------
    print("\n[5/5] Recomputing correlation stats...", flush=True)
    corr_raw, corr_final = compute_correlation_stats(pairs, loc)
    print(f"  correlation_raw.csv: {corr_raw.height} rows", flush=True)
    print(f"  correlation_final.csv: {corr_final.height} rows", flush=True)
    tier_counts = corr_final.group_by("tier").len().to_dicts()
    for tc in tier_counts:
        print(f"    {tc['tier']}: {tc['len']}", flush=True)

    # ---- Verify TIGHT set stability ---------------------------------------
    new_tight = set(
        corr_final.filter(pl.col("tier") == "tight")
        .select("entity_id")
        .to_series()
        .to_list()
    )
    old_tight = set(tight_ids)
    lost = old_tight - new_tight
    gained = new_tight - old_tight
    if lost:
        print(f"  WARNING: {len(lost)} previous TIGHT gauge(s) no longer tight: {sorted(lost)}", flush=True)
    if gained:
        print(f"  NOTE: {len(gained)} new TIGHT gauge(s): {sorted(gained)}", flush=True)
    if not lost and not gained:
        print(f"  TIGHT set unchanged: {len(new_tight)} gauges", flush=True)

    # ---- Dry-run: stop here -----------------------------------------------
    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY-RUN SUMMARY (nothing written)")
        print("=" * 60)
        print(f"  aligned_pairs.parquet: {pairs.height} rows, {pair_min} .. {pair_max}")
        print(f"  correlation_raw.csv:  {corr_raw.height} rows")
        print(f"  correlation_final.csv: {corr_final.height} rows")
        print(f"  NEW period extends past 2026-05: {pair_max}")
        print(f"  Extends past 2026-05 (overall file): {pair_max > '2026-05'}")
        print(f"  BPA-footprint gauges extended: {len(target_ids)} ({target_ids})")
        print(f"  Non-footprint gauges end at last EIA month ({gen_period_max})")
        if bpa_monthly is None:
            print("  BPA extension: NOT applied (unavailable or --no-extend)")
        else:
            bpa_last = bpa_monthly.select(pl.col("period").max()).item()
            print(f"  BPA extension: applied through {bpa_last}")
        print("  TIGHT tiers:", tier_counts)
        print("  Files NOT written (dry-run).")
        print("=" * 60)
        return 0

    # ---- Write outputs atomically -----------------------------------------
    print("\nWriting files...", flush=True)
    atomic_write_parquet(pairs, _ALIGNED_OUT)
    atomic_write_csv(corr_raw, _CORR_RAW_OUT)
    atomic_write_csv(corr_final, _CORR_FINAL_OUT)
    print(f"  ✓ {_ALIGNED_OUT}  ({_ALIGNED_OUT.stat().st_size / 1e6:.1f} MB)", flush=True)
    print(f"  ✓ {_CORR_RAW_OUT}  ({_CORR_RAW_OUT.stat().st_size / 1e3:.0f} KB)", flush=True)
    print(f"  ✓ {_CORR_FINAL_OUT}  ({_CORR_FINAL_OUT.stat().st_size / 1e3:.0f} KB)", flush=True)

    print("\nDone.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())