# Signals Before Disruption — Progress Log

## Phase 1: Source Assessment & Raw Data Inspection

### Step A: openFDA Device Enforcement API — ✅ COMPLETE (2026-08-02)

**Script:** `ingestion_test.py` → `fetch_recent_device_enforcement(limit=5)`
**Endpoint:** `https://api.fda.gov/device/enforcement.json` (params: `limit=5`, `sort=report_date:desc`)
**Result:** 5 live records pulled successfully. `requests 2.34.2` confirmed available.

#### Response structure
- `meta` — disclaimer, license, `last_updated` (2026-07-22), pagination (`total: 39,588`)
- `results[]` — records array, sorted by `report_date` desc

#### Target fields — all present
`recall_number`, `report_date`, `classification`, `product_description`, `distribution_pattern`

#### Bonus fields worth keeping in mind
`status`, `state`, `city`, `country`, `recalling_firm`, `reason_for_recall`, `product_quantity`, `event_id`

#### Observations for Step C (transformation)
1. **Dates are strings, not ISO** — `report_date: "20260722"` (YYYYMMDD), needs parsing
2. **`classification` is ordinal categorical** — Class I (most severe) / II / III
3. **`distribution_pattern` is messy free text** — inconsistent: tab-separated state codes (`"US: AZ\tCA\tCO..."`), prose ("US Nationwide distribution."), or full country lists. `location_reference` decision needed: firm `state` field vs. parsing distribution states
4. **`event_id` is NOT unique per record** — Papablic appears twice under `event_id: 98898` with different `recall_number`s → use `recall_number` as the key

---

### Step B: USGS modernized OGC API — ✅ COMPLETE (2026-08-02)

**Script:** `ingestion_test.py` → `fetch_usgs_daily_values("USGS-02238500", days=7)`
**Endpoint:** `https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items`
**Params:** `f=json`, `monitoring_location_id`, `datetime=<ISO-8601 interval>`, `limit=100`
**Result:** 18 features returned, worked first try.

#### Response structure
- GeoJSON `FeatureCollection` — top-level keys: `type`, `features`, `numberReturned`, `links`, `timeStamp`
- Each feature: `properties` (the data) + `geometry` (Point, lon/lat) + `id` (UUID)
- `properties` fields: `time_series_id`, `monitoring_location_id`, `parameter_code`, `statistic_id`, `time`, `value`, `unit_of_measure`, `approval_status`, `qualifier`, `last_modified`

#### Payload composition (7-day query)
- 18 features = 3 parameter codes × 6 days (2026-07-27 → 2026-08-01)
- `parameter_code` meanings: `00060` = streamflow (ft³/s), `00065` = gage height (ft), `63160` = stream level/elevation (ft)
- All `statistic_id: 00003` (daily mean)
- One location: USGS-02238500 @ (-81.881, 29.081)

#### Observations for Step C (transformation)
1. **`value` is a STRING** (e.g., `"24.9"`), not a number → cast to Float64
2. **`time` is ISO date string** (`"2026-07-31"`) → parses cleanly, unlike FDA dates
3. **One row per (date × parameter_code)** → for unified format, `metric_type` should map from `parameter_code` (+ keep `unit_of_measure`, or fold unit into metric_type e.g. "Streamflow (ft³/s)")
4. **Nested structure** — data lives in `features[].properties`, needs flattening (geometry can be extracted to lat/lon or dropped for Phase 1)
5. **`approval_status: Provisional`** on all records — worth keeping as a data-quality flag later

---

---

## 🧭 PROJECT PIVOT (2026-08-02 17:49)

**Decision:** Project direction confirmed with Alfie. The two pilot sources (FDA + USGS) were plumbing exercises. The flagship build is now:

### **River Personality Monitor** (working title, under the "Signals Before Disruption" umbrella)
- **Goal:** Characterize how each river *behaves* — not another flood map.
- **Source:** USGS modernized Water Data OGC API (Step B code is the seed).
- **Scope (initial pull):** ~50 gauges across diverse US regions × 20 years of daily values.
- **Stack:** Python + polars (ingestion) → parquet → DuckDB/PyArrow (serving) → Plotly Dash (frontend).
- **Planned indicators:** seasonal z-score anomaly, fastest-rising gauges, flashiness index, longest uninterrupted rise/decline, record proximity, water-temp anomalies, gauge disagreement within watersheds, flow percentile vs. history.
- **FDA recall module:** ON ICE — `fetch_recent_device_enforcement()` stays in the codebase as the seed for a future "Signals Before Disruption" module.

