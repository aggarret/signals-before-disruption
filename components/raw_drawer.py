"""raw_drawer.py — Raw-data inspection drawer (Pass D of the Dash build).

A slide-up panel pinned to the bottom of the viewport, hidden by default, that
shows the exact USGS OGC API payload stored for one gauge-metric-day — the
"audit trail" of the whole pipeline. The JSON is pretty-printed and
syntax-highlighted server-side (no external libs): keys teal, strings green,
numbers cyan, booleans crimson, nulls amber.

It also carries an "Export CSV" button: a dcc.Download wired to a callback that
dumps every raw_observations row for that gauge-metric as a CSV.

Show / hide contract for app.py:
  * hidden by default — the drawer renders with style {"display": "none"}
  * to open it, set the drawer's style to DRAWER_VISIBLE (e.g. via the ready
    made `raw_drawer_toggle_button()`, which register_callbacks() wires so the
    toggle opens and Close closes)
  * the drawer's dcc.Store (`raw-drawer-state`) always carries the current
    {entity_id, metric, date}, so any callback can read what the drawer shows

Component IDs owned here:
    raw-drawer          the panel itself (html.Div, className="raw-drawer")
    raw-drawer-state    dcc.Store -> {entity_id, metric, date}
    raw-download-btn    "Download CSV" button
    raw-download        dcc.Download (receives the CSV)
    raw-drawer-close    "Close" button
    raw-drawer-toggle   trigger button created by raw_drawer_toggle_button()

Layout contract:
    render_raw_drawer(conn, entity_id, metric, date) -> html.Div
    register_callbacks(app) -> {"drawer_style": Output(...),
                                "download": Output(...)}
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd
import dash_bootstrap_components as dbc
from dash import Dash, Input, Output, ctx, dcc, html, no_update

import queries
from components.dash4_compat import maybe_wrap
from components.map_panel import (
    get_default_date,
    METRIC_LABELS,
    TEXT_BRIGHT,
)

# ---------------------------------------------------------------------------
# IDs / visibility
# ---------------------------------------------------------------------------
_ID_DRAWER = "raw-drawer"
_ID_STATE = "raw-drawer-state"
_ID_DOWNLOAD_BTN = "raw-download-btn"
_ID_DOWNLOAD = "raw-download"
_ID_CLOSE_BTN = "raw-drawer-close"
_ID_TOGGLE_BTN = "raw-drawer-toggle"

DRAWER_HIDDEN = {"display": "none"}
DRAWER_VISIBLE = {"display": "block"}

_OUTPUTS = {
    "drawer_style": Output(_ID_DRAWER, "style"),
    "download": Output(_ID_DOWNLOAD, "data"),
}

# ---------------------------------------------------------------------------
# JSON syntax highlighting (server-side tokenizer, no external deps)
# ---------------------------------------------------------------------------
def _highlight_json(node: Any, level: int = 0) -> List[Any]:
    """Walk a parsed JSON object and emit color-coded html.Span pieces."""
    pad, inner = "  " * level, "  " * (level + 1)
    out: List[Any] = []

    if isinstance(node, dict):
        if not node:
            return [html.Span("{}", className="tok-punct")]
        out.append(html.Span("{\n", className="tok-punct"))
        items = list(node.items())
        for i, (k, v) in enumerate(items):
            out.append(html.Span(inner, className="tok-punct"))
            out.append(html.Span(json.dumps(k), className="tok-key"))
            out.append(html.Span(": ", className="tok-punct"))
            out.extend(_highlight_json(v, level + 1))
            sep = ",\n" if i < len(items) - 1 else "\n"
            out.append(html.Span(sep, className="tok-punct"))
        out.append(html.Span(pad + "}", className="tok-punct"))
        return out

    if isinstance(node, list):
        if not node:
            return [html.Span("[]", className="tok-punct")]
        out.append(html.Span("[\n", className="tok-punct"))
        for i, v in enumerate(node):
            out.append(html.Span(inner, className="tok-punct"))
            out.extend(_highlight_json(v, level + 1))
            sep = ",\n" if i < len(node) - 1 else "\n"
            out.append(html.Span(sep, className="tok-punct"))
        out.append(html.Span(pad + "]", className="tok-punct"))
        return out

    if node is None:
        return [html.Span("null", className="tok-null")]
    if isinstance(node, bool):
        return [html.Span("true" if node else "false", className="tok-bool")]
    if isinstance(node, (int, float)):
        return [html.Span(json.dumps(node), className="tok-num")]
    return [html.Span(json.dumps(node), className="tok-str")]


def _payload_children(payload: Any) -> List[Any]:
    """Pretty-printed, highlighted children for html.Pre (raw-payload)."""
    try:
        if isinstance(payload, (dict, list)):
            obj = payload
        else:
            obj = json.loads(payload)
        return _highlight_json(obj)
    except (TypeError, ValueError):
        # Not JSON after all — show it as plain text.
        return [html.Span(str(payload), className="tok-str")]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def render_raw_drawer(
    conn: Any = None,
    entity_id: Optional[str] = None,
    metric: str = "streamflow",
    date: Optional[str] = None,
) -> html.Div:
    """The slide-up raw-payload inspection panel (hidden by default)."""
    conn = conn or queries.get_connection()
    date = str(date or get_default_date(conn))
    eid = str(entity_id) if entity_id else None
    metric_label = METRIC_LABELS.get(metric, metric)

    payload = queries.get_raw_payload(conn, eid, metric, date) if eid else None

    if payload:
        pre = html.Pre(_payload_children(payload), className="raw-payload")
        note = f"{len(payload):,} bytes · stored USGS OGC response for {date}"
    else:
        pre = html.Pre("No raw payload stored for this gauge / metric / day.",
                       className="raw-payload")
        note = "raw_observations has no record for this combination"

    header = html.Div(
        [
            html.Div(
                [
                    html.H6("Raw Data Inspection",
                            className="raw-header-title",
                            style={"margin": "0"}),
                    html.Small(f"{eid or 'no station'} · {metric_label} · {date}",
                               style={"color": "#64748b"}),
                ],
                style={"flex": "1 1 auto"},
            ),
        ],
        style={"display": "flex", "alignItems": "center", "flexWrap": "wrap"},
    )

    buttons = html.Div(
        [
            dbc.Button("⬇ Download CSV", id=_ID_DOWNLOAD_BTN,
                       size="sm", color="info", outline=True, className="me-2"),
            dbc.Button("Close", id=_ID_CLOSE_BTN, size="sm", color="secondary"),
        ],
        style={"display": "flex", "gap": "4px", "marginTop": "8px"},
    )

    return html.Div(
        [
            header,
            html.Small(note, style={"color": "#64748b", "marginTop": "2px"}),
            pre,
            buttons,
            dcc.Download(id=_ID_DOWNLOAD),
            dcc.Store(id=_ID_STATE,
                      data={"entity_id": eid, "metric": metric, "date": date}),
        ],
        id=_ID_DRAWER,
        className="raw-drawer",
        style=dict(DRAWER_HIDDEN),
    )


def raw_drawer_toggle_button(label: str = "Inspect raw data") -> dbc.Button:
    """Trigger button app.py places anywhere; wired by register_callbacks()."""
    return dbc.Button(label, id=_ID_TOGGLE_BTN, size="sm", color="info",
                      outline=True)


# ---------------------------------------------------------------------------
# Callbacks (register on the app; wire in app.py via register_callbacks)
# ---------------------------------------------------------------------------
def register_callbacks(app: Dash) -> Dict[str, Any]:
    """Wire the drawer's open/close + CSV-export callbacks.

    Requires `raw_drawer_toggle_button()` (or any element with id
    'raw-drawer-toggle') to exist in the layout — see the module docstring.

    Returns {"drawer_style": Output(raw-drawer, "style"),
             "download": Output(raw-download, "data")}.
    """
    @app.callback(
        Output(_ID_DRAWER, "style"),
        Input(_ID_CLOSE_BTN, "n_clicks"),
        Input(_ID_TOGGLE_BTN, "n_clicks"),
        prevent_initial_call=True,
    )
    def _toggle_drawer(close_clicks, toggle_clicks):
        """Close hides the panel; the toggle button re-opens it."""
        if ctx.triggered_id == _ID_CLOSE_BTN:
            return maybe_wrap(DRAWER_HIDDEN)
        if ctx.triggered_id == _ID_TOGGLE_BTN:
            return maybe_wrap(DRAWER_VISIBLE)
        return maybe_wrap(no_update)

    @app.callback(
        Output(_ID_DOWNLOAD, "data"),
        Input(_ID_DOWNLOAD_BTN, "n_clicks"),
        Input(_ID_STATE, "data"),
        prevent_initial_call=True,
    )
    def _export_csv(n_clicks, state):
        """Export every raw observation row for the current gauge-metric."""
        if not n_clicks or not state or not state.get("entity_id"):
            return maybe_wrap(no_update)
        conn = queries.get_connection()
        eid = state["entity_id"]
        metric = state.get("metric", "streamflow")
        df = conn.execute(
            f"""
            SELECT source, entity_id, observed_at, collected_at, metric,
                   parameter_code, value, unit, latitude, longitude,
                   approval_status, qualifier, raw_payload
            FROM read_parquet('{queries._RAW_GLOB}')
            WHERE entity_id = ? AND metric = ?
            ORDER BY observed_at
            """,
            [eid, metric],
        ).df()
        return maybe_wrap(dcc.send_data_frame(df.to_csv,
                                             filename=f"{eid}_{metric}_raw.csv",
                                             index=False))

    return dict(_OUTPUTS)


# ---------------------------------------------------------------------------
# Self-test: python3 components/raw_drawer.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    conn = queries.get_connection()
    d = str(get_default_date(conn))
    eid = "USGS-01184000"

    div = render_raw_drawer(conn, eid, "streamflow", d)
    assert isinstance(div, html.Div), "must return an html.Div"
    assert div.id == _ID_DRAWER
    assert div.className == "raw-drawer"
    assert div.style == DRAWER_HIDDEN, "drawer must be hidden by default"

    # the payload <pre> should be present and tokenized into spans
    pres = [c for c in div.children if isinstance(c, html.Pre)]
    assert len(pres) == 1
    payload = queries.get_raw_payload(conn, eid, "streamflow", d)
    assert payload, "expected a raw payload for USGS-01184000 on this date"
    spans = [c for c in pres[0].children if isinstance(c, html.Span)]
    classes = {s.className for s in spans}
    print(f"date used        : {d}")
    print(f"return type      : {type(div).__name__} (html.Div, id={div.id!r})")
    print(f"hidden by default: {div.style == DRAWER_HIDDEN}")
    print(f"payload bytes    : {len(payload):,}")
    print(f"highlight tokens : {len(spans)} spans -> {sorted(classes)}")

    # store carries the current selection for the CSV callback
    stores = [c for c in div.children if isinstance(c, dcc.Store)]
    assert len(stores) == 1
    print(f"state store      : {stores[0].data}")

    # CSV export path (same query the callback runs)
    df = conn.execute(
        f"""
        SELECT observed_at, value, unit, approval_status
        FROM read_parquet('{queries._RAW_GLOB}')
        WHERE entity_id = ? AND metric = ?
        ORDER BY observed_at
        """,
        [eid, "streamflow"],
    ).df()
    print(f"csv rows ready   : {len(df):,} observations for {eid}")

    # empty state (gauge without stored payload)
    div0 = render_raw_drawer(conn, "USGS-00000000", "streamflow", d)
    assert div0.style == DRAWER_HIDDEN

    # callback registration smoke test
    app = Dash(__name__)
    outs = register_callbacks(app)
    n_cb = len(app.callback_map)
    print(f"callbacks        : {n_cb} registered (expect 2)")
    print(f"outputs contract : {sorted(outs.keys())}")
    assert n_cb == 2

    print("ALL CHECKS PASSED")
