# FPX — on-disk data and directory format

**Version:** 1.0-draft · **Written:** 2026-08-23 · **Status:** specification, nothing migrated.

This document defines the container that the capture engine, the pipeline, the explorer, the
publisher and the planned native macOS app all read and write. It is written so a second
implementation can be built from it without reading the Python.

Requirements are numbered **FPX-n** and marked **MUST / SHOULD / MAY** in the RFC 2119 sense.
A claim about *current* behaviour cites a file and line. A claim marked **[PROPOSED]** describes
something that does not exist yet. A claim marked **[GUESS]** is my inference about intent and is
listed again in §0.3 and Appendix B.

---

## 0. One page

### 0.1 The tree

```
~/Music/FPX/                                    FPX_HOME. One root, no spaces, no instrument.
│
├── captures/                                   IRREPLACEABLE. Append-only. Never renamed.
│   ├── 2026-08-23T2112Z-important.fpx          the capture. One writer, one file.
│   ├── 2026-08-23T2112Z-important.m1.fpx       mirror writer 1, same stream
│   ├── 2026-08-23T2112Z-important.room.wav     audio witness — proof, not audio
│   ├── PROVENANCE.md                           append-only narrative      KEEP FOREVER
│   ├── labels.json                             Jake's verdicts            KEEP FOREVER
│   └── .cache/<capture>.sqlite3                materialised index      DERIVED · DELETABLE
│
├── performances/                               PUBLICATION. One directory per performance.
│   └── 2026-08-23-take1/
│       ├── fpx.json                            THE MANIFEST. Presence of this file
│       │                                       is what makes a directory a bundle.
│       ├── README.txt                          generated from fpx.json, never authored
│       ├── 2026-08-23-take1.fpx                byte-faithful CLIP of the capture
│       ├── 2026-08-23-take1.mid                derived                DERIVED · DELETABLE
│       ├── 2026-08-23-take1.electric.m4a       derived                DERIVED · DELETABLE
│       └── 2026-08-23-take1.acoustic.m4a       derived                DERIVED · DELETABLE
│
├── masters/                                    CURATED. Hand-chosen exports.  KEEP
│   └── Jake Mirra - <Work> - <BADGE> <M>m<SS>s - <YYYY-MM-DD>.<voice>.<ext>
│
├── assets.json                                 pointers to third-party files, with licences
└── LIBRARY.json                                capture-id → path map, rebuildable
```

Everything under `captures/` except `.cache/` is irreplaceable. Everything under
`performances/` except `fpx.json` and the `.fpx` clip is regenerable from the capture plus the
manifest. `masters/` is neither: it is the human's choice and cannot be recomputed.

### 0.2 The five nouns

| Noun | One line | Identity is |
| --- | --- | --- |
| **Capture** | one continuous run of one writer process against one MIDI input, and the file it wrote | `sha256` of the closed file |
| **Performance** | a maximal span of one capture that is one continuous act of playing one thing | `(stream digest, start_ns, end_ns)` |
| **Segment** | a machine-proposed span from silence-splitting; a *hypothesis* about where performances are | `(capture, start_ns, end_ns, splitter parameters)` — unstable, never a durable key |
| **Clip** | a byte-faithful subrange of a capture, materialised as its own `.fpx` plus entry controller state | `(parent stream digest, window_ns)` |
| **Render** | audio produced from a clip by a named engine and recorded parameters | `sha256` of (clip digest ‖ engine id ‖ canonical parameters) |

Containment: a capture holds zero or more performances; a performance is delimited by one or more
segments and is realised as exactly one clip; a clip yields zero or more renders. A bundle
publishes exactly one performance.

### 0.3 Three decisions I want overruled

1. **`.fpx` as the capture extension, and `~/Music/FPX/` as the root** (FPX-1, FPX-8). I am
   renaming the product's primary noun away from the instrument, which costs every hard-coded path
   in `publish`, `postprocess`, `explorer`, `serve_labels.py`, `identify/runner.py` and the skill,
   and breaks muscle memory on 30 filenames. The gain is that the format stops claiming to be about
   one Roland model when the header already records the instrument
   (`native/fp30x_capture.c:474`). If you do not care about instrument-neutrality, keep `.fp30`
   and delete §1 — nothing else in this document depends on it.

2. **The bundle carries the *clip*, not the whole capture** (FPX-52, FPX-71). Today `publish`
   copies the entire capture into the bundle and renames it to the performance slug
   (`~/.claude/skills/fp30x-recording/bin/publish:78`), so `performances/2026-08-23-take1/
   2026-08-23-take1.fpx` is byte-identical to `takes/2026-08-23-important-take-161623.fp30`
   (verified: both `sha256 a09eb4a6…`) while the bundle's own `README` says the performance is
   only `00:40-end` of it. The name lies about the extent and the capture's own identity is
   destroyed by the rename. My fix puts the clip in the bundle and points at the capture by digest.
   You asked for *"clean recordings in common formats, bundled by performance with the original
   performance data"* (`publish:5-7`) and you may mean the whole capture. If so, keep the copy and
   make FPX-71 require the *original filename* be preserved instead.

3. **Segment indices are abolished as identifiers** (FPX-56, FPX-93). `labels.json` is keyed
   `take.fp30#<segment index>` (`takes/labels.json`, e.g. `2026-08-17-session.fp30#3`), and that
   index is a function of the silence threshold `GAP_S` in `fp30x_studio/segment.py:157`. Change the
   threshold and every one of the 27 stored verdicts silently re-points at a different span.
   Re-keying on `(stream digest, start_ns, end_ns)` fixes that and invalidates ~40 citations of the
   form "`labels.json` `#3`" in `CATALOGUE.md`. You may prefer to keep the index and accept the
   drift; if so, FPX-93 must at least require the splitter parameters be stored alongside.

---

## 1. Names

**FPX-1 (MUST).** The extension for a raw capture is `.fpx`. It names the product, not the
instrument. `native/fp30x_capture.c` writes files named after one Roland model; the model is
already recorded inside the file (`# source FP-30X MIDI Bluetooth`, written at
`fp30x_capture.c:474`), so encoding it in the extension is redundant *and* wrong the moment a
second input path exists — which is the open USB work in `DESIGN-architecture.md` §7.4.

**FPX-2 (MUST).** A reader MUST accept `.fpx`, `.fp30` and `.fp30x` as raw captures and MUST NOT
distinguish them by extension. All three exist in the record: the files on disk are `.fp30`, the
docstrings say `.fp30x` (`fp30x_studio/rawcapture.py:1`, `:300`; `native/benchmark.py:144`), and
`tests/test_rawcapture.py:35` writes fixtures named `t.fp30x`. Content, not suffix, decides.

**FPX-3 (MUST).** A writer MUST emit `.fpx` only.

**FPX-4 (MUST NOT).** No existing capture file is renamed by any tool, ever, including as part of
migration. Filenames are load-bearing keys elsewhere: `labels.json` keys embed them, and
`PROVENANCE.md` blocks are de-duplicated by the HTML marker `<!-- fp30x-pipeline: <name> -->`
(`fp30x_studio/pipeline/provenance.py:42,92`). Renaming a capture orphans both.

**FPX-5 (MUST).** A *bundle* is a directory containing a file named `fpx.json` at its top level.
It is not identified by extension, and it is **not** a macOS package. Rationale: the whole point of
a bundle is that its contents are dragged out — into Messages, into a Drive share, into a video
editor — and a Finder package hides exactly those files.

**FPX-6 (MUST).** The reserved directory names directly under the root are `captures/`,
`performances/`, `masters/`. The reserved file names are `LIBRARY.json` and `assets.json`. The
reserved names inside `captures/` are `PROVENANCE.md`, `labels.json`, `.cache/`. The reserved names
inside a bundle are `fpx.json` and `README.txt`. Nothing else at those levels is reserved, and an
implementation MUST leave unrecognised entries alone rather than deleting or "tidying" them.

**FPX-7 (MUST).** `captures/` replaces `takes/`. "Take" is film vocabulary meaning *one attempt at
a thing*, which is what this spec calls a performance; the file is a recording session that may
hold several. The current tree uses "take" for the file
(`fp30x_studio/pipeline/store.py:59`, table `take`) and that is the root of the
capture/performance conflation this document exists to remove.

**FPX-8 (MUST).** The root is `~/Music/FPX/`, overridable by the environment variable `FPX_HOME`,
and resolved through exactly one function in each implementation.

Defended against the alternatives:

| Candidate | Why not |
| --- | --- |
| `~/Music/FP-30X Studio/` (today) | instrument in the path (same defect as FPX-1); a space and a hyphen that every shell caller must quote and does — `publish:28`, `postprocess:17`, `explorer:28`, `serve_labels.py:51`, `identify/runner.py:41` all carry the quoted literal |
| `~/Library/Application Support/FPX/` | hidden from Finder, and this is irreplaceable user data, not application state. A user who cannot find their recordings without the app has an app-shaped single point of failure |
| `~/Documents/FPX/` | iCloud Desktop & Documents syncs it. "Optimise Mac Storage" then evicts cold originals to the cloud, so a capture may not be on disk when the app opens it. An irreplaceable file MUST NOT live somewhere that can dematerialise it |
| `~/FPX/` | clutters the home root for no gain |
| a user-chosen location | correct eventually, and it is what `FPX_HOME` is for; but a default that is *somewhere specific* is what makes `explorer` and the skill scripts writable at all |

`~/Music` wins because it is where a human looks for music, it is not cloud-managed by default, and
the move from today's layout is one `mv` of one directory rather than 30 file renames (FPX-4).

**FPX-9 (SHOULD).** A capture filename SHOULD be `YYYY-MM-DDTHHMMZ-<slug>.fpx`, where the timestamp
is the UTC instant in `# started_utc`. It is a convenience, never a source of truth: **FPX-10.**

