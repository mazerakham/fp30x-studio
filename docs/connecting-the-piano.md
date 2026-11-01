# Connecting the piano

Bluetooth MIDI pairs in **Audio MIDI Setup**, not System Settings:

1. Open Audio MIDI Setup
2. **Window → Show MIDI Studio**
3. Click the **Bluetooth** toolbar icon
4. Connect to **`FP-30X MIDI`**

The port then appears in CoreMIDI as `FP-30X MIDI Bluetooth` and the app turns green.

A USB-C-to-USB-B cable into the piano's square **COMPUTER** port also works, and is the
only route that additionally carries digital audio.

# Why this is hard

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
