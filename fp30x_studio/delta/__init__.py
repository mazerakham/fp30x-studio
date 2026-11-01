r"""The delta: a performance as the score plus a difference vector.

    [my performance] = [baseline performance] + [difference vector delta]

The baseline is the page read literally, at one constant tempo, one constant
velocity, and every note exactly as long as it is written. The difference splits
into a categorical part, which is a matching between notated notes and played
notes together with the notes each side has and the other does not, and a real
valued part carried on the matched pairs and indexed by **score** note, so that
two performances of one piece are two vectors in one space and a cosine between
them means something.

    from fp30x_studio.delta import fit_delta, compare_deltas
    d = fit_delta("072-1-MEIf-001-nocturne.krn", "tonight.mid")
    d.tempo, d.norms, d.counts

See ``README.md`` in this directory for the definition, and
``python -m fp30x_studio.delta --help`` for the commands.
"""

from __future__ import annotations

from .compare import compare_deltas, cosine, format_comparison
from .fit import (Delta, TempoFit, fit_delta, local_tempo_curve, read_delta,
                  smooth_over_beats, write_delta)
from .match import Matching, match_notes
from .perform import PerfDoc, PerformedNote, load_performance
from .report import render_html, write_report
from .score import ScoreDoc, ScoreNote, load_score_notes

__all__ = [
    "ScoreNote", "ScoreDoc", "load_score_notes",
    "PerformedNote", "PerfDoc", "load_performance",
    "Matching", "match_notes",
    "Delta", "TempoFit", "fit_delta", "write_delta", "read_delta",
    "smooth_over_beats", "local_tempo_curve",
    "compare_deltas", "cosine", "format_comparison",
    "render_html", "write_report",
]
