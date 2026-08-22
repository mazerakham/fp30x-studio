# FP-30X Studio — product design, v1 (native macOS)

Status: draft for review. Written 2026-08-22 from the transcript record, the
`fp30x-recording` skill, `PROJECT_LOG.md`, the takes directory, and the Linear board.
Companion to the architecture design.

---

## Summary

**The job.** Jake sits down at a Roland FP-30X, plays for twenty minutes, and wants what he
played to exist afterwards — as a file he can listen to, send to a friend, and find again in
a month — without any of that costing him attention while he is playing, and without a
failure being able to hide from him. Today that loop only closes when an LLM operates
twelve command-line modules on his behalf. **v1 is that loop, closed by the software.**

**The three moments.**

1. **Arming.** He is on the bench, hands ready. He needs one signal, from several feet away,
   that says *go* — and it must keep saying *go* for the whole session, because the ways this
   fails are silent.
2. **Landing.** The piece is over. He needs a listenable, shareable, correctly-named file
   without answering a single question first.
3. **Return.** A week or a month later, he needs to find the right take and know what it is —
   in particular, to tell a 3:22 fragment from a 23:32 complete performance at a glance.

**The single biggest thing the current tool gets wrong.**

> **Every indicator it shows is about the software, not about the take.**

`connected: FP-30X MIDI Bluetooth`, the live note feed, the packet counter, the running
process, `fp30x-capture -l` — each of these has reported healthy while nothing was being
recorded, and each lie has a named cost in the skill file: 27 seconds of a nocturne's opening
(2026-08-21), six minutes of a nocturne (2026-08-21), eleven minutes (2026-08-20). The one
signal that has never lied is **the size of the file on disk**, and the shipped GUI
(`fp30x_studio/app.py`) does not look at it. It also still uses the 2 ms Python poll loop in
`core.py` that `native/fp30x_capture.c` replaced on 2026-08-17, writes takes as
`2026-08-16 15.03.58.mid`, and calls none of the twelve modules built since. It is a day-one
demo that the rest of the project grew past.

The same disease reached the output side, and shipped: `segment.py` and `stitch.py` read only the
**first** MIDI message of each CoreMIDI packet, silently dropping 5–9% of the notes in every `.mid`
export — 135 of 1,930 on one take (JPA-149, 2026-08-20). A master built through that path was
uploaded to Drive before anyone noticed, and has since been replaced. It reported success
throughout.

Every rule in this document descends from that one correction: **an indicator that is not a
function of bytes on disk does not ship, and a stage that can lose data silently must count what
went in against what came out.**

---

## Evidence key

Transcripts are at `/Users/jake/.claude/projects/-Users-jake-workspace/`. Short names used
below:

| tag | file | span |
| --- | --- | --- |
| `T-A` | `b8a2bca6-62f6-4eab-bde0-7accca66e95f.jsonl` | 2026-08-16 — day one, first sound |
| `T-B` | `50ec5096-900d-48f8-a3f8-44797e01e3bc.jsonl` | 2026-08-17 → 08-20 — the long build |
| `T-C` | `885ff9f8-8347-48d7-b058-b0efef54333d.jsonl` | 2026-08-20 → 08-22 — masters, publishing |
| `T-D` | `edf294c3-cdfb-4d0e-82bf-490fcf2bb4ff.jsonl` | 2026-08-22 — today; the app was commissioned here |

Other sources: `~/.claude/skills/fp30x-recording/SKILL.md` (**S**),
`~/workspace/audio/PROJECT_LOG.md` (**L**),
`~/Music/FP-30X Studio/takes/PROVENANCE.md` (**P**),
`~/Music/FP-30X Studio/takes/labels.json`, and Linear team JPA — issues 118, 124, 125, 129, 130,
133, 134, 136, 137, 138, 139, 147, 148, 149, 150, 151, 152, 154, 155, 156, 157, 158.

Claims not sourced to one of these are marked **[inference]**.

One piece of context for weighting everything below: on this machine's own prompt census,
AUDIO/PIANO is his **most-typed theme of the last fourteen days at 33.7% of all prompts**,
having been ranked 4th the fortnight before (`9e010dd2-…jsonl`). This is not a side project.

---

## 1. Who this is for and what job it does

- **One user.** Jake Mirra: PhD mathematician, working engineer, serious amateur pianist.
  No onboarding, no tooltips, no first-run tour, no second user to design around.
- **The job:** *get what he played off the piano and into a keepable file, without taking his
  attention off the piano, and without ever being able to fail quietly.*
- **The constraint that defines it:** the capture path is BLE-MIDI, which goes deaf while
  reporting success. Therefore the product is not a recorder — it is an **honest witness to a
  recorder**. That is the whole design.
- **What success looks like:** he plays; he never thinks about the software during the piece;
  afterwards there is a correctly-named file he can play, and a month later he can find it.
- **What it is not:** a DAW, a notation editor, a practice tool, a research bench.

The founding request, in his words, is the whole scope statement:

> *"it seems clear to me that it's time we release a desktop for mac os version of the fpx
> studio software. Write it in some suitable coding language, give it the same features, and
> make sure it survives sleep. […] distilling it down to a perfect software solution that
> suits my needs and **doesn't need an LLM to run everything for me**."* — `T-D`, 2026-08-22

---

## 2. The three moments

The brief proposed three moments. All three are confirmed by the record. One correction and
one addition follow.

### (a) Arming — confirmed, and it is not a moment

He has asked for a green light on **five separate occasions**, in five different sessions:

> *"You should have a green light or a redlight at the end of your next message to me."* — `T-B`, 08-18
> *"Just looking for a green light."* — `T-B`, 08-19
> *"Prepare for another recording. Hit me with a green light when you're certain it's working. I'll be playing test notes waiting for your green signal."* — `T-C`, 08-22 00:07
> *"Starting this morning with a few recordings. Green light me."* — `T-C`, 08-22 14:47
> *"You're supposed to give me a green light, please put it in the skill and get it to not talk so god damned much."* — `T-D`, 08-22 21:03

**Correction: arming is a state, not an event.** He re-checks it mid-session, repeatedly,
with a one-word command he invented himself:

