"""The thing he leaves running while he plays.

    $ python -m fp30x_studio.identify watch

and then nothing. No progress bar, no "listening...", no running commentary of
near-misses -- he is at the keyboard with the terminal in the corner of one eye,
and every line printed before there is an answer is a line that pulls his eyes
off the music for nothing. When the arithmetic is sure, one purple line:

    \U0001f7e3 Scott Joplin, The Strenuous Life [26 notes, 6 s, conf 0.86]

Then silence again until he starts something else. A silence longer than six
seconds ends the piece, and the next one is judged on its own evidence.

Subcommands
-----------
``watch``   the live loop. Follows the newest take in the takes directory, so
            starting a fresh recording needs no restart.
``check``   replay a finished take offline and print every verdict, with the
            note count each one needed. This is the regression harness in a
            command.
``cost``    replay a take *through the live path* -- writing the file in
            increments and ticking as the runner would -- and report measured
            CPU per tick and average load. Numbers, not adjectives.
``corpus``  what corpus was loaded and how the index came out.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import corpus as _corpus
from .corpus import load_corpus
from .identifier import Identifier, replay_all

__all__ = ["main", "watch", "newest_take", "TAKES"]

TAKES = Path("~/Music/FP-30X Studio/takes").expanduser()

DIM = "\033[2m"
OFF = "\033[0m"


def newest_take(directory: Path = TAKES) -> Path | None:
    """The most recently modified ``.fp30``. The one he is recording into."""
    try:
        takes = sorted(directory.glob("*.fp30"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return takes[-1] if takes else None


def watch(take: Path | None = None, *, every: float = 3.0,
          directory: Path = TAKES, corpus=None, colour: bool = True,
          min_confidence: float | None = None, follow: bool = True,
          stats: bool = False, limit: float | None = None,
          out=sys.stdout) -> int:
    """Poll, stay quiet, and speak once. Returns the number of verdicts given."""
    kw = {} if min_confidence is None else {"min_confidence": min_confidence}
    ident = Identifier(corpus if corpus is not None else load_corpus(), **kw)
    said = 0
    started = time.perf_counter()
    current = take
    where = directory if follow and take is None else current
    note = (f" | CORPUS NOT LOADED ({_corpus.LAST_CORPUS_ERROR})"
            if _corpus.LAST_CORPUS_ERROR and corpus is None else "")
    banner = (f"watching {where} | {len(ident.index)} themes{note} "
              f"| silent until sure | ctrl-c to stop")
    banner = f"{DIM}{banner}{OFF}" if colour else banner
    print(banner, file=out, flush=True)
    try:
        while True:
            if follow and take is None:
                newest = newest_take(directory)
                if newest is not None and newest != current:
                    current = newest
            if current is not None and Path(current).exists():
                v = ident.update(current)
                if v is not None:
                    print(v.line_text(colour=colour), file=out, flush=True)
                    said += 1
            if limit is not None and time.perf_counter() - started >= limit:
                break
            time.sleep(every)
    except KeyboardInterrupt:
        pass
    finally:
        if stats:
            s = ident.stats
            print(f"{DIM}{s.line()} | average load "
                  f"{100 * s.load:.4f}% of one core{OFF}", file=out, flush=True)
        ident.close()
    return said


def _cmd_check(args) -> int:
    corpus = load_corpus()
    verdicts, ident = replay_all(args.take, corpus, chunk=args.chunk)
    if not verdicts:
        print("no verdict: not enough evidence anywhere in this take")
    for v in verdicts:
        print(f"segment {v.segment}: {v.line_text(colour=not args.no_colour)}")
        print(f"    {v.hits} aligned hits on the {v.line} line, "
              f"density {v.density}, margin {v.margin}"
              + (f", runner-up {v.runner_up}" if v.runner_up else "")
              + f", corpus {v.corpus_size} themes")
    print(f"{ident.stats.line()}")
    return 0


def _cmd_cost(args) -> int:
    """Measure the live path, by actually running it against a growing file.

    Not a microbenchmark of the matcher: the file is appended to in the same
    increments the capture tool would produce between ticks, and every tick
    pays for the ``stat``, the incremental ingest, the pairing, the clustering
    and the votes. That is the number that matters, because the ingest is the
    expensive half and a benchmark that skipped it would flatter the result.
    """
    import tempfile

    src = Path(args.take).expanduser()
    raw = src.read_bytes()

    probe = Identifier(load_corpus())
    probe.attach(src)
    probe.store.ingest()  # type: ignore[union-attr]
    duration = probe.store.duration  # type: ignore[union-attr]
    probe.close()

    ticks = max(1, int(duration / args.every))
    step = max(1, len(raw) // ticks)
    said = 0
    cpu = 0.0

    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / src.name
        ident = Identifier(load_corpus())
        for i in range(0, len(raw), step):
            with dst.open("ab") as fh:
                fh.write(raw[i:i + step])
            t = time.process_time()
            v = ident.update(dst)
            cpu += time.process_time() - t
            if v is not None:
                said += 1
        n = ident.stats.ticks
        ident.close()

    print(f"take            {src.name}")
    print(f"music duration  {duration:.1f} s")
    print(f"tick interval   {args.every:.1f} s -> {n} ticks")
    print(f"CPU total       {cpu * 1000:.1f} ms")
    print(f"CPU per tick    {1000 * cpu / max(1, n):.3f} ms")
    print(f"average load    {100 * cpu / max(1e-9, duration):.4f}% of one core, "
          f"against the music's own duration")
    print(f"verdicts        {said}")
    return 0


def _cmd_corpus(args) -> int:
    from .corpus import STUB
    from .matcher import ThemeIndex

    c = STUB if getattr(args, "stub", False) else load_corpus()
    idx = ThemeIndex(c)
    print(f"{len(c)} themes, {idx.n_hashes} indexed hashes, "
          f"{idx.dropped} dropped as stop-hashes")
    for t in c.themes():
        print(f"  {t.id:34s} {len(t.pitches):3d} notes  {t.label}")
        if args.sources and t.source:
            print(f"      {DIM}{t.source}{OFF}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m fp30x_studio.identify",
        description="Name the piece being played, from the live MIDI stream.")
    sub = p.add_subparsers(dest="cmd")

    w = sub.add_parser("watch", help="the live loop; silent until sure")
    w.add_argument("take", nargs="?", default=None,
                   help="a .fp30 to watch; default is the newest in the "
                        "takes directory, re-checked every tick")
    w.add_argument("--every", type=float, default=3.0, help="seconds per tick")
    w.add_argument("--dir", default=str(TAKES), help="takes directory")
    w.add_argument("--min-confidence", type=float, default=None)
    w.add_argument("--no-colour", action="store_true")
    w.add_argument("--stats", action="store_true",
                   help="print the CPU cost when you stop it")

    c = sub.add_parser("check", help="replay a finished take, print verdicts")
    c.add_argument("take")
    c.add_argument("--chunk", type=int, default=4,
                   help="notes per simulated tick; smaller is a tighter "
                        "measurement of how few notes were needed")
    c.add_argument("--no-colour", action="store_true")

    k = sub.add_parser("cost", help="measure the live path against a growing file")
    k.add_argument("take")
    k.add_argument("--every", type=float, default=3.0)

    s = sub.add_parser("corpus", help="what corpus is loaded")
    s.add_argument("--sources", action="store_true")
    s.add_argument("--stub", action="store_true",
                   help="the built-in themes only; skips the corpus database")

    args = p.parse_args(argv)
    if args.cmd == "check":
        return _cmd_check(args)
    if args.cmd == "cost":
        return _cmd_cost(args)
    if args.cmd == "corpus":
        return _cmd_corpus(args)
    if args.cmd in (None, "watch"):
        take = getattr(args, "take", None)
        watch(Path(take).expanduser() if take else None,
              every=getattr(args, "every", 3.0),
              directory=Path(getattr(args, "dir", str(TAKES))).expanduser(),
              colour=not getattr(args, "no_colour", False),
              min_confidence=getattr(args, "min_confidence", None),
              stats=getattr(args, "stats", False))
        return 0
    p.print_help()
    return 2
