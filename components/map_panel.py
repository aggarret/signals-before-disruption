"""map_panel.py — National Overview map panel (Pass B of the Dash build).

State-level choropleth of today's regional average anomaly (avg_z), with a
scatter_geo overlay of per-gauge anomaly markers (color = z bin, size ∝ √flow),
a dark slate theme, and click-driven selection:

  * click a STATE        -> region-store  gains the region name (filters the
                            station dropdown in other panels)
  * click a STATION      -> station-store gains the entity_id (loads the
                            hydrograph drill-down)
  * metric dropdown      -> redraws the map for streamflow / gage_height /
                            water_temperature
  * date picker          -> redraws the map for the chosen day

Component IDs owned here (wire them in app.py via ``register_callbacks``):
    metric-dropdown, date-picker, map-graph, region-store, station-store

Color mapping (anomaly z, dark-slate app palette):
    z <= -2.0            #f59e0b  amber            (extreme low)
    -2.0 < z <= -1.5     #7dccc4  amber-teal       (low)
    -1.5 < z <  1.5      #14b8a6  teal             (near normal)
     1.5 <= z <  2.0     #0dd4be  teal-cyan        (high)
     2.0 <= z <  3.0     #06b6d4  cyan             (very high)
     z >= 3.0            #f43f5e  crimson          (extreme high)
     null anomaly        #475569  slate gray
     no gauges in state  #334155  darker neutral gray
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import dash_bootstrap_components as dbc
from dash import Input, Output, dcc, html, no_update

import queries
from components.dash4_compat import maybe_wrap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
Z_AMBER = "#f59e0b"       # z <= -2.0
Z_AMBER_TEAL = "#7dccc4"  # -2.0 < z <= -1.5
Z_TEAL = "#14b8a6"        # -1.5 < z < 1.5
Z_TEAL_CYAN = "#0dd4be"   # 1.5 <= z < 2.0
Z_CYAN = "#06b6d4"        # 2.0 <= z < 3.0
Z_CRIMSON = "#f43f5e"     # z >= 3.0
Z_NULL = "#475569"        # null anomaly (slate gray)
Z_NO_DATA = "#334155"     # state with no gauges (neutral gray)

METRIC_OPTIONS = [
    {"label": "Streamflow (ft³/s)", "value": "streamflow"},
    {"label": "Gage height (ft)", "value": "gage_height"},
    {"label": "Water temperature (°C)", "value": "water_temperature"},
]
METRIC_LABELS = {o["value"]: o["label"].split(" (")[0] for o in METRIC_OPTIONS}
METRIC_UNITS = {
    "streamflow": "ft³/s",
    "gage_height": "ft",
    "water_temperature": "°C",
}

# Full US state names (as used in stations.csv) -> postal abbreviations.
_STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
_ABBR_TO_STATE = {v: k for k, v in _STATE_ABBR.items()}

# Dark slate theme (matches the app palette used across panels).
PAPER_BG = "#0f172a"     # slate-900, plot + paper background
CARD_BG = "#1e293b"      # slate-800, card surface
BORDER = "#334155"       # slate-700, borders / coastlines
TEXT_MUTED = "#94a3b8"   # slate-400, axis & body text
TEXT_BRIGHT = "#e2e8f0"  # slate-200, headings
GEO_LAYOUT = dict(
    scope="usa",
    projection=dict(type="albers usa"),
    bgcolor=PAPER_BG,
    landcolor=CARD_BG,          # land slightly lighter than the sea of paper
    coastlinecolor=BORDER,
    showcoastlines=True,
    countrycolor=BORDER,
    showcountries=True,
    subunitcolor=BORDER,        # state borders
    showsubunits=True,
    lakecolor="#0b1220",
    showlakes=True,
    showrivers=False,
    showframe=False,
)

_ID_MAP = "map-graph"
_ID_METRIC = "metric-dropdown"
_ID_DATE = "date-picker"
_ID_REGION_STORE = "region-store"
_ID_STATION_STORE = "station-store"
_ID_HEADER_SUB = "map-header-sub"  # subtitle under the card title (date-aware)

# Callback outputs: (component_id, component_property) pairs for the return
# value of register_callbacks() and for Output(*spec) wiring.
_OUTPUTS = {
    "region_store": (_ID_REGION_STORE, "data"),
    "station_store": (_ID_STATION_STORE, "data"),
    "map_figure": (_ID_MAP, "figure"),
    "map_header_sub": (_ID_HEADER_SUB, "children"),
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def z_to_color(z: Any) -> str:
    """Map an anomaly z-score to its palette hex (see module docstring)."""
    if z is None or pd.isna(z):
        return Z_NULL
    z = float(z)
    if z <= -2.0:
        return Z_AMBER
    if z <= -1.5:
        return Z_AMBER_TEAL
    if z < 1.5:
        return Z_TEAL
    if z < 2.0:
        return Z_TEAL_CYAN
    if z < 3.0:
        return Z_CYAN
    return Z_CRIMSON


# Category-code colorscale for the choropleth. Code = index into this list, so
# every avg_z (even extreme tails like z = 74) maps to exactly one palette
# color — no clipping or interpolation artifacts. 6 anomaly bins + null-anomaly
# slate + no-gauge neutral gray.
_CHORO_COLORS = [
    Z_AMBER,       # 0  z <= -2.0
    Z_AMBER_TEAL,  # 1  -2.0 < z <= -1.5
    Z_TEAL,        # 2  -1.5 < z < 1.5
    Z_TEAL_CYAN,   # 3  1.5 <= z < 2.0
    Z_CYAN,        # 4  2.0 <= z < 3.0
    Z_CRIMSON,     # 5  z >= 3.0
    Z_NULL,        # 6  region reports no anomaly today
    Z_NO_DATA,     # 7  state has no gauges
]
_CHORO_SCALE = [(i / (len(_CHORO_COLORS) - 1), c)
                for i, c in enumerate(_CHORO_COLORS)]


def _z_to_code(avg_z: Any, region: Optional[str]) -> int:
    """Choropleth category code for a state's regional average anomaly."""
    if region is None:
        return 7                      # no gauges in this state
    if avg_z is None or pd.isna(avg_z):
        return 6                      # region exists but no anomaly today
    z = float(avg_z)
    if z <= -2.0:
        return 0
    if z <= -1.5:
        return 1
    if z < 1.5:
        return 2
    if z < 2.0:
        return 3
    if z < 3.0:
        return 4
    return 5


