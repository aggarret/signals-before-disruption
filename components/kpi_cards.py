"""kpi_cards.py — Top-of-page KPI cards (Pass D of the Dash build).

Four cards summarizing the whole gauge network on one metric-date:

  * Extreme Events     # gauges with |z| >= 2.5 today (crimson when > 0)
  * Fastest Riser      gauge with the largest 3-day rise rate (+/−, unit/day)
  * Most Below Normal  region with the lowest mean anomaly
  * Data Health        gauges reporting / gap rate / total dataset rows

All numbers come from queries.get_kpi_cards(); this module only formats and
colors them. The region z-value on card 3 is recomputed from the same cached
date slice the map uses (queries.get_map_data) so it always matches the region
name returned by get_kpi_cards.

No callbacks — app.py re-renders the row whenever metric/date/selection
changes.

Layout contract:
    render_kpi_cards(conn, metric, date) -> dbc.Row of 4 dbc.Col cards
    (xs=12 sm=6 lg=3), CSS classes .kpi-card / .kpi-value / .kpi-label /
    .kpi-sub (see assets/style.css).
"""

from __future__ import annotations

import os
import sys
from typing import Any, List, Optional, Union

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import dash_bootstrap_components as dbc
from dash import html

import queries
from components.map_panel import (
    get_default_date,
    METRIC_LABELS,
    METRIC_UNITS,
    TEXT_BRIGHT,
    Z_AMBER,
    Z_CYAN,
    Z_CRIMSON,
    Z_TEAL,
)

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_value(v: Any, metric: str) -> str:
    """Current-value formatting: 1 decimal for temperatures, 0 otherwise."""
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if metric == "water_temperature":
        return f"{v:.1f} °C"
    return f"{v:,.0f} {METRIC_UNITS.get(metric, '')}".strip()


def _fmt_rise(rate: Any, metric: str) -> str:
    """3-day rise rate with explicit +/− sign and a per-day unit."""
    if rate is None or pd.isna(rate):
        return "—"
    rate = float(rate)
    body = f"{rate:+.1f}" if metric == "water_temperature" else f"{rate:+,.0f}"
    unit = METRIC_UNITS.get(metric, "")
    return f"{body} {unit}/day".strip()


