import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The pipeline writes its JSON to <repo>/data/out (states.json is re-ranked hourly by cron).
// `npm run dev` serves that folder live at /data/; a build copies the files into dist/data/ so the
// site can be hosted as plain static files (`npm run preview` serves that copy).
const DATA_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../data/out')
const DATA_FILES = ['states.json', 'ranker_web.json', 'damage.json', 'ledger.json', 'forecast.json', 'forecast_timeline.json', 'forecast_metrics.json', 'forecast_live.json', 'attribution.json', 'waqi_now.json']

function pipelineData() {
  const serve = (req, res, next) => {
    const url = (req.url || '').split('?')[0]
    if (!url.startsWith('/data/')) return next()
    const file = path.join(DATA_DIR, decodeURIComponent(url.slice('/data/'.length)))
    if (!file.startsWith(DATA_DIR + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.statusCode = 404
      return res.end('Not found')
    }
    res.setHeader('Content-Type', file.endsWith('.json') ? 'application/json; charset=utf-8' : 'application/octet-stream')
    res.setHeader('Cache-Control', 'no-store')
    fs.createReadStream(file).pipe(res)
  }
  return {
    name: 'pipeline-data',
    configureServer(server) { server.middlewares.use(serve) },
    generateBundle() {
      for (const name of DATA_FILES) {
        const file = path.join(DATA_DIR, name)
        // A missing file only breaks the tab that reads it, so warn rather than fail the whole build.
        if (!fs.existsSync(file)) { this.warn(`${file} is missing; the tab that reads it will show an error`); continue }
        this.emitFile({ type: 'asset', fileName: `data/${name}`, source: fs.readFileSync(file) })
      }
    },
  }
}

export default defineConfig({
  base: './', // relative URLs, so dist/ works from any host or sub-folder
  plugins: [react(), pipelineData()],
})
