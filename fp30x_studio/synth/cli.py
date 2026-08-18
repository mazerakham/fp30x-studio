"""``python -m fp30x_studio.synth`` -- render a named take with a named preset.

    render <take> --preset acoustic --out ~/Music/x.wav
    presets                     list what is on disk, with the shared schema
    score <take>                what the renderer will be handed, before it renders
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_TAKES = Path("~/Music/FP-30X Studio/takes").expanduser()
DEFAULT_OUT = Path("~/Music/FP-30X Studio/renders").expanduser()


def resolve_take(name: str) -> Path:
    p = Path(name).expanduser()
    if p.exists():
        return p
    for cand in (DEFAULT_TAKES / name, DEFAULT_TAKES / f"{name}.fp30"):
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"no take {name!r}; looked in {DEFAULT_TAKES} "
        f"(have {sorted(x.name for x in DEFAULT_TAKES.glob('*.fp30'))})")


def cmd_render(args) -> int:
    from .engine import RenderResult, render, write_wav
    from .model import load_preset
    from .score import read_score

    take = resolve_take(args.take)
    preset = load_preset(args.preset)
    score = read_score(take)
    print(score.summary(), file=sys.stderr)
    print(f"preset {args.preset}: model={preset.model} "
          f"partials={preset.partials} B(A4)={preset.inharmonicity_B:g} "
          f"tau1={preset.partial_decay_base:g}s "
          f"release {preset.release_ms_slow:g}->{preset.release_ms_fast:g} ms",
          file=sys.stderr)

    out = Path(args.out).expanduser() if args.out else (
        DEFAULT_OUT / f"{take.stem}.{Path(args.preset).stem}.wav")

    def progress(i, n, el):
        print(f"  {i}/{n} notes, {el:.0f} s elapsed", file=sys.stderr, flush=True)

    mix, stats = render(score, preset, sample_rate=args.sample_rate,
                        tail_s=args.tail, progress=None if args.quiet else progress)
    if args.trim_lead and score.notes:
        # The piece take was armed 37.97 s before the first key went down. That
        # silence is real and is kept by default, because the deliverable is
        # "the full take"; this drops it for listening.
        cut = max(0, int((score.notes[0].t_on - 0.5) * args.sample_rate))
        mix = mix[cut:]
        stats["seconds"] = mix.shape[0] / args.sample_rate
        print(f"  trimmed {cut / args.sample_rate:.2f} s of lead-in silence",
              file=sys.stderr)
    path = write_wav(out, mix, args.sample_rate, subtype=args.subtype)
    res = RenderResult(path=path, preset=str(args.preset), model=preset.model,
                       take=take.name, sample_rate=args.sample_rate,
                       n_notes=stats["n_notes"], seconds=stats["seconds"],
                       peak_before_norm=stats["peak_before_norm"],
                       peak=stats["peak"], rms=stats["rms"],
                       gain_db=stats["gain_db"], crest_db=stats["crest_db"],
                       wall_s=stats["wall_s"])
    print(res.line())
    return 0


def cmd_presets(args) -> int:
    from .model import PRESET_DIR, PRESET_KEYS, load_preset

    print("shared schema:", ", ".join(PRESET_KEYS))
    for f in sorted(PRESET_DIR.glob("*.json")):
        p = load_preset(f)
        print(f"\n{f.stem}  [{p.model}]  {f}")
        if p.note:
            print(f"  {p.note}")
        d = p.to_dict()
        for k in PRESET_KEYS:
            print(f"    {k:<24} {d[k]}")
    return 0


def cmd_score(args) -> int:
    from .score import read_score

    s = read_score(resolve_take(args.take))
    print(s.summary())
    print(f"  peak polyphony {s.peak_polyphony()}")
    return 0


def cmd_partials(args) -> int:
    """The actual coefficients for one note, so the model can be read off."""
    import numpy as np

    from . import model as M
    from .model import load_preset
    from .voices import note_frequency, release_tau

    p = load_preset(args.preset)
    note, vel = args.note, args.velocity
    f0 = note_frequency(note)
    v = M.velocity_norm(vel)
    print(f"{args.preset} [{p.model}]  note {note}  f0 {f0:.3f} Hz  "
          f"velocity {vel} -> v = {v:.3f} of {M.VELOCITY_FULL_SCALE:g}  "
          f"gain {v ** M.VELOCITY_AMP_EXPONENT:.4f}")
    print(f"release velocity {args.release} -> damper tau "
          f"{release_tau(args.release, p) * 1000:.1f} ms")

    if p.model == "string":
        B = M.inharmonicity_at(note, p.inharmonicity_B)
        n, f = M.partial_frequencies(f0, p.partials, B, args.sample_rate / 2)
        a = n ** (-p.partial_amp_rolloff) * n ** (p.velocity_brightness * (v - 0.5))
        a = a * np.abs(np.sin(n * np.pi * M.HAMMER_STRIKE_POINT))
        a = a / a.sum()
        tau1 = p.partial_decay_base * M.decay_scale_at(note)
        tau = tau1 * n ** (-p.partial_decay_exponent)
        cents = 1200 * np.log2(f / (n * f0))
        print(f"B({note}) = {B:.3e}   tau_1 = {tau1:.3f} s")
        print(f"{'n':>3} {'f_n Hz':>10} {'cents sharp':>12} {'a_n':>9} "
              f"{'dB':>7} {'tau_n s':>9}")
        for i in range(n.size):
            print(f"{int(n[i]):>3} {f[i]:>10.2f} {cents[i]:>12.2f} {a[i]:>9.5f} "
                  f"{20 * np.log10(max(a[i], 1e-12)):>7.1f} {tau[i]:>9.3f}")
    else:
        i0 = p.fm_index * v ** 1.5
        i_max = max(0.0, (args.sample_rate / 2 / f0 - 1.0) / p.fm_ratio - 2.0)
        print(f"carrier {f0:.2f} Hz   modulator {p.fm_ratio * f0:.2f} Hz "
              f"(ratio {p.fm_ratio})")
        print(f"FM index {i0:.3f} at strike, capped at {i_max:.3f} by Nyquist, "
              f"decaying with tau {M.FM_INDEX_DECAY_S} s")
        print(f"bell partial {p.bell_partial * f0:.2f} Hz "
              f"({p.bell_partial} x f0), amp {p.bell_amp * (0.45 + 0.55 * v):.4f}")
        print(f"bark {p.bark_amp * v ** 3:.4f} over {p.bark_ms} ms")
        print(f"amplitude tau {p.partial_decay_base * M.decay_scale_at(note, M.TINE_DECAY_HALVING_SEMITONES):.3f} s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fp30x_studio.synth", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render a take through a preset")
    r.add_argument("take", help="path, or a name under ~/Music/FP-30X Studio/takes")
    r.add_argument("--preset", "-p", default="acoustic",
                   help="preset name in synth/presets, or a path to a .json")
    r.add_argument("--out", "-o", default=None, help="output .wav path")
    r.add_argument("--sample-rate", type=int, default=48000)
    r.add_argument("--tail", type=float, default=6.0,
                   help="seconds of ring-out after the last event")
    r.add_argument("--subtype", default="PCM_24")
    r.add_argument("--trim-lead", action="store_true",
                   help="drop the silence before the first note-on")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=cmd_render)

    p = sub.add_parser("presets", help="list presets and the shared schema")
    p.set_defaults(func=cmd_presets)

    s = sub.add_parser("score", help="what the renderer sees")
    s.add_argument("take")
    s.set_defaults(func=cmd_score)

    d = sub.add_parser("partials", help="the coefficients for one note, printed")
    d.add_argument("--preset", "-p", default="acoustic")
    d.add_argument("--note", "-n", type=int, default=60)
    d.add_argument("--velocity", "-v", type=int, default=80)
    d.add_argument("--release", "-r", type=int, default=64)
    d.add_argument("--sample-rate", type=int, default=48000)
    d.set_defaults(func=cmd_partials)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