### Target data architecture (3 tables)
- `raw_observations` — source, entity_id, observed_at, collected_at, metric, value, unit, latitude, longitude, raw_payload (KEEP raw payload for auditability/recalculation)
- `daily_entity_metrics` — date, entity_id, observation_count, min, max, average, daily_change, anomaly_score, completeness_score
- `daily_category_metrics` — date, region, category, entity_count, event_count, average_anomaly, extreme_entity_count

### Why river won (decision rationale)
- Volume is real: ~13K active USGS gauges, many with 50–100+ year baselines → hundreds of millions of rows available
- Free data, no budget needed
- Maps ~1:1 to Alfie's Rivian dashboard narrative (national choropleth → site drill-down → raw export)
- Rich historical comparisons = legitimate statistics (seasonal z-scores), not just counts

---

### Step C: Polars transformation proposal — ✅ APPROVED (2026-08-02 17:56)
Decisions locked: (1) add water temp `00010`; (2) reindex-to-calendar w/ explicit nulls, called out in README; (3) top-5 risers as nested list; (4) fixed 20-yr baseline. Full design in `step_c_proposal.md`; README.md created with design principles.

### Build 1: stations fetcher + gauge selection — ✅ COMPLETE (2026-08-02, code-executor sub-agent, ~1h20m under API rate limits)

**Deliverables:** `stations.csv` (52 gauges) + `stations_fetch.py` (reusable fetcher w/ CLI, CSV mode, `--verify`)

#### Final list characteristics
- 8 regions × 5–7 gauges each; 26 states total
- Drainage areas span 55 mi² (Naselle River, WA) → 697,000 mi² (Mississippi @ St. Louis) — deliberate mix of small streams and major rivers
- `first_year_of_record` ranges 1861–1967; `earliest_verified_year` = 2004 for all
- stations.csv columns: `entity_id, station_name, state, region, latitude, longitude, hydrologic_unit_code, site_type, agency_code, drainage_area, first_year_of_record, earliest_verified_year`

#### Methodology (how the list was built and verified)
1. **Schema exploration first.** Fetched sample records from both `monitoring-locations` and `daily` OGC collections and inspected ACTUAL field names before writing parsing code. No assumed field names.
2. **Candidate discovery.** Seeded candidates from well-known long-record gauges + regional coverage targets; every candidate treated as unverified until probed.
3. **Two-stage verification per gauge:**
   - **Stage 1 (data probe):** queried `daily/items` for `parameter_code=00060` in Jan-2004 window — non-empty `features` = data actually exists that far back. 91 candidates probed → 88 passed.
   - **Stage 2 (metadata cross-check):** batched queries (comma-separated `id` params) against the `time-series-metadata` collection to get exact series begin/end dates; required begin ≤ 2004 AND end ≈ 2026 (active, not discontinued).
4. **Rejections were data-driven:**
   - USGS-02238500 (our Step B pilot gauge!) — no `00060` streamflow series at all, gage height only
   - 07032000 (Memphis) — metadata claims 1933 start but the modern daily endpoint serves nothing pre-~2015. **Lesson: trust the data endpoint, not metadata.**
   - Gauges with series beginning 2008/2016, unknown spans, or discontinued (e.g., Tennessee @ Chattanooga ends 2008)
5. **Curation:** final 52 selected from the 88 verified for regional balance (5–7 per region) and river-size diversity.

#### API operational findings (important for Build 2)
- **Rate limiting is aggressive without an API key** (HTTP 429). Working strategy: exponential backoff 15s → 120s, batch multiple gauges per request where the endpoint supports comma-separated `id`s, keep total request count low.
- **`time-series-metadata` collection** is the efficient way to check record spans — one batched call instead of per-gauge probing.
- **Modern OGC API has data-availability quirks** — metadata record spans can overstate what the daily endpoint actually serves.
- Fetcher design: `stations_fetch.py` supports (a) CLI list of gauge IDs, (b) refresh-from-CSV mode, (c) `--verify` mode to re-check record spans. Tested end-to-end.