> *"Any chance you can peek at the results while they are being generated? It is preferable to
> playing for an hour and finding out it's garbage."* — `T-B`, 08-17
> *"peek"* — `T-B`, 08-19 15:08 · *"Peek now"* — `T-B`, 08-19 16:38
> *"Looks like you need to improve your tooling."* — `T-B`, 08-19 16:39, when peek came back inconclusive

So the light is not something shown once at the start. It is the persistent state of the
window for the entire session, and "peek" is what a user does when the light is not
continuously visible. **In v1 there is no peek command, because the light never goes away.**

### (b) Landing — confirmed

> *"Alright let's see it and hear it, what I played."* — `T-B`, 08-17
> *"Well the piece is over, so let's put it up and see what we got."* — `T-D`, 08-22
> *"OK please get it transformed and pulled up as I asked for before."* — `T-C`, 08-21
> *"This should all be an automated pipeline that you can run in the background while continuing to talk to me."* — `T-D`, 08-22

And the terminal case, from the night he first got sound out of the instrument:

> *"JESUS FUCKING CHRIST CLAUDE IM NOT IN A GOOD MOOD JUST STOP TALKING AND JUST PLAY AN AUDIO
> FILE ON THIS LAPTOP WITH NOTES I PLAYED ON MY OWN FUCKING KEYBOARD"* — `T-A`, 08-16
> Fifteen minutes later: *"Thanks. Finally. Capture what we did so that the next time I ask
> about this you don't torture me for another hour."* — `T-A`, 08-16

### (c) Return — confirmed, and it has already nearly gone wrong in public

> *"Wait this is three minutes I want the full recording"* — `T-C`, 08-21 22:40, after a
> 3:22 fragment had been mastered, named, and queued for upload as a complete nocturne.
> *"Well make sure they're all renamed to segment…"* — `T-C`, 08-21 22:45
> *"FP-30X Studio '' doesn't seem to be working, it doesn't have any of my recordings / isn't
> autorefreshing / has no refresh button"* — `T-B`, 08-18
> *"What is the command to run fpx studio? Run it please."* — `T-B`, 08-19

`~/Music/FP-30X Studio/masters/` today contains four masters of the same nocturne
distinguished only by parenthetical prose: `(SEGMENT 3m22, incomplete take)`,
`(complete performance)`, `(opening missing, starts 0m46)`, and one with no qualifier at all.
That is the artefact of the near-miss, sitting on disk.

### Not a fourth moment: research

Timbre fitting, the synth workbench, the audio→performance inverse problem, the warp
homeomorphism, take alignment. These are heavily worked and genuinely his interest — but they
never sit between him and a recording. They stay where they are, on the command line. See §7.

---

## 3. The arming experience

### What the light is driven by

**Bytes landing on disk. Nothing else.** The skill states the rule and names its casualties;
`greenlight` already implements it in 40 lines of bash. v1 inherits the rule verbatim:

> *"The file size is the only thing that cannot lie. Check that, and nothing else."* — `S`

Concretely: the app `stat`s every armed sink at 4 Hz and holds two numbers per sink — the
size at arming, and the wall-clock time of the last observed increase. Every visual state
below is a pure function of those numbers plus the link probe (below).

### The one hard problem, stated honestly

After green, **silence and a dead radio look identical at the MIDI layer.** There is no
heartbeat to fall back on: the FP-30X sends no Active Sensing (`0xFE`) over BLE-MIDI. Byte
census of `2026-08-22-home.fp30`, a 27-minute take: status bytes are `0x80` ×6517,
`0x90` ×6037, `0xB0` ×3366 and **nothing else**; `fp30x_capture.c` filters no real-time
messages, so their absence is the instrument's, not ours. *(Measured for this document.)*

And a pure timeout cannot save it, because real silence is long. **The longest genuine
inter-message gap inside that known-good take is 147.6 s** — which exceeds `watch.py`'s
`RED_S = 120` threshold. The current amber/red rule would have raised a false alarm inside a
perfect take, which is exactly the failure the skill records for supervisor v3: *"works, but
cycles during long pauses and litters empty files."* **A silence timeout is not a link
detector and v1 must not ship one as if it were.**

So the link needs an out-of-band witness. Two candidates, in preference order:

1. **The room mic.** Already produced a file today (`2026-08-22-home-room.wav`, 140 MB, 27
   min). It sits ~25 dB below usable as audio, but it does not need to be audio — it needs to
   answer one boolean: *is a piano sounding right now?* If the mic hears playing while no
   bytes are landing, the link is dead, with certainty. This is the disambiguator, and it is
   the only one that survives the radio dying. **[inference: the RMS-gate design is mine; the
   mic-as-witness idea is from `S`.]**
2. **A CoreMIDI endpoint probe.** `kMIDIPropertyOffline` on the source endpoint, and/or a
   `MIDINotifyProc` on `kMIDIMsgSetupChanged` — the latter is already named in `S` as the
   real fix for the capture's inability to reconnect. **[inference: whether `Offline` actually
   flips for a BLE-MIDI source that has stopped delivering is unverified and is the single
   highest-value experiment before implementation. If it flips reliably, the mic becomes
   optional.]**

### The states

Five, not three. The extra two exist because collapsing them is what produced the lies.

| state | condition | meaning | colour |
| --- | --- | --- | --- |
| **RED — ARMING** | ≥1 armed sink has not grown since arm | *not recording yet* | red, full-bleed |
| **GREEN — RECORDING** | every sink grew since arm, and a byte landed < 20 s ago | *recording* | green, full-bleed |
| **GREEN — QUIET** | every sink grew since arm, last byte ≥ 20 s ago, link probe alive, room quiet | *you stopped playing; still armed* | green, dimmed 40% |
| **AMBER — DEGRADED** | ≥1 sink stale while ≥1 other still growing | *one path lost; the take is safe* | amber |
| **RED — LOST** | link probe reports gone, **or** mic hears playing with no bytes landing | *not recording; act now* | red, pulsing |

Rules that make the states worth having:

- **RED is the default at arm.** Green is earned by a byte, never by a connect line. `S`:
  *"Never say 'play' on the strength of the connect line. That line is a lie about the radio."*
