# Changelog

All notable changes to **Scr33nX** are documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Dates are `YYYY-MM-DD`. Versioning starts at **V1.0**; earlier entries are
grouped by date / milestone.

---

## [Unreleased]

### Fixed
- **Uploaded files no longer get stranded in the converted folder.** After a
  successful upload the pipeline deletes the local `.mp4`, but Telegram can
  hold the file open for a moment longer than it takes to report the send as
  done — the delete then failed, and because the file was already recorded as
  uploaded, the pipeline skipped it from then on. It sat in the converted
  folder for ever while a one-line warning scrolled past. The delete is now
  retried for about 15 seconds, which clears the race; if the file still can't
  be removed it's moved into a **`stuck`** sub-folder of the converted
  folder, so it's out of the pipeline's way and plainly visible instead of
  silently piling up.

### Changed
- **Player tab: the Status ▾ / Rank ▾ filters now filter the open tiles too.**
  Previously they only scoped what the **＋ Add Tile** picker offered, so
  using them with tiles already open appeared to do nothing. They now work
  like the Recorder/Saved filters: the Player shows only the tiles whose
  live status and rank match — e.g. **Status: Online** leaves a wall of just
  the models that are up right now — and the same filter still scopes the
  picker, so anything you add matches what you're looking at. Hidden tiles
  are only hidden, never closed: they keep streaming in the background and
  reappear instantly when you clear the filter (nothing reloads). The tile
  counter gains a "· N shown" suffix while a filter is active, ★ Fill Top
  Ranked tells you if it added tiles the filter is hiding, and in Theater
  mode a centred tile that gets filtered out hands off to the first visible
  one instead of leaving a blank stage.

---

## V2.5 — 2026-08-25

**Faster ways to find and manage models in the Player and Saved Models tabs.**

### Added
- **"Hide cross-site recording" filter (Saved Models tab).** A new checkbox
  at the bottom of the Status ▾ dropdown hides any model whose linked (🔗)
  identity is already recording on another site — independent of that row's
  own status. Combine with **Status: Online** to see only models that are
  online and not already being captured elsewhere under a different account.
