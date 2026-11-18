"""Tiny HTTP server: live playback + progress for a timelapse build.

Run:  python serve.py --tag gandhinagar --port 8767
Open: http://127.0.0.1:8767/player.html
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(OK|DISCARD_CLOUD|DISCARD|ERROR)\b")
FRAME_JPG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.jpg$")


def frames_for(tag: str = "") -> Path:
    return ROOT / (f"frames_{tag}" if tag else "frames")


def resolve_frame_path(frames: Path, req_path: str) -> tuple[int, Path | None]:
    """Safely resolve a /frames/<file> request strictly inside `frames`.

    Returns (http_status, path_or_none). Prevents path traversal outside `frames`.
    """
    raw = unquote(req_path.split("?", 1)[0])
    if not raw.startswith("/frames/"):
        return 404, None
    rel = raw[len("/frames/") :]
    if not rel or rel.startswith("/") or "\\" in rel:
        return 403, None

    frames_resolved = frames.resolve()
    try:
        candidate = (frames_resolved / rel).resolve()
    except OSError:
        return 403, None

    if not candidate.is_relative_to(frames_resolved) or candidate == frames_resolved:
        return 403, None
    if not candidate.is_file():
        return 404, None
    return 200, candidate


def status(frames: Path) -> dict:
    total = 0
    manifest = frames / "manifest.json"
    if manifest.exists():
        try:
            total = len(json.loads(manifest.read_text(encoding="utf-8")).get("entries", []))
        except (OSError, json.JSONDecodeError):
            total = 0

    names = sorted(p.stem for p in frames.glob("????-??-??.jpg"))
    done_set = set(names)
    logs = sorted(frames.glob("fetch_*.log"), key=lambda p: p.stat().st_mtime)

    ok_times: list[int] = []
    # Track latest outcome per date so restarted shards never double-count.
    date_outcome: dict[str, str] = {}
    recent: list[str] = []

    for log in logs:
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            m = DATE_RE.match(line)
            if m:
                date_str, kind = m.group(1), m.group(2)
                date_outcome[date_str] = kind
                if kind == "OK":
                    tm = re.search(r"\((\d+)s\)", line)
                    if tm:
                        ok_times.append(int(tm.group(1)))
            recent.append(line)
    recent = recent[-40:]

    # Completed JPEGs on disk always supersede any historical discard/error log line.
    discarded_cloud = sum(
        1 for d, k in date_outcome.items() if d not in done_set and k == "DISCARD_CLOUD"
    )
    discarded_missing = sum(
        1 for d, k in date_outcome.items() if d not in done_set and k == "DISCARD"
    )
    errors = sum(1 for d, k in date_outcome.items() if d not in done_set and k == "ERROR")

    avg = sum(ok_times) / len(ok_times) if ok_times else 12.0
    nworkers = max(1, len(logs))
    discarded = discarded_cloud + discarded_missing
    remaining = max(0, total - len(names) - discarded - errors)
    eta_s = int(remaining * avg / nworkers)

    by_year: dict[str, int] = {}
    for n in names:
        by_year[n[:4]] = by_year.get(n[:4], 0) + 1

    return {
        "total": total,
        "done": len(names),
        "discarded": discarded,
        "discarded_cloud": discarded_cloud,
        "discarded_missing": discarded_missing,
        "errors": errors,
        "remaining": remaining,
        "eta_seconds": eta_s,
        "avg_seconds_per_frame": round(avg, 1),
        "workers": nworkers,
        "by_year": by_year,
        "frames": names,
        "recent": recent,
        "updated": time.strftime("%H:%M:%S"),
    }


def make_handler(frames: Path):
    player_path = ROOT / "player.html"

    class Handler(BaseHTTPRequestHandler):
        def _send_bytes(self, code: int, body: bytes, content_type: str, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            clean_path = self.path.split("?", 1)[0]

            if clean_path == "/api/status":
                body = json.dumps(status(frames)).encode("utf-8")
                self._send_bytes(200, body, "application/json", "no-store")
                return

            if clean_path in ("/", "/player.html"):
                if not player_path.is_file():
                    self.send_error(404)
                    return
                self._send_bytes(200, player_path.read_bytes(), "text/html; charset=utf-8", "no-cache")
                return

            if clean_path.startswith("/frames/"):
                code, target = resolve_frame_path(frames, self.path)
                if code != 200 or target is None:
                    self.send_error(code)
                    return
                ctype, _ = mimetypes.guess_type(target.name)
                cache = (
                    "public, max-age=31536000, immutable"
                    if FRAME_JPG_RE.match(target.name)
                    else "no-store"
                )
                self._send_bytes(200, target.read_bytes(), ctype or "application/octet-stream", cache)
                return

            # Explicitly refuse serving arbitrary repo files (e.g. .git, .py, configs).
            self.send_error(404)

        def log_message(self, fmt, *args):
            pass

    return Handler


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", default="gandhinagar", help="location tag -> frames_<tag>/")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args()
    frames = frames_for(args.tag)
    frames.mkdir(exist_ok=True)
    print(
        f"Serving tag={args.tag} frames={frames} on http://{args.host}:{args.port}/player.html",
        flush=True,
    )
    ThreadingHTTPServer((args.host, args.port), make_handler(frames)).serve_forever()
