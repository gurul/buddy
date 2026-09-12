#!/usr/bin/env bash
# Compile + flash the M5StackChan build (firmware/claude_pet_stackchan).
#
# Same port dance as flash.sh: the CoreS3 uses the ESP32-S3 native
# USB-Serial/JTAG (/dev/cu.usbmodem*), and the bridge daemon holds that
# port exclusively with KeepAlive, so it must be booted out before esptool
# can connect. Archives the exact ELF first so a later panic backtrace stays
# symbolizable.
#
# The first flash replaces the factory firmware. Restore it with the full
# dump taken on 2026-09-05 (see docs/stackchan/capabilities.md):
#   esptool -p /dev/cu.usbmodem* write-flash 0x0 firmware/build-archive/stackchan-factory-20260905.bin
# Do NOT pass -b/--baud on this link: the USB-Serial/JTAG stalls on a baud switch.
set -euo pipefail
cd "$(dirname "$0")/.."

# huge_app keeps LittleFS on the 1MB spiffs partition for GIF character packs,
# same layout as the Freenove pet. CoreS3 PSRAM is QSPI, not OPI.
FQBN="esp32:esp32:m5stack_cores3:PartitionScheme=huge_app,PSRAM=enabled"
SKETCH=firmware/claude_pet_stackchan
PLIST="$HOME/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist"
PORT="${1:-}"
if [ -z "$PORT" ]; then
  # A glob loop, not `ls … | head`: with `set -e` and pipefail, ls exiting 2
  # on an unmatched glob takes the whole script down at this line, before the
  # message below can say why. A board that had not finished re-enumerating
  # after a reset read as a silent exit 1 (bench 2026-09-08).
  for dev in /dev/cu.usbmodem*; do
    [ -e "$dev" ] || continue
    PORT="$dev"
    break
  done
fi
[ -n "$PORT" ] || {
  echo "no /dev/cu.usbmodem* device found — plugged in, and finished enumerating?" >&2
  exit 1
}

SHA=$(git rev-parse --short HEAD)
git diff --quiet || SHA="$SHA-dirty"

BUILD=$(mktemp -d)
# the sha lands in the "[boot] claude_pet_stackchan <sha>" banner the daemon logs
arduino-cli compile -b "$FQBN" --build-path "$BUILD" \
  --build-property "compiler.cpp.extra_flags=-DCLAUDE_PET_GIT_SHA=\"$SHA\"" "$SKETCH"

mkdir -p firmware/build-archive
cp "$BUILD/claude_pet_stackchan.ino.elf" \
   "firmware/build-archive/claude_pet_stackchan-$SHA-$(date +%Y%m%d-%H%M%S).elf"
echo "archived ELF for $SHA"

UPLOAD=(arduino-cli upload -p "$PORT" -b "$FQBN" --input-dir "$BUILD" "$SKETCH")

LABEL=com.github.cc-buddy-bridge.daemon
SVC="gui/$(id -u)/$LABEL"
daemon_loaded() { launchctl print "$SVC" >/dev/null 2>&1; }
port_busy()     { lsof "$PORT" >/dev/null 2>&1; }

if daemon_loaded; then
  # Registered BEFORE the bootout, so a bootout that half-succeeds — or an
  # upload that dies below — still puts the daemon back. Guarded, so a
  # bootout that did not take does not get a second copy bootstrapped onto it.
  trap 'daemon_loaded || { launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "daemon restarted"; }' EXIT

  echo "stopping $LABEL — it holds $PORT exclusively"
  # Never discard what launchctl says. A swallowed bootout error is how this
  # last reached the owner: the daemon stayed up, the wait loop below gave up
  # without a word, and the first sign of trouble was esptool failing with
  # "port is busy" forty lines later (bench 2026-09-08).
  if ! out=$(launchctl bootout "$SVC" 2>&1); then
    echo "  launchctl bootout: ${out:-no output}"
  fi

  # And do not believe the exit code either way: bootout returns EINPROGRESS
  # while a KeepAlive job winds down. The job leaving the domain is the fact.
  for _ in $(seq 1 20); do daemon_loaded || break; sleep 1; done
  if daemon_loaded; then
    echo "$LABEL is still loaded after 20s — refusing to flash into a live daemon." >&2
    echo "try: launchctl bootout $SVC" >&2
    exit 1
  fi

  # The job being gone does not mean the fd is closed; a fixed sleep raced it.
  for _ in $(seq 1 20); do port_busy || break; sleep 1; done
fi

# Reached with or without a daemon: an Arduino serial monitor, a stray
# `cc-buddy-bridge daemon` run by hand, or a screen session holds the port
# just as exclusively. Say who, here, instead of letting esptool say "busy".
if port_busy; then
  echo "$PORT is still held — esptool cannot open it. Holder:" >&2
  lsof "$PORT" >&2 || true
  exit 1
fi

"${UPLOAD[@]}"
echo "flashed $SHA to $PORT"
