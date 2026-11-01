r"""From a played passage to a score, for original compositions.

Everything else in this package that produces notation starts from a score that
already exists (:mod:`fp30x_studio.studio.notation` engraves kern). An original
has no score; the performance is the only source, so the score has to be
*inferred* from it, and every inference here is a choice the composer may overrule. The
raw clip is never touched -- this writes new, derived files beside it.

The pipeline
------------
1. **Ghost notes out.** A key brushed at velocity 2 while reaching for its
   neighbour is a fact about the hand, not the composition. Notes at or below
   ``ghost_vel`` that are also short are dropped, and listed in the report so
   the cut can be checked.
2. **Beat tracking with a drifting tempo.** A rubato improvisation has no single
   period: on 2026-10-18-seg25 the onset envelope's autocorrelation never
   exceeds 0.1. So the beat is found by the Ellis (2007) dynamic programme --
   beats are the path through the onset envelope that maximises
   ``sum(onset strength at beats) - alpha * sum(log(interval / local_period)^2)``
   -- with the local period read from a sliding-window autocorrelation rather
   than fixed. ``alpha`` is the stiffness: large holds the tempo, small chases
   every hesitation.
3. **Meter** from where the bass lands: for each grouping k in (2, 3, 4) and each
   phase, the share of bass weight falling on beat 1 of the group. The k that
   concentrates it best wins, with a mild preference for 4 and 3 over 2.
4. **Quantisation, beat by beat.** Inside each beat the onsets choose the
   coarsest subdivision (1, 2, 3 or 4) that fits them within tolerance, so a
   plain beat is not written as four sixteenths and a triplet is not forced
   into duplets.
5. **Staves** by register (bass below G3 by default), not by hand: see the note
   at the split. Semitone slips inside a chord are dropped alongside the ghosts.
6. **Durations.** A pedalled performance releases keys early and lets the pedal
   hold them; notation writes what the ear hears. Each chord is held until its
   hand's next onset, or until its own release plus the pedal, whichever is
   sooner, and rests fill the remainder.
7. **Spelling** from the estimated key: diatonic pitch classes take the key's
   spelling, the rest follow the key's side of the circle of fifths.

Output: ``score.musicxml`` (open in MuseScore to edit by hand), a quantised
``clean.mid`` that sounds what the score says, and a ``report.json`` naming
every decision with its numbers.

    python -m fp30x_studio.transcribe CLIP.mid --out DIR [--meter 3] [--alpha 6]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from . import hands as H

FS = 100                     #: onset envelope sample rate, Hz
PERIOD_RANGE = (0.28, 1.2)   #: plausible beat periods, seconds
SUBDIVISIONS = (1, 2, 3, 4)
#: Penalty per subdivision, in squared beat-fractions. Coarser grids win ties.
SUB_COST = {1: 0.0, 2: 0.004, 3: 0.010, 4: 0.012}


@dataclass
class Report:
    notes_in: int = 0
    ghosts_dropped: list = None
    slips_dropped: list = None
    staff_split: str = ""
    period_median_s: float = 0.0
    bpm: float = 0.0
    meter: int = 4
    meter_scores: dict = None
    key: str = ""
    key_confidence: float = 0.0
    beats: int = 0
    measures: int = 0
    mean_abs_quant_err_beats: float = 0.0
    subdivision_use: dict = None
    hands: dict = None


# ------------------------------------------------------------------ cleaning

def drop_ghosts(notes, *, ghost_vel=12, ghost_dur=0.25):
    keep, gone = [], []
    for n in notes:
        (gone if n.velocity <= ghost_vel and n.duration < ghost_dur else keep).append(n)
    return keep, gone


def drop_slips(notes, *, tol=H.CLUSTER_TOL_S, ratio=0.85):
    """A semitone neighbour struck with a chord note, and quieter, is a finger slip.

    His language is chromatic, so a struck minor second is not wrong by itself;
    the test is that the two arrive inside one cluster window *and* one is
    clearly the weaker. Each removal is listed in the report.
    """
    gone = set()
    for i, a in enumerate(notes):
        for j in range(i + 1, len(notes)):
            b = notes[j]
            if b.t_on - a.t_on > tol:
                break
            if abs(a.pitch - b.pitch) == 1:
                lo = i if a.velocity < b.velocity else j
                hi = j if lo == i else i
                if notes[lo].velocity < ratio * notes[hi].velocity:
                    gone.add(lo)
    return ([n for k, n in enumerate(notes) if k not in gone],
            [notes[k] for k in sorted(gone)])


# ------------------------------------------------------------- beat tracking

def envelope(notes, t_end):
    """Onset strength: velocity-weighted, low notes counted double (they carry the pulse)."""
    env = np.zeros(int(t_end * FS) + 2)
    for n in notes:
        env[int(n.t_on * FS)] += n.velocity * (2.0 if n.pitch < 55 else 1.0)
    from scipy.ndimage import gaussian_filter1d
    return gaussian_filter1d(env, 2.0)


def local_period(env, *, win_s=12.0, prior_s=0.6):
    """Best beat period at every sample, from windowed autocorrelation, smoothed."""
    lo, hi = (int(p * FS) for p in PERIOD_RANGE)
    lags = np.arange(lo, hi)
    # Log-Gaussian prior on period keeps the tracker off octave errors.
    prior = np.exp(-0.5 * (np.log(lags / FS / prior_s) / 0.5) ** 2)
    half, hop = int(win_s * FS / 2), FS
    centres, periods = [], []
    for c in range(0, len(env), hop):
        w = env[max(0, c - half): c + half]
        w = w - w.mean()
        if len(w) < hi * 2 or not w.any():
            continue
        ac = np.array([np.dot(w[:-L], w[L:]) for L in lags]) / np.dot(w, w)
        centres.append(c)
        periods.append(lags[np.argmax(ac * prior)] / FS)
    periods = np.array(periods)
    # Median-smooth: a tempo does not jump between octaves second to second.
    sm = np.array([np.median(periods[max(0, i - 4): i + 5]) for i in range(len(periods))])
    return np.interp(np.arange(len(env)), centres, sm)


def track_beats(env, period, *, alpha=6.0):
    """Ellis's DP. Returns beat times in seconds."""
    # Normalise so ``alpha`` means the same thing for every passage: in units of
    # the envelope's standard deviation, as in Ellis. Raw velocity sums are in
    # the hundreds and swamp any reasonable stiffness.
    env = env / (env.std() or 1.0)
    n = len(env)
    score = env.astype(float).copy()
    back = -np.ones(n, dtype=int)
    for t in range(n):
        p = period[t] * FS
        lo, hi = int(t - 2 * p), int(t - p / 2)
        if hi <= 0:
            continue
        taus = np.arange(max(0, lo), hi)
        cost = -alpha * np.log((t - taus) / p) ** 2
        k = np.argmax(score[taus] + cost)
        score[t] = env[t] + score[taus[k]] + cost[k]
        back[t] = taus[k]
    # End on the best-scoring point in the last period.
    tail = int(period[-1] * FS)
    t = n - tail + int(np.argmax(score[-tail:]))
    beats = [t]
    while back[t] >= 0:
        t = back[t]
        beats.append(t)
    return np.array(beats[::-1]) / FS


