"""Join the head of one take to the body of another, at a matched note.

Why this exists
---------------
On 2026-08-21 the same nocturne was played twice and neither take is whole. The
first went deaf partway in and holds only the opening. The second was armed too
early -- the BLE link took 27 seconds to start delivering after CoreMIDI
reported the source connected -- so it opens mid-note and is missing the
introduction. Between them they cover the piece.

These are two performances, not two halves of one, so the join is a real edit
and the tool says so rather than pretending otherwise. What makes it defensible
is that the splice point is *found* rather than guessed: every 4-gram of pitches
from the body take's opening is looked up in the head take, and each hit votes
for an alignment offset. A real correspondence produces one dominant offset with
its neighbours picking up smaller counts, which is what rubato looks like in
this space. If no offset dominates, the takes are not the same passage and this
refuses to guess.

The seam gets the treatment any cut needs at both edges: notes still sounding at
the end of the head are released, the pedal is lifted, and the body's own
opening controller state is applied before its first note.

    python -m fp30x_studio.stitch --head 2026-08-21-c --head-from 16:15 \\
        --body 2026-08-21-nocturne --out spliced
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from . import core
from .rawcapture import split_messages
from .pipeline.cli import resolve

SUSTAIN = 64


def _index(path: Path) -> Path:
    return path.parent / ".index" / (path.name + ".sqlite3")


def _notes(path: Path, t0=None, t1=None):
    rows = sqlite3.connect(_index(path)).execute(
        "SELECT ns_on, note FROM interval ORDER BY ns_on, note").fetchall()
    base = rows[0][0]
    ev = [((ns - base) / 1e9, n, ns) for ns, n in rows]
    if t0 is not None:
        ev = [e for e in ev if t0 <= e[0] <= t1]
    return ev


def find_seam(head, body, probe_s=90.0, k=4):
    """Vote every k-gram of the body's opening into the head. Returns (index, votes, margin)."""
    hp = [n for _, n, _ in head]
    bp = [n for _, n, _ in body]
    grams = {}
    for i in range(len(hp) - k + 1):
        grams.setdefault(tuple(hp[i:i + k]), []).append(i)
    b0 = body[0][0]
    probe = sum(1 for t, _, _ in body if t - b0 <= probe_s)
    votes = Counter()
    for i in range(min(probe, len(bp) - k)):
        for j in grams.get(tuple(bp[i:i + k]), []):
            votes[j - i] += 1
    if not votes:
        return None, 0, 0.0
    ranked = votes.most_common(2)
    top, n = ranked[0]
    margin = 1.0 - (ranked[1][1] / n if len(ranked) > 1 else 0.0)
    return top, n, margin


def _raw_messages(path: Path, ns_from=None, ns_to=None):
    for line in path.open():
        if line.startswith("#"):
            continue
        parts = line.split()
        ns = int(parts[0])
        if ns_from is not None and ns < ns_from:
            continue
        if ns_to is not None and ns > ns_to:
            break
        # A packet may hold more than one MIDI message; yield each of them.
        for msg in split_messages(bytes(int(b, 16) for b in parts[1:])):
            yield ns, msg


