"""Generate 10 cloudless ~30 MP PNG/JPEG images of iconic Indian regions.

Each image uses the minimum number of near-cloudless Sentinel-2 L2A scenes
(dry-season window) clipped to a 60 x 50 km AOI, giving ~6000 x 5000 px at
native 10 m resolution.

Run:
    python make_cloudless.py                       # JPEG gallery (default)
    python make_cloudless.py --format png          # lossless PNG instead
    python make_cloudless.py --regions 2           # only first 2 (test)
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw

from sentinel_tool.config import S2_COLLECTION
from sentinel_tool.mosaic import composite, load_scene_rgb, pick_scenes, stretch
from sentinel_tool.stac import search_items

OUT = ROOT / "outputs" / "gallery"
OUT.mkdir(parents=True, exist_ok=True)

# name, latitude, longitude -- 60 x 50 km AOIs (~30 MP at 10 m)
REGIONS = [
    ("Agra - Taj Mahal", 27.1751, 78.0421),
    ("Rann of Kutch", 23.8000, 69.5000),
    ("Mumbai coast", 19.0760, 72.8777),
    ("Jaipur - Amber Fort", 26.9124, 75.7873),
    ("Golden Temple, Amritsar", 31.6200, 74.8765),
    ("Varanasi", 25.3176, 82.9739),
    ("Delhi - India Gate", 28.6129, 77.2295),
    ("Hampi", 15.3350, 76.4600),
    ("Mysore Palace", 12.3052, 76.6552),
    ("Thar Desert, Jaisalmer", 26.9157, 70.9083),
]

WINDOW = "2025-11-01T00:00:00Z/2026-02-28T23:59:59Z"
W_KM, H_KM = 60, 50  # -> 6000 x 5000 px at 10 m = 30 MP


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def bbox_km(lon: float, lat: float) -> tuple[float, float, float, float]:
    """60x50 km box centred on (lon, lat), in EPSG:4326."""
    dlat = H_KM / 111.32
    dlon = W_KM / (111.32 * math.cos(math.radians(lat)))
    return (lon - dlon / 2, lat - dlat / 2, lon + dlon / 2, lat + dlat / 2)


def generate_one(name: str, lat: float, lon: float, fmt: str):
    slug = slugify(name)
    bbox = bbox_km(lon, lat)
    items = list(search_items(S2_COLLECTION, bbox=bbox, datetime_=WINDOW))
    if not items:
        print(f"  !! no scenes for {name}")
        return None

    scenes, coverage = pick_scenes(items, bbox)
    if not scenes:
        print(f"  !! no usable scenes for {name}")
        return None
    clouds = [i.properties.get("eo:cloud_cover", 999) for i in scenes]
    print(f"  {len(scenes)} scene(s), cloud={[round(float(c), 3) for c in clouds]}%, coverage={coverage:.3f}")

    layers = [load_scene_rgb(item, bbox, resolution=10) for item in scenes]
    rgb = composite(layers)
    img = Image.fromarray(stretch(rgb, gamma=0.95))

    path = OUT / f"{slug}.{fmt}"
    if fmt == "jpg":
        img.save(path, quality=92, subsampling=0)
    else:
        img.save(path)
    print(f"  saved {path.relative_to(ROOT)}  {img.size[0]}x{img.size[1]} = {img.size[0]*img.size[1]/1e6:.1f} MP")

    # Immediately downscale to a small thumbnail so the 30 MP image is freed.
    thumb = img.copy()
    thumb.thumbnail((360, 300))
    img.close()
    del img, rgb, layers
    return path, thumb


def contact_sheet(entries, fmt: str):
    """Small labelled overview of all generated images."""
    cols = min(5, len(entries))
    rows = math.ceil(len(entries) / cols)
    tw, th = 380, 330
    sheet = Image.new("RGB", (cols * tw, rows * th), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (name, (_, thumb)) in enumerate(entries):
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(thumb, (x + 10, y + 10))
        draw.text((x + 10, y + 305), name, fill="black")
    path = OUT / f"contact_sheet.{fmt}"
    if fmt == "jpg":
        sheet.save(path, quality=90)
    else:
        sheet.save(path)
    print(f"\nContact sheet: {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--format", choices=["jpg", "png"], default="jpg")
    ap.add_argument("--regions", type=int, default=0, help="limit to first N regions (0=all)")
    args = ap.parse_args()

    regions = REGIONS[: args.regions] if args.regions > 0 else REGIONS
    entries = []
    for i, (name, lat, lon) in enumerate(regions, 1):
        print(f"[{i}/{len(regions)}] {name}")
        result = generate_one(name, lat, lon, args.format)
        if result:
            entries.append((name, result))

    if entries:
        contact_sheet(entries, args.format)
    print("\nDone.")


if __name__ == "__main__":
    main()
