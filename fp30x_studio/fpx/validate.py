"""Conformance checker for FPX bundles — ``docs/SPEC-fpx-format.md``.

    python -m fp30x_studio.fpx.validate
    python -m fp30x_studio.fpx.validate ~/Music/"FP-30X Studio"/performances -v

Prints one row per bundle, one column per checkable requirement, and exits 1 if
anything failed. It is a *conformance* tool, so it reports the specification's
numbers rather than opinions: every column is an ``FPX-n`` you can look up.

What it does not check
----------------------
Most of the specification is not decidable from a directory listing. FPX-90
(the listener's verdict outranks the machine) is a rule about who wins an argument;
§7 is a migration plan; FPX-110 (immutability) is a claim about the future.
What is checked is listed in :data:`CHECKS`, and a requirement that cannot be
decided for a given bundle reports ``-`` rather than a guess.

One check is library-wide rather than per-bundle: FPX-99, "no tool may assert a
label the listener has rejected". It is run once, over ``labels.json`` and
``PROVENANCE.md``, and printed separately.

On reading capture bytes
------------------------
FPX-29 and FPX-39 are about structure that :func:`rawcapture.scan` deliberately
discards — it folds comments into a dict, so a file with two headers looks like
a file with one. This module therefore reads comment lines itself, in
:func:`_markers`, which classifies lines as comment-or-not and never decodes a
MIDI byte. Everything that touches a record goes through ``rawcapture``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import rawcapture

__all__ = [
    "CHECKS", "Result", "BundleReport", "LibraryReport",
    "validate_bundle", "validate_tree", "check_tombstones", "main",
]

#: Extensions a raw capture may carry. FPX-2: content decides, not the suffix.
CAPTURE_SUFFIXES = (".fpx", ".fp30", ".fp30x")

#: Assets that must be referenced, never copied into a bundle. FPX-153.
FOREIGN_ASSET_SUFFIXES = (".sf2", ".sf3", ".sf4", ".dls", ".gig")

#: Derived caches that must not live inside a bundle. FPX-151 / FPX-152.
CACHE_NAMES = (".index", ".cache", ".label-cache")

#: (id, one-line description). The order is the column order.
CHECKS: tuple[tuple[str, str], ...] = (
    ("FPX-70", "fpx.json present at the bundle top level"),
    ("FPX-71", "fpx.json parses and declares fpx_manifest"),
    ("FPX-72", "README.txt present and marked generated"),
    ("FPX-74", "manifest files[] covers every file, digests match"),
    ("FPX-21", "every capture file parses, header first, no torn line"),
    ("FPX-39", "one stream per capture file (one header, <=1 trailer)"),
    ("FPX-31", "capture closed, or clip carries clip_end"),
    ("FPX-52", "the bundle carries the clip, not the whole capture"),
    ("FPX-4",  "capture keeps its own name and digest in the library"),
    ("FPX-77", "MIDI readable and accounts for every note-on in the window"),
    ("FPX-151", "no derived cache inside the bundle"),
    ("FPX-153", "no third-party asset inside the bundle"),
)

PASS, FAIL, SKIP = "pass", "fail", "skip"
_MARK = {PASS: ".", FAIL: "X", SKIP: "-"}


# --------------------------------------------------------------- results ----

@dataclass(slots=True)
class Result:
    """One requirement's verdict on one bundle."""

    req: str
    status: str
    detail: str = ""

    @property
    def mark(self) -> str:
        return _MARK[self.status]


@dataclass(slots=True)
class BundleReport:
    path: Path
    results: list[Result] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status == FAIL]

    @property
    def ok(self) -> bool:
        return not self.failures

    def by_req(self) -> dict[str, Result]:
        return {r.req: r for r in self.results}


@dataclass(slots=True)
class LibraryReport:
    """The one check that is about the library rather than a bundle."""

    checked: bool = False
    violations: list[str] = field(default_factory=list)
    note: str = ""


# ----------------------------------------------------------- filesystem ----