#### Sub-agent execution notes
- Executed by `code-executor` sub-agent (deepseek-v4-flash). ~159K tokens total (~$0.10-class cost) for ~90 min of rate-limited API grunt work.
- Flash model was methodical and followed the brief exactly (verified everything, no assumed fields); slow in wall-clock terms mostly due to 429 backoffs.
- Left `_`-prefixed scaffolding files (probe scripts, intermediate JSON) — cleanup folded into Build 2.

### Build 2: raw ingestion loop + storage/serving layer — ✅ PILOT COMPLETE (2026-08-02, review gate: full run NOT started)

**Architecture decision (2026-08-02 19:56, per Alfie):** storage = parquet files (memory-mapped via PyArrow), query layer = DuckDB directly against parquet. No database server. Designed for low-latency serverless hosting (Cloud Run) — files ship with the container, DuckDB reads them in-process. Build locally first.

**Deliverables:** `ingest_daily.py` (CLI, cache-aware, idempotent), `verify_serving.py` (DuckDB + PyArrow checks), `data/raw_cache/` (19 MB), `data/raw_observations/metric=*/year=*.parquet` (1.04 MB, 69 partitions)

#### Pilot: 3 gauges × 2004-01-01→today × 3 params = 40,983 rows (verified independently)
- USGS-01184000 (Connecticut R.), USGS-07010000 (Mississippi @ St. Louis), USGS-14105700 (Columbia @ The Dalles)
- **5 API requests total** (multi-gauge batching works! up to 10 gauges + all 3 params per call), 0 rate-limit hits
- Idempotency proven: re-run = 0 API calls, byte-identical output

#### API behavior verified (critical for full run)
- Multi-gauge batching ✅ (comma-separated `monitoring_location_id`, tested to 10)
- Multi-parameter batching ✅ (`parameter_code=00060,00065,00010`)
- Pagination is **cursor-based** (`links[rel=next]` href carries `cursor` param), NOT offset
- Max `limit` = 50000; datetime windows inclusive of both endpoints → chunks use [start, start+5yr−1day]
- **Filters are mandatory** — unfiltered responses mix statistic_ids (00001/00002/00003/30800) and extra params (00095, 80154, 80155). `statistic_id=00003` confirmed = daily mean.

#### Serving layer validated (the Cloud Run pattern works)
- DuckDB direct-on-parquet w/ hive partitioning: row counts 17ms, date ranges 8.5ms, 23-yr yearly avg 3.9ms, anomaly-style query 10.7ms
- PyArrow `pa.memory_map` read: 22.8ms ✅

#### Schema surprises (documented, none fatal)
1. `qualifier` is a JSON **array** (['ESTIMATED'], ['EQUIP','ESTIMATED']) — polars VARCHAR[], 510/40,983 rows qualified
2. **Gage height coverage is gauge-dependent**: St. Louis serves it only as stat 30800 (not 00003); The Dalles has none in daily collection. Real data gaps, not bugs — verified.
3. **Water temp is sparse**: CT River temp series ends 2004-08-23 (discontinued); Columbia starts 2004-03-11; St. Louis has none.
4. approval_status: 39,902 Approved / 1,081 Provisional. value DOUBLE zero-null, observed_at DATE zero-null.

#### Environment
- Installed --user: polars 1.43.2, duckdb 1.5.5 (pyarrow 24.0.0 already present). Python 3.10.11 framework build (urllib SSL broken; requests fine — pipeline uses requests).

#### ⚠️ Design implication for full run
Per-gauge parameter coverage varies (gage height + temp NOT universal). Full run should expect sparse metric partitions per gauge — the reindex-to-calendar + completeness_score design handles this by making gaps explicit rows.

### Build 4: daily_category_metrics (regional rollup) — ✅ COMPLETE (2026-08-02, code-executor sub-agent, 5m3s)

