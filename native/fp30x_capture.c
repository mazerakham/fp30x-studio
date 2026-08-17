/*
 * fp30x-capture — durable, callback-driven MIDI capture for macOS CoreMIDI.
 *
 * WHY THIS EXISTS
 * ---------------
 * The Python capture in fp30x_studio/core.py runs
 *
 *     while not stopped:
 *         for msg in inp.iter_pending(): record(time.time())
 *         time.sleep(0.002)
 *
 * so every event is stamped with the time the *poll loop noticed it*, not the
 * time it happened. That imposes a ~2 ms quantisation on the archive which is
 * an artifact of our own code. It is not a property of MIDI, of Bluetooth, or
 * of the piano.
 *
 * On macOS you should not poll at all. Verified against the CoreMIDI headers
 * shipped in the macOS SDK (MIDIServices.h), which are Apple's own docs:
 *
 *   - MIDITimeStamp (MIDIServices.h:227-239) — "A host clock time." /
 *     "A host clock time representing the time of an event, as returned by
 *     mach_absolute_time() or UpTime()."
 *
 *   - MIDIPacket.timeStamp (MIDIServices.h:511-515) — "The time at which the
 *     events occurred, if receiving MIDI, ... Zero means 'now.' The time stamp
 *     applies to the first MIDI byte in the packet."
 *
 *   - MIDIReadBlock (MIDIServices.h:368-386) — "The CoreMIDI framework will
 *     create a high-priority receive thread on your client's behalf, and from
 *     that thread, your MIDIReadProc will be called when incoming MIDI messages
 *     arrive."
 *
 * So the packet already carries the timestamp the driver applied, and it is
 * handed to us on a thread CoreMIDI owns and prioritises. We must not poll, and
 * we must not make that thread wait for us.
 *
 * A HONEST NOTE ON THE CALLBACK RULES
 * -----------------------------------
 * Apple's headers say the callback runs on a "high-priority receive thread".
 * They do NOT contain a verbatim prohibition on allocation, locks or I/O inside
 * it. That rule is standard real-time-audio practice rather than a quotable
 * Apple sentence, and this file honours it anyway: the callback below does
 * nothing but read a timestamp, memcpy at most 256 bytes into a preallocated
 * slot, and bump an atomic index. No malloc, no lock, no syscall, no I/O.
 *
 * The file write happens on a separate ordinary thread that drains a lock-free
 * single-producer/single-consumer ring and fsyncs on a timer, so a crash or a
 * laptop sleep costs at most the last unsynced fragment. This machine has
 * already killed one agent by sleeping.
 *
 * TIMESTAMP PROVENANCE, STATED PLAINLY
 * ------------------------------------
 * timeStamp == 0 is documented to mean "now", i.e. the source declined to stamp
 * the event and we must stamp it ourselves on arrival. When that happens we
 * substitute mach_absolute_time() AND count it in `ts_zero`, which is written
 * into the file trailer. If ts_zero equals the packet count, this tool has
 * bought you nothing over the Python one and the file says so out loud.
 *
 * FILE FORMAT (text, append-only, greppable)
 * ------------------------------------------
 *   # comment lines carrying the header, then one record per line:
 *   <absolute_nanoseconds> <SPACE-SEPARATED UPPERCASE HEX BYTES>
 *
 * Nanoseconds are mach_absolute_time() ticks converted through the
 * mach_timebase numer/denom, both of which are recorded in the header so the
 * conversion is auditable and reversible. A wall-clock anchor pairs one mach
 * instant with one CLOCK_REALTIME instant so Python can place the take in
 * civil time without trusting anything this program computed.
 *
 * Text rather than binary is deliberate: the data rate of MIDI is a few KB/s at
 * its absolute worst, so the encoding costs nothing, and in exchange the file
 * is greppable, tail-able, diff-able, and readable by a human with no tooling.
 *
 * BUILD   cc -O2 -Wall -Wextra -framework CoreMIDI -framework CoreFoundation \
 *            -o fp30x-capture fp30x_capture.c
 */

#include <CoreFoundation/CoreFoundation.h>
#include <CoreMIDI/CoreMIDI.h>
#include <mach/mach_time.h>

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define TOOL_VERSION 1

/* A MIDIPacket carries at most 256 bytes unless dynamically allocated; we cap
 * at 256 and count any truncation rather than allocating on the hot path. */
#define SLOT_DATA 256

/* 16384 slots * 264 bytes ~= 4.3 MB, allocated once in .bss before we start. */
#define RING_SLOTS 16384u

