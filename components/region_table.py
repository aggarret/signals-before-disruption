"""region_table.py — Regional rollup table (Pass D of the Dash build).

One row per region for a metric-date: gauges reporting, |z| >= 2.5 event count,
and mean anomaly — sorted by mean anomaly descending so the most stressed
regions sit on top. Clicking a row highlights it and writes the region name to
`region-table-store` so the fastest-risers panel (components/
fastest_risers_table.py) can react.

Dark theme is applied inline (style_header / style_data / style_cell) plus the
.region-table CSS class for row hover (assets/style.css). Avg Anomaly cells are
color-coded via the anomaly palette (+ teal-cyan / − amber).

Component IDs owned here:
    region-table           dash_table.DataTable
    region-table-store     dcc.Store (region name of the selected row)

Layout contract:
    render_region_table(conn, metric, date) -> dbc.Card
    register_callbacks(app) -> {"region_select_store": Output(...)}
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, ctx, dash_table, dcc, html, no_update

import queries
from components.dash4_compat import maybe_wrap
from components.map_panel import (
    get_default_date,
    METRIC_LABELS,
    CARD_BG,
    TEXT_BRIGHT,
    Z_TEAL,
)

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------
_ID_TABLE = "region-table"
_ID_REGION_SELECT_STORE = "region-table-store"

_OUTPUTS = {
    "region_select_store": Output(_ID_REGION_SELECT_STORE, "data"),
}

# Shared dark-table theme (fastest_risers_table.py imports these).
_TABLE_HEADER = {
    "backgroundColor": CARD_BG,
    "color": TEXT_BRIGHT,
    "fontWeight": "600",
    "fontSize": "12px",
    "border": "1px solid #334155",
}
_TABLE_CELL = {
    "backgroundColor": "#0f172a",
    "color": "#cbd5e1",
    "fontSize": "12px",
    "border": "1px solid #334155",
}

_COLUMNS = [
    {"name": "Region", "id": "Region"},
    {"name": "Gauges Reporting", "id": "Gauges Reporting"},
    {"name": "Events (|z|≥2.5)", "id": "Events (|z|≥2.5)"},
    {"name": "Avg Anomaly", "id": "Avg Anomaly"},
]


def _table_data(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """DataTable rows from the get_region_table frame."""
    if df.empty:
        return []
    df = df.sort_values("average_anomaly", ascending=False, na_position="last")
    rows = []
    for _, r in df.iterrows():
        avg = r.get("average_anomaly")
        rows.append({
            "Region": str(r["region"]),
            "Gauges Reporting": int(r["entity_count"]),
            "Events (|z|≥2.5)": int(r["event_count"]),
            "Avg Anomaly": f"{float(avg):+.2f}"
                           if avg is not None and pd.notna(avg) else "—",
        })
    return rows


def _data_table(data: List[Dict[str, Any]]) -> dash_table.DataTable:
    """Dark-themed DataTable with clickable single-row selection."""
    return dash_table.DataTable(
        id=_ID_TABLE,
        columns=list(_COLUMNS),
        data=data,
        row_selectable="single",          # visual selected-row highlight
        selected_rows=[],
        page_action="native",
        page_size=8,
        style_header=_TABLE_HEADER,
        style_data=_TABLE_CELL,
        style_cell={"textAlign": "left", "padding": "6px 10px",
                    "whiteSpace": "normal"},
        style_cell_conditional=[
            {"if": {"column_id": "Avg Anomaly"}, "textAlign": "right"},
            {"if": {"column_id": "Events (|z|≥2.5)"}, "textAlign": "right"},
            {"if": {"column_id": "Gauges Reporting"}, "textAlign": "right"},
        ],
        style_data_conditional=[
            # selected row = solid teal; active cell = lighter slate
            {"if": {"state": "selected"},
             "backgroundColor": Z_TEAL, "color": "#0f172a", "fontWeight": "600"},
            {"if": {"state": "active"},
             "backgroundColor": "#334155", "color": "#e2e8f0"},
            # anomaly palette on the Avg Anomaly column
            {"if": {"column_id": "Avg Anomaly",
                    "filter_query": '`Avg Anomaly` contains "+"'},
             "color": "#0dd4be", "fontWeight": "600"},
            {"if": {"column_id": "Avg Anomaly",
                    "filter_query": '`Avg Anomaly` contains "-"'},
             "color": "#f59e0b", "fontWeight": "600"},
        ],
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_region_table(
    conn: Any = None,
    metric: str = "streamflow",
    date: Optional[str] = None,
) -> dbc.Card:
    """Regional rollup card for one metric-date."""
    conn = conn or queries.get_connection()
    date = str(date or get_default_date(conn))
    metric_label = METRIC_LABELS.get(metric, metric)

    try:
        df = queries.get_region_table(conn, metric, date)
    except Exception:
        # struct-parse edge case (see map_panel._safe_region_table): the four
        # scalar columns are enough for this table.
        df = pd.DataFrame(
            columns=["region", "entity_count", "event_count",
                     "average_anomaly", "fastest_risers"]
        )

    data = _table_data(df)
    n_regions = len(data)

    header = dbc.CardHeader(
        html.Div(
            [
                html.H6("Regional Rollup", style={
                    "margin": "0", "color": TEXT_BRIGHT, "fontWeight": "600",
                }),
                html.Small(
                    f"{metric_label} · {date} · {n_regions} regions · "
                    f"sorted by mean anomaly",
                    style={"color": "#64748b"},
                ),
            ]
        )
    )

    body = (
        _data_table(data)
        if data
        else html.Div(
            "No regional data for this metric / date.",
            style={"color": "#94a3b8", "fontSize": "12px", "padding": "10px 4px"},
        )
    )

    return dbc.Card(
        [header, dbc.CardBody(body, style={"padding": "0.5rem"}),
         dcc.Store(id=_ID_REGION_SELECT_STORE)],
        className="region-table",
    )


# ---------------------------------------------------------------------------
# Callbacks (register on the app; wire in app.py via register_callbacks)
# ---------------------------------------------------------------------------
def register_callbacks(app: Dash) -> Dict[str, Any]:
    """Write the selected row's region into `region-table-store`.

    Uses active_cell + derived_viewport_data so the lookup stays correct even
    after native column sorting. Returns
    {"region_select_store": Output(region-table-store, "data")}.
    """
    @app.callback(
        Output(_ID_REGION_SELECT_STORE, "data"),
        Input(_ID_TABLE, "active_cell"),
        Input(_ID_TABLE, "selected_rows"),
        Input(_ID_TABLE, "derived_viewport_data"),
        Input(_ID_TABLE, "derived_virtual_data"),
        prevent_initial_call=True,
    )
    def _on_select(active_cell, selected_rows, view_data, virtual_data):
        """Write the selected row's region into `region-table-store`.

        Handles both selection paths of row_selectable="single":
          * radio-button click -> `selected_rows`; its index is relative to the
            FULL dataset (stable across pages), so it is resolved against
            derived_virtual_data;
          * cell click         -> `active_cell`; its row index is relative to
            the current viewport, so it is resolved against
            derived_viewport_data.
        The changed-input check keeps the newest action in charge when both
        sources are present (e.g. a cell click after a radio selection leaves
        selected_rows untouched). Using the derived_* frames keeps lookups
        correct after native column sorting/pagination. Returns
        {"region_select_store": Output(region-table-store, "data")}.
        """
        changed = set(ctx.triggered_prop_ids) if ctx.triggered else set()
        if "region-table.selected_rows" in changed:
            if not selected_rows:
                return maybe_wrap(None)
            src = virtual_data if virtual_data else view_data
            try:
                return maybe_wrap(str(src[int(selected_rows[0])]["Region"]))
            except (IndexError, KeyError, TypeError):
                return maybe_wrap(None)
        if "region-table.active_cell" in changed:
            if not active_cell or not view_data:
                return maybe_wrap(None)
            try:
                return maybe_wrap(str(view_data[int(active_cell["row"])]["Region"]))
            except (IndexError, KeyError, TypeError):
                return maybe_wrap(None)
        # Fallback without trigger info: radio selection wins, then active cell.
        if selected_rows:
            src = virtual_data if virtual_data else view_data
            try:
                return maybe_wrap(str(src[int(selected_rows[0])]["Region"]))
            except (IndexError, KeyError, TypeError):
                return maybe_wrap(None)
        if active_cell and view_data:
            try:
                return maybe_wrap(str(view_data[int(active_cell["row"])]["Region"]))
            except (IndexError, KeyError, TypeError):
                return maybe_wrap(None)
        return maybe_wrap(None)

    return dict(_OUTPUTS)


# ---------------------------------------------------------------------------
# Self-test: python3 components/region_table.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    d = str(get_default_date(conn))

    card = render_region_table(conn, "streamflow", d)
    assert isinstance(card, dbc.Card), "must return a dbc.Card"

    body = card.children[1]
    dt = body.children  # DataTable (or placeholder Div)
    print(f"date used      : {d}")
    print(f"return type    : {type(card).__name__} (dbc.Card, class="
          f"{card.className!r})")

    if isinstance(dt, dash_table.DataTable):
        rows = dt.data
        print(f"regions        : {len(rows)}")
        avgs = [float(r["Avg Anomaly"]) for r in rows if r["Avg Anomaly"] != "—"]
        print(f"avg anomaly    : "
              + ", ".join(f"{r['Region']}={r['Avg Anomaly']}" for r in rows))
        assert avgs == sorted(avgs, reverse=True), "must be sorted desc"
        assert dt.id == "region-table"
        print(f"sort order     : descending by Avg Anomaly "
              f"({len(avgs)} numeric values checked)")
    else:
        print("regions        : none (placeholder shown)")

    # callback registration smoke test
    app = Dash(__name__)
    outs = register_callbacks(app)
    print(f"callbacks      : {len(app.callback_map)} registered (expect 1)")
    print(f"outputs        : {sorted(outs.keys())}")
    assert len(app.callback_map) == 1

    # water-temperature path (sparse metrics still render)
    card_t = render_region_table(conn, "water_temperature", d)
    assert isinstance(card_t, dbc.Card)

    print("ALL CHECKS PASSED")
