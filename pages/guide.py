"""pages/guide.py — River Personality Monitor: the Guide page ("/guide").

A dark-themed explainer for the dashboard: what the app does, why it exists,
a concrete use case, other applications, a walkthrough of every dashboard
component, the anomaly color scale, the data sources & methodology, the
technical stack, and the gauge network.

Static page — no callbacks, no Dash state. Colors match assets/style.css
(slate-900 background #0f172a, slate-800 cards #1e293b, slate-200 text
#e2e8f0, slate-400 muted #94a3b8, teal accent #14b8a6, slate-700 borders
#334155) and the anomaly palette used across the dashboard (amber / amber-teal
/ teal / teal-cyan / cyan / crimson).

Startup facts (conn, N_GAUGES, current_latest_date, current_total_rows) are
imported from app.py — safe because those names are defined above app.py's
`app = Dash(...)` line, which is when Dash imports this page.
"""

from __future__ import annotations

import os
import sys
from typing import Any, List, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, html

from app import N_GAUGES, conn, current_latest_date, current_total_rows  # noqa: E402

dash.register_page(
    __name__,
    path="/guide",
    name="Guide",
    title="River Personality Monitor — Guide",
)

# Audience toggle: Expert (current content) vs. General Audience (built by B1).
_ID_AUDIENCE = "guide-audience-toggle"
_ID_GUIDE_BODY = "guide-body-container"

# ---------------------------------------------------------------------------
# Palette (single source of truth for this page; mirrors assets/style.css)
# ---------------------------------------------------------------------------
BG = "#0f172a"        # page background
CARD = "#1e293b"      # card surface
TEXT = "#e2e8f0"      # body text
MUTED = "#94a3b8"     # secondary text
FAINT = "#64748b"     # tertiary text / subtitles
ACCENT = "#14b8a6"    # teal accent (headings)
BORDER = "#334155"    # borders / dividers

# Anomaly z-score palette (matches components/map_panel.py exactly).
Z_AMBER = "#f59e0b"
Z_AMBER_TEAL = "#7dccc4"
Z_TEAL = "#14b8a6"
Z_TEAL_CYAN = "#0dd4be"
Z_CYAN = "#06b6d4"
Z_CRIMSON = "#f43f5e"
Z_NULL = "#475569"
Z_NO_DATA = "#334155"

_BODY = {"color": TEXT, "fontSize": "14px", "lineHeight": "1.65"}
_MUTED = {"color": MUTED, "fontSize": "13px", "lineHeight": "1.6"}
_FAINT = {"color": FAINT, "fontSize": "12px"}


# ---------------------------------------------------------------------------
# Audience body builders
# ---------------------------------------------------------------------------
def _expert_body() -> List[Any]:
    """The current (verbatim) guide sections, unchanged from the original page."""
    return [
        _purpose(),
        _use_case(),
        _expert_why_tight(),
        _applications(),
        _components_guide(),
        _hydro_components_guide(),
        _color_scale(),
        _methodology(),
        _stack(),
        _data_refresh(),
        _gauge_network(),
    ]


# ---------------------------------------------------------------------------
# General-audience content (plain-language, question-driven) - B1
# B0 wires this into the on-page Expert <-> General Audience toggle via
# _general_body() + the expert cards. Reuse the same _section_card / _p /
# _h6 / _bullet / _sub helpers and palette so it renders consistently.
# ---------------------------------------------------------------------------
def _gen_trust_note(text: str) -> html.Div:
    """One-line "how we know" trust footnote (muted, small, italicized)."""
    return _sub("\u2713 " + text, style={"marginTop": "10px", "fontStyle": "italic"})


def _gen_hydro_components() -> dbc.Card:
    """Plain-language tour of the 5 Hydro Coupling panels (top-to-bottom).

    General-audience companion to the Hydro Coupling page (`/hydro`). Covers
    the 14 tight gauges whose streamflow moves in step with regional hydro
    power, panel by panel. No jargon — entries stay conversational. Each
    panel gets an `_h6` name plus what it is / shows, how to use it, and
    what to look for.
    """
    body = [
        _p(
            "This page keeps an eye on 14 rivers whose monthly water flow "
            "tends to move in step with the electricity their region "
            "generates from water. Here's a top-to-bottom tour of the five "
            "panels you'll see, in the order they appear on the page."
        ),
        _h6("1. The Coupling Map"),
        _p(
            "What it is: a map of the whole river network, with the 14 "
            "stand-out rivers drawn as colorful dots and the rest of the "
            "field shown faded in the background for context."
        ),
        _p(
            "What it shows: the teal dots are the 14 rivers this page "
            "tracks. Bigger and darker-teal dots mean that river's flow "
            "keeps step with its region's hydro power more tightly; lighter, "
            "smaller dots are still linked, just less strongly."
        ),
        _p(
            "How to use it: click any teal dot to select that river. The "
            "strip chart, the waveform grid, and the drill-down below all "
            "update to the river you picked, and a white ring marks your "
            "choice."
        ),
        _p(
            "What to look for: where the deep-teal (strongly linked) dots "
            "cluster — often in hydro-heavy regions like the Pacific "
            "Northwest or the Southeast."
        ),
        _h6("2. The Ranked Coupling Strip"),
        _p(
            "What it is: a simple top-to-bottom list of the same 14 rivers, "
            "ranked by how tightly each one's flow moves with its region's "
            "hydro power."
        ),
        _p(
            "What it shows: the river at the top is the tightest match, and "
            "the one at the bottom is the loosest of the 14. The bar length "
            "and its teal shade both reflect the strength of the link."
        ),
        _p(
            "How to use it: click a bar to select that river — handy when "
            "you'd rather work from a sorted list than hunt on a map."
        ),
        _p(
            "What to look for: the anchors near the top — like the "
            "Columbia feeding a major Northwest grid — and which rivers "
            "are the strongest of the fourteen."
        ),
        _h6("3. The Waveform Grid"),
        _p(
            "What it is: a 4×4 grid of small line charts, one per tracked "
            "river, showing how the river's flow and the region's hydro "
            "output have moved together over time."
        ),
        _p(
            "What it shows: in each tiny chart, the teal line is the river's "
            "flow and the amber line is the region's hydro power, both "
            "reshaped so big rivers and small rivers are on the same scale. "
            "Where the two lines ride together, that river is a visibly good "
            "match."
        ),
        _p(
            "How to use it: this is the \"all 14 at a glance\" panel — "
            "read across the grid to see which rivers stay quiet and which "
            "bounce in time with their power."
        ),
        _p(
            "What to look for: the small charts where the teal line and the "
            "amber line hug each other — those are the tightly coupled "
            "rivers. Where the two wander apart, the river is less of a "
            "hydro tell."
        ),
        _h6("4. The Drill-Down"),
        _p(
            "What it is: a deep-dive into whichever river you've selected, "
            "with two views you can flip between."
        ),
        _p(
            "The overlay view: this river's teal flow and the region's "
            "amber hydro power on the same chart, with a soft green shading "
            "wherever the two move in the same direction — a quick check of "
            "whether they are moving together."
        ),
        _p(
            "The next-month view: each point is one month's flow plotted "
            "against the following month's hydro power, with a trend line, "
            "so you can ask whether a watery month tends to lead toward a "
            "stronger-power month that follows."
        ),
        _p(
            "How to use it: pick a river from the map or list, then flip "
            "between the two views. The title bar shows the river's name so "
            "you always know whose drill-down you're in."
        ),
        _p(
            "What to look for: the shaded sweeps in the overlay where teal "
            "and amber rise and fall together, and whether the next-month "
            "scatter forms a clear upward-sloping line."
        ),
        _h6("5. The Daily-Power Note"),
        _p(
            "What it is: a small note near the bottom explaining that, for "
            "a handful of rivers served by the Bonneville Power "
            "Administration (BPA), the power data is enriched with near- "
            "daily readings from BPA on top of the usual monthly reports."
        ),
        _p(
            "What to look for: this note tells you which rivers run on the "
            "fresher daily track and which still rely on the monthly "
            "report — so you can tell how current each river's numbers "
            "really are."
        ),
    ]
    return _section_card(
        "What's on the Hydro Coupling page? \u2014 A guided tour",
        body,
        icon="\U0001f4a7",
    )


