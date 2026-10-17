"""Transboundary haze source attribution, Sep 2019 haze -> data/out/attribution.json

Run from the repo root:  .venv/bin/python pipeline/build_attribution.py
Re-runnable; every download is cached under data/raw/attribution/ (gitignored).

Method (2-D kinematic back-trajectories x fire hotspots):
  1. Wind: Open-Meteo Historical Weather API (ERA5 reanalysis), hourly wind_speed_100m / wind_direction_100m
     (100 m above ground; the archive has no 850 hPa level), sampled on a 1 degree grid over 94-122E, 7S-10N,
     2019-08-28 .. 2019-10-03 UTC. u/v are interpolated bilinearly in space and linearly in time.
  2. Back-trajectories: from each of the 16 Malaysian state capitals (data/resource_allocation/state_ref.csv),
     arrivals at 00/06/12/18 UTC every day 2019-09-01 .. 2019-10-03, stepped back 72 h in 1 h steps
     (Heun predictor-corrector). Meteorological direction = where the wind blows FROM, so the air moves toward
     direction+180; going back in time we move against that vector. Simplifications: one level, 2-D, no vertical
     motion, no dispersion/deposition, no chemistry; a path stops if it leaves the wind grid.
  3. Fires: NASA FIRMS VIIRS S-NPP 375 m yearly country archives 2019 (Indonesia, Malaysia, Brunei),
     2019-08-29 .. 2019-10-03, vegetation fires only (type 0), low-confidence detections dropped.
  4. Attribution: a hotspot counts for a trajectory if it lies within 25 km of a hourly trajectory point and was
     detected within +/-3 h of the parcel being there. Each hotspot counts once per trajectory, weighted by its FRP (MW).
     Indonesian hotspots are assigned to a province by point-in-polygon (geoBoundaries IDN ADM1, ODbL);
     Malaysian ones go to "Malaysia (domestic)", Brunei's to "Brunei". Shares = matched FRP / all matched FRP,
     per state and nationally (national = all 16 states' trajectories summed, each state weighted equally).
"""
import csv
import ssl
import json
import math
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import Point, shape, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data' / 'raw' / 'attribution'
OUT = ROOT / 'data' / 'out' / 'attribution.json'
STATE_REF = ROOT / 'data' / 'resource_allocation' / 'state_ref.csv'

EVENT = ('2019-09-01', '2019-10-03')
REPLAY_DAY = '2019-09-09'
BACK_H = 72
ARRIVALS_UTC = (0, 6, 12, 18)
SHOW_ARRIVAL_UTC = 6            # 14:00 MYT path is the one stored for the map
RADIUS_KM = 25.0
WINDOW_H = 3
LON0, LON1, LAT0, LAT1, STEP = 94, 122, -7, 10, 1.0
WIND_START, WIND_END = '2019-08-28', '2019-10-03'
FIRE_START = '2019-08-29'

FIRMS_URL = 'https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2019/viirs-snpp_2019_{c}.csv'
GB_URL = 'https://github.com/wmgeolab/geoBoundaries/raw/main/releaseData/gbOpen/{iso}/{lvl}/geoBoundaries-{iso}-{lvl}_simplified.geojson'
OM_URL = 'https://archive-api.open-meteo.com/v1/archive'

# map extent for the stored basemap / projection  x=(lon-MAP_L0)*MAP_K, y=(MAP_A1-lat)*MAP_K
MAP_L0, MAP_L1, MAP_A0, MAP_A1, MAP_K = 95.0, 119.5, -6.0, 7.8, 30


def fetch(url, dest, tries=6):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'HazeGuard-MY research (datathon)'})
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
                data = r.read()
            dest.write_bytes(data)
            return dest
        except Exception as e:  # rate limits (429) and transient errors: back off
            wait = 20 * (i + 1)
            print(f'  fetch failed ({e}); retry in {wait}s')
            time.sleep(wait)
    raise RuntimeError(f'could not download {url}')


