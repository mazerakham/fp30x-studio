"""Serve docs/ on loopback, regenerating takes.json from disk on every request.

This is what makes the page's Refresh button real: a plain `http.server` would
hand back whatever takes.json happened to be on disk, so a take recorded after
the last build would never appear. Here every GET of takes.json re-scans
~/Music/FP-30X Studio/takes/ first, so a take that is still being written shows
up with whatever has landed so far.

    ~/workspace/audio/.venv/bin/python serve_workbench.py
    -> http://127.0.0.1:8791/timbre-workbench.html
"""
from __future__ import annotations

import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE
PY = "/Users/jake/workspace/audio/.venv/bin/python"
BUILDER = HERE / "build_takes.py"
PORT = 8791

_lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0].rstrip("/").endswith("takes.json"):
            with _lock:
                try:
                    subprocess.run(
                        [PY, str(BUILDER), "/dev/null"],
                        check=True, capture_output=True, timeout=120,
                    )
                except Exception as exc:  # serve the stale file rather than 500
                    sys.stderr.write(f"regen failed: {exc}\n")
        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "takes.json" in (args[0] if args else ""):
            sys.stderr.write("regenerated takes.json\n")


def main() -> None:
    handler = partial(Handler, directory=str(DOCS))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"serving {DOCS} on http://127.0.0.1:{PORT}/timbre-workbench.html")
    print("takes.json is regenerated from disk on every request. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
