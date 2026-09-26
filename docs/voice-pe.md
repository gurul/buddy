---
title: Voice PE build
icon: 💡
order: 2
---

# buddy on a Home Assistant Voice PE

`firmware/buddy_voice_pe` runs buddy on a stock **Home Assistant Voice Preview
Edition** (Nabu Casa NC-VK-9727). The Voice PE has no screen, camera or
motors. It acts as buddy's lights, button, dial and chirps. It uses the same
newline-delimited JSON link and the same `cc-buddy-bridge` daemon as the
[StackChan](stackchan/build.md).

The **microphone and buddy's voice stay on the Mac**. The daemon's wake word
listens on the Mac microphone while the board is connected. Replies play on
the Mac speaker (`CC_BUDDY_VOICE_OUTPUT=audio`), because captions need a
screen.

## What it does

| Input or state | Voice PE |
|---|---|
| Not connected (no message from the daemon for 30 s) | One dim dot circles the ring |
| Connected, nothing running | Faint warm breathe |
| Claude sessions running, or buddy working on a task | Teal comet |
| buddy thinking | Violet double comet |
| buddy listening (wake word, or the Listen key held) | Soft blue breathe, "hm?" chirp |
| buddy speaking | Quick blue pulse |
| A session waits on you | Amber pulse, attention chirp when the count rises |
| Task finished | Green sweep, happy trill |
| Error | Red blink, falling boop |
| Hardware mute switch on | Two red LEDs at the top. Chirps are silenced. The Mac microphone is not affected |

| Control | While a session waits on you | Otherwise |
|---|---|---|
| Center button, tap | Presses Enter on the Mac (approve, pick the selected choice) | A curious chirp, nothing sent |
| Center button, hold 0.7 s | Brings the waiting session's terminal to the front | Nothing |
| Dial | Down / up arrow through the choices (clockwise = down) | Chirp volume, 0–10, shown on the ring |

The button and dial send keys only while the daemon reports a waiting
session. The daemon also ignores them during a voice conversation.

Chirps follow buddy's sound setting (`{"cmd":"sound"}`). When sound is off,
the board is silent.

## Hardware

| Part | Detail |
|---|---|
| Core | ESP32-S3, 16 MB flash, 8 MB octal PSRAM, native USB-Serial/JTAG (`/dev/cu.usbmodem*`, VID 0x303a PID 0x1001) |
| Audio | XMOS XU316 is the I2S clock master. The TI AIC3204 DAC is on I2C 0x18, and GPIO 47 enables the amplifier. The amplifier stays on, as in ESPHome. Switching it for each chirp made an audible click |
| LEDs | 12 WS2812 on GPIO 21, power enable on GPIO 45 |
| Button | GPIO 0, active low. This is also the boot strap pin |
| Dial | Quadrature on GPIO 16 / 18 |
| Mute switch | GPIO 3, HIGH = microphones cut |

`sb_i2s.*` and `sb_codec.*` come from `~/Documents/personal/homeboxV0`
(commit `9c8a4f8`). In that repository, `scripts/check-pins.mjs` and
`scripts/check-aic3204.mjs` compare them against the upstream ESPHome
firmware. This copy has two changes, both from the first run on a real
board. `sb_codec.cpp` waits 3 s after the XMOS reset, reads the DAC back,
and redoes the setup if the DAC lost it. `sb_i2s.cpp` counts failed
speaker writes for the diagnostics below. `sb_config.h` carries the same pin values.
The chirp recipes are the StackChan ones. Each phrase is rendered at 16 kHz
and written x3 to the 48 kHz bus.

## Flash

```bash
./tools/flash_voice_pe.sh                        # finds the Espressif port by USB VID
./tools/flash_voice_pe.sh /dev/cu.usbmodem101    # explicit port
```

The script compiles with
`esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,CDCOnBoot=cdc`
and archives the ELF under `firmware/build-archive/`. It stops the launchd
daemon, which holds the port, uploads, and restarts the daemon if it was
running. Libraries: ArduinoJson 7.4.3 and Adafruit NeoPixel 1.15.5 on ESP32
core 3.3.10.

If the upload cannot connect, hold the center button while you plug in USB.
This starts the ROM bootloader.

Then set the voice output in `~/.config/cc-buddy-bridge/env` and restart the
daemon:

```
CC_BUDDY_VOICE_OUTPUT=audio
```

## Backup and restore of the stock firmware

The first flash replaces ESPHome. The full 16 MB flash was dumped on
2026-09-26 to `firmware/build-archive/voice-pe-factory-20260926.bin` (gitignored,
sha256 `d12eb03b0b142b5a3bf2538608147badc91cdf616d50f6eee35631602461e045`).
`esptool verify-flash` matched it against the chip before the first flash.

```bash
esptool -p /dev/cu.usbmodem101 write-flash 0x0 firmware/build-archive/voice-pe-factory-20260926.bin
```

The official web installer also restores ESPHome:
https://esphome.github.io/home-assistant-voice-pe/

**Dump quirk.** On this board, two 4 KB sectors (0x1cd000 and 0x321000)
stalled every stub-flasher read ("Serial data stream stopped"). They read
cleanly with `esptool --no-stub`. Read in 1 MB pieces, retry a failing
piece with `--no-stub`, then check the joined image with `verify-flash`.

## Audio diagnostics

Stop the daemon, which holds the port, then send these lines at 115200 baud:

| Line | Effect |
|---|---|
| `{"cmd":"tone","hz":660,"ms":2000,"level":0.6}` | Plays a sine even while chirps are off. Prints `[chirp] played in … ms: N bytes to i2s, E write errors` |
| `{"cmd":"dac","page":0,"reg":11}` | Reads one AIC3204 register. Add `"val":N` to write it first |
| `{"cmd":"amp","on":true}` | Sets the amplifier enable pin |

For a working setup, page 0 register 0x0B (NDAC) reads `0x82`, 0x1B (the
audio interface) reads `0x30`, and page 1 register 0x09 (output power)
reads `0x3C`.

**First bench run, 2026-09-26.** Every tone reached the I2S bus with zero
write errors, but nothing played. The DAC had NDAC and MDAC off, 16-bit I2S,
and page 1 at its reset defaults. The writes made straight after the XMOS
reset were lost while the XMOS booted. The speaker played after those
registers were rewritten. The fix is the 3 s wait and the readback check.

## The link

The daemon does not check which board is attached (`serial_transport.py`,
`daemon.py`). This board meets the two requirements it has:

- It prints an `[alive] … live= total= running= waiting= agent=` line every
  5 s. The daemon reopens the port after 20 s without any bytes.
- It answers `{"cmd":"status"}` with
  `{"ack":"status","ok":true,"data":{"name","owner","board":"voice-pe","snd","sys":{"up","heap"}}}`.
  It sends no `sec`, `bat` or `sys.fsTotal`: this board has no BLE, battery
  or LittleFS, and the daemon reads those keys only when they are present.

The board acks `sound`, `led`, `name`, `owner`, `species` and `unpair`. It
ignores the camera, head, face, caption and character-transfer commands.

## Limits

- The Voice PE microphones are not used. Streaming them to the daemon would
  need a new audio path over serial.
- Two ESP32-S3 boards on USB at once (StackChan and Voice PE) would compete
  for the daemon's single serial port.
- `CC_BUDDY_VOICE_OUTPUT=audio` applies to every board. Put it back to
  `captions` when the StackChan is the connected robot.
- The dial's four steps per detent follow the usual encoder layout. They were
  not measured on this unit.
- Audio starts about 6 s after boot: 3 s for the XMOS, then 2.5 s for the DAC.
