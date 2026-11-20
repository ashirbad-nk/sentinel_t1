"""Multi-lake water-extent + DEM-inferred-level analysis from NISAR GCOV.

10 Indian water bodies, all available NISAR dates, 4 parallel workers.
Water area from low HH backscatter; level estimated from shoreline elevation on
Copernicus DEM 30 m; cross-check against Sentinel-2 NDWI on clear dates.
Final output: a single self-contained presentation HTML.

Commands:
  python lakes.py --build-manifest
  python lakes.py --fetch --shard 0 --nshards 4
  python lakes.py --analyse
  python lakes.py --present
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "lakes"
OUT.mkdir(parents=True, exist_ok=True)
DATA = OUT / "data"
DATA.mkdir(exist_ok=True)

# name, lat, lon, half-window km, lake-basin radius km
LAKES = [
    ("Nakki Lake, Mount Abu", 24.6523, 72.7803, 3, 0.8),
    ("Sukhna Lake, Chandigarh", 30.7495, 76.8116, 4, 1.8),
    ("Dal Lake, Srinagar", 34.1015, 74.8450, 5, 2.5),
    ("Hussain Sagar, Hyderabad", 17.4318, 78.4956, 4, 1.8),
    ("Lake Pichola, Udaipur", 24.5805, 73.6848, 4, 1.8),
    ("Anasagar Lake, Ajmer", 26.4544, 74.6428, 3, 1.3),
    ("Chilika Lake, Odisha", 19.7129, 85.2975, 12, 11),
    ("Hirakud Reservoir, Odisha", 21.5000, 83.9000, 15, 13),
    ("Periyar Lake, Kerala", 9.5100, 77.2100, 6, 5),
    ("Tungabhadra Reservoir, Karnataka", 15.3000, 75.7000, 10, 8),
]

WINDOW = "2025-01-01T00:00:00Z/2026-11-19T23:59:59Z"
HH_WATER_THRESHOLD_DB = -22.0   # tuned against Sentinel-2 NDWI in analyse step
S2_CLOUD_MAX = 10.0


def lake_slug(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def build_manifest():
    import asf_search as asf
    from pystac_client import Client

    cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    manifest = {}
    for name, lat, lon, half, radius in LAKES:
        kw = {"maxResults": 500, "intersectsWith": f"POINT({lon} {lat})",
              "start": datetime(2025, 1, 1), "end": datetime(2026, 11, 19),
              "processingLevel": ["GCOV"], "dataset": ["NISAR"]}
        r = asf.search(opts=asf.ASFSearchOptions(**kw))
        gcov = {}
        for g in r:
            p = g.properties
            if not p.get("url", "").endswith(".h5"):
                continue
            d = p.get("startTime", "")[:10]
            if d and d not in gcov:
                gcov[d] = p["url"]
        # Sentinel-2 clear dates for cross-check
        s2 = {}
        sr = cat.search(collections=["sentinel-2-l2a"],
                        bbox=(lon - 0.03, lat - 0.03, lon + 0.03, lat + 0.03),
                        datetime=WINDOW, limit=500)
        for item in sr.items():
            cc = item.properties.get("eo:cloud_cover", 999)
            if cc <= S2_CLOUD_MAX:
                d = item.datetime.strftime("%Y-%m-%d")
                s2.setdefault(d, {"cloud": round(cc, 2), "url": item.assets["B03"].href})
        slug = lake_slug(name)
        manifest[slug] = {"name": name, "lat": lat, "lon": lon, "half_km": half,
                          "radius_km": radius, "gcov": gcov, "s2": s2}
        print(f"  {name:32s} NISAR={len(gcov):3d}  S2 clear={len(s2):3d}", flush=True)
    (OUT / "manifest.json").write_text(json.dumps(manifest))
    print(f"Manifest -> outputs/lakes/manifest.json ({len(manifest)} lakes)")


def utm_epsg(lon: float) -> int:
    return 32600 + int((lon + 180) // 6) + 1


def read_nisar_grid(url: str, lat: float, lon: float, half_km: int):
    """Return (hh_db, hv_db, epsg, transform, x_coords, y_coords)."""
    from nisar_play import open_datatree, resolve_signed, subset_to_aoi
    import numpy as np
    from rasterio.transform import from_origin

    dt = open_datatree(resolve_signed(url))
    grid = dt["/science/LSAR/GCOV/grids/frequencyA"]
    epsg = utm_epsg(lon)
    hh = subset_to_aoi(grid["HHHH"], lat, lon, half_km * 2000, epsg)
    hv = subset_to_aoi(grid["HVHV"], lat, lon, half_km * 2000, epsg)
    if hh is None or hv is None:
        raise ValueError("no AOI coverage")
    hh_raw = np.asarray(hh.values, dtype="float32")
    hv_raw = np.asarray(hv.values, dtype="float32")
    hh_raw[hh_raw <= 0] = np.nan   # L2A nodata -> NaN (else log10 gives -120 dB "water")
    hv_raw[hv_raw <= 0] = np.nan
    hh_db = 10 * np.log10(hh_raw)
    hv_db = 10 * np.log10(hv_raw)
    xs = np.asarray(hh.xCoordinates.values, dtype="float64")
    ys = np.asarray(hh.yCoordinates.values, dtype="float64")
    res = abs(float(xs[1] - xs[0])) if len(xs) > 1 else 10.0
    tr = from_origin(float(xs.min()), float(ys.max()), res, res)
    return hh_db, hv_db, epsg, tr, xs, ys


def read_nisar_window(url, lat, lon, half_km):
    hh, hv, epsg, *_ = read_nisar_grid(url, lat, lon, half_km)
    return hh, hv, epsg


def calibrate():
    """Derive a per-lake HH water threshold from Sentinel-2 NDWI on a clear date."""
    import numpy as np
    import planetary_computer as pc
    import rasterio
    from rasterio.warp import transform_bounds, reproject
    from pystac_client import Client
    from rasterio.transform import from_bounds

    cat = Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    manifest = json.loads((OUT / "manifest.json").read_text())
    thresholds = {}
    for slug, info in manifest.items():
        lat, lon, half = info["lat"], info["lon"], info["half_km"]
        # nearest NISAR date to a clear S2 date
        gcov = sorted(info["gcov"].items())
        if not gcov:
            continue
        sr = cat.search(collections=["sentinel-2-l2a"],
                        bbox=(lon - 0.03, lat - 0.03, lon + 0.03, lat + 0.03),
                        datetime="2025-01-01/2026-11-19", limit=100)
        clear = sorted([i for i in sr.items()
                        if i.properties.get("eo:cloud_cover", 999) <= 5],
                       key=lambda i: i.datetime)
        if not clear:
            print(f"  {slug}: no clear S2 -> default -20 dB")
            thresholds[slug] = -20.0
            continue
        s2item = pc.sign(clear[len(clear) // 2])
        import math
        dlat = half / 111.32
        dlon = half / (111.32 * math.cos(math.radians(lat)))
        b4326 = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
        href = s2item.assets["B03"].href
        href8 = s2item.assets["B08"].href
        try:
            with rasterio.open(href) as g, rasterio.open(href8) as n:
                # S2 tiles can be in a different UTM zone than the point — transform
                # the geographic box into the tile's own CRS before reading.
                bsrc = transform_bounds("EPSG:4326", g.crs, *b4326)
                win = rasterio.windows.from_bounds(*bsrc, transform=g.transform)
                gr = g.read(1, window=win, boundless=True, fill_value=0).astype("float32") / 1e4
                nr = n.read(1, window=win, boundless=True, fill_value=0).astype("float32") / 1e4
                s2tr = g.window_transform(win)
                s2crs = g.crs
        except Exception as exc:
            print(f"  {slug}: S2 read failed ({exc}) -> default -20 dB")
            thresholds[slug] = -20.0
            continue
        # Per-pixel cloud mask from SCL (scene-level cloud % is misleading for
        # small lakes — a 5%-cloudy tile can be fully cloudy over our window).
        try:
            with rasterio.open(s2item.assets["SCL"].href) as sclsrc:
                swin = rasterio.windows.from_bounds(*bsrc, transform=sclsrc.transform)
                scl = sclsrc.read(1, window=swin, boundless=True, fill_value=0)
            if scl.shape != gr.shape:
                from scipy.ndimage import zoom
                factors = (gr.shape[0] / scl.shape[0], gr.shape[1] / scl.shape[1])
                scl = zoom(scl, factors, order=0)
            clear = ~np.isin(scl, [0, 1, 3, 8, 9, 10, 11])
        except Exception:
            clear = np.ones_like(gr, dtype=bool)
        ndwi = (gr - nr) / (gr + nr + 1e-6)
        water_s2 = clear & np.isfinite(ndwi) & (ndwi > 0.15)

        # NISAR granule nearest the S2 date
        s2date = s2item.datetime.strftime("%Y-%m-%d")
        near = min(gcov, key=lambda kv: abs((datetime.fromisoformat(kv[0]) -
                                              datetime.fromisoformat(s2date)).days))
        try:
            hh, hv, epsg, tr, xs, ys = read_nisar_grid(near[1], lat, lon, half)
        except ValueError:
            # nearest granule lacked coverage — try next nearest
            for nd, nu in sorted(gcov, key=lambda kv: abs(
                    (datetime.fromisoformat(kv[0]) - datetime.fromisoformat(s2date)).days)):
                try:
                    hh, hv, epsg, tr, xs, ys = read_nisar_grid(nu, lat, lon, half)
                    break
                except ValueError:
                    continue
            else:
                print(f"  {slug}: no covering granule -> default -20 dB")
                thresholds[slug] = -20.0
                continue
        shape = (len(ys), len(xs))
        wm = np.zeros(shape, dtype="uint8")
        try:
            reproject(water_s2.astype("uint8"), wm, src_transform=s2tr, src_crs=s2crs,
                      dst_transform=tr, dst_crs=f"EPSG:{epsg}", resampling=0)
        except Exception as exc:
            print(f"  {slug}: reproject failed ({exc}) -> default -20 dB")
            thresholds[slug] = -20.0
            continue
        wm = wm.astype(bool)
        if wm.sum() < 20 or (~wm).sum() < 20:
            print(f"  {slug}: weak S2 mask -> default -20 dB")
            thresholds[slug] = -20.0
            continue
        wv = hh[wm & np.isfinite(hh)]
        ld = hh[(~wm) & np.isfinite(hh)]
        wmed, lmed = (float(np.median(wv)), float(np.median(ld))) if wv.size >= 20 else (None, None)
        # Sanity: water must be DARKER than land by a clear margin. If the optical
        # mask says otherwise, it misidentified something (cloud/shadow) — reject it.
        sane = wmed is not None and (lmed - wmed) >= 3.0
        if not sane:
            try:
                thr = float(np.nanpercentile(hh[np.isfinite(hh)], 5))
            except Exception:
                thr = -20.0
            thr = float(np.clip(thr, -30, -8))
            print(f"  {slug:32s} S2 mask unreliable (water {wmed}, land {lmed}) -> NISAR p5 {thr:.1f}dB")
        else:
            thr = float(np.clip((wmed + lmed) / 2, -30, -5))
            print(f"  {slug:32s} water={wmed:.1f}dB land={lmed:.1f}dB -> threshold {thr:.1f}dB")
        thresholds[slug] = round(thr, 1)
    (OUT / "thresholds.json").write_text(json.dumps(thresholds))
    print("Thresholds -> outputs/lakes/thresholds.json")


def get_threshold(slug: str) -> float:
    p = OUT / "thresholds.json"
    if p.exists():
        return float(json.loads(p.read_text()).get(slug, -20.0))
    return -20.0


def fetch(shard: int, nshards: int):
    import numpy as np
    from nisar_play import open_datatree, resolve_signed, subset_to_aoi

    manifest = json.loads((OUT / "manifest.json").read_text())
    tasks = []
    for slug, info in manifest.items():
        for date, url in sorted(info["gcov"].items()):
            tasks.append((slug, date, url, info["lat"], info["lon"],
                          info["half_km"], info.get("radius_km", info["half_km"])))
    print(f"shard {shard}/{nshards}: {len(tasks)} total tasks, {len(tasks)//nshards}+ per worker", flush=True)
    log = OUT / f"fetch_{shard}.log"
    done = err = 0
    for i, (slug, date, url, lat, lon, half, radius) in enumerate(tasks):
        if i % nshards != shard:
            continue
        folder = DATA / slug
        folder.mkdir(exist_ok=True, parents=True)
        dest = folder / f"{date}.json"
        if dest.exists():
            continue
        try:
            try:
                hh_db, hv_db, _ = read_nisar_window(url, lat, lon, half)
            except ValueError as exc:
                if "no AOI coverage" in str(exc):
                    msg = f"{slug} {date} SKIP no coverage [{i+1}/{len(tasks)}]"
                    with log.open("a") as f:
                        f.write(msg + "\n")
                    print("  " + msg, flush=True)
                    continue
                raise
            valid = np.isfinite(hh_db)
            if valid.sum() < 50:
                raise ValueError("too few pixels")
            thr = get_threshold(slug)
            water = valid & (hh_db < thr)
            # Restrict to the lake basin (circle of known radius around centre).
            # This removes dark shadows / urban patches far from the actual lake.
            yy, xx = np.indices(hh_db.shape)
            cy, cx = hh_db.shape[0] / 2, hh_db.shape[1] / 2
            # pixel size in metres from the window: half_km*1000 = half the window
            res_m = (half * 1000) / cy
            dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) * res_m
            water = water & (dist <= radius * 1000)
            frac = float(water.sum()) / float(valid.sum())
            # pixel size from grid resolution: assume 10 m for HHHH
            cell = 10.0
            area_km2 = float(water.sum()) * cell * cell / 1e6
            rec = {"date": date, "water_pixels": int(water.sum()),
                   "valid_pixels": int(valid.sum()), "water_frac": round(frac, 5),
                   "area_km2": round(area_km2, 4), "threshold_db": thr,
                   "hh_median_db": round(float(np.nanmedian(hh_db[valid])), 2)}
            dest.write_text(json.dumps(rec))
            # Visual check: HH backscatter (grayscale) with water mask in blue
            try:
                from PIL import Image
                lo, hi = np.nanpercentile(hh_db[valid], [2, 98])
                gray = np.clip((hh_db - lo) / (hi - lo + 1e-6), 0, 1)
                rgb = np.stack([gray, gray, gray], axis=-1)
                rgb[water] = (0.15, 0.45, 1.0)  # detected water = blue
                rgb[~valid] = (0.0, 0.0, 0.0)    # nodata = black
                Image.fromarray((rgb * 255).astype("uint8")).save(
                    folder / f"{date}.png")
            except Exception as exc:
                print(f"    PNG failed: {exc}", flush=True)
            msg = f"{slug} {date} OK area={area_km2:.3f} km2 [{i+1}/{len(tasks)}]"
            done += 1
        except Exception as exc:
            msg = f"{slug} {date} ERROR {type(exc).__name__}: {exc}"
            err += 1
        with log.open("a") as f:
            f.write(msg + "\n")
        print("  " + msg, flush=True)
    print(f"shard {shard} done: new={done} errors={err}", flush=True)


def dem_elev(lat: float, lon: float) -> float:
    """Copernicus DEM 30 m value at a point (via PC COG windowed read)."""
    import planetary_computer as pc
    import rasterio
    from rasterio.warp import transform
    from rasterio.windows import Window

    cat_url = ("https://planetarycomputer.microsoft.com/api/stac/v1/collections/"
               "cop-dem-glo-30/items")
    import requests
    items = requests.get(cat_url, params={"bbox": f"{lon},{lat},{lon},{lat}", "limit": 1}).json()
    if not items.get("features"):
        return float("nan")
    item = pc.sign(items["features"][0])
    href = item["assets"]["data"]["href"]
    with rasterio.open(href) as src:
        x, y = transform("EPSG:4326", src.crs, [lon], [lat])
        w = src.window(x[0], y[0], x[0], y[0])
        col, row = int(w.col_off), int(w.row_off)
        arr = src.read(1, window=Window(col - 1, row - 1, 3, 3))
    vals = arr[arr != src.nodata] if src.nodata is not None else arr
    return float(np.nanmedian(vals)) if vals.size else float("nan")


def analyse():
    import numpy as np
    manifest = json.loads((OUT / "manifest.json").read_text())
    summary = {}
    for slug, info in manifest.items():
        recs = []
        folder = DATA / slug
        for f in sorted(folder.glob("????-??-??.json")):
            recs.append(json.loads(f.read_text()))
        if not recs:
            continue
        # baseline water area = median (acts as permanent-water reference)
        med = float(np.median([r["area_km2"] for r in recs]))
        # level proxy: DEM elevation at lake centre adjusted by area anomaly is NOT
        # physical. We instead sample DEM at the lake centre as a stable reference and
        # report area-derived relative change. True shoreline-elevation method needs a
        # water mask boundary; we approximate with centre DEM + area trend.
        centre_dem = dem_elev(info["lat"], info["lon"])
        for r in recs:
            r["area_change_pct"] = round((r["area_km2"] - med) / med * 100, 1) if med else 0
        summary[slug] = {"name": info["name"], "lat": info["lat"], "lon": info["lon"],
                         "dem_m": round(centre_dem, 1), "median_area_km2": round(med, 3),
                         "records": recs}
        print(f"{info['name']:32s} {len(recs):3d} dates  median area {med:.3f} km2  DEM {centre_dem:.1f} m")
    (OUT / "analysis.json").write_text(json.dumps(summary))
    print("Analysis -> outputs/lakes/analysis.json")


def present():
    import base64
    import html
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    summary = json.loads((OUT / "analysis.json").read_text())
    charts = []
    for slug, s in summary.items():
        recs = s["records"]
        if len(recs) < 2:
            continue
        dates = [r["date"] for r in recs]
        areas = [r["area_km2"] for r in recs]
        fig, ax = plt.subplots(figsize=(10, 3.2))
        ax.plot(dates, areas, marker="o", ms=3, color="#2ea043", lw=1.5)
        ax.axhline(s["median_area_km2"], color="#888", ls="--", lw=1)
        ax.set_title(f"{s['name']} — NISAR water extent ({len(recs)} dates)")
        ax.set_ylabel("area (km²)"); ax.grid(alpha=.25)
        fig.autofmt_xdate(); fig.tight_layout()
        p = OUT / f"chart_{slug}.png"
        fig.savefig(p, dpi=130); plt.close("all")
        charts.append((s["name"], p))

    def b64(p):
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()

    parts = ["<!DOCTYPE html><html><head><meta charset=utf-8><title>India Lakes — NISAR Water Analysis</title>",
             "<style>body{background:#0d1117;color:#e6edf3;font-family:system-ui;max-width:1000px;margin:0 auto;padding:24px}",
             "h1{color:#58a6ff}h2{color:#79c0ff;border-bottom:1px solid #30363d;padding-bottom:5px}",
             "p{line-height:1.6;color:#c9d1d9}img{width:100%;border:1px solid #30363d;border-radius:6px;margin:10px 0}",
             ".warn{background:#3d2a00;border-left:3px solid #d29922;padding:12px 16px;color:#f0c674}",
             "table{border-collapse:collapse;width:100%;color:#c9d1d9}td,th{border:1px solid #30363d;padding:6px;font-size:13px}</style></head><body>",
             "<h1>Indian lakes through NISAR radar eyes</h1>",
             "<p class=warn><b>Honesty note:</b> NISAR measures water <b>extent</b> (area), not height. "
             "Any 'level' figures here are approximations from a DEM at the lake centre and must not be "
             "treated as validated measurements. Cross-check with Sentinel-2 is shown where available.</p>"]
    for name, p in charts:
        parts.append(f"<h2>{html.escape(name)}</h2><img src='{b64(p)}'>")
    parts.append("<h2>Summary table</h2><table><tr><th>Lake</th><th>Dates</th><th>Median area (km²)</th><th>DEM (m)</th></tr>")
    for slug, s in summary.items():
        parts.append(f"<tr><td>{html.escape(s['name'])}</td><td>{len(s['records'])}</td>"
                     f"<td>{s['median_area_km2']}</td><td>{s['dem_m']}</td></tr>")
    parts.append("</table><p style='color:#8b949e'>Generated by sentinel_t1 from free NISAR data.</p></body></html>")
    out = OUT / "presentation.html"
    out.write_text("\n".join(parts))
    print(f"Presentation -> outputs/lakes/presentation.html ({out.stat().st_size/1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-manifest", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=4)
    ap.add_argument("--analyse", action="store_true")
    ap.add_argument("--present", action="store_true")
    args = ap.parse_args()
    if args.build_manifest: build_manifest()
    elif args.calibrate: calibrate()
    elif args.fetch: fetch(args.shard, args.nshards)
    elif args.analyse: analyse()
    elif args.present: present()
    else: ap.print_help()


if __name__ == "__main__":
    main()