# ---------------------------------------------------------------- wind grid
def load_wind():
    lons = np.arange(LON0, LON1 + 1e-9, STEP)
    lats = np.arange(LAT0, LAT1 + 1e-9, STEP)
    pts = [(la, lo) for la in lats for lo in lons]
    batch = 40
    cache = RAW / 'wind'
    series = []
    for b in range(0, len(pts), batch):
        chunk = pts[b:b + batch]
        q = (f'{OM_URL}?latitude={",".join(f"{p[0]:g}" for p in chunk)}&longitude={",".join(f"{p[1]:g}" for p in chunk)}'
             f'&start_date={WIND_START}&end_date={WIND_END}&hourly=wind_speed_100m,wind_direction_100m'
             f'&wind_speed_unit=ms&timezone=GMT')
        f = cache / f'om_100m_{WIND_START}_{WIND_END}_{STEP:g}deg_{b:04d}.json'
        was = f.exists()
        fetch(q, f)
        if not was:
            print(f'  wind batch {b // batch + 1}/{math.ceil(len(pts) / batch)}')
            time.sleep(12)
        j = json.loads(f.read_text())
        series.extend(j if isinstance(j, list) else [j])
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in series[0]['hourly']['time']]
    nt = len(times)
    U = np.zeros((nt, len(lats), len(lons)), dtype=np.float32)
    V = np.zeros_like(U)
    for idx, s in enumerate(series):
        i, k = divmod(idx, len(lons))
        ws = np.array([np.nan if x is None else x for x in s['hourly']['wind_speed_100m']], dtype=float)
        wd = np.radians(np.array([np.nan if x is None else x for x in s['hourly']['wind_direction_100m']], dtype=float))
        # meteorological convention: direction the wind blows FROM
        U[:, i, k] = np.nan_to_num(-ws * np.sin(wd))
        V[:, i, k] = np.nan_to_num(-ws * np.cos(wd))
    return lons, lats, times, U, V


class Wind:
    def __init__(self, lons, lats, times, U, V):
        self.lons, self.lats, self.U, self.V = lons, lats, U, V
        self.t0 = times[0]
        self.nt = len(times)

    def inside(self, lat, lon):
        return LON0 <= lon <= LON1 and LAT0 <= lat <= LAT1

    def at(self, lat, lon, t):
        h = (t - self.t0).total_seconds() / 3600.0
        h = min(max(h, 0), self.nt - 1)
        h0 = int(math.floor(h)); h1 = min(h0 + 1, self.nt - 1); fh = h - h0
        x = (lon - LON0) / STEP; y = (lat - LAT0) / STEP
        x0 = int(min(max(math.floor(x), 0), len(self.lons) - 2)); y0 = int(min(max(math.floor(y), 0), len(self.lats) - 2))
        fx = x - x0; fy = y - y0

        def bil(A, hh):
            return ((1 - fx) * (1 - fy) * A[hh, y0, x0] + fx * (1 - fy) * A[hh, y0, x0 + 1]
                    + (1 - fx) * fy * A[hh, y0 + 1, x0] + fx * fy * A[hh, y0 + 1, x0 + 1])
        u = (1 - fh) * bil(self.U, h0) + fh * bil(self.U, h1)
        v = (1 - fh) * bil(self.V, h0) + fh * bil(self.V, h1)
        return float(u), float(v)


def back_trajectory(w, lat, lon, t_arr):
    """Hourly points [(t, lat, lon)] from arrival back BACK_H hours (stops if the path leaves the grid)."""
    dt = 3600.0
    pts = [(t_arr, lat, lon)]
    t = t_arr
    for _ in range(BACK_H):
        u1, v1 = w.at(lat, lon, t)
        la1 = lat - v1 * dt / 111320.0
        lo1 = lon - u1 * dt / (111320.0 * math.cos(math.radians(lat)))
        t2 = t - timedelta(hours=1)
        if not w.inside(la1, lo1):
            break
        u2, v2 = w.at(la1, lo1, t2)
        u, v = (u1 + u2) / 2, (v1 + v2) / 2
        lat_n = lat - v * dt / 111320.0
        lon_n = lon - u * dt / (111320.0 * math.cos(math.radians(lat)))
        if not w.inside(lat_n, lon_n):
            break
        lat, lon, t = lat_n, lon_n, t2
        pts.append((t, lat, lon))
    return pts


# ---------------------------------------------------------------- boundaries and fires
def load_bounds():
    for iso, lvl in [('IDN', 'ADM1'), ('MYS', 'ADM0'), ('BRN', 'ADM0'), ('SGP', 'ADM0'), ('THA', 'ADM0'), ('PHL', 'ADM0'), ('TLS', 'ADM0')]:
        fetch(GB_URL.format(iso=iso, lvl=lvl), RAW / 'bounds' / f'geoBoundaries-{iso}-{lvl}_simplified.geojson')
    idn = json.loads((RAW / 'bounds' / 'geoBoundaries-IDN-ADM1_simplified.geojson').read_text())
    provs = [(f['properties']['shapeName'], shape(f['geometry'])) for f in idn['features']]
    return provs


