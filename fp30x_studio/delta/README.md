# delta

A performance, as the score plus a difference vector.

```
[my performance] = [baseline performance] + [difference vector delta]
```

```
.venv/bin/python -m fp30x_studio.delta fit score.krn take.mid --out d.json --html d.html
.venv/bin/python -m fp30x_studio.delta compare a.json b.json
.venv/bin/python -m fp30x_studio.delta report d.json --html d.html
```

## The definition

Let the score be a finite sequence of notated notes indexed by
$i \in S = \{0,\dots,N-1\}$, note $i$ carrying a pitch $p_i \in \mathbb{Z}$, an
onset $b_i$ in quarter notes and a length $\ell_i$ in quarter notes. Let the
performance be a finite sequence of key presses indexed by
$j \in P = \{0,\dots,M-1\}$, press $j$ carrying a pitch $q_j$, an onset $T_j$ in
seconds, a held length $d_j$ in seconds and a velocity $P_j \in \{1,\dots,127\}$.

The set of operations carrying the page to the playing splits in two, and the
split is not a convenience: the two halves are objects of different kinds and
only one of them is a vector.

### 1. The categorical part

An injective partial map

$$\mu : D \longrightarrow P, \qquad D \subseteq S, \qquad q_{\mu(i)} = p_i ,$$

pairing a notated note with the press that realised it, together with its two
complements: $S \setminus D$, the notes on the page that were not played
(**deletions**), and $P \setminus \mu(D)$, the presses with nothing on the page
under them (**insertions**). This is the edit distance part. It has counts, and
it has bar numbers, and it does not have a direction or a length. Two players
who both leave out the same note have not thereby played alike.

$\mu$ is required to be injective and to preserve pitch exactly. It is not
required to be monotone, because piano playing is not: see
[`match.py`](match.py) for how it is computed and why the monotone stage alone
is not enough.

### 2. The real valued part

The baseline is the score read literally. Fix a tempo $\sigma$ in seconds per
quarter note and an origin $t_0$, and let the baseline play note $i$ at
$\hat T_i = t_0 + \sigma b_i$, for a length $\hat d_i = \sigma \ell_i$, at a
constant velocity $\bar P$. Then $(t_0,\sigma)$ are chosen to minimise the sum of
squared onset residuals over the matched notes,

$$(t_0,\sigma) = \arg\min \sum_{i \in D} \bigl(T_{\mu(i)} - t_0 - \sigma b_i\bigr)^2 ,$$

which is the ordinary least squares regression of performed onset on notated
onset, and $\bar P$ is the mean matched velocity, which minimises the same
criterion for velocity. The fitted $60/\sigma$ is reported as the tempo of the
take.

On the matched notes, three real components, each a function on $D$ and
therefore an element of $\mathbb{R}^D \subseteq \mathbb{R}^S$:

$$r_i = \frac{T_{\mu(i)} - \hat T_i}{\sigma}
  \quad\text{(beats)}, \qquad
  v_i = \frac{P_{\mu(i)}}{127}
  \quad\text{(0 to 1)}, \qquad
  a_i = \log\frac{d_{\mu(i)}}{\sigma \ell_i} .$$

The indexing is by **score** note, and this is the whole point of the
construction. $S$ does not depend on who played, so two performances of one
piece are two vectors in one space, $\mathbb{R}^S$, partially defined, and the
inner product between them is meaningful on $D_A \cap D_B$.

$r$ is dimensionless in beats rather than seconds, so a take at a different
speed is still comparable. $a$ is a log ratio, so held over and detached are
symmetric about zero, and multiplying every length by a constant is a
translation. $v$ is reported raw and centred at $\bar P$; the raw vector is close
to a positive multiple of $\mathbf{1}$ for any pianist and its cosine with
anything is near one, so the centred vector is the one that separates players.

### Rubato and touch

$r$ mixes two phenomena. Convolve it against beat position with a Gaussian of
width $\sigma_b = 2$ quarter notes, in Nadaraya-Watson form because the notes are
not equally spaced,

$$\rho_i = \frac{\sum_j K_{\sigma_b}(b_i - b_j)\, r_j}{\sum_j K_{\sigma_b}(b_i - b_j)},
\qquad m_i = r_i - \rho_i, \qquad r = \rho + m .$$

$\rho$ is the part that persists over a couple of beats, which is what a player
means by rubato, and $m$ is what is left, which is where a note sits inside its
own moment. The decomposition is exact, so nothing is lost by comparing the two
separately, and they should be compared separately: they answer different
questions. The local tempo the rubato curve implies is
$\mathrm{qpm}(b) = 60 / \bigl(\sigma (1 + \rho'(b))\bigr)$, which is what the
report plots.

### Comparing two performances

For each component $c \in \{r, \rho, m, v, v - \bar P, a\}$, restrict to
$D_A \cap D_B$ and take

$$\cos(c^A, c^B) = \frac{\langle c^A, c^B \rangle}{\lVert c^A\rVert\,\lVert c^B\rVert} .$$

The size of $D_A \cap D_B$ is reported next to every cosine, because a cosine
over a fifth of the piece and a cosine over all of it are not the same claim.
The Euclidean norms $\lVert c \rVert$ and their root mean squares are reported
per performance, and those are the answer to how far from the page a
performance is. The categorical part is reported as four counts, insertions and
deletions on each side, and is not folded into any similarity.

## What is measured, and what is not

- **Articulation is measured at the key, not at the ear.** The durations are how
  long the key was down. The sustain pedal is a separate dimension of the
  playing and folding it in would call every pedalled passage legato.
- **A grace note has no notated length**, so $a_i$ does not exist for it. Grace
  notes are matched and carry $r_i$ and $v_i$; they are absent from the
  articulation vector rather than being assigned a value.
- **A written out ornament is an insertion.** Where the page states a trill as a
  symbol and the player realises it as fourteen notes, thirteen of them have
  nothing on the page under them. That is a fact about the notation. The report
  says where these fall, by bar, so they can be read as such.
- **The tempo fit is ordinary least squares**, as specified, not a robust fit.
  One long fermata pulls it. The residual root mean square is reported next to
  the tempo, so a fit that a fermata has bent says so.
- **The transport quantises onsets to a 5 ms lattice**, which is the noise floor
  under every timing number here. See `fp30x_studio/align/events.py`.

## Layout

| File | What it is |
| --- | --- |
| `score.py` | The page as a note table with bars, durations, ties joined |
| `perform.py` | The take as a note table, from `performance.Performance` |
| `cluster.py` | Onset clustering that remembers which notes it folded |
| `match.py` | The categorical part: the three stage matching |
| `fit.py` | The baseline fit and the real valued part |
| `compare.py` | Cosines over the shared notes, counts for the rest |
| `report.py` | The delta as a document with inline SVG charts |

Tests are in `tests/test_delta.py`.
