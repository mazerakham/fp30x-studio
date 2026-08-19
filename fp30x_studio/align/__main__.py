"""Regenerate ``docs/warp-op55.html``.

    .venv/bin/python -m fp30x_studio.align

The page is the deliverable and this is the only thing that makes it. There is
no scratch script and no hand-edited HTML: every number, every plot and every
note event on the page comes out of :mod:`fp30x_studio.align.page` reading the
take through the pipeline.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .page import OUT, TAKE, build


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m fp30x_studio.align",
                                description=__doc__.splitlines()[0])
    p.add_argument("-o", "--out", type=Path, default=OUT,
                   help=f"output HTML (default: {OUT})")
    p.add_argument("-t", "--take", type=Path, default=TAKE,
                   help=f"take to align (default: {TAKE})")
    a = p.parse_args(argv)
    out = build(a.out, a.take)
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
