// Compose: the analysis behind the live score. No DOM here, so Node can test it
// (test.cjs). The page (index.html) owns input, drawing and sound.
//
// A document is the performance, in seconds, and nothing else is authoritative:
//   notes  [{id, t, d, p, v}]   onset and held duration in seconds, MIDI pitch, velocity
//   pedal  [[t, value]]         sustain (CC64) changes
//   beats  [sec, ...] | null    set by Notate; null until then
//   meter  beats per bar once notated (2, 3, 4; or 6, 9, 12 with unit 8)
//   unit   the note value of one beat: 4 (quarter) or 8 (eighth, for x/8 time)
//   keyOverride  key name, or null to use the detected one
// Notation is always *derived* from (notes, beats). Copy, paste and delete of
// bars edit notes and beats together in seconds, so the live view and the score
// stay two readings of one object.
(function (G) {
"use strict";
const TPB = 12; // ticks per beat: divisible by 2, 3 and 4
// An onset this many beats before a beat line belongs to that beat. Quantising
// and bar edits share it, so a note played a hair early moves with its bar.
const EARLY = 0.06;

// ------------------------------------------------------------------ keys
const NAT = [0, 2, 4, 5, 7, 9, 11];
const LETTERS = "CDEFGAB";
const SHARP_ORDER = [3, 0, 4, 1, 5, 2, 6]; // F C G D A E B
const FLAT_ORDER = [6, 2, 5, 1, 4, 0, 3];  // B E A D G C F
const MAJ_NAMES = { "-6": "G♭", "-5": "D♭", "-4": "A♭", "-3": "E♭", "-2": "B♭", "-1": "F", 0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B" };
const MIN_NAMES = { "-6": "E♭", "-5": "B♭", "-4": "F", "-3": "C", "-2": "G", "-1": "D", 0: "A", 1: "E", 2: "B", 3: "F♯", 4: "C♯", 5: "G♯" };

function makeKey(fifths, minor) {
  const majTonic = ((7 * fifths) % 12 + 12) % 12;
  const tonic = minor ? (majTonic + 9) % 12 : majTonic;
  const name = (minor ? MIN_NAMES : MAJ_NAMES)[fifths] + (minor ? " minor" : " major");
  const sig = [0, 0, 0, 0, 0, 0, 0];
  if (fifths > 0) for (let i = 0; i < fifths; i++) sig[SHARP_ORDER[i]] = 1;
  if (fifths < 0) for (let i = 0; i < -fifths; i++) sig[FLAT_ORDER[i]] = -1;
  return { fifths, minor, tonic, name, sig, letter: LETTERS.indexOf(name[0]) };
}
const KEYS = [];
for (let f = -6; f <= 5; f++) { KEYS.push(makeKey(f, false)); KEYS.push(makeKey(f, true)); }
function keyByName(name) { return KEYS.find(k => k.name === name) || null; }

// Krumhansl–Kessler profiles, correlated with duration-weighted pitch classes.
const KK_MAJ = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const KK_MIN = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];
function pearson(a, b) {
  const n = a.length; let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[i]; }
  ma /= n; mb /= n; let s = 0, sa = 0, sb = 0;
  for (let i = 0; i < n; i++) { const x = a[i] - ma, y = b[i] - mb; s += x * y; sa += x * x; sb += y * y; }
  return sa && sb ? s / Math.sqrt(sa * sb) : 0;
}
function detectKey(notes) {
  const w = new Array(12).fill(0);
  for (const n of notes) w[n.p % 12] += Math.min(2, Math.max(0.05, n.d));
  let best = KEYS.find(k => k.name === "C major"), bs = -2;
  for (const k of KEYS) {
    const prof = k.minor ? KK_MIN : KK_MAJ;
    const rot = prof.map((_, i) => prof[(i - k.tonic + 12) % 12]);
    const r = pearson(w, rot);
    if (r > bs) { bs = r; best = k; }
  }
  return best;
}

// Spelling: diatonic pitch classes take the key's letters; the minor leading
// tone is the raised seventh; anything else takes the nearest letter, sharps
// on the sharp side of the circle and flats on the flat side.
function spellPc(pc, key) {
  let cands = [];
  for (let L = 0; L < 7; L++) {
    const a = ((pc - NAT[L] + 18) % 12) - 6;
    if (Math.abs(a) <= 1) cands.push([L, a]);
  }
  for (const [L, a] of cands) if (a === key.sig[L]) return [L, a];
  if (key.minor && pc === (key.tonic + 11) % 12) {
    const L = (key.letter + 6) % 7; return [L, ((pc - NAT[L] + 18) % 12) - 6];
  }
  cands.sort((x, y) => Math.abs(x[1]) - Math.abs(y[1]) || (key.fifths >= 0 ? y[1] - x[1] : x[1] - y[1]));
  return cands[0];
}
// Diatonic step, C4 = 28. B♯3 sits a step below C4's octave boundary, C♭5 above.
function spell(p, key) {
  const [L, a] = spellPc(p % 12, key);
  let oct = Math.floor(p / 12) - 1;
  if (NAT[L] + a < 0) oct += 1; else if (NAT[L] + a > 11) oct -= 1;
  return { L, a, oct, step: oct * 7 + L, name: LETTERS[L] + (a > 0 ? "♯" : a < 0 ? "♭" : "") + oct };
}

// ------------------------------------------------------------------ pedal
function pedalSpans(pedal) {
  const out = []; let s = null;
  for (const [t, v] of [...(pedal || [])].sort((a, b) => a[0] - b[0])) {
    if (v >= 64 && s === null) s = t; else if (v < 64 && s !== null) { out.push([s, t]); s = null; }
  }
  if (s !== null) out.push([s, Infinity]);
  return out;
}
function releaseWithPedal(t, spans) { for (const [a, b] of spans) if (t >= a && t < b) return b; return t; }

// A key brushed while reaching for its neighbour is a fact about the hand.
const isGhost = n => n.v <= 10 && n.d < 0.08;

// ------------------------------------------------------------------ beats
// Ellis (2007) dynamic programme over an onset envelope: beats are the path
// maximising (onset strength at beats) - alpha * sum log(interval/period)^2.
// The period comes from an autocorrelation weighted by a log-normal prior near
// 110 bpm. alpha is the stiffness: rubato within ~20% is absorbed into the beat
// map, which is what lets the score hide it.
function trackBeats(notes, opt = {}) {
  const fr = 0.01;
  const on = notes.filter(n => !isGhost(n)).map(n => [n.t, 0.4 + n.v / 127]).sort((a, b) => a[0] - b[0]);
  const t0 = opt.t0 != null ? opt.t0 : (on.length ? on[0][0] : 0);
  const use = on.filter(o => o[0] >= t0 - 0.02);
  if (use.length < 2) {
    const period = opt.period || 0.6;
    return { beats: [t0, t0 + period], period };
  }
  const tEnd = use[use.length - 1][0];
  const Le = Math.round((tEnd - t0) / fr);
  const raw = new Float64Array(Le + 200);
  for (const [t, w] of use) { const i = Math.max(0, Math.round((t - t0) / fr)); raw[i] += w; }
  const env = new Float64Array(raw.length);
  for (let i = 0; i < raw.length; i++) if (raw[i]) for (let k = -6; k <= 6; k++) {
    const j = i + k; if (j >= 0 && j < env.length) env[j] += raw[i] * Math.exp(-k * k / 8);
  }
  let mx = 0; for (const x of env) mx = Math.max(mx, x); for (let i = 0; i < env.length; i++) env[i] /= mx;

  let P;
  if (opt.period) P = opt.period / fr;
  else if (opt.start) P = opt.start / fr; // a given period that may still bend with the music
  else {
    let bs = -1; P = 60;
    for (let L = 25; L <= 150; L++) {
      let r = 0; for (let i = 0; i + L < env.length; i++) r += env[i] * env[i + L];
      const prior = Math.exp(-0.5 * Math.pow(Math.log2(L * fr / 0.55) / 0.9, 2));
      if (r * prior > bs) { bs = r * prior; P = L; }
    }
    // Continuing a score: same tempo octave as the beat it continues from.
    if (opt.near) { const n = opt.near / fr; P = [P / 2, P, 2 * P].reduce((a, b) => Math.abs(Math.log(b / n)) < Math.abs(Math.log(a / n)) ? b : a); }
  }
  const alpha = opt.alpha || 6;
  const N = Math.min(env.length, Le + Math.round(P) + 1);
  // Pass 1 holds one period; later passes use the local period read off the
  // previous pass (median interval over five beats), so a ritardando is
  // followed instead of fought.
  let Pt = new Float64Array(N).fill(P), frames = null;
  for (let pass = 0; pass < 3; pass++) {
    frames = dpBeats(env, N, Pt, alpha, Le);
    if (frames.length < 4 || opt.period) break;
    const iv = frames.slice(1).map((f, i) => f - frames[i]);
    const loc = iv.map((_, i) => { const w = iv.slice(Math.max(0, i - 2), i + 3).sort((a, b) => a - b); return w[w.length >> 1]; });
    Pt = new Float64Array(N);
    // Within a ritardando's reach of the global period, never a different octave of it.
    for (let t = 0, j = 0; t < N; t++) { while (j < loc.length - 1 && frames[j + 1] <= t) j++; Pt[t] = Math.min(1.45 * P, Math.max(P / 1.45, loc[j])); }
  }
  return { beats: frames.map(f => t0 + f * fr), period: P * fr };
}
function dpBeats(env, N, Pt, alpha, Le) {
  const C = new Float64Array(N).fill(-Infinity), B = new Int32Array(N).fill(-1);
  C[0] = env[0];
  for (let t = 1; t < N; t++) {
    const P = Pt[t], lo = Math.max(0, t - Math.round(2 * P)), hi = t - Math.round(P / 2);
    let best = -Infinity, arg = -1;
    for (let tau = lo; tau <= hi; tau++) {
      if (C[tau] === -Infinity) continue;
      const x = Math.log((t - tau) / P), s = C[tau] - alpha * x * x;
      if (s > best) { best = s; arg = tau; }
    }
    if (arg >= 0) { C[t] = env[t] + best; B[t] = arg; }
  }
  let end = -1, be = -Infinity;
  const win = Math.round(Pt[Math.min(N - 1, Le)] * 0.25);
  for (let t = Math.max(0, Le - win); t <= Math.min(N - 1, Le + win); t++) if (C[t] > be) { be = C[t]; end = t; }
  if (end < 0) for (let t = 0; t < N; t++) if (C[t] > be) { be = C[t]; end = t; }
  const frames = []; for (let t = end; t >= 0; t = B[t]) { frames.push(t); if (t === 0) break; }
  return frames.reverse();
}

function extendBeats(beats, until) {
  const b = beats.slice();
  if (b.length < 2) b.push(b[0] + 0.6);
  const k = Math.min(4, b.length - 1);
  const iv = (b[b.length - 1] - b[b.length - 1 - k]) / k;
  while (b[b.length - 1] < until) b.push(b[b.length - 1] + iv);
  return b;
}

// Seconds <-> beats through the beat map, linear past either end.
function secToBeat(t, beats) {
  const n = beats.length;
  if (t <= beats[0]) return (t - beats[0]) / (beats[1] - beats[0]);
  if (t >= beats[n - 1]) return n - 1 + (t - beats[n - 1]) / (beats[n - 1] - beats[n - 2]);
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (beats[m] <= t) lo = m; else hi = m; }
  return lo + (t - beats[lo]) / (beats[lo + 1] - beats[lo]);
}
function beatToSec(b, beats) {
  const n = beats.length;
  if (b <= 0) return beats[0] + b * (beats[1] - beats[0]);
  if (b >= n - 1) return beats[n - 1] + (b - (n - 1)) * (beats[n - 1] - beats[n - 2]);
  const i = Math.floor(b); return beats[i] + (b - i) * (beats[i + 1] - beats[i]);
}

