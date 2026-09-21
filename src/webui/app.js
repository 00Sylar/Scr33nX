/* Scr33nX web UI — full client logic.
   Pull model: poll the Python bridge once a second and diff-render.
   window.pywebview.api → Bridge methods in app_web.py. */

"use strict";

/* ══ helpers ══ */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SITE_LABEL = { chaturbate: "CHATURBATE", stripchat: "STRIPCHAT",
                     camsoda: "CAMSODA", myfreecams: "MYFREECAMS" };
const SITE_SHORT = { chaturbate: "CB", stripchat: "SC",
                     camsoda: "CS", myfreecams: "MFC" };
const STATUS_TXT = { recording: "RECORDING", online: "ONLINE", offline: "OFFLINE",
                     private: "PRIVATE / TICKET", checking: "CHECKING…", error: "ERROR" };
const STATUS_W = { recording: 0, online: 1, private: 2, checking: 3, error: 4, offline: 5 };
const ROW_H = 40;

function parseSize(s) {
  if (!s) return 0;
  const n = parseFloat(s) || 0;
  return s.includes("GB") ? n * 1024 : n;
}
function starsHtml(rank) {
  let h = "";
  for (let i = 1; i <= 5; i++)
    h += `<i data-i="${i}" class="${i <= rank ? "f" : ""}">${i <= rank ? "★" : "☆"}</i>`;
  return h;
}
/* "site:name" → "name · SITE" (links dialog / tooltips) */
function lnkLabel(k) {
  const i = k.indexOf(":");
  return `${k.slice(i + 1)} · ${SITE_LABEL[k.slice(0, i)] || k.slice(0, i)}`;
}
/* Live status of a "site:name" key across the Recorder and the Saved
   scanner (saved_status only ships non-offline models, so a recording
   alias is always resolvable). */
function liveStatusOfKey(key) {
  const m = (S && S.models || []).find(x => x.key === key);
  if (m) return m.status;
  const sv = S && S.saved_status && S.saved_status[`saved:${key}`];
  return sv ? sv[0] : "offline";
}
/* aka list for any tracked model — Recorder row or Saved item. */
function akaOf(name, site) {
  const lower = String(name).toLowerCase();
  const m = (S && S.models || []).find(x => x.key === `${site}:${lower}`);
  if (m) return m.aka || [];
  const it = savedCache.items.find(x => x.site === site && x.name.toLowerCase() === lower);
  return (it && it.aka) || [];
}
/* Cross-site state of a model's aliases: which are RECORDING right now,
   and which also sit in the Recorder list (same person twice). */
function linkedState(aka) {
  const out = { rec: [], dup: [] };
  for (const k of aka || []) {
    if (liveStatusOfKey(k) === "recording") out.rec.push(k);
    if (S && (S.models || []).some(x => x.key === k)) out.dup.push(k);
  }
  return out;
}
/* 🔗 marker for a row/tile whose model has linked aliases. Escalates:
   plain 🔗 → amber ⧉ (same person also in the Recorder on another site)
   → red REC chip (a linked alias is recording NOW; ×2 when this row is
   recording too — a true double capture). */
function lnkHtml(aka, selfStatus) {
  if (!aka || !aka.length) return "";
  const ls = linkedState(aka);
  if (ls.rec.length) {
    const both = selfStatus === "recording";
    const sites = ls.rec.map(k => SITE_SHORT[k.slice(0, k.indexOf(":"))] || "?").join("+");
    const tip = (both ? "DOUBLE RECORDING — also recording as " : "Already recording as ")
      + ls.rec.map(lnkLabel).join(", ");
    return `<i class="lnk lnk-rec${both ? " lnk-both" : ""}" title="${esc(tip)}">🔗<b>${both ? "REC ×2" : "REC·" + sites}</b></i>`;
  }
  if (ls.dup.length)
    return `<i class="lnk lnk-dup" title="${esc("Also in Recorder: " + ls.dup.map(lnkLabel).join(", "))}">🔗<b>⧉</b></i>`;
  return `<i class="lnk" title="Linked: ${esc(aka.map(lnkLabel).join(", "))}">🔗</i>`;
}
/* Cross-site heads-up toast when ADDING models a linked alias of which is
   already recording or already listed. Warns only — never blocks the add.
   Keys may carry a "saved:" prefix. */
function warnCrossSiteKeys(keys) {
  const msgs = [];
  for (let k of keys || []) {
    k = String(k).replace(/^saved:/, "");
    const i = k.indexOf(":");
    const site = k.slice(0, i), name = k.slice(i + 1);
    const ls = linkedState(akaOf(name, site));
    if (ls.rec.length)
      msgs.push(`${name} is already recording as ${ls.rec.map(lnkLabel).join(", ")}`);
    else if (ls.dup.length)
      msgs.push(`${name} is already in the Recorder as ${ls.dup.map(lnkLabel).join(", ")}`);
  }
  if (msgs.length)
    toast(`⚠ ${msgs.slice(0, 3).join(" • ")}${msgs.length > 3 ? ` (+${msgs.length - 3} more)` : ""}.`,
          "warn");
}
/* Confirm gate before STARTING a recording when a linked alias of any of
   the models is already being recorded: "Record anyway" runs proceed(),
   Cancel drops the whole batch. No conflict → proceed() immediately. */
function confirmCrossSiteRec(keys, proceed) {
  const msgs = [];
  for (let k of keys || []) {
    k = String(k).replace(/^saved:/, "");
    const i = k.indexOf(":");
    const site = k.slice(0, i), name = k.slice(i + 1);
    const recs = linkedState(akaOf(name, site)).rec;
    if (recs.length)
      msgs.push(`${name} (${SITE_SHORT[site] || site}) is already recording as ${recs.map(lnkLabel).join(", ")}`);
  }
  if (!msgs.length) { proceed(); return; }
  modal("⚠ Already recording", msgs.join("\n")
        + "\n\nThis person is already being recorded on another site. Record anyway?",
        "Record anyway", proceed);
}

/* ══ global state ══ */
let API = null;
let S = null;                       // last snapshot
let logSeq = 0, pipeSeq = 0;
let fetching = false;
let qualityOpts = [];               // [{label, height}]
const sel = { rec: new Set(), saved: new Set() };      // click-selection
const checked = { rec: new Set(), saved: new Set() };  // checkbox working set
const collapsed = { rec: new Set(), saved: new Set() };
const anchor = { rec: null, saved: null };
const sort = { rec: { col: "name", dir: 1 }, saved: { col: "name", dir: 1 } };
const filter = { rec: "", saved: "" };
const statusFilter = { rec: new Set(), saved: new Set() };   // empty = all
const rankFilter = { rec: 0, saved: 0 };   // 0 = all, 1-5 = "rank ≥ N", -1 = unranked only
// Hide a row if a linked alias (see linkedState) is recording on another
// site right now — independent of the row's own status, so "Online" +
// this still excludes a model who's ONLINE here but REC·CB elsewhere.
const xsiteFilter = { rec: false, saved: false };

function rankMatch(rank, rf) {
  return !rf || (rf === -1 ? !rank : rank >= rf);
}
let savedCache = { version: -1, items: [] };
let savedDisplay = [];              // flattened (headers + rows) for virtualization
let recVisible = [];                // ordered visible keys (for shift ranges)
const rowEls = new Map();
let settingsLoaded = false;
let suppressClick = false;
let playerMaxTiles = 9;             // cached from settings; see Player tab section

/* ══ boot ══ */
function start() {
  if (API) return;
  if (!(window.pywebview && window.pywebview.api)) return;
  API = window.pywebview.api;
  API.quality_options().then(r => { qualityOpts = r.options || []; });
  tick();
  setInterval(tick, 1000);
}
window.addEventListener("pywebviewready", start);
document.addEventListener("DOMContentLoaded", () => setTimeout(start, 400));

async function tick() {
  if (!API || fetching) return;
  fetching = true;
  try {
    const s = await API.state(logSeq, pipeSeq);
    S = s;
    render(s);
  } catch (e) { console.error("[tick] state()/render() failed:", e); }
  fetching = false;
}

/* ══ top-level render ══ */
function render(s) {
  $("ver").textContent = "v" + s.version;
  const up = $("update-pill");
  if (s.update) { up.hidden = false; up.textContent = `● Update available (${s.update})`; }
  $("lowdisk-pill").hidden = !s.low_disk;
  $("m-down").textContent = s.meters.down.toFixed(1);
  $("m-up").textContent = s.meters.up.toFixed(1);

  const r = s.monitoring.recorder, v = s.monitoring.saved;
  $("hdr-status").textContent = r && v ? "MONITORING (R+S)" : r ? "RECORDER ACTIVE"
                              : v ? "SCANNER ACTIVE" : "IDLE";
  $("hdr-dot").style.background = (r || v) ? "var(--go-bright)" : "var(--text-3)";

  setToggleBtn($("btn-monitor"), r, "▶ Start Monitor", "⏹ Stop Monitor");
  setToggleBtn($("btn-scanner"), v, "▶ Start Scanner", "⏹ Stop Scanner");

  renderDash(s.dash);
  renderRec(s.models);
  renderSaved();
  patchPlayerStatuses();
  patchPreviewControls();
  renderLog(s.log, s.log_seq);
  renderPipe(s.pipe);
  $("saved-count").textContent = `${s.saved_count.toLocaleString()} model(s)`;
  privacyApply(s.privacy);
}

function setToggleBtn(btn, on, offText, onText) {
  btn.textContent = on ? onText : offText;
  btn.classList.toggle("go", !on);
  btn.classList.toggle("stop", on);
}

function renderDash(d) {
  $("dash-live").textContent = `${d.all.recording} LIVE`;
  $("dash-total").textContent = `${d.all.total} models`;
  let html = "";
  for (const [chip, c] of Object.entries(d.sites)) {
    if (!c.total) continue;
    const rw = Math.max(c.recording / c.total * 100, c.recording ? 6 : 0);
    const ow = Math.max(c.online / c.total * 100, c.online ? 6 : 0);
    html += `
      <div class="dash-row"><span class="chip">${chip}</span>
        <span class="c ${c.recording ? "rec" : "dim"}">${c.recording}</span>
        <span class="c ${c.online ? "on" : "dim"}">${c.online}</span>
        <span class="c off">${c.offline}</span></div>
      <div class="bar"><i class="rec" style="width:${rw}%"></i><i class="on" style="width:${ow}%"></i></div>`;
  }
  html += `
    <div class="dash-row all"><span class="chip">ALL</span>
      <span class="c ${d.all.recording ? "rec" : "dim"}">${d.all.recording}</span>
      <span class="c ${d.all.online ? "on" : "dim"}">${d.all.online}</span>
      <span class="c off">${d.all.offline}</span></div>`;
  $("dash-rows").innerHTML = html;
}

/* ══ recorder table ══ */
function cmpModels(a, b, col, dir) {
  let x, y;
  switch (col) {
    case "rank":   x = a.rank; y = b.rank; break;
    case "status": x = STATUS_W[a.status]; y = STATUS_W[b.status]; break;
    case "size":   x = parseSize(a.size); y = parseSize(b.size); break;
    case "auto":   x = a.auto ? 0 : 1; y = b.auto ? 0 : 1; break;
    case "saved":  x = a.saved ? 0 : 1; y = b.saved ? 0 : 1; break;
    default:       x = a.name; y = b.name;
  }
  if (x < y) return -dir;
  if (x > y) return dir;
  return a.name < b.name ? -1 : 1;
}

function computeRecView(models) {
  const f = filter.rec.toLowerCase();
  const sf = statusFilter.rec;
  const rf = rankFilter.rec;
  const list = models.filter(m =>
    (!f || m.name.includes(f)) && (sf.size === 0 || sf.has(m.status))
    && rankMatch(m.rank, rf));
  const bySite = new Map();
  for (const m of list) {
    if (!bySite.has(m.site)) bySite.set(m.site, []);
    bySite.get(m.site).push(m);
  }
  const { col, dir } = sort.rec;
  for (const arr of bySite.values()) arr.sort((a, b) => cmpModels(a, b, col, dir));
  return bySite;
}

function recRowHtml(m) {
  return `
    <span class="cb" data-cb="${esc(m.key)}"></span>
    <span class="name">${esc(m.name)}${lnkHtml(m.aka, m.status)}</span>
    <span class="stars" data-stars="${esc(m.key)}" data-rank="${m.rank}">${starsHtml(m.rank)}</span>
    <span class="st ${m.status}"><span class="dot"></span><span class="st-txt">${STATUS_TXT[m.status]}</span></span>
    <span class="file">${esc(m.file || "—")}</span>
    <span class="size">${esc(m.size || "—")}</span>
    <span class="switch ${m.auto ? "on" : ""}" data-auto="${esc(m.key)}" title="Auto-record"></span>
    <span class="saved-check ${m.saved ? "" : "no"}">${m.saved ? "✓" : "—"}</span>`;
}

function renderRec(models) {
  const bySite = computeRecView(models);
  recVisible = [];
  for (const [site, list] of bySite)
    if (!collapsed.rec.has(site)) recVisible.push(...list.map(m => m.key));

  updateSortArrows("rec-head", sort.rec);
  const container = $("rows");
  const sig = [...bySite.entries()].map(([s, l]) =>
    s + (collapsed.rec.has(s) ? "!" : ":") + l.map(m => m.key).join(",")).join("|");

  // Invariant: with no active filter, a non-empty models[] must produce a
  // non-empty view. If it doesn't, something upstream is inconsistent —
  // warn (so a recurrence leaves a lead) and force the next tick to do a
  // full rebuild instead of trusting a cached sig that may itself be stale.
  if (models.length > 0 && bySite.size === 0 && !filter.rec
      && statusFilter.rec.size === 0 && rankFilter.rec === 0) {
    console.warn("[renderRec] empty view despite non-empty models[] and no active filter — forcing resync.");
    container.dataset.sig = "__forced-resync__";
  } else if (container.dataset.sig !== sig) {
    container.dataset.sig = sig;
    rowEls.clear();
    let html = "";
    for (const [site, list] of bySite) {
      const closed = collapsed.rec.has(site);
      html += `<div class="g-site ${closed ? "closed" : ""}">
        <button class="tog" data-tog="rec:${esc(site)}"></button>
        ${SITE_LABEL[site] || site.toUpperCase()}<small>${list.length} models</small></div>
        <div class="site-rows ${closed ? "collapsed" : ""}">`;
      for (const m of list)
        html += `<div class="row gr ${m.status === "recording" ? "recording" : ""}"
                      data-key="${esc(m.key)}" data-tab="rec">${recRowHtml(m)}</div>`;
      html += `</div>`;
    }
    container.innerHTML = html;
    container.querySelectorAll(".row").forEach(el => rowEls.set(el.dataset.key, el));
  }
  const byKey = new Map(models.map(m => [m.key, m]));
  for (const [key, el] of rowEls) {
    const m = byKey.get(key);
    if (!m) continue;
    el.classList.toggle("recording", m.status === "recording");
    el.classList.toggle("selected", sel.rec.has(key));
    const st = el.querySelector(".st");
    st.className = `st ${m.status}`;
    st.querySelector(".st-txt").textContent = STATUS_TXT[m.status];
    setText(el, ".file", m.file || "—");
    setText(el, ".size", m.size || "—");
    el.querySelector(".switch").classList.toggle("on", m.auto);
    const stars = el.querySelector(".stars");
    if (stars.dataset.rank !== String(m.rank)) {
      stars.dataset.rank = m.rank;
      stars.innerHTML = starsHtml(m.rank);
    }
    // 🔗 marker follows link/status changes without waiting for a rebuild
    const nameEl = el.querySelector(".name");
    const wantLnk = lnkHtml(m.aka, m.status);
    if (nameEl.dataset.lnk !== wantLnk) {
      nameEl.dataset.lnk = wantLnk;
      nameEl.innerHTML = `${esc(m.name)}${wantLnk}`;
    }
    const sc = el.querySelector(".saved-check");
    sc.classList.toggle("no", !m.saved);
    sc.textContent = m.saved ? "✓" : "—";
    el.querySelector(".cb").classList.toggle("on", checked.rec.has(key));
  }
  updateSelLabel();
}