**Deliverables:** `build_category_metrics.py`, `verify_category_metrics.py`, `data/daily_category_metrics/metric=*/year=*/data.parquet` (69 partitions, 5.7 MB, 164,241 rows)

- Per metric: streamflow 66,000 · water_temperature 56,991 · gage_height 41,250
- Per region: proportional to gauge counts (NE/MA 24,750 → Great Basin/SW 9,653)
- 82.7% of naive max (8×3×8270) — shortfall is real coverage (sparse gage-height/temp series in some regions)
- fastest_risers: proper list[struct] — 0 lists >5 elements, 0 null elements, 0 unsorted — all clean
- Global consistency: sum(event_count)=16,441 = entity-layer extremes ✅; sum(entity_count)=632,114 = reporting rows ✅
- Query latency: 1.6–4.9ms (DuckDB on parquet)
- Idempotent (md5-verified, 0.9s transform)
- Edge cases: 8,793 null avg_anomaly rows (entity_count=0 gap days); fastest_risers can include negative rises when <5 gauges rising (correct algebraic behavior)

### Dash Build — Pass A: Data & Query layer — ✅ COMPLETE (2026-08-02, code-executor sub-agent, 14m)

**Deliverables:** `queries.py` (609 lines, 11 query functions), `verify_queries.py` (timing + spot checks)

**All 11 functions <5ms steady-state** (verified independently). Cold-start setup (34ms one-time in get_connection) primes DuckDB stats + latest date slice.

| Function | Latency |
|---|---|
| get_kpi_cards | 0.6ms |
| get_map_data | 0.2ms |
| get_region_table | 2.8ms |
| get_fastest_risers | 1.9ms |
| get_hydrograph_data | 4.1ms |
| get_baseline_band | 0.7ms |
| get_raw_payload | 0.5ms |
| get_flashiness_index | 4.1ms |
| get_personality_cards | 4.2ms |
| get_previous_year_flow | 1.5ms |

**Key design decisions:**
- Partition-pruning on year column for single-date/gauge queries (hydrograph 6.7→2.5ms)
- LRU slice cache (8 entries) + stats cache + per-gauge historical-max cache
- stations.csv registered as DuckDB table once at connection init (avoids 8ms CSV re-parse)
- fastest_risers struct extraction handles numpy ROW arrays → plain dicts

**Correction noted:** fastest_riser for 2026-08-01 is Mississippi at Grafton (+6,533 cfs/day), NOT Columbia (+3,333). The agent caught this — my earlier mock used Columbia as an example, but the data-correct answer is Grafton. Updated in the UI mock.

**Flashiness index:** CT River 2026 = 0.1629, ranked 4th of 7 in Northeast/Mid-Atlantic. Baker-Richards formula confirmed working.

### Dash Build — Pass B: Spatial / Map — ✅ COMPLETE (2026-08-02, code-executor sub-agent, ~18m)

**Deliverables:** `components/__init__.py`, `components/map_panel.py` (700 lines), `_test_map.html` (17.4 KB)
- Choropleth (26 gauge-states colored by regional avg_z, 25 no-gauge states neutral gray) + 52 station scatter markers (size∝√flow, color=anomaly) on same figure
- Plotly built-in USA-states GeoJSON (no external files)
- 3 callbacks verified via Dash HTTP test client: state click → region store, station click → entity_id store, metric/date dropdown updates map
- Dark slate theme applied. `render_map_panel(metric, date, selected_entity_id, conn)` → dbc.Card. `register_callbacks(app)`.
- **Bug fixed in queries.py:** `_risers_to_dicts()` crashed on `pd.NA` (null fastest_risers cells for sparse metrics like water_temperature). Patched to handle NAType. Verified: water_temperature region table now returns 8 rows with empty risers list for 1 region.

### Dash Build — Pass C: Analytics / Chart (hydrograph) — ✅ COMPLETE (2026-08-02, code-executor sub-agent, ~12m)

