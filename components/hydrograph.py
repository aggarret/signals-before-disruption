"""hydrograph.py — Per-gauge hydrograph drill-down panel (Pass C of the Dash build).

The analytical centerpiece of the dashboard: for one gauge, one metric, and a
selectable date window it renders

  * top subplot (70%): the observed daily series (white line), the seasonal
    baseline μ (teal line) with its ±1σ / ±2σ confidence bands (teal fills),
    last year's flow as a dashed ghost, water temperature on a secondary
    right-hand axis (dashed orange, only when data exists), colored markers on
    days where |anomaly_score| >= 2.5, and small gray x's for gap days
    (completeness_score = 0) along the bottom of the plot;
  * bottom subplot (30%): the 3-day rise rate as a bar chart (positive = cyan,
    negative = amber) sharing the same date axis.

All data comes from queries.py; the figure never touches the data files
directly. The card also carries a [1M][3M][6M][1Y][All] range selector and a
stats row (current flow, anomaly z, flow percentile, record proximity).

Component IDs owned here (wire them in app.py via ``register_callbacks``):
    hydrograph-graph, hydro-stats, hydro-header,
    range-store, hydro-range-1m/3m/6m/1y/all
Component IDs read (owned by map_panel.py):
    station-store (entity_id selected by clicking the map),
    metric-dropdown (map panel's metric choice — kept in sync)

Color mapping (anomaly z, dark-slate app palette — same as map_panel):
    z <= -2.0            #f59e0b  amber            (extreme low)
    -2.0 < z <= -1.5     #7dccc4  amber-teal       (low)
    -1.5 < z <  1.5      #14b8a6  teal             (near normal)
     1.5 <= z <  2.0     #0dd4be  teal-cyan        (high)
     2.0 <= z <  3.0     #06b6d4  cyan             (very high)
     z >= 3.0            #f43f5e  crimson          (extreme high)
     null anomaly        #475569  slate gray

Theme: card surface #1e293b (figure paper), plot area #0f172a, axis text
#94a3b8, gridlines #334155.

Note on water temperature: ``queries.get_hydrograph_data`` already left-joins
the gauge's daily water_temperature averages into the returned frame (the
``water_temp`` column), so the overlay needs no second query — but it is still
guarded: the trace is only drawn when the metric is not itself water_temperature
and at least one non-null value exists (the Connecticut River's temp series
ended in 2004, so its overlay is all-null and correctly absent).

Note on the previous-year ghost: ``get_previous_year_flow`` shifts the prior
calendar year's series forward one year, so the ghost overlays exactly for
windows inside the current year. For windows that span the year boundary the
ghost covers only the in-year portion (data is not shifted across the window
itself).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, ctx, dcc, html, no_update

import queries
from components.map_panel import (
    _ID_METRIC,           # metric-dropdown (owned by map_panel)
    _ID_STATION_STORE,    # station-store   (owned by map_panel)
    METRIC_LABELS,
    METRIC_UNITS,
    PAPER_BG,             # #0f172a — plot area
    CARD_BG,              # #1e293b — figure paper / card surface
    BORDER,               # #334155 — gridlines / borders
    TEXT_MUTED,           # #94a3b8 — axis & body text
    TEXT_BRIGHT,          # #e2e8f0 — headings
    z_to_color,           # z-score -> palette hex (shared with the map)
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
Z_TEAL = "#14b8a6"        # baseline band + μ line
Z_CYAN = "#06b6d4"        # positive rise bars
Z_AMBER = "#f59e0b"       # negative rise bars + water-temp overlay
Z_SLATE = "#475569"       # previous-year ghost
Z_GRAY = "#64748b"        # gap markers, muted labels
WHITE_LINE = "#f8fafc"    # observed series

DATA_START = "2004-01-01"                 # earliest verified year (stations.csv)
_DEFAULT_RANGE = "3m"                     # default date window
_ID_DATE_STORE = "selected-date"          # app-level store (mirror of date-picker)

_ID_GRAPH = "hydrograph-graph"
_ID_STATS = "hydro-stats"
_ID_HEADER = "hydro-header"
_ID_RANGE_STORE = "range-store"
_RANGE_BUTTON_IDS = [
    "hydro-range-1m", "hydro-range-3m", "hydro-range-6m",
    "hydro-range-1y", "hydro-range-all",
]
# Ordered dict of range key -> button label. Keys are the id suffixes; order
# must stay in sync with _RANGE_BUTTON_IDS.
_RANGE_LABELS = {"1m": "1M", "3m": "3M", "6m": "6M", "1y": "1Y", "all": "All"}
_RANGE_DAYS = {"1m": 30, "3m": 90, "6m": 182, "1y": 365}

# Callback outputs: (component_id, component_property) pairs for the return
# value of register_callbacks() and for app.py wiring.
_OUTPUTS = {
    "hydrograph_figure": Output(_ID_GRAPH, "figure"),
    "hydro_stats": Output(_ID_STATS, "children"),
    "hydro_header": Output(_ID_HEADER, "children"),
    "range_store": Output(_ID_RANGE_STORE, "data"),
    "range_buttons_active": [Output(bid, "active") for bid in _RANGE_BUTTON_IDS],
    "range_buttons_outline": [Output(bid, "outline") for bid in _RANGE_BUTTON_IDS],
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _fmt_num(v: Any, metric: str) -> str:
    """Number formatting: 1 decimal for temperatures, thousands otherwise."""
    if v is None or pd.isna(v):
        return "—"
    if metric == "water_temperature":
        return f"{float(v):.1f}"
    return f"{float(v):,.0f}"


def _metric_last_date(conn: Any, metric: str) -> str:
    """Most recent date with data for a metric (dataset stats are cached)."""
    try:
        d = queries._dataset_stats(conn, metric).get("last_date")
    except Exception:
        d = None
    return str(d) if d is not None else str(pd.Timestamp.today().date())


def _range_bounds(range_key: str, last_date: Any) -> "tuple[pd.Timestamp, pd.Timestamp]":
    """(start, end) inclusive window for a range key, ending at *last_date*."""
    end = pd.Timestamp(last_date)
    if range_key == "all":
        return pd.Timestamp(DATA_START), end
    return end - pd.Timedelta(days=_RANGE_DAYS[range_key] - 1), end


def _key_for_window(start_date: Any, end_date: Any) -> str:
    """Best-matching range key for a (start, end) window (button active state)."""
    if start_date is None or end_date is None:
        return _DEFAULT_RANGE
    days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
    if days <= 31:
        return "1m"
    if days <= 95:
        return "3m"
    if days <= 190:
        return "6m"
    if days <= 370:
        return "1y"
    return "all"


def _safe_range_key(data: Any) -> str:
    """Validate range-store payload; fall back to the default window."""
    if isinstance(data, dict):
        k = data.get("range")
        if k in _RANGE_LABELS:
            return k
    return _DEFAULT_RANGE


def _station_name(conn: Any, entity_id: str) -> str:
    try:
        row = conn.execute(
            "SELECT station_name FROM stations WHERE entity_id = ?", [entity_id]
        ).fetchone()
        return str(row[0]) if row else entity_id
    except Exception:
        return entity_id


def _range_buttons(active_key: str) -> dbc.ButtonGroup:
    """[1M][3M][6M][1Y][All] selector; the active key is solid, rest outline."""
    return dbc.ButtonGroup(
        [
            dbc.Button(
                label,
                id=bid,
                size="sm",
                color="info",
                outline=(key != active_key),
                active=(key == active_key),
                style={"fontSize": "11px", "padding": "0.2rem 0.7rem",
                       "fontWeight": "600"},
            )
            for (key, label), bid in zip(_RANGE_LABELS.items(), _RANGE_BUTTON_IDS)
        ],
        size="sm",
        style={"flex": "0 0 auto"},
    )


def _header_block(entity_id: Optional[str], title: str, metric: str,
                  subtitle: str) -> html.Div:
    """Station name + entity_id · metric · window (also used by callbacks)."""
    return html.Div(
        [
            html.H6(title, style={
                "margin": "0", "color": TEXT_BRIGHT, "fontWeight": "600",
                "whiteSpace": "nowrap", "overflow": "hidden",
                "textOverflow": "ellipsis", "maxWidth": "560px",
            }),
            html.Small(subtitle, style={"color": "#64748b"}),
        ],
        style={"minWidth": "200px"},
    )


def _chip(label: str, value: str, color: Optional[str] = None,
          sub: Optional[str] = None) -> html.Div:
    """One stat tile: small caps label + bright value + optional sub-label."""
    return html.Div(
        [
            html.Div(label, style={
                "fontSize": "10px", "color": "#64748b",
                "textTransform": "uppercase", "letterSpacing": "0.05em",
            }),
            html.Div(value, style={
                "fontSize": "15px", "fontWeight": "600",
                "color": color or TEXT_BRIGHT, "fontVariantNumeric": "tabular-nums",
            }),
            html.Div(sub, style={"fontSize": "10px", "color": "#64748b"})
            if sub else None,
        ]
    )


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------
def build_hydrograph_figure(
    conn: Any,
    entity_id: str,
    metric: str = "streamflow",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> go.Figure:
    """Full two-row hydrograph figure for one gauge-metric-date window.

    Row 1 (70%): baseline bands (±1σ / ±2σ), μ line, observed series, previous
    year ghost, water-temp overlay (secondary y), |z|>=2.5 markers, gap x's.
    Row 2 (30%): 3-day rise-rate bars. Shared date x-axis.
    """
    if start_date is None or end_date is None:
        start_date, end_date = _range_bounds(_DEFAULT_RANGE,
                                             _metric_last_date(conn, metric))
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)

    df = queries.get_hydrograph_data(conn, entity_id, metric,
                                     str(start.date()), str(end.date()))
    band = queries.get_baseline_band(conn, entity_id, metric,
                                     str(start.date()), str(end.date()))
    prev = queries.get_previous_year_flow(conn, entity_id, metric, int(end.year))

    label = METRIC_LABELS.get(metric, metric)
    unit = METRIC_UNITS.get(metric, "")

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
        specs=[[{"secondary_y": True}], [{}]],
    )

    # --- baseline band: join seasonal baselines onto observed dates by DOY ---
    joined = df.copy()
    if not band.empty:
        joined["_doy"] = joined["observed_at"].dt.dayofyear
        b = band.rename(columns={"day_of_year": "_doy"})
        joined = joined.merge(b, on="_doy", how="left").drop(columns=["_doy"])

    # --- 1) outer ±2σ band --------------------------------------------------
    if not band.empty:
        lo2 = joined["mu"] - 2 * joined["sigma"]
        hi2 = joined["mu"] + 2 * joined["sigma"]
        lo1 = joined["mu"] - joined["sigma"]
        hi1 = joined["mu"] + joined["sigma"]
        for yvals in (lo2, lo1):
            fig.add_trace(go.Scatter(
                x=joined["observed_at"], y=yvals, mode="lines",
                line=dict(width=0), hoverinfo="skip", showlegend=False,
                legendgroup="band",
            ), row=1, col=1)
        for yvals, alpha in ((hi2, 0.08), (hi1, 0.15)):
            fig.add_trace(go.Scatter(
                x=joined["observed_at"], y=yvals, mode="lines",
                line=dict(width=0), fill="tonexty",
                fillcolor=f"rgba(20,184,166,{alpha})",
                hoverinfo="skip", showlegend=False, legendgroup="band",
            ), row=1, col=1)

        # --- 2) μ line --------------------------------------------------------
        fig.add_trace(go.Scatter(
            x=joined["observed_at"], y=joined["mu"], mode="lines",
            name="Seasonal baseline (μ)", legendgroup="baseline",
            line=dict(color=Z_TEAL, width=2), hoverinfo="skip",
        ), row=1, col=1)

    # --- 3) previous-year ghost ---------------------------------------------
    prev = prev[(prev["observed_at"] >= start) & (prev["observed_at"] <= end)]
    if not prev.empty:
        fig.add_trace(go.Scatter(
            x=prev["observed_at"], y=prev["average"], mode="lines",
            name="Last year", legendgroup="ghost",
            line=dict(color=Z_SLATE, width=1, dash="dash"), hoverinfo="skip",
        ), row=1, col=1)

    # --- 4) observed series --------------------------------------------------
    flow_name = "Observed flow" if metric == "streamflow" else f"Observed {label.lower()}"
    hover_texts: List[str] = []
    for _, r in df.iterrows():
        parts = [f"{r['observed_at']:%b %d, %Y}"]
        if pd.notna(r["average"]):
            parts.append(f"{label}: {_fmt_num(r['average'], metric)} {unit}".strip())
            if pd.notna(r["anomaly_score"]):
                parts.append(f"z = {r['anomaly_score']:+.2f} σ")
            if pd.notna(r["flow_percentile"]):
                parts.append(f"percentile {r['flow_percentile']:.0f}%")
        else:
            parts.append("no observation")
        hover_texts.append("<br>".join(parts))
    fig.add_trace(go.Scatter(
        x=df["observed_at"], y=df["average"], mode="lines",
        name=flow_name, legendgroup="observed",
        line=dict(color=WHITE_LINE, width=2.5),
        text=hover_texts, hoverinfo="text", connectgaps=False,
    ), row=1, col=1)

    # --- 5) water-temperature overlay (right axis) ---------------------------
    if metric != "water_temperature" and df["water_temp"].notna().any():
        fig.add_trace(go.Scatter(
            x=df["observed_at"], y=df["water_temp"], mode="lines",
            name="Water temp (°C)", legendgroup="temp",
            line=dict(color=Z_AMBER, width=1.5, dash="dash"),
            hovertemplate="Water temp: %{y:.1f} °C<extra></extra>",
        ), row=1, col=1, secondary_y=True)

    # --- 6) anomaly markers (|z| >= 2.5), colored by z palette ----------------
    anom = df[df["anomaly_score"].abs() >= 2.5]
    anom = anom[anom["average"].notna()]
    if not anom.empty:
        colors = anom["anomaly_score"].map(z_to_color).tolist()
        fig.add_trace(go.Scatter(
            x=anom["observed_at"], y=anom["average"], mode="markers",
            name="|z| ≥ 2.5 event", legendgroup="events",
            marker=dict(size=7, color=colors,
                        line=dict(color=PAPER_BG, width=0.8)),
            text=[f"z = {z:+.2f} σ" for z in anom["anomaly_score"]],
            hoverinfo="text",
        ), row=1, col=1)

    # --- y-range so gap markers sit just below the plotted content ------------
    ymin = ymax = None
    if not band.empty:
        ymin = min(joined["mu"].min(), (joined["mu"] - 2 * joined["sigma"]).min())
        ymax = max(joined["mu"].max(), (joined["mu"] + 2 * joined["sigma"]).max())
    if df["average"].notna().any():
        lo_, hi_ = df["average"].min(), df["average"].max()
        ymin = lo_ if ymin is None else min(ymin, lo_)
        ymax = hi_ if ymax is None else max(ymax, hi_)
    span = (ymax - ymin) if (ymin is not None and ymax is not None and ymax > ymin) else 1.0
    pad = 0.06 * span
    gap_y = (ymin - 0.35 * pad) if ymin is not None else 0.0

    # --- 7) gap markers (completeness_score == 0) ------------------------------
    gaps = df[df["completeness_score"] == 0]
    if not gaps.empty:
        fig.add_trace(go.Scatter(
            x=gaps["observed_at"], y=[gap_y] * len(gaps), mode="markers",
            name="Gap (no observation)", legendgroup="gaps",
            marker=dict(symbol="x", size=6, color=Z_GRAY, line=dict(width=1)),
            hoverinfo="skip", showlegend=False,
        ), row=1, col=1)

    # --- 8) rise-rate bars -----------------------------------------------------
    rise = df["rise_rate_3d"]
    bar_colors = []
    for v in rise:
        if v is None or pd.isna(v):
            bar_colors.append(Z_GRAY)
        elif float(v) >= 0:
            bar_colors.append(Z_CYAN)
        else:
            bar_colors.append(Z_AMBER)
    rise_fmt = ",.1f" if metric == "water_temperature" else ",.0f"
    fig.add_trace(go.Bar(
        x=df["observed_at"], y=rise, name="3-day rise rate",
        marker=dict(color=bar_colors, line=dict(width=0)), opacity=0.92,
        hovertemplate=(f"%{{x|%b %d, %Y}}<br>3-day rise: %{{y:{rise_fmt}}} "
                       f"{unit}/day<extra></extra>"),
    ), row=2, col=1)

    # --- layout / theme ---------------------------------------------------------
    fig.update_layout(
        template=None,
        paper_bgcolor=CARD_BG,          # figure paper matches the card surface
        plot_bgcolor=PAPER_BG,          # plot area is the darker slate
        font=dict(color=TEXT_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=11),
        margin=dict(l=72, r=64, t=16, b=56),
        height=520,
        hovermode="x unified",
        hoverlabel=dict(bgcolor=CARD_BG, font_color=TEXT_BRIGHT,
                        bordercolor=BORDER),
        legend=dict(x=1.0, y=1.0, xanchor="right", yanchor="top",
                    bgcolor="rgba(15,23,42,0.55)", bordercolor=BORDER,
                    borderwidth=1, font=dict(size=10)),
        uirevision="hydrograph-panel",
        bargap=0.15,
    )
    fig.update_xaxes(
        gridcolor=BORDER,
        zeroline=False,
        automargin=True,
    )
    fig.update_yaxes(
        gridcolor=BORDER,
        zeroline=False,
        automargin=True,
        exponentformat="none",
        showexponent="none",
        separatethousands=True,
    )

    y_tickformat = ",.1f" if metric == "water_temperature" else ",.0f"

    fig.update_yaxes(
        title_text=f"{label} ({unit})".strip(),
        title_standoff=14,
        tickformat=y_tickformat,
        nticks=6,
        row=1,
        col=1,
        secondary_y=False,
    )
    if metric != "water_temperature":
        fig.update_yaxes(
            title_text="Water temp (°C)",
            title_standoff=14,
            tickformat=",.1f",
            nticks=6,
            row=1,
            col=1,
            secondary_y=True,
            showgrid=False,
        )
    fig.update_yaxes(
        title_text=f"3-day rise ({unit}/day)".strip(),
        title_standoff=14,
        tickformat=y_tickformat,
        nticks=5,
        row=2,
        col=1,
        secondary_y=False,
    )

    # adaptive date ticks: daily -> monthly -> yearly
    span_days = (end - start).days
    if span_days <= 100:
        tfmt, dtick = "%b %d", None
    elif span_days <= 400:
        tfmt, dtick = "%b %Y", "M1"
    else:
        tfmt, dtick = "%Y", "M12"
    fig.update_xaxes(
        tickformat=tfmt,
        dtick=dtick,
        tickangle=-45,
        ticks="outside",
        nticks=8,
        row=2,
        col=1,
    )
    # ISO date strings (not pandas Timestamps) so the spec survives any JSON
    # serializer (kaleido's orjson rejects Timestamp). Shared axis drives both.
    fig.update_xaxes(range=[str(start.date()), str(end.date())], row=2, col=1)

    # zero line on the rise-rate subplot
    fig.add_hline(y=0, row=2, col=1, line=dict(color=BORDER, width=1))

    # explicit y-range on the flow subplot (keeps gap markers visible).
    # secondary_y=False: without it, update_yaxes targets BOTH axes in the cell
    # and the water-temp axis inherits the flow range (squashing the °C line).
    if ymin is not None:
        rlo = ymin - pad
        if not gaps.empty:
            rlo = min(rlo, gap_y - 0.2 * pad)
        fig.update_yaxes(range=[rlo, ymax + pad], row=1, col=1,
                         secondary_y=False)

    return fig


def _empty_figure() -> go.Figure:
    """Blank dark figure with a 'select a station' prompt."""
    fig = go.Figure()
    fig.update_layout(
        template=None,
        paper_bgcolor=CARD_BG,
        plot_bgcolor=PAPER_BG,
        font=dict(color=TEXT_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=11),
        margin=dict(l=72, r=64, t=16, b=56),
        height=520,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        annotations=[dict(
            text="Select a station from the map",
            xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
            font=dict(size=16, color=TEXT_MUTED),
        )],
    )
    return fig


# ---------------------------------------------------------------------------
# Stats row
# ---------------------------------------------------------------------------
def _build_stats(conn: Any, entity_id: str, metric: str,
                 start_date: Any, end_date: Any) -> html.Div:
    """Current flow / anomaly z / percentile / record proximity for the window."""
    df = queries.get_hydrograph_data(conn, entity_id, metric,
                                     str(pd.Timestamp(start_date).date()),
                                     str(pd.Timestamp(end_date).date()))
    unit = METRIC_UNITS.get(metric, "")
    label = METRIC_LABELS.get(metric, metric)

    cur = df[df["average"].notna()]
    if cur.empty:
        return html.Div("No observations in this range", style={
            "color": TEXT_MUTED, "fontSize": "12px", "padding": "2px 4px"})

    row = cur.iloc[-1]
    date = row["observed_at"]
    v = float(row["average"])

    zser = df["anomaly_score"].dropna()
    z = float(zser.iloc[-1]) if not zser.empty else None
    pser = df["flow_percentile"].dropna()
    pct = float(pser.iloc[-1]) if not pser.empty else None

    pc: Dict[str, Any] = {}
    try:
        pc = queries.get_personality_cards(conn, entity_id, metric,
                                           str(date.date()))
    except Exception:
        pc = {}
    prox, hmax = pc.get("record_proximity"), pc.get("historical_max")

    chips = [
        _chip(f"Current {label.lower()}",
              f"{_fmt_num(v, metric)} {unit}".strip(),
              sub=f"{date:%b %d, %Y}"),
        _chip("Anomaly",
              f"{z:+.2f} σ" if z is not None else "—",
              color=z_to_color(z) if z is not None else TEXT_MUTED),
        _chip("Flow percentile",
              f"{pct:.0f}%" if pct is not None else "—",
              sub="rank among all observations (2004–2026)"),
        _chip("Record proximity",
              f"{float(prox) * 100:.0f}%"
              if prox is not None and not pd.isna(prox) else "—",
              sub=f"record {_fmt_num(hmax, metric)} {unit}".strip()
              if hmax is not None and not pd.isna(hmax) else "of record"),
    ]
    return html.Div(chips, style={
        "display": "flex", "flexWrap": "wrap", "gap": "10px 26px"})


def _empty_stats() -> html.Div:
    return html.Div(
        "No station selected — click a gauge on the map to drill in.",
        style={"color": TEXT_MUTED, "fontSize": "12px", "padding": "2px 4px"},
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_hydrograph(
    entity_id: Optional[str] = None,
    station_name: Optional[str] = None,
    metric: str = "streamflow",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    conn: Optional[Any] = None,
) -> dbc.Card:
    """The complete hydrograph card: header + range buttons + figure + stats.

    With no entity_id it renders the empty state ("Select a station from the
    map"). start_date/end_date default to the last 90 days of data.
    """
    conn = conn or queries.get_connection()
    metric_label = METRIC_LABELS.get(metric, metric)

    if start_date is None or end_date is None:
        start_date, end_date = _range_bounds(_DEFAULT_RANGE,
                                             _metric_last_date(conn, metric))
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)

    if entity_id is None:
        eid, title = None, "Hydrograph"
        range_key = _DEFAULT_RANGE
        subtitle = "Select a station from the map"
        fig, stats = _empty_figure(), _empty_stats()
    else:
        eid = str(entity_id)
        if station_name is None:
            station_name = _station_name(conn, eid)
        range_key = _key_for_window(start, end)
        title = station_name
        subtitle = f"{eid} · {metric_label} · {start:%Y-%m-%d} → {end:%Y-%m-%d}"
        fig = build_hydrograph_figure(conn, eid, metric, start, end)
        stats = _build_stats(conn, eid, metric, start, end)

    header = dbc.CardHeader(
        html.Div(
            [
                html.Div(
                    id=_ID_HEADER,
                    children=_header_block(eid, title, metric, subtitle),
                    style={"flex": "1 1 auto", "minWidth": "240px"},
                ),
                _range_buttons(range_key),
            ],
            style={"display": "flex", "alignItems": "center",
                   "gap": "12px", "flexWrap": "wrap"},
        ),
        style={"backgroundColor": CARD_BG, "borderBottom": f"1px solid {BORDER}"},
    )

    return dbc.Card(
        [
            header,
            dbc.CardBody(
                [
                    dcc.Graph(
                        id=_ID_GRAPH,
                        figure=fig,
                        config={"displayModeBar": False},
                        style={"height": "520px", "width": "100%"},
                    ),
                    html.Div(id=_ID_STATS, children=stats,
                             style={"padding": "0.4rem 0.1rem 0.1rem"}),
                ],
                style={"padding": "0.5rem 0.75rem", "backgroundColor": CARD_BG},
            ),
            dcc.Store(id=_ID_RANGE_STORE, data={"range": range_key}),
        ],
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "overflow": "hidden"},
    )


# ---------------------------------------------------------------------------
# Callbacks (register on the app; NOT wired into app.py yet)
# ---------------------------------------------------------------------------
def register_callbacks(app: Dash) -> Dict[str, Any]:
    """Register this panel's callbacks on *app* and return the outputs.

    Returns the _OUTPUTS dict so app.py can introspect the IDs. Callbacks are
    safe to register before the rest of the layout exists — Dash resolves
    outputs by id at request time.
    """

    @app.callback(
        [Output(_ID_GRAPH, "figure"),
         Output(_ID_STATS, "children"),
         Output(_ID_HEADER, "children")],
        [Input(_ID_STATION_STORE, "data"),
         Input(_ID_METRIC, "value"),
         Input(_ID_RANGE_STORE, "data"),
         Input(_ID_DATE_STORE, "data")],
        prevent_initial_call=True,
    )
    def _update_hydrograph(entity_id, metric, range_data, selected_date):
        """Rebuild figure + stats + header on station/metric/range/date changes."""
        conn = queries.get_connection()  # per-call: callbacks run on worker threads
        metric = metric or "streamflow"
        range_key = _safe_range_key(range_data)
        if not entity_id:
            return (_empty_figure(), _empty_stats(),
                    _header_block(None, "Hydrograph", metric,
                                  "Select a station from the map"))
        eid = str(entity_id)
        # End the window at the selected date (fallback to dataset's latest).
        end_date = selected_date or _metric_last_date(conn, metric)
        start, end = _range_bounds(range_key, end_date)
        fig = build_hydrograph_figure(conn, eid, metric, start, end)
        stats = _build_stats(conn, eid, metric, start, end)
        name = _station_name(conn, eid)
        metric_label = METRIC_LABELS.get(metric, metric)
        subtitle = f"{eid} · {metric_label} · {start:%Y-%m-%d} → {end:%Y-%m-%d}"
        return fig, stats, _header_block(eid, name, metric, subtitle)

    @app.callback(
        [Output(_ID_RANGE_STORE, "data")]
        + [Output(b, "active") for b in _RANGE_BUTTON_IDS]
        + [Output(b, "outline") for b in _RANGE_BUTTON_IDS],
        [Input(b, "n_clicks") for b in _RANGE_BUTTON_IDS],
        prevent_initial_call=True,
    )
    def _on_range_click(*args):
        """Store the clicked range key and sync the button active/outline states.

        _range_buttons renders each button with active=(key == range_key) and
        outline=(key != range_key), so a toggle must update BOTH props: the
        selected button becomes solid (outline=False + active=True) while every
        other button returns to outline style (outline=True + active=False).
        Updating only `active` leaves the previously active button with its
        layout-time outline=False, so it keeps its solid fill and looks
        selected together with the new one.

        range-store is deliberately NOT an input here: this callback is the
        only writer, and a self-referential input made every store write
        re-fire the callback — a re-fire could land after the user's next
        click and revert the selection. The hydrograph figure still tracks the
        store (its own callback listens to range-store.data).
        """
        key = _DEFAULT_RANGE
        if ctx.triggered:
            # triggered_prop_ids maps "component.prop" -> component id. Pick the
            # LAST button whose n_clicks fired so rapid double-clicks (a second
            # click landing before the first round-trip finishes, batching both
            # triggers into one request) still resolve to the newest button.
            clicked = [
                cid for prop, cid in ctx.triggered_prop_ids.items()
                if cid in _RANGE_BUTTON_IDS and prop.endswith(".n_clicks")
            ]
            if clicked:
                key = str(clicked[-1]).split("-")[-1]
        actives = [k == key for k in _RANGE_LABELS]
        # Flat output list (one value per declared output): the range-store
        # payload, then each button's active flag, then each button's outline
        # flag, both in _RANGE_LABELS order.
        return [{"range": key}] + actives + [not a for a in actives]

    return _OUTPUTS


# ---------------------------------------------------------------------------
# Self-test: python3 components/hydrograph.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    last = _metric_last_date(conn, "streamflow")

    cases = [
        ("USGS-01184000", "CONNECTICUT RIVER AT THOMPSONVILLE, CT",
         "3m", "_test_hydrograph.html"),
        ("USGS-14105700", "COLUMBIA RIVER AT THE DALLES, OR",
         "1y", "_test_hydrograph_year.html"),
    ]

    all_ok = True
    for eid, name, rkey, out in cases:
        start, end = _range_bounds(rkey, last)
        fig = build_hydrograph_figure(conn, eid, "streamflow", start, end)

        trace_names = [t.name for t in fig.data]

        def _count(nm: str) -> int:
            idx = trace_names.index(nm) if nm in trace_names else -1
            return len(fig.data[idx].x) if idx >= 0 else 0

        n_anom = _count("|z| ≥ 2.5 event")
        n_gaps = _count("Gap (no observation)")
        n_traces = len(fig.data)
        temp_ok = "Water temp (°C)" in trace_names
        prev_ok = "Last year" in trace_names

        card = render_hydrograph(eid, name, "streamflow", start, end, conn)
        assert isinstance(card, dbc.Card), "render_hydrograph() must return a dbc.Card"

        out_path = os.path.join(_ROOT, out)
        fig.write_html(out_path, include_plotlyjs="cdn", full_html=True,
                       auto_open=False)

        print(f"--- {eid} ({name})  [{rkey}]  {start:%Y-%m-%d} → {end:%Y-%m-%d}")
        print(f"    traces              : {n_traces} -> {trace_names}")
        print(f"    anomaly markers     : {n_anom}")
        print(f"    gap markers         : {n_gaps}")
        print(f"    temp overlay        : {'YES' if temp_ok else 'no'}")
        print(f"    previous-year ghost : {'YES' if prev_ok else 'no'}")
        print(f"    html written        : {out_path} ({os.path.getsize(out_path):,} bytes)")
        all_ok = all_ok and os.path.exists(out_path) and n_traces >= 5

    # empty state card
    card0 = render_hydrograph(None, None, "streamflow", conn=conn)
    assert isinstance(card0, dbc.Card), "empty-state card must be a dbc.Card"

    # callback registration smoke test (no server run, just wiring)
    app = Dash(__name__)
    outs = register_callbacks(app)
    try:
        n_cb = len(app.callback_map)
    except AttributeError:
        n_cb = len(app._callback_list)
    print(f"--- empty-state card  : OK")
    print(f"    callbacks          : {n_cb} registered (expect >= 2)")
    print(f"    outputs contract   : {sorted(outs.keys())}")
    all_ok = all_ok and n_cb >= 2

    print("ALL CHECKS PASSED" if all_ok else "CHECK FAILURE")
