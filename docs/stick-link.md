# The stick link: buddy in your pocket

An M5StickS3 becomes a push-to-talk button for buddy that works anywhere your
phone has signal. Hold the front button, talk, let go. buddy answers out of
the stick's speaker. The stick talks to your iPhone over Bluetooth, and the
**Buddy Link** app on the iPhone carries each press to the daemon on your Mac.
It uses the same path as **Call buddy** in the Mini App.

```
StickS3 ──Bluetooth LE──▶ iPhone (Buddy Link) ──HTTPS / WebSocket──▶ Cloudflare tunnel ──▶ daemon on the Mac
  mic, speaker, screen      relay, pairing          /api/stick                               phone_call.py, the chat's brain
```

The owner asked for this on 2026-09-30: *"it would connect to my iphone which
would communicate to the daemon so i can talk to it from anywhere"*. buddy's
voice plays on the stick's speaker. The phone learns the tunnel's changing
address from a tap-to-update link in Telegram.

A press is a message to the same brain you text. Everything a Telegram
message can do, a press can do too: mail, calendar, tasks on the Mac, watches,
notes and memory. See [calling buddy](stackchan/miniapp.md#calling-buddy).

## Using it

| On the stick | What happens |
|---|---|
| Hold the front button (A) | Talk. A tone when the microphone opens. Let go to send. A lower tone confirms. |
| Press A while buddy talks | buddy stops, and your new press is heard. |
| Click the side button (B) | Cycles through four volume levels. |
| Hold B for 3 seconds | Forgets the paired phone, so a new phone can pair. |
| Hold B while powering on | Self-test: A records, and letting go plays it back. No phone needed. |

The screen shows the link (`phone` / `no phone`), the battery, the volume and
the turn: **hold to talk**, **listening**, **thinking**, **speaking** or
**buddy offline**, with what buddy heard or a note underneath. While buddy
thinks, the stick ticks quietly until the first sound of the reply (the idea
comes from Era's m5-atom-puck).

A call opens when you pick the stick up (or on your first press) and hangs up after 45 quiet seconds. It
takes buddy's chat only from your first press, and gives it back after 8 idle seconds if the Voice PE's button
wants it, so an open stick call never blocks the desk. While a
call is open, buddy's other chat replies are read out too, so it does not
stay open all day.

## Sound levels and transcription

Tuned on 2026-09-30, after the owner said the speaker was "really quiet" and transcription was "terrible":

- **Speaker:** M5Unified leaves the ES8311 DAC at 0 dB (register `0x32 = 0xBF`). The stick sets it to +12 dB (`0xD7`) each time the speaker starts. Click the side volume button for the four levels on top of that.
- **Microphone:** M5Unified's default x16 digital gain clipped presses spoken up close (peaks at 32752). The stick uses x8, which leaves 6 dB of headroom.
- **buddy's ears:** calls are transcribed by `gpt-4o-transcribe`, not the mini model; `CC_BUDDY_CALL_STT_MODEL` overrides it. The live session cuts a press only at pauses of 0.7 s (it was 0.4 s, which split sentences), with OpenAI's near-field noise reduction for a microphone held close.

## Battery

The owner asked on 2026-09-30 for the stick's battery life to be optimised. The 250 mAh battery and the stock
Arduino core set the limits: the core is built without power management or Bluetooth modem sleep, so the
ESP32-S3 cannot light-sleep while it stays connected. The StickS3 also has no 32 kHz crystal (its pins carry
audio), so even a custom build would only reach about 3.3 mA. The stick instead keeps each part powered only
while it is in use. `firmware/buddy_stick/power_policy.h` makes those decisions, and the host tests cover it.

| Part | What the stick does | What it saves (source) |
|---|---|---|
| CPU | 80 MHz instead of 240 (`setCpuFrequencyMhz`). BLE holds the bus at 80 MHz anyway, and I2S runs off its own clock. | Idle current drops from about 33 mA to 22 mA (ESP32-S3 datasheet, table 5-9). |
| Speaker and codec | Off 4 s after the last sound, and back on when audio arrives or a tone plays. `Speaker.end()` turns off only the amplifier, so the stick also powers down the ES8311 codec. | Amplifier about 10 mA, codec about 8 mA (AW8737 and ES8311 datasheets). |
| Screen | Off (`Display.sleep()`: backlight off, ST7789 asleep) after 20 s with nothing new. A button, picking it up, or a message from the phone wakes it. | Backlight and panel. |
| Bluetooth timing | 15–30 ms while a turn is on and for 20 s after. Idle, it asks for 150–180 ms with 4 events of latency. Picking the stick up asks for the fast interval again before you press. | Radio wake-ups between turns. The values follow Apple's Accessory Design Guidelines (R31, 58.6). |
| Advertising | 417.5 ms while no phone is connected. | One of Apple's recommended intervals (58.5). |
| Main loop | Waits 20 ms per pass when idle, instead of 2 ms. | CPU time. |
| Power off | After 30 min lying still on battery (no motion, button or turn), the M5PM1 powers everything off, about 14 µA. Picking it up wakes it: the BMI270's any-motion interrupt, on feature page 1 at `0x3C`, drives the M5PM1's GPIO4. The power button wakes it too. It never powers off on USB. | About 14 µA while off (M5Stack's StickS3 page). |

Sources:
- [ESP32-S3 datasheet](https://documentation.espressif.com/esp32-s3_datasheet_en.pdf)
- [Espressif low-power BLE guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/low-power-mode/low-power-mode-ble.html)
- [Apple Accessory Design Guidelines](https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf)
- [M5Stack StickS3 low-power configuration](https://docs.m5stack.com/en/arduino/m5sticks3/m5pm1)
- [M5PM1 library](https://github.com/m5stack/M5PM1)
- [Bosch BMI270 API](https://github.com/boschsensortec/BMI270_SensorAPI)

Expected: roughly 50 mA down to about 25–30 mA while connected and idle, so about 5 h becomes about 8–10 h, and
the stick lasts days lying unused. These figures are estimates from the datasheets, not measurements. The M5PM1
reports battery voltage but not current, so the stick logs the voltage every 5 minutes since boot. Plug it into
USB and it prints the log (`[pwr] battery mV every 5 min since boot`); type `pwr` on its serial port to print
it again. Bench commands on its serial port:

- `off` powers it off on the spot. On USB the power chip turns straight back on, because USB power is itself a wake source.
- `offsoon` arms a power-off: after unplugging, it powers off once it has lain still for 15 s, which tests the wake on pick-up.
- `imutest` arms the motion wake without powering off and watches the wake line for 10 s. The line must stay high while the stick is still and go low when it moves.
- `pwr` also says what woke the stick last: `0x20` is the motion sensor, `0x04` the power button, `0x08` reset.

Tuned on the device, 2026-09-30:

- **The sensor's INT1 is open-drain, with the M5PM1's pull-up on GPIO4.** With push-pull, the line fell as the rails switched, and the stick woke the moment it powered off.
- **A pick-up is about 200 mg held for 200 ms.** At 125 mg for 100 ms, footsteps on the floor woke it.

The owner confirmed it powers off, stays off, and wakes when picked up.

## Setting it up

You need the StickS3, an iPhone with Buddy Link, and the daemon with the
Telegram door and the Mini App on (`CC_BUDDY_TELEGRAM=1`, `CC_BUDDY_MINIAPP=1`,
[Mini App setup](stackchan/miniapp.md#turning-it-on)).

1. **Flash the stick** ([Flashing](#flashing)).
2. **Install Buddy Link on the iPhone.** Run `sh ios/BuddyLink/build.sh --install`
   with the iPhone unlocked. It is built with the team in `ios/BuddyLink/project.yml`.
3. **Pair the stick with the phone.** Open Buddy Link; it finds the stick by
   itself. iOS asks for a code. Type the six digits on the stick's screen. The
   bond is kept after that, and the stick reconnects on its own when it comes
   back in range, even with the phone locked.
4. **Pair the phone with buddy** ([Pairing the phone](#pairing-the-phone)).

Then hold A and talk.

## Pairing the phone

Send **`/stick`** to buddy in Telegram. It replies with an **Update Buddy
Link** button. Tap it, then tap **Open in Buddy Link** on the page that opens.
The app saves buddy's address and a link token in the Keychain.

The Mini App's quick tunnel gets a new address each time the daemon starts.
Once you have paired, the pinned **buddy: your apps.** message carries the
**Update Buddy Link** button too, and it is edited to the new address with the
Open button. So after a restart:

- the stick shows **buddy offline** with "buddy's address changed. Tap Update
  Buddy Link in Telegram", and
- one tap on the pinned message's button fixes it.

For an address that never changes, use a named Cloudflare tunnel or Tailscale
Funnel ([why the tunnel](stackchan/miniapp.md#why-the-cloudflare-tunnel)).

`/stick new` makes a new token. The old one stops working at once. Use it if a
phone is lost.

## The wire

**Bluetooth.** The stick is a GATT peripheral named `buddy stick` with one
service, `6a5ad52d-ec26-4b36-9b2d-0e4ef9d2f2bc`:

| Characteristic | UUID | Direction |
|---|---|---|
| UP | `19a1cbb3-15c5-4596-bcae-65985cfe7226` | stick → phone, notify |
| DOWN | `dfc25597-3956-43b0-8c38-a7e5dc928505` | phone → stick, write without response |

Both need an encrypted, authenticated link. Pairing is passkey entry: the
stick shows a new random code each time (`ESP_IO_CAP_OUT`, MITM and secure
connections, bonded), so nobody nearby can pair with its microphone. The
stick drops every write from a link that is not secured.

Each value is one frame. Its first byte is the kind:

- `0x01` **audio:** `[seq][predictor, int16 LE][step index]` followed by IMA
  ADPCM codes, two samples per byte, earlier sample in the low nibble. Each
  frame carries its coder state, so a lost frame costs only its own ~15 ms.
  The sequence number counts the losses.
- `0x02` **JSON:** UTF-8 text.

Audio is 24 kHz mono, the call's own rate, so nothing is resampled. ADPCM
makes it 12 KB/s, a quarter of raw PCM. A frame holds up to 244 bytes (478
samples, about 20 ms).

| JSON from the stick | Meaning |
|---|---|
| `{"t":"hello","fw","rate","batt"}` | Reply to the phone's `{"t":"hi"}` after subscribing |
| `{"t":"talk"}`, `{"t":"done"}` | A press began or ended; audio frames come between them |
| `{"t":"ping"}` | Once a second while a turn is live, and for 20 s after, so a locked phone keeps relaying |
| `{"t":"stat","played","buffered","lost","payload"}` | Reply to `{"t":"stat"}` (bench) |

| JSON to the stick | Meaning |
|---|---|
| `{"t":"state","s","note"}` | `listening`, `thinking`, `speaking`, `ready` or `offline`, with an optional note |
| `{"t":"heard","text"}` | What buddy transcribed, folded to ASCII for the stick's font |
| `{"t":"flush"}` | Drop the queued reply (you interrupted) |
| `{"t":"end"}` | The call hung up |

The three copies of the codec must agree byte for byte:
`tools/stick_link/adpcm.py` (the reference), `firmware/buddy_stick/link_codec.h`
and `ios/BuddyLink/Shared/ADPCM.swift`. `tools/stick_link/vectors.txt`, written
by the reference, is what each of the three tests checks against.

**The call.** Buddy Link opens `wss://<tunnel>/api/stick`. Its first message is
`{"token": …}`, and after that it speaks the Mini App call's own protocol
(`talk`, binary PCM, `done`, `ping`, `end`; `state`, `heard`, `flush`,
`ended`). The phone batches the press into 100 ms WebSocket frames and
re-frames buddy's audio for Bluetooth, pacing its writes to what the radio takes.

## The link token

`stick_link.py` keeps one random token (`secrets.token_urlsafe(32)`) in
`~/.config/cc-buddy-bridge/stick-link.json`, mode 0600, next to the Mini App's
ledger. It is made on the first `/stick` and replaced by `/stick new`.
`/api/stick` accepts only this token, compared in constant time. It never
accepts Telegram's initData, and `/api/call` never accepts the token.

- **A browser cannot use `/api/stick`.** Browsers send an `Origin` on every
  WebSocket and the app sends none, so a request with an Origin gets 403.
- **The server never sees the token in a URL.** The pairing link is
  `https://<tunnel>/stick#t=<token>`, and the fragment never leaves the phone.
  The page at `/stick` is static and has a strict CSP (`default-src 'none'`)
  and `Referrer-Policy: no-referrer`. It reads the fragment, clears it from
  the address bar, and offers `buddylink://pair?u=<origin>&t=<token>`.
- **On the phone,** the token is in the Keychain, readable after the first
  unlock (so a locked phone can still call) and only on this device.

## Flashing

```sh
tools/flash_stick.sh                    # finds the stick, builds, flashes
tools/flash_stick.sh --compile-only     # just build
tools/flash_stick.sh /dev/cu.usbmodemX  # a given port
```

The build uses arduino-cli with esp32 core 3.3.10, M5Unified 0.2.21 (StickS3
support), M5GFX and ArduinoJson 7.4.3. The board is
`esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB,PSRAM=opi`.
Each build's ELF is archived in `firmware/build-archive/` so a crash
backtrace can be decoded later.

The script finds the stick by `CC_BUDDY_STICK_SERIAL` (its USB serial, which
is its MAC), or by the "StickS3" name UiFlow2 gives itself.

**Download mode.** If the stick does not show up, hold the side reset button
for about 2 seconds, until the green light inside blinks. The screen stays
dark in download mode. **Afterwards, tap reset once, briefly, to leave it:**
the power chip holds the boot pin low until then, so the new firmware does
not start. The serial log shows this as
`boot:0x21 (DOWNLOAD(USB/UART0)) … waiting for download`.

**The daemon and the stick's cable.** The robot's port glob
(`/dev/cu.usbmodem*`) matches every ESP32-S3. Put the stick's USB serial in
`CC_BUDDY_SERIAL_SKIP` (comma-separated) and the daemon never opens it. It
did before this setting: with the StackChan unplugged, the daemon opened the
stick as the robot and reset it every two minutes.

**Host tests** (run on the Mac, no board needed):

```sh
python3 tools/stick_link/test_adpcm.py     # the reference codec, and that vectors.txt is current
sh firmware/buddy_stick/test/run.sh        # the stick's codec, ring and timers, under ASan and UBSan
sh ios/BuddyLink/Tests/run.sh              # the app's codec
```

**Bench probe.** `ios/BuddyLink/build.sh` also builds **BuddyProbe**, a small
Mac app that runs the phone's own Bluetooth code:

```sh
open -n --env BUDDY_PROBE_OUT=/tmp/probe.txt ios/BuddyLink/build/Build/Products/Debug/BuddyProbe.app --args tone
```

- `tone` sends a second of tone and checks the stick played all of it (`STICK_PROBE_OK`).
- `listen` reports one press's length and loudness (`STICK_PRESS_OK` above −30 dBFS).
- `relay <url> <token>` makes the Mac the phone.

It is an app rather than a script because macOS kills any process that touches
Bluetooth from a terminal whose app declares no Bluetooth use (Warp does not).
The first run asks for Bluetooth permission.

## Back to UiFlow2

The first flash overwrote UiFlow2. `tools/flash_stick.sh` backs up the whole
8 MB before a first flash (`firmware/build-archive/sticks3-uiflow2-<date>.bin`,
not in git). On 2026-09-30 that backup failed partway through ("the chip
stopped responding") and the flash went ahead, so no copy of this stick's
UiFlow2 was kept. To go back, flash UiFlow2 for the StickS3 from M5Burner
(M5Stack's flashing tool, which carries the official UiFlow2 images). If a
backup exists:

```sh
~/Library/Arduino15/packages/esp32/tools/esptool_py/5.3.0/esptool -p /dev/cu.usbmodemX write-flash 0x0 firmware/build-archive/sticks3-uiflow2-<date>.bin
```

## Status

What was checked and how is in [GATES.md](../GATES.md). As of 2026-09-30 it all works through the iPhone: a press on the stick, Buddy Link relaying, buddy hearing it and answering on the stick's speaker (live transcription about 0.3-1 s; release to buddy's first sound 2.4-3.9 s on the measured presses). Picking the stick up opens the call before the press.

Still open: a press with the phone locked and away from home Wi-Fi (G5.3), and the stick's played/lost counters, which count from boot rather than per connection.