_STATE_REGION_CACHE: Optional[Dict[str, str]] = None


def state_to_region_map() -> Dict[str, str]:
    """stations.csv: full state name -> region (most common region per state).

    A state with gauges in several regions is a data smell; we keep the most
    frequent region (pandas value_counts ties resolve lexicographically).
    """
    global _STATE_REGION_CACHE
    if _STATE_REGION_CACHE is None:
        st = pd.read_csv(
            os.path.join(_ROOT, "stations.csv"), usecols=["state", "region"]
        ).dropna(subset=["state", "region"])
        keeper = (
            st.groupby("state")["region"]
            .agg(lambda s: s.value_counts().idxmax())
            .to_dict()
        )
        _STATE_REGION_CACHE = keeper
    return _STATE_REGION_CACHE


_REGION_BY_ABBR_CACHE: Optional[Dict[str, str]] = None


def region_by_abbr() -> Dict[str, str]:
    """Postal abbreviation -> region (for state-click callbacks)."""
    global _REGION_BY_ABBR_CACHE
    if _REGION_BY_ABBR_CACHE is None:
        _REGION_BY_ABBR_CACHE = {
            _STATE_ABBR[full]: region
            for full, region in state_to_region_map().items()
            if full in _STATE_ABBR
        }
    return _REGION_BY_ABBR_CACHE


def get_default_date(conn: Optional[Any] = None) -> str:
    """Most recent date with broad gauge coverage (map default view).

    The ingest's final day is often partial — a few gauges report while the
    rest are explicit gap rows — which would render a near-empty map. We scan
    back up to 14 days and return the newest date where at least half of the
    streamflow gauges report a value; fall back to the raw max date.
    """
    conn = conn or queries.get_connection()
    last = queries._dataset_stats(conn, "streamflow")["last_date"]
    d = pd.Timestamp(last)
    for _ in range(14):
        ds = str(d.date())
        md = queries.get_map_data(conn, "streamflow", ds)
        if not md.empty and float(md["value"].notna().mean()) >= 0.5:
            return ds
        d -= pd.Timedelta(days=1)
    return str(last)