**Deliverable:** `components/hydrograph.py` (31 KB, ~750 lines)
- Dual-axis hydrograph: flow (left, cfs) + water temp overlay (right, °C) + ±1σ/±2σ baseline bands (teal fills) + μ baseline line + previous-year ghost (dashed gray)
- Rise-rate bar subplot (30% height): cyan positive, amber negative
- Anomaly markers (|z|≥2.5) colored per mapping on flow line
- Gap markers (gray x) for completeness_score=0 days
- Date range buttons: [1M] [3M] [6M] [1Y] [All]
- Stats row: current flow, z, percentile, record proximity
- Dark slate theme applied
- Verified: renders dbc.Card for Connecticut River (90-day, storm visible) and Columbia (365-day). `_test_hydrograph.html` (39 KB) generated.
- Contract: `render_hydrograph(entity_id, station_name, metric, start_date, end_date, conn)` → dbc.Card. `register_callbacks(app)`. IDs: 'hydrograph-graph', 'range-btn-1m/3m/6m/1y/all'.

### Dash Build — Pass D: Styling / UI / KPI / Personality / Raw drawer — ✅ COMPLETE (2026-08-02, code-executor sub-agent, ~11m)

**Deliverables (6 files):**
- `assets/style.css` (~300 lines) — full dark-slate theme: cards, KPI, personality, raw drawer (slide-up keyframe), region/fastest-risers tables, anomaly badges (6 color classes), JSON syntax highlighting, dark scrollbars, Bootstrap variable overrides
- `components/kpi_cards.py` (300 lines) — `render_kpi_cards(conn, metric, date)` → 4 cards: Extreme Events, Fastest Riser, Most Below Normal, Data Health
- `components/personality_cards.py` (340 lines) — `render_personality_cards(conn, entity_id, metric, date)` → 3 cards: Flashiness (Baker-Richards + rank + Low/Mod/High badge), Flow Percentile (progress bar colored by z-palette), Record Proximity (amber/teal/cyan/crimson bar)
- `components/raw_drawer.py` (400 lines) — `render_raw_drawer(conn, entity_id, metric, date)` → hidden slide-up panel with server-side JSON syntax highlighter (no external libs), CSV export via dcc.Download, `register_callbacks(app)` for open/close toggle
- `components/region_table.py` (310 lines) — `render_region_table(conn, metric, date)` → dark DataTable sorted by avg_anomaly, `active_cell` → `region-table-store` callback
- `components/fastest_risers_table.py` (330 lines) — `render_fastest_risers(conn, region, metric, date)` → Rank/Station/Rise Rate/Flow, cyan positive / amber negative, reads region-table-store

**Verification:** All 5 component scripts run without errors. Full integration smoke test: 9 callbacks (map 3, hydro 2, raw 2, region 1, fastest 1) register on one app; 17 output IDs verified in combined layout. Live data cross-checks match (Grafton +6,533 ✓, CT flashiness 0.163 rank 4/7 ✓).

**Design fixes worth noting:**
1. Fastest-risers callback outputs to stable `fastest-risers-body` container (placeholder↔table swap) instead of DataTable data/columns — avoids callback break on first region click
2. `region-table-store` dcc.Store added to region card (found by ID-audit test)
3. Bootstrap `.card` white defaults overridden via CSS + `:root` `--bs-*` variables
4. dash_table inline styles need `!important` for row-hover; `filter_query` uses backtick-quoted column names

**Contracts for app.py:**
- `raw_drawer.register_callbacks(app)` — IDs: raw-drawer, raw-drawer-state, raw-download-btn, raw-download, raw-drawer-close, raw-drawer-toggle. App must place `raw_drawer_toggle_button()` in layout.
- `region_table.register_callbacks(app)` → Output('region-table-store','data')
- `fastest_risers_table.register_callbacks(app)` → outputs to fastest-risers-body + fastest-risers-sub; consumes region-table-store, metric-dropdown, date-picker
- Palette single source: `map_panel.py` z-mapping/hexes imported by all components

### Dash Build — Pass E: Integration (app.py) — ✅ COMPLETE (2026-08-02, code-executor sub-agent, 14m20s)

**Deliverable:** `app.py` (382 lines, 14 KB) — the only file created; no existing files modified.

**Verified independently:** server starts clean on :8050, HTTP 200, page renders "River Personality Monitor".

