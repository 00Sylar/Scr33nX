# Settings

All settings live in their own **⚙ Settings** tab (in the default UI it's the
last tab; in the classic UI it's the `⚙ Settings` tab) and are saved when you
click **💾 Save Settings**. Models, AUTO flags, Saved Models, ranks, and the
VIP list are saved to disk **immediately** on every change instead — an
abrupt close never loses those.

| Setting | What it does |
|---|---|
| **Output Folder** | Where recordings are saved. |
| **Max File Size (MB)** | Split recordings into parts at this size (empty = unlimited). |
| **Check Interval (sec)** | How often each model's status is polled (default 30). |
| **Max Quality (all models)** | Global stream‑quality cap: Unlimited / 1080p / 720p / 480p. Applied when a recording (re)starts. Right‑click a model for a per‑model override that beats this. |
| **Minimize to SysTray** | Hide to the tray instead of the taskbar. |
| **🔔 Notifications** | Master toggle for Windows toast notifications. In the default UI this is its own **Notifications** box (see below). |
| **⚠ Dropped‑Segment Warnings** | Toast when a recording is losing segments due to saturated bandwidth (always logged to the Activity Log regardless). A model simply going offline no longer triggers a false warning. |
| **⬇ Auto‑Downgrade Quality** | Restart a stream one quality step lower when it keeps losing segments (≥10 s lost within 60 s). Only that stream is touched; manual per‑model quality choices are respected. Not available for Stripchat. |
| **⛔ Low‑disk guard** | Off by default. Stops every active recording and blocks new ones when the output drive drops below **Stop below (GB free)** (default 20); recording resumes automatically only once free space climbs back to **Resume at (GB free)** (default 40) — two thresholds so the guard can't rapidly flip on/off around a single line. |
| **Max Player tiles** *(default UI only)* | Caps how many tiles can be open at once in the **▶ Player** tab (1–100, default 9). Every open tile streams live (muted), so this is also a bandwidth/CPU cap — lower it if opening many tiles strains your connection. |
| **🌐 Proxy** *(default UI)* | Send Scr33nX's traffic for a site through a **SOCKS5/HTTP proxy** — for when a site's video servers refuse your network (e.g. Chaturbate answering **403** to your whole ISP while it plays through a VPN). **Default proxy** applies to every site without its own; each site (Chaturbate, Stripchat, Camsoda, MyFreeCams) can have its own, or `direct` to never use the default. Empty = connect directly. Each row has a **Test** button (shows the proxy's exit IP and, for Chaturbate, whether the video servers accept it). Chaturbate's room lookups stay direct — only its video uses the proxy; other sites' lookups and video both use theirs. Local traffic and the rest of your PC are never proxied. **Free option:** install Cloudflare WARP (1.1.1.1), run `warp-cli mode proxy` then `warp-cli connect` (full path: `"C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"`), and use `socks5h://127.0.0.1:40000`. Applies on Save to new requests; a running recording keeps its route until it restarts. A slow proxy means dropped segments. Stripchat's browser fallback doesn't support proxies that need a username/password. |
| **API token (Local API)** *(default UI; optional, empty by default)* | Shared secret for the port‑5200 control API. When set, every request must send it in the `X-Api-Token` header or gets **401** — blocks other local apps/webpages from controlling Scr33nX. Give the same token to the browser extension (⚙ **API token…** in its popup) and the OpenClaw bot (`scr33nx_ctl.py` reads it from the config file automatically, or from `SCR33NX_TOKEN`). Applies immediately on Save. See [[Local Control API\|Local-Control-API]]. |
| **🎭 Stripchat Browser Fallback** | When Stripchat's browserless native path can't resolve a stream, fall back to the Playwright browser recorder. Enabled by default. Uncheck to record native‑only — if native fails the stream is skipped and the browser never launches. |
| **🔒 Privacy Mode** | Idle screen cover. Clicking the cover shows an Exit / Stay panel drawn on the cover (no modal). |
| **Open links with** | Which browser **Open in Browser** uses: *Ask each time*, *System default*, or a specific installed browser. Right‑click → **Open in Browser (choose…)** picks a one‑off browser without changing this default. |
| **Stream preview** | **Mode:** External window (own process, lowest impact — default) or Embedded (in‑app; built‑in player in the default UI, VLC/mpv in the classic UI). **Preview engine:** Auto / mpv / VLC for external. Optional **Player path** override. |

## 🔔 Notifications box (default UI)

The default UI splits notifications into their own card:

- **Per‑type toggles** — Recording started, Recording stopped, Dropped
  segments, Quality downgraded, Low disk space, independently. (An
  "app is broken" ffmpeg‑missing alert always fires.)
- **Toast duration** slider (1–5 s) — a hint; Windows ultimately controls how
  long a toast actually stays up.
- **🌟 VIP List** — right‑click a model (Recorder **or** Saved Models) →
  *Add to VIP List*, then enable **VIP only** to be notified for just those
  models. Global safety alerts (low disk, ffmpeg) always come through
  regardless. Manage/remove VIPs right in the box.

## 🔍 System Check

Shows whether each external tool/package is found — ffmpeg, ffplay, mpv, VLC,
python‑vlc, python‑mpv, tdjson, Playwright Chromium — and offers one‑click
fixes: **Add to PATH** for tools installed but not on PATH, and **Install**
for optional Python packages / the Stripchat browser.

## Per‑model quality override

Right‑click any model in the Recorder to set a quality cap just for that model.
A per‑model override **always beats** the global *Max Quality* and is never
touched by Auto‑Downgrade.

## Where settings are stored

- App settings, models, ranks, and the VIP list: `~/.streamrecorder_config.json`
  (ranks live under `ranks`, VIPs under `vip_list`).
- Telegram pipeline settings: `Pipeline/pipeline_settings.json`
  (see [[Telegram Pipeline|Telegram-Pipeline]]).
- Logs: `%LOCALAPPDATA%\Scr33nX\streamrecorder.log` (rotating, 5 MB × 3) —
  shared by both interfaces.
