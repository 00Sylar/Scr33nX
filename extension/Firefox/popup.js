const API = 'http://localhost:5200';  // v2 (TEST) port

// ── Optional API token ─────────────────────────────────────────────────────────
// Shared secret for the local API (Scr33nX Settings → Local API). Stored in
// extension storage and sent as X-Api-Token on every call. When the app
// replies 401, the popup swaps to a token form; the ⚙ link opens it any time.
let TOKEN = '';
let needToken = false;

function authHeaders(base) {
  const h = Object.assign({}, base || {});
  if (TOKEN) h['X-Api-Token'] = TOKEN;
  return h;
}

function renderTokenForm(info) {
  const root = document.getElementById('root');
  root.innerHTML = `
    ${info ? `<div class="model-name">${info.name}</div>
    <div class="site-label">${info.site}</div>` : ''}
    <div class="feedback err" style="margin-bottom:9px">
      API token ${needToken ? 'required' : ''}<br>(Scr33nX Settings → Local API)</div>
    <input id="token-input" type="password" placeholder="paste the API token — empty = none"
      style="width:100%;padding:6px 8px;margin-bottom:6px;border:1px solid #272729;border-radius:4px;background:#17171a;color:#f2f2f4;font-size:12px;font-family:inherit">
    <button class="btn btn-rec" id="token-save">Save token</button>`;
  const inp = document.getElementById('token-input');
  inp.value = TOKEN;
  document.getElementById('token-save').addEventListener('click', async () => {
    TOKEN = inp.value.trim();
    try { await browser.storage.local.set({ apiToken: TOKEN }); } catch {}
    needToken = false;
    lastSig = null;   // force the next render to redraw from fresh state
    render();
  });
}

// ── Live-update state ──────────────────────────────────────────────────────────
// The popup keeps polling the backend while it is open, so the UI reflects status
// changes (e.g. a Start that takes a few seconds to fetch the URL + spawn ffmpeg)
// without the user having to close and reopen it. We only re-render when the
// meaningful state actually changes, so buttons and feedback don't flicker on
// every poll.
const POLL_MS = 1200;
let pageInfo    = null;   // { name, site } for the active tab (resolved once on open)
let lastSig     = null;   // signature of the last rendered backend state
let resyncTimer = null;   // fallback re-render after a Start/Stop action

function stateSig(appData) {
  return appData
    ? `${appData.in_recorder}|${appData.in_saved}|${appData.status}|${appData.auto}|${appData.rank}|${JSON.stringify(appData.aka || [])}`
    : 'down';
}

function scheduleResync(ms) {
  clearTimeout(resyncTimer);
  resyncTimer = setTimeout(() => render(), ms);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractModelInfo() {
  // Runs inside the page context via scripting.executeScript
  const host = window.location.hostname.replace('www.', '');
  const path = window.location.pathname;
  let site = null;
  if (host.includes('chaturbate.com')) site = 'chaturbate';
  else if (host.includes('stripchat.com')) site = 'stripchat';
  else if (host.includes('camsoda.com'))    site = 'camsoda';
  else if (host.includes('myfreecams.com')) site = 'myfreecams';
  if (!site) return null;
  let name;
  if (site === 'myfreecams') {
    // SPA: the model lives in the hash fragment (#name, #/name, #/model/<id>),
    // with a /models/<name> path fallback.
    let h = window.location.hash.replace(/^#\/?/, '');
    if (h.startsWith('model/')) h = h.slice('model/'.length);
    name = h.split(/[/?]/).filter(Boolean)[0] || '';
    if (!name) name = path.split('/').filter(Boolean).pop() || '';
  } else {
    name = path.split('/').filter(Boolean)[0] || '';
  }
  const skip  = ['tags','search','following','discover','login','register',
                 'promo','affiliates','p','trending','new-cams','female','male'];
  if (!name || skip.includes(name.toLowerCase())) return null;
  return { name: name.toLowerCase(), site };
}

async function getPageModel() {
  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  try {
    const [{ result }] = await browser.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractModelInfo,
    });
    return result;
  } catch {
    return null;
  }
}

async function fetchStatus(name, site) {
  try {
    const r = await fetch(
      `${API}/status?name=${encodeURIComponent(name)}&site=${encodeURIComponent(site)}`,
      { signal: AbortSignal.timeout(1500), headers: authHeaders() }
    );
    if (r.status === 401) { needToken = true; return null; }
    if (!r.ok) return null;
    needToken = false;
    return r.json();
  } catch {
    return null;
  }
}

