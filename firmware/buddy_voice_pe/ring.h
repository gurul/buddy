#pragma once
// The 12-LED ring is buddy's face on the Voice PE: one look per state.
#include <stdint.h>

enum RingLook : uint8_t {
  LOOK_OFFLINE,    // no message from the daemon for 30 s: one dim dot circles
  LOOK_IDLE,       // connected, nothing running: faint warm breathe
  LOOK_WORKING,    // Claude sessions running, or buddy doing a task: teal comet
  LOOK_THINKING,   // buddy thinking: violet double comet
  LOOK_LISTENING,  // buddy listening: soft blue, steady breathe
  LOOK_SPEAKING,   // buddy speaking: blue, quick pulse
  LOOK_WAITING,    // a session waits on you: amber pulse
  LOOK_DONE,       // a task finished: green sweep (transient)
  LOOK_ERROR,      // red blink (transient)
};

void ringBegin();
void ringSet(RingLook look);
// Shows `level` of 10 as lit LEDs for 1.5 s, over whatever look is on.
void ringShowLevel(uint8_t level);
// Two red LEDs at the top while the hardware mute switch is on.
void ringSetMuted(bool muted);
// Call every loop; renders at most every 20 ms.
void ringUpdate();
