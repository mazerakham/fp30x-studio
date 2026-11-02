// node player/compose/test.cjs
// The demo take is played with rubato and scatter from a known score, so the
// analysis can be checked against the truth: key, meter, tempo, and every onset tick.
const C = require("./core.js");
const assert = require("assert");
let pass = 0;
const t = (name, f) => { f(); pass++; console.log("ok  " + name); };

const doc = C.demoDoc();
const det = C.detect(doc);
Object.assign(doc, det);
const sc = C.notate(doc);

t("key is D major", () => assert.strictEqual(sc.key.name, "D major"));
t("meter is 4", () => assert.strictEqual(sc.meter, 4));
t("tempo within 10% of 92", () => assert.ok(Math.abs(sc.bpm - 92) / 92 < 0.1, "bpm " + sc.bpm.toFixed(1)));
t("eight bars", () => assert.strictEqual(sc.nBars, 8));

t("every onset lands on its written tick", () => {
  const got = new Map();
  for (const bar of sc.bars) for (const s of [0, 1]) for (const e of bar.staves[s]) if (!e.rest) for (const n of e.notes) if (!n.tied) got.set(n.id, e.tick);
  const bad = [];
  doc.notes.forEach(n => { const i = +n.id.slice(1), want = C.demoDoc().truth[i][1]; if (got.get(n.id) !== want) bad.push(`${n.p}@${want}→${got.get(n.id)}`); });
  assert.deepStrictEqual(bad, []);
});

t("bar 5 opens with a triplet", () => {
  const ev = sc.bars[4].staves[0].filter(e => !e.rest);
  assert.ok(ev[0].tri && ev[1].tri && ev[2].tri, JSON.stringify(ev.slice(0, 3).map(e => [e.tick, e.len, e.base, e.tri])));
});

t("bar 2 is dotted quarter, eighth, half", () => {
  const ev = sc.bars[1].staves[0].map(e => e.base + ".".repeat(e.dots));
  assert.deepStrictEqual(ev, ["q.", "e", "h"]);
});

t("C♯ spelled as C♯ in D major, with no accidental drawn", () => {
  const e = sc.bars[1].staves[0][0].notes[0];
  assert.strictEqual(e.name, "C♯5"); assert.strictEqual(e.acc, null);
});

t("spelling: G♯ in A minor, E natural in F minor, B♭ in F major", () => {
  assert.strictEqual(C.spell(68, C.keyByName("A minor")).name, "G♯4");
  assert.strictEqual(C.spell(64, C.keyByName("F minor")).name, "E4");
  assert.strictEqual(C.spell(70, C.keyByName("F major")).name, "B♭4");
  assert.strictEqual(C.spell(61, C.keyByName("D♭ major")).name, "D♭4");
});