// ------------------------------------------------------------------ quantising
// Each beat takes the coarsest subdivision (1, 2, 3, 4) that fits its onsets
// within tolerance, so a plain beat is not written as four sixteenths and a
// triplet is not forced into duplets.
function quantise(notes, beats, pedal) {
  const spans = pedalSpans(pedal);
  const live = notes.filter(n => !isGhost(n));
  // Notes struck within 40 ms of each other are one onset, placed at their median: a chord
  // rolled by a few milliseconds must not straddle a beat line.
  const at = new Map(), byT = live.slice().sort((a, b) => a.t - b.t);
  for (let i = 0; i < byT.length;) {
    let j = i + 1; while (j < byT.length && byT[j].t - byT[j - 1].t <= 0.04 && byT[j].t - byT[i].t < 0.08) j++;
    const tm = byT[(i + j - 1) >> 1].t; for (let k = i; k < j; k++) at.set(byT[k], tm); i = j;
  }
  const per = new Map(); // beat index -> [frac]
  const q = live.map(n => {
    const fb = secToBeat(at.get(n), beats);
    // Early by a fraction of a beat, or by 40 ms, whichever is more: at a fast
    // eighth, 6% of a beat is closer than a chord's own spread.
    const j0 = Math.max(0, Math.min(beats.length - 2, Math.floor(fb))), sec = beats[j0 + 1] - beats[j0];
    let i = Math.floor(fb + Math.max(EARLY, 0.04 / sec)), f = fb - i;
    const fk = secToBeat(n.t + n.d, beats), fp = secToBeat(releaseWithPedal(n.t + n.d, spans), beats);
    const rec = { n, i, f, fe: Number.isFinite(fp) ? fp : fk, fk }; // a pedal never lifted does not hold forever
    if (!per.has(i)) per.set(i, []); per.get(i).push(f);
    return rec;
  });
  const sub = new Map();
  for (const [i, fs] of per) {
    let pick = 0, perr = Infinity;
    // Tolerance: 9% of a beat or 45 ms, whichever is more, short of the grid's half-step.
    const k = Math.max(0, Math.min(beats.length - 2, i)), sec = beats[k + 1] - beats[k];
    for (const s of [1, 2, 3, 4]) {
      let e = 0; for (const f of fs) e = Math.max(e, Math.abs(f - Math.round(f * s) / s));
      if (e <= Math.min(0.4 / s, Math.max(0.09, 0.045 / sec))) { pick = s; break; }
      if (s >= 3 && e < perr) { perr = e; pick = s; }
    }
    sub.set(i, pick);
  }
  const subAt = i => sub.get(i) || 2;
  const snap = b => { const i = Math.floor(b), s = subAt(i); return Math.round(i * TPB + Math.round((b - i) * s) * TPB / s); };
  for (const r of q) {
    const s = subAt(r.i);
    r.on = r.i * TPB + Math.round(r.f * s) * TPB / s;
    r.off = snap(r.fe); r.offKey = snap(r.fk);
  }
  return { q, subAt, sub };
}