def default_root() -> Path:
    """Where the library lives. FPX-8, with a fallback to today's layout."""
    env = os.environ.get("FPX_HOME")
    if env:
        return Path(env).expanduser()
    new = Path("~/Music/FPX").expanduser()
    if new.is_dir():
        return new
    return Path("~/Music/FP-30X Studio").expanduser()


def captures_dir(root: Path) -> Path:
    """``captures/`` once migrated, ``takes/`` until then. FPX-7."""
    for name in ("captures", "takes"):
        d = root / name
        if d.is_dir():
            return d
    return root / "captures"


def performances_dir(root: Path) -> Path:
    return root / "performances"


def sha256_file(path: Path, *, limit: int | None = None) -> str:
    """Digest a file. ``limit`` skips files larger than it and returns ""."""
    if limit is not None and path.stat().st_size > limit:
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_files(bundle: Path) -> list[Path]:
    """Regular files directly in the bundle, excluding dot-files."""
    return sorted(p for p in bundle.iterdir()
                  if p.is_file() and not p.name.startswith("."))


def capture_files(bundle: Path) -> list[Path]:
    return [p for p in bundle_files(bundle) if p.suffix.lower() in CAPTURE_SUFFIXES]


def primary_capture(bundle: Path) -> Path | None:
    """The bundle's own capture/clip: the one that is not a mirror. FPX-11."""
    caps = capture_files(bundle)
    for p in caps:
        stem = p.name[: -len(p.suffix)]
        if not (stem.endswith("-mirror") or re.search(r"\.m\d+$", stem)
                or "-mirror-" in stem):
            return p
    return caps[0] if caps else None


# ------------------------------------------------------- capture reading ----

@dataclass(slots=True)
class Markers:
    """Structure of one capture file's comment lines, in order.

    Deliberately shallow: it classifies lines and splits on whitespace. It never
    decodes a MIDI byte, so it is not a second parser in the sense FPX-25 and
    §9.1 of the architecture design are about.
    """

    version_lines: list[str] = field(default_factory=list)
    end_lines: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    clip_window_ns: tuple[int, int] | None = None
    clipped_from: str = ""
    clip_end: bool = False
    first_line_is_version: bool = False
    n_records: int = 0
    comment_after_first_record: int = 0


_VERSION_RE = re.compile(r"^#\s*(fpx-capture|fp30x-capture)\s+v(\S+)")


def _markers(path: Path) -> Markers:
    m = Markers()
    seen_record = False
    with path.open("rb") as fh:
        for i, raw in enumerate(fh):
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not line:
                continue
            if line.startswith("#"):
                if seen_record:
                    m.comment_after_first_record += 1
                mv = _VERSION_RE.match(line)
                if mv:
                    m.version_lines.append(line)
                    if i == 0:
                        m.first_line_is_version = True
                parts = line[1:].split()
                if not parts:
                    continue
                kind = parts[0]
                m.kinds.append(kind)
                if kind == "end":
                    if len(parts) >= 2 and parts[1] == "clip":
                        m.clip_end = True
                    else:
                        m.end_lines.append(line)
                elif kind == "clip_end":
                    m.clip_end = True
                elif kind == "clip_window_ns" and len(parts) >= 3:
                    try:
                        m.clip_window_ns = (int(parts[1]), int(parts[2]))
                    except ValueError:
                        pass
                elif kind == "clipped_from" and len(parts) >= 2:
                    m.clipped_from = " ".join(parts[1:])
            else:
                seen_record = True
                m.n_records += 1
    return m


def note_ons_in_window(capture: Path, window: tuple[int, int] | None) -> int:
    """Note-ons (velocity > 0) inside ``window``, or in the whole capture."""
    cap = rawcapture.read(capture)
    lo, hi = window if window else (None, None)
    n = 0
    for rec in cap.records:
        if lo is not None and not (lo <= rec.ns <= hi):
            continue
        for msg in rawcapture.split_messages(rec.data):
            if len(msg) >= 3 and (msg[0] & 0xF0) == 0x90 and msg[2] > 0:
                n += 1
    return n


