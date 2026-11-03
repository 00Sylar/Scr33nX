// Scr33nX hover mini player — runs inside the iframe content.js places next
// to a thumbnail. Asks the app for the model's relay URL (GET /preview, the
// same stream a Player tile uses) and plays it with hls.js. The control bar
// (REC/STOP, AUTO, + Recorder, Saved, rank stars) uses the same API calls as
// the popup, with state from /status polled while the player is open.
const API = 'http://localhost:5200';  // v2 (TEST) port

const params  = new URLSearchParams(location.search);
const name    = params.get('name') || '';
const site    = params.get('site') || '';
const quality = parseInt(params.get('q') || '0', 10) || 0;
const any     = params.get('any') === '1';   // "Preview any model": resolved fresh

const $     = (id) => document.getElementById(id);
const video = $('v');
const msg   = $('msg');
const snd   = $('snd');
$('name').textContent = name;
video.muted = params.get('muted') !== '0';

let hls = null;
let token = '';
const tokenReady = chrome.storage.local.get('apiToken')
  .then((st) => { token = (st && st.apiToken) || ''; })
  .catch(() => {});

function show(text) { msg.textContent = text; msg.hidden = !text; }
function syncSound() { snd.textContent = video.muted ? '🔇' : '🔊'; }

function play() {
  // Unmuted autoplay can be refused (no user gesture yet) — fall back to muted.
  video.play().catch(() => {
    video.muted = true;
    syncSound();
    video.play().catch(() => {});
  });
}

// Clicks on the video toggle sound; the controls handle their own clicks.
document.body.addEventListener('click', (e) => {
  if (e.target.closest('#ctl, #stars, #confirm')) return;
  video.muted = !video.muted;
  syncSound();
  if (video.paused) play();
});

// GET when body is undefined, else POST JSON. null = app not reachable.
async function api(path, body) {
  await tokenReady;
  const headers = token ? { 'X-Api-Token': token } : {};
  const init = { headers, signal: AbortSignal.timeout(body === undefined ? 3000 : 5000) };
  if (body !== undefined) {
    init.method = 'POST';
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(`${API}${path}`, init);
    return await r.json();
  } catch {
    return null;
  }
}

async function start() {
  const qs = new URLSearchParams({ name, site, quality: String(quality),
                                   any: any ? '1' : '0' });
  // A fresh resolve ("Preview any model") can take a while — allow 15 s.
  await tokenReady;
  let res = null;
  try {
    const r = await fetch(`${API}/preview?${qs}`, {
      headers: token ? { 'X-Api-Token': token } : {},
      signal: AbortSignal.timeout(15000),
    });
    res = await r.json();
  } catch {}
  if (!res) { show('Scr33nX app not reachable'); return; }
  if (!res.ok || !res.url) {
    show(res.tile_msg || res.error || 'Preview not available');
    return;
  }
  video.addEventListener('playing', () => show(''), { once: true });
  if (window.Hls && Hls.isSupported()) {
    // No worker: the extension CSP blocks hls.js's blob: worker.
    hls = new Hls({ enableWorker: false, maxBufferLength: 8,
                    liveSyncDurationCount: 2 });
    hls.on(Hls.Events.MANIFEST_PARSED, play);
    hls.on(Hls.Events.ERROR, (_e, d) => { if (d.fatal) show('Stream ended'); });
    hls.loadSource(res.url);
    hls.attachMedia(video);
  } else {
    video.src = res.url;
    play();
  }
}

// ── Controls ────────────────────────────────────────────────────────────────
const recBtn   = $('rec');
const autoBtn  = $('auto');
const addBtn   = $('add');
const savedBtn = $('saved');
const starsEl  = $('stars');
const stars    = [...starsEl.querySelectorAll('span')];

let st   = null;    // last /status answer
let busy = false;   // an action is in flight — buttons stay disabled

