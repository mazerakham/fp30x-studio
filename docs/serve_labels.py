"""Serve the labelling page on loopback, and take his answers back.

    ~/workspace/audio/.venv/bin/python docs/serve_labels.py

Why a server and not a `file://` page, or the one static server on 8777: the
page has to *write*. Every other number in the identification project is
unvalidated, and the only thing that fixes that is Jake listening to a snippet
and saying yes or no. A static file cannot receive that answer, so this speaks
two verbs a static server does not:

    GET  /segments.json   every segment of every take, with the identifier's
                          proposal, its runner-up, and the notes themselves.
                          Rebuilt from disk, so a take still being recorded
                          shows up with whatever has landed so far.
    POST /label           one segment's verdict, appended to labels.json the
                          moment he presses the key. Not on a final submit --
                          he closes tabs.

Cost. Building the theme index over the 70k-theme corpus takes ~7 s and
replaying a 44-minute take through the identifier takes longer than a browser
will wait, so the build runs on a background thread and the page polls. Results
are cached on disk under ``.label-cache/`` keyed by (name, size, mtime): a
restart is instant, and a take that has grown by one note is the only take
recomputed.

Nothing here writes to a ``.fp30``. The takes are primary evidence.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fp30x_studio.identify.corpus import load_corpus  # noqa: E402
from fp30x_studio.identify.identifier import replay_all  # noqa: E402
from fp30x_studio.identify.matcher import (MIN_CONFIDENCE, ThemeIndex,  # noqa: E402
                                           best_candidate)
from fp30x_studio.pipeline.pairing import SUSTAIN_CC  # noqa: E402

HERE = Path(__file__).resolve().parent
TAKES = Path("~/Music/FP-30X Studio/takes").expanduser()
LABELS = TAKES / "labels.json"
PROVENANCE = TAKES / "PROVENANCE.md"
CACHE = HERE / ".label-cache"
PAGE = "label-segments.html"
PORT = 8792                      # 8791 is the timbre workbench's

#: Hard ceiling on the note events shipped for one segment. A 44-minute take's
#: longest segment is a few thousand notes; the cap exists so that one runaway
#: segment cannot make the page unloadable. The page says when it has bitten.
MAX_EVENTS = 6000

#: Below this a "segment" is a few keys pressed between pieces, not an attempt
#: at anything. They are still listed and still answerable -- hiding evidence is
#: not this app's job -- but they start folded away behind a counted toggle.
FRAGMENT_NOTES = 12
FRAGMENT_S = 3.0

#: What Jake identified himself, before the identifier existed. These are not
#: machine proposals and are marked as such on the page -- he is still asked to
#: confirm them, because a label nobody re-checked is the thing this app exists
#: to stop.
HUMAN_LABELS = {
    ("2026-08-17-piece.fp30", 0): {
        "composer": "Scott Joplin", "work": "The Strenuous Life",
        "opus": "", "number": "", "year": "1902"},
    ("2026-08-17-open.fp30", 0): {
        "composer": "Scott Joplin", "work": "The Strenuous Life",
        "opus": "", "number": "", "year": "1902"},
}

#: The session take's Chopin, which Jake named by ear. Keyed by start time
#: rather than segment index, because the index depends on how the silences
#: happened to fall and the start time does not.
HUMAN_BY_START = {
    "2026-08-17-session.fp30": [
        (24.52 * 60, 90.0, {"composer": "Chopin", "work": "Nocturne in B major",
                            "opus": "62", "number": "1", "year": ""}),
    ],
}

_index: ThemeIndex | None = None
_build_lock = threading.Lock()
_write_lock = threading.Lock()
_state = {"status": "starting", "detail": "loading corpus", "payload": None}


# ---------------------------------------------------------------- corpus ----

def _ensure_index() -> ThemeIndex:
    global _index
    if _index is None:
        t = time.perf_counter()
        corpus = load_corpus()
        _index = ThemeIndex(corpus)
        print(f"corpus: {len(_index)} themes indexed in "
              f"{time.perf_counter() - t:.1f}s", flush=True)
    return _index


# -------------------------------------------------------------- segments ----

def _human_for(take_name: str, seg_index: int, start_s: float) -> dict | None:
    hit = HUMAN_LABELS.get((take_name, seg_index))
    if hit:
        return dict(hit)
    for want, tol, label in HUMAN_BY_START.get(take_name, []):
        if abs(start_s - want) <= tol:
            return dict(label)
    return None


def _proposal(cand, take_name: str) -> dict | None:
    """The identifier's answer for one segment, gates and all.

    Reported whether or not it cleared :data:`MIN_CONFIDENCE`. A proposal below
    the bar is exactly the case where his answer is worth most -- the runner
    stayed silent, and only he knows whether it should have.

    ``circular`` is the one thing here the identifier does not tell you itself.
    Several corpus themes were derived *from these takes*, so a segment can
    match itself at confidence 0.999 and mean nothing. The page says so rather
    than asking him to confirm a tautology.
    """
    if cand is None:
        return None
    th = cand.theme
    stem = take_name.rsplit(".", 1)[0]
    return {
        "source_note": th.source,
        "circular": bool(th.source and stem in th.source),
        # carried into labels.json so a verdict stays traceable to the corpus
        # that produced the proposal he was answering
        "corpus_themes": len(_index) if _index else 0,
        "source": "machine",
        "theme_id": th.id,
        "composer": th.composer,
        "work": th.work,
        "opus": th.opus or "",
        "number": th.number or "",
        "label": th.label,
        "confidence": round(cand.confidence, 3),
        "hits": cand.hits,
        "density": round(cand.density, 3),
        "margin": round(cand.margin, 3),
        "line": cand.line,
        "runner_up": cand.runner_up,
        "runner_up_hits": cand.runner_up_hits,
        "announced": cand.confidence >= MIN_CONFIDENCE,
    }


def build_take(path: Path) -> dict:
    """Every segment of one take, with proposal and note events.

    Segments come from the identifier's own segmentation -- the same
    :data:`SEGMENT_GAP_NS` cut the live runner uses -- so a label collected here
    is a label about the thing the runner actually judged.
    """
    idx = _ensure_index()
    st_file = path.stat()
    verdicts, ident = replay_all(path, index=idx, chunk=8)
    try:
        store = ident.store
        origin = store.origin_ns
        rows = store.db.execute(
            "SELECT note, ns_on, ns_off, velocity_on FROM interval "
            "ORDER BY ns_on, note").fetchall()
        # The damper pedal, because without it a nocturne resynthesised from
        # note-offs alone is staccato: he lifts the finger and holds the sound
        # with his foot, so the note-off is not when the string stops.
        ped = store.db.execute(
            f"SELECT ns, d2 FROM message WHERE kind = 'control_change' "
            f"AND d1 = {SUSTAIN_CC} ORDER BY seq").fetchall()
        duration = store.duration
        trusted = bool(store.timing_trusted)
        segs = []
        for st in ident._segments:   # read-only: no public accessor for all of them
            seg = st.seg
            if not seg.notes:
                continue
            cand = best_candidate(st.votes, force=True)
            start_ns, last_ns = seg.start_ns, seg.last_ns
            evs = [r for r in rows if start_ns <= r[1] <= last_ns]
            truncated = len(evs) > MAX_EVENTS
            if truncated:
                evs = evs[:MAX_EVENTS]
            notes = [[round((r[1] - start_ns) / 1e6, 1), r[0], r[3],
                      round(((r[2] - r[1]) / 1e6) if r[2] is not None else 0.0, 1)]
                     for r in evs]
            pedal = [[round((p[0] - start_ns) / 1e6, 1), p[1]] for p in ped
                     if start_ns - 2e9 <= p[0] <= last_ns + 2e9]
            start_s = (start_ns - origin) / 1e9
            human = _human_for(path.name, seg.index, start_s)
            prop = _proposal(cand, path.name)
            if human is not None:
                tail = " ".join(x for x in (
                    f"Op. {human['opus']}" if human["opus"] else "",
                    f"No. {human['number']}" if human["number"] else "") if x)
                human = dict(human, source="human", label=", ".join(
                    b for b in (human["composer"], human["work"], tail) if b))
            segs.append({
                "key": f"{path.name}#{seg.index}",
                "take": path.name,
                "take_id": path.stem,
                "segment": seg.index,
                "start_s": round(start_s, 2),
                "end_s": round((last_ns - origin) / 1e9, 2),
                "duration_s": round(seg.seconds, 2),
                "notes": seg.notes,
                "events": notes,
                "pedal": pedal,
                "events_truncated": truncated,
                "events_span_s": round(notes[-1][0] / 1000.0, 2) if notes else 0.0,
                "fragment": seg.notes < FRAGMENT_NOTES or seg.seconds < FRAGMENT_S,
                "proposed": prop,
                "human": human,
            })
        return {
            "take": path.name,
            "take_id": path.stem,
            "bytes": st_file.st_size,
            "mtime": int(st_file.st_mtime),
            "duration_s": round(duration, 2),
            "timing_trusted": trusted,
            "verdicts": len(verdicts),
            "segments": segs,
        }
    finally:
        ident.close()


#: Bumped whenever the shape of a cached take changes, so an old cache is
#: ignored rather than served as if it were current.
SCHEMA = 2


def _cache_path(path: Path) -> Path:
    """Keyed by the take *and* the corpus.

    The corpus is being grown in another window; it went from 51,557 themes to
    122,848 while this was being written. A cache keyed only by the take would
    then serve proposals from a corpus that no longer exists, and he would be
    labelling an answer nobody could reproduce. The theme count is a coarse
    fingerprint, but it is the one that moves.
    """
    st = path.stat()
    n = len(_ensure_index())
    return CACHE / f"{path.stem}-v{SCHEMA}-c{n}-{st.st_size}-{int(st.st_mtime)}.json"


def build_all() -> dict:
    """Every take in the takes directory, cached by (size, mtime)."""
    CACHE.mkdir(exist_ok=True)
    _ensure_index()
    takes = []
    paths = sorted(TAKES.glob("*.fp30"))
    for i, p in enumerate(paths, 1):
        _state["detail"] = f"{p.name} ({i}/{len(paths)})"
        cp = _cache_path(p)
        if cp.exists():
            try:
                takes.append(json.loads(cp.read_text()))
                print(f"  {p.name}: cached", flush=True)
                continue
            except Exception:
                pass
        t = time.perf_counter()
        try:
            d = build_take(p)
        except Exception as exc:      # a live take must not break the build
            print(f"  !! {p.name}: {exc}", flush=True)
            traceback.print_exc()
            takes.append({"take": p.name, "take_id": p.stem, "error": str(exc),
                          "segments": []})
            continue
        cp.write_text(json.dumps(d, separators=(",", ":")))
        for stale in CACHE.glob(f"{p.stem}-*.json"):
            if stale != cp:
                stale.unlink(missing_ok=True)
        takes.append(d)
        print(f"  {p.name}: {len(d['segments'])} segments in "
              f"{time.perf_counter() - t:.1f}s", flush=True)
    segs = [s for t in takes for s in t.get("segments", [])]
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dir": str(TAKES),
        "min_confidence": MIN_CONFIDENCE,
        "max_events": MAX_EVENTS,
        "fragment_notes": FRAGMENT_NOTES,
        "fragment_s": FRAGMENT_S,
        "labels_path": str(LABELS),
        "corpus_themes": len(_index) if _index else 0,
        "takes": [{k: v for k, v in t.items() if k != "segments"} for t in takes],
        "segments": segs,
    }


def _rebuild(force: bool = False) -> None:
    with _build_lock:
        if force:
            for f in CACHE.glob("*.json"):
                f.unlink(missing_ok=True)
        _state["status"] = "building"
        try:
            payload = build_all()
            _state["payload"] = payload
            _state["status"] = "ready"
            _state["detail"] = f"{len(payload['segments'])} segments"
            print(f"ready: {len(payload['segments'])} segments", flush=True)
        except Exception as exc:
            _state["status"] = "error"
            _state["detail"] = str(exc)
            traceback.print_exc()


# ---------------------------------------------------------------- labels ----

def read_labels() -> dict:
    if not LABELS.exists():
        return {"version": 1, "updated": None, "labels": {}}
    try:
        d = json.loads(LABELS.read_text())
        d.setdefault("labels", {})
        return d
    except Exception:
        # never lose his answers to a half-written file
        bad = LABELS.with_suffix(".json.corrupt")
        LABELS.replace(bad)
        print(f"labels.json unreadable, moved to {bad}", flush=True)
        return {"version": 1, "updated": None, "labels": {}}


def write_label(rec: dict) -> dict:
    """Upsert one segment's answer. Atomic: written to a temp file and renamed."""
    with _write_lock:
        doc = read_labels()
        key = rec["key"]
        rec["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        prev = doc["labels"].get(key)
        if prev and prev.get("verdict") != rec.get("verdict"):
            rec["revised_from"] = prev.get("verdict")
            rec["revised_at"] = prev.get("timestamp")
        doc["labels"][key] = rec
        doc["updated"] = rec["timestamp"]
        doc["version"] = 1
        tmp = LABELS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
        os.replace(tmp, LABELS)
        return doc


PROV_MARK = "<!-- fp30x-labels -->"


def append_provenance(doc: dict) -> None:
    """One dated line per labelling session, per the standing rule.

    Rewritten rather than appended-per-answer: he will press dozens of keys and
    a line each would bury the file. The block is replaced in place, so the
    file carries the current count and the date it was last true.
    """
    counts: dict[str, int] = {}
    for rec in doc["labels"].values():
        counts[rec.get("verdict", "?")] = counts.get(rec.get("verdict", "?"), 0) + 1
    tally = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    block = (f"{PROV_MARK}\n"
             f"### labels.json — ground truth, collected by ear\n\n"
             f"- **File** `{LABELS}`, {len(doc['labels'])} segment(s) labelled "
             f"by Jake ({tally}).\n"
             f"- **How** `docs/label-segments.html` served by "
             f"`docs/serve_labels.py`: each segment resynthesised in the "
             f"browser from its own note events, confirmed or corrected by "
             f"listening. Written on every keypress, not on submit.\n"
             f"- **Proposals** come from `fp30x_studio.identify` over the "
             f"{_state['payload']['corpus_themes'] if _state.get('payload') else 0}"
             f"-theme corpus; a proposal below confidence "
             f"{MIN_CONFIDENCE} is shown but was never announced by the live "
             f"runner.\n"
             f"- **Last updated** {time.strftime('%Y-%m-%d %H:%M')}.\n")
    text = PROVENANCE.read_text() if PROVENANCE.exists() else ""
    if PROV_MARK in text:
        head, _, rest = text.partition(PROV_MARK)
        _, _, after = rest.partition("- **Last updated**")
        _, _, after = after.partition("\n")
        text = head + block + after
    else:
        text = text.rstrip() + "\n\n" + block
    PROVENANCE.write_text(text)


# ---------------------------------------------------------------- server ----

class Handler(SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/segments.json":
            if _state["status"] == "ready":
                return self._json({"status": "ready", **_state["payload"]})
            return self._json({"status": _state["status"],
                               "detail": _state["detail"]}, 202)
        if path == "/labels.json":
            return self._json(read_labels())
        if path == "/rebuild":
            # Not forced: the cache is keyed by (size, mtime), so an unchanged
            # take is reused and only the take he has grown since the last scan
            # is replayed. Forcing would cost two minutes to learn nothing.
            force = "force=1" in self.path
            threading.Thread(target=_rebuild, kwargs={"force": force},
                             daemon=True).start()
            return self._json({"status": "rebuilding", "force": force})
        if path == "/":
            self.path = "/" + PAGE
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        if self.path.split("?")[0] != "/label":
            return self._json({"error": "no such endpoint"}, 404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            rec = json.loads(self.rfile.read(n) or b"{}")
            if not rec.get("key"):
                raise ValueError("record has no key")
            doc = write_label(rec)
            append_provenance(doc)
            print(f"labelled {rec['key']}: {rec.get('verdict')}"
                  + (f" -> {rec.get('correction')}" if rec.get("correction") else ""),
                  flush=True)
            return self._json({"ok": True, "count": len(doc["labels"]),
                               "path": str(LABELS)})
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": str(exc)}, 400)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    url = f"http://127.0.0.1:{PORT}/{PAGE}"
    threading.Thread(target=_rebuild, daemon=True).start()
    handler = partial(Handler, directory=str(HERE))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"labelling app on {url}")
    print(f"answers land in {LABELS} on every keypress. Ctrl-C to stop.")
    if "--no-open" not in sys.argv:
        subprocess.Popen(["open", "-a", "Google Chrome", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
