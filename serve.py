"""Tiny HTTP server: live playback + progress for a timelapse build.

Run:  .venv/bin/python serve.py --tag gandhinagar --port 8767
Open: http://localhost:8767/player.html
"""
from __future__ import annotations

import argparse
import json
import re
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def status(frames: Path) -> dict:
    total = 0
    manifest = frames / "manifest.json"
    if manifest.exists():
        total = len(json.loads(manifest.read_text()).get("entries", []))

    names = sorted(p.stem for p in frames.glob("????-??-??.jpg"))
    logs = sorted(frames.glob("fetch_*.log"), key=lambda p: p.stat().st_mtime)

    ok_times, discarded_cloud, discarded_missing, errors, recent = [], 0, 0, [], []
    for log in logs:
        for line in log.read_text(errors="replace").splitlines():
            if " OK " in line and "(" in line:
                m = re.search(r"\((\d+)s\)", line)
                if m:
                    ok_times.append(int(m.group(1)))
            elif "DISCARD_CLOUD" in line:
                discarded_cloud += 1
            elif "DISCARD" in line:
                discarded_missing += 1
            elif "ERROR" in line:
                errors.append(line)
            recent.append(line)
    recent = recent[-40:]

    avg = sum(ok_times) / len(ok_times) if ok_times else 12.0
    nworkers = max(1, len(logs))
    discarded = discarded_cloud + discarded_missing
    remaining = max(0, total - len(names) - discarded - len(errors))
    eta_s = int(remaining * avg / nworkers)

    by_year: dict[str, int] = {}
    for n in names:
        by_year[n[:4]] = by_year.get(n[:4], 0) + 1

    return {
        "total": total, "done": len(names),
        "discarded": discarded, "discarded_cloud": discarded_cloud,
        "discarded_missing": discarded_missing, "errors": len(errors),
        "remaining": remaining, "eta_seconds": eta_s,
        "avg_seconds_per_frame": round(avg, 1), "workers": nworkers,
        "by_year": by_year, "frames": names, "recent": recent,
        "updated": time.strftime("%H:%M:%S"),
    }


def make_handler(frames: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def translate_path(self, path: str) -> str:
            path = path.split("?", 1)[0]  # strip cache-buster query string
            if path.startswith("/frames/"):
                return str(frames / path[len("/frames/"):])
            return super().translate_path(path)

        def do_GET(self):
            if self.path.startswith("/api/status"):
                body = json.dumps(status(frames)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/":
                self.path = "/player.html"
            super().do_GET()

        def log_message(self, fmt, *args):
            pass

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="gandhinagar")
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args()
    frames = ROOT / f"frames_{args.tag}"
    frames.mkdir(exist_ok=True)
    print(f"Serving tag={args.tag} frames={frames} on http://localhost:{args.port}/player.html",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(frames)).serve_forever()
