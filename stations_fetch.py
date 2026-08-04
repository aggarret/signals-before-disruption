#!/usr/bin/env python3
"""
stations_fetch.py — Stations dimension fetcher for the River Personality Monitor.

Fetches full station metadata from the USGS OGC API `monitoring-locations`
collection for a curated list of stream gauges and rewrites `stations.csv`.

Usage:
    python3 stations_fetch.py                 # reads gauge IDs from stations.csv, refreshes metadata
    python3 stations_fetch.py 01467087 01570500 ...   # fetches metadata for the given gauge IDs

Output: stations.csv with columns
    entity_id, station_name, state, region, latitude, longitude,
    hydrologic_unit_code, site_type, agency_code, drainage_area,
    first_year_of_record, earliest_verified_year

Notes:
- Uses only the modern USGS OGC API (api.waterdata.usgs.gov/ogcapi/v0).
- Respects the API: short sleep between requests, and backs off on HTTP 429
  (OVER_RATE_LIMIT) responses.
- Handles OGC `links`-based pagination on the monitoring-locations collection.
- Gauge IDs may be given with or without the "USGS-" prefix; both are accepted.
"""

import argparse
import csv
import os
import sys
import time

import requests

API_BASE = "https://api.waterdata.usgs.gov/ogcapi/v0"
ML_ITEMS = f"{API_BASE}/collections/monitoring-locations/items"
DAILY_ITEMS = f"{API_BASE}/collections/daily/items"
TS_META_ITEMS = f"{API_BASE}/collections/time-series-metadata/items"

PARAM_STREAMFLOW = "00060"
STATISTIC_MEAN = "00003"

# Regional grouping used for the curated gauge list.
REGION_BY_STATE = {
    # Pacific Northwest
    "WA": "Pacific Northwest", "OR": "Pacific Northwest", "ID": "Pacific Northwest",
    # California
    "CA": "California",
    # Great Basin / Southwest (interior basin + desert southwest outside the Colorado mainstem)
    "NV": "Great Basin/Southwest", "UT": "Great Basin/Southwest",
    "AZ": "Great Basin/Southwest", "NM": "Great Basin/Southwest",
    # Colorado River basin
    "CO": "Colorado River basin", "WY": "Colorado River basin",
    # Upper Midwest / Missouri
    "MN": "Upper Midwest/Missouri", "WI": "Upper Midwest/Missouri",
    "IA": "Upper Midwest/Missouri", "SD": "Upper Midwest/Missouri",
    "ND": "Upper Midwest/Missouri", "NE": "Upper Midwest/Missouri",
    "MO": "Upper Midwest/Missouri", "KS": "Upper Midwest/Missouri",
    "MT": "Upper Midwest/Missouri", "IL": "Upper Midwest/Missouri",
    # Mississippi basin (lower Mississippi + tributaries)
    "MS": "Mississippi basin", "LA": "Mississippi basin", "AR": "Mississippi basin",
    "TN": "Mississippi basin", "KY": "Mississippi basin", "OH": "Mississippi basin",
    "IN": "Mississippi basin", "AL": "Mississippi basin",
    # Gulf Coast / Southeast
    "FL": "Gulf Coast/Southeast", "GA": "Gulf Coast/Southeast",
    "SC": "Gulf Coast/Southeast", "NC": "Gulf Coast/Southeast",
    "VA": "Gulf Coast/Southeast", "WV": "Gulf Coast/Southeast",
    # Northeast / Mid-Atlantic
    "PA": "Northeast/Mid-Atlantic", "NY": "Northeast/Mid-Atlantic",
    "NJ": "Northeast/Mid-Atlantic", "MD": "Northeast/Mid-Atlantic",
    "DE": "Northeast/Mid-Atlantic", "MA": "Northeast/Mid-Atlantic",
    "CT": "Northeast/Mid-Atlantic", "RI": "Northeast/Mid-Atlantic",
    "VT": "Northeast/Mid-Atlantic", "NH": "Northeast/Mid-Atlantic",
    "ME": "Northeast/Mid-Atlantic", "DC": "Northeast/Mid-Atlantic",
    # Fallbacks
    "AK": "Pacific Northwest", "HI": "Pacific Northwest",
}

CSV_FIELDS = [
    "entity_id",
    "station_name",
    "state",
    "region",
    "latitude",
    "longitude",
    "hydrologic_unit_code",
    "site_type",
    "agency_code",
    "drainage_area",
    "first_year_of_record",
    "earliest_verified_year",
]


