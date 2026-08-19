"""A pragmatic Plaine & Easie (PAE) decoder: incipit code -> MIDI pitches.

RISM stores 2.5 million musical incipits as PAE strings in MARC field 031.
PAE is a compact ASCII notation; this decoder extracts what a melodic matcher
needs -- an ordered pitch sequence with relative durations -- and deliberately
ignores engraving detail (beams, slurs, fermatas, repeat structure, text).

It is tolerant by design: unknown characters are skipped rather than raised on,
because a partly-decoded incipit is still a usable search key and RISM's data
is decades of hand entry by hundreds of libraries.

Reference: Plaine & Easie Code, https://www.iaml.info/plaine-easie-code
"""
from __future__ import annotations

import re

# Duration code -> length in quarter notes.
DURATION = {"0": 8.0, "1": 4.0, "2": 2.0, "4": 1.0, "8": 0.5,
            "6": 0.25, "3": 0.125, "5": 0.0625, "7": 0.03125, "9": 0.015625}

STEP = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# 'C is c' = middle C = MIDI 60; each extra apostrophe is an octave up,
# each comma an octave down from c (MIDI 48).
_OCT_UP, _OCT_DOWN = "'", ","


def parse_keysig(keysig: str) -> dict[str, int]:
    """'bBEA' -> {'B': -1, 'E': -1, 'A': -1};  'xFCG' -> {'F': 1, ...}."""
    out: dict[str, int] = {}
    if not keysig:
        return out
    sign = 0
    for ch in keysig.strip():
        if ch == "b":
            sign = -1
        elif ch == "x":
            sign = 1
        elif ch == "n":
            sign = 0
        elif ch.upper() in STEP and sign:
            out[ch.upper()] = sign
    return out


# '=12' is a multi-measure rest count, not two duration codes -- drop it first.
_MEASURE_REST = re.compile(r"=\d*")
_STRIP = re.compile(r"\{|\}|\[|\]|\||\?|%|\$|&|~|\"|`|@|:|;|\*|<|>|#")


def decode(data: str, keysig: str = "", max_notes: int = 96
           ) -> tuple[list[int], list[float]]:
    """Decode a PAE @data string.

    Returns (midi_pitches, onsets_in_quarter_notes).  Chords collapse to their
    highest note, which matches how the rest of this package reduces polyphony.
    """
    if not data:
        return [], []
    s = _STRIP.sub("", _MEASURE_REST.sub("", data))

    pitches: list[int] = []
    onsets: list[float] = []
    t = 0.0
    dur = 1.0                     # current duration, quarters
    octave = 4                    # 'C
    ks = parse_keysig(keysig)
    local: dict[str, int] = {}    # measure-local accidentals
    pending_acc: int | None = None
    pending_oct: int | None = None
    grace = False
    tie_open = False
    tuplet = 1.0
    i, n = 0, len(s)

    while i < n and len(pitches) < max_notes:
        ch = s[i]

        if ch == _OCT_UP:
            j = i
            while j < n and s[j] == _OCT_UP:
                j += 1
            pending_oct = 3 + (j - i)          # ' -> 4, '' -> 5, ...
            i = j
            continue
        if ch == _OCT_DOWN:
            j = i
            while j < n and s[j] == _OCT_DOWN:
                j += 1
            pending_oct = 4 - (j - i)          # , -> 3, ,, -> 2, ...
            i = j
            continue
        if ch in DURATION:
            dur = DURATION[ch]
            i += 1
            # dots
            while i < n and s[i] == ".":
                dur *= 1.5
                i += 1
            continue
        if ch in "xbn":
            acc = {"x": 1, "b": -1, "n": 0}[ch]
            j = i + 1
            while j < n and s[j] == ch and ch in "xb":
                acc *= 2
                j += 1
            pending_acc = acc
            i = j
            continue
        if ch == "-":                          # rest
            t += dur * tuplet
            i += 1
            continue
        if ch == "/":                          # barline: local accidentals expire
            local.clear()
            i += 1
            continue
        if ch in "qg":                         # grace note follows
            grace = True
            i += 1
            continue
        if ch == "r":                          # end of grace group
            grace = False
            i += 1
            continue
        if ch == "+":                          # tie
            tie_open = True
            i += 1
            continue
        if ch == "(":
            m = re.match(r"\((\d+)", s[i:])
            tuplet = 2.0 / 3.0 if not m else (2.0 / int(m.group(1)) if int(m.group(1)) else 1.0)
            i += 1 + (len(m.group(1)) if m else 0)
            continue
        if ch == ")":
            tuplet = 1.0
            i += 1
            continue
        if ch == "^":                          # next note joins the current chord
            i += 1
            if i < n:
                # decode the chord tone, keep the higher of the two
                sub, _ = decode(s[i:i + 6].split("^")[0], keysig, max_notes=1)
                if sub and pitches:
                    pitches[-1] = max(pitches[-1], sub[0])
            continue

        up = ch.upper()
        if up in STEP:
            if pending_oct is not None:
                octave = pending_oct
                pending_oct = None
            if pending_acc is not None:
                local[up] = pending_acc
                pending_acc = None
            alter = local.get(up, ks.get(up, 0))
            midi = 12 * (octave + 1) + STEP[up] + alter
            if 0 <= midi <= 127:
                if tie_open and pitches and pitches[-1] == midi:
                    tie_open = False            # extend, do not re-attack
                    t += dur * tuplet
                    i += 1
                    continue
                pitches.append(midi)
                onsets.append(round(t, 5))
                if not grace:
                    t += dur * tuplet
            tie_open = False
            i += 1
            continue

        i += 1                                  # unknown -> skip

    return pitches, onsets
