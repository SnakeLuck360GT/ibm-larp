# HazeGuard-MY

Real-time haze decision support for Malaysia: when the haze arrives, which states need help first, and what it
costs. A Python pipeline pulls live air-quality data every hour, re-runs the models and writes JSON; a React app
shows it in three tabs (Command map, Resource routing, Cross-border ledger).

## Layout

```
config/          allocation.json: clinic / N95 stock, ranker weights, label method
pipeline/        every runnable script (below) + cron_equity.sh, the hourly / daily job
hazeguard/       physical haze model: XGBoost API forecaster vs ARIMA, Sep 2019 replay (own README-style docs
                 in hazeguard/data/processed/DATA.md; run from inside hazeguard/)
data/
  out/                   JSON the web app reads (written by the pipeline)
  resource_allocation/   OpenDOSM / data.gov.my downloads (raw/), features.csv, saved ranker model
  mock/damage/           inputs for the damage model
  raw/                   download cache (gitignored), e.g. attribution weather and fire files
notebooks/       exploration and checks (equity EDA, damage model tests)
tests/           pytest
docs/            run notes, design prompts
web/             React (Vite) app; tabs in web/src/tabs/ (Map, Routing, Ledger)
```

## Pipeline

| Script | Writes (data/out/ unless noted) | When |
|---|---|---|
| `pipeline/apims_now.py` | `apims_now.json`: official DOE API, every station, highest per state | hourly |
| `pipeline/waqi_now.py` | `waqi_now.json`: nearest WAQI station PM2.5 per state | hourly |
| `pipeline/03_equity.py --refresh` | `states.json`, `ranker_web.json`, `forecast_live.json`: CAMS forecast + re-rank | hourly |
| `pipeline/ledger_brief.py` | `ledger_brief.json`: AI decision brief (LLM on Groq, numbers checked against the data) | hourly |
| `pipeline/fetch_raw.py` | `data/resource_allocation/raw/`: OpenDOSM + PM2.5 history | daily |
| `pipeline/build_features.py` | `data/resource_allocation/features.csv` | daily |
| `pipeline/03_equity.py` | retrains the ranker, then as above | daily |
| `pipeline/04_policy.py` | `damage.json`: damage so far + next 72 h | on demand |
| `pipeline/build_attribution.py` | `attribution.json`: Sep 2019 smoke sources by province | once |
| `pipeline/damage_data.py` | `data/mock/damage/` CSVs (create / view / summarise) | on demand |
| `hazeguard/` `python -m pipeline.physical.models.export_forecast` | `forecast.json`, `forecast_timeline.json` in `hazeguard/data/out/`; copy to `data/out/` | on demand |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # requirements-dev.txt adds pytest + notebook tools
cp .env.example .env                             # then fill in WAQI_TOKEN and GROQ_API_KEY
```

Run any script from the repo root, e.g. `.venv/bin/python pipeline/apims_now.py`.

**Scheduled job** (`crontab -e`, with your path):

```
5 * * * *  /path/to/ibm-larp/pipeline/cron_equity.sh refresh
20 3 * * * /path/to/ibm-larp/pipeline/cron_equity.sh full
```

Logs go to `~/.local/state/hazeguard/equity.log` on Linux (`~/Library/Logs/hazeguard-equity.log` on macOS).

**Web app:** see [web/README.md](web/README.md). `cd web && npm ci && npm run dev` (or `npm run host` to serve on
the network) reads `data/out/` live.

**Tests:** `.venv/bin/python -m pytest tests`