def _marker_sizes(values: pd.Series) -> List[float]:
    """√-scaled marker sizes in px, clamped to 5–20px.

    Big rivers get visually bigger: size = 5 + 15 * (√v − √min)/(√max − √min).
    Nulls fall back to 8px.
    """
    v = np.sqrt(pd.to_numeric(values, errors="coerce").to_numpy(dtype=float))
    vmin, vmax = np.nanmin(v), np.nanmax(v)
    if not np.isfinite(vmin) or vmax <= vmin:
        return [8.0] * len(v)
    return [
        round(5.0 + 15.0 * (x - vmin) / (vmax - vmin), 1) if np.isfinite(x) else 8.0
        for x in v
    ]


def _fmt_value(v: Any, metric: str) -> str:
    if pd.isna(v):
        return "no observation"
    if metric == "water_temperature":
        return f"{float(v):.1f} °C"
    return f"{float(v):,.0f} {METRIC_UNITS.get(metric, '')}".strip()


def _station_hovers(gauges: pd.DataFrame, metric: str) -> List[str]:
    out = []
    for _, r in gauges.iterrows():
        z = r.get("anomaly_score")
        pct = r.get("flow_percentile")
        lines = [str(r["station_name"]), _fmt_value(r.get("value"), metric)]
        lines.append(f"z = {float(z):+.2f} σ" if pd.notna(z) else "z = —")
        lines.append(
            f"flow percentile: {float(pct):.0f}%" if pd.notna(pct) else "flow percentile: —"
        )
        out.append("<br>".join(lines))
    return out


def _state_hover(full_name: str, region: Optional[str], avg_z: Any,
                 n_gauges: int, n_events: int) -> str:
    if region is None:
        return f"<b>{full_name}</b><br>no gauges"
    if avg_z is None:
        return f"<b>{full_name}</b><br>{region}<br>no regional data today"
    return (
        f"<b>{full_name}</b><br>{region}<br>"
        f"avg z = {float(avg_z):+.2f} σ<br>{n_gauges} gauges · {n_events} events"
    )


# ---------------------------------------------------------------------------
# Figure builder
# ---------------------------------------------------------------------------
def _safe_region_table(conn: Any, metric: str, date: str) -> pd.DataFrame:
    """get_region_table with a fallback for the pd.NA struct-parse bug.

    queries.get_region_table parses the `fastest_risers` struct[] column; DuckDB
    surfaces NULL risers as pandas NA, which queries._risers_to_dicts does not
    handle (TypeError on most real dates). The choropleth only needs the four
    scalar columns, so on failure we re-read the same parquet directly,
    skipping the struct column. queries.py is unchanged by design.
    """
    try:
        return queries.get_region_table(conn, metric, date)
    except TypeError:
        df = conn.execute(
            f"""
            SELECT region, entity_count, event_count, average_anomaly
            FROM read_parquet('{queries._CM_GLOB}')
            WHERE metric = ? AND year = ? AND date = ?
            ORDER BY average_anomaly DESC NULLS LAST
            """,
            [metric, int(str(date)[:4]), str(date)],
        ).df()
        return df


def _region_states_frame(conn: Any, metric: str, date: str) -> pd.DataFrame:
    """One row per US state+DC: color hex + hover label for the choropleth.

    States are colored by the average_anomaly of their region (from
    daily_category_metrics via get_region_table). States without gauges get the
    neutral no-data gray; regions reporting a null average today get the
    null-anomaly gray.
    """
    reg = _safe_region_table(conn, metric, date)
    smap = state_to_region_map()
    rows = []
    for full in sorted(_STATE_ABBR):
        abbr = _STATE_ABBR[full]
        region = smap.get(full)
        if region is None:
            rows.append(dict(state=abbr, region=None, avg_z=None, n=0, events=0,
                             code=7, hover=_state_hover(full, None, None, 0, 0)))
            continue
        r = reg[reg["region"] == region]
        if r.empty or pd.isna(r.iloc[0]["average_anomaly"]):
            avg, n, events = None, 0, 0
        else:
            avg = float(r.iloc[0]["average_anomaly"])
            n = int(r.iloc[0]["entity_count"])
            events = int(r.iloc[0]["event_count"])
        rows.append(dict(state=abbr, region=region, avg_z=avg, n=n, events=events,
                         code=_z_to_code(avg, region),
                         hover=_state_hover(full, region, avg, n, events)))
    return pd.DataFrame(rows)


