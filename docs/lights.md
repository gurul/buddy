# Lights

buddy controls your lights from Telegram, the chat window, a Mini App call,
the Voice PE and the robot's voice. It works with three brands. Each brand
uses its own protocol, and none needs a cloud account or a hub once it is set
up.

| Brand | How buddy talks to it | What setup needs |
|---|---|---|
| **Govee** | Govee's LAN API: JSON over UDP on your Wi-Fi | Turn on **LAN Control** for each light in the Govee Home app. No key. |
| **HappyLighting** | Bluetooth LE, the "Triones" protocol that QH-tek's controllers use. Their names start with `QHM-`. | The controller must be within Bluetooth range of the Mac. No key. |
| **Sylvania Smart+ Wi-Fi** | Tuya's local protocol through [tinytuya](https://github.com/jasonacox/tinytuya), on your Wi-Fi | Each device's **local key**, obtained once (see [Sylvania](#sylvania-smart-wi-fi-tuya)). |

The code is `bridge/src/cc_buddy_bridge/lights.py`.

## The Mini App's Lights card

In Telegram, open buddy's Mini App (the **Open buddy** button pinned in the
chat, or `/apps`). The **Lights** card at the top has On, Off, colour swatches
and a brightness slider. By default a tap changes every light, and you can
pick a room or one light instead. See
[the Mini App](stackchan/miniapp.md#lights).

## What you can say

A message that is **only** a light command is done by code, with no model
turn. It takes about a second: less on Wi-Fi, more when Bluetooth has to
connect. buddy replies with one line, such as "Lights blue." or
"Floor lamp off."

| You say | What happens |
|---|---|
| "lights off", "turn off the lights", "lights out" | Every light off |
| "lights blue", "set all the lights to purple" | Every light that colour |
| "make the lights warm white", "lights daylight", "lights to 2700K" | A white at that colour temperature |
| "lights #ff8800" | An exact colour |
| "dim the lights" | 20% |
| "bedroom lights to 30%", "brighten the lamp" | A brightness level (brighten is 100%) |
| "floor lamp red at 40%" | One light, a colour and a level |
| "turn off the lamp and the strip" | Several named lights |

"Lights" with no name means **every** light. A single word that appears in
only one light's name works as its name, so "the lamp" means "floor lamp".
A room name means every light in that room.

Everything else goes to the model, which has two tools: `lights_set` and
`lights_status`. For example, "make it cozy in here", "movie mode", "are the
lights on?" and "turn the lights off in ten minutes" all go to the model. It
picks the settings for a mood itself. Cozy is warm white at about 30%.

**Colours:** red, orange, amber, yellow, gold, lime, green, mint, teal, cyan,
turquoise, sky blue, blue, navy, indigo, purple, violet, lavender, magenta,
pink, hot pink, rose, coral, salmon, peach. **Whites:** candle (2000K), warm
white (2700K), soft white (3000K), white or neutral (4000K), cool white
(5500K), daylight (6500K).

A change sent to several lights goes to all of them at the same time. A light
that fails does not stop the others. The reply names the light that failed
and the reason.

## Set up

1. **Find the lights.** This command adds every light it finds to
   `~/.config/cc-buddy-bridge/lights.json`:

   ```bash
   cc-buddy-bridge lights scan
   ```

   The Bluetooth part needs Bluetooth permission for the program that runs
   it. macOS stops a terminal that has no permission at the first scan
   (exit code 134). If that happens, run `lights scan --no-ble`, then grant
   the terminal Bluetooth permission in System Settings → Privacy & Security
   → Bluetooth and scan again. The daemon's own Python, run by launchd, has
   its own Bluetooth permission.

2. **Name them.** A scan gives each light a default name, such as
   `govee h6076` or `happylighting qhm-sa01`. Rename each light and give it a
   room:

   ```bash
   cc-buddy-bridge lights name "govee h6076" "floor lamp" --room bedroom
   cc-buddy-bridge lights name "happylighting qhm-sa01" "strip" --room bedroom
   ```

   You can also add `"aliases": ["led strip"]` to a light in `lights.json`.

3. **Try them.**

   ```bash
   cc-buddy-bridge lights list
   cc-buddy-bridge lights set all --color blue --brightness 40
   cc-buddy-bridge lights set lamp off
   ```

4. **Restart the daemon.** It reads `lights.json` when it starts:

   ```bash
   launchctl kickstart -k gui/$(id -u)/com.github.cc-buddy-bridge.daemon
   ```

A scan keeps the names, rooms and keys you set. When DHCP gives a Govee light
a new address, buddy finds the light again by its device ID.

### Sylvania Smart+ Wi-Fi (Tuya)

A scan finds a Tuya device and its IP without a key. To control the device,
buddy also needs the device's local key:

1. Install the extra: `pip install -e "bridge[lights]"` (tinytuya).
2. Create a free account on the [Tuya developer platform](https://platform.tuya.com),
   and create a cloud project in your region.
3. Link your app account to the project. Tuya links its own **Smart Life** or
   **Tuya Smart** app accounts. If the Sylvania app cannot link, add the light
   to Smart Life instead: it is the same Tuya device.
4. Run `python -m tinytuya wizard` and give it the project's access ID and
   secret. It lists every device with its `id` and `key`.
5. Give the light its key and a name:

   ```bash
   cc-buddy-bridge lights name "tuya 9a2f" "desk bulb" --room office --key <local key>
   ```

A light's key changes when it is paired again. After you pair it again, get
the new key.

## Files and switches

| What | Where |
|---|---|
| The lights: names, rooms, addresses, Tuya keys | `~/.config/cc-buddy-bridge/lights.json`, mode 600. Never in the repo. |
| Turn lights off | `CC_BUDDY_LIGHTS=0` in `~/.config/cc-buddy-bridge/env` |

With no lights file, or an empty one, lights are off. The model gets no light
tools, and "lights off" goes to the model like any other text.

## How it works

- **Govee:** buddy sends a scan to multicast `239.255.255.250:4001`, and each
  light answers on port 4002. Commands (`turn`, `brightness`, `colorwc`) go to
  the light on port 4003. `devStatus` reads a light's state back.
- **HappyLighting:** buddy writes to characteristic `ffd9`: `cc 23 33` is on,
  `cc 24 33` is off, and `56 RR GG BB 00 f0 aa` sets a colour. `ef 01 77`
  asks for the state, which comes back on `ffd4`. The controller has no
  brightness of its own, so brightness is the colour's level: a new colour
  keeps the current level, and a new level keeps the current colour. buddy
  holds the Bluetooth connection for 20 s after a command, so the next command
  is fast. After that, the phone app can connect again.
- **Tuya:** `tinytuya.BulbDevice` turns the light on and off and sets a
  colour, a brightness or a white. Tuya's colour-temperature scale is mapped
  from 2700K to 6500K.
- A white asked of a light with no white LED is an RGB approximation of that
  colour temperature.

## Troubleshooting

- **A Govee light does not answer:** check that LAN Control is on in the Govee
  app and that the Mac is on the same network. `cc-buddy-bridge lights scan`
  shows what answers.
- **"The Bluetooth light is out of range or taken by the phone app":** close
  HappyLighting on the phone. The controller accepts only one connection.
- **"This Tuya light has no local key yet":** follow
  [Sylvania](#sylvania-smart-wi-fi-tuya).