def choose_meter(notes, beats, forced=None):
    """Grouping and phase that put the most bass weight on beat 1."""
    w = np.zeros(len(beats))
    for n in notes:
        if n.pitch < 55:
            i = int(np.argmin(np.abs(beats - n.t_on)))
            if abs(beats[i] - n.t_on) < 0.12:
                w[i] += n.velocity
    scores = {}
    for k in ((forced,) if forced else (2, 3, 4)):
        best = max(range(k), key=lambda ph: w[ph::k].sum())
        share = w[best::k].sum() / max(w.sum(), 1e-9)
        scores[k] = (share * k ** 0.5 * (1.0 if k > 2 else 0.85), best)
    k = max(scores, key=lambda k: scores[k][0])
    return k, scores[k][1], {k: round(v[0], 3) for k, v in scores.items()}


# -------------------------------------------------------------- quantisation

def quantiser(beats):
    """Map a time to a position in beats, choosing a subdivision per beat."""
    ext = np.concatenate([[beats[0] - (beats[1] - beats[0])], beats,
                          [beats[-1] + (beats[-1] - beats[-2])]])

    def frac(t):
        i = int(np.clip(np.searchsorted(ext, t, side="right") - 1, 0, len(ext) - 2))
        return i - 1, (t - ext[i]) / (ext[i + 1] - ext[i])

    def plan(times):
        """Subdivision for each beat index, from the onsets that fall in it."""
        per = {}
        for t in times:
            i, f = frac(t)
            per.setdefault(i, []).append(f)
        sub = {}
        for i, fs in per.items():
            fs = np.array(fs)
            sub[i] = min(SUBDIVISIONS, key=lambda s: np.mean(
                (fs - np.round(fs * s) / s) ** 2) + SUB_COST[s])
        return sub

    def q(t, sub):
        i, f = frac(t)
        s = sub.get(i, 4)
        return Fraction(i) + Fraction(round(f * s), s)

    return frac, plan, q