def load_fires(provs):
    tree = STRtree([g for _, g in provs])
    rows = []
    t_lo = datetime.fromisoformat(FIRE_START).replace(tzinfo=timezone.utc)
    t_hi = datetime.fromisoformat(EVENT[1]).replace(tzinfo=timezone.utc) + timedelta(days=1)
    stats = {'kept': 0, 'dropped_type': 0, 'dropped_lowconf': 0}
    for country, label in [('Indonesia', None), ('Malaysia', 'Malaysia (domestic)'), ('Brunei_Darussalam', 'Brunei')]:
        f = fetch(FIRMS_URL.format(c=country), RAW / 'firms' / f'viirs-snpp_2019_{country}.csv')
        with open(f, newline='') as fh:
            for r in csv.DictReader(fh):
                d = r['acq_date']
                if d < FIRE_START or d > EVENT[1]:
                    continue
                t = datetime.fromisoformat(d).replace(tzinfo=timezone.utc) + timedelta(hours=int(r['acq_time'][:2]), minutes=int(r['acq_time'][2:]))
                if not (t_lo <= t < t_hi):
                    continue
                if r.get('type', '0') not in ('0', ''):
                    stats['dropped_type'] += 1
                    continue
                if r['confidence'] == 'l':
                    stats['dropped_lowconf'] += 1
                    continue
                rows.append([float(r['latitude']), float(r['longitude']), t, float(r['frp'] or 0), label])
    # province for Indonesian hotspots
    geoms = [g for _, g in provs]
    for r in rows:
        if r[4] is None:
            p = Point(r[1], r[0])
            hit = [i for i in tree.query(p) if geoms[i].contains(p)]
            i = hit[0] if hit else int(tree.nearest(p))  # coastal points just outside the simplified outline
            r[4] = provs[i][0]
    stats['kept'] = len(rows)
    return rows, stats


