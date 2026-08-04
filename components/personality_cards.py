"""personality_cards.py — Per-gauge "personality" cards (Pass D of the Dash build).

Three cards describing how a single river *behaves* at a point in time:

  * Flashiness       Richards-Baker index (3 decimals) + regional rank
                     "X of N" + qualitative class (Low < 0.1, Moderate
                     0.1–0.3, High 0.3–0.6, Very High > 0.6)
  * Flow Percentile  where today's flow sits vs. 2004–2026 history, drawn as
                     a horizontal 0–100 progress bar (dcc.Graph) whose color
                     follows the gauge's anomaly z-score palette
  * Record Proximity current value ÷ historical max, drawn as a progress bar
                     graded amber < 25% · teal 25–75% · cyan 75–95% ·
                     crimson > 95%, with the historical max shown

Numbers come from queries.get_personality_cards(); the anomaly z used to color
the percentile bar comes from queries.get_map_data (the same cached date slice
the map colors by), so the palette is consistent with every other panel.

No callbacks — app.py re-renders the row when the selection changes.

Layout contract:
    render_personality_cards(conn, entity_id, metric, date)
        -> dbc.Row of 3 dbc.Col cards (xs=12 md=6 lg=4)
"""

from __future__ import annotations

import os
import sys
from typing import Any, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import dcc, html

import queries
from components.map_panel import (
    get_default_date,
    METRIC_LABELS,
    METRIC_UNITS,
    CARD_BG,
    TEXT_BRIGHT,
    TEXT_MUTED,
    Z_AMBER,
    Z_CYAN,
    Z_CRIMSON,
    Z_TEAL,
    z_to_color,
)

# ---------------------------------------------------------------------------
# Qualitative helpers
# ---------------------------------------------------------------------------
def _flashiness_class(fi: float) -> "tuple[str, str]":
    """(label, css badge class) for a Richards-Baker index."""
    if fi < 0.1:
        return "Low", "badge-teal"
    if fi < 0.3:
        return "Moderate", "badge-cyan"
    if fi < 0.6:
        return "High", "badge-amber"
    return "Very High", "badge-crimson"


def _proximity_color(pct: float) -> str:
    """Record-proximity bar color: amber/teal/cyan/crimson by closeness."""
    if pct < 25:
        return Z_AMBER
    if pct < 75:
        return Z_TEAL
    if pct < 95:
        return Z_CYAN
    return Z_CRIMSON


def _fmt_value(v: Any, metric: str) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if metric == "water_temperature":
        return f"{v:.1f} °C"
    return f"{v:,.0f} {METRIC_UNITS.get(metric, '')}".strip()


