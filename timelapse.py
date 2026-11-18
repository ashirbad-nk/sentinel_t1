"""Native-resolution timelapse frames for a coordinate.

Modes:
  --build-manifest         one-time STAC search -> frames_<tag>/manifest.json
  --fetch --shard i --nshards N   fetch assigned dates (run N processes in parallel)
  --encode                 make MP4 from the frames that exist

Rules: max 1 image per day, <10% scene cloud cover, 40-60 deg solar elevation,
native 10 m, 1080x1920 portrait, PB 04.00+ radiometrically calibrated.
Small nodata gaps are kept; a frame is discarded if >20% is missing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image
from shapely.geometry import box, shape

Image.MAX_IMAGE_PIXELS = None

from sentinel_tool.config import S2_COLLECTION, utm_epsg
from sentinel_tool.indices import dn_to_reflectance

WIDTH, HEIGHT = 1080, 1920
MAX_MISSING = 0.20
MAX_CLOUD = 10.0        # only scenes with cloud cover strictly below this (%)
MIN_SUN, MAX_SUN = 40.0, 60.0  # only scenes with sun elevation in this range (deg)
RES = 10
READ_RETRIES = 3


def frames_for(tag: str = "") -> Path:
    """Separate frame directory per location so jobs never collide."""
    return ROOT / (f"frames_{tag}" if tag else "frames")


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------
def tile_epsg(item) -> int:
    """Determine the UTM EPSG code of a Sentinel-2 STAC item."""
    props = getattr(item, "properties", None) or {}
    if props.get("proj:epsg"):
        return int(props["proj:epsg"])

    lat = (item.bbox[1] + item.bbox[3]) / 2.0 if getattr(item, "bbox", None) else 0.0
    m = re.search(r"_T(\d{2})[A-Z]{3}_", item.id)
    if m:
        return (32600 if lat >= 0 else 32700) + int(m.group(1))

    lon = (item.bbox[0] + item.bbox[2]) / 2.0
    return utm_epsg(lon, lat)


def center_xy(lon: float, lat: float, epsg: int) -> tuple[float, float]:
    from rasterio.warp import transform

    x, y = transform("EPSG:4326", f"EPSG:{epsg}", [lon], [lat])
    return float(x[0]), float(y[0])


def target_grid(lon: float, lat: float) -> tuple[int, float, float]:
    """Return (target_epsg, left, top) snapped to the 10 m UTM grid."""
    epsg = utm_epsg(lon, lat)
    cx, cy = center_xy(lon, lat, epsg)
    left = round((cx - WIDTH * RES / 2.0) / RES) * RES
    top = round((cy + HEIGHT * RES / 2.0) / RES) * RES
    return epsg, float(left), float(top)


def pixel_window(item, left: float, top: float):
    """Exact 1080x1920 pixel window in the item's native grid (same UTM zone)."""
    from rasterio.windows import Window

    tr = item.assets["B04"].extra_fields["proj:transform"]
    res, tx, ty = tr[0], tr[2], tr[5]
    col = int(round((left - tx) / res))
    row = int(round((ty - top) / res))
    return Window(col, row, WIDTH, HEIGHT)


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def build_manifest(lat: float, lon: float, years: list[int], frames: Path) -> None:
    from rasterio.warp import transform_bounds

    from sentinel_tool.solar import solar_elevation
    from sentinel_tool.stac import open_catalog

    manifest = frames / "manifest.json"
    catalog = open_catalog(sign=False)
    epsg, left, top = target_grid(lon, lat)
    # Full 10.8 x 19.2 km portrait footprint in EPSG:4326 for STAC search & intersection.
    wb4326 = transform_bounds(
        f"EPSG:{epsg}",
        "EPSG:4326",
        left,
        top - HEIGHT * RES,
        left + WIDTH * RES,
        top,
    )
    window4326 = box(*wb4326)

    entries = []
    for year in years:
        search = catalog.search(
            collections=[S2_COLLECTION],
            bbox=wb4326,
            datetime=f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z",
            limit=500,
        )
        by_date: dict[str, list] = {}
        for item in search.items():
            if not item.geometry or item.datetime is None:
                continue
            raw_cloud = item.properties.get("eo:cloud_cover")
            if raw_cloud is None or float(raw_cloud) >= MAX_CLOUD:
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
            entries.append(
                {
                    "date": date,
                    "items": [it.to_dict() for _, it in cands[:4]],
                }
            )
        print(f"  {year}: {len(by_date)} days", flush=True)

    payload = json.dumps({"lat": lat, "lon": lon, "years": years, "entries": entries})
    tmp = frames / f".tmp_manifest_{os.getpid()}.json"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, manifest)
    print(f"Manifest saved: {manifest} ({len(entries)} days)", flush=True)