def build_map_figure(
    conn: Any,
    metric: str = "streamflow",
    date: Optional[str] = None,
    selected_entity_id: Optional[str] = None,
) -> go.Figure:
    """Single dark-slate figure: state choropleth + station scatter overlay.

    The choropleth trace (average regional anomaly) is built with
    plotly.express.choropleth (built-in USA-states GeoJSON); the gauge scatter
    is added with go.Scattergeo so both live on the same figure and share one
    geo scope / projection / zoom state (uirevision).

    The uirevision includes the metric so that zoom/pan is preserved within
    a single metric but the traces fully redraw when the metric changes —
    without this, Plotly.js can skip re-rendering traces when uirevision is
    static, causing the map to appear stuck on the initial metric's data.
    """
    date = date or get_default_date(conn)
    state_df = _region_states_frame(conn, metric, date)
    gauges = queries.get_map_data(conn, metric, date)

    # --- 1) state-level choropleth (px, built-in US state GeoJSON) --------
    # plotly 6.9's *categorical* choropleth path is broken (one trace per
    # category, default purple fills), so we discretize avg_z to palette
    # category codes and drive px's numeric path with an exact 8-stop
    # colorscale: every z lands on exactly one color, extremes included.
    choropleth = px.choropleth(
        state_df,
        locations="state",
        locationmode="USA-states",
        color="code",
        color_continuous_scale=_CHORO_SCALE,
        range_color=(0, len(_CHORO_COLORS) - 1),
        custom_data=["hover"],
        scope="usa",
    ).data[0]
    choropleth.update(
        showlegend=False,
        showscale=False,            # palette legend is rendered in HTML below
        marker_line_color=PAPER_BG,
        marker_line_width=0.75,
        hovertemplate="%{customdata[0]}<extra></extra>",
        name="Regional avg anomaly",
    )
    # px attaches the scale via layout.coloraxis (trace carries a dangling
    # reference once we lift .data[0] into a fresh figure). Re-bind the scale
    # directly on the trace so the extracted trace stays self-contained.
    choropleth.update(
        colorscale=_CHORO_SCALE,
        zmin=0.0,
        zmax=float(len(_CHORO_COLORS) - 1),
        coloraxis=None,
    )

    fig = go.Figure(data=[choropleth])
    fig.update_layout(
        template=None,
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PAPER_BG,
        font=dict(color=TEXT_MUTED, family="Inter, -apple-system, Segoe UI, sans-serif",
                  size=12),
        margin=dict(l=0, r=0, t=30, b=0),
        height=560,
        geo=GEO_LAYOUT,
        # Include metric in uirevision so zoom/pan is preserved within a
        # metric but traces fully redraw when the metric changes. A static
        # uirevision causes Plotly.js to skip trace updates in some cases.
        uirevision=f"map-panel:{metric}",   # keep zoom/pan within a metric
        showlegend=False,
    )

    # --- 2) station scatter overlay ---------------------------------------
    if not gauges.empty:
        sizes = _marker_sizes(gauges["value"])
        colors = gauges["anomaly_score"].map(z_to_color).tolist()
        eids = gauges["entity_id"].tolist()
        fig.add_trace(go.Scattergeo(
            lon=gauges["longitude"],
            lat=gauges["latitude"],
            mode="markers",
            marker=dict(
                size=sizes,
                color=colors,
                opacity=0.95,
                line=dict(color=PAPER_BG, width=1),
            ),
            text=_station_hovers(gauges, metric),
            hoverinfo="text",
            customdata=[[e] for e in eids],
            name="Gauges",
        ))

        # --- 3) selected-station white ring --------------------------------
        if selected_entity_id is not None:
            idx = next((i for i, e in enumerate(eids) if e == selected_entity_id), None)
            if idx is not None and pd.notna(gauges.iloc[idx]["latitude"]):
                r = gauges.iloc[idx]
                fig.add_trace(go.Scattergeo(
                    lon=[r["longitude"]],
                    lat=[r["latitude"]],
                    mode="markers",
                    marker=dict(
                        size=float(sizes[idx]) + 7.0,
                        color="rgba(255,255,255,0)",
                        line=dict(color="#f8fafc", width=2),
                    ),
                    hoverinfo="skip",
                    customdata=[[selected_entity_id]],
                    name="Selected",
                ))
    return fig


# ---------------------------------------------------------------------------
# Legend (manual HTML swatches — exact hexes from the palette)
# ---------------------------------------------------------------------------
_LEGEND_ITEMS = [
    (Z_CRIMSON, "z ≥ +3.0 · extreme high"),
    (Z_CYAN, "+2.0 to +3.0 · very high"),
    (Z_TEAL_CYAN, "+1.5 to +2.0 · high"),
    (Z_TEAL, "−1.5 to +1.5 · near normal"),
    (Z_AMBER_TEAL, "−2.0 to −1.5 · low"),
    (Z_AMBER, "z ≤ −2.0 · extreme low"),
    (Z_NO_DATA, "no gauges"),
    (Z_NULL, "no anomaly data"),
]


