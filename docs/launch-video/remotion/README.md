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

## Web export for the show-and-tell deck

`docs/launch-video/reference/show-and-tell.html` plays the film on its second slide and
seeks it from the chapter chips. The deck expects a smaller file next to it, so after
`npm run render` build the web export (two-pass H.264, 720p, about 12.5 MB) and the
ten chapter stills:

```bash
cd docs/launch-video/remotion
IN=out/launch-video.mp4; OUT=../reference/launch-video.mp4
ffmpeg -y -i $IN -vf scale=1280:-2 -c:v libx264 -preset slow -b:v 760k -maxrate 900k -bufsize 1800k -pass 1 -an -f mp4 /dev/null
ffmpeg -y -i $IN -vf scale=1280:-2 -c:v libx264 -preset slow -b:v 760k -maxrate 900k -bufsize 1800k -pass 2 -c:a aac -b:a 80k -movflags +faststart $OUT
i=1; for t in 1.5 13 23.5 36.5 44.5 55.5 67.5 91.5 103.5 113.5; do
  ffmpeg -y -ss $t -i $IN -frames:v 1 -vf scale=640:-2 -q:v 4 ../reference/film/ch$(printf %02d $i).jpg; i=$((i+1))
done
```

The stills are committed; the web export is ignored by git. Chapter start times in the deck
(0:00, 0:12, 0:22, 0:35, 0:43, 0:54, 1:06, 1:30, 1:42, 1:52) are the `SCENES` table in
`src/Root.tsx` divided by 30 fps. If a scene moves, update both.
