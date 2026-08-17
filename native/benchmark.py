#!/usr/bin/env python3
"""Measure the timing resolution of both capture front ends against one stimulus.

The experiment
--------------
``fp30x-synth`` publishes a virtual CoreMIDI source and emits N messages spaced
by a requested interval. It stamps each message with ``mach_absolute_time()``
read immediately before the send and logs that stamp, so the ground truth is
when the message *actually* left, not when it was scheduled to. Sender jitter
therefore cannot flatter either capture path.

While it sends, both front ends record the same stream:

  * ``fp30x-capture`` (C, CoreMIDI callback, packet timestamps)
  * the ``iter_pending`` + ``time.sleep(0.002)`` loop from
    ``fp30x_studio.core.Capture``

Each is then scored on **inter-message intervals**, which need no clock
alignment between the three processes: for message i, compare the recorded
delta t_i - t_{i-1} against the true delta. The error distribution of those
deltas is exactly "how finely can this path resolve two events in time".

Why every run is its own process
--------------------------------
CoreMIDI virtual endpoints and rtmidi's client state do not survive being torn
down and rebuilt inside one long-lived process -- a second run in the same
interpreter sees ``get_input_names()`` return junk. So each interval gets fresh
processes and a source name unique to the run, which is also what stops a
lingering endpoint from an earlier run being connected to by mistake.

Scope
-----
This is synthetic input. It measures the capture path, which is what was
broken. It does not measure the piano, the Bluetooth link, or the key action.
The real-hardware test is stated at the end of the printed report.

usage: benchmark.py [-i USEC ...] [-c COUNT]
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
sys.path.insert(0, str(HERE.parent))

from fp30x_studio import rawcapture  # noqa: E402


# --------------------------------------------------------------------------
# Child mode: the existing polling capture, run in its own process.
#
# Deliberately a copy of the loop in ``fp30x_studio.core.Capture.run`` rather
# than an import, because that class is bound to a device-name match and a UI
# event queue. The loop shape -- ``iter_pending`` then ``sleep(0.002)`` -- is
# what is under test and it is reproduced here exactly.
# --------------------------------------------------------------------------
def child_python_capture(source: str, out_path: str, seconds: float) -> int:
    import mido

    port = None
    deadline = time.time() + 15.0
    while time.time() < deadline:
        try:
            names = mido.get_input_names() or []
        except Exception:
            names = []
        hit = [n for n in names if source in n]
        if hit:
            port = hit[0]
            break
        time.sleep(0.02)
    if port is None:
        Path(out_path).write_text("")
        return 1

    rows = []
    with mido.open_input(port) as inp:
        end = time.time() + seconds
        while time.time() < end:
            for msg in inp.iter_pending():
                rows.append((time.time(), msg.bytes()))
            time.sleep(0.002)

    with open(out_path, "w") as fh:
        for t, by in rows:
            fh.write(f"{t:.9f} " + " ".join(f"{b:02X}" for b in by) + "\n")
    return 0


def read_python_log(path: Path) -> list[float]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts:
            out.append(float(parts[0]))
    return out


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def deltas(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]


def score(name: str, recorded: list[float], truth: list[float]) -> dict:
    """Compare recorded inter-message deltas against the true ones, in ms."""
    n = min(len(recorded), len(truth))
    if n < 3:
        return {"name": name, "n": n, "usable": False}
    rd = deltas(recorded[:n])
    td = deltas(truth[:n])
    err = sorted(abs(r - t) * 1e3 for r, t in zip(rd, td))  # ms
    return {
        "name": name,
        "n": n,
        "usable": True,
        "median_err_ms": statistics.median(err),
        "p95_err_ms": err[int(0.95 * (len(err) - 1))],
        "max_err_ms": err[-1],
        # The signature of a poll loop: messages that arrived during one sleep
        # are all read in the same pass, so their recorded deltas collapse to
        # the cost of a time.time() call and the events are reported as
        # simultaneous when they were not.
        "collapsed": sum(1 for d in rd if d < 20e-6),
        "n_deltas": len(rd),
        "median_delta_us": statistics.median(rd) * 1e6,
        "median_true_delta_us": statistics.median(td) * 1e6,
    }


def run_one(interval_us: float, count: int, tmp: Path, tag: str) -> dict:
    source = f"FP30X-Bench-{os.getpid()}-{tag}"
    truth_f = tmp / f"truth_{tag}.fp30x"
    cap_f = tmp / f"cap_{tag}.fp30x"
    py_f = tmp / f"py_{tag}.txt"
    for f in (truth_f, cap_f, py_f):
        f.unlink(missing_ok=True)

    send_s = (interval_us * count) / 1e6
    warmup = 3.0
    listen_s = warmup + send_s + 1.5

    synth = subprocess.Popen(
        [str(BUILD / "fp30x-synth"), "-t", str(truth_f), "-n", source,
         "-i", str(interval_us), "-c", str(count), "-w", str(warmup)],
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.6)  # let CoreMIDI publish the endpoint

    py = subprocess.Popen(
        [sys.executable, str(HERE / "benchmark.py"), "--child",
         source, str(py_f), str(listen_s)],
        stderr=subprocess.DEVNULL,
    )
    cap = subprocess.Popen(
        [str(BUILD / "fp30x-capture"), "-o", str(cap_f), "-s", source,
         "-d", str(listen_s), "-q"],
        stderr=subprocess.DEVNULL,
    )

    synth.wait()
    cap.wait()
    py.wait()
    time.sleep(0.4)  # let CoreMIDI settle before the next run

    truth = rawcapture.read(truth_f)
    got = rawcapture.read(cap_f)

    truth_t = [r.ns / 1e9 for r in truth.records]
    c_t = [r.ns / 1e9 for r in got.records]
    p_t = read_python_log(py_f)

    return {
        "interval_us": interval_us,
        "sent": len(truth_t),
        "c_received": len(c_t),
        "py_received": len(p_t),
        "c_dropped": got.n_dropped,
        "c_ts_zero": got.n_ts_zero,
        "exact_match": (len(c_t) == len(truth_t) and len(c_t) > 0
                        and all(a.ns == b.ns for a, b in
                                zip(got.records, truth.records))),
        "c": score("C / CoreMIDI callback", c_t, truth_t),
        "py": score("Python / poll loop", p_t, truth_t),
    }


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        return child_python_capture(sys.argv[2], sys.argv[3], float(sys.argv[4]))

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-i", "--intervals", type=float, nargs="+",
                    default=[5000, 2000, 1000, 500, 200, 100])
    ap.add_argument("-c", "--count", type=int, default=200)
    ap.add_argument("-o", "--out", type=Path, default=Path("/tmp/fp30x-bench"))
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    if not (BUILD / "fp30x-capture").exists():
        print("build the native tools first: make -C native", file=sys.stderr)
        return 1

    rows = []
    for n, iv in enumerate(args.intervals):
        print(f"--- {iv:.0f} us nominal interval, {args.count} messages ...",
              file=sys.stderr)
        rows.append(run_one(iv, args.count, args.out, f"{n}_{int(iv)}"))

    print()
    print("SYNTHETIC INPUT -- a virtual CoreMIDI source, not the piano.")
    print("Measures the capture path only: not the key action, not Bluetooth.")
    print()
    print("Error = |recorded inter-message delta - true inter-message delta|,")
    print("scored against the sender's own emission timestamps.")
    print()
    hdr = (f"{'nominal':>9} {'sent':>5} {'C rx':>5} {'py rx':>6} "
           f"{'C median':>9} {'C p95':>8} {'C max':>8} "
           f"{'py median':>10} {'py p95':>8} {'py max':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        c, p = r["c"], r["py"]
        f = lambda d, k: f"{d[k]:.4f}" if d["usable"] else "n/a"  # noqa: E731
        print(f"{r['interval_us']:>8.0f}u {r['sent']:>5} {r['c_received']:>5} "
              f"{r['py_received']:>6} "
              f"{f(c,'median_err_ms'):>9} {f(c,'p95_err_ms'):>8} "
              f"{f(c,'max_err_ms'):>8} "
              f"{f(p,'median_err_ms'):>10} {f(p,'p95_err_ms'):>8} "
              f"{f(p,'max_err_ms'):>8}")
    print()
    print("all errors in milliseconds")
    print()
    print("Intervals the poll loop destroys outright: recorded delta under")
    print("20 us when the true delta was the nominal spacing. These events are")
    print("reported as simultaneous when they were not.")
    print()
    print(f"{'nominal':>9} {'collapsed':>16} {'py median delta':>17} "
          f"{'true median delta':>19}")
    print("-" * 65)
    for r in rows:
        p = r["py"]
        if not p["usable"]:
            continue
        print(f"{r['interval_us']:>8.0f}u {p['collapsed']:>7}/{p['n_deltas']:<8} "
              f"{p['median_delta_us']:>14.1f} us {p['median_true_delta_us']:>16.1f} us")
    print()
    for r in rows:
        print(f"{r['interval_us']:>6.0f} us | C timestamps bit-identical to "
              f"ground truth: {str(r['exact_match']):>5} | "
              f"dropped {r['c_dropped']} | "
              f"source-stamped {r['c_received'] - r['c_ts_zero']}"
              f"/{r['c_received']}")

    print()
    print("REAL-HARDWARE TEST STILL OUTSTANDING")
    print("  Needs hands on the FP-30X. Play a fast trill or a hard grace-note")
    print("  figure, capture with:")
    print("      native/build/fp30x-capture -s FP-30X -o take.fp30x")
    print("  then check `ts_zero` in the file trailer. If ts_zero == packets,")
    print("  the Bluetooth MIDI driver is handing us unstamped packets and the")
    print("  real-world gain is smaller than the number above. That question")
    print("  cannot be answered from a virtual source, because a virtual source")
    print("  always stamps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