- 18 callbacks registered (9 component + 9 integration), 0 duplicate outputs
- 9 dcc.Store components (5 app-level + 4 component-owned)
- Full layout: header · 4 KPI cards · map (choropleth + 52 station markers) · region table · fastest risers · hydrograph empty state · personality empty state · raw drawer (hidden) · footer
- 14/14 end-to-end callback round-trips verified via real HTTP: metric/date/station/region/range/drawer all work
- Default date: 2026-08-01 (last date with ≥50% gauge coverage); header shows latest data date 2026-08-02
- One shared read-only DuckDB connection; station-name dict loaded once at startup
- Cloud Run ready: `gunicorn app:server`

**Run command:** `cd ~/dev/signals-before-disruption && python3 app.py` → http://localhost:8050

---

## 🎉 FULL DASH APPLICATION — COMPLETE

All 5 passes done: A (queries) → B (map) → C (hydrograph) → D (styling/UI) → E (integration).

**File inventory:**
- `app.py` — 382 lines
- `queries.py` — 609 lines
- `components/` — 7 modules (~3,300 lines total)
- `assets/style.css` — ~300 lines
- `data/` — 4 parquet layers, ~31 MB, 1.5M rows
- `stations.csv` — 52 verified gauges
- `ingest_daily.py`, `build_metrics.py`, `build_category_metrics.py`, `stations_fetch.py` — pipeline scripts
- `README.md`, `progress.md`, `step_c_proposal.md` — documentation

**Deliverables:** `build_metrics.py`, `verify_metrics.py`, `data/seasonal_baselines.parquet` (492 KB), `data/daily_entity_metrics/metric=*/year=*/data.parquet` (18 MB, 69 partitions)

**seasonal_baselines:** 34,038 rows = 93 gauge-metric combos × 366 DOYs. Fixed 2004-2023 baseline, ±7-day rolling window with year-boundary wrap. μ/σ/n_years per combo.

**daily_entity_metrics:** 687,166 rows (streamflow 429K · water_temp 139.6K · gage_height 118.6K). Calendar-reindexed with explicit null gaps.
- Columns: entity_id, metric, observed_at, observation_count, minimum, maximum, average, daily_change, rise_rate_3d, flow_percentile, anomaly_score, completeness_score, record_proximity
- 55,052 gap rows (8.0%) — dominated by discontinued temp series + ice-season gaps + sparse gage height. Exactly the sensor-outage signal the design intended to surface.
- Anomaly distribution: min −27.3, median −0.26, max 74.7. |z|≥2.5: 16,441 extreme rows. Asymmetric (floods unbounded above, zero-flow floor below) — expected for rivers.
- Spot check: CT River 2026-07-04 anomaly_score = −0.200 (13,000 cfs vs. μ=15,525, σ=12,649). Independently cross-checked: −0.200 ✓
- Idempotent (md5-verified re-runs). Full transform runs in ~1.3s.

**Key edge cases documented:**
1. USGS-01358000 water_temp has data only in 2024 → outside baseline → anomaly_score null (correct)
2. 1,519 observed days have null anomaly (winter ice gaps in baseline DOYs)
3. 1,143 raw rows had null values → treated as gaps (completeness_score=0)
4. Extreme z values trace to near-zero-σ windows on stable baseflow — flag for downstream filtering

**Build 2b note:** Full 52-gauge ingestion complete. 633,257 rows, 6.8 MB parquet, 30 API requests, 0 failures. entity_id-sorted row groups for pruning.

**Result:** 633,257 rows across 52 gauges × 3 parameters × 2004-01-01→2026-08-02. 6.8 MB parquet (69 partitions). 30 API requests, 0 failures.
- streamflow: 428,875 rows | gage_height: 110,517 | water_temperature: 93,865
- 62 empty metric-gauge combos (expected sparsity — gage height/temp not measured at every gauge)
- entity_id-sorted row groups (size 8192) for DuckDB row-group pruning on site-filtered queries
- Raw cache: 318 MB (36 GeoJSON pages, retained)
- Independently verified: 52 distinct entity_ids, correct date range, correct row counts

**Note on session glitches (2026-08-02 20:xx):** garbled/repeated messages and a hallucinated image-prompt were caused by kimi-k3 model output degradation, not pipeline or rendering issues. User switched models to resolve.

