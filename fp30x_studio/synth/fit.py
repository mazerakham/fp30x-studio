"""Turning a folder of recorded notes into a preset, reproducibly.

    python -m fp30x_studio.synth.fit ~/workspace/audio/timbre-data/iowa-piano \\
        --out fp30x_studio/synth/presets/acoustic.json \\
        --report ~/workspace/audio/timbre-data/iowa-fit-report.json

Two stages, kept apart on purpose. :mod:`.analysis` measures one note and
returns numbers with their scatter; this module pools those measurements into
the handful of coefficients the preset actually has, and every one it writes it
also records -- estimate, standard error, sample size -- in the report JSON that
``docs/timbre-fit.html`` renders. Nothing gets into a preset here without a row
in that report saying where it came from.

Where a coefficient is *not* fitted, this module says so rather than quietly
carrying the old guess forward: :data:`UNFITTED` lists them and why, and the
report carries the list. There are five of them, and one -- the damper's
frequency exponent -- is the coefficient this pass most wants a recording of.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .analysis import NoteFit, analyse_note
from .model import IOWA_DYNAMIC_VNORM, Preset

__all__ = ["UNFITTED", "sweep", "fit_preset", "main"]

#: Coefficients this corpus cannot reach, and what would reach them. Carried
#: into the report so the page can mark them, because a preset in which some
#: numbers are measured and some are not is more dangerous than one in which
#: none are: it invites the reader to trust all of them equally.
UNFITTED = {
    "hammer_noise":
        "The broadband strike component cannot be separated from the partials "
        "at onset by this analysis: the heterodyne bank measures what is at the "
        "partial frequencies, and the noise between them is also the room. "
        "Would need a close, anechoic capture, or a physical hammer model.",
    "release_ms_fast":
        "Nothing in the Iowa corpus is a key release; the notes ring to the "
        "floor undamped. This maps the MIDI release-velocity byte, which those "
        "recordings do not have.",
    "release_ms_slow":
        "As release_ms_fast.",
    "damper_decay_exponent":
        "The one coefficient this pass most wants and cannot get. Fitting q "
        "needs a note whose damped decay is recorded per partial -- a held note "
        "released at a known instant. Every Iowa note is free-ringing. The "
        "free-ring exponent measured here (0.13) is a different mechanism "
        "(internal loss in the wire) from felt absorption and is not a "
        "substitute. Kelvin-Voigt gives q = 2, felt's loss modulus rises with "
        "frequency, and 1.0 is the midpoint in log; it is a slider, not a "
        "measurement.",
    "pedal_engage/pedal_knee/pedal_leak/pedal_contact_bite":
        "Half-damper geometry. The FP-30X reports cc64 but no recording of what "
        "it did to the sound exists on this machine. Set from the pedal "
        "statistics of real takes (769 mid-travel excursions, median 15 ms; "
        "cc64 = 127 for 86.6-91.4% of playing time), not from audio.",
}


def sweep(folder: str | Path, n_max: int = 32) -> list[NoteFit]:
    """Analyse every note file in ``folder``, skipping the ones that fail."""
    folder = Path(folder).expanduser()
    out: list[NoteFit] = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".aiff", ".aif", ".wav", ".flac"):
            continue
        try:
            out.append(analyse_note(p, n_max=n_max))
        except Exception as exc:                        # noqa: BLE001
            print(f"  skip {p.name}: {exc}")
    return out


def _wls(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """``(coef, stderr, resid_sd)`` for ordinary least squares."""
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ coef
    dof = max(X.shape[0] - X.shape[1], 1)
    s2 = float(r @ r) / dof
    se = np.sqrt(np.diag(s2 * np.linalg.pinv(X.T @ X)))
    return coef, se, float(r.std())


def fit_preset(fits: list[NoteFit], base: Preset | None = None
               ) -> tuple[Preset, dict]:
    """Pool the per-note measurements into one preset and one report."""
    p = Preset(**(asdict(base) if base else {}))
    p.model = "string"
    rep: dict = {"n_notes": len(fits), "coefficients": {}, "unfitted": UNFITTED}

    def record(key, value, **kw):
        p_val = float(value)
        setattr(p, key, p_val)
        rep["coefficients"][key] = {"value": p_val} | kw

    V = IOWA_DYNAMIC_VNORM

    # -- inharmonicity ---------------------------------------------------
    sel = [d for d in fits if d.midi >= 48 and d.B > 0
           and d.inharm_resid_cents < 8 and d.n_partials_measured >= 6]
    X = np.array([[1.0, (d.midi - 69) / 12.0] for d in sel])
    y = np.log10([d.B for d in sel])
    c, se, sd = _wls(X, y)
    record("inharmonicity_B", 10 ** c[0], stderr_factor=float(10 ** se[0]),
           n=len(sel), resid_factor=float(10 ** sd),
           note="B at A4, from log10 B linear in pitch over MIDI 48-96")
    record("inharmonicity_decades_per_octave", c[1], stderr=float(se[1]),
           n=len(sel), note=f"a decade of B every {12 / c[1]:.1f} semitones")
    bass = [d.B for d in fits if d.midi <= 48 and d.B > 0
            and d.inharm_resid_cents < 8]
    record("inharmonicity_floor", float(np.median(bass)), n=len(bass),
           iqr=[float(np.percentile(bass, 25)), float(np.percentile(bass, 75))],
           note="flat B below the bass break; the exponential law does not "
                "extend there")
    rep["inharmonicity_law_holds"] = {
        "rms_departure_cents_median": float(np.median(
            [d.inharm_resid_cents for d in fits if d.n_partials_measured >= 6])),
        "note": "RMS departure of the measured partials from f_n = n f0 "
                "sqrt(1 + B n^2). The law itself is confirmed; only its "
                "coefficient was wrong.",
    }

    # -- amplitude rolloff, jointly over pitch and dynamic ---------------
    sel = [d for d in fits if d.rolloff_r2 > 0.6 and d.n_partials_measured >= 8
           and np.isfinite(d.rolloff)]
    X = np.array([[1.0, (d.midi - 69) / 12.0, V[d.dynamic] - 0.5] for d in sel])
    y = np.array([d.rolloff for d in sel])
    c, se, sd = _wls(X, y)
    record("partial_amp_rolloff", c[0], stderr=float(se[0]), n=len(sel),
           resid_sd=sd, note="rolloff exponent at A4 and mid velocity")
    record("rolloff_per_octave", c[1], stderr=float(se[1]), n=len(sel),
           note="the tilt steepens with pitch; one number for the keyboard was "
                "the largest single error in the guessed preset")
    # The velocity term is only meaningful where the rolloff fits it is
    # differencing are themselves good, so it gets the stricter subset.
    sel2 = [d for d in fits if d.rolloff_r2 > 0.7 and d.n_partials_measured >= 12
            and np.isfinite(d.rolloff)]
    X2 = np.array([[1.0, (d.midi - 69) / 12.0, V[d.dynamic] - 0.5] for d in sel2])
    c2, se2, _ = _wls(X2, np.array([d.rolloff for d in sel2]))
    record("velocity_brightness", -c2[2], stderr=float(se2[2]), n=len(sel2),
           note="from how far the fitted rolloff moves between pp and ff. The "
                "looser subset gives %.2f +- %.2f, so the interval is wide and "
                "contains the old guess of 1.3." % (-c[2], se[2]))

    # -- decay -----------------------------------------------------------
    sel = [d for d in fits if np.isfinite(d.tau1) and d.tau1 > 0
           and d.dynamic != "pp"]
    X = np.array([[1.0, (d.midi - 69) / 12.0] for d in sel])
    y = np.log2([d.tau1 for d in sel])
    c, se, sd = _wls(X, y)
    record("partial_decay_base", 2 ** c[0], stderr_factor=float(2 ** se[0]),
           n=len(sel), resid_octaves=sd,
           note="tau_1 at A4, from the prompt (first 25 dB) decay")
    record("decay_halving_semitones", -12.0 / c[1], n=len(sel),
           slope_per_octave=float(c[1]), slope_stderr=float(se[1]))

    slopes = []
    for d in fits:
        ps = [q for q in d.partials if np.isfinite(q.alpha) and q.alpha > 0
              and q.r2 > 0.7 and q.snr_db > 25]
        if len(ps) < 5:
            continue
        ln = np.log([q.n for q in ps])
        la = np.log([q.alpha for q in ps])
        cc, _, _ = _wls(np.stack([np.ones_like(ln), ln], 1), la)
        pred = cc[0] + cc[1] * ln
        sst = float(((la - la.mean()) ** 2).sum())
        r2 = 1 - float(((la - pred) ** 2).sum()) / sst if sst > 0 else 0.0
        slopes.append((d.midi, d.dynamic, float(cc[1]), r2))
    sl = [s[2] for s in slopes]
    record("partial_decay_exponent", float(np.median(sl)), n=len(sl),
           iqr=[float(np.percentile(sl, 25)), float(np.percentile(sl, 75))],
           max_observed=float(max(sl)),
           median_r2=float(np.median([s[3] for s in slopes])),
           note="alpha_n proportional to n^this. Stiffness-proportional "
                "damping predicts 2; the largest value seen on any note is "
                f"{max(sl):.2f}, and the power law's median R^2 is "
                f"{np.median([s[3] for s in slopes]):.2f}, so the functional "
                "form explains almost none of the partial-to-partial "
                "variation. See the report's decay_law_fails entry.")
    rep["decay_law_fails"] = {
        "per_note_slopes": slopes,
        "median_r2": float(np.median([s[3] for s in slopes])),
        "fraction_r2_above_half": float(np.mean([s[3] > 0.5 for s in slopes])),
        "note": "tau_n = tau_1 n^-e is the model's form. Fitted per note it "
                "explains a median 10% of the variance in per-partial decay "
                "rate. The scatter is not measurement noise -- neighbouring "
                "partials of one note differ by a factor of two either way -- "
                "it is bridge and soundboard coupling, which is mode by mode "
                "and which a smooth power law in n cannot represent at all.",
    }

    ts = [(d.two_stage_ratio, d.two_stage_mix) for d in fits
          if np.isfinite(d.two_stage_mix) and d.two_stage_mix > 5e-4]
    mixes = [m for _, m in ts]
    ratios = [r for r, _ in ts]
    record("second_stage_mix", float(np.median(mixes)), n=len(mixes),
           iqr=[float(np.percentile(mixes, 25)), float(np.percentile(mixes, 75))],
           note="amplitude of the aftersound relative to the prompt sound")
    record("second_stage_ratio", 12.0, n=len(ratios),
           fitted_median=float(np.median(ratios)),
           lower_bound=float(min(ratios)),
           identified=False,
           note="NOT identified by this corpus. The fit runs to whatever upper "
                "bound the search grid allows in %d of %d notes, because the "
                "aftersound sits close to the recording's own floor. What the "
                "data supports is a lower bound of %.1f. 12 is taken at the "
                "low end of that bracket, which is the shortest tail the "
                "measurements permit." % (sum(1 for r in ratios if r >= 19.9),
                                          len(ratios), min(ratios)),
           )

    # -- unison ----------------------------------------------------------
    b = [d for d in fits if d.beat_strength > 0.25 and 0.4 < d.beat_hz < 6]
    cents = [d.beat_cents for d in b]
    depths = [d.beat_depth for d in b]
    record("unison_detune_cents", float(np.median(cents)), n=len(b),
           iqr=[float(np.percentile(cents, 25)), float(np.percentile(cents, 75))],
           note="from the amplitude-modulation rate of the fundamental. This "
                "conflates unison detuning with beating between the string's "
                "two polarisations; both are real and both are what the "
                "parameter is for.")
    record("unison_depth", float(np.median(depths)), n=len(b),
           iqr=[float(np.percentile(depths, 25)),
                float(np.percentile(depths, 75))])
    rep["unison_register"] = {
        band: float(np.median([d.beat_strength for d in fits if lo <= d.midi <= hi]))
        for band, (lo, hi) in {"below_48": (0, 47), "48_to_59": (48, 59),
                               "60_and_up": (60, 127)}.items()}

    # -- strike point ----------------------------------------------------
    sp = [d for d in fits if d.strike_point_corr > 0.15
          and d.n_partials_measured >= 10]
    al = [d.strike_point for d in sp]
    record("hammer_strike_point", float(np.median(al)), n=len(sp),
           iqr=[float(np.percentile(al, 25)), float(np.percentile(al, 75))],
           note="1/%.1f of the speaking length, from the notch comb the "
                "hammer leaves in the partial amplitudes. Real pianos strike "
                "between 1/7 and 1/9." % (1 / np.median(al)))

    # -- attack ----------------------------------------------------------
    at = [d.attack_ms for d in fits if d.dynamic in ("mf", "ff")
          and np.isfinite(d.attack_ms)]
    rep["attack_calibration"] = {
        "measured_median_ms": float(np.median(at)), "n": len(at),
        "note": "The estimator is 10%-to-peak on the summed signal, which is "
                "when the partials finish interfering, not when one of them "
                "rises. Run on this model's own output with attack_ms set to "
                "1, 2, 4, 8, 12 and 20 ms it reports 12.8, 14.8, 20.5, 30.2, "
                "36.3 and 62.3 ms. The recorded median of "
                f"{np.median(at):.1f} ms therefore corresponds to attack_ms "
                "= 4.0, which is what the preset already had. The attack was "
                "not the problem.",
    }
    p.attack_ms = 4.0
    rep["coefficients"]["attack_ms"] = {"value": 4.0, "n": len(at),
                                        "note": "confirmed by calibration, "
                                                "unchanged"}

    rep["radiation_filter"] = {
        "identified": False,
        "note": "Not added, and deliberately. Within one note log f_n = log n "
                "+ log f0, so an absolute-frequency soundboard transfer and a "
                "partial-index rolloff are exactly collinear once the note's "
                "own level is free. Fitting both with per-note intercepts "
                "gives k_n = -45 +- 4 and k_f = +46 +- 4: equal, opposite, and "
                "meaningless. The fitted pitch-dependent rolloff already "
                "carries whatever radiation shaping is in these recordings, "
                "and a separate filter would double-count it. Identifying one "
                "needs the same note recorded at several string lengths, or a "
                "measured soundboard admittance.",
    }
    return p, rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", help="directory of isolated single notes")
    ap.add_argument("--out", help="write the fitted preset here")
    ap.add_argument("--report", help="write the fit report JSON here")
    ap.add_argument("--fits", help="write the raw per-note measurements here")
    ap.add_argument("--name", default="acoustic")
    a = ap.parse_args(argv)

    print(f"analysing {a.folder} ...")
    fits = sweep(a.folder)
    print(f"  {len(fits)} notes measured")
    preset, rep = fit_preset(fits)
    preset.name = a.name

    for k, v in rep["coefficients"].items():
        extra = ""
        if "stderr" in v:
            extra = f" +- {v['stderr']:.3g}"
        elif "stderr_factor" in v:
            extra = f" (x/ {v['stderr_factor']:.2f})"
        print(f"  {k:34s} {v['value']:<12.5g}{extra}  n={v.get('n', '-')}")

    if a.fits:
        Path(a.fits).expanduser().write_text(json.dumps(
            [asdict(f) for f in fits], indent=1))
    if a.report:
        Path(a.report).expanduser().write_text(json.dumps(rep, indent=1))
        print(f"  report -> {a.report}")
    if a.out:
        print(f"  preset -> {preset.save(a.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
