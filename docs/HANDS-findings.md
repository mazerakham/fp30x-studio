# Left hand / right hand from one MIDI stream: does an adaptive boundary beat a fixed split?

Run 2026-09-19. New file `fp30x_studio/hands.py`; nothing else in the package was modified,
and nothing was committed. Venv: `~/workspace/audio/.venv`. Harness code lived in a scratch
directory and is reproduced in outline below.

**Verdict, in one sentence.** For Jake's ragtime the adaptive boundary is **not** worth it:
a constant split at E4 (MIDI 64) scores 96.9% against the adaptive classifier's 95.6% over
5,752 score-aligned notes of his four 2026-09-19 Joplin takes. Across the wider corpus of 47
Joplin scores the adaptive boundary does win, 95.8% against 93.7%, so the machinery is not
worthless; it is just not where the available improvement is. The available improvement is
moving the constant up four semitones from middle C, which is worth 11 points on his takes.

Page with the renders and the plots:

```
show ~/.claude/served/hands/index.html
```

---

## 1. What was built

`fp30x_studio/hands.py` assigns every note-on in a take to LEFT or RIGHT. Three strategies,
all of them a *boundary pitch per onset cluster*, differing only in how the boundary is
chosen:

| strategy | boundary | causal? |
|---|---|---|
| `fixed` | one constant, middle C by default | yes |
| `median` | midpoint of the medians of the recent left and right pitches | yes |
| `viterbi` | hidden state, dynamic programming over the whole take | no |

`median` is the direct two-sided generalisation of `identify.features.RegisterLine`, which
was the seed of this work. `RegisterLine` keeps a running median of admitted pitches and
rejects a skyline note more than `REGISTER_DROP` semitones below it: a one-sided filter with
one window. `median` keeps two windows, one per hand, and assigns rather than rejects.

`viterbi` minimises `sum_t E(b_t, C_t) + sum_t w_move * |b_t - b_{t-1}|` over boundary pitches
`b_t` in [21, 109). The emission `E` has four terms:

* **Registers.** Over a +/-0.6 s window, the cost of `b` as a one-dimensional two-means split
  of the pitches sounding there, normalised by note count. This is the term that finds the
  valley between the hands and follows it.
* **Separation.** Two-means will cut a single register in half when one hand is resting, so
  the split is charged whenever the two centroids come within 14 semitones. The emission then
  goes flat and the transition term carries the boundary through.
* **Reach.** `(span - 15)^2` per hand over the pitches struck within +/-0.12 s. Forbids the
  degenerate answers: a boundary off the bottom of the keyboard puts a stride bass and a
  melody two octaves up into one hand.
* **Gap.** A boundary dropped between two notes a whole tone apart is charged for it.

Hyperparameters were tuned on 12 of the 47 Joplin scores (every fourth, alphabetically) and
every number below that says "held out" is from the other 35. Tune 95.75%, test 95.77%, so
the tuning did not overfit.

### The modelling assumption, and its measured ceiling

All three strategies split each onset cluster **contiguously**: within one struck
simultaneity, every left-hand note is below every right-hand note. `oracle_accuracy` measures
what that assumption costs by choosing the best split point per cluster *with the labels in
hand*.

Across all 47 rags: **25,762 onset clusters, 13,289 with both hands in them, zero in which
the hands cross.** The oracle is exactly 100.00%. Joplin never writes the left hand above the
right on the same beat, so for this repertoire the contiguity assumption is free and the
missing 4% is a boundary-placement problem, not a model-class problem. This will not hold on
Chopin, and the same function will say so when it is pointed there.

---

## 2. Ground truth

There is no hand labelling for the takes, so two independent sources were used.

**Scores.** `~/workspace/audio/corpus/raw/joplin/kern/*.krn`, 47 pieces, 72,238 notes. A
two-staff `**kern` piano score has the hands as separate spines; the spine with the lower
median pitch is the left hand. music21 reads them; `notes_from_kern` returns a note stream
with `hand` filled in. Score timings are notated, not played, so the score is an easier
problem than a performance and its numbers are an upper bound.

**Performances, by transferring the score labels.** For each take, the performance is cut
into chunks of 40 onset clusters and each chunk is placed in the score by subsequence DTW
(free start and end in the score, cost `1 - |A ∩ B| / max(|A|, |B|)` on pitch sets). Chunking
rather than aligning globally is what makes the repeat structure of a rag irrelevant: a chunk
from the second pass of the B strain matches the B strain. A chunk is accepted at mean path
cost < 0.45 with > 60% of its notes matching a score pitch exactly. Within an accepted pair,
a performance note takes the hand of the score note of the same pitch in the matched cluster,
and is left unlabelled when that pitch appears in both hands.