---

## Session: 2026-08-03 → 2026-08-04 — Dashboard Enhancements + Two-Page App + Data Update Pipeline

### 1. Anomaly Scorecards + Monthly Bar Chart — ✅ COMPLETE
- **New file:** `components/anomaly_scorecards.py` — top 10 anomalous dates, #1 visible, hover reveals ranks 2-10 with per-metric breakdown
- Monthly anomaly bar chart with dynamic threshold (mean+1σ ≈ 113 events), teal/crimson legend
- Placed above the sticky filter bar; NOT affected by metric/date filters (global view)
- 3 new query functions in `queries.py`: `get_top_anomaly_dates`, `get_anomaly_date_breakdown`, `get_monthly_anomaly_counts`
- CSS appended to `assets/style.css` for scorecard/dropdown/chart styling

### 2. Date Picker Upgrade — ✅ COMPLETE
- Replaced `dcc.DatePickerSingle` with `dmc.DatePickerInput` (Dash Mantine Components)
- DMC has drill-down nav: click header → decade → year → month → day (no more 20 years of arrow clicking)
- Removed separate year dropdown (no longer needed)
- Wrapped entire layout in `dmc.MantineProvider` with dark color scheme + teal accent
- Updated callbacks in `map_panel.py` and `fastest_risers_table.py` to read `"value"` prop instead of `"date"`
- `dash-mantine-components==2.8.0` installed in venv + added to `requirements.txt`

### 3. Filter Clarity Fixes — ✅ COMPLETE
1. **Flashiness card** — added calendar year sub-label (e.g. "2026 calendar year") so it's clear why the value only changes with the year, not the exact date
2. **Hydrograph** — wired to `selected-date` store; range window now ends at the selected date instead of dataset's last date. Callback has 4 inputs: station, metric, range, selected-date
3. **Flow Percentile sub-label** — changed from "vs 2004–2026 history" to "rank among all observations (2004–2026)"

### 4. Two-Page App with Guide — ✅ COMPLETE
- App refactored to Dash pages: `pages/dashboard.py` (existing dashboard) + `pages/guide.py` (new)
- Navbar with "Dashboard" / "Guide" links, persistent across both pages
- Guide page has 8 styled section cards: Purpose, Proposed Use Case, Other Applications, Component Guide (all 10 components), Color Scale (with visual swatches), Data Sources & Methodology, Technical Stack, Gauge Network
- Guide content researched by subagent that read entire codebase → `_guide_content.md` → implemented as styled Dash page
- Verified: both pages return HTTP 200, 18 callbacks intact, no existing component files modified

### 5. Personality Card Alignment Fix — ✅ COMPLETE
- Flashiness card was misaligned with Flow Percentile and Record Proximity cards
- Root cause: Flashiness used a small text badge (~30px) while the other two used fixed-height `dcc.Graph` progress bars (52px)
- Fix: CSS flex column on `.personality-card` + `margin-top: auto` on last child pins extras to bottom; badge wrapper given matching 46px height + flex centering

### 6. New Sub-Agent: qwen-planner — ✅ COMPLETE
- **Model:** `openrouter/qwen/qwen3.8-max` (alias: `qwen`)
- **Capabilities:** multimodal (text+image+video→text), 1M context, mandatory reasoning
- **Use cases:** architecture, deep planning, image/video analysis, cross-referencing documents
- Added to gateway config, `allowAgents` for main agent, model alias `qwen`
- Workspace: `~/.openclaw/workspace/qwen-planner`
- SOUL.md and MEMORY.md updated with all three sub-agents

### 7. Data Update Pipeline — ✅ BUILT (⚠️ NOT YET RUN — USGS rate limit)

**Goal:** Robust daily incremental update that fetches new/revised data from USGS, upserts into raw_observations, and rebuilds all metrics.

**Three subagents built this in parallel:**

