# Validating the GEMM score-alignment pipeline on takes it was not built on

Run 2026-08-24. Read-only against `fp30x_studio/` (nothing in the package was
modified); all harness code lived in a scratch directory and is reproduced in
outline below. Venv: `fp30x-studio/.venv`.

**One-line result.** The alignment works, and the statistic
`(c − median) · √count` does fix the corner artefact — but the *self-normalised
z of that statistic is not a transferable confidence measure*. It looked
excellent against a 24-score decoy pool (precision 0.98 at z ≥ 6) and collapsed
to precision **0.54** at the same threshold against the real 511-score corpus.
The quantity that survives the corpus-size change is the **margin between the
best score and the best score of a different work**. At margin ≥ 2.0 the
pipeline was right on 30/30 windows; it fires on about a quarter of them.

---

## 1. What was tested

### Takes and how each was identified

`fp30x_studio.identify` was run first on every candidate take (`identify check`)
and its verdict treated as a hypothesis, per JPA-149. It was right about as
often as it was wrong; the alignment then confirmed or refuted it.

| Take | Segment(s) | Identifier said | Verdict after alignment |
|---|---|---|---|
| `2026-08-24-polonaise-fantaisie` | 32 segs | Op. 61 (+ two Debussy songs) | **Op. 61 confirmed** — control, the piece the pipeline was built on |
| `2026-08-19-nocturnes` | 0, 1 | Op. 55 No. 1, conf 0.86/0.95 | **Op. 55 No. 1 confirmed**, bars 0→101 |
| `2026-08-19-a` | 3 | Op. 55 No. 1, conf 0.92 | **Op. 55 No. 1 confirmed**, bars 48→100 |
| `2026-08-19-a` | 5 | "Anonymous, DREIMAL UMS SCHEITERBEIG" | **Op. 9 No. 2** (z = 21.1, margin 14.7). Identifier wrong. |
| `2026-08-22-mozart-c-i` | 0 | *no verdict* | **K. 545 first movement**, picked correctly out of K. 279 / 309 / 330 / 545 and 507 others. Identifier silent. |
| `2026-08-20-b` | 7, 8 | Joplin Sugar Cane / Strenuous Life | **seg 7 = Maple Leaf Rag, seg 8 = Sugar Cane** |
| `2026-08-21-c` | 0 | Joplin Sugar Cane | **Sugar Cane confirmed** (z = 13.7) — not Chopin at all |
| `2026-08-22-home` | 6 (3616 notes) | *not run* | **Op. 61**, bars 48 → 241 monotone. Found blind. |
| `2026-08-18-a` | 3 | Brahms Op. 51 | **Op. 47 Ballade 3**, bars 176→205 (z = 12.3). Identifier wrong. |
| `2026-08-21-nocturne` | 0 (441 s) | Op. 62 No. 1, conf 0.86, 6 hits | **Unconfirmed — see §5.1** |
| `2026-08-21-nocturne-2` | 3 | Rachmaninoff Op. 23/4 | **Unconfirmed** |
| `2026-08-21-c` | 1, 6 | Scriabin / Op. 62 No. 1 | **Unconfirmed** |

### Decoy pools

Two, deliberately:

* **Pool A, 24 scores** — the true piece plus 3–5 Chopin works of comparable
  length, plus both first editions of Op. 61, four C-major Mozart first
  movements and three Joplin rags.
* **Pool B, 515 scores** — all 508 usable Humdrum Chopin first editions
  (4 skipped as under 80 events) plus the same Mozart and Joplin files. This is
  the honest decoy set: it is what the pipeline would face in use.

`diagonal_sums` was replaced in the harness by a mathematically identical
per-pitch-class `np.correlate` (verified: identical `taus`, identical `cnt`,
max |Δc| = 5.1 × 10⁻⁸), which made Pool B affordable — 271 windows × 515 scores
in 39 s.

### Statistic

As prescribed: `c` from `corr.diagonal_sums`, `S = (c − median) · √count`,
`count ≥ max(40, 0.5 · n_window)`, `z = (S_max − median S)/(1.4826 · MAD S)`
over the valid offsets. Windows of 150 events, hop 75, unless stated.

---

## 2. The corner artefact is genuinely fixed

The defect described in the brief — short corner diagonals winning by variance
inflation — does **not** survive the `√count` correction.

