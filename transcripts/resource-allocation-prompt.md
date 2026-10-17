# HazeGuard-MY — Resource Allocation (Equity Layer)

I'm on the RESOURCE ALLOCATION part of HazeGuard-MY, a 12-hour hackathon.

**Goal:** rank all Malaysian states by haze priority, so that resources (mobile clinics, N95s) go to the most vulnerable first.

**Order of work:**
- **Phase 1 is the backend** (data → EDA → XGBoost ranker → tests → `states.json`).
- **Phase 2 is the frontend**, and it only starts once Phase 1 has passed its tests.

**Files we own:**
- `pipeline/03_equity.py`
- `notebooks/03_equity_eda.ipynb`
- `data/resource_allocation/`
- `config/allocation.json`
- `tests/test_equity.py`
- `data/out/states.json`
- later: `web/src/tabs/Routing/`

One teammate works with me on the same branch, `resource_allocation`, using their own Claude Code. The rest of the team is on other branches.

---

## Step 0: Check the branch before writing any code

1. Run `git fetch origin`.
   - If `origin/resource_allocation` exists, check it out and pull.
   - If it doesn't, create it from `main` and run `git push -u origin resource_allocation`.
2. Run `git log --oneline -15` and `git status`, then read whatever already exists in the files listed above.
3. From the commits, work out which Phase 1 steps (1–5 below) are done or in progress, and pick up the next one that hasn't been started. If you can't tell, ask me.
4. Report back in 5 lines or fewer, then wait for my OK.

---

## Output format: `data/out/states.json`

```json
{
  "states": [{
    "state": "Selangor", "lat": 3.07, "lon": 101.52, "population": 7000000,
    "income_median": 0, "income_mean": 0, "poverty_rate": 0, "beds_per_1000": 0,
    "pm25": {"24": 0, "48": 0, "72": 0},
    "risk_score": 0.0, "priority": 1,
    "allocation": {"mobile_clinic": true, "n95": 0},
    "action": "Deploy mobile clinic + distribute N95s"
  }],
  "stock": {"mobile_clinics": 6, "n95": 300000},
  "model": {"type": "XGBRanker", "ndcg@5": 0.0, "baseline_ndcg@5": 0.0},
  "assumptions": [{"name": "", "value": "", "unit": "", "source": ""}]
}
```

Once this file has been pushed, never rename its fields. Adding new fields is fine, but say so in the commit message.

---

## PHASE 1: Backend

### 1. Collect data (data.gov.my / OpenDOSM, CC BY 4.0)

Load each dataset with `pandas.read_parquet` and save a raw copy to `data/resource_allocation/raw/`.

| Saved as | Data | Source |
|---|---|---|
| `population_state.parquet` | Population and age by state | `https://storage.dosm.gov.my/population/population_state.parquet` |
| `hies_state.parquet` | Household income and poverty (HIES) | `https://storage.dosm.gov.my/hies/hies_state.parquet` |
| `household_income.csv` | **Median and mean household income** by state, all available years | From `hies_state`; if a column is missing, find the household income dataset in the data.gov.my catalogue |
| `hospital_beds.parquet` | Hospital availability (beds, hospitals, or health staff by state) | Search the data.gov.my catalogue for "hospital beds" |

Notes on the data:
- Population figures are in **thousands**, so multiply them by 1000.
- Filter `sex`, `age` and `ethnicity` to their overall-total values.
- `household_income.csv` must have these columns: `state, year, income_median, income_mean`.
- If a URL fails, find the correct one in the catalogue rather than guessing.
- Record every dataset ID and year so they can go into `assumptions`.

**Commit:** `model: raw datasets`

### 2. EDA and preprocessing (`notebooks/03_equity_eda.ipynb`)

- Merge everything into one table, with one row per state per year, using the years that overlap.
- Make the state names match across all tables, and assert that all 16 are present (13 states + 3 Federal Territories).
- Check for missing values, outliers and correlations, and look at each feature's distribution.
  - Compare median vs mean income. A large gap between them signals inequality, so add `income_gap = mean − median` as a feature.
  - Keep only the plots that tell us something.
