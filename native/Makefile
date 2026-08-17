# Native CoreMIDI capture front end for FP-30X Studio.
#
#   make          build both tools into build/
#   make clean    remove build/
#
# No dependencies beyond the macOS SDK.

CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11
LDFLAGS ?= -framework CoreMIDI -framework CoreFoundation

BUILD := build
BINS  := $(BUILD)/fp30x-capture $(BUILD)/fp30x-synth

all: $(BINS)

$(BUILD):
	@mkdir -p $(BUILD)

$(BUILD)/fp30x-capture: fp30x_capture.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

$(BUILD)/fp30x-synth: fp30x_synth.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -rf $(BUILD)

.PHONY: all clean
