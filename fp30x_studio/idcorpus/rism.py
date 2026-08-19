"""Stream the RISM MARCXML source dump and emit decoded musical incipits.

    python -m fp30x_studio.idcorpus.rism \
        --dump ~/workspace/audio/corpus/raw/rism-source-latest.xml.gz \
        --out  ~/workspace/audio/corpus/raw/rism-incipits.jsonl

RISM (Repertoire International des Sources Musicales) publishes its full
catalogue monthly as gzipped MARCXML under CC BY 3.0 -- attribution required,
commercial use permitted.  Field 031 carries the musical incipit in Plaine &
Easie; this is the closest openly-licensed equivalent to the Barlow &
Morgenstern *Dictionary of Musical Themes*, which is NOT usable (its 1948
copyright was renewed in 1975 and runs to 2043).

The dump decompresses to several gigabytes, so it is streamed with iterparse
and never held in memory or written out in full.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from .pae import decode

MARC = "{http://www.loc.gov/MARC21/slim}"
MIN_NOTES = 6

LICENCE = "CC BY 3.0 Unported (attribution required; commercial use permitted)"
ATTRIBUTION = ("Repertoire International des Sources Musicales (RISM), "
               "https://rism.info - source dump, CC BY 3.0")


def _subs(field: ET.Element) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sf in field.findall(f"{MARC}subfield"):
        code = sf.get("code") or ""
        out.setdefault(code, []).append((sf.text or "").strip())
    return out


def _first(d: dict[str, list[str]], code: str, default: str = "") -> str:
    v = d.get(code)
    return v[0] if v else default


_DATES = re.compile(r"\s*,?\s*\(?\d{3,4}[-–—]\d{0,4}\)?\s*$")
_CAT = re.compile(r"(?i)\b(BWV|K\.?V?|Hob\.?|D\.?|WoO|S\.?|L\.?|Op\.?)\s*[\dIVXL]")


def _clean_name(s: str) -> str:
    s = _DATES.sub("", (s or "").strip().rstrip(",.")).strip()
    return s


def records(dump: Path):
    """Yield one dict per decoded incipit."""
    opener = gzip.open if str(dump).endswith(".gz") else open
    with opener(dump, "rb") as fh:
        for event, el in ET.iterparse(fh, events=("end",)):
            if el.tag != f"{MARC}record":
                continue
            try:
                yield from _one(el)
            except Exception:  # noqa: BLE001 - never let one bad record stop the stream
                pass
            el.clear()


def _one(rec: ET.Element):
    ctrl = {c.get("tag"): (c.text or "") for c in rec.findall(f"{MARC}controlfield")}
    rid = ctrl.get("001", "").strip()

    composer = title = uniform = key = genre = date = ""
    opus = ""
    incipits: list[dict] = []

    for f in rec.findall(f"{MARC}datafield"):
        tag = f.get("tag")
        if tag == "100":
            composer = _clean_name(_first(_subs(f), "a"))
        elif tag == "240":
            d = _subs(f)
            uniform = _first(d, "a")
            n = _first(d, "n")
            key = _first(d, "r")
            if n:
                uniform = f"{uniform}, {n}"
        elif tag == "245" and not title:
            d = _subs(f)
            title = _first(d, "a")
            b = _first(d, "b")
            if b:
                title = f"{title} {b}"
        elif tag == "260" and not date:
            date = _first(_subs(f), "c")
        elif tag in ("650", "690") and not genre:
            genre = _first(_subs(f), "a")
        elif tag == "031":
            d = _subs(f)
            if _first(d, "2", "pe").lower() not in ("pe", "pae", ""):
                continue
            data = _first(d, "p")
            if not data:
                continue
            incipits.append(dict(
                data=data, keysig=_first(d, "n"), timesig=_first(d, "o"),
                clef=_first(d, "g"),
                caption=_first(d, "d") or _first(d, "t"),
                work=_first(d, "a"), movement=_first(d, "b"), number=_first(d, "c"),
            ))

    if not incipits:
        return
    work_title = uniform or title
    if not work_title:
        return
    m = _CAT.search(work_title)
    if m:
        tail = work_title[m.start():]
        opus = tail.split(";")[0].split(",")[0].strip()

    for inc in incipits:
        pitches, onsets = decode(inc["data"], inc["keysig"])
        if len(pitches) < MIN_NOTES:
            continue
        mv = " ".join(x for x in (inc["movement"], inc["caption"]) if x).strip()
        yield dict(
            rism_id=rid, composer=composer or "Anonymous",
            title=work_title, source_title=title,
            opus=opus, key=key, genre=genre, date=date,
            movement=mv, movement_number=inc["movement"],
            timesig=inc["timesig"], clef=inc["clef"],
            pitches=pitches, onsets=onsets,
            pae=inc["data"],
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    n = 0
    seen_works = set()
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in records(a.dump):
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            seen_works.add((r["composer"], r["title"]))
            n += 1
            if n % 100000 == 0:
                print(f"  {n} incipits, {len(seen_works)} distinct works", flush=True)
            if a.limit and n >= a.limit:
                break
    print(f"wrote {n} incipits ({len(seen_works)} distinct composer/title pairs) -> {a.out}")


if __name__ == "__main__":
    main()