// ------------------------------------------------------------------ meter
// Accent per beat: what starts on the beat line, weighted by length, bass and loudness.
function beatWeights(q, n, cap = 2) {
  const w = new Float64Array(n);
  for (const r of q) {
    if (r.on % TPB) continue;
    const b = r.on / TPB; if (b < 0 || b >= n) continue;
    const dur = Math.min(cap, Math.max(0.25, (r.offKey - r.on) / TPB));
    w[b] += (dur + (r.n.p < 55 ? 1 : 0)) * (0.5 + r.n.v / 127);
  }
  return w;
}
// Beats per bar, by vote over windows of 48 beats, each window free to find its
// own downbeat: over a long take one slipped beat would blur a single global fit.
const METERS = [[4, 1.06], [3, 1.0], [2, 0.92], [6, 1.0]];
function chooseMeter(w, cands = METERS) {
  const n = w.length, W = Math.min(48, n), votes = new Map();
  if (n < 4) return cands[0][0];
  for (let s = 0; s + W <= n; s += Math.max(1, W >> 1)) {
    let mean = 0; for (let i = s; i < s + W; i++) mean += w[i]; mean /= W; if (!mean) continue;
    let best = null, bs = -1;
    for (const [k, pr] of cands) for (let ph = 0; ph < k; ph++) {
      let t = 0, c = 0; for (let i = s + ph; i < s + W; i += k) { t += w[i]; c++; }
      const sc = t / c / mean * pr; if (sc > bs) { bs = sc; best = k; }
    }
    votes.set(best, (votes.get(best) || 0) + 1);
  }
  let best = cands[0][0], bv = -1;
  for (const [k] of cands) if ((votes.get(k) || 0) > bv) { bv = votes.get(k) || 0; best = k; }
  return best;
}
// Downbeats: a dynamic programme over each beat's place in the bar, rewarded by
// accent on strong places and charged for any break in the count. A break is
// a beat the tracker added or lost; it is repaired in the beat map (a beat
// removed, or a long interval split), so every bar keeps its length.
const TEMPLATE = { 2: [1, 0], 3: [1, 0, 0], 4: [1, 0, .5, 0], 6: [1, 0, 0, .5, 0, 0],
  9: [1, 0, 0, .5, 0, 0, .5, 0, 0], 12: [1, 0, 0, .5, 0, 0, .7, 0, 0, .5, 0, 0] };
