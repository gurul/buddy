import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Composition,
  Easing,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

const FPS = 30;
const DURATION_SECONDS = 118;
const TOTAL_FRAMES = DURATION_SECONDS * FPS;

const C = {
  paper: '#FBF5EC',
  sheet: '#FFFDF8',
  ink: '#1F3A78',
  inkSoft: '#3E4F82',
  pink: '#E68AAE',
  pinkSoft: '#F4C3D4',
  teal: '#79C6B2',
  sun: '#F1C85B',
  sky: '#7FA2DE',
  desk: '#E9E1D3',
  screen: '#1B2350',
  screenPink: '#FF74D4',
  purple: '#A487D0',
};

type CSS = React.CSSProperties;

const fontFace = `
  @font-face { font-family: Grandstander; src: url(${staticFile('fonts/grandstander-700-900-latin.woff2')}) format('woff2'); font-weight: 700 900; }
  @font-face { font-family: Andika; src: url(${staticFile('fonts/andika-400-latin.woff2')}) format('woff2'); font-weight: 400; }
  @font-face { font-family: Andika; src: url(${staticFile('fonts/andika-700-latin.woff2')}) format('woff2'); font-weight: 700; }
  @font-face { font-family: Gochi Hand; src: url(${staticFile('fonts/gochi-hand-400-latin.woff2')}) format('woff2'); font-weight: 400; }
  @font-face { font-family: VT323; src: url(${staticFile('fonts/vt323-400-latin.woff2')}) format('woff2'); font-weight: 400; }
`;

function appear(frame: number, fps: number, delay = 0, duration = 18) {
  return spring({
    frame: Math.max(0, frame - delay),
    fps,
    config: {damping: 16, stiffness: 125, mass: 0.7},
    durationInFrames: duration,
  });
}

