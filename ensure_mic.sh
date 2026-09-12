#!/usr/bin/env bash
# Unmute + max the microphone so Kim voice mode always has audio input.
# Idempotent; safe to run at every boot and on every Kim launch.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

src="${KIM_MIC_DEVICE:-${AURORA_MIC_DEVICE:-}}"
if [ -z "$src" ] && [ -f "$DIR/config.yaml" ]; then
  src="$(grep -E '^[[:space:]]*input_device:' "$DIR/config.yaml" | head -1 | sed 's/.*input_device:[[:space:]]*//' | tr -d '"' | tr -d "'")"
fi
if [ -z "$src" ]; then
  src="$(pactl get-default-source 2>/dev/null || true)"
fi
if [ -z "$src" ] || [ "$src" = "auto_null" ] || [ "$src" = "null" ]; then
  echo "ensure_mic: no microphone source found" >&2
  exit 1
fi

dev="$src"
# If the configured name is a short key (e.g. "alsa_input"), resolve to a live source.
if ! pactl list sources short 2>/dev/null | grep -q "$src"; then
  dev="$(pactl list sources short | awk '{print $2}' | grep -F "$src" | head -1)"
fi
if [ -z "$dev" ]; then
  echo "ensure_mic: source '$src' not found" >&2
  exit 1
fi

pactl set-source-mute "$dev" 0 2>/dev/null
pactl set-source-volume "$dev" 100% 2>/dev/null
# Keep the mic as the session default for later clients.
pactl set-default-source "$dev" 2>/dev/null

echo "ensure_mic: ready '$dev' (unmuted, 100%)"