typedef struct {
    uint64_t ts;   /* mach_absolute_time() ticks */
    uint32_t len;
    uint8_t  data[SLOT_DATA];
} slot_t;

static slot_t g_ring[RING_SLOTS];

/* SPSC ring. Producer = CoreMIDI receive thread. Consumer = writer thread. */
static _Atomic uint64_t g_head;      /* producer writes */
static _Atomic uint64_t g_tail;      /* consumer writes */
static _Atomic uint64_t g_dropped;   /* ring was full */
static _Atomic uint64_t g_truncated; /* packet longer than SLOT_DATA */
static _Atomic uint64_t g_ts_zero;   /* source supplied no timestamp */
static _Atomic uint64_t g_packets;   /* packets accepted */
static _Atomic int      g_stop;

static mach_timebase_info_data_t g_tb;
static int      g_fd = -1;
static int      g_quiet = 0;
static double   g_sync_interval = 0.25; /* seconds between fsyncs */

static inline uint64_t ticks_to_ns(uint64_t t)
{
    /* 128-bit so a long uptime cannot overflow the intermediate product. */
    return (uint64_t)(((__uint128_t)t * g_tb.numer) / g_tb.denom);
}

static uint64_t realtime_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* ---------------------------------------------------------------------------
 * The hot path. Runs on CoreMIDI's high-priority receive thread.
 * No allocation, no locks, no syscalls, no I/O. Never blocks; if the ring is
 * full it drops and counts, because stalling this thread would corrupt the
 * timing of every subsequent event.
 * ------------------------------------------------------------------------ */
static void receive_packets(const MIDIPacketList *pktlist, void *srcConnRefCon)
{
    (void)srcConnRefCon;
    const MIDIPacket *pkt = &pktlist->packet[0];

    for (UInt32 i = 0; i < pktlist->numPackets; ++i) {
        uint64_t head = atomic_load_explicit(&g_head, memory_order_relaxed);
        uint64_t tail = atomic_load_explicit(&g_tail, memory_order_acquire);

        if (head - tail >= RING_SLOTS) {
            atomic_fetch_add_explicit(&g_dropped, 1, memory_order_relaxed);
        } else {
            slot_t *s = &g_ring[head % RING_SLOTS];

            /* Documented: zero means "now", i.e. unstamped by the source. */
            if (pkt->timeStamp == 0) {
                s->ts = mach_absolute_time();
                atomic_fetch_add_explicit(&g_ts_zero, 1, memory_order_relaxed);
            } else {
                s->ts = pkt->timeStamp;
            }

            uint32_t n = pkt->length;
            if (n > SLOT_DATA) {
                n = SLOT_DATA;
                atomic_fetch_add_explicit(&g_truncated, 1, memory_order_relaxed);
            }
            s->len = n;
            memcpy(s->data, pkt->data, n);

            atomic_store_explicit(&g_head, head + 1, memory_order_release);
            atomic_fetch_add_explicit(&g_packets, 1, memory_order_relaxed);
        }
        pkt = MIDIPacketNext(pkt);
    }
}

/* ---------------------------------------------------------------------------
 * The writer thread. Ordinary priority, does all the I/O.
 * ------------------------------------------------------------------------ */
static const char HEXD[] = "0123456789ABCDEF";

static void *writer_main(void *arg)
{
    (void)arg;
    /* One line is at most 20 digits + space + 256*3 hex + newline. */
    char buf[65536];
    size_t used = 0;
    uint64_t last_sync = realtime_ns();

    for (;;) {
        int idle = 1;

        for (;;) {
            uint64_t tail = atomic_load_explicit(&g_tail, memory_order_relaxed);
            uint64_t head = atomic_load_explicit(&g_head, memory_order_acquire);
            if (tail == head) break;

            slot_t *s = &g_ring[tail % RING_SLOTS];
            char line[1024];
            int n = snprintf(line, sizeof line, "%llu",
                             (unsigned long long)ticks_to_ns(s->ts));
            for (uint32_t j = 0; j < s->len && n < (int)sizeof line - 4; ++j) {
                line[n++] = ' ';
                line[n++] = HEXD[(s->data[j] >> 4) & 0xF];
                line[n++] = HEXD[s->data[j] & 0xF];
            }
            line[n++] = '\n';

            if (used + (size_t)n > sizeof buf) {
                (void)!write(g_fd, buf, used);
                used = 0;
            }
            memcpy(buf + used, line, (size_t)n);
            used += (size_t)n;

            atomic_store_explicit(&g_tail, tail + 1, memory_order_release);
            idle = 0;
        }

        if (used) {
            (void)!write(g_fd, buf, used);
            used = 0;
        }

        uint64_t now = realtime_ns();
        if ((double)(now - last_sync) / 1e9 >= g_sync_interval) {
            fsync(g_fd);
            last_sync = now;
        }

        if (atomic_load_explicit(&g_stop, memory_order_acquire)) {
            /* One last drain, then out. */
            if (atomic_load_explicit(&g_tail, memory_order_relaxed) ==
                atomic_load_explicit(&g_head, memory_order_acquire))
                break;
        }
        if (idle) usleep(2000); /* 2 ms of *writer* latency; costs no timing */
    }
    fsync(g_fd);
    return NULL;
}

