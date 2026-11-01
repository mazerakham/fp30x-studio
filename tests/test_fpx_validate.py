"""The FPX bundle validator, pinned against synthetic bundles.

Every fixture is written inline, so the suite needs no piano, no captures
directory, and no network. The one test that touches the real library is
skipped when it is not present.

Each test names the requirement from ``docs/SPEC-fpx-format.md`` it pins, so a
change to the spec has a failing test to point at.
"""

from __future__ import annotations

import json

import pytest

from fp30x_studio.fpx import validate as V

HEADER = """\
# fp30x-capture v1
# columns abs_ns hex_bytes
# mach_timebase_numer 125
# mach_timebase_denom 3
# anchor_mach_ns 1000000000
# anchor_unix_ns 1786981500000000000
# started_utc 2026-08-17T15:45:00Z
# source FP-30X MIDI Bluetooth
# sources_connected 1
"""

TRAILER = ("# end packets 2 dropped 0 truncated 0 ts_zero 0 "
           "stopped_utc 2026-08-17T15:46:00Z\n")

# One note-on and its note-off, one nanosecond apart on the mach clock.
BODY = "2000000000 90 3C 40\n2000000001 80 3C 40\n"


def write_capture(path, *, header=HEADER, body=BODY, trailer=TRAILER,
                  clip_window=None, clip_end=False):
    text = header + body
    if clip_window:
        text += f"# clipped_from parent.fp30\n"
        text += f"# clip_window_ns {clip_window[0]} {clip_window[1]}\n"
    if trailer:
        text += trailer
    if clip_end:
        text += "# clip_end 1\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def write_midi(path, notes=1):
    mido = pytest.importorskip("mido")
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    for _ in range(notes):
        track.append(mido.Message("note_on", note=60, velocity=64, time=0))
        track.append(mido.Message("note_off", note=60, velocity=64, time=10))
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(path)
    return path


def conforming_bundle(tmp_path, name="perf"):
    """A bundle that satisfies every check the validator implements."""
    root = tmp_path / "FPX"
    (root / "captures").mkdir(parents=True)
    bundle = root / "performances" / name
    bundle.mkdir(parents=True)

    clip = write_capture(bundle / f"{name}.fpx",
                         clip_window=(1_500_000_000, 3_000_000_000),
                         trailer=None, clip_end=True)
    mid = write_midi(bundle / f"{name}.mid", notes=1)
    (bundle / f"{name}.electric.m4a").write_bytes(b"not really aac, but a file")
    readme = bundle / "README.txt"
    readme.write_text("WHAT one performance\nGenerated from fpx.json — do not edit.\n")

    files = []
    for p in sorted(bundle.iterdir()):
        if p.name == "fpx.json":
            continue
        files.append({"name": p.name, "sha256": V.sha256_file(p),
                      "bytes": p.stat().st_size,
                      "class": "curated" if p.suffix == ".fpx" else "derived"})
    manifest = {
        "fpx_manifest": 1,
        "written_utc": "2026-08-23T00:00:00Z",
        "written_by": "tests/1.0",
        "performance": {"id": "d:1500000000-3000000000", "slug": name,
                        "start_ns": 1_500_000_000, "end_ns": 3_000_000_000,
                        "duration_s": 1.5, "badge": "SEGMENT",
                        "badge_basis": "synthetic"},
        "capture": {"file": "parent.fp30", "sha256": "0" * 64,
                    "stream_sha256": "0" * 64, "bytes": 0,
                    "header": {}, "trailer": None, "closed": True,
                    "markers": [], "holes": [], "mirrors": []},
        "integrity": {"verdict": "GOOD"},
        "segments": [],
        "segmenter": {"gap_s": 4.0, "version": 1},
        "labels": [], "label_tombstones": [], "renders": [],
        "files": files,
    }
    (bundle / "fpx.json").write_text(json.dumps(manifest, indent=1))
    return root, bundle, clip, mid


def status(rep, req):
    return rep.by_req()[req].status


# ------------------------------------------------------------ the happy path