def midi_note_ons(path: Path) -> int:
    """Note-ons in a standard MIDI file. Raises if it cannot be read."""
    import mido

    mid = mido.MidiFile(path)
    return sum(1 for track in mid.tracks for msg in track
               if msg.type == "note_on" and msg.velocity > 0)


# ------------------------------------------------------------- the checks ---

def _library_captures(root: Path) -> dict[str, list[Path]]:
    """digest -> capture paths in the library. Captures are small; hash them."""
    index: dict[str, list[Path]] = {}
    cdir = captures_dir(root)
    if not cdir.is_dir():
        return index
    for p in sorted(cdir.iterdir()):
        if p.is_file() and p.suffix.lower() in CAPTURE_SUFFIXES:
            index.setdefault(sha256_file(p), []).append(p)
    return index


def _exact_window(bundle: Path, root: Path,
                  manifest: dict | None) -> tuple[tuple[int, int] | None, str]:
    """The performance's exact span, and where it came from.

    The manifest is authoritative (FPX-51). Failing that, a clip file written by
    ``segment clip`` records ``# clip_window_ns``, which is exact; the human
    ``.txt`` records mm:ss, which is rounded and therefore not usable for an
    equality check.
    """
    if manifest:
        perf = manifest.get("performance") or {}
        if isinstance(perf.get("start_ns"), int) and isinstance(perf.get("end_ns"), int):
            return (perf["start_ns"], perf["end_ns"]), "manifest"
    own = primary_capture(bundle)
    if own is not None:
        mk = _markers(own)
        if mk.clip_window_ns:
            return mk.clip_window_ns, "bundled clip"
    clips = captures_dir(root) / "clips"
    for suffix in CAPTURE_SUFFIXES:
        cand = clips / f"{bundle.name}{suffix}"
        if cand.is_file():
            mk = _markers(cand)
            if mk.clip_window_ns:
                return mk.clip_window_ns, f"library clip {cand.name}"
    return None, ""