**FPX-10 (MUST NOT).** No tool may derive any fact about a capture from its filename. The record
contains the counter-example: `2026-08-19-usb.fp30` is not a USB take — its own header says
`# source FP-30X MIDI Bluetooth` (verified today, line 8 of the file). Path, instrument, extent and
completeness come from the header and the manifest, never from the name.

**FPX-11 (MUST).** Mirror captures are named `<capture-stem>.m<N>.fpx`, N starting at 1. The
current convention interpolates `-mirror` into the middle of the stem
(`2026-08-23-important-mirror-take-161623.fp30`), which does not sort next to its primary and
cannot express more than one mirror.

**FPX-12 (MUST).** The audio witness is named `<capture-stem>.room.wav` and MUST be labelled
`witness` wherever it is listed. It is evidence that a performance happened and a wall-clock
timeline to align against; it is not a render and MUST NOT be offered as one. Measured on
`takes/2026-08-22-home-room.wav`: first 60 s peak −18.67 dBFS, **RMS −45.68 dBFS**
(`DESIGN-architecture.md` §7.3) — roughly 26 dB below usable.

---

## 2. The capture format

### 2.1 Shape

**FPX-20 (MUST).** A capture is a UTF-8, line-oriented, append-only text file. Every line ends
`\n` (0x0A). There is no `\r`.

**FPX-21 (MUST).** Two line kinds only:

```
comment  := "#" SP key [ SP value ] LF
record   := abs_ns 1*( SP HEXBYTE ) LF
abs_ns   := 1*20DIGIT                    ; absolute nanoseconds, mach clock
HEXBYTE  := 2( "0"-"9" / "A"-"F" )       ; uppercase, two digits, no 0x
```

Records are written by `native/fp30x_capture.c:208-214`. Comments are written by
`:455-465` (open), `:474,481` (sources), `:342` (reconnect) and `:519-524` (close).

**FPX-22 (MUST).** Text stays. The argument, which I am adopting rather than originating
(`fp30x_capture.c:70-72`): MIDI's worst case is a few KB/s, so the encoding costs nothing —
measured, 408,344 bytes for a 27.5-minute take, **0.25 KB/s** — and in exchange a torn write costs
exactly one line, and the file is `tail`-able, `grep`-able and `diff`-able with no tooling. A
binary form would buy nothing at this rate and would cost the property that a half-written file is
still fully readable, which is the property that has actually saved takes on this machine.

**FPX-23 (SHOULD).** Revisit FPX-22 only if a capture path sustains **> 1 MB/min**. Nothing on the
FP-30X approaches it: polyphonic key pressure and channel pressure are both **0** across every take
(`fp30x_studio/pipeline/integrity.py` census; reproduced in every `README.txt` in
`performances/`).

**FPX-24 (MUST).** A record's `abs_ns` is `mach_absolute_time()` ticks converted through
`mach_timebase numer/denom`, both of which are in the header so the conversion is reversible
(`fp30x_capture.c:129-133`). Timestamps within one capture MUST be non-decreasing; a backward step
is the defect `non_monotone_timestamp` (`fp30x_studio/pipeline/pairing.py:96-103`).

**FPX-25 (MUST).** A single record may carry several complete MIDI messages. A reader MUST decode
all of them. Reading only `data[0:3]` has been shipped **twice** — once in the analysis path
(fabricating 157 orphan note-offs and a phantom 37.97 s note) and once in the export path, where it
dropped 135 of 1,930 notes from a master already uploaded to Drive (`DESIGN-architecture.md` §0,
JPA-137/JPA-149). The splitter is `fp30x_studio/rawcapture.py:124-159`; it needs no running-status
state because CoreMIDI guarantees complete messages and forbids running status.

### 2.2 Header keys

The first line MUST be the version line. Every other header key may appear in any order before the
first record.

| Key | Type | Req. | Written at | Meaning |
| --- | --- | --- | --- | --- |
| `fpx-capture` | `v<major>.<minor>` | **MUST** | *(new)* | format version. Legacy files carry `fp30x-capture v1` |
| `columns` | free text | MUST | `:457` | column legend; readers MUST NOT parse it |
| `mach_timebase_numer` | uint32 | MUST | `:458` | tick→ns numerator |
| `mach_timebase_denom` | uint32 | MUST | `:459` | tick→ns denominator |
| `anchor_mach_ns` | uint64 | MUST | `:460` | one mach instant … |
| `anchor_unix_ns` | uint64 | MUST | `:461` | … paired with one `CLOCK_REALTIME` instant |
| `started_utc` | ISO-8601 `Z` | MUST | `:462` | wall clock at open |
| `source` | free text | MUST, ≥1 | `:474` | CoreMIDI display name of each connected endpoint. Repeats |
| `sources_connected` | int ≥ 0 | MUST | `:481` | count. `0` is legal: an honest empty capture |
| `path` | `ble`\|`usb`\|`virtual` | SHOULD **[PROPOSED]** | — | the transport. Never the filename (FPX-10) |
| `writer` | free text | SHOULD **[PROPOSED]** | — | `primary` or `mirror:<N>` |
| `tool` | free text | SHOULD **[PROPOSED]** | — | binary name + build id |

**FPX-26 (MUST).** A reader that meets an unknown header key MUST retain it verbatim, in order,
and MUST NOT fail. Unknown keys are forward compatibility, not errors.

**FPX-27 (MUST).** A reader MUST reject a file whose `fpx-capture` **major** differs from the
majors it implements, and MUST accept any **minor**. New keys and new markers are minor bumps; a
change to the record grammar is a major bump.

**FPX-28 (MUST).** `fp30x-capture v1` is read as `fpx-capture v1.0` forever. All 30 captures on
disk carry it (`fp30x_capture.c:95`, `TOOL_VERSION 1`).

### 2.3 Markers

**FPX-29 (MUST).** Comment lines after the first record are **markers**, and they form an
**ordered list**, not a dictionary.

This is the single most consequential defect in the current reader.
`fp30x_studio/rawcapture.py:326-332` folds every `# key rest` line into `out.header[key] = rest`,
so the last occurrence wins. Measured today on `takes/2026-08-22-evening.fp30`: the file contains
**44 `# setup_changed` lines**, alternating `reconnected 0` and `reconnected 1`, ending in four
`reconnected 0`. Every consumer of that file sees exactly one, and it says zero sources
reconnected. Forty-four link events, reported as one, with the wrong value.

Adopting `DESIGN-architecture.md` §8.2 verbatim, the parse result becomes:

```python
@dataclass(slots=True)
class RawEvent:
    byte_offset: int
    kind: str            # started | source | sources_connected | setup_changed | end
                         # | path | link_lost | link_restored | host_sleep | host_wake
                         # | clipped_from | clip_window_ns | clip_window_rel | clip_entry_cc
    fields: dict[str, str]
```

with `Scan.events: list[RawEvent]` and `Scan.header` retained as a compatibility view over the
*first* occurrence of each key. Existing files parse unchanged.

**FPX-30 (MUST).** Marker vocabulary. All are optional and all MUST be ignorable by an older
reader.

| Marker | Grammar | Status |
| --- | --- | --- |
| `# setup_changed reconnected <n>` | n ≥ 0 | exists (`fp30x_capture.c:342`) |
| `# link_lost <unix_ns> <last_packet_ns>` | | **[PROPOSED]** |
| `# link_restored <unix_ns> reconnected <n>` | | **[PROPOSED]** |
| `# host_sleep <unix_ns>` / `# host_wake <unix_ns>` | | **[PROPOSED]** |
| `# clipped_from <name>` | | exists (`segment.py:243`) |
| `# clip_window_ns <from> <to>` | absolute mach ns | exists (`segment.py:244`) |
| `# clip_window_rel <from_s> <to_s>` | seconds from capture origin | exists (`segment.py:245`) |
| `# clip_entry_cc <controller> <value>` | repeats | exists (`segment.py:247`) |

### 2.4 The trailer, and closure

**FPX-31 (MUST).** A cleanly closed capture ends with

```
# end packets <n> dropped <n> truncated <n> ts_zero <n> stopped_utc <ISO8601Z>
```

written at `fp30x_capture.c:519-524`. Absence of the trailer means the file was cut short or is
still being written — never that it is invalid. `RawCapture.complete` is exactly this test
(`rawcapture.py:207-209`).

**FPX-32 (MUST).** `# end` keys MUST parse as space-separated `key value` pairs and a trailer MUST
carry an even number of tokens.

**Defect, verified today.** `segment.py:248` writes `# end clip` into every clip. Under
`rawcapture.py:329-331` that parses to `{}` because `range(0, len(parts)-1, 2)` is empty for a
one-token rest — *and it replaces the capture's real trailer*, which the clip copied verbatim at
`segment.py:235`. Result, reproduced:

```
$ read("takes/clips/2026-08-17-open.fp30")   trailer {}       complete False
$ read("takes/2026-08-17-open.fp30")         trailer {...}    complete True
```

Every clip on this machine reports "no clean stop" and has done since clipping existed.
**FPX-33 (MUST):** a clip's closure marker is `# end clip 1`, or better, `# clip_end 1` in the
marker namespace, leaving `# end` to mean exactly one thing.

**FPX-34 (MUST).** A reader MUST NOT infer a rest, a silence, or an end-of-performance from a large
inter-packet gap. Measured on a healthy 27.5-minute take with zero reconnect markers: median gap
35.0 ms, p95 365 ms, **max 147.6 s** (`DESIGN-architecture.md` §6.2). A 147-second hole in a take
with nothing wrong with it is indistinguishable, in the packet stream alone, from 147 seconds of
lost music.