# --------------------------------------------------------------------- key

def estimate_key(notes):
    import music21 as m21
    s = m21.stream.Stream()
    for n in notes:
        x = m21.note.Note(n.pitch)
        x.quarterLength = max(0.25, min(4.0, n.duration * 2))
        s.append(x)
    k = s.analyze("key")
    return k, float(k.correlationCoefficient)


def speller(key):
    import music21 as m21
    names = {p.pitchClass: p.name for p in key.getScale().getPitches()}
    flats = key.sharps < 0 or (key.sharps == 0 and key.mode == "minor")

    def spell(midi):
        pc = midi % 12
        if pc in names:
            name = names[pc]
        else:
            p = m21.pitch.Pitch(midi=midi)
            sharp = p.accidental is not None and p.accidental.alter > 0
            name = p.getEnharmonic().name if (flats and sharp) else p.name
        p = m21.pitch.Pitch(name)
        p.octave = 4
        p.octave += (midi - p.midi) // 12
        return p
    return spell


# ------------------------------------------------------------------ assembly

def transcribe(src, out, *, meter=None, alpha=6.0, ghost_vel=12, title=None,
               pedal=None, split=55, key=None, composer=None):
    import music21 as m21
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rep = Report()

    raw = H.notes_from_midi(src)
    rep.notes_in = len(raw)
    notes, gone = drop_ghosts(raw, ghost_vel=ghost_vel)
    rep.ghosts_dropped = [dict(t=round(n.t_on, 3), pitch=n.pitch, vel=n.velocity) for n in gone]
    notes, slips = drop_slips(notes)
    rep.slips_dropped = [dict(t=round(n.t_on, 3), pitch=n.pitch, vel=n.velocity) for n in slips]
    t0 = notes[0].t_on
    notes = [H.Note(n.t_on - t0, n.t_off - t0, n.pitch, n.velocity) for n in notes]

    env = envelope(notes, max(n.t_off for n in notes) + 1)
    per = local_period(env)
    beats = track_beats(env, per, alpha=alpha)
    rep.beats = len(beats)
    rep.period_median_s = float(np.median(np.diff(beats)))
    rep.bpm = round(60 / rep.period_median_s, 1)

    k, phase, rep.meter_scores = choose_meter(notes, beats, meter)
    rep.meter = k
    frac, plan, q = quantiser(beats)
    sub = plan([n.t_on for n in notes])
    rep.subdivision_use = {s: sum(1 for v in sub.values() if v == s) for s in SUBDIVISIONS}

    # Onset positions in beats, then shift so the first downbeat is beat 0 of bar 1.
    pos = [q(n.t_on, sub) for n in notes]
    errs = [abs(float(p) - (frac(n.t_on)[0] + frac(n.t_on)[1])) for p, n in zip(pos, notes)]
    rep.mean_abs_quant_err_beats = round(float(np.mean(errs)), 3)
    first = min(pos)
    # Pickup: everything before the first downbeat at or after `first`.
    down0 = Fraction(phase) + k * ((first - phase) // k)
    pos = [p - down0 for p in pos]

    est, conf = estimate_key(notes)
    rep.key_confidence = round(conf, 3)
    # The detector reads pitch classes, so it cannot tell C# major from Db major.
    # His name for the key wins; the estimate stays in the report.
    if key:
        rep.key_estimated = est.name
        key = m21.key.Key(key[0] + key[1:].replace("b", "-"))
    else:
        key = est
    rep.key = key.name
    spell = speller(key)

    # Staff is not hand. When both hands share the middle of the keyboard, the
    # viterbi hand model splits one melodic line across two staves, which is
    # right about the fingers and unreadable on the page. A fixed register split
    # keeps a line on one staff; ``split=None`` restores the hand model.
    if split is None:
        hand = H.assign(notes, "viterbi").hands
        rep.staff_split = "viterbi hands"
    else:
        hand = [H.LEFT if n.pitch < split else H.RIGHT for n in notes]
        rep.staff_split = f"register, bass below MIDI {split}"
    rep.hands = {"left": hand.count(H.LEFT), "right": hand.count(H.RIGHT)}

    # Release positions: the key's own release, extended by the pedal if it was down.
    ped = pedal if pedal is not None else _pedal_from_midi(src, t0)

    def released(n):
        t = n.t_off
        for a, b in ped:
            if a <= t < b:
                t = b
                break
        return t

    # Chords per hand at each quantised onset.
    parts = {H.RIGHT: {}, H.LEFT: {}}
    for n, p, h in zip(notes, pos, hand):
        end = q(released(n), sub) - down0
        c = parts[h].setdefault(p, {"pitches": set(), "end": p, "vel": {}})
        c["pitches"].add(n.pitch)
        c["vel"][n.pitch] = max(c["vel"].get(n.pitch, 0), n.velocity)
        c["end"] = max(c["end"], end)

    score = m21.stream.Score()
    score.metadata = m21.metadata.Metadata()
    score.metadata.title = title or Path(src).stem
    if composer:
        score.metadata.composer = composer
    total = max(max(c["end"] for c in d.values()) for d in parts.values() if d)
    start = min(min(d) for d in parts.values() if d)
    for h, clef in ((H.RIGHT, m21.clef.TrebleClef()), (H.LEFT, m21.clef.BassClef())):
        part = m21.stream.Part()
        part.insert(0, clef)
        part.insert(0, m21.key.KeySignature(key.sharps))
        part.insert(0, m21.meter.TimeSignature(f"{k}/4"))
        if h == H.RIGHT:
            part.insert(0, m21.tempo.MetronomeMark(number=round(rep.bpm)))
        onsets = sorted(parts[h])
        for i, p in enumerate(onsets):
            c = parts[h][p]
            nxt = onsets[i + 1] if i + 1 < len(onsets) else total
            dur = min(max(c["end"], p + Fraction(1, 4)), nxt) - p
            if dur <= 0:
                continue
            ps = [spell(m) for m in sorted(c["pitches"])]
            el = m21.note.Note(ps[0]) if len(ps) == 1 else m21.chord.Chord(ps)
            el.quarterLength = dur
            part.insert(float(p - start), el)
        part.makeRests(fillGaps=True, inPlace=True, timeRangeFromBarDuration=False)
        score.insert(0, part)

    # Shift the bar line so beat 0 falls on a downbeat; the stretch before it is a pickup.
    score = score.makeNotation()
    rep.measures = len(score.parts[0].getElementsByClass("Measure"))

    score.write("musicxml", fp=out / "score.musicxml")
    _write_clean_midi(parts, rep.bpm, start, out / "clean.mid")
    (out / "report.json").write_text(json.dumps(rep.__dict__, indent=1, default=str))
    np.savetxt(out / "beats.txt", beats + t0, fmt="%.3f")
    return rep, score


CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def engrave(out, *, title=""):
    """MusicXML -> Verovio SVG pages -> one HTML -> PDF (headless Chrome)."""
    import subprocess
    import verovio
    out = Path(out)
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": 2100, "pageHeight": 2970, "scale": 40,
                   "footer": "none", "adjustPageHeight": False})
    if not tk.loadFile(str(out / "score.musicxml")):
        raise RuntimeError("verovio could not load score.musicxml")
    d = out / "pages"
    d.mkdir(exist_ok=True)
    for old in d.glob("p*.svg"):
        old.unlink()
    n = tk.getPageCount()
    for i in range(1, n + 1):
        (d / f"p{i}.svg").write_text(tk.renderToSVG(i))
    (out / "score.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>{title}</title>"
        "<style>@page{size:A4;margin:0}body{margin:0;background:#fff}"
        "img{width:100%;display:block;break-after:page}</style>"
        + "".join(f'<img src="pages/p{i}.svg">' for i in range(1, n + 1)))
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out / 'score.pdf'}", (out / "score.html").as_uri()],
                   check=True, capture_output=True)
    return n


