#!/bin/sh
# Run BuddyProbe (ios/BuddyLink/Probe) on the bench and print its log: tone | listen | relay <url> <token>.
# An app, launched with `open`, because macOS kills Bluetooth use from a terminal that declares none.
set -eu
cd "$(dirname "$0")/../.."
APP=ios/BuddyLink/build/Build/Products/Debug/BuddyProbe.app
[ -d "$APP" ] || sh ios/BuddyLink/build.sh >/dev/null
OUT=$(mktemp)
open -n -W --env BUDDY_PROBE_OUT="$OUT" ${BUDDY_PROBE_WAV:+--env BUDDY_PROBE_WAV="$BUDDY_PROBE_WAV"} "$APP" --args "$@"
cat "$OUT"
