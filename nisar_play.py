"""Play locally with NASA/ISRO NISAR L-band SAR data via ASF APIs.

All auth comes from ~/.netrc (populated from ~/Documents/Erthdata_login).
All data is free/open; all processing is local FOSS (rasterio, xarray, h5py).

Commands:
    python nisar_play.py search                  # list granules over the AOI
    python nisar_play.py browse --limit 6        # fetch browse PNGs -> local gallery
    python nisar_play.py fetch                   # download one GCOV .h5
    python nisar_play.py inspect                 # open .h5, export HH/HV GeoTIFF + PNG
    python nisar_play.py series                  # lazy multi-date stack -> Zarr + GIF

Requires: pip install asf_search earthaccess h5netcdf rioxarray s3fs
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "nisar"
OUT.mkdir(parents=True, exist_ok=True)

LAT, LON = 23.0507771, 72.5448828        # Gujarat AOI from the timelapse work
START, END = datetime(2025, 6, 1), datetime(2026, 11, 19)
PRODUCT = "GCOV"


def find_granules(limit: int = 100):
    """Search ASF for NISAR GCOV granules over the AOI using ~/.netrc auth."""
    import asf_search as asf

    opts = asf.ASFSearchOptions(**{
        "maxResults": limit,
        "intersectsWith": f"POINT({LON} {LAT})",
        "start": START,
        "end": END,
        "processingLevel": [PRODUCT],
        "dataset": ["NISAR"],
    })
    return list(asf.search(opts=opts))


def granule_h5_bytes(p: dict) -> int:
    """Main product size = largest .h5 entry (QA_STATS.h5 is tiny)."""
    sizes = [m.get("bytes", 0) for fn, m in (p.get("bytes") or {}).items()
             if isinstance(m, dict) and fn.endswith(".h5")]
    return max(sizes) if sizes else 0


def cmd_search(args):
    granules = find_granules(args.limit)
    rows = []
    for g in granules:
        p = g.properties
        h5_bytes = granule_h5_bytes(p)
        rows.append({
            "scene": p.get("sceneName"),
            "start": p.get("startTime"),
            "mode": p.get("productionConfiguration"),
            "collection": p.get("collectionName"),
            "polarization": p.get("polarization"),
            "bytes": h5_bytes,
            "center": [p.get("centerLat"), p.get("centerLon")],
        })
    (OUT / "granules.json").write_text(json.dumps(rows, indent=2))
    print(f"Found {len(rows)} NISAR GCOV granules -> outputs/nisar/granules.json\n")
    for r in rows[:args.limit]:
        size = r["bytes"] / 1e6 if r["bytes"] else 0
        print(f"  {r['start'][:10]}  {r['mode']:3s}  {size:7.1f} MB  {r['scene']}")
    return granules


def browse_urls(g) -> list[str]:
    out = []
    for item in (g.properties.get("browse") or []):
        if isinstance(item, dict):
            url = item.get("url") or item.get("href")
        else:
            url = str(item)
        if url:
            out.append(url)
    return out


def cmd_browse(args):
    import requests

    granules = find_granules(args.limit)
    cards = []
    for g in granules[:args.limit]:
        urls = [u for u in browse_urls(g) if ".png" in u.lower()]
        if not urls:
            continue
        url = next((u for u in urls if "thumbnail" not in u.lower()), urls[0])
        name = g.properties["sceneName"]
        dest = OUT / "browse" / f"{name}.png"
        dest.parent.mkdir(exist_ok=True, parents=True)
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            dest.write_bytes(r.content)
            cards.append((name, g.properties.get("startTime", "")[:10], dest.name))
            print(f"  got {dest.name} ({len(r.content)//1024} KB)")
        except Exception as exc:
            print(f"  FAILED {name}: {exc}")
    if not cards:
        print("No browse images fetched.")
        return
    html = ["<html><body style='background:#111;color:#eee;font-family:sans-serif'>",
            "<h2>NISAR GCOV browse imagery — Gujarat AOI</h2>"]
    for name, date, fn in cards:
        html.append(f"<div style='display:inline-block;margin:8px'><img src='browse/{fn}' width=420>"
                    f"<div>{date}<br>{name[:58]}</div></div>")
    html.append("</body></html>")
    (OUT / "browse.html").write_text("\n".join(html))
    print(f"\nGallery: outputs/nisar/browse.html ({len(cards)} images)")


def choose_h5(granules):
    """Prefer a non-browse .h5 URL from the PRODUCTION collection."""
    cands = []
    for g in granules:
        p = g.properties
        if p.get("productionConfiguration") == "UR":
            continue  # urgent-response products are temporary
        url = p.get("url")
        if url and url.endswith(".h5"):
            cands.append((p.get("startTime", ""), url, p.get("sceneName")))
    if not cands:
        for g in granules:
            url = g.properties.get("url")
            if url and url.endswith(".h5"):
                cands.append((g.properties.get("startTime", ""), url, g.properties.get("sceneName")))
    cands.sort()
    return cands[-1] if cands else None


def cmd_fetch(args):
    import asf_search as asf

    granules = find_granules(200)
    choice = choose_h5(granules)
    if not choice:
        print("No HDF5 product found.")
        return
    date, url, name = choice
    dest = OUT / "h5" / f"{name}.h5"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"Already downloaded: {dest.relative_to(ROOT)} ({dest.stat().st_size/1e6:.1f} MB)")
        return
    print(f"Downloading {date[:10]} {name} ...")
    session = asf.ASFSession()  # picks up ~/.netrc
    asf.download_urls(urls=[url], path=str(dest.parent), session=session)
    downloaded = dest.parent / url.rsplit("/", 1)[-1]
    if downloaded != dest and downloaded.exists():
        downloaded.rename(dest)
    print(f"Saved {dest.relative_to(ROOT)} ({dest.stat().st_size/1e6:.1f} MB)")


def resolve_signed(url: str) -> str:
    """Resolve EDL URL -> signed CloudFront URL (no auth needed for range reads)."""
    import earthaccess
    import requests

    token = earthaccess.login().token["access_token"]
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                     allow_redirects=False, timeout=120)
    if r.status_code in (302, 303) and r.headers.get("location"):
        return r.headers["location"]
    r.raise_for_status()
    return url


def open_datatree(url: str):
    """Lazy-open a resolved NISAR HDF5 via HTTPS range reads (no full download)."""
    import fsspec
    import xarray as xr

    fs = fsspec.filesystem("https", client_kwargs={"trust_env": True})
    f = fs.open(url, block_size=16 * 1024 * 1024, cache_type="background")
    return xr.open_datatree(f, engine="h5netcdf", phony_dims="access",
                            decode_timedelta=False)


def subset_to_aoi(da, lat: float, lon: float, size_m: int = 12000, epsg: int = 32643):
    """Window a geocoded array to a square centred on (lon, lat) in UTM metres."""
    from rasterio.warp import transform

    x, y = transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    xc, yc = float(x[0]), float(y[0])
    hx = hy = size_m / 2
    out = da.sel(
        xCoordinates=slice(xc - hx, xc + hx),
        yCoordinates=slice(yc + hy, yc - hy),
    )
    if out.size == 0:  # granule does not cover the AOI
        return None
    return out


def cmd_inspect(args):
    import numpy as np
    import rioxarray  # noqa: F401  (registers .rio)
    from PIL import Image

    granules = find_granules(200)
    choice = choose_h5(granules)
    if not choice:
        print("No product found.")
        return
    date, url, name = choice
    print(f"Lazy-opening {date[:10]} {name} (range reads, no full download) ...")
    dt = open_datatree(resolve_signed(url))
    node = dt["/science/LSAR/GCOV/grids/frequencyA"]
    epsg = int(node["projection"].attrs.get("epsg_code", 32643))
    print(f"CRS EPSG:{epsg} | full grid {dict(node['HHHH'].sizes)}")

    for pol in ("HHHH", "HVHV"):
        da = subset_to_aoi(node[pol], LAT, LON, size_m=args.window, epsg=epsg)
        a = np.asarray(da.values, dtype="float32")
        db = 10 * np.log10(a + 1e-12)
        finite = np.isfinite(db)
        if not finite.any():
            print(f"  {pol}: no valid samples in window")
            continue
        lo, hi = np.nanpercentile(db[finite], [2, 98])
        img = np.clip((db - lo) / (hi - lo + 1e-6), 0, 1)
        png = OUT / f"{date[:10]}_{pol}.png"
        Image.fromarray((img * 255).astype("uint8")).save(png)
        tif = OUT / f"{date[:10]}_{pol}.tif"
        da.rio.write_crs(f"EPSG:{epsg}").rio.to_raster(tif)
        print(f"  {pol}: {db[finite].mean():.1f} dB mean, {img.shape[0]}x{img.shape[1]} px"
              f" -> {png.relative_to(ROOT)}, {tif.relative_to(ROOT)}")


def cmd_series(args):
    import csv
    import numpy as np
    from PIL import Image, ImageDraw

    granules = find_granules(200)
    cands = []
    for g in granules:
        p = g.properties
        if p.get("productionConfiguration") != "PR" or not p.get("url", "").endswith(".h5"):
            continue
        cands.append((p.get("startTime", ""), p["url"], p["sceneName"],
                      granule_h5_bytes(p)))
    cands = [c for c in sorted(cands) if c[3] > 1e8]  # skip tiny/empty products
    picks = cands[-args.count:]
    if not picks:
        print("No usable PR products.")
        return

    rows, thumbs = [], []
    for date, url, name, sz in picks:
        print(f"  {date[:10]} ...", flush=True)
        try:
            dt = open_datatree(resolve_signed(url))
            node = dt["/science/LSAR/GCOV/grids/frequencyA"]
            epsg = int(node["projection"].attrs.get("epsg_code", 32643))
            da = subset_to_aoi(node["HVHV"], LAT, LON, size_m=args.window, epsg=epsg)
            if da is None:
                print("    no AOI coverage; skipped")
                continue
            a = np.asarray(da.values, dtype="float32")
            db = 10 * np.log10(a + 1e-12)
            valid = np.isfinite(db)
            if not valid.any():
                continue
            mean_db = float(db[valid].mean())
            lo, hi = np.nanpercentile(db[valid], [2, 98])
            img = np.clip((db - lo) / (hi - lo + 1e-6), 0, 1)
            im = Image.fromarray((img * 255).astype("uint8"))
            im.save(OUT / f"series_{date[:10]}_HVHV.png")
            t = im.copy(); t.thumbnail((320, 320))
            thumbs.append((date[:10], t))
            rows.append({"date": date[:10], "mean_hvhv_db": round(mean_db, 2),
                         "scene": name, "valid_pixels": int(valid.sum())})
            print(f"    mean HVHV {mean_db:.1f} dB", flush=True)
        except Exception as exc:
            print(f"    ERROR {type(exc).__name__}: {exc}", flush=True)

    if not rows:
        print("No series data collected.")
        return
    with (OUT / "series.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    print(f"Wrote outputs/nisar/series.csv ({len(rows)} dates)")

    # time-series plot
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot([r["date"] for r in rows], [r["mean_hvhv_db"] for r in rows],
            marker="o", color="#2ea043")
    ax.set_title("NISAR L-band HVHV backscatter — Gujarat AOI")
    ax.set_ylabel("mean gamma-0 (dB)")
    ax.grid(alpha=.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(OUT / "series_plot.png", dpi=140)
    print("Wrote outputs/nisar/series_plot.png")

    # contact sheet
    if thumbs:
        cols = min(4, len(thumbs))
        sheet = Image.new("RGB", (cols * 330, ((len(thumbs) + cols - 1) // cols) * 360), "white")
        d = ImageDraw.Draw(sheet)
        for i, (date, t) in enumerate(thumbs):
            x, y = (i % cols) * 330, (i // cols) * 360
            sheet.paste(t, (x + 5, y + 5)); d.text((x + 5, y + 330), date, fill="black")
        sheet.save(OUT / "series_contact.jpg", quality=88)
        print("Wrote outputs/nisar/series_contact.jpg")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("search", "browse", "fetch", "inspect", "series"):
        p = sub.add_parser(name)
        if name in ("search", "browse"):
            p.add_argument("--limit", type=int, default=10 if name == "browse" else 100)
        if name in ("inspect", "series"):
            p.add_argument("--window", type=int, default=12000, help="AOI window size in metres")
        if name == "series":
            p.add_argument("--count", type=int, default=6, help="number of dates")
    args = ap.parse_args()
    {"search": cmd_search, "browse": cmd_browse, "fetch": cmd_fetch,
     "inspect": cmd_inspect, "series": cmd_series}[args.cmd](args)


if __name__ == "__main__":
    main()
