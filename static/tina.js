const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const box = document.getElementById('tasks');

fetch('/colour_scheme.xml').then(r => r.ok ? r.text() : '').then(x => {
  const map = {background:'--bg', panel:'--panel', border:'--border', divider:'--divider', text:'--text', text_sec:'--text-sec',
               text_dim:'--text-dim', accent:'--accent', accent2:'--accent2', ok:'--ok', warn:'--warn', problem:'--problem'};
  for (const m of x.matchAll(/name="([^"]+)"\s+value="(#[0-9A-Fa-f]{6})"/g)) if (map[m[1]]) document.documentElement.style.setProperty(map[m[1]], m[2]);
}).catch(() => {});

function render(ev) {
  const d = ev.data;
  switch (ev.kind) {
    case 'think': return `<div class="ev think">${esc(d)}</div>`;
    case 'say': return `<div class="ev say">${esc(d)}</div>`;
    case 'tool': return `<div class="ev tool">&rarr; ${esc(d.name)}(${esc(Object.entries(d.args).map(([k, v]) => k + '=' + JSON.stringify(v).slice(0, 80)).join(', '))})</div>`;
    case 'result': return `<div class="ev result">${esc(d.text)}</div>`;
    case 'memory': return `<div class="ev memory">remembers: ${d.map(esc).join(' &middot; ')}</div>`;
    case 'ask': return `<div class="ev tool">asked: ${esc(d)}</div>`;
    case 'answered': return `<div class="ev memory">you said ${esc(d)}</div>`;
    case 'error': return `<div class="ev error">${esc(d)}</div>`;
    case 'answer': return `<div class="ev answer">${esc(d)}</div>`;
  }
  return '';
}

function start(text, voice = false) {
  document.getElementById('empty')?.remove();
  fetch('/task', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task: text, voice})})
    .then(r => r.json()).then(({id}) => {
      const el = document.createElement('section');
      el.className = 'task';
      el.innerHTML = `<div class="head"><b>${voice ? '<span class="said">said</span> ' : ''}${esc(text)}</b><span class="st">working...</span><button onclick="stopTask('${id}')">STOP</button></div><div class="evs"></div><div class="ask" style="display:none"></div>`;
      box.prepend(el);
      poll(id, el, 0, voice);
    });
}

function poll(id, el, since, voiced = false) {
  fetch(`/task/${id}?since=${since}`).then(r => r.json()).then(d => {
    el.querySelector('.evs').insertAdjacentHTML('beforeend', d.events.map(render).join(''));
    const ask = el.querySelector('.ask');
    if (d.question) {
      ask.style.display = '';
      ask.innerHTML = `<p>${esc(d.question)}</p><button class="yes" onclick="reply('${id}', true)">ALLOW</button> <button class="no" onclick="reply('${id}', false)">DENY</button>`;
    } else ask.style.display = 'none';
    el.querySelector('.st').textContent = d.status === 'running' ? 'working...' : d.status + (d.result ? ` · ${d.result.steps} steps · ${d.result.seconds}s` : '');
    if (d.status === 'running') setTimeout(() => poll(id, el, d.next, voiced), 800);
    else {
      el.querySelector('.head button').remove();
      if (voiced) voiceDone(d.result);     // a voice task: her final answer is spoken, never her reasoning
    }
  }).catch(() => setTimeout(() => poll(id, el, since, voiced), 2000));
}

function reply(id, yes) { fetch(`/task/${id}/answer`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({yes})}); }
function stopTask(id) { fetch(`/task/${id}/stop`, {method: 'POST'}); }

const t = document.getElementById('t');
document.getElementById('f').addEventListener('submit', e => { e.preventDefault(); if (t.value.trim()) { start(t.value.trim()); t.value = ''; } });
t.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); document.getElementById('f').requestSubmit(); } });

function state() {
  fetch('/status').then(r => r.json()).then(s => {
    document.getElementById('state').textContent =
      `${s.model_ready ? 'model ready' : 'model missing'} · memory ${s.memory.reachable ? 'on' : 'offline'}${s.memory.pending ? ` (${s.memory.pending} waiting)` : ''}${s.busy ? ' · busy' : ''}${s.voice.piper && s.voice.voice ? '' : ' · no voice'}`;
  }).catch(() => {});
}
state(); setInterval(state, 10000);

// ---- her reasoning: shown or hidden (it's never spoken either way)
const showThink = document.getElementById('showThink');
try { showThink.checked = localStorage.getItem('tina.showThink') !== 'off'; } catch (e) {}
const applyThink = () => document.body.classList.toggle('hide-think', !showThink.checked);
showThink.addEventListener('change', () => {
  applyThink();
  try { localStorage.setItem('tina.showThink', showThink.checked ? 'on' : 'off'); } catch (e) {}
});
applyThink();

// ---- voice chat: say "Hey TINA". The page hears you (mic level, a clip per thing
// you say), her Whisper works out what you said, she answers in her Piper voice.
const mic = document.getElementById('mic');
const vbar = document.getElementById('voicebar');
const vstate = document.getElementById('vstate');
const level = document.getElementById('level');
const heardEl = document.getElementById('heard');

