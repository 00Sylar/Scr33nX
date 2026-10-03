# Troubleshooting

> When reporting a problem, attach `streamrecorder.log` (in
> `%LOCALAPPDATA%\Scr33nX`) — it contains everything the Activity Log shows plus
> ffmpeg/relay internals.

## App / general

| Symptom | Cause / fix |
|---|---|
| Tray icon controls the "wrong" app, extension does nothing | A **second instance** is running — either interface, since both bind the same port 5200. Only one Scr33nX (default *or* classic) can run at a time; a second one shows "You can only open one instance of this app" and closes itself. |
| Default UI won't open / shows a WebView2 message | The **WebView2 Runtime** wasn't detected. Windows 11 ships it; on Windows 10 it's normally there via Edge auto‑update — if not, the message links the installer. Or just use **`Scr33nX-Classic.bat`**, which doesn't need it. |
| Window looks frozen | If it happened around Privacy Mode, update — older builds could pop a modal hidden behind the cover. Otherwise check the log for a stalled subprocess. |
| Update indicator never appears | The check needs a **published GitHub Release**, not just a tag. If no releases are published, nothing triggers. It also fails silently when offline. |

## Recording / quality

| Symptom | Cause / fix |
|---|---|
| Streams drop segments / ⚠ warnings | Your total bandwidth is the limit (~5–6 Mbps per 1080p stream). Set a global **Max Quality** cap (720p ≈ half the usage), enable **⬇ Auto‑Downgrade**, or record fewer models at once. |
| Recording quality changed mid‑stream | Shouldn't happen — the relay pins the top variant within your cap. If it does, capture the log and the model/site. |
| Chaturbate: model resolves but nothing records, every playlist 403s (Player: `manifestLoadError`), and Chaturbate's own site shows a slow slideshow instead of video | Chaturbate's video servers are refusing your whole network/ISP (not something Scr33nX sends). It plays through a VPN or Cloudflare WARP. Set a proxy for Chaturbate in [[Settings]] → 🌐 Proxy (e.g. WARP proxy mode, `socks5h://127.0.0.1:40000`) and press **Test**. |
| Stripchat won't record | If **Browser Fallback** is off and the native path fails, the stream is skipped by design. Enable the fallback in [[Settings]] (needs `playwright install chromium`). |
| Bandwidth meter shows nothing for a Stripchat recording | The Playwright browser fallback doesn't pass through the relay, so it isn't counted. That's expected. |
| `.ts` won't play | Use VLC/MPV, or convert: `ffmpeg -i input.ts -c copy output.mp4`. |

## Extension

| Symptom | Cause / fix |
|---|---|
| Popup buttons do nothing | Scr33nX must be running and listening on `localhost:5200`. Confirm only one instance is open. |
| Star rating is disabled in the popup | By design — a model must be in **Saved Models or the Recorder** before it can be ranked (prevents orphan ranks). Add it first. |

## OpenClaw bot

See the full table in `docs/OPENCLAW-HOWTO.md`. Quick hits:

| Symptom | Cause / fix |
|---|---|
| Bot: *"Is the app running?"* | Scr33nX is closed → say *"open Scr33nX"*. |
| New command does nothing / 404 | Restart Scr33nX after an `app.py` change. |
| Bot ignores a new phrasing | Send `/new` after editing `AGENTS.md`. |
| Bot: *"Unknown model"* | Model id missing the `provider/` prefix in `openclaw.json`. |
| Bot: *"out of extra usage"* | It's on the direct‑API runtime — switch to the **Claude CLI** runtime. |
| Bot: *"Provider … in cooldown (billing)"* | Stale circuit‑breaker → `openclaw gateway restart`. |
| `close` seems to hang | It flushes active recordings first (can take ~20 s with many). Normal. |

Still stuck? Open an issue on the
[repository](https://github.com/00Sylar/Scr33nX) with the relevant log excerpt.