const JUMP = 4;
function alignBars(beats, w, k, onsets = []) {
  const n = Math.min(beats.length, w.length), T = TEMPLATE[k] || [1, ...Array(k - 1).fill(0)];
  let mean = 0; for (let i = 0; i < n; i++) mean += w[i]; mean = mean / Math.max(1, n) || 1;
  if (n < 2) return { beats: beats.slice(), phase: 0, edits: 0 };
  const S = [], back = [];
  S[0] = T.map((t, p) => t * w[0] / mean + (p === 0 ? 0.5 : 0)); back[0] = T.map(() => -1); // most pieces start on a downbeat
  for (let i = 1; i < n; i++) {
    const prev = S[i - 1]; let am = 0; for (let p = 1; p < k; p++) if (prev[p] > prev[am]) am = p;
    S[i] = []; back[i] = [];
    for (let p = 0; p < k; p++) {
      const pp = (p - 1 + k) % k, stay = prev[pp], jump = prev[am] - JUMP;
      const [v, from] = stay >= jump ? [stay, pp] : [jump, am];
      S[i][p] = v + T[p] * w[i] / mean; back[i][p] = from;
    }
  }
  const pos = new Array(n); let p = 0; for (let q = 1; q < k; q++) if (S[n - 1][q] > S[n - 1][p]) p = q;
  for (let i = n - 1; i >= 0; i--) { pos[i] = p; p = back[i][p]; }
  const B = beats.slice(), Wt = Array.from(w).slice(0, B.length); let edits = 0;
  while (Wt.length < B.length) Wt.push(0);
  for (let i = n - 1; i >= 1; i--) {
    const d = (pos[i] - pos[i - 1] - 1 + k) % k; if (!d) continue;
    // The repair goes where it leaves the nearby onsets closest to the beat
    // grid (halves of a beat), so a beat squeezed into a ritardando is the one removed.
    const lo = Math.max(1, i - 2 * k), win = [B[Math.max(0, lo - 1)], B[Math.min(B.length - 1, i + 2 * k)]];
    const near = onsets.filter(t => t >= win[0] && t < win[1]);
    const err = BB => { let e = 0; for (const t of near) { const f = secToBeat(t, BB) * 2; e += (f - Math.round(f)) ** 2; } return e; };
    // ...and inside the silent run of beats the break falls in: any beat in it
    // can go, but a beat with an onset on it was already counted by its side.
    let from = i, to = i;
    while (from > 1 && from > i - 2 * k && !(Wt[from - 1] > 0)) from--;
    while (to < B.length - 1 && to < i + 2 * k && !(Wt[to] > 0)) to++;
    if (from >= to) { from = Math.max(1, i - 2 * k); to = i; }
    for (let c = 0; c < (d <= k / 2 ? d : k - d); c++) {
      let best = null, be = Infinity;
      if (d <= k / 2) for (let j = from; j <= to; j++) {
        const BB = B.slice(); BB.splice(j, 0, (B[j] + B[j - 1]) / 2); const e = err(BB); if (e < be) { be = e; best = [j, BB]; }
      } else for (let j = from; j < to; j++) {
        const BB = B.slice(); BB.splice(j, 1); const e = err(BB) + 2 * Wt[j] / mean; if (e < be) { be = e; best = [j, BB]; }
      }
      if (!best) break;
      const [j, BB] = best; B.length = 0; B.push(...BB);
      if (d <= k / 2) { Wt.splice(j, 0, 0); to++; } else { Wt.splice(j, 1); to--; }
    }
    edits++;
  }
  return { beats: B, phase: (k - pos[0]) % k, edits };
}
// Divide each beat into f. With onsets given, every line moves onto the
// onsets played near it, so the grid follows the timing inside the beat.
function splitBeats(b, f, onsets) {
  const out = [];
  for (let i = 0; i < b.length - 1; i++) for (let j = 0; j < f; j++) {
    let t = b[i] + (b[i + 1] - b[i]) * j / f;
    if (onsets) {
      const r = 0.3 * (b[i + 1] - b[i]) / f, near = onsets.filter(o => Math.abs(o - t) < r);
      if (near.length) t = out.length ? near[near.length >> 1] : near[0]; // nothing may fall before the first line
    }
    const nominal = b[i] + (b[i + 1] - b[i]) * j / f, step = (b[i + 1] - b[i]) / f;
    if (out.length && t <= out[out.length - 1] + 0.5 * step) t = Math.max(nominal, out[out.length - 1] + 0.5 * step);
    out.push(t);
  }
  out.push(b[b.length - 1]); return out;
}
const unitOf = meter => meter % 3 === 0 && meter > 3 ? 8 : 4;
// Put bar lines on beats: downbeats from the accents, then beats prepended so a
// pickup sits in a first bar that starts with rests.
function barLines(notes, beats, pedal, meter) {
  const end = Math.max(...notes.map(n => n.t + n.d));
  let b = extendBeats(beats, end + 0.01);
  const { q } = quantise(notes, b, pedal);
  const on = notes.map(n => n.t).sort((x, y) => x - y);
  const al = alignBars(b, beatWeights(q, b.length, meter), meter, on); // a note held a whole bar marks its start
  b = al.beats;
  if (al.phase) {
    const iv = b[1] - b[0], pre = [];
    for (let i = meter - al.phase; i >= 1; i--) pre.push(b[0] - i * iv);
    b = pre.concat(b);
  }
  return b;
}
// Kept for callers that want the old answer shape.
function detectMeter(q) {
  let n = 0; for (const r of q) if (r.on % TPB === 0) n = Math.max(n, r.on / TPB + 1);
  return { meter: chooseMeter(beatWeights(q, n)), phase: 0 };
}

