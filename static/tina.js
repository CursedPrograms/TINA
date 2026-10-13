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
      el.innerHTML = `<div class="head"><b>${esc(text)}</b><span class="st">working...</span><button onclick="stopTask('${id}')">STOP</button></div><div class="evs"></div><div class="ask" style="display:none"></div>`;
      box.prepend(el);
      poll(id, el, 0);
    });
}

function poll(id, el, since) {
  fetch(`/task/${id}?since=${since}`).then(r => r.json()).then(d => {
    el.querySelector('.evs').insertAdjacentHTML('beforeend', d.events.map(render).join(''));
    const ask = el.querySelector('.ask');
    if (d.question) {
      ask.style.display = '';
      ask.innerHTML = `<p>${esc(d.question)}</p><button class="yes" onclick="reply('${id}', true)">ALLOW</button> <button class="no" onclick="reply('${id}', false)">DENY</button>`;
    } else ask.style.display = 'none';
    el.querySelector('.st').textContent = d.status === 'running' ? 'working...' : d.status + (d.result ? ` · ${d.result.steps} steps · ${d.result.seconds}s` : '');
    if (d.status === 'running') setTimeout(() => poll(id, el, d.next), 800);
    else {
      el.querySelector('.head button').remove();
      if (d.result?.audio) speak(d.result.audio);      // a voice task: her final answer, never her reasoning
      else if (voiceMode) listen();
    }
  }).catch(() => setTimeout(() => poll(id, el, since), 2000));
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

// ---- voice chat: the browser hears you, she answers in her Piper voice, then listens again
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const mic = document.getElementById('mic');
let voiceMode = false, rec = null, playing = null;

function setMic(text, on) { mic.textContent = text; mic.classList.toggle('on', on); }

function listen() {
  if (!voiceMode) return;
  rec = new SR();
  rec.lang = 'en-US';
  rec.interimResults = true;
  let heard = '';
  rec.onresult = e => {
    const r = e.results[e.results.length - 1];
    t.value = r[0].transcript;
    if (r.isFinal) heard = r[0].transcript.trim();
  };
  rec.onend = () => {
    rec = null;
    if (!voiceMode) return;
    if (heard) { t.value = ''; setMic('WAIT', true); start(heard, true); }
    else { voiceMode = false; setMic('MIC', false); }      // silence: voice chat ends
  };
  rec.onerror = e => { if (e.error === 'not-allowed') { voiceMode = false; setMic('MIC', false); } };
  setMic('LISTENING', true);
  rec.start();
}

function speak(url) {
  const audio = playing = new Audio(url);
  let done = false;
  const finish = () => {
    if (done || playing !== audio) return;
    done = true; playing = null;
    voiceMode ? listen() : setMic('MIC', false);
  };
  setMic(voiceMode ? 'SPEAKING' : 'MIC', voiceMode);
  audio.onended = audio.onerror = finish;
  audio.play().catch(finish);
}

if (!SR) { mic.disabled = true; mic.title = 'Voice chat needs Chrome or Edge'; }
mic.addEventListener('click', () => {
  voiceMode = !voiceMode;
  if (voiceMode) listen();
  else { rec?.abort(); playing?.pause(); playing = null; setMic('MIC', false); }
});
