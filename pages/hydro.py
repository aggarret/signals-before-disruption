"""pages/hydro.py — River Personality Monitor: the Hydro Coupling page ("/hydro").

TIGHT-tier scope, full window. This page surfaces the 14 "tight"-tier USGS
gauges whose monthly streamflow (anomaly) is most strongly correlated with
regional hydro-electric generation — Spearman |rho| >= 0.5, p < 0.05 — over
the full available aligned window (2004-01 → present).

BUILDER B3 owns the four visualization panels + the gridstatus note. The layout
scaffold (intro, stores, empty containers) was built by B1; the data-access
layer (get_tight_gauges / get_ranked_coupling / get_gauge_series /
get_eia_hydro) lives in hydro_queries.py (B2). This module implements the
panels and wires them into the existing containers via register_callbacks():

    1. Coupling map        -> hydro-map-container        (14 tight teal markers
       over the full 52-gauge field; faint gray context markers are inert)
    2. Ranked coupling strip -> hydro-strip-container    (14 tight, by |rho|
       desc), bar click selects a gauge
    3. Small-multiples grid -> hydro-smallmultiples-container (4x4 sparklines)
    4. Drill-down          -> hydro-drilldown-container  (anomaly overlay +
       lag-0 / lag-+1 toggle)
    5. GridStatus note     -> hydro-gridstatus-note      (gated daily-hydro note)

The gridstatus import is lazy/guarded (see hydro_gridstatus.py); importing this
module is always safe.
"""

from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html, no_update
from plotly.subplots import make_subplots

from app import TEXT_FAINT, TEXT_MUTED, _MANTINE_THEME  # noqa: E402

import hydro_queries  # B2 data-access layer (functions + _conn helper)

# ---------------------------------------------------------------------------
# Resolve the live Dash app + dark-slate palette (reuse map_panel's constants
# so the coupling map is visually consistent with the National Overview map).
# ---------------------------------------------------------------------------
app = dash.get_app()

try:
    from components.map_panel import (
        PAPER_BG, CARD_BG, BORDER, TEXT_MUTED as _MP_MUTED,
        TEXT_BRIGHT, GEO_LAYOUT, Z_TEAL, Z_TEAL_CYAN,
    )
    _MP_OK = True
except Exception:  # pragma: no cover - defensive fallback palette
    PAPER_BG, CARD_BG, BORDER = "#0f172a", "#1e293b", "#334155"
    _MP_MUTED, TEXT_BRIGHT = "#94a3b8", "#e2e8f0"
    Z_TEAL, Z_TEAL_CYAN = "#14b8a6", "#0dd4be"
    GEO_LAYOUT = dict(scope="usa", bgcolor="#0f172a")
    _MP_OK = False

dash.register_page(
    __name__,
    path="/hydro",
    name="Hydro Coupling",
    order=3,
    title="River Personality Monitor — Hydro Coupling",
)

# The first tight-tier gauge (highest |rho|) — the default gauge selection.
DEFAULT_TIGHT_GAUGE = "USGS-01578310"

# Full aligned window (2004-01 → present, 269 months). Kept in sync with
# hydro_correlation/aligned_pairs.parquet (see the intros / Guide).
DEFAULT_DATE_RANGE = ["2004-01", "2026-05"]

# Sequential teal scale for |rho| (monotonic: weakest-tight = lightest teal).
_TEAL_WEAK = "#5eead4"   # 0.50 <= |rho| < 0.58
_TEAL_MID = "#14b8a6"    # 0.58 <= |rho| < 0.66
_TEAL_STRONG = "#0d9488"  # |rho| >= 0.66
_FG_GRAY = "#475569"     # faint context markers (non-tight gauges)
_FG_OPACITY = 0.45

# Component ids
_ID_MAP_GRAPH = "hydro-map-graph"
_ID_STRIP_GRAPH = "hydro-strip-graph"
_ID_LAG_TOGGLE = "hydro-lag-toggle"
_ID_DRILL_GRAPH = "hydro-drilldown-graph"
_ID_GRID_NOTE = "hydro-gridstatus-note-body"

_ID_SELECTED = "hydro-selected-gauge"
_ID_RANGE = "hydro-date-range"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _conn():
    """Best-effort handle to the B2 per-thread read-only connection.

    hydro_queries registers corr_final / gauge_geo / gauge_loc / aligned_pairs
    as relations on this connection, which the map's 52-gauge query relies on.
    """
    return hydro_queries._conn()


