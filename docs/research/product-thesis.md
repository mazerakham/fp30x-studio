# Product thesis: symbolic work identification from a MIDI stream

**A stress test.** Written 2026-08-19. Strategy and prior-art lane; the melodic-retrieval
algorithm survey and the theme corpus are owned by other agents and are referenced here,
not duplicated.

Everything below is either cited to a URL or marked **unverified**. Where the evidence
contradicts the thesis, the evidence is stated first.

---

## 0. The thesis as stated

> "I view this as a significant improvement over the product offered by Shazam, which can
> never identify classical music."

The claim underneath it, as given:

1. Shazam fingerprints a *specific audio recording* (spectral peak constellation hashes),
   so it matches recordings, not works.
2. Every performance of a Chopin nocturne is a different signal, so classical is
   structurally out of reach.
3. Symbolic identification from a MIDI stream matches the **work** instead, using
   transposition-invariant intervals and tempo-invariant rhythm ratios.

Claim 1 is correct and is confirmed by the primary source. Claim 3 is correct and is
confirmed by published systems that already do it. **Claim 2 is false as stated**, and it
is the load-bearing claim in the sentence.

---

## 1. The strongest objections, in order of damage

There are five. Four are below; the fifth is a business objection that only makes sense
after the competitive evidence, so it lives at **§4.5, Objection 5: selection is the
monetisation surface**. It may well be the one that matters most.

### Objection 1. Shazam identifies classical music, and Apple has spent real money making it good at it

This is the objection that must be confronted rather than dodged, and it lands.

Shazam matches recordings. Most classical *listening* is listening to released recordings.
Released recordings are in the index. Therefore, for the actual Shazam use case (unknown
music in a café, in a film, on the radio), classical is largely within reach and always
has been.

Worse for the thesis, Apple has invested specifically in the classical case:

- Apple acquired **Primephonic** in August 2021 explicitly to build a classical service
  ([Wikipedia, Apple Music § Apple Music Classical](https://en.wikipedia.org/wiki/Apple_Music#Apple_Music_Classical),
  citing [MacRumors](https://www.macrumors.com/2021/08/30/apple-acquires-primephonic-classical-music-service/)).
- **Apple Music Classical** shipped 2023-03-28 on iOS, 2023-05-30 on Android,
  2023-11-16 on iPad, now in 161 countries (same source, citing
  [The Verge](https://www.theverge.com/2023/3/27/23659326/apple-music-classical-available-download-app-store-ios/)
  and [TechCrunch](https://techcrunch.com/2023/03/28/apple-music-classical-is-now-available-for-download-to-everyone/)).
- Apple acquired the classical label **BIS Records** in September 2023
  ([TechCrunch](https://techcrunch.com/2023/09/05/apples-bis-acquisition-is-a-bet-on-a-classical-music-catalogue-and-on-building-cred-in-the-industry/)).
- Shazam hands classical identifications straight to it. Apple's own Shazam User Guide:
  *"If the Shazam app identifies a song as classical, you can open the song in the Apple
  Music Classical app."*
  ([support.apple.com Shazam User Guide](https://support.apple.com/en-afri/guide/shazam/deve9fff0ee4/web))

So the sentence "Shazam can never identify classical music" is not defensible. The
defensible version is much narrower:

> Shazam cannot identify a **performance that is not in its index** — a live concert, a
> student at a piano, your own playing, an unreleased or obscure recording — even when the
> *work* is famous and is represented in the index by fifty other recordings.

That narrower claim is true, is confirmed by the author of the algorithm (§2), and is the
only version worth building on. It is a claim about **performances**, not about
**classical**. Stating it as a claim about classical is what makes it wrong, and it is
wrong in a way a knowledgeable listener will catch in one sentence.

Note also the secondary literature repeats a *softer* version of the thesis in Shazam's
favour: that classical is harder for fingerprinting because of the density of competing
recordings of the same work and the non-linear structure of the music. Those articles are
low-quality SEO/consumer sources and should not be leaned on
([liveabout](https://www.liveabout.com/shazam-724072),
[groovypost](https://www.groovypost.com/howto/use-shazam-with-apple-music-classical/)),
but they consistently repeat one thing that matters: **Shazam does not work on live music**.
That is the thesis's real ground.

**What this does to the pitch.** "Better than Shazam at classical" is a losing frame. It
invites a demo where someone plays a Rubinstein CD at both apps and Shazam wins with
better metadata. The honest frame is "Shazam identifies recordings; this identifies
works, from playing that was never recorded by anyone."

---

### Objection 2. The Shazam analogy is a category error about *who is asking*

Shazam's entire value is identifying music you can hear but **did not choose**. At a
digital piano, the user is the source. He chose the piece, he has the score in front of
him, and he almost always knows what it is.

The genuinely open-set cases at a MIDI piano are thin:

- sight-reading something unfamiliar — but the score is right there with the title on it;
- improvising — there is no work to identify;
- a teacher or parent hearing a student play something unnamed;
- returning to a half-remembered piece learned years ago.

That is a real but small set of moments. **The demand is not for identification-as-discovery.
It is for identification-as-automatic-labelling**: the user knows what he played and does
not want to type it in. Same primitive, completely different product, completely different
pitch, and (see §4) a much better one.

**Falsifiable now, cheaply:** ask twenty pianists how many times in the last month they
played something at a keyboard they could not name. If the answer is near zero, the
identification product is dead and the *logging* product is the only thing left standing.

---

### Objection 3. "MIDI sidesteps polyphonic transcription, so the hard part is free" — the hard part is now free for everybody

This is the objection that removes the technical moat.

Solo-piano automatic music transcription is effectively solved and is a commodity:

| System | Result | Source |
| --- | --- | --- |
| Hawthorne et al., Onsets and Frames (2018) | note onset F1 **94.80%** on MAESTRO | via Kong et al. below |
| Kong et al., ByteDance (2021), high-resolution + pedals | onset F1 **96.72%**; onset+offset F1 **82.47%**; onset+offset+velocity F1 **80.92%**; sustain-pedal onset F1 **91.86%**, pedal onset+offset F1 **86.58%** | [IEEE/ACM TASLP](https://dl.acm.org/doi/10.1109/TASLP.2021.3121991), code at [github.com/bytedance/piano_transcription](https://github.com/bytedance/piano_transcription), `pip install piano-transcription-inference` |
| Spotify **Basic Pitch** (2022) | free, open source, runs in a browser tab | [basicpitch.spotify.com](https://basicpitch.spotify.com/) |
| **Klangio** (commercial) | Piano2Notes, Transcription Studio, plus a paid API | [klang.io](https://klang.io/) |

A competitor with a phone microphone next to an acoustic upright gets a symbolic stream
too, at 96.7% onset F1, for free. **The MIDI requirement is not a moat.** It is a
convenience, a fidelity advantage, and a market restriction, all at once.

Worse: MIDI input is already **table stakes** in the practice-app category. Yousician,
Simply Piano, flowkey, Skoove, Playground Sessions, Piano Marvel, Melodics and PianoVision
all accept MIDI, several document USB, DIN and Bluetooth MIDI separately, and Synthesia
accepts *nothing else* (§4.5). "We take MIDI from your digital piano" is not a claim anyone
in this market will find interesting.

What MIDI genuinely buys that transcription does not:

- exact note-level ground truth with essentially no false positives or octave errors;
- **release velocity**, which no transcription model recovers at all, and which the
  local measurements show carries 3.684 bits against strike velocity's 3.140, effectively
  independent (`docs/` in this repo);
- exact pedal CC values rather than an 86.6% F1 estimate;
- ~5 ms timing over BLE on this instrument, ~1 ms expected over USB (untested).

That is a real sliver, and it is narrow. It supports *expressive analysis* claims. It does
not support *identification* claims, because identification only needs pitch and onset
time, which is precisely the part transcription already does at 96.7%.

**The strategic consequence is favourable if you accept it early.** Build the identifier
to consume a symbolic stream and to not care where the stream came from. Then MIDI-only is
a v1 constraint rather than an architecture, and the acoustic-piano market is a later
switch rather than a rewrite. But stop describing MIDI as the advantage.

---

### Objection 4. The core algorithm is published, is fourteen years old, and already works

The exact primitive in the thesis — transposition-invariant pitch relations plus
tempo-invariant time ratios, hashed and histogrammed — is Arzt, Böck & Widmer, ISMIR 2012,
*Fast Identification of Piece and Score Position via Symbolic Fingerprinting*
([ISMIR 2012, pp. 433–438](https://www.researchgate.net/publication/264011322_Fast_Identification_of_Piece_and_Score_Position_via_Symbolic_Fingerprinting)).
Arzt & Widmer describe it as: tokens *"created based on the pitches of three temporally
local note events, together with the ratio of their distances in time. Due to the way they
are created, the tokens are invariant to the global tempo, and can be stored in a hash
table and efficiently queried for."*

The follow-on paper is the closest thing in the literature to the product being
contemplated: Arzt & Widmer, **ISMIR 2017**, *Piece Identification in Classical Piano Music
Without Reference Scores*
([PDF](https://archives.ismir.net/ismir2017/paper/000127.pdf), [arXiv:1708.00733](https://arxiv.org/abs/1708.00733)).

Their setup, and it is harder than Jake's in one specific way and easier in another:

- 339 classical piano works (Mozart, Beethoven, Chopin, Scriabin, Debussy).
- Reference database built **fully automatically** by crawling YouTube from a plain text
  list of "composer; piece", transcribing with a neural piano transcriber (madmom), and
  fingerprinting the transcription.
- Ground truth: 370 commercial tracks, ~30 hours, 665,000 transcribed events (Uchida,
  Brendel, Arrau, Pires, Pollini, Thibaudet, Zimerman), with exact replicas manually
  excluded.
- 3,700 queries per query length.
- **Both query and reference are noisy transcriptions from audio.**

Recall@1:

| Reference DB | 2 s | 5 s | 10 s |
| --- | --- | --- | --- |
| 1 crawled performance per piece (baseline) | 0.28 | 0.38 | 0.46 |
| top-5 crawled | 0.58 | 0.69 | 0.74 |
| top-15 crawled | 0.76 | 0.87 | 0.91 |
| top-1 auto-selected | 0.54 | 0.68 | 0.74 |
| top-5 auto-selected | 0.72 | 0.85 | 0.89 |
| top-5 auto-selected, 10 × 10 s queries pooled | 0.92 | 0.95 | 0.95 |

Mean query time 0.13 s (2 s query, baseline DB) to 6.08 s (10 s query, 15 performances per
piece) on a 2017 Core i7-6700K, single core.

And on the point at issue, from the same paper's introduction, about audio fingerprinting:

> "However, these algorithms are not able to identify different performances of the same
> piece of music, as they are not designed to work in the face of musical variations such
> as different tempi, expressive timing, differences in instrumentation, ornamentation and
> other performance aspects."

**Read this two ways, both of them important.**

*Against the thesis:* the algorithm is not novel and must never be pitched as such. If the
project's claimed contribution is "transposition-invariant intervals and tempo-invariant
rhythm ratios", the correct response from any MIR person in the room is a citation to 2012.
The novelty, if there is any, is in the product, the corpus, and the open-set behaviour,
not the hashing.

*For the thesis, and this is the single most encouraging fact in this brief:* Arzt's
dominant error source is transcription noise, on **both** sides. He says so repeatedly, and
his fix is brute redundancy (fifteen reference performances per piece). Jake's query side
is clean MIDI with zero transcription error and hardware timestamps. **Arzt's 0.91 at 10
seconds is therefore a floor, not a target.** If a clean-MIDI query against a clean
symbolic reference cannot beat 0.91 at 10 seconds on a comparable corpus, something is
wrong with the implementation, and that is a crisp Stage 1 falsifier (§6).

---

## 2. What the Wang 2003 paper actually says

Source: A. Wang, *An Industrial-Strength Audio Search Algorithm*, ISMIR 2003, pp. 7–13.
[PDF](https://www.ee.columbia.edu/~dpwe/papers/Wang03-shazam.pdf)

**Mechanism, as described in the paper.** Spectrogram peaks chosen by local-maximum energy
plus a density criterion, reduced to a "constellation map" with amplitude discarded. Peaks
are combinatorially paired: each anchor point is paired with points in a target zone, each
pair yielding two frequency components plus the time difference between them, packed into a
32-bit hash and stored with the time offset of its anchor and a track ID (64-bit struct).
Matching hashes generate (t_k', t_k) pairs binned by track; a match is a significant cluster
in the histogram of δt_k = t_k' − t_k. Two 10-bit frequencies plus a 10-bit Δt gives ~30 bits
against ~10 for a bare peak, roughly a million times the specificity, and about 10,000× the
search speed for ~10× the storage.

**The invariances the paper claims** are exactly three: temporal locality, translation
invariance (position within the file), and robustness to degradation. **Pitch invariance and
tempo invariance are not claimed and are not mentioned anywhere in the paper.** The scoring
step assumes t_k' = t_k + offset with the slope of the diagonal equal to 1.0. A linear
time-scale change breaks that assumption, and a resampling-style speed change moves every
peak in frequency as well. This is why sped-up social-media edits (typically +10% to +30%)
defeat Shazam
([discussion](https://www.inspiredbybeatz.com/en/song-detection-limit-sped-up-remix-ai/), consumer-blog source, low quality but consistent with the paper's mathematics).

**The two sentences that settle the mechanism question.** From §3.3, *Specificity and False
Positives*:

> "The algorithm was designed specifically to target recognition of sound files that are
> already present in the database. **It is not expected to generalize to live recordings.**
> That said, we have anecdotally discovered several artists in concert who apparently
> either have extremely accurate and reproducible timing (with millisecond precision), or
> are more plausibly lip synching."

> "The algorithm is conversely very sensitive to which particular version of a track has
> been sampled. Given a multitude of different performances of the same song by an artist,
> the algorithm can pick the correct one even if they are virtually indistinguishable by
> the human ear."

The author of Shazam states, in the founding paper, that his system is recording-level and
not performance-general, and jokes that the only live shows it identifies are the mimed
ones. **The thesis's mechanism claim is correct and has the best possible source.**

**Quantitative anchors from the paper.** 250 recognitions against a 10,000-track database of
popular music, noise recorded in a pub, excerpts from the middle of each track. 50%
recognition rate at approximately −9 / −6 / −3 dB SNR for 15 / 10 / 5 second samples;
after GSM 6.10 compression, −3 / 0 / +4 dB. 8 kHz mono 16-bit. Search time 5–500 ms on
~20,000 tracks; under 10 ms on radio-quality audio. A statistically significant match needs
only 1–2% of generated hash tokens to survive. "Transparency": several tracks mixed together
can each be identified, *"including multiple versions of the same piece."*

**Honesty caveat, and it matters.** This is a 2003 paper describing a 2003 system with 1.8M
tracks. Shazam in 2026 is owned by Apple, is not the 2003 system, and Apple has published
nothing about the current pipeline. Arguing about present-day Shazam from a 23-year-old
architecture paper is exactly the kind of move Jake would catch in someone else's work.
**Unverified:** what Shazam's current matching pipeline does, whether it has added any
tempo- or performance-tolerant stage, and how many classical recordings are in its index.
No public numbers were found for classical coverage.

---

## 3. Scorecard on the thesis

| Claim | Verdict | Basis |
| --- | --- | --- |
| Shazam fingerprints a specific recording via spectral peak constellation hashes | **Survives, primary source** | Wang 2003 §2.1–2.3 |
| Shazam is recording-level, not work-level | **Survives, primary source** | Wang 2003 §3.3, verbatim above |
| Shazam does not generalize to live performance | **Survives, primary source** | Wang 2003 §3.3, verbatim above |
| Shazam is not tempo- or pitch-invariant | **Survives** (by absence of any such claim + the slope-1.0 scoring) | Wang 2003 §2.3 |
| "Shazam can never identify classical music" | **Fails** | Shazam → Apple Music Classical handoff; Primephonic and BIS acquisitions; classical listening is overwhelmingly of indexed recordings |
| Symbolic ID matches the work, using transposition-invariant intervals and tempo-invariant ratios | **Survives, and is already published and evaluated** | Arzt/Böck/Widmer ISMIR 2012; Arzt/Widmer ISMIR 2017 |
| MIDI input means the hard part (polyphonic transcription) is free | **Survives narrowly, but the moat does not** | Piano AMT at 96.72% onset F1 is open source and free; Basic Pitch; Klangio |
| Work-level ID is a more valuable primitive than recording-level ID | **Unproven, and this is the real question** | No product anywhere monetises it; see §4–§5 |

---

## 4. The competitive picture

Names, capabilities, and what each one actually does with an input stream.

### 4.1 Recording-level identification — commodity

Shazam (Apple), and the B2B fingerprinters ACRCloud, Audible Magic, Pex, BMAT. All
recording-level. Nothing to build here.
(**Unverified:** whether any of these commercially claim cover-version or live-performance
detection; the intended check of their product pages was not completed before the session's
web-search budget was exhausted.)

### 4.2 Work-level identification from audio — academic, no product

- **Arzt & Widmer (JKU Linz / OFAI)**, ISMIR 2012 and 2017, numbers in §1.4 above. This is
  the state of the art for "tell me which classical piano piece this is". It was never
  productised.
- **Peachnote** (Vladimir Viro, Munich, founded 2007/2009): melodic and chord-sequence
  search over OMR'd sheet music, an n-gram viewer, score similarity, performance comparison.
  [peachnote.com](http://www.peachnote.com/) still serves a page; the copyright line reads
  2021 and no corpus size is stated on the landing page. **Unverified:** whether search
  still functions in 2026. Original paper: [ISMIR 2011](https://ismir2011.ismir.net/papers/PS3-1.pdf).

### 4.3 Query by humming — alive at Google, faded elsewhere

- **Google "Hum to Search"** (2020). Fully machine-learned; a neural network produces an
  embedding of a melody directly from the spectrogram of a song, trained with triplet loss
  on paired sung/recorded audio and on synthetic humming generated via SPICE. Critically:
  *"This enables the model to match a hummed melody directly to the original (polyphonic)
  recordings without the need for a hummed or MIDI version of each track."* Reference corpus
  is "over half a million songs" and Google notes it *"still has room to grow to include
  more of the world's many melodies."*
  ([Google Research blog](https://research.google/blog/the-machine-learning-behind-hum-to-search/))
  **Google publishes no accuracy numbers, no genre breakdown, and no statement about
  classical or instrumental music.** Consumer blogs claim ~70% overall and poor classical
  performance; that is not a citable measurement.
  ([IEEE Spectrum coverage](https://spectrum.ieee.org/how-to-find-a-song-by-humming))
- **SoundHound / Midomi.** `midomi.com` now redirects to
  `music.soundhound.com/soundhound`, i.e. the standalone query-by-humming site is gone and
  the surface is an app marketing page (verified by HTTP redirect, 2026-08-19).
  **Unverified:** whether hum-to-search still functions in the current app, and whether
  SoundHound AI still treats consumer music ID as a product line rather than a legacy asset
  behind its voice-AI business.
- **Musipedia.** `musipedia.org` returns HTTP 403 to automated fetches (verified
  2026-08-19). **Unverified:** whether it is functional for human visitors, its current
  corpus size, or its maintenance status.

The pattern in this row: melody-based retrieval at consumer scale exists in exactly one
place, Google, it is pointed at pop, and its reference corpus is *recordings*, not works.

### 4.4 Score following and accompaniment — commercially alive, and closed-set

**Antescofo** is the IRCAM score-following system created by Arshia Cont in 2007 with
composer Marco Stroppa, co-developed with INRIA since 2012, shipped as Max/MSP and Pd
externals, used by the Berlin and LA Philharmonics, written for by Boulez and Manoury
([Wikipedia](https://en.wikipedia.org/wiki/Antescofo)). It locates a performer's position
within *a given score*: closed-set by construction.

**Antescofo SAS ships Metronaut**, and it is thriving. `antescofo.com` now redirects to
`metronautapp.com`; the app is **v8.4.2, updated 2026-08-19**, on iOS, Android, macOS and
web, with a 15,000+ piece catalog, 20+ instruments including piano, professional
accompaniments, auto-scrolling notation, transposition, "100,000+ musicians" claimed, and
partnerships with Woodbrass, Buffet-Crampon and Radio France
([antescofo.com](https://www.antescofo.com/)). Its score-following feature is branded
**"Magic Mode": "Activate Magic Mode to have the tempo automatically adapted to your speed
in real time."** You browse and select the piece first, always.

**Cadenza** (the Christopher Raphael / *Music Plus One* lineage) is alive but nearly
dormant, and has changed hands: now published by **MetaMusic Inc.**, current v2.0.9, **last
updated 2025-05-19**, i.e. no release in fifteen months. It offers accompaniment that
*"follows you in real-time using musical artificial intelligence"* over *"nearly 3,000
pieces and movements covering the core classical instrumental repertoire"*. Microphone
based; you pick the piece. **Unverified:** Raphael's current involvement (his Indiana
University *Music Plus One* page 404s), and the present availability of Music Plus One
itself.

So the best score-following technology in the world, after nearly twenty years and two
commercialisations, still requires you to name the piece.

### 4.5 Practice apps — all closed-set, all "did you hit the right note"

A dedicated audit of this category was run for this brief. The finding was unanimous and
was reached from primary sources (vendor help centres, App Store listings, the iTunes
Search API, Wayback).

Most products take **both** microphone and MIDI input, and several document the MIDI path
carefully because it is more accurate. **MIDI input is therefore already table stakes in
this category, not a differentiator.** flowkey states the tradeoff outright: connecting by
cable makes note detection *"nearly perfect, since computers often process digital data
more precisely than acoustic data"*
([flowkey help](https://help.flowkey.com/en/articles/412925-connect-your-instrument-to-your-laptop-or-pc-mac)),
and warns that *"in some instances, your iPad microphone won't reliably detect the notes
you're playing"*
([flowkey help](https://help.flowkey.com/en/articles/412853-connect-your-instrument-to-your-ipad)).
Simply Piano documents USB MIDI, DIN MIDI and Bluetooth MIDI separately
([help centre](https://piano-help.hellosimply.com/en/articles/8147838-connect-to-midi-over-bluetooth)).
Yousician documents Bluetooth MIDI. Synthesia is **MIDI-only and has no microphone support
at all** ([FAQ](https://www.synthesiagame.com/faq)).

Ranked by how explicit the closed-set evidence is:

| Product | Input | Evidence it is closed-set |
| --- | --- | --- |
| **Synthesia** | MIDI only | The "score" is literally a MIDI file you selected. $29 unlock. |
| **Soundslice** | none — does not listen | MIDI is *"to enter notes via our editor"* ([docs](https://www.soundslice.com/help/en/creating/basics/308/midi-entry/)); practice tracking is a manual **"Practiced?"** button ([docs](https://www.soundslice.com/help/en/player/notebook/283/practice-tracking/)) |
| **flowkey** | mic + MIDI | Wait Mode *"waits for you to hit the right notes"* — it knows the next expected note and blocks |
| **Tonara** | mic | Tap the teacher's assignment, then "Start Practice" (see below) |
| **Metronaut** (Antescofo) | mic | Browse catalog, select, then "Magic Mode" adapts tempo to you |
| **Cadenza** (MetaMusic) | mic | Select from ~3,000 pieces, accompaniment follows you |
| **Piano Marvel** | MIDI-centric | SASR sight-reading: 90 levels of excerpts, adaptively *selected by the system*. Unfamiliar to you, fully known to it. |
| **Trala** (violin) | mic | *"signal processing … pitch and rhythm … when you play the wrong notes"* — the most honest description in the category |
| **Yousician** | mic + MIDI | *"accuracy and timing"* against the lesson you opened |
| **Simply Piano** | mic + MIDI | see the trap below |

**The one misleading sentence in the category**, worth knowing because it will be quoted at
you: Simply Piano's App Store listing says *"the app will immediately recognize what you are
playing."* It means it recognises the notes of the lesson you opened. There is no
piece-identification feature anywhere in their documentation.

**Consolidation and mortality signals, both worth noting:**

- **Tonara is dead.** `tonara.com` is a parked Sedo domain as of 2026-08-19; last healthy
  Wayback capture 2025-10-15; no shutdown announcement found. It matters because Tonara was
  **the nearest miss in the market to ambient practice detection**: *"hears your students
  play"*, *"patented AI technology knows how you are playing."* What that actually meant, per
  its own help centre, was: tap the assignment your teacher sent, tap Start Practice, and the
  microphone confirms you are playing *something*
  ([archived](https://web.archive.org/web/20240620/https://www.tonara.com/helpcenter/knowledge-base/how-to-practice-my-assignment/)).
  It detected *that* you played, never *what*. And it still died.
- **Trala was acquired by Learnfield GmbH, the parent of Skoove** (stated in Trala's own App
  Store listing; corroborated by trala.com's JSON-LD and its "© 2026 Learnfield GmbH"
  footer). The category is consolidating.

**And the practice-journal evidence, which cuts directly at the proposed wedge:**

The serious-musician practice-journal products do **zero** automatic content detection.
**Modacity** (BotRobot Inc) is *"plan, record, reflect"* — timers, audio recordings, notes.
**Andante Music Practice Journal** is manual session logging with a timer and streaks that
*you* enter. Soundslice's practice history is a button you press once a day and cannot even
backdate. These are the products bought by people who care most about practising, and none
of them tries to know what was played.

Read that two ways as well: either automatic content logging is the obvious unbuilt feature,
or the customers who most want a practice journal are perfectly happy pressing a button.

**The single most important structural observation in this brief:**

> Every listening product in the market is closed-set. The user names the piece; the
> software checks the performance against it. **Nothing takes an unlabelled stream and
> tells you what it was.** And nothing logs repertoire from unprompted playing: every
> practice log found is either a manual mark, a session-scoped activity detector, or
> progress through a curriculum you opened.

---

### 4.5.1 Objection 5 (business, not technical): selection is the monetisation surface

This came out of the landscape audit and it is the sharpest business objection in the brief.

Every product in §4.5 makes money from its **catalog**. The subscription buys access to
songs, lessons, arrangements, accompaniments. Selection-before-performance is not an
accident of the technology; it is the paywall. Metronaut sells 15,000 pieces. Piano Marvel
sells a graded bank. Synthesia sells a Music Store.

A system that identifies unprompted playing has **no catalog gate**. The user brings his own
repertoire and his own sheet music. There is nothing to sell him access to. That may be a
larger obstacle than the open-set rejection problem, and it explains the market's shape
better than "nobody thought of it" does.

If the wedge is repertoire logging (§5), the business model has to be something other than a
catalog: the accumulated personal archive itself, a teacher-facing seat, or a one-time
purchase. That question should be answered before Stage 4, not after.

Two readings of the whole §4.5 pattern, and the project turns on which is right:

(a) Nobody has done it because the corpus, the open-set rejection problem, and the absent
business model are genuinely hard, and there is a real gap. (b) Nobody has done it because
at a piano the player already knows, and the feature has no demand.

Objection 2 and §4.7 both argue (b) is a serious risk for identification-as-a-feature. §5
argues (a) is where the value is, if it is reframed as effortless labelling.

### 4.5.2 Open-set analysis *does* ship — just never pointed at your own playing

Two counterexamples that matter, because they undercut "open-set is too hard":

- **Chord ai** (Nomad AI OÜ, updated 2026-08-19) does open-set chord and beat recognition
  from arbitrary audio, as a shipping consumer app.
- **Klangio** transcribes arbitrary unknown audio to MIDI/MusicXML/PDF and claims >10 million
  transcriptions ([klang.io](https://klang.io/)). Explicitly **not** real-time: upload and
  wait.

So open-set *analysis* is commercially viable. What nobody has built is open-set
*identification against a repertoire*. The academic line that addresses exactly this is:
[Exploiting Temporal Dependencies for Cross-Modal Music Piece Identification](https://arxiv.org/abs/2105.12536)
(2021), plus the audio–sheet-music retrieval work
[Towards Robust and Truly Large-Scale Audio-Sheet Music Retrieval](https://arxiv.org/abs/2309.12158)
and [Self-Supervised Contrastive Learning for Robust Audio-Sheet Music Retrieval](https://arxiv.org/abs/2309.12134)
(both 2023), and [CLaMP 3](https://arxiv.org/abs/2502.10362) (2025). None has shipped.

### 4.6 Transcription — commodity, and it is the reason MIDI is not a moat

Covered in Objection 3. ByteDance's model, Spotify Basic Pitch, Klangio's API.

### 4.7 The live-performance royalty analogue — manual, and instructively so

The closest thing to a named commercial buyer for work-level live identification is the
performing-rights system, which has to know which *works* were performed live in venues.
It is worth knowing how they actually do it in 2026, and the answer is: by hand.

- **BMI Live**: *"BMI Live allows performing songwriters to input up to six months of their
  performance data to be considered for payment... Performance data may be entered by
  logging on to BMI Online Services from a desktop computer, a supported mobile device or
  through the BMI Mobile app."* Submission deadlines and royalty distribution dates are
  published through 2028. ([bmi.com/live](https://www.bmi.com/live))
- **ASCAP OnStage**: *"STEP 3: You send us some basic details about the performance,
  including your setlist, and you get an OnStage payment with your normal ASCAP
  distribution."* ([ascap.com/onstage](https://www.ascap.com/onstage))

Both are pure manual self-report, in production, today, at the two largest US PROs.

**Read it carefully, because it cuts both ways and the second reading is the important
one.** The naive reading is "here is a huge unautomated market". The correct reading is
that these programmes do not need identification *at all*: the performer knows his own
setlist. What he will not do is type it in. The entire product is a data-entry problem
wearing an identification problem's clothes.

That is the same shape as Objection 2, arrived at from a completely different direction,
and it is the strongest independent support in this brief for the wedge chosen in §5:
**the value is in labelling without effort, not in answering "what is this?"**

(The genuinely open-set live case — a venue with many performers, or an orchestra's
programme — is served today by broadcast-oriented fingerprinters. **Unverified:** whether
BMAT Vericast, DJ Monitor, ACRCloud or Audible Magic claim live or cover-version detection,
and at what accuracy.)

---

## 5. Where the defensible advantage is, ranked honestly

**1. Automatic repertoire logging. "What have I actually played this year."**

This is the best candidate and it is not close.

- It needs open-set work identification, which is the thing nobody ships.
- It requires **zero user effort**, which is the only way a practice log ever survives
  contact with a real practising musician. Every manual practice journal dies in three
  weeks.
- It **accumulates**. Month twelve is worth more than month one, and switching cost grows
  with the archive. That is the only durable moat available here, since neither the
  algorithm (§1.4) nor the input format (§1.3) is defensible.
- The output is a *thing*: a year-in-review, a repertoire list, a heat map of what you
  actually touch versus what you think you practise. That is shareable, and shareable is
  distribution.
- It is supported from an unexpected direction: the performing-rights system's live
  programmes (§4.7) are pure manual data entry for people who already know their setlists.
  The industry's revealed problem is labelling effort, not identification.

**The three honest counts against it, all from the landscape audit (§4.5):**

1. **Tonara got closest to ambient practice detection and is a parked domain.** That is one
   data point, not a verdict, and Tonara's mechanism was much weaker than what is proposed
   here. But it is the only company that tried something adjacent and it did not make it.
2. **The people who most want a practice journal are content to press a button.** Modacity,
   Andante and Soundslice sell manual logging to serious practisers and do no content
   detection at all. If pressing "Practiced?" were the friction, one of them would have
   automated it.
3. **There is no catalog to sell** (Objection 5). Every competitor monetises access to
   music. This product monetises the user's own history. That business model is unproven in
   this category and needs an answer before Stage 4.

It is a vitamin, not a painkiller. Nobody wakes up needing it. The Stage 4 test in §6 is
designed to kill it fast if it is a novelty.

**2. Performance-versus-score diffing and practice analytics.**

Real value, adjacent to a crowded field. The alignment itself is *not* differentiating:
Antescofo has been doing better alignment than anyone since the 2000s, and flowkey/Yousician
do a cruder version at scale. The differentiator would have to be what you *say* after the
alignment, and that is a musicianship problem, not an engineering one.

**3. Expressive-parameter analysis from data nobody else has.**

Release velocity is genuinely unexploited. The local finding (3.684 bits versus 3.140 for
strike velocity, effectively independent, and no commercial synth uses it) is a real,
novel, measured fact and no transcription model can recover it. This is the strongest
*technical* differentiator in the repo.

Its limits are also measured, and they are severe on this instrument: the FP-30X emits zero
aftertouch and zero polyphonic pressure, and the half-damper pedal position is not
transmitted — 769 pedal excursions in 15 minutes, median 15 ms, all transits, never a
sustained partial position. So on this hardware the pedal channel is effectively binary,
and any pedalling analytics claim must be scoped to that. **Check whether this is FP-30X
specific before generalising**; a Yamaha or Kawai with continuous half-damper CC64 would
change the picture. (Unverified.)

**4. Identification as a standalone consumer feature.**

Weakest. It is the enabling primitive under (1) and (2) and should be positioned that way,
never sold on its own, and never sold against Shazam.

### On the market-narrowing objection

"It requires a MIDI instrument, so the market is people at digital pianos, not people in
cafés."

This is a **beachhead, not a fatal narrowing**, but only because transcription is a
commodity (§1.3). The correct architectural response is to define the identifier's input as
a symbolic note stream and to keep the MIDI capture path as one producer among several. A
microphone path via Basic Pitch or the ByteDance model is then a later switch, not a
rewrite, and the acoustic-piano and phone-in-the-room markets stay reachable.

What that costs you: the honest admission that MIDI is not the differentiator. What it
buys you: a v1 with perfect input for debugging the retrieval, and a credible v3 story.

**Unverified:** the size of the installed base of MIDI-capable digital pianos, and of the
piano-learning-app market. No figures were obtained before the web-search budget ran out.
This number matters for the beachhead argument and should be pinned down before any
resourcing decision.

---

## 6. Staged project outline, with falsifiers

Each stage names what must be true, the number that proves it, and the result that kills
the stage. Nothing here is a "review" or a "sign-off"; every stage ends in a measurement.

### Stage 0 — Capture. **Done, and verified.**
C CoreMIDI front end with hardware timestamps, 0 dropped, measured 5.000 ms BLE lattice;
enrichment pipeline accounting for every message with per-take link integrity.
*Open item:* the USB path is expected to reach ~1 ms and is **untested**. Test it, because
Stage 2's timing-ratio features depend on how much of the 5 ms lattice is quantisation noise
in the rhythm ratios.

### Stage 1 — Closed-set identification on clean MIDI.
**Must be true:** clean symbolic input beats noisy transcribed input on the same task.
**Success:** recall@1 ≥ 0.90 on a 10-second clean-MIDI query against a corpus of ≥ 300
works, single query, no pooling. That is Arzt's 15-performance pooled-DB number (0.91)
achieved with one clean query.
**Falsifier:** cannot beat 0.91 at 10 s. Then the clean-input advantage is illusory, the
whole "MIDI removes the hard part" argument is empty, and the project is a
reimplementation of a 2012 paper. Stop, or fold into Stage 6 as analytics only.
**Also report, which Arzt does not:** recall as a function of *note count* rather than
seconds, since a MIDI stream gives exact note counts and a slow nocturne and a fast étude
are not comparable at ten seconds.

### Stage 2 — Open-set rejection. **The stage most likely to kill the product.**
Arzt reports recall@k on a closed set where a correct answer is guaranteed to exist. That
measurement does not exist for the real use case. A repertoire log that confidently labels
your improvising as "Chopin Op. 9 No. 2" is worse than no log at all.
**Must be true:** the system can say "not in the corpus" and "this is improvisation".
**Success:** at the threshold that holds recall ≥ 0.90 on in-corpus queries, false-accept
rate < 5% on a held-out set of improvisations, scales, exercises, and works deliberately
excluded from the corpus.
**Falsifier:** the score distributions do not separate. Then automatic logging is not
shippable and the product must retreat to user-confirmed labelling ("we think this was X,
yes/no"), which is a materially weaker product and should be re-costed as such.

### Stage 3 — Corpus scale.
**Must be true:** precision does not collapse as the index grows. This is the classic
failure mode of n-gram melodic retrieval, and Arzt's own query times already grew from
0.13 s to 6.08 s within a 339-piece corpus.
**Success:** ≥ 5,000 works; recall@1 within 5 points of the Stage 1 number; median query
under 1 second on a laptop.
**Falsifier:** recall drops more than 10 points, or query time grows superlinearly. Then
either the token design needs work (hand back to the melodic-retrieval lane) or the product
scope shrinks to a curated repertoire, which is a legitimate but much smaller product.
*Dependency:* corpus acquisition is owned by another lane. The licensing and encoding
status of a classical symbolic corpus is a first-order risk and is not assessed here.

### Stage 4 — The log as a product. **The stage that tests demand, not technology.**
**Must be true:** people want the artifact enough to leave software running.
**Success:** ship the repertoire journal to 20 pianists. ≥ 10 still have it running
unprompted after 4 weeks, and ≥ 4 name the accumulated history (not the identification
trick) as the reason.
**Falsifier:** they try it once, screenshot it, and stop. Then it is a demo, and Objection 2
was right: the player always knew what he was playing.
**Run this control alongside it**, because it is nearly free and it tests the actual
hypothesis: give ten of the twenty a version where they press a button and type the piece
name, the Modacity/Soundslice model. If the manual cohort retains as well as the automatic
one, effortlessness is not the value, the identifier is not the product, and the entire
technical programme in Stages 1 to 3 was solving the wrong problem.
**Answer before shipping:** what is sold, given there is no catalog (Objection 5).

### Stage 5 — Widen the input.
**Must be true:** the identifier is genuinely input-agnostic and the acoustic market is
reachable.
**Success:** a microphone path (Basic Pitch or the ByteDance model) reaches recall@1 within
10 points of the MIDI path on the same works and the same corpus.
**Falsifier:** transcription noise destroys retrieval even at 96.7% onset F1. That is a
genuinely interesting negative result, it validates the MIDI requirement, and it means the
market really is people at digital pianos. Price and scope accordingly, and say so.

### Stage 6 — Second-order value, only on top of a surviving Stage 4.
Performance-versus-score diffing, sight-reading assessment, teacher-facing tooling, and the
release-velocity expressive analysis from §5.3. Each needs its own falsifier when it is
scoped; none of them should be started before Stage 4 returns a number.

---

## 7. What I could not verify

Stated plainly rather than asserted:

1. **Shazam's current (2026) matching pipeline.** All mechanism claims here rest on Wang
   2003. Apple has published nothing since. Do not argue about present-day Shazam from the
   2003 architecture in any external-facing document.
2. **Classical coverage in Shazam's index.** No public numbers found. The Apple Music
   Classical catalogue size was not obtained either.
3. **Musipedia's operational status** (HTTP 403 to automated fetch).
4. **Peachnote's operational status** (page serves; 2021 copyright; search untested).
5. **Whether SoundHound's hum-to-search still functions**, and whether consumer music ID is
   still a live product line for SoundHound AI.
6. **Any independent measured accuracy for Google Hum to Search**, and in particular any
   measurement on classical or instrumental music. Google publishes none.
7. **Whether any commercial fingerprinter (ACRCloud, Audible Magic, Pex, BMAT Vericast, DJ
   Monitor) claims live-performance or cover-version identification**, and at what accuracy.
   *Partially resolved:* how PROs capture live setlists today **is** now verified, and it is
   manual self-report at both BMI and ASCAP (§4.7). The remaining gap is the B2B vendor
   claims.
8. **The installed base of MIDI-capable digital pianos** and the size of the piano-app
   market.
9. **Whether the missing half-damper CC is FP-30X specific** or general to the price class.
10. **The USB-MIDI timing floor** on this instrument (~1 ms expected, untested).
11. **That no 2024–2026 startup is already doing open-set repertoire identification.** This
    is the weakest negative finding in the brief. The landscape audit reached its conclusions
    by fetching canonical vendor URLs, the iTunes Search API, the Wayback CDX API, the arXiv
    API and Wikipedia, because search was unavailable. A company with a small marketing
    footprint on a domain nobody guessed would not have surfaced. **Re-run discovery with a
    raised search budget before treating "nobody is doing this" as established.**
12. **Pricing** for Yousician, Simply Piano, flowkey, Playground Sessions, Melodics,
    Metronaut and Cadenza (all JS-rendered plan pages).
13. **Christopher Raphael's current involvement** with Cadenza/MetaMusic, and Antescofo's
    current licence terms.
14. **Tonara's shutdown date**, bounded to between 2025-10-15 and 2026-08-19, with no
    announcement found.

### Methodology note

The session's shared web-search budget was exhausted (200/200) early. Items 1–6 and 11–13
are consequences of that. Everything actually asserted in this brief came from primary
sources fetched directly: the Wang and Arzt PDFs, vendor documentation, Apple support pages,
BMI and ASCAP programme pages, Wikipedia, arXiv, the iTunes Search API and Wayback. That is
good for the positive claims and bad for the negative ones. **Treat every "nobody does X" in
this document as provisional; treat every quoted number and sentence as checkable.**

---

## 8. One-paragraph summary

The mechanism half of the thesis is right and has the best possible source: Wang states in
the founding Shazam paper that the algorithm targets recordings already in the database and
"is not expected to generalize to live recordings", and that it will distinguish two
performances that are indistinguishable to the ear. The marketing half is wrong: Shazam
identifies classical recordings fine, and Apple bought Primephonic and BIS and shipped
Apple Music Classical specifically to do it well, so "Shazam can never identify classical
music" will be refuted in one sentence by anyone who has used the app. The true and
narrower claim is about *performances*, not about *classical*. The algorithm being proposed
was published by Arzt, Böck and Widmer in 2012 and evaluated at 0.91 recall@1 on ten-second
queries in 2017 from noisy audio transcription, which makes it prior art and simultaneously
sets a floor that clean MIDI ought to beat. MIDI is not a moat, because piano transcription
from audio now runs at 96.72% onset F1 as free open-source code. What is genuinely
unoccupied is that every listening product in the market is closed-set: the user names the
piece and the software checks him against it, and nothing anywhere takes an unlabelled
stream and tells you what it was, including Antescofo's Metronaut, which has the best
score-following technology in the world and still makes you pick from a catalog of fifteen
thousand. The defensible wedge is therefore not identification, it is the artifact
identification makes possible without any user effort: an automatic repertoire log that
accumulates. Three things would kill it, and all three are cheap to test: open-set rejection
failing (Stage 2), pianists not caring (Stage 4), and the absence of anything to sell them,
since every competitor in the category monetises catalog access and this product has no
catalog. Tonara came nearest to ambient practice detection and its domain is parked;
Modacity and Soundslice sell manual practice logging to the people who care most and have
never bothered to automate it. Test the manual control alongside the automatic one in Stage
4, because if the button is fine, none of the retrieval work was the product.