def _gen_dashboard_components() -> dbc.Card:
    """Plain-language tour of the 9 Dashboard panels (top-to-bottom, left-
    to-right). Mirrors _gen_hydro_components(): each panel gets an `_h6`
    name plus what it is / what it shows, how to use it, and what to look
    for. No jargon — entries stay conversational.
    """
    body = [
        _p(
            "Here's a top-to-bottom tour of the Dashboard page, in the "
            "order the panels actually appear on screen — so the first "
            "time you open it, nothing feels like a mystery."
        ),
        _h6("1. The Anomaly Scorecards"),
        _p("What it is: a “greatest hits” panel of the most extreme days on "
           "record, ranked from #1 down."),
        _p("What it shows: the single most unusual day in the whole 20-year "
           "dataset, up front — plus, on hover, the next nine most extreme "
           "days — each with a count of how many rivers were acting "
           "strangely that day at once."),
        _p("How to use it: hover over the top card to expand the top-10 list "
           "— it's always live and doesn't depend on your filters."),
        _p("What to look for: dates where a whole handful of rivers fired at "
           "the same time — that's the fingerprint of a big storm or heatwave "
           "sweeping through many watersheds."),
        _h6("2. The Monthly Anomaly Bar Chart"),
        _p("What it is: a long bar chart, one bar for every month since 2004, "
           "showing how busy each month was."),
        _p("What it shows: teal bars are calm months; red bars are months "
           "where an unusual number of rivers were off their normal all at "
           "once. Like the scorecards, it ignores your filters and tells the "
           "full-record story."),
        _p("How to use it: hover any bar to see that month's total and which "
           "rivers were involved."),
        _p("What to look for: whether the red bars are getting more common in "
           "any season over the years — a pattern worth noticing."),
        _h6("3. The Filter Bar"),
        _p("What it is: the control bar that stays pinned to the top of the "
           "page while you scroll, so the controls are always in reach."),
        _p("What it shows: two choices — which thing to look at (river flow, "
           "water level, or water temperature) and which day to inspect. "
           "Everything below the bar updates to match."),
        _p("How to use it: pick a measurement and a date, then watch the whole "
           "page re-draw for that choice. The date picker opens into decade → "
           "year → month so skipping across 20+ years is quick."),
        _p("What to look for: switching measurements can tell a very different "
           "story — a day when water levels look calm might hide unusual "
           "flow, and vice versa."),
        _h6("4. The KPI Cards"),
        _p("What it is: four big headline numbers that summarize the whole "
           "river network for the moment you've picked."),
        _p("What it shows: how many rivers are running unusually high right "
           "now, which one is climbing fastest (and by how much), which "
           "region is running driest, and whether the sensors are reporting "
           "properly."),
        _p("How to use it: no clicking needed — the cards update on their own "
           "whenever you change the measurement or date."),
        _p("What to look for: a red count above zero, or a big “fastest riser” "
           "number — those are your cue that it's worth digging in below."),
        _h6("5. The National Overview Map"),
        _p("What it is: a color-coded map of the whole country — each state "
           "shaded by how its rivers are doing, with every river a dot sized "
           "by how much water it carries."),
        _p("What it shows: at a glance, which parts of the country are calm "
           "and which are running unusually high or low, plus how big each "
           "river is. The colors follow the same plain story as the rest of "
           "the page: red = a lot of water, brown = running dry."),
        _p("How to use it: click a state to focus on that region's story, or "
           "click a river dot to pull up its details in the panels below (a "
           "white ring marks your pick). Zoom and pan stick with you as the "
           "data updates."),
        _p("What to look for: a tight cluster of deep-red dots — a region "
           "running very high — or a spread of brown — a region running dry. "
           "The biggest dots are the major rivers."),
        _h6("6. The Regional Rollup Table"),
        _p("What it is: a compact table, one row per region, that sums up how "
           "each part of the country is doing today."),
        _p("What it shows: for every region, how many of its gauges are "
           "reporting, how many rivers are behaving unusually, and the "
           "region's overall mood — teal for running high, amber for running "
           "low — ranked so the most stressed region sits on top."),
        _p("How to use it: click a region row to focus the Fastest Risers "
           "table below on that part of the country."),
        _p("What to look for: which region concentrates today's action, and a "
           "strongly amber (low) region — a possible drought signal."),
        _h6("7. The Fastest Risers Table"),
        _p("What it is: the top-five rivers in the region you've selected that "
           "are changing fastest right now."),
        _p("What it shows: which rivers are climbing (cyan) or dropping "
           "(amber) the quickest over a three-day window, together with each "
           "one's current flow."),
        _p("How to use it: pick a region in the rollup table above to light "
           "this panel up — it stays quiet until you do."),
        _p("What to look for: a big cyan “climbing fast” number — that's a "
           "flood-watch signal to take seriously."),
        _h6("8. The Hydrograph"),
        _p("What it is: one river's personal diary — a chart of a single "
           "river's daily story, set against what that river normally does on "
           "those same dates."),
        _p("What it shows: whether the river is running above or below its "
           "usual band for the season, how fast it's rising or falling, and "
           "its current reading compared with its own history."),
        _p("How to use it: pick a river on the map, then use the range buttons "
           "(1M, 3M, 6M, 1Y, All) to zoom the window in or out."),
        _p("What to look for: the white line poking outside the shaded normal "
           "band, or a run of gray ×'s where the sensors went quiet — both "
           "are worth understanding."),
        _h6("9. The Personality Cards"),
        _p("What it is: three cards that describe the character of the river "
           "you've selected."),
        _p("What it shows: how “flashy” the river tends to be, where today's "
           "level sits in its whole history, and how close it is to an "
           "all-time record."),
        _p("How to use it: just select a river on the map — the cards follow "
           "your choice automatically."),
        _p("What to look for: context. A naturally lively river acting lively "
           "is routine; a normally steady river suddenly near its all-time "
           "high is genuinely notable."),
        _h6("10. The Raw Data Drawer"),
        _p("What it is: a drawer that slides up from the bottom to show the "
           "exact, unedited source information behind whichever number you're "
           "looking at."),
        _p("What it shows: the fine print on any reading — whether the value "
           "is confirmed or a provisional estimate, the units, and the "
           "original source record the page is built on."),
        _p("How to use it: click “Inspect raw data” to open the drawer and “"
           "Download CSV” to grab a copy of the underlying records."),
        _p("What to look for: this is the honesty layer — proof that every "
           "number on the page traces back to a real, published measurement "
           "you can check yourself."),
    ]
    return _section_card(
        "What's on the dashboard? — A guided tour",
        body,
        icon="🧭",
    )