function setText(root, selector, value) {
  const el = root.querySelector(selector);
  if (el && el.textContent !== value) el.textContent = value;
}

function updateSelLabel() {
  const n = checked.rec.size;
  $("rec-sel").textContent = n ? `✓ ${n} checked`
    : (sel.rec.size ? `${sel.rec.size} selected` : "0 selected");
}

function updateSortArrows(headId, st) {
  document.querySelectorAll(`#${headId} .sortable`).forEach(el => {
    const on = el.dataset.col === st.col;
    el.classList.toggle("on", on);
    el.querySelector("i").textContent = on ? (st.dir > 0 ? "↑" : "↓") : "↕";
  });
}

/* ══ saved table (virtualized) ══ */
async function ensureSavedList() {
  if (!API) return;
  if (S && savedCache.version === S.saved_version) return;
  const r = await API.saved_list();
  savedCache = r;
  rebuildSavedDisplay();
}

function rebuildSavedDisplay() {
  const f = filter.saved.toLowerCase();
  const sf = statusFilter.saved;
  const stMap = (S && S.saved_status) || {};
  const items = [];
  const rf = rankFilter.saved;
  const hideXRec = xsiteFilter.saved;
  for (const it of savedCache.items) {
    const stat = stMap[it.sid];
    const status = stat ? stat[0] : "offline";
    if (f && !it.name.toLowerCase().includes(f)) continue;
    if (sf.size && !sf.has(status)) continue;
    if (!rankMatch(it.rank, rf)) continue;
    if (hideXRec && it.aka && it.aka.length && linkedState(it.aka).rec.length) continue;
    items.push({ ...it, status, file: stat ? stat[1] : "", size: stat ? stat[2] : "" });
  }
  const bySite = new Map();
  for (const it of items) {
    if (!bySite.has(it.site)) bySite.set(it.site, []);
    bySite.get(it.site).push(it);
  }
  const { col, dir } = sort.saved;
  savedDisplay = [];
  for (const [site, list] of bySite) {
    list.sort((a, b) => cmpModels(a, b, col, dir));
    savedDisplay.push({ head: site, count: list.length });
    if (!collapsed.saved.has(site)) savedDisplay.push(...list);
  }
  $("saved-viewport").style.height = (savedDisplay.length * ROW_H) + "px";
  renderSavedWindow();
}

function renderSaved() {
  ensureSavedList();
  rebuildSavedDisplay();      // statuses may have changed this tick
}

function savedRowHtml(it, top) {
  return `<div class="row gr gr-s ${it.status === "recording" ? "recording" : ""}
      ${sel.saved.has(it.sid) ? "selected" : ""}" style="top:${top}px"
      data-key="${esc(it.sid)}" data-tab="saved">
    <span class="cb ${checked.saved.has(it.sid) ? "on" : ""}" data-cb="${esc(it.sid)}"></span>
    <span class="name">${esc(it.name)}${lnkHtml(it.aka, it.status)}</span>
    <span class="stars" data-stars="${esc(it.sid)}" data-rank="${it.rank}">${starsHtml(it.rank)}</span>
    <span class="st ${it.status}"><span class="dot"></span><span class="st-txt">${STATUS_TXT[it.status]}</span></span>
    <span class="file">${esc(it.file || "—")}</span>
    <span class="size">${esc(it.size || "—")}</span>
  </div>`;
}

function renderSavedWindow() {
  updateSortArrows("saved-head", sort.saved);
  const wrap = $("saved-wrap");
  const vp = $("saved-viewport");
  const first = Math.max(0, Math.floor(wrap.scrollTop / ROW_H) - 4);
  const last = Math.min(savedDisplay.length,
                        first + Math.ceil(wrap.clientHeight / ROW_H) + 8);
  let html = "";
  for (let i = first; i < last; i++) {
    const it = savedDisplay[i];
    const top = i * ROW_H;
    if (it.head !== undefined) {
      const closed = collapsed.saved.has(it.head);
      html += `<div class="g-site ${closed ? "closed" : ""}" style="top:${top}px">
        <button class="tog" data-tog="saved:${esc(it.head)}"></button>
        ${SITE_LABEL[it.head] || it.head.toUpperCase()}<small>${it.count} models</small></div>`;
    } else {
      html += savedRowHtml(it, top);
    }
  }
  vp.innerHTML = html;
}
$("saved-wrap").addEventListener("scroll", () => requestAnimationFrame(renderSavedWindow));

/* ══ log panes ══ */
function renderLog(entries, seq) {
  appendLog($("logpane"), entries);
  logSeq = seq;
}
function appendLog(pane, entries) {
  if (!entries || !entries.length) return;
  const stick = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 24;
  for (const e of entries) {
    const div = document.createElement("div");
    div.innerHTML = `<span class="t">[${esc(e.t)}]</span> <span class="k-${esc(e.k)}">${esc(e.m)}</span>`;
    pane.appendChild(div);
  }
  while (pane.childElementCount > 800) pane.firstElementChild.remove();
  if (stick) pane.scrollTop = pane.scrollHeight;
}

/* ══ pipeline ══ */
function renderPipe(p) {
  const btn = $("b-pipe");
  btn.textContent = p.running ? "⏹ Stop Pipeline" : "▶ Start Pipeline";
  btn.classList.toggle("go", !p.running);
  btn.classList.toggle("stop", p.running);
  const el = $("pipe-state");
  let cls = "offline", txt = "STOPPED";
  if (p.state === "starting") { cls = "checking"; txt = "STARTING"; }
  else if (p.state === "stopping") { cls = "private"; txt = "STOPPING"; }
  else if (p.state === "error") { cls = "error"; txt = "ERROR"; }
  else if (p.running) {
    if (p.convert && p.upload) { cls = "online"; txt = "CONVERTING & UPLOADING"; }
    else if (p.convert) { cls = "online"; txt = "CONVERTING"; }
    else if (p.upload) { cls = "online"; txt = "UPLOADING"; }
    else { cls = "checking"; txt = "STAND BY"; }
  }
  el.className = `st ${cls}`;
  el.innerHTML = `<span class="dot"></span>${txt}`;
  $("stage-convert").classList.toggle("on", p.convert);
  $("stage-upload").classList.toggle("on", p.upload);
  $("pipe-lines").textContent = p.lines.join("\n");
  appendLog($("pipe-log"), p.log);
  pipeSeq = p.log_seq;
}

let pipeLoaded = false;
async function loadPipeFields() {
  if (pipeLoaded || !API) return;
  pipeLoaded = true;
  const p = await API.pipeline_get();
  $("p-api-id").value = p.api_id || "";
  $("p-api-hash").value = p.api_hash || "";
  $("p-group").value = p.group_id || "";
  $("p-topic").value = p.topic_id || "0";
  $("p-conv").value = p.converted_dir || "";
  $("p-sess").value = p.session_dir || "";
}

$("b-pipe").addEventListener("click", () => API.pipeline_toggle());
$("stage-convert").addEventListener("click", () =>
  API.pipeline_stage(!$("stage-convert").classList.contains("on"), null));
$("stage-upload").addEventListener("click", () =>
  API.pipeline_stage(null, !$("stage-upload").classList.contains("on")));
$("b-pipesave").addEventListener("click", async () => {
  await API.pipeline_save({
    api_id: $("p-api-id").value, api_hash: $("p-api-hash").value,
    group_id: $("p-group").value, topic_id: $("p-topic").value,
    converted_dir: $("p-conv").value, session_dir: $("p-sess").value,
  });
  toast("Pipeline settings saved.");
});
$("b-reauth").addEventListener("click", async () => {
  const r = await API.pipeline_reauth(false);
  if (r.confirm) {
    modal("Re-auth", `Delete cached Telegram session in:\n${r.dir}\n\nYou'll need to log in again on next Start.`,
          "Delete session", async () => {
      const r2 = await API.pipeline_reauth(true);
      toast(r2.ok ? r2.msg : r2.error, !r2.ok);
    });
  } else {
    toast(r.msg || r.error, !r.ok);
  }
});

/* Telegram OTP / 2FA prompt (called from Python via evaluate_js). */
window.UI = {
  pipePrompt(label) {
    askText("Telegram", label, (v) => API.pipe_prompt_answer(v || ""), true,
            () => API.pipe_prompt_answer(""));
  },
  confirmQuit(n) {
    modal("Quit Scr33nX",
          `${n} recording(s) still active.\n\nQuit anyway? Recordings will be stopped.`,
          "Quit", () => API.quit());
  },
};

/* ══ wizard ══ */
const WIZ_STEPS = [
  { t: "Welcome", body: () => `
      <p>This wizard sets up the Telegram upload pipeline: it converts finished
      .ts recordings to .mp4 and uploads them to a group/topic of yours.</p>
      <p>You'll need Telegram API credentials from
      <b>https://my.telegram.org → API development tools</b>.</p>` },
  { t: "Telegram API credentials", body: () => `
      <div class="field"><label>API ID</label><input id="w-api-id" value="${esc($("p-api-id").value)}"></div>
      <div class="field"><label>API Hash</label><input id="w-api-hash" value="${esc($("p-api-hash").value)}"></div>` },
  { t: "Destination", body: () => `
      <div class="field"><label>Chat / Group ID (e.g. -1001234567890)</label>
        <input id="w-group" value="${esc($("p-group").value)}"></div>
      <div class="field"><label>Topic ID (0 if none)</label>
        <input id="w-topic" value="${esc($("p-topic").value || "0")}"></div>
      <div class="field"><label>Converted .mp4 folder (optional)</label>
        <input id="w-conv" value="${esc($("p-conv").value)}"></div>` },
  { t: "Review & finish", body: () => `
      <p>Settings will be saved to Pipeline/pipeline_settings.json.
      On the first Upload connect you'll be prompted for your phone number
      and login code.</p>
      <label class="toggle-row"><span class="cb on" id="w-startnow"></span>
        Start the pipeline with Upload enabled now</label>` },
];
let wizStep = 0;
const wizData = {};
$("b-wizard").addEventListener("click", () => { wizStep = 0; wizShow(); });
function wizShow() {
  const st = WIZ_STEPS[wizStep];
  $("wiz-title").textContent = `🧙 ${st.t}`;
  $("wiz-stepnum").textContent = `Step ${wizStep + 1} of ${WIZ_STEPS.length}`;
  $("wiz-body").innerHTML = st.body();
  $("wiz-back").style.visibility = wizStep ? "visible" : "hidden";
  $("wiz-next").textContent = wizStep === WIZ_STEPS.length - 1 ? "✓ Save & Finish" : "Next →";
  $("wizard").hidden = false;
}
function wizCollect() {
  for (const [id, k] of [["w-api-id", "api_id"], ["w-api-hash", "api_hash"],
                         ["w-group", "group_id"], ["w-topic", "topic_id"],
                         ["w-conv", "converted_dir"]]) {
    const el = $(id);
    if (el) wizData[k] = el.value;
  }
}
$("wiz-next").addEventListener("click", async () => {
  wizCollect();
  if (wizStep < WIZ_STEPS.length - 1) { wizStep++; wizShow(); return; }
  const startNow = $("w-startnow") && $("w-startnow").classList.contains("on");
  $("wizard").hidden = true;
  await API.pipeline_save(wizData);
  for (const [k, id] of [["api_id", "p-api-id"], ["api_hash", "p-api-hash"],
                         ["group_id", "p-group"], ["topic_id", "p-topic"],
                         ["converted_dir", "p-conv"]])
    if (wizData[k] !== undefined) $(id).value = wizData[k];
  if (startNow) {
    await API.pipeline_stage(null, true);
    if (!(S && S.pipe.running)) await API.pipeline_toggle();
    toast("Pipeline settings saved — starting with Upload enabled.");
  } else {
    toast("Pipeline settings saved.");
  }
});
$("wiz-back").addEventListener("click", () => { wizCollect(); wizStep--; wizShow(); });
$("wiz-cancel").addEventListener("click", () => { $("wizard").hidden = true; });

