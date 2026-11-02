// Scr33nX hover mini player — runs inside the iframe content.js places next
// to a thumbnail. Asks the app for the model's relay URL (GET /preview, the
// same stream a Player tile uses) and plays it with hls.js.
const API = 'http://localhost:5200';  // v2 (TEST) port

const params  = new URLSearchParams(location.search);
const name    = params.get('name') || '';
const site    = params.get('site') || '';
const quality = parseInt(params.get('q') || '0', 10) || 0;
const any     = params.get('any') === '1';   // "Preview any model": resolved fresh

const video = document.getElementById('v');
const msg   = document.getElementById('msg');
const snd   = document.getElementById('snd');
document.getElementById('name').textContent = name;
video.muted = params.get('muted') !== '0';

let hls = null;

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

document.body.addEventListener('click', () => {
  video.muted = !video.muted;
  syncSound();
  if (video.paused) play();
});

async function start() {
  let token = '';
  try {
    const st = await chrome.storage.local.get('apiToken');
    token = (st && st.apiToken) || '';
  } catch {}
  let res = null;
  try {
    const qs = new URLSearchParams({ name, site, quality: String(quality),
                                     any: any ? '1' : '0' });
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

// The iframe is removed when the pointer leaves — stop fetching segments.
window.addEventListener('pagehide', () => { if (hls) hls.destroy(); });

syncSound();
start();
