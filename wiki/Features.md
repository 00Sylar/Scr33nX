# Features

## Recording

- **Auto‑detect online** — starts recording the moment a model goes live, stops
  when they go offline.
- **Multi‑site, simultaneous** — record many models at once across Chaturbate,
  Stripchat, Camsoda, and MyFreeCams.
- **Highest‑quality pinning** — the relay locks ffmpeg to the top‑bitrate
  variant so quality can't silently fall back mid‑recording.
- **Quality caps** — a global *Max Quality* setting (Unlimited / 1080p / 720p /
  480p) plus per‑model overrides (right‑click a model). Per‑model beats global.
- **Auto‑downgrade (optional)** — a stream that persistently loses segments is
  restarted one step lower (720p → 480p → 240p) without touching other
  recordings; resets when that model's session ends. Models with a manual
  override are never auto‑downgraded.
- **Parallel segment prefetching** — segments are fetched ahead of ffmpeg in
  parallel (64 shared workers), so slow streams catch up instead of skipping
  1–2 s chunks. Built for dozens of concurrent recordings.
- **File splitting** — automatically starts a new numbered file when a recording
  hits your Max File Size (e.g. 3070 MB).
- **Stripchat native path** — records browserless (MOUFLON) when possible, with
  automatic Playwright/Chromium fallback.
- **Per‑site proxy (optional)** — if a site's video servers refuse your network
  (e.g. Chaturbate answering 403 to every request), route just that site's
  traffic through a SOCKS5/HTTP proxy such as Cloudflare WARP in proxy mode.
  One default proxy or one per site, with a **Test** button. See [[Settings]]
  → 🌐 Proxy.

See [[Recording Internals|Recording-Internals]] for how all of this works.

## Monitoring & UI

- **Two interfaces, one engine** — a modern default UI (native window, no
  browser involved, smooth animations, drag‑rectangle multi‑select,
  collapsible per‑site groups) and a classic Tk fallback (`--classic`). See
  [[Installation]].
- **Version number** in the header next to the logo, plus a background
  **update check** against GitHub (see [[Installation]]).
- **Live bandwidth meter** in the header (`↓` download / `↑` Telegram upload) —
  your signal for when you're approaching your connection's limit.
- **Log file** — everything (Activity Log, ffmpeg stderr, relay warnings, crash
  tracebacks) is also written to `%LOCALAPPDATA%\Scr33nX\streamrecorder.log`
  (rotating, 5 MB × 3) — shared by both interfaces.
- **Dropped‑segment warnings** — get notified when a stream is losing segments
  because bandwidth can't keep up (toggle in [[Settings]]). A model simply
  going offline no longer triggers a false warning.
- **▶ Stream preview** — right‑click an online/recording model → **Preview**.
  External mode opens a standalone mpv/VLC/ffplay window; embedded mode plays
  in‑app (a built‑in player in the default UI, VLC/mpv in the classic UI). The
  embedded preview shows the model's live status plus **▶ REC / ⏹ Stop**
  buttons — starting a model that isn't in the Recorder adds it automatically.