static void on_signal(int sig)
{
    (void)sig;
    atomic_store_explicit(&g_stop, 1, memory_order_release);
}

/* ------------------------------------------------------------------------ */

static char *cf_to_utf8(CFStringRef s)
{
    if (!s) return strdup("");
    CFIndex max = CFStringGetMaximumSizeForEncoding(CFStringGetLength(s),
                                                    kCFStringEncodingUTF8) + 1;
    char *out = malloc((size_t)max);
    if (!CFStringGetCString(s, out, max, kCFStringEncodingUTF8)) out[0] = '\0';
    return out;
}

static char *endpoint_name(MIDIEndpointRef ep)
{
    CFStringRef s = NULL;
    /* kMIDIPropertyDisplayName is the name the user sees in Audio MIDI Setup
     * and the one mido/rtmidi reports, so the two tools agree on port names. */
    if (MIDIObjectGetStringProperty(ep, kMIDIPropertyDisplayName, &s) != noErr || !s) {
        if (MIDIObjectGetStringProperty(ep, kMIDIPropertyName, &s) != noErr) return strdup("");
    }
    char *out = cf_to_utf8(s);
    CFRelease(s);
    return out;
}

static void list_sources(void)
{
    ItemCount n = MIDIGetNumberOfSources();
    if (n == 0) {
        fprintf(stderr, "no CoreMIDI sources present\n");
        return;
    }
    for (ItemCount i = 0; i < n; ++i) {
        char *nm = endpoint_name(MIDIGetSource(i));
        printf("%lu\t%s\n", (unsigned long)i, nm);
        free(nm);
    }
}

static void usage(void)
{
    fprintf(stderr,
        "fp30x-capture v%d — callback-driven CoreMIDI capture with hardware timestamps\n\n"
        "usage:\n"
        "  fp30x-capture -l                        list CoreMIDI sources\n"
        "  fp30x-capture -o FILE [options]         capture until SIGINT\n\n"
        "options:\n"
        "  -o FILE     output file (appended to; required for capture)\n"
        "  -s MATCH    capture only sources whose name contains MATCH\n"
        "              (default: every source present)\n"
        "  -d SECS     stop automatically after SECS seconds\n"
        "  -f SECS     fsync interval, default %.2f\n"
        "  -q          quiet; no progress on stderr\n"
        "  -l          list sources and exit\n",
        TOOL_VERSION, g_sync_interval);
}

