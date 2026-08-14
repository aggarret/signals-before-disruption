"""pages/dashboard.py — Dashboard page (path "/").

The full River Personality Monitor dashboard, moved verbatim from app.py's
``build_layout()`` / ``register_integration_callbacks()`` into a Dash page
module so the app can host multiple pages behind a shared navbar.

Component render functions build the initial layout server-side (with the
defaults: metric=streamflow, date=last date with broad gauge coverage, no
station selected). Each component's own ``register_callbacks`` handles its
internal interactivity (map clicks, range buttons, drawer open/close, CSV
export, region-row selection). The integration callbacks in this module keep
the state-store mirrors (``selected-metric``, ``selected-date``,
``selected-station``, ``selected-region``, ``date-range``) in sync with the
panels' controls and re-render the sections that have no internal callbacks
(KPI cards, region table, personality cards, raw-drawer contents) whenever
their inputs change.

Page modules are imported by Dash when the app is created (``enable_pages``
imports the ``pages/`` folder during ``Dash(...)`` construction), so the
``from app import ...`` names below must all be defined above the ``app =
Dash(...)`` line in app.py — they are. The Dash app *instance* itself is not
bound yet at that moment, so it is resolved via ``dash.get_app()`` below
instead of being imported.
"""

from __future__ import annotations

from typing import Any, Optional

import dash
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
from dash import Dash, Input, Output, dcc, html

import queries
from app import (
    DEFAULT_DATE,
    DEFAULT_METRIC,
    LATEST_DATA_DATE,
    N_GAUGES,
    STATION_NAMES,
    TEXT_FAINT,
    TEXT_MUTED,
    TOTAL_ROWS,
    _ID_DATE_STORE,
    _ID_DRAWER_CONTAINER,
    _ID_KPI_CONTAINER,
    _ID_METRIC_STORE,
    _ID_PERSONALITY_CONTAINER,
    _ID_RANGE_STORE,
    _ID_REGION_CONTAINER,
    _ID_REGION_STORE,
    _ID_STATION_STORE,
    _MANTINE_THEME,
    conn,
    refresh_data_if_stale,
)

# The Dash app instance is not importable from app.py at module import time
# (app.py is still constructing it when Dash imports this page), so resolve it
# via dash.get_app() — the same instance, no circular import.
app = dash.get_app()

from components import (
    anomaly_scorecards,
    fastest_risers_table,
    hydrograph,
    kpi_cards,
    map_panel,
    personality_cards,
    raw_drawer,
    region_table,
)
from components.dash4_compat import maybe_wrap

dash.register_page(__name__, path="/", name="Dashboard",
                   title="River Personality Monitor — Dashboard")