/* ══ settings ══ */
async function loadSettings() {
  if (settingsLoaded || !API) return;
  settingsLoaded = true;
  const s = await API.get_settings();
  $("s-output").value = s.output_dir;
  $("s-maxsize").value = s.max_size_mb;
  $("s-interval").value = s.check_interval;
  const q = $("s-quality");
  q.innerHTML = s.quality_options.map(o =>
    `<option value="${o.height}" ${o.height === s.max_quality ? "selected" : ""}>${esc(o.label)}</option>`).join("");
  setSwitch("s-tray", s.minimize_to_tray);
  setSwitch("s-notif", s.notifications_enabled);
  setSwitch("s-gap", s.gap_warnings_enabled);
  setSwitch("s-n-started", s.notify_started);
  setSwitch("s-n-stopped", s.notify_stopped);
  setSwitch("s-n-down", s.notify_downgraded);
  setSwitch("s-n-disk", s.notify_lowdisk);
  setSwitch("s-vip-only", s.notify_vip_only);
  $("s-dur").value = s.notify_toast_secs;
  $("dur-val").textContent = s.notify_toast_secs;
  updateNotifDim();
  setSwitch("s-down", s.auto_downgrade_enabled);
  setSwitch("s-disk", s.low_disk_guard_enabled);
  $("s-disk-stop").value = s.low_disk_stop_gb;
  $("s-disk-resume").value = s.low_disk_resume_gb;
  updateDiskDim();
  setSwitch("s-pw", s.playwright_fallback_enabled);
  setSwitch("s-privacy", s.privacy_mode_enabled);
  const b = await API.browsers();
  const bs = $("s-browser");
  let opts = `<option value="">Ask each time</option><option value="system">System default</option>`;
  for (const br of b.browsers)
    opts += `<option value="${esc(br.path)}">${esc(br.name)}</option>`;
  bs.innerHTML = opts;
  bs.value = [...bs.options].some(o => o.value === s.preferred_browser)
    ? s.preferred_browser : "";
  $("s-pmode").value = s.preview_mode;
  $("s-pengine").value = s.preview_engine;
  $("s-ppath").value = s.preview_player_path;
  $("s-player-max").value = s.max_player_tiles;
  $("s-api-token").value = s.api_token || "";
  playerMaxTiles = s.max_player_tiles;
  runSystemCheck();
  loadVip();
}
function setSwitch(id, on) { $(id).classList.toggle("on", !!on); }
function updateNotifDim() {
  $("notif-types").classList.toggle("disabled", !$("s-notif").classList.contains("on"));
}
function updateDiskDim() {
  const on = $("s-disk").classList.contains("on");
  $("disk-thresholds").classList.toggle("disabled", !on);
  $("s-disk-stop").disabled = !on;
  $("s-disk-resume").disabled = !on;
}
$("s-dur").addEventListener("input", (e) => { $("dur-val").textContent = e.target.value; });
$("s-notif").closest(".toggle-row").addEventListener("click", () => setTimeout(updateNotifDim, 0));
$("s-disk").closest(".toggle-row").addEventListener("click", () => setTimeout(updateDiskDim, 0));

async function loadVip() {
  if (!API) return;
  const r = await API.vip_get();
  $("vip-count").textContent = r.items.length;
  const box = $("vip-list");
  box.innerHTML = r.items.length
    ? r.items.map(it => `<span class="vip-chip">${esc(it.name)} <small>${esc(it.site)}</small>` +
        `<button data-vipdel="${esc(it.key)}" title="Remove from VIP">✕</button></span>`).join("")
    : `<span class="vip-empty">No VIP models yet.</span>`;
}
function vipRefresh(fn) { fn(); setTimeout(loadVip, 150); }
$("vip-list").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-vipdel]");
  if (!b || !API) return;
  await API.vip_remove([b.dataset.vipdel]);
  setTimeout(loadVip, 120);
});

$("s-pick").addEventListener("click", async () => {
  const r = await API.pick_folder($("s-output").value);
  if (r.path) $("s-output").value = r.path;
});
$("s-save").addEventListener("click", async () => {
  const r = await API.save_settings({
    output_dir: $("s-output").value,
    max_size_mb: $("s-maxsize").value,
    check_interval: $("s-interval").value,
    max_quality: $("s-quality").value,
    minimize_to_tray: $("s-tray").classList.contains("on"),
    notifications_enabled: $("s-notif").classList.contains("on"),
    gap_warnings_enabled: $("s-gap").classList.contains("on"),
    notify_started: $("s-n-started").classList.contains("on"),
    notify_stopped: $("s-n-stopped").classList.contains("on"),
    notify_downgraded: $("s-n-down").classList.contains("on"),
    notify_lowdisk: $("s-n-disk").classList.contains("on"),
    notify_toast_secs: $("s-dur").value,
    notify_vip_only: $("s-vip-only").classList.contains("on"),
    auto_downgrade_enabled: $("s-down").classList.contains("on"),
    low_disk_guard_enabled: $("s-disk").classList.contains("on"),
    low_disk_stop_gb: $("s-disk-stop").value,
    low_disk_resume_gb: $("s-disk-resume").value,
    playwright_fallback_enabled: $("s-pw").classList.contains("on"),
    privacy_mode_enabled: $("s-privacy").classList.contains("on"),
    preferred_browser: $("s-browser").value,
    preview_mode: $("s-pmode").value,
    preview_engine: $("s-pengine").value,
    preview_player_path: $("s-ppath").value,
    max_player_tiles: $("s-player-max").value,
    api_token: $("s-api-token").value,
  });
  playerMaxTiles = Number($("s-player-max").value) || playerMaxTiles;
  renderPlayerTab();
  toast("✓ Settings saved" + (r.note ? ` — ${r.note}` : ""));
});

async function runSystemCheck() {
  const r = await API.system_check();
  $("syscheck").innerHTML = r.rows.map(row => `
    <div class="sys-row">
      <span class="mark-${row.state}">${{ ok: "✓", warn: "⚠", err: "✕" }[row.state]}</span>
      ${esc(row.label)}<span class="path">${esc(row.detail)}</span>
      ${row.actions.map(a => `<button class="btn mini" data-act="${esc(a.act)}"
        data-arg="${esc(a.arg)}">${esc(a.label)}</button>`).join("")}
    </div>`).join("");
}
$("s-recheck").addEventListener("click", runSystemCheck);
$("syscheck").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  API.sys_fix(b.dataset.act, b.dataset.arg);
  toast("Started — check the Activity Log; then Re-check.");
});

/* ══ selection & interactions ══ */
function tabOf(el) { return el.closest('[data-panel="saved"]') ? "saved" : "rec"; }
function workset(tab) {
  return checked[tab].size ? [...checked[tab]] : [...sel[tab]];
}
function visibleOrder(tab) {
  return tab === "rec" ? recVisible
       : savedDisplay.filter(i => i.head === undefined).map(i => i.sid);
}

document.addEventListener("click", (e) => {
  if (suppressClick) { suppressClick = false; return; }
  closeCtx();

  // Settings switches, wizard/browser-picker checkboxes: a plain visual
  // toggle read on Save (they carry no data-* hook of their own).
  const trow = e.target.closest("label.toggle-row");
  if (trow) {
    const t = trow.querySelector(".switch, .cb");
    if (t) t.classList.toggle("on");
    return;
  }

  const tog = e.target.closest("[data-tog]");
  if (tog) {
    const [tab, site] = tog.dataset.tog.split(":");
    if (collapsed[tab].has(site)) collapsed[tab].delete(site);
    else collapsed[tab].add(site);
    if (tab === "rec") {
      $("rows").dataset.sig = "";
      try { renderRec(S.models); } catch (e) { console.error("[data-tog] renderRec failed:", e); }
    } else rebuildSavedDisplay();
    return;
  }

  const cb = e.target.closest(".cb[data-cb]");
  if (cb) {
    const tab = tabOf(cb);
    const key = cb.dataset.cb;
    if (checked[tab].has(key)) checked[tab].delete(key);
    else checked[tab].add(key);
    cb.classList.toggle("on", checked[tab].has(key));
    updateSelLabel();
    return;
  }

  const sw = e.target.closest(".switch[data-auto]");
  if (sw && API) {
    const on = !sw.classList.contains("on");
    sw.classList.toggle("on", on);
    API.set_auto(sw.dataset.auto, on);
    return;
  }

  const star = e.target.closest(".stars[data-stars] i");
  if (star && API) {
    const wrap = star.parentElement;
    const key = wrap.dataset.stars;
    const cur = parseInt(wrap.dataset.rank, 10) || 0;
    const idx = parseInt(star.dataset.i, 10);
    const target = idx === cur ? 0 : idx;
    const saved = key.startsWith("saved:");
    const doIt = () => API.set_rank([key], target, saved);
    if (cur !== 0) {
      const stars = (n) => "★".repeat(n) + "☆".repeat(5 - n);
      modal("Change rank",
            `${target === 0 ? "Clear" : "Change"} the rank?\n\n${stars(cur)}  →  ${stars(target)}`,
            "Yes", doIt);
    } else doIt();
    return;
  }

  const row = e.target.closest(".row[data-key]");
  if (row) {
    const tab = row.dataset.tab === "saved" ? "saved" : "rec";
    const key = row.dataset.key;
    const order = visibleOrder(tab);
    if (e.shiftKey && anchor[tab]) {
      const a = order.indexOf(anchor[tab]), b = order.indexOf(key);
      if (a >= 0 && b >= 0) {
        sel[tab].clear();
        for (let i = Math.min(a, b); i <= Math.max(a, b); i++) sel[tab].add(order[i]);
      }
    } else if (e.ctrlKey) {
      if (sel[tab].has(key)) sel[tab].delete(key); else sel[tab].add(key);
      anchor[tab] = key;
    } else {
      sel[tab].clear();
      sel[tab].add(key);
      anchor[tab] = key;
    }
    refreshSelectionClasses(tab);
    return;
  }

  // Click on empty table space clears the current selection.
  const gw = e.target.closest(".grid-wrap");
  if (gw) {
    const tab = gw.closest('[data-panel="saved"]') ? "saved" : "rec";
    if (sel[tab].size) { sel[tab].clear(); anchor[tab] = null; refreshSelectionClasses(tab); }
  }
});

function refreshSelectionClasses(tab) {
  if (tab === "rec") {
    for (const [key, el] of rowEls) el.classList.toggle("selected", sel.rec.has(key));
    updateSelLabel();
  } else {
    renderSavedWindow();
  }
}

/* Ctrl/Cmd+A — select every visible row in the active table tab (respects the
   current filter). Ignored while typing in a field so it still selects text. */
document.addEventListener("keydown", (e) => {
  if (!(e.ctrlKey || e.metaKey) || (e.key !== "a" && e.key !== "A")) return;
  const t = e.target;
  if (t instanceof Element && t.matches("input, textarea, select")) return;
  const panel = document.querySelector(".panel.active");
  const p = panel && panel.dataset.panel;
  const tab = p === "recorder" ? "rec" : p === "saved" ? "saved" : null;
  if (!tab) return;
  e.preventDefault();
  const order = visibleOrder(tab);
  sel[tab].clear();
  for (const k of order) sel[tab].add(k);
  if (order.length) anchor[tab] = order[order.length - 1];
  refreshSelectionClasses(tab);
});

/* marquee drag-selection */
let mq = null, mqStart = null;
document.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const wrap = e.target.closest(".grid-wrap");
  if (!wrap || !wrap.closest('[data-panel="recorder"], [data-panel="saved"]')) return;
  if (e.target.closest(".cb, .switch, .stars, .tog, button, input, select")) return;
  mqStart = { x: e.clientX, y: e.clientY, wrap, tab: tabOf(wrap) };
});
document.addEventListener("mousemove", (e) => {
  if (!mqStart) return;
  if (!mq) {
    if (Math.abs(e.clientX - mqStart.x) < 5 && Math.abs(e.clientY - mqStart.y) < 5) return;
    mq = document.createElement("div");
    mq.className = "marquee";
    document.body.appendChild(mq);
    sel[mqStart.tab].clear();
  }
  const x1 = Math.min(mqStart.x, e.clientX), y1 = Math.min(mqStart.y, e.clientY);
  const x2 = Math.max(mqStart.x, e.clientX), y2 = Math.max(mqStart.y, e.clientY);
  Object.assign(mq.style, { left: x1 + "px", top: y1 + "px",
                            width: (x2 - x1) + "px", height: (y2 - y1) + "px" });
  const tab = mqStart.tab;
  mqStart.wrap.querySelectorAll(".row").forEach(r => {
    const b = r.getBoundingClientRect();
    const hit = !(b.right < x1 || b.left > x2 || b.bottom < y1 || b.top > y2);
    if (hit) sel[tab].add(r.dataset.key); else sel[tab].delete(r.dataset.key);
    r.classList.toggle("selected", hit);
  });
  if (tab === "rec") updateSelLabel();
  e.preventDefault();
});
document.addEventListener("mouseup", () => {
  if (mq) { mq.remove(); suppressClick = true; }
  mq = null; mqStart = null;
});

/* ══ context menus ══ */
function closeCtx() { const c = $("ctx"); c.hidden = true; c.innerHTML = ""; }

const menuActions = new Map();
let actSeq = 0;
function showMenu(items, x, y) {
  menuActions.clear();
  actSeq = 0;
  const withIds = (list) => list.map(it => {
    if (it === "hr" || !it) return it;
    const copy = { ...it };
    if (copy.sub) copy.sub = withIds(copy.sub);
    else if (copy.action) { copy.id = String(++actSeq); menuActions.set(copy.id, copy.action); }
    return copy;
  });
  const tagged = withIds(items.filter(Boolean));
  const html = (function build(list) {
    let h = "";
    for (const it of list) {
      if (it === "hr") { h += "<hr>"; continue; }
      const cls = `mi ${it.danger ? "danger" : ""} ${it.disabled ? "disabled" : ""}`;
      if (it.sub) h += `<div class="${cls}">${esc(it.label)}<span class="sub-mark">▸</span><div class="ctx sub">${build(it.sub)}</div></div>`;
      else h += `<div class="${cls}" data-id="${it.id}">${esc(it.label)}</div>`;
    }
    return h;
  })(tagged);
  const c = $("ctx");
  c.innerHTML = html;
  c.hidden = false;
  const mw = c.offsetWidth, mh = c.offsetHeight;
  c.style.left = Math.min(x, innerWidth - mw - 8) + "px";
  c.style.top = Math.min(y, innerHeight - mh - 8) + "px";
}
$("ctx").addEventListener("click", (e) => {
  const mi = e.target.closest(".mi[data-id]");
  if (!mi) return;
  const fn = menuActions.get(mi.dataset.id);
  closeCtx();
  if (fn) fn();
  e.stopPropagation();
});
document.addEventListener("mousedown", (e) => {
  if (!$("ctx").hidden && !e.target.closest("#ctx")) closeCtx();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeCtx();
    $("modal").hidden = true;
    // The prompt may have Python blocked on an answer (Telegram OTP/2FA
    // waits up to 5 min) — Escape must CANCEL it, not just hide it, or the
    // pipeline hangs until the timeout. Route through the Cancel handler.
    if (!$("prompt").hidden) {
      const cancel = $("prompt-cancel");
      if (cancel.onclick) cancel.onclick(); else $("prompt").hidden = true;
    }
    $("bpick").hidden = true;
    $("addsaved").hidden = true;
    $("playerpick").hidden = true;
    $("wizard").hidden = true;
  }
});
window.addEventListener("blur", closeCtx);

