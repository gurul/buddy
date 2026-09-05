#pragma once
// Persona states shared by the render loop (main.cpp) and the robot body
// choreography (body.cpp). Order matches the species state tables and the
// character GIF manifest: 0=sleep .. 6=heart. Moved out of main.cpp for the
// StackChan port so body.h can take a PersonaState without including the
// board compat layer.
enum PersonaState { P_SLEEP, P_IDLE, P_BUSY, P_ATTENTION, P_CELEBRATE, P_DIZZY, P_HEART };