- **Status/Rank filters for the Player tab's "+ Add Tile" picker.** A
  Status ▾ / Rank ▾ pair now sits in the Player toolbar, left of ★ Fill Top
  Ranked, scoping what the picker offers — narrowing thousands of tracked
  models down to e.g. only ★4+ online ones is as fast as on the Recorder/
  Saved tabs. They persist until cleared, like the Recorder/Saved filters
  (only the picker's own name search resets each time it opens), and never
  affect tiles already open.
- **Right-click context menu on Player tiles.** A tile now offers the same
  menu as its model's row on the Recorder or Saved Models tab — start/stop
  recording, set rank, edit links, VIP, copy URL, open in browser — plus a
  Player-only **✕ Close Tile** that removes just the tile, leaving the
  Recorder/Saved entry untouched. A tile whose model was since removed from
  both (closing a tile never removes it there, and vice versa) gets a
  minimal menu (Close Tile, Open in Browser).

---

## V2.4 — 2026-08-25

**Stripchat is back.** Stripchat silently changed its API partway through
V2.3's life, breaking the Player, Preview, and recording for every Stripchat
model while status still showed online. This release fixes that and also
speeds up how fast Chaturbate models open in the Player.

### Changed
- **Chaturbate models open much faster in the Player.** Opening a tile used to
  re-resolve the stream from scratch; it now reuses the URL the online check
  already fetched (up to 30 seconds old), so the tile opens without a single
  request. Stripchat gets the same treatment. Online/offline detection is
  unchanged — those checks still go out to the site every time, so a model
  going offline is noticed exactly as fast as before. Still slow in one case:
  the first open of a *saved* model while a background scan is running, since
  nothing has fetched its URL yet.

### Fixed
- **Stripchat models are recordable and previewable again.** Stripchat's bot
  filter started answering the endpoint the app used to look up a model's
  stream id (`/api/front/v2/models/username/<name>/cam`) with HTTP 418 on
  every request. Nothing after that lookup could work: the Player and the
  right-click Preview reported "couldn't resolve" for models who were plainly
  online, and every recording silently dropped to the slower Playwright
  browser fallback (or, with that fallback turned off, didn't record at all).
  The stream id now comes from the model page instead, so the lightweight
  browserless path works again.
- **Stripchat online checks no longer build a dead playlist URL.** The URL the
  scanner used to confirm a model was up (`media-hls.doppiocdn.com/hls/…`) had
  been returning 404; it now points at the master playlist that actually
  serves.

---

## V2.3 — 2026-07-25

**Cross-site recording warnings, now in the app itself.** V2.2 taught the
browser extension to flag when a linked model is already being recorded
elsewhere; V2.3 brings that same protection into the app and turns it from a
passive warning into an active confirmation before a recording starts —
plus a Player tab refinement.

### Added
- **In-app cross-site warnings (linked identities).** The 🔗 marker on
  Recorder / Saved rows and Player tiles now escalates with live state:
  amber **🔗⧉** when the same person is also in the Recorder on another
  site, a red **REC·<site>** chip when a linked account is being recorded
  right now, and **REC ×2** on both rows when two accounts of the same
  person are recording at once. Hover the marker for the exact accounts.
- **"Record anyway?" confirmation.** Starting a recording (row menu, Saved,
  Player tile, or preview REC) on a model whose linked account is already
  being recorded now asks for confirmation before starting; Cancel aborts,
  nothing starts behind your back. The browser extensions ask the same
  question: the popup's Start Recording button swaps to an inline
  **Yes, record / Cancel** confirm when her other account is already
  recording. Adding (without starting) still just shows an amber heads-up
  toast, and adds from any source (extension, API, bot) log the warning in
  the Activity log.
- **AUTO toggle on the Theater tile.** The Player tab's big Theater tile now
  has the same Auto-Record switch as the Recorder table, next to its
  REC/Stop buttons — flip auto-record for the model you're watching without
  leaving the Player. Dimmed (with a hint) when the model isn't in the
  Recorder yet, since Auto-Record only applies to Recorder models. Theater
  tile only — grid and strip tiles stay clean.

---

## V2.2 — 2026-07-20

**Linked identities.** Scr33nX now understands when the same model has
accounts on multiple sites — link them once and her star rank stays in sync
everywhere, with a warning (never a block) if you're about to double-record
her under a different username. Plus a big round of browser-extension
upgrades (dynamic badges, right-click add), config-corruption recovery, a
full model audit log, an optional local-API token, several Player tab
refinements, and a fix for a low-disk-guard bug that could leave ffmpeg
processes running — and downloading — long after the app reported 0 active
recordings.

### Added
- **Linked identities (🔗 aka groups).** Link the accounts one model has on
  different sites — same or different usernames — so Scr33nX knows they're
  one person. Effects: her **star rank stays identical across all linked
  accounts** (rating any of them re-rates the rest, highest rank wins when
  first linking), and the browser extension shows an **amber REC** toolbar
  and thumbnail badge plus an "⚠ Already recording on … as …" popup warning
  when you're looking at an account whose linked twin is being recorded — so
  you never double-record the same person by accident (recording is never
  blocked, only flagged). Manage links from the web UI (**🔗 Links** button
  on the Saved Models tab: current groups, one-click **same-username
  suggestions**, manual pairing of any two tracked models), from the bot /
  CLI (`link`, `unlink`, `links`), or the API (`POST /link`, `POST /unlink`,
  `GET /links`; `/status` and `/models` now report `aka` +
  `linked_recording`). Stored in the **new, additive** `model_links` config
  key — existing models / saved models / ranks data is untouched, and links
  survive Clear Recorder. Link/unlink events go to the audit log.
- **Link editor + bulk linking.** Right-click any model (Recorder or Saved)
  → **🔗 Edit Links…**, or click a row's 🔗 marker: a card shows her account
  on each of the four sites with **type-ahead search** fields — type a few
  letters, pick from your tracked models on that site, Save. Groups of 3–4
  accounts are set up in one card; typing a username you don't track yet
  offers "➕ add to Saved & link" in one step; clearing a field unlinks that
  account. The Links dialog also gained a **filter box**, type-ahead inputs
  instead of two giant dropdowns, and a **🔗 Link all N** button that
  confirms every same-username suggestion in one click (ranks sync per
  group, one confirm dialog).
- **Extension: dynamic toolbar badge.** The extension icon now shows live
  state for the tab you're on — **REC** (red) while that model is recording,
  **ON** (green) when it's online in your Recorder, **OFF** (grey) when it's
  in the Recorder but idle, **★** (blue) when it's only in Saved Models. On
  every other tab the badge shows the number of active recordings, so a red
  "3" means three recordings running. Updates on tab switch/navigation and
  every 30 s in the background (both Chromium and Firefox).
- **Extension: live status badges on browse pages.** While browsing
  Chaturbate / Stripchat / Camsoda / MyFreeCams listings, thumbnails of
  models you track get a small corner badge (pulsing **REC**, **ON**,
  **OFF**, or **★**) — see who's already being recorded without opening
  their page. Hover a badged card for details. Requires the new cam-site
  permissions (Firefox: grant them in `about:addons` → Permissions).
- **Extension: right-click add.** Right-click any model link or thumbnail →
  **Add to Scr33nX Recorder** / **Add to Scr33nX Saved Models** — add
  models without ever opening their tab.
- **`GET /models` API endpoint + `models` bot command.** Bulk snapshot of
  every Recorder + Saved model (same per-model fields as `/status`, plus a
  global `recording` count) in one call — feeds the extension badges. The
  `scr33nx_ctl.py` CLI (and so the OpenClaw bot) wraps it as
  `models [--site S] [--recording] [--online] [--min-rank N]`, so asking the
  bot "who's recording?" or "which of my 4★+ models are live?" is now one
  call instead of a per-model loop.
- **Config backups + corruption recovery — saved models/ranks can no longer
  be wiped by a bad config file.** The settings file (the only store of your
  model lists and star ranks) now keeps 3 rotating backups (`.bak`/`.bak2`/
  `.bak3`). Previously, if `~/.streamrecorder_config.json` ever failed to
  parse (truncated write, disk hiccup), the app silently started with empty
  lists and the next save overwrote the file — permanently deleting every
  saved model and rank. Now the newest readable backup is restored
  automatically, the unreadable file is preserved as `.corrupt-<timestamp>`,
  and a warning appears in the Activity Log.
- **Model audit log.** Every membership/rank event — add/remove on Recorder
  and Saved, rank changes (old → new), VIP changes, import/export summaries,
  Clear Recorder — is appended as one JSON line to
  `%LOCALAPPDATA%\Scr33nX\models_audit.log` (rotating, 2 MB × 3), tagged
  with its source (`ui` / `api` / `import`), from both interfaces. Lets you
  reconstruct later when a model appeared, vanished, or changed rank.
- **Rank changes now show in the Activity Log** (`⭐ Rank ★★☆☆☆ → ★★★★☆:
  name (site)`; bulk changes log one summary line). They were previously
  completely silent.
- **Rank filter.** A **Rank: All ▾** dropdown next to the status filter on
  the Recorder and Saved Models tabs shows only models ranked ★N-and-up, or
  only unranked ones; combines with the name/status filters.
- **★ Fill Top Ranked (Player tab).** One click opens tiles for your
  highest-ranked models that are online right now (Recorder + Saved, best
  rank first) until the tile cap is reached.
- **Optional API token for the local control API.** Set a token in
  ⚙ Settings → Local API and every request to port 5200 must carry it in the
  `X-Api-Token` header (or `?token=` on GETs) or it gets `401` — blocks
  other local apps/webpages from controlling Scr33nX. Empty (the default)
  keeps the API open exactly as before. Supported by the browser extension
  (⚙ **API token…** link at the bottom of the popup — appears automatically
  when the app starts rejecting it) and by `scr33nx_ctl.py` / the OpenClaw
  bot (reads the app's config file automatically, or the `SCR33NX_TOKEN`
  env var).
- **Star ranks in the preview overlay, Player-tab grid, and Theater tile.**
  The 1–5 star rank now shows (and is clickable, same as the tables) right
  after the model's name in the embedded preview overlay, right below the
  name on Player-tab **Grid** tiles, and right after the name in the big
  Theater tile.
- **Max Player tiles cap raised from 20 to 100.** Settings still defaults to
  9, but you can now open up to 100 tiles in the **▶ Player** tab if your
  bandwidth/CPU can take it.
- **🧹 Clear Player button.** One click (plus a confirm) removes every tile
  from the **▶ Player** tab. Only the Player empties — recordings, the
  Recorder list, and Saved Models are untouched.

### Changed
- **Update check re-polls every 24 h.** It used to run once at startup, so a
  long-running app never learned that a new release shipped.
- **Pipeline split is much faster on big files.** The Convert stage's
  splitter now seeks the input (`-ss` before `-i`) instead of reading the
  whole file up to each cut point — part N of a multi-GB recording no longer
  re-reads everything before it. Cut points still land on keyframes exactly
  as before (stream copy); please verify one split output plays fine.
- **Windows toasts now identify as "Scr33nX"** instead of the old
  "StreamRecorder" app name.
- **Player tiles keep streaming while you're on other tabs.** Leaving the
  **▶ Player** tab used to stop every tile, so hopping Recorder ⇄ Player
  reloaded all streams from scratch. Tiles now start loading the moment a
  model is sent to the Player and keep playing in the background, so the tab
  is instantly live when you come back. (Streams still count against
  bandwidth while backgrounded — use 🧹 Clear Player or close tiles to stop
  them.)

### Fixed
- **Low-disk guard (and Stop All / Clear Recorder) could leave orphaned ffmpeg
  processes running, still downloading and writing to disk, even though the
  UI showed 0 active recordings.** A model that's in both the Recorder list
  and Saved Models list has its split/stall housekeeping polled by two
  independent monitor loops; with no lock between them, both could act on the
  same session at a split point and each launch a replacement ffmpeg process
  — only one wins the tracked session reference, so the other kept running
  forever, invisible to Stop All, Clear Recorder, and the low-disk guard
  (which showed "⛔ LOW DISK — recording blocked" while the orphan kept
  eating bandwidth and disk space). Session start/split/restart is now
  serialized per model (`ModelConfig.session_lock`), and `stop_all_recordings`
  additionally sweeps every process this app has ever launched — not just the
  one a model currently references — so a bookkeeping mismatch can no longer
  leave a process behind.
- **Esc on the Telegram login prompt no longer hangs the pipeline.** Pressing
  Escape on the phone/OTP/2FA prompt used to hide the dialog without
  answering it, leaving the upload worker blocked for up to 5 minutes. Esc
  now cancels the prompt properly (same as the Cancel button). Esc also
  closes the Player picker and the Setup Wizard now.
- **Clicking a rank star on a Player grid tile no longer yanks you into
  Theater mode.** The star click was also being treated as a tile click; now
  it just rates the model.
- **Convert stage no longer retries a broken `.ts` forever.** A file that
  fails to convert is retried twice and then skipped until the pipeline is
  restarted (same 3-attempt policy uploads already had) — previously it
  re-converted on every scan pass, burning CPU endlessly.
- **"Remember my choice" now works in "Open in Browser (choose…)".** The
  checkbox was silently ignored when the picker was opened via the explicit
  choose flow.
- **Extension /add can no longer corrupt a state pass.** The API handler
  wrote to the web UI's shared model dicts without taking the lock that
  protects them (the same race family as the "Recorder list rendering
  empty" fix in this release) — all shared-dict accesses (Recorder rows,
  Saved list, ranks — including import/export and the /add path) are now
  consistently lock-guarded.
- **Removing Saved Models reports the real count** (it used to count
  requested rows, including ones that no longer existed), and ranking a
  Player tile whose model was just removed from both lists is now refused
  with a note instead of storing a rank that silently vanished on restart.
- **🧹 Clear Recorder now actually stops the downloads it clears.** Previously
  the button removed every model from the list while firing the "stop all"
  off in a background thread that raced the removal, and it left the **Saved
  scanner** running — so a model that was also Saved got re-recorded moments
  after it was killed, and the download appeared to vanish from the list yet
  kept writing to disk in the background. Clear now pauses **both** monitors,
  force-stops every active download and **waits for the ffmpeg processes to
  exit before** removing the models, so nothing survives the clear. Your Saved
  list is kept, but the Saved scanner is left paused (its toggle reflects this)
  so nothing resumes on its own.
- **Player Grid now scrolls.** With more tiles than fit the window, the
  bottom rows were simply cut off; the Grid (and Theater) area now scrolls
  vertically.
- **Recorder list occasionally rendering empty.** `_rows` (the set of
  models the web UI tracks) was read on one thread and mutated on another
  with no synchronization, which could intermittently raise an error mid-poll
  and silently abort a state update — the more add/remove activity accumulated
  over a session, the likelier it got. The read/write paths are now
  lock-protected, JS errors that used to be swallowed silently are now logged
  to the console, and the Recorder list self-corrects within one polling
  cycle if it ever renders empty while it shouldn't.
- **Telegram-pipeline `.mp4`s exceeding the configured Max File Size.** The
  Convert stage split files against a hardcoded ~3.8 GB (Telegram's own
  upload cap) instead of the user's **Max File Size** setting, so a 1000 MB
  limit could still produce multi-GB `.mp4`s. The split now honors Max File
  Size (capped at Telegram's limit only when the Upload stage is also on),
  plus a small safety margin so variable-bitrate streams don't overshoot it.

---

## V2.1 — 2026-07-09

**The ▶ Player tab.** Watch several live models at once inside the app — a
muted grid of tiles, or one playing large in Theater mode — and start/stop
recording straight from the tile or preview you're watching. Plus
configurable low-disk thresholds, consistent tab icons, and a round of
Player, Telegram-upload, and star-rating fixes.

### Added
- **▶ Player tab** (web UI only). Open several models at once as tiles
  picked from Recorder/Saved Models. Every open tile streams live and
  **muted** at once in a **Grid** wall, so you can monitor several cams
  visually without a wall of simultaneous audio; click a tile to switch to
  **Theater** mode, where that tile plays large and the rest sit — still
  playing — in a thumbnail strip you can toggle between Bottom and Side
  placement. More open tiles means more concurrent streams, so the number
  you can have open at once is capped by a new **Max Player tiles** setting
  (1–20, default 9). Add tiles from the Player tab's **+ Add Tile** picker,
  or right-click an **online/recording** model on the Recorder or Saved
  Models tab → **▶ Add to Player** (single or multi-select) for a faster
  path.
- **Start/Stop recording from the Player and the preview.** The embedded
  preview overlay now shows the model's live status next to Close, plus
  **▶ REC** / **⏹ Stop** buttons; the big Theater tile gets the same
  controls beside its status badge (thumbnails stay clean). REC is enabled
  only when the model is online, Stop only while recording. Starting a
  model that isn't in the Recorder adds it automatically.
- **Configurable low-disk thresholds.** Settings → Behavior now has "Stop
  below (GB free)" and "Resume at (GB free)" fields (defaults 20 GB / 40 GB)
  instead of a hardcoded 20 GB cutoff. Applied to both the web UI and the
  classic (`--classic`) Tkinter UI.

### Changed
- **Web UI: consistent tab icons.** All six navigation tabs now carry a
  matching line icon (previously only Player and Settings had ad-hoc text
  glyphs, which looked inconsistent).
- **Player Theater layout fits the window.** The big Theater tile is now
  height-capped so the thumbnail strip always stays on screen (previously
  a tall stream pushed the Bottom strip out of view entirely), and the
  Side strip scrolls vertically when more tiles are open than fit. In
  Bottom mode the big tile hugs the video's 16:9 shape and stays centered
  (no letterbox bars), and the strip scrolls horizontally — previously,
  once enough tiles were open, their combined width silently stretched
  the whole stage past the window edge, shoving the player off-center and
  putting the strip's scrollbar out of reach.
- **Smooth Theater switching + polish.** Switching the active Theater tile
  (or Grid ↔ Theater) now moves the live tiles in place instead of
  rebuilding them, so streams keep playing with no gray flicker/rebuffer.
  The status badge is centered in the big tile's bar and in the preview
  overlay's header (REC/Stop stay on the right), the theater strip's
  scrollbar is red for visibility, and the mouse wheel scrolls the Bottom
  strip horizontally.

### Fixed
- **Star rating silently lost right after adding a model.** Rating a model
  (via the browser extension, or the web UI's own Add buttons) immediately
  after adding it to Saved Models/Recorder could race the add — the server
  confirmed the add before the model actually registered, so a rank request
  landing in that window was rejected and the rating never saved, even
  though the extension's stars appeared to update. Model membership is now
  recorded synchronously on add, closing the race.
- **Low-disk guard restart loop.** The guard used a single free-space
  threshold (20 GB) to both trip and clear, so as soon as free space ticked
  back over the line, auto-record models would relaunch immediately, eat the
  sliver of headroom, and re-trip the guard on the next check — looping
  start/stop indefinitely. The guard now trips below a **stop** threshold and
  stays tripped until free space climbs back up to a separate, higher
  **resume** threshold (hysteresis), stopping the flapping.
- **Player tab showed a stretched, empty duplicate tile.** Switching Grid ↔
  Theater layout set the other panel's `hidden` attribute, but the panel's
  own CSS `display` rule silently overrode it, leaving both panels visible
  at once. Also, Player tiles now always resolve an embedded in-page stream
  regardless of the general **Preview mode** setting — previously, if that
  setting was left on its default (**External window**), tiles would try to
  spawn external mpv/VLC windows instead of playing in the tile itself.
- **Telegram uploads silently lost when a send failed.** When Telegram
  rejected a video (wrong topic ID, size cap, no write access, …), the
  uploader treated the failure exactly like a success: the file was marked
  as uploaded and skipped forever — with no error anywhere. A whole folder
  of .mp4s could pile up "ignored" this way. Failed sends now log the real
  Telegram error, are retried up to 3 times, and are **never** marked as
  uploaded; files that were wrongly marked before have been un-marked and
  will upload on the next pipeline run. Bonus hardening: the uploader no
  longer queues .mp4s that ffmpeg is still writing (previously it could
  send a truncated video), and a file that uploads but can't be deleted is
  now reported instead of silently left behind.
- **Player tiles failed silently and could stop retrying.** A tile whose
  stream couldn't start (model in a private show, playback error, stalled
  resolver call) just sat on its poster forever with no hint. Tiles now
  show a short error line ("no public stream — retrying…"), retry every
  30 s (immediately when you re-open the tab), log in-page playback errors
  to the Activity Log, and a stuck resolver call can no longer wedge the
  tile permanently. Tiles also no longer restart their streams in the
  background after you leave the Player tab, and switching Grid ↔ Theater
  re-attaches live streams correctly instead of corrupting them.
- **Player tiles played video into invisible elements.** The two big ones:
  switching Grid ↔ Theater left the previous layout's tile nodes in the
  (hidden) inactive panel, so the player attached the stream to the stale
  hidden copy of the tile — audio-less video "played" where nobody could
  see it while the visible tile kept its poster. And the tile's initial
  `play()` could be cancelled by the player's own media load ("AbortError"),
  leaving the video permanently paused. Layout switches now clear the
  inactive panel, and playback retries once the media is ready.
- **Player tiles: video played but stayed invisible — root cause found.**
  The tile videos had been playing correctly all along; the gray poster
  card drawn over each tile never actually went away, because the HTML
  `hidden` attribute is only a browser *default* (`display:none`) and the
  poster's own `display:flex` rule silently overrode it. A global
  `[hidden] { display:none !important }` rule now makes `hidden` always
  win, app-wide, eliminating this entire bug class (the same trap had
  already caused the duplicate-panel bug). Along the way the tile player
  was also rebuilt on the preview overlay's proven pattern
  (`<video autoplay muted>`, plain block styling), the big Theater tile
  gained player controls (pause / unmute one model — tiles stay muted by
  default), and the recorder's transient "checking" poll state no longer
  stops tile playback every cycle.

---

## V2.0 — 2026-07-07

**The web UI redesign.** A complete visual and structural rebuild of
Scr33nX — a modern dark red/black interface with smooth animations,
shadows, and transitions, rendered in a native window (Windows WebView2;
no browser involved) — built and refined over many rounds of hands-on
testing. Same recording engine, same config files, zero feature loss.

### Added
- **New web-based UI.** Recorder tab (full toolbar, right-click menus with
  per-model quality and rank submenus, column sorting, search + status
  filters, checkboxes, Shift/Ctrl/marquee selection), Saved Models (smooth
  with thousands of rows via virtual scrolling, scanner, import/export, add
  prompt), Output/Upload (pipeline with live stage toggles, setup wizard,
  re-auth, in-app Telegram login prompts), Activity Log, full Settings (all
  options + System Check with one-click fixes), stream preview (external
  mpv/VLC/ffplay **and** a new built-in in-app player), privacy mode with a
  starfield cover, tray, update check, single-instance lock, and the
  identical port-5200 API. Feature-parity reference: `docs/PARITY.md`.
- **Desktop-style drag-rectangle (marquee) selection** over the model
  tables, and **collapsible per-site groups** with [+]/[−] controls — on
  both the Recorder and Saved Models tabs.
- **Embedded preview without VLC:** in-app previews use a built-in player
  (hls.js through the local relay) — python-vlc/libmpv are no longer needed
  for embedded preview in the new UI (still used by the classic UI).
- **Multi-condition status filter:** the Recorder and Saved Models status
  filters let you pick any combination (e.g. Online **and** Recording); the
  button shows how many are active.
- **Right-click ▶ Add to Recorder & Start Recording** on an online model in
  Saved Models (adds it and starts recording in one step).
- **Notifications settings box + VIP list.** Notifications now have their
  own Settings card, separate from Behavior:
  - **Per-type toggles** — turn each notification on/off independently:
    Recording started, Recording stopped, Dropped segments, Quality
    downgraded, Low disk space. (An "app is broken" ffmpeg-missing alert
    always fires.)
  - **Toast duration** slider (1–5 s). Note: Windows ultimately controls how
    long a toast stays up, so the value is a hint it may round.
  - **🌟 VIP List** — add models via right-click (Recorder **or** Saved
    Models → *Add to VIP List*), then enable **VIP only** to receive
    per-model notifications *just* for your VIP models. Global safety alerts
    (low disk, ffmpeg) always come through. Manage/remove VIPs right in the box.

### Changed
- **`Scr33nX.bat` now launches the new web UI by default.** The previous
  interface still works exactly as before (same engine, same settings, same
  config files — nothing about it changed) — double-click the new
  **`Scr33nX-Classic.bat`**, or run `Scr33nX.bat --classic`.
  `Scr33nX-WebUI.bat` is kept as an alias for the new default.
- **`pywebview` is now a required dependency** (previously only needed to try
  the redesign preview) — `pip install -r requirements.txt` picks it up.
- **App version bumped to `2.0`.**

### Fixed
- **No more false "dropped segments" toast when a model goes offline.** The
  last live-edge segments of an ending stream looked identical to bandwidth
  loss; the toast is now deferred a few seconds and only fires if the model is
  still recording, so a normal offline no longer triggers a spurious warning.
  (The gap is still always written to the Activity Log.)
- **Telegram pipeline — uploads no longer wait for conversion.** The Convert
  and Upload stages now run as fully independent workers: the uploaders start
  sending any `.mp4` in the converted folder as soon as it appears (including
  files already there before you started, or produced mid-batch), instead of
  waiting for the entire `.ts → .mp4` conversion pass to finish first. Applies
  to both the classic and the new UI.
- **Web UI:** Copy Model URL now works (a 64-bit clipboard-handle truncation
  silently dropped the copy); pipeline status lines no longer leave a large
  blank gap before the log; clicking empty table space clears the selection;
  Add-to-Saved now has its own site dropdown instead of assuming Chaturbate
  for a bare username; toolbar button hovers are consistent (destructive
  buttons are tinted at rest rather than only reddening on hover); the
  browser-picker radio buttons no longer flicker; Ctrl/Cmd+A now selects all
  visible rows in Recorder and Saved Models; Save Settings moved to a
  centered bar below all Settings cards (it applies to all of them, not just
  one); the "preview not available" message is now short and user-facing,
  with the full reason kept in the Activity Log for troubleshooting.

---

## V1.6 — 2026-07-04

Minor bug fixes plus a few safety features: a low-disk guard, a hard
single-instance lock, and faster offline detection.

### Added
- **⛔ Low-disk guard** (Settings checkbox, off by default). When enabled and
  the drive holding the output folder falls below **20 GB free**, all active
  recordings are stopped immediately and every new start — manual REC,
  auto-rec, auto-restart, and max-size file splits — is blocked until you
  free up space or disable the option. You get one toast when the guard
  trips and a log line when space recovers.
- **Rank misclick guard (app only).** Clicking the RANK stars on a model that
  already has a rank now asks for confirmation ("★★★★☆ → ★★☆☆☆?") before
  changing or clearing it. Rating an unranked model stays one-click, and the
  browser extension / bot API are never prompted.
- **Single-instance lock.** Launching Scr33nX while another instance is
  already running (control port 5200 taken) now shows "You can only open one
  instance of this app" and the new window closes itself. Two live instances
  share one settings file and silently overwrite each other's models and star
  ranks — the most likely cause of "my ranks disappeared after a restart".
- **✕ Remove Offline button** (Recorder toolbar, next to ✕ Remove) — removes
  every model whose status is currently **OFFLINE** in one click, after a
  confirmation. Goes by the visible status, so RECORDING / PRIVATE / CHECKING
  / ERROR rows are kept, and Saved Models are never touched.

### Changed
- **Launcher renamed** `StreamRecorder.bat` → **`Scr33nX.bat`** to match the
  app name. Update any shortcuts pointing at the old name; the bot's `open`
  command uses the new name automatically.

### Fixed
- **Faster RECORDING → offline detection.** When a model went offline,
  ffmpeg often kept reconnecting instead of exiting, so the status stayed
  **RECORDING** for up to ~75 s until the 60 s stall timeout fired. Now, when a
  recording's file stalls for ~20 s, the app quietly asks the resolver whether
  she's still online (off the monitor thread, so no slowdown) — if she's
  offline it stops the recording right away (≈25 s instead of ~75 s); if she's
  just buffering it's left alone. The 60 s stall hard-stop remains as a backstop.
- **Stripchat recordings no longer get stuck on RECORDING after a model goes
  offline.** When a Stripchat model goes offline, the CDN often swaps her live
  playlist for a looping advert placeholder — the recorder kept downloading
  those filler segments, the file kept growing, and stall detection never
  triggered, so the row showed **RECORDING** indefinitely (recording adverts).
  The relay now spots the advert markers on every playlist refresh (no extra
  network traffic or CPU) and stops the recording immediately → **OFFLINE**.
- **Stall detection now also covers recordings that never create their output
  file.** A recorder process that hung before writing anything was invisible
  to the stall check and stayed **RECORDING** forever; a missing file now
  counts as 0 bytes, so the normal 20 s probe / 60 s stop applies.
- **Column sorting no longer resets while a status filter is active.** With
  e.g. *Online* filtered and the list sorted by rank, every status update
  re-applied the filter and shuffled rows back to their original order, so
  the sort had to be redone over and over. The last-clicked sort is now
  remembered and re-applied after every filter pass (Recorder and Saved tabs).

---

## V1.5 — 2026-06-28

In-app stream preview, a dedicated Settings tab with a dependency **System
Check**, a guided Telegram setup, a split stand-by pipeline, a browser picker,
and a round of UI-freeze fixes.

### Added
- **▶ Stream preview (mpv / VLC / ffplay).** Right-click a model → **Preview**
  to watch its live stream (available on both the Recorder and Saved Models
  tabs). A brief "Opening preview…" indicator shows while the stream resolves,
  then the player appears and is brought to the foreground. Choose **Mode**
  (External window / Embedded in-app) and **Preview engine** (Auto / mpv / VLC)
  in Settings:
  - *External* launches a standalone **mpv**, **VLC**, or **ffplay** window in its
    own process (minimal impact on recording) — ffplay (bundled with ffmpeg)
    works with no extra install.
  - *Embedded* plays inside a window with play/pause/mute/volume via **python-vlc**
    (easiest — auto-finds an installed VLC, no DLL step) or **python-mpv**
    (needs libmpv-2.dll in the `src/` folder). If neither is available you're told
    and offered the external player instead.
  Playback goes through the same local relay the recorder uses; an optional
  player-path setting overrides auto-detection. The `python-vlc` bridge ships in
  `requirements.txt`, and if it's ever missing while VLC is installed, the app
  offers a **one-click install** the first time you open an embedded preview.
- **Choose which browser "Open in Browser" uses.** Right-click a model →
  **Open in Browser** now lets you pick the browser (System default, or any of
  Chrome / Edge / Firefox / Brave / Opera / Vivaldi detected on your PC) the
  first time, with a *Remember my choice* option so you're not asked again. A new
  **Open links with** dropdown in Settings changes or resets the saved default at
  any time, and an **Open in Browser (choose…)** menu entry re-opens the picker
  for a one-off browser without touching your saved default.
- **Telegram Setup Wizard.** A new **🧙 Setup Wizard** button on the Output /
  Upload tab walks first-time users through configuring the upload pipeline:
  API ID / Hash (with a link to my.telegram.org), the destination group/topic
  ID, and optional folders. It saves everything to the normal settings and can
  start the pipeline straight away — the phone-number/login-code prompts then run
  through the usual login flow.
- **Split the pipeline into independent Convert and Upload stages, with a
  live stand-by model.** The Output / Upload tab now has two **Stages**
  checkboxes — *① Convert .ts → .mp4* and *② Upload .mp4 to Telegram*. The
  pipeline starts even with nothing checked and sits in **● STAND BY**; tick
  stages at any time and they apply immediately (the header shows *CONVERTING*,
  *UPLOADING*, or *CONVERTING & UPLOADING*). Unchecking a stage stops it after
  its current task finishes — an in-progress conversion or upload is never
  interrupted. Run Convert alone to get `.mp4` files without uploading, Upload
  alone to send `.mp4`s you already have, or both for the full flow. Enabling
  Upload connects to Telegram on demand (reusing your saved session — no restart,
  no re-login); credentials are only needed when Upload is on. Stage choices
  persist across restarts.
- **Control the pipeline stages from the OpenClaw bot.** New
  `scr33nx_ctl.py pipeline convert on|off` and `pipeline upload on|off` commands
  tick/untick the stages from your phone — working whether the pipeline is
  running or stopped. Backed by a new `POST /pipeline/stage` local-API endpoint.
- **System Check panel (⚙ Settings).** A dependency validator showing whether
  each external tool / package is found: ffmpeg, ffplay, mpv, VLC, python-vlc,
  python-mpv + libmpv, tdjson, and Playwright Chromium. It catches the common
  "installed but not on PATH" case (e.g. mpv) and offers one-click fixes — **Add
  to PATH** (appends to your user PATH, no admin, applied immediately),
  **Install** for the Python packages / the Stripchat browser, and **Re-check**.
  System apps (mpv/VLC) are detected and guided rather than silently installed.

### Changed
- **Settings moved to their own ⚙ Settings tab.** All settings now live in a
  dedicated, scrollable **⚙ Settings** tab; the left panel keeps just **Add
  Model** and the live status panel, so adding models stays one click away.

### Fixed
- **Preview an offline model no longer crashes the app.** An offline model
  resolved to a dead stream that could hard-crash the in-process player
  (libVLC/libmpv). Preview now runs only for **online or recording** models and
  shows a clear note otherwise.
- **Stripchat preview now works.** Preview resolves Stripchat through the same
  browserless MOUFLON path the recorder uses, so the relay can decrypt it.
- **"Settings saved" confirmation.** Saving settings shows a brief on-screen
  confirmation that auto-clears, and warns when the chosen Preview engine isn't
  installed (so the fallback is no longer a silent mystery).
- **UI no longer freezes when opening many model pages at once.** "Open in
  Browser" launches the tabs on a background thread instead of blocking the app.
- **Smoother UI under heavy load (monitor + scanner, recording storms).** Engine
  status updates are applied in small coalesced batches (~150 ms) instead of one
  event per model, so a scan pass or a burst of recordings no longer stalls the
  event loop.
- **Removed a UI-thread lock contention.** The status handler no longer grabs the
  recorder lock on the UI thread during a recording storm.

---

## V1.4 — 2026-06-26

First release published as an official GitHub Release — from this version on,
Scr33nX checks GitHub on startup and flags when a newer build is available.

### Added
- **Version number in the header.** The running build (e.g. `v1.3`) now shows
  next to the Scr33nX logo, so it's easy to tell which version you have when
  comparing with someone else. Driven by a single `APP_VERSION` constant.
- **Automatic update check.** On startup Scr33nX checks GitHub for the latest
  published release in the background. If a newer version exists, a clickable
  `● Update available (vX.Y)` indicator appears in the header and opens the
  releases page. It fails silently when offline and never interrupts you with a
  popup. *Note: requires releases to be published on GitHub, not just tags.*
- **Rank models from the OpenClaw bot.** New `scr33nx_ctl.py rank <model> 0-5`
  command sets a model's star rank, and `add-saved` / `add-recorder` / `record`
  gained a `--rank N` option so *"save her and rank 5"* adds **and** rates in one
  step. A bare `rank` requires the model to already be in Saved Models or the
  Recorder (same rule as the app/extension); the bot relays the error otherwise.
  Wired into the OpenClaw command map in `OPENCLAW-HOWTO.md`.

- **⛔ Force Quit / Terminate.** A new red **Terminate** button in the header
  (and a **Force Quit (Terminate)** item in the system-tray right-click menu)
  instantly hard-kills Scr33nX and its whole child-process tree — ffmpeg, the
  relay, any Playwright/Chromium — like Task Manager's *End Task*, instead of the
  graceful Quit that flushes recordings first. It confirms only when a recording
  is active, so an idle app dies immediately and a misclick mid-recording can't
  silently drop footage.

### Changed
- **Reorganized the project into a cleaner layout.** All Python source (and
  `icons/`) now lives under `src/`, and the docs (`CHANGELOG`, `CONTRIBUTING`,
  `OPENCLAW-HOWTO`, the renamed `RecordingLogics`) under `docs/`. The repo root
  is now just `README`, `CLAUDE.md`, `requirements.txt`, `StreamRecorder.bat`,
  and the top-level folders. **No change to how you run it** — double-click
  `StreamRecorder.bat` exactly as before. Stray/private files (a leftover
  `saved_models.json`, the outdated `Sample Content/` samples) were removed from
  the repo and are now git-ignored.

### Fixed
- **Taskbar icon could show blank.** With the app's explicit taskbar identity
  set, Tk's `iconbitmap`/`iconphoto` didn't reliably reach the Windows taskbar
  button, leaving a blank icon. The `.ico` is now pushed to the taskbar directly
  via `WM_SETICON` (both icon sizes), with 64-bit handle types declared so the
  handles aren't truncated.
- **Ranks no longer linger in memory after a model leaves both lists.** Removing
  a model from Saved Models (or the Recorder) while it's on no other list now
  drops its rank from the live session too, matching the on-save pruning — so a
  removed model can't report a stale rank until the next restart.

---

## V1.3 — 2026-06-24

### Added
- **⭐ 1–5 star model ranks.** Rate models on both the Recorder and Saved
  Models tabs: a sortable **RANK** column where you click a star to set 1–5
  (click the current star again to clear), or right-click → **Set Rank** to
  rate a single row or a whole checked/selected set at once. Ranks are keyed
  by model identity, so the same model shows the same stars on both tabs and
  in the browser extension, and they persist between sessions (in
  `~/.streamrecorder_config.json` under `ranks`). The Saved Models **export
  now carries ranks and merges** into an existing export file (keeps
  file-only entries, refreshes ranks) instead of overwriting; **import
  back-fills** stars onto models you already have.
- **Rank from the browser extension.** The popup (Chromium **and** Firefox)
  shows a clickable star row for the model on the current page. To avoid
  "orphan" ranks with no row to manage them, rating is only enabled once the
  model is in **Saved Models or the Recorder** — otherwise the stars are
  shown disabled with a hint. New `POST /rank {name, site, rank}` endpoint
  (rejects ranking a model that isn't on a list); `GET /status` now also
  returns `rank`.
- **Live polling in the browser-extension popup.** While the popup is open it
  keeps polling the backend, so a model's status (and rank / list membership)
  updates in place without closing and reopening it.
- **Status dashboard (left panel).** The single "N recording · N offline" line
  is now a per-site + totals breakdown: one row per site that has models —
  🟡 CB, 🔴 SC, 🔵 CS, 🟢 MFC (colors match each site's brand) — showing
  total / ▶ recording / ● online / ○ offline, plus an ALL summary line.

### Fixed
- **Privacy Mode could freeze the whole app.** Moving or resizing the window
  while the idle starfield cover was up popped a **modal** "Exit privacy mode?"
  dialog that rendered *behind* the full-window cover — the window kept the
  modal's input grab but the dialog was invisible, so the UI looked frozen
  (recordings and background threads kept running). Moving the window no
  longer prompts; clicking the cover now shows an **Exit / Stay panel drawn on
  the cover itself** — no modal, no grab, nothing that can hang the event loop.
- **UI freeze when starting (or stopping) many recordings at once.** A burst
  of starts — the monitor finding many models online, AUTO firing on all of
  them, or "Start" on a large selection — spawned one thread *and one ffmpeg
  subprocess per model simultaneously*, storming the CPU/disk and freezing
  the window. Launches now go through a bounded pool (4 concurrent) with
  per-model dedupe, so a big batch staggers smoothly instead of stampeding.
- **Silent duplicate-instance.** Starting a second Scr33nX while one was
  already running left the new one unable to bind the API port (5200); it ran
  half-working, and its tray icon and the browser extension actually
  controlled the *other* instance. The failed bind is now logged and shown in
  the Activity Log with a clear "another Scr33nX is likely running" warning.
- **Saved Models desync between the API and the lazy-built UI rows.** Adding or
  removing models via the extension/bot now stays consistent with the
  on-screen Saved rows even when the Saved tab hasn't been opened yet.

---

## V1.2 — 2026-06-13

### Added
- **🧹 CLEAR RECORDER button** (next to STOP ALL DOWNLOADS). One click to a
  clean slate: stops the recorder monitor, force-stops every active download,
  unchecks all AUTO, and removes every model from the Recorder (all sites).
  Saved Models are untouched. Confirms first. Also exposed to the bot via the
  new `POST /clear` endpoint and `scr33nx_ctl.py clear` command.
- **Bot dashboard + clear (OpenClaw).** New `GET /dashboard` endpoint returns the
  aggregate per-site (CB/SC/CS/MFC) + totals snapshot, and `POST /clear` clears
  the Recorder. Wired into `scr33nx_ctl.py` as `dashboard` and `clear` commands,
  so you can ask the bot "Scr33nX dashboard status" or "clear the recorder".
- **🎭 Stripchat Browser Fallback toggle (Settings).** Gates the Playwright
  browser fallback used when Stripchat's browserless native path can't resolve
  a stream. Enabled (default) = unchanged behavior. Disabled = native path
  only; if it fails the stream is simply not recorded and Playwright never
  launches under any circumstance.
- **Chat-bot / agent control via the local API (OpenClaw).** Scr33nX can now be
  driven from a messaging app (Telegram/WhatsApp) through an OpenClaw agent that
  calls the local API. New control script `scr33nx_ctl.py` wraps every action
  (record / stop / stop-all / add to recorder or saved / remove / AUTO on-off /
  status / start-stop the recorder monitor, saved scanner, and Telegram pipeline /
  open / close the app). Full walkthrough in the new **`OPENCLAW-HOWTO.md`**.
- **New local-API endpoints (port 5200).** Added `POST /stop_all` (stop every
  download + clear AUTO), `POST /monitor {target, enabled}` (start/stop the
  recorder monitor or saved scanner), `POST /pipeline {enabled}` (start/stop the
  Telegram pipeline), and `POST /quit` (graceful shutdown). `POST /remove` now
  also accepts `target: "saved"` to remove from Saved Models. All API-triggered
  actions use no-dialog code paths so they never block the UI thread that serves
  the API.

### Changed
- **Standardized output filenames across all sites.** A recording that never
  splits now keeps NO part suffix (`modelname_CB_20240515_143022.ts`); the
  moment a size-split happens, the first segment is renamed and the set reads
  `_part001`, `_part002`, … All parts of one recording share a single
  timestamp, and the Telegram-pipeline `.mp4` splits use the same 3-digit
  `_partNNN` padding as the recorder (was `_part1`).

### Fixed
- **STOP TELEGRAM PIPELINE now halts the uploader workers.** Previously the
  converter stopped but the Telegram upload clients kept draining the queued
  backlog. Stopping now lets the in-flight upload finish, then halts the
  uploaders and closes the TDLib clients until the pipeline is resumed.

---

## V1.1 — 2026-06-12

### Added
- **OneTab / browser integration.** Right-click on either tab now offers
  "🌐 Open in Browser" (opens each model's page as a browser tab; asks for
  confirmation above 10 tabs) and, for multi-selections, "📋 Copy as OneTab
  List" — copies one `URL | name (site)` line per model, ready to paste into
  OneTab's Import/Export URLs page. Both act on the checked set when boxes
  are checked, otherwise on the highlighted selection.
- **Status filter on both tabs.** A "Status ▾" dropdown next to the name
  filter with one checkbox per status (Online, Recording, Offline, Private,
  Checking, Error) — any combination works (e.g. Online + Recording).
  Nothing checked = show all. The view updates live: a model whose status
  changes moves in/out of the filtered list automatically, and the name
  filter and status filter combine.
- **Row checkboxes on both tabs.** A ☐/☑ box at the start of each model row
  builds an explicit working set: when any rows are checked, every bulk
  action (REC, Stop, Toggle AUTO, Remove, Add to Saved, right-click menu)
  operates on the checked rows instead of the click-selection. The counter
  label shows "✓ N checked" (click it to clear). Right-click offers
  "Check All Visible" (respects active filters — e.g. filter to Online,
  check all, act) and "Uncheck All". Checks survive filtering and sorting.
- **Faster multi-selection.** Press-and-drag across rows selects the whole
  range (with edge auto-scroll); Shift+click range-select and Ctrl+click
  toggle now work everywhere in the row (the checkbox zone ignores modified
  clicks); right-click "Check Selected" converts the highlight into checked
  boxes in one step.
- **Saved-tab bulk actions.** Right-click acts on the checked set (or the
  multi-selection): "Add to Recorder (N)" with duplicate-skipping and
  "Remove from Saved (N)" with a single log/persist instead of N.

---

## V1.0 — 2026-06-11

### Added
- **Filter boxes on the Recorder and Saved Models tabs.** Type to show only
  matching model names (debounced; hidden rows keep updating and reappear
  when the filter clears). The Saved tab shows a "X / Y shown" counter.
- **Lazy Saved Models tab.** With a large watchlist (1500+ models) the tab
  used to cost startup time, Tk memory, and engine overhead even when never
  opened. Rows are now built on the first visit to the tab, and models are
  registered in the recording engine only when the scanner starts. Status
  mirroring is preserved: a model recording in the Recorder tab shows
  RECORDING in Saved Models the moment the rows are built, and live updates
  take over from there. Export/import/persistence now read from the data
  list, so nothing is lost even if the tab is never opened.

### Fixed
- **Tray-icon hard crashes (access violations).** Two combined causes: several
  Win32 calls lacked 64-bit `restype` declarations, so ctypes truncated
  pointer-sized handles (`GetModuleHandleW`/`LoadIconW`/`CreatePopupMenu`),
  and the tray window class was re-registered on every minimize with a fresh
  WNDPROC trampoline while the previous one could still be referenced by a
  live window. The class is now registered once per process with a single
  persistent trampoline, and tray creation no longer blocks the UI thread
  (the old code could freeze the window for up to 5 s).
- **Recordings stalled after the first file split.** Split parts relaunched
  ffmpeg without a stderr-drain thread; once the pipe buffer filled, ffmpeg
  blocked mid-write until the stall detector killed it. Every launch path now
  drains stderr.
- **Auto-restart could resurrect a stopped recording.** The guard intended to
  block restarts after a stop was dead code (`if not self._running:` on a
  dict that is always truthy). Explicit stops now set a per-model flag the
  delayed restart respects. Restarts also re-resolve the stream URL instead
  of falling back to the expired one (which burned a restart attempt on a
  guaranteed failure).
- **Quit no longer freezes the window.** Closing with many active recordings
  ran the full ffmpeg flush (up to ~20 s) on the UI thread; it now runs in
  the background while the header shows STOPPING…, and quitting from the
  tray first restores the window so the confirm dialog is visible.

### Changed — UI smoothness with many recordings
- **Privacy-mode starfield (and the whole UI) no longer stutters under load.**
  Worker-thread log lines (ffmpeg stderr, relay warnings) were posted as one
  Tk event per line and flooded the event loop; they are now queued and
  inserted in one batch per 250 ms tick, and the log only autoscrolls when
  actually visible. Per-model file-size timers (blocking `getsize` calls on
  the UI thread) were replaced by a single worker-thread sweep that posts one
  batched update. Header stats are recomputed at most twice per second
  instead of on every status callback.
- **Starfield is time-based and cheaper.** Motion now advances by elapsed
  time (late frames no longer freeze-and-jump the stars), star widths are
  only rewritten when they visibly change, and the animation pauses while
  the window is minimized.
- **Monitor checks run in parallel.** Due online checks go through a small
  thread pool (8 workers) instead of one serial pass, so one slow site no
  longer delays every other model's check and the split/stall housekeeping.
  Chaturbate API calls remain globally rate-limited as before.
- **Relay housekeeping.** The prefetch cache size is tracked incrementally
  (the old per-refresh full-cache scan ran under the global lock), and a
  janitor thread prunes expired segments — previously, stopping all
  recordings could leave up to 768 MB of cached segments in RAM forever.
- **Logs moved out of the app folder** to `%LOCALAPPDATA%\Scr33nX`
  (`streamrecorder.log`, `streamrecorder_crash.log`). The app folder is often
  cloud-synced (OneDrive), where sync locks break log rotation. The crash log
  also restarts once it exceeds 1 MB instead of growing forever. The app now
  warns when the recordings output folder itself is inside a cloud-synced
  directory.

---

## 2026-06-11 — High-concurrency fixes & quality control

### Added
- **Quality caps.** Global "Max Quality (all models)" dropdown in Settings
  (Unlimited / 1080p / 720p / 480p) plus a per-model override in the model
  right-click menu (single and multi-select). Resolution order:
  per-model → global → unlimited. Applied by the relay when a recording
  (re)starts; if a stream has no variant at/below the cap, the lowest
  available is used. Stripchat is not capped (it bypasses variant selection).
- **⬇ Auto-Downgrade Struggling Streams** (Settings checkbox, off by default).
  A stream that loses ≥10 s of video within a 60 s window is restarted one
  quality step lower (720p → 480p → 240p), with a 2-minute cooldown between
  steps. Only the struggling stream is touched; models with a manual quality
  override are never auto-downgraded. The downgrade is session-only — it
  resets when the model's recording ends.
- **Beta log file in the app directory.** `streamrecorder.log` (rotating,
  5 MB × 3) next to `app.py` captures the Activity Log, ffmpeg stderr, relay
  warnings, and thread tracebacks; crash dumps go to
  `streamrecorder_crash.log`. Previously a 1 MB hidden file in the home dir.

### Fixed
- **Mass segment loss with many concurrent recordings.** The relay's shared
  prefetch pool starved at ~10–15 simultaneous streams: 16 workers, each
  stallable for up to 60 s (3 × 20 s retries), and a 300 MB cache cap that
  silently disabled prefetching entirely. Now 64 workers, fail-fast segment
  fetches (2 tries, 5 s connect / 10 s read), 768 MB cache cap with a logged
  warning when hit, and a larger upstream connection pool (32/128).
- **ffmpeg "Error number -138" connecting to the relay.** The relay's listen
  backlog was the Python default of 5 pending connections; raised to 128 so
  dozens of concurrent ffmpeg processes don't get connection-refused.
- **Bogus upload-meter spikes** (e.g. ↑760 Mbps on a 200 Mbps line): TDLib
  reports a file as instantly uploaded when Telegram dedupes or resumes it;
  such physically implausible samples are now discarded.

## 2026-06 — Documentation

### Added
- **`RercordingLogics.md`** — full per-site technical reference ("how to make it
  work") covering the relay, resolvers, Stripchat MOUFLON/Playwright paths, the
  MyFreeCams FCS websocket, ffmpeg invocation, exit codes, and a troubleshooting
  map. Written so another engineer or LLM can pick up the recording pipeline cold.
- **This `CHANGELOG.md`** to track changes going forward.

---

## 2026-06 — MyFreeCams support

### Added
- **MyFreeCams (`MFC`) recording.** New `mfc.py` resolver speaks MFC's FCS chat
  protocol over a guest websocket (no public API exists): fetches `serverconfig.js`,
  connects `wss://{xchat}.myfreecams.com/fcsl`, does a USERNAMELOOKUP, maps the
  video-state (`vs`) to online/away/private/offline, and probes candidate HLS edge
  URLs (`f4v_cmaf` / `f4v_mobile`) until one answers. Records through the existing
  relay + `ffmpeg -c copy` path.
- MFC integrated into the saved-watchlist bulk scanner (one websocket per sweep),
  with `PRIVATE` status + 5-minute cooldown for away/private/group shows.
- `websocket-client` added to `requirements.txt`.

## 2026-06 — Branding & icons

### Changed
- App now sets its Windows `AppUserModelID` so the taskbar shows the devil icon
  instead of python.exe's default icon.
- Browser extensions (Chromium **and** Firefox) use the red devil icon in the
  manifest and popup header.
- Devil app icon recolored to brand red for contrast on the dark theme.

### Added
- Devil app icon, upload-speed meter, and settings-checkbox icons.

## Earlier — Reliability, relay & UI

### Added
- **Segment-drop fix for concurrent recordings:** the local relay (`cb_relay.py`)
  now prefetches upcoming HLS segments in parallel (16 workers, in-memory cache)
  so slow streams catch up instead of dropping 1–2 s chunks.
- **Live bandwidth meter** (`↓ X.X Mbps`) in the header — counts every byte the
  relay fetches upstream.
- **Dropped-segment warnings** — toast + Activity Log entry when a stream loses
  segments because bandwidth can't keep up (toggle in Settings).
- **Highest-bitrate variant pinning** through the relay, so ffmpeg can never
  silently fall back to a lower resolution mid-recording.
- **Native (browserless) Stripchat recording** via MOUFLON decryption
  (`stripchat_native.py`), with automatic Playwright/Chromium fallback
  (`stripchat_live.py`) when keys rotate or the model isn't public.
- Camsoda recording (highest-quality, via relay).
- "Copy Model URL" right-click menu entry.
- Site hashtag in Telegram upload captions.

### Changed
- App renamed to **Scr33nX**; UI redesigned to an elegant black & red minimalist
  theme.
- Improved recorder reliability and overall app UI/settings.
- Hide the console window of ffmpeg spawned inside `stripchat_live`.

### Fixed
- Corrupted `.ts` files: ffmpeg is now shut down gracefully (`q`) so the MPEG-TS
  trailer flushes.
- Camsoda recording (extension whitelist / relay routing).
