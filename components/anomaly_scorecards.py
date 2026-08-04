"""anomaly_scorecards.py — Top-level anomaly scorecard + monthly bar chart.

Two global (unfiltered) visualizations that sit ABOVE the sticky metric/date
filter bar, summarizing the whole dataset across ALL metrics, ALL regions and
ALL dates:

  * Anomaly Scorecard (left, width=4) — ONE card showing the #1 date with the
    most anomalous events (|z| >= 2.5 gauges). Hovering the card opens a
    CSS-only dropdown ranking the top-10 anomalous dates (#2 - #10) below it,
    each with its event count and per-metric breakdown.
  * Monthly Anomaly Bar Chart (right, width=8) — total anomalous events per
    month, teal bars with high-anomaly months (>= 50 events) in crimson.

Both are computed once at startup from the immutable parquet dataset and are
NOT re-rendered by any callback — the metric/date filter deliberately does not
touch them (same pattern as the header stats). The section is NOT sticky: it
scrolls away normally; only the filter bar below it stays sticky.

Layout contract:
    render_anomaly_scorecards(conn=None) -> dbc.Row
    (one dbc.Col width=4 scorecard, one dbc.Col width=8 chart)
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import dcc, html

import queries
from components.map_panel import (
    METRIC_LABELS,
    BORDER,
    PAPER_BG,
    TEXT_MUTED,
    Z_CRIMSON,
    Z_TEAL,
)

# How many anomalous dates to rank in the hover dropdown (incl. the #1 card).
_N_TOP_DATES = 10

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_date(date: Any) -> str:
    """'2004-09-19' -> 'Sep 19, 2004'."""
    return pd.Timestamp(str(date)).strftime("%b %d, %Y")


def _metric_summary(top: Dict[str, Any]) -> str:
    """Per-metric breakdown line, e.g. 'Streamflow 17 · Gage Height 5'."""
    return " · ".join(
        f"{METRIC_LABELS.get(m, m)} {int(c)}"
        for m, c in top.get("metrics_breakdown", {}).items()
        if int(c) > 0
    )


# ---------------------------------------------------------------------------
# Scorecard piece
# ---------------------------------------------------------------------------
def _scorecard(top: Dict[str, Any], others: List[Dict[str, Any]]) -> dbc.Col:
    """One scorecard: the #1 anomalous date visible; hover reveals ranks 2-N."""
    date_fmt = _fmt_date(top["date"])
    count = int(top["total_events"])

    items = []
    for rank, d in enumerate(others, start=2):
        items.append(
            html.Div(
                className="anomaly-dropdown-item",
                children=[
                    html.Div(
                        [
                            html.Div(
                                f"#{rank}  {_fmt_date(d['date'])}",
                                className="anomaly-dropdown-station",
                            ),
                            html.Div(
                                _metric_summary(d),
                                className="anomaly-dropdown-region",
                            ),
                        ],
                        style={"minWidth": "0", "overflow": "hidden"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                f"{int(d['total_events']):,}",
                                className="anomaly-dropdown-score",
                            ),
                            html.Div(
                                "anomalous events",
                                style={"color": "#64748b", "fontSize": "10px",
                                       "textAlign": "right"},
                            ),
                        ],
                        style={"textAlign": "right"},
                    ),
                ],
            )
        )

    return dbc.Col(
        html.Div(
            className="anomaly-scorecard-wrapper",
            children=[
                dbc.Card(
                    html.Div(
                        className="anomaly-scorecard",
                        children=[
                            html.Div(date_fmt, className="anomaly-scorecard-date"),
                            html.Div(f"{count:,}", className="anomaly-scorecard-value"),
                            html.Div("anomalous events", className="anomaly-scorecard-label"),
                            html.Div(
                                f"rank #1 of {_N_TOP_DATES} · all metrics & regions",
                                style={"fontSize": "10px", "color": "#64748b",
                                       "marginTop": "6px"},
                            ),
                        ],
                    )
                ),
                html.Div(
                    className="anomaly-scorecard-dropdown",
                    children=[
                        html.Div(
                            f"Top {_N_TOP_DATES} Anomalous Dates",
                            className="anomaly-dropdown-title",
                        ),
                    ] + items,
                ),
            ],
        ),
        xs=12,
    )