// Tell the background worker to resync toolbar/listing badges right away
// after an action, instead of waiting for its next poll.
function pingBackground() {
  try { browser.runtime.sendMessage({ type: 'refresh' }).catch(() => {}); } catch {}
}

async function postAdd(name, site, target) {
  const r = await fetch(`${API}/add`, {
    method:  'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body:    JSON.stringify({ name, site, target }),
    signal:  AbortSignal.timeout(3000),
  });
  const j = await r.json();
  if (j.ok) pingBackground();
  return j;
}

async function postRecord(name, site, action) {
  const r = await fetch(`${API}/record`, {
    method:  'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body:    JSON.stringify({ name, site, action }),
    signal:  AbortSignal.timeout(3000),
  });
  const j = await r.json();
  if (j.ok) pingBackground();
  return j;
}

async function postRemove(name, site, target) {
  const r = await fetch(`${API}/remove`, {
    method:  'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body:    JSON.stringify({ name, site, target }),
    signal:  AbortSignal.timeout(3000),
  });
  const j = await r.json();
  if (j.ok) pingBackground();
  return j;
}

async function postAuto(name, site, enabled) {
  const r = await fetch(`${API}/auto`, {
    method:  'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body:    JSON.stringify({ name, site, enabled }),
    signal:  AbortSignal.timeout(3000),
  });
  return r.json();
}

async function postRank(name, site, rank) {
  const r = await fetch(`${API}/rank`, {
    method:  'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body:    JSON.stringify({ name, site, rank }),
    signal:  AbortSignal.timeout(3000),
  });
  return r.json();
}

// ── Status badge ──────────────────────────────────────────────────────────────

function statusBadgeHTML(s) {
  if (!s) return '';
  const labels = {
    recording: '⬤  RECORDING',
    online:    '●  ONLINE',
    offline:   '●  OFFLINE',
    checking:  '◌  CHECKING…',
    error:     '⚠  ERROR',
  };
  return `<div class="status-badge ${s}">${labels[s] || s.toUpperCase()}</div>`;
}

// ── Linked identities (aka) ─────────────────────────────────────────────────────

const SITE_NICE = { chaturbate: 'Chaturbate', stripchat: 'Stripchat',
                    camsoda: 'Camsoda', myfreecams: 'MyFreeCams' };

function akaHTML(aka) {
  if (!aka || !aka.length) return '';
  return `<div class="aka-row">${aka.map((a) =>
    `🔗 <b>${a.name}</b> @ ${SITE_NICE[a.site] || a.site} — ` +
    `<span class="aka-st ${a.status || 'none'}">${a.status || 'not tracked'}</span>`
  ).join('<br>')}</div>`;
}

function linkedBannerHTML(appData) {
  const lr = appData?.linked_recording;
  if (!lr || appData?.status === 'recording') return '';
  return `<div class="banner-warn">⚠ Already recording on ` +
         `${SITE_NICE[lr.site] || lr.site} as <b>${lr.name}</b></div>`;
}

// ── Rank stars ──────────────────────────────────────────────────────────────────

function rankRowHTML(rank, enabled) {
  let stars = '';
  for (let i = 1; i <= 5; i++) {
    const on = i <= rank;
    stars += `<span class="star ${on ? 'on' : ''}" data-r="${i}">${on ? '★' : '☆'}</span>`;
  }
  const cls  = enabled ? '' : ' disabled';
  const hint = enabled ? 'Click a star to rate (click it again to clear)'
                       : 'Add to Saved Models or Recorder to rate';
  return `<div class="rank-row${cls}" id="rank-row" title="${hint}">${stars}</div>`;
}

// ── Render ────────────────────────────────────────────────────────────────────

function recorderButtonHTML(inRec, status, appUp) {
  if (!inRec) {
    return `<button class="btn btn-rec" id="btn-rec" ${!appUp ? 'disabled' : ''}>
      ＋  Add to Recorder
    </button>`;
  }
  // Already in Recorder — render Start or Stop based on status
  if (status === 'recording') {
    return `<button class="btn btn-stop" id="btn-stop">⏹  Stop Recording</button>`;
  }
  if (status === 'checking') {
    return `<button class="btn btn-ghost" id="btn-checking" disabled>
      ◌  Checking…
    </button>`;
  }
  // online / offline / error / null → allow manual start
  return `<button class="btn btn-start" id="btn-start">⏺  Start Recording</button>`;
}

