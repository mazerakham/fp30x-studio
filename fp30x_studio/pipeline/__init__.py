"""The data-enrichment pipeline: ingest once, account for everything, then query.

    raw .fp30 bytes
        |  store.TakeStore.ingest()      incremental, resumable by byte offset
        v
    packets -> messages                 every message numbered and kept
        |  pairing.Pairer                every message given exactly one role
        v
    intervals + defects + accounting     materialised, never recomputed ad hoc
        |  integrity.report()            health readings on the link
        |  queries.run()                 the measurements, as folds
        v
    "is this take any good?"

The three properties worth stating up front, because each one was bought by a
specific failure:

**Nothing is dropped.** Every message leaves the pairing layer with a role, and
the count of roles is checked against the count of messages. A message that is
neither paired nor classified is an assertion failure, not a warning.

**Nothing is recomputed by hand.** The pairing lives in one place and is
materialised. Two answers to the same question cannot disagree because there is
only one pairing, and it is on disk.

**Nothing is inferred silently.** Every interval records which observation
closed it, every take records how far its timestamps can be trusted, and the
inferred link-loss figure is printed with the reason it is the only signal
available -- the capture tool's own drop counter cannot see radio-side loss.

Entry point: ``python -m fp30x_studio.pipeline report <take>``.
"""

from __future__ import annotations

from .integrity import IntegrityReport, LOSS_CAVEAT, Lattice, LossInference
from .integrity import report as integrity_report
from .pairing import DEFECT_CLASSES, ROLES, Defect, Interval, Msg, Pairer
from .provenance import append_provenance
from .queries import QUERIES, Query, Result, query, run
from .store import IngestResult, TakeStore, index_path_for

__all__ = [
    "TakeStore",
    "IngestResult",
    "index_path_for",
    "Pairer",
    "Interval",
    "Defect",
    "Msg",
    "DEFECT_CLASSES",
    "ROLES",
    "integrity_report",
    "IntegrityReport",
    "Lattice",
    "LossInference",
    "LOSS_CAVEAT",
    "QUERIES",
    "Query",
    "Result",
    "query",
    "run",
    "append_provenance",
]
