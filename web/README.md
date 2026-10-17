# HazeGuard web

React + Vite. Three tabs in `src/tabs/`, listed in `TABS` in `src/App.jsx` (`src/shell.css` is the shared design):

- `Map/`: Command map. DOE's measured API now and the CAMS PM2.5 forecast converted to API (`scales.js`), plus
  the Sep 2019 XGBoost replay.
- `Routing/`: Resource routing. The XGBoost ranker re-runs in the browser (`engine/`) for scenarios.
- `Ledger/`: Cross-border ledger. Damage estimate, smoke sources, AI decision brief.

Every tab reads JSON from `data/` (the repo's `data/out/`, written by the pipeline; see the root README).

```bash
cd web
npm ci
npm run dev        # http://localhost:5173, serves ../data/out/ live at /data/
npm run host       # the same, reachable from other machines on the network
npm run build      # static site in web/dist, with a copy of the data files in dist/data/
npm run preview    # serve dist/ locally to check the build
```

## Hosting

`dist/` is plain static files with relative paths, so it works from any static host or sub-folder.
Tabs use the URL hash, so no rewrite rules are needed.

- The build bundles the data files that exist at build time (list in `vite.config.js`). The pipeline refreshes
  them hourly (`pipeline/cron_equity.sh`), so rebuild to publish newer data, or serve with `npm run dev` /
  `npm run host`, which read `data/out/` live.
