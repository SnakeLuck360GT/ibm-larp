# HazeGuard web

React + Vite. Only the Resource routing tab (`src/tabs/Routing/`) is built so far; add the Map and
Ledger tabs to `TABS` in `src/App.jsx` as they're ported from the team design (`src/shell.css` is
that design's stylesheet).

```bash
cd web
npm install
npm run dev        # http://localhost:5173, reads ../data/out/states.json live
npm run build      # static site in web/dist, with a copy of states.json in dist/data/
npm run preview    # serve dist/ locally to check the build
```

## Hosting

`dist/` is plain static files with relative paths, so it works from any static host or sub-folder
(Netlify, Vercel, GitHub Pages, S3). Tabs use the URL hash, so no rewrite rules are needed.

- Netlify / Vercel: base directory `web`, build command `npm run build`, publish directory `dist`.
- The build bundles the `states.json` that exists at build time. The pipeline re-ranks hourly
  (`pipeline/cron_equity.sh`), so rebuild and redeploy to publish a newer ranking. The tab shows
  when it was ranked and flags rankings older than 6 hours.
