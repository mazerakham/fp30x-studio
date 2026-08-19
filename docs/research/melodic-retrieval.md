# Live piece identification from a symbolic MIDI stream

**Research brief for implementation.** Written 2026-08-19. Target: `fp30x-studio`, live identification
of the classical work being played on a Roland FP-30X, from the MIDI note stream alone.

---

## 0. Verdict, up front

**Build a tempo-invariant symbolic fingerprint with offset-histogram voting: Arzt, Böck & Widmer's
scheme (ISMIR 2012), with `n1 = n2 = 2` instead of their `5`, and a query-side ornament-collapse
prefilter.**

The token is a triple of note events `(e1, e2, e3)` selected by successor rank, hashed on
**absolute MIDI pitch** `(p1, p2, p3)`, carrying the **inter-onset ratio** `tdr = (t3-t2)/(t2-t1)`
and `td12` as payload. Matching is: hash lookup → tempo-ratio filter → vote into a
`(piece, score-offset)` histogram → take the piece whose best bin has the most votes.
It is integer hashing, binary search and counting. No DP, no alignment, no model inference,
no melody extraction, no LLM.

**Notes needed to identify:** on a measured 968-work index,

| condition | notes for ~90% top-1 | notes for ~97% top-1 |
|---|---|---|
| score-clean query (what you get if you play accurately) | **11** | **15** |
| realistic live playing (rubato, 4% wrong notes, trills) | **~25** | **~50** |
| deliberately sloppy (σ=0.35 drift, 8% wrong, a trill every 10 notes) | ~80 | not reached at 120 |

With the confidence gate of §9 applied, precision rises to ~98% while still firing on ~74% of ticks.

At piano tempi (5–8 notes/s across both hands) **15 notes is about 2.5 seconds and 30 notes about
5 seconds**. Extrapolating to a 10,000-work index costs roughly 5–8 more notes — the requirement
grows logarithmically in corpus size, which is measured, not assumed (§5).

**Single biggest risk:** you do not have a symbolic reference corpus of the music you actually
play. The algorithm is solved; the index is not. §11.

---

## 1. The thesis, tested

> *Shazam cannot identify classical music because it fingerprints a specific audio recording, so every
> new performance is a new signal. Symbolic identification from MIDI matches the work, not the
> recording. Interval sequences are transposition-invariant; inter-onset ratios are tempo-invariant.
> That should make live classical ID tractable and cheap.*

Three of the four clauses survive. One is a false economy that will cost you an order of magnitude
of discriminating power if you act on it.

### 1.1 Right: the diagnosis of Shazam

Wang's constellation fingerprint hashes pairs of spectral peaks `(f1, f2, Δt)` — the `Δt` is in
*absolute seconds of the master recording*. A different tempo shifts every `Δt`; a different piano,
hall and microphone shifts every `f`. Nothing survives. This is by design: audio identification "by
definition only finds exact replicas of the query in the database, possibly distorted in some ways
(e.g., compression artefacts, noise)" — Arzt et al. 2012, §1. That is not a fixable bug in Shazam;
it is the property that makes Shazam fast.

### 1.2 Right, and this is the real content of the thesis: MIDI capture *relocates* the problem

The interesting move is not "symbolic is better." It is that a digital piano hands you, for free,
the output of the one step that makes the audio version of this problem hard. Arzt et al. had to
run a bidirectional-RNN piano transcriber to get symbolic notes from audio, and even at the state
of the art they were working with **precision 0.869 / recall 0.725 / F 0.790** at a 100 ms
detection window (their Table 1). Their fingerprinter still hit 94% top-1 at 20 notes *through*
that 21% miss rate.

You get the note stream at F = 1.000. **That is the entire advantage, and it is a large one.** My
measurements below show it is worth roughly a factor of two in notes-to-identification versus their
transcribed-audio numbers.

### 1.3 Wrong, and expensively so: transposition invariance

"Interval sequences are transposition-invariant" is true and irrelevant. You play a written work in
its written key on a keyboard. You are not humming. Transposition invariance is a property you need
for query-by-humming, where the singer's key is arbitrary; it is a property you should *refuse* here,
because it is bought with discriminating power you cannot spare.

Measured on the same 968-work index, same token geometry, same voting, only the hash key changed:

| hash key | distinct keys | key entropy | top-1 @ L=15 | top-1 @ L=30 | median query time @ L=30 |
|---|---|---|---|---|---|
| **absolute pitch triple** `(p1,p2,p3)` | **107,820** | **15.37 bits** | **0.985** | **1.000** | **3.0 ms** |
| melodic interval pair `(p2-p1, p3-p2)` | 2,398 | 10.26 bits | 0.725 | 0.885 | 82.0 ms |
| pitch-class triple `(p1,p2,p3) mod 12` | 1,728 | 10.38 bits | 0.455 | 0.775 | 68.2 ms |

Transposition invariance costs **5.1 bits per token**, drops accuracy at 15 notes from 98.5% to
72.5%, and makes each query **27× slower** because every posting list is 45× longer. Octave
invariance is worse still.

The literature says the same thing from the other direction. Barlow & Morgenstern's 1948 index
*is* transposed (everything to C major / C minor, octave discarded) and they had to carry entries to
six letters, extending to eleven on collision, over ~9,825 themes. McNab et al.'s MELDEX measured it
directly on 9,400 melodies: exact interval + rhythm needs ~4 notes, exact interval alone ~7, exact
contour alone ~10, and approximate contour more than 20. Every invariance you add costs notes.

**Take transposition invariance only if you later want to identify hummed queries. For keyboard
queries it is a strict loss.** If you do want it eventually, add it as a *second, separate* index
(interval keys, `n1=n2=3`, queried only when the absolute index abstains), not as a replacement.

### 1.4 Oversimplified: "inter-onset ratios are tempo-invariant"

They are invariant to *linear* tempo scaling. Rubato is not linear.

What saves the scheme is that the ratio is taken over **two adjacent** inter-onset intervals. Tempo
drift is smooth on the timescale of two notes, so it cancels to first order — this is a numerical
derivative, not a global normalisation. Arzt et al. name the failure honestly: "A flaw of the current
approach is that it cannot cope with non-linear tempo deviations (i.e., with tempo variations within
a query)," and they mitigate it three ways, all of which you should copy: short queries, coarse 1-second
offset bins, and a per-token tempo estimate `r = std12/qtd12` applied before binning
(`bin = round(stime − qtime·r)`), which linearises the diagonal locally.

Measured cost of rubato with those mitigations in place, at 968 works:

| timing corruption | L=15 | L=20 | L=30 | L=50 |
|---|---|---|---|---|
| none | 0.976 | 0.992 | 0.992 | 1.000 |
| smooth tempo drift σ=0.15 (musical rubato) | 0.956 | 0.984 | 0.992 | 0.992 |
| smooth tempo drift σ=0.30 (heavy rubato) | 0.832 | 0.932 | 0.972 | 0.960 |
| smooth tempo drift σ=0.50 (extreme) | 0.656 | 0.772 | 0.848 | 0.856 |
| uncorrelated onset jitter σ=0.10 per IOI | 0.980 | 0.984 | 0.996 | 0.996 |

Note the last row. **Independent per-note timing jitter is nearly free; correlated tempo drift is
what hurts.** That is the opposite of what most people expect, and it follows directly from the
ratio-of-adjacent-IOIs construction: jitter perturbs one ratio, drift perturbs the diagonal.

### 1.5 Right, with a caveat: "tractable and cheap"

CPU is cheap. Memory is the real cost, and it is the thing that will bite at scale.

Measured, 968 works / 1,977,705 notes, pure numpy, single core, on this laptop:

| `n1=n2` | tokens | index RAM | build | median query, L=30 |
|---|---|---|---|---|
| 2 | 6,442,318 | ~128 MB | 8.7 s | 0.6–5 ms |
| 3 | 14,648,593 | ~292 MB | 27 s | 19–30 ms |
| 5 (Arzt's setting) | ~40 M *(extrapolated)* | ~800 MB | — | — |

`n1 = n2 = 2` is not merely adequate, it is **better** — same accuracy, 2.3× less memory, 5× faster
queries. Arzt et al. used 5×5 because their query notes were 21% missing; redundant tokens bought
them coverage. Your notes are not missing. Spend the token budget elsewhere or not at all.

Scaling to a 10,000-work index: ~20 M notes → ~66 M tokens → **~1.3 GB** at `n=2`. That fits on a
laptop but you must store it as sorted numpy arrays and binary-search it, never as a Python dict.

---

## 2. Prior art, with the numbers

### 2.1 The paper you are re-implementing

**Arzt, Böck & Widmer, "Fast Identification of Piece and Score Position via Symbolic Fingerprinting",
ISMIR 2012, pp. 433–438.** https://archives.ismir.net/ismir2012/paper/000433.pdf

Database: 183 pieces, 435,670 notes (154 Chopin works from the Magaloff corpus, 13 Mozart sonata
movements, 16 additional pieces incl. Beethoven 5, Liszt Mephisto Waltz No. 1, Schoenberg Op. 23/3).
Queries: 50,000 random audio excerpts per query length, transcribed by RNN, then fingerprinted.

| query length (notes) | 5 | 10 | 20 | 30 | 40 | 50 | 60 |
|---|---|---|---|---|---|---|---|
| correct **piece** top-1 | 22.55% | 78.33% | 94.07% | 96.70% | 97.50% | 98.01% | 98.42% |
| correct piece in top 5 | 37.24% | 87.86% | 97.44% | 98.49% | 98.87% | 99.17% | 99.32% |
| correct **score position** top-1 | 14.41% | 60.47% | 80.35% | 84.63% | 84.86% | 83.91% | 83.70% |
| mean query duration | 0.60 s | 1.33 s | 2.78 s | 4.21 s | 5.63 s | 7.04 s | 8.48 s |
| mean query execution | 1.71 ms | 5.13 ms | 11.76 ms | 16.86 ms | 20.76 ms | 26.36 ms | 31.89 ms |

Tempo robustness (30-note queries, score MIDI re-rendered at double / half tempo): position top-1
84.63% / 83.30% / 85.15%. Essentially flat. The IOI-ratio construction works.

Their exact parameters: `d = 0.05 s` minimum event separation, `n1 = n2 = 5`, successor pitch
constrained to within 2 octaves, ratio tolerance ±¼ of the query's `tdr`, offset histogram bins of
1 second, score = raw token count in the best bin ("we did experiments with different methods of
computing the matching score but in the end simply taking the number of tokens in each bin produced
the best results").

Their stated limitation and their own fix: for queries longer than ~10 s, split into overlapping
sub-queries (e.g. 30 notes with 15 overlap) and track scores across them. Do this.

### 2.2 The follow-up, which is a warning about corpora, not about algorithms

**Arzt, Frostel, Gadermaier, Gasser, Grachten & Widmer / Arzt & Widmer, "Piece Identification in
Classical Piano Music Without Reference Scores", ISMIR 2017, pp. 354–360.**
https://archives.ismir.net/ismir2017/paper/000127.pdf

Same fingerprinter, 339 piano pieces, but the reference database is built from *crawled YouTube
audio, transcribed* — no scores at all. Recall@1 for a 10-second query:

| reference build strategy | 2 s | 5 s | 10 s |
|---|---|---|---|
| one crawled recording per piece | 0.28 | 0.38 | 0.46 |
| top five crawled per piece | 0.58 | 0.69 | 0.74 |
| five *auto-selected* per piece (§6 of the paper) | 0.72 | 0.85 | 0.89 |
| + ten random sub-queries pooled per performance | 0.92 | 0.95 | 0.95 |

Two lessons. First, **reference quality dominates**: the same algorithm went from 0.46 to 0.95 on
identical queries by fixing the index. Second, their §6 selection strategy — query each candidate
performance against the database, drop its self-match, normalise the remaining scores by the
self-match score, and keep the performances that agree with each other — is a good template for
sanity-checking any corpus you assemble yourself.

### 2.3 The other line: n-gram / IR retrieval

**Doraisamy & Rüger, "An Approach Towards A Polyphonic Music Retrieval System", ISMIR 2001**
(https://ismir2001.ismir.net/pdf/doraisamy.pdf), extended as "Robust Polyphonic Music Retrieval with
N-grams", *JIIS* 20(1):53–70, 2003 (https://link.springer.com/article/10.1023/A:1023553801115).

Corpus 3,096 classical MIDI performances. They tokenise by sliding a window over *onset times*,
taking **all monophonic subsequences within the window** — i.e. they do no melody extraction at all —
and encode each as `[interval1, ratio1, …, interval_{n-2}, ratio_{n-2}, interval_{n-1}]` where the
ratio is `(onset_{i+2}−onset_{i+1})/(onset_{i+1}−onset_i)`. Exactly your inter-onset ratio.
Mean reciprocal rank, known-item search:

| index | 10 onsets | 30 onsets | 50 onsets |
|---|---|---|---|
| P4 — pitch intervals only, 4-onset window | 0.60 | 0.77 | 0.81 |
| R4 — **rhythm ratios only** | 0.03 | 0.11 | 0.15 |
| PR3 — pitch + rhythm, 3-onset window | 0.46 | 0.74 | 0.81 |
| **PR4 — pitch + rhythm, 4-onset window** | **0.74** | **0.90** | **0.95** |

Their own conclusion on R4: "the 441 possible different index terms are insufficient to discriminate
music pieces." **Rhythm alone is worthless; rhythm as a filter on a pitch key is worth ~0.14 MRR.**
That is the empirical justification for keeping `tdr` as payload rather than as the key.

Their error simulation is the most useful part. Gaussian interval noise with mean deviation
`Di = 3` semitones dropped PR4 from 0.90 to 0.24; `Di = 2` dropped it to 0.50. Coarsening the
interval quantisation (PR4CA, ~2 semitones per code) recovered it to 0.65 — i.e. **under heavy pitch
noise, coarser keys win; under clean input, finer keys win.** You have clean input. Use the finest
key available, which is absolute MIDI pitch.

### 2.4 The disambiguation question, answered three times

**Barlow & Morgenstern, *A Dictionary of Musical Themes*, Crown, 1948.** ~9,825 themes
(https://archive.org/details/dictionaryofmusi00barl). Their notation index transposes every theme to
C major or C minor, discards octave and discards rhythm entirely, and indexes the resulting letter
string. From "HOW TO USE THE NOTATION INDEX": *"Each definition has been carried to **six places**
except in the case of duplication. Duplicates are continued to a point of difference, but in no case
to more than **eleven places**."* The Preface: *"In our notation key we had to carry some themes to
seven or eight letters before their lines began to diverge."* Beethoven 5/i indexes as exactly six
letters: `G G G E♭ F F`. Their own named collision is the Mannheim figure
`G C E♭ G C E♭ C B C` — Beethoven Op. 2/1, Mozart's G minor symphony, Mendelssohn's E minor quartet.

**So: 6 notes of transposed, rhythm-free pitch-class was enough for ~10,000 themes on paper, with a
tail out to 11.** That is the historical floor, and it is a *theme-beginning* index — matching from
an arbitrary point mid-piece is strictly harder.

**McNab, Smith, Witten, Henderson & Cunningham, MELDEX / New Zealand Digital Library, D-Lib Magazine,
May 1997.** https://www.dlib.org/dlib/may97/meldex/05witten.html — 9,400 melodies, ~500,000 notes,
mean melody 56.8 notes. Their Figure 3 gives notes needed for approximately one collision, matching
from song beginnings:

| representation | notes |
|---|---|
| exact interval + rhythm | **~4** |
| exact contour + rhythm | ~6 |
| approximate interval + rhythm | ~6 |
| exact interval only | ~7 |
| exact contour only (Parsons) | ~10 |
| approximate contour + rhythm | ~12 |

Add 3–5 notes for *embedded* matching (query starts anywhere, not at the beginning) — which is your
case. Approximate contour then needs over 20. And the load-bearing result: **"The number of notes
required for retrieval seems to scale logarithmically with database size"** (their Figure 4).

**Denys Parsons, *The Directory of Tunes and Musical Themes*, 1975** — ~15,000 pieces indexed by
up/down/repeat contour. Uitdenbogerd & Yap ("Was Parsons right?", ISMIR 2003) measured that
**`*UU` — the opening contour "up, up" — accounts for 23% of all themes in it.** Contour space is
catastrophically skewed and this is the number that proves it.

### 2.5 What the named systems actually do, and what they get wrong

| system | representation | algorithm | corpus | measured |
|---|---|---|---|---|
| **Musipedia / Melodyhound** (Typke) | contour string; or notes as weighted points in (onset, pitch), weight = duration, pitch in Hewlett base-40 | edit distance on contour; **Earth Mover's Distance** / Proportional Transportation Distance for the point-set modes; vantage-object index over the **first 6 contour characters** | ~30,000 themes | MIREX 2006 ADR 0.715 (RISM 10k) / 0.819 (karaoke 1k) / 0.784 (mixed 15,741); 108 s per query on the mixed task |
| **Themefinder** (CCARH / Ohio State) | Humdrum `**kern`; keys are pitch, interval, scale degree, gross contour (`/ \ -`), refined contour (`U u D d s`) | PCRE regex over a precomputed one-line-per-theme index (`themax` / `tindex`) | ~10,000 classical themes, 7,000+ Essen folksongs, ~18,000 Renaissance incipits | **none published.** Frozen since 2001; live probes now return 2,616 classical entries, and the Renaissance repertory returns zero for every query |
| **RISM incipit search** | Plaine & Easie code; modes `intervals` (default), `exact-pitches`, `contour` | Solr `ShingleFilterFactory` with **fixed trigrams, `outputUnigrams=false`**, `BooleanSimilarity` (TF/IDF stripped), score = Jaccard over trigram sets | **1,624,794 sources / 2,540,340 incipits** (live API, 2026-08-19), CC-BY | none published; measured 0.79–1.45 s per query |
| **Peachnote** | interval n-grams up to n=15, transposition-invariant, **rhythm discarded**, polyphony linearised | Hadoop + HBase inverted index, 50 GB compressed | 264 M notes / 1 M+ sheets / 65,000+ scores (ISMIR 2011 §3.2) | **none.** API now returns a degenerate constant payload for every query |
| **music21 `search`** | one ASCII char per note (`chr(midi)`); `search.segment` uses **30-note segments with 12-note overlap** | naive windowed scan; `difflib.SequenceMatcher.ratio()` | n/a | none — it is a toolkit, not a system |
| **SIMSSA / ELVIS `vis-framework`** | n-grams interleaving vertical intervals with the lower voice's horizontal motion | pandas pipeline | 159 Renaissance files (test set) | none; and its documented limitation is fatal for you: *"MIDI files where all parts are given in the same channel cannot be analyzed usefully with our software"* |
| **C-BRAHMS** (Lemström) | (onset, pitch) point sets, transposition-invariant by construction | P1 exact O(mn); P2 partial O(mn log m); P3 longest-common-shared-time O(mn log mn) | — | **no index exists**; on 500k RISM documents queries take "hours instead of seconds" |

The pattern across all of it: **almost nobody publishes a number.** RISM, Peachnote, Themefinder,
SIMSSA and music21 have zero published retrieval accuracy and zero published latency between them.
The only measured comparisons in the field are MIREX 2005–2007 and Typke's own evaluations. Treat
"System X does melodic search" as an unverified claim by default.

Failure modes worth internalising:

- **Exact-contiguous matching is brittle.** RISM's trigram design returns 195 hits for `8CCGGAAG`
  and **0 hits** if you append one non-matching note. One wrong note kills the query. Vote-counting
  degrades instead; that is the whole argument for it.
- **Geometric point-set methods find the wrong things.** Typke's example: a Haydn query returned
  **113 matches all tied at maximum shared common time**, including a Palestrina offertorium that
  sounds nothing like it.
- **EMD cannot be indexed.** It violates the triangle inequality when total weights differ. That is
  why Typke had to invent PTD, and why MIREX 2005 saw his entry take **51,240 seconds for 11 queries
  over 558 incipits** (a genetic search for the optimal alignment).
- **Everyone throws rhythm away** — Barlow & Morgenstern, Parsons, Themefinder's UI, RISM ("note
  durations have no effect on query results"), Peachnote. MELDEX's numbers price that decision at
  ~3 extra notes of query.

---

## 3. Representations ranked

Ranked by discriminating power per token on *this* problem (keyboard, correct key, correct-ish
rhythm). Bits are measured entropy of the key distribution over the 968-work index at `n1=n2=2`.

| rank | representation | invariance bought | bits/token | index cost | verdict |
|---|---|---|---|---|---|
| 1 | **absolute pitch triple + IOI-ratio payload** | tempo (via ratio); nothing else | **15.4** | 4 tokens/note; 20 B each | **use this** |
| 2 | absolute pitch triple, no ratio filter | tempo | 15.4 key, but ~3× more surviving postings | same | fallback if timing is garbage |
| 3 | melodic interval pair + ratio | transposition | 10.3 | same tokens, 45× longer posting lists | only for hummed queries |
| 4 | pitch-class triple | transposition + octave | 10.4 | same tokens, ~60× longer lists | no |
| 5 | quantised interval n-gram, n=4–5 (Doraisamy PR4) | transposition, tempo | ~14 at n=4 with ratio bins | more tokens, needs longer n | good design, wrong invariance for you |
| 6 | pitch-class set histogram / chroma profile | transposition, order, rhythm | ~5–6 | tiny | useful only as a *coarse prefilter*, never as a key |
| 7 | IOI-ratio-only ("query by tapping") | pitch entirely | ~8.8 (441 codes in Doraisamy's R4) | tiny | **measured MRR 0.15 at 50 onsets.** Dead. |
| 8 | Parsons contour (U/D/R) | transposition, interval size, rhythm | ~1.5 | tiny | 23% of all themes start `*UU`. Dead. |

Two notes on this table.

**On combined pitch-rhythm n-grams (rank 5).** Doraisamy's PR4 is the right shape and it gets MRR
0.95 at 50 onsets over 3,096 pieces. It is a genuine alternative. It loses to the fingerprint
because (a) it commits to transposition invariance, costing 5 bits, and (b) its "all monophonic
subsequences in a window of 4 onsets" enumeration is combinatorially worse than "next 2 events ×
next 2 events" while buying the same polyphony robustness. But if you ever need a text-search-engine
backend rather than a numpy array, PR4 is the design to port.

**On why the ratio is payload, not key.** Doraisamy quantised the ratio into 21 bins and put it in
the key; Arzt kept it as a payload and filtered with a ±25% tolerance at query time. The payload
form is strictly better here: a bin boundary is a hard failure (a ratio of 1.49 and 1.51 land in
different bins and never meet), whereas a tolerance window is soft. It costs you a slightly longer
posting scan and buys you graceful degradation under rubato. Keep it as payload.

---

## 4. Matching algorithms and what they cost

The application constraint is: re-run every few seconds, on a laptop, while the user is playing,
without spinning the CPU. That eliminates most of the literature outright.

| family | representative | cost | verdict |
|---|---|---|---|
| **hash + offset-histogram voting** | Wang 2003 (audio); **Arzt et al. 2012 (symbolic)** | O(q · postings) per query; **measured 1.7 ms at 10 notes, 16.9 ms at 30, 31.9 ms at 60** on 2012 hardware; 0.6–5 ms in my numpy reimplementation at 968 works | **use this** |
| edit distance / DP alignment | Mongeau & Sankoff 1990 (adds *consolidation* and *fragmentation* ops for ornaments) | **O(mn) per candidate.** At 10,000 candidates × 2,000 notes × 30 query notes that is 6×10⁸ cell updates per tick | **too expensive.** Viable only as a rerank over a shortlist of ≤20 |
| local alignment (Smith-Waterman variants) | MIREX SMS entrants | O(mn) per candidate, same problem | rerank only |
| geometric point-set matching | C-BRAHMS P1/P2/P3 (Ukkonen, Lemström, Mäkinen) | P1 O(mn), P2 O(mn log m), P3 O(mn log mn); **no indexing scheme was ever published**, so every query scans every document | **too expensive**, and it retrieves badly (the 113-way tie) |
| transportation distance | Typke EMD / PTD | superlinear per pair; MIREX 2005: **51,240 s for 11 queries over 558 incipits** | **catastrophically expensive** |
| DTW | online DTW (Dixon 2005), any-time tracking (Arzt & Widmer) | O(mn) with a band; incremental | wrong tool: DTW is for *following* a known piece, not for choosing among 10,000 |
| LSH / MinHash | — | O(1)-ish | no published symbolic retrieval system uses it with measured recall; RISM's Solr schema has a MinHash field with the `copyField` commented out. Unproven here, and unnecessary — exact hashing on a 15-bit key already works |
| n-gram IR + TF-IDF ranking | Downie; Doraisamy & Rüger; Uitdenbogerd & Zobel | O(q · postings), same order as fingerprinting | viable; see §3 rank 5 |

**The reason hashing beats everything is structural, not incidental.** Every DP and geometric method
computes a *pairwise* score, so cost is (corpus size) × (query size). Hash-and-vote inverts the
loop: cost is (query size) × (average posting list length), and the posting list length is set by
your key entropy, not by your corpus size. Doubling the corpus doubles posting lists in the worst
case but in practice grows them sublinearly, because real music's key distribution is already
saturated. My measured query time went from ~0.9 ms at 50 works to ~3 ms at 968 works — a factor of
3 for a factor of 19 in corpus.

### The offset histogram is the whole trick, and it is worth saying why

You do not need any single token to be unique. You need the *true* piece's matching tokens to agree
on **one score offset** while the false pieces' accidental matches scatter across offsets. With
1-second bins over a 10-minute piece there are 600 bins, so a chance match contributes 1/600 as much
signal as a real one. This is why the method tolerates 21% missing notes and 20% wrong notes: those
notes simply do not vote, and the survivors still pile into the same bin.

It also handles, for free, three things that would otherwise need special code:
- **Repeats and da capo**: the same query legitimately matches two offsets in one piece. You get two
  peaks in that piece's histogram. Take the max for piece ID; report both for position.
- **Inner voices and both hands in one stream**: no voice assignment is ever made, so there is
  nothing to get wrong.
- **Arpeggiated chords**: a roll spreads onsets, changing `tdr` for tokens inside the roll. Those
  tokens fail the ±25% filter and abstain. Tokens spanning the roll survive.

---

## 5. How many notes — the number, measured

I built the index and ran the experiment rather than quoting one. Method in §12.

**Corpus:** 1,361 Humdrum `**kern` scores from `~/workspace/audio/corpus/raw` — Bach 370 chorales
(369) and Musical Offering (6), Beethoven 103 piano sonata movements + 71 string quartet movements,
Mozart 69 sonata movements, Scarlatti 65 keyboard sonatas, Haydn 25 sonata movements, Chopin 512
first-edition scores + 52 mazurkas + 24 preludes, Hummel 18 preludes, Joplin 47 rags.
**1,977,705 note events.** Fingerprint-clustered into **968 distinct works** (multiple editions of the
same Chopin piece collapse; the clustering is itself done with the fingerprinter, §12).

**Score-clean queries** — random contiguous windows of L notes drawn from anywhere in any piece,
timing exactly as notated, `n1=n2=2`:

| L (notes) | 5 | 8 | 10 | 12 | 15 | 20 | 25 | 30 | 40 |
|---|---|---|---|---|---|---|---|---|---|
| top-1 (work) | 0.283 | 0.763 | 0.883 | **0.943** | **0.980** | 0.987 | 0.990 | 0.997 | **1.000** |
| top-5 | 0.493 | 0.857 | 0.960 | 0.987 | 0.997 | 0.997 | 1.000 | 1.000 | 1.000 |
| median query ms | 0.9 | 1.0 | 0.7 | 0.6 | 0.9 | 2.7 | 2.3 | 4.4 | 5.0 |
| median vote margin (top / runner-up) | 1.50 | 2.00 | 2.50 | 2.83 | 3.08 | 3.77 | 4.42 | 4.90 | 6.23 |

**Corpus-size scaling** — same experiment, index subsampled to N works:

| works in index | 50 | 100 | 250 | 500 | 968 |
|---|---|---|---|---|---|
| files / tokens | 69 / 0.69 M | 142 / 1.6 M | 360 / 3.7 M | 701 / 7.8 M | 1361 / 14.6 M |
| top-1 at L=10 | 0.972 | 0.988 | 0.948 | 0.944 | 0.920 |
| top-1 at L=15 | 1.000 | 0.996 | 0.996 | 0.992 | 0.984 |
| top-1 at L=20 | 1.000 | 1.000 | 1.000 | 0.992 | 0.992 |

A 19× increase in corpus costs about 5 points at L=10 and 1.6 points at L=15. This is the
logarithmic scaling MELDEX reported. Extrapolating: **a 10,000-work index should need about 5–8 more
notes than a 1,000-work index for the same accuracy** — call it 20–25 clean notes for ~98%.

**Realistic live playing.** Corruption model: smooth (auto-correlated, τ≈8 notes) tempo drift of
σ in log-IOI; independent onset jitter; wrong notes replaced by ±1–2 semitones; dropped notes;
and **trills modelled properly** — alternating principal/upper-neighbour at 55–95 ms filling a real
note's inter-onset gap, inserted at a given rate. The `COLLAPSED` rows apply the O(n) ornament-collapse
prefilter of §7 to the query only (the index is score-derived and has no realised trills).

| condition | L=10 | L=15 | L=20 | L=30 | L=50 | L=80 |
|---|---|---|---|---|---|---|
| clean | 0.880 | 0.972 | 0.988 | 0.996 | 0.996 | 1.000 |
| trills 1 per 25 notes, raw | 0.784 | 0.836 | 0.952 | 0.964 | 0.992 | 0.996 |
| trills 1 per 25 notes, collapsed | 0.848 | 0.908 | 0.988 | 0.988 | 1.000 | 1.000 |
| trills 1 per 10 notes, raw | 0.784 | 0.836 | 0.848 | 0.932 | 0.952 | 0.976 |
| trills 1 per 10 notes, collapsed | 0.848 | 0.908 | 0.936 | 0.984 | 1.000 | 0.996 |
| **LIVE** (drift σ.25, jitter σ.06, 4% wrong, 3% dropped, trills 1/25), raw | 0.540 | 0.764 | 0.792 | 0.880 | 0.944 | 0.940 |
| **LIVE, collapsed** | **0.604** | **0.804** | **0.824** | **0.916** | **0.976** | **0.968** |
| LIVE-hard (drift σ.35, jitter σ.10, 8% wrong, 5% dropped, trills 1/10), collapsed | 0.504 | 0.592 | 0.660 | 0.788 | 0.856 | 0.908 |

An independent, larger run of the LIVE-collapsed condition (4,168 queries, different seed, §9) gives
0.628 / 0.753 / 0.858 / 0.941 / 0.970 / 0.980 at the same L. The two runs agree to within sampling
noise (250 vs 700 trials per cell); take the larger one as authoritative.

Isolated corruptions, `n1=n2=2`, no collapse:

| corruption | L=10 | L=20 | L=30 | L=50 |
|---|---|---|---|---|
| 5% wrong notes | 0.840 | 0.984 | 0.992 | 1.000 |
| 10% wrong notes | 0.804 | 0.956 | 0.976 | 1.000 |
| 20% wrong notes | 0.592 | 0.888 | 0.972 | 1.000 |
| 10% notes dropped | 0.816 | 0.964 | 0.988 | 1.000 |
| 25% notes dropped | 0.612 | 0.864 | 0.956 | 0.996 |

### The answer to the question as asked

> *How many notes of melody do you need before an n-gram index over ~10,000 classical themes is
> uniquely determining?*

Three answers, because the question has three regimes:

1. **Uniquely determining, in the Barlow & Morgenstern sense** (the n-gram appears in exactly one
   work). Measured on the 968-work index: of 107,820 distinct absolute-pitch-triple keys, **13.6%
   resolve to exactly one work — but those account for only 0.49% of all tokens.** The median key
   has 21 postings, the mean 60, the 90th percentile 154, the worst 5,136. In other words
   **essentially no individual 3-note token is uniquely determining**, and 3 notes is nowhere near
   enough by this criterion. This is the criterion B&M had to satisfy on paper, which is why they
   needed 6 letters extending to 11 — and it is the *wrong* criterion for a voting system, which is
   the point. You never need a unique token; you need enough non-unique tokens to agree on an offset.
2. **What voting actually needs, clean input, 10,000 works:** **~20 notes for 98%, ~15 for 95%,
   ~12 for 90%.** (Measured 15/12 at 968 works; +5 for the corpus extrapolation.)
3. **What voting needs from a live human at the keyboard, 10,000 works:** **~30 notes for ~90%,
   ~55–60 for ~97%**, ungated. With the §9 confidence gate, ~98% precision at ~74% firing rate from
   about 15 notes onward.

The number to put in the spec is **30 notes**. It is where the LIVE curve crosses 0.94 at 968 works
(0.90 after the corpus extrapolation to 10k), it is Arzt's crossover at 96.7% on transcribed audio,
and it is about five seconds of playing.

---

## 6. Melody extraction: do not build it

This was flagged as "the specific hard part … likely the dominant error source and deserves the most
careful answer." The careful answer is that **you should delete this stage entirely**, and the
reason is that it would be the dominant error source — so don't have one.

### 6.1 The recommended algorithm does not need a melody

Arzt's fingerprint runs on the raw, undifferentiated polyphonic stream. Doraisamy's n-grams run on
*all* monophonic subsequences in a 4-onset window. Neither system separates voices, identifies a
melody, or knows which hand played what. The polyphony is not an obstacle to be removed; it is
*additional evidence*. My measurements above are all on raw polyphonic Humdrum scores with no
reduction of any kind.

### 6.2 If you did build skyline, here is what it would cost you

Skyline (Uitdenbogerd & Zobel's `all-mono`: at each onset cluster keep the highest-pitched note) is
the cheap heuristic. Measured recall against human-annotated melody:

| corpus | skyline precision / recall / F1 | source |
|---|---|---|
| Mozart piano sonatas, 38 movements, melody annotated by a professional pianist | 88.49 / **93.91** / 91.09 | Hsiao & Su, ISMIR 2021, Table 2 |
| "Americans Folks", 1,262 MIDIs | 73.33 / **74.40** / 73.74 | same |
| POP909 (909 pop piano covers, note-level melody labels) | 81.42 / **56.57** / 66.76 | MidiBERT-Piano, arXiv 2107.05223, Table 3 |

So roughly **94% of melody notes are the top note in Mozart, 74% in folk arrangements, 57% in
pop-piano texture.** Chopin is not in any of these tables, but the mechanism that breaks skyline is
documented and it is exactly Chopin's writing: Simonetta et al. (ISMIR 2019) name Liszt's
*Ihr Glocken von Marling* as a failure because "the melody is in the middle voices," and state
flatly that "the skyline method fails when the melody is not the highest voice; furthermore, this
method cannot identify when pauses occur in the solo part."

And the number that should settle it:

> **Skyline's frame-level voice accuracy on Bach chorales drops from 95.76% (clean, quantised) to
> 26.26–27.49% under onset/offset jitter of σ = 0.15 of note duration.** — Hsiao & Su, ISMIR 2021,
> Table 1.

Every other method they tested degraded far less (HMM 67%, theirs 95%). **Skyline is not robust to
live performance timing**, which is precisely the input you have. Putting it in front of the
fingerprinter would take a stage that currently has zero error rate and give it a 30–70% one.

### 6.3 What you would use instead, if you ever needed a melody for a different reason

Ranked by fitness for a live stream:

- **McLeod & Steedman, "HMM-Based Voice Separation of MIDI Performance", JNMR 45(1), 2016** —
  https://apmcleod.github.io/pdf/VoiceSeparation.pdf, code https://github.com/apmcleod/voice-splitting.
  The only published method designed for live keyboard MIDI: incremental Viterbi with beam search
  (beam 25), no fixed voice count, tolerates within-voice note overlap. Measured on 19 live Bach
  performances: F 0.97 (Inventions) / 0.91 (WTC) vs Duane & Pardo's 0.91 / 0.82. Explicitly:
  "the incrementality of our algorithm allows it in principle to be run in real time."
- Gray & Bunescu's neural greedy / chord-level models (ISMIR 2016; arXiv 2011.03028) — greedy and
  incremental, F 88.26 on annotated popular piano vs 72.63 for iterative skyline.
- **Chew & Wu contig mapping is O(n²) and globally optimal over the whole piece** — explicitly not
  real-time.
- Temperley's Streamer requires beat quantisation first, so it needs a meter analysis you don't have.
- Simonetta et al.'s CNN wants a GPU for training (a full day on POP909) though inference is cheap.

**None of these belong in the identification loop.** If you want a melody line for *display* — a
piano-roll overlay, a "here's the tune you're playing" readout — take McLeod & Steedman's top voice
and keep it strictly downstream of the identification.

### 6.4 Two implementation details that will bite

**Sustain pedal.** CC64 > 64 = down. The standard "Onsets and Frames" preprocessing rewrites
note-offs so that a note held under pedal is extended until the pedal lifts (Hawthorne et al., ISMIR
2018, §3). **Do not apply that rewrite.** It is for transcription evaluation. Under pedal, every
earlier note is still sounding, so "highest sounding pitch" becomes meaningless and every
overlap-based heuristic downstream breaks. Uitdenbogerd & Zobel's original formulation avoids this
entirely because it is **onset-triggered**: "whenever a note starts, it chooses the top note of any
notes that start at the same time." The fingerprinter only ever reads onsets, so it is pedal-immune
by construction. Keep CC64 as a separate phrase-boundary feature if you want it; never as a duration
modifier.

**Onset clustering.** The published value with a real-time-keyboard lineage is **75 ms** — Jiang &
Dannenberg, SMC 2019, §4.1.4: "Considering that ornaments and chords may introduce a very short
IOIs, we set a window of 75 ms, and when two note onsets are within that window, we treat them as a
single onset," citing Bloch & Dannenberg, ICMC 1985. `mir_eval.transcription` uses 50 ms as its
onset tolerance. McLeod & Steedman use exact equality and absorb jitter in the transition model.
**I could find no paper that empirically compares 50 / 75 / 100 ms.** Start at 75 ms, treat it as a
tunable, and note that for the fingerprinter this only matters through the `d = 0.05 s` minimum
separation parameter — which you should probably raise to 0.075 s for the same reason.

---

## 7. Ornaments: noise, evidence, and the thing in between

### 7.1 They are treated as noise, and there is a good reason

Mongeau & Sankoff (1990) added **fragmentation** (one note → several) and **consolidation** (several
→ one) to edit distance *specifically* so an ornamented realisation collapses onto its plain version.
OFAH ("Ornamentation Filtering using Adaptive Histograms") deletes ornament notes and merges them
into the underlying long note. The flamenco MIR group states the doctrine most cleanly: a cante is
recognised by its main notes in order, and "what happens between two of those notes does not matter."

**My measurements say the filtering doctrine is right for the primary loop**, and the mechanism is
worth understanding because it is not the one you'd guess. A trill does not merely add noise notes.
It acts as a **wall in the successor graph**: my tokens pair each event with its next `n1` events, so
a 5-note trill inserted between notes `i` and `i+1` means every token that used to reach across that
point now terminates inside the trill instead. The trill severs adjacency for `n1` notes on each
side. That is why the effect is out of proportion to the note count — at L=15 a single trill costs
14 points of accuracy (0.972 → 0.836 at 1 trill per 25 notes), far more than 5 random inserted notes
would.

The fix is cheap, O(n), and measured: **collapse ornament runs on the query side before tokenising.**
Detect maximal runs where consecutive IOIs are ≤ 130 ms, all pitches lie within ±3 semitones of the
run's first note, the run is ≥ 4 notes long, and the run contains ≤ 3 distinct pitches; replace the
run with its first note. That recovers 5–9 points across the board (the `COLLAPSED` rows in §5) and
costs one linear pass.

Whether you should also collapse on the *index* side depends on your reference corpus: Humdrum and
MusicXML scores carry trills as ornament *symbols*, not realised notes, so a score-derived index is
already collapsed. A transcribed-audio index (e.g. GiantMIDI) is not, and you must collapse both
sides symmetrically.

### 7.2 Is there prior art treating ornamentation as signal?

Yes, but thin, and none of it is retrieval. The honest summary:

**The strongest quantitative evidence that ornaments carry structure**: Nakamura, Ono, Sagayama &
Watanabe, "A Stochastic Temporal Model of Polyphonic MIDI Performance with Ornaments", *JNMR*
44(4):287–304, 2015 (https://arxiv.org/abs/1404.2314). They give trills and unmeasured tremolos
their own self-looping HMM state, rewrite mordents and turns into appoggiatura + after-note pairs,
and model arpeggios explicitly. Offline note-level alignment error rate, with vs without ornament
modelling:

| piece | with ornaments | without | preprocess-them-away |
|---|---|---|---|
| Couperin, *Allemande à deux clavecins* | **2.67%** | 12.1% | 24.2% (online) |
| Beethoven PC1 mvt 2 (long sustained trills) | **1.41%** | 5.86% | 28.2% (online) |
| Beethoven PC2 mvt 3 (short appoggiaturas) | **0.87%** | 3.16% | 8.36% (online) |
| Chopin PC2 mvt 2 (arpeggios + trills + after notes) | **6.96%** | 11.2% | 28.2% (online) |

Their verdict: "it is hard to correctly match all notes by treating the indeterminacies of trill
notes simply as deletions and insertions." Modelling ornaments explicitly cut error **3–4×** versus
treating them as edits, and **5–9×** versus stripping them. That is a direct empirical refutation of
the noise doctrine — *for alignment*.

**Ornament detection as its own task** exists in folk and non-Western traditions: Köküer, Kearney,
Jančovič et al. on Irish flute cuts and strikes (ISMIR 2014,
https://archives.ismir.net/ismir2014/paper/000203.pdf, ~68% on multi-note ornaments); the RōD dataset
for Indian art-music ornamentation (arXiv 2505.04419, 2025), whose stated motivations are singer
identification and genre classification; and Carnatic *gamaka*, the one tradition where the ornament
genuinely *is* the identity of the rāga.

**But**: there is no ornament-density feature in any published symbolic feature set. jSymbolic 2.2
has 246 features / 1,497 values and exactly **one** embellishment proxy — M-21, "fraction of all
notes surrounded on both sides by notes at least three times as long" — which cannot distinguish a
trill from a passing sixteenth. Two more (grace-note count, slur count) exist only in the MEI-specific
group, unavailable from MIDI. music21 has first-class `expressions.Trill / Mordent / Turn` objects
and `realizeOrnaments()`, but exposes no features over them. **And no retrieval system indexes on
ornament placement.** This is unclaimed territory.

### 7.3 What to do with your trill detector

Yesterday's Chopin Op. 62 No. 1 identification succeeded because a trill detector found 38 trill
runs. Be precise about what that established: Op. 62/1 is famous for an extended trill passage, so
"38 trills" is a strong *prior over a small class of pieces* — it is composer-and-texture evidence,
not identification. It would have been just as consistent with the *Fantaisie-Impromptu*'s middle
section, or with Beethoven Op. 111's Arietta, or with a Baroque ornamented Adagio.

The right place for it is therefore:

1. **In the primary loop, as a filter.** The collapse detector of §7.1 *is* your trill detector,
   reused. This is the elegant part: one O(n) pass simultaneously cleans the query and emits the
   ornament runs.
2. **In the rerank, as a feature.** Once the fingerprint returns a shortlist of 5–20 candidates,
   score each on ornament agreement: does the candidate score have notated ornaments at the score
   positions where you detected runs? This is a cheap comparison against a per-piece ornament-position
   list, it uses information the fingerprint deliberately threw away, and it is exactly the kind of
   evidence that discriminates between two Chopin nocturnes with similar figuration.
3. **Never as an index key.** No one has done it, so there is no measured evidence it works, and
   ornament realisation varies enormously between performances (that is what makes it interesting
   for style and useless for hashing).

If you want to publish something small and genuinely novel out of this project, an
`ornament_placement_histogram` feature over MusicXML/MEI, evaluated on composer attribution, is a
real gap, not a re-implementation.

---

## 8. The recommended design, concretely

### 8.1 Index build (offline, once per corpus)

For each reference piece, from a note-event list `(onset_seconds, midi_pitch)` sorted by onset:

1. Optionally collapse ornament runs (only if the reference is transcribed audio, not score).
2. `s[i] = searchsorted(t, t[i] + d)` with **`d = 0.075 s`** — the first event at least `d` later.
3. For `a in 0..n1-1`: `j = s[i] + a`. For `b in 0..n2-1`: `k = s[j] + b`. With **`n1 = n2 = 2`**.
4. Keep the triple if `|p2 − p1| ≤ 24` and `|p3 − p2| ≤ 24` and both time differences are positive.
5. Emit `key = (p1 << 14) | (p2 << 7) | p3` (21 bits, fits int32; use int64 arrays),
   payload `(piece_id: int32, t1: float32, td12: float32)`.
6. Concatenate all pieces, `argsort` by key, store four parallel numpy arrays. Persist with
   `np.save` / memory-map on load.

Cost at 968 works / 2 M notes: 6.4 M tokens, 128 MB, 8.7 s to build. At 10,000 works: ~66 M tokens,
~1.3 GB — memory-map it and do not hold a Python object per token.

### 8.2 Query (every tick, incrementally)

Maintain a ring buffer of the last W = 120 note events. On each new note:

1. Run the ornament collapse over the tail of the buffer (it is local; only the last ~20 notes can
   change classification).
2. The new note completes at most `n1·n2 = 4` new tokens for earlier notes. **Emit only those**;
   never re-tokenise the buffer. This is what makes it incremental — O(1) new work per note, not
   O(W).
3. For each new token: `lo, hi = searchsorted(K, key)`; slice the payload arrays.
4. Compute `r = index_td12 / query_td12`; keep postings with `0.75 ≤ r ≤ 1.25`.
5. `bin = round((index_t1 − query_t1 · r) / 1.0)`. Increment `votes[(piece_id, bin)]`.
6. Every N notes (or every 500 ms), collapse `votes` to per-piece maxima and rank.

Vote accumulation is monotone, so the histogram is genuinely cumulative — no recomputation. Expire
the whole histogram on a silence gap of > 4 s or when the user's top hit changes decisively; this is
your "new piece started" reset.

Cost: 4 binary searches + ~160 posting operations per note. At 8 notes/second that is negligible.
Batch the numpy work across a tick rather than doing it per note if profiling says so.

### 8.3 Decision rule and calibrated confidence

Score is the raw vote count in the best `(piece, bin)` cell. Two quantities matter, and both are
measured under the LIVE regime in §9:

- **margin** = `top_votes / runner_up_votes` (runner-up over *distinct works*, not files)
- **absolute votes** in the winning bin

Emit a decision only when both gates pass; otherwise return "not enough yet" and keep listening.
Report the margin, not a fabricated probability. If you must show a percentage, calibrate it from
the table in §9 by interpolating on (L, margin) — do not invent a softmax over vote counts.

### 8.4 Optional rerank over the shortlist (cheap, ≤ 20 candidates)

Once the fingerprint has a top-20, these are all affordable because 20 × O(mn) is nothing:

- Mongeau–Sankoff local alignment of the query against the candidate's score at the retrieved offset.
- Ornament agreement (§7.3).
- Key/mode agreement, register agreement, pedal-usage agreement.
- **Only at the very end**, hand the shortlist plus metadata to an LLM to name the piece in prose.
  Nothing upstream of this line involves model inference.

---

## 9. Calibration: knowing when to say "not enough yet"

The requirement is "return calibrated confidence, and be willing to say *not enough yet*." Vote counts
are not probabilities and you must not softmax them. What *is* calibrated, measurably, is the
**separation between the winner and the rest of the field**.

Measured over **4,168 queries** at L ∈ {10, 15, 20, 30, 50, 80}, all under the LIVE regime (smooth
drift σ=0.25, jitter σ=0.06, 4% wrong notes, 3% dropped, one trill per 25 notes, ornament-collapsed),
968 works. Ungated top-1 over the pool: **0.857**.

Three candidate statistics, all O(shortlist):

**(a) margin = top votes / runner-up votes** (runner-up over distinct *works*):

| gate | ticks kept | precision when it fires |
|---|---|---|
| margin ≥ 1.00 (no gate) | 100.0% | 0.857 |
| margin ≥ 1.25 | 87.5% | 0.927 |
| **margin ≥ 1.50** | **79.1%** | **0.955** |
| margin ≥ 1.75 | 70.7% | 0.974 |
| margin ≥ 2.00 | 65.6% | 0.976 |
| margin ≥ 2.50 | 51.5% | 0.995 |
| margin ≥ 3.00 | 41.9% | 0.997 |

**(b) z = (top − mean(rest)) / sd(rest)** over the top-60 tail — a gap statistic:

| gate | ticks kept | precision |
|---|---|---|
| z ≥ 5 | 89.6% | 0.926 |
| z ≥ 8 | 74.7% | 0.972 |
| **z ≥ 12** | **57.0%** | **0.991** |
| z ≥ 20 | 34.7% | 0.992 |
| z ≥ 30 | 18.3% | 0.986 |

**(c) joint gate — margin ≥ 1.5 AND absolute votes ≥ V.** This is the one to ship:

| V | ticks kept | precision |
|---|---|---|
| 5 | 77.8% | 0.964 |
| **10** | **73.6%** | **0.978** |
| 15 | 67.9% | 0.984 |
| 25 | 55.4% | 0.990 |
| 40 | 39.1% | 0.992 |

The absolute-vote term matters because margin alone is unstable when both counts are small: 3 votes
vs 1 vote is a margin of 3.0 and means nothing. But vote count *alone* is a poor gate — in a separate
run it moved precision only from 0.40 to 0.48 as the threshold went 3 → 30. It is useful only in
conjunction with margin.

**The separation between correct and incorrect calls is clean and stable across query length**, which
is what makes this calibratable at all:

| L | top-1 | median margin, correct | median margin, wrong | median votes, correct / wrong | median z, correct / wrong |
|---|---|---|---|---|---|
| 10 | 0.628 | 2.00 | 1.20 | 12 / 6 | 9.9 / 4.7 |
| 15 | 0.753 | 2.33 | 1.20 | 20 / 8 | 12.4 / 4.6 |
| 20 | 0.858 | 2.43 | 1.12 | 28 / 11 | 13.7 / 4.9 |
| 30 | 0.941 | 3.00 | 1.17 | 42 / 16 | 17.4 / 5.2 |
| 50 | 0.970 | 3.69 | 1.33 | 58 / 21 | 22.2 / 7.0 |
| 80 | 0.980 | 3.94 | 1.16 | 74 / 30 | 25.3 / 5.8 |

Note that the **wrong**-call margin sits at 1.12–1.33 regardless of L, while the correct-call margin
grows monotonically from 2.0 to 3.9. A false top-1 is essentially always a near-tie. That is the
signal the gate is reading, and it is why the gate works uniformly across query lengths without a
per-L threshold.

### Recommended decision policy

```
if top_votes >= 10 and margin >= 1.5:      announce      # ~74% of ticks, ~98% precision
elif top_votes >= 10 and margin >= 1.25:   announce as "probably"   # ~93% precision
else:                                      "listening…"  # keep accumulating
```

Report confidence as the empirical precision from the table for the observed
(margin bucket, vote bucket), stored as a small lookup built from a run like this one on *your* index.
Re-measure it whenever the index changes size — precision at a fixed margin drifts with corpus size.

**Time to a call.** Median votes reach 10 somewhere between L=8 and L=12; margin reaches 1.5 for
correct calls by L=10. So the gate typically first fires around **10–15 notes (~2 seconds)** and is
right ~93–96% of the time when it does; by **30 notes (~5 seconds)** it is right ~98%. If it has not
fired by 60 notes, the honest report is "I don't recognise this" — which for you will usually mean
the piece is not in the index.

---

## 10. Rejected alternatives, and why

| rejected | why |
|---|---|
| **Interval / contour keys for transposition invariance** | costs 5.1 measured bits per token, drops L=15 accuracy 0.985 → 0.725, makes queries 27× slower. You play in the written key. §1.3 |
| **Melody extraction (skyline) as a preprocessing stage** | adds a stage with a 6–43% error rate on annotated melody, collapsing to **26% under live timing jitter**, in front of a stage that currently has none. The recommended algorithm does not need a melody. §6 |
| **Voice separation (Chew & Wu, Temperley, VISA)** | O(n²) or globally optimal, not real-time; and it solves a problem you don't have |
| **Edit distance / DP over the whole corpus** | O(mn) per candidate; 6×10⁸ cell updates per tick at 10k works. Fine as a rerank over ≤20, fatal as a search |
| **Geometric point-set matching (C-BRAHMS P1/P2/P3)** | no published index, so it scans every document; hours per query on 500k documents; and it returns degenerate ties (113 matches tied at maximum shared time) |
| **Earth Mover's Distance / PTD (Musipedia)** | violates the triangle inequality so it can't be indexed; MIREX 2005 measured **51,240 s for 11 queries over 558 incipits** |
| **DTW / online DTW** | the right tool for *following* a known score, the wrong tool for *choosing* among thousands |
| **LSH / MinHash over melodic n-grams** | no published symbolic system with measured recall; unnecessary when a 15-bit exact key already gives 40-posting median lists |
| **Rhythm-only ("query by tapping") index** | Doraisamy measured MRR **0.15 at 50 onsets** — 441 possible codes cannot discriminate music |
| **Parsons contour index** | 23% of 15,000 themes share the opening `*UU`; MELDEX needs >20 notes for approximate contour |
| **`n1 = n2 = 5` (Arzt's own setting)** | 6.3× the tokens for zero accuracy gain on clean MIDI. Their redundancy was buying back a 21% transcription miss rate you don't have |
| **A neural melody / embedding model** | Simonetta's CNN needs a day of GPU training and beats skyline significantly only on Mozart; MidiBERT-Piano is a BERT. None of them identify pieces — they identify melody *lines*. Wrong problem, and it violates the no-model-inference constraint |
| **Quantising the IOI ratio into the hash key** (Doraisamy's ratio bins) | hard bin boundaries are a cliff under rubato; a ±25% tolerance window on a payload degrades gracefully. §3 |

---

## 11. Risks, in order of severity

**1. You do not have a reference corpus of what you play. This is the whole risk.**
The algorithm is a 2012 result with published numbers and I have reproduced it. The index is the
open problem, and the ISMIR 2017 follow-up is a 339-piece cautionary tale in which reference quality
moved Recall@1 from 0.46 to 0.95 with the algorithm held fixed. Options, with counts and licences:

| source | size | format | licence | note |
|---|---|---|---|---|
| **`humdrum-tools/humdrum-data`** | **26,490 files, 18.4 M notes, 381 MB** | `**kern` | per-collection, heterogeneous | https://github.com/humdrum-tools/humdrum-data — you already have a subset locally; this is the practical bulk download |
| KernScores | 108,703 files, 7.87 M notes (site's own count) | `**kern` + server-side MIDI/MusicXML | mixed | https://kern.humdrum.org |
| **GiantMIDI-Piano** | **10,855 works, 2,786 composers, 38.7 M notes**; curated subset 7,236 works | transcribed MIDI, no scores | CC BY 4.0, download gated behind a disclaimer email | https://github.com/bytedance/GiantMIDI-Piano — the only thing at 10k scale. Transcribed, so ornaments are realised and you must collapse both sides |
| ASAP | 222 scores / 1,067 performances | MusicXML + score MIDI + performance MIDI + alignments | CC BY-NC-SA 4.0 | https://github.com/CPJKU/asap-dataset — small but the only corpus with note-level score↔performance alignments; **use it as your evaluation set** |
| MTD (Musical Theme Dataset) | 2,067 Barlow & Morgenstern themes | MusicXML / MIDI / CSV + audio + alignments | CC BY 4.0 | https://www.audiolabs-erlangen.de/resources/MIR/MTD |
| RISM incipits | 2,540,340 incipits | Plaine & Easie | CC-BY | https://rism.online — manuscript-biased, 4–8 bars each, but ornament signs survive into the index |
| Themefinder | ~10,000 classical themes | — | **"any attempt to download the database … will be considered a breach of copyright"** | do not scrape it |

**Recommended stack:** `humdrum-data` for the index (it is `**kern`, so ornaments are symbols and
the index is pre-collapsed), ASAP for evaluation (real performances with ground-truth alignment),
GiantMIDI only if you need 10k-scale coverage and are willing to handle realised ornaments.

**2. My accuracy numbers are optimistic in one specific way: queries and index come from the same
encoding.** My "LIVE" corruption is synthetic. It models rubato, wrong notes, dropped notes and
trills, but it does not model the systematic differences between a score edition and a performance —
different repeat structures, editorial ornament realisations, ossia passages, a pianist's
redistribution between hands. **Before trusting any of this, re-run the evaluation on ASAP**: index
the MusicXML scores, query with the human performance MIDI. That is a two-hour job and it is the
single most valuable validation available.

**3. Repeats and near-identical passages within a corpus.** Chopin wrote 59 mazurkas and Bach 370
chorales; Scarlatti sonatas share figuration wholesale. My "correct work" ground truth was built by
fingerprint-clustering, which means genuinely identical passages across works were collapsed into
one cluster and therefore never counted as errors. Real users will hit them. Mitigate by reporting
the top-3 and by using §8.4's rerank, not by pretending the ambiguity isn't there.

**4. Rubato beyond σ≈0.35.** The measured cliff is real: L=30 accuracy drops from 0.99 to 0.85 as
smooth drift goes σ=0.15 → 0.50. A genuinely free *tempo rubato* passage, a fermata, or a long
ritardando will break tokens spanning it. Arzt's own mitigation — overlapping sub-queries of 30 notes
with 15 overlap, scores pooled — is the fix, and it also improves the long-query case generally
(ISMIR 2017 Table 7: 0.89 → 0.95 by pooling ten sub-queries).

**5. Memory at 10k works.** ~1.3 GB. Memory-map, use int32/float32 where possible, and consider
dropping `t1` to a uint16 count of 0.25-second units (a 4-hour piece still fits) to get the payload
to 12 bytes.

**6. A left-hand-only or right-hand-only passage.** A long unaccompanied melodic line has fewer notes
per second and no vertical structure, so it needs more wall-clock time to reach 30 notes. This is not
an accuracy problem, just a latency one — say so in the UI.

**7. Things nobody has measured, which you should not assume.** No published comparison of 50/75/100 ms
onset clustering windows. No published symbolic retrieval system indexed on ornament placement. No
large-scale (>1,000 works) evaluation of symbolic fingerprinting — Arzt et al. promised one in 2017
("a few thousand pieces … we are going to conduct experiments regarding the scalability") and I could
not find it published. My 968-work measurement below may be the largest public number on this
specific method; treat it accordingly.

---

## 12. Reproducing the measurements

Everything in §1.3, §1.4, §1.5, §5 and §9 was measured here, not quoted. The scripts lived in a
session scratchpad and are gone; the protocol is short enough to restate exactly.

**Corpus.** `~/workspace/audio/corpus/raw/**/kern/*.krn` — 1,368 files, 1,361 with ≥100 notes.
Parsed with `music21==10.5.0` (`converter.parse`, `s.flatten().notes`, chords expanded to their
constituent pitches, offsets via `getOffsetInHierarchy`). Offsets are in quarter notes; multiply by
0.5 for a nominal 120 bpm. Total 1,977,705 note events. Parse takes ~10 minutes.

**Work clustering (to build ground truth).** Multiple Chopin first editions encode the same work.
Rather than parse metadata, cluster with the fingerprinter itself: for each file take three 120-note
queries at 20% / 45% / 70% depth, retrieve, and union-find any file scoring ≥ 40% of the query's
self-score. Result: **968 clusters from 1,361 files**, largest cluster 8. Report accuracy at cluster
level, not file level; otherwise duplicate editions register as errors.

**Index.** As §8.1, `d = 0.05 s` in the runs above (§8.1 recommends 0.075 s; I did not re-run the
whole battery at 0.075 s — that is a loose end).

**Queries.** Random contiguous L-note windows from random files, onset times rebased to zero.
250–800 trials per cell, fixed seeds. Correct = the top-scoring *cluster* equals the source file's
cluster.

**Corruption models.**
- *Smooth tempo drift, σ*: multiply each IOI by `exp(σ·x_i)` where `x` is unit-variance Gaussian
  white noise convolved with an exponential kernel `exp(−i/8)`, renormalised. Autocorrelation
  timescale ≈ 8 notes. This is the musically realistic model.
- *Onset jitter, σ*: multiply each IOI by `exp(N(0, σ))`, independent per note.
- *Wrong notes, p*: with probability `p`, add ±1 or ±2 semitones.
- *Dropped notes, p*: delete with probability `p`.
- *Trills, rate r*: pick a random note, fill its IOI with alternating principal / upper-neighbour
  (+1 or +2 semitones) at 55–95 ms spacing. `r` given as trills per L notes.

**Ornament collapse (query side).** Single left-to-right pass. Extend a run from index `i` while
consecutive IOIs ≤ 130 ms and `|p_j − p_i| ≤ 3`. If the run is ≥ 4 notes and contains ≤ 3 distinct
pitches, keep only note `i` and record `(t_i, run_length, min_pitch)`.

**Key-uniqueness statistics (§5).** `np.unique(K, return_index=True, return_counts=True)` over the
sorted key array; a key "resolves to one work" if all its postings' piece-ids map to the same
cluster. Key entropy is `−Σ p log₂ p` over the posting-count distribution.

**Hardware.** M-series MacBook, 8.6 GB usable, Python 3.14, numpy 2.5.1, single core, no BLAS in the
hot path.

**Loose ends I did not close.** (a) `d = 0.075 s` battery. (b) `n1=n2=5` full battery — the run was
cut short; the `n=2` vs `n=3` comparison is complete and conclusive, and `n=5` is strictly worse on
cost. (c) ASAP score-vs-performance validation (§11 risk 2) — the important one. (d) A 10,000-work
index, to check the log-scaling extrapolation directly.

---

## 13. Sources

Primary, with the numbers this brief depends on:

- Arzt, Böck & Widmer, *Fast Identification of Piece and Score Position via Symbolic Fingerprinting*,
  ISMIR 2012, 433–438. https://archives.ismir.net/ismir2012/paper/000433.pdf — **the recommended algorithm**
- Arzt et al., *Piece Identification in Classical Piano Music Without Reference Scores*, ISMIR 2017,
  354–360. https://archives.ismir.net/ismir2017/paper/000127.pdf
- Wang, *An Industrial-Strength Audio Search Algorithm*, ISMIR 2003 — the offset-histogram trick
- Doraisamy & Rüger, *An Approach Towards A Polyphonic Music Retrieval System*, ISMIR 2001.
  https://ismir2001.ismir.net/pdf/doraisamy.pdf ; *Robust Polyphonic Music Retrieval with N-grams*,
  JIIS 20(1):53–70, 2003. https://link.springer.com/article/10.1023/A:1023553801115
- McNab, Smith, Witten, Henderson & Cunningham, *Towards the Digital Music Library: Tune Retrieval
  from Acoustic Input* / MELDEX, D-Lib Magazine, May 1997.
  https://www.dlib.org/dlib/may97/meldex/05witten.html — **the notes-vs-representation table**
- Barlow & Morgenstern, *A Dictionary of Musical Themes*, Crown, 1948.
  https://archive.org/details/dictionaryofmusi00barl — "six places … in no case more than eleven"
- Uitdenbogerd & Zobel, *Manipulation of Music For Melody Matching*, ACM MM '98.
  https://people.eng.unimelb.edu.au/jzobel/fulltext/acmmm98.pdf ; *Melodic Matching Techniques for
  Large Music Databases*, ACM MM '99. https://people.eng.unimelb.edu.au/jzobel/fulltext/acm-mm99.pdf
  — the origin of skyline (they call it `all-mono`)
- Uitdenbogerd & Yap, *Was Parsons right? An experiment in usability of music*, ISMIR 2003 — the 23%
- Hsiao & Su, *Learning Note-to-Note Affinity for Voice Segregation and Melody Line Identification*,
  ISMIR 2021. https://archives.ismir.net/ismir2021/paper/000035.pdf — **skyline 95.76% → 26% under jitter**
- Simonetta, Cancino-Chacón, Ntalampiras & Widmer, *A Convolutional Approach to Melody Line
  Identification in Symbolic Scores*, ISMIR 2019. https://arxiv.org/abs/1906.10547
- Chou, Chen et al., *MidiBERT-Piano*, arXiv 2107.05223 — skyline on POP909
- McLeod & Steedman, *HMM-Based Voice Separation of MIDI Performance*, JNMR 45(1), 2016.
  https://apmcleod.github.io/pdf/VoiceSeparation.pdf ; code https://github.com/apmcleod/voice-splitting
- Chew & Wu, *Separating Voices in Polyphonic Music: A Contig Mapping Approach*, CMMR 2004 — the O(n²)
- Gray & Bunescu, *A Neural Greedy Model for Voice Separation*, ISMIR 2016.
  https://archives.ismir.net/ismir2016/paper/000296.pdf ; chord-level, arXiv 2011.03028
- Nakamura, Ono, Sagayama & Watanabe, *A Stochastic Temporal Model of Polyphonic MIDI Performance
  with Ornaments*, JNMR 44(4), 2015. https://arxiv.org/abs/1404.2314 — **ornaments as signal**
- Nakamura, Yoshii & Katayose, *Performance Error Detection and Post-Processing for Fast and Accurate
  Symbolic Music Alignment*, ISMIR 2017.
  https://eita-nakamura.github.io/articles/EN_etal_ErrorDetectionAndRealignment_ISMIR2017.pdf ;
  MIT-licensed code https://midialignment.github.io/demo.html
- Mongeau & Sankoff, *Comparison of Musical Sequences*, Computers and the Humanities 24:161–175, 1990.
  https://link.springer.com/content/pdf/10.1007/BF00117340.pdf
- Jiang & Dannenberg, *Melody Identification in Standard MIDI Files*, SMC 2019.
  https://www.smc2019.uma.es/articles/P1/P1_10_SMC2019_paper.pdf — the 75 ms onset window
- Hawthorne et al., *Onsets and Frames*, ISMIR 2018. https://arxiv.org/abs/1710.11153 — the pedal
  rewrite you should **not** apply
- Typke, *Music Retrieval based on Melodic Similarity*, PhD thesis, Utrecht, 2007.
  https://dspace.library.uu.nl/handle/1874/19776 — EMD/PTD, MIREX evaluations, vantage indexing
- Lemström, Mäkinen, Ukkonen et al., C-BRAHMS. https://github.com/ELVIS-Project/PatternFinder
  (P1/P2 implementations)
- McKay, Cumming & Fujinaga, *jSymbolic 2.2*, ISMIR 2018.
  https://jmir.sourceforge.net/publications/mckay18jsymbolic.pdf — 246 features, one ornament proxy
- Zalkow, Balke, Arifi-Müller & Müller, *MTD: A Multimodal Dataset of Musical Themes*, TISMIR 3(1),
  2020. https://transactions.ismir.net/articles/10.5334/tismir.68
- Foscarin et al., *ASAP dataset*, ISMIR 2020. https://archives.ismir.net/ismir2020/paper/000127.pdf ;
  note-level alignments, TISMIR 2023. https://transactions.ismir.net/articles/10.5334/tismir.149
- Kong et al., *GiantMIDI-Piano*, TISMIR. https://transactions.ismir.net/articles/10.5334/tismir.80
- Themefinder. https://www.themefinder.org ; RISM. https://rism.online ;
  Peachnote, ISMIR 2011. http://ismir2011.ismir.net/papers/PS3-1.pdf ;
  music21 `search`. https://www.music21.org/music21docs/moduleReference/moduleSearch.html