async function render(appData) {
  const root = document.getElementById('root');

  const info = pageInfo;
  if (!info) {
    root.innerHTML = `<div class="msg">
      <span class="icon">🔍</span>
      No model detected<br>on this page.
    </div>`;
    return;
  }

  // Called from a poll with fresh data, or standalone (undefined) → fetch it.
  if (appData === undefined) appData = await fetchStatus(info.name, info.site);
  lastSig = stateSig(appData);

  // The app answered 401 — a token is set in Scr33nX but this extension
  // doesn't have it (or has the wrong one). Show the token form.
  if (needToken) { renderTokenForm(info); return; }

  const appUp     = appData !== null;
  const inRec     = appData?.in_recorder ?? false;
  const inSaved   = appData?.in_saved    ?? false;
  const curStatus = appData?.status      ?? null;
  const curRank   = appData?.rank        ?? 0;

  const siteLabel = info.site.charAt(0).toUpperCase() + info.site.slice(1);

  // "Remove from Recorder" is only offered when in the list AND idle
  // (not recording, not mid-check). Protects against removing an active session.
  const canRemoveRec = inRec && appUp
    && curStatus !== 'recording'
    && curStatus !== 'checking';

  root.innerHTML = `
    <div class="model-name">${info.name}</div>
    <div class="site-label">${siteLabel}</div>
    ${statusBadgeHTML(curStatus)}
    ${linkedBannerHTML(appData)}
    ${akaHTML(appData?.aka)}
    ${appUp ? rankRowHTML((inSaved || inRec) ? curRank : 0, inSaved || inRec) : ''}
    ${recorderButtonHTML(inRec, curStatus, appUp)}
    ${inRec ? `<label class="auto-row">
        <input type="checkbox" id="chk-auto" ${appData?.auto ? 'checked' : ''}>
        <span>Auto-Record when online</span>
      </label>` : ''}
    <button class="btn btn-saved"
      id="btn-saved"
      ${inSaved || !appUp ? 'disabled' : ''}>
      ${inSaved ? '⭐  In Saved Models' : '⭐  Add to Saved Models'}
    </button>
    ${canRemoveRec
      ? '<button class="btn btn-remove" id="btn-remove-rec">✕  Remove from Recorder</button>'
      : ''}
    ${!appUp ? '<div class="feedback err">Scr33nX is not running</div>' : ''}
    <div id="fb"></div>
    <div id="token-link" style="margin-top:9px;text-align:center;font-size:10px;color:#3a3a3e;cursor:pointer">⚙ API token…</div>
  `;

  // Wired before the app-down early-return: the token link must work even
  // when the API is unreachable (a wrong token looks "down" to a user).
  document.getElementById('token-link')
    .addEventListener('click', () => renderTokenForm(info));

  if (!appUp) return;

  // ── Rank stars ──────────────────────────────────────────────────────────────
  // Only wired when the model is on a list (Saved or Recorder); a disabled
  // row is display-only so you can't create a rank with no home.
  const rankRow = document.getElementById('rank-row');
  if (rankRow && !rankRow.classList.contains('disabled')) {
    rankRow.querySelectorAll('.star').forEach((st) => {
      st.addEventListener('click', async () => {
        const fb   = document.getElementById('fb');
        const star = parseInt(st.dataset.r, 10);
        const cur  = appData?.rank ?? 0;
        const next = (cur === star) ? 0 : star;   // click current rank to clear
        // Optimistic paint so the stars respond instantly.
        rankRow.querySelectorAll('.star').forEach((s2) => {
          const on = parseInt(s2.dataset.r, 10) <= next;
          s2.classList.toggle('on', on);
          s2.textContent = on ? '★' : '☆';
        });
        try {
          const res = await postRank(info.name, info.site, next);
          if (res.ok) {
            fb.className = 'feedback ok';
            fb.textContent = next ? `Rated ${next}★` : 'Rank cleared';
            // Keep local state in sync so the poll doesn't redraw and wipe it.
            if (appData) appData.rank = next;
            lastSig = stateSig(appData);
          } else {
            fb.className = 'feedback err';
            fb.textContent = res.error || 'Failed';
            setTimeout(() => render(), 200);
          }
        } catch {
          fb.className = 'feedback err';
          fb.textContent = 'Could not reach StreamRecorder';
          setTimeout(() => render(), 200);
        }
      });
    });
  }

  // ── Add to Recorder ─────────────────────────────────────────────────────────
  const btnRec = document.getElementById('btn-rec');
  if (btnRec) {
    btnRec.addEventListener('click', async () => {
      const fb  = document.getElementById('fb');
      btnRec.disabled = true;
      btnRec.textContent = '…';
      try {
        const res = await postAdd(info.name, info.site, 'recorder');
        if (res.ok) {
          // Re-render so the button flips to Start/Stop based on live status
          setTimeout(render, 250);
        } else {
          btnRec.disabled = false;
          btnRec.textContent = '＋  Add to Recorder';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnRec.disabled = false;
        btnRec.textContent = '＋  Add to Recorder';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Start Recording ────────────────────────────────────────────────────────
  // When a linked account of this person is already being recorded on another
  // site, the first click swaps the button for an inline Yes/Cancel confirm
  // (window.confirm is unreliable inside extension panels).
  const btnStart = document.getElementById('btn-start');
  if (btnStart) {
    const doStart = async (btn) => {
      const fb = document.getElementById('fb');
      btn.disabled = true;
      btn.textContent = '…  Starting';
      try {
        const res = await postRecord(info.name, info.site, 'start');
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = 'Starting… (updates automatically)';
          // The button stays in this "…  Starting" state; the live poll flips it
          // to Stop as soon as the backend reports RECORDING. A fallback resync
          // recovers the UI if the status never changes (e.g. the model is offline).
          scheduleResync(10000);
        } else {
          btn.disabled = false;
          btn.textContent = '⏺  Start Recording';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btn.disabled = false;
        btn.textContent = '⏺  Start Recording';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    };
    btnStart.addEventListener('click', () => {
      const lr = appData?.linked_recording;
      if (!lr) { doStart(btnStart); return; }
      const wrap = document.createElement('div');
      wrap.className = 'confirm-rec';
      wrap.innerHTML =
        `<div class="confirm-txt">⚠ Already recording on ` +
        `${SITE_NICE[lr.site] || lr.site} as <b>${lr.name}</b>. Record anyway?</div>` +
        `<button class="btn btn-start" id="btn-start-yes">✔  Yes, record</button>` +
        `<button class="btn btn-cancel" id="btn-start-no">✕  Cancel</button>`;
      btnStart.replaceWith(wrap);
      document.getElementById('btn-start-yes')
        .addEventListener('click', () => doStart(document.getElementById('btn-start-yes')));
      document.getElementById('btn-start-no')
        .addEventListener('click', () => render());
    });
  }

  // ── Stop Recording ─────────────────────────────────────────────────────────
  const btnStop = document.getElementById('btn-stop');
  if (btnStop) {
    btnStop.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnStop.disabled = true;
      btnStop.textContent = '…  Stopping';
      try {
        const res = await postRecord(info.name, info.site, 'stop');
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = 'Stopping… (updates automatically)';
          scheduleResync(6000);
        } else {
          btnStop.disabled = false;
          btnStop.textContent = '⏹  Stop Recording';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnStop.disabled = false;
        btnStop.textContent = '⏹  Stop Recording';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Remove from Recorder (only when in list and not recording) ─────────────
  const btnRemoveRec = document.getElementById('btn-remove-rec');
  if (btnRemoveRec) {
    btnRemoveRec.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnRemoveRec.disabled = true;
      btnRemoveRec.textContent = '…  Removing';
      try {
        const res = await postRemove(info.name, info.site, 'recorder');
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = 'Removed from Recorder';
          setTimeout(render, 400);
        } else {
          btnRemoveRec.disabled = false;
          btnRemoveRec.textContent = '✕  Remove from Recorder';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnRemoveRec.disabled = false;
        btnRemoveRec.textContent = '✕  Remove from Recorder';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }

  // ── Auto-Record toggle ─────────────────────────────────────────────────────
  const chkAuto = document.getElementById('chk-auto');
  if (chkAuto) {
    chkAuto.addEventListener('change', async () => {
      const fb = document.getElementById('fb');
      const want = chkAuto.checked;
      chkAuto.disabled = true;
      try {
        const res = await postAuto(info.name, info.site, want);
        if (res.ok) {
          fb.className = 'feedback ok';
          fb.textContent = want ? 'Auto-record enabled' : 'Auto-record disabled';
        } else {
          chkAuto.checked = !want;
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        chkAuto.checked = !want;
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
      chkAuto.disabled = false;
    });
  }

  // ── Add to Saved Models ────────────────────────────────────────────────────
  const btnSaved = document.getElementById('btn-saved');
  if (btnSaved && !inSaved) {
    btnSaved.addEventListener('click', async () => {
      const fb = document.getElementById('fb');
      btnSaved.disabled = true;
      btnSaved.textContent = '…';
      try {
        const res = await postAdd(info.name, info.site, 'saved');
        if (res.ok) {
          btnSaved.textContent = '⭐  In Saved Models';
          fb.className = 'feedback ok';
          fb.textContent = 'Added successfully';
          // Re-render so the rank stars unlock immediately (they're disabled
          // until inSaved/inRec is true) instead of waiting on the next poll.
          setTimeout(render, 250);
        } else {
          btnSaved.disabled = false;
          btnSaved.textContent = '⭐  Add to Saved Models';
          fb.className = 'feedback err';
          fb.textContent = res.error || 'Failed';
        }
      } catch {
        btnSaved.disabled = false;
        btnSaved.textContent = '⭐  Add to Saved Models';
        fb.className = 'feedback err';
        fb.textContent = 'Could not reach StreamRecorder';
      }
    });
  }
}

// ── Live polling ────────────────────────────────────────────────────────────
async function poll() {
  if (!pageInfo) return;   // no model on this page → nothing to track
  const appData = await fetchStatus(pageInfo.name, pageInfo.site);
  // Only re-render when the meaningful state changed — avoids flicker and
  // preserves transient button states ("…  Starting") between polls.
  if (stateSig(appData) !== lastSig) render(appData);
}

// ── Add-on options (highlight filter + mini player) ──────────────────────────
// Stored in extension storage; content scripts pick changes up live.
// Keep defaults in sync with content.js OPT_DEFAULTS.
const OPT_DEFAULTS = { highlight: [], mpEnabled: true, mpWidth: 360,
                       mpDelay: 1000, mpMuted: true, mpQuality: 480,
                       mpAny: false };

async function initOptions() {
  let st = {};
  try { st = await browser.storage.local.get(Object.keys(OPT_DEFAULTS)); } catch {}
  const o = Object.assign({}, OPT_DEFAULTS, st);
  const save = (k, v) => {
    try { browser.storage.local.set({ [k]: v }); } catch {}
  };
  const hl = [...document.querySelectorAll('#hl input')];
  // Chip look via a class, not :has() — Firefox only has :has() from 121.
  const saveHl = () => {
    hl.forEach((c) => c.parentElement.classList.toggle('on', c.checked));
    save('highlight', hl.filter((c) => c.checked).map((c) => c.value));
  };
  for (const cb of hl) {
    cb.checked = (o.highlight || []).includes(cb.value);
    cb.parentElement.classList.toggle('on', cb.checked);
    cb.addEventListener('change', saveHl);
  }
  document.getElementById('hlClear').addEventListener('click', () => {
    hl.forEach((c) => { c.checked = false; });
    saveHl();
  });
  for (const id of ['mpEnabled', 'mpAny', 'mpMuted']) {
    const el = document.getElementById(id);
    el.checked = !!o[id];
    el.addEventListener('change', () => save(id, el.checked));
  }
  for (const id of ['mpWidth', 'mpDelay', 'mpQuality']) {
    const el = document.getElementById(id);
    el.value = String(o[id]);
    el.addEventListener('change', () => save(id, Number(el.value)));
  }
}

async function init() {
  initOptions();
  try {
    const st = await browser.storage.local.get('apiToken');
    TOKEN = (st && st.apiToken) || '';
  } catch { TOKEN = ''; }
  pageInfo = await getPageModel();   // resolved once; the popup is tied to one tab
  await render();
  // Keep the popup in sync with the backend for as long as it stays open.
  setInterval(poll, POLL_MS);
}

init();
