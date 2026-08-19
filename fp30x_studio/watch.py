"""Is the capture actually receiving anything right now?

The distinction this module exists for: a live capture *process* and a
structurally clean *file* both stay green while the piano sits silent, because
neither of them can see the radio. The only honest liveness signal is "how long
since a message landed", so that is what the light is keyed on.

    python -m fp30x_studio.watch              # one status line, exit
    python -m fp30x_studio.watch --follow     # refresh until Ctrl-C

Exit status is 0 green, 1 amber, 2 red, so it composes with shell tests.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

TAKES = Path.home() / "Music" / "FP-30X Studio" / "takes"
CAPTURE = Path(__file__).resolve().parent.parent / "native" / "build" / "fp30x-capture"

#: Silence beyond this is amber: plausible if he is between pieces.
AMBER_S = 20.0
#: Silence beyond this is red. The FP-30X's factory Auto Off is 30 minutes,
#: and it powers down mid-session without announcing it.
RED_S = 120.0

GREEN, AMBER, RED = "\033[32m", "\033[33m", "\033[31m"
DIM, BOLD, OFF = "\033[2m", "\033[1m", "\033[0m"


def newest_take() -> Path | None:
    takes = sorted(TAKES.glob("*.fp30"), key=lambda p: p.stat().st_mtime)
    return takes[-1] if takes else None


def capture_running() -> bool:
    return subprocess.run(["pgrep", "-f", "fp30x-capture -o"],
                          capture_output=True).returncode == 0


def source_present() -> bool:
    """Is the piano still enumerable by CoreMIDI?

    Note this stays true after the FP-30X powers itself off -- the Bluetooth
    pairing outlives the instrument -- which is exactly why it cannot be the
    liveness signal on its own.
    """
    if not CAPTURE.exists():
        return False
    r = subprocess.run([str(CAPTURE), "-l"], capture_output=True, text=True)
    return "FP-30X" in r.stdout


def status(path: Path) -> dict:
    from fp30x_studio.pipeline.store import TakeStore

    st = TakeStore(str(path))
    st.ingest()
    rows = [dict(r) for r in st.intervals()]
    msgs = [dict(r) for r in st.messages()]

    now_ns = time.time_ns()
    last_ns = msgs[-1]["ns"] if msgs else None

    # The capture stamps with mach_absolute_time, not wall clock, so the two
    # are not comparable. File mtime is the honest cross-clock proxy: the
    # writer fsyncs every 0.25 s, so it tracks arrivals closely.
    quiet = time.time() - path.stat().st_mtime

    span = (msgs[-1]["ns"] - msgs[0]["ns"]) / 1e9 if len(msgs) > 1 else 0.0
    recent = [r for r in rows if last_ns and (last_ns - r["ns_on"]) / 1e9 <= 30]

    defects = {}
    if hasattr(st, "defect_counts"):
        defects = {k: v for k, v in dict(st.defect_counts()).items() if v}

    return {
        "path": path, "messages": len(msgs), "notes": len(rows),
        "span_s": span, "quiet_s": quiet, "recent30": len(recent),
        "defects": defects, "running": capture_running(),
        "source": source_present(),
    }


def light(s: dict) -> tuple[str, str, int]:
    if not s["running"]:
        return RED, "NOT CAPTURING", 2
    if not s["source"]:
        return RED, "PIANO NOT ENUMERABLE", 2
    if s["defects"]:
        return RED, f"DEFECTS {s['defects']}", 2
    if s["quiet_s"] > RED_S:
        return RED, f"SILENT {s['quiet_s'] / 60:.1f} min -- check Auto Off", 2
    if s["quiet_s"] > AMBER_S:
        return AMBER, f"quiet {s['quiet_s']:.0f}s", 1
    return GREEN, "receiving", 0


def line(s: dict) -> str:
    col, msg, _ = light(s)
    return (f"{col}{BOLD}●{OFF} {col}{msg}{OFF}  "
            f"{s['notes']} notes / {s['span_s'] / 60:.1f} min  "
            f"{DIM}last 30s: {s['recent30']}  {s['path'].name}{OFF}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("take", nargs="?", help="take name; default is newest")
    ap.add_argument("--follow", "-f", action="store_true")
    ap.add_argument("--interval", "-n", type=float, default=3.0)
    a = ap.parse_args()

    p = TAKES / f"{a.take}.fp30" if a.take else newest_take()
    if p is None or not p.exists():
        print(f"{RED}● NO TAKE{OFF}  nothing in {TAKES}", file=sys.stderr)
        return 2

    if not a.follow:
        s = status(p)
        print(line(s))
        return light(s)[2]

    try:
        while True:
            s = status(p)
            print("\r\033[K" + line(s), end="", flush=True)
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
