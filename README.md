# HazeGuard-MY

- `data/raw/` — downloaded source data (not committed)
- `data/interim/` — cleaned data
- `data/mock/` — mock JSON outputs for frontend development
- `data/out/` — final JSON outputs from the pipeline
- `pipeline/` — numbered Python scripts: fetch, physical forecast, equity allocation, policy attribution + costs
- `config/` — configuration files
- `api/` — FastAPI backend
- `web/` — React (Vite) frontend; tabs live in `web/src/tabs/` (Map, Routing, Ledger)
- `docs/` — project documentation


## Haze prediction: weather and fire features

This part produces the daily weather and fire inputs for the haze models: one row per Malaysian state per day, from 1 Jan 2013 to 6 Oct 2019. The models forecast the Malaysian **Air Pollution Index (API)** 1, 2 and 3 days ahead. API targets and lags are built on the models side.

### Data sources

| Data | Source | Notes |
|---|---|---|
| Wind speed, wind direction, rain (hourly) | [Open-Meteo Historical Weather API](https://open-meteo.com/) | Free, no key. CC BY 4.0. |
| Fire hotspots | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/country/), VIIRS S-NPP 375 m, country yearly summaries | **Malaysia only** (team decision, see Limitations) |
| Air Pollution Index (target) | APIMS station data, averaged per state | Built by `data/aggregate_states.py` |

Each of the 16 states (KL, Putrajaya and Labuan counted separately) is represented by one point for weather and fire distance, usually the state capital. The coordinates are in `pipeline/physical/config.py`.

### How the features are built

1. **Hourly weather → daily**, using Malaysian local dates (UTC+8):
   - `wind_speed`: daily mean (km/h)
   - `wind_dir`: direction the wind blows *from*, averaged as a speed-weighted vector (a plain average of 350° and 10° would wrongly give 180°)
   - `rain_mm`: daily total
2. **Fire hotspots** are cleaned and matched to days:
   - low-confidence detections are dropped
   - detection times are converted from UTC to Malaysian local dates
   - each day uses fires from **that day and the day before**, since smoke takes time to travel
3. **Fire features**, per state per day:

| Feature | Meaning |
|---|---|
| `fire_pressure` | Σ (fire intensity × wind alignment ÷ distance). Alignment is 1 if the wind blows straight from the fire towards the state, and 0 if sideways or away. Distance has a 10 km floor. |
| `fire_pressure_lag1` | Yesterday's `fire_pressure` |
| `fire_pressure_3d`, `fire_pressure_7d` | Rolling sums over 3 and 7 days, since smoke builds up |
| `fires_100km`, `fires_300km` | Number of fires within that distance, ignoring wind |
| `frp_300km` | Total fire intensity (FRP, fire radiative power) within 300 km |

### What the features show

Spearman correlation with **next-day API**. Spearman suits fire data better than Pearson, because most days have almost no fire and a few have a lot.

| Feature | All states, 2013–2019 | Sep 2019 haze |
|---|---|---|
| `fire_pressure` | 0.31 | 0.23 |
| `fire_pressure_7d` | 0.32 | 0.31 |
| `fires_300km` | **0.39** | **0.40** |
| `frp_300km` | 0.37 | 0.35 |

- **Simple proximity beats the wind-weighted measure.** A state's own wind isn't a reliable guide to where smoke travels, while having fires nearby is.
- **The link is strongest in states with local burning** (Sarawak, Pahang) and weakest in urban states (Pulau Pinang, Putrajaya).
- **Local fires don't explain the 2019 crisis.** Early August 2019 had the most fires near KL and Pahang, but API stayed moderate. The September peak (KL ≈ 185) came while local fires were moderate. This points to cross-border smoke. See `figures/api_vs_fires_2019.png`.

### Limitations

- **Malaysia-only fire data.** Transboundary smoke from outside Malaysia is not represented, so models will tend to underpredict major haze peaks and react late to their onset. Adding regional fire data is the most obvious improvement.
- **One point per state.** Large states (Sarawak, Sabah, Pahang) are represented by a single location.
- **Seasonality overlap.** Fires and haze both peak in the dry season. Part of the correlation is seasonal, so models should include day of year or month, to show what fire features add on top of that.
- **Correlation is not causation.** These checks show association with next-day API, not proof that the fires caused it.

### Reproducing

Run from the `hazeguard/` folder:

```bash
python -m pipeline.physical.prep.fetch           # weather → data/raw/weather_hourly.csv
python -m pipeline.physical.prep.combine_firms   # FIRMS country files → data/raw/firms_hotspots.csv
python -m pipeline.physical.prep.features        # → data/processed/features.csv + correlation check
```

`combine_firms` expects the FIRMS VIIRS S-NPP yearly Malaysia files (2013–2019) in `data/raw/firms_country/`.

### Acknowledgements

We acknowledge the use of data from NASA's Fire Information for Resource Management System (FIRMS), part of NASA's Earth Observing System Data and Information System (EOSDIS). Weather data by Open-Meteo.com (CC BY 4.0).
