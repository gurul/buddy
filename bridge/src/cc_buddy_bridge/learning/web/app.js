/* Local learning workspace: no build step, remote scripts, or browser API keys. */
'use strict';
const $ = s => document.querySelector(s);
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config, lesson, dirty = false, busy = false, drawing = false, tool = 'pen', stroke, saveTimer, checkTimer;
let speak = false, supervise = false, tour = false, lastRevision = 0, saveInFlight = false;
/* Think out loud. listening mirrors GET /api/listening; ideasBase is the revision whose ideas text this page last loaded,
   so the server can put back a spoken line that arrived while the learner was typing. */
let listening = {available:false, state:'off', lesson_id:null, reason:''}, listenBusy = false, ideasBase = 0;
const main = $('#main');
function fail(error) { $('#error').textContent = error.message || error; $('#error').classList.remove('hidden'); }
function clearError() { $('#error').classList.add('hidden'); }
function say(text) { if (speak && 'speechSynthesis' in window) { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(text)); } }
async function api(path, data) {
  const r = await fetch(path, data ? {method:'POST',headers:{'Content-Type':'application/json','X-Buddy-Token':config.token},body:JSON.stringify(data)} : {});
  const result = await r.json(); if (!r.ok) throw new Error(result.error || 'Request failed'); return result;
}
function status(text) { $('#save-status').textContent = text; }
/* The Think out loud toggle stays usable while the tutor works: a learner must always be able to stop the microphone. */
function setBusy(value) { busy = value; document.querySelectorAll('main button, main textarea, main input, #new-side, #dashboard-link').forEach(b => { if ('disabled' in b && b.id !== 'think-aloud') b.disabled=value; }); }
/* Card art: a glyph for the topic. Math topics keep their expressions; any other subject gets the first letters of the topic on the plain sun background. */
const art = topic => /calculus|derivative/i.test(topic) ? ['calculus','dy / dx'] : /algebra|equation/i.test(topic) ? ['algebra','2x + 3 = 11'] : /\b(add|sum|fraction|arithmetic|count|multipl|divi|number)/i.test(topic) ? ['', '7 + 5 = ?'] : ['', escapeHTML(topic.trim().split(/\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase() || '?')];
const readableStage = stage => ({setup:'Ready to begin',input:'Add your problem',confirm:'Confirm problem',working:'In progress',complete:'Completed',ended:'Saved for later'}[stage] || stage);
/* Static robot drawings from the Show-and-Tell deck. SVG presentation attributes are CSP-safe; no animation. */
const ROBOT = `<svg viewBox="0 0 200 240" role="img" aria-label="buddy, a small robot with a screen face"><defs><linearGradient id="bb-hero-label" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#9DB3EE"></stop><stop offset="1" stop-color="#B98BD8"></stop></linearGradient></defs><rect x="68" y="168" width="64" height="30" fill="#8C90A0" stroke="#4D5160" stroke-width="2.5"></rect><rect x="46" y="194" width="108" height="38" rx="7" fill="#6F7384" stroke="#4D5160" stroke-width="2.5"></rect><circle cx="64" cy="220" r="5" fill="#4D5160"></circle><circle cx="136" cy="220" r="5" fill="#4D5160"></circle><rect x="22" y="10" width="156" height="34" rx="7" fill="url(#bb-hero-label)" stroke="#5E5F98" stroke-width="2.5"></rect><text x="100" y="35" text-anchor="middle" font-family="Grandstander, sans-serif" font-weight="700" font-size="22" fill="#F4F4FB">buddy</text><rect x="12" y="42" width="176" height="14" rx="4" fill="#A487D0" stroke="#5E5F98" stroke-width="2.5"></rect><rect x="30" y="52" width="140" height="122" rx="14" fill="#C3C6CF" stroke="#5C6070" stroke-width="3"></rect><rect x="42" y="62" width="116" height="100" rx="6" fill="#1B2350"></rect><path fill="#FF74D4" d="M72 75 H88 V80 H92 V94 H87 V89 H73 V94 H68 V80 H72 Z M112 75 H128 V80 H132 V94 H127 V89 H113 V94 H108 V80 H112 Z"></path><text x="100" y="134" text-anchor="middle" font-family="VT323, monospace" font-size="22" fill="#FF74D4">Hey there!</text></svg>`;
const FACE = `<svg viewBox="26 48 148 130" aria-hidden="true" focusable="false"><rect x="30" y="52" width="140" height="122" rx="14" fill="#C3C6CF" stroke="#5C6070" stroke-width="3"></rect><rect x="42" y="62" width="116" height="100" rx="6" fill="#1B2350"></rect><g fill="#FF74D4"><rect x="68" y="84" width="24" height="10" rx="3"></rect><rect x="108" y="84" width="24" height="10" rx="3"></rect></g></svg>`;
const FEED_LABEL = {hint:'a hint', check:'checking your work', step:'one step · buddy’s work', recap:'recap', generate:'your problem', recognize:'reading your problem', confirm:'let’s go', end:'saved for later'};
async function dashboard() {
  if (busy) return;
  clearTimeout(checkTimer); await flush(); lesson=null; $('#breadcrumb').textContent='Learning dashboard';
  const rows=await api('/api/lessons');
  main.innerHTML=`<section class="hero"><div><span class="kicker">a little curiosity goes a long way</span><h1>Big ideas start<br>with little steps.</h1><p>Think out loud, try things, and figure it out together. Your work is saved right here.</p><button class="primary" id="new-main">＋ Start a lesson</button></div><div class="hero-robot">${ROBOT}</div></section>
  ${config.demo ? '<div class="demo-banner"><div><span class="tag tag--sun">offline demo</span><strong>Try Buddy, one step at a time.</strong><p>Offline demo · built-in examples · no microphone, API calls, or handwriting recognition.</p></div><button id="walkthrough">▶ Play workflow demo</button></div>' : !config.ready ? `<div class="demo-banner">Live tutor needs ${escapeHTML(config.key_name)} in the bridge environment file. Restart the service after adding it. You can still save work.</div>` : ''}
  <section class="stats"><div class="stat"><strong>${rows.length}</strong><span>Saved lessons</span></div><div class="stat"><strong>${rows.filter(r=>r.stage==='complete').length}</strong><span>Problems completed</span></div><div class="stat"><strong>${new Set(rows.map(r=>r.topic.toLowerCase())).size}</strong><span>Topics explored</span></div></section>
  <div class="section-title"><h2>Your learning journey</h2><input id="search" placeholder="Search your lessons…" aria-label="Search lessons"></div><section class="cards" id="cards"></section>`;
  const cards = filter => {
    $('#cards').innerHTML=rows.filter(r=>(r.topic+' '+r.problem).toLowerCase().includes(filter.toLowerCase())).map(r=>{const [cls,expr]=art(r.topic);return `<article class="lesson-card"><div class="card-art ${cls}">${expr}</div><div class="card-body"><span class="badge">${readableStage(r.stage)}</span>${r.demo?'<span class="badge demo">DEMO</span>':''}<h3>${escapeHTML(r.topic)}</h3><p>${escapeHTML(r.problem || 'Bring your problem and a little curiosity.')}</p><span class="small">${escapeHTML(r.level)}</span></div><div class="card-footer"><span>${new Date(r.updated*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'})} · ${r.mode==='learn'?'Practice':'Your problem'}</span><button data-open="${r.id}">${r.stage==='complete'?'Review':'Continue'} →</button></div></article>`}).join('') || '<div class="empty"><span class="kicker">nothing here yet</span><h3>Your next discovery belongs here.</h3><p>Start a lesson. Every sketch, idea, and helpful step will be saved.</p></div>';
    document.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>location.hash='lesson/'+b.dataset.open);
  }; cards(''); $('#search').oninput=e=>cards(e.target.value); $('#new-main').onclick=openNew;
  if ($('#walkthrough')) $('#walkthrough').onclick=()=>walkthrough().catch(fail);
  renderListen();
}
function openNew() { if (busy) return; $('#new-dialog').showModal(); $('#topic').focus(); say('Would you like to learn a topic or work on a problem you already have?'); }
async function openLesson(id) {
  clearTimeout(checkTimer); await flush(); lesson=await api('/api/action',{action:'select',id}); dirty=false; lastRevision=lesson.revision; ideasBase=lesson.revision; renderLesson();
  const draft=localStorage.getItem('buddy-draft-'+id);
  if (draft) { const parsed=JSON.parse(draft); if (parsed.revision===lesson.revision) { Object.assign(lesson,parsed.work); dirty=true; renderLesson(); status('Recovered unsaved work'); scheduleSave(); } }
}
function renderLesson() {
  const s=lesson; if(!s) return; $('#breadcrumb').textContent=s.topic;
  const input=['input','confirm'].includes(s.stage), done=s.stage==='complete', ended=s.stage==='ended';
  main.innerHTML=`<section class="lesson-head"><div><span class="kicker">${s.mode==='learn'?'learn a topic':'work through your problem'}${s.demo?' · offline demo':''}</span><h1>${escapeHTML(s.topic)}</h1><p>${escapeHTML(s.level)} <span class="slash">/</span> ${readableStage(s.stage)}</p></div><div class="toolbar-actions"><button id="history">Saved work</button><button id="export">Export</button><button id="finish">${ended?'Resume lesson':'Save & end'}</button></div></section>
  <div class="workspace"><section class="board-panel"><div class="problem-strip"><span class="kicker">${input?'your problem':'let’s try this'}</span><strong class="${(s.problem||'').length>160||(s.problem||'').includes('\n')?'long':''}">${escapeHTML(s.problem||'Write a problem or add a screenshot below.')}</strong></div>
  ${input?`<div class="input-area"><label for="upload" class="small">Upload a screenshot, or paste an image into this window</label><input id="upload" class="file-input" type="file" accept="image/png,image/jpeg,image/webp">${s.source_image?'<img id="source-preview" class="source-image" alt="Your original problem screenshot">':''}<label class="field">${s.stage==='confirm'?'Did Buddy read this correctly? Edit any unclear symbols.':'Or type the problem'}<textarea id="problem-input" placeholder="Example: Solve 2x + 3 = 11">${escapeHTML(s.problem)}</textarea></label><p>${s.demo?'Demo does not recognize images. Type the problem to continue.':'Buddy will read the problem first and ask you to confirm it.'}</p><button id="recognize">${s.stage==='confirm'?'Read again':'Read my problem'}</button>${s.stage==='confirm'?'<button id="confirm" class="primary">Yes, this is my problem</button>':''}</div>`:''}
  <div class="board-toolbar"><button id="pen" class="${tool==='pen'?'selected':''}" aria-pressed="${tool==='pen'}">✎ Pen</button><button id="eraser" class="${tool==='eraser'?'selected':''}" aria-pressed="${tool==='eraser'}">Eraser</button><button id="text" class="${tool==='text'?'selected':''}" aria-pressed="${tool==='text'}" title="Click the board to place a text box">T Text</button><button id="undo">Undo</button><button id="clear">Clear board</button><span>Your thinking goes here</span></div><canvas id="board" width="1200" height="775" aria-label="Whiteboard. Draw with a mouse or pen, or use the ideas text box below."></canvas>
  <div class="ideas-area"><label for="ideas">${s.mode==='help'?'How would you start? Show your ideas or tell Buddy where you are stuck.':'Your ideas, working, or answer'}</label><textarea id="ideas" placeholder="I think the first thing to do is…">${escapeHTML(s.ideas)}</textarea></div>
  ${done?'<div class="completion"><h3>One more idea figured out. ✦</h3><p class="small">Review how you got here, then try something new.</p><button id="recap">Explain the method</button><button id="another" class="primary">Another problem</button><button id="change">Change topic</button></div>':''}
  ${s.stage==='setup'?'<div class="completion"><button id="generate" class="primary">Give me a problem</button></div>':''}</section>
  <aside class="tutor-panel"><div class="tutor-head">${FACE}<div><h3>Buddy is here.</h3><p>Let's take it one step at a time.</p></div></div><p id="listen-banner" class="listen-banner hidden" aria-hidden="true"><span class="listen-dot"></span>buddy is listening</p><div class="tutor-feed" id="feed"></div><div class="tutor-controls"><div class="listen-box"><button id="think-aloud" class="help help--listen" aria-pressed="false" aria-describedby="listen-note">◉ Think out loud</button><p id="listen-note" class="small listen-note"></p></div><button id="hint" class="help help--hint">✦ Give me a hint</button><button id="check" class="help help--check">✓ Check my work</button><button id="step" class="help help--step">→ Show one step</button><button id="stuck">I don't know how to start</button></div><label class="supervise"><input type="checkbox" id="supervise" ${supervise?'checked':''}>Check after a pause (5 seconds)</label><p class="small supervise">Your writing stays on the board. Buddy's steps appear here.</p></aside></div>`;
  if ($('#source-preview')) $('#source-preview').src=s.source_image;
  renderFeed(); bindCanvas(); drawBoard();
  $('#ideas').oninput=()=>{lesson.ideas=$('#ideas').value; markDirty();};
  if ($('#problem-input')) $('#problem-input').oninput=()=>{lesson.problem=$('#problem-input').value;markDirty();};
  TOOLS.forEach(t=>{$('#'+t).onclick=()=>selectTool(t);});
  $('#undo').onclick=()=>{closeTextEditor(false);lesson.strokes.pop();drawBoard();markDirty();};
  $('#clear').onclick=()=>{closeTextEditor(false);lesson.strokes=[];drawBoard();markDirty();};
  ['hint','check','step','recognize','confirm','generate','recap'].forEach(a=>{if($('#'+a)) $('#'+a).onclick=()=>act(a).catch(fail);});
  $('#stuck').onclick=async()=>{lesson.stuck=true;markDirty();await act('hint').catch(fail);};
  $('#finish').onclick=()=>act(ended?'resume':'end').catch(fail);
  $('#history').onclick=()=>showHistory().catch(fail); $('#export').onclick=()=>download().catch(fail);
  if($('#another')) $('#another').onclick=()=>createLesson({mode:'learn',topic:s.topic,level:s.level}).catch(fail);
  if($('#change')) $('#change').onclick=openNew;
  $('#supervise').onchange=e=>{supervise=e.target.checked;if(!supervise)clearTimeout(checkTimer);};
  $('#think-aloud').onclick=()=>toggleListen();
  if($('#upload')) $('#upload').onchange=e=>upload(e.target.files[0]).catch(fail);
  if(input || done || ended || s.stage==='setup') ['hint','check','step','stuck'].forEach(id=>$('#'+id).disabled=true);
  if(ended){$('#ideas').disabled=true;['pen','eraser','text','undo','clear'].forEach(id=>$('#'+id).disabled=true);}
  status('Saved locally');
  renderListen();
}
/* Think out loud: the toggle, the note under it, the panel banner and the header pill, all from one state. */
function renderListen() {
  const on=listening.state==='on', starting=listening.state==='starting', active=on||starting;
  const text=on?'buddy is listening':starting?'Getting buddy’s ears ready…':'';
  const pill=$('#listen-pill');
  if(pill.textContent!==text){pill.innerHTML=text?`<span class="listen-dot" aria-hidden="true"></span>${escapeHTML(text)}`:'';}
  pill.classList.toggle('hidden',!text); pill.classList.toggle('listen-pill--on',on);
  const button=$('#think-aloud'); if(!button||!lesson) return;
  const here=!listening.lesson_id||listening.lesson_id===lesson.id, ready=['working','complete'].includes(lesson.stage);
  button.setAttribute('aria-pressed',String(active));
  button.textContent=active?'■ Stop listening':'◉ Think out loud';
  button.disabled=listenBusy||(!active&&(!listening.available||!ready));
  $('#listen-note').textContent=!listening.available?(listening.reason||'Think out loud needs the buddy robot app running.')
    :active?(here?'Talk through your thinking. buddy saves what you say to your ideas. Say “I’m done” to finish.':'buddy is listening to another lesson. Stop it here first.')
    :!ready?(lesson.stage==='ended'?'Resume this lesson to think out loud.':'Get your problem ready, then think out loud.')
    :'buddy listens with the microphone only while this is on. Nothing is recorded.';
  $('#listen-banner').classList.toggle('hidden',!on);
}
function setListening(result) { listening={available:!!result.available,state:result.state||'off',lesson_id:result.lesson_id||null,reason:result.reason||''}; renderListen(); }
async function pollListen() { try { const fresh=await api('/api/listening'); if(JSON.stringify(fresh)!==JSON.stringify(listening)) setListening(fresh); } catch { /* Keep the last state during a transient disconnect. */ } }
async function toggleListen() {
  if(listenBusy||!lesson) return; clearError();
  const stop=listening.state!=='off'; listenBusy=true; renderListen();
  try { if(!stop) await flush(); setListening(await api('/api/action',{action:stop?'stop-listening':'listen',id:lesson.id})); if(!stop) say('I am listening.'); }
  catch(e) { fail(e); await pollListen(); }
  finally { listenBusy=false; renderListen(); }
}
function renderReferences(event) {
  const sources = (event.sources || []).filter(s => {
    try { const u = new URL(s.url); return u.protocol === 'https:' && !u.username && !u.password; }
    catch { return false; }
  });
  return `${event.search_note ? `<p>${escapeHTML(event.search_note)}</p>` : ''}${sources.length ? `<details><summary>Practice references (may include answers)</summary><ul>${sources.map(s => `<li><a href="${escapeHTML(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHTML(s.title)}</a></li>`).join('')}</ul></details>` : ''}`;
}
function renderFeed() {
  const feed=$('#feed'); if(!feed)return;
  feed.innerHTML=lesson.events.filter(e=>e.feedback||e.step).map(e=>`<div class="bubble"><span class="kicker">${escapeHTML(FEED_LABEL[e.action] || e.action)}</span>${e.step?`<strong class="math-step">${escapeHTML(e.step)}</strong>`:''}${escapeHTML(e.feedback)}${renderReferences(e)}</div>`).join('') || `<div class="bubble">${lesson.mode==='help'?'First, add your problem. Then write your ideas—even a small start helps.':'Take your time. Try an idea, draw it out, or ask me for a hint.'}</div>`;
  feed.scrollTop=feed.scrollHeight;
}
function point(e) { const r=$('#board').getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))]; }
const TOOLS=['pen','eraser','text'];
function selectTool(t){tool=t;TOOLS.forEach(id=>{const b=$('#'+id);if(!b)return;b.classList.toggle('selected',id===t);b.setAttribute('aria-pressed',String(id===t));});const c=$('#board');if(c)c.style.cursor=t==='text'?'text':'crosshair';}
/* Text boxes are strokes too: {tool:'text', points:[[x,y]], text}. Their drawn bounding boxes live here, keyed by stroke,
   only for hit-testing; they are never saved. */