def _all_gauges_geo() -> pd.DataFrame:
    """All 52 gauges joined with geo metadata (corr_final × gauge_geo).

    Columns: entity_id, eia_location, spearman_anom, best_lag, tier,
             station_name, state, latitude, longitude.
    """
    df = _conn().execute(
        """
        SELECT cf.entity_id,
               cf.eia_location,
               cf.spearman_anom,
               cf.best_lag,
               cf.tier,
               gg.station_name,
               gg.state,
               gg.latitude,
               gg.longitude
        FROM corr_final cf
        LEFT JOIN gauge_geo gg ON cf.entity_id = gg.entity_id
        """
    ).df()
    for col in ("spearman_anom", "latitude", "longitude"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["best_lag"] = pd.to_numeric(df["best_lag"], errors="coerce").astype("Int64")
    df["tier"] = df["tier"].astype(str)
    return df.reset_index(drop=True)


def _rho_bin(rho: float) -> str:
    """|rho| -> teal hex for the 3-bin sequential scale."""
    a = abs(float(rho))
    if a >= 0.66:
        return _TEAL_STRONG
    if a >= 0.58:
        return _TEAL_MID
    return _TEAL_WEAK


def _zscore(s: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    sd = x.std()
    if not np.isfinite(sd) or sd == 0:
        return x * 0.0
    return (x - x.mean()) / sd


def _monthly_anomaly(df: pd.DataFrame, col: str) -> np.ndarray:
    """De-seasonalized monthly z-score anomaly (flow/gen share this axis).

    Removes the calendar-month climatology (mean + std), then z-scores so
    streamflow and hydro generation live on a common dimensionless axis.
    """
    x = pd.to_numeric(df[col], errors="coerce")
    arr = x.to_numpy(dtype=float)
    month = df["period"].str.slice(5, 7).astype(int).to_numpy()
    out = np.full(len(arr), np.nan)
    for m in range(1, 13):
        idx = month == m
        if not idx.any():
            continue
        mu = np.nanmean(arr[idx])
        sd = np.nanstd(arr[idx])
        out[idx] = (arr[idx] - mu) / sd if (sd and np.isfinite(sd)) else 0.0
    return out


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 5:
        return float("nan")
    from scipy.stats import spearmanr
    r, _ = spearmanr(a[mask], b[mask])
    return float(r)


# ---------------------------------------------------------------------------
# Panel 1 — Coupling map
# ---------------------------------------------------------------------------
def _build_map_figure(selected: str | None) -> go.Figure:
    gauges = _all_gauges_geo()
    tight = gauges[gauges["tier"] == "tight"].copy()
    others = gauges[gauges["tier"] != "tight"].copy()

    fig = go.Figure()

    # --- faint gray context markers (38 non-tight; inert, not selectable) ---
    if not others.empty:
        fig.add_trace(go.Scattergeo(
            lon=others["longitude"], lat=others["latitude"],
            mode="markers",
            marker=dict(size=7, color=_FG_GRAY, opacity=_FG_OPACITY,
                        line=dict(color=PAPER_BG, width=0.5)),
            hoverinfo="skip",
            customdata=[[None] for _ in range(len(others))],
            showlegend=False,
        ))

    # --- teal tight markers, color/size by |rho| ----------------------------
    if not tight.empty:
        sizes = [6.0 + 8.0 * (abs(r) - 0.5) / 0.25 for r in tight["spearman_anom"]]
        colors = tight["spearman_anom"].map(_rho_bin).tolist()
        hov = [
            (
                f"<b>{r.station_name}</b><br>{r.state}<br>"
                f"ρ = {float(r.spearman_anom):+.3f}<br>"
                f"|ρ| = {abs(float(r.spearman_anom)):.3f} · lag {r.best_lag}<br>"
                f"TIGHT"
            )
            for _, r in tight.iterrows()
        ]
        fig.add_trace(go.Scattergeo(
            lon=tight["longitude"], lat=tight["latitude"],
            mode="markers",
            marker=dict(size=sizes, color=colors, opacity=0.95,
                        line=dict(color="#f8fafc", width=1)),
            text=hov, hoverinfo="text",
            customdata=[[e] for e in tight["entity_id"]],
            name="Tight gauges", showlegend=False,
        ))

        # --- selected white ring -------------------------------------------
        if selected:
            sel = tight[tight["entity_id"] == str(selected)]
            if not sel.empty and pd.notna(sel.iloc[0]["latitude"]):
                srow = sel.iloc[0]
                sr = abs(float(srow["spearman_anom"]))
                ssize = 6.0 + 8.0 * (sr - 0.5) / 0.25
                fig.add_trace(go.Scattergeo(
                    lon=[srow["longitude"]], lat=[srow["latitude"]],
                    mode="markers",
                    marker=dict(size=ssize + 7.0, color="rgba(255,255,255,0)",
                                line=dict(color="#f8fafc", width=2)),
                    hoverinfo="skip",
                    customdata=[[str(selected)]],
                    name="Selected", showlegend=False,
                ))

    fig.update_layout(
        template=None,
        paper_bgcolor=PAPER_BG, plot_bgcolor=PAPER_BG,
        font=dict(color=_MP_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=12),
        margin=dict(l=0, r=0, t=8, b=0),
        height=430,
        geo=GEO_LAYOUT,
        uirevision="hydro-map",   # keep zoom/pan stable across selection changes
        showlegend=False,
    )
    fig.update_geos(showframe=False)
    return fig


def _map_legend() -> html.Div:
    """Manual HTML swatches — exact hexes from the |rho| teal scale."""
    items = [
        (_TEAL_STRONG, "|ρ| ≥ 0.66"),
        (_TEAL_MID, "0.58–0.66"),
        (_TEAL_WEAK, "0.50–0.58"),
        (_FG_GRAY, "others (38, inert)"),
    ]
    swatches = [
        html.Div(
            [
                html.Span(style={
                    "display": "inline-block", "width": "12px", "height": "12px",
                    "borderRadius": "50%", "backgroundColor": color, "marginRight": "6px",
                }),
                html.Span(label, style={"fontSize": "11px", "color": _MP_MUTED,
                                        "whiteSpace": "nowrap"}),
            ],
            style={"display": "flex", "alignItems": "center", "marginRight": "16px"},
        )
        for color, label in items
    ]
    return html.Div(swatches, style={"display": "flex", "flexWrap": "wrap",
                                    "padding": "6px 2px 0"})


def _render_map(selected: str | None) -> dbc.Card:
    fig = _build_map_figure(selected)
    return dbc.Card(
        [
            dbc.CardHeader(
                html.Div(
                    [
                        html.H6("Tight-coupling gauges (|ρ| ≥ 0.5)", style={
                            "margin": "0", "color": TEXT_BRIGHT, "fontWeight": "600",
                        }),
                        html.Small(
                            "14 tight (teal, selectable) over the full 52-gauge field · "
                            "click a teal marker",
                            style={"color": "#64748b"},
                        ),
                    ],
                    style={"display": "flex", "alignItems": "center",
                           "flexWrap": "wrap", "gap": "8px",
                           "justifyContent": "space-between"},
                ),
                style={"backgroundColor": CARD_BG,
                       "borderBottom": f"1px solid {BORDER}"},
            ),
            dbc.CardBody(
                [
                    dcc.Graph(id=_ID_MAP_GRAPH, figure=fig,
                              config={"displayModeBar": False},
                              style={"height": "430px", "width": "100%"}),
                    _map_legend(),
                ],
                style={"padding": "0.5rem 0.75rem", "backgroundColor": PAPER_BG},
            ),
        ],
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "overflow": "hidden", "marginBottom": "16px"},
    )


# ---------------------------------------------------------------------------
# Panel 2 — Ranked coupling strip
# ---------------------------------------------------------------------------
def _build_strip_figure(selected: str | None) -> go.Figure:
    ranked = hydro_queries.get_ranked_coupling()
    if ranked.empty:
        return go.Figure()
    ranked = ranked.sort_values("spearman_anom", ascending=False)  # strongest on top
    labels = [f"{r.eia_location} · {float(r.spearman_anom):.3f}"
              for _, r in ranked.iterrows()]
    colors = [_rho_bin(r.spearman_anom) for _, r in ranked.iterrows()]
    # highlight the selected bar with a brighter fill so selection is visible
    colors = [("#22d3ee" if r.entity_id == str(selected) else c)
              for r, c in zip(ranked.itertuples(), colors)]
    hov = [
        (f"<b>{r.station_name}</b><br>{r.eia_location}<br>"
         f"ρ = {float(r.spearman_anom):+.3f} · best lag {r.best_lag}")
        for _, r in ranked.iterrows()
    ]
    fig = go.Figure(go.Bar(
        x=ranked["spearman_anom"], y=labels,
        orientation="h",
        marker=dict(color=colors, line=dict(color="#0f172a", width=1),
                    opacity=0.95),
        customdata=[[e] for e in ranked["entity_id"]],
        text=[f"{float(r.spearman_anom):+.2f}" for _, r in ranked.iterrows()],
        textposition="outside",
        textfont=dict(color=_MP_MUTED, size=10),
        hovertemplate="%{customdata[0]}<br>%{customdata[1]}<br>"
                      "ρ = %{x:.3f}<extra></extra>",
    ))
    # enrich hover via a second (invisible) customdata channel
    for t in fig.data:
        t.customdata = [[e, h] for e, h in zip(ranked["entity_id"], hov)]
    fig.update_layout(
        template=None,
        paper_bgcolor=PAPER_BG, plot_bgcolor=PAPER_BG,
        font=dict(color=_MP_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=11),
        margin=dict(l=0, r=56, t=8, b=0),
        height=430,
        xaxis=dict(title="Spearman ρ (flow vs hydro, anom)",
                   gridcolor=BORDER, zeroline=False, range=[0.45, 0.8]),
        yaxis=dict(autorange="reversed"),
        uirevision="hydro-strip",
        showlegend=False,
        bargap=0.35,
    )
    return fig


def _render_strip(selected: str | None) -> dbc.Card:
    fig = _build_strip_figure(selected)
    return dbc.Card(
        [
            dbc.CardHeader(
                html.Div(
                    [
                        html.H6("Ranked coupling — the 14 tight gauges", style={
                            "margin": "0", "color": TEXT_BRIGHT, "fontWeight": "600",
                        }),
                        html.Small("by |ρ| desc · click a bar to inspect",
                                   style={"color": "#64748b"}),
                    ],
                    style={"display": "flex", "alignItems": "center",
                           "flexWrap": "wrap", "gap": "8px",
                           "justifyContent": "space-between"},
                ),
                style={"backgroundColor": CARD_BG,
                       "borderBottom": f"1px solid {BORDER}"},
            ),
            dbc.CardBody(
                dcc.Graph(id=_ID_STRIP_GRAPH, figure=fig,
                          config={"displayModeBar": False},
                          style={"height": "430px", "width": "100%"}),
                style={"padding": "0.5rem 0.75rem", "backgroundColor": PAPER_BG},
            ),
        ],
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "overflow": "hidden", "marginBottom": "16px"},
    )


