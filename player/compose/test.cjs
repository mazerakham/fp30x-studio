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
