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
PORT="${1:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
[ -n "$PORT" ] || { echo "no /dev/cu.usbmodem* device found" >&2; exit 1; }

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
if launchctl list 2>/dev/null | grep -q com.github.cc-buddy-bridge.daemon; then
  launchctl bootout "gui/$(id -u)/com.github.cc-buddy-bridge.daemon" 2>/dev/null || true
  trap 'launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "daemon restarted"' EXIT
  # wait for the reader thread to release the fd; a fixed sleep raced it
  for _ in $(seq 1 20); do
    lsof "$PORT" >/dev/null 2>&1 || break
    sleep 1
  done
fi
"${UPLOAD[@]}"
echo "flashed $SHA to $PORT"