```
median (winner's diagonal length ÷ count floor), all windows       2.00
  ... when the winning score is CORRECT                            2.00
  ... when the winning score is WRONG                              2.00
fraction of WRONG winners sitting within 10% of the count floor    0.04
fraction of CORRECT winners sitting within 10% of the floor        0.02
```

2.00 is the maximum attainable ratio (n = 150, floor = 75, full overlap = 150),
so the statistic now *prefers* full-overlap diagonals. Confirmation: restricting
to `count ≥ 0.9·min(n,m)` outright changed top-1 accuracy from 92/138 to 93/138.
The count floor is close to inert once `√count` is in the statistic. **The fix
works; the failures below are a different problem.**

## 3. True vs decoy: the numbers

75 windows over 10 segments with independently established ground truth, each
scored against the true work and against all 511 other works:

|  | true piece | best decoy |
|---|---|---|
| median z | 5.50 | 5.72 |
| 10th pct | 3.37 | 5.10 |
| 90th pct | 10.89 | 6.80 |
| **max** | **21.08** | **7.73** |

The medians are *inverted*: on a typical window the best wrong score outscores
the right one. What separates them is the tail. The best decoy never once
exceeded z = 7.73 over 75 × 511 comparisons; the true piece exceeded it on the
windows where the diagonal really locked.

Threshold sweep, 138 labelled windows, Pool B:

```
 z≥   margin≥ | fired  right  wrong | precision  recall
 4.0    0.0   |  138     55     83  |   0.399     0.399
 6.0    0.0   |   79     43     36  |   0.544     0.312
 7.0    0.0   |   39     34      5  |   0.872     0.246
 8.0    0.0   |   28     28      0  |   1.000     0.203
 4.0    1.0   |   43     35      8  |   0.814     0.254
 4.0    1.5   |   36     33      3  |   0.917     0.239
 4.0    2.0   |   30     30      0  |   1.000     0.217
 4.0    3.0   |   23     23      0  |   1.000     0.167
```

Margin alone, no z gate at all, is the cleanest single criterion:

```
margin ≥ 1.0  →  precision 0.814   margin ≥ 2.0  →  precision 1.000 (30/30)
margin ≥ 1.5  →  precision 0.917   margin ≥ 3.0  →  precision 1.000 (23/23)
```

**Recommended gate: `margin ≥ 2.0`, where margin = z(best) − z(best score of a
different work).** Add `z ≥ 8` as a redundant floor if you want an absolute
number; empirically it selects almost the same windows. Expect to emit a bar
range for roughly **25% of 150-event windows** and to be silent on the rest.
That is the honest operating point.

### Why the z threshold does not transfer

Against Pool A (24 scores), z ≥ 6 gave precision 0.979. Against Pool B (515),
the identical threshold gave 0.544. `z` is a maximum over offsets *and*, in use,
over scores; its null distribution grows with both the number of scores searched
and the number of valid offsets per score (long scores are favoured). Any
absolute z threshold is a statement about the corpus it was tuned on. The margin
is not immune — more decoys push the runner-up up — but it degrades by losing
recall rather than by emitting confident wrong answers.

### Does the diagonal beat a bag of pitch classes?

Yes, but by less than one would like. Ranking the 24-score pool by plain
pitch-class-histogram cosine (no order information at all) got 63/138 windows
right; the diagonal statistic got 92/138. Real information, not a rout.

---

## 4. Per-take behaviour at margin ≥ 2.0

Bar ranges are the score bars covered by the winning diagonal.