function rankSub(keys, saved) {
  const sub = [{ label: "☆  Clear rank", action: () => API.set_rank(keys, 0, saved) }];
  for (let r = 1; r <= 5; r++)
    sub.push({ label: "★".repeat(r) + "☆".repeat(5 - r),
               action: () => API.set_rank(keys, r, saved) });
  return sub;
}
function qualitySub(keys, cur) {
  const sub = [{ label: `${cur === 0 ? "●  " : "    "}Default (use global setting)`,
                 action: () => API.set_quality(keys, 0) }];
  for (const o of qualityOpts) {
    if (!o.height) continue;
    sub.push({ label: `${cur === o.height ? "●  " : "    "}${o.label}`,
               action: () => API.set_quality(keys, o.height) });
  }
  return sub;
}
function checkHelpers(tab) {
  return [
    "hr",
    sel[tab].size ? { label: `☑  Check Selected  (${sel[tab].size})`,
      action: () => { for (const k of sel[tab]) checked[tab].add(k); retab(tab); } } : null,
    { label: "☑  Check All Visible",
      action: () => { for (const k of visibleOrder(tab)) checked[tab].add(k); retab(tab); } },
    checked[tab].size ? { label: `☐  Uncheck All  (${checked[tab].size})`,
      action: () => { checked[tab].clear(); retab(tab); } } : null,
  ];
}
function retab(tab) {
  if (tab === "rec") {
    $("rows").dataset.sig = "";
    try { if (S) renderRec(S.models); } catch (e) { console.error("[retab] renderRec failed:", e); }
  } else renderSavedWindow();
  updateSelLabel();
}

// Player-tile right-click menu: same actions as the Recorder/Saved row this
// model actually lives in (Recorder takes priority if it's tracked in both,
// same precedence as statusOfTile/rankInfoFor), plus a Player-only "Close
// Tile". A tile can outlive its source (closing a tile never touches the
// Recorder or Saved Models, and removing a model there never auto-closes an
// open tile) — that orphaned case gets a minimal menu.
function tileContextItems(t) {
  const closeItem = { label: "✕  Close Tile (Player only)", action: () => closePlayerTile(t.id) };
  const m = (S && S.models || []).find(x => x.key === t.key.toLowerCase());
  if (m) {
    const keys = [m.key];
    return [
      closeItem, "hr",
      { label: `▶  Start Recording  ${m.name}`,
        action: () => confirmCrossSiteRec(keys, () => API.record(keys, true)) },
      { label: "⏹  Stop Recording", action: () => API.record(keys, false) },
      { label: `${m.auto ? "☑" : "☐"}  Auto-Record`, action: () => API.toggle_auto(keys) },
      { label: `🎞  Max Quality  (${m.q ? m.q + "p" : "Default"})`, sub: qualitySub(keys, m.q) },
      "hr",
      m.saved
        ? { label: "✕  Remove from Saved Models", action: () => API.saved_remove([`saved:${m.key}`]) }
        : { label: "⭐  Add to Saved Models", action: () => API.add_saved(keys) },
      { label: "▶  Preview", action: () => doPreview(m.name, m.site) },
      { label: "⭐  Set Rank", sub: rankSub(keys, false) },
      { label: "🔗  Edit Links…", action: () => openLinkEditor(m.key) },
      m.vip ? { label: "🌟  Remove from VIP List", action: () => vipRefresh(() => API.vip_remove(keys)) }
            : { label: "🌟  Add to VIP List", action: () => vipRefresh(() => API.vip_add(keys)) },
      "hr",
      { label: "🔗  Copy Model URL", action: () => API.copy_urls(keys, false, false) },
      { label: "🌐  Open in Browser", action: () => openBrowser(keys, false, false) },
      "hr",
      { label: "✕  Remove Model", danger: true, action: () => API.remove(keys) },
    ];
  }
  const savedIt = savedCache.items.find(x => x.site === t.site && x.name.toLowerCase() === t.name.toLowerCase());
  if (savedIt) {
    const keys = [savedIt.sid];
    const st0 = (S.saved_status || {})[savedIt.sid];
    const liveNow = st0 && (st0[0] === "online" || st0[0] === "recording");
    return [
      closeItem, "hr",
      { label: `＋  Add to Recorder  ${savedIt.name}`,
        action: () => { warnCrossSiteKeys(keys); API.saved_to_recorder(keys); } },
      liveNow ? { label: "▶  Add to Recorder & Start Recording",
                  action: () => confirmCrossSiteRec(keys, () => API.saved_record(keys)) } : null,
      { label: "▶  Preview", action: () => doPreview(savedIt.name, savedIt.site) },
      "hr",
      { label: "🔗  Copy Model URL", action: () => API.copy_urls(keys, true, false) },
      { label: "🌐  Open in Browser", action: () => openBrowser(keys, true, false) },
      { label: "⭐  Set Rank", sub: rankSub(keys, true) },
      { label: "🔗  Edit Links…", action: () => openLinkEditor(savedIt.sid) },
      savedIt.vip ? { label: "🌟  Remove from VIP List", action: () => vipRefresh(() => API.vip_remove(keys)) }
                  : { label: "🌟  Add to VIP List", action: () => vipRefresh(() => API.vip_add(keys)) },
      "hr",
      { label: "✕  Remove from Saved Models", danger: true, action: () => API.saved_remove(keys) },
    ];
  }
  // Orphaned tile — its model isn't tracked anywhere anymore.
  return [
    closeItem, "hr",
    { label: "🌐  Open in Browser", action: () => openBrowser([`${t.site}:${t.name}`], false, false) },
  ];
}

document.addEventListener("contextmenu", (e) => {
  const tileEl = e.target.closest(".tile[data-tile]");
  if (tileEl) {
    e.preventDefault();
    const t = findPlayerTile(Number(tileEl.dataset.tile));
    if (t) showMenu(tileContextItems(t), e.clientX, e.clientY);
    return;
  }
  const row = e.target.closest(".row[data-key]");
  if (!row) return;
  e.preventDefault();
  const tab = row.dataset.tab === "saved" ? "saved" : "rec";
  const key = row.dataset.key;
  if (!sel[tab].has(key) && !checked[tab].has(key)) {
    sel[tab].clear();
    sel[tab].add(key);
    anchor[tab] = key;
    refreshSelectionClasses(tab);
  }
  const keys = workset(tab).length ? workset(tab) : [key];
  const n = keys.length;
  const single = n === 1;
  const k0 = keys[0];

  if (tab === "rec") {
    const m = (S.models || []).find(x => x.key === k0);
    const items = single ? [
      { label: `▶  Start Recording  ${m.name}`,
        action: () => confirmCrossSiteRec(keys, () => API.record(keys, true)) },
      { label: "⏹  Stop Recording", action: () => API.record(keys, false) },
      { label: `${m.auto ? "☑" : "☐"}  Auto-Record`, action: () => API.toggle_auto(keys) },
      { label: `🎞  Max Quality  (${m.q ? m.q + "p" : "Default"})`, sub: qualitySub(keys, m.q) },
      "hr",
      m.saved
        ? { label: "✕  Remove from Saved Models",
            action: () => API.saved_remove([`saved:${k0}`]) }
        : { label: "⭐  Add to Saved Models", action: () => API.add_saved(keys) },
      { label: "▶  Preview", action: () => doPreview(m.name, m.site) },
      (m.status === "online" || m.status === "recording")
        ? { label: "▶  Add to Player", action: () => addPlayerTile(m.name, m.site) } : null,
      { label: "⭐  Set Rank", sub: rankSub(keys, false) },
      { label: "🔗  Edit Links…", action: () => openLinkEditor(k0) },
      m.vip ? { label: "🌟  Remove from VIP List", action: () => vipRefresh(() => API.vip_remove(keys)) }
            : { label: "🌟  Add to VIP List", action: () => vipRefresh(() => API.vip_add(keys)) },
      "hr",
      { label: "🔗  Copy Model URL", action: () => API.copy_urls(keys, false, false) },
      { label: "🌐  Open in Browser", action: () => openBrowser(keys, false, false) },
      { label: "🌐  Open in Browser (choose…)", action: () => openBrowser(keys, false, true) },
      { label: "📁  Open Output Folder", action: () => API.open_output_folder() },
      "hr",
      { label: "✕  Remove Model", danger: true,
        action: () => API.remove(keys) },
      ...checkHelpers(tab),
    ] : [
      { label: `▶  Start Recording  (${n} selected)`,
        action: () => confirmCrossSiteRec(keys, () => API.record(keys, true)) },
      { label: `⏹  Stop Recording  (${n} selected)`, action: () => API.record(keys, false) },
      { label: `☑  Toggle AUTO  (${n} selected)`, action: () => API.toggle_auto(keys) },
      { label: `🎞  Max Quality  (${n} selected)`, sub: qualitySub(keys, null) },
      { label: `⭐  Set Rank  (${n} selected)`, sub: rankSub(keys, false) },
      { label: `🌟  Add to VIP List  (${n})`, action: () => vipRefresh(() => API.vip_add(keys)) },
      { label: `🌟  Remove from VIP List  (${n})`, action: () => vipRefresh(() => API.vip_remove(keys)) },
      { label: `▶  Add to Player  (${n})`, action: () => addPlayerTilesBulk(
          keys.map(k => (S.models || []).find(x => x.key === k))
              .filter(mm => mm && (mm.status === "online" || mm.status === "recording"))
              .map(mm => ({ name: mm.name, site: mm.site }))) },
      "hr",
      { label: `🌐  Open in Browser  (${n})`, action: () => openBrowser(keys, false, false) },
      { label: `🌐  Open in Browser (choose…)  (${n})`, action: () => openBrowser(keys, false, true) },
      { label: `📋  Copy as OneTab List  (${n})`, action: () => API.copy_urls(keys, false, true) },
      { label: "📁  Open Output Folder", action: () => API.open_output_folder() },
      "hr",
      { label: `✕  Remove  (${n})`, danger: true,
        action: () => modal("Remove models", `Remove ${n} model(s) from the Recorder?`,
                            "Remove", () => API.remove(keys)) },
      ...checkHelpers(tab),
    ];
    showMenu(items, e.clientX, e.clientY);
  } else {
    const it = savedCache.items.find(x => x.sid === k0) || { name: "?", site: "?" };
    const st0 = (S.saved_status || {})[k0];
    const liveNow = st0 && (st0[0] === "online" || st0[0] === "recording");
    const items = single ? [
      { label: `＋  Add to Recorder  ${it.name}`,
        action: () => { warnCrossSiteKeys(keys); API.saved_to_recorder(keys); } },
      liveNow ? { label: "▶  Add to Recorder & Start Recording",
                  action: () => confirmCrossSiteRec(keys, () => API.saved_record(keys)) } : null,
      { label: "▶  Preview", action: () => doPreview(it.name, it.site) },
      liveNow ? { label: "▶  Add to Player", action: () => addPlayerTile(it.name, it.site) } : null,
      "hr",
      { label: "🔗  Copy Model URL", action: () => API.copy_urls(keys, true, false) },
      { label: "🌐  Open in Browser", action: () => openBrowser(keys, true, false) },
      { label: "🌐  Open in Browser (choose…)", action: () => openBrowser(keys, true, true) },
      { label: "⭐  Set Rank", sub: rankSub(keys, true) },
      { label: "🔗  Edit Links…", action: () => openLinkEditor(k0) },
      it.vip ? { label: "🌟  Remove from VIP List", action: () => vipRefresh(() => API.vip_remove(keys)) }
             : { label: "🌟  Add to VIP List", action: () => vipRefresh(() => API.vip_add(keys)) },
      "hr",
      { label: "✕  Remove from Saved Models", danger: true,
        action: () => API.saved_remove(keys) },
      ...checkHelpers(tab),
    ] : [
      { label: `＋  Add to Recorder  (${n})`,
        action: () => { warnCrossSiteKeys(keys); API.saved_to_recorder(keys); } },
      { label: `⭐  Set Rank  (${n})`, sub: rankSub(keys, true) },
      { label: `🌟  Add to VIP List  (${n})`, action: () => vipRefresh(() => API.vip_add(keys)) },
      { label: `🌟  Remove from VIP List  (${n})`, action: () => vipRefresh(() => API.vip_remove(keys)) },
      { label: `▶  Add to Player  (${n})`, action: () => {
          const stMap = S.saved_status || {};
          addPlayerTilesBulk(keys.map(k => savedCache.items.find(x => x.sid === k))
            .filter(it => it && stMap[it.sid] && (stMap[it.sid][0] === "online" || stMap[it.sid][0] === "recording"))
            .map(it => ({ name: it.name, site: it.site })));
        } },
      "hr",
      { label: `🌐  Open in Browser  (${n})`, action: () => openBrowser(keys, true, false) },
      { label: `🌐  Open in Browser (choose…)  (${n})`, action: () => openBrowser(keys, true, true) },
      { label: `📋  Copy as OneTab List  (${n})`, action: () => API.copy_urls(keys, true, true) },
      "hr",
      { label: `✕  Remove from Saved  (${n})`, danger: true,
        action: () => modal("Remove from Saved", `Remove ${n} model(s) from Saved Models?`,
                            "Remove", () => API.saved_remove(keys)) },
      ...checkHelpers(tab),
    ];
    showMenu(items, e.clientX, e.clientY);
  }
});

/* ══ browser picker ══ */
async function openBrowser(keys, saved, forceChoose) {
  if (keys.length > 10) {
    modal("Open in Browser", `Open ${keys.length} tabs in your browser?`, "Open",
          () => openBrowser2(keys, saved, forceChoose));
    return;
  }
  openBrowser2(keys, saved, forceChoose);
}
async function openBrowser2(keys, saved, forceChoose) {
  if (!forceChoose) {
    const r = await API.open_browser(keys, saved);
    if (!r.choose) return;
  }
  const b = await API.browsers();
  let list = `<label class="toggle-row"><input type="radio" name="bp" value="system" checked> System default</label>`;
  for (const br of b.browsers)
    list += `<label class="toggle-row"><input type="radio" name="bp" value="${esc(br.path)}"> ${esc(br.name)}</label>`;
  $("bpick-list").innerHTML = list;
  $("bpick-remember").classList.remove("on");
  $("bpick").hidden = false;
  $("bpick-ok").onclick = () => {
    const target = document.querySelector('input[name="bp"]:checked').value;
    const remember = $("bpick-remember").classList.contains("on");
    $("bpick").hidden = true;
    // "Remember my choice" is honored in BOTH flows — it used to be
    // silently ignored when the picker was opened via "(choose…)".
    API.open_browser(keys, saved, target, remember);
  };
  $("bpick-cancel").onclick = () => { $("bpick").hidden = true; };
}
// bpick-remember toggles via the shared label.toggle-row handler above.

/* ══ preview ══ */
let hls = null;
let previewModel = null;   // {name, site} while the embedded overlay is open
async function doPreview(name, site) {
  toast(`● Opening preview for ${name} (${site}) — resolving the stream…`);
  const r = await API.preview(name, site);
  if (!r.ok) { toast(r.error || "Preview failed.", true); return; }
  if (r.mode === "external") { toast(`Preview: ${r.player} window opened.`); return; }
  const video = $("player-video");
  $("player-title").textContent = `▶ ${r.title}`;
  previewModel = { name, site };
  patchPreviewControls();
  $("player").hidden = false;
  if (window.Hls && Hls.isSupported()) {
    if (hls) hls.destroy();
    hls = new Hls({ liveDurationInfinity: true });
    hls.loadSource(r.url);
    hls.attachMedia(video);
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) { toast("Preview playback error.", true); closePlayer(); }
    });
  } else {
    video.src = r.url;   // native HLS fallback (not on Windows, but harmless)
  }
}
function closePlayer() {
  if (hls) { hls.destroy(); hls = null; }
  previewModel = null;
  const v = $("player-video");
  v.pause();
  v.removeAttribute("src");
  v.load();
  $("player").hidden = true;
}
$("player-close").addEventListener("click", closePlayer);

