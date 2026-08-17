"""The read-query layer: every repeated measurement as a small fold.

The point of the registry
-------------------------
Each of these was a throwaway script once. Several were written more than once
in a day, with slightly different pairing rules each time, which is how a take
ends up with two different note counts depending on who asked. Here a new
measurement is a decorated function of one argument -- the store -- and it
inherits the pairing, the defect accounting and the timing caveats for free.

    @query("attack-density", "onsets per second in a sliding window",
           timing_sensitive=True)
    def attack_density(store, window: float = 1.0):
        ...
        return Result(...)

Timing-sensitive queries
------------------------
A query marked ``timing_sensitive`` gets a loud note attached whenever the take
it ran against is not hardware-stamped -- the five pre-2026-08-17 poll-loop
takes, or any ``.fp30`` in which CoreMIDI never stamped a packet. The note
travels with the result rather than living in a README, because the failure mode
being designed out is exactly a number from an untrustworthy take being quoted
next to one from a good take.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Callable

from .. import core
from .integrity import LATTICE_NS, LOSS_CAVEAT
from .integrity import report as integrity_report

__all__ = ["Result", "Query", "QUERIES", "query", "run"]


@dataclass(slots=True)
class Result:
    name: str
    columns: tuple[str, ...]
    rows: list[tuple]
    notes: list[str] = field(default_factory=list)

    def text(self, *, limit: int | None = 40) -> str:
        rows = self.rows if limit is None else self.rows[:limit]
        widths = [len(c) for c in self.columns]
        cells = [[_fmt(v) for v in r] for r in rows]
        for r in cells:
            for i, v in enumerate(r):
                widths[i] = max(widths[i], len(v))
        out = ["  ".join(c.ljust(w) for c, w in zip(self.columns, widths)).rstrip(),
               "  ".join("-" * w for w in widths)]
        out += ["  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip()
                for r in cells]
        if limit is not None and len(self.rows) > limit:
            out.append(f"... {len(self.rows) - limit} more rows")
        out += [f"note: {n}" for n in self.notes]
        return "\n".join(out)


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


@dataclass(slots=True)
class Query:
    name: str
    doc: str
    fn: Callable
    timing_sensitive: bool = False


QUERIES: dict[str, Query] = {}


def query(name: str, doc: str, *, timing_sensitive: bool = False):
    def deco(fn):
        QUERIES[name] = Query(name=name, doc=doc, fn=fn,
                              timing_sensitive=timing_sensitive)
        return fn
    return deco


def run(store, name: str, **kwargs) -> Result:
    """Run a named query, attaching whatever caveats this take earns."""
    if name not in QUERIES:
        raise KeyError(f"no query {name!r}; have {', '.join(sorted(QUERIES))}")
    q = QUERIES[name]
    res = q.fn(store, **kwargs)
    if q.timing_sensitive and not store.timing_trusted:
        res.notes.insert(0, (
            f"TIMING NOT TRUSTED for this take ({store.meta['timing_grade']}): "
            f"{store.meta['timing_note']} Every number above depends on "
            f"timestamps. Do not compare it with a hardware-stamped take."))
    if not store.accounted:
        res.notes.insert(0, "MESSAGES UNACCOUNTED FOR: the index is incomplete.")
    d = store.defect_counts()
    lost = d.get("orphan_note_off", 0) + d.get("restrike_before_note_off", 0)
    if lost:
        res.notes.append(
            f"{lost} messages inferred lost on the link "
            f"({d.get('orphan_note_off', 0)} note-ons, "
            f"{d.get('restrike_before_note_off', 0)} note-offs). {LOSS_CAVEAT}")
    return res


# -- helpers ----------------------------------------------------------------

def _stats(values: list[float]) -> list[tuple]:
    if not values:
        return [("n", 0)]
    v = sorted(values)
    q = statistics.quantiles(v, n=100) if len(v) > 1 else [v[0]] * 99
    return [
        ("n", len(v)), ("min", v[0]), ("p10", q[9]), ("median", statistics.median(v)),
        ("mean", statistics.fmean(v)), ("p90", q[89]), ("max", v[-1]),
        ("stdev", statistics.pstdev(v)),
    ]


def _histogram(values: list[int], width: int) -> list[tuple]:
    buckets: dict[int, int] = {}
    for x in values:
        buckets[x // width] = buckets.get(x // width, 0) + 1
    total = len(values) or 1
    return [(b * width, b * width + width - 1, n, n / total)
            for b, n in sorted(buckets.items())]


# -- the queries ------------------------------------------------------------

@query("census", "message-type census, by wire type")
def _census(store) -> Result:
    rows = [(k, n, n / max(1, store.count("message")))
            for k, n in store.census().items()]
    notes = [
        f"polytouch (0xAn) {store.census().get('polytouch', 0)}, "
        f"channel pressure (0xDn) {store.census().get('aftertouch', 0)}: the "
        f"FP-30X's action is discrete-event, it sends no continuous key pressure.",
        f"{store.count('packet')} packets carried these "
        f"{store.count('message')} messages; a reader that takes only the first "
        f"message of a packet silently loses the rest.",
    ]
    return Result("census", ("kind", "n", "fraction"), rows, notes)


@query("roles", "how every message was accounted for by the pairing layer")
def _roles(store) -> Result:
    rows = [(r, n) for r, n in store.role_counts().items()]
    total = sum(n for _, n in rows)
    notes = [f"{total} roles issued for {store.count('message')} messages -- "
             f"{'complete' if store.accounted else 'INCOMPLETE'}."]
    return Result("roles", ("role", "n"), rows, notes)


@query("defects", "every defect, by class, with the first few instances")
def _defects(store, cls: str | None = None, limit: int = 20) -> Result:
    if cls:
        rows = [(d["cls"], round(store.seconds(d["ns"]), 6),
                 core.note_name(d["note"]) if d["note"] is not None else "",
                 d["msg_seq"], d["detail"])
                for d in list(store.defects(cls))[:limit]]
        return Result("defects", ("class", "t", "note", "msg_seq", "detail"), rows)
    counts = store.defect_counts()
    from .pairing import DEFECT_CLASSES
    rows = [(c, counts.get(c, 0)) for c in DEFECT_CLASSES]
    rows += [(f"closure:{k}", v) for k, v in store.closure_counts().items()]
    return Result("defects", ("class", "n"), rows,
                  ["`closure:*` counts what ended each interval; only "
                   "`closure:note_off` is a measured release."])


@query("integrity", "the full link-integrity report")
def _integrity(store) -> Result:
    r = integrity_report(store)
    return Result("integrity", ("reading",), [(ln,) for ln in r.lines()])


@query("strike-velocity", "distribution of note-on velocities")
def _strike_velocity(store) -> Result:
    v = [r["velocity_on"] for r in store.intervals()]
    rows = _stats([float(x) for x in v])
    rows += [("--histogram (width 8)--", "")]
    rows += [(f"{lo}-{hi}", f"{n} ({f:.1%})")
             for lo, hi, n, f in _histogram(v, 8)]
    return Result("strike-velocity", ("statistic", "value"), rows)


@query("release-velocity", "distribution of note-off velocities")
def _release_velocity(store) -> Result:
    v = [r["velocity_off"] for r in store.intervals(trusted_only=True)
         if r["velocity_off"] is not None]
    rows = _stats([float(x) for x in v])
    rows += [("--histogram (width 8)--", "")]
    rows += [(f"{lo}-{hi}", f"{n} ({f:.1%})")
             for lo, hi, n, f in _histogram(v, 8)]
    n_all = store.count("interval")
    return Result("release-velocity", ("statistic", "value"), rows, [
        f"{len(v)} of {n_all} intervals have a measured release velocity; the "
        f"rest were closed by a re-strike or by the end of the take and have "
        f"no release to report. Filtering to those is a biased subsample.",
    ])


@query("durations", "interval durations b - a", timing_sensitive=True)
def _durations(store, trusted_only: bool = True) -> Result:
    d = [(r["ns_off"] - r["ns_on"]) / 1e9
         for r in store.intervals(trusted_only=trusted_only)]
    rows = _stats(d)
    longest = sorted(store.intervals(trusted_only=trusted_only),
                     key=lambda r: r["ns_on"] - r["ns_off"])[:5]
    rows += [("--longest--", "")]
    rows += [(f"{core.note_name(r['note'])} at t={store.seconds(r['ns_on']):.3f}",
              f"{(r['ns_off'] - r['ns_on']) / 1e9:.3f} s [{r['closure']}]")
             for r in longest]
    return Result("durations", ("statistic", "value"), rows, [
        f"trusted_only={trusted_only}: "
        + ("only intervals a real note-off closed. An interval closed by a "
           "re-strike or by the end of the take has an endpoint that was never "
           "played, so its duration is not a duration."
           if trusted_only else
           "INCLUDES intervals whose closing endpoint was inferred, not played."),
    ])


@query("polyphony", "polyphony as a function of t, with dwell times",
       timing_sensitive=True)
def _polyphony(store) -> Result:
    events: list[tuple[int, int]] = []
    for r in store.intervals():
        events.append((r["ns_on"], 1))
        events.append((r["ns_off"], -1))
    events.sort()
    dwell: dict[int, int] = {}
    spans: dict[int, int] = {}
    level = 0
    # Anchored to the take's own bounds, not to the first onset, so the level-0
    # dwell includes the silence before the first note and after the last.
    prev = store.origin_ns
    last_ns = store.checkpoint["last_ns"] or prev
    peak_at = 0
    peak = 0
    i = 0
    while i < len(events):
        ns = events[i][0]
        dwell[level] = dwell.get(level, 0) + (ns - prev)
        while i < len(events) and events[i][0] == ns:
            level += events[i][1]
            i += 1
        spans[level] = spans.get(level, 0) + 1
        if level > peak:
            peak, peak_at = level, ns
        prev = ns
    dwell[level] = dwell.get(level, 0) + max(0, last_ns - prev)
    total = sum(dwell.values()) or 1
    rows = [(lv, dwell.get(lv, 0) / 1e9, dwell.get(lv, 0) / total,
             spans.get(lv, 0))
            for lv in sorted(set(dwell) | set(spans))]
    return Result("polyphony", ("level", "dwell_s", "fraction", "n_spans"), rows, [
        f"peak polyphony {peak} at t={store.seconds(peak_at):.3f} s.",
        "Level 0 dwell is silence: the complement of supp f, and exactly where "
        "the cumulative F is flat.",
    ])


@query("support", "the support measure of f and the plateau structure of F",
       timing_sensitive=True)
def _support(store) -> Result:
    perf = store.to_performance()
    sup = perf.support()
    plat = perf.plateaus()
    rows = [
        ("lambda(supp f)", perf.support_measure()),
        ("take length", perf.t_end - perf.t_start),
        ("duty cycle", perf.support_measure() / max(1e-12,
                                                    perf.t_end - perf.t_start)),
        ("components of supp f", len(sup)),
        ("plateaus of F", len(plat)),
        ("plateau measure", perf.plateau_measure()),
        ("sum_k lambda(supp f_k)", sum(t.support_measure() for t in perf.tracks)),
        ("integral of f (energy)", perf.energy()),
    ]
    return Result("support", ("quantity", "value"), rows, [
        "supp f is the union over keys, so lambda(supp f) <= sum_k "
        "lambda(supp f_k) with equality iff the playing is never polyphonic.",
        "F is constant exactly on the plateaus; they are the components of the "
        "complement of supp f inside the take.",
    ])


@query("variation", "the total variation of the cumulative F, per norm")
def _variation(store) -> Result:
    perf = store.to_performance()
    rows = [(n, perf.total_variation(norm=n)) for n in ("l1", "l2", "linf")]
    rows.append(("sum over keys of |Df_k|(R)",
                 sum(t.total_variation() for t in perf.tracks)))
    rows.append(("2 * sum of velocities",
                 2.0 * sum(s.velocity for s in perf.strikes())))
    return Result("variation", ("norm", "value"), rows, [
        "|Df|(R) in the l1 norm is 2 * sum P_i when no two atoms coincide; the "
        "gap between the last two rows is exactly the cancellation from "
        "simultaneous onsets and releases.",
    ])


@query("onsets", "inter-onset distribution, and its residue on the BLE lattice",
       timing_sensitive=True)
def _onsets(store) -> Result:
    ons = store.note_onsets()
    gaps = [b - a for a, b in zip(ons, ons[1:])]
    rows = _stats([g / 1e9 for g in gaps])
    on_lat = sum(1 for g in gaps if g % LATTICE_NS == 0)
    zero = sum(1 for g in gaps if g == 0)
    rows += [
        ("--lattice--", ""),
        ("gaps that are exact multiples of 5 ms", f"{on_lat}/{len(gaps)}"),
        ("gaps recorded as exactly 0", zero),
        ("smallest non-zero gap (ms)", min((g / 1e6 for g in gaps if g), default=0)),
    ]
    return Result("onsets", ("statistic", "value"), rows, [
        "A zero gap means two onsets arrived in the same BLE connection event "
        "or the same packet. Below the 5 ms connection interval this capture "
        "cannot separate them; that is a floor on the link, not a fact about "
        "the playing.",
    ])


@query("lattice", "the 5 ms arrival lattice, gap by gap", timing_sensitive=True)
def _lattice(store) -> Result:
    from .integrity import lattice as measure
    L = measure(store.packet_ns())
    rows = [
        ("step (ms)", L.step_ns / 1e6),
        ("gaps", L.n_gaps),
        ("on lattice", L.n_on_lattice),
        ("fraction", L.fraction),
        ("observed gcd (ms)", L.observed_gcd_ns / 1e6),
        ("off-lattice runs", len(L.runs)),
        ("runs restoring phase", L.runs_phase_restoring),
    ]
    rows += [("--runs (first, last, total ms, phase restored)--", "")]
    rows += [(f"{a}-{b}", f"{tot / 1e6:g} ms  {'restored' if ok else 'SLIPPED'}")
             for a, b, tot, ok in L.runs[:20]]
    return Result("lattice", ("quantity", "value"), rows, [
        "A run that restores the phase is one packet late and the next early: "
        "jitter, nothing lost. A run that does not is a genuine anomaly.",
    ])


@query("keys", "per-key interval sets")
def _keys(store) -> Result:
    rows = []
    for r in store.db.execute(
            "SELECT note, COUNT(*) n, SUM(ns_off - ns_on) held, "
            "AVG(velocity_on) v, SUM(trusted) tr FROM interval "
            "GROUP BY note ORDER BY note"):
        rows.append((r["note"], core.note_name(r["note"]), r["n"],
                     r["held"] / 1e9, r["held"] / 1e9 / r["n"], r["v"],
                     r["n"] - r["tr"]))
    return Result("keys",
                  ("note", "name", "n", "held_s", "mean_s", "mean_vel",
                   "untrusted"), rows,
                  [f"{len(rows)} of 88 keys were used."])


@query("key", "every interval on one key: --key 60 or --key C4")
def _key(store, key: str | int = 60) -> Result:
    note = _as_note(key)
    rows = [(round(store.seconds(r["ns_on"]), 6),
             round(store.seconds(r["ns_off"]), 6),
             (r["ns_off"] - r["ns_on"]) / 1e9, r["velocity_on"],
             r["velocity_off"] if r["velocity_off"] is not None else "-",
             r["closure"])
            for r in store.intervals(note=note)]
    return Result(f"key {core.note_name(note)}",
                  ("a", "b", "b-a", "P_on", "P_off", "closure"), rows)


def _as_note(key: str | int) -> int:
    if isinstance(key, int):
        return key
    s = str(key).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    for n in range(21, 109):
        if core.note_name(n).upper() == s.upper():
            return n
    raise ValueError(f"{key!r} is not a MIDI note number or a note name")