const textBoxes=new WeakMap();
let textEditor=null;
function textAt(p){const c=$('#board');for(let i=lesson.strokes.length-1;i>=0;i--){const s=lesson.strokes[i];if(s.tool!=='text')continue;const b=textBoxes.get(s);if(!b)continue;const x=p[0]*c.width,y=p[1]*c.height;if(x>=b.x&&x<=b.x+b.w&&y>=b.y&&y<=b.y+b.h)return s;}return null;}
function openTextEditor(p,existing){closeTextEditor(true);const c=$('#board');const wrap=c.parentElement;const r=c.getBoundingClientRect(),w=wrap.getBoundingClientRect();const anchor=existing?existing.points[0]:p;const box=document.createElement('textarea');box.className='board-text-editor';box.setAttribute('aria-label','Text box on the whiteboard');box.rows=2;box.value=existing?existing.text:'';box.style.left=(r.left-w.left+anchor[0]*r.width)+'px';box.style.top=(r.top-w.top+anchor[1]*r.height)+'px';box.style.maxWidth=Math.max(160,(1-anchor[0])*r.width)+'px';textEditor={box,existing,anchor};box.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();closeTextEditor(false);}else if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();closeTextEditor(true);}};box.onblur=()=>closeTextEditor(true);wrap.appendChild(box);if(existing)drawBoard(existing);box.focus();}
function closeTextEditor(commit){if(!textEditor)return;const {box,existing,anchor}=textEditor;textEditor=null;box.onblur=null;box.remove();if(commit&&!busy&&lesson&&lesson.stage!=='ended'){const text=box.value.replace(/\r\n?/g,'\n').trim().slice(0,500);if(existing){if(text)existing.text=text;else lesson.strokes.splice(lesson.strokes.indexOf(existing),1);markDirty();}else if(text){lesson.strokes.push({tool:'text',points:[anchor],text});markDirty();}}drawBoard();}
function bindCanvas(){const c=$('#board');c.style.cursor=tool==='text'?'text':'crosshair';c.onpointerdown=e=>{if(busy||lesson.stage==='ended')return;e.preventDefault();if(tool==='text'){const p=point(e);openTextEditor(p,textAt(p));return;}if(textEditor){closeTextEditor(true);}c.setPointerCapture(e.pointerId);drawing=true;stroke={tool,points:[point(e)]};lesson.strokes.push(stroke);drawBoard();};c.onpointermove=e=>{if(!drawing)return;stroke.points.push(point(e));drawBoard();};const end=()=>{if(!drawing)return;drawing=false;markDirty();};c.onpointerup=end;c.onpointercancel=end;}
const TEXT_FONT='40px "Gochi Hand", "Chalkboard SE", "Comic Sans MS", cursive', TEXT_LINE=48;
function wrapText(ctx,text,maxWidth){const lines=[];for(const raw of text.split('\n')){let line='';for(const word of raw.split(' ')){const trial=line?line+' '+word:word;if(line&&ctx.measureText(trial).width>maxWidth){lines.push(line);line=word;}else line=trial;}lines.push(line);}return lines;}
function drawText(ctx,c,s,skip){const x=s.points[0][0]*c.width,y=s.points[0][1]*c.height;ctx.font=TEXT_FONT;ctx.textBaseline='top';ctx.fillStyle='#1F3A78';const lines=wrapText(ctx,s.text,Math.max(120,c.width-x-8));let w=0;lines.forEach((line,i)=>{w=Math.max(w,ctx.measureText(line).width);if(!skip)ctx.fillText(line,x,y+i*TEXT_LINE);});textBoxes.set(s,{x,y,w:Math.max(w,24),h:lines.length*TEXT_LINE});}
function drawBoard(editing){const c=$('#board');if(!c||!lesson)return;const ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);for(const s of lesson.strokes){ctx.globalCompositeOperation=s.tool==='eraser'?'destination-out':'source-over';if(s.tool==='text'){drawText(ctx,c,s,s===editing);continue;}ctx.strokeStyle='#1F3A78';ctx.lineWidth=s.tool==='eraser'?28:3.5;ctx.lineCap='round';ctx.lineJoin='round';ctx.beginPath();s.points.forEach((p,i)=>{if(!i)ctx.moveTo(p[0]*c.width,p[1]*c.height);else ctx.lineTo(p[0]*c.width,p[1]*c.height);});if(s.points.length===1){const p=s.points[0];ctx.lineTo(p[0]*c.width+.1,p[1]*c.height+.1);}ctx.stroke();}ctx.globalCompositeOperation='source-over';}
function boardImage(){const c=$('#board');if(!c)return lesson.board_image;const flat=document.createElement('canvas');flat.width=c.width;flat.height=c.height;const ctx=flat.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(c,0,0);return flat.toDataURL('image/png');}
function savedStrokes(){return lesson.strokes.map(s=>s.tool==='text'?{tool:'text',points:s.points,text:s.text}:{tool:s.tool,points:s.points});}
function work(){return {ideas:lesson.ideas,problem:lesson.problem,stuck:lesson.stuck,strokes:savedStrokes(),source_image:lesson.source_image,board_image:boardImage()};}
function markDirty(){dirty=true;status('Saving…');try{localStorage.setItem('buddy-draft-'+lesson.id,JSON.stringify({revision:lesson.revision,work:work()}));}catch{/* Server persistence remains primary when browser quota is full. */}scheduleSave();clearTimeout(checkTimer);if(supervise&&lesson.stage==='working')checkTimer=setTimeout(()=>{if(!busy&&!drawing)act('check').catch(fail);},5000);}
function scheduleSave(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>flush().catch(fail),650);}
let saving=Promise.resolve();
/* A save can come back with more ideas than it sent: lines buddy heard while the learner typed. Show them unless the learner
   typed again meanwhile; then keep ideasBase old, so the next save asks the server to put them back again. */