// ------------------------------------------------------------------ notate
// First Notate: find beats, meter, key. Returns the fields to merge into the doc.
function detect(doc) {
  const notes = doc.notes.filter(n => !isGhost(n));
  if (!notes.length) return null;
  const { beats } = trackBeats(notes);
  const end = Math.max(...notes.map(n => n.t + n.d));
  let b = extendBeats(beats, end + 0.01), cands = METERS;
  let { q, sub } = quantise(notes, b, doc.pedal);
  // Beats that mostly divide in three are dotted quarters: count eighths instead.
  let n3 = 0, n24 = 0; for (const s of sub.values()) { if (s === 3) n3++; else if (s === 2 || s === 4) n24++; }
  const on = notes.map(n => n.t).sort((x, y) => x - y);
  if (n3 >= 8 && n3 >= 1.5 * n24) { b = splitBeats(b, 3, on); q = quantise(notes, b, doc.pedal).q; cands = [[6, 1], [9, .9]]; }
  const meter = chooseMeter(beatWeights(q, b.length), cands);
  // Compound time counted in eighths: track the dotted quarter, which a long
  // held note cannot hide, and count three eighths in each.
  if (unitOf(meter) === 8 && cands === METERS) {
    const P = (b[b.length - 1] - b[0]) / (b.length - 1);
    b = extendBeats(splitBeats(trackBeats(notes, { start: 3 * P }).beats, 3, on), end + 0.01);
  }
  return { beats: barLines(notes, b, doc.pedal, meter), meter, unit: unitOf(meter), key: detectKey(notes).name };
}
// A time signature chosen by hand: the beats stay, the downbeats are found again.
function setMeter(doc, meter, unit) {
  const notes = doc.notes.filter(n => !isGhost(n));
  doc.meter = meter; doc.unit = unit;
  if (!notes.length || !doc.beats) return;
  const first = Math.min(...notes.map(n => n.t));
  let b = doc.beats.slice();
  while (b.length > 2 && b[1] - first <= EARLY * (b[1] - b[0])) b.shift(); // drop the pickup padding
  doc.beats = barLines(notes, b, doc.pedal, meter);
}

// Re-fit only what lies past the last beat (after Write added notes there),
// seeded with the last beat and the current local period.
function extendTracking(doc) {
  const b = doc.beats, last = b[b.length - 1];
  const after = doc.notes.filter(n => n.t > last - 0.02);
  if (after.length < 2) return extendBeats(b, Math.max(...doc.notes.map(n => n.t + n.d)) + 0.01);
  const period = (b[b.length - 1] - b[b.length - 5 >= 0 ? b.length - 5 : 0]) / Math.min(4, b.length - 1);
  // With enough new notes the passage gets its own tempo; with a few, the old one.
  const tr = trackBeats(after, after.length >= 4 ? { t0: last, near: period } : { t0: last, period, alpha: 10 });
  const nb = b.concat(tr.beats.slice(1));
  return extendBeats(nb, Math.max(...doc.notes.map(n => n.t + n.d)) + 0.01);
}

function currentKey(doc) {
  return keyByName(doc.keyOverride || doc.key) || detectKey(doc.notes);
}

const VALUES = { 48: ["w", 0], 36: ["h", 1], 24: ["h", 0], 18: ["q", 1], 12: ["q", 0], 9: ["e", 1], 6: ["e", 0], 3: ["s", 0], 8: ["q", 0, 1], 4: ["e", 0, 1] };
// With the eighth as the beat ("t" is a thirty-second).
const VALUES8 = { 72: ["h", 1], 48: ["h", 0], 36: ["q", 1], 24: ["q", 0], 18: ["e", 1], 12: ["e", 0], 9: ["s", 1], 6: ["s", 0], 3: ["t", 0], 8: ["e", 0, 1], 4: ["s", 0, 1] };