def build(head_path, head_from_ns, seam_ns, body_path, out_mid: Path):
    import mido
    mid = mido.MidiFile()
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tpb, tempo = mid.ticks_per_beat, 500000
    tick = lambda s: int(mido.second2tick(s, tpb, tempo))

    prev_s = 0.0
    held, pedal = set(), 0

    def emit(delta_s, status, d1, d2):
        nonlocal prev_s
        d = tick(delta_s)
        if status == 0x90 and d2 > 0:
            held.add(d1); tr.append(mido.Message("note_on", note=d1, velocity=d2, time=d))
        elif status in (0x80, 0x90):
            held.discard(d1); tr.append(mido.Message("note_off", note=d1, velocity=d2, time=d))
        elif status == 0xB0:
            tr.append(mido.Message("control_change", control=d1, value=d2, time=d))

    # ---- head, up to the seam ----
    t0 = None
    for ns, data in _raw_messages(head_path, head_from_ns, seam_ns):
        if len(data) < 2:
            continue
        if t0 is None:
            t0 = ns
        t = (ns - t0) / 1e9
        st, d1 = data[0] & 0xF0, data[1]
        d2 = data[2] if len(data) > 2 else 0
        if st == 0xB0 and d1 == SUSTAIN:
            pedal = d2
        emit(t - prev_s, st, d1, d2); prev_s = t
    head_len = prev_s

    # ---- the seam ----
    # Do NOT damp here. An earlier version released every sounding note and
    # forced CC64 to 0 at the join, which is the correct thing to do at the end
    # of a file and destructive in the middle of one: the head is pedalling
    # through this moment, so lifting strips the sustain from the notes either
    # side of the seam until the body's next pedal event arrives. Jake heard it
    # as "a few notes seeming to not have sustain", four seconds wide.
    #
    # Carrying the pedal across also makes the body's own opening work for us.
    # The body was captured mid-note, so it begins with orphan note-offs -- and
    # those are exactly the notes that were sounding at the splice point. Let
    # the head's notes ring into the body and the body releases them itself, on
    # the timing the performance actually had.
    if pedal < 64:
        for n in sorted(held):
            tr.append(mido.Message("note_off", note=n, velocity=64, time=0))
        held.clear()

    # ---- body, from its first message ----
    #
    # DO NOT "FIX" THIS TO ANCHOR ON THE FIRST NOTE-ON without re-listening.
    # Two offsets here cancel, and an audit on 2026-08-21 found both:
    #
    #   * the body is anchored to its first raw *message*, which is an orphan
    #     note-off, so its first note-on lands 0.51 s after the nominal seam;
    #   * the vote lands mid-chord -- head note 123 is the third of four notes
    #     spread over 40 ms -- so the cut is 0.25-0.6 s early.
    #
    # Net, the body's music begins at 45.63 s, against two independent
    # estimates of the true anchor at 45.33 s and 45.70 s. That is inside the
    # rubato spread between the two performances, and Jake reports the seam
    # undetectable. Correcting either offset alone moves it out of that window.
    b0 = None
    body_pedal_state = 0
    for ns, data in _raw_messages(body_path):
        if len(data) < 2:
            continue
        if b0 is None:
            b0 = ns
            # No opening chase is applied here on purpose. The body has no
            # controller history to chase -- it is the start of its own file --
            # and the musically correct value is the head's pedal at the seam,
            # which is already in the stream because the seam no longer lifts
            # it. An earlier `if body_pedal:` branch here was dead code: the
            # variable was initialised to 0 and never assigned.
            pass
        t = head_len + (ns - b0) / 1e9
        st, d1 = data[0] & 0xF0, data[1]
        d2 = data[2] if len(data) > 2 else 0
        if st == 0xB0 and d1 == SUSTAIN:
            body_pedal_state = d2      # what the pedal was doing when it ended
        emit(t - prev_s, st, d1, d2); prev_s = t

    from .segment import close_tail
    close_tail(tr, mido, held, body_pedal_state >= 64, tpb, tempo)
    mid.save(out_mid)
    return head_len, prev_s


def parse_t(s):
    if ":" in s:
        m, sec = s.split(":"); return int(m) * 60 + float(sec)
    return float(s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.stitch")
    ap.add_argument("--head", required=True)
    ap.add_argument("--head-from", default="0")
    ap.add_argument("--head-to", default=None)
    ap.add_argument("--body", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-votes", type=int, default=8)
    a = ap.parse_args(argv)

    hp, bp = resolve(a.head), resolve(a.body)
    hf = parse_t(a.head_from)
    ht = parse_t(a.head_to) if a.head_to else 10 ** 9
    head = _notes(hp, hf, ht)
    body = _notes(bp)
    idx, votes, margin = find_seam(head, body)
    if idx is None or votes < a.min_votes or not (0 <= idx < len(head)):
        raise SystemExit(f"no confident seam: idx={idx} votes={votes} — refusing to guess")

    seam_t = head[idx][0] - head[0][0]
    print(f"seam found at note {idx} of the head, {seam_t:.1f}s in "
          f"({votes} votes, margin {margin:.2f})")

    out_dir = hp.parent / "clips"
    out_dir.mkdir(exist_ok=True)
    out_mid = out_dir / f"{a.out}.mid"
    head_len, total = build(hp, head[0][2], head[idx][2] - 1, bp, out_mid)
    print(f"head contributes {head_len:.1f}s, total {total:.1f}s -> {out_mid}")

    wav = out_dir / f"{a.out}.wav"
    core.render(out_mid, wav, core.soundfont_path())
    print(f"rendered {wav}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