int main(int argc, char **argv)
{
    const char *out_path = NULL;
    const char *match = NULL;
    double duration = 0.0;
    int do_list = 0, opt;

    while ((opt = getopt(argc, argv, "o:s:d:f:qlh")) != -1) {
        switch (opt) {
        case 'o': out_path = optarg; break;
        case 's': match = optarg; break;
        case 'd': duration = atof(optarg); break;
        case 'f': g_sync_interval = atof(optarg); break;
        case 'q': g_quiet = 1; break;
        case 'l': do_list = 1; break;
        default:  usage(); return 2;
        }
    }

    mach_timebase_info(&g_tb);

    if (do_list) { list_sources(); return 0; }
    if (!out_path) { usage(); return 2; }

    g_fd = open(out_path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (g_fd < 0) { perror(out_path); return 1; }

    MIDIClientRef client = 0;
    OSStatus err = MIDIClientCreate(CFSTR("fp30x-capture"), NULL, NULL, &client);
    if (err != noErr) { fprintf(stderr, "MIDIClientCreate failed: %d\n", (int)err); return 1; }

    MIDIPortRef port = 0;
    /* MIDIInputPortCreateWithBlock / MIDIPacketList are soft-deprecated in
     * favour of MIDIInputPortCreateWithProtocol / MIDIEventList. We use the
     * packet-list API on purpose: it delivers the MIDI 1.0 bytes exactly as
     * they arrived, with no UMP repacking between the driver and this file.
     * The whole point of this project is arguing from the bytes the instrument
     * actually sent, so a lossless path beats a modern one. Both carry the same
     * MIDITimeStamp, so nothing is given up on timing. */
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    err = MIDIInputPortCreateWithBlock(client, CFSTR("in"), &port,
                                       ^(const MIDIPacketList *pl, void *ref) {
                                           receive_packets(pl, ref);
                                       });
#pragma clang diagnostic pop
    if (err != noErr) { fprintf(stderr, "MIDIInputPortCreate failed: %d\n", (int)err); return 1; }

    /* Header first, so a file that exists at all identifies itself. */
    uint64_t anchor_mach = mach_absolute_time();
    uint64_t anchor_unix = realtime_ns();
    time_t   secs = (time_t)(anchor_unix / 1000000000ull);
    struct tm tmv;
    char iso[64];
    gmtime_r(&secs, &tmv);
    strftime(iso, sizeof iso, "%Y-%m-%dT%H:%M:%SZ", &tmv);

    dprintf(g_fd,
            "# fp30x-capture v%d\n"
            "# columns abs_ns hex_bytes\n"
            "# mach_timebase_numer %u\n"
            "# mach_timebase_denom %u\n"
            "# anchor_mach_ns %llu\n"
            "# anchor_unix_ns %llu\n"
            "# started_utc %s\n",
            TOOL_VERSION, g_tb.numer, g_tb.denom,
            (unsigned long long)ticks_to_ns(anchor_mach),
            (unsigned long long)anchor_unix, iso);

    ItemCount nsrc = MIDIGetNumberOfSources();
    int connected = 0;
    for (ItemCount i = 0; i < nsrc; ++i) {
        MIDIEndpointRef src = MIDIGetSource(i);
        char *nm = endpoint_name(src);
        if (!match || strstr(nm, match)) {
            if (MIDIPortConnectSource(port, src, NULL) == noErr) {
                dprintf(g_fd, "# source %s\n", nm);
                if (!g_quiet) fprintf(stderr, "connected: %s\n", nm);
                connected++;
            }
        }
        free(nm);
    }
    dprintf(g_fd, "# sources_connected %d\n", connected);
    fsync(g_fd);

    if (connected == 0) {
        fprintf(stderr, "no matching CoreMIDI source%s%s — nothing to capture\n",
                match ? " for " : "", match ? match : "");
        /* Not an error: the file is still a valid, honest, empty capture. */
    }

    pthread_t writer;
    pthread_create(&writer, NULL, writer_main, NULL);

    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    if (!g_quiet)
        fprintf(stderr, "capturing to %s — ctrl-c to stop\n", out_path);

    uint64_t t0 = realtime_ns();
    while (!atomic_load_explicit(&g_stop, memory_order_acquire)) {
        /* Run the CFRunLoop so MIDI system notifications are serviced; the
         * receive callback itself is on CoreMIDI's own thread and does not
         * depend on this. 200 ms wake just to poll the stop flag. */
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.2, false);
        if (duration > 0.0 && (double)(realtime_ns() - t0) / 1e9 >= duration)
            atomic_store_explicit(&g_stop, 1, memory_order_release);
        if (!g_quiet)
            fprintf(stderr, "\r%llu packets, %llu dropped   ",
                    (unsigned long long)atomic_load(&g_packets),
                    (unsigned long long)atomic_load(&g_dropped));
    }

    pthread_join(writer, NULL);

    uint64_t stop_unix = realtime_ns();
    secs = (time_t)(stop_unix / 1000000000ull);
    gmtime_r(&secs, &tmv);
    strftime(iso, sizeof iso, "%Y-%m-%dT%H:%M:%SZ", &tmv);
    dprintf(g_fd,
            "# end packets %llu dropped %llu truncated %llu ts_zero %llu stopped_utc %s\n",
            (unsigned long long)atomic_load(&g_packets),
            (unsigned long long)atomic_load(&g_dropped),
            (unsigned long long)atomic_load(&g_truncated),
            (unsigned long long)atomic_load(&g_ts_zero), iso);
    fsync(g_fd);
    close(g_fd);

    MIDIPortDispose(port);
    MIDIClientDispose(client);

    if (!g_quiet)
        fprintf(stderr, "\n%llu packets, %llu dropped, %llu unstamped by source\n",
                (unsigned long long)atomic_load(&g_packets),
                (unsigned long long)atomic_load(&g_dropped),
                (unsigned long long)atomic_load(&g_ts_zero));
    return 0;
}
