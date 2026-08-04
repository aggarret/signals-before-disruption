"""
Signals Before Disruption — Phase 1: Source Assessment & Raw Data Inspection

Step A: Pull the 5 most recent records from the openFDA Device Enforcement API
and print the raw JSON payload for schema inspection.

Step B: Pull the last 7 days of daily values for one monitoring location from
the modernized USGS Water Data OGC API and print the raw JSON payload.
"""

import json
from datetime import datetime, timedelta, timezone

import requests

OPENFDA_DEVICE_ENFORCEMENT_URL = "https://api.fda.gov/device/enforcement.json"


def fetch_recent_device_enforcement(limit: int = 5) -> dict:
    """
    Fetch the most recent device enforcement (recall) records from openFDA.

    Returns the full decoded JSON payload (meta + results).
    """
    params = {
        "limit": limit,
        "sort": "report_date:desc",
    }
    response = requests.get(OPENFDA_DEVICE_ENFORCEMENT_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


USGS_DAILY_VALUES_URL = "https://api.waterdata.usgs.gov/ogcapi/v0/collections/daily/items"


def fetch_usgs_daily_values(
    monitoring_location_id: str = "USGS-02238500",
    days: int = 7,
) -> dict:
    """
    Fetch the last `days` days of daily values for a single monitoring location
    from the modernized USGS Water Data API (OGC API - Features, JSON format).

    Returns the full decoded JSON payload (a GeoJSON FeatureCollection).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "f": "json",
        "monitoring_location_id": monitoring_location_id,
        "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "limit": 100,
    }
    response = requests.get(USGS_DAILY_VALUES_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    print("=" * 80)
    print("STEP A: openFDA Device Enforcement — 5 most recent records")
    print("=" * 80)
    fda_payload = fetch_recent_device_enforcement(limit=5)
    print(json.dumps(fda_payload, indent=2))

    print()
    print("=" * 80)
    print("STEP B: USGS Daily Values — USGS-02238500, last 7 days")
    print("=" * 80)
    usgs_payload = fetch_usgs_daily_values("USGS-02238500", days=7)
    print(json.dumps(usgs_payload, indent=2))