def validate_bundle(bundle: Path, *, root: Path | None = None,
                    lib: dict[str, list[Path]] | None = None) -> BundleReport:
    """Run every checkable requirement against one bundle directory."""
    bundle = Path(bundle).expanduser()
    root = Path(root).expanduser() if root else default_root()
    rep = BundleReport(path=bundle)
    add = lambda req, status, detail="": rep.results.append(Result(req, status, detail))  # noqa: E731

    files = bundle_files(bundle)
    caps = capture_files(bundle)
    own = primary_capture(bundle)

    # -- FPX-70 / FPX-71: the manifest ------------------------------------
    manifest_path = bundle / "fpx.json"
    manifest: dict | None = None
    if manifest_path.is_file():
        add("FPX-70", PASS)
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            add("FPX-71", FAIL, f"fpx.json does not parse: {exc}")
        else:
            if not isinstance(loaded, dict):
                add("FPX-71", FAIL, "fpx.json is not an object")
            elif not isinstance(loaded.get("fpx_manifest"), int):
                add("FPX-71", FAIL, "no integer 'fpx_manifest' key")
            else:
                manifest = loaded
                missing = [k for k in ("performance", "capture", "integrity", "files")
                           if k not in loaded]
                if missing:
                    add("FPX-71", FAIL, "manifest missing " + ", ".join(missing))
                else:
                    add("FPX-71", PASS)
    else:
        add("FPX-70", FAIL, "no fpx.json — this directory is not a bundle (FPX-5)")
        add("FPX-71", SKIP, "no manifest")

    # -- FPX-72: the human file -------------------------------------------
    readme = bundle / "README.txt"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="replace")
        if "Generated from fpx.json" in text:
            add("FPX-72", PASS)
        else:
            add("FPX-72", FAIL, "README.txt is not marked as generated from fpx.json")
    else:
        legacy = [p for p in files if p.suffix == ".txt"]
        add("FPX-72", FAIL,
            f"no README.txt (found {legacy[0].name})" if legacy else "no README.txt")

    # -- FPX-74: the file table -------------------------------------------
    if manifest is None:
        add("FPX-74", SKIP, "no manifest")
    else:
        listed = {e.get("name"): e for e in manifest.get("files", [])
                  if isinstance(e, dict)}
        problems: list[str] = []
        for p in files:
            if p.name == "fpx.json":
                continue
            entry = listed.pop(p.name, None)
            if entry is None:
                problems.append(f"{p.name} not listed")
                continue
            want = entry.get("sha256")
            if want and want != sha256_file(p):
                problems.append(f"{p.name} digest mismatch")
            if entry.get("bytes") not in (None, p.stat().st_size):
                problems.append(f"{p.name} size mismatch")
        problems += [f"{n} listed but absent" for n in listed]
        add("FPX-74", FAIL if problems else PASS, "; ".join(problems[:4]))

    # -- capture-level checks ---------------------------------------------
    if not caps:
        for req in ("FPX-21", "FPX-39", "FPX-31", "FPX-52", "FPX-4"):
            add(req, FAIL, "bundle contains no capture or clip")
    else:
        marks = {p: _markers(p) for p in caps}

        bad = []
        for p, mk in marks.items():
            if not mk.version_lines:
                bad.append(f"{p.name}: no version line")
            elif not mk.first_line_is_version:
                bad.append(f"{p.name}: version line is not first")
            scan = rawcapture.scan(p, whole=True)
            if scan.torn:
                bad.append(f"{p.name}: torn line at byte {scan.next_offset}")
            if not scan.records and mk.n_records:
                bad.append(f"{p.name}: records present but none parsed")
        add("FPX-21", FAIL if bad else PASS, "; ".join(bad[:3]))

        bad = []
        for p, mk in marks.items():
            if len(mk.version_lines) > 1:
                bad.append(f"{p.name}: {len(mk.version_lines)} headers — "
                           f"multiple streams in one file")
            if len(mk.end_lines) > 1:
                bad.append(f"{p.name}: {len(mk.end_lines)} '# end' trailers")
        add("FPX-39", FAIL if bad else PASS, "; ".join(bad[:3]))

        bad = []
        for p, mk in marks.items():
            is_clip = mk.clip_window_ns is not None or bool(mk.clipped_from)
            if is_clip:
                if not mk.clip_end:
                    bad.append(f"{p.name}: clip with no clip_end marker")
            elif not mk.end_lines:
                bad.append(f"{p.name}: no '# end' trailer — capture not closed")
        add("FPX-31", FAIL if bad else PASS, "; ".join(bad[:3]))

        # FPX-52: the bundle should carry the clip, not the whole capture.
        mk_own = marks[own] if own in marks else None
        if mk_own is None:
            add("FPX-52", FAIL, "no primary capture identified")
        elif mk_own.clip_window_ns is not None:
            add("FPX-52", PASS)
        else:
            add("FPX-52", FAIL,
                f"{own.name} carries no clip_window_ns: it is a whole capture, "
                f"not the performance")

        # FPX-4: a capture in a bundle keeps its own name and is in the library.
        if mk_own is not None and mk_own.clip_window_ns is not None:
            add("FPX-4", SKIP, "bundle carries a clip, not a capture")
        else:
            index = lib if lib is not None else _library_captures(root)
            bad = []
            for p in caps:
                digest = sha256_file(p)
                matches = index.get(digest, [])
                if not matches:
                    bad.append(f"{p.name}: no library capture with this digest")
                elif p.name not in {m.name for m in matches}:
                    bad.append(f"{p.name}: library copy is named "
                               f"{matches[0].name} — renamed on publish")
            add("FPX-4", FAIL if bad else PASS, "; ".join(bad[:3]))

    # -- FPX-77: renders account for every note-on ------------------------
    mids = [p for p in files if p.suffix.lower() == ".mid"]
    audio = [p for p in files if p.suffix.lower() in (".wav", ".m4a", ".flac", ".mp4")]
    if not audio and not mids:
        add("FPX-77", SKIP, "no renders in this bundle")
    elif not mids:
        add("FPX-77", FAIL, f"{len(audio)} audio render(s) with no .mid source")
    else:
        problems = []
        counts = {}
        for m in mids:
            try:
                counts[m] = midi_note_ons(m)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{m.name} unreadable ({type(exc).__name__}), "
                                f"{m.stat().st_size} bytes")
        if not problems:
            window, origin = _exact_window(bundle, root, manifest)
            if own is None:
                problems.append("no capture to count against")
            elif window is None:
                add("FPX-77", SKIP,
                    "exact window unrecorded (no manifest, no clip record); "
                    f".mid readable, {sum(counts.values())} note-ons")
                window = origin = None
            else:
                want = note_ons_in_window(own, window)
                for m, got in counts.items():
                    if got != want:
                        problems.append(f"{m.name} has {got} note-ons, window "
                                        f"({origin}) has {want}")
        if problems:
            add("FPX-77", FAIL, "; ".join(problems[:3]))
        elif not any(r.req == "FPX-77" for r in rep.results):
            add("FPX-77", PASS)

    # -- FPX-151 / FPX-153: what must not be in a bundle -------------------
    caches = [p.name for p in bundle.iterdir()
              if p.is_dir() and p.name in CACHE_NAMES]
    caches += [p.name for p in bundle.rglob("*.sqlite3")]
    add("FPX-151", FAIL if caches else PASS,
        "derived cache inside the bundle: " + ", ".join(sorted(set(caches))[:3])
        if caches else "")

    foreign = [p.name for p in bundle.rglob("*")
               if p.is_file() and p.suffix.lower() in FOREIGN_ASSET_SUFFIXES]
    add("FPX-153", FAIL if foreign else PASS,
        "third-party asset copied in: " + ", ".join(foreign[:3]) if foreign else "")

    return rep