def normalize_site_id(raw: str) -> str:
    """Accept 'USGS-01467087' or '01467087'; return 'USGS-01467087'."""
    raw = raw.strip()
    if raw.upper().startswith("USGS-"):
        return f"USGS-{raw[5:]}"
    return f"USGS-{raw}"


def get_json(url: str, params: dict, retries: int = 8, base_sleep: float = 0.5):
    """GET with JSON handling and 429 backoff. Returns parsed dict."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"  ! request error ({exc}); retry {attempt + 1}/{retries}", file=sys.stderr)
            time.sleep(base_sleep * (attempt + 1))
            continue
        if resp.status_code == 429:
            wait = base_sleep * 30 * (attempt + 1)  # 15s, 30s, 45s, ...
            print(f"  ! HTTP 429 (rate limit); backing off {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            print(f"  ! HTTP {resp.status_code} for {url} {params}", file=sys.stderr)
            time.sleep(base_sleep * (attempt + 1))
            continue
        try:
            return resp.json()
        except ValueError:
            time.sleep(base_sleep * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url} with params {params} after {retries} retries")


def fetch_station_metadata(site_ids, sleep_between=0.5):
    """Fetch full metadata records for a list of normalized USGS- site ids.

    Uses comma-separated `id` queries (batches of 50) and follows the OGC
    `links`-based pagination when a query spans multiple pages.
    """
    metadata = {}
    BATCH = 50
    for i in range(0, len(site_ids), BATCH):
        batch = site_ids[i:i + BATCH]
        params = {
            "f": "json",
            "id": ",".join(batch),
            "limit": BATCH,
        }
        url = ML_ITEMS
        while url:
            if url == ML_ITEMS:
                data = get_json(url, params)
            else:
                # OGC next link already carries its own query parameters
                sep = "&" if "?" in url else "?"
                if "f=" not in url:
                    url = f"{url}{sep}f=json"
                data = get_json(url, {})
            for feature in data.get("features", []):
                props = feature.get("properties", {}) or {}
                gid = props.get("id") or feature.get("id")
                geometry = feature.get("geometry") or {}
                coords = geometry.get("coordinates") or [None, None]
                metadata[gid] = {
                    "entity_id": gid,
                    "station_name": props.get("monitoring_location_name"),
                    "state": props.get("state_name"),
                    "state_code": props.get("state_code"),
                    "latitude": coords[1] if len(coords) > 1 else None,
                    "longitude": coords[0] if coords else None,
                    "hydrologic_unit_code": props.get("hydrologic_unit_code"),
                    "site_type": props.get("site_type"),
                    "agency_code": props.get("agency_code"),
                    "drainage_area": props.get("drainage_area"),
                }
            # OGC paging: next link has 'next' rel and carries its own params.
            next_link = None
            for link in data.get("links", []):
                if link.get("rel") == "next":
                    next_link = link.get("href")
                    break
            if next_link:
                url = next_link
                time.sleep(sleep_between)
            else:
                url = None
        time.sleep(sleep_between)
    return metadata


def verify_2004(site_id, sleep_between=0.5):
    """Return True if the gauge has daily streamflow (00060) observations in Jan 2004."""
    data = get_json(
        DAILY_ITEMS,
        {
            "f": "json",
            "monitoring_location_id": site_id,
            "parameter_code": PARAM_STREAMFLOW,
            "datetime": "2004-01-01T00:00:00Z/2004-02-01T00:00:00Z",
            "limit": 1,
        },
    )
    return len(data.get("features", [])) > 0


def fetch_period_of_record(site_ids, sleep_between=0.5):
    """Return {site_id: begin_year} for daily-mean streamflow from time-series-metadata.

    Some sites have multiple 00060/00003 series (e.g. a discontinued legacy
    series plus the active one); the series with the LATEST end date is kept.
    """
    result = {}
    BATCH = 30
    for i in range(0, len(site_ids), BATCH):
        batch = site_ids[i:i + BATCH]
        data = get_json(
            TS_META_ITEMS,
            {
                "f": "json",
                "monitoring_location_id": ",".join(batch),
                "parameter_code": PARAM_STREAMFLOW,
                "statistic_id": STATISTIC_MEAN,
                "limit": 500,
            },
        )
        for feature in data.get("features", []):
            props = feature.get("properties", {}) or {}
            gid = props.get("monitoring_location_id")
            begin = props.get("begin_utc")
            end = props.get("end_utc") or ""
            if gid and begin:
                cur = result.get(gid)
                if cur is None or end > cur.get("end", ""):
                    result[gid] = {"begin": begin[:4], "end": end}
        time.sleep(sleep_between)
    return {gid: v["begin"] for gid, v in result.items()}


def read_existing_ids(csv_path):
    """Read entity_ids from an existing stations.csv, preserving their region."""
    existing = {}
    if not os.path.exists(csv_path):
        return existing
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("entity_id"):
                existing[row["entity_id"].strip()] = row
    return existing


def region_for(state_name, state_code):
    if state_name:
        abbr = STATE_ABBR.get(state_name.strip().upper())
        if abbr and abbr in REGION_BY_STATE:
            return REGION_BY_STATE[abbr]
    if state_code and state_code in STATE_CODE_ABBR:
        abbr = STATE_CODE_ABBR[state_code]
        if abbr in REGION_BY_STATE:
            return REGION_BY_STATE[abbr]
    return "Unknown"


def write_csv(rows, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


def summarize(rows):
    from collections import Counter
    by_region = Counter(r.get("region", "Unknown") for r in rows)
    by_state = Counter(r.get("state", "Unknown") for r in rows)
    print(f"\n=== stations summary: {len(rows)} gauges ===")
    print("\nby region:")
    for region, n in sorted(by_region.items()):
        print(f"  {region:28s} {n}")
    print("\nby state:")
    for state, n in sorted(by_state.items()):
        print(f"  {state:28s} {n}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gauge_ids", nargs="*", help="USGS gauge ids (8-digit numbers or USGS-xxxx)")
    parser.add_argument("--csv", default="stations.csv", help="path to stations.csv (default: stations.csv)")
    parser.add_argument("--sleep", type=float, default=0.5, help="seconds between API requests (default 0.5)")
    parser.add_argument("--verify", action="store_true",
                        help="run the Jan-2004 daily-streamflow probe per gauge; "
                             "gates earliest_verified_year on the probe result")
    args = parser.parse_args()

    csv_path = args.csv
    existing = read_existing_ids(csv_path)

    if args.gauge_ids:
        ids = [normalize_site_id(g) for g in args.gauge_ids]
        label = "requested gauges"
    else:
        if not existing:
            print("No gauge ids given and no stations.csv found. Nothing to do.", file=sys.stderr)
            sys.exit(1)
        ids = sorted(existing.keys())
        label = f"gauges from {csv_path}"

    print(f"Fetching metadata for {len(ids)} {label} ...")
    metadata = fetch_station_metadata(ids, sleep_between=args.sleep)
    missing = [sid for sid in ids if sid not in metadata]
    if missing:
        print(f"WARNING: no monitoring-locations record found for: {', '.join(missing)}", file=sys.stderr)

    print("Checking period of record (daily-mean streamflow) ...")
    por = fetch_period_of_record(list(metadata.keys()), sleep_between=args.sleep)

    verified_2004 = {}
    if args.verify:
        print("Running Jan-2004 daily streamflow probe per gauge ...")
        for sid in sorted(metadata.keys()):
            ok = verify_2004(sid, sleep_between=args.sleep)
            verified_2004[sid] = ok
            print(f"  {sid} -> {'OK' if ok else 'NO 2004 data'}")
            time.sleep(args.sleep)

    rows = []
    for sid in sorted(metadata.keys()):
        m = metadata[sid]
        state_name = m.get("state") or ""
        state_code = m.get("state_code") or ""
        begin_year = por.get(sid)
        first_year = begin_year if begin_year else ""
        # earliest_verified_year: the standard threshold this project verified
        # against (Jan-2004 daily streamflow probe). With --verify, gated on the
        # actual probe result; otherwise gated on the API's own record start.
        if args.verify:
            verified = "2004" if verified_2004.get(sid) else ""
        else:
            verified = "2004" if first_year and int(first_year) <= 2004 else ""
        region = region_for(state_name, state_code)
        # preserve region from existing csv if it was curated
        if sid in existing and existing[sid].get("region"):
            region = existing[sid]["region"]
        rows.append({
            "entity_id": sid,
            "station_name": m.get("station_name"),
            "state": state_name,
            "region": region,
            "latitude": m.get("latitude"),
            "longitude": m.get("longitude"),
            "hydrologic_unit_code": m.get("hydrologic_unit_code"),
            "site_type": m.get("site_type"),
            "agency_code": m.get("agency_code"),
            "drainage_area": m.get("drainage_area"),
            "first_year_of_record": first_year,
            "earliest_verified_year": verified,
        })

    write_csv(rows, csv_path)
    print(f"\nWrote {len(rows)} rows to {csv_path}")
    summarize(rows)


# state name -> abbrev, and FIPS code -> abbrev (from monitoring-locations state_code)
STATE_ABBR = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA",
    "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR", "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN",
    "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}
STATE_CODE_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY",
}


if __name__ == "__main__":
    main()