| Take / seg | windows | fired | bar sequence | consistent? |
|---|---|---|---|---|
| `mozart-c-i` s0 | 13 | 9 | (4,15) (10,20) (14,24) (19,31) (23,35) (29,39) (35,46) (58,67) (62,73) | **yes — strictly monotone across 73 bars** |
| `19-nocturnes` s1 | 3 | 3 | (90,101) (80,92) (84,97) | yes — a short passage worked three times |
| `19-nocturnes` s0 | 14 | 4 | (48,68) (59,76) (86,101) (90,101) | yes, monotone, with two gaps |
| `19-a` s3 | 11 | 1 | (59,75) | too sparse to judge |
| `19-a` s5 | 9 | 1 | (0,34) | one huge hit (z = 21) then nothing |
| `22-home` s6 | 47 | 3 at m≥2, 10 at m≥1 | 48→84→119→126→130→137→161→209→231 | **yes — a clean walk through Op. 61** |
| `20-b` s7 (Maple Leaf) | 10 | 4 | (0,22) (4,25) (15,37) **(0,13)** | monotone then jumps back — see §5.2 |
| `20-b` s8 (Sugar Cane) | 14 | 2 | (0,15) (0,15) | both windows 375 events apart map to the same bars — §5.2 |
| `21-c` s0 | 2 | 1 | (0,18) | fine |
| `24-polonaise` s11 | 14 | 1 | (128,141) | **1 of 14** on the control piece |
| `24-polonaise` s12 | 9 | 1 | (159,183) | 1 of 9 |
| `24-polonaise` s15,16,19,20,27,31 | 39 | **0** | — | **silent on the piece it was built for** |
| `21-nocturne` s0 | 16 | 0 | — | silent |
| `21-c` s1, s6 | 10 | 0 | — | silent |
| `21-nocturne-2` s3 | 13 | 0 | — | silent |
| `22-piece2` s3, s5 | 24 | 0 | — | silent |

---

## 5. Where it fails, and why

### 5.1 It cannot align a whole performance as one segment

The single most important failure. A rigid diagonal assumes the *i*-th
performed chord is the *i*-th notated chord. `2026-08-21-nocturne` seg 0 is 1277
onset events; Op. 62 No. 1 is 808 score events — 58% more events than the text
has. Rolled chords, ornaments, repeats taken differently and pedal-blurred
onset clustering all shear the diagonal, and no single offset can absorb it.
Aligned whole-segment, the true piece placed **6th of 24** on that take.

Consequence: the segment-level API (`load_segments` → `chroma` → one GEMM) is
the wrong granularity. Windows of 120–150 events are where the assumption is
locally true. Everything in §3 and §4 above is windowed for that reason; the
whole-segment numbers are considerably worse.

### 5.2 Repetition puts a confident answer at the wrong bar

Exactly as predicted, and it is not confined to mazurkas.

* Maple Leaf Rag, seg 7: bars (0,22) → (4,25) → (15,37) → **(0,13)**. Window 300
  is 300 events into the take and lands on bar 0. The A strain is played twice;
  the diagonal has no way to prefer the second traversal.
* Sugar Cane, seg 8: windows 75 and 450 both fire at margin ≈ 6 on bars (0,15).
* A ternary nocturne repeats its A section literally, so the same applies to
  Op. 55 No. 1 and Op. 62 No. 1.

The statistic reports *offset*, and a repeated strain genuinely has two correct
offsets. Nothing local can resolve this. Only a monotonicity constraint across
windows within a segment — which the current pipeline does not impose — can.

### 5.3 Magnet passages: the same wrong bars, on unrelated takes

Certain score regions win spuriously over and over, always at the same bars,
across takes with nothing in common:

```
019-1-PE            22 spurious wins across 8 takes, median bars (154,168)
019-1a-PR           16 spurious wins across 6 takes, median bars (153,166)
016-1a-BH           11 spurious wins across 4 takes, median bars (  6, 23)
008-1b-W-001-piano   7 spurious wins across 5 takes, median bars ( 92,119)
013-1a-Sm            7 spurious wins across 3 takes, median bars (216,225)
011-1ax-KI-003       6 spurious wins across 5 takes, median bars (361,374)
```

Op. 19 bars 152–168 beat the true score on takes of Op. 61, Maple Leaf Rag and
two unidentified sessions. These are chromatic or scalar passages whose local
chroma is near-uniform, so their cosine against *anything* is elevated. They are
a fixed, enumerable list — a stop-list of magnet regions, or a per-score
self-similarity penalty, would remove most of the residual false positives.
Note that the `identify` package already carries the analogous idea
(`ThemeIndex.dropped`, 42 stop-hashes); the aligner has no equivalent.

### 5.4 Short windows break precision

Window-length sweep, gate margin ≥ 2.0, 7 known-true segments:

```
   W    windows  fired  right  wrong  precision
   40      440     81     69     12     0.852
   60      292     66     60      6     0.909
   80      218     49     46      3     0.939
  120      142     44     44      0     1.000
  150      112     29     29      0     1.000
  300       53     16     16      0     1.000
```