Coverage: 5,752 of 8,106 notes across the four takes (71%). The unlabelled 29% are the wrong
notes, the ornaments, and the passages the alignment would not commit to, which are also the
hardest notes to classify. Every strategy is flattered by the same amount, so the comparison
between them stands even though the absolute numbers are optimistic.

---

## 3. Results

### 3.1 Jake's four takes, score-aligned labels

| take | labelled / notes | middle C | E4 = 64 | F4 = 65 | median | adaptive |
|---|---|---|---|---|---|---|
| Something Doing | 1846 / 2297 | 83.91 | 96.26 | **97.40** | 93.50 | 95.83 |
| Swipesy | 1699 / 2242 | 86.87 | **99.12** | 99.00 | 93.53 | 95.11 |
| Sunflower, part 1 | 736 / 1805 | 87.91 | **97.96** | 97.42 | 92.93 | 92.66 |
| Sunflower, part 2 | 1471 / 1762 | 86.06 | 94.77 | 94.77 | 93.13 | **97.48** |
| **weighted** | **5752** | **85.85** | **96.94** | **97.20** | **93.34** | **95.63** |

The adaptive classifier wins on one of the four. A constant at F4 wins overall by 1.6 points.

### 3.2 47 Joplin scores, 72,238 notes

| strategy | accuracy | error rate |
|---|---|---|
| fixed, middle C | 87.56 | 12.44 |
| fixed, E4 = 64 (best single constant) | 93.78 | 6.22 |
| running median, window 8 | 93.32 | 6.68 |
| **adaptive, Viterbi** | **95.76** | **4.24** |
| ceiling of the contiguous-split model | 100.00 | 0.00 |

Held out (35 pieces, 54,000 notes) the adaptive number is 95.77 and fixed at 64 is 93.69, so
the 2-point gap is real and not a tuning artefact.

### 3.3 How much should the boundary move?

`w_move` is the cost per semitone of boundary movement. Send it to infinity and the boundary
freezes into the best constant the emission costs can find without labels; send it to zero
and it follows the emission anywhere. On the 35 held-out rags:

| `w_move` | accuracy | mean within-piece sd of the boundary |
|---|---|---|
| 0 (free) | 94.72 | 4.94 semitones |
| 0.3 | 95.07 | 3.33 |
| 1 | 95.40 | 3.06 |
| **3 (default)** | **95.77** | **2.51** |
| 10 | 95.58 | 1.93 |
| 100 | 93.80 | 0.47 |
| 1000 (frozen) | 93.41 | 0.00 |

Unimodal, and the shape is the answer to the question the module exists to ask. The boundary
*should* move, and should move about a tone and a half either side of its resting place. The
whole benefit of adaptivity is the 2.4 points between the frozen end of that curve and its
peak. Figure: `docs/figures/hands-stiffness.png`.

### 3.4 Constraint violations, where there are no labels at all

`violations()` counts moments a hand is asked to hold more than 15 semitones inside 0.12 s,
and clusters where the assignment puts a left-hand note above a right-hand one. On the
rendered 80-second excerpt:

| strategy | span violations / min | crossings / min | widest reach |
|---|---|---|---|
| fixed, middle C | 11.5 | 0 | 24 semitones |
| median | 1.5 | 0 | 20 |
| adaptive | 0.0 | 0 | 12 |

This agrees with the labelled result about middle C, and it needs no score, so it is the
check to run on a take with no corpus match. It does not prove an assignment right: the
all-one-hand answer has zero crossings.

---

## 4. What failed

**A free-movement allowance.** The first design let the boundary move `free_move` semitones
per cluster for nothing, plus `move_rate` per second of elapsed time, on the argument that a
hand travels further when it has longer to travel. It costs about a point: the boundary
spends the allowance on noise. Both parameters survive in the signature and default to zero.

**A first cut of the emission with no register term** (reach and gap only) scored 79.7% on
four rags, worse than middle C. Without the two-means term the emission is nearly flat and
the DP has nothing to anchor to, so the boundary wanders into whatever gap is nearest. This
is worth recording because it is the difference between a plausible-looking cost function and
one that works.

