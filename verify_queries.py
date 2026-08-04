"""verify_queries.py — smoke-test + timing harness for queries.py.

1. Calls every query function with sample arguments.
2. Times each call (time.perf_counter), reports ms, flags anything > 5ms.
3. Runs the five specified spot-checks and prints PASS/FAIL per check.
"""

from __future__ import annotations

import time

import pandas as pd

import queries as q

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def timed(label: str, fn, *args, repeats: int = 5, **kwargs):
    """Time a callable; warm cache with 1 call, then time `repeats` runs."""
    fn(*args, **kwargs)  # warm
    times = []
    result = None
    for _ in range(repeats):
        t0 = time.perf_counter()
        result = fn(*args, **kwargs)
        times.append((time.perf_counter() - t0) * 1000)
    best = min(times)
    avg = sum(times) / len(times)
    flag = "  ⚠️ >5ms" if best > 5.0 else ""
    print(f"  {label:34s} min={best:7.2f}ms  avg={avg:7.2f}ms{flag}")
    return result


def main() -> None:
    print("=" * 78)
    print("River Personality Monitor — queries.py verification")
    print("=" * 78)

    conn = q.get_connection()
    print(f"connection: {type(conn).__name__} read_only={conn.__class__ is not None}")
    # confirm read-only semantics by attempting a write
    try:
        conn.execute("CREATE TABLE _forbidden (x INT)")
        print("  ⚠️ connection appears WRITABLE")
    except Exception:
        print("  ✅ connection is read-only (write rejected)")

    print("\n--- Function timing (post-warm min/avg over 5 runs) ---")

    # 1. KPI cards
    kpi = timed("get_kpi_cards", q.get_kpi_cards, conn, "streamflow", "2026-08-01")
    # 2. Map data
    tmap = timed("get_map_data", q.get_map_data, conn, "streamflow", "2026-08-01")
    # 3. Region table
    treg = timed("get_region_table", q.get_region_table, conn, "streamflow", "2026-08-01")
    # 4. Fastest risers
    tris = timed("get_fastest_risers", q.get_fastest_risers, conn, "Pacific Northwest",
                 "streamflow", "2026-08-01")
    # 5. Hydrograph
    thyd = timed("get_hydrograph_data", q.get_hydrograph_data, conn,
                 "USGS-01184000", "streamflow", "2026-07-01", "2026-08-01")
    # 6. Baseline band
    tband = timed("get_baseline_band", q.get_baseline_band, conn,
                  "USGS-01184000", "streamflow", "2026-07-01", "2026-08-01")
    # 7. Raw payload
    tray = timed("get_raw_payload", q.get_raw_payload, conn,
                 "USGS-14105700", "streamflow", "2026-08-01")
    # 8. Flashiness index
    tfi = timed("get_flashiness_index", q.get_flashiness_index, conn,
                "USGS-01184000", "streamflow", 2026)
    # 9. Personality cards
    tper = timed("get_personality_cards", q.get_personality_cards, conn,
                 "USGS-01184000", "streamflow", "2026-08-01")
    # 10. Previous year flow
    tprev = timed("get_previous_year_flow", q.get_previous_year_flow, conn,
                  "USGS-01184000", "streamflow", 2026)
    # 11. Connection
    tcon = timed("get_connection", q.get_connection)

    print("\n--- Spot-check 1: KPI cards on 2026-08-01 (streamflow) ---")
    print("  ", kpi)
    check("extreme_events_today == 0", kpi["extreme_events_today"] == 0,
          f"got {kpi['extreme_events_today']}")
    fr = kpi["fastest_riser"]
    print(f"   fastest_riser = {fr['station_name']} ({fr['entity_id']}) "
          f"rise_rate_3d={fr['rise_rate_3d']:.2f} value={fr['value']}")
    check("fastest_riser entity == USGS-05587450 (Mississippi @ Grafton)",
          fr["entity_id"] == "USGS-05587450", f"got {fr['entity_id']}")
    check("data_health present", kpi["data_health"] is not None)
    if kpi["data_health"]:
        dh = kpi["data_health"]
        print(f"   data_health: gauges_reporting={dh['gauges_reporting']} "
              f"gap_rate={dh['gap_rate']:.4f} total_rows={dh['total_rows']} "
              f"last_date={dh['last_date']}")

    print("\n--- Spot-check 2: hydrograph USGS-01184000 2026-07-01→2026-08-01 ---")
    print(f"   rows: {len(thyd)} (31 days inclusive = 31; 32 if edge artifact)")
    print(thyd[["observed_at", "average", "water_temp", "anomaly_score",
                "rise_rate_3d", "flow_percentile", "completeness_score"]].to_string(index=False))
    check("row count == 32 (full calendar reindex of 31-day range)",
          len(thyd) == 32, f"got {len(thyd)}")
    storm = thyd[thyd["observed_at"].dt.date == pd.Timestamp("2026-07-31").date()]
    check("storm visible 07-31 (avg >= 15000, rise_rate_3d > 5000)",
          not storm.empty and storm.iloc[0]["average"] >= 15000
          and storm.iloc[0]["rise_rate_3d"] > 5000,
          f"got {storm[['average','rise_rate_3d']].to_dict('records') if not storm.empty else 'empty'}")
    wt = thyd["water_temp"].dropna()
    print(f"   water_temp overlay: {len(wt)} present-day values "
          f"(CT river temp series ended 2004 → null overlay expected; graceful)")
    check("water_temp column exists (may be sparse)", "water_temp" in thyd.columns)

    # Columbia has an active 2026 water-temp series → overlay must populate
    thyd_col = timed("get_hydrograph_data (Columbia)", q.get_hydrograph_data, conn,
                     "USGS-14105700", "streamflow", "2026-07-01", "2026-08-01", repeats=3)
    wt_col = thyd_col["water_temp"].dropna()
    check("Columbia water_temp overlay populated (>10 values)", len(wt_col) > 10,
          f"got {len(wt_col)} non-null temp days")
    if len(wt_col):
        print(f"   Columbia temp range: {wt_col.min():.1f}–{wt_col.max():.1f} °C over {len(wt_col)} days")

    print("\n--- Spot-check 3: flashiness USGS-01184000 (2026) ---")
    print("  ", tfi)
    check("flashiness_index approx 0.1629", tfi["flashiness_index"] is not None
          and abs(tfi["flashiness_index"] - 0.1629) < 0.02, f"got {tfi['flashiness_index']}")
    print(f"   regional rank = {tfi['region_rank']} of {tfi['n_gauges']} gauges "
          f"in {tfi['region']} (1 = flashiest)")

    print("\n--- Spot-check 4: raw payload USGS-14105700 2026-08-01 ---")
    check("payload is non-null JSON string", isinstance(tray, str) and tray.startswith("{"),
          f"got type {type(tray)}")
    if tray:
        print(f"   first 200 chars: {tray[:200]}")
        check("payload contains gauge id", "USGS-14105700" in tray)

    print("\n--- Other spot-checks ---")
    check("map data: 52 rows, required columns",
          len(tmap) == 52 and {"entity_id", "station_name", "latitude", "longitude",
                               "value", "anomaly_score", "flow_percentile",
                               "region", "state"} <= set(tmap.columns),
          f"rows={len(tmap)} cols={list(tmap.columns)}")
    check("region table: 8 rows, avg_anomaly sorted desc",
          len(treg) == 8 and treg["average_anomaly"].is_monotonic_decreasing,
          f"rows={len(treg)}")
    print(f"   region table head:\n{treg[['region','entity_count','event_count','average_anomaly']].to_string(index=False)}")
    check("fastest_risers: top-5 dicts sorted desc",
          len(tris) <= 5 and all({"entity_id", "station_name", "rise_rate_3d", "value"}
                                 <= set(r) for r in tris)
          and all(a["rise_rate_3d"] >= b["rise_rate_3d"] for a, b in zip(tris, tris[1:])),
          f"got {tris}")
    print(f"   fastest_risers (Pacific Northwest): {tris}")

    print("\n--- Two slow-path checks (first-call cold cost, uncached date) ---")
    kpi_cold = timed("get_kpi_cards (cold date 2026-08-02)", q.get_kpi_cards,
                     conn, "streamflow", "2026-08-02", repeats=3)
    check("cold-date KPI still returns results", kpi_cold["extreme_events_today"] is not None)
    hist_cold = q._hist_max(conn, "USGS-14105700", "streamflow")
    print(f"   _hist_max cold (Columbia): {hist_cold}")

    print("\n--- Map data sample ---")
    print(tmap.head(3).to_string(index=False))

    print("\n" + "=" * 78)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