**120 events is the floor.** Below it the gate stops holding — 12 confident
wrong answers at W = 40. In practice a segment under ~120 onset events (roughly
20–30 s of nocturne texture) should get no bar range at any confidence.

### 5.5 Transposed practice: not tested positively, but cheap to handle

Rotating the performance chroma by *k* semitones costs one `np.roll`. Sweeping
all 12 rotations of `2026-08-21-nocturne` seg 0 against all 508 Chopin scores
(6096 comparisons per window) produced no window at margin ≥ 2.0 at any
transposition, so no transposed practice was found in that take. But note the
cost: sweeping 12 rotations multiplies the decoy count by 12 and therefore
raises the null. If transposition sweeping is added, the margin threshold must
be re-derived on the enlarged pool, not inherited.

### 5.6 Single-hand practice degrades less than expected — with a caveat

Simulating right-hand-only by keeping the highest note of each event, and
left-hand-only by keeping the lowest (W = 150, margin ≥ 2.0, 7 segments):

```
both hands   29/112 fired, 29 right, 0 wrong
skyline only 27/112 fired, 27 right, 0 wrong
bass only    27/112 fired, 26 right, 1 wrong
```

**This is a weak test and should not be read as a pass.** The simulation keeps
the two-hand event grid: same number of events, same times, one note each. Real
hands-separate practice produces a *different event sequence* — the left hand
alone has its own onsets — which is the shearing problem of §5.1 again. The
result establishes only that reducing each chord to one voice does not by itself
destroy the match.

### 5.7 Silent on the piece it was built for

At margin ≥ 2.0, six of the eight examined segments of
`2026-08-24-polonaise-fantaisie` produce **no** bar range at all, and the other
two produce one window each out of 23. The same music in `2026-08-22-home`
seg 6 (one continuous 3616-note run) walked cleanly from bar 48 to bar 241.
The difference is take structure, not repertoire: the polonaise take is 32
fragmentary practice segments, and fragments are short, restarted, and full of
the repetitions of §5.2. **The pipeline aligns performances; it does not yet
align practice**, which is most of what is on this disk.

### 5.8 Takes that are not the piece assumed

Three of the twelve segments examined were not what the file name or the
identifier said:

* `2026-08-21-c` seg 0 is named as a Chopin session and is **Joplin's Sugar Cane**
  (z = 13.7, margin 7.5).
* `2026-08-19-a` seg 5 was identified as an anonymous folk tune and is
  **Op. 9 No. 2** (z = 21.1).
* `2026-08-18-a` seg 3 was identified as a Brahms string quartet and is
  **Op. 47, Ballade 3** (z = 12.3, bars 176–205).

In each case the alignment against a large corpus was right and the n-gram
identifier was wrong. That is a point in the aligner's favour and a data point
for JPA-149.

**`2026-08-21-nocturne` seg 0 remains unidentified.** The identifier claimed
Op. 62 No. 1 on 6 aligned hits — its stated minimum. Alignment finds Op. 62
No. 1 at best z = 4.83 over every window and every one of 12 transpositions,
which is below the median *decoy*. Across 16 windows × 508 Chopin scores nothing
fires at margin ≥ 2.0. Either the piece is not in the first-editions corpus, or
the take is unalignable for the reasons in §5.1. It should not be recorded as
Op. 62 No. 1 on present evidence.

---

## 6. Recommendations

1. **Gate on margin, not on z.** `margin ≥ 2.0` against the best score of a
   *different work* (editions of the same work must not count as decoys — the
   two Op. 61 editions score within 0.2 z of each other and would cancel).
2. **Re-derive any threshold on the corpus you will actually search.** A z
   threshold tuned on 24 scores was 0.98 precise and became 0.54 precise on 515.
3. **Window at 120–150 events, never align a whole segment.** Refuse to emit a
   bar range for anything shorter than 120 onset events.
4. **Impose monotonicity across windows within a segment.** Repetition is the
   largest remaining source of confident-but-misplaced bars, and it is the only
   failure mode here that a cheap post-hoc constraint would fix outright.
5. **Build a magnet stop-list.** Six score regions account for a large share of
   the residual false positives, and they are the same six regardless of what is
   being played.
6. **Keep the `√count` statistic.** It does what it was meant to do. The
   remaining problem is the *confidence measure* built on top of it, not the
   statistic.
