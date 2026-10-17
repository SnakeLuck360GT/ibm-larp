"""Latest official Air Pollutant Index (API) per state from DOE Malaysia's APIMS (eqms.doe.gov.my).

Run from the repo root:  .venv/bin/python pipeline/apims_now.py
No token needed. Writes data/out/apims_now.json for the Command map.

The feed is the one DOE's own public map (https://eqms.doe.gov.my/APIMS/main) loads: the latest hourly API at
every continuous air-quality monitoring station. It is not a documented public API, so it may change without notice.
Each state gets its highest station reading (what DOE's own rankings and haze warnings use), plus every station.
"""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "out" / "apims_now.json"
FEED = "https://eqms.doe.gov.my/api3/publicportalapims/mapcaqm"
PORTAL = "https://eqms.doe.gov.my/APIMS/main"
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")


def state_name(s: str) -> str:
    # DOE writes "WP Kuala Lumpur"; states.json uses "W.P. Kuala Lumpur".
    return "W.P. " + s[3:] if s.startswith("WP ") else s


def main() -> None:
    rows = requests.get(FEED, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).json()["data"]
    by_state: dict[str, list] = {}
    for r in rows:
        api = r.get("API")
        if r.get("STATION_STATUS") != 1 or not isinstance(api, (int, float)) or api < 0:
            continue  # offline station or no reading this hour
        loc = r["STATION_LOCATION"].split(",")[0].strip()
        by_state.setdefault(state_name(r["STATE_NAME"]), []).append({
            "id": r["STATION_ID"], "location": loc.title() if loc.isupper() else loc,
            "api": int(api), "class": r.get("CLASS"), "symbol": r.get("PARAM_SYMBOL"),
            "time": datetime.fromisoformat(r["RECORD_DT"]).replace(tzinfo=KL_TZ).isoformat(),
            "geo": [r.get("LATITUDE"), r.get("LONGITUDE")],
        })
    if not by_state:
        raise SystemExit("APIMS: no station readings")
    states = {}
    for name, sts in sorted(by_state.items()):
        sts.sort(key=lambda s: -s["api"])
        top = sts[0]
        states[name] = {"api": top["api"], "class": top["class"], "station": top["location"], "time": top["time"],
                        "n_stations": len(sts), "stations": sts}
    out = {
        "fetched_at": datetime.now(KL_TZ).isoformat(timespec="seconds"),
        "source": "Department of Environment Malaysia (DOE), Air Pollutant Index Management System (APIMS)",
        "url": PORTAL,
        "rule": "state value = highest station API this hour",
        "states": states,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(OUT)
    print(f"apims_now.json: {len(states)} states, {sum(s['n_stations'] for s in states.values())} stations")


if __name__ == "__main__":
    main()