def _monthly_chart(conn: Any) -> html.Div:
    """Monthly anomalous-events bar chart (all metrics, all regions)."""
    df = queries.get_monthly_anomaly_counts(conn)

    fig = go.Figure()
    if not df.empty:
        df = df.sort_values("year_month").reset_index(drop=True)

        # Dynamic threshold: mean + 1σ (months above this are statistically
        # elevated — fits the anomaly theme of the dashboard).
        events = df["total_events"].astype(float)
        threshold = float(events.mean() + events.std())
        threshold_lbl = f"{threshold:.0f}"

        # Split into two traces so Plotly builds a native legend:
        #   teal    = normal months (< threshold)
        #   crimson = high-anomaly months (>= threshold)
        y_normal = [v if v < threshold else None for v in events]
        y_high   = [v if v >= threshold else None for v in events]
        extreme  = df["total_extreme"].tolist()

        fig.add_trace(go.Bar(
            name=f"Normal (< {threshold_lbl} events)",
            x=df["year_month"],
            y=y_normal,
            customdata=extreme,
            marker_color=Z_TEAL,
            hovertemplate=(
                "<b>%{x|%b %Y}</b><br>"
                "%{y:,.0f} anomalous events<br>"
                "%{customdata:,.0f} extreme entities<extra></extra>"
            ),
        ))
        fig.add_trace(go.Bar(
            name=f"High (≥ {threshold_lbl} events)",
            x=df["year_month"],
            y=y_high,
            customdata=extreme,
            marker_color=Z_CRIMSON,
            hovertemplate=(
                "<b>%{x|%b %Y}</b><br>"
                "%{y:,.0f} anomalous events<br>"
                "%{customdata:,.0f} extreme entities<extra></extra>"
            ),
        ))

    fig.update_layout(
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PAPER_BG,
        font={"color": TEXT_MUTED, "size": 11, "family": "Inter, sans-serif"},
        margin={"l": 40, "r": 12, "t": 8, "b": 24},
        height=160,
        xaxis={"gridcolor": BORDER, "tickformat": "%b %Y"},
        yaxis={"gridcolor": BORDER, "title": {"text": "Anomalous Events",
                                               "font": {"size": 10}}},
        showlegend=True,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1.0,
            "font": {"size": 10, "color": TEXT_MUTED},
            "bgcolor": PAPER_BG,
            "bordercolor": BORDER,
            "borderwidth": 0,
        },
        barmode="stack",
    )

    return html.Div(
        className="anomaly-monthly-chart-container",
        children=[
            html.Div(
                "Monthly Anomalous Events",
                style={"fontSize": "11px", "textTransform": "uppercase",
                       "letterSpacing": "0.05em", "color": "#64748b",
                       "fontWeight": "600", "padding": "4px 8px 0"},
            ),
            dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"height": "160px"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_anomaly_scorecards(conn: Any = None) -> dbc.Row:
    """Top-level anomaly scorecard + monthly bar chart.

    Not affected by metric/date filters — global anomaly summary across ALL
    metrics, ALL regions, ALL dates. Static (no callbacks): computed once at
    startup from the immutable parquet dataset. NOT sticky: the section
    scrolls away normally; only the filter bar below it stays sticky.
    """
    conn = conn or queries.get_connection()

    top_dates = queries.get_top_anomaly_dates(conn, n=_N_TOP_DATES)

    if top_dates:
        card = _scorecard(top_dates[0], top_dates[1:])
    else:
        # Empty dataset: placeholder card keeps the row's shape.
        card = dbc.Col(
            dbc.Card(
                html.Div(
                    className="anomaly-scorecard",
                    children=[
                        html.Div("—", className="anomaly-scorecard-date"),
                        html.Div("0", className="anomaly-scorecard-value"),
                        html.Div("anomalous events", className="anomaly-scorecard-label"),
                    ],
                )
            ),
            xs=12,
        )

    return dbc.Row(
        class_name="anomaly-scorecard-row g-2",
        children=[
            dbc.Col(width=4, children=[dbc.Row([card], class_name="g-2")]),
            dbc.Col(width=8, children=[_monthly_chart(conn)]),
        ],
    )


# ---------------------------------------------------------------------------
# Self-test: python3 components/anomaly_scorecards.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    row = render_anomaly_scorecards(conn)

    assert isinstance(row, dbc.Row), "render_anomaly_scorecards() must return a dbc.Row"
    cols = list(row.children)
    assert len(cols) == 2, f"expected 2 columns, got {len(cols)}"

    # Left column: exactly ONE scorecard (the #1 date), full width of the col.
    inner = cols[0].children[0]          # inner dbc.Row
    cards = list(inner.children)
    assert len(cards) == 1, f"expected 1 scorecard, got {len(cards)}"
    wrapper = cards[0].children          # wrapper Div (card + dropdown)
    visible = wrapper.children[0].children.children
    date_el, value_el = visible[0], visible[1]
    print(f"return type   : {type(row).__name__} (dbc.Row)")
    print(f"columns       : {len(cols)} (left scorecard / right chart)")
    print(f"visible card  : {date_el.children!r}  {value_el.children!r} anomalous events")

    # Dropdown: ranks 2..N of the top-N dates.
    dropdown = wrapper.children[1]
    title = dropdown.children[0].children
    items = [c for c in dropdown.children if c.className == "anomaly-dropdown-item"]
    top_dates = queries.get_top_anomaly_dates(conn, n=_N_TOP_DATES)
    expected_items = max(0, len(top_dates) - 1)
    print(f"dropdown      : {title!r} — {len(items)} ranked date rows "
          f"(expected {expected_items})")
    assert len(items) == expected_items, "dropdown item count mismatch"
    for it in items[:3]:
        left = it.children[0].children
        right = it.children[1].children
        print(f"  - {left[0].children:<34} {right[0].children:>4} events  "
              f"[{left[1].children}]")

    mc = queries.get_monthly_anomaly_counts(conn)
    print(f"monthly rows  : {len(mc)} months "
          f"({mc['year_month'].min():%Y-%m} → {mc['year_month'].max():%Y-%m})")

    print("ALL CHECKS PASSED")