**FPX-35 (MUST).** Therefore a *hole* is asserted only by a marker, never by a threshold. A reader
MUST classify each inter-packet gap as:

- **`music`** — no marker inside the gap. The instrument was silent or the player paused. This is
  the default and it is what every gap in every existing file is.
- **`hole`** — a `link_lost`/`host_sleep`/`setup_changed` marker falls inside the gap. Content is
  unknown; extent is known.
- **`unknown`** — the capture's writer version predates markers, *and* the gap exceeds the
  observed p95 by more than 3 orders of magnitude. Reported, never repaired, never silently
  treated as either of the above.

**FPX-36 (MUST).** A note held across a hole is closed at the hole's start with `closure="gap"`.
The pairing layer already records which observation closed each interval and counts only
`closure="note_off"` as a measured release (`pairing.py:224-229`), so `gap` slots into an existing
vocabulary that is already honest.

**FPX-37 (MUST).** A segmenter MUST treat a hole as a hard boundary regardless of duration.

### 2.5 One writer, one file

**FPX-38 (MUST).** A capture file has exactly one writer for its whole life. A writer MUST create
its output with `O_CREAT|O_EXCL` and MUST fail if the path exists.

Today the writer opens `O_WRONLY|O_CREAT|O_APPEND` (`fp30x_capture.c:419`) and two processes
pointed at one path will interleave silently. **That has already happened.** Verified today on
`takes/2026-08-19-usb.fp30`:

```
line     1  # fp30x-capture v1 … anchor_mach_ns 120996048909625 … started_utc 2026-08-20T00:33:29Z
line    10  # fp30x-capture v1 … anchor_mach_ns 120996075220250 … started_utc 2026-08-20T00:33:29Z
line  9855  # end packets 4918 … stopped_utc 2026-08-20T00:43:29Z
line 10688  # end packets 5750 … stopped_utc 2026-08-20T01:21:16Z
backward timestamp steps: 424, the first at line 22
```

Two writers, 26 ms apart, one file. The consequences propagated all the way to a published
artifact: the pipeline grades that capture `SUSPECT: 4088 messages inferred lost (36.631%)` with
2,042 `orphan_note_off` and 2,046 `restrike_before_note_off` — which is not radio loss at all, it
is the ten-minute overlap of the two writers appearing twice. `publish` then produced
`performances/2026-08-19-usb/` containing a **14-byte `.mid`** (a header chunk with no track, so
`mido` raises `EOFError` on it) and two **1-millisecond silent `.m4a`** files, and mirrored the lot
to Google Drive. Nothing in the pipeline objected.

**FPX-39 (MUST).** A reader that finds a second version line, or a second `# end`, in one file MUST
report `multiple_streams` and MUST refuse to produce a `.mid`, a render, or an identifier verdict
from it until the streams are separated into distinct capture files.

**FPX-40 (MUST NOT).** No tool may copy, snapshot, or publish a capture that is still open.
`2026-08-22-piece2.fp30` and `2026-08-22-piece2-mirror.fp30` are mid-capture snapshots of what
later became `2026-08-22-evening*`: identical packet streams (`stream_sha256` `eea3095f…` for both
primaries) but 1,345 fewer bytes of comments and **no `# end` trailer**. They are two more names
for one capture and they are indistinguishable, by extension or by parse, from a capture that was
cut short. Eight of the 30 files in `takes/` today lack a trailer; only one of them
(`2026-08-23-session3*`) is legitimately still being written.

---

## 3. Performance, capture, segment, clip, render

**FPX-50 (MUST).** A **capture** is the file. Its identity is `sha256` over the whole file once
closed. Its *stream identity* is `stream_sha256`, the `sha256` over the record lines only, comments
excluded. Two files with the same `stream_sha256` observed the same MIDI stream; two files with the
same `sha256` are the same file.

Both are needed. Verified today: `takes/2026-08-23-important-take-161623.fp30` and its mirror have
different `sha256` (`a09eb4a6…` vs `a5f385cf…`, because the headers carry different anchors) and
**identical `stream_sha256`** (`ba1dec22…`). That equality is the whole content of "the mirror
corroborates the primary".

**FPX-51 (MUST).** A **performance** is a maximal span of one capture that is one continuous act of
playing one thing. Its identity is `(stream_sha256, start_ns, end_ns)` in absolute mach
nanoseconds. Two performances are the same performance iff those three agree. A performance is
**asserted by a human or by a human-confirmed segment**, not by the segmenter alone (FPX-53).

**FPX-52 (MUST).** A performance is realised on disk as exactly one **clip**: a byte-faithful
subrange of the capture, written with the header copied verbatim and absolute timestamps untouched
(`segment.py:232-241`), plus the `clip_entry_cc` controller state that was in force at the window's
start. Identity `(parent stream_sha256, window_ns)`.

**FPX-53 (MUST).** A **segment** is a machine proposal: a span produced by splitting on silence.
`segment.py:157` splits on `GAP_S`, which is `4.0` seconds (`segment.py:48`). A segment is a *hypothesis about where performances are*, it is
a function of a tunable parameter, and therefore:

**FPX-54 (MUST NOT).** A segment index MUST NOT be used as a durable identifier anywhere — not in
a label key, not in a filename, not in a citation. See §0.3(3).

**FPX-55 (MUST).** Where a segment must be named, it is named by its span in absolute mach
nanoseconds together with the parameters that produced it: `{start_ns, end_ns, splitter:
{gap_s: 4.0, version: 1}}`.

**FPX-56 (SHOULD).** A tool that presents segment indices to a human (e.g. `segment list`) SHOULD
print the absolute span beside them so a human citation can be re-resolved later.

**FPX-57 (MUST).** A **render** is audio derived from a clip by a named engine with recorded
parameters. Its identity is `sha256(clip stream_sha256 ‖ engine_id ‖ canonical JSON of parameters)`.
Two renders with the same identity are byte-identical and one may replace the other.

**FPX-58 (MUST).** Derivability, stated exhaustively.

| Artifact | Class | May a tool delete it? |
| --- | --- | --- |
| `captures/*.fpx`, `*.m<N>.fpx` | **irreplaceable** | **never** |
| `captures/*.room.wav` | **irreplaceable** | **never** |
| `captures/PROVENANCE.md` | **irreplaceable** | never; append only |
| `captures/labels.json` | **irreplaceable** | never |
| `performances/*/fpx.json` | **irreplaceable** (carries human choices) | never |
| `performances/*/*.fpx` (the clip) | **curated** — the window was chosen | only on explicit delete of the performance |
| `masters/*` | **curated** | only on explicit delete |
| `performances/*/README.txt` | derived from `fpx.json` | freely |
| `performances/*/*.mid`, `*.wav`, `*.m4a` | derived | freely |
| `captures/.cache/*.sqlite3` | derived | freely |
| `assets/` soundfonts | derived (re-fetchable) or user-supplied | only if `assets.json` says `refetchable: true` |

**FPX-59 (MUST).** "You cannot re-play a take" (JPA-158). A tool's reclaim-space command MUST
offer only the *derived* rows above, and MUST require a separate, differently-worded confirmation
to remove a curated one. Measured pressure, one 23m32s performance: 408 KB of capture yields
≈1.4 GB of derived data (`DESIGN-architecture.md` §8.1). Reclaim is a real feature.

---

## 4. The manifest

**FPX-70 (MUST).** Every bundle carries `fpx.json` at its top level. A directory without one is not
a bundle and MUST NOT be published, uploaded, or listed as a performance.

**FPX-71 (MUST).** `fpx.json` is UTF-8 JSON, written atomically (`tmp` + `rename(2)`), with
`"fpx_manifest": 1` as its first key.

**FPX-72 (MUST).** The human file `README.txt` is **generated from** `fpx.json` and carries the
line `Generated from fpx.json — do not edit.` It exists because the bundle travels: `publish:134`
`rclone copy`s it to `gdrive-work:FP-30X Studio/performances/<name>`, where it is read by people
with no tooling. Under the house rule for shared folders it MUST open with the four lines
`WHAT / WHO / WHY / STATE`, `STATE` dated.

**FPX-73 (MUST).** Unknown top-level keys are preserved on rewrite. A reader MUST NOT drop what it
does not understand.

### 4.1 Schema