**The running median loses to a good constant** (93.32 against 93.78). The direct
generalisation of `RegisterLine` does not beat a number you can write down. It remains the
only one of the three that is causal, which is the only reason to reach for it.

**Nine of 47 rags are worse under the adaptive boundary than under a flat 64**, and two of
those are large: `searchlight.krn` 87.74 against 97.18, and `swipesy.krn` 93.08 against 98.70.
Swipesy is one of the four pieces Jake plays. The failure mode is the boundary chasing a
passage where one hand rests and the other spreads out; the separation guard reduces it but
does not remove it.

---

## 5. Renders

Eighty seconds of Something Doing, 149.870 to 229.650 s of
`~/Music/FP-30X Studio/takes/clips/2026-09-19-something-doing-SEGMENT.fp30`. That window was
picked because it carries the highest disagreement between middle C and the adaptive boundary
of any window in the take (about 22% of notes), so it is the passage where the comparison has
the most to show. Start and end snapped to the nearest onset gap of at least 0.35 s.

Rendered with fluidsynth against `MuseScore_General.sf3`, probed at `-g 0.1`, gain recomputed
to land at -3.00 dBFS peak, re-rendered, then two-pass `loudnorm` to -18 LUFS with
`,aresample=48000` and encoded to 192 kbps MP3. All five pass `python -m fp30x_studio.qa`.

| file | what |
|---|---|
| `01-control-clavinet.mp3` | one instrument, both hands (clavinet, GM 7) |
| `02-fixed-middle-c.mp3` | clavinet right / electric bass left, split at 60 |
| `03-adaptive.mp3` | same instruments, adaptive boundary |
| `04-adaptive-extreme.mp3` | vibraphone right / synth bass left, adaptive boundary |
| `05-fixed-f4.mp3` | same instruments as 02 and 03, split at 65 |

Track 5 is not in the original brief. It is there because it is the configuration that wins
and it would be strange to publish the comparison without it.

Measured against the transferred score labels, over the excerpt's 283 labelled notes:

| render | notes on the wrong instrument | per minute |
|---|---|---|
| fixed, middle C | 55 | 41.4 |
| fixed, F4 | 11 | 8.3 |
| adaptive | 9 | 6.8 |

**Tracks 3 and 5 differ on 6 notes out of 343.** Whether that is audible is an ear question
and the answer is Jake's, but 6 notes in 80 seconds is the size of the thing being argued
about, and it should be stated before anyone listens for it.

---

## 6. Files

```
fp30x_studio/hands.py                          the module
docs/HANDS-findings.md                         this file
docs/figures/hands-fixed-middle-c.png          pitch/time, fixed at 60, errors marked
docs/figures/hands-adaptive.png                pitch/time, adaptive, errors marked
docs/figures/hands-stiffness.png               accuracy against boundary stiffness
~/.claude/served/hands/index.html              the page, with all five renders
~/.claude/served/hands/audio/*.mp3             the renders
```

Reproduce:

```bash
cd ~/workspace/audio/fp30x-studio
~/workspace/audio/.venv/bin/python -m fp30x_studio.hands eval \
    ~/workspace/audio/corpus/raw/joplin/kern/*.krn
~/workspace/audio/.venv/bin/python -m fp30x_studio.hands assign \
    "$HOME/Music/FP-30X Studio/takes/clips/2026-09-19-something-doing-SEGMENT.fp30" \
    --strategy all --from 150 --to 230
```

The score-to-performance label transfer is not in the package. It is about 40 lines of
subsequence DTW and it belongs in `fp30x_studio.align` next to the rest of the alignment
machinery rather than in `hands.py`; it was left in scratch deliberately rather than dropped
into the wrong module.

---

## 7. What this would need to become useful

* **Ground truth that is not ragtime.** Every number here is Joplin. The contiguity ceiling
  of 100% is a fact about ragtime, and the E4 constant is almost certainly a fact about
  ragtime too. Chopin is the test that would say whether the adaptive boundary earns its
  keep, and the corpus for it is already on disk (`raw/nifc-chopin`, tier 1 licence).
* **A causal version.** `viterbi` reads the whole take before it answers, so nothing live can
  use it. A fixed-lag smoother over the same cost function would be a small change and would
  make the thing usable in `watch`.
* **Sustain pedal.** Nothing here reads controller 64. Pedalled passages let both hands leave
  the keys, which changes what "reach" means, and the reach term is currently measuring the
  wrong thing during them.