#### 7a. Parameterize Build Scripts — ✅ COMPLETE
- `build_metrics.py` — hardcoded `TODAY = date(2026, 8, 2)` replaced with `--today` flag (defaults to system date)
- `build_category_metrics.py` — same `--today` flag added
- `ingest_daily.py` — added `--since`/`--end` flags for incremental fetching, fixed no-expiry cache bug (now TTL-based, auto-bypassed in `--since` mode), fixed clobbering bug (partial fetches were overwriting year partitions with only the incremental slice)
- Backward compat verified: `--today 2026-08-02` reproduces exact baseline row counts

#### 7b. `update_data.py` — ✅ COMPLETE (556 lines)
- Checks `max(observed_at)` → determines fetch window (last 30 days for revision checks + 2 days for stragglers)
- Fetches from USGS API, bypassing cache (provisional data may have been revised)
- Upsert logic keyed on `(entity_id, parameter_code, observed_at)`: updates changed rows, appends new ones
- Only rewrites affected year partitions (not all years)
- Full backup to `data/backup/YYYY-MM-DD/` before any writes (~32 MB)
- Auto-restore from backup on write/rebuild failure
- Full metrics rebuild via subprocess (~3 seconds)
- Logs to stdout + `data/UPDATE_LOG.md`
- `--dry-run` mode: fetch + compare only, write nothing
- Error handling: all-gauges-fail → exit 1 (data untouched), some-gauges-fail → continue, write/rebuild-fail → restore from backup
- **Verified:** agent ran real dry-runs (found 121 new rows for Aug 3-4, 41 updated rows from USGS revisions), sandbox end-to-end test, failure injection, idempotency check

#### 7c. Scheduling + Safety — ✅ COMPLETE
- **launchd plist:** `~/Library/LaunchAgents/ai.openclaw.river-data-update.plist` — runs daily at 7:00 AM PT (14:00 UTC), NOT loaded yet
- **Healthcheck:** `scripts/healthcheck.py` — verifies row counts, date freshness, NULL rates, gauge/metric counts, app import
- **Manual run script:** `scripts/run-update.sh` — executable, logs to `data/update-manual.log`

### ⚠️ WHERE WE LEFT OFF

**The data update pipeline is built and tested but NOT yet run for real.** USGS rate-limited our IP (the subagent's testing fired too many requests). The rate limit should clear in ~15-20 minutes.

**To complete:**
1. Wait for USGS rate limit to clear (~15-20 min from 01:35 PDT)
2. Run the real update: `cd ~/dev/signals-before-disruption && .venv/bin/python3 update_data.py`
3. Run healthcheck: `.venv/bin/python3 scripts/healthcheck.py`
4. Verify app still loads: `.venv/bin/python3 -c "import app; print('OK')"`
5. Load the launchd job: `launchctl load ~/Library/LaunchAgents/ai.openclaw.river-data-update.plist`
6. (Optional) If no new data was available from USGS, do the trim-10 test: remove latest 10 rows from raw_observations, run update, verify it pulls them back

**Files created/modified this session:**
- `components/anomaly_scorecards.py` — NEW
- `pages/dashboard.py` — NEW
- `pages/guide.py` — NEW
- `pages/__init__.py` — NEW
- `update_data.py` — NEW (556 lines)
- `scripts/healthcheck.py` — NEW
- `scripts/run-update.sh` — NEW
- `~/Library/LaunchAgents/ai.openclaw.river-data-update.plist` — NEW
- `app.py` — modified (MantineProvider, DMC date picker, pages, navbar)
- `queries.py` — modified (3 new anomaly query functions)
- `components/personality_cards.py` — modified (flashiness year label, percentile sub-label, badge alignment)
- `components/hydrograph.py` — modified (wired to selected-date store, 4th callback input)
- `components/map_panel.py` — modified (date picker prop change)
- `components/fastest_risers_table.py` — modified (date picker prop change)
- `assets/style.css` — modified (scorecard/dropdown/chart styles, personality card flex fix)
- `build_metrics.py` — modified (`--today` flag, parameterized)
- `build_category_metrics.py` — modified (`--today` flag)
- `ingest_daily.py` — modified (`--since`/`--end` flags, cache fix, clobbering fix)
- `requirements.txt` — modified (added `dash-mantine-components`)
- `SOUL.md` — modified (sub-agent delegation section)
- `MEMORY.md` — modified (sub-agents entry)