def _general_body() -> List[Any]:
    """General-audience guide body: question -> answer -> why it matters.

    Returns a non-empty list of Dash components (section cards) matching the
    expert sections' component style. Each heading is a question a general
    reader would actually ask. No formulas, no z-score / standard-deviation /
    lag jargon - plain language, trust-facts, and three tested analogies.
    """
    s_what = _section_card(
        "What is this dashboard?",
        [
            _p(
                "It's a daily health check for 52 rivers across the United "
                "States, all answering one question: is each river behaving "
                "like itself today? Instead of just \u201chow high is the "
                "water?\u201d, it asks the smarter question \u2014 \u201chow "
                "unusual is this water, for this river, for this time of "
                "year?\u201d"
            ),
            _p(
                "Every number on the page is a comparison: today's river vs. "
                "what that same river has normally done on this calendar day "
                "for the past 20 years. A river can be high in absolute "
                "terms but completely normal for its season \u2014 or it can "
                "be quietly bizarre. This dashboard is built to tell the "
                "difference at a glance."
            ),
        ],
        icon="\U0001f30a",
    )

    s_why = _section_card(
        "Why should I care?",
        [
            _p("Rivers touch almost every part of daily life. Here's why it "
               "matters if one is behaving unusually:"),
            _bullet("Water supply", "the rivers here feed reservoirs and "
                    "drinking-water systems. An unusual signal now can mean "
                    "a town's tap water is in trouble before the headlines "
                    "are."),
            _bullet("Flood heads-up", "a river running far above its own "
                    "normal can be an early, concrete warning that a flood "
                    "might be on the way."),
            _bullet("Clean power", "flowing water spins the turbines that "
                    "make hydroelectricity. When rivers run strong, the grid "
                    "has more clean power; when they run low, it has less "
                    "(more on this below)."),
            _bullet("Recreation & outdoors", "fishing, rafting, boating, and "
                    "river-lovers' days hinge on whether a river is at a "
                    "normal level for the season."),
            _p("In short: rivers quietly decide a lot about water, safety, "
               "power, and fun \u2014 so knowing when one is off its own "
               "normal is genuinely useful."),
        ],
        icon="\u2753",
    )

    s_colors = _section_card(
        "What do the colors mean?",
        [
            _p(
                "Think of the map like a traffic light for water. Every "
                "gauge is colored by how far it is from its own normal for "
                "the season \u2014 and we translate that into plain words:"
            ),
            _bullet("Blue / green", "close to normal \u2014 the river is "
                    "behaving like itself for this time of year."),
            _bullet("Orange / red", "unusually high \u2014 the river is "
                    "carrying more water than it normally does on this date, "
                    "from a bit high to extreme."),
            _bullet("Brown", "unusually low \u2014 the river is running below "
                    "its normal for this date, a possible drought signal."),
            _p(
                "That's the whole color story: teal means \u201clooks "
                "normal,\u201d cyan/red means \u201ccarrying a lot,\u201d amber "
                "means \u201crunning dry-ish.\u201d The deeper the shade, the "
                "more unusual. You never need to decipher a number to get "
                "the picture."
            ),
        ],
        icon="\U0001f6a6",
    )

    s_unusual = _section_card(
        "What does \u201cunusual\u201d mean here?",
        [
            _p(
                "Unusual always means: compared to that river's own 20-year "
                "normal for this exact date. Each river is its own baseline. "
                "A mountain creek can be low in absolute terms but totally "
                "normal for early spring; a big river can be at a record "
                "high that's still \u201cexpected\u201d in flood season. We "
                "compare each river to itself, on the same calendar day, "
                "across two decades of its own history."
            ),
            _h6("Think of your own body temperature"),
            _p(
                "98.6\u00b0 is \u201cnormal,\u201d but what's normal for you "
                "depends on the season, the time of day, what you've been "
                "doing. A river's \u201cnormal\u201d works the same way \u2014 "
                "it changes with the calendar. So this dashboard doesn't ask "
                "\u201cis the water high?\u201d It asks \u201cis this river "
                "high for it, on this date, after decades of its own "
                "history?\u201d That's the difference between a flood alarm "
                "and a false alarm."
            ),
            _gen_trust_note(
                "How we know: based on daily USGS streamflow records; "
                "\u201cnormal\u201d = the middle range of that river's past "
                "readings on the same calendar day, over a fixed "
                "2004\u20132023 baseline."
            ),
        ],
        icon="\U0001f321\ufe0f",
    )

    s_dashboard_tour = _gen_dashboard_components()
    s_hydro_tour = _gen_hydro_components()

    s_power = _section_card(
        "How do rivers talk to the power grid?",
        [
            _p(
                "Here's the surprising part: a river can read the near-term "
                "power picture. Dams turn flowing water into electricity "
                "\u2014 more water (and a taller drop) means more power. So "
                "a river is a live fuel gauge for the grid: when it runs "
                "strong, there's more water to spin the turbines."
            ),
            _h6("14 rivers move in step with the region's hydro power"),
            _p(
                "Some rivers are like hydroelectric on/off switches; others "
                "aren't. Hydro plants make power from water \u2014 roughly, "
                "the more water flowing through a dam and the higher the "
                "drop, the more electricity. So when a river runs higher or "
                "lower than usual, its dams make more or less power in that "
                "same month, and the two move together. That's why a few "
                "gauges \u2014 mostly rivers feeding hydro-heavy grids like "
                "the Pacific Northwest's Columbia system (BPA) or the TVA "
                "dams of the Southeast \u2014 line up tightly with regional "
                "hydro output. Others stay quiet because hydro is a small "
                "slice of their grid's power, their dams store and release "
                "on their own schedule, or the river is just one small "
                "tributary among many, so its ups and downs get averaged "
                "out."
            ),
            _h6("A dipstick, not a forecast"),
            _p(
                "The 14 rivers we track are like a dipstick \u2014 check them "
                "and you get a rough read on near-term hydro power before "
                "the monthly report lands. Not a weather forecast; more like "
                "checking the gas tank."
            ),
            _h6("Real examples"),
            _bullet("Columbia \u2192 BPA", "the Columbia at The Dalles, OR "
                    "tracks Pacific Northwest hydro very tightly \u2014 a "
                    "textbook case of a river feeding a hydro-heavy grid."),
            _bullet("Southeast \u2192 TVA", "rivers feeding the Tennessee "
                    "Valley Authority's dam system also move in step with "
                    "that region's hydro output."),
            _h6("Important: co-movement, not prediction"),
            _p(
                "These rivers and the region's hydro power rise and fall "
                "together in the same month \u2014 because the same rainfall "
                "drives both. They share a cause; the river is not "
                "\u201ccausing\u201d a specific megawatt, and it doesn't "
                "forecast next month. Think of it as two things moving in "
                "the same current, not one telling the other what to do."
            ),
            _h6("Why some rivers stay quiet"),
            _p(
                "A dam upstream is like a sponge: it soaks up spring floods "
                "and releases stored water steadily. That smooths a river's "
                "ups and downs, so a gauge below a dam no longer shows the "
                "true heartbeat of the watershed \u2014 it shows what the "
                "operator chooses to release. That's a big reason only 14 of "
                "our 52 rivers align tightly with hydro power; the rest have "
                "their signal muffled."
            ),
        ],
        icon="\u26a1",
    )

    s_cannot = _section_card(
        "What can it NOT tell me?",
        [
            _p("Every tool has honest limits. Here's what this dashboard "
               "does not claim to do:"),
            _bullet("It doesn't predict the future", "the co-movement we "
                    "show is a same-month link, not a forecast. A river "
                    "moving with hydro today does not tell you next month's "
                    "weather or power."),
            _bullet("Storage dams smooth the signal", "below a big dam, the "
                    "river reflects what an operator releases, not the raw "
                    "weather pulse of the watershed \u2014 so some rivers "
                    "look \u201ctoo calm\u201d for what's really happening "
                    "upstream."),
            _bullet("Monthly grain only", "we compare each month's water to "
                    "that same month's power. Day-by-day, minute-by-minute "
                    "movements are beyond this tool's scope."),
            _bullet("It's about behavior, not absolute levels", "a river "
                    "can be \u201cnormal\u201d yet still be high in plain "
                    "terms \u2014 this dashboard measures "
                    "unusual-for-this-river, not raw size."),
            _p("Read the colors as a heads-up, and pair them with local "
               "warnings and officials for big decisions. It's a fast, "
               "trustworthy signal \u2014 not the final word."),
        ],
        icon="\U0001f6ab",
    )

    s_sources = _section_card(
        "Where does this come from?",
        [
            _bullet("USGS", "river data \u2014 daily streamflow readings "
                    "from U.S. Geological Survey gauges on these 52 "
                    "rivers."),
            _bullet("EIA", "power data \u2014 electricity generation and "
                    "regional hydro output from the U.S. Energy Information "
                    "Administration."),
            _p(
                "Numbers you can trust: live data from USGS gauges and EIA "
                "reports, compared against each river's own 20-year "
                "history. No guessing, no smoothed-over estimates \u2014 "
                "named sources, named method, fixed period (2004\u20132023)."
            ),
            _gen_trust_note(
                "Method, in one line: we matched each month's water to that "
                "same month's power output and looked for rivers that rise "
                "and fall together with their region's hydro."
            ),
        ],
        icon="\U0001f5c2\ufe0f",
    )

    return [s_what, s_why, s_colors, s_unusual, s_dashboard_tour, s_hydro_tour,
            s_power, s_cannot, s_sources]





# ---------------------------------------------------------------------------
# Section helpers
# ---------------------------------------------------------------------------
def _section_card(title: str, body_children: List[Any],
                  icon: str = "") -> dbc.Card:
    """One guide section: teal header + dark body card."""
    return dbc.Card(
        [
            dbc.CardHeader(
                html.H5(
                    f"{icon} {title}" if icon else title,
                    style={"color": ACCENT, "margin": "0", "fontWeight": "700"},
                ),
                style={"backgroundColor": CARD, "borderBottom": f"1px solid {BORDER}"},
            ),
            dbc.CardBody(body_children,
                         style={"backgroundColor": CARD, "padding": "1.1rem 1.25rem"}),
        ],
        style={"marginBottom": "16px", "border": f"1px solid {BORDER}",
               "borderRadius": "8px", "backgroundColor": CARD},
    )


def _p(text: str, style: Any = None) -> html.P:
    return html.P(text, style={**_BODY, **(style or {})})


def _sub(text: str, style: Any = None) -> html.Div:
    return html.Div(text, style={**_MUTED, **(style or {})})


def _h6(text: str) -> html.H6:
    return html.H6(text, style={"color": TEXT, "fontWeight": "700",
                                "marginTop": "14px", "marginBottom": "6px"})


def _bullet(label: str, text: str, color: str = ACCENT) -> html.Div:
    """Labeled bullet: accent-colored marker + bold label + muted text."""
    return html.Div(
        [
            html.Span("▸ ", style={"color": color, "fontWeight": "700"}),
            html.Span(f"{label}: " if label else "",
                      style={"color": TEXT, "fontWeight": "600"}),
            html.Span(text, style={"color": MUTED}),
        ],
        style={"marginBottom": "8px", "fontSize": "13px", "lineHeight": "1.6"},
    )


def _number_step(n: int, text: str) -> html.Div:
    """Numbered workflow step: teal number chip + muted body text."""
    return html.Div(
        [
            html.Span(
                str(n),
                style={
                    "display": "inline-flex", "alignItems": "center",
                    "justifyContent": "center", "minWidth": "26px", "height": "26px",
                    "borderRadius": "50%", "backgroundColor": "rgba(20,184,166,0.15)",
                    "color": ACCENT, "fontWeight": "700", "fontSize": "13px",
                    "marginRight": "10px", "flex": "0 0 auto",
                },
            ),
            html.Span(text, style={"color": MUTED, "fontSize": "13px",
                                   "lineHeight": "1.6"}),
        ],
        style={"display": "flex", "alignItems": "flex-start", "marginBottom": "10px"},
    )


def _swatch(color: str, label: str, note: str = "") -> html.Div:
    """One color-scale row: square swatch + label + optional note."""
    return html.Div(
        [
            html.Span("■", style={"color": color, "fontSize": "20px",
                                  "marginRight": "10px", "lineHeight": "1"}),
            html.Span(label, style={"color": MUTED, "fontSize": "13px",
                                    "fontWeight": "600"}),
            html.Span(f"  {note}", style={"color": FAINT, "fontSize": "12px"})
            if note else None,
        ],
        style={"display": "flex", "alignItems": "center", "marginBottom": "8px"},
    )


