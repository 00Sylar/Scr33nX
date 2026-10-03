# Installation

Scr33nX runs on **Windows 10/11**. There's no installer — you run it from the
project folder.

## 1. Install Python

Download **Python 3.10+** from <https://python.org>. During install, **check
"Add Python to PATH."**

## 2. Install ffmpeg

Pick one:

- `winget install Gyan.FFmpeg` — easiest; the app finds the WinGet install
  automatically, **or**
- Download `ffmpeg-release-essentials.zip` from
  <https://www.gyan.dev/ffmpeg/builds/>, extract it, and place `ffmpeg.exe` in
  the Scr33nX folder, **or**
- Add ffmpeg to your system PATH.

## 3. Install Python dependencies

From the Scr33nX folder:

```
pip install -r requirements.txt
playwright install chromium
```

`playwright install chromium` is **only** required for the Stripchat browser
fallback — skip it if you don't record Stripchat.

If you update from an older version, run `pip install -r requirements.txt`
again — V2.8 added `PySocks` (SOCKS proxy support for **Settings → Proxy**).

## 4. Run the app

Double‑click **`Scr33nX.bat`** — it launches the GUI with no console window.
Only one instance can run at a time (it binds the API on port 5200); a second
instance shows a warning and closes itself.

### Two interfaces

| | Launcher | Notes |
|---|---|---|
| **Default (recommended)** | `Scr33nX.bat` (or `Scr33nX-WebUI.bat`) | Modern native window (Windows WebView2) — smooth animations, drag‑select, built‑in preview player. Needs the **WebView2 Runtime** (built into Windows 11; usually already present on Windows 10 via Edge auto‑update). If it's missing, the app tells you and links the installer instead of crashing. |
| **Classic** | `Scr33nX-Classic.bat` (or `Scr33nX.bat --classic`) | The original Tk interface, unchanged. No WebView2 needed. |

Same recording engine, same settings, same config files either way — pick
whichever you prefer, or fall back to Classic if WebView2 isn't available.

## 5. Install the browser extension (optional)

See [[Browser Extension|Browser-Extension]] for the one‑click "add from this
page" helper (Chromium **and** Firefox).

---

## Checking your version & updates

The running version shows in the header next to the logo (e.g. `v2.0`). On
startup Scr33nX quietly checks GitHub for the latest **published release**; if a
newer one exists, a clickable **● Update available** indicator appears in the
header and opens the [Releases page](https://github.com/00Sylar/Scr33nX/releases).
The check runs in the background and fails silently when offline.

> See [[Release History|Release-History]] for what changed in each version.
