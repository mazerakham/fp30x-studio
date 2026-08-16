# FP-30X Studio

A small macOS desktop app that records what you play on a **Roland FP-30X**, saves it as a
MIDI file, renders it to audio, and plays it back.

It exists because getting sound off the piano is unreasonably hard by default, and the
obvious routes are all dead ends. See [Why this is hard](#why-this-is-hard).

```
./run.sh
```

Press **Record**, play, press **Stop**. The take is written to
`~/Music/FP-30X Studio/takes/` as a `.mid` and a `.wav`, and starts playing.

## What it does

- Finds the piano on CoreMIDI and shows a live connection indicator
- Live note feed with velocity bars, and a sustain-pedal indicator
- Saves a real Standard MIDI File — the notes, not a recording of them
- Renders to 44.1 kHz WAV via fluidsynth
- Keeps a list of takes; double-click one to play it

## Requirements

- macOS
- Python 3.10+ with tkinter — `brew install python-tk@3.14` if `import tkinter` fails
- `brew install fluid-synth` for rendering
- The instrument set (38 MB, MIT licensed) downloads itself on first render

`run.sh` creates the virtualenv and installs Python dependencies on first launch.

## Connecting the piano

Bluetooth MIDI pairs in **Audio MIDI Setup**, not System Settings:

1. Open Audio MIDI Setup
2. **Window → Show MIDI Studio**
3. Click the **Bluetooth** toolbar icon
4. Connect to **`FP-30X MIDI`**

The port then appears in CoreMIDI as `FP-30X MIDI Bluetooth` and the app turns green.

A USB-C-to-USB-B cable into the piano's square **COMPUTER** port also works, and is the
only route that additionally carries digital audio.

## Why this is hard

Four dead ends, each of which looks like the right answer:

1. **`FP-30X Audio` over Bluetooth cannot carry audio out.** It is an A2DP sink —
   CoreAudio reports `in=0 out=2`. It streams *into* the piano's speakers. No setting
   changes this.
2. **Bluetooth MIDI is a second, separate pairing**, and macOS hides it in Audio MIDI Setup
   rather than System Settings. The BLE peripheral advertises as `FP-30X MIDI`, which is a
   different name from `FP-30X Audio`.
3. **The piano's USB-A socket is a host port for flash drives.** A USB-A-to-USB-C cable to
   a Mac is host-to-host and enumerates nothing at all — `system_profiler SPUSBDataType`
   silently reports zero devices. You need the square USB-B `COMPUTER` port.
4. **There is no Roland macOS driver, and one would not help.** Roland ships only a
   firmware updater. CoreMIDI already contains Apple's BLE-MIDI driver; it simply had
   nothing connected to it.

## Layout

| Path | What it is |
| --- | --- |
| `fp30x_studio/core.py` | Capture, render, playback. No GUI. |
| `fp30x_studio/app.py` | The tkinter interface. |
| `run.sh` | Launcher; bootstraps the virtualenv. |

`bt_midi.py` in the parent directory is a standalone userland BLE-MIDI client that talks
to the piano's GATT service directly, bypassing CoreMIDI entirely. It is useful when the
piano is unpaired and still advertising, and is not needed by this app.

## Not done yet

- Live digital-audio capture over USB (needs the USB-C-to-USB-B cable)
- Choosing an instrument other than the default piano
- Metronome, tempo detection, quantisation