// ── Start/Stop recording from the preview overlay ──
// Start via saved_record (adds the model to the Recorder if it isn't there
// yet, then records); Stop hits the engine directly.
function recControl(name, site, start) {
  if (start) {
    confirmCrossSiteRec([`${site}:${name}`],
                        () => API.saved_record([`saved:${site}:${name}`]));
  } else API.record([`${site}:${name}`], false);
}

// Live status of a model by name/site, checking both the Recorder engine
// and the Saved-Models scanner (same logic tiles use).
function statusOfNameSite(name, site) {
  return statusOfTile({ key: `${site}:${name}`, name, site });
}

// Resolve a model's rank + the key set_rank() should use, checking both the
// Recorder engine (S.models) and Saved Models (savedCache.items) — same
// dual-lookup idiom as statusOfTile, since a previewed/tiled model might
// only be tracked in one of the two. Shared by the preview overlay and
// theater tile star widgets.
function rankInfoFor(name, site) {
  const key = `${site}:${name}`;
  const m = (S && S.models || []).find(x => x.key === key);
  if (m) return { key, rank: m.rank };
  const it = savedCache.items.find(x =>
    x.site === site && x.name.toLowerCase() === name.toLowerCase());
  return { key: `saved:${site}:${name.toLowerCase()}`, rank: it ? it.rank : 0 };
}

// Called from doPreview and every tick — keeps the overlay's status badge,
// REC/Stop buttons, and rank stars in sync while it's open.
function patchPreviewControls() {
  if (!previewModel) return;
  const st = statusOfNameSite(previewModel.name, previewModel.site);
  const { key: rkey, rank } = rankInfoFor(previewModel.name, previewModel.site);
  const starsEl = $("player-stars");
  starsEl.dataset.stars = rkey;
  if (starsEl.dataset.rank !== String(rank)) {
    starsEl.dataset.rank = rank;
    starsEl.innerHTML = starsHtml(rank);
  }
  const badge = $("player-status");
  badge.hidden = false;
  badge.className = `st ${st}`;
  badge.querySelector(".st-txt").textContent = STATUS_TXT[st];
  $("player-rec").disabled = st !== "online";
  $("player-stop").disabled = st !== "recording";
}
$("player-rec").addEventListener("click", () => {
  if (previewModel) recControl(previewModel.name, previewModel.site, true);
});
$("player-stop").addEventListener("click", () => {
  if (previewModel) recControl(previewModel.name, previewModel.site, false);
});

/* ══ Player tab (grid + theater preview) ══
   Every open tile streams live (muted) at once, in either layout — up to
   playerMaxTiles concurrent HLS connections. That's the real bandwidth/CPU
   cost of opening more tiles; the Settings cap exists to bound it.
   Tiles start streaming the moment they're added and KEEP streaming while
   other tabs are in front, so switching back to the Player never reloads. */
let playerTiles = [];           // [{id, key, name, site}], insertion order = grid order
let playerHls = new Map();      // tileId -> Hls instance (or {destroy,media} stub)
let playerLayout = "grid";      // "grid" | "theater"
let playerStripPos = "bottom";  // "bottom" | "side"
let playerActiveId = null;      // tileId centered in theater mode
let playerTileSeq = 0;
let playerTabLoaded = false;
let playerPickerList = [];
// Status/Rank filters for the whole Player tab — same semantics as
// statusFilter/rankFilter on the Recorder/Saved tabs. ONE state drives both
// the open tiles (non-matching tiles are hidden, never closed) and the
// "+ Add Tile" picker list, so what you filter is what you see and what you
// can add. Tab-persistent: it survives closing/reopening the picker.
const playerFilter = { status: new Set(), rank: 0 };
let playerPending = new Set();       // tileIds with an in-flight preview_embedded() call
let playerCooldownUntil = new Map(); // tileId -> ms timestamp; skip auto-retry until then
let playerTileErr = new Map();       // tileId -> short error text shown on the tile

const PLAYER_RETRY_MS = 30000;  // wait between attempts for a tile that failed

// Playback diagnostics land in the Activity Log ("[ui] …") — in-page media
// failures are otherwise completely invisible from outside the WebView.
function plog(msg) { try { API.client_log(msg); } catch (_) {} }

// An immediate play() can lose the race against hls.js's own media load
// ("AbortError: interrupted by a new load request") and the video then sits
// paused forever. Try now AND once more when the element reports canplay.
function tilePlay(el, name) {
  const attempt = () => el.play().catch((e) =>
    plog(`tile ${name}: play() rejected — ${e && e.name}: ${e && e.message}`));
  el.addEventListener("canplay", attempt, { once: true });
  attempt();
}

function setTileError(tileId, msg) {
  playerTileErr.set(tileId, msg);
  const el = document.querySelector(`[data-tile="${tileId}"] .tile-err`);
  if (el) { el.textContent = msg; el.hidden = false; }
}
function clearTileError(tileId) {
  playerTileErr.delete(tileId);
  const el = document.querySelector(`[data-tile="${tileId}"] .tile-err`);
  if (el) el.hidden = true;
}

function tileKey(name, site) { return `${site}:${name}`; }
function findPlayerTile(id) { return playerTiles.find(t => t.id === id); }
function statusOfKey(key) {
  const m = (S && S.models || []).find(x => x.key === key);
  return m ? m.status : "offline";
}

// Like statusOfKey, but also checks Saved Models' live status — needed for
// Player tiles added from Saved Models, which aren't tracked in S.models.
function statusOfTile(t) {
  const st = statusOfKey(t.key);
  if (st !== "offline") return st;
  const sv = S && S.saved_status && S.saved_status[`saved:${t.site}:${t.name.toLowerCase()}`];
  return sv ? sv[0] : "offline";
}

function loadPlayerTab() {
  if (playerTabLoaded) return;
  playerTabLoaded = true;
  plog("player tab opened (diagnostics active)");
  if (!settingsLoaded) API.get_settings().then(s => { playerMaxTiles = s.max_player_tiles; renderPlayerTab(); });
}

// REC/Stop buttons are in every tile's markup but only displayed (via CSS)
// on the big theater tile — identical markup lets tiles MOVE between the
// grid / theater containers without being rebuilt, so playback survives.
function tileHtml(t) {
  const st = statusOfTile(t);
  const playing = playerHls.has(t.id);
  const { key: rkey, rank } = rankInfoFor(t.name, t.site);
  // AUTO switch is only live when the model is in the Recorder (set_auto
  // refuses otherwise) — "na" renders it dimmed with a hint instead.
  // Recorder keys are always lowercase; tile keys may keep Saved-list case.
  const am = (S && S.models || []).find(x => x.key === t.key.toLowerCase());
  return `
    <div class="tile" data-tile="${t.id}">
      <div class="tile-media">
        <video class="tile-video" autoplay muted playsinline></video>
        <div class="tile-poster"${playing ? " hidden" : ""}>${esc((t.name[0] || "?").toUpperCase())}</div>
        <div class="tile-err"${playerTileErr.has(t.id) ? "" : " hidden"}>${esc(playerTileErr.get(t.id) || "")}</div>
        <button class="tile-close" title="Close tile" data-tile-close="${t.id}">✕</button>
      </div>
      <div class="tile-info">
        <span class="tile-name">${esc(t.name)}</span>
        <span class="tile-lnk">${lnkHtml(akaOf(t.name, t.site), st)}</span>
        <span class="tile-star stars" data-stars="${esc(rkey)}" data-rank="${rank}">${starsHtml(rank)}</span>
        <span class="tile-site">${SITE_LABEL[t.site] || t.site}</span>
        <button class="btn rec tile-recbtn" data-tile-rec="${t.id}"${st === "online" ? "" : " disabled"}>▶ REC</button>
        <button class="btn tile-stopbtn" data-tile-stop="${t.id}"${st === "recording" ? "" : " disabled"}>⏹ Stop</button>
        <label class="tile-auto${am ? "" : " na"}" title="${am ? "Auto-record when online" : "Add to Recorder to enable Auto-Record"}"><span class="switch${am && am.auto ? " on" : ""}"${am ? ` data-auto="${esc(t.key.toLowerCase())}"` : ""}></span>AUTO</label>
        <span class="st ${st}"><span class="dot"></span><span class="st-txt">${STATUS_TXT[st]}</span></span>
      </div>
      <div class="tile-actions" data-phase2-slot></div>
    </div>`;
}

// tileId -> live DOM element; elements are created once and MOVED between
// containers on layout/active changes instead of re-rendered, so <video>
// playback continues without the rebuffer "blackout".
let playerTileEls = new Map();
function ensureTileEl(t) {
  let el = playerTileEls.get(t.id);
  if (!el) {
    const tmp = document.createElement("div");
    tmp.innerHTML = tileHtml(t);
    el = tmp.firstElementChild;
    playerTileEls.set(t.id, el);
  }
  return el;
}

// Text of #player-empty in index.html — restored whenever the Player is
// genuinely empty (the same element doubles as the "filtered out" notice).
const PLAYER_EMPTY_TXT = $("player-empty") ? $("player-empty").textContent : "";

function playerFilterActive() {
  return playerFilter.status.size > 0 || playerFilter.rank !== 0;
}

// Does this tile pass the toolbar Status/Rank filters? Rank comes from
// rankInfoFor (S.models alone misses tiles added from Saved Models).
// `sticky` is used by the per-tick pass: "checking" is a transient poll
// state, so a tile mid-check keeps whatever visibility it already had
// instead of blinking out and back every cycle.
function tileMatchesFilter(t, opts) {
  const sf = playerFilter.status, rf = playerFilter.rank;
  const st = statusOfTile(t);
  if (st === "checking" && sf.size && !sf.has("checking") && opts && opts.sticky) {
    const prev = playerTileEls.get(t.id);
    if (prev) return !prev.classList.contains("is-filtered");
  }
  return (sf.size === 0 || sf.has(st))
    && rankMatch(rankInfoFor(t.name, t.site).rank, rf);
}

// Hide/show open tiles to match the filters and refresh the count + empty
// state. Hidden tiles are only display:none — they stay in the DOM and KEEP
// streaming, so clearing the filter brings them back instantly with no
// reload (removing or re-parenting them would pause their <video>).
// Returns the list of tiles currently visible.
function applyPlayerTileFilter(opts) {
  if (!$("player-stage")) return [];
  const vis = [];
  for (const t of playerTiles) {
    const ok = tileMatchesFilter(t, opts);
    const el = playerTileEls.get(t.id);
    if (el) {
      const was = el.classList.contains("is-filtered");
      el.classList.toggle("is-filtered", !ok);
      // A tile un-hidden by this pass alone (e.g. its rank changed while a
      // Rank filter hid it) never goes through reattachPlayerPlayback, and
      // display:none can leave its <video> paused — nudge it. play() on an
      // already-playing element is a no-op.
      if (was && ok && playerHls.has(t.id)) {
        const v = el.querySelector(".tile-video");
        if (v) v.play().catch(() => {});
      }
    }
    if (ok) vis.push(t);
  }
  const act = playerFilterActive();
  $("player-count").textContent = `${playerTiles.length} / ${playerMaxTiles} tiles`
    + (act ? ` · ${vis.length} shown` : "");
  const empty = $("player-empty");
  empty.textContent = playerTiles.length === 0
    ? PLAYER_EMPTY_TXT
    : "No open tiles match the Status / Rank filters. Tiles are only hidden — clear the filters to bring them back.";
  empty.hidden = vis.length > 0;
  $("player-stage").hidden = vis.length === 0;
  return vis;
}

function renderPlayerTab() {
  if (!$("player-stage")) return;
  // Reconcile, don't rebuild: each tile has ONE persistent element that is
  // moved into the right container. A tile can only exist in one place, so
  // there are never stale duplicates, and moving keeps videos playing.
  for (const [id, el] of playerTileEls) {
    if (!findPlayerTile(id)) { el.remove(); playerTileEls.delete(id); }
  }
  if (playerLayout === "grid") {
    $("player-grid").hidden = false;
    $("player-theater").hidden = true;
    // appendChild on an element already in place still re-inserts it, which
    // pauses its <video> — skip tiles that are already parented to the grid
    // (tiles are only ever appended in insertion order, so order holds).
    for (const t of playerTiles) {
      const el = ensureTileEl(t);
      if (el.parentElement !== $("player-grid")) $("player-grid").appendChild(el);
    }
  } else {
    $("player-grid").hidden = true;
    $("player-theater").hidden = false;
    // Never centre a tile the filters hide — promote the first visible one.
    const cur = findPlayerTile(playerActiveId);
    const active = (cur && tileMatchesFilter(cur) ? cur : null)
      || playerTiles.find(t => tileMatchesFilter(t)) || playerTiles[0] || null;
    playerActiveId = active ? active.id : null;
    if (active) $("theater-active").appendChild(ensureTileEl(active));
    for (const t of playerTiles) {
      if (!active || t.id !== active.id) $("theater-strip").appendChild(ensureTileEl(t));
    }
  }
  // Native player controls only on the big theater tile.
  for (const [id, el] of playerTileEls) {
    const v = el.querySelector(".tile-video");
    if (v) v.controls = (playerLayout === "theater" && id === playerActiveId);
  }
  applyPlayerTileFilter();
  reattachPlayerPlayback();
}

// Structural re-renders replace tile DOM nodes; re-point any live Hls
// instance at its (possibly new) <video> element instead of restarting it.
// hls.js requires an explicit detach before re-attaching a new element.
function reattachPlayerPlayback() {
  for (const [tileId, inst] of playerHls) {
    const el = document.querySelector(`[data-tile="${tileId}"] .tile-video`);
    if (!el) continue;
    if (inst.media !== el) {
      if (inst.attachMedia) {
        try { inst.detachMedia(); } catch (_) {}
        inst.attachMedia(el);
      } else {
        el.src = inst.media ? inst.media.src : "";
        inst.media = el;
      }
      const t = findPlayerTile(tileId);
      tilePlay(el, t ? t.name : tileId);
    } else if (el.paused) {
      // Moving an element in the DOM pauses its <video>; just resume.
      el.play().catch(() => {});
    }
    const poster = document.querySelector(`[data-tile="${tileId}"] .tile-poster`);
    if (poster) poster.hidden = true;
  }
}