# ---------------------------------------------------------------------------
# Panel 3 — Small-multiples grid (4x4 sparklines)
# ---------------------------------------------------------------------------
def _build_smallmultiples_figure() -> go.Figure:
    tight = hydro_queries.get_tight_gauges()  # already sorted by |rho| desc
    n = len(tight)
    cols, rows = 4, 4
    fig = make_subplots(
        rows=rows, cols=cols,
        subplot_titles=[f"{r.state.split()[-1] if r.state else ''} · "
                        f"{float(r.spearman_anom):+.2f}" for _, r in tight.iterrows()]
                      + [""] * (rows * cols - n),
        horizontal_spacing=0.045, vertical_spacing=0.16,
    )
    for i, (_, r) in enumerate(tight.iterrows()):
        s = hydro_queries.get_gauge_series(entity_id=str(r.entity_id))
        if s.empty:
            continue
        row, col = i // cols + 1, i % cols + 1
        flow_z = _zscore(s["mean_flow_cfs"])
        gen_z = _zscore(s["generation_thousand_mwh"])
        x = pd.to_datetime(s["period"])
        fig.add_trace(go.Scatter(
            x=x, y=flow_z, mode="lines",
            line=dict(color=_TEAL_MID, width=1.4),
            name="flow", showlegend=False,
            hovertemplate=f"{r.entity_id}<br>%{{x|%b %Y}}<br>flow z = %{{y:.2f}}<extra></extra>",
        ), row=row, col=col)
        fig.add_trace(go.Scatter(
            x=x, y=gen_z, mode="lines",
            line=dict(color="#f59e0b", width=1.1),
            name="hydro", showlegend=False,
            hovertemplate=f"{r.entity_id}<br>%{{x|%b %Y}}<br>hydro z = %{{y:.2f}}<extra></extra>",
        ), row=row, col=col)
    fig.update_layout(
        template=None,
        paper_bgcolor=PAPER_BG, plot_bgcolor=PAPER_BG,
        font=dict(color=_MP_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=10),
        margin=dict(l=10, r=10, t=28, b=10),
        height=560,
        showlegend=False,
        uirevision="hydro-smallmultiples",
    )
    for ax in fig.select_xaxes():
        ax.update(type="date", showticklabels=False, showgrid=False, zeroline=False)
    for ax in fig.select_yaxes():
        ax.update(showticklabels=False, showgrid=False, zeroline=False,
                  range=[-4, 4])
    for t in fig.layout.annotations:
        t.update(font=dict(size=10, color=_MP_MUTED))
    return fig


