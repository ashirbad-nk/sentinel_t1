"""Generate full-tile ~121 MP JPEGs of 10 Indian targets.

For each target we pick three cloud-free scenes from the same Sentinel-2 tile
with contrasting solar elevations, so you can compare illumination/contrast.
Each image is one complete native tile (10980 x 10980 px = 120.6 MP) — no
clipping and no stitching.

Run:
    python make_tiles.py --targets 1    # test one target (3 images)
    python make_tiles.py                # all 10 targets
    python make_tiles.py --skip-existing  # resume
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None  # 120 MP images are intentional here
from shapely.geometry import Point, shape

from src.sentinel_tool.config import S2_COLLECTION
from src.sentinel_tool.fulltile import load_fulltile_rgb
from src.sentinel_tool.solar import solar_elevation
from src.sentinel_tool.stac import search_items

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "tiles"
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = [
    ("Agra - Taj Mahal", 27.1751, 78.0421),
    ("Rann of Kutch", 23.8000, 69.5000),
    ("Mumbai coast", 19.0760, 72.8777),
    ("Delhi - India Gate", 28.6129, 77.2295),
    ("Varanasi", 25.3176, 82.9739),
    ("Jaipur - Amber Fort", 26.9124, 75.7873),
    ("Thar Desert - Jaisalmer", 26.9157, 70.9083),
    ("Hampi", 15.3350, 76.4600),
    ("Sundarbans delta", 21.8000, 88.7000),
    ("Himalayas - Darjeeling", 27.0410, 88.2663),
]

WINDOW = "2024-01-01T00:00:00Z/2026-11-30T23:59:59Z"
N_OPTIONS = 3


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def tile_code(item) -> str:
    m = re.search(r"_(T\d{2}[A-Z]{3})_", item.id)
    return m.group(1) if m else "unknown"


def choose_options(items, lat: float, lon: float):
    """Return up to 3 (elevation, cloud, item) tuples from one tile."""
    point = Point(lon, lat)
    raw = []
    for item in items:
        cloud = float(item.properties.get("eo:cloud_cover", 999))
        if cloud > 2.0 or not item.geometry:
            continue
        geom = shape(item.geometry)
        if not geom.contains(point):
            continue
        raw.append((item, cloud, geom.area))

    if not raw:
        return []

    # Keep only full-tile footprints (partial swath scenes are ~1/3 the area).
    max_area = max(area for _, _, area in raw)
    candidates = []
    for item, cloud, area in raw:
        if area < 0.85 * max_area:
            continue
        elev = solar_elevation(item, lat, lon)
        candidates.append((elev, cloud, item))

    if not candidates:
        return []

    # Prefer the tile with the most strictly cloud-free scenes.
    strict = [c for c in candidates if c[1] < 0.5]
    pool = strict or candidates
    counts: dict[str, int] = {}
    for c in pool:
        code = tile_code(c[2])
        counts[code] = counts.get(code, 0) + 1
    best_tile = max(counts, key=counts.get)

    options = [c for c in candidates if tile_code(c[2]) == best_tile]
    if not options:
        options = pool
    options.sort(key=lambda c: c[0], reverse=True)

    if len(options) <= N_OPTIONS:
        return options
    idx = [round(i * (len(options) - 1) / (N_OPTIONS - 1)) for i in range(N_OPTIONS)]
    return [options[i] for i in idx]


def generate(name: str, lat: float, lon: float, skip_existing: bool):
    folder = OUT / slug(name)
    folder.mkdir(exist_ok=True)
    items = list(search_items(S2_COLLECTION, bbox=(lon - 0.05, lat - 0.05,
                                                    lon + 0.05, lat + 0.05),
                              datetime_=WINDOW, limit=500))
    options = choose_options(items, lat, lon)
    if not options:
        print(f"  !! no cloud-free options for {name}")
        return []

    print(f"  tile {tile_code(options[0][2])} | options:")
    for elev, cloud, item in options:
        print(f"    sun={elev:5.1f}deg cloud={cloud:.3f}% {item.datetime.date()}")

    saved = []
    for n, (elev, cloud, item) in enumerate(options, 1):
        date = item.datetime.strftime("%Y%m%d")
        path = folder / f"option{n}_sun{elev:04.1f}_{date}.jpg"
        if skip_existing and path.exists():
            print(f"    skip existing {path.name}")
            saved.append((name, elev, cloud, path))
            continue
        t0 = time.time()
        rgb = load_fulltile_rgb(item)
        Image.fromarray(rgb).save(path, quality=90, subsampling=0)
        mp = rgb.shape[0] * rgb.shape[1] / 1e6
        print(f"    saved {path.name}  {rgb.shape[1]}x{rgb.shape[0]} = {mp:.1f} MP"
              f"  ({time.time()-t0:.0f}s, {path.stat().st_size/1e6:.0f} MB)")
        del rgb
        saved.append((name, elev, cloud, path))
    return saved


def make_sheet(entries, path: Path, title: str):
    cols = min(3, len(entries))
    tw, th = 400, 300
    rows = (len(entries) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), "white")
    draw = ImageDraw.Draw(sheet)
    for i, entry in enumerate(entries):
        name, elev, cloud, p = entry
        img = Image.open(p)
        img.draft("RGB", (tw * 2, th * 2))  # fast JPEG decode at reduced scale
        img.thumbnail((tw - 10, th - 40))
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(img, (x + 5, y + 5))
        draw.text((x + 5, y + th - 32), f"{name}\nsun {elev:.1f}deg | cloud {cloud:.2f}%",
                  fill="black")
    sheet.save(path, quality=88)
    print(f"  sheet: {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", type=int, default=0, help="limit to first N targets (0=all)")
    ap.add_argument("--start", type=int, default=0, help="first target index (1-based)")
    ap.add_argument("--end", type=int, default=0, help="last target index (1-based, inclusive)")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--no-master", action="store_true")
    args = ap.parse_args()

    targets = TARGETS[: args.targets] if args.targets else TARGETS
    if args.start:
        targets = targets[args.start - 1:]
    if args.end:
        targets = targets[: args.end - args.start + 1] if args.start else targets[: args.end]
    all_entries = []
    for i, (name, lat, lon) in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {name}")
        entries = generate(name, lat, lon, args.skip_existing)
        if not entries:
            continue
        all_entries.append(entries[0])  # highest-sun option for master sheet
        make_sheet(entries, OUT / slug(name) / "options.jpg", name)

    if all_entries and not args.no_master:
        make_sheet(all_entries, OUT / "master_overview.jpg", "Best-sun option per target")
    print("\nDone.")


if __name__ == "__main__":
    main()