def hav_km(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1; dl = np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


# ---------------------------------------------------------------- basemap (projected, simplified)
def basemap(provs, shares):
    clip = box(MAP_L0 - 1, MAP_A0 - 1, MAP_L1 + 1, MAP_A1 + 1)

    def path(g, tol=0.03):
        g = g.intersection(clip)
        if g.is_empty:
            return ''
        g = g.simplify(tol, preserve_topology=True)
        polys = [g] if g.geom_type == 'Polygon' else [p for p in getattr(g, 'geoms', []) if p.geom_type == 'Polygon']
        out = []
        for p in polys:
            if p.area < 0.004:
                continue
            c = list(p.exterior.coords)
            out.append('M' + 'L'.join(f'{(lo - MAP_L0) * MAP_K:.1f},{(MAP_A1 - la) * MAP_K:.1f}' for lo, la in c) + 'Z')
        return ''.join(out)
    prov_paths = {}
    for name, g in provs:
        if g.intersects(clip):
            p = path(g)
            if p:
                prov_paths[name] = p
    other = ''
    for iso in ['BRN', 'SGP', 'THA', 'PHL', 'TLS']:
        j = json.loads((RAW / 'bounds' / f'geoBoundaries-{iso}-ADM0_simplified.geojson').read_text())
        for f in j['features']:
            other += path(shape(f['geometry']))
    j = json.loads((RAW / 'bounds' / 'geoBoundaries-MYS-ADM0_simplified.geojson').read_text())
    my = ''.join(path(shape(f['geometry'])) for f in j['features'])
    return {'L0': MAP_L0, 'A1': MAP_A1, 'K': MAP_K, 'w': round((MAP_L1 - MAP_L0) * MAP_K), 'h': round((MAP_A1 - MAP_A0) * MAP_K),
            'provinces': prov_paths, 'my': my, 'other': other}


# ---------------------------------------------------------------- main
def main():
    print('boundaries…'); provs = load_bounds()
    print('fires…'); fires, fstats = load_fires(provs)
    print(f'  {len(fires)} hotspots kept', fstats)
    print('wind…'); w = Wind(*load_wind())

    # index hotspots by (UTC hour bucket, 0.25 deg cell)
    F_lat = np.array([f[0] for f in fires]); F_lon = np.array([f[1] for f in fires]); F_frp = np.array([f[3] for f in fires])
    F_prov = [f[4] for f in fires]
    F_t = np.array([f[2].timestamp() for f in fires])
    idx = defaultdict(list)
    for i, f in enumerate(fires):
        hb = int(f[2].timestamp() // 3600)
        idx[(hb, int(math.floor(f[0] * 4)), int(math.floor(f[1] * 4)))].append(i)
    idx = {k: np.array(v) for k, v in idx.items()}

    states = list(csv.DictReader(open(STATE_REF)))
    d0 = datetime.fromisoformat(EVENT[0]).replace(tzinfo=timezone.utc)
    ndays = (datetime.fromisoformat(EVENT[1]).replace(tzinfo=timezone.utc) - d0).days + 1
    days = [(d0 + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(ndays)]

    frp_state = {s['state']: defaultdict(float) for s in states}
    hs_state = {s['state']: defaultdict(set) for s in states}
    frp_day = {d: defaultdict(float) for d in days}
    frp_state_day = {s['state']: {d: 0.0 for d in days} for s in states}
    cell_frp = {s['state']: defaultdict(float) for s in states}
    trajs = []
    n_traj = n_trunc = 0
    hours_total = 0
    for s in states:
        la0, lo0 = float(s['lat']), float(s['lon'])
        for d in days:
            for hr in ARRIVALS_UTC:
                t_arr = datetime.fromisoformat(d).replace(tzinfo=timezone.utc) + timedelta(hours=hr)
                pts = back_trajectory(w, la0, lo0, t_arr)
                n_traj += 1; hours_total += len(pts) - 1
                if len(pts) < BACK_H + 1:
                    n_trunc += 1
                matched = set()
                for (t, la, lo) in pts:
                    hb = int(t.timestamp() // 3600)
                    cy, cx = int(math.floor(la * 4)), int(math.floor(lo * 4))
                    cand = [idx[k] for hh in range(hb - WINDOW_H - 1, hb + WINDOW_H + 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                            if (k := (hh, cy + dy, cx + dx)) in idx]
                    if not cand:
                        continue
                    c = np.concatenate(cand)
                    ok = (np.abs(F_t[c] - t.timestamp()) <= WINDOW_H * 3600) & (hav_km(la, lo, F_lat[c], F_lon[c]) <= RADIUS_KM)
                    matched.update(c[ok].tolist())
                for i in matched:
                    p = F_prov[i]
                    frp_state[s['state']][p] += F_frp[i]
                    hs_state[s['state']][p].add(i)
                    frp_day[d][p] += F_frp[i]
                    frp_state_day[s['state']][d] += F_frp[i]
                    cell_frp[s['state']][(round(math.floor(F_lat[i] * 4) / 4 + 0.125, 3), round(math.floor(F_lon[i] * 4) / 4 + 0.125, 3))] += F_frp[i]
                if hr == SHOW_ARRIVAL_UTC:
                    trajs.append({'state': s['state'], 'day': d,
                                  'path': [[round(la, 2), round(lo, 2)] for (_, la, lo) in pts[::3]]})
        print(f'  {s["state"]}: {sum(frp_state[s["state"]].values()):.0f} MW matched')

    def shares(dd, hs=None):
        tot = sum(dd.values())
        rows = sorted(dd.items(), key=lambda kv: -kv[1])
        out = []
        for p, v in rows:
            r = {'province': p, 'share': round(v / tot, 4) if tot else 0, 'frp': round(v, 1)}
            if hs is not None:
                r['hotspots'] = len(hs[p])
            out.append(r)
        return out

    nat = defaultdict(float); nat_hs = defaultdict(set)
    for s in states:
        for p, v in frp_state[s['state']].items():
            nat[p] += v; nat_hs[p] |= hs_state[s['state']][p]
    sources = shares(nat, nat_hs)
    mean = defaultdict(float)
    for s in states:
        tot = sum(frp_state[s['state']].values())
        for p, v in frp_state[s['state']].items():
            mean[p] += v / tot / len(states) if tot else 0
    sources_state_mean = [{'province': p, 'share': round(v, 4)} for p, v in sorted(mean.items(), key=lambda kv: -kv[1])]
    by_state = {s['state']: [dict(r) for r in shares(frp_state[s['state']], hs_state[s['state']])] for s in states}
    daily = []
    for d in days:
        sh = shares(frp_day[d])
        daily.append({'date': d, 'frp': round(sum(frp_day[d].values()), 1), 'top': [{'province': r['province'], 'share': r['share']} for r in sh[:5]]})
    state_daily = {s['state']: [round(frp_state_day[s['state']][d], 1) for d in days] for s in states}
    cells = {k: [[la, lo, round(v, 1)] for (la, lo), v in sorted(c.items(), key=lambda kv: -kv[1])[:80]] for k, c in cell_frp.items()}
    nat_cells = defaultdict(float)
    for c in cell_frp.values():
        for k, v in c.items():
            nat_cells[k] += v
    nat_cells = [[la, lo, round(v, 1)] for (la, lo), v in sorted(nat_cells.items(), key=lambda kv: -kv[1])[:250]]

    # replay-day breakdown
    rp = frp_day[REPLAY_DAY]
    replay = {'date': REPLAY_DAY, 'frp': round(sum(rp.values()), 1),
              'sources': [{'province': r['province'], 'share': r['share']} for r in shares(rp)[:8]]}

    out = {
        'event': {'name': 'Sep 2019 haze', 'start': EVENT[0], 'end': EVENT[1], 'replay_day': REPLAY_DAY},
        'method': ('72 h 2-D back-trajectories from each state capital (arrivals 00/06/12/18 UTC daily), stepped hourly '
                   'through ERA5 100 m winds; VIIRS hotspots within 25 km and ±3 h of the path counted once per trajectory, '
                   'weighted by fire radiative power (FRP); hotspots assigned to Indonesian provinces by point-in-polygon.'),
        'wind_level': '100 m above ground (ERA5 via Open-Meteo archive; no 850 hPa in the archive)',
        'params': {'back_hours': BACK_H, 'step_h': 1, 'arrivals_utc': list(ARRIVALS_UTC), 'radius_km': RADIUS_KM, 'window_h': WINDOW_H,
                   'wind_grid_deg': STEP, 'wind_grid_bbox': [LON0, LAT0, LON1, LAT1], 'stored_path_arrival_utc': SHOW_ARRIVAL_UTC,
                   'stored_path_every_h': 3, 'national_weighting': 'Matched FRP summed over all 16 states, each state equal (the file also holds the plain mean of the 16 state shares)'},
        'stats': {'trajectories': n_traj, 'truncated_left_grid': n_trunc, 'hotspots_in_window': len(fires),
                  'hotspots_matched': len(set().union(*nat_hs.values())) if nat_hs else 0,
                  'frp_matched_mw': round(sum(nat.values()), 1), **{f'fires_{k}': v for k, v in fstats.items()}},
        'sources': sources,
        'sources_state_mean': sources_state_mean,
        'by_state': by_state,
        'state_daily_frp': {'dates': days, 'by_state': state_daily},
        'daily': daily,
        'replay': replay,
        'trajectories': trajs,
        'cells': {'deg': 0.25, 'national': nat_cells, 'by_state': cells},
        'basemap': basemap(provs, sources),
        'data_sources': [
            {'name': 'NASA FIRMS VIIRS S-NPP 375 m active fires, 2019 country archives (Indonesia, Malaysia, Brunei)',
             'url': 'https://firms.modaps.eosdis.nasa.gov/data/country/', 'licence': 'NASA open data (no restrictions; cite FIRMS)'},
            {'name': 'Open-Meteo Historical Weather API (ERA5 reanalysis), hourly 100 m wind',
             'url': 'https://archive-api.open-meteo.com/v1/archive', 'licence': 'CC BY 4.0 (Open-Meteo); ERA5: Copernicus C3S'},
            {'name': 'geoBoundaries gbOpen IDN ADM1 (Indonesian provinces, 2017; OSM/Wambacher) and ADM0 outlines',
             'url': 'https://www.geoboundaries.org/', 'licence': 'ODbL 1.0 (© OpenStreetMap contributors)'},
            {'name': 'State capital coordinates', 'url': 'data/resource_allocation/state_ref.csv', 'licence': 'project file'},
        ],
        'caveats': [
            'Single-level 2-D trajectories at 100 m: no vertical motion, dispersion, deposition or chemistry.',
            'Wind sampled on a 1° grid and interpolated; paths over ~1,000 km carry growing position error.',
            'Shares are of fire intensity (FRP) the air passed near, not measured smoke mass or PM2.5.',
            'VIIRS sees fires only at ~01:30 and ~13:30 local overpasses; cloud and thick smoke hide fires; peat smoulders with low FRP.',
            'One trajectory per state capital; a state is larger than one point.',
            'Fires outside Indonesia, Malaysia and Brunei are not counted.',
            'Provinces only; no companies or concessions are identified.',
        ],
    }
    OUT.write_text(json.dumps(out, separators=(',', ':'), ensure_ascii=False))
    print(f'wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)')
    print('national top:', [(r['province'], r['share']) for r in sources[:8]])


if __name__ == '__main__':
    main()