// Called every tick — patches status badges, and starts/stops per-tile
// playback as models go online/offline. Never touches a <video> element
// that's already correctly playing, so an active stream isn't interrupted.
function patchPlayerStatuses() {
  for (const t of playerTiles) {
    const st = statusOfTile(t);
    const el = document.querySelector(`[data-tile="${t.id}"] .st`);
    if (el) {
      el.className = `st ${st}`;
      el.querySelector(".st-txt").textContent = STATUS_TXT[st];
    }
    const recBtn = document.querySelector(`[data-tile-rec="${t.id}"]`);
    if (recBtn) recBtn.disabled = st !== "online";
    const stopBtn = document.querySelector(`[data-tile-stop="${t.id}"]`);
    if (stopBtn) stopBtn.disabled = st !== "recording";
    const starEl = document.querySelector(`[data-tile="${t.id}"] .tile-star`);
    if (starEl) {
      const { key: rkey, rank } = rankInfoFor(t.name, t.site);
      starEl.dataset.stars = rkey;
      if (starEl.dataset.rank !== String(rank)) {
        starEl.dataset.rank = rank;
        starEl.innerHTML = starsHtml(rank);
      }
    }
    // 🔗 cross-site marker (dataset caches the last-set html so the node —
    // and any open tooltip — is only replaced when the state changes)
    const lnkEl = document.querySelector(`[data-tile="${t.id}"] .tile-lnk`);
    if (lnkEl) {
      const want = lnkHtml(akaOf(t.name, t.site), st);
      if (lnkEl.dataset.h !== want) { lnkEl.dataset.h = want; lnkEl.innerHTML = want; }
    }
    // AUTO switch (theater tile): follows Recorder membership + auto state
    const autoEl = document.querySelector(`[data-tile="${t.id}"] .tile-auto`);
    if (autoEl) {
      const am = (S && S.models || []).find(x => x.key === t.key.toLowerCase());
      const sw = autoEl.querySelector(".switch");
      sw.classList.toggle("on", !!(am && am.auto));
      autoEl.classList.toggle("na", !am);
      autoEl.title = am ? "Auto-record when online"
                        : "Add to Recorder to enable Auto-Record";
      if (am) sw.dataset.auto = t.key.toLowerCase();
      else delete sw.dataset.auto;
    }
    // "checking" is a transient recorder-poll state, not a real status change
    // — reacting to it stopped live playback every poll cycle.
    if (st === "checking") continue;
    const live = st === "online" || st === "recording";
    // Auto-(re)start playback even while another tab is in front — tiles
    // stream continuously so returning to the Player never reloads them.
    if (live && !playerHls.has(t.id)) playTile(t.id, { silent: true });
    else if (!live && playerHls.has(t.id)) stopTilePlayback(t.id);
  }
  // Statuses just moved — re-apply the toolbar filters so tiles appear and
  // disappear as models go online/offline (visibility only; nothing is
  // closed and nothing stops streaming).
  const vis = applyPlayerTileFilter({ sticky: true });
  // Structural work only when the centred theater tile itself got hidden.
  if (playerLayout === "theater" && vis.length
      && !vis.some(t => t.id === playerActiveId)) {
    playerActiveId = vis[0].id;
    renderPlayerTab();
  }
}

function stopAllPlayerPlayback() {
  for (const tileId of [...playerHls.keys()]) stopTilePlayback(tileId);
}

// Plays a tile without disturbing any other tile's stream — the Player
// tab runs every open tile live (muted) at once. opts.silent suppresses
// toasts, used for the automatic per-tick play/retry pass so a model
// that's briefly unavailable doesn't spam the user every second.
async function playTile(tileId, opts = {}) {
  const t = findPlayerTile(tileId);
  if (!t || playerHls.has(tileId) || playerPending.has(tileId)) return;
  const cd = playerCooldownUntil.get(tileId);
  if (cd && Date.now() < cd) return;
  playerPending.add(tileId);
  let r;
  try {
    // The bridge call can take a while (Stripchat resolution makes several
    // upstream requests); the race keeps a wedged call from pinning the tile
    // in "pending" forever, which would permanently stop retries.
    r = await Promise.race([
      API.preview_embedded(t.name, t.site),
      new Promise((_, rej) => setTimeout(() => rej(new Error("timed out")), 60000)),
    ]);
  } catch (e) {
    playerCooldownUntil.set(tileId, Date.now() + PLAYER_RETRY_MS);
    setTileError(tileId, "preview error — retrying…");
    try { API.client_log(`Player tile ${t.name} (${t.site}): bridge call failed — ${e && e.message}`); } catch (_) {}
    return;
  } finally {
    playerPending.delete(tileId);
  }
  if (!findPlayerTile(tileId)) return;   // tile closed while awaiting
  if (!r.ok) {
    playerCooldownUntil.set(tileId, Date.now() + PLAYER_RETRY_MS);
    setTileError(tileId, "no public stream — retrying…");
    if (!opts.silent) toast(r.error || "Preview failed.", true);
    return;
  }
  playerCooldownUntil.delete(tileId);
  clearTileError(tileId);
  if (r.mode === "external") { toast(`Preview: ${r.player} window opened.`); return; }
  const el = document.querySelector(`[data-tile="${tileId}"] .tile-video`);
  if (!el) { plog(`tile ${t.name}: no <video> element found (layout=${playerLayout})`); return; }
  let inst;
  if (window.Hls && Hls.isSupported()) {
    plog(`tile ${t.name}: url ok — attaching hls.js`);
    // Tight retry budget: a stream that doesn't play should fail fast and
    // fall back to the 30 s retry cycle, not grind for a minute in silence.
    inst = new Hls({
      liveDurationInfinity: true,
      manifestLoadingMaxRetry: 2, levelLoadingMaxRetry: 2, fragLoadingMaxRetry: 2,
      manifestLoadingRetryDelay: 1000, levelLoadingRetryDelay: 1000, fragLoadingRetryDelay: 1000,
    });
    inst.loadSource(r.url);
    inst.attachMedia(el);
    inst.once(Hls.Events.MANIFEST_PARSED, () => plog(`tile ${t.name}: manifest parsed`));
    inst.once(Hls.Events.FRAG_BUFFERED, () => plog(`tile ${t.name}: first fragment buffered`));
    el.addEventListener("playing", () => {
      plog(`tile ${t.name}: video playing ✓`);
      // 5 s later, report decode + layout facts: separates "not playing"
      // from "playing but not painting" without needing devtools.
      setTimeout(() => {
        const rc = el.getBoundingClientRect();
        plog(`tile ${t.name}: t=${el.currentTime.toFixed(1)}s decoded=${el.videoWidth}x${el.videoHeight} ` +
             `paused=${el.paused} rect=${Math.round(rc.width)}x${Math.round(rc.height)}@${Math.round(rc.x)},${Math.round(rc.y)} ` +
             `inDoc=${document.contains(el)}`);
      }, 5000);
    }, { once: true });
    let errLogged = 0;
    inst.on(Hls.Events.ERROR, (_e, data) => {
      if (errLogged < 3 || data.fatal) {
        errLogged++;
        plog(`tile ${t.name}: hls ${data.fatal ? "FATAL" : "warn"} ${data.type}/${data.details}`);
      }
      if (data.fatal) {
        if (!opts.silent) toast("Player tile playback error.", true);
        stopTilePlayback(tileId);
        playerCooldownUntil.set(tileId, Date.now() + PLAYER_RETRY_MS);
        setTileError(tileId, "playback error — retrying…");
      }
    });
  } else {
    plog(`tile ${t.name}: hls.js unavailable — native <video> fallback`);
    el.src = r.url;   // native HLS fallback
    inst = { media: el, destroy() {} };
  }
  playerHls.set(tileId, inst);
  tilePlay(el, t.name);
  // The opaque poster overlays the video; hiding it reveals playback.
  const poster = document.querySelector(`[data-tile="${tileId}"] .tile-poster`);
  if (poster) poster.hidden = true;
}

function stopTilePlayback(tileId) {
  const inst = playerHls.get(tileId);
  if (!inst) return;
  inst.destroy();
  playerHls.delete(tileId);
  const el = document.querySelector(`[data-tile="${tileId}"] .tile-video`);
  if (el) { el.pause(); el.removeAttribute("src"); el.load(); }
  const poster = document.querySelector(`[data-tile="${tileId}"] .tile-poster`);
  if (poster) poster.hidden = false;
}

const PLAYER_CAP_MSG = "Player has the max tiles open — remove one to add a new one, or raise the cap in Settings.";

function _pushPlayerTile(name, site) {
  // returns "added" | "dup" | "cap" — shared by addPlayerTile/addPlayerTilesBulk
  const key = tileKey(name, site);
  if (playerTiles.some(t => t.key === key)) return "dup";
  if (playerTiles.length >= playerMaxTiles) return "cap";
  playerTiles.push({ id: ++playerTileSeq, key, name, site });
  return "added";
}
function addPlayerTile(name, site) {
  const r = _pushPlayerTile(name, site);
  if (r === "dup") { toast("That model already has an open tile.", true); return false; }
  if (r === "cap") { toast(PLAYER_CAP_MSG, true); return false; }
  const t = playerTiles[playerTiles.length - 1];
  renderPlayerTab();
  toast(playerFilterActive() && !tileMatchesFilter(t)
    ? `▶ Added ${name} — hidden by the Player's Status/Rank filters.`
    : `▶ Added ${name} to the Player tab.`);
  playTile(t.id);
  return true;
}
function addPlayerTilesBulk(items) {
  let added = 0, cap = false;
  const newIds = [];
  for (const { name, site } of items) {
    const r = _pushPlayerTile(name, site);
    if (r === "added") { added++; newIds.push(playerTiles[playerTiles.length - 1].id); }
    else if (r === "cap") { cap = true; break; }
  }
  if (added) {
    renderPlayerTab();
    const hidden = playerFilterActive()
      ? newIds.filter(id => !tileMatchesFilter(findPlayerTile(id))).length : 0;
    toast(`▶ Added ${added} model(s) to the Player tab.`
      + (hidden ? ` (${hidden} hidden by the Status/Rank filters.)` : ""));
    for (const id of newIds) playTile(id);
  }
  if (cap) toast(PLAYER_CAP_MSG, true);
  else if (!added) toast("Those models already have open tiles.", true);
}

function closePlayerTile(tileId) {
  tileId = Number(tileId);
  stopTilePlayback(tileId);
  playerPending.delete(tileId);
  playerCooldownUntil.delete(tileId);
  playerTileErr.delete(tileId);
  const i = playerTiles.findIndex(t => t.id === tileId);
  if (i === -1) return;
  playerTiles.splice(i, 1);
  if (playerActiveId === tileId) {
    playerActiveId = playerTiles.length ? playerTiles[0].id : null;
    if (playerLayout === "theater" && !playerTiles.length) playerLayout = "grid";
  }
  renderPlayerTab();
}

function setPlayerLayoutButtons() {
  document.querySelectorAll("#player-layout [data-layout]")
    .forEach(b => b.classList.toggle("active", b.dataset.layout === playerLayout));
  $("player-strip-pos").hidden = playerLayout !== "theater";
}

function enterTheater(tileId) {
  if (!playerTiles.length) return;
  playerLayout = "theater";
  playerActiveId = (tileId != null ? Number(tileId) : playerTiles[0].id);
  setPlayerLayoutButtons();
  renderPlayerTab();
  playTile(playerActiveId);
}

function exitTheater() {
  playerLayout = "grid";
  setPlayerLayoutButtons();
  renderPlayerTab();
}

function setStripPosition(pos) {
  playerStripPos = pos;
  $("player-stage").dataset.stripe = pos;
  document.querySelectorAll("#player-strip-pos [data-stripe]")
    .forEach(b => b.classList.toggle("active", b.dataset.stripe === pos));
}

function openPlayerPicker() {
  const open = new Set(playerTiles.map(t => t.key));
  const seen = new Set();
  const list = [];
  for (const m of (S && S.models) || []) {
    if (seen.has(m.key) || open.has(m.key)) continue;
    seen.add(m.key);
    list.push({ name: m.name, site: m.site, key: m.key, status: m.status, rank: m.rank || 0 });
  }
  const stMap = (S && S.saved_status) || {};
  for (const it of savedCache.items || []) {
    const key = tileKey(it.name, it.site);
    if (seen.has(key) || open.has(key)) continue;
    seen.add(key);
    const st = stMap[it.sid];
    list.push({ name: it.name, site: it.site, key, status: st ? st[0] : "offline", rank: it.rank || 0 });
  }
  list.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
  playerPickerList = list;
  // Status/Rank live in the main toolbar now (Recorder/Saved-style — sticky
  // across opens); only the name search resets, for a fresh text search
  // each time without losing your Status/Rank scope.
  $("playerpick-filter").value = "";
  applyPlayerPickFilters();
  $("playerpick").hidden = false;
  setTimeout(() => $("playerpick-filter").focus(), 60);
}

function renderPlayerPickerList(list) {
  $("playerpick-list").innerHTML = list.length
    ? list.map(it => `
        <div class="playerpick-row" data-pp-name="${esc(it.name)}" data-pp-site="${esc(it.site)}">
          <span class="tile-name">${esc(it.name)}</span>
          <span class="tile-site">${SITE_LABEL[it.site] || it.site}</span>
        </div>`).join("")
    : `<div class="playerpick-empty">No tracked models available — add one to Recorder or Saved Models first.</div>`;
}

// Empties the Player tab only — playback stops and every tile goes away,
// but recordings, the Recorder list, and Saved Models are untouched.
function clearPlayerTiles() {
  stopAllPlayerPlayback();
  playerTiles = [];
  playerPending.clear();
  playerCooldownUntil.clear();
  playerTileErr.clear();
  playerActiveId = null;
  playerLayout = "grid";
  setPlayerLayoutButtons();
  renderPlayerTab();
  toast("Player cleared.");
}
$("player-clear").addEventListener("click", () => {
  if (!playerTiles.length) { toast("Player is already empty."); return; }
  modal("Clear Player",
        "Remove ALL tiles from the Player?\n\nOnly the Player empties — recordings, the Recorder, and Saved Models are untouched.",
        "Clear", clearPlayerTiles);
});

