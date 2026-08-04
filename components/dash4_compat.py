"""dash4_compat.py — Dash 4.x single-output callback return-value compatibility.

Why this exists
---------------
Dash 4.4.1's server-side ``_prepare_response`` wraps the request's ``outputs``
field for single-output callbacks::

    output_value, output_spec = [output_value], [output_spec]

That is only correct when the request sent ``outputs`` as a plain dict, which
is what the bundled dash-renderer v4.x does for single-output callbacks.  But
requests that send ``outputs`` as a flat list ``[{id, property}]`` — the format
of older dash-renderers (<= 3.x) and of any hand-written POST that mirrors
them — get double-wrapped into ``[[{id, property}]]``, which Dash then
classifies as a *wildcard multi-output* and rejects unless the callback's
return value is a list/tuple of the same length::

    dash.exceptions.InvalidCallbackReturnValue: The callback
    kpi-cards-container.children output 0 is a wildcard multi-output.
    Expected the output type to be a list or tuple but got:
    Row(children=[...])

``maybe_wrap(value)`` makes single-output callbacks format-agnostic: it reads
``dash.ctx.outputs_list`` (the raw request field) and returns ``[value]`` only
for the legacy flat-list form, leaving the value untouched for the v4 renderer.
Multi-output callbacks are unaffected — their request ``outputs`` is always a
flat list and their return values are already lists, which Dash 4.x validates
correctly.

Usage
-----
    from components.dash4_compat import maybe_wrap

    @app.callback(Output("c", "children"), Input("i", "value"))
    def cb(value):
        return maybe_wrap(dbc.Row(...))
"""

from __future__ import annotations

from typing import Any

import dash


def outputs_list() -> Any:
    """The raw ``outputs`` field of the current callback request.

    Returns a dict for dash-renderer v4.x single-output callbacks, a list of
    dicts for multi-output callbacks and for legacy (<= 3.x) single-output
    requests, and ``None`` when there is no active callback context.
    """
    try:
        return dash.ctx.outputs_list
    except Exception:
        return None


def legacy_flat_list_request() -> bool:
    """True when the current request sent ``outputs`` as a flat list of dicts
    (the pre-v4 renderer format that Dash 4.4.1 mishandles)."""
    ol = outputs_list()
    return isinstance(ol, list) and bool(ol) and isinstance(ol[0], dict)


def maybe_wrap(value: Any) -> Any:
    """Return *value* shaped for the request's output format.

    For a legacy flat-list request Dash 4.4.1 demands a list/tuple back from a
    single-output callback (it treats the double-wrapped spec as a wildcard
    multi-output).  For the v4 renderer (dict request) the bare value is
    correct.  Values that are already lists/tuples are never re-wrapped, and
    ``no_update`` / ``None`` returns are safe in both modes.
    """
    if legacy_flat_list_request() and not isinstance(value, (list, tuple)):
        return [value]
    return value