# ---------------------------------------------------------------------------
# Component-guide row (2-column: icon + name | description)
# ---------------------------------------------------------------------------
def _component_row(icon: str, name: str, shows: str, data: str,
                   interact: str, look_for: str) -> html.Div:
    """One dashboard-component row: left = icon + name, right = description."""
    def _line(kind: str, color: str, text: str) -> html.Div:
        return html.Div(
            [
                html.Span(f"{kind} — ", style={"color": color, "fontWeight": "600",
                                               "fontSize": "12px"}),
                html.Span(text, style={"color": color, "fontSize": "12px"}),
            ],
            style={"marginBottom": "5px", "lineHeight": "1.55"},
        )

    return html.Div(
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.Div(icon, style={"fontSize": "22px",
                                              "marginBottom": "4px"}),
                        html.Div(name, style={
                            "color": TEXT, "fontWeight": "700", "fontSize": "14px",
                        }),
                    ],
                    xs=12, md=3,
                    style={"paddingTop": "2px"},
                ),
                dbc.Col(
                    [
                        _line("What it shows", ACCENT, shows),
                        _line("Data", FAINT, data),
                        _line("Interact", FAINT, interact),
                        _line("Look for", FAINT, look_for),
                    ],
                    xs=12, md=9,
                ),
            ],
            class_name="g-2",
        ),
        style={"padding": "10px 2px", "borderBottom": f"1px solid {BORDER}"},
    )


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def _hero() -> html.Div:
    return html.Div(
        [
            html.H1("🌊 River Personality Monitor", style={
                "color": ACCENT, "fontWeight": "800", "fontSize": "36px",
                "textAlign": "center", "marginBottom": "4px",
            }),
            html.Div("A Guide to the Dashboard", style={
                "color": MUTED, "fontSize": "16px", "textAlign": "center",
                "marginBottom": "16px", "fontWeight": "600",
            }),
            html.Div(
                f"Signals Before Disruption · {N_GAUGES} USGS gauges · "
                "20-year baselines",
                style={"color": FAINT, "fontSize": "13px", "textAlign": "center",
                       "marginBottom": "20px", "fontWeight": "600",
                       "letterSpacing": "0.02em"},
            ),
            _p(
                "The River Personality Monitor is a daily-updated operations "
                "dashboard that characterizes how 52 USGS river gauges across "
                "8 U.S. regions are behaving right now, relative to what those "
                "same rivers have done every day for the past 20 years. Instead "
                "of answering “is the river high?” it answers “is this river "
                "behaving like itself?” — is it flashy today, sitting near a "
                "record, rising fast, or quietly trending toward drought? It is "
                "built for water-resource managers, hydrologists, emergency "
                "managers, and anyone who wants statistical context around a "
                "river reading: every number on the page is an anomaly score, a "
                "percentile, or a rate of change computed against a fixed "
                "2004–2023 seasonal baseline. This is not a simple flood map; it "
                "is a personality profile of the nation's rivers, updated per "
                "day, drillable down to the raw USGS API payload that backs "
                "every datapoint.",
                style={"maxWidth": "900px", "margin": "0 auto", "textAlign": "center",
                       "fontSize": "15px"},
            ),
        ],
        style={"padding": "30px 12px 22px"},
    )


def _purpose() -> dbc.Card:
    body = [
        _p(
            "The app characterizes river behavior across 52 USGS gauges — "
            "drainage areas from 55 mi² (the Naselle River, WA) to 697,000 mi² "
            "(the Mississippi at St. Louis) — using seasonal z-score anomalies. "
            "For each gauge, each metric, and each day of the year, it knows "
            "the historical mean (μ) and standard deviation (σ) of that day's "
            "flow from a fixed 20-year baseline (2004–2023). Every observed "
            "value is compared to that expectation and expressed as "
            "z = (observed − μ) / σ — the number of standard deviations away "
            "from normal. |z| ≥ 2.5 marks a statistically unusual day, and the "
            "whole dashboard is built around surfacing, ranking, and drilling "
            "into those days."
        ),
        _h6("Why “personality”"),
        _p(
            "Rivers have character. A mountain stream responds to a storm in "
            "hours (flashy); the Mississippi takes days (buffered). Some rivers "
            "flirt with their all-time records routinely; others never come "
            "close. The app quantifies this character with three per-gauge "
            "measures:"
        ),
        _bullet("Flashiness", "the Richards-Baker Index per calendar year — "
                "sum(|daily change|) ÷ sum(daily average). A value near 1.0 "
                "means the river's day-to-day swings are as large as its "
                "typical flow; values under 0.1 mean a very stable, buffered "
                "river."),
        _bullet("Flow percentile", "where today's value sits vs. every "
                "observation 2004–2026 (rank-based; ties share a percentile)."),
        _bullet("Record proximity", "today's value ÷ the gauge's all-time "
                "maximum observed daily average."),
        _sub("Read together, these cards answer: “is this spike normal for this "
             "river, or is this river just naturally dramatic?”",
             style={"marginTop": "6px"}),
        _h6("The statistical approach in plain language"),
        _number_step(1, "Fixed 20-year baseline (2004–2023). For every "
                        "(gauge, metric) pair, the pipeline computes μ and σ "
                        "for each day-of-year using a ±7-day circular window — "
                        "e.g., the baseline for March 1 includes Feb 22 – Mar 8 "
                        "across all 20 years, wrapping around year boundaries. "
                        "That is up to 20 × 15 = 300 observations per "
                        "day-of-year."),
        _number_step(2, "Seasonal expectation, not a flat average. A z-score in "
                        "January is computed against January's baseline, so "
                        "snowmelt season and baseflow season each get their own "
                        "“normal.”"),
        _number_step(3, "Anomaly score. For every observed day: "
                        "z = (average − μ) / σ, using sample standard deviation "
                        "(ddof=1). σ = 0 or missing → anomaly is null (no "
                        "signal), which the UI renders as slate gray."),
        _number_step(4, "Event threshold |z| ≥ 2.5. Roughly the 99th percentile "
                        "of normal variation — under a standard normal "
                        "distribution, |z| ≥ 2.5 happens only ~1.2% of the time "
                        "(~0.6% per tail). The app deliberately does not flag "
                        "z = 1.5 or 2.0: those are “a bit high.” Flowing at "
                        "2.5σ+ above its seasonal mean means the river is doing "
                        "something genuinely unusual — the kind of signal worth "
                        "a phone call. (In practice the raw anomaly "
                        "distribution is asymmetric — min −27.3, median −0.26, "
                        "max 74.7 — because floods are unbounded above while "
                        "flow is floored at zero.)"),
    ]
    return _section_card("Purpose", body, icon="🎯")


def _use_case() -> dbc.Card:
    body = [
        _p(
            "Imagine you manage water resources for a multi-state region. "
            "Every morning you need to answer one question: is anything wrong "
            "with the rivers today? The dashboard is designed to answer that "
            "in under 90 seconds -- and then let you drill as deep as you need."
        ),
        _h6("The morning scan (today, right now)"),
        html.Div(
            [
                _number_step(1, "Open the dashboard. The date filter defaults to "
                                "the most recent day with at least 50% gauge "
                                "coverage -- that is \u2018today\u2019 in any "
                                "meaningful sense. The map, KPI cards, and region "
                                "table are already rendering for that date. You "
                                "do not need to touch the date picker to check "
                                "current conditions."),
                _number_step(2, "Read the KPI cards (top row). Extreme Events "
                                "tells you how many gauges are at |z| >= 2.5 right "
                                "now. Fastest Riser names the gauge climbing "
                                "fastest and by how much. Most Below Normal flags "
                                "the region with the lowest mean anomaly. Data "
                                "Health confirms the gauges are reporting. In a "
                                "quiet day, all four are calm; in an active day, "
                                "the crimson numbers jump out immediately."),
                _number_step(3, "Scan the national map. The choropleth colors "
                                "each state by its region\u2019s average anomaly; "
                                "the 52 station markers (size proportional to "
                                "sqrt(flow)) are colored by each gauge\u2019s own "
                                "z-score. You instantly see where the country is "
                                "teal (normal), cyan (high), crimson (extreme "
                                "high), or amber (extreme low). A cluster of "
                                "crimson in one region is your first signal."),
                _number_step(4, "Notice a cluster. Click a state on the "
                                "choropleth (or a region row in the Regional "
                                "Rollup) to filter the Fastest Risers panel to "
                                "that region. The rollup shows, per region: "
                                "Gauges Reporting, Events (|z|>=2.5), and Avg "
                                "Anomaly, sorted with the most stressed region on "
                                "top."),
                _number_step(5, "See the Fastest Risers for that region -- the "
                                "top-5 gauges by 3-day rise rate, cyan for rising "
                                "and amber for falling. A big cyan number is a "
                                "flood-watch trigger: the gauge is climbing fast "
                                "and the column tells you how fast."),
                _number_step(6, "Click a gauge on the map. The hydrograph opens "
                                "for that station: the observed daily series "
                                "(white) over the seasonal baseline \u03bc with "
                                "\u00b11\u03c3 and \u00b12\u03c3 teal bands, "
                                "spanning the 3 months ending at the selected "
                                "date, with colored markers on every |z| >= 2.5 "
                                "day and 3-day rise-rate bars below. The stats "
                                "row shows current value, anomaly z, flow "
                                "percentile, and record proximity -- all for "
                                "today."),
                _number_step(7, "Check the personality cards. Is this gauge "
                                "naturally flashy (High/Very High R-B Index), or "
                                "is this spike out of character? Is it at 92% of "
                                "its all-time record (cyan -> approaching record) "
                                "or past the 95% crimson zone? That distinction "
                                "changes how seriously you take a single reading."),
                _number_step(8, "Open the raw data drawer (\u2018Inspect raw "
                                "data\u2019 at the bottom of the page). The "
                                "slide-up panel shows the exact USGS OGC API JSON "
                                "payload stored for that gauge-metric-day -- "
                                "syntax-highlighted server-side -- so you can "
                                "verify approval status, qualifiers, and units "
                                "before acting. Download CSV exports every raw "
                                "observation row for that gauge-metric."),
            ],
            style={"marginTop": "8px", "marginBottom": "6px"},
        ),
        _h6("Historical context (when you need it)"),
        _p(
            "Above the filter bar, two panels are deliberately filter-"
            "independent: the Anomaly Scorecards and the Monthly Anomaly Bar "
            "Chart. These show the full 2004-2026 record -- the #1 most "
            "anomalous date in the dataset (e.g., Sep 19, 2004 with 22 "
            "anomalous events) and 272 months of anomaly counts. They are "
            "context, not the entry point. When today\u2019s scan surfaces "
            "something unusual, these panels let you ask: how does today "
            "compare to the worst days on record? Use the date picker to "
            "navigate to any historical day and the entire dashboard "
            "re-renders for that date -- same map, same hydrograph, same "
            "personality cards -- so you can compare a historical flood day "
            "side-by-side with today using identical visualizations."
        ),
        _h6("Decisions this informs"),
        _bullet("Reservoir releases", "a fast riser + 2.5-sigma+ event + "
                "near-record proximity justifies pre-release before the flood "
                "wave arrives."),
        _bullet("Flood watches", "anomaly clusters across a region plus high "
                "rise rates are a concrete, defensible early-warning trigger."),
        _bullet("Drought monitoring", "sustained negative z-scores over "
                "months -- visible in the monthly anomaly chart -- separate "
                "\u2018low for August\u2019 from \u2018low for March.\u2019"),
        _bullet("Data QA", "gap markers and the Data Health KPI distinguish "
                "a sensor outage from a dry river."),
        _bullet("Hydropower coupling", "the Hydro Coupling page ranks the 14 "
                "tight-tier gauges where monthly streamflow tracks regional "
                "hydro generation (Spearman |ρ| ≥ 0.5). A gauge that is both "
                "extreme on this dashboard (|z| ≥ 2.5) and tight-coupled is "
                "doubly meaningful — a flood/drought anomaly that also swings "
                "generation. Example: the Columbia at The Dalles, OR (~0.86) "
                "feeds Bonneville Power Administration directly, so the same "
                "Morning Scan that flags an extreme event there also flags a "
                "likely generation move."),
    ]
    return _section_card("Use Case: A Water Resources Manager\u2019s "
                         "Morning Scan", body, icon="\U0001f9ed")


