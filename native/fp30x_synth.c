/*
 * fp30x-synth — a virtual CoreMIDI source that emits messages at known
 * intervals, and writes down exactly what it emitted and when.
 *
 * This exists so the capture path can be measured without hands on the piano.
 * It creates a virtual source (MIDISourceCreate) that any CoreMIDI client can
 * connect to — the C capture tool and the old Python one simultaneously — and
 * then emits note-on/note-off pairs spaced by a requested interval.
 *
 * GROUND TRUTH
 * ------------
 * Each message is stamped with mach_absolute_time() read immediately before the
 * MIDIReceived() call, and that stamp is what goes both into the packet and
 * into the ground-truth log. So the log records when the message *actually*
 * left, not when it was *scheduled* to leave. Sender jitter therefore cannot
 * flatter the measurement: it shows up as jitter in the truth, and the capture
 * tools are scored against the truth, not against the ideal grid.
 *
 * The ground-truth log is written in exactly the same format the capture tool
 * emits, so one Python reader loads both and the comparison is a subtraction.
 *
 * BUILD   cc -O2 -Wall -Wextra -framework CoreMIDI -framework CoreFoundation \
 *            -o fp30x-synth fp30x_synth.c
 */

#include <CoreFoundation/CoreFoundation.h>
#include <CoreMIDI/CoreMIDI.h>
#include <mach/mach_time.h>

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define TOOL_VERSION 1

static mach_timebase_info_data_t g_tb;

static inline uint64_t ticks_to_ns(uint64_t t)
{
    return (uint64_t)(((__uint128_t)t * g_tb.numer) / g_tb.denom);
}

static inline uint64_t ns_to_ticks(uint64_t ns)
{
    return (uint64_t)(((__uint128_t)ns * g_tb.denom) / g_tb.numer);
}

static uint64_t realtime_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void usage(void)
{
    fprintf(stderr,
        "fp30x-synth v%d — virtual CoreMIDI source emitting at known intervals\n\n"
        "usage: fp30x-synth -t TRUTHFILE [options]\n\n"
        "  -t FILE     ground-truth log (same format as fp30x-capture output)\n"
        "  -n NAME     virtual source name (default: FP30X-Synth)\n"
        "  -i USEC     interval between messages in microseconds (default 1000)\n"
        "  -c COUNT    number of messages to send (default 200)\n"
        "  -w SECS     wait this long before sending, so clients can connect\n"
        "              (default 1.5)\n",
        TOOL_VERSION);
}

int main(int argc, char **argv)
{
    const char *truth_path = NULL;
    const char *name = "FP30X-Synth";
    double interval_us = 1000.0;
    long count = 200;
    double warmup = 1.5;
    int opt;

    while ((opt = getopt(argc, argv, "t:n:i:c:w:h")) != -1) {
        switch (opt) {
        case 't': truth_path = optarg; break;
        case 'n': name = optarg; break;
        case 'i': interval_us = atof(optarg); break;
        case 'c': count = atol(optarg); break;
        case 'w': warmup = atof(optarg); break;
        default:  usage(); return 2;
        }
    }
    if (!truth_path) { usage(); return 2; }

    mach_timebase_info(&g_tb);

    MIDIClientRef client = 0;
    if (MIDIClientCreate(CFSTR("fp30x-synth"), NULL, NULL, &client) != noErr) {
        fprintf(stderr, "MIDIClientCreate failed\n");
        return 1;
    }

    CFStringRef cfname = CFStringCreateWithCString(NULL, name, kCFStringEncodingUTF8);
    MIDIEndpointRef src = 0;
    if (MIDISourceCreate(client, cfname, &src) != noErr) {
        fprintf(stderr, "MIDISourceCreate failed\n");
        return 1;
    }
    CFRelease(cfname);

    int fd = open(truth_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror(truth_path); return 1; }

    uint64_t anchor_mach = mach_absolute_time();
    uint64_t anchor_unix = realtime_ns();
    time_t secs = (time_t)(anchor_unix / 1000000000ull);
    struct tm tmv;
    char iso[64];
    gmtime_r(&secs, &tmv);
    strftime(iso, sizeof iso, "%Y-%m-%dT%H:%M:%SZ", &tmv);

    dprintf(fd,
            "# fp30x-capture v%d\n"
            "# columns abs_ns hex_bytes\n"
            "# mach_timebase_numer %u\n"
            "# mach_timebase_denom %u\n"
            "# anchor_mach_ns %llu\n"
            "# anchor_unix_ns %llu\n"
            "# started_utc %s\n"
            "# source %s\n"
            "# role ground-truth\n"
            "# nominal_interval_ns %llu\n",
            TOOL_VERSION, g_tb.numer, g_tb.denom,
            (unsigned long long)ticks_to_ns(anchor_mach),
            (unsigned long long)anchor_unix, iso, name,
            (unsigned long long)(uint64_t)(interval_us * 1000.0));

    fprintf(stderr, "virtual source \"%s\" is up; waiting %.2fs for clients\n",
            name, warmup);
    /* Let CoreMIDI publish the endpoint and let capture clients connect. */
    uint64_t wait_until = mach_absolute_time() + ns_to_ticks((uint64_t)(warmup * 1e9));
    mach_wait_until(wait_until);

    uint64_t step = ns_to_ticks((uint64_t)(interval_us * 1000.0));
    uint64_t next = mach_absolute_time() + ns_to_ticks(1000000ull); /* +1 ms */

    for (long i = 0; i < count; ++i) {
        mach_wait_until(next);

        /* Alternate note-on / note-off so the stream is well-formed MIDI and
         * the existing performance.py parser can consume it unchanged. */
        uint8_t note = (uint8_t)(60 + (i / 2) % 12);
        uint8_t msg[3];
        if (i % 2 == 0) { msg[0] = 0x90; msg[1] = note; msg[2] = (uint8_t)(1 + (i % 127)); }
        else            { msg[0] = 0x80; msg[1] = note; msg[2] = 64; }

        MIDIPacketList pl;
        MIDIPacket *cur = MIDIPacketListInit(&pl);

        /* Stamp as late as possible: this is the honest emission time. */
        uint64_t now = mach_absolute_time();
        cur = MIDIPacketListAdd(&pl, sizeof pl, cur, now, sizeof msg, msg);
        if (!cur) { fprintf(stderr, "MIDIPacketListAdd failed\n"); return 1; }
        MIDIReceived(src, &pl);

        dprintf(fd, "%llu %02X %02X %02X\n",
                (unsigned long long)ticks_to_ns(now), msg[0], msg[1], msg[2]);

        next += step;
    }

    uint64_t stop_unix = realtime_ns();
    secs = (time_t)(stop_unix / 1000000000ull);
    gmtime_r(&secs, &tmv);
    strftime(iso, sizeof iso, "%Y-%m-%dT%H:%M:%SZ", &tmv);
    dprintf(fd, "# end packets %ld dropped 0 truncated 0 ts_zero 0 stopped_utc %s\n",
            count, iso);
    fsync(fd);
    close(fd);

    fprintf(stderr, "sent %ld messages at %.0f us nominal interval\n", count, interval_us);

    /* Give clients a moment to drain before the endpoint disappears. */
    mach_wait_until(mach_absolute_time() + ns_to_ticks(300000000ull));

    MIDIEndpointDispose(src);
    MIDIClientDispose(client);
    return 0;
}
