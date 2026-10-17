# Ranker run — 2026-10-17

The XGBoost ranker now ranks **2026**, re-ranks hourly on the live PM2.5 forecast, and uses fresher OpenDOSM financial data. All 20 tests pass.

## How it runs

| Step | Command | When |
|---|---|---|
| Download OpenDOSM + PM2.5 history | `pipeline/fetch_raw.py` | daily 03:20 (cron `full`) |
| Build state × year features, 2017–2026 | `pipeline/build_features.py` | daily 03:20 |
| Retrain on 2017–2025, rank 2026 | `pipeline/03_equity.py` | daily 03:20 |
| Re-rank 2026 on the latest forecast (saved model) | `pipeline/03_equity.py --refresh` | hourly at :05 (cron `refresh`) |

Wrapper: `pipeline/cron_equity.sh [refresh|full]`. Log: `~/Library/Logs/hazeguard-equity.log`. No API keys needed.

## What changed in the model

- **Training years 2017–2025, 2026 ranked only.** 2026 has partial data, so the model never trains on it.
- **New OpenDOSM financial features:** state unemployment (labour force survey, through 2026 Q2), household Gini (HIES), real GDP per head (through 2025). Income carried past the last HIES (2024) is scaled by state CPI (through Aug 2026).
- **Gaps between surveys are filled and flagged** (`*_source` columns in `features.csv`):

  | Year | Income / poverty / Gini | Beds | GDP | Unemployment | PM2.5 |
  |---|---|---|---|---|---|
  | 2017–18, 2021, 2023 | interpolated | observed | observed* | observed | climatology (2017–22) / observed (2023) |
  | 2019, 2020, 2022, 2024 | observed | observed | observed* | observed | climatology / observed (2024) |
  | 2025 | carried + CPI | carried | observed | observed | observed |
  | **2026** | **carried + CPI** | **carried** | **carried** | **Q1–Q2** | **year to date** |

  \* Putrajaya GDP is only published from 2023; earlier years are back-filled at its 2023 GDP per head.
- **3-day PM2.5 episodes added to training.** Every non-overlapping 3-day window of observed PM2.5 since Aug 2022 is its own ranking group (415 groups plus 9 yearly groups). Without them the model ignored a forecast spike: tripling Kelantan's PM2.5 left it at #8. With them it moves to #5.
- **16 label grades instead of 4** (`config/allocation.json` → `label.grades`). With quartiles, the top four states tied every year, so the model never learned their order.

## Validation (leave-one-year-out NDCG@5)

| Year | Ranker | Baseline |
|---|--:|--:|
| 2017 | 0.976 | 0.529 |
| 2018 | 0.987 | 0.537 |
| 2019 | 0.997 | 0.543 |
| 2020 | 0.963 | 0.746 |
| 2021 | 0.963 | 0.714 |
| 2022 | 0.987 | 0.694 |
| 2023 | 0.993 | 0.633 |
| 2024 | 0.992 | 0.721 |
| 2025 | 0.992 | 0.593 |
| **Mean** | **0.983** | **0.634** |

The label is a proxy (PM2.5 × vulnerable people), and the ranker is scored against that same proxy. The high score partly shows it learned the label definition.

## 2026 priority, forecast issued 2026-10-17 19:00 MYT

Allocation ranks the whole 0–72 h forecast. `24/48/72` are the priorities for each 24 h window. `YTD` ranks on 2026 year-to-date PM2.5, i.e. what the ranking would be without the forecast.

| Priority | State | PM2.5 0–72 h | Risk | 24 | 48 | 72 | YTD | Clinic | N95 | Action |
|--:|---|--:|--:|--:|--:|--:|--:|:-:|--:|---|
| 1 | Selangor | 50.8 | 1.000 | 1 | 1 | 1 | 1 | ✓ | 46,280 | Deploy mobile clinic + distribute N95s |
| 2 | Johor | 53.3 | 0.856 | 2 | 2 | 2 | 3 | ✓ | 30,127 | Deploy mobile clinic + distribute N95s |
| 3 | Sabah | 7.1 | 0.807 | 3 | 3 | 3 | 2 | ✓ | 68,602 | Deploy mobile clinic + distribute N95s |
| 4 | Perak | 24.2 | 0.760 | 4 | 4 | 4 | 5 | ✓ | 29,155 | Deploy mobile clinic + distribute N95s |
| 5 | W.P. Kuala Lumpur | 50.8 | 0.712 | 5 | 5 | 5 | 6 | ✓ | 11,095 | Deploy mobile clinic + distribute N95s |
| 6 | Sarawak | 11.8 | 0.611 | 6 | 6 | 7 | 4 | ✓ | 30,986 | Pre-position N95s, issue advisory |
| 7 | Kedah | 12.8 | 0.609 | 7 | 7 | 6 | 7 | | 28,289 | Pre-position N95s, issue advisory |
| 8 | Kelantan | 8.6 | 0.584 | 8 | 8 | 8 | 8 | | 28,433 | Pre-position N95s, issue advisory |
| 9 | Negeri Sembilan | 32.3 | 0.518 | 9 | 9 | 9 | 9 | | 10,664 | Pre-position N95s, issue advisory |
| 10 | Pahang | 14.9 | 0.496 | 10 | 10 | 11 | 11 | | 16,369 | Pre-position N95s, issue advisory |
| 11 | Pulau Pinang | 17.5 | 0.462 | 11 | 11 | 10 | 10 | | 0 | Monitor |
| 12 | Melaka | 29.9 | 0.433 | 12 | 12 | 13 | 13 | | 0 | Monitor |
| 13 | Terengganu | 7.7 | 0.394 | 13 | 13 | 12 | 12 | | 0 | Monitor |
| 14 | Perlis | 3.8 | 0.202 | 14 | 14 | 14 | 14 | | 0 | Monitor |
| 15 | W.P. Putrajaya | 49.2 | 0.169 | 15 | 15 | 15 | 15 | | 0 | Monitor |
| 16 | W.P. Labuan | 5.8 | 0.000 | 16 | 16 | 16 | 16 | | 0 | Monitor |

## Known limits

- **Small states rank low even under heavy haze** (Putrajaya: 49 µg/m³, 121k people, #15). The label counts vulnerable *people*, so headcount dominates. A per-person risk label would change this; it's a team decision, not a bug.
- **Sabah stays high at 7 µg/m³** because it has the highest poverty rate (17.7%) and 3.8M people.
- **2026 socioeconomics are estimates** (HIES 2026 results are due around 2027). The current-year rows are flagged in `features.csv` and in the `states.json` assumptions.
- **Notebook `03_equity_eda.ipynb` still writes the old `features.csv` in its last cell.** Running it would overwrite the new file and break the pipeline. Rebuild with `build_features.py` instead.
- **On 1 Jan 2027 the daily rebuild will fail** until DOSM publishes 2027 population. The hourly refresh keeps working on the 2026 rows.
- XGBoost needs OpenMP on macOS. The cron wrapper borrows scikit-learn's bundled `libomp`; `brew install libomp` is the permanent fix.