- **RED — ARMING asks for exactly one thing: one key.** Not the piece, not a cable, not a
  setting, not a decision. `S`: *"Never ask him for a second thing."*
- **GREEN — QUIET is dim, not amber.** Dimming carries "nothing is arriving" without
  implying fault. Amber for a rest would train him to ignore amber, which is how the
  2026-08-20 real drop alarm got dismissed and cost eleven minutes (`S`).
- **DEGRADED never says stop.** Two sinks exist so that one can die without ending the take.
  The headline stays *STILL RECORDING*; the fault is the subtitle.
- **RED — LOST states its own evidence.** *"the room mic heard you playing at 12:59"* — because
  he has been given confident wrong status three times and the only repair is showing the
  reasoning.

### Bench legibility

- The light is **the window**, not a dot in it. Full-bleed field, one word, ≥ 96 pt.
  Readable at 8 feet, in peripheral vision, by colour alone.
- **Colour is never the only channel** — the word changes too (`DO NOT PLAY` / `RECORDING` /
  `STILL RECORDING` / `NOT RECORDING`). **[inference]**
- **Silent.** No sound, ever, unattended. `S`: *"`--no-play` matters. He is often at the
  keyboard and will tell you not to make sound."* Also `T-C`: *"I also want an agent searching
  (without making sounds on this laptop)."*
- **Quiet.** No log, no counters, no packet stats, no paths in the arming view. Note count and
  elapsed time live in one small line under the field. `T-D`: *"get it to not talk so god
  damned much."*

### What arming actually does, in one press

1. Take a power assertion (`PreventUserIdleSystemSleep` + `PreventSystemSleep`) for the
   session. `S`: *"SLEEP KILLS THE CAPTURE — pin it off before you say GO."* This is also a
   literal line of the commissioning request: *"make sure it survives sleep"* (`T-D`).
2. Open **two independent capture sinks** on the one CoreMIDI source, writing two files.
   Cheap; survives a process or disk fault; does not survive a BLE drop, which is why the mic
   is the third. `S`.
3. Start the room-mic witness.
4. Register a `MIDINotifyProc` so a setup change reconnects instead of going deaf.
5. Show RED. Wait for bytes on every sink. Show GREEN. Say nothing else.

---

## 4. The post-session flow

### The default path has zero questions

`postprocess` (written today, `~/.claude/skills/fp30x-recording/bin/postprocess`) is the
working prototype of this and its header quotes the requirement:

> *"This should all be an automated pipeline that you can run in the background while
> continuing to talk to me."* — `T-D`, 08-22

Pressing **Stop** starts it. Nothing is asked. The sequence, all of it already implemented as
Python modules:

```
stop → ingest (incremental, resumes at a byte offset)
     → segment list
     → pick the longest segment as the performance
     → clip it, chasing CC64 across the entry
     → identify against 122,847 themes
     → for BOTH voices: gain-probe at 0.1, render at the computed gain,
       convolution hall (wet 0.28, RT60 2.1), encode to m4a
     → append the integrity record to takes/PROVENANCE.md
     → the take appears in the library, playable
```

Two details that are settled and must not become options: **the gain probe** (probing at 1.0
clips and then reports the clipping point rather than the signal) and **never peak-normalise
after compressing** (it produced a "compressed" file 4.4 dB quieter than its linear control).
Both from `S`, both bought with a bad master.

**Both voices render by default.** This is the move that removes the questions: electric and
acoustic are cheap relative to the capture, so "which voice?" stops being a prompt and becomes
a **toggle on the finished result**. `postprocess` already does this today.

### Where the choices go

The choices he sometimes wants are real. They go **after**, on the result — never before, as a
modal.

| choice | default | where it lives |
| --- | --- | --- |
| voice | **electric** (settled: `S`) | A/B toggle above the player; re-renders in background, keeps playhead |
| dynamics | **linear** (settled: `S`) | second toggle: `linear` / `compressed 3:1 @ −20`. Linear is his stated preference; compression exists because *"I have maxed out every speaker and the beginning is too quiet"* (`T-C`, 08-21) |
| space | **hall** (settled: `S`) | third toggle: `hall` / `dry` |
| which segment | **the longest** | a segment strip under the waveform; click any segment to re-cut. The full `segment list` table (start, dur, notes, n/s, stops, range, vel, key) is one disclosure click away — it is genuinely useful and genuinely not for the bench |
| where it starts | **first note of the chosen segment** | drag the left handle; pedal is chased across the new entry automatically |

His settled combination — **electric / linear / hall** — is the default of defaults, and the
app should render that one first so it is playable while the acoustic is still encoding.
**[inference: the ordering.]**

### The rule the flow exists to enforce

**No modal question ever stands between a finished take and a playable file.** Every
adjustment is applied to something that already plays. Corollary: the app never blocks on
render — the take is in the library the moment the clip exists, with the render state shown
in the row.

### Sharing

He asked twice, in one evening:

> *"let's prepare for an upload to YouTube, this is an original recording. I also separately
> want to get a high quality sound file uploaded to google drive that I want to share to a
> friend who asked for a recording."* — `T-C`, 08-21
> *"Upload it to NWD drive, I'll use it for some content somewhere."* — `T-C`, 08-21

v1 does the file-level half and stops at the network boundary: an **Export** action that
writes the master under its canonical name (§5) to a chosen folder and reveals it in Finder,
and a **Copy path** action. The app does not authenticate to Google or YouTube — that is
outward-facing, needs his consent per exchange, and is a whole surface of failure modes that
has nothing to do with pianos. See §7.

---

## 5. The take library

### What went wrong, precisely

Two files differing by one word nearly published a 3:22 fragment as a complete nocturne
(`S`, `T-C`). The root cause is that **the durable identity of a take is its filename** — the
filename is what travels to Drive, to YouTube, to Sarah — while everything that makes a take
distinguishable (duration, completeness, piece) lived only in an agent's context.

### Naming

Canonical, generated, never hand-typed:

```
Jake Mirra - <Work> - <COMPLETE|SEGMENT|STITCHED> <M>m<SS>s - <YYYY-MM-DD>.<voice>.<ext>

Jake Mirra - Chopin Nocturne in B major Op. 62 No. 1 - COMPLETE 9m37s - 2026-08-17.electric.m4a
Jake Mirra - Chopin Nocturne in B major Op. 62 No. 1 - SEGMENT 3m22s - 2026-08-21.electric.m4a
```

Raw captures keep `YYYY-MM-DD-<slug>.fp30`; clips go to `takes/clips/`; masters to `masters/`.
The word and the duration are both in the name, in that order, so the two files above sort
adjacently and differ visibly in the first field that differs.

### Completeness is computed, not typed

The skill's rule — *"Put FULL, COMPLETE or SEGMENT in the name"* — is a human protocol
patching a missing feature. **The data already knows**, and the pipeline already extracts every
term needed. From `P`:

| badge | derived from |
| --- | --- |
| **COMPLETE** | stream ended cleanly (trailer present), no notes held at end beyond the final pedalled chord, no orphan note-offs at the head |
| **JOINED LATE** | the take opens with orphan note-offs — the structural signature of a stream joined mid-note (four of them, 2026-08-21) |
| **CUT** | `held_at_end > 0`, or the trailer is missing — the capture stopped while he was playing |
| **STITCHED** | assembled by `stitch.py` from ≥2 takes; carries the seam offset and its vote margin |
| **FRAGMENT** | a segment clipped out of a longer take, not the whole of anything |
| **POLL-LOOP** | captured before 2026-08-17 through the 2 ms Python poll loop. 161 of 799 gaps read exactly 0.000 ms and nothing at all falls between 0 and 2.083 ms in 804 events — rolled chords and grace notes were collapsed at capture time and are unrecoverable (JPA-129). Five such takes are in the directory today. Timing questions about them are meaningless and the badge says so |

A badge the machine cannot justify is not shown.

### Two preservation rules

**A `.fp30` is irreplaceable; everything else is cache.** JPA-158 states it plainly: *"You cannot
re-play a take."* Renders, clips, indices and masters are all reconstructible from the capture and
a command. So: the app never deletes, moves, or overwrites a `.fp30`; a "delete" on a take deletes
its renders and offers the capture separately, with a different confirmation. The `.index/`
SQLite files are explicitly derived and deletable.

**A render is verified against its source before it is offered.** The note-drop defect described
in the summary was invisible because nothing compared the exported `.mid` to the take it came
from. Every render records the note count going in and the note count coming out, and a mismatch
is a red row, not a log line.

### The row

Duration is the largest thing in the row, because 3:22 vs 23:32 is the confusion that has
already nearly cost him. The identification is secondary and always carries its state.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  23:32   ▁▂▅███▇▅▃▁▂▄██████▇▅▂▁▁▃▅███▆▄▂▁▁▂▅██▇▄▂▁     [COMPLETE]         │
│          Chopin — Polonaise-Fantaisie in A♭, Op. 61        ? unconfirmed  │
│          2026-08-22 · 6,751 notes · link GOOD · electric+acoustic         │
├───────────────────────────────────────────────────────────────────────────┤
│   3:22   ▂▄███▅▃▁                                      [FRAGMENT]         │
│          Chopin — Nocturne in B major, Op. 62 No. 1        ✓ confirmed    │
│          2026-08-21 · 981 notes · link GOOD · electric · exported         │
├───────────────────────────────────────────────────────────────────────────┤
│   7:24   ░░▂▄███▆▄▂▁▁▃▅███▆▃▁                          [JOINED LATE]      │
│          Chopin — Nocturne in B major, Op. 62 No. 1        ✓ confirmed    │
│          2026-08-21 · 1,930 notes · opening missing, starts 0m46          │
└───────────────────────────────────────────────────────────────────────────┘
```

The sparkline is note density over the take. It does two jobs at once: it makes a 23-minute
session visibly different in shape from a 3-minute fragment, and it makes the multi-piece
structure of a long session legible before any click — that is the *"I was playing some random
stuff. Then did a take of rachmaninoff. Then practiced… Then at a certain point I started doing
interesting stuff in C-major. Find that point and clip it for me"* problem (`T-C`, 08-20),
answered by looking rather than by asking. **[inference: the sparkline; the underlying segment
data is real and already computed.]**

`link GOOD` is the take's own integrity verdict, already generated per-take into
`PROVENANCE.md` — pairing completeness, inferred loss, 5 ms lattice conformance. It answers
*"was this take any good"* before he spends nine minutes listening to find out.

### One thing the library must not claim

`.fp30` records **the key pressed, not the pitch that sounded**. Transpose and Master Tuning never
reach the MIDI stream — verified by experiment on 2026-08-20, four repetitions of one phrase at
different settings producing byte-identical note numbers (`S`, JPA-155). So a render can be in a
different key from what he heard in the room, and the app cannot detect it. The detected-key field
is therefore labelled *key as played on the keys*, and when a key reading disagrees with him the
app defers: he plays scales like G A B♭ C♯ D E♭ F♯ G, and the likelier fault is a detector
collapsing a non-traditional scale onto the nearest diatonic set. **[inference: the label wording.]**

### Live, never cached

The library is a live view of `~/Music/FP-30X Studio/takes/` via FSEvents. **There is no
refresh button because there is nothing to refresh.** Direct response to *"it doesn't have any
of my recordings / isn't autorefreshing / has no refresh button"* (`T-B`, 08-18).

---

## 6. The identifier, presented honestly

### What it actually is, and what it is for

`fp30x_studio/identify/` matches melodic interval n-grams from the live note stream against a
122,847-theme corpus, using Shazam's alignment-histogram trick with score position in place of
time. No model is called anywhere in it. It is genuinely good work and it is genuinely thin
evidence, and the design problem is showing both at once.

The framing that makes the design tractable is from the project's own product thesis (JPA-147):

> *"At a MIDI piano the user is the source and knows what he played. The demand is not
> identification, it is **labelling without effort**."*

He is never asking the app *what is this*. He knows. He is asking it to **write down** what he
knows, so that a year of takes carries its own index. That reframe decides every choice below: the
identifier's job is to make a correct label cheap to accept, not to be right.

### The five facts that must shape the presentation

**1. Today's verdict was 0.4% coverage.** From `T-D`, 2026-08-22:

> *"**23:32, 6751 notes.** The identifier says Chopin, **Polonaise-Fantaisie in A♭, Op. 61** —
> twice, conf 0.95 and 0.97, but only on ~30-note windows out of 6751, so treat it as a guess
> until you tell me otherwise."*

Two 30-note windows out of 6,751 notes is 0.4% of the performance. **A confidence number
without a coverage number is a lie by omission**, and 0.95 is exactly the kind of number that
gets glanced at from a piano stool and believed.

**2. The high-confidence numbers in the historical record were self-recall.** From `L` and
JPA-149: four segments of `2026-08-17-session.fp30` returned 0.949, 0.997, 0.993 and 0.999 —
against corpus themes *derived from that same take*, and unchanged when the corpus doubled
underneath them, which is what a tautology does. `docs/label-segments.html` already flags these
with a **circular** badge. v1 inherits that badge as a first-class state.

**3. When it cannot align a performance, it does not refuse — it reaches.** This is the most
important fact in the section and it was itself discovered by correcting a wrong correction
(JPA-149, 2026-08-22):

> *"My earlier comment said the Chopin segment 'produces no verdict at all'. **That is wrong**,
> and the softer version is the more flattering one."*

The Op. 62 segment does answer. It answers **Anonymous, "HOERT DIE LERCHE SIE SINGT", Op. 150**,
on 6 aligned hits at margin 0.333 — while the corpus holds 85 indexed theme windows of the actual
work from the Chopin Institute's scores. Why the right answer lost:

```
performance   E  F# C# B  A# G# F# D#     intervals   2, 7, -2, -1, -2, -2, -3
score         E     C# B  A# G# F# D#     intervals     -3, -2, -1, -2, -2, ...
```

Same theme. **One interpolated F#.** The matcher votes on 3-grams, so one inserted note reframes
everything downstream: 5 of 13 interval 3-grams survive, the true match falls under the gate, and
something else clears it. JPA-149's own conclusion:

> *"A false positive is a worse failure than a refusal, because a refusal is visible and a wrong
> answer is not."*

The test suite had a hole shaped exactly like this: `test_removing_the_answer_from_the_corpus_
produces_silence_not_a_guess` passes, but it only covers a piece **absent** from the corpus — not
one present that fails to align. Different failure modes; one was tested.

**4. His verdicts are already collected, and they say the confidence number is not usable as a UI
primitive.** `takes/labels.json` holds **27 verdicts** — 17 confirm, 5 correct, 4 reject, 1 unknown
— as of 2026-08-20. Read against the confidence the matcher assigned:

| segment | proposal | conf | his verdict |
| --- | --- | --- | --- |
| `2026-08-17-wiggle#1` | Chopin, Op. 48 No. 2 | **0.000** | **confirm** |
| `2026-08-20-a#5`, `#6` | Mozart, Sonata Facile i | **0.000** | **confirm** (both) |
| `2026-08-19-a#2` | Chopin, Op. 55 No. 1 | **0.466** | **confirm** |
| `2026-08-19-b#0` | Chopin, Op. 9 No. 1 | 0.532 | **reject** |
| `2026-08-20-a#0` | Palestrina, *Sanctus* | **0.865** | **reject** |
| `2026-08-20-a#4` | Haydn, Op. 74 No. 1 ii | 0.374 | **reject** (revised from confirm) |
| `2026-08-20-a#1` | Chopin, Polonaise-Fantaisie Op. 61 | 0.000 | **correct** → Nocturne in B, Op. 62 |

