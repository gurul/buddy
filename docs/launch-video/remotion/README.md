# buddy launch video

This is the 118-second, 16:9 Remotion cut for buddy. It starts with the shared problem—an answer is not the same as knowing what to do next—then balances the everyday desk companion with the teacher-like math loop.

The film is grounded in the repository's current implementation:

- `gpt-live-1` receives the voice conversation.
- `gpt-6-astra` routes tools and drives guarded computer use.
- `gpt-5.4-nano` describes the camera when asked.
- Exa is shown as an optional lesson-generation reference search that receives only topic and level.
- The robot firmware, local Mac bridge, memory, approvals, and whiteboard are shown as separate parts of the system.

The supplied instrumental is trimmed from 160 seconds to 118 seconds and fades out over the final three seconds. There is no VO asset in the folder, so the script is carried by burned-in show-and-tell typography and UI captions over the supplied music bed.

```bash
npm install
npm run studio
npm run render
```