7. Consider a per-window `identify` cross-check: the two methods fail
   differently (n-gram melodic vs chroma-diagonal), and every take where they
   agreed here was correct.

---

## Reproducing

Harness scripts were written to a session scratch directory and are not
committed. They are short and the method is fully specified above: cache
`load_score(...)` → `corr.chroma(...)` per `.krn` file to a pickle; slide a
150-event window over `corr.chroma(segment.events)`; for each cached score
compute the diagonal means by 12 per-pitch-class `np.correlate(R[:,k], Q[:,k],
'full')` (identical to `corr.diagonal_sums`, verified to 5e-8); apply
`S = (c − median)·√count` over `count ≥ max(40, 0.5n)`; rank scores by the
MAD-normalised z of `S`; gate on the margin to the best different work.

Ground truth was established, not assumed: a take counts as known only where the
alignment margin exceeded 2.0 somewhere in the segment *and* the resulting bar
ranges advanced monotonically across independent windows, which no false
positive observed here did.

---

# Addendum, 2026-08-26 — the features were the problem

Run against the **same 515-score pool** (508 Chopin first editions, 4 Mozart,
3 Joplin), same statistic
`S = (c − median)·√count`, same `margin ≥ 2.0` gate, W = 150 / hop 75. Windows
were rebuilt from the takes (segment indices differ from §4 under the default
`load_segments`), so the baseline was **re-measured on the identical windows**:
122 windows over the nine segments of §1 with independent ground truth. The
replication is close enough to trust the comparison — chroma gives true-piece
median z 5.07 vs best-decoy 5.74, and best-decoy max **7.73**, the same number
as §3.

New code: `fp30x_studio/align/features.py`; `corr`, `correspond` and `warp`
untouched.

## Recall at precision 1.000, full 515-score pool

| feature | dims | recall @ P=1.0 | best-decoy median z |
|---|---|---|---|
| chroma (baseline) | 12 | **0.180** | 5.74 |
| interval one-hot, outer voices | 54 | *never reaches P=1.0* (max P 0.74) | 6.57 |
| pitch class × 3 register bands | 36 | 0.295 | 6.05 |
| **88 keys** | 88 | 0.377 | 6.70 |
| 88 keys, event-frequency IDF | 88 | 0.385 | 6.78 |
| **88 keys + contour × 0.5** | 142 | **0.598** | 6.16 |

`features.register_contour` — 88 key indicators concatenated with the
outer-voice interval one-hot at weight 0.5 — is the winner: **precision 1.000
at recall 0.598 against 0.180**, 73/73 windows correct. The median inversion of
§3 is undone: true 9.34, best decoy 6.16, true beats every decoy on 75% of
windows (chroma: 39%). Contour weight 0.25 and 1.0 both give ≈0.50, so this is
a broad optimum.

## What did not work

* **Interval features alone are the worst thing tested** — worse than chroma.
  Transposition invariance is not the invariance this problem has.
* **IDF weighting bought nothing** (0.377 → 0.385, noise at n = 122). Magnets
  are common *textures*, not common *keys*; a per-dimension weight cannot see a
  texture.
* **Coarse register bands recover only half the gain** — granularity is the
  point, not the presence of register.
* **Velocity/duration weighting is untestable cross-domain**: `load_score`
  gives every notated note velocity 64, so it would weight one side only.
* **The 120-event floor survives.** At W = 80 no margin ≤ 3.0 reaches
  precision 1.0. §5.4 stands unchanged.

## Magnets

Largely gone. Wrong top-1 winners fall 74/122 → 30/122; Op. 19 (12 spurious
wins on 3 takes under chroma) disappears entirely, and no work wins more than 4
spurious top-1s. **None survives `margin ≥ 2.0`.** A weak replacement (Op. 65,
4 wins across 3 takes) exists below the gate.

## `2026-08-21-nocturne` seg 0 — resolved

Jake was right. Under `register_contour` it fires on **Op. 62 No. 1** at
margin 2.4 on two independent windows (z = 8.7 at event 75, z = 10.3 at 150,
across both editions). Chroma reached z 5.06 and never cleared the gate.
Separately, `2026-08-18-a` returns Op. 47 on 22 of 31 windows, up to z = 20.5.