// ★ Fill Top Ranked — open tiles for the highest-ranked models that are
// online right now (Recorder + Saved), best rank first, up to the tile cap.
function fillTopRanked() {
  const seen = new Set(playerTiles.map(t => t.key));
  const cands = [];
  for (const m of (S && S.models) || []) {
    if (seen.has(m.key) || !m.rank) continue;
    if (m.status !== "online" && m.status !== "recording") continue;
    seen.add(m.key);
    cands.push({ name: m.name, site: m.site, rank: m.rank });
  }
  const stMap = (S && S.saved_status) || {};
  for (const it of savedCache.items || []) {
    const key = tileKey(it.name, it.site);
    if (seen.has(key) || !it.rank) continue;
    const st = stMap[it.sid];
    if (!st || (st[0] !== "online" && st[0] !== "recording")) continue;
    seen.add(key);
    cands.push({ name: it.name, site: it.site, rank: it.rank });
  }
  if (!cands.length) {
    toast("No ranked models are online right now (or they already have tiles).");
    return;
  }
  const room = playerMaxTiles - playerTiles.length;
  if (room <= 0) { toast(PLAYER_CAP_MSG, true); return; }
  cands.sort((a, b) => b.rank - a.rank || (a.name < b.name ? -1 : 1));
  addPlayerTilesBulk(cands.slice(0, room));
}
$("player-fill").addEventListener("click", fillTopRanked);
$("player-add").addEventListener("click", openPlayerPicker);
$("playerpick-cancel").addEventListener("click", () => { $("playerpick").hidden = true; });
function applyPlayerPickFilters() {
  const f = $("playerpick-filter").value.trim().toLowerCase();
  const sf = playerFilter.status;
  const rf = playerFilter.rank;
  renderPlayerPickerList(playerPickerList.filter(it =>
    (!f || it.name.toLowerCase().includes(f))
    && (!sf.size || sf.has(it.status))
    && rankMatch(it.rank, rf)));
}
$("playerpick-filter").addEventListener("input", applyPlayerPickFilters);
function updatePlayerFilterLabels() {
  const sf = playerFilter.status, rf = playerFilter.rank;
  const sBtn = $("player-msf").querySelector(".msf-btn");
  sBtn.classList.toggle("active", sf.size > 0);
  sBtn.textContent = (sf.size === 0 ? "Status: All"
    : sf.size === 1 ? "Status: " + CAP([...sf][0]) : `Status: ${sf.size}`) + " ▾";
  $("player-msf").querySelectorAll(".msf-menu label[data-v]").forEach(l => {
    const cb = l.querySelector(".cb");
    if (cb) cb.classList.toggle("on", sf.has(l.dataset.v));
  });
  const rBtn = $("player-rankf").querySelector(".msf-btn");
  rBtn.classList.toggle("active", rf !== 0);
  rBtn.textContent = (rf === 0 ? "Rank: All"
    : rf === -1 ? "Rank: Unranked" : rf === 5 ? "Rank: ★5" : `Rank: ★${rf}+`) + " ▾";
  $("player-rankf").querySelectorAll(".msf-menu label[data-v]").forEach(l => {
    const cb = l.querySelector(".cb");
    if (cb) cb.classList.toggle("on", Number(l.dataset.v) === rf);
  });
}
$("player-msf").querySelector(".msf-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("player-msf").querySelector(".msf-menu");
  const wasOpen = !menu.hidden;
  document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
  menu.hidden = wasOpen;
});
$("player-msf").querySelector(".msf-menu").addEventListener("click", (e) => {
  e.stopPropagation();
  const lab = e.target.closest("label[data-v]");
  if (!lab) return;
  const v = lab.dataset.v;
  if (v === "__clear") playerFilter.status.clear();
  else if (playerFilter.status.has(v)) playerFilter.status.delete(v);
  else playerFilter.status.add(v);
  updatePlayerFilterLabels();
  applyPlayerPickFilters();
  renderPlayerTab();
});
$("player-rankf").querySelector(".msf-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $("player-rankf").querySelector(".msf-menu");
  const wasOpen = !menu.hidden;
  document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
  menu.hidden = wasOpen;
});
$("player-rankf").querySelector(".msf-menu").addEventListener("click", (e) => {
  e.stopPropagation();
  const lab = e.target.closest("label[data-v]");
  if (!lab) return;
  const v = lab.dataset.v === "__clear" ? 0 : Number(lab.dataset.v);
  playerFilter.rank = (playerFilter.rank === v) ? 0 : v;
  updatePlayerFilterLabels();
  applyPlayerPickFilters();
  renderPlayerTab();
});
$("playerpick-list").addEventListener("click", (e) => {
  const row = e.target.closest("[data-pp-name]");
  if (!row) return;
  $("playerpick").hidden = true;
  addPlayerTile(row.dataset.ppName, row.dataset.ppSite);
});

$("player-layout").addEventListener("click", (e) => {
  const b = e.target.closest("[data-layout]");
  if (!b) return;
  if (b.dataset.layout === "theater") enterTheater(playerActiveId);
  else exitTheater();
});
$("player-strip-pos").addEventListener("click", (e) => {
  const b = e.target.closest("[data-stripe]");
  if (b) setStripPosition(b.dataset.stripe);
});
// Bottom strip: translate the mouse wheel into horizontal scrolling.
$("theater-strip").addEventListener("wheel", (e) => {
  if (playerStripPos !== "bottom" || !e.deltaY) return;
  e.preventDefault();
  $("theater-strip").scrollLeft += e.deltaY;
}, { passive: false });
$("player-stage").addEventListener("click", (e) => {
  const closeBtn = e.target.closest("[data-tile-close]");
  if (closeBtn) { closePlayerTile(closeBtn.dataset.tileClose); return; }
  const recBtn = e.target.closest("[data-tile-rec]");
  if (recBtn) {
    const t = findPlayerTile(Number(recBtn.dataset.tileRec));
    if (t) recControl(t.name, t.site, true);
    return;
  }
  const stopBtn = e.target.closest("[data-tile-stop]");
  if (stopBtn) {
    const t = findPlayerTile(Number(stopBtn.dataset.tileStop));
    if (t) recControl(t.name, t.site, false);
    return;
  }
  // Rank-star and AUTO-switch clicks are handled by the global click
  // handler — they must not ALSO count as a tile click, which would yank
  // the layout into Theater mode mid-interaction.
  if (e.target.closest(".stars") || e.target.closest(".tile-auto")) return;
  const tileEl = e.target.closest("[data-tile]");
  if (!tileEl) return;
  const tileId = Number(tileEl.dataset.tile);
  if (playerLayout !== "theater" || tileId !== playerActiveId) enterTheater(tileId);
});

/* ══ toolbar wiring ══ */
function needSel(tab) {
  const keys = workset(tab);
  if (!keys.length) { toast("Select or check model(s) first.", true); return null; }
  return keys;
}
$("b-rec").addEventListener("click", () => { const k = needSel("rec"); if (k) API.record(k, true); });
$("b-stop").addEventListener("click", () => { const k = needSel("rec"); if (k) API.record(k, false); });
$("b-auto").addEventListener("click", () => { const k = needSel("rec"); if (k) API.toggle_auto(k); });
$("b-remove").addEventListener("click", () => {
  const k = needSel("rec");
  if (k) modal("Remove models", `Remove ${k.length} model(s) from the Recorder?\n(Recording models are skipped.)`,
               "Remove", () => { API.remove(k); checked.rec.clear(); sel.rec.clear(); });
});
$("b-remoff").addEventListener("click", async () => {
  const r = await API.offline_count();
  if (!r.count) { toast("No OFFLINE models to remove."); return; }
  modal("Remove Offline", `Remove every model currently OFFLINE (${r.count})?\nRecording/private/checking rows and Saved Models are untouched.`,
        "Remove", () => API.remove_offline());
});
$("b-addsaved").addEventListener("click", () => { const k = needSel("rec"); if (k) API.add_saved(k); });
$("b-clear").addEventListener("click", () =>
  modal("Clear recorder", "Stop everything and remove ALL models from the Recorder?\n\nThis pauses both monitors, force-stops every active download, clears AUTO, and empties the Recorder list. Your Saved list is kept, but the Saved scanner is paused so nothing resumes in the background.",
        "Clear", () => { API.clear_recorder(); checked.rec.clear(); sel.rec.clear(); }));
$("b-stopall").addEventListener("click", () =>
  modal("Stop all downloads", "Force-stop ALL active downloads and uncheck AUTO on every model?",
        "Stop all", () => API.stop_all()));
$("btn-monitor").addEventListener("click", async () => {
  await API.set_monitor("recorder", $("btn-monitor").classList.contains("go"));
  tick();
});
$("btn-scanner").addEventListener("click", async () => {
  await API.set_monitor("saved", $("btn-scanner").classList.contains("go"));
  tick();
});

$("b-export").addEventListener("click", async () => {
  const r = await API.saved_export();
  if (r.cancelled) return;
  if (r.ok) modal("Export complete", r.msg, "OK", () => {}, true);
  else toast(r.error, true);
});
$("b-import").addEventListener("click", async () => {
  const r = await API.saved_import();
  if (r.cancelled) return;
  if (r.ok) { modal("Import complete", r.msg, "OK", () => {}, true); savedCache.version = -1; }
  else toast(r.error, true);
});
$("b-savedadd").addEventListener("click", () => {
  $("addsaved-input").value = "";
  $("addsaved").hidden = false;
  setTimeout(() => $("addsaved-input").focus(), 60);
});
$("addsaved-cancel").addEventListener("click", () => { $("addsaved").hidden = true; });
$("addsaved-ok").addEventListener("click", doAddSaved);
$("addsaved-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doAddSaved(); });
async function doAddSaved() {
  const raw = $("addsaved-input").value.trim();
  if (!raw) { toast("Enter a username or link.", true); return; }
  $("addsaved").hidden = true;
  const r = await API.saved_add(raw, $("addsaved-site").value);
  toast(r.ok ? `⭐ Added ${r.name} (${r.site}) to Saved Models` : r.error, !r.ok);
}

/* ══ linked identities dialog ══ */
async function trackedLinkKeys() {
  // Every tracked model as "site:name" — Recorder (from the live snapshot)
  // plus Saved (from the bridge, so it works before the tab was ever opened).
  const seen = new Set();
  for (const m of (S && S.models) || []) seen.add(`${m.site}:${m.name}`.toLowerCase());
  try {
    const r = await API.saved_list();
    for (const it of r.items) seen.add(`${it.site}:${it.name}`.toLowerCase());
  } catch {}
  return [...seen].sort();
}

/* type-ahead cache: [{key: "site:name", label: "name · SITE"}] */
let linkOpts = [];
async function refreshLinkOpts() {
  linkOpts = (await trackedLinkKeys()).map(k => ({ key: k, label: lnkLabel(k) }));
}

/* Attach a type-ahead dropdown to an input. getOpts() → filtered linkOpts
   subset; allowNew adds a "➕ add & link" row for unknown usernames. Picking
   a row stores the model key in input.dataset.key; typing clears it. */
function comboAttach(input, getOpts, allowNew) {
  const list = document.createElement("div");
  list.className = "combo-list";
  list.hidden = true;
  input.parentElement.appendChild(list);
  input.addEventListener("input", () => {
    input.dataset.key = "";
    const q = input.value.trim().toLowerCase();
    if (!q) { list.hidden = true; return; }
    const matches = getOpts().filter(o => o.label.toLowerCase().includes(q)).slice(0, 40);
    let html = matches.map(o =>
      `<div class="combo-it" data-k="${esc(o.key)}">${esc(o.label)}</div>`).join("");
    if (allowNew && /^[a-z0-9_.-]+$/.test(q)
        && !matches.some(o => o.key.split(":").slice(1).join(":") === q))
      html += `<div class="combo-it combo-new" data-new="${esc(q)}">➕ add “${esc(q)}” to Saved &amp; link</div>`;
    list.innerHTML = html || `<div class="combo-none">no match</div>`;
    list.hidden = false;
  });
  list.addEventListener("mousedown", (e) => {
    const it = e.target.closest(".combo-it");
    if (!it) return;
    e.preventDefault();
    if (it.dataset.k) { input.value = it.textContent; input.dataset.key = it.dataset.k; }
    else if (it.dataset.new !== undefined) { input.value = it.dataset.new; input.dataset.key = "__new__"; }
    list.hidden = true;
  });
  input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
}

function applyLinksFilter() {
  const q = $("links-filter").value.trim().toLowerCase();
  for (const row of document.querySelectorAll("#links-groups .lnk-group, #links-suggests .lnk-group"))
    row.style.display = (!q || row.textContent.toLowerCase().includes(q)) ? "" : "none";
}

async function renderLinksDlg() {
  const data = await API.links();
  const groups = $("links-groups");
  groups.innerHTML = data.links.length
    ? data.links.map(g => `<div class="lnk-group">${g.map(k =>
        `<span class="vip-chip">${esc(lnkLabel(k))}
           <button data-unlink="${esc(k)}" title="Unlink this account">✕</button></span>`)
        .join('<span class="lnk-eq">=</span>')}</div>`).join("")
    : `<div class="lnk-empty">No linked models yet — link two accounts below.</div>`;
  const sug = $("links-suggests");
  const n = data.suggestions.length;
  sug.innerHTML = n
    ? `<div class="lnk-subhead">Same username on several sites — same person?
         <button class="btn lnk-linkall" id="links-linkall" data-n="${n}">🔗 Link all ${n}</button></div>` +
      data.suggestions.map(s => `<div class="lnk-group">
          <span class="lnk-sugname">${esc(s.name)}<small> · ${
            s.keys.map(k => SITE_SHORT[k.split(":")[0]] || "?").join(" + ")
          }</small></span>
          <button class="btn" data-sug-link="${esc(s.keys.join("|"))}">🔗 Link</button>
          <button class="btn" data-sug-ign="${esc(s.keys.join("|"))}">Ignore</button>
        </div>`).join("")
    : "";
  await refreshLinkOpts();
  applyLinksFilter();
}

function linksChanged() {
  savedCache.version = -1;            // refresh 🔗 markers in the saved table
  renderLinksDlg();
}

$("b-links").addEventListener("click", () => {
  $("linksdlg").hidden = false;
  renderLinksDlg();
});
$("links-close").addEventListener("click", () => { $("linksdlg").hidden = true; });
/* the list can be long — also close on Esc or a click on the backdrop */
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("lnkedit").hidden) { $("lnkedit").hidden = true; return; }
  if (!$("linksdlg").hidden) $("linksdlg").hidden = true;
});
$("linksdlg").addEventListener("mousedown", (e) => {
  if (e.target === $("linksdlg")) $("linksdlg").hidden = true;
});
$("links-filter").addEventListener("input", applyLinksFilter);
comboAttach($("links-a"), () => linkOpts, false);
comboAttach($("links-b"), () => linkOpts, false);
$("links-add").addEventListener("click", async () => {
  const a = $("links-a").dataset.key, b = $("links-b").dataset.key;
  if (!a || !b) { toast("Type and pick two models to link.", true); return; }
  if (a === b) { toast("Pick two different models.", true); return; }
  const r = await API.link_models([a, b]);
  toast(r.ok ? `🔗 Linked ${lnkLabel(a)} = ${lnkLabel(b)}` : r.error, !r.ok);
  if (r.ok) {
    $("links-a").value = ""; $("links-a").dataset.key = "";
    $("links-b").value = ""; $("links-b").dataset.key = "";
    linksChanged();
  }
});
$("linksdlg").addEventListener("click", async (e) => {
  const un = e.target.closest("[data-unlink]");
  if (un) {
    const r = await API.unlink_model(un.dataset.unlink);
    toast(r.ok ? `Unlinked ${lnkLabel(un.dataset.unlink)}` : r.error, !r.ok);
    if (r.ok) linksChanged();
    return;
  }
  const sl = e.target.closest("[data-sug-link]");
  if (sl) {
    const r = await API.link_models(sl.dataset.sugLink.split("|"));
    toast(r.ok ? "🔗 Linked" : r.error, !r.ok);
    if (r.ok) linksChanged();
    return;
  }
  const si = e.target.closest("[data-sug-ign]");
  if (si) {
    await API.link_ignore(si.dataset.sugIgn.split("|"));
    renderLinksDlg();
    return;
  }
  const la = e.target.closest("#links-linkall");
  if (la) {
    const n = la.dataset.n;
    modal("Link all suggestions",
          `Link all ${n} same-username groups as one person each? ` +
          `Ranks sync within each group (highest wins). You can unlink ` +
          `any mistake afterwards.`,
          "Link all", async () => {
      const r = await API.link_all_suggestions();
      toast(r.ok ? `🔗 Linked ${r.linked} group(s)` : (r.error || "Failed"), !r.ok);
      if (r.ok) linksChanged();
    });
  }
});