const LABELS = {
  off: 'MIC OFF', wake: 'SAY "HEY TINA"', hearing: 'HEARING YOU', transcribing: 'UNDERSTANDING...',
  command: 'GO AHEAD', thinking: 'THINKING...', speaking: 'SPEAKING', error: 'ERROR',
};
const START_X = 3;           // speech: this many times louder than the room
const MIN_START = 0.015;     // ...and at least this loud (RMS)
const SILENCE_MS = 900;      // a pause this long ends what you're saying
const MAX_MS = 15000;        // longest clip
const MIN_SPEECH_MS = 300;   // shorter is a bump, not a word
const FOLLOW_MS = 8000;      // after she answers (or says "Yes?"), talk without her name for this long

let voiceOn = false, vs = 'off', stream = null, ctx = null, analyser = null, buf = null;
let recorder = null, chunks = [], clipMode = 'wake', speechStart = 0, lastLoud = 0, floor = 0.01;
let followUntil = 0, playing = null;

function setState(s, text) {
  vs = s;
  vstate.textContent = text || LABELS[s];
  vstate.className = 'vstate ' + s;
  mic.classList.toggle('on', voiceOn);
  mic.textContent = voiceOn ? 'MIC ON' : 'MIC';
  vbar.hidden = !voiceOn && s !== 'error';
}
const listening = () => Date.now() < followUntil ? 'command' : 'wake';

async function voiceStart() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
  } catch (e) {
    voiceOn = false;
    setState('error', 'NO MIC: ' + (e.name === 'NotAllowedError' ? 'allow the microphone for this page' : e.message));
    return;
  }
  ctx = new AudioContext();
  analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  buf = new Float32Array(analyser.fftSize);
  heardEl.textContent = '';
  setState('wake');
  tick();
}

function voiceStop() {
  voiceOn = false;
  if (recorder) { recorder.onstop = null; recorder.stop(); recorder = null; }
  stream?.getTracks().forEach(tr => tr.stop());
  ctx?.close();
  playing?.pause();
  stream = ctx = playing = null;
  level.style.width = '0';
  setState('off');
}

function tick() {
  if (!voiceOn) return;
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  const rms = Math.sqrt(sum / buf.length);
  level.style.width = Math.min(100, rms * 600) + '%';
  const now = performance.now();
  if (vs === 'command' && !recorder && Date.now() > followUntil) setState('wake');
  if (vs === 'wake' || vs === 'command' || vs === 'hearing') {
    const loud = rms > Math.max(MIN_START, floor * START_X);
    if (!recorder && !loud) floor = floor * 0.95 + rms * 0.05;      // learn how loud the room is
    if (loud) lastLoud = now;
    if (!recorder && loud) beginClip(now);
    else if (recorder && (now - lastLoud > SILENCE_MS || now - speechStart > MAX_MS)) endClip();
  }
  setTimeout(tick, 50);
}

function beginClip(now) {
  chunks = [];
  clipMode = listening();
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
  recorder.start();
  speechStart = lastLoud = now;
  setState('hearing');
}

function endClip() {
  const r = recorder, spoke = lastLoud - speechStart, mode = clipMode;
  recorder = null;
  r.onstop = () => {
    if (!voiceOn) return;
    if (spoke < MIN_SPEECH_MS) return setState(listening());
    send(new Blob(chunks, {type: r.mimeType}), mode);
  };
  r.stop();
  setState('transcribing');
}

function send(blob, mode) {
  const fd = new FormData();
  fd.append('audio', blob, blob.type.includes('ogg') ? 'clip.ogg' : 'clip.webm');
  fd.append('mode', mode);
  const retry = msg => { setState('error', msg); setTimeout(() => voiceOn && setState(listening()), 4000); };
  fetch('/hear', {method: 'POST', body: fd}).then(r => r.json()).then(d => {
    if (!voiceOn) return;
    if (d.error) return retry(d.error.slice(0, 80));
    heardEl.textContent = d.heard ? `heard: "${d.heard}"` + (d.woke ? '' : ' (no "Hey TINA")') : 'heard nothing';
    if (d.task) { followUntil = 0; setState('thinking'); start(d.task, true); }
    else if (d.reply) speak(d.reply, () => { followUntil = Date.now() + FOLLOW_MS; setState('command'); });
    else setState(listening());
  }).catch(() => voiceOn && retry("TINA isn't answering"));
}

function speak(url, then) {
  const audio = playing = new Audio(url);
  let done = false;
  const finish = () => {
    if (done || playing !== audio) return;
    done = true; playing = null;
    if (voiceOn) then();
  };
  setState('speaking');
  audio.onended = audio.onerror = finish;
  audio.play().catch(finish);
}

function voiceDone(result) {
  if (!voiceOn) return;
  const follow = () => { followUntil = Date.now() + FOLLOW_MS; setState('command'); };
  if (result?.audio) speak(result.audio, follow);
  else follow();
}

if (!navigator.mediaDevices || !window.MediaRecorder) { mic.disabled = true; mic.title = "This browser can't record the mic"; }
mic.addEventListener('click', () => { if (voiceOn) voiceStop(); else { voiceOn = true; voiceStart(); } });