# ------------------------------------------------- the library-wide check ---

_REJECT_NOTE = ("FPX-99: a label the listener rejected must not be asserted "
                "anywhere afterwards.")


def check_tombstones(root: Path | None = None) -> LibraryReport:
    """FPX-98/99: is any rejected label still asserted somewhere on disk?"""
    root = Path(root).expanduser() if root else default_root()
    cdir = captures_dir(root)
    labels_path = cdir / "labels.json"
    rep = LibraryReport(note=_REJECT_NOTE)
    if not labels_path.is_file():
        rep.note = f"no labels.json under {cdir}"
        return rep
    try:
        doc = json.loads(labels_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        rep.note = f"labels.json does not parse: {exc}"
        return rep
    rep.checked = True

    tombstones: list[tuple[str, str]] = []
    for key, rec in (doc.get("labels") or {}).items():
        if rec.get("verdict") != "reject":
            continue
        proposed = rec.get("proposed") or {}
        work = (proposed.get("work") or "").strip()
        take = (rec.get("take") or key.split("#")[0]).strip()
        if work and take:
            tombstones.append((take, work))

    haystacks: list[Path] = []
    prov = cdir / "PROVENANCE.md"
    if prov.is_file():
        haystacks.append(prov)
    perf = performances_dir(root)
    if perf.is_dir():
        haystacks += sorted(perf.glob("*/*.txt")) + sorted(perf.glob("*/fpx.json"))

    for take, work in tombstones:
        take_stem = take.rsplit(".", 1)[0]
        for hay in haystacks:
            text = hay.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if work.lower() in line.lower() and take_stem.lower() in line.lower():
                    rep.violations.append(
                        f"{hay}:{i} asserts {work!r} for {take}, which was "
                        f"rejected in labels.json")
    return rep


# ------------------------------------------------------------- reporting ----

def validate_tree(directory: Path | None = None, *,
                  root: Path | None = None) -> list[BundleReport]:
    """Validate every candidate bundle directly under ``directory``."""
    root = Path(root).expanduser() if root else default_root()
    directory = Path(directory).expanduser() if directory else performances_dir(root)
    if not directory.is_dir():
        return []
    lib = _library_captures(root)
    out = []
    for child in sorted(directory.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            out.append(validate_bundle(child, root=root, lib=lib))
    return out


def render_table(reports: list[BundleReport], *, out=None) -> None:
    out = out or sys.stdout
    ids = [c[0] for c in CHECKS]
    heads = [i.split("-", 1)[1] for i in ids]
    width = max([len(r.name) for r in reports] + [6])
    cols = " ".join(f"{h:>3}" for h in heads)
    print(f"{'bundle':<{width}}  {cols}   result", file=out)
    print("-" * (width + 2 + len(cols) + 10), file=out)
    for rep in reports:
        by = rep.by_req()
        cells = " ".join(f"{by[i].mark:>3}" if i in by else "  ?" for i in ids)
        n = len(rep.failures)
        verdict = "PASS" if rep.ok else f"FAIL {n}"
        print(f"{rep.name:<{width}}  {cells}   {verdict}", file=out)
    print(file=out)
    print("legend   . requirement met    X violated    - not applicable", file=out)


def render_details(reports: list[BundleReport], *, out=None,
                   verbose: bool = False) -> None:
    out = out or sys.stdout
    by_req: dict[str, list[tuple[str, str]]] = {}
    for rep in reports:
        for r in rep.results:
            if r.status == FAIL:
                by_req.setdefault(r.req, []).append((rep.name, r.detail))
    if not by_req:
        return
    desc = dict(CHECKS)
    print("\nviolations, by requirement", file=out)
    print("-" * 60, file=out)
    for req, _ in CHECKS:
        hits = by_req.get(req)
        if not hits:
            continue
        print(f"\n{req}  {desc[req]}   [{len(hits)}/{len(reports)} bundles]", file=out)
        shown = hits if verbose else hits[:4]
        for name, detail in shown:
            print(f"    {name}" + (f" — {detail}" if detail else ""), file=out)
        if len(hits) > len(shown):
            print(f"    … and {len(hits) - len(shown)} more (-v for all)", file=out)


def render_library(lib: LibraryReport, *, out=None) -> None:
    out = out or sys.stdout
    print("\nlibrary check", file=out)
    print("-" * 60, file=out)
    if not lib.checked:
        print(f"FPX-99  skipped — {lib.note}", file=out)
        return
    if not lib.violations:
        print("FPX-99  no rejected label is asserted anywhere.  PASS", file=out)
        return
    print(f"FPX-99  {len(lib.violations)} rejected label(s) still asserted.  FAIL",
          file=out)
    for v in lib.violations:
        print(f"    {v}", file=out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m fp30x_studio.fpx.validate",
        description="Check FPX bundles against docs/SPEC-fpx-format.md")
    ap.add_argument("directory", nargs="?",
                    help="directory of bundles (default: <root>/performances)")
    ap.add_argument("--root", help="library root (default: $FPX_HOME or ~/Music/...)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="list every violating bundle, not the first few")
    ap.add_argument("--no-library", action="store_true",
                    help="skip the library-wide FPX-99 check")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser() if args.root else default_root()
    directory = Path(args.directory).expanduser() if args.directory else performances_dir(root)
    reports = validate_tree(directory, root=root)

    if args.json:
        payload = {
            "root": str(root), "directory": str(directory),
            "bundles": [
                {"name": r.name, "path": str(r.path), "ok": r.ok,
                 "results": [{"req": x.req, "status": x.status, "detail": x.detail}
                             for x in r.results]}
                for r in reports],
        }
        if not args.no_library:
            lib = check_tombstones(root)
            payload["library"] = {"FPX-99": {"checked": lib.checked,
                                             "violations": lib.violations,
                                             "note": lib.note}}
        json.dump(payload, sys.stdout, indent=1)
        print()
        return 0 if all(r.ok for r in reports) else 1

    print(f"FPX bundle conformance — {directory}")
    print(f"library root {root}")
    if not reports:
        print("no bundle directories found")
        return 0
    passed = sum(1 for r in reports if r.ok)
    print(f"{len(reports)} bundle(s), {len(CHECKS)} checkable requirements, "
          f"{passed} passing\n")
    render_table(reports)
    render_details(reports, verbose=args.verbose)
    if not args.no_library:
        render_library(check_tombstones(root))
    print(f"\n{passed}/{len(reports)} bundles conform.")
    return 0 if passed == len(reports) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
