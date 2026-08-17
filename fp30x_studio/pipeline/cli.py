"""``python -m fp30x_studio.pipeline`` -- the terminal front end.

The command this exists for is::

    python -m fp30x_studio.pipeline report 2026-08-17-piece

asked at the keyboard, between takes, and answered in a few lines. Everything
else here is in service of that: ``report`` brings the index up to date first,
so there is never a step to remember, and the incremental ingest makes that
cheap enough to do after every take however long they get.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .. import core
from . import queries as q
from .integrity import report as integrity_report
from .provenance import append_provenance
from .store import TakeStore

__all__ = ["main", "resolve"]

_SUFFIXES = (".fp30", ".fp30x", ".mid")


def resolve(name: str) -> Path:
    """Accept a path, or a bare take name to look up in the takes directory."""
    p = Path(name).expanduser()
    if p.exists():
        return p
    d = core.takes_dir()
    for suffix in ("",) + _SUFFIXES:
        cand = d / (name + suffix)
        if cand.exists():
            return cand
    hits = sorted(x for s in _SUFFIXES for x in d.glob(f"*{name}*{s}"))
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise SystemExit(f"{name!r} matches {len(hits)} takes: "
                         + ", ".join(h.name for h in hits))
    raise SystemExit(f"no take {name!r} in {d}")


def _takes(names: list[str]) -> list[Path]:
    if names:
        return [resolve(n) for n in names]
    d = core.takes_dir()
    return sorted(x for s in _SUFFIXES for x in d.glob(f"*{s}"))


def _coerce(v: str):
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


# -- commands ---------------------------------------------------------------

def cmd_ingest(args) -> int:
    for path in _takes(args.takes):
        with TakeStore(path) as st:
            while True:
                res = st.ingest(reset=args.reset)
                print(res.line())
                if res.finished and not args.no_provenance:
                    _provenance_once(st)
                if not args.follow or res.complete:
                    break
                time.sleep(args.interval)
    return 0


def cmd_status(args) -> int:
    for path in _takes(args.takes):
        with TakeStore(path) as st:
            cp, m = st.checkpoint, st.meta
            size = path.stat().st_size
            print(f"{path.name}")
            print(f"  index         {st.index}")
            print(f"  checkpoint    {cp['byte_offset']} of {size} B "
                  f"({cp['byte_offset'] / max(1, size):.1%}), "
                  f"updated {cp['updated_utc'] or 'never'}")
            print(f"  ingested      {cp['n_packets']} packets, "
                  f"{cp['n_messages']} messages, "
                  f"{st.count('interval')} intervals, "
                  f"{st.count('defect')} defects")
            print(f"  state         "
                  f"{'complete' if cp['complete'] else 'still open'}"
                  f"{', finished' if cp['finished'] else ''}"
                  f"{', TORN' if cp['torn'] else ''}")
            print(f"  timing        {m['timing_grade']} "
                  f"({'trusted' if m['timing_trusted'] else 'NOT trusted'})")
            print(f"  accounting    {st.count('accounting')} roles for "
                  f"{st.count('message')} messages -- "
                  f"{'complete' if st.accounted else 'INCOMPLETE'}")
    return 0


def cmd_report(args) -> int:
    for path in _takes(args.takes):
        with TakeStore(path) as st:
            if not args.no_ingest:
                res = st.ingest()
                if res.finished and not args.no_provenance:
                    _provenance_once(st)
            print(integrity_report(st).text())
            print()
    return 0


def cmd_query(args) -> int:
    kwargs = {}
    for item in args.args:
        if "=" not in item:
            raise SystemExit(f"query argument {item!r} must be name=value")
        k, v = item.split("=", 1)
        kwargs[k.replace("-", "_")] = _coerce(v)
    for path in _takes(args.takes):
        with TakeStore(path) as st:
            if not args.no_ingest:
                st.ingest()
            res = q.run(st, args.name, **kwargs)
            print(f"== {path.name}: {res.name}")
            print(res.text(limit=None if args.all else args.limit))
            print()
    return 0


def cmd_queries(args) -> int:
    for name in sorted(q.QUERIES):
        item = q.QUERIES[name]
        flag = " [timing-sensitive]" if item.timing_sensitive else ""
        print(f"{name:18s} {item.doc}{flag}")
    return 0


def cmd_provenance(args) -> int:
    for path in _takes(args.takes):
        with TakeStore(path) as st:
            if not args.no_ingest:
                st.ingest()
            written = append_provenance(st, dry_run=args.dry_run, force=True)
            print(written if args.dry_run else
                  f"{path.name}: appended to {core.takes_dir() / 'PROVENANCE.md'}")
    return 0


def _provenance_once(store: TakeStore) -> None:
    """Record a take in PROVENANCE.md the first time its stream is known to end."""
    if append_provenance(store):
        print(f"  provenance    appended to "
              f"{core.takes_dir() / 'PROVENANCE.md'}")


# -- entry point ------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m fp30x_studio.pipeline",
        description="Ingest, check and query FP-30X takes.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def takes_arg(sp):
        sp.add_argument("takes", nargs="*",
                        help="take names or paths; default is every take")

    sp = sub.add_parser("ingest", help="bring a take's index up to date")
    takes_arg(sp)
    sp.add_argument("--reset", action="store_true",
                    help="discard the index and re-ingest from byte zero")
    sp.add_argument("--follow", action="store_true",
                    help="keep ingesting until the take stops cleanly")
    sp.add_argument("--interval", type=float, default=1.0,
                    help="seconds between passes under --follow")
    sp.add_argument("--no-provenance", action="store_true")
    sp.set_defaults(fn=cmd_ingest)

    sp = sub.add_parser("status", help="what the checkpoint knows")
    takes_arg(sp)
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("report", help="is this take any good?")
    takes_arg(sp)
    sp.add_argument("--no-ingest", action="store_true")
    sp.add_argument("--no-provenance", action="store_true")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("query", help="run a named measurement")
    sp.add_argument("name")
    takes_arg(sp)
    sp.add_argument("args", nargs="*", default=[],
                    help="query arguments as name=value")
    sp.add_argument("--limit", type=int, default=40)
    sp.add_argument("--all", action="store_true", help="no row limit")
    sp.add_argument("--no-ingest", action="store_true")
    sp.set_defaults(fn=cmd_query)

    sp = sub.add_parser("queries", help="list the measurements available")
    sp.set_defaults(fn=cmd_queries)

    sp = sub.add_parser("provenance", help="append this take to PROVENANCE.md")
    takes_arg(sp)
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--no-ingest", action="store_true")
    sp.set_defaults(fn=cmd_provenance)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # `query` takes both a query name and take names; argparse cannot split two
    # greedy positionals, so the take list is recovered from the trailing k=v.
    if args.cmd == "query":
        rest = list(args.takes) + list(args.args)
        args.takes = [x for x in rest if "=" not in x]
        args.args = [x for x in rest if "=" in x]
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