def _legend_div() -> html.Div:
    swatches = [
        html.Div(
            [
                html.Span(style={
                    "display": "inline-block", "width": "12px", "height": "12px",
                    "borderRadius": "3px", "backgroundColor": color, "marginRight": "6px",
                }),
                html.Span(label, style={
                    "fontSize": "11px", "color": TEXT_MUTED, "whiteSpace": "nowrap",
                }),
            ],
            style={"display": "flex", "alignItems": "center",
                   "marginRight": "16px", "marginBottom": "4px"},
        )
        for color, label in _LEGEND_ITEMS
    ]
    return html.Div(swatches, style={"display": "flex", "flexWrap": "wrap",
                                    "paddingTop": "6px"})


def _dark_controls_style() -> html.Script:
    """Scoped dark styling for the dcc.Dropdown / dcc.DatePickerSingle.

    Dash 4 removed dangerously_set_inner_HTML, so the CSS is injected via a
    tiny script (content has no quotes / script tags, so it is safe).
    """
    css = """
#metric-dropdown .Select-control,
#metric-dropdown .Select-menu-outer {
  background-color: #0f172a; color: #e2e8f0; border-color: #334155;
}
#metric-dropdown .Select-value-label { color: #e2e8f0 !important; }
#metric-dropdown .Select-option { background-color: #0f172a; color: #cbd5e1; }
#metric-dropdown .Select-option.is-focused { background-color: #1e293b; color: #e2e8f0; }
#metric-dropdown .Select-input > input { color: #e2e8f0 !important; }
#date-picker .DateInput,
#date-picker .DateInput_input {
  background-color: #0f172a; color: #e2e8f0; border-color: #334155;
  font-size: 13px;
}
#date-picker .DateInput_input { padding: 6px 10px; }
#date-picker .CalendarDay__selected { background: #06b6d4; border: none; }
#date-picker .CalendarDay__selected:hover { background: #0dd4be; }
"""
    js = ("document.head.insertAdjacentHTML('beforeend', "
          f"'<style>{css}</style>');")
    return html.Script(js, type="text/javascript")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_map_panel(
    metric: str = "streamflow",
    date: Optional[str] = None,
    selected_entity_id: Optional[str] = None,
    conn: Optional[Any] = None,
) -> dbc.Card:
    """The complete National Overview card: header controls, map, legend."""
    conn = conn or queries.get_connection()
    date = date or get_default_date(conn)
    fig = build_map_figure(conn, metric, date, selected_entity_id)
    metric_label = METRIC_LABELS.get(metric, metric)

    # Metric dropdown + date picker live in the app-level sticky filter bar
    # (app.py, .filter-bar) so they stay visible while the page scrolls; this
    # header keeps the title + the date-aware subtitle, which the _redraw_map
    # callback updates in sync with the selected metric/date.
    header = dbc.CardHeader(
        html.Div(
            [
                html.Div(
                    [
                        html.H6("National Overview", style={
                            "margin": "0", "color": TEXT_BRIGHT, "fontWeight": "600",
                        }),
                        html.Small(
                            id=_ID_HEADER_SUB,
                            children=f"{metric_label} · daily regional anomaly · {date}",
                            style={"color": "#64748b"},
                        ),
                    ],
                    style={"flex": "1 1 auto"},
                ),
            ],
            style={"display": "flex", "alignItems": "center",
                   "gap": "14px", "flexWrap": "wrap"},
        ),
        style={"backgroundColor": CARD_BG, "borderBottom": f"1px solid {BORDER}"},
    )

    return dbc.Card(
        [
            header,
            dbc.CardBody(
                [
                    dcc.Graph(
                        id=_ID_MAP,
                        figure=fig,
                        config={"displayModeBar": False},
                        style={"height": "560px", "width": "100%"},
                    ),
                    _dark_controls_style(),
                    _legend_div(),
                ],
                style={"padding": "0.5rem 0.75rem", "backgroundColor": PAPER_BG},
            ),
            dcc.Store(id=_ID_REGION_STORE, storage_type="memory"),
            dcc.Store(id=_ID_STATION_STORE, storage_type="memory"),
        ],
        style={"backgroundColor": CARD_BG, "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "overflow": "hidden"},
    )


# ---------------------------------------------------------------------------
# Callbacks (register on the app; NOT wired into app.py yet)
# ---------------------------------------------------------------------------
def register_callbacks(app: Any) -> Dict[str, Any]:
    """Register this panel's callbacks on *app* and return the outputs.

    Returns {"region_store": Output(...), "station_store": Output(...),
             "map_figure": Output(...)} so app.py can introspect the IDs.
    Callbacks are safe to register before the rest of the layout exists —
    Dash resolves outputs by id at request time.
    """

    @app.callback(
        Output(*_OUTPUTS["region_store"]),
        Input(_ID_MAP, "clickData"),
        prevent_initial_call=True,
    )
    def _on_state_click(click_data):
        """Click on the choropleth (trace 0) -> region-store = region name."""
        if not click_data or not click_data.get("points"):
            return no_update
        p = click_data["points"][0]
        if int(p.get("curveNumber", -1)) != 0:
            return no_update
        region = region_by_abbr().get(p.get("location"))
        return maybe_wrap(region if region else no_update)

    @app.callback(
        Output(*_OUTPUTS["station_store"]),
        Input(_ID_MAP, "clickData"),
        prevent_initial_call=True,
    )
    def _on_station_click(click_data):
        """Click on a gauge marker (traces 1/2) -> station-store = entity_id."""
        if not click_data or not click_data.get("points"):
            return no_update
        p = click_data["points"][0]
        if int(p.get("curveNumber", -1)) not in (1, 2):
            return no_update
        cd = p.get("customdata")
        if not cd:
            return no_update
        eid = cd[0] if isinstance(cd, (list, tuple)) else cd
        return maybe_wrap(str(eid) if eid else no_update)

    @app.callback(
        Output(*_OUTPUTS["map_figure"]),
        Output(*_OUTPUTS["map_header_sub"]),
        Input(_ID_METRIC, "value"),
        Input(_ID_DATE, "value"),
        Input(_ID_STATION_STORE, "data"),
        prevent_initial_call=True,
    )
    def _redraw_map(metric, date, selected_entity_id):
        """Rebuild the figure AND the date-aware header subtitle on metric/date/
        selection changes (the subtitle used to be static layout text)."""
        conn = queries.get_connection()  # per-call: callbacks run on worker threads
        metric = metric or "streamflow"
        date = date or get_default_date(conn)
        header_text = (
            f"{METRIC_LABELS.get(metric, metric)} · daily regional anomaly · {date}"
        )
        return build_map_figure(conn, metric, date, selected_entity_id), header_text

    return {
        "region_store": Output(*_OUTPUTS["region_store"]),
        "station_store": Output(*_OUTPUTS["station_store"]),
        "map_figure": Output(*_OUTPUTS["map_figure"]),
        "map_header_sub": Output(*_OUTPUTS["map_header_sub"]),
    }


# ---------------------------------------------------------------------------
# Self-test: python3 components/map_panel.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    default_date = get_default_date(conn)
    fig = build_map_figure(conn, "streamflow", default_date)

    # Smoke-test the full layout (card + stores + legend).
    card = render_map_panel("streamflow", default_date, conn=conn)
    assert isinstance(card, dbc.Card), "render_map_panel() must return a dbc.Card"

    out_path = os.path.join(_ROOT, "_test_map.html")
    fig.write_html(out_path, include_plotlyjs="cdn", full_html=True, auto_open=False)

    trace_types = [t.type for t in fig.data]
    n_choropleth = trace_types.count("choropleth")
    n_scattergeo = trace_types.count("scattergeo")
    marker_count = (
        len(fig.data[1].marker.size) if n_scattergeo and fig.data[1].marker.size else 0
    )
    # 50 states + DC, one region/state pair, plus station markers
    loc_count = len(fig.data[0].locations) if n_choropleth else 0

    print(f"default date used        : {default_date}")
    print(f"traces on figure         : {trace_types}")
    print(f"choropleth states+DC     : {loc_count} (expect 51 including no-data states)")
    print(f"station markers rendered : {marker_count}")
    print(f"same figure              : choropleth={bool(n_choropleth)} scatter={bool(n_scattergeo)}"
          f" -> {'OK' if n_choropleth and n_scattergeo else 'FAIL'}")
    print(f"html written             : {out_path} ({os.path.getsize(out_path):,} bytes)")
    print("ALL CHECKS PASSED" if (n_choropleth and n_scattergeo and marker_count > 0
                                  and os.path.exists(out_path)) else "CHECK FAILURE")