function flush(){clearTimeout(saveTimer);saving=saving.catch(()=>{}).then(async()=>{if(!dirty||!lesson)return;const current=lesson;const payload=work();dirty=false;saveInFlight=true;try{const saved=await api('/api/action',{action:'save',id:current.id,revision:current.revision,ideas_base:ideasBase,work:payload});if(lesson===current){lesson.revision=saved.revision;lastRevision=saved.revision;const box=$('#ideas');if(saved.ideas===payload.ideas){ideasBase=saved.revision;}else if(!dirty&&(!box||box.value===payload.ideas)){lesson.ideas=saved.ideas;if(box)box.value=saved.ideas;ideasBase=saved.revision;}if(!dirty){localStorage.removeItem('buddy-draft-'+current.id);status('Saved locally');}else scheduleSave();}}catch(e){dirty=true;status('Not saved — retry needed');throw e;}finally{saveInFlight=false;}});return saving;}
async function act(action){if(busy||!lesson)return;clearError();clearTimeout(checkTimer);setBusy(true);try{await flush();status(action==='step'?'Buddy is working on one step…':'Buddy is thinking…');lesson=await api('/api/action',{action,id:lesson.id,revision:lesson.revision});lastRevision=lesson.revision;ideasBase=lesson.revision;renderLesson();const last=lesson.events.at(-1);if(last)say([last.step,last.feedback].filter(Boolean).join('. '));}finally{setBusy(false);if(lesson)renderLesson();}}
async function createLesson(data){clearTimeout(checkTimer);await flush();setBusy(true);try{lesson=await api('/api/action',{action:'create',...data});dirty=false;ideasBase=lesson.revision;historyReplace(lesson.id);renderLesson();}finally{setBusy(false);if(lesson)renderLesson();}if(lesson.mode==='learn')await act('generate');}
function historyReplace(id){window.history.replaceState(null,'','#lesson/'+id);}
async function upload(file){if(!file||!lesson||!['input','confirm'].includes(lesson.stage))return;if(!/^image\/(png|jpeg|webp)$/.test(file.type)||file.size>10*1024*1024)throw new Error('Choose a PNG, JPEG or WebP smaller than 10 MB.');const image=await createImageBitmap(file);const c=document.createElement('canvas');const scale=Math.min(1,1600/Math.max(image.width,image.height));c.width=image.width*scale;c.height=image.height*scale;c.getContext('2d').drawImage(image,0,0,c.width,c.height);image.close();lesson.source_image=c.toDataURL('image/jpeg',.9);markDirty();await flush();renderLesson();}
document.addEventListener('paste',e=>{const file=[...(e.clipboardData?.items||[])].find(x=>x.type.startsWith('image/'))?.getAsFile();if(file&&lesson&&['input','confirm'].includes(lesson.stage)){e.preventDefault();upload(file).catch(fail);}});
async function showHistory(){await flush();const rows=await api('/api/lessons/'+lesson.id+'/history');$('#history-list').innerHTML=rows.slice().reverse().map(r=>`<details class="history-row"><summary>Revision ${r.revision} · ${new Date(r.updated*1000).toLocaleTimeString()} · ${readableStage(r.stage)}</summary><p>${escapeHTML(r.problem)}</p><pre>${escapeHTML(r.ideas||'No typed ideas in this revision.')}</pre>${r.source_image?`<img src="${r.source_image}" alt="Original screenshot">`:''}${r.board_image?`<img src="${r.board_image}" alt="Saved whiteboard revision ${r.revision}">`:''}<p>${r.strokes.filter(s=>s.tool!=='text').length} pen / eraser strokes · ${r.strokes.filter(s=>s.tool==='text').length} text boxes · ${r.events.length} tutor events</p></details>`).join('');$('#history-dialog').showModal();}
async function download(){await flush();const revisions=await api('/api/lessons/'+lesson.id+'/history');const url=URL.createObjectURL(new Blob([JSON.stringify({lesson,revisions},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='buddy-lesson-'+lesson.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function walkthrough(){if(!config.demo||tour)return;tour=true;try{await createLesson({mode:'learn',topic:'Algebra — balancing equations',level:'Grades 6–8'});await pause(1500);lesson.ideas='I think I should subtract 3 from both sides.';$('#ideas').value=lesson.ideas;markDirty();await flush();await act('hint');await pause(1600);for(let i=0;i<3;i++){await act('step');await pause(1700);}await act('recap');await pause(1500);location.hash='';}finally{tour=false;}}
$('#new-side').onclick=openNew;$('#close-dialog').onclick=()=>$('#new-dialog').close();$('#close-history').onclick=()=>$('#history-dialog').close();
$('#new-form').onsubmit=async e=>{e.preventDefault();const mode=new FormData(e.target).get('mode');const topic=$('#topic').value.trim();if(mode==='learn'&&!topic){$('#topic').focus();return;}$('#new-dialog').close();await createLesson({mode,topic,level:$('#level').value}).catch(fail);};
$('#speak-toggle').onclick=()=>{speak=!speak;$('#speak-toggle').textContent='Read aloud: '+(speak?'on':'off');$('#speak-toggle').setAttribute('aria-pressed',speak);if(!speak&&'speechSynthesis'in window)speechSynthesis.cancel();};
window.addEventListener('beforeunload',e=>{if(dirty||busy){e.preventDefault();e.returnValue='';}});
async function route(){clearError();if(busy)return;const id=location.hash.startsWith('#lesson/')?location.hash.slice(8):null;if(id)await openLesson(id);else await dashboard();}
window.addEventListener('hashchange',()=>route().catch(fail));
/* A spoken idea lands as a new revision. While the learner's cursor is in the ideas box, update the text in place instead of
   redrawing the page, so the caret and focus stay where they are. */
function applyFresh(fresh){const box=$('#ideas');const focused=box&&document.activeElement===box&&fresh.stage===lesson.stage&&fresh.events.length===lesson.events.length;lesson=fresh;lastRevision=fresh.revision;ideasBase=fresh.revision;if(focused){const end=box.selectionStart===box.value.length;const at=box.selectionStart;box.value=fresh.ideas;if(end)box.selectionStart=box.selectionEnd=box.value.length;else box.selectionStart=box.selectionEnd=Math.min(at,box.value.length);renderListen();}else renderLesson();}
(async()=>{config=await api('/api/config');$('#mode-label').textContent=config.demo?'OFFLINE DEMO':`${config.provider.toUpperCase()} · ${config.model}`;listening.available=!!config.listen_available;await pollListen();await route();setInterval(pollListen,1500);setInterval(async()=>{if(!lesson||busy||dirty||drawing||saveInFlight||textEditor)return;try{const fresh=await api('/api/lessons/'+lesson.id);if(fresh.revision>lastRevision&&!dirty&&lesson.id===fresh.id)applyFresh(fresh);}catch{/* Keep work visible during transient disconnects. */}},3000);})().catch(fail);