/* ══ per-model link editor (right-click → Edit Links…) ══ */
const LINK_SITES = ["chaturbate", "stripchat", "camsoda", "myfreecams"];
let lnkeditKey = null;

async function openLinkEditor(rawKey) {
  let k = String(rawKey);
  if (k.startsWith("saved:")) k = k.slice(6);
  k = k.toLowerCase();
  lnkeditKey = k;
  const site = k.split(":")[0];
  const name = k.split(":").slice(1).join(":");
  $("lnkedit-title").textContent = `🔗 ${name} — linked accounts`;
  await refreshLinkOpts();
  let group = [k];
  try {
    const data = await API.links();
    group = data.links.find(g => g.includes(k)) || [k];
  } catch {}
  let html = "";
  for (const s of LINK_SITES) {
    if (s === site) {
      html += `<div class="lnked-row"><span class="lnked-site">${SITE_SHORT[s]}</span>
        <span class="lnked-self">${esc(name)} <small>(this model)</small></span></div>`;
    } else {
      const alias = group.find(g2 => g2.startsWith(s + ":")) || "";
      html += `<div class="lnked-row"><span class="lnked-site">${SITE_SHORT[s]}</span>
        <span class="combo-wrap"><input data-site="${s}"
          value="${esc(alias ? lnkLabel(alias) : "")}" data-key="${esc(alias)}"
          placeholder="type to search…" autocomplete="off" spellcheck="false"></span></div>`;
    }
  }
  $("lnkedit-rows").innerHTML = html;
  $("lnkedit-rows").querySelectorAll("input[data-site]").forEach(inp =>
    comboAttach(inp, () => linkOpts.filter(o => o.key.startsWith(inp.dataset.site + ":")), true));
  $("lnkedit").hidden = false;
}

$("lnkedit-cancel").addEventListener("click", () => { $("lnkedit").hidden = true; });
$("lnkedit").addEventListener("mousedown", (e) => {
  if (e.target === $("lnkedit")) $("lnkedit").hidden = true;
});
$("lnkedit-save").addEventListener("click", async () => {
  const slots = {};
  for (const inp of $("lnkedit-rows").querySelectorAll("input[data-site]")) {
    let v;
    if (inp.dataset.key && inp.dataset.key !== "__new__")
      v = inp.dataset.key.split(":").slice(1).join(":");
    else
      v = inp.value.split("·")[0].trim().toLowerCase();  // raw name → auto-add
    slots[inp.dataset.site] = v;
  }
  const r = await API.apply_link_editor(lnkeditKey, slots);
  if (r.ok) {
    toast(`🔗 Links updated — ${r.group.length} account(s) in the group`);
    $("lnkedit").hidden = true;
    savedCache.version = -1;
  } else toast(r.error || "Failed", true);
});

/* clicking a row's 🔗 marker opens the editor for that model */
document.addEventListener("click", (e) => {
  const l = e.target.closest(".row .lnk");
  if (!l) return;
  const row = l.closest(".row[data-key]");
  if (row) { e.stopPropagation(); openLinkEditor(row.dataset.key); }
});

/* filters + sorting */
$("rec-filter").addEventListener("input", (e) => { filter.rec = e.target.value.trim(); retab("rec"); });
$("saved-filter").addEventListener("input", (e) => { filter.saved = e.target.value.trim(); rebuildSavedDisplay(); });

/* Multi-select status filters (pick any combination, e.g. Online + Recording). */
const CAP = (s) => s.charAt(0).toUpperCase() + s.slice(1);
function updateMsfLabel(tab) {
  const set = statusFilter[tab];
  const root = $(`${tab}-msf`);
  const btn = root.querySelector(".msf-btn");
  btn.classList.toggle("active", set.size > 0 || xsiteFilter[tab]);
  btn.textContent = (set.size === 0 ? "Status: All"
    : set.size === 1 ? "Status: " + CAP([...set][0])
    : `Status: ${set.size}`) + (xsiteFilter[tab] ? " +" : "") + " ▾";
  root.querySelectorAll(".msf-menu label[data-v]").forEach(l => {
    const cb = l.querySelector(".cb");
    if (!cb) return;
    cb.classList.toggle("on", l.dataset.v === "__hidexrec"
      ? xsiteFilter[tab] : set.has(l.dataset.v));
  });
}
for (const tab of ["rec", "saved"]) {
  const root = $(`${tab}-msf`);
  const menu = root.querySelector(".msf-menu");
  root.querySelector(".msf-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    const wasOpen = !menu.hidden;
    document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
    menu.hidden = wasOpen;
  });
  menu.addEventListener("click", (e) => {
    e.stopPropagation();
    const lab = e.target.closest("label[data-v]");
    if (!lab) return;
    const v = lab.dataset.v;
    if (v === "__clear") { statusFilter[tab].clear(); xsiteFilter[tab] = false; }
    else if (v === "__hidexrec") xsiteFilter[tab] = !xsiteFilter[tab];
    else if (statusFilter[tab].has(v)) statusFilter[tab].delete(v);
    else statusFilter[tab].add(v);
    updateMsfLabel(tab);
    if (tab === "rec") retab("rec"); else rebuildSavedDisplay();
  });
}
/* Rank filter (single-select): show only rows ranked ≥ N stars, or unranked. */
function updateRankfLabel(tab) {
  const rf = rankFilter[tab];
  const root = $(`${tab}-rankf`);
  const btn = root.querySelector(".msf-btn");
  btn.classList.toggle("active", rf !== 0);
  btn.textContent = (rf === 0 ? "Rank: All"
    : rf === -1 ? "Rank: Unranked"
    : rf === 5 ? "Rank: ★5"
    : `Rank: ★${rf}+`) + " ▾";
  root.querySelectorAll(".msf-menu label[data-v]").forEach(l => {
    const cb = l.querySelector(".cb");
    if (cb) cb.classList.toggle("on", Number(l.dataset.v) === rf);
  });
}
for (const tab of ["rec", "saved"]) {
  const root = $(`${tab}-rankf`);
  const menu = root.querySelector(".msf-menu");
  root.querySelector(".msf-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    const wasOpen = !menu.hidden;
    document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
    menu.hidden = wasOpen;
  });
  menu.addEventListener("click", (e) => {
    e.stopPropagation();
    const lab = e.target.closest("label[data-v]");
    if (!lab) return;
    const v = lab.dataset.v === "__clear" ? 0 : Number(lab.dataset.v);
    rankFilter[tab] = (rankFilter[tab] === v) ? 0 : v;   // re-click clears
    menu.hidden = true;
    updateRankfLabel(tab);
    if (tab === "rec") retab("rec"); else rebuildSavedDisplay();
  });
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".msf"))
    document.querySelectorAll(".msf-menu").forEach(m => m.hidden = true);
});
for (const [headId, tab] of [["rec-head", "rec"], ["saved-head", "saved"]]) {
  $(headId).addEventListener("click", (e) => {
    const col = e.target.closest(".sortable");
    if (!col) return;
    const c = col.dataset.col;
    if (sort[tab].col === c) sort[tab].dir = -sort[tab].dir;
    else sort[tab] = { col: c, dir: 1 };
    if (tab === "rec") retab("rec"); else rebuildSavedDisplay();
  });
}

/* add model / header buttons */
$("btn-add").addEventListener("click", addModel);
$("add-name").addEventListener("keydown", (e) => { if (e.key === "Enter") addModel(); });
async function addModel() {
  if (!API) return;
  const raw = $("add-name").value.trim();
  if (!raw) { toast("Enter a model username or link.", true); return; }
  const res = await API.add_model(raw, $("add-site").value);
  if (res.ok) {
    $("add-name").value = "";
    toast(`Added ${res.name} (${res.site})`);
    warnCrossSiteAdd(res.name, res.site);
    tick();
  } else toast(res.error || "Could not add model.", true);
}
// Add-flow variant of warnCrossSiteKeys: the model isn't in the snapshot
// yet, so its aka group comes from the links payload instead.
async function warnCrossSiteAdd(name, site) {
  try {
    const key = `${site}:${String(name).toLowerCase()}`;
    const r = await API.links();
    const g = (r.links || []).find(x => x.includes(key));
    if (!g) return;
    const ls = linkedState(g.filter(k => k !== key));
    if (ls.rec.length)
      toast(`⚠ ${name} is already recording as ${ls.rec.map(lnkLabel).join(", ")}.`, "warn");
    else if (ls.dup.length)
      toast(`⚠ ${name} is already in the Recorder as ${ls.dup.map(lnkLabel).join(", ")}.`, "warn");
  } catch (_) { /* informational only — never block the add */ }
}
$("btn-term").addEventListener("click", () => {
  const n = (S && S.active_recordings) || 0;
  if (n > 0)
    modal("Terminate Scr33nX",
          `${n} recording(s) still active.\n\nForce-terminate now? Their final segments will be dropped.`,
          "Terminate", () => API.terminate());
  else API.terminate();
});
$("btn-clearlog").addEventListener("click", () => { $("logpane").innerHTML = ""; });
$("update-pill").addEventListener("click", () =>
  API.open_url("https://github.com/00Sylar/Scr33nX/releases/latest"));

/* tabs */
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => {
  document.querySelector(".tab.active").classList.remove("active");
  t.classList.add("active");
  document.querySelector(".panel.active").classList.remove("active");
  document.querySelector(`.panel[data-panel="${t.dataset.tab}"]`).classList.add("active");
  if (t.dataset.tab === "settings") { loadSettings(); loadVip(); }
  if (t.dataset.tab === "output") loadPipeFields();
  if (t.dataset.tab === "saved") { savedCache.version = -1; renderSaved(); }
  if (t.dataset.tab === "player") {
    loadPlayerTab(); renderPlayerTab();
    playerCooldownUntil.clear();   // fresh visit → retry failed tiles now
    patchPlayerStatuses();
  }
  // Leaving the Player tab keeps every tile streaming in the background —
  // switching Recorder ⇄ Player must never restart the streams.
}));

/* ══ privacy mode (idle starfield cover) ══ */
let privacyOn = false, covered = false, lastActivity = Date.now(), starsRAF = 0;
function privacyApply(on) {
  privacyOn = !!on;
  if (!privacyOn && covered) uncover();
}
["mousemove", "keydown", "mousedown", "wheel"].forEach(ev =>
  document.addEventListener(ev, () => {
    lastActivity = Date.now();
  }, { passive: true }));
setInterval(() => {
  if (privacyOn && !covered && Date.now() - lastActivity > 3000) cover();
}, 500);
function cover() {
  covered = true;
  $("privacy").hidden = false;
  $("privacy-exit").hidden = true;
  startStars();
}
function uncover() {
  covered = false;
  $("privacy").hidden = true;
  cancelAnimationFrame(starsRAF);
  lastActivity = Date.now();
}
$("privacy").addEventListener("click", (e) => {
  if (e.target.closest(".privacy-exit")) return;
  $("privacy-exit").hidden = false;
});
$("privacy-stay").addEventListener("click", () => { $("privacy-exit").hidden = true; });
$("privacy-leave").addEventListener("click", uncover);
function startStars() {
  const cv = $("stars");
  const ctx2 = cv.getContext("2d");
  cv.width = innerWidth;
  cv.height = innerHeight;
  const stars = Array.from({ length: 130 }, () => ({
    x: Math.random() * cv.width, y: Math.random() * cv.height,
    r: Math.random() * 1.4 + 0.3, s: Math.random() * 0.25 + 0.05,
    tw: Math.random() * Math.PI * 2,
  }));
  (function frame() {
    if (!covered) return;
    ctx2.fillStyle = "#03030a";
    ctx2.fillRect(0, 0, cv.width, cv.height);
    for (const st of stars) {
      st.y += st.s;
      st.tw += 0.03;
      if (st.y > cv.height) { st.y = -2; st.x = Math.random() * cv.width; }
      ctx2.globalAlpha = 0.5 + 0.5 * Math.sin(st.tw);
      ctx2.fillStyle = "#cfd2e0";
      ctx2.beginPath();
      ctx2.arc(st.x, st.y, st.r, 0, 7);
      ctx2.fill();
    }
    ctx2.globalAlpha = 1;
    starsRAF = requestAnimationFrame(frame);
  })();
}

/* ══ modal / prompt / toast ══ */
function modal(title, text, okLabel, onOk, infoOnly = false) {
  $("modal-title").textContent = title;
  $("modal-text").textContent = text;
  $("modal-ok").textContent = okLabel;
  $("modal-cancel").style.display = infoOnly ? "none" : "";
  $("modal").hidden = false;
  $("modal-ok").onclick = () => { $("modal").hidden = true; onOk(); };
  $("modal-cancel").onclick = () => { $("modal").hidden = true; };
}
function askText(title, text, onOk, password = false, onCancel = null) {
  $("prompt-title").textContent = title;
  $("prompt-text").textContent = text;
  const inp = $("prompt-input");
  inp.type = password ? "password" : "text";
  inp.value = "";
  $("prompt").hidden = false;
  setTimeout(() => inp.focus(), 60);
  const finish = (v) => { $("prompt").hidden = true; if (v === null) { if (onCancel) onCancel(); } else onOk(v); };
  $("prompt-ok").onclick = () => finish(inp.value.trim());
  $("prompt-cancel").onclick = () => finish(null);
  inp.onkeydown = (e) => { if (e.key === "Enter") finish(inp.value.trim()); };
}
let toastTimer = null;
function toast(msg, kind = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (kind === "warn" ? " warn" : kind ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3600);
}