def layout() -> dbc.Container:
    """The full page: header, stores, KPI row, map + right rail, hydrograph,
    personality cards, raw drawer, footer, and the drawer toggle button."""
    metric, date = DEFAULT_METRIC, DEFAULT_DATE

    header = html.Div(
        [
            html.Div(
                [
                    html.H1("🌊 River Personality Monitor", className="nav-header"),
                    html.Span(
                        f"Signals Before Disruption · {N_GAUGES} gauges · "
                        f"20-year baselines",
                        style={"color": TEXT_MUTED, "fontSize": "13px",
                               "marginLeft": "12px"},
                    ),
                ],
                style={"display": "flex", "alignItems": "baseline", "gap": "12px",
                       "flex": "1 1 auto"},
            ),
            html.Span(
                f"Today: {LATEST_DATA_DATE}",
                id="latest-data-date",
                style={"color": TEXT_FAINT, "fontSize": "12px",
                       "whiteSpace": "nowrap"},
            ),
        ],
        style={"display": "flex", "alignItems": "center",
               "justifyContent": "space-between", "gap": "12px",
               "flexWrap": "wrap", "padding": "12px 2px 4px"},
    )

    footer = html.Div(
        f"Data: USGS Water Data OGC API · Baseline: 2004-2023 · "
        f"{N_GAUGES} gauges · {TOTAL_ROWS:,} rows",
        className="footer",
    )

    return dmc.MantineProvider(
        theme=_MANTINE_THEME,
        children=dbc.Container(
            fluid=True,
            id="app-container",
            children=[
                dcc.Interval(id="refresh-interval", interval=60_000),
                dcc.Store(id="data-version", storage_type="memory",
                          data={"date": LATEST_DATA_DATE}),
                header,

                # ---- global anomaly scorecards + monthly bar (above filter, -------
                # ---- NOT affected by the metric/date sticky filter) ---------------
                anomaly_scorecards.render_anomaly_scorecards(conn=conn),

                # ---- sticky filter bar: metric + date (stay visible on scroll) --
                # dmc.DatePickerInput opens to a decade/year/month drill-down so
                # navigating 20 years of data is fast (no month-arrow clicking).
                html.Div(
                    className="filter-bar",
                    children=[
                        html.Div(
                            [
                                html.Span("Metric", style={
                                    "color": TEXT_MUTED, "fontSize": "11px",
                                    "fontWeight": "600", "textTransform": "uppercase",
                                    "letterSpacing": "0.05em",
                                }),
                                dcc.Dropdown(
                                    id=map_panel._ID_METRIC,
                                    options=map_panel.METRIC_OPTIONS,
                                    value=metric,
                                    clearable=False,
                                    searchable=False,
                                    style={"minWidth": "220px"},
                                ),
                            ],
                            style={"display": "flex", "alignItems": "center",
                                   "gap": "10px"},
                        ),
                        html.Div(
                            [
                                html.Span("Date", style={
                                    "color": TEXT_MUTED, "fontSize": "11px",
                                    "fontWeight": "600", "textTransform": "uppercase",
                                    "letterSpacing": "0.05em",
                                }),
                                dmc.DatePickerInput(
                                    id=map_panel._ID_DATE,
                                    value=date,
                                    minDate="2004-01-01",
                                    maxDate=date,
                                    clearable=False,
                                    size="sm",
                                    w=180,
                                    popoverProps={"shadow": "md"},
                                ),
                            ],
                            style={"display": "flex", "alignItems": "center",
                                   "gap": "10px"},
                        ),
                    ],
                ),

                # ---- app-level state stores ----------------------------------
                dcc.Store(id=_ID_METRIC_STORE, data=metric),
                dcc.Store(id=_ID_DATE_STORE, data=date),
                dcc.Store(id=_ID_STATION_STORE, data=None),
                dcc.Store(id=_ID_REGION_STORE, data=None),
                dcc.Store(id=_ID_RANGE_STORE, data="3m"),  # hydrograph window

                # ---- KPI cards row -------------------------------------------
                html.Div(
                    id=_ID_KPI_CONTAINER,
                    children=kpi_cards.render_kpi_cards(
                        conn=conn, metric=metric, date=date
                    ),
                ),

                # ---- main: map (8) | region table + fastest risers (4) --------
                dbc.Row(
                    [
                        dbc.Col(
                            width=8,
                            children=[
                                map_panel.render_map_panel(
                                    metric=metric, date=date,
                                    selected_entity_id=None, conn=conn,
                                ),
                            ],
                        ),
                        dbc.Col(
                            width=4,
                            children=[
                                html.Div(
                                    id=_ID_REGION_CONTAINER,
                                    children=region_table.render_region_table(
                                        conn=conn, metric=metric, date=date
                                    ),
                                ),
                                fastest_risers_table.render_fastest_risers(
                                    conn=conn, region=None, metric=metric, date=date
                                ),
                            ],
                        ),
                    ],
                    class_name="g-2",
                ),

                # ---- hydrograph (full width) -----------------------------------
                dbc.Row(
                    dbc.Col(
                        width=12,
                        children=[
                            hydrograph.render_hydrograph(
                                entity_id=None, station_name=None,
                                metric=metric, conn=conn,
                                end_date=date,
                            ),
                        ],
                    ),
                    class_name="g-2",
                ),

                # ---- personality cards -----------------------------------------
                dbc.Row(
                    html.Div(
                        id=_ID_PERSONALITY_CONTAINER,
                        children=personality_cards.render_personality_cards(
                            conn=conn, entity_id=None, metric=metric, date=date
                        ),
                    ),
                    class_name="g-2",
                ),

                # ---- raw-data drawer (hidden; contents refreshed by callbacks) -
                html.Div(
                    id=_ID_DRAWER_CONTAINER,
                    children=raw_drawer.render_raw_drawer(
                        conn=conn, entity_id=None, metric=metric, date=date
                    ),
                ),

                footer,

                html.Div(
                    raw_drawer.raw_drawer_toggle_button(),
                    style={"textAlign": "center", "marginTop": "6px",
                           "marginBottom": "48px"},
                ),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# Integration callbacks (app-level wiring)
# ---------------------------------------------------------------------------
def register_integration_callbacks(app: Dash) -> int:
    """State-store mirrors + re-render of the callback-less sections.

    Mirrors keep the app-level stores (selected-metric / selected-date /
    selected-station / selected-region / date-range) in sync with the panels'
    own controls and stores. The section containers (KPI, region table,
    personality cards, raw drawer) have no internal content callbacks, so they
    are re-rendered here whenever their inputs change. The map, hydrograph and
    fastest-risers panels re-render themselves via their own callbacks.

    Returns the number of callbacks registered by this function.
    """
    base = len(_callbacks(app))

    # --- mirrors: panel controls / stores -> app-level stores --------------
    @app.callback(
        Output(_ID_METRIC_STORE, "data"),
        Input(map_panel._ID_METRIC, "value"),
        prevent_initial_call=True,
    )
    def _mirror_metric(value: Optional[str]) -> str:
        return maybe_wrap(value or DEFAULT_METRIC)

    @app.callback(
        Output(_ID_DATE_STORE, "data"),
        Input(map_panel._ID_DATE, "value"),
        prevent_initial_call=True,
    )
    def _mirror_date(value: Optional[str]) -> str:
        return maybe_wrap(value or DEFAULT_DATE)

    @app.callback(
        Output(_ID_STATION_STORE, "data"),
        Input(map_panel._ID_STATION_STORE, "data"),
        prevent_initial_call=True,
    )
    def _mirror_station(value: Optional[str]) -> Optional[str]:
        return maybe_wrap(value)

    @app.callback(
        Output(_ID_REGION_STORE, "data"),
        Input(map_panel._ID_REGION_STORE, "data"),
        prevent_initial_call=True,
    )
    def _mirror_region(value: Optional[str]) -> Optional[str]:
        return maybe_wrap(value)

    @app.callback(
        Output(_ID_RANGE_STORE, "data"),
        Input(hydrograph._ID_RANGE_STORE, "data"),
        prevent_initial_call=True,
    )
    def _mirror_range(value: Any) -> Any:
        """Keep the app-level date-range store as a plain range key string."""
        if isinstance(value, dict):
            return maybe_wrap(value.get("range", "3m"))
        return maybe_wrap(value or "3m")

    # --- sections without internal content callbacks -----------------------
    @app.callback(
        Output(_ID_KPI_CONTAINER, "children"),
        Input(_ID_METRIC_STORE, "data"),
        Input(_ID_DATE_STORE, "data"),
        prevent_initial_call=True,
    )
    def _render_kpis(metric: Optional[str], date: Optional[str]):
        return maybe_wrap(kpi_cards.render_kpi_cards(
            conn=queries.get_connection(),
            metric=metric or DEFAULT_METRIC, date=date or DEFAULT_DATE,
        ))

    @app.callback(
        Output(_ID_REGION_CONTAINER, "children"),
        Input(_ID_METRIC_STORE, "data"),
        Input(_ID_DATE_STORE, "data"),
        prevent_initial_call=True,
    )
    def _render_region_table(metric: Optional[str], date: Optional[str]):
        # Rebuilding the card also resets region-table-store to None, which
        # clears the fastest-risers drill-down (correct for a new metric/date).
        return maybe_wrap(region_table.render_region_table(
            conn=queries.get_connection(),
            metric=metric or DEFAULT_METRIC, date=date or DEFAULT_DATE,
        ))

    @app.callback(
        Output(_ID_PERSONALITY_CONTAINER, "children"),
        Input(_ID_STATION_STORE, "data"),
        Input(_ID_METRIC_STORE, "data"),
        Input(_ID_DATE_STORE, "data"),
        prevent_initial_call=True,
    )
    def _render_personality(entity_id: Optional[str], metric: Optional[str],
                            date: Optional[str]):
        return maybe_wrap(personality_cards.render_personality_cards(
            conn=queries.get_connection(), entity_id=entity_id,
            metric=metric or DEFAULT_METRIC, date=date or DEFAULT_DATE,
        ))

    @app.callback(
        Output(_ID_DRAWER_CONTAINER, "children"),
        Input(_ID_STATION_STORE, "data"),
        Input(_ID_METRIC_STORE, "data"),
        Input(_ID_DATE_STORE, "data"),
        prevent_initial_call=True,
    )
    def _render_raw_drawer(entity_id: Optional[str], metric: Optional[str],
                           date: Optional[str]):
        # The rebuilt panel starts hidden; if it was open it closes, and the
        # toggle re-opens it against the new selection.
        return maybe_wrap(raw_drawer.render_raw_drawer(
            conn=queries.get_connection(), entity_id=entity_id,
            metric=metric or DEFAULT_METRIC, date=date or DEFAULT_DATE,
        ))

    # --- cloud freshness poll: re-sync + refresh the "Today" label ---------
    @app.callback(
        Output("latest-data-date", "children"),
        Output("data-version", "data"),
        Input("refresh-interval", "n_intervals"),
    )
    def _poll_refresh(_n: int):
        new_date = refresh_data_if_stale()
        if new_date is None:
            return dash.no_update, dash.no_update
        return f"Today: {new_date}", {"date": new_date}

    return len(_callbacks(app)) - base


def _callbacks(app: Dash) -> list:
    """Callback registry (Dash 3/4 exposes _callback_list; older: callback_map)."""
    try:
        return list(app.callback_map.values())
    except AttributeError:
        return list(app._callback_list)


# ---------------------------------------------------------------------------
# Wire everything: component callbacks + integration callbacks
# ---------------------------------------------------------------------------
map_panel.register_callbacks(app)
hydrograph.register_callbacks(app)
raw_drawer.register_callbacks(app)
region_table.register_callbacks(app)
fastest_risers_table.register_callbacks(app)
register_integration_callbacks(app)