def test_a_conforming_bundle_passes_every_check(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    rep = V.validate_bundle(bundle, root=root)
    assert rep.ok, [(r.req, r.detail) for r in rep.failures]
    assert len(rep.results) == len(V.CHECKS)


def test_every_check_reports_exactly_once(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    rep = V.validate_bundle(bundle, root=root)
    seen = [r.req for r in rep.results]
    assert sorted(seen) == sorted(c[0] for c in V.CHECKS)


# ------------------------------------------------------------- FPX-70 / 71


def test_a_directory_without_a_manifest_is_not_a_bundle(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (bundle / "fpx.json").unlink()
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-70") == V.FAIL
    assert status(rep, "FPX-71") == V.SKIP
    # FPX-74 cannot be decided without the manifest, and says so rather than
    # guessing.
    assert status(rep, "FPX-74") == V.SKIP


def test_a_manifest_missing_required_sections_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    doc = json.loads((bundle / "fpx.json").read_text())
    del doc["integrity"]
    (bundle / "fpx.json").write_text(json.dumps(doc))
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-71") == V.FAIL
    assert "integrity" in rep.by_req()["FPX-71"].detail


# ------------------------------------------------------------------ FPX-74


def test_a_file_not_listed_in_the_manifest_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (bundle / "stray.wav").write_bytes(b"x")
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-74") == V.FAIL
    assert "stray.wav" in rep.by_req()["FPX-74"].detail


def test_a_digest_that_does_not_match_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (bundle / "README.txt").write_text(
        "WHAT changed after the manifest was written\n"
        "Generated from fpx.json — do not edit.\n")
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-74") == V.FAIL
    assert "digest" in rep.by_req()["FPX-74"].detail


# ------------------------------------------------------------------ FPX-39


def test_two_headers_in_one_file_is_multiple_streams(tmp_path):
    """Two writers appending to one -o path (O_APPEND) put two headers in one file."""
    root, bundle, clip, _ = conforming_bundle(tmp_path)
    clip.write_text(clip.read_text() + HEADER + BODY)
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-39") == V.FAIL
    assert "2 headers" in rep.by_req()["FPX-39"].detail


def test_two_end_trailers_in_one_file_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    cap = write_capture(bundle / "extra.fp30")
    cap.write_text(cap.read_text() + TRAILER)
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-39") == V.FAIL


# ------------------------------------------------------------------ FPX-31


def test_an_unclosed_capture_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    write_capture(bundle / "open.fp30", trailer=None)
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-31") == V.FAIL
    assert "not closed" in rep.by_req()["FPX-31"].detail


def test_a_clip_without_clip_end_fails(tmp_path):
    """FPX-33: `# end clip` parses to an empty trailer, so it is not closure."""
    root, bundle, clip, _ = conforming_bundle(tmp_path)
    clip.write_text(clip.read_text().replace("# clip_end 1\n", ""))
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-31") == V.FAIL


def test_the_legacy_end_clip_marker_is_accepted_as_closure(tmp_path):
    root, bundle, clip, _ = conforming_bundle(tmp_path)
    clip.write_text(clip.read_text().replace("# clip_end 1\n", "# end clip\n"))
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-31") == V.PASS
    # ... and it is not mistaken for a capture trailer.
    assert status(rep, "FPX-39") == V.PASS


# ------------------------------------------------------------- FPX-52 / 4


def test_a_whole_capture_in_a_bundle_fails_and_the_name_is_checked(tmp_path):
    root, bundle, clip, _ = conforming_bundle(tmp_path)
    # Replace the clip with the whole capture, as `publish` does today.
    clip.unlink()
    write_capture(bundle / "perf.fp30")
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-52") == V.FAIL
    # No library copy with that digest at all.
    assert status(rep, "FPX-4") == V.FAIL
    assert "no library capture" in rep.by_req()["FPX-4"].detail


def test_a_renamed_capture_is_caught(tmp_path):
    root, bundle, clip, _ = conforming_bundle(tmp_path)
    clip.unlink()
    library = write_capture(root / "captures" / "2026-08-23-real-name.fp30")
    (bundle / "perf.fp30").write_text(library.read_text())
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-4") == V.FAIL
    assert "2026-08-23-real-name.fp30" in rep.by_req()["FPX-4"].detail


def test_a_clip_in_a_bundle_skips_the_name_check(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-52") == V.PASS
    assert status(rep, "FPX-4") == V.SKIP


# ------------------------------------------------------------------ FPX-77


def test_an_unreadable_midi_fails(tmp_path):
    """A 14-byte .mid published beside the renders must fail."""
    root, bundle, _, mid = conforming_bundle(tmp_path)
    mid.write_bytes(b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0")
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-77") == V.FAIL
    assert "unreadable" in rep.by_req()["FPX-77"].detail


def test_a_midi_that_drops_notes_fails(tmp_path):
    """The export is compared to the take it came from, note-on for note-on."""
    root, bundle, _, mid = conforming_bundle(tmp_path)
    write_midi(mid, notes=2)      # window holds one note-on, the .mid has two
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-77") == V.FAIL
    assert "note-ons" in rep.by_req()["FPX-77"].detail


def test_audio_with_no_midi_source_fails(tmp_path):
    root, bundle, _, mid = conforming_bundle(tmp_path)
    mid.unlink()
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-77") == V.FAIL


def test_an_unrecoverable_window_skips_rather_than_guesses(tmp_path):
    root, bundle, clip, _ = conforming_bundle(tmp_path)
    (bundle / "fpx.json").unlink()
    clip.write_text(HEADER + BODY + "# clip_end 1\n")   # no clip_window_ns
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-77") == V.SKIP


# --------------------------------------------------------- FPX-151 / 153


def test_a_derived_cache_inside_the_bundle_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (bundle / ".index").mkdir()
    (bundle / ".index" / "perf.fp30.sqlite3").write_bytes(b"SQLite format 3\x00")
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-151") == V.FAIL
    assert ".index" in rep.by_req()["FPX-151"].detail


def test_a_soundfont_inside_the_bundle_fails(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (bundle / "jRhodes3c-stereo.sf2").write_bytes(b"RIFF")
    rep = V.validate_bundle(bundle, root=root)
    assert status(rep, "FPX-153") == V.FAIL


# ---------------------------------------------------------------- FPX-99


def test_a_rejected_label_asserted_in_provenance_is_a_violation(tmp_path):
    root = tmp_path / "FPX"
    caps = root / "captures"
    caps.mkdir(parents=True)
    (caps / "labels.json").write_text(json.dumps({
        "version": 1, "updated": None,
        "labels": {"2026-08-17-open.fp30#0": {
            "take": "2026-08-17-open.fp30", "verdict": "reject",
            "correction": None,
            "proposed": {"composer": "Scott Joplin",
                         "work": "The Strenuous Life", "source": "human"}}}}))
    (caps / "PROVENANCE.md").write_text(
        "## notes\n\n`2026-08-17-open.fp30` is **The Strenuous Life**.\n")
    lib = V.check_tombstones(root)
    assert lib.checked
    assert len(lib.violations) == 1
    assert "PROVENANCE.md:3" in lib.violations[0]


def test_no_violation_when_nothing_was_rejected(tmp_path):
    root = tmp_path / "FPX"
    caps = root / "captures"
    caps.mkdir(parents=True)
    (caps / "labels.json").write_text(json.dumps({
        "version": 1, "labels": {"a.fp30#0": {
            "take": "a.fp30", "verdict": "confirm",
            "proposed": {"work": "The Strenuous Life"}}}}))
    (caps / "PROVENANCE.md").write_text("`a.fp30` is The Strenuous Life.\n")
    assert V.check_tombstones(root).violations == []


def test_a_missing_labels_file_is_reported_not_passed(tmp_path):
    lib = V.check_tombstones(tmp_path / "nothing-here")
    assert not lib.checked
    assert "labels.json" in lib.note


# --------------------------------------------------------------- the tree


def test_validate_tree_walks_only_directories(tmp_path):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (root / "performances" / "notes.html").write_text("<p>not a bundle</p>")
    reports = V.validate_tree(root / "performances", root=root)
    assert [r.name for r in reports] == ["perf"]


def test_the_cli_exits_nonzero_when_a_bundle_fails(tmp_path, capsys):
    root, bundle, _, _ = conforming_bundle(tmp_path)
    (bundle / "fpx.json").unlink()
    code = V.main([str(root / "performances"), "--root", str(root)])
    assert code == 1
    out = capsys.readouterr().out
    assert "FPX-70" in out and "0/1 bundles conform" in out


def test_the_cli_exits_zero_on_a_conforming_tree(tmp_path, capsys):
    root, _, _, _ = conforming_bundle(tmp_path)
    assert V.main([str(root / "performances"), "--root", str(root)]) == 0
    assert "1/1 bundles conform" in capsys.readouterr().out


def test_json_output_is_machine_readable(tmp_path, capsys):
    root, _, _, _ = conforming_bundle(tmp_path)
    V.main([str(root / "performances"), "--root", str(root), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["bundles"][0]["ok"] is True
    assert {r["req"] for r in doc["bundles"][0]["results"]} == {
        c[0] for c in V.CHECKS}


# ------------------------------------------------------- the real library


REAL = V.default_root() / "performances"


@pytest.mark.skipif(not REAL.is_dir(), reason="no library on this machine")
def test_the_real_bundles_are_readable_and_every_check_answers():
    reports = V.validate_tree(REAL)
    assert reports, "expected at least one bundle"
    for rep in reports:
        assert sorted(r.req for r in rep.results) == sorted(c[0] for c in V.CHECKS)
