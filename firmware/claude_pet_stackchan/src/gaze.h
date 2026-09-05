#pragma once
// Person tracking + owner memory glue (spike: firmware/spikes/stackchan_look).
//
// look.cpp runs the GC0308 on a core-0 task and reports a motion/face
// bearing; owner_model.h keeps a LIVE gaze (fresh motion) and a MEMORY of
// where the owner usually sits. gaze.cpp turns those into bodyLookAt()
// requests according to the pet state, and persists the memory in NVS
// (namespace "owner", key "model"). Plain header: no M5 types.
#include <stdint.h>
#include "persona.h"

// After bodyBegin(). Restores the owner model from NVS (prints
// `[owner] restored yaw=.. pitch=.. conf=..` or `[owner] fresh`) and starts
// the camera task. loop() never blocks on the camera.
void gazeBegin();

// Once per loop after bodyUpdate(). `ownerReset` is data.h's
// {"cmd":"owner","op":"reset"} flag; it is consumed (cleared) here.
void gazeUpdate(PersonaState active, bool needsAttention, bool listening,
                uint32_t now, bool* ownerReset);

// A touch on the pet is an owner observation (weight 2 at yaw ±28, pitch
// level). Called from bodyNoteToucher().
void gazeNoteTouch(int8_t side);
