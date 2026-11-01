"""Build an FPX performance bundle from a capture.

Non-destructive by construction. Nothing is moved, renamed or rewritten: the
capture is COPIED into `originals/`, every derived file is written under
`results/`, and every step that produced one is recorded in
`transformations/` as a recipe that can be replayed. A bundle is a new
version, never an edit of an old one.

The manifest, `fpx.json`, is what makes a directory a bundle. `README.txt` is
generated from it and must never be authored by hand.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sqlite3
from pathlib import Path

from .. import timbre
from ..segment import load, split, texture, mmss

BUNDLE_VERSION = "1.0"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def link_or_copy(src: Path, dst: Path) -> str:
    """Hardlink when we can, copy when we cannot.

    A hardlink costs no disk and cannot drift from its twin, which is what we
    want for a 15 MB render that already exists. It only works inside one
    filesystem, so the copy is the fallback, not the exception.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "present"
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def git_rev(repo: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
    except Exception:
        return None


def build(take: str, out_dir: Path, clips_dir: Path, *,
          gap: float, title: str, piece: dict,
          mirrors: list[Path] | None = None,
          render: dict | None = None,
          now_utc: str) -> dict:
    path, t0, ev = load(take)
    segs = split(ev, gap)
    db = path.parent / ".index" / (path.name + ".sqlite3")
    pedal = timbre.load_pedal(db)

    out_dir.mkdir(parents=True, exist_ok=True)
    orig = out_dir / "originals"
    xform = out_dir / "transformations"
    res = out_dir / "results" / "clips"
    orig.mkdir(exist_ok=True)
    xform.mkdir(exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)

    originals = []
    for src in [path] + list(mirrors or []):
        dst = orig / src.name
        how = link_or_copy(src, dst)
        originals.append({
            "name": src.name,
            "role": "capture" if src == path else "mirror",
            "bytes": dst.stat().st_size,
            "sha256": sha256(dst),
            "acquired_by": how,
            "source_path": str(src),
        })

    con = sqlite3.connect(db)
    hdr = dict(con.execute("SELECT key, value FROM header").fetchall())

    clips = []
    for i, s in enumerate(segs):
        stem = f"polfan-s{i:02d}"
        t_start, t_end = s[0][0], s[-1][0]
        tx = texture(s)
        ns0 = t0 + int(t_start * 1e9)
        ns1 = t0 + int(t_end * 1e9)

        files = {}
        for ext in ("fp30", "mid", "wav"):
            src = clips_dir / f"{stem}.{ext}"
            if not src.exists():
                continue
            dst = res / f"{stem}.{ext}"
            how = link_or_copy(src, dst)
            files[ext] = {
                "path": f"results/clips/{stem}.{ext}",
                "bytes": dst.stat().st_size,
                "sha256": sha256(dst),
                "acquired_by": how,
            }

        clips.append({
            "id": stem,
            "index": i,
            "window": {
                "start_s": round(t_start, 3), "end_s": round(t_end, 3),
                "duration_s": round(t_end - t_start, 3),
                "start_mmss": mmss(t_start), "end_mmss": mmss(t_end),
            },
            "musical": {
                "notes": len(s),
                "key_signature": tx["key"] + " maj",
                "in_key_fraction": round(tx["inset"], 3),
                "density_hz": round(tx["density"], 2),
                "stops": tx["stops"],
                "repetition": round(tx["rep"], 3),
                "pitch_range": tx["range"],
            },
            "timbre": {
                "performed": timbre.performed(s, pedal, ns0, ns1),
                "rendered": render if "wav" in files else None,
            },
            "envelope": timbre.envelope(s),
            "files": files,
            # Filled in by the listener. Left empty rather than guessed.
            "labels": {"verdict": None, "section": None, "notes": None,
                       "tags": []},
        })

    manifest = {
        "fpx_bundle_version": BUNDLE_VERSION,
        "generated_utc": now_utc,
        "title": title,
        "piece": piece,
        "capture": {
            "name": path.name,
            "started_utc": hdr.get("started_utc"),
            "source": hdr.get("source"),
            "notes": len(ev),
            "duration_s": round(ev[-1][0], 3),
            "duration_mmss": mmss(ev[-1][0]),
            "cc64_messages": len(pedal),
            "setup_changed_reconnects": int(hdr.get("setup_changed", 0) or 0),
        },
        "originals": originals,
        "transformations": [{
            "id": "segment-clip",
            "tool": "fp30x_studio.segment clip",
            "code_rev": git_rev(Path(__file__).resolve().parents[2]),
            "params": {"gap_s": gap, "pad_s": 0, "chase_cc64": True},
            "produced": "results/clips/*.{fp30,mid,wav}",
            "replay": ("python -m fp30x_studio.segment clip <capture> "
                       f"--segment N --gap {gap} --name polfan-sNN "
                       "--out <dir> --no-play"),
            "note": ("Windows come from silences longer than "
                     f"{gap}s. Pedal state is chased across each entry, so a "
                     "clip that begins under a held damper renders wet."),
        }],
        "clips": clips,
        "known_gaps": [
            "Velocity is the gesture, not the sound. The FP-30X sends no "
            "audio over BLE-MIDI, so no file here is a recording of what the "
            "room heard.",
            "Transpose and Master Tuning never reach the MIDI stream. A key "
            "signature below is the key of the KEYS PRESSED.",
            "Section labels are empty until the listener fills them in. The "
            "segmenter finds silences, not musical form.",
        ],
    }
    (out_dir / "fpx.json").write_text(json.dumps(manifest, indent=2))
    # The recipes also stand alone, so a transformation can be replayed
    # without parsing the whole manifest.
    (xform / "recipes.json").write_text(
        json.dumps(manifest["transformations"], indent=2))
    return manifest


def readme(m: dict) -> str:
    c = m["capture"]
    lines = [
        m["title"],
        "=" * len(m["title"]), "",
        f"WHAT   {m['piece']['composer']} — {m['piece']['work']}, "
        f"{m['piece']['key']}. A practice session, not a performance: "
        f"{len(m['clips'])} attempts cut out of one {c['duration_mmss']} capture.",
        f"WHEN   Captured {c['started_utc']}. Bundle built {m['generated_utc']}.",
        f"HOW    Roland FP-30X over BLE-MIDI into fp30x-capture, two writers, "
        f"{c['notes']} notes and {c['cc64_messages']} pedal messages.",
        "STATE  live — labels are empty until the listener fills them in.",
        "",
        "THE RULE THIS DIRECTORY IS BUILT ON",
        "  Nothing here is ever edited or deleted. originals/ is the capture,",
        "  copied byte-for-byte. transformations/ says what was done to it.",
        "  results/ is what came out. To change something, build a new bundle.",
        "",
        "WHERE THINGS ARE",
        "  fpx.json          the manifest. Everything below is generated from it.",
        "  originals/        the capture and its mirror. IRREPLACEABLE.",
        "  transformations/  the recipes, replayable from the command line.",
        "  results/clips/    one .fp30, .mid and .wav per attempt.",
        "",
        "WHAT THIS CANNOT TELL YOU",
    ]
    lines += [f"  - {g}" for g in m["known_gaps"]]
    lines += ["", "CLIPS", ""]
    lines.append(f"  {'id':<12} {'window':<14} {'dur':>7} {'notes':>6} "
                 f"{'key':>8} {'ped%':>5} {'leg':>5}")
    for c_ in m["clips"]:
        w, mu, p = c_["window"], c_["musical"], c_["timbre"]["performed"]
        ped = p.get("pedal", {}).get("down_fraction")
        lines.append(
            f"  {c_['id']:<12} {w['start_mmss']}–{w['end_mmss']:<7} "
            f"{w['duration_s']:6.1f}s {mu['notes']:>6} {mu['key_signature']:>8} "
            f"{(ped * 100 if ped is not None else 0):5.0f} "
            f"{p.get('legato_index', 0):5.2f}")
    return "\n".join(lines) + "\n"
