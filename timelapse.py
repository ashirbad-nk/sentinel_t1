"""Native-resolution timelapse frames for a coordinate.

Modes:
  --build-manifest   one-time STAC search -> frames/manifest.json
  --fetch --shard i/N   fetch assigned dates (run N processes in parallel)
  --encode           make MP4 from the frames that exist

Rules: max 1 image per day, clouds included, native 10 m, 1080x1920 portrait.
Small nodata gaps are kept; a frame is discarded if >20% is missing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import planetary_computer as pc
import rasterio
from PIL import Image
from pystac import Item
from rasterio.warp import transform
from rasterio.windows import Window
from shapely.geometry import box, shape

Image.MAX_IMAGE_PIXELS = None

from src.sentinel_tool.config import S2_COLLECTION
from src.sentinel_tool.solar import solar_elevation
from src.sentinel_tool.stac import open_catalog

ROOT = Path(__file__).resolve().parent


def frames_for(tag: str = "") -> Path:
    """Separate frame directory per location so jobs never collide."""
    return ROOT / (f"frames_{tag}" if tag else "frames")


WIDTH, HEIGHT = 1080, 1920
MAX_MISSING = 0.20
MAX_CLOUD = 10.0        # only scenes with cloud cover strictly below this (%)
MIN_SUN, MAX_SUN = 40.0, 60.0  # only scenes with sun elevation in this range (deg)
RES = 10


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def tile_epsg(item) -> int:
    import re
    m = re.search(r"_T(\d{2})[A-Z]{3}_", item.id)
    if m:
        return 32600 + int(m.group(1))
    return 32600 + int((item.bbox[0] + item.bbox[2]) / 2 + 180) // 6 + 1


def center_xy(lon: float, lat: float, epsg: int):
    x, y = transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    return float(x[0]), float(y[0])


def pixel_window(item, lon: float, lat: float):
    """Exact 1080x1920 pixel window centred on (lon, lat)."""
    epsg = tile_epsg(item)
    x, y = center_xy(lon, lat, epsg)
    tr = item.assets["B04"].extra_fields["proj:transform"]
    res, tx, ty = tr[0], tr[2], tr[5]
    col = int(round((x - tx) / res - WIDTH / 2))
    row = int(round((ty - y) / res - HEIGHT / 2))
    return Window(col, row, WIDTH, HEIGHT)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def build_manifest(lat: float, lon: float, years: list[int], frames: Path) -> None:
    manifest = frames / "manifest.json"
    catalog = open_catalog()
    epsg = 32600 + int((lon + 180) // 6) + 1
    cx, cy = center_xy(lon, lat, epsg)
    # window footprint in lat/lon for intersection tests
    from rasterio.warp import transform_bounds
    wb4326 = transform_bounds(f"EPSG:{epsg}", "EPSG:4326",
                              cx - WIDTH * RES / 2, cy - HEIGHT * RES / 2,
                              cx + WIDTH * RES / 2, cy + HEIGHT * RES / 2)
    window4326 = box(*wb4326)

    entries = []
    for year in years:
        search = catalog.search(
            collections=[S2_COLLECTION],
            bbox=(lon - 0.02, lat - 0.02, lon + 0.02, lat + 0.02),
            datetime=f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z",
            limit=500,
        )
        by_date: dict[str, list] = {}
        for item in search.items():
            if not item.geometry:
                continue
            cloud = float(item.properties.get("eo:cloud_cover", 999))
            if cloud >= MAX_CLOUD:
                continue
            elev = solar_elevation(item, lat, lon)
            if not (MIN_SUN <= elev <= MAX_SUN):
                continue
            geom = shape(item.geometry)
            if not geom.intersects(window4326):
                continue
            d = item.datetime.strftime("%Y-%m-%d")
            by_date.setdefault(d, []).append((geom.intersection(window4326).area, item))

        for date in sorted(by_date):
            cands = sorted(by_date[date], key=lambda t: t[0], reverse=True)
            entries.append({
                "date": date,
                "items": [it.to_dict() for _, it in cands[:4]],
            })
        print(f"  {year}: {len(by_date)} days", flush=True)

    manifest.write_text(json.dumps({
        "lat": lat, "lon": lon, "years": years,
        "entries": entries,
    }))
    print(f"Manifest saved: {manifest} ({len(entries)} days)", flush=True)


# ---------------------------------------------------------------------------
# frame generation
# ---------------------------------------------------------------------------
def read_layer(item, lon: float, lat: float) -> np.ndarray:
    """Read B04/B03/B02 window (~12 MB total) as float reflectance (h,w,3)."""
    win = pixel_window(item, lon, lat)
    hrefs = [item.assets[b].href for b in ("B04", "B03", "B02")]
    out = np.full((HEIGHT, WIDTH, 3), np.nan, dtype="float32")
    for i, href in enumerate(hrefs):
        with rasterio.open(href) as src:
            arr = src.read(1, window=win, boundless=True, fill_value=0)
        if arr.shape != (HEIGHT, WIDTH):
            # pad/crop defensively (should not happen with exact window)
            tmp = np.full((HEIGHT, WIDTH), np.nan, dtype="float32")
            h, w = min(HEIGHT, arr.shape[0]), min(WIDTH, arr.shape[1])
            tmp[:h, :w] = arr[:h, :w]
            arr = tmp
        arr = arr.astype("float32") / 10_000.0
        arr[arr <= 0] = np.nan
        out[..., i] = arr
    return out


def make_frame(entry: dict, lat: float, lon: float):
    """Return uint8 RGB frame or None if >20% missing."""
    items = []
    for d in entry["items"]:
        signed = pc.sign(Item.from_dict(d))
        items.append(signed)

    layers = []
    for item in items:
        layers.append(read_layer(item, lon, lat))

    # composite: first valid pixel wins
    out = np.full_like(layers[0], np.nan)
    for arr in layers:
        fill = np.isnan(out) & np.isfinite(arr)
        out[fill] = arr[fill]

    missing = float(np.isnan(out[..., 0]).mean())
    if missing > MAX_MISSING:
        return None, missing

    # fixed stretch for flicker-free playback
    lo, hi, gamma = 0.01, 0.30, 0.75
    out = np.clip((out - lo) / (hi - lo), 0, 1)
    np.nan_to_num(out, nan=0.0, copy=False)
    rgb = (out**gamma * 255).astype("uint8")
    return rgb, missing


def fetch(shard: int, nshards: int, frames: Path) -> None:
    data = json.loads((frames / "manifest.json").read_text())
    lat, lon = data["lat"], data["lon"]
    entries = data["entries"]
    log = frames / f"fetch_{shard}.log"
    done = skipped = discarded = 0

    for i, entry in enumerate(entries):
        if i % nshards != shard:
            continue
        date = entry["date"]
        final = frames / f"{date}.jpg"
        if final.exists():
            skipped += 1
            continue
        t0 = time.time()
        cloud = float(entry["items"][0].get("properties", {}).get("eo:cloud_cover", 0) or 0)
        if cloud > MAX_CLOUD:
            discarded += 1
            msg = f"{date} DISCARD_CLOUD cloud={cloud:.1f}%"
            print(msg, flush=True)
            with log.open("a") as f:
                f.write(msg + "\n")
            continue
        try:
            rgb, missing = make_frame(entry, lat, lon)
        except Exception as exc:
            msg = f"{date} ERROR {type(exc).__name__}: {exc}"
            print(msg, flush=True)
            with log.open("a") as f:
                f.write(msg + "\n")
            continue
        if rgb is None:
            discarded += 1
            msg = f"{date} DISCARD missing={missing:.1%}"
            print(msg, flush=True)
            with log.open("a") as f:
                f.write(msg + "\n")
            continue
        tmp = frames / f".tmp_{date}_{os.getpid()}.jpg"
        Image.fromarray(rgb).save(tmp, quality=88, subsampling=0)
        os.replace(tmp, final)
        done += 1
        msg = (f"{date} OK missing={missing:.1%} "
               f"({time.time()-t0:.0f}s) [{i+1}/{len(entries)}]")
        print(msg, flush=True)
        with log.open("a") as f:
            f.write(msg + "\n")

    print(f"shard {shard} done: new={done} skipped={skipped} discarded={discarded}", flush=True)


def encode(frames: Path) -> None:
    import subprocess
    files = sorted(frames.glob("????-??-??.jpg"))
    if not files:
        print("no frames to encode")
        return
    listfile = frames / "frames.txt"
    listfile.write_text("".join(f"file '{p}'\n" for p in files))
    out = ROOT / "outputs" / "timelapse_2019_2023.mp4"
    out.parent.mkdir(exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-vf", "scale=1080:1920", "-r", "10", "-c:v", "libx264",
        "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(out),
    ], check=True)
    print(f"encoded {len(files)} frames -> {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lat", type=float, default=23.0507771)
    ap.add_argument("--lon", type=float, default=72.5448828)
    ap.add_argument("--years", default="2019,2020,2021,2022,2023")
    ap.add_argument("--tag", default="gandhinagar", help="location tag -> frames_<tag>/")
    ap.add_argument("--build-manifest", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--encode", action="store_true")
    args = ap.parse_args()
    frames = frames_for(args.tag)
    frames.mkdir(exist_ok=True)

    if args.build_manifest:
        build_manifest(args.lat, args.lon, [int(y) for y in args.years.split(",")], frames)
    elif args.fetch:
        fetch(args.shard, args.nshards, frames)
    elif args.encode:
        encode(frames)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