```jsonc
{
  "fpx_manifest": 1,                      // int, MUST, schema major
  "written_utc": "…Z",                    // MUST, ISO-8601 Z
  "written_by": "publish/2.0",            // MUST, tool + version

  "performance": {                        // MUST — the one thing this bundle publishes
    "id": "<stream_sha256>:<start_ns>-<end_ns>",   // MUST, FPX-51
    "slug": "2026-08-23-take1",           // MUST, filesystem-safe
    "start_ns": 253493531000000,          // MUST, absolute mach ns in the parent capture
    "end_ns":   253654576000000,          // MUST
    "duration_s": 160.045,                // MUST, = (end_ns-start_ns)/1e9
    "badge": "SEGMENT",                   // MUST ∈ COMPLETE|SEGMENT|JOINED_LATE|CUT|
                                          //        STITCHED|FRAGMENT|POLL_LOOP|GAP
    "badge_basis": "window is 00:40-end of a longer capture"   // MUST — never an unjustified badge
  },

  "capture": {                            // MUST — provenance of the source
    "file": "2026-08-23-important-take-161623.fpx",  // MUST, the capture's OWN name (FPX-4)
    "sha256": "a09eb4a6…",                // MUST
    "stream_sha256": "ba1dec22…",         // MUST
    "bytes": 35010,                       // MUST
    "header": { … },                      // MUST, every header key verbatim, in order
    "trailer": { … } | null,              // MUST; null means no clean stop
    "closed": true,                       // MUST
    "packet_span_s": 203.8,               // MUST — first to last packet
    "wall_span_s": 240.0,                 // MUST — started_utc to stopped_utc.
                                          //   These differ, sometimes hugely: on
                                          //   2026-08-22-evening they are 1978 s and 70 660 s.
    "markers": [ {"kind":"setup_changed","fields":{"reconnected":"1"},"byte_offset":…} ],  // MUST
    "holes": [],                          // MUST, FPX-35
    "hole_detection": "markers-only",     // MUST ∈ markers-only | none
    "mirrors": [                          // MUST, possibly empty
      { "file":"2026-08-23-important-mirror-take-161623.fpx",
        "sha256":"a5f385cf…", "stream_sha256":"ba1dec22…",
        "relation":"identical",           // identical | subset | superset | divergent
        "same_radio": true,               // MUST — see FPX-113
        "repairs_possible": false }       // MUST — MUST be false when same_radio
    ]
  },

  "integrity": {                          // MUST — the link verdict, not the playing
    "verdict": "GOOD",                    // MUST ∈ GOOD | SUSPECT | EMPTY
    "verdict_text": "589 note-ons and 589 releases, all paired, …",  // MUST
    "timing_grade": "hardware",           // MUST
    "timing_trusted": true,               // MUST
    "census": {"note_on":589,"note_off":589,"control_change":224,"program_change":1},  // MUST
    "packets": 1378, "messages": 1403,    // MUST
    "multi_message_packets": 24, "max_messages_per_packet": 3,       // MUST
    "accounted": true,                    // MUST
    "intervals": 589, "trusted_intervals": 589,                      // MUST
    "defects": { "orphan_note_off":0, "restrike_before_note_off":0,  // MUST — all six classes,
                 "held_at_end":0, "implausible_duration":0,          //   zeros included
                 "non_monotone_timestamp":0, "undecodable_bytes":0 },
    "lattice": {"step_ns":5000000,"n_gaps":1377,"n_on_lattice":1377,
                "fraction":1.0,"runs":0,"runs_phase_restoring":0,"phase_intact":true},  // MUST
    "loss": {"inferred_lost":0,"rate":0.0,"inferred_lost_note_ons":0,
             "inferred_lost_note_offs":0,"balance":0,"reported_dropped":0},             // MUST
    "loss_caveat": "The capture tool's `dropped` counter cannot see this…"              // MUST
  },

  "segments": [                           // MUST — the splitter's proposal, with its parameters
    { "index": 2, "start_ns": …, "end_ns": …, "duration_s": 159.0,
      "notes": 573, "key_fit": {"key":"C maj","fraction":0.93},
      "is_the_performance": true }
  ],
  "segmenter": { "gap_s": 4.0, "version": 1 },   // MUST — without this the indices mean nothing

  "labels": [ … ],                        // MUST, possibly empty. §5 schema, most recent first
  "label_tombstones": [ … ],              // MUST, possibly empty. §5

  "renders": [                            // MUST, possibly empty
    { "file": "2026-08-23-take1.electric.m4a",
      "sha256": "9ecd3a93…", "bytes": 5420811,
      "source": "2026-08-23-take1.mid", "source_sha256": "b48df161…",
      "note_ons_in": 573, "note_ons_out": 573,     // MUST — FPX-77
      "engine": "fluidsynth", "engine_version": "2.6.0",
      "voice": "electric",
      "asset": "jrhodes3c-stereo",                 // MUST — key into assets.json, never a path
      "params": { "sample_rate": 48000, "probe_gain": 0.1, "probe_peak": 0.081390,
                  "gain": 1.094, "reverb": {"wet":0.28,"rt60":2.1},
                  "loudness": null, "codec":"aac", "bitrate":"256k",
                  "movflags":"+faststart" },
      "render_id": "sha256(clip‖engine‖params)" }
  ],

  "files": [                              // MUST — every file in the bundle, no exceptions
    { "name":"2026-08-23-take1.fpx", "sha256":"…", "bytes":34070, "class":"curated" },
    { "name":"2026-08-23-take1.mid", "sha256":"…", "bytes":5462,  "class":"derived" }
  ]
}
```

**FPX-74 (MUST).** `files` covers every regular file in the bundle including `README.txt`, and
excludes only `fpx.json` itself.

**FPX-75 (MUST).** `class` ∈ `irreplaceable | curated | derived`, matching FPX-58.

**FPX-76 (MUST).** `integrity` is copied from the pipeline's report and MUST NOT be recomputed by
the publisher. The producing code is `fp30x_studio/pipeline/integrity.py:report()`; every field
above exists there today.

**FPX-77 (MUST).** `note_ons_in` and `note_ons_out` are mandatory on every render and MUST be
equal. This is the JPA-149 check: nothing today compares a `.mid` to the take it came from, which
is why a 5–9% note drop reached a Drive-hosted master. It is also what catches the
`2026-08-19-usb` bundle, whose `.mid` contains no track at all.

**FPX-78 (MUST).** `params` MUST record every number that affected the audio, including the
probe-derived gain. Today `publish:95-96` computes `G` and prints it to the terminal; it is written
nowhere, so no render on this machine is reproducible from the record. (I recovered the two gains
for the worked example by re-running the probe: `fluidsynth -ni -g 0.1` peaks at 0.081390 electric
and 0.021149 acoustic, giving `min(10, 0.1·0.89/pk)` = **1.094** and **4.208**.)

**FPX-79 (SHOULD).** A bundle SHOULD be self-describing to a reader with no filesystem access
beyond the bundle. `capture.header` and `capture.markers` are copied in for exactly that reason.

### 4.2 Worked example — `performances/2026-08-23-take1`

Every value below was read off disk today. Where the current bundle cannot supply a required
field, the field is present and explicitly `null`, with a sibling `_unrecoverable` note — that is
what a conforming manifest for a non-conforming bundle looks like.

