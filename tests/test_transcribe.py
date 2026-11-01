"""transcribe: a played passage with a known score must come back as that score."""
import random

import mido
import music21 as m21

from fp30x_studio import transcribe as T


def _waltz(path, *, bpm=100, bars=16, jitter=0.012, seed=1, ramp=0.0):
    """Bass on beat 1, chord on 2 and 3, a melody eighth pair on beat 1: 3/4.

    ``ramp`` is an accelerando: the last bar is that fraction faster than the first.
    """
    rnd = random.Random(seed)
    ev = []
    t0 = 0.5
    for b in range(bars):
        beat = 60 / (bpm * (1 + ramp * b / max(bars - 1, 1)))
        if b:
            t0 += 3 * prev
        prev = beat
        def at(t, p, v, d):
            t += rnd.uniform(-jitter, jitter)
            ev.append((t, "note_on", p, v))
            ev.append((t + d, "note_off", p, 0))
        at(t0, 41, 100, beat * 0.9)                       # F2, accented
        for k in (1, 2):
            at(t0 + k * beat, 56, 50, beat * 0.5)        # Ab3
            at(t0 + k * beat, 60, 50, beat * 0.5)        # C4
        at(t0, 72, 80, beat * 0.45)                       # C5
        at(t0 + beat / 2, 68, 70, beat * 0.45)            # Ab4
    ev.sort(key=lambda e: (e[0], e[1] == "note_on"))
    mf = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    last = 0                      # 480 tpb at the default 120 bpm: 960 ticks/s
    for t, kind, p, v in ev:
        tick = int(round(t * 960))
        tr.append(mido.Message(kind, note=p, velocity=v, time=tick - last))
        last = tick
    mf.save(path)


def test_waltz_round_trip(tmp_path):
    src = tmp_path / "w.mid"
    _waltz(src)
    rep, score = T.transcribe(src, tmp_path / "out", pedal=[])
    assert rep.meter == 3
    assert abs(rep.bpm - 100) < 4
    assert rep.key in ("F minor", "A- major")
    assert rep.mean_abs_quant_err_beats < 0.05
    # Every bar of the bass staff: one F on the downbeat.
    bass = score.parts[1].getElementsByClass("Measure")
    downs = [m.notes[0] for m in bass if m.notes]
    assert len(downs) >= 15
    assert all(n.offset == 0 and n.pitch.midi == 41 for n in downs)
    # The melody eighths survive as eighths, spelled in flats.
    names = {p.nameWithOctave for n in score.parts[0].flatten().notes for p in n.pitches}
    assert "A-4" in names and "G#4" not in names


def test_accelerando_keeps_the_bar_line(tmp_path):
    """Rubato is the case that matters: a 25% accelerando must not slip a beat."""
    src = tmp_path / "w.mid"
    _waltz(src, bpm=90, bars=24, ramp=0.25)
    rep, score = T.transcribe(src, tmp_path / "out", pedal=[])
    assert rep.meter == 3
    bass = score.parts[1].getElementsByClass("Measure")
    downs = [m.notes[0] for m in bass if m.notes]
    assert len(downs) == 24
    assert all(n.offset == 0 and n.pitch.midi == 41 for n in downs)


def test_slip_dropped_chromatic_kept():
    N = T.H.Note
    slip = [N(1.0, 1.5, 60, 90), N(1.01, 1.2, 61, 30)]
    kept, gone = T.drop_slips(slip)
    assert [n.pitch for n in gone] == [61]
    even = [N(1.0, 1.5, 60, 80), N(1.01, 1.5, 61, 78)]   # a deliberate minor second
    kept, gone = T.drop_slips(even)
    assert not gone