- Build these features:
  - `income_median`, `income_mean`, `income_gap`
  - `poverty_rate`
  - `beds_per_1000`
  - `pct_65plus`, `pct_under5`
  - `density` (hard-coding state area is fine)
  - `pm25` exposure from `forecast.json` (`data/out/` or `data/mock/` on main). If that doesn't exist yet, use historical unhealthy-air days per state and note it in `assumptions`.
- Scale the features and save the result to `data/resource_allocation/features.csv`.

**Commit:** `model: EDA + features`

### 3. Train an XGBoost ranker (`pipeline/03_equity.py`)

- Use `xgboost.XGBRanker` with `objective="rank:pairwise"`.
  - Each **group** is one year (or one haze scenario).
  - Each row is one state.
- **Labels:** there is no ground-truth priority list, so build a proxy relevance grade from 0 to 3, using quantiles of a harm proxy.
  - Preferred proxy: historical unhealthy-air days × vulnerable population (65+, under-5, and poor households).
  - Fallback proxy: a weighted index (low median income 0.4, 65+ 0.2, under-5 0.2, low beds per 1000 0.2).
  - Write down which proxy you used in `assumptions`, and say clearly that it is a proxy.
- **Baseline:** rank the states using the weighted index alone. The ranker has to beat it.
- The dataset is small, so use leave-one-year-out validation and keep the trees shallow (`max_depth` 2–3).
- Save the model to `data/resource_allocation/ranker.json`.

**Commit:** `model: XGBoost ranker`

### 4. Allocate and export

- `priority` is the ranker's order, and `risk_score` is its score rescaled to 0–1.
- Allocate from a fixed stock (defaults: 6 mobile clinics and 300,000 N95s).
  - **Clinics:** one each to the top states in priority order.
  - **N95s:** split in proportion to vulnerable population, most urgent state first, until the stock runs out.
- Set the action from priority:

  | Priority | Action |
  |---|---|
  | Top 5 | "Deploy mobile clinic + distribute N95s" |
  | 6–10 | "Pre-position N95s, issue advisory" |
  | Rest | "Monitor" |

- Put the stock, weights and label method in `config/allocation.json`, and copy every one of them into `assumptions`, with a source for each.
- Write the output to `data/out/states.json`.

**Commit:** `model: states.json export`

### 5. Test (`tests/test_equity.py`, run with pytest)

The tests should check that:
- all 16 states are present, with no NaNs in the features;
- `household_income.csv` has both `income_median` and `income_mean` for every state;
- priorities are unique and run from 1 to 16;
- allocations never exceed the stock;
- the output matches the format above;
- the NDCG@5 values for both the ranker and the baseline are filled in.

Then sanity-check the top 5 states by eye, and report the NDCG results to me honestly, including if the ranker doesn't beat the baseline.

**Commit:** `model: tests passing`, then tell me Phase 1 is done.

---

## PHASE 2: Frontend (`web/src/tabs/Routing/`), only after Phase 1 passes

Build the tab against `data/out/states.json`.

- **Priority table:** sortable, with red, amber or green rows by action. Columns are priority, state, risk score, median income, poverty rate, beds per 1000, N95s, mobile clinic, and action.
- **Time window:** follow the app's 24/48/72h slider if the shell has one. If it doesn't, add a local selector that's easy to swap out later.
- **Summary cards:**
  - clinics used vs stock
  - N95s used vs stock
  - ranker NDCG vs baseline
- **"Assumptions" expander:** list every assumption, including the OpenDOSM / data.gov.my credit.
- Keep all code inside `web/src/tabs/Routing/`. If something outside it needs to change, tell me instead of editing it.
- Prefix these commits with `ui:`.

---

## Sharing the branch

- Run `git pull --rebase origin resource_allocation` before each step and before every push.
- Commit small and push at least every 30–45 minutes. Never force-push.
- Start commit messages with `model:` or `ui:` so my teammate's Claude Code can follow `git log`.
- If a rebase hits a conflict in a file you didn't write, stop and tell me. Don't resolve it yourself.
- Don't merge into `main`.

After each push, tell me in two lines what landed and what you're picking up next.