// Split [a, b) into notatable pieces: whole beats from a beat line, otherwise
// pieces inside one beat on that beat's own grid. Never crosses a barline.
// In x/8 time the beat groups are dotted quarters (36 ticks): a whole-beat
// piece never crosses one unless it starts on it.
function pieces(a, b, barStart, barLen, subAt, unit = 4) {
  const V = unit === 8 ? VALUES8 : VALUES, grouped = unit === 8 && barLen % 36 === 0;
  const out = [], barEnd = barStart + barLen;
  while (a < b) {
    const bi = Math.floor(a / TPB), bs = bi * TPB, inBeat = a - bs;
    let v;
    if (inBeat === 0 && b - a >= TPB) {
      const room = Math.min(b, barEnd) - a, pos = a - barStart, g = pos % 36;
      if (grouped) v = [72, 36, 24, 12].find(x => x <= room && (x === 72 ? pos % 72 === 0 : x === 36 ? g === 0 : x === 24 ? g + 24 <= 36 : true)) || 12;
      else v = [48, 36, 24, 18, 12].find(x => x <= room && (x !== 36 || (a - barStart) % 24 === 0 || barLen === 36)) || 12;
    } else {
      const len = Math.min(b, bs + TPB) - a;
      if (subAt(bi) === 3) v = len >= 8 && inBeat % 4 === 0 ? 8 : 4;
      else v = len >= 9 && inBeat === 0 ? 9 : len >= 6 && inBeat % 6 === 0 ? 6 : 3;
      v = Math.min(v, len);
    }
    const [base, dots, tri] = V[v] || [unit === 8 ? "t" : "s", 0, 0];
    out.push({ tick: a, len: v, base, dots, tri: !!tri });
    a += v;
  }
  return out;
}

// The score: bars of events for two staves (0 treble, 1 bass, split at C4).
// A chord lasts until that staff's next onset, unless it was let go at least
// half a beat earlier (then the gap is a rest), so legato playing is not
// riddled with short rests and the pedal does not smear across chords.
function notate(doc) {
  if (!doc.beats) return null;
  const key = currentKey(doc), meter = doc.meter || 4, unit = doc.unit || 4, barLen = meter * TPB;
  const notes = doc.notes.filter(n => !isGhost(n));
  const end = notes.length ? Math.max(...notes.map(n => n.t + n.d)) : doc.beats[0];
  const beats = extendBeats(doc.beats, end + 0.01);
  const { q, subAt } = quantise(notes, beats, doc.pedal);
  let lastTick = 0;
  const staves = [[], []];
  for (const s of [0, 1]) {
    const mine = q.filter(r => (r.n.p >= 60 ? 0 : 1) === s).sort((a, b) => a.on - b.on || a.n.p - b.n.p);
    const chords = [];
    for (const r of mine) {
      const c = chords[chords.length - 1];
      if (c && c.on === r.on) { if (!c.ps.has(r.n.p)) { c.ps.add(r.n.p); c.rs.push(r); } }
      else chords.push({ on: r.on, rs: [r], ps: new Set([r.n.p]) });
    }
    chords.forEach((c, i) => {
      const next = i + 1 < chords.length ? chords[i + 1].on : Infinity;
      // The last chord ends where the keys came up; the pedal's ring-out is not written.
      const rel = Math.max(...c.rs.map(r => next === Infinity ? r.offKey : r.off));
      const unit = TPB / subAt(Math.floor(c.on / TPB));
      let e = rel >= next - TPB / 2 ? next : rel;
      // The piece's last chord, let go within half a beat of a barline, is held to it.
      // Held on past one (a fermata, a final ring) by under a third of a bar, it stops at that one.
      if (next === Infinity) { const bl = Math.ceil(rel / barLen) * barLen, bp = bl - barLen; e = bl - rel <= TPB / 2 ? bl : bp > c.on && rel - bp <= barLen / 3 ? bp : rel; }
      c.end = Math.min(next, Math.max(e, c.on + unit));
      lastTick = Math.max(lastTick, c.end);
    });
    staves[s] = chords;
  }
  const nBars = Math.max(1, Math.ceil(lastTick / barLen));
  const bars = [];
  for (let m = 0; m < nBars; m++) bars.push({ index: m, start: m * barLen, len: barLen, unit, staves: [[], []] });
  for (const s of [0, 1]) {
    const chords = staves[s];
    let cursor = 0;
    const put = (a, b, chord) => {
      while (a < b) {
        const m = Math.floor(a / barLen); if (m >= nBars) return;
        const segEnd = Math.min(b, (m + 1) * barLen);
        const ps = pieces(a, segEnd, m * barLen, barLen, subAt, unit);
        ps.forEach((pc, j) => {
          pc.rest = !chord;
          if (chord) {
            const cont = a > chord.on || j > 0, more = j < ps.length - 1 || segEnd < b;
            pc.notes = [...chord.rs].sort((x, y) => x.n.p - y.n.p).map(r => ({ p: r.n.p, v: r.n.v, id: r.n.id, ...spell(r.n.p, key), tieNext: more, tied: cont }));
          }
          bars[m].staves[s].push(pc);
        });
        a = segEnd;
      }
    };
    for (const c of chords) {
      if (c.on > cursor) put(cursor, c.on, null);
      put(c.on, c.end, c);
      cursor = c.end;
    }
    if (cursor < nBars * barLen) put(cursor, nBars * barLen, null);
  }
  // A bar with nothing sounding on a staff is one whole-bar rest.
  for (const bar of bars) for (const s of [0, 1]) {
    if (bar.staves[s].every(e => e.rest)) bar.staves[s] = [{ tick: bar.start, len: barLen, base: "w", dots: 0, tri: false, rest: true, wholeBar: true }];
  }
  // Accidentals: shown when the letter's alteration differs from what the key
  // signature, or an earlier note on that line in this bar, has set.
  for (const bar of bars) for (const s of [0, 1]) {
    const state = new Map();
    for (const e of bar.staves[s]) if (!e.rest) for (const n of e.notes) {
      const k = n.L + 7 * n.oct, cur = state.has(k) ? state.get(k) : key.sig[n.L];
      n.acc = n.tied && e.tick !== bar.start ? null : n.a !== cur ? n.a : null;
      if (n.tied && e.tick === bar.start) n.acc = null;
      state.set(k, n.a);
    }
  }
  const ivs = []; for (let i = 1; i < beats.length; i++) ivs.push(beats[i] - beats[i - 1]);
  ivs.sort((a, b) => a - b);
  const bpm = 60 / ivs[ivs.length >> 1];
  return { key, meter, unit, bpm, bars, beats, barLen, subAt, nBars };
}