def _expert_why_tight() -> dbc.Card:
    """Expert section (B2): why these 14 gauges couple to hydro generation.

    Method-heavy companion to the Hydropower coupling bullet in the Use Case.
    Uses the same helpers (_p / _h6 / _bullet / _sub) as the rest of the page.
    """
    body = [
        _p(
            "Coupling is the expected consequence of hydroelectric physics: "
            "P = \u03c1\u00b7g\u00b7Q\u00b7H\u00b7\u03b7 \u2014 power equals water "
            "density \u00d7 gravity \u00d7 flow (Q) \u00d7 head (H) \u00d7 "
            "efficiency (\u03b7). To good approximation P \u2248 flow \u00d7 head, "
            "so a wetter-than-normal river delivers more water and (often) more "
            "head to the machines, and its dams generate more in the same "
            "calendar month; a drier river generates less. That physical basis "
            "is why 14 of the 52 gauges move with their region's hydro output. "
            "Each gauge's Spearman anomaly correlation is computed against its "
            "EIA region-aggregate HYC (hydroelectricity) series \u2014 a measure "
            "of how much of that river's monthly flow variance survives, at lag-0, "
            "into regional hydro generation."
        ),
        _h6("Why every tight gauge peaks at lag 0 (co-movement, not prediction)"),
        _p(
            "All 14 tight gauges peak at lag 0: flow anomaly and generation "
            "anomaly rise and fall in the same calendar month. This is "
            "co-movement, not a predictive lead \u2014 the series share a common "
            "driver (the weather's effect on water), so neither forecasts the "
            "other. Reservoirs shift timing by days-to-weeks, but at the monthly "
            "grain that blur is absorbed into the same calendar month, so the "
            "monthly signals still align at lag 0. Testing true daily dispatch "
            "would require daily/BPA-hourly hydro data, which is out of scope "
            "for these monthly inputs."
        ),
        _h6("Why some gauges are tight and others decoupled"),
        _p(
            "Three mechanisms decide whether a river shows up in its region's "
            "hydro totals \u2014 and the tight bucket is deliberately mixed, not a "
            "simple west-vs-east map (MD, NC, GA, PA\u00d73, CT, MO\u00d72, CA, "
            "NE\u00d72, MN, OR):"
        ),
        _bullet("Regional hydro share",
                "where hydro is a large slice of the mix, a big hydro-fed "
                "river's swings move the region's aggregate output \u2192 tight; "
                "where hydro is tiny, that flow variance is lost in gas/coal/"
                "nuclear noise \u2192 decoupled. (Hydro is ~6% of U.S. "
                "generation, with roughly half of installed capacity across "
                "WA/OR/CA.)"),
        _bullet("Reservoir / pumped-storage / regulated routing",
                "dams store and dispatch water when economical, not when it "
                "arrives, and pumped storage even consumes power \u2014 this "
                "masks the flow signal and pushes a gauge toward decoupled "
                "(e.g. regulated CA-11251000, \u03c1 0.085)."),
        _bullet("Drainage-basin integration",
                "a single-tributary gauge sees only its own variable, but the "
                "region's fleet integrates many independently-weathered "
                "tributaries, diluting any one gauge even in hydro regions "
                "(e.g. WA-12010000 at 0.187)."),
        _h6("Real anchors where coupling is verifiable"),
        _bullet("Columbia \u2192 BPA (Pacific NW)",
                "BPA markets power from 31 federal hydro dams on the Columbia "
                "and tributaries; ~87% of sustained peak capacity is hydro \u2014 "
                "the archetype of a hydro-dominated grid, hence a Columbia-at-"
                "The-Dalles-style gauge coupling near \u03c1 \u2248 0.86."),
        _bullet("Southeast \u2192 TVA",
                "29 hydro dams + pumped storage, but hydro is only ~13% of "
                "output (nuclear 41%, gas 26%, coal 14%) \u2014 material, not "
                "dominant, so Southeast gauges couple well where rivers feed "
                "the TVA fleet (NC 0.706, GA 0.622) but below the PNW."),
        _bullet("Mid-Atlantic \u2192 PJM",
                "a market dominated by gas/coal/nuclear with a small hydro "
                "share, so PA/MD gauges couple only where the river is a "
                "meaningful contributor (PA 0.619/0.555/0.500, MD 0.715)."),
        _h6("Method caveats \u2014 read the tight/decoupled split carefully"),
        _bullet("Region/state aggregate, not per-dam",
                "correlation is at the EIA region/state AGGREGATE, not per dam. "
                "\u201cTight\u201d thus means \u201ca meaningful hydro contributor "
                "within its region,\u201d not a 1:1 match to any single dam."),
        _bullet("Monthly grain only",
                "these are monthly inputs; true daily dispatch is not being "
                "claimed. A genuine daily test would need BPA hourly hydro for "
                "the PNW, out of scope here."),
        _bullet("Autocorrelation & effective N",
                "strong autocorrelation in the HYC/flow series inflates naive "
                "p-values; the top correlations survive a Bayley\u2013Hammersley "
                "effective-N correction to p \u2248 1e-4\u20131e-15."),
        _sub(
            "Sources: USGS Hydroelectric Power: How it Works / Water Use "
            "(usgs.gov); EIA \u2014 Where hydropower is generated (eia.gov); BPA \u2014 "
            "Power Services (bpa.gov); TVA; PJM \u2014 Markets & Operations; "
            "project data hydro_correlation/correlation_final.csv and "
            "significance_memo.md.",
            style={"marginTop": "10px"},
        ),
    ]
    return _section_card("Why These 14 Gauges Couple to Hydro Generation",
                         body, icon="\u26a1")

def _applications() -> dbc.Card:
    body = [
        _bullet("Flood early warning",
                "the dashboard surfaces speed (3-day rise rate), magnitude "
                "(z-score), and geographic clustering (regional avg anomaly + "
                "event counts) in one view. Watching a region's event count "
                "climb day-over-day while several gauges go cyan/crimson is a "
                "concrete trigger, not a hunch."),
        _bullet("Drought monitoring",
                "sustained negative z-scores over months show up as amber-"
                "dominant maps and in the Monthly Anomaly Bar Chart. Because "
                "baselines are seasonal, “low for August” is correctly separated "
                "from “low for March.”"),
        _bullet("Climate trend analysis",
                "the Monthly Anomaly Bar Chart (272 months, Jan 2004 → Aug 2026) "
                "marks months above mean + 1σ of the monthly distribution in "
                "crimson; comparing year-over-year patterns reveals whether "
                "extreme-flow events are becoming more frequent in any season."),
        _bullet("Educational tool",
                "every statistical concept is on screen with a real example — "
                "z-scores, seasonal baselines, standard-deviation bands, "
                "percentiles, the Richards-Baker index, record proximity — and "
                "the raw drawer exposes the actual API payload behind each "
                "chart."),
        _bullet("Portfolio piece",
                "a complete full-stack data project: rate-limited API ingestion, "
                "a verified gauge-selection methodology, an immutable parquet "
                "data lake, a read-only DuckDB serving layer, and a fully "
                "interactive Dash visualization with drill-downs to the raw "
                "JSON."),
    ]
    return _section_card("Other Potential Applications", body, icon="💡")


