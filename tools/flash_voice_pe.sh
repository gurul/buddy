#!/usr/bin/env bash
# Compile + flash buddy onto a Home Assistant Voice PE (firmware/buddy_voice_pe).
#
# Same port dance as flash_stackchan.sh: the Voice PE uses the ESP32-S3 native
# USB-Serial/JTAG (/dev/cu.usbmodem*), and the bridge daemon holds that port
# exclusively with KeepAlive, so it must be booted out before esptool can
# connect. Archives the exact ELF first so a later panic backtrace stays
# symbolizable.
#
# The first flash replaces the stock ESPHome firmware. Restore it from the full
# dump taken before the first flash (docs/voice-pe.md):
#   esptool -p /dev/cu.usbmodem* write-flash 0x0 firmware/build-archive/voice-pe-factory-20260926.bin
# Do NOT pass -b/--baud on this link: the USB-Serial/JTAG stalls on a baud switch.
set -euo pipefail
cd "$(dirname "$0")/.."

# 16 MB flash, 8 MB octal PSRAM. CDCOnBoot routes Serial to the native USB.
FQBN="esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,CDCOnBoot=cdc"
SKETCH=firmware/buddy_voice_pe
PLIST="$HOME/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist"
PORT="${1:-}"
if [ -z "$PORT" ]; then
  # Several usbmodem nodes are common (hubs, other boards). Pick the one with
  # Espressif's USB vendor id 0x303a, as the daemon's port picker does.
  PORT=$(python3 -c 'from serial.tools import list_ports
print(next((p.device for p in list_ports.comports() if p.vid == 0x303A and "usbmodem" in p.device), ""))' 2>/dev/null || true)
fi
[ -n "$PORT" ] || {
  echo "no Espressif /dev/cu.usbmodem* found — plugged in, and finished enumerating? (pass the port as the first argument)" >&2
  exit 1
}

SHA=$(git rev-parse --short HEAD)
git diff --quiet || SHA="$SHA-dirty"

BUILD=$(mktemp -d)
# the sha lands in the "[boot] buddy_voice_pe <sha>" banner the daemon logs
arduino-cli compile -b "$FQBN" --build-path "$BUILD" \
  --build-property "compiler.cpp.extra_flags=-DBUDDY_GIT_SHA=\"$SHA\"" "$SKETCH"

mkdir -p firmware/build-archive
cp "$BUILD/buddy_voice_pe.ino.elf" \
   "firmware/build-archive/buddy_voice_pe-$SHA-$(date +%Y%m%d-%H%M%S).elf"
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