# ---------------------------------------------------------------------------
# Figure helpers
# ---------------------------------------------------------------------------
def _progress_figure(pct: float, color: str, height: int = 46) -> go.Figure:
    """Tiny dark-slate horizontal bar from 0 → 100 (progress-bar look)."""
    pct = min(max(float(pct), 0.0), 100.0)
    fig = go.Figure(go.Bar(
        x=[pct],
        orientation="h",
        width=0.32,
        marker=dict(color=color, line=dict(width=0)),
        hoverinfo="skip",
        showlegend=False,
    ))
    fig.update_layout(
        template=None,
        paper_bgcolor=CARD_BG,
        plot_bgcolor=CARD_BG,
        height=height,
        margin=dict(l=0, r=0, t=2, b=2),
        xaxis=dict(range=[0, 100], visible=False),
        yaxis=dict(visible=False),
        bargap=0.0,
    )
    return fig


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------
def _personality_card(icon: str, label: str, value: Any, sub: Any,
                      color: Optional[str] = None,
                      extra: Any = None) -> dbc.Col:
    """One personality column: icon, label, value, sub-text, optional extra."""
    children: List[Any] = [
        html.Div(icon, style={"fontSize": "20px", "marginBottom": "2px"}),
        html.Div(label, className="kpi-label"),
        html.Div(value, className="kpi-value",
                 style={"color": color} if color else None),
    ]
    if sub:
        children.append(html.Div(sub, className="kpi-sub"))
    if extra is not None:
        children.append(extra)
    return dbc.Col(
        dbc.Card(html.Div(children, className="personality-card")),
        xs=12, md=6, lg=4,
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_personality_cards(
    conn: Any = None,
    entity_id: Optional[str] = None,
    metric: str = "streamflow",
    date: Optional[str] = None,
) -> dbc.Row:
    """Three 'how this river behaves' cards for one gauge-metric-date."""
    conn = conn or queries.get_connection()
    date = str(date or get_default_date(conn))

    if entity_id is None:
        return dbc.Row([
            dbc.Col(
                html.Div(
                    "Select a station on the map to see its personality.",
                    style={"color": TEXT_MUTED, "fontSize": "12px",
                           "padding": "8px 4px"},
                ),
                width=12,
            )
        ])

    eid = str(entity_id)
    pc = queries.get_personality_cards(conn, eid, metric, date)

    # --- card 1: flashiness ---------------------------------------------------
    fi = pc.get("flashiness_index")
    rank, n = pc.get("flashiness_rank"), pc.get("n_gauges")
    region = pc.get("region")
    year = int(str(date)[:4])
    if fi is None:
        c1 = _personality_card("🌊", "Flashiness", "—",
                               f"no qualifying observations in {year}")
    else:
        cls, badge_cls = _flashiness_class(float(fi))
        if rank is not None and n:
            sub = f"rank {rank} of {n} in {region} · {year} calendar year"
        else:
            sub = f"{region or 'regional rank unavailable'} · {year} calendar year"
        badge = html.Span(cls, className=f"anomaly-badge {badge_cls}")
        c1 = _personality_card(
            "🌊", "Flashiness",
            f"{float(fi):.3f}", sub,
            color=TEXT_BRIGHT,
            extra=html.Div(badge, style={"marginTop": "6px", "height": "46px", "display": "flex", "alignItems": "center", "justifyContent": "center"}),
        )

    # --- card 2: flow percentile ----------------------------------------------
    # Bar color follows the anomaly z palette (same slice the map colors by).
    z = None
    md = queries.get_map_data(conn, metric, date)
    if not md.empty:
        mrow = md[md["entity_id"] == eid]
        if not mrow.empty:
            z = mrow.iloc[0].get("anomaly_score")
    bar_color = z_to_color(z) if (z is not None and not pd.isna(z)) else Z_TEAL

    pct = pc.get("flow_percentile")
    if pct is None:
        c2 = _personality_card("📊", "Flow Percentile", "—",
                               "no observation today")
    else:
        pct_v = float(pct)
        graph = dcc.Graph(
            figure=_progress_figure(pct_v, bar_color),
            config={"displayModeBar": False},
            style={"height": "46px", "marginTop": "6px"},
        )
        c2 = _personality_card(
            "📊", "Flow Percentile",
            f"{pct_v:.1f}%", "rank among all observations (2004–2026)",
            color=bar_color,
            extra=graph,
        )

    # --- card 3: record proximity ---------------------------------------------
    prox = pc.get("record_proximity")
    hmax = pc.get("historical_max")
    if prox is None:
        c3 = _personality_card("🏆", "Record Proximity", "—",
                               "no observation today")
    else:
        pct_v = float(prox) * 100.0
        color = _proximity_color(pct_v)
        graph = dcc.Graph(
            figure=_progress_figure(pct_v, color),
            config={"displayModeBar": False},
            style={"height": "46px", "marginTop": "6px"},
        )
        c3 = _personality_card(
            "🏆", "Record Proximity",
            f"{pct_v:.1f}%", f"record {_fmt_value(hmax, metric)}",
            color=color,
            extra=graph,
        )

    return dbc.Row([c1, c2, c3], class_name="g-2")


# ---------------------------------------------------------------------------
# Self-test: python3 components/personality_cards.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    d = str(get_default_date(conn))
    eid = "USGS-01184000"

    row = render_personality_cards(conn, eid, "streamflow", d)
    assert isinstance(row, dbc.Row), "must return a dbc.Row"
    cols = list(row.children)
    assert len(cols) == 3, f"expected 3 cards, got {len(cols)}"

    print(f"date used      : {d}")
    print(f"station        : {eid}")
    print(f"return type    : {type(row).__name__} (dbc.Row)")
    print(f"cards          : {len(cols)}")

    pc = queries.get_personality_cards(conn, eid, "streamflow", d)
    fi = pc["flashiness_index"]
    cls, _ = _flashiness_class(float(fi))
    print(f"flashiness     : {fi:.3f} (rank {pc['flashiness_rank']} of "
          f"{pc['n_gauges']} in {pc['region']}) -> class {cls!r}")
    print(f"percentile     : {float(pc['flow_percentile']):.1f}%")
    print(f"record prox    : {float(pc['record_proximity']) * 100:.1f}% "
          f"(record {float(pc['historical_max']):,.0f})")

    # card 1 spot checks
    c1 = cols[0].children.children
    assert c1.children[2].children == f"{float(fi):.3f}", "flashiness value"

    # empty-state path
    row0 = render_personality_cards(conn, None, "streamflow", d)
    assert isinstance(row0, dbc.Row)

    # water-temperature path (units/formatting)
    row_t = render_personality_cards(conn, eid, "water_temperature", d)
    assert isinstance(row_t, dbc.Row) and len(row_t.children) == 3

    print("ALL CHECKS PASSED")