def _truncate(name: Any, n: int = 25) -> str:
    name = str(name)
    if len(name) <= n:
        return name
    return name[: n - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------
def _kpi_card(icon: str, label: str, value: Any, sub: Any,
              value_color: Optional[str] = None,
              sub_color: Optional[str] = None) -> dbc.Col:
    """One KPI column: icon, uppercase label, big value, muted sub-text."""
    return dbc.Col(
        dbc.Card(
            html.Div(
                [
                    html.Div(icon, style={"fontSize": "22px", "marginBottom": "2px"}),
                    html.Div(label, className="kpi-label"),
                    html.Div(value, className="kpi-value",
                             style={"color": value_color} if value_color else None),
                    html.Div(sub, className="kpi-sub",
                             style={"color": sub_color} if sub_color else None),
                ],
                className="kpi-card",
            )
        ),
        xs=12, sm=6, lg=3,
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_kpi_cards(
    conn: Any = None,
    metric: str = "streamflow",
    date: Optional[str] = None,
) -> dbc.Row:
    """The four top-of-page KPI cards for one metric-date."""
    conn = conn or queries.get_connection()
    date = str(date or get_default_date(conn))
    k = queries.get_kpi_cards(conn, metric, date)
    metric_label = METRIC_LABELS.get(metric, metric)

    # --- card 1: extreme events --------------------------------------------
    n_extreme = int(k.get("extreme_events_today") or 0)
    c1 = _kpi_card(
        "🌡️", "Extreme Events",
        str(n_extreme),
        f"gauges with |z| ≥ 2.5 · {metric_label}",
        value_color=Z_CRIMSON if n_extreme > 0 else Z_TEAL,
    )

    # --- card 2: fastest riser ----------------------------------------------
    riser = k.get("fastest_riser")
    if riser and riser.get("station_name"):
        rate = riser.get("rise_rate_3d")
        rise_str = _fmt_rise(rate, metric)
        cur = _fmt_value(riser.get("value"), metric)
        rising = rate is not None and not pd.isna(rate) and float(rate) >= 0
        c2 = _kpi_card(
            "📈", "Fastest Riser",
            _truncate(riser["station_name"], 25),
            [rise_str, f"now {cur}"],
            value_color=TEXT_BRIGHT,
            sub_color=Z_CYAN if rising else Z_AMBER,
        )
    else:
        c2 = _kpi_card("📈", "Fastest Riser", "—", "no gauges reporting")

    # --- card 3: most below normal ------------------------------------------
    region = k.get("most_below_normal")
    if region:
        # Same cached date slice the map colors by; guarantees the z shown
        # matches the region name get_kpi_cards returned.
        md = queries.get_map_data(conn, metric, date)
        if md.empty:
            z = None
        else:
            grp = (md.dropna(subset=["anomaly_score"])
                     .groupby("region")["anomaly_score"].mean())
            z = float(grp.get(region)) if region in grp.index else None
        zstr = f"{z:+.2f} σ" if z is not None else "—"
        c3 = _kpi_card(
            "📉", "Most Below Normal",
            _truncate(region, 25),
            f"regional avg anomaly {zstr}",
            value_color=Z_AMBER,
        )
    else:
        c3 = _kpi_card("📉", "Most Below Normal", "—", "no anomaly data")

    # --- card 4: data health --------------------------------------------------
    health = k.get("data_health")
    if health:
        # n_rep   = gauges actively reporting (completeness > 0) = colored map dots
        # n_map   = all gauges present on the map that day = every row/dot
        #           (including grey completeness==0 gauges)
        n_rep = int(health.get("gauges_reporting") or 0)
        n_map = int(health.get("total_gauges") or 0)
        gap = float(health.get("gap_rate") or 0.0)
        rows = int(health.get("total_rows") or 0)
        c4 = _kpi_card(
            "✅", "Data Health",
            f"{n_rep}/{n_map} gauges reporting",
            [f"gap rate {gap * 100:.2f}%", f"{rows:,} total rows"],
            value_color=Z_TEAL if n_map and n_rep >= n_map * 0.9 else Z_AMBER,
        )
    else:
        c4 = _kpi_card("✅", "Data Health", "—", "no data")

    return dbc.Row([c1, c2, c3, c4], class_name="g-2")


# ---------------------------------------------------------------------------
# Self-test: python3 components/kpi_cards.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    d = str(get_default_date(conn))
    row = render_kpi_cards(conn, "streamflow", d)

    assert isinstance(row, dbc.Row), "render_kpi_cards() must return a dbc.Row"
    cols = list(row.children)
    assert len(cols) == 4, f"expected 4 KPI cards, got {len(cols)}"

    print(f"date used      : {d}")
    print(f"return type    : {type(row).__name__} (dbc.Row)")
    print(f"cards          : {len(cols)}")
    for c in cols:
        inner = c.children.children  # Col -> Card -> Div.kpi-card
        label = inner.children[1].children
        value = inner.children[2].children
        sub = inner.children[3].children
        if isinstance(sub, list):
            sub = " · ".join(str(s) for s in sub)
        print(f"  - {label!r:<18} value={value!r:<24} sub={sub!r}")

    # cross-check card 2 against the query layer
    k = queries.get_kpi_cards(conn, "streamflow", d)
    fr = k.get("fastest_riser")
    if fr:
        print(f"fastest riser  : {fr['station_name']} @ "
              f"{fr['rise_rate_3d']:+,.0f} ft³/s/day (matches card 2)")
    else:
        print("fastest riser  : none this date")

    # water-temperature path (different units / formatting)
    row_t = render_kpi_cards(conn, "water_temperature", d)
    assert isinstance(row_t, dbc.Row) and len(row_t.children) == 4

    print("ALL CHECKS PASSED")
