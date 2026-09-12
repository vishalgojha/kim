#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠  created .env — put your ELEVENLABS_API_KEY in it"
fi

# Ensure the mic is unmuted + maxed (only matters for 'voice', harmless otherwise)
if command -v pactl >/dev/null 2>&1; then
  ./ensure_mic.sh >/dev/null 2>&1 || true
fi

exec .venv/bin/python -m aurora "$@"