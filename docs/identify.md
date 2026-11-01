# Identifying the piece, live

    python -m fp30x_studio.identify watch

Leave it running while you play. It prints nothing until it is sure, and then one purple
line naming the work and how many notes it needed:

    🟣 Scott Joplin, The Strenuous Life [51 notes, 12 s, conf 0.86]

**How it works.** Shazam's trick, moved out of time and into score position. Onsets within
55 ms are one struck event, because a chord reaches us as a ~5 ms arpeggio. Ornament runs
collapse to their principal note, because one Chopin take carried 38 trill runs and every
n-gram that overlaps one is garbage. Three monophonic lines are read off the events -- the
skyline, the bass, and a register-filtered melody that refuses notes far below the running
melodic register, which is what stops the left hand from showing through the gaps in the
tune. Each line is cut into four-interval n-grams: *intervals*, so the hash is invariant
under transposition, and *no durations at all*, so it is invariant under tempo and rubato.
Each n-gram is looked up in an inverted index over the theme corpus and votes for a
`(theme, offset)` pair. A real match piles onto one offset; coincidences scatter.

Then three gates, and all three must pass before it says anything: at least six aligned
hits (nine consecutive intervals), sitting densely on their offset rather than smeared
across the query, and clear of the best rival *work* by enough hits to matter. Below that
the answer is silence, which is the right answer when you are reading a terminal out of
the corner of one eye.

**Cost.** Every layer is incremental and keeps its own cache, so a tick costs the music
played since the last tick and not the music played since you sat down. Measured on the
44-minute session take with a 59,862-theme corpus, ticking every three seconds through the
live path (`python -m fp30x_studio.identify cost <take>`): **18.9 ms of CPU per tick,
0.63% of one core**. The same measurement against the built-in fifteen-theme stub is
1.4 ms and 0.045%. No model is called anywhere in the path; the identification is an
inverted-index lookup and an integer histogram.

**Other commands.** `check <take>` replays a finished take and prints every verdict with
the note count it needed; `cost <take>` measures the live path; `corpus` says what is
loaded. The corpus itself lives in `fp30x_studio/idcorpus/`; if it is missing the
identifier falls back to a small built-in set and says so in its banner.