def _render_smallmultiples() -> dbc.Card:
    fig = _build_smallmultiples_figure()
    return dbc.Card(
        [
            dbc.CardHeader(
                html.Div(
                    [
                        html.H6("All 14 tight gauges — flow vs hydro (normalized)",
                                style={"margin": "0", "color": TEXT_BRIGHT,
                                       "fontWeight": "600"}),
                        html.Small("teal = streamflow z · amber = hydro gen z",
                                   style={"color": "#64748b"}),
                    ],
                    style={"display": "flex", "alignItems": "center",
                           "flexWrap": "wrap", "gap": "8px",
                           "justifyContent": "space-between"},
                ),
                style={"backgroundColor": CARD_BG,
                       "borderBottom": f"1px solid {BORDER}"},
            ),
            dbc.CardBody(
                dcc.Graph(figure=fig, config={"displayModeBar": False},
                          style={"height": "560px", "width": "100%"}),
                style={"padding": "0.5rem 0.75rem", "backgroundColor": PAPER_BG},
            ),
        ],
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "overflow": "hidden", "marginBottom": "16px"},
    )


# ---------------------------------------------------------------------------
# Panel 4 — Drill-down (anomaly overlay + lag toggle)
# ---------------------------------------------------------------------------
def _drill_data(gauge: str):
    """Aligned flow + location hydro with common date axis + anomalies."""
    loc = None
    try:
        tight = hydro_queries.get_tight_gauges()
        row = tight[tight["entity_id"] == str(gauge)]
        loc = row.iloc[0]["eia_location"] if not row.empty else None
    except Exception:
        pass

    s = hydro_queries.get_gauge_series(entity_id=str(gauge))
    if s.empty:
        return None, None, None

    # location-level hydro (aggregate across gauges mapped to this location)
    # Guard: keep the expected columns even when empty so figure builders
    # never KeyError on gauges outside the TIGHT set (e.g. stale selection,
    # URL-crafted entity_id). Empty hydro -> gen series renders as NaN/flat.
    hydro = pd.DataFrame(columns=["period", "generation_thousand_mwh"])
    if loc:
        e = hydro_queries.get_eia_hydro(eia_location=loc)
        if not e.empty:
            hydro = (e.groupby("period", as_index=False)["generation_thousand_mwh"]
                      .sum(min_count=1))

    dinfo = dict(station_name=s.iloc[0]["entity_id"], eia_location=loc,
                 eia_location_name=loc, state="")
    if loc is not None:
        try:
            tight = hydro_queries.get_tight_gauges()
            row = tight[tight["entity_id"] == str(gauge)]
            if not row.empty:
                dinfo["station_name"] = row.iloc[0]["station_name"]
                dinfo["state"] = row.iloc[0]["state"]
                dinfo["eia_location_name"] = row.iloc[0]["eia_location_name"]
                dinfo["rho0"] = float(row.iloc[0]["spearman_anom"])
        except Exception:
            pass
    return s, hydro, dinfo


