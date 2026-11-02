# Local Control API (port 5200)

While Scr33nX is running it serves a small HTTP API on
`http://127.0.0.1:5200`, used by the [[Browser Extension|Browser-Extension]]
**and** by external automation (e.g. the [[OpenClaw Bot|OpenClaw-Bot]]).

> **Security:** the API is **loopback‑only** (no remote access) and **open by
> default** — anything running on your PC can call it. Don't expose port 5200
> to the network. Optionally set an **API token** in ⚙ Settings → Local API:
> every request must then carry it in the `X-Api-Token` header (or `?token=`
> on GETs) or it gets `401 unauthorized`. The browser extension (⚙ **API
> token…** in its popup) and `scr33nx_ctl.py` (reads the app's config file,
> or the `SCR33NX_TOKEN` env var) both support it.

## Endpoints

| Method & path | Body | Action |
|---|---|---|
| `GET /status` | `?name=&site=` | model state: `in_recorder`, `in_saved`, `status`, `auto`, `rank` |
| `GET /models` | — | bulk snapshot: every Recorder + Saved model with the same per‑model fields as `/status`, plus per‑model `aka`/`linked_recording` and a global `recording` count — one call for clients tracking many models (the extension badges use it) |
| `GET /preview` | `?name=&site=&quality=&any=` | live relay URL for an online/recording Recorder model (`quality` = height cap in px, 0 = Max; `any=1` also resolves models the Recorder doesn't track, fresh from the site) — what the extension's hover mini player plays. Web UI only |
| `GET /links` | — | identity groups (`links`) + same‑username‑on‑another‑site `suggestions` |
| `POST /link` | `{a:{name,site}, b:{name,site}}` | mark two tracked models as the same person — ranks sync across the group (highest wins); `/status` & `/models` report the aliases |
| `POST /unlink` | `{name, site}` | remove one model from its identity group |
| `GET /dashboard` | — | aggregate snapshot: per‑site (`CB`/`SC`/`CS`/`MFC`) + `all` totals of `total`/`recording`/`online`/`offline` |
| `POST /add` | `{name, site, target}` | add to `recorder` or `saved` |
| `POST /record` | `{name, site, action}` | `start` / `stop` recording one model |
| `POST /auto` | `{name, site, enabled}` | toggle AUTO for a model |
| `POST /rank` | `{name, site, rank}` | set a model's 0–5 star rank (`0` clears); the model must already be in Saved Models or the Recorder |
| `POST /remove` | `{name, site, target}` | remove from `recorder` or `saved` |
| `POST /stop_all` | — | stop every active download + clear all AUTO |
| `POST /clear` | — | pause **both** monitors, force-stop all downloads, clear AUTO, remove every Recorder model (Saved list kept; scanner paused so nothing resumes) |
| `POST /monitor` | `{target, enabled}` | start/stop the `recorder` monitor or `saved` scanner |
| `POST /pipeline` | `{enabled}` | start/stop the Telegram upload pipeline (starts in stand‑by) |
| `POST /pipeline/stage` | `{convert?, upload?}` | tick/untick the Convert and/or Upload stages; applies live if running, otherwise on next start |
| `POST /quit` | — | gracefully shut the app down |

All POST actions run through no‑dialog code paths so they never block the UI
thread that serves the API.

## Command‑line helper

`scr33nx_ctl.py` wraps every endpoint (plus `open`, which launches the app):

```
python scr33nx_ctl.py <command> [args]
python scr33nx_ctl.py --help
```

See [[OpenClaw Bot|OpenClaw-Bot]] for the full command list and how a chat bot
drives these from your phone.
