#!/usr/bin/env bash
# Launch FP-30X Studio. Creates the virtualenv on first run.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "first run — creating virtualenv..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

if ! command -v fluidsynth >/dev/null 2>&1; then
  echo "fluidsynth is required for audio rendering:"
  echo "    brew install fluid-synth"
fi

exec .venv/bin/python -m fp30x_studio