// ------------------------------------------------------------------ bar edits
// All in seconds, on notes, pedal and beats together.
function ensureBars(doc, m) {
  const need = (m + 1) * doc.meter;
  while (doc.beats.length <= need) doc.beats = extendBeats(doc.beats, doc.beats[doc.beats.length - 1] + 0.001);
}
// Which side of beat line b a note falls on: the side its written onset is on,
// so an edit can never split a bar differently from how the score shows it.
function onsetTicks(doc) {
  const end = doc.notes.reduce((e, n) => Math.max(e, n.t + n.d), 0);
  const { q } = quantise(doc.notes, extendBeats(doc.beats, end + 0.01), doc.pedal);
  const m = new Map(q.map(r => [r.n, r.on]));
  return n => m.has(n) ? m.get(n) : Math.floor(secToBeat(n.t, doc.beats) + EARLY) * TPB;
}
function copyBars(doc, m1, m2) {
  ensureBars(doc, m2 + 1);
  const k = doc.meter, A = doc.beats[m1 * k], B = doc.beats[(m2 + 1) * k];
  return {
    bars: m2 - m1 + 1, meter: k, unit: doc.unit || 4, len: B - A,
    beats: doc.beats.slice(m1 * k, (m2 + 1) * k).map(t => t - A),
    notes: (tk => doc.notes.filter(n => tk(n) >= m1 * k * TPB && tk(n) < (m2 + 1) * k * TPB))(onsetTicks(doc)).map(n => ({ ...n, t: n.t - A })),
    pedal: (doc.pedal || []).filter(([t]) => t >= A && t < B).map(([t, v]) => [t - A, v]),
  };
}
let idSeq = 1;
function newId() { return "n" + Date.now().toString(36) + (idSeq++).toString(36); }
function pasteBars(doc, clip, c) {
  ensureBars(doc, c + 1);
  const k = doc.meter, C = doc.beats[c * k], D = clip.len;
  const tk = onsetTicks(doc), moves = doc.notes.map(n => tk(n) >= c * k * TPB);
  doc.notes.forEach((n, i) => { if (moves[i]) n.t += D; });
  doc.pedal = (doc.pedal || []).map(([t, v]) => [t >= C ? t + D : t, v]);
  for (const n of clip.notes) doc.notes.push({ ...n, id: newId(), t: n.t + C });
  for (const [t, v] of clip.pedal) doc.pedal.push([t + C, v]);
  doc.notes.sort((a, b) => a.t - b.t || a.p - b.p);
  doc.pedal.sort((a, b) => a[0] - b[0]);
  doc.beats = doc.beats.slice(0, c * k).concat(clip.beats.map(t => t + C), doc.beats.slice(c * k).map(t => t + D));
}
function deleteBars(doc, m1, m2) {
  ensureBars(doc, m2 + 1);
  const k = doc.meter, A = doc.beats[m1 * k], B = doc.beats[(m2 + 1) * k], D = B - A;
  const tk = onsetTicks(doc), side = new Map(doc.notes.map(n => [n, tk(n) < m1 * k * TPB ? 0 : tk(n) < (m2 + 1) * k * TPB ? 1 : 2]));
  doc.notes = doc.notes.filter(n => side.get(n) !== 1).map(n => side.get(n) === 2 ? { ...n, t: n.t - D } : n);
  doc.pedal = (doc.pedal || []).filter(([t]) => t < A || t >= B).map(([t, v]) => [t >= B ? t - D : t, v]);
  doc.beats = doc.beats.slice(0, m1 * k).concat(doc.beats.slice((m2 + 1) * k).map(t => t - D));
}
// Double or halve the beat, keeping the bar lines where they are when halving.
function scaleTempo(doc, factor) {
  const b = doc.beats;
  if (factor === 2) { const nb = []; for (let i = 0; i < b.length - 1; i++) nb.push(b[i], (b[i] + b[i + 1]) / 2); nb.push(b[b.length - 1]); doc.beats = nb; }
  else if (b.length > 3) doc.beats = b.filter((_, i) => i % 2 === 0);
}

// ------------------------------------------------------------------ playback
// The score as it reads: every event at a steady tempo, ties merged.
function scoreEvents(sc, fromBar) {
  const sec = 60 / sc.bpm / TPB, t0 = fromBar * sc.barLen, out = [];
  for (const s of [0, 1]) {
    const open = new Map();
    for (const bar of sc.bars) for (const e of bar.staves[s]) {
      if (e.rest || e.tick < t0) continue;
      for (const n of e.notes) {
        if (n.tied && open.has(n.p)) open.get(n.p).d += e.len * sec;
        else { const x = { t: (e.tick - t0) * sec, d: e.len * sec, p: n.p, v: n.v }; out.push(x); }
        if (n.tieNext) open.set(n.p, out.find(x => x.p === n.p && Math.abs(x.t + x.d - (e.tick + e.len - t0) * sec) < 1e-6) || out[out.length - 1]);
        else open.delete(n.p);
      }
    }
  }
  return out.sort((a, b) => a.t - b.t);
}