def _pedal_from_midi(path, t0):
    import mido
    m = mido.MidiFile(path)
    t, down, spans = 0.0, None, []
    for msg in mido.merge_tracks(m.tracks):
        t += mido.tick2second(msg.time, m.ticks_per_beat, 500000)
        if msg.type == "control_change" and msg.control == 64:
            if msg.value >= 64 and down is None:
                down = t - t0
            elif msg.value < 64 and down is not None:
                spans.append((down, t - t0))
                down = None
    return spans


def _write_clean_midi(parts, bpm, start, path):
    """The score as sound: quantised, at one tempo. What the notation says."""
    import mido
    tpb = 480
    ev = []
    for h, d in parts.items():
        on = sorted(d)
        for i, p in enumerate(on):
            c = d[p]
            nxt = on[i + 1] if i + 1 < len(on) else max(c["end"], p + Fraction(1, 4))
            end = min(max(c["end"], p + Fraction(1, 4)), nxt)
            for m in c["pitches"]:
                # Timing is the score's; loudness is still the performance's.
                ev.append((int((p - start) * tpb), "note_on", m, c["vel"][m]))
                ev.append((int((end - start) * tpb) - 1, "note_off", m, 0))
    ev.sort(key=lambda e: (e[0], e[1] == "note_on"))
    mf = mido.MidiFile(ticks_per_beat=tpb)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    last = 0
    for tick, kind, m, v in ev:
        tr.append(mido.Message(kind, note=m, velocity=v, time=max(0, tick - last)))
        last = max(last, tick)
    mf.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--meter", type=int)
    ap.add_argument("--alpha", type=float, default=6.0)
    ap.add_argument("--ghost-vel", type=int, default=12)
    ap.add_argument("--title")
    ap.add_argument("--composer", help="composer name for the score's metadata")
    ap.add_argument("--key", help="spell in this key, e.g. Db or f#; overrides the estimate")
    ap.add_argument("--split", default="55",
                    help="MIDI pitch: notes below go on the bass staff; 'hands' for the viterbi hand model")
    a = ap.parse_args(argv)
    rep, _ = transcribe(a.src, a.out, meter=a.meter, alpha=a.alpha,
                        ghost_vel=a.ghost_vel, title=a.title,
                        split=None if a.split == "hands" else int(a.split), key=a.key,
                        composer=a.composer)
    pages = engrave(a.out, title=a.title or "")
    print(f"engraved {pages} pages -> {Path(a.out) / 'score.pdf'}")
    print(json.dumps({k: v for k, v in rep.__dict__.items() if not k.endswith("_dropped")}, indent=1))
    print(f"ghosts dropped: {len(rep.ghosts_dropped)}, slips dropped: {len(rep.slips_dropped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
