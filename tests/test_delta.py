"""Tests for :mod:`fp30x_studio.delta`.

The synthetic half needs no piano and no corpus. It builds a small score in
``**kern``, synthesises a performance of it by applying a perturbation whose
value is known in closed form, and checks that the delta is that perturbation:
the tempo comes back, the timing residual comes back note for note, the
articulation comes back as the log of the factor that was applied, and an extra
note that was never on the page is reported as exactly one insertion.

The real half runs against tonight's Op. 72 No. 1 take and the first edition of
the score, and skips cleanly if either is absent from this machine.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from fp30x_studio.align.events import cluster_onsets
from fp30x_studio.delta import (compare_deltas, cosine, fit_delta,
                                load_performance, load_score_notes,
                                match_notes, read_delta, render_html,
                                write_delta)
from fp30x_studio.delta.cluster import cluster_with_index
from fp30x_studio.delta.score import ScoreParseError

# A four bar scale in 4/4, one voice, quarter notes throughout: sixteen notes
# whose notated onsets are 0, 1, 2, ... and whose notated lengths are all 1.
KERN = """**kern
*M4/4
=1
4c
4d
4e
4f
=2
4g
4a
4b
4cc
=3
4cc
4b
4a
4g
=4
4f
4e
4d
4c
==
*-
"""

QPM = 60.0                      # the tempo the synthetic take is played at
SPB = 60.0 / QPM


def write_score(tmp_path: Path) -> Path:
    p = tmp_path / "scale.krn"
    p.write_text(KERN, encoding="utf-8")
    return p


def write_midi(path: Path, notes) -> Path:
    """``notes`` is a list of (t_on, t_off, pitch, velocity), in seconds."""
    import mido

    tpb = 9600                          # 0.1 ms at 60 bpm
    tempo = mido.bpm2tempo(60)          # 1 s per beat, so ticks are simple
    mid = mido.MidiFile(ticks_per_beat=tpb)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    events = []
    for t_on, t_off, pitch, vel in notes:
        events.append((t_on, "note_on", pitch, vel))
        events.append((t_off, "note_off", pitch, 64))
    events.sort(key=lambda e: (e[0], e[1] == "note_on"))
    # Absolute ticks first, then differences, so the rounding does not
    # accumulate into a drift that the tempo fit would then have to absorb.
    ticks = [int(round(t * tpb)) for t, *_ in events]
    prev = 0
    for tk, (_, kind, pitch, vel) in zip(ticks, events):
        tr.append(mido.Message(kind, note=pitch, velocity=vel, time=tk - prev))
        prev = tk
    mid.save(str(path))
    return path


def synth(tmp_path: Path, *, shift=None, hold=1.0, extra=None,
          velocity=lambda k: 64) -> tuple[Path, Path, list[float]]:
    """A score and a performance of it with a known perturbation applied.

    ``shift(k)`` is the timing perturbation of note ``k`` in seconds, ``hold``
    multiplies every notated length, and ``extra`` is a list of
    ``(t_on, t_off, pitch)`` played but not written.
    """
    score = write_score(tmp_path)
    doc = load_score_notes(score, qpm=100.0)
    order = sorted(doc.notes, key=lambda n: n.index)
    shift = shift or (lambda k: 0.0)
    rows, applied = [], []
    for k, n in enumerate(order):
        s = shift(k)
        applied.append(s)
        t_on = 1.0 + n.beat * SPB + s
        rows.append((t_on, t_on + hold * n.dur_ql * SPB, n.pitch, velocity(k)))
    for t_on, t_off, pitch in (extra or []):
        rows.append((t_on, t_off, pitch, 70))
    perf = write_midi(tmp_path / "take.mid", rows)
    return score, perf, applied


# ---------------------------------------------------------------------------
# the clustering the two note tables share
# ---------------------------------------------------------------------------

def test_cluster_with_index_agrees_with_align():
    rows = [(0.000, 60, 64), (0.005, 64, 62), (0.010, 67, 66),
            (0.400, 62, 70), (0.410, 65, 71), (1.000, 60, 40)]
    groups = cluster_with_index(rows, tol=0.055)
    events = cluster_onsets(rows, tol=0.055)
    assert len(groups) == len(events)
    for g, e in zip(groups, events):
        assert frozenset(rows[i][1] for i in g) == e.notes
        assert rows[g[0]][0] == pytest.approx(e.t)


# ---------------------------------------------------------------------------
# the score side
# ---------------------------------------------------------------------------

def test_score_reads_notes_bars_and_lengths(tmp_path):
    doc = load_score_notes(write_score(tmp_path), qpm=100.0)
    assert len(doc.notes) == 16
    assert [n.beat for n in doc.notes] == [float(k) for k in range(16)]
    assert {n.dur_ql for n in doc.notes} == {1.0}
    assert [n.measure for n in doc.notes[:4]] == [1, 1, 1, 1]
    assert [n.measure for n in doc.notes[-4:]] == [4, 4, 4, 4]
    assert doc.n_measures == 4


# ---------------------------------------------------------------------------
# a known perturbation must come back
# ---------------------------------------------------------------------------

def test_flat_performance_recovers_the_tempo_and_a_zero_delta(tmp_path):
    score, perf, _ = synth(tmp_path)
    d = fit_delta(score, perf)
    assert d.counts["matched"] == 16
    assert d.counts["insertions"] == 0 and d.counts["deletions"] == 0
    assert d.tempo["qpm"] == pytest.approx(QPM, abs=1e-6)
    assert d.tempo["t0"] == pytest.approx(1.0, abs=1e-6)
    assert np.allclose(d.component("timing"), 0.0, atol=1e-6)
    assert np.allclose(d.component("articulation"), 0.0, atol=1e-6)
    assert d.norms["timing"]["l2"] == pytest.approx(0.0, abs=1e-6)


def test_known_timing_perturbation_is_recovered(tmp_path):
    # A four note pattern chosen to be orthogonal to both the constant and the
    # beat number, so the least squares baseline is exactly the unperturbed one
    # and the residual is exactly the perturbation that was applied.
    push = lambda k: 0.12 * [1, -1, -1, 1][k % 4]
    score, perf, applied = synth(tmp_path, shift=push)
    d = fit_delta(score, perf)
    assert d.tempo["qpm"] == pytest.approx(QPM, abs=1e-4)
    assert d.tempo["t0"] == pytest.approx(1.0, abs=1e-4)
    r = d.component("timing")
    assert np.allclose(r, np.array(applied) / SPB, atol=1e-3)
    # A pattern with a period of four beats is touch, not rubato: a Gaussian of
    # width two beats sees almost none of it, so the smooth part is small and
    # the residual carries the perturbation.
    assert np.abs(d.component("rubato")).max() < 0.05
    assert np.allclose(d.component("micro"), r, atol=0.05)
    assert np.allclose(r, d.component("rubato") + d.component("micro"), atol=1e-9)


def test_a_slow_drift_is_recovered_as_rubato_not_touch(tmp_path):
    # A quadratic drag over the piece: the lowest mode the tempo fit cannot
    # absorb, and smooth on the scale of the two beat kernel.
    drift = lambda k: 0.0025 * (k - 7.5) ** 2
    score, perf, _ = synth(tmp_path, shift=drift)
    d = fit_delta(score, perf)
    r, rho, m = (d.component("timing"), d.component("rubato"),
                 d.component("micro"))
    # Away from the ends, where a one sided kernel is biased by construction,
    # the drift is in rubato and touch is empty.
    core = slice(3, 13)
    assert np.abs(r[core]).max() > 0.04
    assert np.abs(m[core]).max() < 0.02
    assert np.allclose(rho[core], r[core], atol=0.02)


def test_articulation_is_the_log_of_the_hold_factor(tmp_path):
    score, perf, _ = synth(tmp_path, hold=0.5)
    d = fit_delta(score, perf)
    a = d.component("articulation")
    assert np.allclose(a, math.log(0.5), atol=1e-3)


def test_velocity_is_the_struck_velocity_over_127(tmp_path):
    score, perf, _ = synth(tmp_path, velocity=lambda k: 40 + 4 * k)
    d = fit_delta(score, perf)
    v = d.component("velocity")
    assert np.allclose(v, (40 + 4 * np.arange(16)) / 127.0, atol=1e-6)
    assert d.norms["mean_velocity"] == pytest.approx(float(v.mean()))
    assert np.allclose(d.component("velocity_centred"), v - v.mean(), atol=1e-9)


# ---------------------------------------------------------------------------
# the categorical part
# ---------------------------------------------------------------------------

def test_one_inserted_note_is_reported_as_one_insertion(tmp_path):
    # A pitch that is nowhere in the scale, struck between notes 5 and 6.
    score, perf, _ = synth(tmp_path, extra=[(6.5, 6.7, 73)])
    d = fit_delta(score, perf)
    assert d.counts["insertions"] == 1
    assert d.counts["deletions"] == 0
    assert d.counts["matched"] == 16
    assert d.insertions[0]["pitch"] == 73
    assert d.insertions[0]["near_measure"] in (2, 3)


def test_one_missed_note_is_reported_as_one_deletion(tmp_path):
    score = write_score(tmp_path)
    doc = load_score_notes(score, qpm=100.0)
    rows = [(1.0 + n.beat * SPB, 1.0 + n.beat * SPB + SPB, n.pitch, 64)
            for n in doc.notes if n.index != 9]
    perf = write_midi(tmp_path / "gap.mid", rows)
    d = fit_delta(score, perf)
    assert d.counts["deletions"] == 1
    assert d.counts["insertions"] == 0
    assert d.deletions[0]["i"] == 9
    assert d.deletions[0]["pitch"] == doc.notes[9].pitch


def test_the_matching_accounts_for_every_note_on_both_sides(tmp_path):
    score, perf, _ = synth(tmp_path, extra=[(6.5, 6.7, 73), (7.5, 7.6, 75)])
    m = match_notes(load_score_notes(score, qpm=100.0), load_performance(perf))
    assert len(m.pairs) + len(m.deletions) == m.n_score
    assert len(m.pairs) + len(m.insertions) == m.n_perf


# ---------------------------------------------------------------------------
# comparison
# ---------------------------------------------------------------------------

def test_cosine_of_a_delta_with_itself_is_one(tmp_path):
    score, perf, _ = synth(tmp_path, shift=lambda k: 0.1 * math.sin(k),
                           hold=0.8, velocity=lambda k: 50 + 3 * (k % 5))
    d = fit_delta(score, perf)
    c = compare_deltas(d, d)
    assert c["intersection"] == 16
    for name, rec in c["cosine"].items():
        assert rec["value"] == pytest.approx(1.0, abs=1e-9), name
        assert c["l2_distance"][name] == pytest.approx(0.0, abs=1e-9)


def test_a_reversed_delta_is_anticorrelated(tmp_path):
    score, a, _ = synth(tmp_path, shift=lambda k: 0.1 * math.sin(k))
    da = fit_delta(score, a)
    tmp2 = tmp_path / "b"
    tmp2.mkdir()
    score2, b, _ = synth(tmp2, shift=lambda k: -0.1 * math.sin(k))
    db = fit_delta(score2, b)
    c = compare_deltas(da, db)
    assert c["same_score"] is True
    assert c["cosine"]["timing"]["value"] == pytest.approx(-1.0, abs=1e-3)


def test_cosine_of_orthogonal_vectors_is_zero():
    assert cosine(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest.approx(0.0)
    assert cosine(np.zeros(3), np.ones(3)) is None


def test_comparison_reports_the_intersection_not_the_union(tmp_path):
    score = write_score(tmp_path)
    doc = load_score_notes(score, qpm=100.0)

    def take(name, drop):
        rows = [(1.0 + n.beat * SPB, 1.0 + n.beat * SPB + SPB, n.pitch, 64)
                for n in doc.notes if n.index != drop]
        return fit_delta(score, write_midi(tmp_path / name, rows))

    c = compare_deltas(take("a.mid", 3), take("b.mid", 11))
    assert c["matched_a"] == 15 and c["matched_b"] == 15
    assert c["intersection"] == 14
    assert c["edits_a"]["deletions"] == 1 and c["edits_b"]["deletions"] == 1


# ---------------------------------------------------------------------------
# round trip and report
# ---------------------------------------------------------------------------

def test_delta_survives_a_json_round_trip(tmp_path):
    score, perf, _ = synth(tmp_path, shift=lambda k: 0.05 * k, hold=0.9)
    d = fit_delta(score, perf)
    back = read_delta(write_delta(d, tmp_path / "d.json"))
    assert back.counts == d.counts
    assert back.tempo == d.tempo
    assert np.allclose(back.component("timing"), d.component("timing"))


def test_report_is_a_self_contained_page_with_no_em_dashes(tmp_path):
    score, perf, _ = synth(tmp_path, shift=lambda k: 0.05 * math.cos(k),
                           extra=[(6.5, 6.7, 73)])
    html = render_html(fit_delta(score, perf))
    assert '<meta charset="utf-8">' in html
    assert chr(0x2014) not in html   # no em-dashes, by house rule
    assert "<svg" in html and "http" not in html.split("<body>")[1].replace(
        "http://www.w3.org/2000/svg", "")
    assert "nan" not in html.lower()


# ---------------------------------------------------------------------------
# the real take
# ---------------------------------------------------------------------------

SCORE_62 = Path("~/workspace/audio/corpus/raw/humdrum-chopin-first-editions/"
                "kern/062-1-BR-001.krn").expanduser()


@pytest.mark.skipif(not SCORE_62.exists(),
                    reason="the Op. 62 No. 1 score is not on this machine")
def test_a_score_whose_parts_disagree_is_refused():
    """music21 reads the left hand of this file 92 quarter notes short.

    The failure is silent, so the check has to be explicit: a delta fitted to
    it matched 34% of the page and looked like bad playing rather than a bad
    parse.
    """
    with pytest.raises(ScoreParseError) as e:
        load_score_notes(SCORE_62)
    assert "did not parse as one piece" in str(e.value)
    # The unchecked read still works, for anyone diagnosing the parse itself.
    assert len(load_score_notes(SCORE_62, check=False).notes) > 1000


SCORE_72 = Path("~/workspace/audio/corpus/raw/humdrum-chopin-first-editions/"
                "kern/072-1-MEIf-001-nocturne.krn").expanduser()
TAKE_72 = Path("~/Music/FP-30X Studio/takes/clips/"
               "2026-09-08-op72no1-e-minor-COMPLETE-5m05s.mid").expanduser()


@pytest.mark.skipif(not (SCORE_72.exists() and TAKE_72.exists()),
                    reason="the Op. 72 No. 1 score or take is not on this machine")
def test_op72_no1_take_matches_almost_all_of_the_page():
    d = fit_delta(SCORE_72, TAKE_72)
    assert d.counts["match_rate_score"] > 0.95
    assert 30 < d.tempo["qpm"] < 90
    # The onset residual is real rubato, not a broken fit: the smooth part must
    # carry most of it, and the note to note part must stay small.
    assert d.norms["micro"]["rms"] < 0.4
    assert d.norms["rubato"]["l2"] > d.norms["micro"]["l2"]
