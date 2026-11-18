"""Derive a date -> cloud-percentage lookup from a location's manifest.

Run: python clouds.py --tag gandhinagar
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def frames_for(tag: str = "") -> Path:
    return ROOT / (f"frames_{tag}" if tag else "frames")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", default="gandhinagar", help="location tag -> frames_<tag>/ (empty for frames/)")
    args = ap.parse_args()
    frames = frames_for(args.tag)
    manifest = frames / "manifest.json"
    if not manifest.exists():
        print(f"error: manifest not found at {manifest}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(manifest.read_text(encoding="utf-8"))
    out = {}
    for entry in data.get("entries", []):
        items = entry.get("items", [])
        if not items:
            continue
        cloud = items[0].get("properties", {}).get("eo:cloud_cover")
        out[entry["date"]] = round(float(cloud), 2) if cloud is not None else None

    path = frames / "clouds.json"
    tmp = frames / f".tmp_clouds_{os.getpid()}.json"
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, path)
    print(f"wrote {path} ({len(out)} dates)")


if __name__ == "__main__":
    main()