def _build_overlay(s: pd.DataFrame, hydro: pd.DataFrame) -> go.Figure:
    x_dates = pd.to_datetime(s["period"])
    flow_a = _monthly_anomaly(s, "mean_flow_cfs")
    gen_a = _monthly_anomaly(hydro, "generation_thousand_mwh").astype(float)

    # align gen to flow's periods (both share the same calendar) for the overlay
    gen_z = _zscore(hydro["generation_thousand_mwh"]).to_numpy(dtype=float)
    gen_map = dict(zip(hydro["period"], gen_z))
    gen_aligned = np.array([gen_map.get(p, np.nan) for p in s["period"]])

    fig = go.Figure()
    # green co-movement shading where flow & hydro both move the same direction
    co = (flow_a > 0) & (gen_aligned > 0) | (flow_a < 0) & (gen_aligned < 0)
    co = np.array(co, dtype=bool)
    i = 0
    while i < len(co):
        if co[i]:
            j = i
            while j + 1 < len(co) and co[j + 1]:
                j += 1
            fig.add_shape(dict(type="rect", xref="x", yref="paper",
                               x0=x_dates.iloc[i] - pd.Timedelta(days=1),
                               x1=x_dates.iloc[j] + pd.Timedelta(days=1),
                               y0=0, y1=1,
                               fillcolor="rgba(20,184,166,0.13)", layer="below",
                               line_width=0))
            i = j + 1
        else:
            i += 1

    fig.add_trace(go.Scatter(
        x=x_dates, y=flow_a, mode="lines", name="flow (de-seasonalized)",
        line=dict(color=_TEAL_MID, width=1.6),
        hovertemplate="flow z = %{y:.2f}<br>%{x|%b %Y}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=x_dates, y=gen_aligned, mode="lines", name="hydro (de-seasonalized)",
        line=dict(color="#f59e0b", width=1.4),
        hovertemplate="hydro z = %{y:.2f}<br>%{x|%b %Y}<extra></extra>",
    ))
    fig.update_layout(
        template=None,
        paper_bgcolor=PAPER_BG, plot_bgcolor=PAPER_BG,
        font=dict(color=_MP_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=11),
        margin=dict(l=72, r=24, t=40, b=72),
        height=420,
        xaxis=dict(type="date", tickformat="%b %Y", nticks=10,
                   showgrid=False, zeroline=False, tickangle=-45,
                   automargin=True, ticks="outside", showticklabels=True),
        yaxis=dict(title=dict(text="monthly z-score", standoff=14),
                   gridcolor=BORDER, zeroline=True, zerolinecolor=BORDER,
                   automargin=True, tickangle=0, ticks="outside",
                   separatethousands=True, exponentformat="none"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0, font=dict(size=11)),
        showlegend=True,
    )
    return fig