```json
{
  "fpx_manifest": 1,
  "written_utc": "2026-08-23T23:40:00Z",
  "written_by": "spec-worked-example/1.0",
  "performance": {
    "id": "ba1dec22759846f3ffa386f19c28f6d095190bba0172c2296cc7fb6319df7466:253493531000000-253654576000000",
    "slug": "2026-08-23-take1",
    "start_ns": 253493531000000,
    "end_ns": 253654576000000,
    "duration_s": 161.045,
    "badge": "SEGMENT",
    "badge_basis": "clip window 00:40.000-03:20.045 of a 203.8 s capture; capture closed cleanly"
  },
  "capture": {
    "file": "2026-08-23-important-take-161623.fp30",
    "sha256": "a09eb4a6794e9320a56d69f1e15d9039d745e98f24f6c354872be7d665e3f111",
    "stream_sha256": "ba1dec22759846f3ffa386f19c28f6d095190bba0172c2296cc7fb6319df7466",
    "bytes": 35010,
    "header": {
      "fp30x-capture": "v1",
      "columns": "abs_ns hex_bytes",
      "mach_timebase_numer": "125",
      "mach_timebase_denom": "3",
      "anchor_mach_ns": "253442533270500",
      "anchor_unix_ns": "1787519541016769000",
      "started_utc": "2026-08-23T21:12:21Z",
      "source": "FP-30X MIDI Bluetooth",
      "sources_connected": "1"
    },
    "trailer": {
      "packets": "1378", "dropped": "0", "truncated": "0",
      "ts_zero": "0", "stopped_utc": "2026-08-23T21:16:21Z"
    },
    "closed": true,
    "packet_span_s": 203.8,
    "wall_span_s": 240.0,
    "markers": [],
    "holes": [],
    "hole_detection": "markers-only",
    "mirrors": [
      {
        "file": "2026-08-23-important-mirror-take-161623.fp30",
        "sha256": "a5f385cf28a038b14cd97271ef5a235226f012c3c9cb869471927f59987dc2bb",
        "stream_sha256": "ba1dec22759846f3ffa386f19c28f6d095190bba0172c2296cc7fb6319df7466",
        "relation": "identical",
        "same_radio": true,
        "repairs_possible": false
      }
    ]
  },
  "integrity": {
    "verdict": "GOOD",
    "verdict_text": "589 note-ons and 589 releases, all paired, no inferred loss, 100.0% on the 5 ms lattice",
    "timing_grade": "hardware",
    "timing_trusted": true,
    "timing_note": "CoreMIDI packet timestamps applied near the driver, 1378 of 1378 stamped by the source",
    "census": {"note_off": 589, "note_on": 589, "control_change": 224, "program_change": 1},
    "packets": 1378,
    "messages": 1403,
    "multi_message_packets": 24,
    "max_messages_per_packet": 3,
    "accounted": true,
    "intervals": 589,
    "trusted_intervals": 589,
    "defects": {
      "orphan_note_off": 0, "restrike_before_note_off": 0, "held_at_end": 0,
      "implausible_duration": 0, "non_monotone_timestamp": 0, "undecodable_bytes": 0
    },
    "lattice": {
      "step_ns": 5000000, "n_gaps": 1377, "n_on_lattice": 1377,
      "fraction": 1.0, "observed_gcd_ns": 5000000,
      "runs": 0, "runs_phase_restoring": 0, "phase_intact": true
    },
    "loss": {
      "inferred_lost": 0, "rate": 0.0,
      "inferred_lost_note_ons": 0, "inferred_lost_note_offs": 0,
      "balance": 0, "reported_dropped": 0
    },
    "loss_caveat": "The capture tool's `dropped` counter cannot see this: it counts only packets CoreMIDI handed us and refused, and a message lost on the radio never reaches CoreMIDI. Structural inference is the only signal there is."
  },
  "segments": [
    {"index": 2, "start_ns": 253493531000000, "end_ns": 253654576000000,
     "duration_s": 159.0, "notes": 573,
     "key_fit": {"key": "C maj", "fraction": 0.93},
     "is_the_performance": true}
  ],
  "segmenter": {"gap_s": 4.0, "version": 1},
  "labels": [
    {
      "assertion_id": "01J-identifier-2026-08-23T22-24Z-0001",
      "target": "ba1dec22759846f3ffa386f19c28f6d095190bba0172c2296cc7fb6319df7466:253493531000000-253654576000000",
      "asserted_by": "identifier",
      "asserted_by_detail": "fp30x_studio.identify 0.1.0, corpus 122847 themes",
      "asserted_at": "2026-08-23T22:24:00Z",
      "status": "proposed",
      "label": {
        "composer": "Charles Gounod",
        "work": "Ave Maria: Meditation on the First Prelude in C by J. S. Bach",
        "opus": "", "number": "", "year": ""
      },
      "evidence": {
        "confidence": 0.99, "margin": 0.409, "hits": 22, "density": 0.815,
        "line": "top", "notes_used": 84, "seconds_used": 23,
        "runner_up": "Johann Sebastian Bach, Das Wohltemperierte Clavier I, Praeludium I, Op. BWV 846",
        "runner_up_hits": null,
        "corpus_themes": 122847, "circular": false,
        "corpus_licence_tier": null,
        "_unrecoverable": "runner_up_hits and the corpus licence tier are not emitted by `identify check`; they exist in identify.matcher.Candidate and must be plumbed through"
      },
      "supersedes": null,
      "rejected_by": null
    }
  ],
  "label_tombstones": [],
  "renders": [
    {
      "file": "2026-08-23-take1.electric.m4a",
      "sha256": "9ecd3a936b071bbff2b9a171298aa351761c0d2a5210d4b3c673a4d8a2272e72",
      "bytes": 5420811,
      "source": "2026-08-23-take1.mid",
      "source_sha256": "b48df1614bb343eb502f69915347105c9a910c17790ba766afd6176be7141974",
      "note_ons_in": 573,
      "note_ons_out": 573,
      "engine": "fluidsynth",
      "engine_version": "2.6.0",
      "voice": "electric",
      "asset": "jrhodes3c-stereo",
      "params": {
        "sample_rate": 48000,
        "probe_gain": 0.1, "probe_peak": 0.081390, "gain": 1.094,
        "reverb": {"wet": 0.28, "rt60": 2.1},
        "loudness": null,
        "codec": "aac", "bitrate": "256k", "movflags": "+faststart"
      }
    },
    {
      "file": "2026-08-23-take1.acoustic.m4a",
      "sha256": "6dcb121fd6f59571cf6da0ba26a260bcbd55710462217d3b49c4d1318f104093",
      "bytes": 5512689,
      "source": "2026-08-23-take1.mid",
      "source_sha256": "b48df1614bb343eb502f69915347105c9a910c17790ba766afd6176be7141974",
      "note_ons_in": 573,
      "note_ons_out": 573,
      "engine": "fluidsynth",
      "engine_version": "2.6.0",
      "voice": "acoustic",
      "asset": "musescore-general",
      "params": {
        "sample_rate": 48000,
        "probe_gain": 0.1, "probe_peak": 0.021149, "gain": 4.208,
        "reverb": {"wet": 0.28, "rt60": 2.1},
        "loudness": null,
        "codec": "aac", "bitrate": "256k", "movflags": "+faststart"
      }
    }
  ],
  "files": [
    {"name": "2026-08-23-take1.fpx", "sha256": "…clip digest…", "bytes": 34070, "class": "curated"},
    {"name": "2026-08-23-take1.mid", "sha256": "b48df1614bb343eb502f69915347105c9a910c17790ba766afd6176be7141974", "bytes": 5462, "class": "derived"},
    {"name": "2026-08-23-take1.electric.wav", "sha256": "67d1ed4f7b8e9629bace03043ad9c87ea700b0446c20bdfa42db5d7c09934b80", "bytes": 33403500, "class": "derived"},
    {"name": "2026-08-23-take1.electric.m4a", "sha256": "9ecd3a936b071bbff2b9a171298aa351761c0d2a5210d4b3c673a4d8a2272e72", "bytes": 5420811, "class": "derived"},
    {"name": "2026-08-23-take1.acoustic.wav", "sha256": "c7ddb786667457a1d1f39b31d7af563c127ace47e39b833435e21d2a99f7ef16", "bytes": 33511788, "class": "derived"},
    {"name": "2026-08-23-take1.acoustic.m4a", "sha256": "6dcb121fd6f59571cf6da0ba26a260bcbd55710462217d3b49c4d1318f104093", "bytes": 5512689, "class": "derived"},
    {"name": "README.txt", "sha256": "…", "bytes": 1712, "class": "derived"}
  ]
}
```

Three things this example exposes about the bundle as it exists today:

- The file in the bundle called `2026-08-23-take1.fpx` is not the clip; it is the whole capture,
  renamed (both `sha256 a09eb4a6…`). The clip exists — `takes/clips/2026-08-23-take1.fp30`,
  34,070 bytes, `# clip_window_ns 253493531000000 253654576000000` — and was left behind.
- No render parameter survived. The gains above were recomputed, not read.
- The identifier's verdict is presented in `README.txt` with no field for Jake's answer, so a
  strong unverified call (0.99 / 0.409, 22 hits — the strongest unverified call in the archive per
  `CATALOGUE.md` §Open questions 3) is indistinguishable from a confirmed one to anyone reading the
  Drive copy.

---

## 5. Labels and identification

**FPX-90 (MUST).** Jake's verdict is ground truth and outranks every machine guess, everywhere,
permanently. This is not a display convention; it is a data rule with consequences in FPX-98.

**FPX-91 (MUST).** The library-wide store is `captures/labels.json`. The current file is
`~/Music/FP-30X Studio/takes/labels.json`, `{"version": 1, "updated": …, "labels": {…}}`, 27
segments, written by `docs/serve_labels.py:344-359`.

**FPX-92 (MUST).** A label is not a value; it is an **assertion**: *who* said *what* about *which
span*, *when*, *on what evidence*, and *whether it still stands*.

**FPX-93 (MUST).** `target` is a performance id per FPX-51, not a segment index. The 27 existing
records are keyed `"<take>.fp30#<segment>"` (`labels.json`); migration MUST resolve each to a span
and keep the old string in `legacy_key` (FPX-131).

**FPX-94 (MUST).** Assertions are **append-only**. The current writer keeps exactly one prior
verdict — `rec["revised_from"] = prev["verdict"]` at `serve_labels.py:351-353` — so a second
revision destroys the first. Reversals are data: he revised one verdict from `confirm` to `reject`
after listening again (`DESIGN-product.md` §6), and that is the most informative record in the
file.

### 5.1 Assertion schema

```jsonc
{
  "assertion_id": "…",                    // MUST, unique, sortable
  "target": "<stream_sha256>:<start_ns>-<end_ns>",   // MUST
  "legacy_key": "2026-08-17-open.fp30#0", // SHOULD, when migrated
  "asserted_by": "jake",                  // MUST ∈ jake | identifier | agent | import
  "asserted_by_detail": "labelling app, keypress",   // MUST
  "asserted_at": "2026-08-20T15:06:10-0500",         // MUST, with offset
  "status": "asserted",                   // MUST ∈ proposed | asserted | superseded | rejected
  "verdict": "reject",                    // MUST when asserted_by == "jake":
                                          //   confirm | correct | reject | unknown
  "label": { "composer": …, "work": …, "opus": …, "number": …, "year": …, "note": … },
  "evidence": {                           // MUST when asserted_by == "identifier"
    "confidence": 0.949, "margin": 0.400, "hits": 15, "runner_up_hits": 9,
    "density": 1.0, "line": "bass", "notes_used": …, "seconds_used": …,
    "runner_up": "…", "theme_id": "…", "corpus_themes": 122847,
    "circular": true,                     // MUST — the theme was derived from Jake's own playing
    "corpus_licence_tier": 3,             // MUST — 1|2|3, from idcorpus/sources.py
    "source_note": "derived, not read from a score: …"
  },
  "supersedes": "<assertion_id>|null",    // MUST
  "rejected_by": "<assertion_id>|null"    // MUST
}
```