- **▶ Player tab** *(default UI only)* — open several models as live tiles
  picked from Recorder/Saved Models. All tiles stream **muted** at once in a
  **Grid** wall; click a tile for **Theater** mode (that tile large, with
  player controls, **▶ REC / ⏹ Stop**, and an **AUTO** toggle to flip
  Auto‑Record without leaving the Player (dimmed until the model is in the
  Recorder, since Auto‑Record only applies there; Theater tile only, grid
  tiles stay clean), the rest in a Bottom/Side thumbnail
  strip, still playing). Add tiles via **+ Add Tile** or right‑click an
  online/recording model → **▶ Add to Player**. Tiles start streaming the
  moment they're added and keep streaming while other tabs are in front, so
  returning to the Player never reloads them; the Grid scrolls when tiles
  overflow the window, **★ Fill Top Ranked** opens tiles for your
  highest‑ranked models that are online right now (best rank first, up to
  the cap), and **🧹 Clear Player** removes every tile in one
  click (Player only — recordings and the other tabs are untouched). The
  open‑tile count is capped by **Max Player tiles** in [[Settings]] (every
  tile is a live stream, so it's also a bandwidth/CPU cap).
- **Saved Models** tab — view‑only watchlist with online/offline status,
  import/export, and a background scanner.
- **⭐ 1–5 star ranks** — rate any model on the Recorder or Saved Models tab
  (click a star, click again to clear; or right‑click → **Set Rank** for a whole
  selection). Sortable **RANK** column, shared per‑model across both tabs and the
  extension, saved between sessions. Changing/clearing an *existing* rank asks
  for confirmation first (misclick guard). A **Rank: All ▾** filter next to the
  status filter shows only ★N‑and‑up (or unranked) models *(default UI only)*,
  and every rank change is logged (Activity Log + audit log).
- **Model audit log** — every add/remove (Recorder and Saved), rank change
  (old → new), VIP change, import/export and Clear Recorder is appended as one
  JSON line to `%LOCALAPPDATA%\Scr33nX\models_audit.log` (rotating, 2 MB × 3),
  tagged `ui` / `api` / `import` — reconstruct any list change after the fact.
- **Config backups + corruption recovery** — the settings file (models, Saved
  list, ranks) keeps 3 rotating backups; a corrupt/unreadable config is
  restored from the newest good backup automatically at startup instead of
  silently starting (and then saving) empty. The broken file is kept as
  `.corrupt-<timestamp>`.
- **🔔 Notifications** — Windows desktop toasts for recording started/stopped,
  dropped segments, quality downgrades, and low disk space, each independently
  toggleable, plus a **🌟 VIP List**: right‑click any model → *Add to VIP List*,
  then enable **VIP only** to be notified for just those models.
- **✕ Remove Offline** — one click removes every currently‑OFFLINE model from
  the Recorder (asks first; recording/private/checking rows and Saved Models
  are untouched).
- **⛔ Terminate** — hard‑kills the app and every child process (ffmpeg, relay,
  Chromium) instantly, like Task Manager's *End Task*; confirms only if a
  recording is active. Also available from the tray menu.
- **⛔ Low‑disk guard** (off by default) — stops all recordings and blocks new
  ones when the output drive drops below a configurable **Stop** threshold
  (default 20 GB free); recovers automatically once free space climbs back to
  a separate **Resume** threshold (default 40 GB), so it can't flap on/off.
- **Single‑instance lock** — a second Scr33nX can't silently corrupt your
  models/ranks; it shows a warning and closes itself instead.
- Minimize to system tray.
- 🔒 **Privacy Mode** — idle screen cover.
- Activity log with timestamps; settings saved between sessions.

## Linked identities

Some models stream on several sites — sometimes with the same username,
sometimes not. **🔗 Links** (button on the Saved Models tab) lets you mark
those accounts as one person:

- **Rank sync** — rating any linked account re‑rates all of them, so the same
  person carries the same stars everywhere (when you first link two accounts
  with different ranks, the higher one wins).
- **Duplicate‑recording warnings, in the app and the extension.** The web
  UI's 🔗 marker (Recorder rows, Saved rows, Player tiles) escalates with
  live state: amber **🔗⧉** when the same person is also in the Recorder on
  another site, a red **REC·SITE** chip when a linked account is being
  recorded right now, and **REC ×2** when both are recording at once — hover
  it for the exact accounts. **Starting** a recording (row menu, Saved,
  Player tile, or preview REC) on a model whose linked account is already
  recording asks **"Record anyway?"** first — Cancel aborts, nothing starts
  behind your back. The browser extension asks the same question: its
  **⏺ Start Recording** button swaps to an inline **✔ Yes, record / ✕ Cancel**
  confirm, and the toolbar/listing‑thumbnail badge still shows the amber
  **REC** variant. Just *adding* (without starting) only shows a heads‑up
  toast — recording is the only action that's gated.
- **Link editor** — right‑click any model → **🔗 Edit Links…** (or click a
  row's 🔗 marker): one card with her account on each of the four sites,
  filled via type‑ahead search over your tracked models. A username you
  don't track yet is added to Saved Models and linked in one step; clearing
  a field unlinks that account. Groups of 3–4 accounts are one card's work.
- **Same‑username suggestions** — models tracked under the same name on
  several sites are suggested for linking; confirm one‑by‑one, **Link all N**
  at once (one confirm), or Ignore false positives. The dialog has a filter
  box for hunting through big lists; the bot (`link alice@chaturbate
  bobby@stripchat`) and API work too.
- Links live in their own additive config key (`model_links`) — they never
  touch your models/ranks data and survive **Clear Recorder**.

## Integrations

- **Browser extension** (Chromium **and** Firefox) — one‑click add from a
  model's page, plus 1–5 star rating right from the popup, with support for
  the optional API token (⚙ **API token…** link in the popup). A **dynamic
  toolbar badge** shows the current model's state (REC/ON/OFF/★) or the
  active-recording count, tracked models get **status badges on their
  thumbnails** while you browse the cam sites, and a right‑click menu adds
  models straight from any link. See
  [[Browser Extension|Browser-Extension]].
- **Telegram upload pipeline** (optional) — converts finished recordings and
  uploads them to a Telegram group/topic, as two independent stages you can
  run alone or together, with a guided **Setup Wizard**. See
  [[Telegram Pipeline|Telegram-Pipeline]].
- **🔍 System Check** (Settings) — shows whether each external tool/package is
  found (ffmpeg, ffplay, mpv, VLC, python‑vlc, tdjson, Playwright Chromium),
  with one‑click **Add to PATH** / **Install** fixes.
- **Chat‑bot / agent control (OpenClaw)** — drive Scr33nX from your phone over
  Telegram/WhatsApp. See [[OpenClaw Bot|OpenClaw-Bot]].