function renderCtl() {
  const ok    = !!st;
  const inRec = ok && st.in_recorder;
  const rec   = ok && st.status === 'recording';
  recBtn.querySelector('.ic').textContent  = rec ? '■' : '●';
  recBtn.querySelector('.lbl').textContent = rec ? ' STOP' : ' REC';
  recBtn.classList.toggle('stop', rec);
  recBtn.title = rec ? 'Stop recording'
    : inRec ? 'Start recording' : 'Add to Recorder and start recording';
  recBtn.disabled = busy || !ok;

  autoBtn.classList.toggle('on', ok && !!st.auto);
  autoBtn.title = !inRec ? 'Auto-Record (add to Recorder first)'
    : st.auto ? 'Auto-Record is on — click to turn off' : 'Auto-Record is off — click to turn on';
  autoBtn.disabled = busy || !inRec;

  addBtn.hidden   = inRec;
  addBtn.disabled = busy || !ok;

  const saved = ok && st.in_saved;
  savedBtn.classList.toggle('on', saved);
  savedBtn.querySelector('.ic').textContent = saved ? '★' : '☆';
  savedBtn.title    = saved ? 'In Saved Models' : 'Add to Saved Models';
  savedBtn.disabled = busy || !ok || saved;

  const rank = ok ? (st.rank | 0) : 0;
  stars.forEach((s, i) => s.classList.toggle('on', i < rank));
  const rankable = ok && (st.in_recorder || st.in_saved) && !busy;
  starsEl.classList.toggle('off', !rankable);
  starsEl.title = rankable ? 'Set rank (click the current rank to clear)'
                           : 'Add to Recorder or Saved to rank';
}

async function refreshStatus() {
  const j = await api(`/status?${new URLSearchParams({ name, site })}`);
  st = j && j.in_recorder !== undefined ? j : null;   // 401/app down → disabled
  renderCtl();
}

let toastTimer = null;
function toast(text, err) {
  const t = $('toast');
  t.textContent = text;
  t.classList.toggle('err', !!err);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
}

// Run API calls in order, stopping at the first failure; then resync.
async function act(okText, calls) {
  if (busy) return;
  busy = true;
  renderCtl();
  let res = null;
  for (const [path, body] of calls) {
    res = await api(path, body);
    if (!res || !res.ok) break;
  }
  busy = false;
  if (res && res.ok) {
    toast(okText);
    // Resync toolbar + listing badges now instead of on the next poll.
    try { chrome.runtime.sendMessage({ type: 'refresh' }).catch(() => {}); } catch {}
  } else {
    toast((res && res.error) || 'Scr33nX app not reachable', true);
  }
  await refreshStatus();
}

function startRecording() {
  const calls = [];
  if (!st.in_recorder) calls.push(['/add', { name, site, target: 'recorder' }]);
  calls.push(['/record', { name, site, action: 'start' }]);
  act('Recording started', calls);
}

recBtn.addEventListener('click', () => {
  if (!st) return;
  if (st.status === 'recording') {
    act('Recording stopped', [['/record', { name, site, action: 'stop' }]]);
    return;
  }
  const lr = st.linked_recording;
  if (lr) {
    // Same person already recording on another account — ask first.
    const t = $('cfText');
    t.textContent = 'Already recording as ';
    t.appendChild(Object.assign(document.createElement('b'), { textContent: lr.name }));
    t.append(` on ${lr.site}.`);
    $('confirm').hidden = false;
    return;
  }
  startRecording();
});
$('cfYes').addEventListener('click', () => { $('confirm').hidden = true; startRecording(); });
$('cfNo').addEventListener('click', () => { $('confirm').hidden = true; });

autoBtn.addEventListener('click', () => {
  if (!st) return;
  const on = !st.auto;
  act(on ? 'Auto-Record on' : 'Auto-Record off',
      [['/auto', { name, site, enabled: on }]]);
});

addBtn.addEventListener('click', () => {
  act('Added to Recorder', [['/add', { name, site, target: 'recorder' }]]);
});

savedBtn.addEventListener('click', () => {
  act('Added to Saved Models', [['/add', { name, site, target: 'saved' }]]);
});

stars.forEach((s, i) => s.addEventListener('click', () => {
  if (starsEl.classList.contains('off')) return;
  const rank = (st.rank | 0) === i + 1 ? 0 : i + 1;   // current rank → clear
  act(rank ? `Rank set to ${rank}/5` : 'Rank cleared',
      [['/rank', { name, site, rank }]]);
}));

const poll = setInterval(refreshStatus, 2000);

// The iframe is removed when the pointer leaves — stop fetching segments.
window.addEventListener('pagehide', () => {
  clearInterval(poll);
  if (hls) hls.destroy();
});

syncSound();
renderCtl();
refreshStatus();
start();