// ------------------------------------------------------------------ MIDI file
function smf(events, bpm, meter, unit = 4) {
  const qbpm = unit === 8 ? bpm / 2 : bpm, PPQ = 480, tps = PPQ * qbpm / 60;
  const ev = [];
  for (const e of events) { ev.push([Math.round(e.t * tps), 0x90, e.p, Math.max(1, e.v)]); ev.push([Math.round((e.t + e.d) * tps), 0x80, e.p, 0]); }
  for (const [t, v] of events.pedal || []) ev.push([Math.round(t * tps), 0xB0, 64, v]);
  ev.sort((a, b) => a[0] - b[0] || (a[1] === 0x80 ? -1 : 1));
  const bytes = [];
  const vlq = n => { const s = [n & 0x7f]; while ((n >>= 7)) s.unshift((n & 0x7f) | 0x80); return s; };
  const us = Math.round(60e6 / qbpm);
  bytes.push(0, 0xFF, 0x51, 3, (us >> 16) & 255, (us >> 8) & 255, us & 255);
  if (meter) bytes.push(0, 0xFF, 0x58, 4, meter, unit === 8 ? 3 : 2, unit === 8 && meter % 3 === 0 ? 36 : 24, 8);
  let last = 0;
  for (const [t, st, a, b] of ev) { bytes.push(...vlq(t - last), st, a, b); last = t; }
  bytes.push(0, 0xFF, 0x2F, 0);
  const u32 = n => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
  return new Uint8Array([0x4d, 0x54, 0x68, 0x64, ...u32(6), 0, 0, 0, 1, PPQ >> 8, PPQ & 255,
    0x4d, 0x54, 0x72, 0x6b, ...u32(bytes.length), ...bytes]);
}

// ------------------------------------------------------------------ demo
// An original eight-bar phrase in D major, 4/4, played at 92 bpm with a
// breathing tempo, a ritardando into the last two bars, ±12 ms of onset
// scatter on the 5 ms Bluetooth grid, legato releases and a pedal per bar.
// It exists so the page can be tried, and the analysis tested, without a piano.
const DEMO_SCORE = [
  // treble [pitch, tick, len]
  [69, 0, 12], [66, 12, 6], [67, 18, 6], [69, 24, 12], [74, 36, 12],
  [73, 48, 18], [71, 66, 6], [69, 72, 24],
  [71, 96, 12], [67, 108, 12], [64, 120, 6], [66, 126, 6], [67, 132, 12],
  [66, 144, 24], [64, 168, 12],
  [62, 192, 4], [64, 196, 4], [66, 200, 4], [67, 204, 12], [69, 216, 12], [71, 228, 12],
  [69, 240, 36],
  [74, 288, 12], [73, 300, 12], [71, 312, 12], [73, 324, 12],
  [74, 336, 48],
  // bass
  [50, 0, 24], [45, 24, 24], [45, 48, 24], [49, 72, 24], [43, 96, 24], [40, 120, 24],
  [38, 144, 24], [45, 168, 24], [47, 192, 24], [43, 216, 24], [45, 240, 48],
  [43, 288, 24], [45, 312, 24], [38, 336, 48], [54, 336, 48], [57, 336, 48],
];
function demoDoc(seed = 7) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const gauss = () => { let u = 0; for (let i = 0; i < 6; i++) u += rnd(); return (u - 3) / Math.sqrt(0.5); };
  const P = 60 / 92, beatTimes = [0];
  for (let b = 0; b < 40; b++) {
    const rit = b >= 24 ? Math.min(1.35, 1 + 0.04 * (b - 23)) : 1;
    beatTimes.push(beatTimes[b] + P * (1 + 0.08 * Math.sin(2 * Math.PI * b / 8)) * rit);
  }
  const at = tick => { const b = tick / TPB, i = Math.floor(b); return beatTimes[i] + (b - i) * (beatTimes[i + 1] - beatTimes[i]); };
  const grid = t => Math.round(t / 0.005) * 0.005;
  const notes = DEMO_SCORE.map(([p, tk, len], i) => {
    const t = grid(1 + at(tk) + 0.012 * gauss()), e = 1 + at(tk + len) - (p < 60 ? 0.06 : 0.04) - 0.02 * rnd();
    return { id: "d" + i, t, d: Math.max(0.05, e - t), p, v: Math.round((p < 60 ? 55 : 72) + 8 * gauss()) };
  }).sort((a, b) => a.t - b.t);
  const pedal = [];
  for (let m = 0; m < 9; m++) { pedal.push([1 + at(m * 48) + 0.06, 100]); pedal.push([1 + at((m + 1) * 48) - 0.03, 0]); }
  return { format: "compose", version: 1, notes, pedal, beats: null, meter: null, key: null, keyOverride: null, truth: DEMO_SCORE };
}

const api = { TPB, KEYS, keyByName, detectKey, spell, spellPc, pedalSpans, releaseWithPedal, isGhost,
  trackBeats, extendBeats, secToBeat, beatToSec, quantise, detectMeter, detect, setMeter, chooseMeter, alignBars, beatWeights, splitBeats, unitOf, extendTracking, currentKey,
  notate, pieces, copyBars, pasteBars, deleteBars, ensureBars, scaleTempo, scoreEvents, smf, demoDoc, newId };
G.ComposeCore = api;
if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