# ---------------------------------------------------------------------------
# frame generation
# ---------------------------------------------------------------------------
def _read_band_once(href: str, item, target_epsg: int, left: float, top: float) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.vrt import WarpedVRT

    item_epsg = tile_epsg(item)
    with rasterio.open(href) as src:
        if item_epsg == target_epsg and "proj:transform" in item.assets["B04"].extra_fields:
            win = pixel_window(item, left, top)
            arr = src.read(1, window=win, boundless=True, fill_value=0)
        else:
            # Reproject cross-UTM-zone tiles onto the exact target 10 m grid.
            dst_transform = Affine(RES, 0.0, left, 0.0, -RES, top)
            with WarpedVRT(
                src,
                crs=f"EPSG:{target_epsg}",
                transform=dst_transform,
                width=WIDTH,
                height=HEIGHT,
                resampling=Resampling.bilinear,
                src_nodata=0,
                nodata=0,
            ) as vrt:
                arr = vrt.read(1)

    if arr.shape != (HEIGHT, WIDTH):
        tmp = np.zeros((HEIGHT, WIDTH), dtype=arr.dtype)
        h, w = min(HEIGHT, arr.shape[0]), min(WIDTH, arr.shape[1])
        tmp[:h, :w] = arr[:h, :w]
        arr = tmp
    return dn_to_reflectance(arr, item=item)


def read_layer(item, lon: float, lat: float) -> np.ndarray:
    """Read B04/B03/B02 window (~12 MB total) as PB-calibrated float reflectance (h,w,3)."""
    target_epsg, left, top = target_grid(lon, lat)
    hrefs = [item.assets[b].href for b in ("B04", "B03", "B02")]
    out = np.full((HEIGHT, WIDTH, 3), np.nan, dtype="float32")
    for i, href in enumerate(hrefs):
        last_exc = None
        for attempt in range(READ_RETRIES):
            try:
                out[..., i] = _read_band_once(href, item, target_epsg, left, top)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if attempt + 1 < READ_RETRIES:
                    time.sleep(2**attempt)
        if last_exc is not None:
            raise last_exc
    return out


def make_frame(entry: dict, lat: float, lon: float):
    """Return (uint8 RGB frame or None, missing_fraction)."""
    import planetary_computer as pc
    from pystac import Item

    raw_items = entry.get("items", [])
    if not raw_items:
        return None, 1.0

    items = [pc.sign(Item.from_dict(d)) for d in raw_items]
    layers = [read_layer(item, lon, lat) for item in items]

    # composite: first valid pixel wins
    out = np.full_like(layers[0], np.nan)
    for arr in layers:
        fill = np.isnan(out) & np.isfinite(arr)
        out[fill] = arr[fill]

    missing = float(np.isnan(out).any(axis=-1).mean())
    if missing > MAX_MISSING:
        return None, missing

    # fixed stretch for flicker-free playback (PB 04.00+ offset already applied)
    lo, hi, gamma = 0.01, 0.30, 0.75
    out = np.clip((out - lo) / (hi - lo), 0.0, 1.0)
    np.nan_to_num(out, nan=0.0, copy=False)
    rgb = (out**gamma * 255).astype("uint8")
    return rgb, missing


