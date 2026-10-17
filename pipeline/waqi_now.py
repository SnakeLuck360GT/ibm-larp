"""Latest measured air quality per state from the World Air Quality Index project (aqicn.org / WAQI).

Run from the repo root:  .venv/bin/python pipeline/waqi_now.py
Needs WAQI_TOKEN in the environment or in <repo>/.env (gitignored; free token from
https://aqicn.org/data-platform/token/). Writes data/out/waqi_now.json for the Command map.

For each state capital (data/resource_allocation/state_ref.csv) it takes the nearest WAQI station
(Malaysia's DOE stations) and keeps the reading, its time and WAQI's daily PM2.5 forecast. WAQI
reports pollutants as US AQI sub-indices, so PM2.5 is also converted to µg/m³ with the US EPA
breakpoints (pre-2024 table, the one WAQI documents); the conversion is approximate.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out" / "waqi_now.json"
REF = ROOT / "data" / "resource_allocation" / "state_ref.csv"
API = "https://api.waqi.info/feed/geo:{lat};{lon}/"
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")

# US EPA PM2.5 (24 h) breakpoints: (AQI lo, AQI hi, conc lo, conc hi) in µg/m³.
PM25_BREAKPOINTS = [(0, 50, 0.0, 12.0), (51, 100, 12.1, 35.4), (101, 150, 35.5, 55.4),
                    (151, 200, 55.5, 150.4), (201, 300, 150.5, 250.4), (301, 500, 250.5, 500.4)]


def pm25_aqi_to_ugm3(aqi):
    if aqi is None:
        return None
    a = float(aqi)
    for ilo, ihi, clo, chi in PM25_BREAKPOINTS:
        if a <= ihi:
            a = max(a, ilo)
            return round(clo + (a - ilo) * (chi - clo) / (ihi - ilo), 1)
    return round(PM25_BREAKPOINTS[-1][3], 1)


def token() -> str:
    tok = os.environ.get("WAQI_TOKEN")
    env = ROOT / ".env"
    if not tok and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("WAQI_TOKEN="):
                tok = line.split("=", 1)[1].strip()
    if not tok:
        raise SystemExit("WAQI_TOKEN not set (environment or .env)")
    return tok


def fetch_state(lat: float, lon: float, tok: str) -> dict:
    d = requests.get(API.format(lat=lat, lon=lon), params={"token": tok}, timeout=30).json()
    if d.get("status") != "ok":
        raise RuntimeError(d.get("data"))
    x = d["data"]
    iaqi = x.get("iaqi") or {}
    pm = (iaqi.get("pm25") or {}).get("v")
    fc = ((x.get("forecast") or {}).get("daily") or {}).get("pm25") or []
    return {
        "station": x["city"]["name"], "station_url": x["city"].get("url"),
        "station_geo": x["city"].get("geo"), "time": (x.get("time") or {}).get("iso"),
        "aqi": x.get("aqi") if isinstance(x.get("aqi"), (int, float)) else None,
        "dominant": x.get("dominentpol"),
        "pm25_aqi": pm, "pm25_ugm3": pm25_aqi_to_ugm3(pm),
        "forecast_pm25": [{"day": f["day"], "avg_aqi": f.get("avg"), "min_aqi": f.get("min"), "max_aqi": f.get("max"),
                           "avg_ugm3": pm25_aqi_to_ugm3(f.get("avg"))} for f in fc],
        "attributions": [{"name": a.get("name"), "url": a.get("url")} for a in x.get("attributions") or []],
    }


def main() -> None:
    tok = token()
    ref = pd.read_csv(REF)
    states, failed = {}, {}
    for r in ref.itertuples():
        try:
            states[r.state] = fetch_state(r.lat, r.lon, tok)
        except Exception as e:  # keep the other states if one station is down
            failed[r.state] = str(e)
    if not states:
        raise SystemExit(f"WAQI: no state fetched ({failed})")
    out = {
        "fetched_at": datetime.now(KL_TZ).isoformat(timespec="seconds"),
        "source": "World Air Quality Index Project (aqicn.org), nearest station to each state capital",
        "units": {"aqi": "US AQI", "pm25_ugm3": "µg/m³, converted from the US AQI PM2.5 sub-index (US EPA breakpoints)"},
        "states": states, "failed": failed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)
    print(f"waqi_now.json: {len(states)} states" + (f", failed: {sorted(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