def _build_lag_scatter(s: pd.DataFrame, hydro: pd.DataFrame) -> go.Figure:
    # lag +1: flow(t) vs hydro(t+1)  -> shift hydro up by one month
    gen_aligned = _zscore(hydro["generation_thousand_mwh"]).to_numpy(dtype=float)
    gen_map = dict(zip(hydro["period"], gen_aligned))
    flow_a = _monthly_anomaly(s, "mean_flow_cfs")
    gen_lag = np.full(len(s), np.nan)
    for i, p in enumerate(s["period"]):
        nxt = _shift_period(p, 1)
        if nxt in gen_map:
            gen_lag[i] = gen_map[nxt]
    mask = np.isfinite(flow_a) & np.isfinite(gen_lag)
    x, y = flow_a[mask], gen_lag[mask]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=y, mode="markers",
        marker=dict(color=_TEAL_MID, size=7, opacity=0.7,
                    line=dict(color=PAPER_BG, width=0.5)),
        customdata=[[s["period"].iloc[k]] for k in np.where(mask)[0]],
        hovertemplate="%{customdata[0]}<br>flow z = %{x:.2f} · "
                      "hydro next-mo z = %{y:.2f}<extra></extra>",
    ))
    if len(x) >= 3:
        A = np.vstack([x, np.ones_like(x)]).T
        slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
        xs = np.linspace(x.min(), x.max(), 50)
        fig.add_trace(go.Scatter(
            x=xs, y=slope * xs + intercept, mode="lines",
            line=dict(color="#f8fafc", width=2, dash="dash"),
            name="fit", hovertemplate="fit<extra></extra>",
        ))
        rho_lag = _spearman(x, y)
    else:
        rho_lag = float("nan")
    fig.add_annotation(
        x=0.02, y=0.98, xref="paper", yref="paper", xanchor="left", yanchor="top",
        text=(f"lag +1 ρ = {rho_lag:+.3f}" if np.isfinite(rho_lag) else "lag +1 ρ = —"),
        showarrow=False, font=dict(color=_MP_MUTED, size=12),
        bgcolor="rgba(15,23,42,0.7)", bordercolor=BORDER, borderwidth=1,
    )
    fig.update_layout(
        template=None,
        paper_bgcolor=PAPER_BG, plot_bgcolor=PAPER_BG,
        font=dict(color=_MP_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=11),
        margin=dict(l=80, r=24, t=24, b=64),
        height=420,
        xaxis=dict(title=dict(text="flow z-score", standoff=10),
                   gridcolor=BORDER, zeroline=True,
                   zerolinecolor=BORDER, automargin=True,
                   showticklabels=True, tickangle=0, ticks="outside",
                   separatethousands=True, exponentformat="none"),
        yaxis=dict(title=dict(text="hydro z-score (next month)", standoff=14),
                   gridcolor=BORDER, zeroline=True, zerolinecolor=BORDER,
                   automargin=True, tickangle=0, ticks="outside",
                   separatethousands=True, exponentformat="none"),
        showlegend=False,
    )
    return fig