def _components_guide() -> dbc.Card:
    rows = [
        _component_row(
            "🎯", "Anomaly Scorecards",
            "The #1 most anomalous date in the whole dataset — the date with "
            "the most |z| ≥ 2.5 events across all metrics and all regions — "
            "with the event count in large crimson type (e.g., Sep 19, 2004 — "
            "22 anomalous events). Hovering opens a CSS-only dropdown ranking "
            "#2–#10, each with its event count and per-metric breakdown "
            "(e.g., “Streamflow 17 · Gage Height 5”).",
            "queries.get_top_anomaly_dates(conn, n=10), summing event_count "
            "from daily_category_metrics by date. Computed once at startup "
            "from the immutable parquet; deliberately NOT affected by the "
            "metric/date filter.",
            "Hover the card to expand the top-10 dropdown.",
            "The dataset's “hall of fame” of extreme days — when many gauges "
            "fire at once, that is a storm or heatwave signature.",
        ),
        _component_row(
            "📅", "Monthly Anomaly Bar Chart",
            "Total anomalous events per month (all metrics, all regions), "
            "spanning Jan 2004 → Aug 2026 (272 months, 16,441 total events). "
            "Teal bars are normal months; crimson marks statistically elevated "
            "months — those at or above the dynamic threshold of mean + 1σ of "
            "the monthly distribution.",
            "queries.get_monthly_anomaly_counts; the threshold is computed at "
            "render time. Like the scorecard, static and filter-independent.",
            "Hover a bar for the month, total events, and extreme-entity count.",
            "Year-over-year patterns of crimson months — are extreme-flow "
            "events becoming more frequent in any season?",
        ),
        _component_row(
            "🎛️", "Metric & Date Filter",
            "The sticky filter bar (position: sticky; top: 0) stays visible "
            "while the page scrolls and controls every filtered component below "
            "it — map, KPI cards, region table, fastest risers, hydrograph, "
            "personality cards, and raw drawer.",
            "Metric dropdown (Streamflow / Gage height / Water temperature) and "
            "a Mantine DatePickerInput limited to 2004-01-01 → the default date "
            "(the most recent day with ≥50% of streamflow gauges reporting — "
            "currently 2026-08-01, because the ingest's final day is usually "
            "partial).",
            "Pick a metric and a date; the picker opens to a decade → year → "
            "month drill-down so 20+ years of navigation takes seconds.",
            "Switching metrics tells different stories — temperature extremes "
            "rarely align with flow extremes.",
        ),
        _component_row(
            "📋", "KPI Cards",
            "Four network-level summaries for the selected metric/date: Extreme "
            "Events (gauges at |z| ≥ 2.5, crimson when > 0), Fastest Riser "
            "(largest 3-day rise rate — e.g., Mississippi River at Grafton, IL "
            "+6,533 ft³/s/day on the default date), Most Below Normal (region "
            "with the lowest mean anomaly, amber), and Data Health (gauges "
            "reporting / 52, gap rate, total rows).",
            "queries.get_kpi_cards, backed by the same cached date slice the "
            "map colors by (so the shown z always matches the region name).",
            "No interaction — re-rendered whenever the metric or date changes.",
            "Extreme Events > 0; a large signed rise rate; and the per-metric "
            "gap rates (streamflow 0.04%, gage height 6.8%, water temperature "
            "33.5%).",
        ),
        _component_row(
            "🗺️", "National Overview Map",
            "A state choropleth colored by each region's average anomaly, with "
            "52 station markers on top (size ∝ √flow, 5–20 px; color = the "
            "gauge's own z-score). States without gauges are neutral gray; "
            "regions with no anomaly data today are slate.",
            "get_region_table for the choropleth (discretized into the 6-bin z "
            "palette so even extreme values like z = 74 render as plain "
            "crimson) + the cached daily slice for the markers.",
            "Click a state to select its region (filters the fastest risers); "
            "click a gauge to select it for the hydrograph and personality "
            "cards (white ring on the selection). Zoom/pan persist across "
            "updates (uirevision).",
            "Direction and severity at a glance: cyan/crimson = unusually high, "
            "amber family = unusually low; big markers are major rivers.",
        ),
        _component_row(
            "🗂️", "Regional Rollup Table",
            "One row per region — Region, Gauges Reporting, Events (|z|≥2.5), "
            "Avg Anomaly — sorted by Avg Anomaly descending so the most "
            "stressed region sits on top. Avg Anomaly cells are color-coded "
            "(+ → teal-cyan, − → amber).",
            "queries.get_region_table for the selected metric/date (8 regions, "
            "paginated).",
            "Click a row (radio or cell) to highlight it in solid teal and "
            "write the region to region-table-store, which filters the Fastest "
            "Risers table below. Selections stay correct after native "
            "sorting/pagination.",
            "Which regions concentrate today's events; a strongly negative "
            "region is a drought signal.",
        ),
        _component_row(
            "⚡", "Fastest Risers Table",
            "The top-5 gauges by 3-day rise rate for the selected region: Rank "
            "(teal highlight), Station, Rise Rate (cyan rising / amber "
            "falling), and Current Flow. Placeholder message until a region is "
            "selected.",
            "The fastest_risers list[struct] column precomputed in "
            "daily_category_metrics, via queries.get_fastest_risers.",
            "Select a region row in the Regional Rollup to populate it.",
            "A big cyan rise rate is a flood-watch trigger. The data honestly "
            "includes negative rises when fewer than 5 gauges are rising in a "
            "region — that is the signal.",
        ),
        _component_row(
            "🌊", "Hydrograph",
            "The drill-down centerpiece for one gauge × one metric over a "
            "window ending at the selected date. Top subplot: observed daily "
            "series (white), seasonal baseline μ (teal) with ±1σ / ±2σ "
            "confidence bands, last year's flow as a dashed ghost, a "
            "water-temperature overlay (dashed amber, right axis, only when "
            "data exists), palette-colored markers on every |z| ≥ 2.5 day, and "
            "gray × gap markers. Bottom subplot: 3-day rise-rate bars (cyan "
            "up / amber down). Stats row: current value, anomaly z, flow "
            "percentile, record proximity.",
            "queries.get_hydrograph_data (calendar-reindexed — missing days are "
            "explicit rows), get_baseline_band (joined to observed dates by "
            "day-of-year), get_previous_year_flow. The figure never touches "
            "data files directly.",
            "Range buttons [1M][3M][6M][1Y][All] — all windows end at the "
            "selected date; hover for day-level values; adaptive date ticks "
            "keep the axis readable at any zoom.",
            "The white line punching outside the ±2σ band, clusters of anomaly "
            "markers, a surge of rise-rate bars — and runs of gray ×'s that "
            "reveal sensor outages (e.g., the Connecticut River's temp series "
            "ended in 2004).",
        ),
        _component_row(
            "🧬", "Personality Cards",
            "Three cards profiling how the selected river behaves: Flashiness "
            "(Richards-Baker Index + regional rank + a Low < 0.1 / Moderate "
            "0.1–0.3 / High 0.3–0.6 / Very High > 0.6 badge), Flow Percentile "
            "(rank among ALL observations 2004–2026, drawn as a progress bar "
            "colored by the gauge's anomaly z-score), and Record Proximity "
            "(current ÷ all-time max, graded amber < 25% · teal 25–75% · cyan "
            "75–95% · crimson > 95%).",
            "queries.get_personality_cards + get_flashiness_index (per "
            "calendar year, over observed days with completeness_score > 0).",
            "Select a gauge on the map; the cards follow metric, date, and "
            "selection.",
            "Is the spike in character? A naturally flashy river (High/Very "
            "High) vs. a stable river at 92% of its record — different "
            "seriousness. (Reference: the Connecticut River's 2026 index is "
            "0.163 — moderate, ranked 4 of 7 in its region.)",
        ),
        _component_row(
            "🔬", "Raw Data Drawer",
            "A slide-up panel pinned to the bottom of the viewport (hidden by "
            "default), showing the exact USGS OGC API JSON payload stored for "
            "the selected gauge-metric-day — pretty-printed and "
            "syntax-highlighted server-side (keys teal, strings green, numbers "
            "cyan, booleans crimson, nulls amber) with a byte-size note — plus "
            "a Download CSV button.",
            "raw_observations parquet (per-year files) via "
            "queries.get_raw_payload; the CSV exports every raw observation "
            "row for the gauge-metric as {entity_id}_{metric}_raw.csv.",
            "“Inspect raw data” opens the drawer, “Close” hides it, “⬇ Download "
            "CSV” exports via dcc.Download.",
            "Approval status (provisional vs approved), qualifiers like "
            "“estimated,” and units — the audit trail behind every number.",
        ),
    ]
    return _section_card("Dashboard Components Guide", rows, icon="🧩")


