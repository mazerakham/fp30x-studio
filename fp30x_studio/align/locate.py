"""Locate every stretch of a take in the Chopin nocturnes, bar by bar.

Windows of WIN onset events (hop HOP) inside each segment are aligned with
Smith-Waterman (fp30x_studio.align.correspond) against one first edition of
each nocturne. Windowed because a rigid whole-attempt alignment shears
(VALIDATION-score-alignment.md s5.1). For the winning work, a second-best span
elsewhere in the same score is also found, so a literal reprise, which has two
correct bar positions, is reported as ambiguous rather than guessed.
"""
import json, os, pickle, sys, time
from multiprocessing import Pool

from .events import load_segments
from .correspond import correspond

from ..idcorpus.sources import RAW_DIR

K = str(RAW_DIR / "humdrum-chopin-first-editions" / "kern")
WORKS = [
    ("Op. 9 No. 1", "B-flat minor", "009-1-KI-001"), ("Op. 9 No. 2", "E-flat major", "009-1-KI-002"),
    ("Op. 9 No. 3", "B major", "009-1-KI-003"), ("Op. 15 No. 1", "F major", "015-1a-BH-001"),
    ("Op. 15 No. 2", "F-sharp major", "015-1a-BH-002"), ("Op. 15 No. 3", "G minor", "015-1a-BH-003"),
    ("Op. 27 No. 1", "C-sharp minor", "027-1-BH-001"), ("Op. 27 No. 2", "D-flat major", "027-1-BH-002"),
    ("Op. 32 No. 1", "B major", "032-1-Sm-001"), ("Op. 32 No. 2", "A-flat major", "032-1-Sm-002"),
    ("Op. 37 No. 1", "G minor", "037-1-BH-001"), ("Op. 37 No. 2", "G major", "037-1-BH-002"),
    ("Op. 48 No. 1", "C minor", "048-1-BH-001"), ("Op. 48 No. 2", "F-sharp minor", "048-1-BH-002"),
    ("Op. 55 No. 1", "F minor", "055-1-BH-001"), ("Op. 55 No. 2", "E-flat major", "055-1-BH-002"),
    ("Op. 62 No. 1", "B major", "062-1-BH-001"), ("Op. 62 No. 2", "E major", "062-1-BH-002"),
    ("Op. 72 No. 1", "E minor", "072-1-MEIf-001-nocturne"),
]
WIN, HOP, MIN_EVENTS, ALT_GUARD = 120, 60, 40, 20
CACHE = os.path.expanduser("~/.cache/fp30x-studio/nocturne-scores.pkl")
SCORES = None

def load_scores():
    if os.path.exists(CACHE):
        return pickle.load(open(CACHE, "rb"))
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    from .score import load_score
    s = {w: load_score(os.path.join(K, f + ".krn")) for w, _, f in WORKS}
    pickle.dump(s, open(CACHE, "wb"))
    return s

def init():
    global SCORES
    SCORES = pickle.load(open(CACHE, "rb"))

def align(win, events, offset=0):
    try:
        c = correspond(win, events)
    except ValueError:
        return None
    b0, b1 = (int(x) for x in c.span_b)
    return {"score": round(float(c.score), 2), "pairs": len(c.pairs),
            "rate": round(float(c.census()["match_rate_a"]), 3), "b0": b0 + offset, "b1": b1 + offset}

def task(args):
    seg, i0, i1, win = args
    rows = {}
    for w, _, _ in WORKS:
        r = align(win, SCORES[w].events)
        rows[w] = r
    ranked = sorted(((r["score"] if r else 0.0, w) for w, r in rows.items()), reverse=True)
    best_w = ranked[0][1]
    best = rows[best_w]
    runner_score, runner_w = ranked[1]
    out = {"seg": int(seg), "i0": int(i0), "i1": int(i1), "t0": round(float(win[0].t), 2), "t1": round(float(win[-1].t), 2),
           "n_events": len(win), "n_notes": sum(e.n for e in win),
           "work": best_w, "runner_up": runner_w, "runner_score": runner_score}
    if best:
        sc = SCORES[best_w]
        out.update(best)
        out["bars"] = [sc.measures[best["b0"]], sc.measures[best["b1"]]]
        out["margin"] = round(best["score"] / max(runner_score, 1.0), 2)
        # Second-best span elsewhere in the same score: the reprise test.
        ev = sc.events
        alts = []
        lo, hi = best["b0"] - ALT_GUARD, best["b1"] + ALT_GUARD
        if lo >= MIN_EVENTS:
            alts.append(align(win, ev[:lo], 0))
        if len(ev) - hi >= MIN_EVENTS:
            alts.append(align(win, ev[hi:], hi))
        alts = [a for a in alts if a]
        if alts:
            a = max(alts, key=lambda r: r["score"])
            out["alt"] = {**a, "bars": [sc.measures[a["b0"]], sc.measures[a["b1"]]]}
    return out