def _shift_period(period: str, months: int) -> str:
    y, m = int(period[:4]), int(period[5:7])
    m += months
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def _render_drilldown(gauge: str, lag: int | None) -> dbc.Card | html.Div:
    lag = 1 if lag == 1 else 0
    s, hydro, dinfo = _drill_data(gauge)
    if s is None or s.empty:
        return html.Div("No aligned data for this gauge.",
                        style={"color": TEXT_FAINT, "fontSize": "13px",
                               "padding": "8px 2px"})

    rho0 = dinfo.get("rho0")
    name = dinfo.get("station_name") or gauge
    st = dinfo.get("state") or ""
    loc_nm = dinfo.get("eia_location_name") or dinfo.get("eia_location") or ""
    title = f"{name}"
    if st:
        title += f" · {st}"
    if loc_nm:
        title += f" · {loc_nm}"

    badge = html.Span(
        "TIGHT",
        style={"backgroundColor": "#0d9488", "color": "#ecfdf5",
               "fontSize": "11px", "fontWeight": "700", "borderRadius": "9999px",
               "padding": "2px 10px", "letterSpacing": "0.5px"},
    )
    rho_label = (f"ρ = {rho0:+.3f}" if rho0 is not None and np.isfinite(rho0)
                 else "ρ = —")

    fig = _build_lag_scatter(s, hydro) if lag == 1 else _build_overlay(s, hydro)

    header = dbc.CardHeader(
        html.Div(
            [
                html.Div(
                    [
                        html.H6(title, style={"margin": "0", "color": TEXT_BRIGHT,
                                              "fontWeight": "600"}),
                        html.Small(
                            (f"{rho_label} · overlaid de-seasonalized series"
                             if lag == 0 else
                             f"lag +1 view · contemporaneous ρ = "
                             f"{rho0 if rho0 is not None and np.isfinite(rho0) else float('nan'):+.3f}"),
                            style={"color": "#64748b"},
                        ),
                    ],
                    style={"flex": "1 1 auto"},
                ),
                badge if lag == 0 else badge,
            ],
            style={"display": "flex", "alignItems": "center", "gap": "12px",
                   "flexWrap": "wrap"},
        ),
        style={"backgroundColor": CARD_BG, "borderBottom": f"1px solid {BORDER}"},
    )

    mode_label = "Overlay: monthly flow vs hydro (normalized)" if lag == 0 \
        else "Lag +1 month: flow vs next-month hydro"
    controls = html.Div(
        [
            dbc.RadioItems(
                id=_ID_LAG_TOGGLE,
                options=[
                    {"label": "Lag 0 (overlay)", "value": 0},
                    {"label": "Lag +1 (scatter)", "value": 1},
                ],
                value=lag,
                inline=True,
                style={"fontSize": "12px", "color": _MP_MUTED},
            ),
            html.Small(mode_label, style={"color": "#64748b", "marginLeft": "6px"}),
        ],
        style={"display": "flex", "alignItems": "center", "flexWrap": "wrap",
               "gap": "10px", "padding": "0.4rem 0.75rem 0.15rem"},
    )

    return dbc.Card(
        [
            header,
            controls,
            dbc.CardBody(
                dcc.Graph(id=_ID_DRILL_GRAPH, figure=fig,
                          config={"displayModeBar": False},
                          style={"height": "420px", "width": "100%"}),
                style={"padding": "0.5rem 0.75rem", "backgroundColor": PAPER_BG},
            ),
        ],
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "overflow": "hidden", "marginBottom": "16px"},
    )


# ---------------------------------------------------------------------------
# Panel 5 — GridStatus note
# ---------------------------------------------------------------------------
def _render_gridnote() -> dbc.Card:
    try:
        from hydro_gridstatus import gridstatus_status_text  # lazy, guarded
        text = gridstatus_status_text()
    except Exception:
        text = ("Daily-resolution BPA hydro requires a GridStatus token — "
                "currently showing monthly EIA data.")
    return dbc.Card(
        [
            dbc.CardBody(
                html.Div(
                    [
                        html.Span("⚡", style={"marginRight": "8px"}),
                        html.Span(text, style={"color": _MP_MUTED,
                                               "fontSize": "12.5px",
                                               "lineHeight": "1.5"}),
                    ],
                    style={"display": "flex", "alignItems": "flex-start"},
                ),
                style={"backgroundColor": CARD_BG, "padding": "0.6rem 0.9rem"},
            )
        ],
        id=_ID_GRID_NOTE,
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "marginBottom": "16px"},
    )


