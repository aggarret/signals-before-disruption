"""fastest_risers_table.py — Top-5 risers for a selected region (Pass D).

Ranked table of the five fastest-rising gauges inside one region for a
metric-date: Rank, Station, Rise Rate (unit/day, cyan when rising, amber when
falling), Current Flow. The region comes from the region table's row selection
(`region-table-store`, written by components/region_table.py); metric and date
follow the map panel's dropdowns. Data comes from queries.get_fastest_risers().

Dark theme matches the region table (shared _TABLE_HEADER/_TABLE_CELL imported
from region_table.py); the .fastest-risers-table CSS class supplies row hover.

Component IDs owned here:
    fastest-risers-table   dash_table.DataTable
    fastest-risers-sub     header subtitle (updated by register_callbacks)

Layout contract:
    render_fastest_risers(conn, region, metric, date) -> dbc.Card
    register_callbacks(app) -> {"fastest_risers_data": Output(...),
                                "fastest_risers_columns": Output(...),
                                "fastest_risers_sub": Output(...)}
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, dash_table, html

import queries
from components.map_panel import (
    _ID_METRIC,          # metric-dropdown (owned by map_panel)
    _ID_DATE,            # date-picker     (owned by map_panel)
    get_default_date,
    METRIC_LABELS,
    METRIC_UNITS,
    TEXT_BRIGHT,
    Z_AMBER,
    Z_CYAN,
    Z_TEAL,
)
from components.region_table import (
    _ID_REGION_SELECT_STORE,   # region-table-store (owned by region_table)
    _TABLE_HEADER,
    _TABLE_CELL,
)

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------
_ID_TABLE = "fastest-risers-table"
_ID_BODY = "fastest-risers-body"
_ID_SUB = "fastest-risers-sub"

_OUTPUTS = {
    "fastest_risers_body": Output(_ID_BODY, "children"),
    "fastest_risers_sub": Output(_ID_SUB, "children"),
}

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _truncate(name: Any, n: int = 30) -> str:
    name = str(name)
    if len(name) <= n:
        return name
    return name[: n - 1].rstrip() + "…"


def _fmt_rise(rate: Any, metric: str) -> str:
    """Rise rate with explicit +/− sign and a per-day unit."""
    if rate is None or pd.isna(rate):
        return "—"
    rate = float(rate)
    body = f"{rate:+.1f}" if metric == "water_temperature" else f"{rate:+,.0f}"
    unit = METRIC_UNITS.get(metric, "")
    return f"{body} {unit}/day".strip()


def _fmt_value(v: Any, metric: str) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if metric == "water_temperature":
        return f"{v:.1f} °C"
    return f"{v:,.0f} {METRIC_UNITS.get(metric, '')}".strip()


# ---------------------------------------------------------------------------
# Data / table builders
# ---------------------------------------------------------------------------
def _risers_data(conn: Any, region: Optional[str], metric: str,
                 date: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(rows, columns) for the DataTable; empty rows when no region chosen."""
    unit = METRIC_UNITS.get(metric, "")
    rise_col = f"Rise Rate ({unit}/day)".strip()
    columns = [
        {"name": "Rank", "id": "Rank"},
        {"name": "Station", "id": "Station"},
        {"name": rise_col, "id": "Rise Rate"},
        {"name": "Current Flow", "id": "Current Flow"},
    ]
    if not region:
        return [], columns

    risers = queries.get_fastest_risers(conn, str(region), metric, date)
    rows = []
    for i, r in enumerate(risers):
        rows.append({
            "Rank": i + 1,
            "Station": _truncate(r.get("station_name"), 30),
            "Rise Rate": _fmt_rise(r.get("rise_rate_3d"), metric),
            "Current Flow": _fmt_value(r.get("value"), metric),
        })
    return rows, columns