Five confirms at conf ≥ 0.99 are genuinely right. But **a 0.865 was wrong and three 0.000s were
right.** Any surface that gates on this number will suppress correct answers and announce wrong
ones — in both directions, on his own labelled data. Two consequences for v1:

- **Sub-threshold proposals are shown anyway**, marked as such. JPA-148 predicted the case
  exactly: *"a 0.466 that he confirms is a threshold that is too high, and there is no way to know
  which without asking him."* It then happened.
- **A verdict is never presented as a probability.** It is presented as evidence he can check,
  and the number that actually discriminates — coverage — is stated in the same breath.

**5. One segment can contain more than one work.** His own correction, in `labels.json`:
*"Multiple pieces, including Rondo A La Turca starting toward the middle. Not a clean label."*
The label schema must admit a list, and the correction field must accept free text. A UI that
forces one work per segment will collect wrong ground truth.

### How a verdict is shown

Never a bare confidence. **Three numbers or none**, phrased as a falsifiable sentence:

```
┌───────────────────────────────────────────────────────────────────────────┐
│  PROBABLY   Chopin — Polonaise-Fantaisie in A♭, Op. 61                    │
│                                                                            │
│  matched 30 of 6,751 notes (0.4%) in 2 places                             │
│  clear of the runner-up (Chopin, Op. 44) by 4 aligned intervals           │
│  ▏▏                                                          coverage 0.4% │
│                                                                            │
│  [ ✓ that's right ]   [ ✗ no — it's … ]   [ skip ]                        │
└───────────────────────────────────────────────────────────────────────────┘
```

Three registers of language, keyed on coverage and separation rather than on the confidence
product alone:

- **`IS`** — ≥10% of the take's notes matched, across ≥3 separated windows. *"This is Chopin,
  Op. 62 No. 1."* **[inference: the thresholds are proposed, not measured — they need
  calibrating against `labels.json`.]**
- **`PROBABLY`** — fires but thin, as today. Coverage stated in the same breath, always.
- **`NOT IDENTIFIED`** — shown when nothing clears the gate, with the caveat attached rather than
  buried: *"Not identified. That is not evidence it is unfamiliar music — a single added chord
  tone can reframe every n-gram in a passage and cost the true match its hits."*
- **`CIRCULAR`** — the matched theme was derived from this take. Shown **in place of** a verdict,
  never alongside a confidence. Detection is mechanical: the take's stem appears in
  `Theme.source`. Four segments of `2026-08-17-session.fp30` match at 0.949–0.999 and hold that
  value when the corpus doubles underneath them, *"which is what a tautology does"* (JPA-151).