# ---------------------------------------------------------------- stretches
# Turn located windows into stretches of playing: what, which bars, how many passes.
#
# A window is SURE when its best work outscores the best different work by
# MARGIN_SURE times and matches at least RATE_MIN of its own events. A literal
# reprise gives a second span in the same score scoring within REPRISE of the
# first; that window's bars are then chosen by continuity with its neighbour
# when one is close, and flagged ambiguous when none is.
#
# Within a segment, consecutive windows of one work extend a stretch while the
# bars keep moving forward. A backward jump of more than JUMP bars starts a new
# pass: the player has gone back to work a passage again.


MARGIN_SURE, MARGIN_LIKELY, RATE_MIN, REPRISE, JUMP, NEAR = 3.0, 1.8, 0.25, 0.9, 4, 12

def mmss(s):
    s = int(round(s)); return f"{s//60}:{s%60:02d}"

def confidence(w):
    if "bars" not in w or w.get("rate", 0) < RATE_MIN: return "none"
    if w["margin"] >= MARGIN_SURE: return "sure"
    if w["margin"] >= MARGIN_LIKELY: return "likely"
    return "unsure"

def choose_bars(w, prev):
    """Pick between the primary span and a near-tie reprise span."""
    alt = w.get("alt")
    if not alt or alt["score"] < REPRISE * w["score"]:
        return w["bars"], None
    if prev is not None and prev["work"] == w["work"]:
        ph = prev["bars_used"][1]
        cands = [(abs(b[0] - ph), b) for b in (w["bars"], alt["bars"]) if -JUMP <= b[0] - ph <= NEAR]
        if cands:
            return min(cands)[1], "reprise, chosen by continuity"
    return w["bars"], f"reprise: bars {w['bars'][0]}-{w['bars'][1]} or {alt['bars'][0]}-{alt['bars'][1]}"

def build(data):
    stretches = []
    by_seg = {}
    for w in data["windows"]:
        by_seg.setdefault(w["seg"], []).append(w)
    for seg, ws in sorted(by_seg.items()):
        ws.sort(key=lambda w: w["i0"])
        cur, prev = None, None
        for w in ws:
            conf = confidence(w)
            if conf in ("none", "unsure"):
                w["bars_used"], w["note"] = None, None
                if cur: cur["unsure_windows"] += 1
                continue
            bars, note = choose_bars(w, prev)
            w["bars_used"], w["note"] = bars, note
            new = (cur is None or cur["work"] != w["work"]
                   or bars[0] < cur["last_lo"] - JUMP)
            if new:
                cur = {"seg": seg, "work": w["work"], "t0": w["t0"], "t1": w["t1"],
                       "lo": bars[0], "hi": bars[1], "last_lo": bars[0],
                       "windows": 0, "sure": 0, "unsure_windows": 0, "notes": []}
                stretches.append(cur)
            cur["t1"] = max(cur["t1"], w["t1"])
            cur["lo"], cur["hi"] = min(cur["lo"], bars[0]), max(cur["hi"], bars[1])
            cur["last_lo"] = bars[0]
            cur["windows"] += 1
            cur["sure"] += conf == "sure"
            if note: cur["notes"].append(note)
            prev = w
    return stretches


def locate(take, workers=4, limit=None):
    """Every window of ``take`` located in the nocturnes, plus the stretches built from them."""
    load_scores()
    segs = [s for s in load_segments(take) if len(s.events) >= MIN_EVENTS]
    jobs = []
    for s in segs:
        n = len(s.events)
        starts = list(range(0, max(n - WIN, 0) + 1, HOP))
        if n > WIN and starts[-1] != n - WIN:
            starts.append(n - WIN)
        for a in starts:
            b = min(a + WIN, n)
            jobs.append((s.index, a, b - 1, s.events[a:b]))
    if limit:
        jobs = jobs[:limit]
    with Pool(workers, initializer=init) as p:
        windows = list(p.imap(task, jobs))
    data = {"take": str(take), "win": WIN, "hop": HOP,
            "segments": [{"seg": s.index, "t0": float(s.t0), "t1": float(s.events[-1].t),
                          "events": len(s.events), "notes": s.n_notes} for s in segs],
            "windows": windows,
            "works": [{"work": w, "key": k, "score": f} for w, k, f in WORKS]}
    data["stretches"] = build(data)
    return data


def main(argv=None):
    import argparse, json
    ap = argparse.ArgumentParser(prog="python -m fp30x_studio.align.locate",
                                 description="Locate every stretch of a take in the Chopin nocturnes, bar by bar.")
    ap.add_argument("take")
    ap.add_argument("--json", help="write windows and stretches here")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, help="only the first N windows (smoke test)")
    a = ap.parse_args(argv)
    data = locate(a.take, a.workers, a.limit)
    if a.json:
        json.dump(data, open(a.json, "w"), indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(f"{'from':>6} {'to':>6}  {'work':13s} {'bars':>9}  windows")
    for s in data["stretches"]:
        print(f"{mmss(s['t0']):>6} {mmss(s['t1']):>6}  {s['work']:13s} {s['lo']:>4}-{s['hi']:<4}  {s['windows']}")


if __name__ == "__main__":
    main()
