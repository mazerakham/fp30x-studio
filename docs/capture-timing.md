# Timing: the native capture front end

The app's Python capture polls the MIDI port and stamps each message with the time the
poll loop *noticed* it:

```python
while not stopped:
    for msg in inp.iter_pending(): record(time.time())
    time.sleep(0.002)
```

That imposes a ~2 ms quantisation on every take, and it is an artifact of our loop — not
of the piano, of MIDI, or of Bluetooth. `native/fp30x_capture.c` removes it.

CoreMIDI is callback-driven and every incoming `MIDIPacket` already carries a
`MIDITimeStamp` applied near the driver, before any of our code runs. Apple's SDK headers
(`MIDIServices.h`) define it as "A host clock time representing the time of an event, as
returned by `mach_absolute_time()`", applying to "the first MIDI byte in the packet". So
the right move is not a faster poll — it is not polling.

```
make -C native                                    # builds build/fp30x-capture
native/build/fp30x-capture -l                     # list CoreMIDI sources
native/build/fp30x-capture -s FP-30X -o take.fp30x
```

The receive callback runs on a thread CoreMIDI owns and prioritises, so it does nothing
but copy the timestamp and bytes into a preallocated lock-free ring — no allocation, no
locks, no I/O. A separate writer thread drains the ring to an append-only text file and
`fsync`s on a timer, so a crash or a laptop sleep costs at most the last unsynced
fragment.

The file is line-oriented and greppable — `<absolute_nanoseconds> <hex bytes>` with a
header carrying the `mach_timebase` numer/denom and a wall-clock anchor:

```
# fp30x-capture v1
# mach_timebase_numer 125
# mach_timebase_denom 3
# anchor_mach_ns 50666761187333
# anchor_unix_ns 1786981500562062000
50679390933166 90 3C 01
50679391832208 80 3C 40
# end packets 40 dropped 0 truncated 0 ts_zero 0 stopped_utc ...
```

`ts_zero` counts packets the *source* did not stamp; CoreMIDI documents a zero timestamp
as meaning "now", so those were stamped on arrival and are no better than the poll loop.
`RawCapture.hardware_timestamped` reads that trailer rather than assuming.

**Measured**, by `native/benchmark.py`, on **synthetic input** — a virtual CoreMIDI source
emitting at known intervals, scored against its own emission timestamps. 200 messages at
each of six spacings from 5 ms down to 100 µs:

| nominal spacing | C path median error | Python poll-loop median error | poll-loop p95 | intervals the poll loop collapsed |
| --- | --- | --- | --- | --- |
| 5000 µs | 0.0000 ms | 0.34 ms | 2.38 ms | 4/199 |
| 2000 µs | 0.0000 ms | 0.54 ms | 2.02 ms | 40/199 |
| 1000 µs | 0.0000 ms | 1.01 ms | 1.59 ms | 113/199 |
| 500 µs | 0.0000 ms | 0.50 ms | 2.04 ms | 152/199 |
| 200 µs | 0.0000 ms | 0.20 ms | 2.11 ms | 175/199 |
| 100 µs | 0.0000 ms | 0.09 ms | 0.25 ms | 182/199 |

"Collapsed" means the recorded interval came out under 20 µs when the true interval was
the nominal spacing — the poll loop read those messages in one pass and reported them as
simultaneous. At 1 ms spacing it does that to more than half of them.

The C path's timestamps were **bit-identical to the sender's** for all 1200 messages, with
zero dropped, so its error is exactly zero and its resolution is bounded by the mach tick,
41.67 ns. This measures the capture path only. It does not measure the piano, the key
action, or the Bluetooth link — see *On the real piano* below.

Reading a capture back, through the same interface the polling front end uses:

```python
from fp30x_studio import rawcapture
from fp30x_studio.performance import Performance

cap = rawcapture.read("take.fp30x")
print(cap.summary())
perf = Performance.from_capture(cap)      # identical to a live core.Capture
perf = Performance.from_raw_capture("take.fp30x")   # or straight from the path
```

There is no second copy of the analysis: `RawCapture.messages` has exactly the
`[(seconds, mido.Message)]` shape `core.Capture` produces, and `inspect_capture.load()`
reads `.fp30x` alongside `.mid` and `.json`.

## On the real piano

Measured on two real takes from 2026-08-17 (2,185 and 482 packets): `ts_zero 0` in both,
so Apple's BLE-MIDI driver stamps every packet. Removing the 2 ms poll floor exposed the
link's own: every timestamp is a whole millisecond and a multiple of 5 ms, the minimum
inter-packet gap is 5 ms, and a chord struck together is delivered as a 5 ms arpeggio. The
only simultaneity the link admits is the 53 packets carrying two or three messages. The
full analysis is §5 of [take-2026-08-17.html](take-2026-08-17.html).