# ---------------------------------------------------------------------------
# Layout (owned by B1) — unchanged scaffold.
# ---------------------------------------------------------------------------
def layout() -> dmc.MantineProvider:
    """The hydro page scaffold: intro card + state stores + B3 container slots."""
    intro = dbc.Card(
        [
            dbc.CardHeader(
                html.H5("💧 Hydro Coupling", style={
                    "color": "#14b8a6", "margin": "0", "fontWeight": "700",
                }),
                style={"backgroundColor": "#1e293b",
                       "borderBottom": "1px solid #334155"},
            ),
            dbc.CardBody(
                [
                    html.P(
                        "This page shows the 14 “tight”-tier USGS gauges where "
                        "monthly streamflow is most strongly correlated with "
                        "regional hydro-electric generation (Spearman |ρ| ≥ 0.5, "
                        "p < 0.05), over the full available window "
                        "(2004–present).",
                        style={"color": "#e2e8f0", "fontSize": "14px",
                               "lineHeight": "1.65", "margin": "0 0 8px"},
                    ),
                    html.Div(
                        [
                            "For methodology, data sources, and the full "
                            "52-gauge analysis, see the ",
                            html.A("Guide", href="/guide",
                                   style={"color": "#14b8a6",
                                          "textDecoration": "underline"}),
                            ". The tight gauges here extend the Guide's "
                            "Morning Scan into a fifth decision — how a river's "
                            "drama moves the power it generates (e.g., the "
                            "Columbia at The Dalles feeding BPA).",
                        ],
                        style={"color": TEXT_MUTED, "fontSize": "13px",
                               "lineHeight": "1.6"},
                    ),
                ],
                style={"backgroundColor": "#1e293b", "padding": "1.1rem 1.25rem"},
            ),
        ],
        style={"border": "1px solid #334155", "borderRadius": "8px",
               "backgroundColor": "#1e293b", "marginBottom": "16px"},
    )

    return dmc.MantineProvider(
        theme=_MANTINE_THEME,
        children=dbc.Container(
            fluid=True,
            id="hydro-container",
            children=[
                intro,

                # ---- page-level state stores -------------------------------
                dcc.Store(id=_ID_SELECTED, data=DEFAULT_TIGHT_GAUGE),
                dcc.Store(id=_ID_RANGE, data=DEFAULT_DATE_RANGE),

                # ---- panels (B3 fills these containers) --------------------
                # NOTE: the coupling map and the ranked strip are rendered
                # directly into their containers here (initial layout) rather
                # than via a hydro-selected-gauge callback, so their inner
                # graph ids (hydro-map-graph / hydro-strip-graph) exist at
                # callback-registration time and BOTH can feed the selection
                # callback. The drill-down / small-multiples / gridstatus note
                # remain callback-driven (they have no click inputs).
                html.Div(id="hydro-map-container",
                         children=_render_map(DEFAULT_TIGHT_GAUGE)),
                html.Div(id="hydro-strip-container",
                         children=_render_strip(DEFAULT_TIGHT_GAUGE)),
                html.Div(id="hydro-smallmultiples-container", children=[]),
                # Drill-down (incl. its lag toggle) rendered up-front so
                # hydro-lag-toggle exists at callback-registration time.
                html.Div(id="hydro-drilldown-container",
                         children=_render_drilldown(DEFAULT_TIGHT_GAUGE, "0")),
                html.Div(id="hydro-gridstatus-note", children=[]),

                html.Div(
                    f"Data: USGS Water Data · EIA-930 / EIA 923 hydro "
                    f"generation · 2004–present",
                    className="footer",
                ),
            ],
            style={"backgroundColor": "#0f172a"},
        ),
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
_REGISTERED = False


def register_callbacks(app: Dash) -> int:
    """Wire the four panels + gridstatus note into their containers.

    Idempotent-ish: only registers once per process (a global flag guards double
    registration). Returns the number of callbacks registered.
    """
    global _REGISTERED
    if _REGISTERED:
        return 8
    _REGISTERED = True

    @app.callback(
        Output("hydro-map-container", "children"),
        Input(_ID_SELECTED, "data"),
        prevent_initial_call=False,
    )
    def _render_map_cb(selected):
        return _render_map(selected)

    @app.callback(
        Output("hydro-smallmultiples-container", "children"),
        Input(_ID_SELECTED, "data"),
        prevent_initial_call=False,
    )
    def _render_smallmultiples_cb(_selected):
        return _render_smallmultiples()

    @app.callback(
        Output("hydro-drilldown-container", "children"),
        Input(_ID_SELECTED, "data"),
        Input(_ID_LAG_TOGGLE, "value"),
        prevent_initial_call=False,
    )
    def _render_drilldown_cb(selected, lag):
        return _render_drilldown(selected, lag)

    @app.callback(
        Output("hydro-gridstatus-note", "children"),
        Input(_ID_SELECTED, "data"),
        prevent_initial_call=False,
    )
    def _render_gridnote_cb(_selected):
        return _render_gridnote()

    @app.callback(
        Output(_ID_SELECTED, "data"),
        Input(_ID_MAP_GRAPH, "clickData"),
        Input(_ID_STRIP_GRAPH, "clickData"),
        prevent_initial_call=True,
    )
    def _on_select(map_click_data, strip_click_data):
        """Single selection callback for both the map and the ranked strip.

        Under Dash (registry keyed by output string) we MUST register only ONE
        callback writing to hydro-selected-gauge.data. Both click inputs feed
        here; whatever was clicked most recently wins, otherwise keep current.
        """
        # Map click -> only tight markers (trace 1) / selected ring (trace 2).
        # Trace 0 (inert gray context) carries no entity_id and must be ignored.
        if map_click_data and map_click_data.get("points"):
            p = map_click_data["points"][0]
            if int(p.get("curveNumber", -1)) in (1, 2):
                cd = p.get("customdata")
                if cd:
                    eid = cd[0] if isinstance(cd, (list, tuple)) else cd
                    if eid:
                        return str(eid)
        # Strip click -> entity_id rides in the bar's customdata[0].
        if strip_click_data and strip_click_data.get("points"):
            p = strip_click_data["points"][0]
            cd = p.get("customdata")
            if cd:
                eid = cd[0] if isinstance(cd, (list, tuple)) else cd
                if eid:
                    return str(eid)
        return no_update

    return 8


register_callbacks(app)
