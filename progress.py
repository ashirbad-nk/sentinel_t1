"""Terminal progress monitor for a timelapse build.

Run:  .venv/bin/python progress.py --port 8767   (Ctrl-C to quit)
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def bar(done: int, total: int, width: int = 30) -> str:
    pct = done / total if total else 0
    filled = int(width * pct)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {pct:5.1%}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args()
    url = f"http://localhost:{args.port}/api/status"
    while True:
        try:
            s = json.load(urllib.request.urlopen(url))
        except Exception as exc:
            print(f"waiting for server: {exc}")
            time.sleep(5)
            continue
        print("\033[2J\033[H", end="")  # clear screen
        print("SENTINEL TIMELAPSE — LIVE PROGRESS")
        print(bar(s["done"], s["total"]))
        print(f"  done: {s['done']} / {s['total']}   "
              f"cloud-skip: {s['discarded_cloud']}   nodata-skip: {s['discarded_missing']}   "
              f"errors: {s['errors']}   remaining: {s['remaining']}")
        m, sec = divmod(s["eta_seconds"], 60)
        print(f"  ETA: ~{m}m {sec}s   "
              f"avg {s['avg_seconds_per_frame']}s/frame across {s['workers']} workers")
        print("  years: " + "  ".join(f"{y}:{c}" for y, c in sorted(s["by_year"].items())))
        print("\n  recent:")
        for line in s["recent"][-8:]:
            print("   ", line[:110])
        print("\n  (Ctrl-C to quit — downloading continues in background)")
        time.sleep(10)


if __name__ == "__main__":
    main()
