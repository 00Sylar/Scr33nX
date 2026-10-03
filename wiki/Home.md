# Scr33nX Wiki

**Scr33nX** is a lightweight Windows desktop app that automatically records live
streams from **Chaturbate**, **Stripchat**, **Camsoda**, and **MyFreeCams** — at
the highest available quality by default, with optional global and per‑model
quality caps for heavy multi‑model sessions.

Recordings are pulled through a local smart relay that prefetches HLS segments in
parallel, so dozens of simultaneous recordings stay smooth instead of dropping
segments when bandwidth gets tight.

---

## Start here

- **New to Scr33nX?** → [[Installation]], then [[Usage]]
- **Configuring it?** → [[Settings]]
- **Curious how recording works under the hood?** → [[Recording Internals|Recording-Internals]]
- **Automating it?** → [[Local Control API|Local-Control-API]] · [[OpenClaw Bot|OpenClaw-Bot]]
- **Something broken?** → [[Troubleshooting]]

## Pages

| Page | What's in it |
|---|---|
| [[Installation]] | Python, ffmpeg, dependencies, running the app |
| [[Features]] | Everything Scr33nX can do, by area |
| [[Usage]] | Day‑to‑day: add models, record, split, rank |
| [[Settings]] | Every setting in the left panel explained |
| [[Browser Extension\|Browser-Extension]] | One‑click add from Chrome/Firefox |
| [[Telegram Pipeline\|Telegram-Pipeline]] | Auto‑convert + upload finished recordings |
| [[Local Control API\|Local-Control-API]] | The HTTP API on port 5200 |
| [[OpenClaw Bot\|OpenClaw-Bot]] | Drive Scr33nX from your phone |
| [[Recording Internals\|Recording-Internals]] | Relay, resolvers, quality pinning |
| [[Troubleshooting]] | Common problems and fixes |
| [[Release History\|Release-History]] | Version‑by‑version changes |

---

## Two interfaces, one engine

Since **V2.0**, Scr33nX ships two interfaces on the same recording engine,
settings, and config files:

- **Default** — a modern native window (Windows WebView2), launched by
  `Scr33nX.bat` (or `Scr33nX-WebUI.bat`).
- **Classic** — the original Tk interface, launched by `Scr33nX-Classic.bat`
  (or `Scr33nX.bat --classic`).

Everything in this wiki applies to both — only the look differs. See
[[Installation]] for details.

## Requirements at a glance

- **Windows 10/11**
- **WebView2 Runtime** — for the default UI; built into Windows 11, usually
  present on Windows 10 via Edge auto‑update
- **Python 3.10+** (add to PATH during install)
- **ffmpeg**
- **Playwright Chromium** — only for the Stripchat browser fallback
- **A proxy / VPN** — optional; only if a site refuses your network (see
  [[Settings]] → 🌐 Proxy)

> The canonical, code‑level documentation lives in the repository: `README.md`,
> `docs/RecordingLogics.md`, `docs/OPENCLAW-HOWTO.md`, `docs/CONTRIBUTING.md`, and `docs/CHANGELOG.md`.
> This wiki mirrors those for easy browsing — when something is described in more
> depth in the repo, the relevant page links to it.