- **`OFF BY DEFAULT: nothing`** — there is no fifth state where the app quietly picks the
  runner-up. When he corrected the circular ones the runner-up was right twice (Joplin, *Fig Leaf
  Rag*; Joplin, *Sugar Cane*) and wrong once (a nocturne where the answer was Chopin's Ballade 3).
  Two out of three is not a feature.

### His verdict is ground truth

He asked for the correction surface himself, in one sentence:

> *"Op 55 No 1 was most definitely played yesterday, I could confirm any of these if you just
> put an html doc up that had recording snippets and then asked me to confirm the labels."*
> — `T-B`, 08-19

And he has corrected it with more precision than a confidence score can carry:

> *"Absolutely, that playthrough that you're referring to with the GGFEDCBCDG is STRENUOUS LIFE
> by SCOTT JOPLIN. You should have paid more attention to the syncopation, preferring [3/16,
> 1/16] over [1/8, 1/8] for a 1/4-measure. Should give rag."* — `T-B`, 08-18
> *"The more recently-played piece was Chopin, but you need to get the opus and number."* — `T-B`, 08-18

Design consequences:

1. **Four verdicts, not two**, on every take row and in the post-session view — the set JPA-151
   settled on and the set his 27 answers actually used: **confirm**, **reject**, **correct it**
   (free text), **don't know**. Two-button designs force a reject where the truth is a correction,
   and the corrections are the valuable half. The store already exists: `takes/labels.json`, keyed
   `take.fp30#segment`, holding `verdict`, `correction`, `revised_from`, and the proposal with its
   `source`.
2. **Written on every press, never on a submit**, and loaded on start so the queue resumes.
   Revisions record what they were revised *from* — he revised one verdict from confirm to reject
   after listening again, and that reversal is data.
3. **Once he has answered, the machine's guess is demoted to a footnote and never shown as the
   headline again.** A corrected label is the take's name. JPA-151's phrasing of the layout rule:
   the guess sits in an *"identifier said"* row underneath, *"so the confidence figure is never
   read as belonging to his label."*
4. **A correction is training data, not just a label.** Every mismatch between his verdict and
   the matcher is a case for the corpus and the prefilters — the Op. 62 interpolated-F# defect
   was found exactly this way. The library needs a view of "takes where he and the matcher
   disagree", which is the highest-value list in the app for anyone improving it.
   **[inference]**
5. **Play the snippet inline.** He confirms by ear, not by reading a note list. The existing
   labelling page synthesises segments in-browser with the pedal chased; a native app can play
   the actual render.

---

## 7. Out of scope for v1

This section is load-bearing. The failure mode of this project is not missing features — it
has twelve modules and a 175-test suite. It is that none of them are reachable without an
agent. Anything added to v1 that is not on the path from bench to file makes that worse.

**Rule for the cut: if it does not sit between him and a listenable recording, it stays on the
command line, where it already works.**

| out | why |
| --- | --- |
| **MIDI / piano-roll editing, quantisation, note nudging** | Never requested once in four sessions. Actively contrary to the project's stated position: *"Timing, velocity and pedal are the performances themselves — hardware timestamps applied near the CoreMIDI driver, nothing quantised"* (`P`). Note editing is most of what makes a DAW complicated. |
| **Notation / sheet-music export** | Zero requests. He reads and names pieces by ear and by opus number; he has never asked to see one on a stave. |
| **Multi-track, non-piano instruments, mixing, EQ** | Zero requests. One instrument, one performer. |
| **The timbre workbench and synth fitting** | Genuinely his interest and heavily worked (JPA-138/139/154/156/157), but it is research, and the voice question is *settled* — electric / linear / hall. v1 ships two presets and the three toggles of §4. The workbench keeps its existing web/CLI life. |
| **Audio → performance inversion; the warp homeomorphism; `align/`** | *"why can't we learn a projection from sound space to performance space"* (`T-B`), *"the tangent space to the space of homeomorphisms… variational problem"* (`T-B`). Real mathematics, real tickets — and no bearing on getting a take to a file. |
| **Stitching two takes together** | Used once, on 2026-08-21, to repair a capture that went deaf mid-nocturne. It is a repair for the failure §3 exists to prevent. Keep `stitch.py`; do not build UI for it. Revisit if arming still loses openings after v1. |
| **Google Drive / YouTube upload** | Asked for twice (`T-C`) — the only cut here with real repetition behind it, so it is called out rather than dropped. But it is outward-facing (his standing rule: confirm before publishing, per exchange), it needs OAuth and a token-refresh failure surface, and the actual need — *a correctly-named high-quality file in a folder* — is met by §4's Export. |
| **USB audio capture of the FP-30X's own voice** | JPA-133 / JPA-155. This is the most valuable open question in the project — every `.wav` on this machine is a fluidsynth render, and no recording of the instrument he actually plays exists anywhere. But it is blocked on a cable, and it changes the *source*, not the app. **The sink abstraction must accept it without a rewrite; v1 does not build it.** |
| **Repertoire logging** — "what did I play this week" | JPA-147 argues this is the *actual* product: *"not identification — the artifact identification makes possible with zero user effort, which accumulates and therefore has switching cost."* It is probably right. It is also downstream of a library whose labels are trustworthy, which is what v1 builds. Ship v1; the log is v2's first feature and costs nothing extra once labels are honest. |
| **Guided capture for the instrument's own voice** (JPA-155 protocol) | 13 notes × 3 dynamics × 4 sets is a real wizard and a real need — no recording of the FP-30X's actual voice exists anywhere on this machine. Blocked on a $6 adapter, and it is an instrument-analysis workflow, not the bench-to-file loop. |
| **A second user, accounts, sync, preferences UI** | One user, one machine. Settings are a plist and a `⌘,` sheet with under ten rows. |
| **Any LLM in any loop** | The explicit purpose of the rewrite (`T-D`). `identify/` already states the rule: *"No model is called anywhere in this package… a loop that calls out is a loop that cannot run every three seconds for an hour."* |
| **Sound the app makes on its own** | Hard prohibition, not a preference. `S`: *"`--no-play` matters."* The app plays audio only on an explicit press, never on completion, never on launch, never on arm. |

---

## 8. Wireframes

### 8.1 Arming and the live session

**RED — ARMING.** The state every session starts in. One instruction, one thing asked for.

```
┌──────────────────────────────────────────────────────────────────────┐
│ FP-30X Studio                                                    ⌘,  │
├──────────────────────────────────────────────────────────────────────┤
│ ████████████████████████████████████████████████████████████████████ │
│ ██                                                                ██ │
│ ██                                                                ██ │
│ ██                  D O   N O T   P L A Y                         ██ │
│ ██                                                                ██ │
│ ██              press one key on the piano                        ██ │
│ ██                                                                ██ │
│ ██                                                                ██ │
│ ████████████████████████████████████████████████████████████████████ │
│                                                                      │
│  waiting for the first byte · 4 s                          [ Cancel ]│
└──────────────────────────────────────────────────────────────────────┘
```

**GREEN — RECORDING.** Everything except the word and the clock is 11 pt and grey.

```
┌──────────────────────────────────────────────────────────────────────┐
│ ████████████████████████████████████████████████████████████████████ │
│ ██                                                                ██ │
│ ██                  R E C O R D I N G                             ██ │
│ ██                                                                ██ │
│ ██                       12:47                                    ██ │
│ ██                                                                ██ │
│ ████████████████████████████████████████████████████████████████████ │
│                                                                      │
│  2026-08-22-home · 3,208 notes · 2 of 2 sinks · mic on               │
│  last note 0.4 s ago                                        [ Stop ] │
└──────────────────────────────────────────────────────────────────────┘
```

**GREEN — QUIET.** Same field at 40% brightness. He stopped playing; nothing is wrong.

```
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ dimmed green ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
│ ▓▓                  R E C O R D I N G                             ▓▓ │
│ ▓▓                       14:02                                    ▓▓ │
│ ▓▓                  no notes for 38 s                             ▓▓ │
│ ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ │
│  link alive · room quiet                                    [ Stop ] │
```

**AMBER — DEGRADED.** One sink died. Do not stop playing.

```
│ ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ amber ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ │
│ ▒▒            S T I L L   R E C O R D I N G                       ▒▒ │
│ ▒▒                       18:03                                    ▒▒ │
│ ▒▒          mirror stopped 41 s ago — primary is growing          ▒▒ │
│ ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ │
│  keep playing · 1 of 2 sinks · mic on          [ Restart mirror ]    │
```

**RED — LOST.** The state that cost him a nocturne's opening, six minutes, and eleven minutes.

```
│ ████████████████████████ red, pulsing ██████████████████████████████ │
│ ██              N O T   R E C O R D I N G                         ██ │
│ ██                                                                ██ │
│ ██        the link died at 12:47 · 41 s ago                       ██ │
│ ██        the room mic heard you playing at 12:59                 ██ │
│ ██                                                                ██ │
│ ████████████████████████████████████████████████████████████████████ │
│                                                                      │
│  12:47 is safe on disk (3,208 notes)      [ Re-arm ]   [ Stop, keep ]│
```

Note what `Re-arm` does: it starts **new** sinks and leaves the old files closed and intact.
It never reuses a file it is not certain about.

**FAILURE — NO PIANO.** Distinguished from RED — ARMING, because the fix is different.

```
│ ██              N O   P I A N O                                   ██ │
│ ██   no CoreMIDI source matching "FP-30X"                         ██ │
│ ██   turn the piano on, then pair it in Audio MIDI Setup          ██ │
│                                    [ Open Audio MIDI Setup ]         │
```

### 8.2 Landing — the post-session view

Opens automatically on Stop. Nothing is asked. Nothing plays until pressed.

```
┌──────────────────────────────────────────────────────────────────────┐
│ ‹ Library          2026-08-22-home                                   │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ▁▂▅███▇▅▃▁▂▄██████▇▅▂▁▁▃▅███▆▄▂▁▁▂▅██▇▄▂▁▁▂▃▅███▇▅▃▂▁              │
│  └seg 4┘  └────────────── segment 5 · 23:32 ──────────────┘  └6┘     │
│  ▶ ────────────────●───────────────────────────────────  4:12/23:32  │
│                                                                      │
│  voice   [ electric ]  acoustic        rendering acoustic… ▓▓▓▓░░ 62% │
│  gain    [ linear ]    compressed                                    │
│  space   [ hall ]      dry                                           │
│                                                                      │
│  PROBABLY  Chopin — Polonaise-Fantaisie in A♭, Op. 61                │
│            30 of 6,751 notes (0.4%) in 2 places; clear of Op. 44 by 4 │
│            [ ✓ right ]  [ ✗ no — it's … ]  [ skip ]                  │
│                                                                      │
│  COMPLETE · 6,751 notes · link GOOD, 0 inferred loss, 99.7% lattice  │
│                                                                      │
│  [ Export… ]  [ Copy path ]  [ Reveal in Finder ]   ▸ 7 segments      │
└──────────────────────────────────────────────────────────────────────┘
```

Everything above is reached with zero clicks. The segment strip, the three toggles and the
`▸ 7 segments` disclosure are the entire choice surface, and each acts on something that is
already playing.

**Post-session failure — nothing usable.** Says what it has, not what it wanted.

```
│  ✗ 2026-08-22-b · 485 bytes · 6 notes · 0.0 s                        │
│    No segment longer than 3 s. Nothing was rendered.                 │
│    The capture ran 4 min and received notes for 0.4 s of it.         │
│    [ Keep ]   [ Delete ]                                             │
```

### 8.3 Return — the library

The window that opens when he launches the app.

```
┌──────────────────────────────────────────────────────────────────────┐
│ FP-30X Studio                                    [ search ]      ⌘,  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓  │
│  ┃                        A R M                                   ┃  │
│  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛  │
│                                                                      │
│  TODAY                                                               │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ 23:32  ▁▂▅███▇▅▃▁▂▄█████▇▅▂▁▃▅███▆▄▂▁▂▅██▇▄▂▁    [COMPLETE]    │  │
│  │        Chopin — Polonaise-Fantaisie in A♭, Op. 61  ? unconfirmed│  │
│  │        6,751 notes · link GOOD · electric, acoustic       ▶     │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  YESTERDAY                                                           │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  7:24  ░░▂▄███▆▄▂▁▁▃▅███▆▃▁                    [JOINED LATE]   │  │
│  │        Chopin — Nocturne in B major, Op. 62 No. 1  ✓ confirmed │  │
│  │        1,930 notes · opening missing, starts 0m46         ▶     │  │
│  ├────────────────────────────────────────────────────────────────┤  │
│  │  3:22  ▂▄███▅▃▁                                 [FRAGMENT]     │  │
│  │        Chopin — Nocturne in B major, Op. 62 No. 1  ✓ confirmed │  │
│  │        981 notes · link GOOD · exported                   ▶     │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  AUG 17                                                              │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  9:37  ▁▃▅███▇▅▃▂▁▂▄███▇▅▃▁▂▄██▇▅▃▁             [COMPLETE]     │  │
│  │        Chopin — Nocturne in B major, Op. 62 No. 1  ✓ confirmed │  │
│  │        1,730 notes · link GOOD · exported                 ▶     │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**If a capture is already running when the app opens, it opens on 8.1, not here.**

---

## 9. The one-screen test

*He has not touched the app for a month. He opens it. What does he see, and how long before he
knows what to do?*

**Target: under one second to arm; under five to identify any past take.**

What has to be true for that:

| requirement | the failure it answers |
| --- | --- |
| It is an app in `/Applications` with a Dock icon. No command, no venv, no `run.sh`. | *"What is the command to run fpx studio? Run it please."* — `T-B`, 08-19 |
| The window that opens **is** the library. No dashboard, no home screen, no empty state. | *"it doesn't have any of my recordings"* — `T-B`, 08-18 |
| One primary button, `ARM`, larger than anything else and always in the same place. | Five separate requests for a green light across five sessions |
| Every row answers *what is this* with no click: duration first, completeness badge, piece, date. | The 3:22-vs-9:37 near-miss; the four ambiguously-named masters on disk |
| The list is live from the filesystem. No refresh button, because nothing is cached. | *"isn't autorefreshing / has no refresh button"* — `T-B`, 08-18 |
| Nothing plays. Nothing pops up. No release notes, no tips, no "what's new". | *"`--no-play` matters"*; *"get it to not talk so god damned much"* |
| If a capture is running, it opens on the light instead. | Reconnecting to a session in progress, after a crash or a reopen |

**The test, stated as a pass/fail he can run himself:** open the app cold, and without reading
anything, (1) start a recording, and (2) name the longest take he has ever made. If either
takes more than one deliberate thought, v1 has failed, and the fix is subtraction.

The standard is his, and it predates the app:

> *"Actionable output has to clearly benefit me. My reading time has to be justified by the
> result of the reading. The state of the world has to change in a way that I positively value.
> If this standard cannot be met by output, then, first and foremost, say less."* — `T-B`, 08-17

---

## Appendix A — what he never once asked for

Compiled by scanning every user message across all 83 transcripts on this machine for
piano-related terms. Across four sessions of intensive work, he never once asked to:

- edit, quantise, or nudge a note
- see or export notation
- record anything other than the piano
- change tempo, add a click track, or record to a grid
- mix, EQ, or master beyond "make it louder without saturating"
- organise takes into projects, albums, folders, or playlists
- tag, rate, star, or favourite anything
- see a settings screen

He asked, repeatedly, for exactly four things: **a green light**, **peek**, **make it
listenable**, and **what piece is this**. That list is the product.

## Appendix B — the requirements hiding inside the profanity

Each of these is a design constraint, stated in the moment it was violated.

| what he said | when | the requirement |
| --- | --- | --- |
| *"JUST STOP TALKING AND JUST PLAY AN AUDIO FILE ON THIS LAPTOP WITH NOTES I PLAYED"* | `T-A` 08-16 | The default path ends in a playable file, not in a report |
| *"why is this still so fucking complex?"* / *"You're supposed to give me a green light"* | `T-D` 08-22 | One signal, not a status report |
| *"get it to not talk so god damned much"* | `T-D` 08-22 | Silence is the default output of a working system |
| *"I want to be playing piano."* | `T-D` 08-22 | Every second between his intent and a green light is the product's fault |
| *"Looks like you need to improve your tooling."* | `T-B` 08-19 | An inconclusive peek is a failure, not an answer |
| *"I don't know what any of that means. What do I click and type"* | `T-B` 08-20 | Never explain the architecture at the bench |
| *"Wait this is three minutes I want the full recording"* | `T-C` 08-21 | Duration and completeness are identity, not metadata |
| *"The sound is extremely 8-bit, does not sound anything like a cool electric piano"* | `T-B` 08-19 | The default voice must be the good one — this is why electric/linear/hall is settled |
| *"It is preferable to playing for an hour and finding out it's garbage."* | `T-B` 08-17 | The light must persist for the whole session |


---

## Appendix C — three things to settle before implementation

Each of these changes the design, not just the code. None is expensive.

1. **Does `kMIDIPropertyOffline` flip when a BLE-MIDI source stops delivering?** If it does, the
   link probe of §3 is four lines of CoreMIDI and the room mic becomes optional redundancy rather
   than the load-bearing witness. If it does not, the mic is mandatory in v1 and the app needs
   microphone permission on first launch. Cost: one afternoon with the piano and a power switch.
2. **Is the 5 ms lattice the radio or the piano?** JPA-133, blocked on a USB-C-to-USB-B cable.
   *"If the grid is the radio, sub-5 ms timing exists and a USB path unlocks chord-simultaneity,
   roll spread, and hand-independence analysis… If the grid is the piano, none of those are
   recoverable at all."* It does not change v1's three moments, but it decides whether the capture
   source abstraction needs to carry two fidelity classes or one.
3. **Recalibrate the identifier's gates against `labels.json` before shipping any threshold.**
   27 verdicts exist and no threshold in the code has ever been fitted to them. Three confirmed
   0.000s and one rejected 0.865 are enough to say the current gate is wrong; they are not enough
   to say what the right one is. The `IS` / `PROBABLY` boundaries in §6 are placeholders and are
   marked as such.