def _data_table(data: List[Dict[str, Any]],
                columns: List[Dict[str, Any]]) -> dash_table.DataTable:
    """Dark-themed DataTable; rank column highlighted, rise colored ±."""
    return dash_table.DataTable(
        id=_ID_TABLE,
        columns=columns,
        data=data,
        page_action="none",                 # max 5 rows, no pagination
        style_header=_TABLE_HEADER,
        style_data=_TABLE_CELL,
        style_cell={"textAlign": "left", "padding": "6px 10px",
                    "whiteSpace": "normal"},
        style_cell_conditional=[
            {"if": {"column_id": "Rank"}, "textAlign": "center", "width": "48px"},
            {"if": {"column_id": "Current Flow"}, "textAlign": "right"},
            {"if": {"column_id": "Rise Rate"}, "textAlign": "right"},
        ],
        style_data_conditional=[
            # rank column highlighted in teal
            {"if": {"column_id": "Rank"},
             "color": Z_TEAL, "fontWeight": "700",
             "backgroundColor": "rgba(20, 184, 166, 0.12)"},
            # rise-rate sign coloring: cyan rising / amber falling
            {"if": {"column_id": "Rise Rate",
                    "filter_query": '`Rise Rate` contains "+"'},
             "color": Z_CYAN, "fontWeight": "600"},
            {"if": {"column_id": "Rise Rate",
                    "filter_query": '`Rise Rate` contains "-"'},
             "color": Z_AMBER, "fontWeight": "600"},
        ],
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def _body_children(conn: Any, region: Optional[str], metric: str,
                   date: str) -> Any:
    """Card-body contents: the DataTable, or a placeholder before a region."""
    data, columns = _risers_data(conn, region, metric, date)
    if data:
        return _data_table(data, columns)
    return html.Div(
        "Select a region row above to see its top risers",
        style={"color": "#94a3b8", "fontSize": "12px", "padding": "10px 4px"},
    )


def render_fastest_risers(
    conn: Any = None,
    region: Optional[str] = None,
    metric: str = "streamflow",
    date: Optional[str] = None,
) -> dbc.Card:
    """Fastest-risers card for a region (placeholder when region is None)."""
    conn = conn or queries.get_connection()
    date = str(date or get_default_date(conn))
    metric_label = METRIC_LABELS.get(metric, metric)

    subtitle = (
        f"{region} · {metric_label} · {date}"
        if region else
        "Select a region row above to see its top risers"
    )

    header = dbc.CardHeader(
        html.Div(
            [
                html.H6("Fastest Risers", style={
                    "margin": "0", "color": TEXT_BRIGHT, "fontWeight": "600",
                }),
                html.Small(id=_ID_SUB, children=subtitle,
                           style={"color": "#64748b"}),
            ]
        )
    )

    body = html.Div(
        id=_ID_BODY,
        children=_body_children(conn, region, metric, date),
    )

    return dbc.Card(
        [header, dbc.CardBody(body, style={"padding": "0.5rem"})],
        className="fastest-risers-table",
    )


# ---------------------------------------------------------------------------
# Callbacks (register on the app; wire in app.py via register_callbacks)
# ---------------------------------------------------------------------------
def register_callbacks(app: Dash) -> Dict[str, Any]:
    """Refresh the risers table on region selection / metric / date changes.

    Reads `region-table-store` (written by components/region_table.py) and the
    map panel's metric-dropdown + date-picker. Returns the _OUTPUTS dict.
    """
    @app.callback(
        [Output(_ID_BODY, "children"),
         Output(_ID_SUB, "children")],
        [Input(_ID_REGION_SELECT_STORE, "data"),
         Input(_ID_METRIC, "value"),
         Input(_ID_DATE, "value")],
        prevent_initial_call=True,
    )
    def _update_risers(region, metric, date):
        conn = queries.get_connection()
        metric = metric or "streamflow"
        date = str(date or get_default_date(conn))
        metric_label = METRIC_LABELS.get(metric, metric)
        sub = (f"{region} · {metric_label} · {date}" if region
               else "Select a region row above to see its top risers")
        return _body_children(conn, region, metric, date), sub

    return dict(_OUTPUTS)


# ---------------------------------------------------------------------------
# Self-test: python3 components/fastest_risers_table.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    d = str(get_default_date(conn))

    # populated path
    card = render_fastest_risers(conn, "Northeast/Mid-Atlantic",
                                 "streamflow", d)
    assert isinstance(card, dbc.Card), "must return a dbc.Card"
    body = card.children[1].children      # CardBody -> Div#fastest-risers-body
    dt = body.children                    # DataTable (or placeholder Div)
    print(f"date used      : {d}")
    print(f"return type    : {type(card).__name__} (dbc.Card, class="
          f"{card.className!r})")
    if isinstance(dt, dash_table.DataTable):
        print(f"risers         : {len(dt.data)}")
        for r in dt.data:
            print(f"   #{r['Rank']} {r['Station']!r:<34} "
                  f"{r['Rise Rate']!r:<22} {r['Current Flow']!r}")
        assert dt.data[0]["Rank"] == 1
        assert dt.id == _ID_TABLE
    else:
        print("risers         : none (placeholder)")

    # empty-state path (no region yet)
    card0 = render_fastest_risers(conn, None, "streamflow", d)
    assert isinstance(card0, dbc.Card)
    body0 = card0.children[1].children
    assert not isinstance(body0.children, dash_table.DataTable)
    print("empty state    : OK (placeholder without a region)")

    # callback registration smoke test
    app = Dash(__name__)
    outs = register_callbacks(app)
    print(f"callbacks      : {len(app.callback_map)} registered (expect 1)")
    print(f"outputs        : {sorted(outs.keys())}")
    assert len(app.callback_map) == 1

    print("ALL CHECKS PASSED")