def fetch(shard: int, nshards: int, frames: Path) -> None:
    manifest = frames / "manifest.json"
    if not manifest.exists():
        print(f"error: manifest not found at {manifest} (run --build-manifest first)", file=sys.stderr)
        sys.exit(1)

    data = json.loads(manifest.read_text(encoding="utf-8"))
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
        items = entry.get("items", [])
        if not items:
            discarded += 1
            msg = f"{date} DISCARD missing=100.0%"
            print(msg, flush=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
            continue

        raw_cloud = items[0].get("properties", {}).get("eo:cloud_cover")
        if raw_cloud is None or float(raw_cloud) >= MAX_CLOUD:
            discarded += 1
            cloud_label = f"{float(raw_cloud):.1f}%" if raw_cloud is not None else "unknown"
            msg = f"{date} DISCARD_CLOUD cloud={cloud_label}"
            print(msg, flush=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
            continue

        try:
            rgb, missing = make_frame(entry, lat, lon)
        except Exception as exc:
            msg = f"{date} ERROR {type(exc).__name__}: {exc}"
            print(msg, flush=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
            continue

        if rgb is None:
            discarded += 1
            msg = f"{date} DISCARD missing={missing:.1%}"
            print(msg, flush=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
            continue

        tmp = frames / f".tmp_{date}_{os.getpid()}.jpg"
        Image.fromarray(rgb).save(tmp, quality=88, subsampling=0)
        os.replace(tmp, final)
        done += 1
        msg = f"{date} OK missing={missing:.1%} ({time.time()-t0:.0f}s) [{i+1}/{len(entries)}]"
        print(msg, flush=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")

    print(f"shard {shard} done: new={done} skipped={skipped} discarded={discarded}", flush=True)


def write_concat_list(files: list[Path], listfile: Path, fps: float = 10.0) -> None:
    """Write a cross-platform FFmpeg concat demuxer list with exact per-frame duration."""
    if not files:
        raise ValueError("files must not be empty")
    dur = 1.0 / fps
    lines = []
    for p in files:
        safe = p.name.replace("'", "'\\''")
        lines.append(f"file '{safe}'\nduration {dur:.6f}\n")
    # FFmpeg concat demuxer requires repeating the final entry so its duration is honoured.
    last = files[-1].name.replace("'", "'\\''")
    lines.append(f"file '{last}'\n")
    listfile.write_text("".join(lines), encoding="utf-8")


def output_mp4_path(frames: Path, files: list[Path]) -> Path:
    """Derive a descriptive output MP4 path from the frames folder and actual date span."""
    y0, y1 = files[0].stem[:4], files[-1].stem[:4]
    tag = frames.name[len("frames_") :] if frames.name.startswith("frames_") else ""
    stem = f"timelapse_{tag}_{y0}_{y1}" if tag else f"timelapse_{y0}_{y1}"
    return ROOT / "outputs" / f"{stem}.mp4"


def encode(frames: Path, fps: int = 10) -> None:
    files = sorted(frames.glob("????-??-??.jpg"))
    if not files:
        print("no frames to encode")
        return
    listfile = frames / "frames.txt"
    write_concat_list(files, listfile, fps=float(fps))
    out = output_mp4_path(frames, files)
    out.parent.mkdir(exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listfile),
            "-vf",
            f"scale={WIDTH}:{HEIGHT}",
            "-fps_mode",
            "cfr",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    print(f"encoded {len(files)} frames @ {fps} fps -> {out}")


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
    ap.add_argument("--fps", type=int, default=10, help="output MP4 frame rate (default 10)")
    args = ap.parse_args()
    frames = frames_for(args.tag)
    frames.mkdir(exist_ok=True)

    if args.build_manifest:
        build_manifest(args.lat, args.lon, [int(y) for y in args.years.split(",")], frames)
    elif args.fetch:
        if args.nshards < 1 or not (0 <= args.shard < args.nshards):
            ap.error("--shard must satisfy 0 <= shard < nshards")
        fetch(args.shard, args.nshards, frames)
    elif args.encode:
        if args.fps <= 0:
            ap.error("--fps must be positive")
        encode(frames, fps=args.fps)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