function fade(frame: number, start: number, end: number) {
  return interpolate(frame, [start, end], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
}

function drift(frame: number, distance = 8, speed = 0.055) {
  return Math.sin(frame * speed) * distance;
}

function Card({
  children,
  color = C.sky,
  delay = 0,
  style,
  className = '',
}: {
  children: React.ReactNode;
  color?: string;
  delay?: number;
  style?: CSS;
  className?: string;
}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, delay);
  return (
    <div
      className={className}
      style={{
        background: C.sheet,
        border: `3px solid ${C.ink}`,
        borderRadius: 8,
        boxShadow: `10px 10px 0 ${color}`,
        opacity: a,
        transform: `translateY(${(1 - a) * 28}px) rotate(${(1 - a) * -1.2}deg)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function Tag({children, color = C.teal, delay = 0}: {children: React.ReactNode; color?: string; delay?: number}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, delay, 14);
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '10px 16px',
        border: `3px solid ${C.ink}`,
        borderRadius: 999,
        background: color,
        font: '700 18px/1 Andika',
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
        color: C.ink,
        opacity: a,
        transform: `translateY(${(1 - a) * 14}px)`,
      }}
    >
      {children}
    </span>
  );
}

function Kicker({children, color = C.inkSoft}: {children: React.ReactNode; color?: string}) {
  return <div style={{font: '400 33px/1 Gochi Hand', color, marginBottom: 10}}>{children}</div>;
}

function H1({children, size = 72, style}: {children: React.ReactNode; size?: number; style?: CSS}) {
  return (
    <div
      style={{
        font: `900 ${size}px/0.98 Grandstander`,
        letterSpacing: '-0.035em',
        color: C.ink,
        textShadow: `6px 6px 0 ${C.pinkSoft}`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function Body({children, size = 25, style}: {children: React.ReactNode; size?: number; style?: CSS}) {
  return <div style={{font: `400 ${size}px/1.28 Andika`, color: C.ink, ...style}}>{children}</div>;
}

function Arrow({color = C.ink, direction = 'right', width = 118, delay = 0}: {color?: string; direction?: 'right' | 'down' | 'left'; width?: number; delay?: number}) {
  const frame = useCurrentFrame();
  const a = fade(frame, delay, delay + 14);
  const vertical = direction === 'down';
  const reverse = direction === 'left';
  return (
    <div style={{width: vertical ? 48 : width, height: vertical ? 54 : 38, opacity: a, display: 'flex', alignItems: 'center', justifyContent: 'center', transform: reverse ? 'scaleX(-1)' : undefined}}>
      {vertical ? (
        <svg width="30" height="54" viewBox="0 0 30 54">
          <path d="M15 2v42M4 33l11 14 11-14" fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        <svg width={width} height="28" viewBox={`0 0 ${width} 28`}>
          <path d={`M3 14H${width - 18}M${width - 30} 4l12 10-12 10`} fill="none" stroke={color} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
    </div>
  );
}

function Robot({message = 'hey!', scale = 1, delay = 0, screenPink = C.screenPink}: {message?: string; scale?: number; delay?: number; screenPink?: string}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, delay, 24);
  const bob = drift(frame, 5, 0.06);
  const blink = frame % 108 > 96 && frame % 108 < 102 ? 0.18 : 1;
  const safeMessage = message.length > 16 ? `${message.slice(0, 15)}…` : message;
  return (
    <div style={{width: 250 * scale, opacity: a, transform: `translateY(${(1 - a) * 80 + bob}px)`}}>
      <svg width={250 * scale} height={300 * scale} viewBox="0 0 200 240" style={{display: 'block', overflow: 'visible'}}>
        <defs>
          <linearGradient id={`label-${delay}`} x1="0" x2="1" y1="0" y2="1"><stop offset="0" stopColor="#9DB3EE"/><stop offset="1" stopColor="#B98BD8"/></linearGradient>
        </defs>
        <rect x="68" y="168" width="64" height="30" fill="#8C90A0" stroke="#4D5160" strokeWidth="2.5"/>
        <rect x="46" y="194" width="108" height="38" rx="7" fill="#6F7384" stroke="#4D5160" strokeWidth="2.5"/>
        <circle cx="64" cy="220" r="5" fill="#4D5160"/><circle cx="136" cy="220" r="5" fill="#4D5160"/>
        <g transform={`translate(0 ${Math.sin(frame * 0.045) * 1.5})`}>
          <rect x="22" y="10" width="156" height="34" rx="7" fill={`url(#label-${delay})`} stroke="#5E5F98" strokeWidth="2.5"/>
          <text x="100" y="35" textAnchor="middle" fontFamily="Grandstander" fontWeight="700" fontSize="22" fill="#F4F4FB">buddy</text>
          <rect x="12" y="42" width="176" height="14" rx="4" fill="#A487D0" stroke="#5E5F98" strokeWidth="2.5"/>
          <rect x="30" y="52" width="140" height="122" rx="14" fill="#C3C6CF" stroke="#5C6070" strokeWidth="3"/>
          <rect x="42" y="62" width="116" height="100" rx="6" fill={C.screen}/>
          <g fill={screenPink} transform={`translate(0 ${blink === 1 ? 0 : 7}) scale(1 ${blink})`}>
            <path d="M72 75H88V80H92V94H87V89H73V94H68V80H72Z M112 75H128V80H132V94H127V89H113V94H108V80H112Z"/>
          </g>
          <text x="100" y="134" textAnchor="middle" fontFamily="VT323" fontSize="22" fill={screenPink}>{safeMessage}</text>
        </g>
      </svg>
    </div>
  );
}

function PaperBackground({accent = C.sky, wipe = false}: {accent?: string; wipe?: boolean}) {
  const frame = useCurrentFrame();
  const wipeX = interpolate(frame, [0, 22], [1920, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{background: C.paper, color: C.ink, overflow: 'hidden'}}>
      <svg width="1920" height="1080" viewBox="0 0 1920 1080" style={{position: 'absolute', inset: 0, opacity: 0.98}}>
        <circle cx="40" cy="60" r="190" fill={C.sky}/>
        <circle cx="40" cy="60" r="128" fill="none" stroke="#9BB6E6" strokeWidth="22"/>
        <path d="M-20 200C130 165 175 290 120 410C80 500 10 470-40 420Z" fill={C.teal}/>
        <polygon points="126,118 205,42 230,94 292,30 320,80 372,28 350,74 285,132 260,82 206,145 179,101 120,150" fill={C.sun}/>
        <path d="M1670 -50H1950V270C1874 292 1772 260 1726 196C1690 142 1650 40 1670 -50Z" fill={C.pink}/>
        <path d="M1670 -50H1950V270C1874 292 1772 260 1726 196C1690 142 1650 40 1670 -50Z" fill="none" stroke="#D97A9E" strokeWidth="12" strokeDasharray="4 22"/>
        <polygon points="1760,830 1920,786 1920,1080 1802,1080" fill={C.sun}/>
        <polygon points="1760,830 1920,786 1920,1080 1802,1080" fill="none" stroke="#E7B44A" strokeWidth="12" strokeDasharray="3 22"/>
        <path d="M820 1030C940 900 1110 878 1220 945L1185 1000C1070 954 964 970 885 1080Z" fill={C.pink}/>
        <circle cx="1500" cy="820" r="88" fill={accent} opacity="0.18"/>
        <path d="M1460 720l18 42 46 4-35 30 10 45-39-24-39 24 10-45-35-30 46-4z" fill={C.pink} opacity="0.52"/>
      </svg>
      {wipe && <div style={{position: 'absolute', inset: 0, background: accent, transform: `translateX(${wipeX}px)`, zIndex: 30}}/>}
    </AbsoluteFill>
  );
}

function SceneFrame({children, accent, wipe = true}: {children: React.ReactNode; accent: string; wipe?: boolean}) {
  return (
    <AbsoluteFill style={{fontFamily: 'Andika', background: C.paper}}>
      <PaperBackground accent={accent}/>
      <div style={{position: 'absolute', inset: 0, padding: '70px 100px', zIndex: 1}}>{children}</div>
    </AbsoluteFill>
  );
}

function BrowserWindow({children, style}: {children: React.ReactNode; style?: CSS}) {
  return (
    <div style={{background: C.sheet, border: `3px solid ${C.ink}`, borderRadius: 12, boxShadow: `12px 12px 0 ${C.sky}`, overflow: 'hidden', ...style}}>
      <div style={{height: 42, background: C.pinkSoft, borderBottom: `3px solid ${C.ink}`, display: 'flex', alignItems: 'center', gap: 10, padding: '0 16px'}}>
        <span style={{width: 13, height: 13, borderRadius: '50%', background: C.pink, border: `2px solid ${C.ink}`}}/>
        <span style={{width: 13, height: 13, borderRadius: '50%', background: C.sun, border: `2px solid ${C.ink}`}}/>
        <span style={{width: 13, height: 13, borderRadius: '50%', background: C.teal, border: `2px solid ${C.ink}`}}/>
        <span style={{marginLeft: 8, font: '700 15px Andika', color: C.inkSoft}}>buddy · learning</span>
      </div>
      {children}
    </div>
  );
}

function ProblemScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const intro = appear(frame, fps, 4);
  const line = fade(frame, 46, 72);
  return (
    <SceneFrame accent={C.pink}>
      <div style={{position: 'absolute', left: 112, top: 84, opacity: intro}}><Tag color={C.pinkSoft}>the problem</Tag></div>
      <div style={{position: 'absolute', left: 105, top: 178, width: 920}}>
        <Kicker>When you are stuck…</Kicker>
        <H1 size={92}>an answer<br/>isn't enough.</H1>
        <Body size={29} style={{marginTop: 28, maxWidth: 820, opacity: intro}}>
          A search result can be right. A busy day can still be hard.<br/>
          What helps is knowing what to do next.
        </Body>
      </div>
      <Card color={C.sun} delay={18} style={{position: 'absolute', left: 1080, top: 118, width: 660, minHeight: 270, padding: 34, transform: undefined}}>
        <div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>at the desk</div>
        <div style={{font: '700 34px/1.08 Grandstander', color: C.ink}}>A hundred tiny asks.</div>
        <div style={{marginTop: 26, padding: '18px 22px', border: `3px solid ${C.ink}`, borderRadius: 999, background: C.sun, font: '400 28px Gochi Hand', transform: 'rotate(-1deg)'}}>
          “buddy, where is that file?”
        </div>
      </Card>
      <Card color={C.teal} delay={28} style={{position: 'absolute', left: 1180, top: 456, width: 620, minHeight: 290, padding: 34, transform: undefined}}>
        <div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>in the lesson</div>
        <div style={{font: '700 34px/1.08 Grandstander', color: C.ink}}>A learner needs the next thought.</div>
        <div style={{marginTop: 26, padding: '18px 22px', border: `3px solid ${C.ink}`, borderRadius: 999, background: C.teal, font: '400 28px Gochi Hand', transform: 'rotate(1deg)'}}>
          “where do I start?”
        </div>
      </Card>
      <div style={{position: 'absolute', left: 110, bottom: 90, font: '400 34px Gochi Hand', color: C.ink, opacity: line, transform: `translateY(${(1 - line) * 18}px)`}}>
        Meet the buddy that can do both.
      </div>
      <div style={{position: 'absolute', right: 130, bottom: 78, transform: `rotate(-8deg)`, opacity: line}}>
        <svg width="170" height="100" viewBox="0 0 170 100"><path d="M8 85C48 72 95 43 146 12" fill="none" stroke={C.ink} strokeWidth="5" strokeLinecap="round"/><path d="M124 12l24-1-8 23" fill="none" stroke={C.ink} strokeWidth="5" strokeLinecap="round" strokeLinejoin="round"/></svg>
      </div>
    </SceneFrame>
  );
}

function RealRobotScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, 5);
  const photoA = appear(frame, fps, 10);
  const labelA = fade(frame, 36, 52);
  return (
    <SceneFrame accent={C.sun}>
      <div style={{position: 'absolute', left: 108, top: 88}}><Kicker>so, what is buddy?</Kicker><H1 size={70}>A real robot<br/>on your desk.</H1></div>
      <div style={{position: 'absolute', left: 108, top: 468, width: 730, opacity: a}}>
        <Body size={28}>A physical head, a local Mac app,<br/>and a memory that stays useful.</Body>
        <div style={{marginTop: 30}}><Tag color={C.teal}>works today</Tag></div>
      </div>
      <div style={{position: 'absolute', left: 930, top: 96, width: 820, opacity: photoA, transform: `translateY(${(1 - photoA) * 30}px) rotate(-1.2deg)`}}>
        <div style={{position: 'absolute', inset: 14, background: C.sun, transform: 'translate(10px, 10px)', border: `3px solid ${C.ink}`, borderRadius: 10}}/>
        <div style={{position: 'relative', background: C.sheet, padding: 14, border: `3px solid ${C.ink}`, borderRadius: 10}}>
          <Img src={staticFile('assets/hero.png')} style={{display: 'block', width: '100%', height: 370, objectFit: 'cover', objectPosition: 'center'}}/>
        </div>
        <div style={{marginTop: 18, display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
          <span style={{font: '400 27px Gochi Hand', color: C.ink}}>a little machine, a lot of personality</span>
          <span style={{font: '700 18px Andika', color: C.inkSoft}}>M5StackChan · real hardware</span>
        </div>
      </div>
      <div style={{position: 'absolute', left: 105, bottom: 104, width: 740, display: 'flex', flexWrap: 'wrap', gap: 12, opacity: labelA}}>
        {['camera', 'mic', '2 motors', '12 lights', 'touch', 'screen face'].map((x, i) => <span key={x} style={{padding: '12px 18px', border: `3px solid ${C.ink}`, borderRadius: 999, background: [C.sun, C.teal, C.pinkSoft][i % 3], font: '700 21px Andika'}}>{x}</span>)}
      </div>
    </SceneFrame>
  );
}

function EverydayScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const titleA = appear(frame, fps, 3);
  const webA = appear(frame, fps, 22);
  const taskA = appear(frame, fps, 42);
  return (
    <SceneFrame accent={C.sky}>
      <div style={{position: 'absolute', left: 108, top: 76, opacity: titleA}}><Kicker>everyday sidekick · works today</Kicker><H1 size={71}>You say it.<br/>buddy routes it.</H1></div>
      <div style={{position: 'absolute', left: 108, top: 420, width: 660, opacity: titleA}}>
        <div style={{font: '400 30px Gochi Hand', color: C.inkSoft, marginBottom: 22}}>“hey buddy, where's my mug?”</div>
        <div style={{display: 'flex', alignItems: 'center', gap: 12}}><Robot message="listening…" scale={0.65}/><Arrow width={82} delay={16}/><div style={{font: '700 29px/1.1 Grandstander'}}>look → find → answer</div></div>
      </div>
      <Card color={C.teal} delay={20} style={{position: 'absolute', left: 840, top: 112, width: 910, minHeight: 255, padding: 28, transform: undefined}}>
        <div style={{font: '700 31px Grandstander'}}>Questions take the light path.</div>
        <div style={{display: 'flex', alignItems: 'center', gap: 8, marginTop: 16}}>
          <div style={{padding: '15px 20px', border: `3px solid ${C.ink}`, borderRadius: 10, background: C.pinkSoft, font: '700 22px Andika'}}>voice</div><Arrow width={78} delay={26}/>
          <div style={{padding: '15px 20px', border: `3px solid ${C.ink}`, borderRadius: 10, background: C.sun, font: '700 22px Andika'}}>gpt-live-1</div><Arrow width={78} delay={30}/>
          <div style={{padding: '15px 20px', border: `3px solid ${C.ink}`, borderRadius: 10, background: C.teal, font: '700 22px Andika'}}>web search</div>
        </div>
        <div style={{font: '400 23px Gochi Hand', color: C.inkSoft, marginTop: 18}}>Today's facts are checked, not guessed.</div>
      </Card>
      <Card color={C.sun} delay={40} style={{position: 'absolute', left: 900, top: 480, width: 850, minHeight: 285, padding: 28, transform: undefined}}>
        <div style={{font: '700 31px Grandstander'}}>Mac work takes the guarded path.</div>
        <div style={{display: 'flex', alignItems: 'center', gap: 8, marginTop: 16}}>
          <div style={{padding: '15px 20px', border: `3px solid ${C.ink}`, borderRadius: 10, background: C.pinkSoft, font: '700 22px Andika'}}>request</div><Arrow width={78} delay={46}/>
          <div style={{padding: '15px 20px', border: `3px solid ${C.ink}`, borderRadius: 10, background: C.sun, font: '700 22px Andika'}}>gpt-6-astra</div><Arrow width={78} delay={50}/>
          <div style={{padding: '15px 20px', border: `3px solid ${C.ink}`, borderRadius: 10, background: C.teal, font: '700 22px Andika'}}>start_task</div>
        </div>
        <div style={{marginTop: 20, display: 'flex', gap: 10, font: '700 20px Andika'}}><span style={{padding: '9px 14px', background: C.teal, border: `2px solid ${C.ink}`, borderRadius: 999}}>click + type</span><span style={{padding: '9px 14px', background: C.pinkSoft, border: `2px solid ${C.ink}`, borderRadius: 999}}>ask before big actions</span><span style={{padding: '9px 14px', background: C.sheet, border: `2px solid ${C.ink}`, borderRadius: 999}}>stop anytime</span></div>
      </Card>
      <div style={{position: 'absolute', right: 106, bottom: 62, opacity: taskA, font: '400 26px Gochi Hand', color: C.inkSoft}}>not magic — a system with boundaries.</div>
    </SceneFrame>
  );
}

function NodeBox({title, detail, color, style, delay = 0, dashed = false}: {title: string; detail?: string; color: string; style?: CSS; delay?: number; dashed?: boolean}) {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, delay, 16);
  return (
    <div style={{background: C.sheet, border: `3px ${dashed ? 'dashed' : 'solid'} ${C.ink}`, borderRadius: 10, boxShadow: `7px 7px 0 ${color}`, padding: '14px 18px', opacity: a, transform: `translateY(${(1 - a) * 18}px)`, ...style}}>
      <div style={{font: '700 24px/1 Grandstander', color: C.ink}}>{title}</div>
      {detail && <div style={{font: '400 17px/1.2 Andika', color: C.inkSoft, marginTop: 8}}>{detail}</div>}
    </div>
  );
}

function UnderHoodScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const titleA = appear(frame, fps, 0);
  const arrowA = fade(frame, 58, 82);
  return (
    <SceneFrame accent={C.teal}>
      <div style={{position: 'absolute', left: 108, top: 56, opacity: titleA}}><Kicker>show & tell · under the hood</Kicker><H1 size={64}>The physical buddy<br/>is one system.</H1></div>
      <div style={{position: 'absolute', left: 108, top: 280, width: 490, height: 620, background: C.sheet, border: `3px solid ${C.ink}`, borderRadius: 12, boxShadow: `10px 10px 0 ${C.sun}`, padding: 24}}>
        <div style={{font: '700 28px Grandstander'}}>the robot</div><div style={{font: '400 18px Andika', color: C.inkSoft, marginTop: 6}}>firmware on the ESP32-S3</div>
        <div style={{marginTop: 22, display: 'grid', gap: 12}}>
          <NodeBox title="screen face" detail="captions + eyes" color={C.sky} delay={10}/>
          <NodeBox title="head + lights" detail="motors, moods, chirps" color={C.pinkSoft} delay={16}/>
          <NodeBox title="camera + touch" detail="frames, pats, gestures" color={C.teal} delay={22}/>
          <NodeBox title="local state" detail="the body reacts here" color={C.sun} delay={28}/>
        </div>
      </div>
      <div style={{position: 'absolute', left: 650, top: 504, width: 250, textAlign: 'center', opacity: arrowA}}>
        <Arrow width={245} delay={52}/><div style={{font: '700 18px Andika', color: C.inkSoft}}>events · state · captions</div>
      </div>
      <div style={{position: 'absolute', left: 930, top: 280, width: 430, height: 620, background: C.sheet, border: `3px solid ${C.ink}`, borderRadius: 12, boxShadow: `10px 10px 0 ${C.teal}`, padding: 24}}>
        <div style={{font: '700 28px Grandstander'}}>the Mac</div><div style={{font: '400 18px Andika', color: C.inkSoft, marginTop: 6}}>buddy app · local control</div>
        <div style={{marginTop: 22, display: 'grid', gap: 12}}>
          <NodeBox title="wake word" detail="heard locally: hey buddy" color={C.pinkSoft} delay={34}/>
          <NodeBox title="voice + tools" detail="routes questions and tasks" color={C.sun} delay={40}/>
          <NodeBox title="computer worker" detail="PyAutoGUI + approvals" color={C.teal} delay={46}/>
          <NodeBox title="memory" detail="chat notes + lesson store" color={C.sky} delay={52}/>
        </div>
      </div>
      <div style={{position: 'absolute', left: 1460, top: 280, width: 350, height: 620, background: C.paper, border: `3px dashed ${C.ink}`, borderRadius: 12, padding: 24}}>
        <div style={{font: '700 28px Grandstander'}}>AI services</div><div style={{font: '400 18px Andika', color: C.inkSoft, marginTop: 6}}>called for the job</div>
        <div style={{marginTop: 22, display: 'grid', gap: 14}}>
          <NodeBox title="gpt-live-1" detail="voice receptionist" color={C.pinkSoft} delay={58}/>
          <NodeBox title="gpt-6-astra" detail="tools + computer use" color={C.sun} delay={64}/>
          <NodeBox title="gpt-5.4-nano" detail="camera understanding" color={C.teal} delay={70}/>
          <NodeBox title="Exa" detail="optional practice references" color={C.sky} delay={76} dashed/>
        </div>
        <div style={{position: 'absolute', left: 24, right: 24, bottom: 22, font: '400 18px/1.2 Gochi Hand', color: C.inkSoft}}>Exa receives topic + level;<br/>the tutor creates the adapted problem.</div>
      </div>
      <div style={{position: 'absolute', left: 644, top: 892, font: '400 25px Gochi Hand', color: C.inkSoft}}>A Mac app connects the body to the right model at the right moment.</div>
    </SceneFrame>
  );
}

function TeacherScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const title = appear(frame, fps, 0);
  return (
    <SceneFrame accent={C.pink}>
      <div style={{position: 'absolute', left: 108, top: 88, opacity: title}}><Kicker>the other half · designed for learners</Kicker><H1 size={77}>Not a homework<br/>answer machine.</H1></div>
      <div style={{position: 'absolute', left: 110, top: 470, width: 700, opacity: title}}><Body size={30}>More like a patient teacher who knows<br/>when to be small.</Body><div style={{marginTop: 28, font: '400 31px Gochi Hand', color: C.inkSoft}}>the goal is the next thought.</div></div>
      <Card color={C.sun} delay={18} style={{position: 'absolute', left: 940, top: 122, width: 740, minHeight: 250, padding: 30, transform: undefined}}>
        <div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>the kid</div><div style={{font: '700 34px/1.08 Grandstander'}}>“I got stuck right here.”</div><div style={{marginTop: 20, font: '400 24px/1.28 Andika'}}>They want help without losing the feeling of solving it.</div>
      </Card>
      <Card color={C.teal} delay={32} style={{position: 'absolute', left: 1050, top: 460, width: 700, minHeight: 250, padding: 30, transform: undefined}}>
        <div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>the grown-up</div><div style={{font: '700 34px/1.08 Grandstander'}}>“Please keep them thinking.”</div><div style={{marginTop: 20, font: '400 24px/1.28 Andika'}}>A helper that explains the next move, not the whole game.</div>
      </Card>
      <div style={{position: 'absolute', left: 120, bottom: 82, display: 'flex', alignItems: 'center', gap: 18}}>
        {['nudge', 'check', 'one step'].map((x, i) => <React.Fragment key={x}><span style={{padding: '16px 25px', background: [C.sun, C.teal, C.pink][i], border: `3px solid ${C.ink}`, borderRadius: 999, font: '700 24px Grandstander', opacity: fade(frame, 40 + i * 8, 54 + i * 8)}}>{x}</span>{i < 2 && <Arrow width={58} delay={44 + i * 8}/>}</React.Fragment>)}
      </div>
    </SceneFrame>
  );
}

