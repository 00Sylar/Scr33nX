# Release History

This is a readable summary. The authoritative, full changelog is `docs/CHANGELOG.md`
in the repository, and each version is also a
[GitHub Release](https://github.com/00Sylar/Scr33nX/releases).

---

## V2.8 — 2026‑10‑03

**Proxy support — because Chaturbate can now refuse a whole network.** Since
the night of 2026‑10‑02, Chaturbate's video servers started refusing every
video request from some internet providers (seen from a Dominican ISP), over
both connection types. The streams resolve, then every video request is
answered with `403 Forbidden` and nothing records. Chaturbate's own website
shows it too: its player falls back to a slow slideshow of still images.

This is different from V2.6 and V2.7. Those were the servers refusing one
connection type, which Scr33nX can switch around on its own. This time the
whole network is refused no matter what Scr33nX sends, while the same stream
plays fine through a VPN or Cloudflare WARP. So the fix is to reach Chaturbate
from a different address.

**Added**
- **Settings → 🌐 Proxy.** Send Scr33nX's traffic for a site through a
  SOCKS5/HTTP proxy — one default for everything, or one per site (Chaturbate,
  Stripchat, Camsoda, MyFreeCams), or `direct` to opt a site out. Each row has
  a **Test** button. Only Scr33nX's traffic for that site is affected; the
  rest of your PC is untouched. Free option: Cloudflare WARP in proxy mode —
  see [[Settings]].
- New dependency `PySocks` — run `pip install -r requirements.txt` after
  updating.

---

## V2.7 — 2026‑10‑02

**Chaturbate fix AGAIN!** Chaturbate changed its connection rules a second
time: its video servers now refuse IPv4 (the opposite of what broke things in
V2.6), so the V2.6 fix made every Chaturbate stream fail instead of helping.
V2.7 no longer picks a side.

**Fixed**
- **Chaturbate recording and Player tiles work again.** Scr33nX tries one
  connection type; if Chaturbate's servers refuse it, it switches to the other
  and remembers which one works. If Chaturbate flips again, it adapts on its
  own, with no new release needed. Other sites are unchanged.

---

## V2.6 — 2026‑09‑28

**Chaturbate is back, and faster.** Around 2026‑09‑23 Chaturbate's video
servers started refusing IPv6 connections, which broke every Chaturbate
stream on most home connections. V2.6 fixes that and removes the old
one-request-at-a-time throttle that made Chaturbate the slowest site to load.

**Fixed**
- **Chaturbate recording and Player tiles work again.** Scr33nX now connects
  to Chaturbate over IPv4. Before, models resolved but every playlist was
  refused (`manifestLoadError`, nothing recorded).
- **Uploaded files no longer get stranded in the converted folder.** The
  post-upload delete is retried for about 15 s. If it still fails, the file is
  moved to a **`stuck\`** sub-folder instead of being silently skipped
  forever.

**Changed**
- **Chaturbate is much faster.**
  - A 16-tile Player wall resolves in ~1 s (was ~24 s).
  - A full saved-models scan takes ~18 s (was ~2.5 min).
  - With 45 or fewer saved Chaturbate models, each one is checked directly,
    so their tiles open instantly.
  - If Chaturbate rate-limits, Scr33nX backs off automatically and keeps each
    model's last status instead of flipping her OFFLINE.
  - Retrying a tile, or opening one for a model that's already recording, no
    longer fails.
- **Player Status ▾ / Rank ▾ filters now filter the open tiles**, not just
  the ＋ Add Tile picker. Hidden tiles keep streaming and reappear instantly
  when the filter is cleared.

---

## V2.5 — 2026‑08‑25

**Faster ways to find and manage models in the Player and Saved Models tabs.**

**Added**
- **"Hide cross-site recording" filter (Saved Models tab)** — a checkbox at
  the bottom of the Status ▾ dropdown hides any model whose linked (🔗)
  identity is already recording on another site. Combine it with
  **Status: Online** to see only models that are up and not already being
  captured under another account.
- **Status ▾ / Rank ▾ filters for the Player's ＋ Add Tile picker** — they
  sit in the Player toolbar, left of ★ Fill Top Ranked, and narrow the picker
  to, say, ★4+ online models only. They persist until cleared, like the
  Recorder/Saved filters. *(Since V2.6 they also filter the open tiles.)*
- **Right-click menu on Player tiles** — the same menu as the model's
  Recorder/Saved row (start/stop recording, rank, links, VIP, copy URL, open
  in browser), plus a Player-only **✕ Close Tile** that removes just the
  tile.

---

## V2.4 — 2026‑08‑25

**Stripchat is back.** Partway through V2.3, Stripchat changed its API. That
broke the Player, Preview and recording for every Stripchat model, while
status still showed online. This release fixes that and speeds up opening
Chaturbate models in the Player.

**Fixed**
- **Stripchat models record and preview again.** Stripchat's bot filter
  started answering the stream-id lookup with HTTP 418. The id now comes from
  the model page, so the lightweight browserless path works again instead of
  dropping to the slower Playwright fallback (or not recording at all).
- **Stripchat online checks** now point at the master playlist that actually
  serves. The old URL had started returning 404.

**Changed**
- **Faster Player opens for Chaturbate and Stripchat.** A tile reuses the
  stream URL the online check already fetched (up to 30 s old) instead of
  resolving it again. Online/offline detection is unchanged.

---

## V2.3 — 2026‑07‑25

**Cross-site recording warnings, now in the app itself.** V2.2 taught the
browser extension to flag when a linked model is already being recorded
elsewhere; V2.3 brings that same protection into the app and turns it from
a passive warning into an active confirmation before a recording starts —
plus a Player tab refinement.

**Added**
- **In‑app cross‑site warnings** — the 🔗 marker on Recorder / Saved rows
  and Player tiles escalates live: amber **🔗⧉** (same person also in the
  Recorder on another site), red **REC·SITE** (a linked account is
  recording now), or **REC ×2** (both are). Hover for the exact accounts.
- **"Record anyway?" confirmation** — starting a recording on a model whose
  linked account is already being recorded now asks first, in both the app
  (a confirm dialog) and the browser extension (an inline **Yes, record /
  Cancel** swap on the Start button). Recording is still never silently
  blocked — just gated behind one click. Adding without starting still just
  shows a heads‑up toast, logged to the Activity Log.
- **AUTO toggle on the Player's Theater tile** — flip Auto‑Record for the
  model you're watching without leaving the Player; dimmed until the model
  is in the Recorder. Theater tile only.

---

## V2.2 — 2026‑07‑20

**Linked identities.** Scr33nX now understands when the same model has
accounts on multiple sites — link them once and her star rank stays in sync
everywhere, with a warning (never a block) if you're about to double‑record
her under a different username.

**Added**
- **🔗 Linked identities (aka groups)** — link a model's accounts across
  sites from the web UI, the bot/CLI (`link`/`unlink`/`links`), or the API
  (`POST /link`, `POST /unlink`, `GET /links`). Ranks sync across the group;
  the extension warns (never blocks) if a linked account is already
  recording elsewhere. A link editor supports bulk linking and
  same‑username suggestions.
- **Browser extension: dynamic badges** — the toolbar icon shows live REC/
  ON/OFF/★ state for the current tab (or an active‑recording count
  elsewhere); browse pages get the same badges on thumbnails of tracked
  models. Right‑click any model link/thumbnail to add it without opening
  the page.
- **`GET /models` API** — bulk snapshot of every tracked model in one call
  (feeds the extension badges); wrapped by `scr33nx_ctl.py` / the bot as
  `models [--site] [--recording] [--online] [--min-rank]`.
- **Config backups + corruption recovery** — the settings file keeps 3
  rotating backups; a corrupt config now restores the newest good backup
  automatically instead of silently wiping your models and ranks.
- **Model audit log** (`models_audit.log`) — every add/remove/rank/VIP/
  import/export/Clear Recorder event, timestamped and source‑tagged.
- **Optional local‑API token** — lock down port 5200 with a token
  (Settings → Local API); supported by the extension and `scr33nx_ctl.py`.
- **Rank filter**, **★ Fill Top Ranked** (Player tab), star ranks visible in
  the preview/Player/Theater tiles, Max Player tiles raised to 100, and a
  **🧹 Clear Player** button.

**Fixed**
- **Low‑disk guard could leave orphaned ffmpeg processes running and
  downloading** even after the UI reported 0 active recordings — caused by
  a race between the Recorder and Saved monitor loops when a model was
  tracked in both. Session start/split/restart is now serialized per model,
  and the guard's "stop everything" sweep now force‑kills every recorder
  process ever launched, not just the ones currently tracked.
- Telegram-pipeline splits now honor **Max File Size** instead of a
  hardcoded ~3.8 GB cap; Esc no longer hangs the Telegram login prompt;
  Convert no longer retries a broken `.ts` forever; Clear Recorder now
  waits for every ffmpeg process to actually exit before clearing the list;
  a Recorder-list race that could intermittently render it empty is fixed.

## V2.1 — 2026‑07‑09

**The ▶ Player tab.** Watch several live models at once inside the app, and
record straight from what you're watching.

**Added**
- **▶ Player tab** *(default UI)* — models open as live **muted** tiles in a
  **Grid** wall; click a tile for **Theater** mode (that tile large + a
  Bottom/Side thumbnail strip, all still playing). Add tiles via **+ Add
  Tile** or right‑click → **▶ Add to Player**; capped by a new **Max Player
  tiles** setting (1–20, default 9).
- **▶ REC / ⏹ Stop from the Player and the embedded preview** — next to a
  live status badge. Starting a model that isn't in the Recorder adds it
  automatically.
- **Configurable low‑disk thresholds** — separate **Stop below** / **Resume
  at** GB values (defaults 20/40) replace the hardcoded 20 GB cutoff, in both
  UIs.

**Changed**
- All six navigation tabs now have matching icons; Theater layout always
  keeps the thumbnail strip on screen; switching tiles no longer rebuffers
  streams.

**Fixed**
- Star ratings no longer silently lost when rating a model right after
  adding it.
- Low‑disk guard no longer start/stop loops around the threshold
  (stop/resume hysteresis).
- Telegram uploads: failed sends are no longer marked as uploaded and
  forgotten — they log the real error and retry (up to 3×); files still
  being written by ffmpeg are never queued; wrongly‑marked files from before
  will upload on the next run.
- A long list of Player‑tile bugs: invisible/duplicate tiles, streams
  playing into hidden elements, silent failures that never retried, and
  background streams continuing after leaving the tab.

## V2.0 — 2026‑07‑07

**The web UI redesign.** A complete visual and structural rebuild — a modern
native window (Windows WebView2) becomes the default interface, with the
original Tk UI kept as a fallback. Same recording engine, same config files,
zero feature loss.

**Added**
- **New default UI** — full Recorder tab (toolbar, right‑click menus with
  per‑model quality/rank submenus, column sorting, search + multi‑condition
  status filters, checkboxes, Shift/Ctrl/marquee selection), Saved Models
  (virtual‑scrolled for thousands of rows, scanner, import/export), Output/
  Upload (pipeline, wizard, re‑auth, in‑app Telegram login prompts), Activity
  Log, full Settings + System Check, stream preview (external **and** a new
  built‑in in‑app player), Privacy Mode, tray, update check, single‑instance
  lock, and the identical local API.
- **Desktop‑style marquee selection** and **collapsible per‑site groups**
  ([+]/[−]) on the model tables.
- **Right‑click ▶ Add to Recorder & Start Recording** on an online Saved model.
- **Notifications box + 🌟 VIP List** — per‑type notification toggles, a toast
  duration slider, and a VIP list that restricts per‑model notifications to
  models you've starred via right‑click.

**Changed**
- **`Scr33nX.bat` now launches the new UI by default.** The classic UI is
  unchanged, available via `Scr33nX-Classic.bat` / `--classic`.
- `pywebview` is now a required dependency.

**Fixed**
- **False "dropped segments" toast on a normal offline** — the toast is now
  deferred a few seconds and only fires if the model is still recording.
- **Telegram uploads no longer wait for the whole conversion batch** — Convert
  and Upload run as fully independent workers.

## V1.6 — 2026‑07‑04

**Added**
- **⛔ Low‑disk guard** (off by default) — stops all recordings and blocks new
  ones under 20 GB free on the output drive; auto‑recovers.
- **Rank misclick guard** — changing/clearing an *existing* star rank asks for
  confirmation first; rating an unranked model stays one‑click.
- **Single‑instance lock** — a second Scr33nX now shows "You can only open
  one instance of this app" and closes itself, instead of silently
  corrupting the first instance's models/ranks on save.
- **✕ Remove Offline** toolbar button — removes every currently‑OFFLINE model
  from the Recorder in one click (confirms first).

**Changed**
- Launcher renamed `StreamRecorder.bat` → `Scr33nX.bat`.

**Fixed**
- **Faster RECORDING → offline detection** (~25 s instead of ~75 s) via a
  stall probe that asks the resolver directly instead of waiting out the
  full timeout.
- **Stripchat recordings no longer get stuck on RECORDING** when the CDN
  swaps in an advert‑loop placeholder after the model goes offline.
- **Stall detection now also covers recordings that never create their
  output file.**
- **Column sorting no longer resets** while a status filter is active.

## V1.5 — 2026‑06‑28

**Added**
- **▶ Stream preview (mpv / VLC / ffplay)** — right‑click a model → Preview
  to watch it live, external (own process) or embedded in‑app, through the
  same local relay the recorder uses.
- **Choose which browser "Open in Browser" uses** — pick once, with a
  *Remember my choice* option; change it later in Settings.
- **Telegram Setup Wizard** — guided first‑time pipeline setup.
- **Split the pipeline into independent Convert and Upload stages**, with a
  live **● STAND BY** mode — tick/untick either at any time, nothing in
  flight is ever interrupted.
- **🔍 System Check** (Settings) — detects ffmpeg, ffplay, mpv, VLC,
  python‑vlc, python‑mpv, tdjson, Playwright Chromium, with one‑click
  **Add to PATH** / **Install** fixes.

**Changed**
- **Settings moved to their own ⚙ Settings tab.**

**Fixed**
- **Preview an offline model no longer crashes the app** — restricted to
  online/recording models.
- **UI no longer freezes opening many browser tabs at once.**
- **Smoother UI under heavy load** — status updates batched (~150 ms) instead
  of one event per model.

---

## V1.4 — 2026‑06‑26

First version published as an official GitHub Release; the in‑app update check
goes live from here.

**Added**
- **Version number + automatic update check** in the header — shows the running
  build and flags when a newer GitHub release exists.
- **⛔ Force Quit / Terminate** — header button + tray "Force Quit" item that
  hard‑kills the whole process tree (ffmpeg, relay, Chromium) like Task Manager's
  *End Task*; confirms only when a recording is active.
- **Rank models from the OpenClaw bot** — `rank <model> 0‑5`, plus `--rank N` on
  `add‑saved` / `add‑recorder` / `record`.

**Changed**
- **Cleaner repo layout** — source under `src/`, docs under `docs/`. No change to
  how you run it (double‑click `StreamRecorder.bat`).

**Fixed**
- **Blank taskbar icon** — the `.ico` is now pushed to the taskbar via
  `WM_SETICON`.
- **Stale ranks** no longer linger after a model leaves both lists.

## V1.3 — 2026‑06‑24

**Added**
- **⭐ 1–5 star model ranks** on both the Recorder and Saved Models tabs —
  sortable **RANK** column, keyed by model identity (same stars everywhere,
  including the extension), persisted between sessions. Saved‑Models export now
  carries ranks and merges instead of overwriting; import back‑fills stars.
- **Rank from the browser extension** — a star row in the popup, enabled once
  the model is in Saved Models or the Recorder. New `POST /rank`; `GET /status`
  now also returns `rank`.
- **Live polling in the extension popup** — status/rank/membership update in
  place while open.
- **Status dashboard (left panel)** — per‑site breakdown (🟡 CB, 🔴 SC, 🔵 CS,
  🟢 MFC) with total / recording / online / offline, plus an ALL summary.

**Fixed**
- Privacy Mode could freeze the app (modal hidden behind the cover) — now an
  on‑cover Exit/Stay panel, no modal.
- UI freeze when starting/stopping many recordings at once — launches now go
  through a bounded pool (4 concurrent) with per‑model dedupe.
- Silent duplicate‑instance — the failed port bind is now logged and surfaced.
- Saved Models desync between the API and the lazy‑built UI rows.

## V1.2 — 2026‑06‑13

**Added**
- **🧹 CLEAR RECORDER** button (and `POST /clear` + `scr33nx_ctl.py clear`).
- **Bot dashboard + clear (OpenClaw)** — `GET /dashboard`, wired into the script.
- **🎭 Stripchat Browser Fallback toggle** in Settings.
- **Chat‑bot / agent control via the local API (OpenClaw)** — `scr33nx_ctl.py`
  wraps every action; full walkthrough in `docs/OPENCLAW-HOWTO.md`.
- **New API endpoints** — `POST /stop_all`, `/monitor`, `/pipeline`, `/quit`;
  `POST /remove` gained `target: "saved"`.

**Changed**
- **Standardized output filenames** — no part suffix until a split happens, then
  `_part001`… with one shared timestamp; pipeline `.mp4` splits use the same
  3‑digit padding.

**Fixed**
- **STOP TELEGRAM PIPELINE** now halts the uploader workers (was draining the
  backlog).

## V1.1 — 2026‑06‑12

**Added**
- **OneTab / browser integration** — "Open in Browser" and "Copy as OneTab List".
- **Status filter** on both tabs (any combination of statuses; live updates).
- **Row checkboxes** on both tabs — an explicit working set for every bulk
  action, with "Check All Visible"/"Uncheck All".
- **Faster multi‑selection** — press‑and‑drag range select, Shift/Ctrl click.
- **Saved‑tab bulk actions** — "Add to Recorder (N)" and "Remove from Saved (N)".

## V1.0 — 2026‑06‑11

**Added**
- **Filter boxes** on the Recorder and Saved Models tabs.
- **Lazy Saved Models tab** — rows built on first visit; engine registration on
  scanner start; status mirroring preserved.

**Fixed**
- **Tray‑icon hard crashes** (64‑bit `restype` declarations; single persistent
  WNDPROC; non‑blocking tray creation).
- **Recordings stalled after the first file split** — every launch path now
  drains ffmpeg stderr.

---

> Newer, not‑yet‑released changes accumulate under `## [Unreleased]` in
> `docs/CHANGELOG.md`.