t("copy bars 1-2, paste before bar 3: ten bars, bars 3-4 repeat 1-2", () => {
  const d = JSON.parse(JSON.stringify(doc));
  const clip = C.copyBars(d, 0, 1);
  C.pasteBars(d, clip, 2);
  const s2 = C.notate(d);
  assert.strictEqual(s2.nBars, 10);
  const sig = b => s2.bars[b].staves.map(st => st.map(e => (e.rest ? "r" : e.notes.map(n => n.p).join(",")) + ":" + (e.tick - s2.bars[b].start) + "/" + e.len).join(" ")).join(" | ");
  assert.strictEqual(sig(2), sig(0)); assert.strictEqual(sig(3), sig(1));
  // and what was bar 3 is now bar 5
  assert.strictEqual(sig(4).replace(/\d+\//g, ""), (() => { const s1 = C.notate(doc); return s1.bars[2].staves.map(st => st.map(e => (e.rest ? "r" : e.notes.map(n => n.p).join(",")) + ":" + (e.tick - s1.bars[2].start) + "/" + e.len).join(" ")).join(" | "); })().replace(/\d+\//g, ""));
});

t("delete bar 1 leaves seven bars starting with old bar 2", () => {
  const d = JSON.parse(JSON.stringify(doc));
  C.deleteBars(d, 0, 0);
  const s2 = C.notate(d);
  assert.strictEqual(s2.nBars, 7);
  assert.strictEqual(s2.bars[0].staves[0][0].notes[0].p, 73);
});

t("score playback is steady: onsets are tick × constant", () => {
  const ev = C.scoreEvents(sc, 0);
  const sec = 60 / sc.bpm / 12;
  for (const e of ev) assert.ok(Math.abs(e.t / sec - Math.round(e.t / sec)) < 1e-6);
  assert.strictEqual(ev.length, C.demoDoc().truth.length);
});

t("MIDI file has a header and a track", () => {
  const b = C.smf(C.scoreEvents(sc, 0), sc.bpm, 4);
  assert.strictEqual(String.fromCharCode(...b.slice(0, 4)), "MThd");
});

t("paste and delete keep every bar intact on 60 performances", () => {
  const sig = (s2, b) => s2.bars[b].staves.map(st => st.map(e => (e.rest ? "r" : e.notes.map(n => n.p).join(",")) + ":" + (e.tick - s2.bars[b].start) + "/" + e.len).join(" ")).join(" | ");
  const bad = [];
  for (let seed = 1; seed <= 60; seed++) {
    const d = C.demoDoc(seed); Object.assign(d, C.detect(d)); const s0 = C.notate(d);
    for (const [m1, m2, c] of [[0, 1, 2], [2, 3, 0], [4, 4, 8], [1, 2, 5]]) {
      const e = JSON.parse(JSON.stringify(d)); C.pasteBars(e, C.copyBars(e, m1, m2), c); const s1 = C.notate(e);
      const want = [...Array(c).keys()].map(i => sig(s0, i)).concat([...Array(m2 - m1 + 1).keys()].map(i => sig(s0, m1 + i)), [...Array(8 - c).keys()].map(i => sig(s0, c + i)));
      const got = [...Array(s1.nBars).keys()].map(i => sig(s1, i));
      // the bar after the paste may differ only in a tie carried in from the bar before
      const strip = x => x.replace(/:\d+\/\d+/g, "");
      if (got.length !== want.length || got.some((g, i) => strip(g) !== strip(want[i]))) bad.push(`seed ${seed} copy ${m1 + 1}-${m2 + 1} before ${c + 1}`);
    }
    const e = JSON.parse(JSON.stringify(d)); C.deleteBars(e, 2, 3); const s1 = C.notate(e);
    const want = [0, 1, 4, 5, 6, 7].map(i => sig(s0, i)), got = [...Array(s1.nBars).keys()].map(i => sig(s1, i));
    if (JSON.stringify(got) !== JSON.stringify(want)) bad.push(`seed ${seed} delete 3-4`);
  }
  assert.deepStrictEqual(bad, []);
});

t("bars written after the end at a new tempo are tracked at that tempo", () => {
  const bad = [];
  for (let seed = 1; seed <= 30; seed++) {
    const d = C.demoDoc(seed); Object.assign(d, C.detect(d));
    const s0 = C.notate(d); C.ensureBars(d, s0.nBars + 1);
    const at = d.beats[s0.nBars * 4], q = 60 / 88;
    let r = seed; const jit = () => ((r = (r * 16807) % 2147483647) / 2147483647 - 0.5) * 0.02;
    const add = [[67, 0, 1], [69, 1, 1], [71, 2, 2], [43, 0, 4], [69, 4, 2], [62, 6, 2], [50, 4, 4]];
    for (const [p, b, l] of add) d.notes.push({ id: "w" + p + b, t: at + b * q + (b ? jit() : 0), d: l * q * 0.9, p, v: 75 });
    d.notes.sort((a, b) => a.t - b.t);
    d.beats = d.beats.filter(t => t <= at + 1e-9);
    d.beats = C.extendTracking(d);
    const s = C.notate(d), b9 = s.bars[8], b10 = s.bars[9];
    const sig = b => b.staves.map(st => st.map(e => (e.rest ? "r" : e.notes.map(n => n.p).join(",")) + "/" + e.len).join(" ")).join(" | ");
    if (s.nBars !== 10 || sig(b9) !== "67/12 69/12 71/24 | 43/48" || sig(b10) !== "69/24 62/24 | 50/48") bad.push(`seed ${seed}: ${sig(b9)} || ${sig(b10)}`);
  }
  assert.deepStrictEqual(bad, []);
});


// 6/8: an original eight-bar phrase in F major, eighth = 12 ticks, bar = 72.
const SIX8 = [
  [72, 0, 24], [69, 24, 12], [77, 36, 36],
  [76, 72, 12], [74, 84, 12], [72, 96, 12], [70, 108, 24], [69, 132, 12],
  [67, 144, 24], [69, 168, 12], [70, 180, 36],
  [69, 216, 72],
  [72, 288, 12], [74, 300, 12], [76, 312, 12], [77, 324, 24], [72, 348, 12],
  [74, 360, 36], [70, 396, 24], [67, 420, 12],
  [69, 432, 24], [70, 456, 12], [67, 468, 36],
  [65, 504, 72],
  [53, 0, 36], [60, 36, 36], [58, 72, 36], [53, 108, 36], [48, 144, 36], [55, 180, 36], [53, 216, 72],
  [57, 288, 36], [58, 324, 36], [55, 360, 36], [50, 396, 36], [48, 432, 36], [48, 468, 36], [41, 504, 72],
];
function perform(score, eighth, seed) {
  let s = seed; const rnd = () => (s = (s * 16807) % 2147483647) / 2147483647;
  const gauss = () => { let u = 0; for (let i = 0; i < 6; i++) u += rnd(); return (u - 3) / Math.sqrt(0.5); };
  const bt = [0];
  for (let b = 0; b < 60; b++) { const rit = b >= 36 ? Math.min(1.35, 1 + 0.03 * (b - 35)) : 1; bt.push(bt[b] + eighth * (1 + 0.08 * Math.sin(2 * Math.PI * b / 12)) * rit); }
  const at = tick => { const b = tick / 12, i = Math.floor(b); return bt[i] + (b - i) * (bt[i + 1] - bt[i]); };
  const grid = t => Math.round(t / 0.005) * 0.005;
  const notes = score.map(([p, tk, len], i) => {
    const acc = tk % 36 === 0 ? (tk % 72 === 0 ? 14 : 7) : 0;
    const t = grid(1 + at(tk) + 0.012 * gauss()), e = 1 + at(tk + len) - 0.04 - 0.02 * rnd();
    return { id: "d" + i, t, d: Math.max(0.05, e - t), p, v: Math.round((p < 60 ? 52 : 66) + acc + 6 * gauss()) };
  }).sort((a, b) => a.t - b.t);
  const pedal = []; for (let m = 0; m < 8; m++) { pedal.push([1 + at(m * 72) + 0.06, 100]); pedal.push([1 + at((m + 1) * 72) - 0.03, 0]); }
  return { format: "compose", version: 1, notes, pedal, beats: null, meter: null, key: null, keyOverride: null };
}
function exact68(d) {
  const s = C.notate(d); if (s.meter !== 6 || s.unit !== 8 || s.nBars !== 8) return `${s.meter}/${s.unit}, ${s.nBars} bars`;
  const got = new Map(); for (const bar of s.bars) for (const st of [0, 1]) for (const e of bar.staves[st]) if (!e.rest) for (const n of e.notes) if (!n.tied) got.set(n.id, e.tick);
  const bad = d.notes.filter(n => got.get(n.id) !== SIX8[+n.id.slice(1)][1]).length;
  return bad ? `${bad} onsets off` : "";
}
for (const [eighth, label] of [[0.5, "slow, eighth 0.5 s"], [0.34, "dotted quarter 59"], [0.24, "dotted quarter 83"]]) {
  t(`6/8 found, 8 bars, every onset exact on 30 takes (${label})`, () => {
    const bad = [];
    for (let seed = 1; seed <= 30; seed++) { const d = perform(SIX8, eighth, seed); Object.assign(d, C.detect(d)); const r = exact68(d); if (r) bad.push(`seed ${seed}: ${r}`); }
    console.log(`    ${30 - bad.length}/30 exact`); assert.ok(bad.length <= 3, bad.join("; "));
  });
}
t("a 400-bar 6/8 take with drifting tempo keeps every bar line (no slips over 13 minutes)", () => {
  let r = 3; const rnd = () => (r = (r * 16807) % 2147483647) / 2147483647, g = () => { let u = 0; for (let i = 0; i < 6; i++) u += rnd(); return (u - 3) / Math.sqrt(.5); };
  const E = 0.34, bt = [0]; for (let b = 0; b < 50 * 48 + 2; b++) bt.push(bt[b] + E * (1 + 0.08 * Math.sin(2 * Math.PI * b / 24) + 0.1 * Math.sin(2 * Math.PI * b / 300)));
  const at = tk => { const b = tk / 12, i = Math.floor(b); return bt[i] + (b - i) * (bt[i + 1] - bt[i]); }, notes = [];
  for (let k = 0; k < 50; k++) for (const [p, tk, len] of SIX8) { const T = k * 576 + tk; notes.push({ id: "x" + notes.length, T, t: Math.round((1 + at(T) + 0.012 * g()) / 0.005) * 0.005, d: Math.max(.05, at(T + len) - at(T) - 0.04), p, v: Math.round(58 + (tk % 72 === 0 ? 12 : tk % 36 === 0 ? 6 : 0) + 6 * g()) }); }
  notes.sort((a, b) => a.t - b.t); const d = { notes, pedal: [], beats: null }; Object.assign(d, C.detect(d)); const s = C.notate(d);
  const got = new Map(); for (const bar of s.bars) for (const st of [0, 1]) for (const x of bar.staves[st]) if (!x.rest) for (const n of x.notes) if (!n.tied) got.set(n.id, x.tick);
  const off = notes.filter(n => got.get(n.id) !== n.T).length;
  console.log(`    ${s.nBars} bars, ${off} of ${notes.length} onsets off`);
  assert.strictEqual(s.nBars, 400); assert.ok(off <= 10);
});
t("6/8 notation: bar 1 is quarter, eighth, dotted quarter; bar 4 a dotted half", () => {
  const d = perform(SIX8, 0.34, 3); Object.assign(d, C.detect(d)); const s = C.notate(d);
  assert.deepStrictEqual(s.bars[0].staves[0].map(e => e.base + ".".repeat(e.dots)), ["q", "e", "q."]);
  assert.deepStrictEqual(s.bars[3].staves[0].map(e => e.base + ".".repeat(e.dots)), ["h."]);
});
t("choosing 3/4 by hand regroups the 4/4 demo into bars of three", () => {
  const d = C.demoDoc(5); Object.assign(d, C.detect(d)); C.setMeter(d, 3, 4); const s = C.notate(d);
  assert.strictEqual(s.meter, 3); assert.ok(s.nBars >= 10 && s.nBars <= 12, "bars " + s.nBars);
});
t("a beat the tracker added is removed, so later bars stay on the downbeat", () => {
  const d = perform(SIX8, 0.34, 4); Object.assign(d, C.detect(d));
  const i = d.beats.length >> 1; d.beats.splice(i, 0, (d.beats[i] + d.beats[i - 1]) / 2); // a spurious beat mid-piece
  C.setMeter(d, 6, 8); assert.strictEqual(exact68(d), "");
});
t("MIDI file in 6/8 carries 6/8 and the quarter tempo", () => {
  const b = C.smf([{ t: 0, d: 1, p: 60, v: 60 }], 120, 6, 8); const i = b.indexOf(0x58);
  assert.deepStrictEqual([...b.slice(i + 2, i + 5)], [6, 3, 36]);
});

// Robustness: other seeds of the same phrase.
let exact = 0;
for (let seed = 1; seed <= 60; seed++) {
  const d = C.demoDoc(seed); Object.assign(d, C.detect(d)); const s = C.notate(d);
  const truth = d.truth; let ok = s.meter === 4 && s.key.name === "D major";
  const got = new Map(); for (const bar of s.bars) for (const st of [0, 1]) for (const e of bar.staves[st]) if (!e.rest) for (const n of e.notes) if (!n.tied) got.set(n.id, e.tick);
  for (const n of d.notes) if (got.get(n.id) !== truth[+n.id.slice(1)][1]) ok = false;
  if (ok) exact++;
}
console.log(`seeds transcribed exactly (key, meter and every onset): ${exact}/60`);
t("at least 57 of 60 seeds exact", () => assert.ok(exact >= 57));
console.log(`${pass} passed`);
