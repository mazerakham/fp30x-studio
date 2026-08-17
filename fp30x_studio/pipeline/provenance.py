"""Writing each take's integrity numbers into ``PROVENANCE.md`` automatically.

The working agreement is that any directory holding data captured from outside
this machine carries a ``PROVENANCE.md`` saying what it is, when and how it was
captured, what the known gaps are, and what came out of it -- appended to, dated,
every time something is added.

Doing that by hand fails the way hand-written provenance always fails: it gets
written for the first take and for none of the rest. So the numbers that matter
-- the census, the lattice fraction, the defect counts and the inferred loss --
are appended by the ingest itself, the first time a take's stream is known to
have ended. One entry per take, marked, so a re-ingest does not duplicate it.

The entry is deliberately the *link* story and not the analysis: what arrived,
what did not, and how much the timestamps can be trusted.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import core
from .integrity import LOSS_CAVEAT
from .integrity import report as integrity_report

__all__ = ["append_provenance", "entry_for"]

MARKER = "fp30x-pipeline"


def entry_for(store) -> str:
    """The Markdown block recorded for one take."""
    r = integrity_report(store)
    L, loss = r.lattice, r.loss
    h = store.header()
    stamp = time.strftime("%Y-%m-%d", time.localtime())
    census = ", ".join(f"{k} {v}" for k, v in r.census.items())
    defects = ", ".join(f"{k} {v}" for k, v in sorted(r.defects.items()))
    lines = [
        "",
        f"<!-- {MARKER}: {r.name} -->",
        f"### {r.name} — ingested {stamp}",
        "",
        f"- **File** `{r.path}`, {r.duration_s:.1f} s, {r.packets} packets "
        f"carrying {r.messages} MIDI messages "
        f"({r.multi_message_packets} packets carry more than one, up to "
        f"{r.max_messages_per_packet}).",
        f"- **Captured** {h.get('started_utc', '(unrecorded)')} to "
        f"{store.trailer().get('stopped_utc', '(no clean stop)')} from "
        f"`{h.get('source', '(unrecorded)')}`.",
        f"- **Timing** {r.timing_grade}, "
        f"{'trusted' if r.timing_trusted else '**not trusted**'} — "
        f"{r.timing_note}.",
        f"- **Census** {census}. Polyphonic key pressure (0xAn) "
        f"{r.census.get('polytouch', 0)}, channel pressure (0xDn) "
        f"{r.census.get('aftertouch', 0)}.",
        f"- **Pairing** {r.intervals} intervals, {r.trusted_intervals} closed "
        f"by a real note-off. Every one of the {r.messages} messages carries a "
        f"role: {'accounting complete' if r.accounted else '**INCOMPLETE**'}.",
        f"- **Defects** {defects}.",
        f"- **5 ms lattice** {L.n_on_lattice}/{L.n_gaps} inter-packet gaps "
        f"({L.fraction:.2%}) are exact integer multiples of 5.000000 ms. "
        f"{len(L.runs)} off-lattice run{'' if len(L.runs) == 1 else 's'}, "
        f"{L.runs_phase_restoring} of which restore the grid phase"
        + ("." if L.phase_intact else " — **the phase slipped**."),
        f"- **Inferred link loss** {loss.inferred_lost} messages "
        f"({loss.rate:.3%}): {loss.inferred_lost_note_ons} note-ons inferred "
        f"from orphan releases, {loss.inferred_lost_note_offs} note-offs "
        f"inferred from re-strikes. Note-on/release balance "
        f"{loss.balance:+d}. The capture tool reported "
        f"`dropped {loss.reported_dropped}`. {LOSS_CAVEAT}",
        f"- **Verdict** {r.verdict}",
        f"- **Index** `{store.index}` — derived, deletable, rebuilt by "
        f"`python -m fp30x_studio.pipeline ingest {r.name}`.",
    ]
    return "\n".join(lines) + "\n"


def append_provenance(store, *, path: str | Path | None = None,
                      dry_run: bool = False, force: bool = False) -> str | bool:
    """Append this take's entry to ``PROVENANCE.md``. Once, unless forced.

    Returns the text under ``dry_run``, otherwise True if it wrote and False if
    the take was already recorded.
    """
    text = entry_for(store)
    if dry_run:
        return text
    target = Path(path) if path else core.takes_dir() / "PROVENANCE.md"
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if not force and f"<!-- {MARKER}: {store.meta['name']} -->" in existing:
        return False
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if f"## Per-take integrity, appended by the pipeline" not in existing:
        existing += (
            "\n## Per-take integrity, appended by the pipeline\n"
            "\nOne block per take, written by "
            "`fp30x_studio.pipeline` the first time the take's stream is known "
            "to have ended. These are readings on the *link*, not on the "
            "playing.\n")
    target.write_text(existing + text, encoding="utf-8")
    return True
