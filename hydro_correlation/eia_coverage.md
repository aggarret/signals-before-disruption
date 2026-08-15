# EIA Coverage: Conventional Hydroelectric (HYC) Monthly Generation

## Endpoint
`https://api.eia.gov/v2/electricity/electric-power-operational-data/data/`

## Confirmation
EIA API reach/auth: **CONFIRMED** (returned HYC rows).

## Units
Generation reported in **thousand megawatthours** (`generation-units`).

## Coverage Window
- **Earliest month:** 2001-01
- **Latest month:** 2026-05

## Locations
Distinct `location` codes across the dataset: **61**
See `eia_locations.json` for the mapping {location_code: stateDescription}.

## Schema
Fields per row: period (YYYY-MM), location, stateDescription, sectorDescription,
fueltypeid (HYC), fuelTypeDescription, generation (string), generation-units.

## CAVEATS
(a) **MONTHLY grain** — this is a monthly series, not daily or hourly.
(b) **By state OR census-region**, NOT per-gauge and NOT daily per-balancing-authority.
    Granularity is at the state/sector level, not individual hydro plants or gauges.
(c) **No fuel-specific DAILY series exists in this route** — only monthly granularity
    is available for HYC generation here.
(d) **Merges may be needed for multi-state census regions** — some `location` codes
    represent census-region aggregates spanning multiple states; sums will require
    care to avoid double counting or to align territories.