**FPX-95 (MUST).** The four verdicts are `confirm`, `reject`, `correct`, `unknown`. Two-button
designs force a reject where the truth is a correction, and the corrections carry the information —
of the 27 stored verdicts, 5 are `correct` and they are the ones that name pieces the matcher never
proposed (`CATALOGUE.md`, table rows for `2026-08-17-session` #0, #1, #2, #4).

**FPX-96 (MUST).** `circular: true` MUST be set when the winning theme was derived from Jake's own
playing rather than from a score. Four such entries remain in
`fp30x_studio/identify/corpus.py:STUB` (`CATALOGUE.md` §"Where the identifier is wrong" 5), and
`labels.json` already carries the flag on the affected records. A circular match is self-recall,
not identification, and MUST NOT be displayed as a verdict.

**FPX-97 (MUST).** Confidence MUST NOT be shown without margin and hit count. On this archive,
confidence is not monotone in correctness: three proposals at confidence **0.000** were confirmed
and one at **0.865** was rejected (`CATALOGUE.md` §"Below the performance threshold"). Every junk
verdict in the record fires at 6–7 aligned hits; every confirmed one carried 17–28.

### 5.2 Rejections are first class

**FPX-98 (MUST).** A `reject` creates a **tombstone**: the pair (target, label) is thereafter
forbidden.

```jsonc
{ "target": "<perf id>", "label": {…}, "rejected_at": "…",
  "rejected_by_assertion": "…", "reason": "" }
```

**FPX-99 (MUST NOT).** No tool may write a tombstoned (target, label) pair into a manifest, a
`README.txt`, a `PROVENANCE.md` entry, a master filename, or a Drive upload. A tool that would have
emitted it MUST emit nothing and record `suppressed_by: "<tombstone>"`.

**FPX-100 (MUST).** This rule exists because it has already been broken and stayed broken for
three days. `takes/PROVENANCE.md:260-261` asserts that `2026-08-17-open.fp30` is Joplin's *The
Strenuous Life*, "Identified by Jake on 2026-08-17". On 2026-08-20 at 15:06:10 he **rejected**
exactly that label for exactly that take (`labels.json` `2026-08-17-open.fp30#0`, `"verdict":
"reject"`, no correction). The later, by-ear verdict wins; the earlier text is still on disk and
still reads as his (`CATALOGUE.md` §"Where the identifier is wrong" 3).

**FPX-101 (SHOULD).** Because `PROVENANCE.md` is append-only (FPX-58), a tombstone violation in it
is corrected by **appending a dated retraction**, never by editing the original line.

**FPX-102 (MUST).** Every assertion is written the moment it is made, not on a submit
(`serve_labels.py:15` already does this) and MUST survive a crash of whatever made it.

---

## 6. Integrity

**FPX-110 (MUST).** Once a capture's `# end` trailer is written, the file is immutable. No tool
may open it for writing, truncate it, rename it, or move it. `cp` is the only legal way to put its
bytes anywhere else, and the copy is not a capture — it is a copy, and FPX-4/FPX-40 apply.

**FPX-111 (MUST).** Every file recorded anywhere in FPX carries `sha256`. Captures additionally
carry `stream_sha256` (FPX-50). Digests are lowercase hex, full length.

**FPX-112 (MUST).** A **capture group** is a set of captures sharing one MIDI stream: one
`primary` and N `mirror` writers. Membership is asserted by the writer at arm time (`# writer`
header, FPX-25 table) and verified by digest.

**FPX-113 (MUST).** Reconciliation of a capture group:

1. If all `stream_sha256` agree → `relation: identical`. The mirror **corroborates**; nothing is
   merged.
2. Otherwise, compare on the **intersection of coverage windows** only —
   `[max(first_ns), min(last_ns)]`. Outside that window, absence in one file is not evidence of
   anything.
3. Inside it, take the set union on `(ns, bytes)`. Any record present in one file and not the other
   is a `path_divergence` defect and MUST be reported, never silently absorbed.
4. The merged result is a **new derived artifact**, `captures/merged/<name>.fpx`, with a header
   naming its inputs by digest. Source captures are never rewritten.

**FPX-114 (MUST).** A manifest MUST record `same_radio` per mirror, and when `same_radio` is true
it MUST record `repairs_possible: false`, and no UI may imply otherwise.

The empirical basis, measured today:

- **Mirrors corroborate.** `2026-08-23-important-take-161623` and its mirror: identical
  `stream_sha256 ba1dec22…`, 1,378 packets each.
- **Mirrors cannot repair.** `2026-08-22-evening` and `2026-08-22-evening-mirror` were both graded
  `2 messages inferred lost (2 note-ons inferred from orphan releases), balance −2`
  (`takes/PROVENANCE.md:547` and `:561`). The **same two note-ons** are missing from both files.
  Both are downstream of one CoreMIDI client set and one BLE link; a message lost on the radio
  never reaches either writer.
- **And the difference between them is arming skew, not loss.** The primary holds 20,388 records,
  the mirror 20,369. The 19 records the mirror lacks all fall in the first **0.945 s**, and the
  mirror's first packet is **1.040 s** after the primary's. The mirror is a strict subset; the
  union equals the primary; the two note-ons are still gone. This is precisely why FPX-113(2)
  intersects the coverage windows before differencing — a naive set difference reads a late arm as
  19 lost packets.

**FPX-115 (MUST).** A genuinely independent path (USB, when the cable lands) is a different case
and MUST be represented as such: `same_radio: false`, and reconciliation is still union-with-report,
never silent repair. `DESIGN-architecture.md` §7.5 proposes a ±3 ms merge window preferring the USB
timestamp; that rule is **[GUESS]** — no USB take exists — and MUST NOT be implemented before there
is data.

**FPX-116 (MUST).** The audio witness is never merged with MIDI. Different clocks entirely; it is
aligned for display only, anchored at `anchor_unix_ns`. It answers "did this happen", not "when
exactly".

**FPX-117 (MUST).** `dropped` in the trailer counts packets *our own ring buffer* refused
(`fp30x_capture.c:157-158`). Radio-side loss never reaches CoreMIDI and is therefore invisible to
it by construction. Any presentation of `dropped 0` MUST carry the caveat
(`pipeline/integrity.py:LOSS_CAVEAT`) or be omitted.

**FPX-118 (MUST).** The index under `captures/.cache/` is derived and deletable. Migration of the
index schema is deletion: bump `SCHEMA_VERSION` (`pipeline/store.py:53`) and re-ingest. Measured
cost of a full re-ingest of the largest take on disk: **0.29 s**. Nothing that is not recomputable
may ever be written there.

**FPX-119 (MUST).** The ingest checkpoint keeps `byte_offset`, `file_size`, `head_sha256` and the
pairer state (`pipeline/store.py:78-91`), and a shrink or a head-digest change forces a full
re-ingest (`:285`). That design is settled and MUST be preserved: it was verified by replaying a
353 s take as 17 uneven appends cutting lines mid-record and producing a row-for-row identical
index (JPA-136).

---

## 7. Migration

**Nothing in this section has been performed.** It is a plan.

### 7.1 Path map

| Today | Becomes | What breaks |
| --- | --- | --- |
| `~/Music/FP-30X Studio/` | `~/Music/FPX/` | every hard-coded literal: `publish:28`, `postprocess:17-20`, `explorer:28-34`, `serve_labels.py:51`, `identify/runner.py:41`, `docs/build_takes.py`, the skill. One `mv`, then a compatibility symlink for one release |
| `takes/*.fp30` (30 files) | `captures/*.fp30` — **moved, never renamed** (FPX-4) | `labels.json` keys and `PROVENANCE.md` markers still resolve; `.index/` paths change, and the index is deletable |
| `takes/2026-08-19-usb.fp30` | `captures/quarantine/` | it is two interleaved captures (FPX-38). It MUST NOT be split automatically — separating two interleaved writers by timestamp is a guess. Quarantine, report, ask |
| `takes/2026-08-22-piece2*.fp30` | `captures/quarantine/` | mid-capture snapshots of `2026-08-22-evening*`, same `stream_sha256`, no trailer (FPX-40) |
| `takes/2026-08-22-mozart-c-i.fp30` | `captures/quarantine/` | a stash of segment 0 of `2026-08-22-evening` (`CATALOGUE.md` §"What is on the device"); it is a clip stored as a capture |
| `takes/clips/*` (20 clips) | `performances/<slug>/<slug>.fpx` where a bundle exists, else `captures/clips/` | the `# end clip` trailer must become `# clip_end 1` (FPX-33); until then every clip reads `complete=False` |
| `takes/labels.json` | `captures/labels.json`, schema 2 | 27 keys re-targeted to spans; `legacy_key` retained; ~40 citations in `CATALOGUE.md` of the form "`labels.json` `#3`" become indirect |
| `takes/PROVENANCE.md` | `captures/PROVENANCE.md` | append-only; the FPX-100 retraction for `2026-08-17-open` is appended, not edited |
| `takes/.index/*.sqlite3` (36) | `captures/.cache/*.sqlite3` | detected as v1, deleted, rebuilt at 0.29 s each |
| `takes/*-room.wav` | `captures/*.room.wav` | 2 files, 670 MB. Labelled `witness` (FPX-12) |
| `takes/2026-08-16 *.mid` (5) | `captures/` unchanged | poll-loop takes; keep `timing_grade='poll-loop'`; timing queries still refuse to answer from them |
| `performances/<slug>/` (15) | unchanged in place; gain `fpx.json` + `README.txt` | 15 manifests to backfill; 12 of them have no `.wav` and no recorded render params, so `params.gain` is `null` unless re-probed |
| `performances/<slug>/<slug>.fp30` | replaced by the clip; capture referenced by digest | §0.3(2). If overruled, the file keeps the capture's own name instead |
| `performances/2026-08-19-usb/` | **deleted and unpublished, including the Drive copy** | it is a bundle built from a two-writer file: 14-byte `.mid`, two 1 ms silent `.m4a`. It is also a duplicate of `2026-08-19-nocturnes`, the same performance over the same link |
| `performances/2026-08-23-compare.html` | `performances/.reports/` or deleted | a loose file at bundle level; not a bundle, would be listed as one |
| `renders/*` (34 files) | `performances/<slug>/` or deleted | pure derived; the `.raw.wav` files are 547 MB of intermediates |
| `renders/figures/`, `docs/figures/` | `performances/.reports/figures/` | derived |
| `masters/*` (21) | unchanged | FULL / COMPLETE / SEGMENT naming preserved; these are curated and irreplaceable in the sense that matters |
| `MuseScore_General.sf3`, `soundfonts/` | `assets/` + `assets.json` | §8 |
| `CATALOGUE.md`, `explorer.html` | `~/Music/FPX/` root, regenerated | both are generated views |
| `docs/.label-cache/` (31 files + audio) | `~/Library/Caches/FPX/` | FPX-152 |

### 7.2 Tools that need changing

| Tool | Change |
| --- | --- |
| `native/fp30x_capture.c` | `O_EXCL` not `O_APPEND` (FPX-38); emit `fpx-capture v1.1`; `# path`, `# writer`, `# tool`; the marker set of FPX-30 |
| `fp30x_studio/rawcapture.py` | ordered `events` list (FPX-29); `Scan.header` as first-occurrence view; multi-header/multi-trailer detection (FPX-39); `stream_sha256` |
| `fp30x_studio/segment.py` | stop opening the file directly at `:218`, `:232`, `:260` — three of the five parser sites; `# clip_end 1` (FPX-33); emit spans not just indices |
| `fp30x_studio/stitch.py` | fourth parser site (`:79`); route through `rawcapture.scan()` |
| `fp30x_studio/pipeline/*` | `.cache/` path; `SCHEMA_VERSION` 2 with the `gap` table and a `path` column; `closure='gap'` |
| `fp30x_studio/take_analysis.py` | consumes spans, not indices; must refuse to answer timing questions on `poll-loop` and on captures with `multiple_streams` |
| `~/.claude/skills/…/bin/publish` | write `fpx.json`; put the clip in the bundle; record render params; refuse to publish a `SUSPECT` capture without an explicit override; verify `note_ons_in == note_ons_out` before uploading |
| `~/.claude/skills/…/bin/postprocess` | same gain/params recording; stop scraping `segment list` text with `awk` (`:64-73`) |
| `~/.claude/skills/…/bin/explorer` | read `fpx.json` instead of re-ingesting a bundle copy (`explorer:44-48`); stop creating a second index per bundle |
| `~/.claude/skills/…/bin/greenlight` | count packet bytes past the header, not file growth — it currently flips GREEN on a `# setup_changed` comment line with zero packets (`DESIGN-architecture.md` §6.5) |
| `docs/serve_labels.py` | assertion list, tombstones, span targets |

**FPX-130 (MUST).** Migration is one atomic pass that writes a dated line to
`captures/PROVENANCE.md` saying exactly what it moved, per the house rule.

**FPX-131 (MUST).** Migration MUST NOT delete, rename, or rewrite any capture. Quarantine means
`mv` into a subdirectory of `captures/`, with a `QUARANTINE.md` naming the requirement violated.

**FPX-132 (MUST).** Migration MUST NOT run while any capture process is live.

---

## 8. Reserved and forbidden

**FPX-150 (MUST NOT).** FPX writes nowhere outside `FPX_HOME` except:
`~/Library/Application Support/FPX/` (preferences and window state — non-precious, deletable) and
`~/Library/Caches/FPX/` (caches). It MUST NOT write to `~/Music/` root, `~/Documents`, `~/Desktop`,
`/tmp` for anything that outlives a process, or any cloud-synced directory without an explicit user
action.

**FPX-151 (MUST NOT).** No tool writes anything into `captures/` that is not a capture, a witness,
`PROVENANCE.md`, `labels.json`, or `.cache/`. Today `takes/` also holds `clips/`, `labels.json`,
five legacy `.mid`, two 500+ MB `-room.wav`, and 36 index files, and `renders/` holds two HTML
reports and a `figures/` directory.

**FPX-152 (MUST).** Caches live under `~/Library/Caches/FPX/`, never inside the repository.
`fp30x-studio/docs/.label-cache/` currently holds 31 JSON caches and 31 `.m4a` audio excerpts inside
a git working tree.

**FPX-153 (MUST NOT).** A bundle MUST NOT contain a soundfont, a corpus file, or any third-party
asset. It names them.

**FPX-154 (MUST).** Third-party assets are referenced through `assets.json`:

```jsonc
{
  "assets": {
    "musescore-general": {
      "path": "~/Music/FPX/assets/MuseScore_General.sf3",
      "sha256": "5b85b6c2c61d10b2b91cddd41efcce7b25cd31c8271d511c73afafbef20b6fa3",
      "bytes": 39900972,
      "licence": "MIT",
      "redistributable": true,
      "refetchable": true,
      "origin": "auto-downloaded by core.ensure_soundfont"
    },
    "jrhodes3c-stereo": {
      "path": "~/Music/FPX/assets/jRhodes3/jRhodes3c-stereo.sf2",
      "sha256": "35a8213c8aa09e5e982e4ed1e96b6995bb04e89d6559bd8f7ac7142a35112547",
      "licence": "CC BY-NC-SA (samples)",
      "redistributable": false,
      "refetchable": false,
      "origin": "user-supplied",
      "note": "The author's own text permits using the samples freely in artistic works with no restriction on commercial use of the resulting music. Music rendered with it is unencumbered; the .sf2 itself must not be redistributed."
    }
  }
}
```

**FPX-155 (MUST).** `redistributable: false` means: never copied into a bundle, never uploaded by
`rclone`, never committed. `.gitignore` already excludes `*.sf2`. The distinction matters and is
narrow — the **renders** made with jRhodes3 are fine to publish; the **soundfont** is not.

**FPX-156 (MUST).** Corpus material is referenced, never redistributed, and every label assertion
derived from it carries `corpus_licence_tier` (FPX-94 schema). The tiers, verified 2026-08-19 and
recorded in `~/workspace/audio/corpus/PROVENANCE.md:33-57` and
`fp30x_studio/idcorpus/sources.py`:

- **Tier 1** — commercial use permitted (CC0 / CC BY / CC BY-SA / PD): NIFC Chopin, OpenScore
  Lieder, OpenScore String Quartets, Mutopia, RISM.
- **Tier 2** — CC BY-NC-SA, verified from the repository's own LICENSE: Joplin, Mozart/Haydn/
  Scarlatti sonatas, Bach 370 chorales, all DCML. Share-alike propagates to derivatives.
- **Tier 3** — **NO LICENCE STATED ANYWHERE**: `beethoven-piano-sonatas`,
  `beethoven-string-quartets`, `chopin-mazurkas`, `chopin-preludes`, `hummel-preludes`,
  `bach-musical-offering`, `scriabin`. The compositions are public domain; the **encodings are
  not licensed**, so all-rights-reserved is the default. `chopin-preludes` even carries
  `!!!YEC: Copyright 2008 by Craig Stuart Sapp` with no grant.

**FPX-157 (MUST).** A *title and composer* asserted as a label is a fact about a performance and
may be published under any tier. The corpus **encoding** — pitches, onsets, intervals, the theme
row — MUST NOT leave the machine for tiers 2 and 3. A manifest therefore carries the label text and
the evidence *scalars*, never the matched theme's note data.

**FPX-158 (MUST).** Barlow & Morgenstern's *Dictionary of Musical Themes* is not usable: copyright
renewed, Stanford record `R604688`, US public domain 2044-01-01. RISM (CC BY 3.0, 2,005,420
decoded incipits) is the substitute and is already in the tree.

**FPX-159 (SHOULD).** If FPX ever ships as a product, tier 1 alone is safe. Dropping tiers 2 and 3
costs Beethoven, Scarlatti, the Bach chorales, the Chopin mazurkas and preludes, Scriabin and
Debussy — and **keeps Chopin and Joplin**, which is the whole of the repertoire Jake is confirmed
to play (`corpus/PROVENANCE.md:58-64`).

---

## 9. Conformance

**FPX-170 (MUST).** An implementation is conforming if `python -m fp30x_studio.fpx.validate <dir>`
reports no `FAIL` for every bundle it produces.

The validator lives at `fp30x_studio/fpx/validate.py` and implements the subset of this document
that is checkable from a bundle on disk. It is deliberately not exhaustive: FPX-90, FPX-98–101 and
most of §7 are checkable only against the library, and the validator checks the library-level
tombstone rule (FPX-99) once, separately, rather than per bundle.

```
$ .venv/bin/python -m fp30x_studio.fpx.validate
$ .venv/bin/python -m fp30x_studio.fpx.validate ~/Music/"FP-30X Studio"/performances -v
$ .venv/bin/python -m fp30x_studio.fpx.validate --json
```

Exit status is 1 if any bundle fails. Twelve requirements are decided per bundle:

| Column | Requirement |
| --- | --- |
| `70` | FPX-70 — `fpx.json` present at the bundle top level |
| `71` | FPX-71 — it parses and declares `fpx_manifest` and the four required sections |
| `72` | FPX-72 — `README.txt` present and marked generated |
| `74` | FPX-74 — `files[]` covers every file, digests and sizes match |
| `21` | FPX-21 — every capture parses, version line first, no torn line |
| `39` | FPX-39 — one stream per file: one header, at most one trailer |
| `31` | FPX-31 — capture closed, or clip carries `clip_end` |
| `52` | FPX-52 — the bundle carries the clip, not the whole capture |
| `4`  | FPX-4 — the capture keeps its own name, and its digest resolves in the library |
| `77` | FPX-77 — the `.mid` is readable and accounts for every note-on in the window |
| `151`| FPX-151 — no derived cache inside the bundle |
| `153`| FPX-153 — no third-party asset inside the bundle |

**Result on the archive as it stands, 2026-08-23 18:2x** (a `publish` run was adding bundles
while this was measured, so the denominator moves):

```
17 bundle(s), 12 checkable requirements, 0 passing

FPX-70   17/17   no bundle carries a manifest — the format does not exist yet
FPX-72   17/17   every bundle carries <slug>.txt instead of a generated README.txt
FPX-52   17/17   every bundle carries the whole capture rather than the performance
FPX-151  15/17   .index/<name>.sqlite3 inside the bundle, written by `explorer`
FPX-31    3/17   2026-08-19-nocturnes, 2026-08-22-mozart-c-i, 2026-08-23-take2 — no trailer
FPX-4     2/17   2026-08-23-take{1,2} — capture renamed on publish
FPX-39    1/17   2026-08-19-usb — two headers and two trailers in one file
FPX-77    1/17   2026-08-19-usb — 2026-08-19-usb.mid unreadable (EOFError), 14 bytes
FPX-99    library — takes/PROVENANCE.md:261 asserts a label rejected in labels.json
```

Zero of seventeen is the honest answer and it is mostly one fact repeated: FPX-70, FPX-72 and
FPX-52 are three faces of "the bundle format proposed here has not been written yet". The
interesting rows are the last five, which are defects in bundles that already exist and have
already been mirrored to Google Drive.

---

## Appendix A — measurements made for this document, 2026-08-23

All reproducible; the piano was mid-capture and every read was read-only.

| Measurement | Result |
| --- | --- |
| Bundles on disk | 15 at 17:41, 17 by 18:25 — a `publish` run was adding more while this was written; plus one loose `.html` |
| Captures on disk | 30 `.fp30` + 5 legacy `.mid` in `takes/` |
| Captures with no `# end` trailer | **8** of 30; only `2026-08-23-session3*` legitimately |
| Captures with more than one header | **1** — `2026-08-19-usb.fp30`, two headers 26 ms apart, two trailers (4,918 and 5,750 packets), 424 backward timestamp steps from line 22 |
| `# setup_changed` lines in `2026-08-22-evening.fp30` | **44**, alternating `reconnected 0`/`1`; the current reader reports one |
| Mirror pairs with identical `stream_sha256` | 4 of 6 (`home`, `take-161623`, `take2`, `until-163845`) |
| Mirror pairs divergent | 2 (`evening`, `piece2` — same pair, two names). Mirror lacks 19 records, all within the first 0.945 s; mirror's first packet is 1.040 s late |
| Both members of the `evening` pair | `2 messages inferred lost`, `balance −2` — the same two note-ons (`takes/PROVENANCE.md:547,561`) |
| `.mid` files in bundles that `mido` cannot open | **1** — `performances/2026-08-19-usb/2026-08-19-usb.mid`, 14 bytes |
| Renders in that bundle | 2 × `.m4a`, 844 bytes, **0.001 s** duration |
| Bundle `.fp30` byte-identical to a `takes/` capture | 15 of 15 — every bundle carries the whole capture, not the clip |
| Bundles whose `.fp30` name differs from the capture's | 2 (`2026-08-23-take1`, `-take2`) |
| Clip trailer parse | `# end clip` → `trailer {}` → `complete False` on every clip |
| Recovered render gains, take1 | electric probe 0.081390 → gain **1.094**; acoustic probe 0.021149 → gain **4.208** |
| Test suite | 319 before this document; **347 passing** after adding `tests/test_fpx_validate.py` |

## Appendix B — register of guesses

Everything above is cited except these.

1. **§0.3(2), FPX-71** — that "bundled by performance with the original performance data"
   (`publish:5-7`) means the clip rather than the whole capture. He may mean the capture.
2. **FPX-8** — that iCloud's "Optimise Mac Storage" can evict a capture from `~/Documents`.
   From Apple's documented behaviour; not tested on this machine, and testing it means risking a
   take.
3. **FPX-35, the `unknown` gap class** — the "p95 × 10³" rule is a placeholder. There is no
   principled threshold, which is the point of FPX-34; the class exists so that pre-marker files
   are not silently classified as `music`.
4. **FPX-115** — the ±3 ms BLE↔USB merge window, inherited from `DESIGN-architecture.md` §7.5.
   No USB take exists.
5. **FPX-157** — that publishing a matched work's *title* under a tier-3 encoding licence is
   fine while publishing its note data is not. This is my reading of the fact/expression line, not
   legal advice, and it is the one requirement here that would benefit from a lawyer.
6. **FPX-11** — the `.m<N>.fpx` mirror naming. Arbitrary; chosen to sort adjacent to the primary
   and to admit more than one mirror.
7. **§0.3(3)** — that he would rather have stable label targets than stable citations. The
   trade is real and he may want the other side of it.

## 10. Timbre

Added 2026-08-24. The 1.0-draft of this document did not mention timbre once, which was a
hole rather than an omission: the format could describe *which notes were played* and not
*what the result sounded like*, so two audibly different renderings of one clip were
indistinguishable in the manifest.

Timbre enters FPX as **two fields that must never be merged**, because they answer different
questions and are owned by different objects.

### 10.1 Performed timbre — a property of a window of capture

**FPX-171 (MUST)** — Every clip in a manifest MUST carry `timbre.performed`, computed from
the capture alone. It is a function of the note and controller stream and nothing else, so it
is identical on every recomputation and survives any change of renderer.

Implemented in `fp30x_studio/timbre.py:performed`. Fields, all with `schema_version`:

| field | what it is | why it is here |
| -- | -- | -- |
| `velocity` | n, mean, sd, p10, p90 | dynamic level and dynamic *range*, separately |
| `attack_rate_hz` | onsets per second | the difference between a chord and a run |
| `legato_index` | share of onsets arriving while the previous note is still held | overlap made by the hand — not the pedal, which is CC64 and a different sound |
| `duty_cycle` | mean note duration ÷ mean inter-onset interval | how far the touch sits between staccato and over-held |
| `register_centroid`, `register_spread` | duration-weighted pitch mean and sd | where on the instrument the sound is |
| `voicing_top_lead` | in chords (onsets within 30 ms), the top note's velocity minus the mean of the notes under it | "projecting the melody", as a number |
| `dynamic_arc` | mean velocity of the second half minus the first | whether the passage grew or died |
| `chords` | count of onset clusters within 30 ms | denominator for `voicing_top_lead` |
| `pedal` | see FPX-172 | |

**FPX-172 (MUST)** — `timbre.performed.pedal` MUST be computed with the pedal state **chased**
across the window entry, and MUST record `entering_value` and `chased` so a reader can see that
it was. CC64 is held across bar lines for tens of seconds; a window that starts under a held
damper and reports `down_fraction: 0` is wrong in the direction that matters, and the clip
renders dry. This is the same chase `segment clip` performs on the MIDI it writes — the manifest
and the audio MUST agree about it.

**FPX-173 (SHOULD)** — `pedal` SHOULD report `half_fraction` separately from `down_fraction`.
The FP-30X sends continuous CC64, verified 2026-08-24 on a 44:26 capture whose 5,950 CC64
messages include values such as 18 and 3. The MIDI convention that ≥64 is "down" cannot express
a half-pedal the instrument actually produces, so collapsing to a boolean discards real
technique.

### 10.2 Rendered timbre — a property of a result, never of a capture

**FPX-174 (MUST)** — A capture MUST NOT carry `timbre.rendered`. A `.fpx` file contains no
audio and therefore has no tone. Any implementation that attaches a rendered timbre to a
capture is wrong.

**FPX-175 (MUST)** — Every result that is audio (`.wav`) MUST carry `timbre.rendered`, and it
MUST be sufficient to reproduce that file from the clip without consulting anything outside the
bundle. Two `kind`s exist:

- `kind: "soundfont"` — sampled playback. Records `engine`, `soundfont` name and path, `gain`,
  `sample_rate`, `reverb`. **`gain` is mandatory and is not cosmetic**: fluidsynth's default is
  0.2, roughly 24 dB below full scale, so a file rendered at the default and a file amplified
  afterwards are different files and must not both claim to be "the render".
- `kind: "model"` — the physical model in `fp30x_studio/synth`. Records `engine`, `preset_name`,
  `model`, `fitted`, `gain`, `reverb`, **and the entire preset inline**.

**FPX-176 (MUST)** — For `kind: "model"`, the preset MUST be embedded in full, not referenced by
path. Presets are refitted: `acoustic` already carries coefficients measured from 38 Iowa
Steinway notes and will change when better measurements arrive. A path alone silently
re-points an old result at a new instrument.

**FPX-177 (SHOULD)** — `rendered` SHOULD carry `fitted`, distinguishing coefficients measured
against a real instrument from first guesses. As of 2026-08-24 `acoustic` is fitted and
`electric` is not — its own `note` field says so, and that honesty MUST survive into the
manifest rather than being flattened to a preset name.

### 10.3 What the pair is for

**FPX-178 (SHOULD)** — Readers SHOULD treat `performed` as searchable and `rendered` as
reproducible. Sorting a practice session by `pedal.down_fraction` or `voicing_top_lead` is a
question about the playing; it must not change when the soundfont changes. Rebuilding a `.wav`
byte-for-byte is a question about the renderer; it must not change when the segmenter changes.

**FPX-179 (MUST NOT)** — An implementation MUST NOT present velocity as timbre. Velocity is the
gesture that causes tone. What the room heard is not in a `.fpx` file at all — the FP-30X sends
no audio over BLE-MIDI, and the USB `COMPUTER` port that would carry it is still unresolved
(§2). Every generated `README.txt` and viewer MUST keep that caveat visible.

### 10.4 Display derivatives

**FPX-180 (MAY)** — A clip MAY carry `envelope`: fixed-count buckets of note density, mean
velocity and mean pitch across the window. It exists so a viewer can draw the shape of an
attempt without loading its audio. It is derived, regenerable, and MUST NOT be read as
evidence of anything the fields above do not already state.

### 10.5 The viewer is part of the format

**FPX-181 (SHOULD)** — A bundle SHOULD contain `index.html`, generated from `fpx.json` by
`fp30x_studio.fpx.viewer`, so that pointing any static file server at the bundle directory
renders it. Like `README.txt` it is generated and MUST NOT be hand-authored; if it disagrees
with the manifest, regenerate it and the manifest wins.

**FPX-182 (MUST)** — A server used to view a bundle MUST honour Range requests. Without them a
browser cannot seek within a clip: it asks for a byte range, receives the whole file with a
200, and the playhead snaps back with no error anywhere. `python3 -m http.server` fails this.


---

*If a statement here disagrees with the code, the code wins — check it and correct this document.*