def _hydro_components_guide() -> dbc.Card:
    """Hydro Coupling page component guide (B3): the five coupling panels.

    Mirrors the Dashboard Components Guide rows (same _component_row format),
    documenting the Hydro Coupling page's five panels: the coupling map, the
    ranked coupling strip, the small-multiples grid, the drill-down (lag-0 /
    lag-+1), and the GridStatus enrichment note.
    """
    rows = [
        _component_row(
            "\U0001f5fa", "Coupling Map",
            "A U.S. Scattergeo map over the full 52-gauge field: 14 teal "
            "\"tight\" markers (|\u03c1| \u2265 0.5, selectable) for the tight-tier "
            "gauges on top of 38 faint gray context markers for the rest. "
            "Marker color and size scale by |\u03c1| on the three-bin teal "
            "sequential scale (weak \u2192 mid \u2192 strong), and a white ring "
            "highlights the currently selected gauge.",
            "corr_final \u00d7 gauge_geo (all 52 gauges via _all_gauges_geo); the "
            "tight subset is filtered by tier and colored by _rho_bin(spearman). "
            "Context markers carry no entity id and are inert by design.",
            "Click a teal marker to select that gauge (drives the ranked strip "
            "and drill-down); the selection gets a white ring. Zoom / pan persist "
            "across updates (uirevision |hydro-map|).",
            "Which tight gauges sit where \u2014 a strongly colored / larger marker "
            "means a higher |\u03c1|, and the teal cluster should match the ranked "
            "strip ordering below.",
        ),
        _component_row(
            "\U0001f4ca", "Ranked Coupling Strip",
            "A horizontal bar chart ranking all 14 tight gauges by |\u03c1| "
            "(Spearman vs regional hydro), strongest on top. Marker color and "
            "bar length both scale with |\u03c1|; the currently selected gauge's "
            "bar fills brighter (cyan) so the selection is legible at a glance.",
            "hydro_queries.get_ranked_coupling(), sorted by spearman_anom "
            "descending; bar color via _rho_bin on the same three-bin teal scale.",
            "Click a bar to select that gauge \u2014 the mirror of the map click, and "
            "the two stay in sync. uirevision |hydro-strip| keeps the axis stable "
            "across selections.",
            "The top of the list is the strongest flow\u2192hydro link; read the "
            "magnitudes to see how quickly the tight group drops off toward the "
            "|\u03c1| = 0.5 boundary.",
        ),
        _component_row(
            "\U0001f9e9", "Small-Multiples Grid",
            "A 4\u00d74 grid of sparklines, one per tight gauge, overlaying the "
            "de-seasonalized flow z-score (teal) against the hydro generation "
            "z-score (amber) across the full aligned window. Each subplot's "
            "title carries the gauge's region / state and its |\u03c1|.",
            "hydro_queries.get_tight_gauges() (already |\u03c1| descending) + "
            "per-gauge get_gauge_series(); both series are z-scored onto a "
            "common dimensionless axis (the axes are hidden for the sparkline "
            "look).",
            "Hover a sparkline for day-level flow / hydro values; the grid is "
            "purely observational (no click interaction).",
            "Whether the teal and amber tracks actually rise and fall together \u2014 "
            "sustained runs of co-movement are the signal, and a wide divergence "
            "at a specific gauge flags a weaker coupling.",
        ),
        _component_row(
            "\U0001f50d", "Drill-Down",
            "The per-gauge comparison in two modes, chosen by the lag toggle. "
            "Lag 0 (overlay): monthly flow anomaly vs hydro anomaly plotted "
            "together over time with green shading wherever both move the same "
            "direction. Lag +1 (scatter): each month's flow z-score against the "
            "next month's hydro z-score, with a linear fit line and a Spearman "
            "\u03c1 annotation (lag +1 \u03c1).",
            "_drill_data for the selected gauge + its eia_location aggregate; "
            "_build_overlay / _build_lag_scatter for the two modes.",
            "Toggle Lag 0 \u2194 Lag +1; click a gauge on the map or the strip to "
            "re-target the drill-down. Hover for month-level values.",
            "Co-movement at lag-0 (matching rises / falls, green shading) is the "
            "core signal; a lag-+1 scatter should stay weak \u2014 if it climbs, the "
            "river carries predictive power beyond co-movement.",
        ),
        _component_row(
            "\u26a1", "GridStatus Note",
            "A small info card at the top of the page summarizing the BPA daily "
            "hydro enrichment status \u2014 whether the 7 BPA-footprint gauges are "
            "being extended with near-real-time daily GridStatus data or "
            "falling back to monthly EIA data. It explains what daily data is "
            "available and current for this build.",
            "hydro_gridstatus.gridstatus_status_text() (lazy / guarded import) "
            "\u2014 the same enrichment flag that drives the data refresh note.",
            "Read-only status text; no interaction.",
            "Whether BPA daily data is live (which gauges are extended) vs the "
            "EIA monthly fallback \u2014 and the current month lag driving that "
            "decision.",
        ),
    ]
    return _section_card("Hydro Coupling Components Guide", rows, icon="\U0001f517")


def _color_scale() -> dbc.Card:
    body = [
        _p(
            "One palette (defined once in components/map_panel.py as "
            "z_to_color()) is used consistently everywhere: the map's station "
            "markers, the choropleth region colors, the hydrograph's anomaly "
            "markers and stats-row z chip, and the personality cards' "
            "percentile bar. The legend under the map lists it explicitly."
        ),
        html.Div(
            [
                _swatch(Z_AMBER, "Extreme low", "z ≤ −2.0"),
                _swatch(Z_AMBER_TEAL, "Low", "−2.0 < z ≤ −1.5"),
                _swatch(Z_TEAL, "Near normal", "−1.5 < z < +1.5"),
                _swatch(Z_TEAL_CYAN, "High", "+1.5 ≤ z < +2.0"),
                _swatch(Z_CYAN, "Very high", "+2.0 ≤ z < +3.0"),
                _swatch(Z_CRIMSON, "Extreme high", "z ≥ +3.0"),
                _swatch(Z_NULL, "No anomaly data", "no baseline / no signal"),
                _swatch(Z_NO_DATA, "No gauges in state", "choropleth only"),
            ],
            style={"marginTop": "10px"},
        ),
        _p(
            "Read it like a thermometer with two ends: teal is “normal for this "
            "day of year,” cyan/crimson mean the river is carrying unusually "
            "much (crimson = |z| ≥ 3, genuinely extreme), and the amber family "
            "means unusually little. The same palette colors low-flow events "
            "amber and high-flow events cyan/crimson, so a quick glance at a "
            "map tells you direction and severity simultaneously. The "
            "choropleth discretizes region averages to the same bins (via "
            "category codes) so even absurd z values (e.g., a near-zero-σ "
            "baseline producing z = 74) render as plain crimson — never a "
            "clipped or interpolated artifact.",
            style={"marginTop": "12px"},
        ),
    ]
    return _section_card("Understanding the Color Scale", body, icon="🎨")


def _methodology() -> dbc.Card:
    body = [
        _bullet("Source", "USGS Water Data OGC API (modernized endpoint) — the "
                "daily collection (statistic_id=00003, daily mean), fetched as "
                "GeoJSON FeatureCollections with cursor-based pagination, "
                "batched multi-gauge/multi-parameter requests, and exponential "
                "backoff (15s → 120s) for rate limits."),
        _bullet("Network", f"{N_GAUGES} gauges · 8 regions · 26 states, curated "
                "from 91 probed candidates with two-stage verification (a 2004 "
                "data probe + a time-series-metadata span cross-check). Gauge "
                "sizes span 55 mi² → 697,000 mi²."),
        _bullet("Metrics", "3 per gauge: streamflow (ft³/s), gage height (ft), "
                "water temperature (°C). Coverage varies by gauge — gage height "
                "and water temperature are not measured everywhere (62 empty "
                "gauge-metric combos); real data sparsity, not a bug."),
        _bullet("Baseline", "2004-01-01 → 2023-12-31 (fixed 20 years), ±7-day "
                "circular day-of-year window (34,038 rows = 93 gauge-metric "
                "combos × 366 DOYs; μ, σ with ddof=1, n_years). σ = 0 or null "
                "→ anomaly is null."),
        _bullet("Anomaly score", "(average − μ) / σ per (gauge, metric, day). "
                "Distribution: min −27.3, median −0.26, max 74.7 — asymmetric "
                "by the nature of rivers."),
        _bullet("Event threshold", "|z| ≥ 2.5 → 16,441 extreme rows across the "
                "dataset (~2.4% of observed days)."),
        _bullet("Flashiness", "Richards-Baker Index = sum(|daily change|) ÷ "
                "sum(average) per calendar year over observed days only, plus "
                "a regional rank."),
        _bullet("Flow percentile", "rank-based over all observations "
                "2004–2026 (rank('max') / count × 100 — ties share a "
                "percentile)."),
        _bullet("Record proximity", "average ÷ max(average) over the full "
                "2004–2026 record per gauge-metric."),
        _bullet("Rise rate", "rise_rate_3d = (average − average.shift(3)) / 3 "
                "computed on the calendar-reindexed series, so a shift across a "
                "gap is null rather than a silently wrong delta."),
        _bullet("Data pipeline", "polars (ingestion + transform) → parquet, "
                "hive-partitioned by metric=*/year=* (entity_id-sorted row "
                "groups of 8192 for pruning) → DuckDB (read-only, thread-local "
                "connections) for serving. Three layers: raw_observations "
                "(633,257 rows, raw payloads retained for audit), "
                "daily_entity_metrics (687,166 calendar-reindexed rows), "
                "daily_category_metrics (164,241 regional rollup rows incl. "
                "fastest_risers as list[struct])."),
        _bullet("Immutability", "the parquet dataset is immutable during "
                "serving — caches are therefore safe (LRU date-slice cache of "
                "8, per-metric dataset stats, per-gauge historical maxima), and "
                "every callback runs on its own cheap read-only connection."),
        _sub(f"Data range in this build: 2004 through {current_latest_date()} · "
             f"{current_total_rows():,} total rows across all metrics.",
             style={"marginTop": "10px"}),
    ]
    return _section_card("Data Sources & Methodology", body, icon="🗄️")


