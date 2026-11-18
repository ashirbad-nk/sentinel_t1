"""Derive a date -> cloud-percentage lookup from a location's manifest.

Run: .venv/bin/python clouds.py --tag gandhinagar
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="gandhinagar")
    args = ap.parse_args()
    frames = ROOT / f"frames_{args.tag}"

    data = json.loads((frames / "manifest.json").read_text())
    out = {}
    for entry in data["entries"]:
        items = entry["items"]
        if not items:
            continue
        cloud = items[0].get("properties", {}).get("eo:cloud_cover")
        out[entry["date"]] = round(float(cloud), 2) if cloud is not None else None
    path = frames / "clouds.json"
    path.write_text(json.dumps(out))
    print(f"wrote {path} ({len(out)} dates)")


if __name__ == "__main__":
    main()