function LessonBoard({phase = 0}: {phase?: number}) {
  const frame = useCurrentFrame();
  const writeA = fade(frame, 22, 42);
  const answerA = fade(frame, 70, 90);
  const buttonA = fade(frame, 104, 124);
  const phaseShift = phase === 1 ? 0.65 : phase === 2 ? 1 : 0;
  return (
    <BrowserWindow style={{width: 1000, height: 560}}>
      <div style={{padding: 24, background: C.paper, height: '100%', position: 'relative'}}>
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}><div style={{font: '700 30px Grandstander'}}>Solve 2x + 3 = 11.</div><span style={{padding: '8px 14px', border: `2px solid ${C.ink}`, borderRadius: 999, background: C.sun, font: '700 16px Andika'}}>algebra · level 7</span></div>
        <div style={{marginTop: 20, height: 265, background: C.sheet, border: `3px dashed ${C.ink}`, borderRadius: 8, padding: 26, position: 'relative', overflow: 'hidden'}}>
          <div style={{font: '400 34px Gochi Hand', color: C.ink, opacity: writeA, transform: `translateX(${(1 - writeA) * -30}px)`}}>2x + 3 = 11</div>
          <div style={{position: 'absolute', left: 238, top: 116, font: '700 42px Grandstander', color: C.ink, opacity: writeA * phaseShift, transform: `rotate(-1deg) translateY(${(1 - writeA * phaseShift) * 24}px)`}}>2x = 8</div>
          <div style={{position: 'absolute', left: 610, top: 58, font: '400 24px Gochi Hand', color: C.inkSoft, opacity: answerA}}>learner's board</div>
          <div style={{position: 'absolute', right: 34, bottom: 24, padding: '12px 18px', border: `3px solid ${C.ink}`, borderRadius: 8, background: C.pinkSoft, font: '700 18px Andika', opacity: answerA}}>buddy's separate panel → x = 4</div>
        </div>
        <div style={{display: 'flex', gap: 16, marginTop: 22, opacity: buttonA}}>
          <div style={{flex: 1, padding: '17px 10px', textAlign: 'center', background: C.sun, border: `3px solid ${C.ink}`, borderRadius: 9, boxShadow: `5px 5px 0 ${C.ink}`, font: '700 19px Andika'}}>Give me a hint</div>
          <div style={{flex: 1, padding: '17px 10px', textAlign: 'center', background: C.teal, border: `3px solid ${C.ink}`, borderRadius: 9, boxShadow: `5px 5px 0 ${C.ink}`, font: '700 19px Andika'}}>Check my work</div>
          <div style={{flex: 1, padding: '17px 10px', textAlign: 'center', background: C.pink, border: `3px solid ${C.ink}`, borderRadius: 9, boxShadow: `5px 5px 0 ${C.ink}`, font: '700 19px Andika'}}>Show one step</div>
        </div>
      </div>
    </BrowserWindow>
  );
}

function LessonScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const speech = fade(frame, 2, 20);
  const board = appear(frame, fps, 20, 20);
  const note = fade(frame, 160, 184);
  return (
    <SceneFrame accent={C.sun}>
      <div style={{position: 'absolute', left: 108, top: 52}}><Kicker>show & tell · a math lesson</Kicker><H1 size={64}>A teacher-like loop,<br/>with a real whiteboard.</H1></div>
      <div style={{position: 'absolute', left: 116, top: 290, width: 480, opacity: speech, transform: `translateY(${(1 - speech) * 22}px)`}}>
        <div style={{font: '400 31px Gochi Hand', color: C.inkSoft}}>the learner says:</div>
        <div style={{marginTop: 18, padding: '20px 24px', background: C.teal, border: `3px solid ${C.ink}`, borderRadius: 18, boxShadow: `8px 8px 0 ${C.ink}`, font: '700 30px/1.1 Grandstander'}}>“Okay buddy, I want<br/>to do a math lesson!”</div>
        <div style={{marginTop: 30, display: 'grid', gap: 11}}>
          {['pick a topic', 'bring a problem', 'write your ideas'].map((x, i) => <div key={x} style={{display: 'flex', gap: 12, alignItems: 'center', opacity: fade(frame, 26 + i * 11, 40 + i * 11)}}><span style={{width: 28, height: 28, borderRadius: '50%', background: [C.sun, C.pinkSoft, C.sky][i], border: `3px solid ${C.ink}`, display: 'inline-flex', justifyContent: 'center', alignItems: 'center', font: '700 17px Andika'}}>{i + 1}</span><span style={{font: '700 23px Andika'}}>{x}</span></div>)}
        </div>
      </div>
      <div style={{position: 'absolute', left: 670, top: 210, opacity: board, transform: `translateY(${(1 - board) * 35}px)`}}><LessonBoard phase={1}/></div>
      <div style={{position: 'absolute', left: 724, top: 814, opacity: note, display: 'flex', gap: 18, alignItems: 'center'}}>
        <div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>topic + level</div><Arrow width={100} delay={172}/><div style={{padding: '12px 18px', border: `3px dashed ${C.ink}`, borderRadius: 9, background: C.sheet, font: '700 21px Andika'}}>Exa · references</div><Arrow width={100} delay={182}/><div style={{padding: '12px 18px', border: `3px solid ${C.ink}`, borderRadius: 9, background: C.sun, font: '700 21px Andika'}}>Astra · adapted problem</div>
      </div>
      <div style={{position: 'absolute', right: 100, bottom: 48, font: '400 23px Gochi Hand', color: C.inkSoft}}>optional practice search · learner work stays out of Exa</div>
    </SceneFrame>
  );
}

function GuardrailsScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const titleA = appear(frame, fps, 0);
  const ruleA = fade(frame, 120, 150);
  return (
    <SceneFrame accent={C.teal}>
      <div style={{position: 'absolute', left: 108, top: 78, opacity: titleA}}><Kicker>the teaching rule</Kicker><H1 size={81}>The whole answer is<br/><span style={{background: `linear-gradient(transparent 56%, ${C.pinkSoft} 56%)`}}>never on the menu.</span></H1></div>
      <div style={{position: 'absolute', left: 110, top: 430, display: 'flex', gap: 22}}>
        <Card color={C.sun} delay={22} style={{width: 500, minHeight: 270, padding: 25, transform: undefined}}><div style={{font: '700 30px Grandstander'}}>Give me a hint</div><div style={{font: '400 26px Gochi Hand', marginTop: 18}}>“What could you do to both sides?”</div><Body size={21} style={{marginTop: 18}}>A nudge. It never performs a step.</Body></Card>
        <Card color={C.teal} delay={34} style={{width: 500, minHeight: 270, padding: 25, transform: undefined}}><div style={{font: '700 30px Grandstander'}}>Check my work</div><div style={{font: '400 26px Gochi Hand', marginTop: 18}}>“Let's look at line two.”</div><Body size={21} style={{marginTop: 18}}>It checks what is on the board and explains the first slip.</Body></Card>
        <Card color={C.pink} delay={46} style={{width: 500, minHeight: 270, padding: 25, transform: undefined}}><div style={{font: '700 30px Grandstander'}}>Show one step</div><div style={{font: '400 26px Gochi Hand', marginTop: 18}}>“Here's the next line, and why.”</div><Body size={21} style={{marginTop: 18}}>Exactly one transformation, in its own panel.</Body></Card>
      </div>
      <div style={{position: 'absolute', left: 110, bottom: 62, opacity: ruleA, font: '400 29px Gochi Hand', color: C.inkSoft}}>Your board stays yours. Buddy adds the smallest useful move.</div>
    </SceneFrame>
  );
}

function TwoWorldsScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, 0);
  const bridge = fade(frame, 38, 68);
  return (
    <SceneFrame accent={C.pink}>
      <div style={{position: 'absolute', left: 108, top: 84, opacity: a}}><Kicker>same buddy · two kinds of help</Kicker><H1 size={82}>The everyday build<br/>and the teacherly bit.</H1></div>
      <div style={{position: 'absolute', left: 108, top: 430, width: 700, opacity: a}}>
        <Card color={C.sun} delay={15} style={{padding: 30, minHeight: 280, transform: undefined}}><div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>at the desk · works today</div><div style={{font: '700 32px Grandstander', marginTop: 12}}>Listen. Look. Act. Remember.</div><div style={{display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 22}}>{['weather', 'find my mug', 'open Spotify', 'go explore'].map(x => <span key={x} style={{padding: '9px 13px', background: C.sun, border: `2px solid ${C.ink}`, borderRadius: 999, font: '700 18px Andika'}}>{x}</span>)}</div></Card>
      </div>
      <div style={{position: 'absolute', right: 108, top: 430, width: 700, opacity: a}}>
        <Card color={C.teal} delay={27} style={{padding: 30, minHeight: 280, transform: undefined}}><div style={{font: '400 28px Gochi Hand', color: C.inkSoft}}>in a lesson · designed</div><div style={{font: '700 32px Grandstander', marginTop: 12}}>Nudge. Check. Show one step.</div><div style={{display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 22}}>{['topic or problem', 'whiteboard', 'hint', 'recap'].map(x => <span key={x} style={{padding: '9px 13px', background: C.teal, border: `2px solid ${C.ink}`, borderRadius: 999, font: '700 18px Andika'}}>{x}</span>)}</div></Card>
      </div>
      <div style={{position: 'absolute', left: 804, top: 383, width: 310, height: 80, background: C.pinkSoft, border: `3px solid ${C.ink}`, borderRadius: 999, display: 'flex', alignItems: 'center', justifyContent: 'center', font: '700 24px Grandstander', opacity: bridge, transform: `scale(${0.8 + 0.2 * bridge})`}}>one physical buddy</div>
      <div style={{position: 'absolute', left: 824, top: 355, font: '400 26px Gochi Hand', color: C.inkSoft, opacity: bridge}}>the bridge</div>
    </SceneFrame>
  );
}

function EndScene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const a = appear(frame, fps, 5);
  const b = fade(frame, 46, 72);
  return (
    <SceneFrame accent={C.sky}>
      <div style={{position: 'absolute', left: 180, right: 150, top: 210, height: 590, display: 'flex', alignItems: 'center', justifyContent: 'space-between'}}>
        <div style={{width: 900, opacity: a, transform: `translateY(${(1 - a) * 22}px)`}}>
          <div style={{font: '900 180px/0.9 Grandstander', letterSpacing: '-0.06em', color: C.ink, textShadow: `9px 9px 0 ${C.pinkSoft}`}}>buddy</div>
          <div style={{font: '400 45px Gochi Hand', marginTop: 18, color: C.ink}}>your desktop sidekick</div>
          <div style={{marginTop: 54, display: 'flex', gap: 14}}><Tag color={C.sun}>voice</Tag><Tag color={C.teal} delay={8}>desk</Tag><Tag color={C.pinkSoft} delay={16}>learning</Tag></div>
        </div>
        <div style={{width: 420, display: 'flex', justifyContent: 'center', opacity: a}}><Robot message="see you!" scale={1.32}/></div>
      </div>
      <div style={{position: 'absolute', left: 220, bottom: 98, font: '700 24px Andika', color: C.inkSoft, opacity: b}}>open source · github.com/gurul/buddy</div>
      <div style={{position: 'absolute', right: 180, bottom: 92, font: '400 30px Gochi Hand', color: C.inkSoft, opacity: b}}>keep thinking.</div>
    </SceneFrame>
  );
}

function Scene({from, duration, accent, children}: {from: number; duration: number; accent: string; children: React.ReactNode}) {
  return <Sequence from={from} durationInFrames={duration}><AbsoluteFill>{children}</AbsoluteFill></Sequence>;
}

export function LaunchVideo() {
  const frame = useCurrentFrame();
  const volume = interpolate(frame, [0, 36, TOTAL_FRAMES - 90, TOTAL_FRAMES], [0, 0.72, 0.72, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <AbsoluteFill style={{background: C.paper}}>
      <style dangerouslySetInnerHTML={{__html: fontFace}}/>
      <Audio src={staticFile('audio/buddy-launch.mp3')} volume={volume}/>
      <Scene from={0} duration={360} accent={C.pink}><ProblemScene/></Scene>
      <Scene from={360} duration={300} accent={C.sun}><RealRobotScene/></Scene>
      <Scene from={660} duration={480} accent={C.sky}><EverydayScene/></Scene>
      <Scene from={1140} duration={480} accent={C.teal}><UnderHoodScene/></Scene>
      <Scene from={1620} duration={360} accent={C.pink}><TeacherScene/></Scene>
      <Scene from={1980} duration={720} accent={C.sun}><LessonScene/></Scene>
      <Scene from={2700} duration={360} accent={C.teal}><GuardrailsScene/></Scene>
      <Scene from={3060} duration={300} accent={C.pink}><TwoWorldsScene/></Scene>
      <Scene from={3360} duration={180} accent={C.sky}><EndScene/></Scene>
    </AbsoluteFill>
  );
}

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="LaunchVideo"
        component={LaunchVideo}
        durationInFrames={TOTAL_FRAMES}
        fps={FPS}
        width={1920}
        height={1080}
      />
    </>
  );
};