def _data_refresh() -> dbc.Card:
    body = [
        _h6("Dashboard data (daily)"),
        _p("The main Dashboard page fetches daily USGS streamflow, gage "
           "height, and water temperature data. A launchd job (local) or "
           "Cloud Run job (prod) runs at 7 AM PT every day to pull fresh "
           "data, rebuild seasonal baselines and metrics, and publish the "
           "updated parquet files. The Dashboard's date picker defaults to "
           "the latest available date — typically yesterday."),
        _h6("Hydro Coupling data (monthly)"),
        _p("The Hydro Coupling page operates on a monthly grain: monthly "
           "mean streamflow × monthly hydro generation. Two data sources "
           "feed this page, each with a different refresh cadence:"),
        _bullet("EIA monthly hydro generation",
                "published ~2–3 months in arrears. The EIA HYC (conventional "
                "hydroelectric) monthly data is the base layer for all 52 "
                "gauges. As of August 2026, the latest EIA month is May 2026."),
        _bullet("BPA daily hydro via GridStatus",
                "extends 7 Pacific Northwest gauges (BPA footprint) with "
                "near-real-time daily data, resampled to monthly. The current "
                "month is excluded (≥25-day completeness gate), so BPA data "
                "lags ~1 month. Non-BPA gauges remain capped at the EIA "
                "publication lag until EIA catches up."),
        _h6("What to expect"),
        _bullet("BPA gauges (7 of 14 tight)",
                "data extends through the previous month (e.g., August data "
                "appears in early September once the month has ≥25 complete "
                "days)."),
        _bullet("Non-BPA gauges (45 of 52 total)",
                "data extends through the last EIA-published month (~2–3 "
                "months behind real time). August 2026 data is expected "
                "October–November 2026."),
        _bullet("Freshness sync",
                "in Cloud Run, a 60-second poll checks the GCS "
                "UPDATE_LOG.md generation. When the daily job publishes new "
                "data, the serving container re-syncs from GCS and "
                "invalidates both the Dashboard and Hydro Coupling query "
                "caches, so both pages pick up fresh data without a redeploy."),
        _sub("This monthly lag is inherent to the correlation methodology — "
             "a full month of both streamflow and generation is needed for "
             "a meaningful monthly data point.",
             style={"marginTop": "10px"}),
    ]
    return _section_card("Data Refresh & Update Schedule", body, icon="🔄")


def _stack() -> dbc.Card:
    body = [
        _bullet("Python 3.10", "framework build; requests 2.34.2 for "
                "ingestion, PyArrow for parquet."),
        _bullet("Dash 4.4.1", "app framework — callbacks, stores, and Dash "
                "Pages (multi-page routing); served via WSGI."),
        _bullet("dash-bootstrap-components 2.0.4", "DARKLY theme + layout "
                "grid, cards, buttons."),
        _bullet("dash-mantine-components 2.8.0", "the DatePickerInput "
                "(decade/year/month drill-down) and MantineProvider theming "
                "(primary color teal, dark scheme)."),
        _bullet("DuckDB 1.5.5", "read-only serving layer over parquet — one "
                "thread-local connection per worker thread."),
        _bullet("polars 1.43.2", "ingestion/transform pipeline (baselines, "
                "entity metrics, category metrics)."),
        _bullet("Plotly 6.9.0", "all figures — choropleth + scattergeo map, "
                "hydrograph subplots, progress bars."),
        _bullet("Deploy", "gunicorn `app:server` (WSGI entry, Cloud Run-ready; "
                "run locally with `python3 app.py` → http://localhost:8050)."),
    ]
    return _section_card("Technical Stack", body, icon="⚙️")


def _gauge_network() -> dbc.Card:
    # Live rollup straight from the registered stations table (same source the
    # dashboard queries) — always in sync with stations.csv.
    region_rows: List[Tuple[str, int]] = [
        (str(r), int(n))
        for r, n in conn.execute(
            "SELECT region, count(*) FROM stations GROUP BY region ORDER BY region"
        ).fetchall()
    ]

    _cell = {"padding": "5px 12px", "border": f"1px solid {BORDER}",
             "color": MUTED, "fontSize": "13px"}

    table_rows = [
        html.Tr(
            [
                html.Td(region, style=_cell),
                html.Td(f"{n}", style={**_cell, "textAlign": "right"}),
            ]
        )
        for region, n in region_rows
    ]
    table_rows.append(
        html.Tr(
            [
                html.Td("Total", style={**_cell, "fontWeight": "700",
                                        "color": TEXT}),
                html.Td(f"{N_GAUGES}", style={**_cell, "fontWeight": "700",
                                              "color": TEXT, "textAlign": "right"}),
            ]
        )
    )

    table = html.Table(
        [
            html.Thead(html.Tr([
                html.Th("Region", style={**_cell, "color": ACCENT,
                                         "fontWeight": "700"}),
                html.Th("Gauges", style={**_cell, "color": ACCENT,
                                         "fontWeight": "700",
                                         "textAlign": "right"}),
            ])),
            html.Tbody(table_rows),
        ],
        style={"width": "100%", "borderCollapse": "collapse",
               "margin": "8px 0 14px", "backgroundColor": BG},
    )

    body = [
        _p(f"{N_GAUGES} USGS gauges across 8 regions (verified live against "
           "stations.csv, registered as a DuckDB table):"),
        table,
        _bullet("Station record", "stations.csv carries entity_id "
                "(USGS-XXXXXXX), station_name, state, region, latitude, "
                "longitude, hydrologic_unit_code, site_type, agency_code, "
                "drainage_area, first_year_of_record (1861–1967 — e.g., the "
                "Mississippi at St. Louis since 1861), and "
                "earliest_verified_year (2004 for all — the verified start of "
                "the modern daily record used as the baseline)."),
        _bullet("Metric coverage", "all 52 gauges report streamflow; gage "
                "height and water temperature exist at the subset of gauges "
                "where USGS actually measures them — hence the higher gap "
                "rates (6.8% for gage height, 33.5% for water temperature vs. "
                "0.04% for streamflow) — itself a data-health signal the "
                "dashboard surfaces rather than hides."),
    ]
    return _section_card("The Gauge Network", body, icon="🗺️")


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------
def build_guide_layout() -> dbc.Container:
    """The complete guide page: hero + audience toggle + guide body + footer."""
    footer = html.Div(
        f"Data: USGS Water Data OGC API · Baseline: 2004-2023 · "
        f"{N_GAUGES} gauges · {current_total_rows():,} rows",
        className="footer",
    )

    return dbc.Container(
        fluid=True,
        id="guide-container",
        children=[
            _hero(),
            html.Div(
                [
                    html.Div("Choose how this guide explains things.",
                             style=_MUTED),
                    dbc.RadioItems(
                        id=_ID_AUDIENCE,
                        options=[
                            {"label": "Expert (data nerds)",
                             "value": "expert"},
                            {"label": "General Audience",
                             "value": "general"},
                        ],
                        value="expert",
                        inline=True,
                    ),
                ],
                style={"margin": "0 auto", "maxWidth": "900px",
                       "padding": "6px 12px 18px", "textAlign": "center"},
            ),
            html.Div(id=_ID_GUIDE_BODY, children=[]),
            footer,
            html.Div(style={"height": "32px"}),
        ],
        style={"backgroundColor": BG},
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
app = dash.get_app()
_REGISTERED = False


def register_callbacks(app) -> int:
    """Wire the audience toggle into the guide body container (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return 1
    _REGISTERED = True

    @app.callback(
        Output(_ID_GUIDE_BODY, "children"),
        Input(_ID_AUDIENCE, "value"),
        prevent_initial_call=False,
    )
    def _on_audience(value):
        body = _expert_body() if value == "expert" else _general_body()
        # Wrap the section list in a single container component. A bare
        # top-level Python list returned from a single-output `children`
        # callback is ambiguous to Dash (it can read as a multi-output
        # tuple and raise InvalidCallbackReturnValue in validate_multi_return
        # depending on the request's `outputs` grouping). Returning one
        # component keeps every section and always serializes as a single
        # `children` value for the real renderer.
        return html.Div(body)

    return 1


register_callbacks(app)


def layout() -> dbc.Container:
    """Dash-pages layout entry point for the "/guide" page."""
    return build_guide_layout()
