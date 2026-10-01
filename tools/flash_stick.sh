#!/usr/bin/env bash
# Compile + flash buddy onto an M5StickS3 (firmware/buddy_stick, docs/stick-link.md).
#
#   tools/flash_stick.sh                  find the stick, back up its flash the first time, flash
#   tools/flash_stick.sh /dev/cu.usbmodemX   that port
#   tools/flash_stick.sh --compile-only   just build
#
# Finding it: CC_BUDDY_STICK_SERIAL (its USB serial number, from the environment or the daemon's env file), else
# the one node that says StickS3 (UiFlow2's name for itself, before the first flash). Never a guess between two
# Espressif boards: the StackChan and the Voice PE are ESP32-S3s on the same /dev/cu.usbmodem* glob.
#
# The first flash replaces UiFlow2. The whole 8 MB is read out first to
# firmware/build-archive/sticks3-uiflow2-<date>.bin (ignored by git); docs/stick-link.md says how to write it back.
#
# The daemon is stopped while flashing only when it holds the stick's port (it did before CC_BUDDY_SERIAL_SKIP
# named the stick), and is put back on exit either way.
set -euo pipefail
cd "$(dirname "$0")/.."

# 8 MB flash, 8 MB octal PSRAM, Serial on the native USB (the StickS3's docs: USB mode 1, CDC on boot).
FQBN="esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB,PSRAM=opi"
SKETCH=firmware/buddy_stick
ESPTOOL="$HOME/Library/Arduino15/packages/esp32/tools/esptool_py/5.3.0/esptool"
PLIST="$HOME/Library/LaunchAgents/com.github.cc-buddy-bridge.daemon.plist"
PY=bridge/.venv/bin/python3
[ -x "$PY" ] || PY=../buddy/bridge/.venv/bin/python3
[ -x "$PY" ] || PY=python3

COMPILE_ONLY=0
PORT=""
for arg in "$@"; do
  case "$arg" in
    --compile-only) COMPILE_ONLY=1 ;;
    *) PORT="$arg" ;;
  esac
done

SHA=$(git rev-parse --short HEAD)
[ -z "$(git status --porcelain -- firmware/buddy_stick)" ] || SHA="$SHA-dirty"
BUILD=$(mktemp -d)
arduino-cli compile -b "$FQBN" --build-path "$BUILD" \
  --build-property "compiler.cpp.extra_flags=-DBUDDY_GIT_SHA=\"$SHA\"" "$SKETCH"
mkdir -p firmware/build-archive
cp "$BUILD/buddy_stick.ino.elf" "firmware/build-archive/buddy_stick-$SHA-$(date +%Y%m%d-%H%M%S).elf"
echo "archived ELF for $SHA"
[ "$COMPILE_ONLY" = 1 ] && exit 0

STICK_SERIAL="${CC_BUDDY_STICK_SERIAL:-$(sed -n 's/^CC_BUDDY_STICK_SERIAL=//p' "$HOME/.config/cc-buddy-bridge/env" 2>/dev/null | tail -1)}"
if [ -z "$PORT" ]; then
  PORT=$(WANT="$STICK_SERIAL" "$PY" -c 'import os, sys
from serial.tools import list_ports
want = os.environ.get("WANT", "").lower()
ports = [p for p in list_ports.comports() if "/cu.usbmodem" in p.device]
hits = [p.device for p in ports if want and (p.serial_number or "").lower() == want]
if not hits:
    hits = [p.device for p in ports if "sticks3" in (p.description or "").lower()]
if len(hits) != 1:
    sys.stderr.write("found %d sticks: set CC_BUDDY_STICK_SERIAL or pass the port\n" % len(hits))
    hits = []
print(hits[0] if hits else "")' || true)
fi
[ -n "$PORT" ] || { echo "no StickS3 found (pass the port as the first argument)" >&2; exit 1; }

LABEL=com.github.cc-buddy-bridge.daemon
SVC="gui/$(id -u)/$LABEL"
daemon_loaded() { launchctl print "$SVC" >/dev/null 2>&1; }
port_busy()     { lsof "$PORT" >/dev/null 2>&1; }

# Only when the daemon's own process holds the port: "some python holds it" also matched a serial monitor on the
# bench (2026-09-30) and restarted the daemon, which moved the Mini App to a new port under a live call.
DAEMON_PID=$(launchctl print "$SVC" 2>/dev/null | awk '/^\tpid = / {print $3}')
if port_busy && daemon_loaded && [ -n "$DAEMON_PID" ] && lsof -t "$PORT" 2>/dev/null | grep -qx "$DAEMON_PID"; then
  trap 'daemon_loaded || { launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "daemon restarted"; }' EXIT
  echo "stopping $LABEL — it holds $PORT"
  if ! out=$(launchctl bootout "$SVC" 2>&1); then echo "  launchctl bootout: ${out:-no output}"; fi
  for _ in $(seq 1 20); do daemon_loaded || break; sleep 1; done
  daemon_loaded && { echo "$LABEL still loaded after 20s — not flashing into a live daemon" >&2; exit 1; }
  for _ in $(seq 1 20); do port_busy || break; sleep 1; done
fi
if port_busy; then
  echo "$PORT is still held. Holder:" >&2
  lsof "$PORT" >&2 || true
  exit 1
fi

# The first time, keep what was there. UiFlow2 names itself in the USB description; buddy's firmware does not.
DESC=$("$PY" -c "from serial.tools import list_ports; print(next((p.description or '' for p in list_ports.comports() if p.device == '$PORT'), ''))")
if echo "$DESC" | grep -qi uiflow; then
  BACKUP="firmware/build-archive/sticks3-uiflow2-$(date +%Y%m%d).bin"
  if [ ! -s "$BACKUP" ]; then
    echo "backing up the stick's 8 MB (UiFlow2) to $BACKUP"
    "$ESPTOOL" -p "$PORT" read-flash 0 0x800000 "$BACKUP"
  fi
fi

arduino-cli upload -p "$PORT" -b "$FQBN" --input-dir "$BUILD" "$SKETCH"
echo "flashed $SHA to $PORT"
