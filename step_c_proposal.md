# Step C — Polars Transformation Proposal
**River Personality Monitor · Phase 1 · 2026-08-02**
*Status: PROPOSAL for discussion — no implementation code yet.*

---

## 1. What we're transforming

**Input:** USGS OGC API daily-values payloads — GeoJSON FeatureCollections where each
feature carries data in `properties` and a Point `geometry`.

**Output:** Three tables matching the agreed architecture, written as parquet,
served later by DuckDB/PyArrow.

```
raw_observations      ← one row per (gauge × parameter × day) — the atomic layer
daily_entity_metrics  ← one row per (gauge × metric × day) with stats + anomaly scores
daily_category_metrics← one row per (region × metric × day) rolled up
```

---

## 2. Layer 1: `raw_observations` — flattening the GeoJSON

Mapping from `features[].properties` + `geometry`:

| Target column | Source | Transform |
|---|---|---|
| `source` | — | constant `"usgs_daily"` |
| `entity_id` | `monitoring_location_id` | as-is (e.g. `USGS-02238500`) |
| `observed_at` | `time` | `str.to_date()` — ISO, parses clean |
| `collected_at` | — | ingestion timestamp (UTC now), for freshness audits |
| `metric` | `parameter_code` | map via lookup: `00060→streamflow`, `00065→gage_height`, `63160→stream_level` — **keep the raw code too** (`parameter_code` column) so unmapped codes don't die silently |
| `value` | `value` | `cast(Float64, strict=False)` — source is a *string*; non-numeric qualifiers become null, flagged |
| `unit` | `unit_of_measure` | as-is (`ft^3/s`, `ft`, `deg C`) |
| `latitude` / `longitude` | `geometry.coordinates[1]` / `[0]` | explode the 2-element array |
| `approval_status` | `approval_status` | keep — Provisional vs Approved is a data-quality dimension |
| `qualifier` | `qualifier` | keep — ice-affected, estimated, etc. matter for anomaly work |
| `raw_payload` | whole `properties` dict | serialized JSON string — the audit/recalc escape hatch |

**Polars shape:** `pl.from_dicts(features)` → `unnest("properties")` →
`with_columns(...)` for casts/maps → concat across paginated responses.
Lazy frames throughout; collect once per gauge-batch.

---

## 3. The dimension table we didn't have before: `stations`

The daily-values payload gives us coordinates but **not** name, state, or watershed.
Proposal: pull the OGC `monitoring-locations` collection once per gauge into a small
`stations` dimension table:

`entity_id | station_name | state | huc_code (watershed) | agency | site_type | lat | lon | first_year_of_record`

This is what powers region rollups (`daily_category_metrics`) and the map. One-time
fetch per gauge, cheap.

---

## 4. Layer 2: `daily_entity_metrics` — where the personality lives

Grain: one row per (`date`, `entity_id`, `metric`). Built from `raw_observations`
via group-by aggregations plus **historical baselines**:

| Column | Derivation |
|---|---|
| `observation_count` | count of raw rows that day (usually 1; >1 flags duplication) |
| `minimum` / `maximum` / `average` | trivial for daily values (single obs) but the schema generalizes to sub-daily sources later |
| `daily_change` | `value - value.shift(1)` per entity+metric, sorted by date |
| `rise_rate_3d` | 3-day rolling slope — the "fastest-rising rivers" input |
| `flow_percentile` | rank of today's value vs. all historical values for this gauge |
| `anomaly_score` | **seasonal z-score**: z = (x − μ_doy) / σ_doy where μ/σ are computed over the same day-of-year (±7-day window) across the full 20-yr baseline |
| `completeness_score` | 1.0 if observed, 0 for gap days (we'll explicitly reindex the calendar per gauge so gaps are *rows*, not absences) |
| `record_proximity` | value / historical_max — >0.95 means flirting with the record |

**Key decision to discuss:** anomaly baselines need the *full* history loaded before
scoring. Proposal: compute baselines once per gauge as a `seasonal_baselines` table
(`entity_id, metric, day_of_year, mu, sigma, n_years`), then join — keeps the daily
job a cheap join instead of a 20-year rescan.

---

## 5. Layer 3: `daily_category_metrics` — the national pulse

Grain: one row per (`date`, `region`, `metric`). Region = state first, HUC watershed
as an alternate rollup (both come from `stations`).

- `entity_count` — gauges reporting that day (data-health signal itself)
- `average_anomaly` — mean z-score across the region
- `extreme_entity_count` — count of |z| ≥ 2.5 gauges
- `fastest_riser` — entity_id with max `rise_rate_3d` (top-N kept as a struct/array column? — open question, see §7)

---

## 6. Ingestion mechanics (proposal)

- **Gauge selection:** ~50 gauges chosen for diversity — mix of regions (Pacific NW,
  Colorado basin, Mississippi, Northeast, Southeast, Southwest), mix of river sizes,
  all with ≥20 years of continuous daily record. Selection query against
  `monitoring-locations`, manually curated to ~50.
- **Chunking:** one request per (gauge × 5-year window) — keeps payloads sane and
  makes retries cheap. 50 gauges × 4 chunks = 200 requests. Respectful pacing,
  cached to disk (`data/raw_cache/`) so re-runs don't re-hit the API.
- **Parameters pulled:** `00060` (streamflow), `00065` (gage height), `00010` (water temperature).
- **Storage:** parquet, partitioned `metric/year` — ~55M rows theoretical max for
  50 gauges × 20 yr × 3 params; realistically ~10–20M rows. Trivial for DuckDB,
  and a genuine size flex for the portfolio.
- **Idempotency:** re-ingesting a window overwrites its parquet partition, never appends.

---

## 7. Decisions (locked 2026-08-02)

1. **Parameter scope:** streamflow (`00060`) + gage height (`00065`) + **water temperature (`00010`)** — all three personality dimensions in the first pull.
2. **Gap handling:** reindex-to-calendar with explicit null rows. Completeness is a first-class signal, not an absence. **Called out in README.md.**
3. **`fastest_riser` storage:** top-5 per region-day as a nested list column (polars `list[struct]`).
4. **Baselines:** fixed 20-year baseline for reproducibility.

---

## 8. What happens after approval

1. Sub-agent builds the `stations` dimension fetcher + gauge selection list → your review
2. Sub-agent builds the raw ingestion loop (polars, chunked, cached) → your review
3. Baseline + daily metrics transforms → your review
4. Then, and only then, we talk Dash.
