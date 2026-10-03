"""
app.py — Scr33nX — GUI
"""

# Single source of truth for the running version. Shown in the header and
# compared against the latest GitHub release by the update checker.
# When cutting a release (see CONTRIBUTING.md), bump this to match the new tag.
APP_VERSION = "2.8"
GITHUB_REPO = "00Sylar/Scr33nX"   # owner/repo, used for the update check

import os
import sys
import hmac
import json
import time
import math
import random
import subprocess
import threading
import contextlib
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import audit
import links as model_links
import recorder
import cb_relay
import netproxy
import settings as settings_mod
from recorder import StreamRecorder, ModelStatus
from settings import AppSettings, load_settings, save_settings, save_pipeline_settings
from notifier import send_notification

# ── File logging ──────────────────────────────────────────────────────────────
# pythonw has no console (stderr is discarded), so without this any background
# thread error vanishes. Everything recorder.py / cb_relay.py logs — including
# monitor-crash tracebacks — lands in %LOCALAPPDATA%\Scr33nX\streamrecorder.log,
# with the thread name. Falls back to the home directory when unavailable.
import logging
import faulthandler
from logging.handlers import RotatingFileHandler

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Logs live under %LOCALAPPDATA%\Scr33nX, NOT next to app.py: the app dir is
# often cloud-synced (OneDrive), where sync file locks break the rotating
# handler's rollover rename and every log write causes sync churn.
_LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA")
                        or os.path.expanduser("~"), "Scr33nX")
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
except OSError:
    _LOG_DIR = os.path.expanduser("~")
LOG_FILE = os.path.join(_LOG_DIR, "streamrecorder.log")
try:
    _handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3,
                                   encoding="utf-8")
except OSError:
    LOG_FILE = os.path.join(os.path.expanduser("~"), "streamrecorder.log")
    _handler = RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3,
                                   encoding="utf-8")
_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(threadName)-16s %(levelname)-7s %(message)s"))
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)
CRASH_FILE = os.path.join(os.path.dirname(LOG_FILE), "streamrecorder_crash.log")
try:
    # faulthandler appends forever — start fresh once it grows past 1 MB
    _crash_mode = "a"
    try:
        if os.path.getsize(CRASH_FILE) > 1_000_000:
            _crash_mode = "w"
    except OSError:
        pass
    _crash_f = open(CRASH_FILE, _crash_mode)
    faulthandler.enable(_crash_f)
except OSError:
    pass


def _is_cloud_synced(path: str) -> bool:
    """True when `path` sits inside a OneDrive/Dropbox/Google Drive folder —
    cloud sync competes with recording bandwidth and its file locks interfere
    with file splitting and pipeline renames."""
    try:
        p = os.path.realpath(path).lower()
    except OSError:
        return False
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env)
        if root:
            try:
                if p.startswith(os.path.realpath(root).lower().rstrip("\\") + "\\"):
                    return True
            except OSError:
                pass
    return ("\\onedrive" in p or "\\dropbox" in p
            or "\\google drive" in p or "\\googledrive" in p)

if sys.platform == "win32":
    from tray_win import WinTray
else:
    WinTray = None  # type: ignore

# ── Stream quality caps ───────────────────────────────────────────────────────
# Display label ↔ max variant height (px); 0 = no cap (highest available).
QUALITY_OPTIONS = {
    "Unlimited (highest)": 0,
    "1080p": 1080,
    "720p":  720,
    "480p":  480,
}


def _quality_label(height: int) -> str:
    for lbl, h in QUALITY_OPTIONS.items():
        if h == height:
            return lbl
    return f"{height}p" if height else "Unlimited (highest)"


# ── Browser detection (for "Open in Browser" picker) ──────────────────────────
# Common Windows install locations per browser; first existing path wins.
_BROWSER_CANDIDATES = (
    ("Google Chrome", (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                        r"%LocalAppData%\Google\Chrome\Application\chrome.exe")),
    ("Microsoft Edge", (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")),
    ("Mozilla Firefox", (r"%ProgramFiles%\Mozilla Firefox\firefox.exe",
                        r"%ProgramFiles(x86)%\Mozilla Firefox\firefox.exe")),
    ("Brave", (r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
               r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
               r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe")),
    ("Opera", (r"%LocalAppData%\Programs\Opera\opera.exe",
               r"%ProgramFiles%\Opera\opera.exe")),
    ("Vivaldi", (r"%LocalAppData%\Vivaldi\Application\vivaldi.exe",
                 r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe")),
)


def _detect_browsers() -> list:
    """Return [(display_name, exe_path), ...] for browsers installed on this PC."""
    found = []
    for name, paths in _BROWSER_CANDIDATES:
        for p in paths:
            ep = os.path.expandvars(p)
            if os.path.isfile(ep):
                found.append((name, ep))
                break
    return found

# ── Palette ───────────────────────────────────────────────────────────────────
# Elegant black & red — minimalist. Red is the single signature accent;
# supporting colors are muted so red and near-black carry the design.
BG      = "#0a0a0b"   # deep near-black (canvas)
BG2     = "#101011"   # header / elevated surfaces
BG3     = "#17171a"   # inputs, rows, cards
BORDER  = "#272729"   # hairline borders
ACCENT  = "#ff2b3d"   # signature red (brand, recording, primary actions)
ACCENT2 = "#c81f2e"   # deep red (hover / pressed)
GREEN   = "#3ecf8e"   # refined emerald (online / go)
RED     = "#ff2b3d"   # destructive — same red family as accent
ORANGE  = "#ff8a3d"   # warm amber (private / warnings)
YELLOW  = "#ffc14d"   # soft gold (checking)
TEXT    = "#f2f2f4"   # near-white
TEXT2   = "#8a8a90"   # muted grey
TEXT3   = "#56565c"   # dim grey
MONO    = ("Consolas", 10)
UI      = ("Segoe UI", 10)

PRIVACY_IDLE_SECONDS = 3   # idle time before privacy mode covers the window

STATUS_COLORS = {
    ModelStatus.OFFLINE:   (TEXT3,  "●  OFFLINE"),
    ModelStatus.ONLINE:    (GREEN,  "●  ONLINE"),
    ModelStatus.RECORDING: (ACCENT, "⬤  RECORDING"),
    ModelStatus.ERROR:     (RED,    "✖  ERROR"),
    ModelStatus.CHECKING:  (YELLOW, "◌  CHECKING..."),
    ModelStatus.PRIVATE:   (YELLOW, "🔒  PRIVATE / TICKET"),
}

# Reverse map: status-cell label text → ModelStatus (drives the status filter)
STATUS_BY_LABEL = {label: st for st, (_c, label) in STATUS_COLORS.items()}

# Status tag names used in the Treeview
STATUS_TAGS = {
    ModelStatus.OFFLINE:   "s_offline",
    ModelStatus.ONLINE:    "s_online",
    ModelStatus.RECORDING: "s_recording",
    ModelStatus.ERROR:     "s_error",
    ModelStatus.CHECKING:  "s_checking",
    ModelStatus.PRIVATE:   "s_private",
}


# ── Local API server (browser-extension bridge) ───────────────────────────────

_API_PORT = 5200  # v2 (TEST) uses 5200 to coexist with v1's 5199


class _ApiHandler(BaseHTTPRequestHandler):
    _app: "StreamRecorderApp" = None  # set after App() is built

    # ModelStatus → wire string, shared by /status and /models
    _STATUS_STR = {
        ModelStatus.OFFLINE:   "offline",
        ModelStatus.ONLINE:    "online",
        ModelStatus.RECORDING: "recording",
        ModelStatus.CHECKING:  "checking",
        ModelStatus.ERROR:     "error",
        ModelStatus.PRIVATE:   "private",
    }

    def _auth_ok(self) -> bool:
        """When Settings has an API token, every request must present it in
        the X-Api-Token header (or ?token= for GET). Empty token (the
        default) keeps the API open exactly as before."""
        token = (getattr(getattr(self._app, "settings", None),
                         "api_token", "") or "").strip()
        if not token:
            return True
        sent = self.headers.get("X-Api-Token", "") or ""
        if not sent:
            qs = parse_qs(urlparse(self.path).query)
            sent = qs.get("token", [""])[0]
        return hmac.compare_digest(sent, token)

    def _rows_guard(self):
        """The web UI guards its shared dicts with _rows_lock; honor it here
        so an /add landing mid-iteration can't corrupt a state pass. The
        classic app has no lock — fall back to a no-op context."""
        return getattr(self._app, "_rows_lock", None) or contextlib.nullcontext()

    def do_OPTIONS(self):
        self._cors(200)
        self.end_headers()

    def do_GET(self):
        if not self._auth_ok():
            self._json({"ok": False, "error": "unauthorized — set the API "
                        "token from Scr33nX Settings in this client"}, 401)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/dashboard":
            # Aggregate per-site + totals snapshot (read-only — safe off the
            # Tk thread, same as the /status reads below).
            self._json(self._app._api_dashboard())
            return
        if parsed.path == "/models":
            # Bulk snapshot of every Recorder + Saved model — one call for
            # clients that track many models at once (extension badges).
            self._handle_models()
            return
        if parsed.path == "/links":
            # Identity groups + same-name suggestions (read-only snapshot).
            self._json(self._app._links_payload())
            return
        if parsed.path != "/status":
            self._json({"error": "not found"}, 404)
            return
        qs   = parse_qs(parsed.query)
        name = qs.get("name", [""])[0].strip().lower()
        site = qs.get("site", [""])[0].strip().lower()
        if not name or not site:
            self._json({"error": "missing name or site"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        sid = f"saved:{key}"
        cfg = app.recorder.models.get(key)
        status_str = self._STATUS_STR.get(cfg.status, "offline") if cfg else None
        # Linked identities: every alias with its own live state, plus a
        # shortcut for "the same person is already recording elsewhere".
        aka, linked_rec = [], None
        for k2 in app._link_aka(name, site):
            n2, s2 = model_links.split_key(k2)
            cfg2 = app.recorder.models.get(k2)
            st2 = self._STATUS_STR.get(cfg2.status, "offline") if cfg2 else None
            aka.append({"name": n2, "site": s2, "status": st2,
                        "in_recorder": k2 in app._rows,
                        "in_saved": f"saved:{k2}" in app._saved_data,
                        "rank": app._get_rank(n2, s2)})
            if st2 == "recording" and linked_rec is None:
                linked_rec = {"name": n2, "site": s2}
        self._json({
            "in_recorder": key in app._rows,
            "in_saved":    sid in app._saved_data,
            "status":      status_str,
            "auto":        app._auto_rec.get(key, False),
            "rank":        app._get_rank(name, site),
            "aka":         aka,
            "linked_recording": linked_rec,
        })

    def _handle_models(self):
        """GET /models — every Recorder + Saved model in one response, with the
        same per-model fields as /status. Read-only snapshot (copy-then-read
        under the rows guard), so it's safe off the Tk thread."""
        app = self._app
        with self._rows_guard():
            rec_keys   = list(app._rows)
            saved_keys = list(app._saved_data)
            links_copy = [list(g) for g in app._links]
        entries: dict = {}
        for key in rec_keys:                      # "site:name"
            site, _, name = key.partition(":")
            entries[(site, name)] = {"in_recorder": True, "in_saved": False}
        for sid in saved_keys:                    # "saved:site:name"
            parts = sid.split(":", 2)
            if len(parts) != 3:
                continue
            _, site, name = parts
            e = entries.setdefault((site, name),
                                   {"in_recorder": False, "in_saved": False})
            e["in_saved"] = True
        models, recording = [], 0
        for (site, name), e in sorted(entries.items()):
            key = f"{site}:{name}"
            cfg = app.recorder.models.get(key)
            status = self._STATUS_STR.get(cfg.status, "offline") if cfg else None
            if status == "recording":
                recording += 1
            models.append({
                "name": name, "site": site, "status": status,
                "in_recorder": e["in_recorder"], "in_saved": e["in_saved"],
                "auto": app._auto_rec.get(key, False),
                "rank": app._get_rank(name, site),
            })
        # Second pass: linked identities. Statuses were just computed, so an
        # alias look-up is a dict hit, not another engine read.
        status_by_key = {f"{m['site']}:{m['name']}": m["status"] for m in models}
        for m in models:
            aka = model_links.aka(links_copy, f"{m['site']}:{m['name']}")
            m["aka"] = aka
            lr = None
            for k2 in aka:
                st2 = status_by_key.get(k2)
                if st2 is None:                 # alias not tracked → ask engine
                    cfg2 = app.recorder.models.get(k2)
                    st2 = (self._STATUS_STR.get(cfg2.status, "offline")
                           if cfg2 else None)
                if st2 == "recording":
                    n2, s2 = model_links.split_key(k2)
                    lr = {"name": n2, "site": s2}
                    break
            m["linked_recording"] = lr
        self._json({"ok": True, "recording": recording, "models": models})

    def do_POST(self):
        if not self._auth_ok():
            self._json({"ok": False, "error": "unauthorized — set the API "
                        "token from Scr33nX Settings in this client"}, 401)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/add":
            self._handle_add()
        elif parsed.path == "/record":
            self._handle_record()
        elif parsed.path == "/remove":
            self._handle_remove()
        elif parsed.path == "/auto":
            self._handle_auto()
        elif parsed.path == "/rank":
            self._handle_rank()
        elif parsed.path == "/link":
            self._handle_link()
        elif parsed.path == "/unlink":
            self._handle_unlink()
        elif parsed.path == "/stop_all":
            self._handle_stop_all()
        elif parsed.path == "/clear":
            self._handle_clear()
        elif parsed.path == "/monitor":
            self._handle_monitor()
        elif parsed.path == "/pipeline":
            self._handle_pipeline()
        elif parsed.path == "/pipeline/stage":
            self._handle_pipeline_stage()
        elif parsed.path == "/quit":
            self._handle_quit()
        else:
            self._json({"error": "not found"}, 404)

    def _handle_add(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name   = body.get("name",   "").strip().lower()
        site   = body.get("site",   "").strip().lower()
        target = body.get("target", "recorder")
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        if site not in ("chaturbate", "stripchat", "camsoda", "myfreecams"):
            self._json({"ok": False, "error": f"unsupported site: {site}"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        sid = f"saved:{key}"
        # Pre-check so we return a useful error before scheduling
        if target == "saved" and sid in app._saved_data:
            self._json({"ok": False, "error": "Already in Saved Models"})
            return
        if target != "saved" and key in app._rows:
            self._json({"ok": False, "error": "Already in Recorder"})
            return
        # Write the membership entry synchronously (plain dict write, no
        # widgets touched) before scheduling the UI/engine work on the main
        # thread. Otherwise a /rank call that lands right after this /add
        # response can run its own live-state check before the deferred
        # _add_to_saved/_add_to_recorder has actually executed, and gets
        # rejected even though the client just got {"ok": true} — the star
        # rating silently doesn't save.
        if target == "saved":
            with self._rows_guard():
                app._saved_data[sid] = {"name": name, "site": site}
            app.after(0, lambda n=name, s=site:
                      app._finish_add_saved(n, s, source="api"))
        else:
            with self._rows_guard():
                app._rows[key] = True
            app.after(0, lambda n=name, s=site:
                      app._finish_add_recorder(n, s, source="api"))
        self._json({"ok": True})

    def _handle_record(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name   = body.get("name",   "").strip().lower()
        site   = body.get("site",   "").strip().lower()
        action = body.get("action", "").strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        if action not in ("start", "stop"):
            self._json({"ok": False, "error": "action must be 'start' or 'stop'"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        if key not in app._rows:
            self._json({"ok": False, "error": "Model is not in the Recorder list"})
            return

        # Run the network/ffmpeg work off the HTTP handler thread so the
        # response returns quickly.
        def _run():
            try:
                if action == "start":
                    app.recorder.start_recording(name, site)
                else:
                    app.recorder.stop_recording(name, site)
            except Exception as e:
                app.after(0, lambda: app._log_add(
                    f"[api /record {action}] {name}/{site}: {e}", "warn"))

        threading.Thread(target=_run, daemon=True,
                         name=f"api-{action}-{name}").start()
        self._json({"ok": True})

    def _handle_remove(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name   = body.get("name",   "").strip().lower()
        site   = body.get("site",   "").strip().lower()
        target = body.get("target", "recorder").strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        app = self._app
        if target == "saved":
            sid = f"saved:{site}:{name}"
            if sid not in app._saved_data:
                self._json({"ok": False, "error": "Model is not in Saved Models"})
                return
            app.after(0, lambda s=sid: app._remove_saved(s, source="api"))
            self._json({"ok": True})
            return
        if target != "recorder":
            self._json({"ok": False, "error": f"unsupported target: {target}"}, 400)
            return
        key = f"{site}:{name}"
        if key not in app._rows:
            self._json({"ok": False, "error": "Model is not in the Recorder list"})
            return
        cfg = app.recorder.models.get(key)
        if cfg and cfg.status == ModelStatus.RECORDING:
            self._json({"ok": False,
                        "error": "Stop the recording before removing"})
            return
        app.after(0, lambda n=name, s=site:
                  app._do_remove_from_recorder(n, s, source="api"))
        self._json({"ok": True})

    def _handle_auto(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name    = body.get("name", "").strip().lower()
        site    = body.get("site", "").strip().lower()
        enabled = bool(body.get("enabled", False))
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        app = self._app
        key = f"{site}:{name}"
        if key not in app._rows:
            self._json({"ok": False, "error": "Model is not in the Recorder list"})
            return
        app.after(0, lambda: app._set_auto(key, enabled))
        self._json({"ok": True, "auto": enabled})

    def _handle_rank(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name = body.get("name", "").strip().lower()
        site = body.get("site", "").strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        try:
            rank = int(body.get("rank", 0))
        except (TypeError, ValueError):
            self._json({"ok": False, "error": "rank must be an integer 0-5"}, 400)
            return
        if not 0 <= rank <= 5:
            self._json({"ok": False, "error": "rank must be 0-5"}, 400)
            return
        app = self._app
        # A rank must belong to a model that's actually on a list — otherwise it
        # would be an invisible "orphan" rank with no row to manage it. Clearing
        # (rank 0) is always allowed so a stale rank can be wiped.
        key = f"{site}:{name}"
        if rank > 0 and key not in app._rows and f"saved:{key}" not in app._saved_data:
            self._json({"ok": False,
                        "error": "Add the model to Saved Models or Recorder "
                                 "before ranking"})
            return
        app.after(0, lambda n=name, s=site, r=rank:
                  app._set_rank_many([(n, s)], r, source="api"))
        self._json({"ok": True, "rank": rank})

    def _handle_link(self):
        """POST /link {a:{name,site}, b:{name,site}} — mark two tracked models
        as the same person. Group membership is merged synchronously (so an
        immediate /status sees it); rank reconciliation + persist run on the
        main thread like every other mutation."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        pair = []
        for side in ("a", "b"):
            d = body.get(side) or {}
            name = str(d.get("name", "")).strip().lower()
            site = str(d.get("site", "")).strip().lower()
            if not name or not site:
                self._json({"ok": False,
                            "error": f"missing name or site for '{side}'"}, 400)
                return
            if site not in ("chaturbate", "stripchat", "camsoda", "myfreecams"):
                self._json({"ok": False,
                            "error": f"unsupported site: {site}"}, 400)
                return
            pair.append(model_links.norm_key(name, site))
        key_a, key_b = pair
        if key_a == key_b:
            self._json({"ok": False, "error": "cannot link a model to itself"})
            return
        app = self._app
        # Both sides must be tracked — a link is metadata about models you
        # keep, not a free-floating note.
        for k in pair:
            if k not in app._rows and f"saved:{k}" not in app._saved_data:
                self._json({"ok": False,
                            "error": f"{k} is not in Saved Models or the "
                                     f"Recorder — add it first"})
                return
        with self._rows_guard():
            group = list(model_links.link(app._links, key_a, key_b))
        app.after(0, lambda: app._finish_link(key_a, key_b, source="api"))
        self._json({"ok": True, "group": group})

    def _handle_unlink(self):
        """POST /unlink {name, site} — remove one model from its group."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        name = str(body.get("name", "")).strip().lower()
        site = str(body.get("site", "")).strip().lower()
        if not name or not site:
            self._json({"ok": False, "error": "missing name or site"}, 400)
            return
        app = self._app
        key = model_links.norm_key(name, site)
        with self._rows_guard():
            found = model_links.unlink(app._links, key)
        if not found:
            self._json({"ok": False, "error": "model is not linked"})
            return
        app.after(0, lambda: app._finish_unlink(key, source="api"))
        self._json({"ok": True})

    def _handle_stop_all(self):
        """Force-stop every active download and clear AUTO (no confirm dialog)."""
        self._app.after(0, self._app._api_stop_all)
        self._json({"ok": True})

    def _handle_clear(self):
        """Clean-slate the Recorder (no confirm dialog): stop monitor + all
        downloads, clear AUTO, remove every model. Saved Models untouched."""
        self._app.after(0, lambda: self._app._do_clear_recorder(via_api=True))
        self._json({"ok": True})

    def _handle_monitor(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        target  = body.get("target", "").strip().lower()
        enabled = bool(body.get("enabled", False))
        if target not in ("recorder", "saved"):
            self._json({"ok": False,
                        "error": "target must be 'recorder' or 'saved'"}, 400)
            return
        app = self._app
        app.after(0, lambda t=target, e=enabled: app._api_set_monitor(t, e))
        self._json({"ok": True, "target": target, "enabled": enabled})

    def _handle_pipeline(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        enabled = bool(body.get("enabled", False))
        app = self._app
        app.after(0, lambda e=enabled: app._api_set_pipeline(e))
        self._json({"ok": True, "enabled": enabled})

    def _handle_pipeline_stage(self):
        """Set the Convert/Upload stage toggles. Body may contain "convert" and/or
        "upload" (bool). Works whether the pipeline is running or stopped — like
        ticking the checkboxes in the app."""
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._json({"ok": False, "error": "invalid JSON"}, 400)
            return
        convert = body.get("convert", None)
        upload = body.get("upload", None)
        if convert is None and upload is None:
            self._json({"ok": False, "error": "specify 'convert' and/or 'upload'"}, 400)
            return
        c = None if convert is None else bool(convert)
        u = None if upload is None else bool(upload)
        app = self._app
        app.after(0, lambda: app._api_set_pipeline_stage(c, u))
        self._json({"ok": True, "convert": c, "upload": u})

    def _handle_quit(self):
        # Reply first, THEN schedule the shutdown (which stops this very
        # server) so the client still receives the response.
        self._json({"ok": True})
        self._app.after(0, self._app._api_quit)

    def _cors(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Api-Token")

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self._cors(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence default access logs


# ─────────────────────────────────────────────────────────────────────────────

class StreamRecorderApp(tk.Tk):
    # Max recordings to *launch* concurrently. Steady-state recording count is
    # unbounded; this only staggers the start storm (network fetch + ffmpeg
    # spawn per model) so a big batch doesn't freeze the UI.
    _LAUNCH_POOL_SIZE = 4

    def __init__(self):
        super().__init__()
        self.title("Scr33nX")
        self._set_app_icon()
        self.geometry("1060x700")
        self.minsize(900, 520)
        self.configure(bg=BG)

        self.settings = load_settings()
        netproxy.configure(self.settings.proxy_default, self.settings.proxies)
        self.recorder = StreamRecorder()
        self.recorder.output_dir     = self.settings.output_dir
        self.recorder.max_size_mb    = self.settings.max_size_mb
        self.recorder.check_interval = self.settings.check_interval
        self.recorder.on_status_change = self._cb_status
        self.recorder.on_log           = self._cb_log
        self.recorder.on_notification  = self._cb_notif
        self.recorder.gap_warnings_enabled = self.settings.gap_warnings_enabled

        self._rows: dict[str, bool] = {}          # key → exists flag
        self._saved_rows: dict[str, bool] = {}    # saved-only models (view only)
        # Recording launches are throttled through a small pool. A burst —
        # the monitor finding many models online at once, AUTO firing on all
        # of them, or "Start" on a big selection — used to spawn one thread
        # (and one ffmpeg subprocess) PER model simultaneously, storming the
        # CPU/disk and freezing the Tk UI. The pool caps how many start at
        # once; _launching dedupes so the same model isn't queued twice.
        self._rec_pool = ThreadPoolExecutor(max_workers=self._LAUNCH_POOL_SIZE,
                                            thread_name_prefix="rec-launch")
        self._launching: set[str] = set()
        self._launching_lock = threading.Lock()
        self._auto_rec: dict[str, bool] = {}      # key → auto-rec state (recorder tab)
        self._model_q: dict[str, int] = {}        # key → per-model quality cap (height px, 0 = default)
        # The recorder answers the relay's per-stream quality queries; the app
        # just feeds it the global cap and shares the per-model override dict.
        self.recorder.quality_global = self.settings.max_quality
        self.recorder.quality_overrides = self._model_q
        self.recorder.auto_downgrade_enabled = self.settings.auto_downgrade_enabled
        self.recorder.playwright_fallback_enabled = self.settings.playwright_fallback_enabled
        self.recorder.low_disk_guard_enabled = self.settings.low_disk_guard_enabled
        self.recorder.low_disk_stop_gb = self.settings.low_disk_stop_gb
        self.recorder.low_disk_resume_gb = self.settings.low_disk_resume_gb
        self._monitoring_recorder = False
        self._monitoring_saved    = False
        self._tray: Optional[WinTray] = None
        self._hiding_to_tray = False
        # Tray callbacks fire on the tray thread — they set these events,
        # which a Tk after-loop polls (never call Tk from the tray thread).
        self._tray_show_evt = threading.Event()
        self._tray_quit_evt = threading.Event()
        self._tray_term_evt = threading.Event()
        self._tray_poll_id: Optional[str] = None
        # Worker-thread log lines arrive at a high rate when many recordings
        # struggle (ffmpeg stderr + relay warnings). Queue them and insert in
        # one batch per 250 ms tick — one Tk event per LINE flooded the event
        # loop and made the privacy starfield (and the whole UI) stutter.
        # MUST exist before any add_model/_restore_models call: the recorder's
        # on_log callback appends here.
        self._log_queue: deque = deque()
        # Status callbacks are queued and applied in batches by _drain_status
        # (one Tk pass per ~150 ms, coalesced per model). A flood of per-model
        # after() calls during a scan or a recording storm used to saturate the
        # event loop and freeze the UI.
        self._status_queue: deque = deque()
        # Status callbacks mark stats dirty; one 500 ms tick recomputes them
        # instead of a full row scan per status event.
        self._stats_dirty = False
        # File sizes are polled by ONE worker thread (os.path.getsize off the
        # Tk thread — it can block on cloud-synced folders) and applied in a
        # single batched UI update; replaces the old per-model after() chains.
        self._size_cache: dict[str, str] = {}
        # Saved Models is lazy: _saved_data (sid → {name, site}) is the source
        # of truth, loaded from settings at startup. Treeview rows are built on
        # the first tab visit; engine registration happens at scanner start.
        # A 1500+ watchlist otherwise costs startup time, Tk memory, and makes
        # every engine scan iterate the full list even when the tab is unused.
        self._saved_data: dict[str, dict] = {}
        # Per-model 0-5 star rank, keyed by "site:name" (identity, not list) so
        # the same model shows the same rank in the Recorder tab, the Saved tab
        # and the browser extension. Persisted in settings.ranks.
        self._ranks: dict[str, int] = {
            k: int(v) for k, v in (getattr(self.settings, "ranks", None) or {}).items()
            if v
        }
        # Cross-site identity links: groups of "site:name" keys that are the
        # same person (see links.py). Additive settings key — never touches
        # models/saved_models/ranks.
        self._links: list[list[str]] = model_links.sanitize(
            getattr(self.settings, "model_links", None))
        self._link_ignores: list[list[str]] = [
            sorted(x) for x in (getattr(self.settings, "link_ignores", None) or [])
            if isinstance(x, (list, tuple))]
        self._saved_built = False
        self._filter_jobs: dict[str, Optional[str]] = {"rec": None, "saved": None}
        # Last applied column sort per tree ("_site_"/"_ssite_" → (col, reverse)),
        # re-applied after every filter pass so the sort survives status updates.
        self._last_sort: dict[str, tuple[str, bool]] = {}
        # Row checkboxes (☐/☑ glyph in the name column): an explicit working
        # set per tab. When any row is checked, bulk actions operate on the
        # checked set instead of the click-selection.
        self._checked: set[str] = set()
        self._saved_checked: set[str] = set()
        # Press-row per tree, drives drag-selection (press a row, drag down →
        # the whole range gets selected, like a rubber band over a list)
        self._drag_anchor: dict[str, Optional[str]] = {"rec": None, "saved": None}
        self._build_styles()
        self._build_ui()
        self._restore_models()
        self._start_api_server()
        self._privacy_init()
        if settings_mod.LOAD_WARNING:
            self._log_add(settings_mod.LOAD_WARNING, "error")
        audit.log_event("startup", source="ui", app="classic",
                        version=APP_VERSION, recorder=len(self._rows),
                        saved=len(self._saved_data), ranked=len(self._ranks))
        if _is_cloud_synced(self.settings.output_dir):
            self._log_add("⚠ Output folder is inside a cloud-synced directory "
                          "(OneDrive/Dropbox) — sync uploads compete with "
                          "recording bandwidth and file locks can break "
                          "splitting. A local folder is strongly recommended.",
                          "warn")
        self._bw_prev: Optional[tuple] = None   # (monotonic_ts, bytes_total)
        self._bw_mbps = 0.0
        self._ul_prev: Optional[tuple] = None   # (monotonic_ts, bytes_uploaded)
        self._ul_mbps = 0.0
        threading.Thread(target=self._size_sweep_loop, daemon=True,
                         name="size-sweep").start()
        self.after(150, self._drain_status)
        self.after(250, self._drain_logs)
        self.after(500, self._stats_tick)
        self.after(1000, self._bw_tick)
        self.after(5000, self._sync_monitor_buttons)
        threading.Thread(target=self._check_for_updates, daemon=True,
                         name="update-check").start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if WinTray is not None:
            self.bind("<Unmap>", self._on_window_unmap)

    # ── Update check ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ver(s: str) -> tuple:
        """'v1.3' / '1.3.2' → (1, 3) / (1, 3, 2) for comparison. Junk → ()."""
        nums = []
        for part in str(s).lstrip("vV").split("."):
            part = "".join(c for c in part if c.isdigit())
            if not part:
                break
            nums.append(int(part))
        return tuple(nums)

    def _check_for_updates(self):
        """Background: ask GitHub for the latest release and, if it's newer
        than APP_VERSION, reveal the header update indicator. Re-checks every
        24 h so a long-running app still learns about a release. Runs off the
        Tk thread; fails silently when offline. Never pops a modal (would
        freeze the event loop) — see CLAUDE.md."""
        import urllib.request
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        while True:
            try:
                req = urllib.request.Request(
                    url, headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": f"Scr33nX/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                latest = str(data.get("tag_name") or "").strip()
                cur, new = self._parse_ver(APP_VERSION), self._parse_ver(latest)
                if new and new > cur:
                    self.after(0, lambda l=latest: self._lbl_update.config(
                        text=f"● Update available ({l})"))
                    return   # found one — the pill stays up; stop polling
            except Exception:
                pass   # offline / rate-limited / no releases — stay quiet
            time.sleep(86400)

    # ── App icon ──────────────────────────────────────────────────────────────

    def _set_app_icon(self):
        base = os.path.dirname(os.path.abspath(__file__))
        self._hdr_icon = None
        # Own AppUserModelID so the taskbar shows our window icon
        # instead of grouping under python.exe's rocket icon.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Scr33nX.App")
        except Exception:
            pass
        try:
            self.iconbitmap(os.path.join(base, "icons", "devil.ico"))
        except Exception:
            pass
        try:
            png = tk.PhotoImage(file=os.path.join(base, "icons", "devil.png"))
            self.iconphoto(True, png)
            # 512px source → ~28px header logo
            self._hdr_icon = png.subsample(18, 18)
        except Exception:
            pass
        # Force the .ico onto the taskbar button via WM_SETICON. With an explicit
        # AppUserModelID set above, iconbitmap/iconphoto don't reliably reach the
        # taskbar, so push both icon sizes directly. restype/argtypes are declared
        # so 64-bit handles aren't truncated (same class of bug as the tray crashes).
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.LoadImageW.restype  = wintypes.HANDLE
            u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                       wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                       wintypes.UINT]
            u32.GetParent.restype  = wintypes.HWND
            u32.GetParent.argtypes = [wintypes.HWND]
            u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
            ico = os.path.join(base, "icons", "devil.ico")
            hwnd = u32.GetParent(self.winfo_id())
            IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
            WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
            big   = u32.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                                   LR_LOADFROMFILE | LR_DEFAULTSIZE)
            small = u32.LoadImageW(None, ico, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            if big:
                u32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            if small:
                u32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        except Exception:
            pass

    # ── Styles ────────────────────────────────────────────────────────────────

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT,
                    fieldbackground=BG3, font=UI)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TEXT, font=UI)

        for name, bg, fg, abg in [
            ("Accent.TButton", ACCENT, BG,    ACCENT2),
            ("Green.TButton",  GREEN,  BG,    "#32a877"),
            ("Red.TButton",    RED,    TEXT,  ACCENT2),
            ("Ghost.TButton",  BG3,    TEXT,  BORDER),
            ("Flat.TButton",   BG2,    TEXT2, BG3),
        ]:
            s.configure(name, background=bg, foreground=fg,
                        font=("Segoe UI Semibold", 10),
                        padding=(10, 5), relief="flat", borderwidth=0)
            s.map(name, background=[("active", abg), ("pressed", abg)],
                  relief=[("pressed", "flat")])

        s.configure("TEntry", fieldbackground=BG3, foreground=TEXT,
                    insertcolor=ACCENT, relief="flat", padding=(8, 6))
        s.map("TEntry", fieldbackground=[("disabled", BG2)],
              foreground=[("disabled", TEXT3)])
        s.configure("TCombobox", fieldbackground=BG3, background=BG3,
                    foreground=TEXT, arrowcolor=TEXT2, relief="flat", padding=(8, 5))
        s.map("TCombobox", fieldbackground=[("readonly", BG3)],
              selectbackground=[("readonly", BG3)],
              selectforeground=[("readonly", TEXT)])
        s.configure("Vertical.TScrollbar", background=BG3, troughcolor=BG2,
                    relief="flat", arrowcolor=TEXT3, borderwidth=0)
        s.configure("TMenubutton", background=BG3, foreground=TEXT,
                    arrowcolor=TEXT2, relief="flat", padding=(8, 4),
                    font=("Segoe UI", 9))
        s.map("TMenubutton", background=[("active", BORDER)])
        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=TEXT2,
                    font=("Segoe UI Semibold", 10), padding=(16, 8), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", BG3)],
              foreground=[("selected", ACCENT)])

        # ── Treeview dark style ──
        s.configure("Dark.Treeview",
                    background=BG2, foreground=TEXT, fieldbackground=BG2,
                    font=("Segoe UI", 10), rowheight=38,
                    borderwidth=0, relief="flat")
        s.configure("Dark.Treeview.Heading",
                    background=BG3, foreground=TEXT3,
                    font=("Segoe UI Semibold", 9),
                    borderwidth=0, relief="flat", padding=(8, 6))
        s.map("Dark.Treeview.Heading",
              background=[("active", BORDER)])
        s.map("Dark.Treeview",
              background=[("selected", BG3)],
              foreground=[("selected", ACCENT)])

    # ── UI Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG2, height=54)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        if self._hdr_icon is not None:
            tk.Label(hdr, image=self._hdr_icon, bg=BG2
                     ).pack(side="left", padx=(16, 6))
        else:
            tk.Label(hdr, text="⬤", fg=ACCENT, bg=BG2,
                     font=("Segoe UI", 17)).pack(side="left", padx=(16,4))
        tk.Label(hdr, text="Scr33n", fg=TEXT, bg=BG2,
                 font=("Segoe UI Black", 15)).pack(side="left")
        tk.Label(hdr, text="X", fg=ACCENT, bg=BG2,
                 font=("Segoe UI Black", 15)).pack(side="left", padx=(1,0))
        # Version tag — dim, just after the logo. Single source of truth is
        # APP_VERSION so it never drifts from the released tag.
        tk.Label(hdr, text=f"v{APP_VERSION}", fg=TEXT3, bg=BG2,
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=(6, 0),
                                                     pady=(6, 0), anchor="s")
        # Update indicator — hidden until the GitHub check finds a newer release.
        # Click opens the releases page. No modal (would freeze the Tk loop).
        self._lbl_update = tk.Label(hdr, text="", fg=ACCENT, bg=BG2, cursor="hand2",
                                    font=("Segoe UI Semibold", 9))
        self._lbl_update.pack(side="left", padx=(8, 0), anchor="s", pady=(6, 0))
        self._lbl_update.bind(
            "<Button-1>",
            lambda _e: __import__("webbrowser").open(
                f"https://github.com/{GITHUB_REPO}/releases/latest"))
        # Force Quit / Terminate — hard-kills the process tree (End-Task style),
        # confirming first only if a recording is active. Far-right of the header.
        self._btn_terminate = tk.Button(
            hdr, text="⛔ Terminate", command=self._force_terminate,
            bg=ACCENT, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT,
            font=("Segoe UI Semibold", 9), relief="flat", bd=0,
            cursor="hand2", padx=10, pady=3)
        self._btn_terminate.pack(side="right", padx=(0, 12), pady=10)
        self._lbl_hdr_status = tk.Label(hdr, text="● IDLE", fg=TEXT3, bg=BG2,
                                         font=("Segoe UI Semibold", 10))
        self._lbl_hdr_status.pack(side="right", padx=12)
        # Live bandwidth meter: ↓ download of all active recordings,
        # ↑ upload of the pipeline pushing to Telegram.
        self._lbl_bw_up = tk.Label(hdr, text="↑ 0.0 Mbps", fg=TEXT3, bg=BG2,
                                   font=("Segoe UI Semibold", 10))
        self._lbl_bw_up.pack(side="right", padx=(0, 4))
        self._lbl_bw = tk.Label(hdr, text="↓ 0.0 Mbps", fg=TEXT3, bg=BG2,
                                font=("Segoe UI Semibold", 10))
        self._lbl_bw.pack(side="right", padx=(0, 10))

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")

        # Body
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # Left panel
        left = tk.Frame(body, bg=BG2, width=258)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._build_left(left)

        tk.Frame(body, bg=BORDER, width=1).pack(side="left", fill="y")

        # Right notebook
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True)
        self._build_right(right)

    def _build_left(self, p):
        def label(text):
            tk.Label(p, text=text, fg=TEXT3, bg=BG2,
                     font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=16, pady=(14,3))

        label("ADD MODEL")
        tk.Label(p, text="Username or Link", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._e_name = ttk.Entry(p)
        self._e_name.pack(fill="x", padx=16, pady=(2,8))
        self._e_name.bind("<Return>", lambda e: self._add_model())
        # Auto-detect site when pasting a URL
        self._e_name.bind("<KeyRelease>", lambda e: self._auto_detect_site())

        tk.Label(p, text="Site", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._c_site = ttk.Combobox(p,
                                     values=["chaturbate","stripchat","camsoda","myfreecams"],
                                     state="readonly")
        self._c_site.set("chaturbate")
        self._c_site.pack(fill="x", padx=16, pady=(2,12))

        ttk.Button(p, text="＋  Add Model", style="Accent.TButton",
                   command=self._add_model).pack(fill="x", padx=16, pady=(0,14))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(8,0))
        self._build_stats_panel(p)

    def _build_settings_tab(self, p):
        """SETTINGS live in their own scrollable tab; the left panel keeps just
        ADD MODEL + the stats panel, freeing vertical space and decluttering."""
        canvas = tk.Canvas(p, bg=BG2, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(p, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=BG2)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win_id, width=e.width))
        # Mouse-wheel scroll only while the pointer is over this tab.
        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self._build_settings_widgets(inner)

    def _build_settings_widgets(self, p):
        def label(text):
            tk.Label(p, text=text, fg=TEXT3, bg=BG2,
                     font=("Segoe UI Semibold", 9)).pack(anchor="w", padx=16, pady=(14,3))

        label("SETTINGS")

        tk.Label(p, text="Output Folder", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        row = tk.Frame(p, bg=BG2)
        row.pack(fill="x", padx=16, pady=(2,8))
        self._e_folder = ttk.Entry(row)
        self._e_folder.pack(side="left", fill="x", expand=True)
        self._e_folder.insert(0, self.settings.output_dir)
        ttk.Button(row, text="…", style="Ghost.TButton",
                   command=self._pick_folder, width=3).pack(side="left", padx=(4,0))

        tk.Label(p, text="Max File Size (MB)", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        row2 = tk.Frame(p, bg=BG2)
        row2.pack(fill="x", padx=16, pady=(2,8))
        self._v_maxsize = tk.StringVar(
            value=str(self.settings.max_size_mb) if self.settings.max_size_mb else "")
        ttk.Entry(row2, textvariable=self._v_maxsize).pack(side="left", fill="x", expand=True)
        tk.Label(row2, text="MB", fg=TEXT3, bg=BG2, font=UI).pack(side="left", padx=(6,0))

        tk.Label(p, text="Check Interval (sec)", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._v_interval = tk.StringVar(value=str(self.settings.check_interval))
        ttk.Entry(p, textvariable=self._v_interval).pack(fill="x", padx=16, pady=(2,8))

        tk.Label(p, text="Max Quality (all models)", fg=TEXT2, bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._v_quality = tk.StringVar(value=_quality_label(self.settings.max_quality))
        ttk.Combobox(p, textvariable=self._v_quality, state="readonly",
                     values=list(QUALITY_OPTIONS)).pack(fill="x", padx=16, pady=(2,8))

        self._v_tray = tk.BooleanVar(value=self.settings.minimize_to_tray)
        tk.Checkbutton(p, text="⤵ Minimize to SysTray", variable=self._v_tray,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,4))

        self._v_notif = tk.BooleanVar(value=self.settings.notifications_enabled)
        tk.Checkbutton(p, text="🔔 Notifications", variable=self._v_notif,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_gapwarn = tk.BooleanVar(value=self.settings.gap_warnings_enabled)
        tk.Checkbutton(p, text="⚠ Dropped-Segment Warnings", variable=self._v_gapwarn,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_autodown = tk.BooleanVar(value=self.settings.auto_downgrade_enabled)
        tk.Checkbutton(p, text="⬇ Auto-Downgrade Quality",
                       variable=self._v_autodown,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_lowdisk = tk.BooleanVar(value=self.settings.low_disk_guard_enabled)
        tk.Checkbutton(p, text="⛔ Stop All on Low Disk Space",
                       variable=self._v_lowdisk, command=self._update_disk_dim,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        row_disk = tk.Frame(p, bg=BG2)
        row_disk.pack(fill="x", padx=16, pady=(2,6))
        tk.Label(row_disk, text="Stop below (GB free)", fg=TEXT3, bg=BG2, font=UI).pack(
            side="left")
        self._v_disk_stop = tk.StringVar(value=str(self.settings.low_disk_stop_gb))
        self._e_disk_stop = ttk.Entry(row_disk, textvariable=self._v_disk_stop, width=6)
        self._e_disk_stop.pack(side="left", padx=(6,16))
        tk.Label(row_disk, text="Resume at (GB free)", fg=TEXT3, bg=BG2, font=UI).pack(
            side="left")
        self._v_disk_resume = tk.StringVar(value=str(self.settings.low_disk_resume_gb))
        self._e_disk_resume = ttk.Entry(row_disk, textvariable=self._v_disk_resume, width=6)
        self._e_disk_resume.pack(side="left", padx=(6,0))
        self._update_disk_dim()

        self._v_pwfallback = tk.BooleanVar(value=self.settings.playwright_fallback_enabled)
        tk.Checkbutton(p, text="🎭 Stripchat Browser Fallback",
                       variable=self._v_pwfallback,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,0))

        self._v_privacy = tk.BooleanVar(value=self.settings.privacy_mode_enabled)
        tk.Checkbutton(p, text="🔒 Privacy Mode", variable=self._v_privacy,
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=16, pady=(0,4))

        tk.Label(p, text="Open links with", fg=TEXT2, bg=BG2, font=UI).pack(
            anchor="w", padx=16, pady=(8, 0))
        self._browser_choices = [("Ask each time", ""), ("System default", "system")]
        self._browser_choices += _detect_browsers()
        # Preserve a remembered browser whose exe isn't auto-detected.
        pref = self.settings.preferred_browser
        if pref and pref != "system" and pref not in (v for _, v in self._browser_choices):
            self._browser_choices.append((f"Custom ({os.path.basename(pref)})", pref))
        self._v_browser = tk.StringVar()
        ttk.Combobox(p, textvariable=self._v_browser, state="readonly",
                     values=[d for d, _ in self._browser_choices]).pack(
            fill="x", padx=16, pady=(2, 8))
        self._sync_browser_combo()
        if not self._v_browser.get():
            self._v_browser.set("Ask each time")

        tk.Label(p, text="Stream preview", fg=TEXT2, bg=BG2, font=UI).pack(
            anchor="w", padx=16, pady=(8, 0))
        self._v_preview_mode = tk.StringVar(
            value=("Embedded (in-app)" if self.settings.preview_mode == "embedded"
                   else "External window"))
        ttk.Combobox(p, textvariable=self._v_preview_mode, state="readonly",
                     values=["External window", "Embedded (in-app)"]).pack(
            fill="x", padx=16, pady=(2, 4))
        tk.Label(p, text="Preview engine", fg=TEXT3, bg=BG2, font=UI).pack(
            anchor="w", padx=16)
        self._v_preview_engine = tk.StringVar(
            value={"auto": "Auto", "mpv": "mpv", "vlc": "VLC"}.get(
                self.settings.preview_engine, "Auto"))
        ttk.Combobox(p, textvariable=self._v_preview_engine, state="readonly",
                     values=["Auto", "mpv", "VLC"]).pack(fill="x", padx=16, pady=(2, 4))
        tk.Label(p, text="Player path (optional, mpv.exe / vlc.exe)", fg=TEXT3,
                 bg=BG2, font=UI).pack(anchor="w", padx=16)
        self._v_preview_path = tk.StringVar(value=self.settings.preview_player_path)
        ttk.Entry(p, textvariable=self._v_preview_path).pack(
            fill="x", padx=16, pady=(2, 8))

        ttk.Button(p, text="💾  Save Settings", style="Flat.TButton",
                   command=self._save_settings).pack(fill="x", padx=16, pady=(12,4))
        self._lbl_saved = tk.Label(p, text="", bg=BG2, fg=GREEN, font=UI,
                                   anchor="center", justify="center",
                                   wraplength=240)
        self._lbl_saved.pack(fill="x", padx=16, pady=(0, 12))

        # ── System check: shows whether external tools / packages are found,
        # with one-click fixes (add-to-PATH for installed-but-unpathed tools,
        # pip installs). Prevents the silent "can't find it" class of problems.
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(4, 0))
        label("SYSTEM CHECK")
        ttk.Button(p, text="🔍  Re-check", style="Flat.TButton",
                   command=self._run_system_check).pack(fill="x", padx=16, pady=(0, 6))
        self._syscheck_frame = tk.Frame(p, bg=BG2)
        self._syscheck_frame.pack(fill="x", padx=16, pady=(0, 12))
        self._run_system_check()

    def _build_right(self, p):
        nb = ttk.Notebook(p)
        nb.pack(fill="both", expand=True)
        self._nb = nb

        # Recorder tab
        tab_m = ttk.Frame(nb)
        nb.add(tab_m, text="  Recorder  ")
        self._build_models_tab(tab_m)

        # Saved Models tab
        tab_s = ttk.Frame(nb)
        nb.add(tab_s, text="  Saved Models  ")
        self._build_saved_tab(tab_s)
        self._tab_saved = tab_s
        # Saved rows are built lazily on the first visit to the tab
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Output / Upload tab (Pipeline integration)
        tab_o = ttk.Frame(nb)
        nb.add(tab_o, text="  Output / Upload  ")
        self._build_output_tab(tab_o)

        # Log tab
        tab_l = ttk.Frame(nb)
        nb.add(tab_l, text="  Activity Log  ")
        self._build_log_tab(tab_l)

        # Settings tab (moved off the left panel)
        tab_set = ttk.Frame(nb)
        nb.add(tab_set, text="  ⚙ Settings  ")
        self._build_settings_tab(tab_set)

    # ── Models tab (Treeview — single native widget, no flicker) ─────────────

    def _build_models_tab(self, p):
        # ── Action bar ──
        bar = tk.Frame(p, bg=BG2, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._lbl_selected = tk.Label(bar, text="0 selected",
            fg=TEXT3, bg=BG2, font=("Segoe UI", 9))
        self._lbl_selected.pack(side="left", padx=(12, 10))
        # Clicking the counter clears the checked set
        self._lbl_selected.bind("<Button-1>", lambda e: self._uncheck_all("rec"))

        ttk.Button(bar, text="▶ REC", style="Green.TButton",
                   command=self._rec_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="⏹ Stop", style="Ghost.TButton",
                   command=self._stop_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="☑ Toggle AUTO", style="Accent.TButton",
                   command=self._toggle_auto_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="📁", style="Flat.TButton",
                   command=lambda: os.startfile(self.settings.output_dir)
                   ).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="✕ Remove", style="Red.TButton",
                   command=self._remove_selected).pack(side="left", padx=(0,4), pady=5)
        ttk.Button(bar, text="✕ Remove Offline", style="Ghost.TButton",
                   command=self._remove_offline).pack(side="left", padx=(0,4), pady=5)
        self._btn_saved = ttk.Button(bar, text="★ Add to Saved", style="Flat.TButton",
                                     command=self._toggle_saved_selected, state="disabled")
        self._btn_saved.pack(side="left", padx=(0,4), pady=5)
        tk.Label(bar, text="🔎", fg=TEXT3, bg=BG2,
                 font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))
        self._v_filter_rec = tk.StringVar()
        ttk.Entry(bar, textvariable=self._v_filter_rec, width=14
                  ).pack(side="left", padx=(2, 0), pady=8)
        self._v_filter_rec.trace_add(
            "write", lambda *a: self._schedule_filter("rec"))
        self._mb_status_rec = self._build_status_menubutton(bar, "rec")
        self._mb_status_rec.pack(side="left", padx=(4, 0), pady=5)

        self._btn_monitor_rec = ttk.Button(bar, text="▶  START MONITOR",
                                            style="Green.TButton",
                                            command=self._toggle_monitor_recorder)
        self._btn_monitor_rec.pack(side="right", padx=10, pady=5)
        ttk.Button(bar, text="⏹  STOP ALL DOWNLOADS", style="Red.TButton",
                   command=self._stop_all_downloads
                   ).pack(side="right", padx=(0, 0), pady=5)
        ttk.Button(bar, text="🧹  CLEAR RECORDER", style="Ghost.TButton",
                   command=self._clear_recorder
                   ).pack(side="right", padx=(0, 6), pady=5)

        # ── Treeview ──
        cols = ("rank", "status", "file", "size", "auto", "saved")
        frame = tk.Frame(p, bg=BG)
        frame.pack(fill="both", expand=True)

        self._tree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                   selectmode="extended", style="Dark.Treeview")

        # Tree column (#0) = model name — sortable
        self._tree.heading("#0", text="MODEL  ↕", anchor="w",
                           command=lambda: self._sort_tree("#0"))
        self._tree.column("#0", width=200, minwidth=120)

        self._tree.heading("rank", text="RANK  ↕", anchor="w",
                           command=lambda: self._sort_tree("rank"))
        self._tree.column("rank", width=92, minwidth=92, stretch=False)

        self._tree.heading("status", text="STATUS  ↕", anchor="w",
                           command=lambda: self._sort_tree("status"))
        self._tree.column("status", width=140, minwidth=80)

        self._tree.heading("file", text="CURRENT FILE", anchor="w")
        self._tree.column("file", width=320, minwidth=100)

        self._tree.heading("size", text="SIZE  ↕", anchor="e",
                           command=lambda: self._sort_tree("size"))
        self._tree.column("size", width=90, minwidth=60, anchor="e")

        self._tree.heading("auto", text="AUTO  ↕", anchor="center",
                           command=lambda: self._sort_tree("auto"))
        self._tree.column("auto", width=70, minwidth=50, anchor="center")

        self._tree.heading("saved", text="SAVED  ↕", anchor="center",
                           command=lambda: self._sort_tree("saved"))
        self._tree.column("saved", width=70, minwidth=50, anchor="center")

        self._sort_reverse: dict[str, bool] = {}  # track sort direction per column

        sb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._tree.pack(side="left", fill="both", expand=True)

        # Status color tags
        self._tree.tag_configure("s_offline",   foreground=TEXT3)
        self._tree.tag_configure("s_online",    foreground=GREEN)
        self._tree.tag_configure("s_recording", foreground=ACCENT)
        self._tree.tag_configure("s_error",     foreground=RED)
        self._tree.tag_configure("s_checking",  foreground=YELLOW)
        self._tree.tag_configure("site_hdr",    foreground=ACCENT,
                                  font=("Segoe UI Semibold", 9))

        # Bindings
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<B1-Motion>", lambda e: self._on_tree_drag(e, "rec"))
        self._tree.bind("<ButtonRelease-1>",
                        lambda e: self._end_tree_drag("rec"), add="+")
        self._tree.bind("<<TreeviewSelect>>", lambda e: self._on_tree_select())
        self._tree.bind("<Button-3>", self._on_tree_right_click)
        self._tree.bind("<Control-a>", lambda e: self._select_all(self._tree))

        # Context menu
        self._ctx = tk.Menu(self, tearoff=0, bg=BG3, fg=TEXT, activebackground=BG2,
                            activeforeground=ACCENT, font=UI, relief="flat", bd=0)

    # ── Treeview helpers ──────────────────────────────────────────────────────

    def _ensure_site(self, site: str):
        site_id = f"_site_{site}"
        if self._tree.exists(site_id):
            return
        label = {"chaturbate": "CHATURBATE", "stripchat": "STRIPCHAT",
                 "camsoda": "CAMSODA",
                 "myfreecams": "MYFREECAMS"}.get(site, site.upper())
        self._tree.insert("", "end", iid=site_id, text=f"  {label}",
                          values=("", "", "", "", "", ""), tags=("site_hdr",), open=True)

    # ── Row checkboxes ────────────────────────────────────────────────────────
    # Tk's Treeview has no native checkbox column, and nothing can sit left of
    # the name column — so the checkbox is a ☐/☑ glyph prefixed to the name
    # (same trick as the AUTO column). Clicking the glyph zone toggles it.

    @staticmethod
    def _row_name(tree, iid) -> str:
        return tree.item(iid, "text").lstrip("☐☑ ")

    def _set_row_glyph(self, tree, iid, checked: bool):
        tree.item(iid, text=f"{'☑' if checked else '☐'}  {self._row_name(tree, iid)}")

    def _toggle_check(self, tree, iid, checked: set):
        if iid in checked:
            checked.discard(iid)
        else:
            checked.add(iid)
        self._set_row_glyph(tree, iid, iid in checked)
        if tree is self._tree:
            self._update_selection_label()
        else:
            self._update_saved_count()

    def _check_all_visible(self, which: str):
        """Check every row currently visible (respects active filters)."""
        if which == "rec":
            tree, rows, checked = self._tree, self._rows, self._checked
        else:
            tree, rows, checked = self._stree, self._saved_rows, self._saved_checked
        for iid in rows:
            # attached to a site header = visible (filters detach hidden rows)
            if iid not in checked and tree.exists(iid) and tree.parent(iid):
                checked.add(iid)
                self._set_row_glyph(tree, iid, True)
        self._update_selection_label()
        self._update_saved_count()

    def _uncheck_all(self, which: str):
        if which == "rec":
            tree, checked = self._tree, self._checked
        else:
            tree, checked = self._stree, self._saved_checked
        for iid in list(checked):
            if tree.exists(iid):
                self._set_row_glyph(tree, iid, False)
        checked.clear()
        self._update_selection_label()
        self._update_saved_count()

    # ── Drag selection ────────────────────────────────────────────────────────
    # ttk.Treeview has Shift+click (range) and Ctrl+click (toggle) built in,
    # but no press-and-drag selection — added here: drag from a row across
    # others to select the whole range, with edge auto-scroll.

    @staticmethod
    def _visible_rows(tree) -> list:
        rows = []
        for hdr in tree.get_children(""):
            rows.extend(tree.get_children(hdr))
        return rows

    def _on_tree_drag(self, event, which: str):
        anchor = self._drag_anchor.get(which)
        tree = self._tree if which == "rec" else self._stree
        if not anchor or not tree.exists(anchor):
            return
        h = tree.winfo_height()
        if event.y < 12:
            tree.yview_scroll(-1, "units")
        elif event.y > h - 12:
            tree.yview_scroll(1, "units")
        cur = tree.identify_row(min(max(event.y, 1), h - 1))
        prefix = "_site_" if which == "rec" else "_ssite_"
        if not cur or cur.startswith(prefix):
            return "break"
        rows = self._visible_rows(tree)
        try:
            i0, i1 = rows.index(anchor), rows.index(cur)
        except ValueError:
            return "break"
        if i0 > i1:
            i0, i1 = i1, i0
        tree.selection_set(rows[i0:i1 + 1])
        return "break"

    def _end_tree_drag(self, which: str):
        self._drag_anchor[which] = None

    def _check_selection(self, which: str):
        """Convert the current click-selection into checked boxes."""
        if which == "rec":
            tree, checked, prefix = self._tree, self._checked, "_site_"
        else:
            tree, checked, prefix = self._stree, self._saved_checked, "_ssite_"
        for iid in tree.selection():
            if not iid.startswith(prefix) and iid not in checked:
                checked.add(iid)
                self._set_row_glyph(tree, iid, True)
        self._update_selection_label()
        self._update_saved_count()

    def _update_selection_label(self):
        n_chk = len(self._checked)
        if n_chk:
            # Checked set takes priority — make that visible. Click to clear.
            self._lbl_selected.configure(text=f"✓ {n_chk} checked", fg=ACCENT)
            return
        sel = [iid for iid in self._tree.selection()
               if not iid.startswith("_site_")]
        n = len(sel)
        self._lbl_selected.configure(
            text=f"{n} selected" if n else "0 selected",
            fg=TEXT if n else TEXT3)

    def _on_tree_select(self):
        self._update_selection_label()
        self._update_saved_btn()

    def _update_saved_btn(self):
        """Update the Add/Remove Saved button text based on selection."""
        keys = self._get_selected_keys()
        if not keys:
            self._btn_saved.configure(text="★ Add to Saved", state="disabled")
            return
        self._btn_saved.configure(state="normal")
        site, name = keys[0].split(":", 1)
        if self._saved_key(name, site) in self._saved_data:
            self._btn_saved.configure(text="★ Remove from Saved")
        else:
            self._btn_saved.configure(text="★ Add to Saved")

    def _toggle_saved_selected(self):
        """Add or remove selected model(s) from Saved Models."""
        keys = self._get_selected_keys()
        if not keys:
            return
        site, name = keys[0].split(":", 1)
        if self._saved_key(name, site) in self._saved_data:
            for key in keys:
                s, n = key.split(":", 1)
                self._remove_saved(self._saved_key(n, s))
        else:
            for key in keys:
                s, n = key.split(":", 1)
                self._add_to_saved(n, s)

    def _get_selected_keys(self) -> list[str]:
        """Model keys for bulk actions: the checked set when any row is
        checked, otherwise the click-selection (site headers skipped)."""
        if self._checked:
            return [k for k in self._checked if self._tree.exists(k)]
        return [iid for iid in self._tree.selection()
                if not iid.startswith("_site_")]

    def _on_tree_click(self, event):
        """Toggle the row checkbox (name-column glyph) or AUTO."""
        region = self._tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        col = self._tree.identify_column(event.x)
        iid = self._tree.identify_row(event.y)
        if not iid or iid.startswith("_site_"):
            return
        modified = event.state & 0x0005  # Shift (1) / Control (4) held
        if col == "#0" and event.x <= 48 and not modified:
            # Plain click on the glyph toggles the check; with Shift/Ctrl the
            # click falls through so native range/toggle selection works
            # everywhere in the row.
            self._toggle_check(self._tree, iid, self._checked)
            return "break"  # don't let the click also change the selection
        # Click a star in the RANK column (#1) to set 1-5 stars; clicking the
        # star that's already the rank clears it back to 0.
        if col == "#1" and not modified:
            site, name = iid.split(":", 1)
            bbox = self._tree.bbox(iid, col)
            if bbox:
                bx, _, bw, _ = bbox
                star = max(1, min(5, int((event.x - bx) / (bw / 5)) + 1))
                cur = self._get_rank(name, site)
                new = 0 if cur == star else star
                if self._confirm_rank_change(name, site, new):
                    self._set_rank_many([(name, site)], new)
            return "break"
        if not modified:
            self._drag_anchor["rec"] = iid  # start of a possible drag-select
        # Columns: #0 tree (name), #1=rank, #2=status, #3=file, #4=size,
        # #5=auto, #6=saved.
        if col == "#5":
            cur = self._auto_rec.get(iid, False)
            new_val = not cur
            self._auto_rec[iid] = new_val
            self._tree.set(iid, "auto", "☑" if new_val else "☐")
            self._persist_models()

    def _on_tree_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid or iid.startswith("_site_"):
            return
        # Add right-clicked row to selection if not already selected
        if iid not in self._tree.selection():
            self._tree.selection_set(iid)
        sel = self._get_selected_keys()
        n = len(sel)
        m = self._ctx
        m.delete(0, "end")

        if n == 1:
            # Single-model context menu
            site, name = iid.split(":", 1)
            m.add_command(label=f"▶  Start Recording  {name}",
                          command=lambda: self._toggle_rec(iid, name, site))
            m.add_command(label=f"⏹  Stop Recording  {name}",
                          command=lambda: self._stop_async(name, site))
            m.add_separator()
            auto = self._auto_rec.get(iid, False)
            m.add_command(label=f"{'☑' if auto else '☐'}  Auto-Record",
                          command=lambda: self._toggle_auto_single(iid))
            m.add_cascade(label=f"🎞  Max Quality  ({self._quality_text(iid)})",
                          menu=self._build_quality_menu([iid]))
            sid_check = self._saved_key(name, site)
            if sid_check in self._saved_data:
                m.add_command(label="✕  Remove from Saved Models",
                              command=lambda s=sid_check: self._remove_saved(s))
            else:
                m.add_command(label="⭐  Add to Saved Models",
                              command=lambda: self._add_to_saved(name, site))
            m.add_separator()
            m.add_command(label="▶  Preview",
                          command=lambda: self._preview_model(name, site))
            m.add_command(label="🔗  Copy Model URL",
                          command=lambda: self._copy_model_url(name, site))
            m.add_command(label="🌐  Open in Browser",
                          command=lambda: self._open_in_browser([iid]))
            m.add_command(label="🌐  Open in Browser (choose…)",
                          command=lambda: self._open_in_browser([iid], force_choose=True))
            m.add_command(label="📁  Open Output Folder",
                          command=lambda: os.startfile(self.settings.output_dir))
            m.add_command(label="✕  Remove Model",
                          command=lambda: self._remove_model(iid, name, site))
        else:
            # Multi-model context menu
            m.add_command(label=f"▶  Start Recording  ({n} selected)",
                          command=self._rec_selected)
            m.add_command(label=f"⏹  Stop Recording  ({n} selected)",
                          command=self._stop_selected)
            m.add_separator()
            m.add_command(label=f"☑  Toggle AUTO  ({n} selected)",
                          command=self._toggle_auto_selected)
            m.add_cascade(label=f"🎞  Max Quality  ({n} selected)",
                          menu=self._build_quality_menu(sel))
            m.add_separator()
            m.add_command(label=f"🌐  Open in Browser  ({n} selected)",
                          command=lambda s=sel: self._open_in_browser(s))
            m.add_command(label=f"🌐  Open in Browser (choose…)  ({n} selected)",
                          command=lambda s=sel: self._open_in_browser(s, force_choose=True))
            m.add_command(label=f"📋  Copy as OneTab List  ({n} selected)",
                          command=lambda s=sel: self._copy_onetab(s))
            m.add_command(label="📁  Open Output Folder",
                          command=lambda: os.startfile(self.settings.output_dir))
            m.add_command(label=f"✕  Remove  ({n} selected)",
                          command=self._remove_selected)

        # Set Rank submenu — one row or the whole selection
        m.add_separator()
        rank_items = [(k.split(":", 1)[1], k.split(":", 1)[0]) for k in sel]
        rank_lbl = "Set Rank" if n == 1 else f"Set Rank  ({n} selected)"
        rank_menu = tk.Menu(m, tearoff=0, bg=BG3, fg=TEXT,
                            activebackground=BG2, activeforeground=ACCENT,
                            font=UI, relief="flat", bd=0)
        for r in (5, 4, 3, 2, 1):
            rank_menu.add_command(
                label=self._rank_stars(r),
                command=lambda it=rank_items, rr=r: self._set_rank_many(it, rr))
        rank_menu.add_separator()
        rank_menu.add_command(
            label="☆  Clear rank",
            command=lambda it=rank_items: self._set_rank_many(it, 0))
        m.add_cascade(label=f"⭐  {rank_lbl}", menu=rank_menu)

        m.add_separator()
        raw_sel = [i for i in self._tree.selection()
                   if not i.startswith("_site_")]
        if raw_sel:
            m.add_command(label=f"☑  Check Selected  ({len(raw_sel)})",
                          command=lambda: self._check_selection("rec"))
        m.add_command(label="☑  Check All Visible",
                      command=lambda: self._check_all_visible("rec"))
        if self._checked:
            m.add_command(label=f"☐  Uncheck All  ({len(self._checked)})",
                          command=lambda: self._uncheck_all("rec"))

        m.tk_popup(event.x_root, event.y_root)

    def _quality_text(self, key: str) -> str:
        h = self._model_q.get(key, 0)
        return _quality_label(h) if h else "Default"

    def _build_quality_menu(self, keys: list[str]) -> tk.Menu:
        sub = tk.Menu(self._ctx, tearoff=0, bg=BG3, fg=TEXT, activebackground=BG2,
                      activeforeground=ACCENT, font=UI, relief="flat", bd=0)
        cur = self._model_q.get(keys[0], 0) if len(keys) == 1 else None
        opts = [("Default (use global setting)", 0)] + \
               [(lbl, h) for lbl, h in QUALITY_OPTIONS.items() if h]
        for lbl, h in opts:
            mark = "●  " if cur == h else "    "
            sub.add_command(label=f"{mark}{lbl}",
                            command=lambda h=h, ks=keys: self._set_quality(ks, h))
        return sub

    def _set_quality(self, keys: list[str], height: int):
        for key in keys:
            if height:
                self._model_q[key] = height
            else:
                self._model_q.pop(key, None)
        self._persist_models()
        names = ", ".join(k.split(":", 1)[1] for k in keys)
        lbl = _quality_label(height) if height else "Default"
        self._log_add(f"Max quality → {lbl}: {names} "
                      f"(applies on next recording start)", "accent")

    def _toggle_auto_single(self, key: str):
        self._set_auto(key, not self._auto_rec.get(key, False))

    def _set_auto(self, key: str, val: bool):
        self._auto_rec[key] = val
        self._tree.set(key, "auto", "☑" if val else "☐")
        self._persist_models()

    def _stop_all_downloads(self):
        """Force-stop every active download (all sites) and uncheck every
        AUTO toggle so the monitor doesn't restart them."""
        if not messagebox.askyesno(
                "Stop all downloads",
                "Force-stop ALL active downloads and uncheck AUTO on every model?"):
            return
        for key in list(self._rows):
            self._auto_rec[key] = False
            if self._tree.exists(key):
                self._tree.set(key, "auto", "☐")
        self._persist_models()
        self._log_add("Stopping all downloads…", "warn")
        # Killing sessions waits on graceful stops — keep it off the UI thread
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="stop-all-dl").start()

    def _clear_recorder(self):
        """Clean-slate the Recorder: stop the monitor, stop every download,
        uncheck all AUTO, and remove every model (all sites). Leaves the app
        ready to use. Saved Models are untouched."""
        if not messagebox.askyesno(
                "Clear recorder",
                "Stop everything and remove ALL models from the Recorder?\n\n"
                "This pauses both monitors, force-stops every active download, "
                "clears AUTO, and empties the Recorder list. Your Saved list is "
                "kept, but the Saved scanner is paused so nothing resumes in the "
                "background."):
            return
        self._do_clear_recorder()

    def _do_clear_recorder(self, via_api: bool = False):
        """Clean-slate the Recorder without a dialog. Shared by the button
        (after confirm) and the bot API (/clear). Force-stops every active
        download and waits for the ffmpeg processes to die BEFORE removing the
        models, so nothing is left recording in the background after the list
        clears. Both monitors are paused — the saved scanner too, so a model
        that is also Saved can't resume the instant its download is killed
        (its toggle reflects this; the Saved list itself is kept)."""
        # Stop both monitors so neither can restart a download mid-clear.
        if self._monitoring_recorder:
            self._toggle_monitor_recorder()
        if self._monitoring_saved:
            self._toggle_monitor_saved()
        # Uncheck every AUTO now (the tree rows go away once stops complete).
        row_keys = list(self._rows)
        for key in row_keys:
            self._auto_rec[key] = False
            if self._tree.exists(key):
                self._tree.set(key, "auto", "☐")
        self._log_add("Clearing recorder — force-stopping all downloads…", "warn")

        def _worker():
            # Force-stop EVERY active download (all sites/groups) and wait for
            # the processes to exit, THEN remove the models on the Tk thread.
            stopped = self.recorder.stop_all_recordings()

            def _finish():
                for key in row_keys:
                    site, name = key.split(":", 1)
                    self._do_remove_from_recorder(name, site, log_audit=False)
                audit.log_event("clear_recorder",
                                source="api" if via_api else "ui",
                                count=len(row_keys), stopped=stopped,
                                models=sorted(row_keys))
                self._uncheck_all("rec")
                self._update_stats()
                self._log_add(
                    "Recorder cleared%s — %d download(s) stopped."
                    % (" (via API)" if via_api else "", stopped), "warn")
            self.after(0, _finish)

        threading.Thread(target=_worker, daemon=True,
                         name="clear-recorder").start()

    # ── Extension/bot API actions (no modal dialogs) ─────────────────────────
    def _api_stop_all(self):
        """API: same as the Stop-All button but without the confirm dialog."""
        for key in list(self._rows):
            self._auto_rec[key] = False
            if self._tree.exists(key):
                self._tree.set(key, "auto", "☐")
        self._persist_models()
        self._log_add("Stopping all downloads (via API)…", "warn")
        threading.Thread(target=self.recorder.stop_all_recordings,
                         daemon=True, name="stop-all-dl-api").start()

    def _api_set_monitor(self, which: str, enabled: bool):
        """API: bring the recorder/saved monitor to *enabled* (idempotent)."""
        current = (self._monitoring_recorder if which == "recorder"
                   else self._monitoring_saved)
        if enabled == current:
            return  # already in the desired state
        if which == "recorder":
            self._toggle_monitor_recorder()
        else:
            self._toggle_monitor_saved()

    def _api_set_pipeline(self, enabled: bool):
        """API: bring the Telegram pipeline to *enabled* (idempotent)."""
        running = bool(self.pipeline and self.pipeline.running)
        if enabled == running:
            return
        self._toggle_pipeline(silent=True)

    def _api_set_pipeline_stage(self, convert=None, upload=None):
        """API: tick/untick the Convert/Upload stage checkboxes (no dialogs).
        Persists the choice and applies it live if the pipeline is running —
        works regardless of pipeline status, mirroring the UI checkboxes."""
        if not (hasattr(self, "_v_do_convert") and hasattr(self, "_v_do_upload")):
            return
        if convert is not None:
            self._v_do_convert.set(bool(convert))
        if upload is not None:
            self._v_do_upload.set(bool(upload))
        self._on_stage_toggle()

    def _api_quit(self):
        """API: graceful shutdown with no confirm dialog (mirrors _on_close
        minus the prompt). Used by the bot's `close` command."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._rec_pool.shutdown(wait=False, cancel_futures=True)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._lbl_hdr_status.configure(text="● STOPPING…", fg=ORANGE)
        self._stop_api_server()
        self._remove_tray()
        self._save_settings()
        def _shutdown():
            try:
                self.recorder.stop_monitor()
            except Exception:
                pass
            if hasattr(self, "pipeline"):
                try:
                    self.pipeline.stop()
                except Exception:
                    pass
            try:
                self.after(0, self.destroy)
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=_shutdown, daemon=True, name="shutdown-api").start()

    def _select_all(self, tree: ttk.Treeview):
        """Select all model rows (skip site/section headers)."""
        items = []
        for site_id in tree.get_children(""):
            items.extend(tree.get_children(site_id))
        if items:
            tree.selection_set(items)
        return "break"

    @staticmethod
    def _size_sort_key(val: str) -> float:
        """Parse '12.3 MB' / '1.23 GB' / '—' into a float for numeric sort."""
        v = val.strip()
        if not v or v == "—":
            return -1.0
        try:
            if "GB" in v:
                return float(v.replace("GB", "").strip()) * 1024
            if "MB" in v:
                return float(v.replace("MB", "").strip())
        except ValueError:
            pass
        return -1.0

    def _sort_generic(self, tree: ttk.Treeview, col: str,
                      site_prefix: str, sort_state: dict,
                      col_labels: dict):
        """Sort model rows under every site header in *tree* by *col*."""
        reverse = sort_state.get(col, False)
        sort_state[col] = not reverse

        # Remember the applied sort so filter passes can restore it —
        # _filter_tree re-attaches rows in insertion order, which used to
        # wipe the sort every time a status changed under an active filter.
        self._last_sort[site_prefix] = (col, reverse)
        self._apply_sort(tree, col, reverse, site_prefix)

        arrow = " ▼" if reverse else " ▲"
        for c, label in col_labels.items():
            tree.heading(c, text=f"{label}  {arrow}" if c == col
                         else f"{label}  ↕")

    def _apply_sort(self, tree: ttk.Treeview, col: str, reverse: bool,
                    site_prefix: str):
        """Order the visible rows under every site header by *col* (no
        direction toggle, no heading update — just the reordering)."""
        for site_id in list(tree.get_children("")):
            if not site_id.startswith(site_prefix):
                continue
            children = list(tree.get_children(site_id))
            if not children:
                continue
            if col == "#0":
                # lstrip the checkbox glyph so checked rows don't sort apart
                children.sort(
                    key=lambda iid: tree.item(iid, "text").lstrip("☐☑ ").lower(),
                    reverse=reverse)
            elif col == "size":
                children.sort(
                    key=lambda iid: self._size_sort_key(tree.set(iid, "size")),
                    reverse=reverse)
            elif col == "rank":
                children.sort(
                    key=lambda iid: tree.set(iid, "rank").count("★"),
                    reverse=reverse)
            else:
                children.sort(
                    key=lambda iid: tree.set(iid, col).strip().lower(),
                    reverse=reverse)
            for idx, iid in enumerate(children):
                tree.move(iid, site_id, idx)

    def _sort_tree(self, col: str):
        self._sort_generic(
            self._tree, col,
            site_prefix="_site_",
            sort_state=self._sort_reverse,
            col_labels={"#0": "MODEL", "rank": "RANK", "status": "STATUS",
                        "size": "SIZE", "auto": "AUTO", "saved": "SAVED"},
        )

    def _sort_stree(self, col: str):
        self._sort_generic(
            self._stree, col,
            site_prefix="_ssite_",
            sort_state=self._stree_sort_reverse,
            col_labels={"#0": "MODEL", "rank": "RANK",
                        "status": "STATUS", "size": "SIZE"},
        )

    # ── Model management ─────────────────────────────────────────────────────

    def _parse_model_input(self, raw: str) -> tuple[str, str]:
        """Parse a username or URL into (name, site).
        Supports:
          chaturbate.com/username/
          stripchat.com/username/
          camsoda.com/username/
          myfreecams.com/#username   (model lives in the URL hash fragment)
          plain username (uses the site dropdown)
        """
        raw = raw.strip().lower()
        # Strip protocol
        for prefix in ("https://", "http://", "www."):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]

        # MyFreeCams profiles are single-page-app URLs: the model name is in
        # the hash fragment (myfreecams.com/#name), not the path.
        if raw.startswith("myfreecams.com"):
            frag = raw.split("#", 1)[1] if "#" in raw else ""
            frag = frag.lstrip("/")
            if frag.startswith("model/"):
                frag = frag[len("model/"):]
            username = frag.split("/")[0].split("?")[0]
            return username.strip("/"), "myfreecams"

        # Detect site from domain
        for domain, site in (("chaturbate.com", "chaturbate"),
                             ("stripchat.com",  "stripchat"),
                             ("camsoda.com",    "camsoda")):
            if raw.startswith(domain):
                parts = raw.split("/")
                username = parts[1] if len(parts) > 1 else ""
                return username.strip("/"), site

        # Plain username — use the site dropdown
        return raw.strip("/"), self._c_site.get()

    def _auto_detect_site(self):
        """Auto-switch the site dropdown when a URL is typed/pasted."""
        text = self._e_name.get().strip().lower()
        if "chaturbate.com" in text:
            self._c_site.set("chaturbate")
        elif "stripchat.com" in text:
            self._c_site.set("stripchat")
        elif "camsoda.com" in text:
            self._c_site.set("camsoda")
        elif "myfreecams.com" in text:
            self._c_site.set("myfreecams")

    def _add_model(self):
        raw = self._e_name.get().strip()
        if not raw:
            messagebox.showwarning("Input required", "Enter a model username or link.")
            return
        name, site = self._parse_model_input(raw)
        if not name:
            messagebox.showwarning("Input required", "Could not extract a username from the input.")
            return
        key = f"{site}:{name}"
        if key in self._rows:
            messagebox.showinfo("Already added", f"{name} ({site}) is already in the list.")
            return
        self.recorder.add_model(name, site, "recorder")
        self._insert_model(name, site)
        self._e_name.delete(0, "end")
        self._persist_models()
        self._update_stats()
        self._log_add(f"Added model: {name} ({site})", "accent")

    def _insert_model(self, name: str, site: str, auto_rec: bool = False):
        key = f"{site}:{name}"
        self._ensure_site(site)
        parent = f"_site_{site}"
        auto_text  = "☑" if auto_rec else "☐"
        saved_text = "✔️" if self._saved_key(name, site) in self._saved_data else "❌"
        self._tree.insert(parent, "end", iid=key, text=f"☐  {name}",
                          values=(self._rank_stars(self._get_rank(name, site)),
                                  "●  OFFLINE", "—", "—", auto_text, saved_text),
                          tags=("s_offline",))
        self._rows[key] = True
        self._auto_rec[key] = auto_rec
        if (self._v_filter_rec.get().strip()
                or self._status_filter_set("rec") is not None):
            self._schedule_filter("rec")  # respect an active filter

    def _do_remove_from_recorder(self, name: str, site: str,
                                 source: str = "ui", log_audit: bool = True):
        """Remove a model from the Recorder tab without a confirm dialog.
        Shared by the right-click flow (after confirm) and the extension API.
        `log_audit=False` is used by bulk clears, which write ONE summary
        audit event instead of hundreds of per-model lines."""
        key = f"{site}:{name}"
        if log_audit:
            audit.log_event("recorder_remove", name=name, site=site,
                            source=source)
        self.recorder.remove_model(name, site, "recorder")
        if self._tree.exists(key):
            self._tree.delete(key)
        self._rows.pop(key, None)
        self._auto_rec.pop(key, None)
        self._model_q.pop(key, None)
        self._checked.discard(key)
        # Drop the rank if the model is now on neither list (no orphans).
        if self._saved_key(name, site) not in self._saved_data:
            self._ranks.pop(self._rank_key(name, site), None)
        site_id = f"_site_{site}"
        # Filtered-out rows are detached (parentless), so check _rows — not
        # get_children() — or we'd delete a header that still owns hidden rows.
        if (self._tree.exists(site_id)
                and not any(k.split(":", 1)[0] == site for k in self._rows)):
            self._tree.delete(site_id)
        self._persist_models()
        self._update_stats()
        self._update_selection_label()
        self._log_add(f"Removed: {name} ({site})", "warn")

    def _remove_model(self, key: str, name: str, site: str):
        if not messagebox.askyesno("Remove model", f"Remove {name} ({site})?"):
            return
        self._do_remove_from_recorder(name, site)

    def _remove_selected(self):
        keys = self._get_selected_keys()
        if not keys:
            return
        names = ", ".join(k.split(":")[1] for k in keys)
        if not messagebox.askyesno("Remove selected", f"Remove {len(keys)} model(s)?\n{names}"):
            return
        for key in keys:
            site, name = key.split(":", 1)
            audit.log_event("recorder_remove", name=name, site=site, source="ui")
            self.recorder.remove_model(name, site, "recorder")
            if self._tree.exists(key):
                self._tree.delete(key)
            self._rows.pop(key, None)
            self._auto_rec.pop(key, None)
            self._model_q.pop(key, None)
            self._checked.discard(key)
            site_id = f"_site_{site}"
            # _rows includes filter-detached rows; get_children() doesn't
            if (self._tree.exists(site_id)
                    and not any(k.split(":", 1)[0] == site for k in self._rows)):
                self._tree.delete(site_id)
            self._log_add(f"Removed: {name} ({site})", "warn")
        self._persist_models()
        self._update_stats()
        self._update_selection_label()

    def _remove_offline(self):
        """Remove every Recorder model whose row currently shows OFFLINE.
        Goes by the visible status cell (WYSIWYG — matches the status filter),
        so CHECKING/PRIVATE/ERROR rows are kept. Saved Models are untouched."""
        offline = [k for k in list(self._rows)
                   if self._tree.exists(k)
                   and STATUS_BY_LABEL.get(self._tree.set(k, "status"))
                       == ModelStatus.OFFLINE]
        if not offline:
            self._log_add("Remove Offline: no OFFLINE models in the Recorder.")
            return
        if not messagebox.askyesno(
                "Remove offline models",
                f"Remove {len(offline)} OFFLINE model(s) from the Recorder?\n\n"
                "Saved Models are kept."):
            return
        for key in offline:
            site, name = key.split(":", 1)
            self._do_remove_from_recorder(name, site)
        self._log_add(f"Removed {len(offline)} offline model(s) from the "
                      f"Recorder.", "warn")

    _SITE_URLS = {
        "chaturbate": "https://chaturbate.com/{}/",
        "stripchat":  "https://stripchat.com/{}",
        "camsoda":    "https://www.camsoda.com/{}",
        "myfreecams": "https://www.myfreecams.com/#{}",
    }

    def _copy_model_url(self, name: str, site: str):
        url = self._SITE_URLS.get(site, "https://{}/").format(name)
        self.clipboard_clear()
        self.clipboard_append(url)
        self._log_add(f"Copied URL: {url}")

    def _keys_to_models(self, keys: list, saved: bool = False) -> list:
        """Map tree row ids to (name, site, url) tuples.
        Recorder keys are "site:name"; saved keys are "saved:site:name"."""
        out = []
        for k in keys:
            if saved:
                _, site, name = k.split(":", 2)
            else:
                site, name = k.split(":", 1)
            url = self._SITE_URLS.get(site, "https://{}/").format(name)
            out.append((name, site, url))
        return out

    def _open_in_browser(self, keys: list, saved: bool = False,
                         force_choose: bool = False):
        items = self._keys_to_models(keys, saved)
        if not items:
            return
        if len(items) > 10 and not messagebox.askyesno(
                "Open in Browser",
                f"Open {len(items)} tabs in your browser?"):
            return
        target = self.settings.preferred_browser
        # Ask when nothing is remembered, or when the user explicitly asked to
        # pick a one-off browser via the "(choose…)" menu item.
        if force_choose or not target:
            choice = self._choose_browser_dialog()
            if choice is None:          # user cancelled
                return
            target, remember = choice
            if remember:
                self.settings.preferred_browser = target
                save_settings(self.settings)
                self._sync_browser_combo()
        # Launch off the UI thread — opening many tabs synchronously (one
        # Popen/webbrowser.open per model) blocked the event loop and froze the
        # app for large selections.
        threading.Thread(target=self._launch_urls, args=(items, target),
                         daemon=True, name="open-in-browser").start()

    def _launch_urls(self, items: list, target: str):
        """Open each model URL using `target` ("" / "system" = OS default,
        otherwise a browser exe path). Safe to call from a worker thread — all
        logging is marshalled back to the UI thread via after()."""
        import webbrowser
        opened = 0
        for _, _, url in items:
            ok = False
            try:
                if not target or target == "system":
                    webbrowser.open(url)
                else:
                    subprocess.Popen([target, url])
                ok = True
            except Exception:
                try:                       # fall back to the system default
                    webbrowser.open(url)
                    ok = True
                except Exception as e:
                    self.after(0, lambda e=e: self._log_add(
                        f"Browser open failed ({e}).", "warn"))
            if ok:
                opened += 1
            time.sleep(0.12)               # small stagger; don't hammer the browser
        self.after(0, lambda n=opened: self._log_add(
            f"Opened {n} model page(s) in browser"))

    def _choose_browser_dialog(self):
        """Modal picker (UI-triggered only). Returns (target, remember) or None
        if cancelled. `target` is "system" or a browser exe path."""
        win = tk.Toplevel(self)
        win.title("Open in Browser")
        win.configure(bg=BG2)
        win.resizable(False, False)
        win.transient(self)
        result = {"value": None}
        tk.Label(win, text="Open in which browser?", bg=BG2, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 8))
        sel = tk.StringVar(value="system")
        tk.Radiobutton(win, text="System default", variable=sel, value="system",
                       bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                       activeforeground=TEXT, font=UI, relief="flat").pack(
            anchor="w", padx=20)
        for name, exe in _detect_browsers():
            tk.Radiobutton(win, text=name, variable=sel, value=exe,
                           bg=BG2, fg=TEXT2, selectcolor=BG3, activebackground=BG2,
                           activeforeground=TEXT, font=UI, relief="flat").pack(
                anchor="w", padx=20)
        remember = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text="Remember my choice (change later in Settings)",
                       variable=remember, bg=BG2, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT, font=UI,
                       relief="flat").pack(anchor="w", padx=16, pady=(10, 4))
        btns = tk.Frame(win, bg=BG2)
        btns.pack(fill="x", padx=16, pady=(4, 14))

        def _ok():
            result["value"] = (sel.get(), remember.get())
            win.destroy()

        ttk.Button(btns, text="Open", style="Flat.TButton", command=_ok).pack(
            side="right")
        ttk.Button(btns, text="Cancel", style="Ghost.TButton",
                   command=win.destroy).pack(side="right", padx=(0, 8))
        win.update_idletasks()
        win.grab_set()
        win.wait_window()
        return result["value"]

    def _sync_browser_combo(self):
        """Reflect the saved preferred_browser in the Settings dropdown."""
        if not hasattr(self, "_v_browser"):
            return
        pref = self.settings.preferred_browser
        for disp, val in self._browser_choices:
            if val == pref:
                self._v_browser.set(disp)
                return

    def _copy_onetab(self, keys: list, saved: bool = False):
        """Copy models in OneTab's import format: one "URL | title" per line.
        Paste into OneTab → Import / Export URLs."""
        items = self._keys_to_models(keys, saved)
        text = "\n".join(f"{url} | {name} ({site})"
                         for name, site, url in items)
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log_add(f"Copied {len(items)} model(s) as OneTab list")

    # ── Stream preview ─────────────────────────────────────────────────────────

    def _import_mpv(self):
        """Import python-mpv, making libmpv discoverable if the user dropped
        libmpv-2.dll into the app's folder (so no PATH fiddling needed).
        Returns the module, or None if python-mpv / libmpv isn't available."""
        try:
            import mpv
            return mpv
        except Exception:
            pass
        d = os.path.dirname(os.path.abspath(__file__))   # the src/ folder
        try:
            if hasattr(os, "add_dll_directory") and os.path.isdir(d):
                os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass
        try:
            import mpv
            return mpv
        except Exception:
            return None

    def _import_vlc(self):
        """Import python-vlc. It auto-locates an installed VLC (libvlc) via the
        registry, so no manual DLL step. Returns the module or None."""
        try:
            import vlc
            return vlc
        except Exception:
            return None

    def _embedded_available(self) -> bool:
        """True if ANY embedded backend can load. The engine setting only sets
        preference/order — the open path falls back to the other backend — so
        availability is not engine-specific (e.g. engine=mpv still works via VLC
        when only VLC is installed)."""
        return self._import_mpv() is not None or self._import_vlc() is not None

    def _pip_install(self, pkg: str, on_done=None):
        """Install a Python package via pip in the background using the app's own
        interpreter. Logs progress and calls on_done(success: bool) on the UI
        thread. Used to auto-enable optional preview backends on demand."""
        self._log_add(f"Installing {pkg} … (one-time, needs internet)")
        def _do():
            ok, detail = False, ""
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = (r.returncode == 0)
                if not ok:
                    lines = (r.stderr or r.stdout or "").strip().splitlines()
                    detail = lines[-1] if lines else "pip failed"
            except Exception as e:
                detail = str(e)
            if ok:
                try:
                    import importlib
                    importlib.invalidate_caches()   # so the new package is importable now
                except Exception:
                    pass
            def _report():
                if ok:
                    self._log_add(f"{pkg} installed.", "success")
                else:
                    self._log_add(f"Could not install {pkg}: {detail}", "error")
                if on_done:
                    on_done(ok)
            self.after(0, _report)
        threading.Thread(target=_do, daemon=True, name=f"pip-{pkg}").start()

    def _preview_model(self, name: str, site: str):
        """Open a live preview of a model's stream. The upstream is resolved off
        the UI thread, wrapped in the local relay, then played either in an
        external mpv/ffplay window (default) or an embedded in-app player —
        per the Preview setting."""
        # Only preview online/recording models. An offline model resolves to a
        # dead stream, and handing that to an in-process player (libvlc/libmpv)
        # can hard-crash the whole app — so block it up front with a clear note.
        cfg = self.recorder.models.get(f"{site}:{name.lower()}")
        status = cfg.status if cfg else None
        if status not in (ModelStatus.ONLINE, ModelStatus.RECORDING):
            messagebox.showinfo(
                "Preview unavailable",
                f"{name} ({site}) isn't online right now — preview only works for "
                "online or recording models.")
            return
        title = f"{name} ({site})"
        mode = (self.settings.preview_mode or "external").lower()
        # Embedded needs python-mpv AND a loadable libmpv DLL. Check up front
        # (actually try to import — find_spec can't tell if libmpv is missing)
        # so the user gets immediate feedback instead of waiting through resolve.
        if mode == "embedded" and not self._embedded_available():
            # Auto-enable: VLC is installed but the python-vlc bridge isn't —
            # offer to install it for the user, then retry the preview.
            if self._detect_player("vlc") and self._import_vlc() is None:
                if messagebox.askyesno(
                        "Enable in-app preview",
                        "VLC is installed, but the Python bridge 'python-vlc' is "
                        "missing.\n\nInstall it now to enable in-app (embedded) "
                        "preview?  (one-time, needs internet)"):
                    self._pip_install(
                        "python-vlc",
                        on_done=lambda ok: self._preview_model(name, site)
                        if ok else None)
                    return
            if not messagebox.askyesno(
                    "Embedded preview unavailable",
                    "In-app (embedded) preview needs one of:\n"
                    "  • python-vlc + VLC installed  (pip install python-vlc), or\n"
                    "  • python-mpv + libmpv-2.dll in the 'src' folder.\n\n"
                    "Open in an external player window instead?"):
                return
            mode = "external"
        self._log_add(f"Preview: resolving {title}…")
        loading = self._preview_loading_show(title)
        threading.Thread(target=self._preview_resolve,
                         args=(name, site, title, mode, loading), daemon=True).start()

    def _preview_loading_show(self, title: str):
        """Small non-modal 'opening preview' indicator, centered over the app.
        Returns the Toplevel so it can be closed once the player appears."""
        win = tk.Toplevel(self)
        win.title("Preview")
        win.configure(bg=BG2)
        win.resizable(False, False)
        win.transient(self)
        tk.Label(win, text=f"●  Opening preview for {title}…", bg=BG2, fg=TEXT,
                 font=("Segoe UI", 11)).pack(padx=28, pady=(20, 4))
        tk.Label(win, text="Resolving the stream — this can take a few seconds.",
                 bg=BG2, fg=TEXT3, font=UI).pack(padx=28, pady=(0, 18))
        win.update_idletasks()
        try:
            x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
            y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass
        return win

    def _preview_loading_close(self, win):
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _focus_external_window(self, title: str, timeout: float = 6.0):
        """Poll for the player window by title and bring it to the foreground,
        so the preview doesn't open hidden behind other windows."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
        except Exception:
            return
        deadline = time.time() + timeout
        hwnd = 0
        while time.time() < deadline:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.25)
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 9)        # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _preview_resolve(self, name, site, title, mode, loading):
        """Worker thread: resolve the upstream stream + wrap it in the relay,
        then hand the localhost URL to the chosen player."""
        def _fail(msg, tag="error"):
            self.after(0, lambda: (self._preview_loading_close(loading),
                                   self._log_add(msg, tag)))
        try:
            # Preview only runs for a model the monitor just saw online, so
            # both paths may reuse a URL of that same age rather than resolving
            # again (for Chaturbate that also skips the global API gate).
            max_age = recorder.PREVIEW_URL_MAX_AGE
            if site == "stripchat":
                # Stripchat is MOUFLON-encrypted; resolve the keyed variant the
                # same way recording does (the relay then decrypts the playlist).
                import stripchat_native
                upstream = stripchat_native.resolve(name, max_age=max_age)
            else:
                upstream = recorder.get_stream_url(site, name, max_age=max_age)
        except Exception as e:
            _fail(f"Preview failed for {title}: {e}")
            return
        if not upstream:
            _fail(f"Preview: couldn't resolve {title} "
                  + ("(Stripchat private/needs browser path?)" if site == "stripchat"
                     else "(offline?)"), "warn")
            return
        try:
            url = cb_relay.wrap(upstream, recorder.USER_AGENT, mode=site,
                                label=f"{site}:{name}")
        except Exception as e:
            _fail(f"Preview relay error: {e}")
            return
        if mode == "embedded":
            self.after(0, lambda: self._preview_open_embedded(url, title, loading))
        else:
            self._preview_launch_external(url, title, loading)

    def _detect_player(self, kind: str):
        """Path to a specific external player ('mpv' | 'vlc' | 'ffplay'), or None.
        Honors the optional override path when it matches the kind."""
        import shutil
        override = (self.settings.preview_player_path or "").strip()
        if override and os.path.isfile(override) and kind in os.path.basename(override).lower():
            return override
        if kind == "mpv":
            return shutil.which("mpv")
        if kind == "vlc":
            p = shutil.which("vlc")
            if p:
                return p
            for c in (r"%ProgramFiles%\VideoLAN\VLC\vlc.exe",
                      r"%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"):
                c = os.path.expandvars(c)
                if os.path.isfile(c):
                    return c
            return None
        if kind == "ffplay":
            ff = getattr(self.recorder, "ffmpeg_path", "") or ""
            if ff:
                cand = os.path.join(os.path.dirname(ff), "ffplay.exe")
                if os.path.isfile(cand):
                    return cand
            return shutil.which("ffplay")
        return None

    def _find_preview_player(self):
        """Return (exe_path, kind) for an external player, honoring the engine
        preference (Auto/mpv/VLC). Auto and any unmet preference fall through to
        whatever is installed; ffplay is the universal fallback. (None, None) if
        nothing is found."""
        engine = (self.settings.preview_engine or "auto").lower()
        if engine == "vlc":
            order = ["vlc", "mpv", "ffplay"]
        elif engine == "mpv":
            order = ["mpv", "vlc", "ffplay"]
        else:
            order = ["mpv", "vlc", "ffplay"]
        for kind in order:
            p = self._detect_player(kind)
            if p:
                return p, kind
        return None, None

    def _preview_launch_external(self, url: str, title: str, loading=None):
        exe, kind = self._find_preview_player()
        if not exe:
            self.after(0, lambda: (self._preview_loading_close(loading),
                self._log_add(
                    "Preview: no player found. Install mpv (https://mpv.io) or VLC "
                    "(https://videolan.org), or set a player path in Settings.",
                    "error")))
            return
        wtitle = f"Preview — {title}"
        if kind == "mpv":
            cmd = [exe, "--profile=low-latency", "--force-window=yes",
                   "--keep-open=no", f"--title={wtitle}", url]
        elif kind == "vlc":
            cmd = [exe, "--no-video-title-show", "--network-caching=1500",
                   f"--meta-title={wtitle}", url]
        else:  # ffplay
            cmd = [exe, "-autoexit", "-window_title", wtitle, url]
        try:
            subprocess.Popen(cmd,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.after(0, lambda: (self._preview_loading_close(loading),
                self._log_add(f"Preview launch failed: {e}", "error")))
            return
        # Bring the player window to the front (it otherwise opens behind).
        threading.Thread(target=self._focus_external_window, args=(wtitle,),
                         daemon=True).start()
        self.after(0, lambda: (self._preview_loading_close(loading),
            self._log_add(f"Preview: {kind} window opened for {title}.")))

    def _preview_open_embedded(self, url: str, title: str, loading=None):
        """In-app preview. Tries the preferred engine first (python-mpv or
        python-vlc), then the other, then falls back to an external window."""
        engine = (self.settings.preview_engine or "auto").lower()
        order = ["vlc", "mpv"] if engine == "vlc" else ["mpv", "vlc"]
        for kind in order:
            if kind == "mpv":
                m = self._import_mpv()
                if m:
                    self._open_embedded_mpv(m, url, title, loading)
                    return
            else:
                v = self._import_vlc()
                if v:
                    self._open_embedded_vlc(v, url, title, loading)
                    return
        self._log_add("Embedded preview unavailable (need python-vlc+VLC or "
                      "python-mpv+libmpv); opening an external window instead.", "warn")
        self._preview_launch_external(url, title, loading)

    def _new_preview_window(self, title: str):
        """Create the embedded-preview Toplevel; returns (win, video_frame, bar)."""
        self._preview_close_embedded()          # one embedded preview at a time
        win = tk.Toplevel(self)
        win.title(f"Preview — {title}")
        win.configure(bg="black")
        win.geometry("640x420")
        video = tk.Frame(win, bg="black")
        video.pack(fill="both", expand=True)
        bar = tk.Frame(win, bg=BG2, height=34)
        bar.pack(fill="x")
        win.update_idletasks()
        return win, video, bar

    def _open_embedded_mpv(self, mpv, url, title, loading=None):
        win, video, bar = self._new_preview_window(title)
        try:
            player = mpv.MPV(wid=str(video.winfo_id()),
                             profile="low-latency", keep_open="no")
            player.play(url)
        except Exception as e:
            win.destroy()
            self._log_add(f"Embedded mpv failed ({e}); opening an external window.",
                          "warn")
            self._preview_launch_external(url, title, loading)
            return
        self._preview_loading_close(loading)
        self._preview_win = win
        self._preview_player = player
        self._preview_player_stop = player.terminate
        self._build_preview_controls(
            bar,
            lambda: setattr(player, "pause", not player.pause),
            lambda: setattr(player, "mute", not player.mute),
            lambda v: setattr(player, "volume", float(v)))
        win.protocol("WM_DELETE_WINDOW", self._preview_close_embedded)
        self._log_add(f"Embedded preview (mpv) opened for {title}.")

    def _open_embedded_vlc(self, vlc, url, title, loading=None):
        win, video, bar = self._new_preview_window(title)
        try:
            inst = vlc.Instance("--no-video-title-show", "--network-caching=1500")
            player = inst.media_player_new()
            player.set_hwnd(video.winfo_id())
            player.set_media(inst.media_new(url))
            player.play()
        except Exception as e:
            win.destroy()
            self._log_add(f"Embedded VLC failed ({e}); opening an external window.",
                          "warn")
            self._preview_launch_external(url, title, loading)
            return
        self._preview_loading_close(loading)
        self._preview_win = win
        self._preview_player = player
        self._preview_vlc_instance = inst       # keep a ref so it isn't GC'd
        self._preview_player_stop = lambda: (player.stop(), player.release())
        self._build_preview_controls(
            bar,
            player.pause,                        # VLC pause() toggles play/pause
            player.audio_toggle_mute,
            lambda v: player.audio_set_volume(int(float(v))))
        win.protocol("WM_DELETE_WINDOW", self._preview_close_embedded)
        self._log_add(f"Embedded preview (VLC) opened for {title}.")

    def _build_preview_controls(self, bar, on_pause, on_mute, on_vol):
        def _safe(fn):
            def _w(*a):
                try:
                    fn(*a)
                except Exception:
                    pass
            return _w
        ttk.Button(bar, text="⏯", style="Flat.TButton", width=3,
                   command=_safe(on_pause)).pack(side="left", padx=6, pady=4)
        ttk.Button(bar, text="🔇", style="Flat.TButton", width=3,
                   command=_safe(on_mute)).pack(side="left", padx=2, pady=4)
        ttk.Scale(bar, from_=0, to=100, value=100,
                  command=_safe(on_vol)).pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(bar, text="✕ Close", style="Ghost.TButton",
                   command=self._preview_close_embedded).pack(side="right", padx=6, pady=4)

    def _preview_close_embedded(self):
        stop = getattr(self, "_preview_player_stop", None)
        if stop is not None:
            try: stop()
            except Exception: pass
            self._preview_player_stop = None
        self._preview_player = None
        self._preview_vlc_instance = None
        win = getattr(self, "_preview_win", None)
        if win is not None:
            try: win.destroy()
            except Exception: pass
            self._preview_win = None

    def _stop_async(self, name: str, site: str):
        """Stop a recording on a worker thread — graceful_stop blocks up to
        ~15 s per process, which froze the GUI when run on the Tk thread."""
        def _do():
            self.recorder.stop_recording(name, site)
            self.after(0, lambda: self._log_add(
                f"Stopped recording: {name} ({site})", "warn"))
        threading.Thread(target=_do, daemon=True,
                         name=f"stop-{site}-{name}").start()

    def _stop_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            site, name = key.split(":", 1)
            cfg = self.recorder.models.get(key)
            if cfg and cfg.status == ModelStatus.RECORDING:
                self._stop_async(name, site)

    def _rec_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            site, name = key.split(":", 1)
            cfg = self.recorder.models.get(key)
            if cfg and cfg.status in (ModelStatus.ONLINE, ModelStatus.OFFLINE, ModelStatus.PRIVATE):
                self._toggle_rec(key, name, site)

    def _toggle_auto_selected(self):
        keys = self._get_selected_keys()
        for key in keys:
            self._toggle_auto_single(key)

    def _size_sweep_loop(self):
        """Worker thread: every 3 s stat all recording files at once and post
        ONE batched UI update (replaces a per-model after() chain that did
        blocking getsize calls on the Tk thread)."""
        while True:
            time.sleep(3)
            with self.recorder._lock:
                snap = [(k, c.session.current_file)
                        for k, c in self.recorder.models.items()
                        if c.session and c.session.current_file]
            sizes = {}
            for key, path in snap:
                try:
                    sizes[key] = os.path.getsize(path)
                except OSError:
                    pass
            if not sizes:
                continue
            try:
                self.after(0, lambda s=sizes: self._apply_sizes(s))
            except (tk.TclError, RuntimeError):
                return  # app is shutting down

    def _apply_sizes(self, sizes: dict):
        for key, size in sizes.items():
            mb = size / (1024 * 1024)
            txt = f"{mb/1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"
            if self._size_cache.get(key) == txt:
                continue  # unchanged — skip the Treeview write
            self._size_cache[key] = txt
            if key in self._rows and self._tree.exists(key):
                self._tree.set(key, "size", txt)
            sid = f"saved:{key}"
            if sid in self._saved_rows and self._stree.exists(sid):
                self._stree.set(sid, "size", txt)

    def _stats_tick(self):
        try:
            if self._stats_dirty:
                self._stats_dirty = False
                self._update_stats()
        finally:
            self.after(500, self._stats_tick)

    # ── Record toggle (per model) ─────────────────────────────────────────────

    def _launch_recording(self, name: str, site: str):
        """Start a recording through the bounded launch pool instead of a raw
        thread, so a burst of starts (auto-rec firing on many models, or a big
        manual selection) can't spawn dozens of ffmpeg processes at once and
        freeze the UI. _launching dedupes a model that's already queued."""
        key = f"{site}:{name.lower()}"
        with self._launching_lock:
            if key in self._launching:
                return
            self._launching.add(key)

        def _do():
            try:
                ok = self.recorder.start_recording(name, site)
                if not ok:
                    self.after(0, lambda: self._log_add(
                        f"{name} ({site}) is offline — cannot record.", "warn"))
            finally:
                with self._launching_lock:
                    self._launching.discard(key)

        try:
            self._rec_pool.submit(_do)
        except RuntimeError:           # pool already shut down (app closing)
            with self._launching_lock:
                self._launching.discard(key)

    def _toggle_rec(self, key: str, name: str, site: str):
        with self.recorder._lock:
            cfg = self.recorder.models.get(key)
        if not cfg:
            return
        if cfg.status == ModelStatus.RECORDING:
            self._stop_async(name, site)
        else:
            self._launch_recording(name, site)

    # ── Monitor toggles (per-tab, independent) ────────────────────────────────

    def _sync_recorder_settings(self):
        """Push current UI settings into the engine before (re)starting a monitor."""
        self.recorder.output_dir     = self._e_folder.get().strip() or self.settings.output_dir
        self.recorder.max_size_mb    = self._parse_int(self._v_maxsize.get())
        self.recorder.check_interval = self._parse_int(self._v_interval.get(), 30)

    def _refresh_hdr_status(self):
        r, s = self._monitoring_recorder, self._monitoring_saved
        if r and s:
            self._lbl_hdr_status.configure(text="● MONITORING (R+S)", fg=GREEN)
        elif r:
            self._lbl_hdr_status.configure(text="● RECORDER ACTIVE", fg=GREEN)
        elif s:
            self._lbl_hdr_status.configure(text="● SCANNER ACTIVE", fg=GREEN)
        else:
            self._lbl_hdr_status.configure(text="● IDLE", fg=TEXT3)

    def _toggle_monitor_recorder(self):
        if self._monitoring_recorder:
            # stop_monitor flushes every active ffmpeg (seconds) — keep it
            # off the Tk thread so the GUI stays responsive.
            threading.Thread(target=self.recorder.stop_monitor,
                             args=("recorder",), daemon=True,
                             name="stop-mon-recorder").start()
            self._monitoring_recorder = False
            self._btn_monitor_rec.configure(text="▶  START MONITOR", style="Green.TButton")
            self._log_add("Recorder monitor stopped.", "warn")
        else:
            self._sync_recorder_settings()
            if self.recorder.start_monitor("recorder"):
                self._monitoring_recorder = True
                self._btn_monitor_rec.configure(text="⏹  STOP MONITOR", style="Red.TButton")
                self._log_add("Recorder monitor started.", "success")
            else:
                self._log_add("Recorder monitor FAILED to start — "
                              "see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)", "error")
        self._refresh_hdr_status()

    def _toggle_monitor_saved(self):
        if self._monitoring_saved:
            threading.Thread(target=self.recorder.stop_monitor,
                             args=("saved",), daemon=True,
                             name="stop-mon-saved").start()
            self._monitoring_saved = False
            self._btn_monitor_saved.configure(text="▶  START SCANNER", style="Green.TButton")
            self._log_add("Saved Models scanner stopped.", "warn")
        else:
            self._sync_recorder_settings()
            # Lazy saved tab: make sure the rows exist and the engine knows
            # the watchlist before the scanner starts.
            self._populate_saved_tab()
            self._register_saved_models()
            if self.recorder.start_monitor("saved"):
                self._monitoring_saved = True
                self._btn_monitor_saved.configure(text="⏹  STOP SCANNER", style="Red.TButton")
                self._log_add("Saved Models scanner started.", "success")
            else:
                self._log_add("Saved Models scanner FAILED to start — "
                              "see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)", "error")
        self._refresh_hdr_status()

    def _sync_monitor_buttons(self):
        """Watchdog: if a monitor thread died (crash guard resets its running
        flag), flip the button/header back so START works again instead of
        silently pretending to monitor."""
        if self._monitoring_recorder and not self.recorder._running.get("recorder"):
            self._monitoring_recorder = False
            self._btn_monitor_rec.configure(text="▶  START MONITOR",
                                            style="Green.TButton")
            self._log_add("Recorder monitor is no longer running.", "error")
            self._refresh_hdr_status()
        if self._monitoring_saved and not self.recorder._running.get("saved"):
            self._monitoring_saved = False
            self._btn_monitor_saved.configure(text="▶  START SCANNER",
                                              style="Green.TButton")
            self._log_add("Saved Models scanner is no longer running.", "error")
            self._refresh_hdr_status()
        self.after(5000, self._sync_monitor_buttons)

    # ── Privacy Mode ──────────────────────────────────────────────────────────
    # When enabled and the window sits untouched for PRIVACY_IDLE_SECONDS, a
    # full-window starfield covers the UI (and the title is blanked) so nothing
    # is readable while AFK. Click the cover to exit (a confirm panel is drawn
    # ON the cover); the mode re-arms after the next idle period until the box
    # is unchecked.

    def _privacy_init(self):
        self._last_activity      = time.time()
        self._privacy_canvas     = None
        self._privacy_prompting  = False
        self._privacy_anim_job   = None
        self._privacy_title      = ""
        self._privacy_confirm    = None
        for seq in ("<Motion>", "<KeyPress>", "<Button>", "<MouseWheel>"):
            self.bind_all(seq, self._privacy_touch, add="+")
        # Children share the root's bindtag, so filter to root-only events
        self.bind("<Configure>", self._privacy_on_configure, add="+")
        self.after(500, self._privacy_tick)

    def _privacy_touch(self, event=None):
        self._last_activity = time.time()

    def _privacy_on_configure(self, event):
        if event.widget is not self:
            return
        # While covered, a window move/resize must NOT pop a dialog — the cover
        # (placed relwidth/relheight=1) just tracks the new size. Exiting is an
        # explicit click on the cover. (The old code opened a modal here, which
        # could hide behind the cover and hang the whole UI.)
        if self._privacy_canvas is None:
            self._last_activity = time.time()

    def _privacy_tick(self):
        try:
            if (self._v_privacy.get()
                    and self._privacy_canvas is None
                    and not self._privacy_prompting
                    and self.state() in ("normal", "zoomed")
                    and time.time() - self._last_activity >= PRIVACY_IDLE_SECONDS):
                self._privacy_engage()
        finally:
            self.after(500, self._privacy_tick)

    def _privacy_engage(self):
        self._privacy_title = self.title()
        self.title(" ")
        c = tk.Canvas(self, bg="#03030a", highlightthickness=0)
        c.place(x=0, y=0, relwidth=1, relheight=1)
        c.bind("<Button-1>", lambda e: self._privacy_prompt_exit())
        self._privacy_canvas = c
        self._privacy_scene_init()
        self._privacy_animate()

    def _privacy_disengage(self):
        if self._privacy_anim_job:
            self.after_cancel(self._privacy_anim_job)
            self._privacy_anim_job = None
        if self._privacy_canvas is not None:
            self._privacy_canvas.destroy()   # also destroys the confirm panel
            self._privacy_canvas = None
        self._privacy_confirm   = None
        self._privacy_prompting = False
        if self._privacy_title:
            self.title(self._privacy_title)
        self._last_activity = time.time()

    def _privacy_prompt_exit(self):
        # Confirm with a panel drawn ON the cover instead of a modal dialog.
        # A modal askyesno runs a nested event loop with an input grab; with
        # the full-window cover up it rendered behind the cover, so the window
        # was grabbed but the dialog was invisible — the app looked frozen.
        if self._privacy_prompting or self._privacy_canvas is None:
            return
        self._privacy_prompting = True
        c = self._privacy_canvas
        panel = tk.Frame(c, bg="#0a0a14", highlightthickness=1,
                         highlightbackground="#ff2b3d")
        tk.Label(panel, text="Exit privacy mode?", bg="#0a0a14", fg="#f2f2f4",
                 font=("Segoe UI", 12, "bold")).pack(padx=26, pady=(16, 12))
        row = tk.Frame(panel, bg="#0a0a14")
        row.pack(pady=(0, 16))
        tk.Button(row, text="Exit", width=8, relief="flat", bd=0,
                  bg="#ff2b3d", fg="#0a0a0b", activebackground="#ff5563",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=lambda: self._privacy_resolve(True)).pack(side="left", padx=6)
        tk.Button(row, text="Stay", width=8, relief="flat", bd=0,
                  bg="#17171a", fg="#8a8a90", activebackground="#222226",
                  font=("Segoe UI", 10, "bold"), cursor="hand2",
                  command=lambda: self._privacy_resolve(False)).pack(side="left", padx=6)
        panel.place(relx=0.5, rely=0.5, anchor="center")
        self._privacy_confirm = panel

    def _privacy_resolve(self, do_exit: bool):
        if self._privacy_confirm is not None:
            self._privacy_confirm.destroy()
            self._privacy_confirm = None
        self._privacy_prompting = False
        self._last_activity = time.time()
        if do_exit:
            self._privacy_disengage()

    def _privacy_scene_init(self):
        c = self._privacy_canvas
        self.update_idletasks()
        w = max(self.winfo_width(),  200)
        h = max(self.winfo_height(), 200)
        # Layered nebulas: a wide stippled base + a brighter stippled core per
        # cloud fakes a soft alpha glow (Tk canvas has no real transparency)
        self._neb_items = []
        for base, glow in (("#141031", "#241d56"), ("#0c1c33", "#17345e"),
                           ("#1c0f2e", "#352052"), ("#0e2430", "#1b4254"),
                           ("#26101b", "#471f33")):
            r = random.randint(100, 190)
            x, y = random.uniform(0, w), random.uniform(0, h)
            outer = c.create_oval(x - r, y - r, x + r, y + r,
                                  fill=base, outline="", stipple="gray50")
            ri = r * 0.55
            inner = c.create_oval(x - ri, y - ri, x + ri, y + ri,
                                  fill=glow, outline="", stipple="gray25")
            self._neb_items.append([outer, inner,
                                    random.uniform(-0.35, 0.35),
                                    random.uniform(-0.25, 0.25), r])
        # Stars: warp streaks in centered unit coords with depth z, per-star
        # speed (parallax) and color temperature, plus a twinkle phase and
        # the last width written (so unchanged widths skip itemconfigure)
        self._star_items = []
        palette = ("#ffffff", "#ffffff", "#dfe8ff", "#cfd8f5",
                   "#ffe9d0", "#bcd2ff", "#f5d7e3")
        for _ in range(170):
            item = c.create_line(0, 0, 0, 0, fill=random.choice(palette),
                                 width=1, capstyle="round")
            self._star_items.append([item,
                                     random.uniform(-1, 1),
                                     random.uniform(-1, 1),
                                     random.uniform(0.05, 1.0),
                                     random.uniform(0.003, 0.010),
                                     random.uniform(0, math.tau),
                                     1.0])
        # One shooting star, reused between flights
        self._comet = c.create_line(0, 0, 0, 0, fill="#eaf2ff",
                                    width=2, capstyle="round")
        self._comet_state = None
        self._privacy_frame = 0.0
        self._privacy_last = time.monotonic()

    def _privacy_animate(self):
        c = self._privacy_canvas
        if c is None:
            return
        # Window minimized/hidden: skip the rendering work entirely, keep a
        # slow heartbeat so the scene resumes when the window comes back.
        if self.state() not in ("normal", "zoomed"):
            self._privacy_last = time.monotonic()
            self._privacy_anim_job = self.after(500, self._privacy_animate)
            return
        # Time-based motion: when the event loop is busy and frames arrive
        # late, advance the scene by elapsed time instead of one fixed step
        # per frame — late frames no longer make the stars freeze-and-jump.
        now = time.monotonic()
        dt = min(now - self._privacy_last, 0.2)
        self._privacy_last = now
        step = dt / 0.033 if dt > 0 else 1.0
        w = max(c.winfo_width(), 2)
        h = max(c.winfo_height(), 2)
        cx, cy = w / 2, h / 2
        self._privacy_frame += step
        f = self._privacy_frame
        for s in self._star_items:
            item, x, y, z, spd, ph, last_w = s
            z -= spd * step
            if z <= 0.03:
                x, y, z = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
            px = cx + (x / z) * cx * 0.85
            py = cy + (y / z) * cy * 0.85
            if px < -20 or px > w + 20 or py < -20 or py > h + 20:
                x, y, z = random.uniform(-1, 1), random.uniform(-1, 1), 1.0
                px = cx + x * cx * 0.85
                py = cy + y * cy * 0.85
            # Tail end sampled at a slightly earlier depth — close, fast stars
            # stretch into warp streaks; distant ones stay near-points
            zt = min(z + spd * 7, 1.0)
            qx = cx + (x / zt) * cx * 0.85
            qy = cy + (y / zt) * cy * 0.85
            depth = 1.0 - z
            width = max(1.0, 3.2 * depth + 0.7 * math.sin(f * 0.18 + ph))
            c.coords(item, qx, qy, px, py)
            if abs(width - last_w) >= 0.2:
                c.itemconfigure(item, width=width)
                s[6] = width
            s[1], s[2], s[3] = x, y, z
        # Shooting star: rare, fast diagonal streak with a long tail
        if self._comet_state is None:
            if random.random() < 0.006 * step:
                self._comet_state = [random.uniform(0.1, 0.9) * w, -12.0,
                                     random.uniform(-7, 7),
                                     random.uniform(8, 13)]
        else:
            st = self._comet_state
            st[0] += st[2] * step
            st[1] += st[3] * step
            c.coords(self._comet, st[0] - st[2] * 5, st[1] - st[3] * 5,
                     st[0], st[1])
            if st[0] < -80 or st[0] > w + 80 or st[1] > h + 80:
                c.coords(self._comet, 0, 0, 0, 0)
                self._comet_state = None
        for n in self._neb_items:
            outer, inner, dx, dy, r = n
            c.move(outer, dx * step, dy * step)
            c.move(inner, dx * step, dy * step)
            x0, y0, x1, y1 = c.coords(outer)
            mx = my = 0
            if x1 < 0:
                mx = w + 2 * r
            elif x0 > w:
                mx = -(w + 2 * r)
            if y1 < 0:
                my = h + 2 * r
            elif y0 > h:
                my = -(h + 2 * r)
            if mx or my:
                c.move(outer, mx, my)
                c.move(inner, mx, my)
        self._privacy_anim_job = self.after(33, self._privacy_animate)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _cb_status(self, key: str, status: ModelStatus, detail: str):
        # Called from worker threads — just queue (deque.append is thread-safe).
        # _drain_status applies them in coalesced batches on the UI thread.
        self._status_queue.append((key, status, detail))

    def _drain_status(self):
        try:
            if self._status_queue:
                # Coalesce: keep only the latest status per model in this window
                # so a burst (scan pass, recording storm) is one cheap UI pass.
                latest = {}
                while self._status_queue:
                    key, status, detail = self._status_queue.popleft()
                    latest[key] = (status, detail)
                for key, (status, detail) in latest.items():
                    self._apply_status(key, status, detail)
        finally:
            self.after(150, self._drain_status)

    def _apply_status(self, key: str, status: ModelStatus, detail: str):
        # Mirror status into the Saved Models tab as well
        sid = f"saved:{key}"
        if sid in self._saved_rows and self._stree.exists(sid):
            color_s, label_s = STATUS_COLORS.get(status, (TEXT3, "● UNKNOWN"))
            tag_s = STATUS_TAGS.get(status, "s_offline")
            self._stree.item(sid, tags=(tag_s,))
            self._stree.set(sid, "status", label_s)
            if status == ModelStatus.RECORDING:
                fname = os.path.basename(detail) if detail else "…"
                self._stree.set(sid, "file", fname)
            elif status in (ModelStatus.ONLINE, ModelStatus.OFFLINE,
                            ModelStatus.CHECKING, ModelStatus.PRIVATE):
                self._stree.set(sid, "file", "—")
                self._stree.set(sid, "size", "—")
            if self._status_filter_set("saved") is not None:
                self._schedule_filter("saved")  # status changed → re-filter

        if key not in self._rows or not self._tree.exists(key):
            return

        # Guard: don't let a stale ONLINE callback overwrite an active recording.
        # Lock-free read (dict.get + attribute read are atomic under the GIL) —
        # taking recorder._lock on the UI thread caused stalls during storms when
        # the check/launch/scan threads hold it constantly. A slightly stale read
        # is harmless for this guard.
        if status == ModelStatus.ONLINE:
            cfg = self.recorder.models.get(key)
            if cfg and cfg.session:
                # Model is actually recording — ignore this late ONLINE callback
                return

        color, label = STATUS_COLORS.get(status, (TEXT3, "● UNKNOWN"))
        tag = STATUS_TAGS.get(status, "s_offline")
        self._tree.item(key, tags=(tag,))
        self._tree.set(key, "status", label)

        if status == ModelStatus.RECORDING:
            fname = os.path.basename(detail) if detail else "…"
            self._tree.set(key, "file", fname)
        elif status == ModelStatus.ONLINE:
            self._tree.set(key, "file", "—")
            self._tree.set(key, "size", "—")
            self._size_cache.pop(key, None)
            # Auto-record when the Recorder monitor is active and AUTO is checked
            if self._monitoring_recorder and self._auto_rec.get(key, False):
                site, name = key.split(":", 1)
                self._launch_recording(name, site)
        elif status in (ModelStatus.OFFLINE, ModelStatus.CHECKING, ModelStatus.PRIVATE):
            self._tree.set(key, "file", "—")
            self._tree.set(key, "size", "—")
            self._size_cache.pop(key, None)
        elif status == ModelStatus.ERROR:
            self._tree.set(key, "file", detail[:50] if detail else "error")

        # Coalesced: _stats_tick recomputes at most twice a second instead of
        # one full row scan per status callback.
        self._stats_dirty = True
        if self._status_filter_set("rec") is not None:
            self._schedule_filter("rec")  # status changed → re-filter (debounced)

    def _bw_tick(self):
        """Update the header bandwidth meter once a second from the relay's
        upstream byte counter (covers all relay-routed recordings)."""
        try:
            import cb_relay
            total = cb_relay.bytes_downloaded()
        except Exception:
            total = 0
        now = time.monotonic()
        if self._bw_prev is not None:
            t0, b0 = self._bw_prev
            dt = max(now - t0, 0.001)
            cur = (total - b0) * 8 / dt / 1_000_000
            # light smoothing so the number doesn't flicker
            self._bw_mbps = 0.6 * self._bw_mbps + 0.4 * cur
        self._bw_prev = (now, total)
        mbps = self._bw_mbps
        if mbps >= 0.05:
            self._lbl_bw.configure(text=f"↓ {mbps:.1f} Mbps", fg=GREEN)
        else:
            self._lbl_bw.configure(text="↓ 0.0 Mbps", fg=TEXT3)

        # Upload: pipeline → Telegram
        try:
            ul_total = self.pipeline.bytes_uploaded() if self.pipeline else 0
        except Exception:
            ul_total = 0
        if self._ul_prev is not None:
            t0, b0 = self._ul_prev
            dt = max(now - t0, 0.001)
            # counter resets when the pipeline restarts → ignore negative deltas
            cur = max(0.0, (ul_total - b0) * 8 / dt / 1_000_000)
            # TDLib's uploaded_size jumps to the full file size instantly when
            # Telegram dedupes a file it already has or resumes a partial
            # upload — not real wire traffic. Drop physically implausible
            # samples instead of showing a multi-hundred-Mbps spike.
            if cur <= 500.0:
                self._ul_mbps = 0.6 * self._ul_mbps + 0.4 * cur
        self._ul_prev = (now, ul_total)
        if self._ul_mbps >= 0.05:
            self._lbl_bw_up.configure(text=f"↑ {self._ul_mbps:.1f} Mbps", fg=ORANGE)
        else:
            self._lbl_bw_up.configure(text="↑ 0.0 Mbps", fg=TEXT3)
        self.after(1000, self._bw_tick)

    def _cb_log(self, line: str):
        # Called from worker threads — just queue; _drain_logs batches the
        # Tk work (deque.append is thread-safe, no Tk call needed here).
        self._log_queue.append(("app", line, "info"))

    def _drain_logs(self):
        try:
            if self._log_queue:
                app_lines, pipe_lines = [], []
                while self._log_queue:
                    dest, msg, tag = self._log_queue.popleft()
                    (app_lines if dest == "app" else pipe_lines).append((msg, tag))
                if app_lines:
                    self._bulk_insert(self._log, app_lines, 2000)
                if pipe_lines:
                    self._bulk_insert(self._pipe_log, pipe_lines, 1000)
        finally:
            self.after(250, self._drain_logs)

    def _bulk_insert(self, widget, lines, max_lines: int):
        widget.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        for msg, tag in lines:
            line = msg if msg.startswith("[") else f"[{ts}]  {msg}"
            widget.insert("end", line + "\n", tag)
        n = int(widget.index("end-1c").split(".")[0])
        if n > max_lines:
            widget.delete("1.0", f"{n - max_lines}.0")
        # Autoscroll only when someone can actually see it — see() forces
        # layout work that's wasted while the tab is hidden or the privacy
        # cover is up.
        if self._privacy_canvas is None and widget.winfo_viewable():
            widget.see("end")
        widget.configure(state="disabled")

    def _cb_notif(self, title: str, body: str, key: str = None):
        # key (model identity) is used by the web UI's VIP filter; the classic
        # app just honors the master toggle.
        if self.settings.notifications_enabled and self._v_notif.get():
            send_notification(title, body)

    # ── Saved Models tab ─────────────────────────────────────────────────────

    def _build_saved_tab(self, p):
        bar = tk.Frame(p, bg=BG2, height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="Saved Models  ·  view-only status watchlist",
                 fg=TEXT3, bg=BG2, font=("Segoe UI", 9)).pack(side="left", padx=12)
        self._lbl_saved_count = tk.Label(bar, text="0 model(s)", fg=TEXT2,
                                         bg=BG2, font=("Segoe UI Semibold", 9))
        self._lbl_saved_count.pack(side="left", padx=(0, 8))
        self._lbl_saved_count.bind("<Button-1>",
                                   lambda e: self._uncheck_all("saved"))
        tk.Label(bar, text="🔎", fg=TEXT3, bg=BG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._v_filter_saved = tk.StringVar()
        ttk.Entry(bar, textvariable=self._v_filter_saved, width=14
                  ).pack(side="left", padx=(2, 0), pady=8)
        self._v_filter_saved.trace_add(
            "write", lambda *a: self._schedule_filter("saved"))
        self._mb_status_saved = self._build_status_menubutton(bar, "saved")
        self._mb_status_saved.pack(side="left", padx=(4, 0), pady=5)
        self._btn_monitor_saved = ttk.Button(bar, text="▶  START SCANNER",
                                              style="Green.TButton",
                                              command=self._toggle_monitor_saved)
        self._btn_monitor_saved.pack(side="right", padx=10, pady=5)
        ttk.Button(bar, text="＋ Add Current Recorder Model",
                   style="Flat.TButton",
                   command=self._saved_add_prompt).pack(side="right", padx=6, pady=5)
        ttk.Button(bar, text="📥 Import", style="Flat.TButton",
                   command=self._saved_import).pack(side="right", padx=2, pady=5)
        ttk.Button(bar, text="📤 Export", style="Flat.TButton",
                   command=self._saved_export).pack(side="right", padx=2, pady=5)

        cols = ("rank", "status", "file", "size")
        frame = tk.Frame(p, bg=BG)
        frame.pack(fill="both", expand=True)

        self._stree = ttk.Treeview(frame, columns=cols, show="tree headings",
                                    selectmode="extended", style="Dark.Treeview")
        self._stree_sort_reverse: dict[str, bool] = {}
        self._stree.heading("#0", text="MODEL  ↕", anchor="w",
                            command=lambda: self._sort_stree("#0"))
        self._stree.column("#0", width=200, minwidth=120)
        self._stree.heading("rank", text="RANK  ↕", anchor="w",
                            command=lambda: self._sort_stree("rank"))
        self._stree.column("rank", width=92, minwidth=92, stretch=False)
        self._stree.heading("status", text="STATUS  ↕", anchor="w",
                            command=lambda: self._sort_stree("status"))
        self._stree.column("status", width=140, minwidth=80)
        self._stree.heading("file", text="CURRENT FILE", anchor="w")
        self._stree.column("file", width=320, minwidth=100)
        self._stree.heading("size", text="SIZE  ↕", anchor="e",
                            command=lambda: self._sort_stree("size"))
        self._stree.column("size", width=90, minwidth=60, anchor="e")

        sb = ttk.Scrollbar(frame, orient="vertical", command=self._stree.yview)
        self._stree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._stree.pack(side="left", fill="both", expand=True)

        for tag, color in [("s_offline", TEXT3), ("s_online", GREEN),
                            ("s_recording", ACCENT), ("s_error", RED),
                            ("s_checking", YELLOW)]:
            self._stree.tag_configure(tag, foreground=color)
        self._stree.tag_configure("site_hdr", foreground=ACCENT,
                                   font=("Segoe UI Semibold", 9))

        self._stree.bind("<Button-1>", self._on_stree_click)
        self._stree.bind("<B1-Motion>", lambda e: self._on_tree_drag(e, "saved"))
        self._stree.bind("<ButtonRelease-1>",
                         lambda e: self._end_tree_drag("saved"), add="+")
        self._stree.bind("<Button-3>", self._on_stree_right_click)
        self._stree.bind("<Control-a>", lambda e: self._select_all(self._stree))

        self._sctx = tk.Menu(self, tearoff=0, bg=BG3, fg=TEXT,
                              activebackground=BG2, activeforeground=ACCENT,
                              font=UI, relief="flat", bd=0)

        # Restore persisted saved models as DATA only — rows are built on the
        # first tab visit (_populate_saved_tab), engine registration happens
        # at scanner start (_register_saved_models).
        for m in getattr(self.settings, "saved_models", []) or []:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self._saved_data[self._saved_key(n, s)] = {"name": n, "site": s}
                # Migrate a rank stored inline on older configs into the
                # shared store (settings.ranks is authoritative now).
                r = int(m.get("rank", 0) or 0)
                if r:
                    self._ranks.setdefault(self._rank_key(n, s), r)
        self._update_saved_count()

    def _populate_saved_tab(self):
        """Build the Treeview rows for the saved watchlist on first use, then
        sync each row's status from the recorder — a model that is already
        recording in the Recorder tab shows RECORDING here immediately, and
        live mirroring (_apply_status) takes over once the rows exist."""
        if self._saved_built:
            return
        self._saved_built = True
        for d in self._saved_data.values():
            # _insert_saved_model skips existing rows and ends with
            # _saved_sync_from_recorder (status/file mirror)
            self._insert_saved_model(d["name"], d["site"])
        self._update_saved_count()
        if (self._v_filter_saved.get().strip()
                or self._status_filter_set("saved") is not None):
            self._filter_saved()

    def _register_saved_models(self):
        """Register the watchlist in the recording engine — needed only when
        the scanner actually runs. add_model is idempotent: models that are
        also in the Recorder tab just gain the 'saved' group."""
        for d in self._saved_data.values():
            self.recorder.add_model(d["name"], d["site"], "saved", quiet=True)
        if self._saved_data:
            self._log_add(f"Registered {len(self._saved_data)} saved model(s) "
                          f"for scanning.")

    def _update_saved_count(self, visible: Optional[int] = None):
        if visible is not None:
            self._saved_visible = visible  # remember across check toggles
        total = len(self._saved_data)
        vis = getattr(self, "_saved_visible", None)
        if vis is not None and vis < total:
            txt = f"{vis} / {total} shown"
        else:
            txt = f"{total} model(s)"
        if self._saved_checked:
            txt += f"  ·  ✓ {len(self._saved_checked)}"
        self._lbl_saved_count.configure(text=txt)

    # ── Tree filtering ────────────────────────────────────────────────────────

    _SITE_ORDER = ("chaturbate", "stripchat", "camsoda", "myfreecams")
    _FILTER_STATUSES = (ModelStatus.ONLINE, ModelStatus.RECORDING,
                        ModelStatus.OFFLINE, ModelStatus.PRIVATE,
                        ModelStatus.CHECKING, ModelStatus.ERROR)

    def _build_status_menubutton(self, bar, which: str) -> ttk.Menubutton:
        """'Status ▾' dropdown with one checkbox per status — any combination
        (e.g. Online + Recording). Nothing checked = show all."""
        svars = {st: tk.BooleanVar(value=False) for st in self._FILTER_STATUSES}
        if which == "rec":
            self._status_vars_rec = svars
        else:
            self._status_vars_saved = svars
        mb = ttk.Menubutton(bar, text="Status: All", style="TMenubutton")
        menu = tk.Menu(mb, tearoff=0, bg=BG3, fg=TEXT, activebackground=BG2,
                       activeforeground=ACCENT, font=UI, relief="flat", bd=0)
        for st, var in svars.items():
            menu.add_checkbutton(label=st.value.title(), variable=var,
                                 command=lambda w=which: self._on_status_filter(w))
        mb["menu"] = menu
        return mb

    def _status_filter_set(self, which: str) -> Optional[set]:
        """Checked statuses, or None when the filter is inactive (none or all
        checked — both mean 'show everything')."""
        svars = getattr(self, "_status_vars_rec" if which == "rec"
                        else "_status_vars_saved", None)
        if not svars:
            return None
        sel = {st for st, v in svars.items() if v.get()}
        return sel if sel and len(sel) < len(svars) else None

    def _on_status_filter(self, which: str):
        sel = self._status_filter_set(which)
        mb = self._mb_status_rec if which == "rec" else self._mb_status_saved
        mb.configure(text="Status: All" if sel is None else f"Status: {len(sel)}")
        self._schedule_filter(which)

    def _on_tab_changed(self, event=None):
        if self._nb.select() == str(self._tab_saved):
            self._populate_saved_tab()

    def _schedule_filter(self, which: str):
        """Debounce filter keystrokes — a full pass over 1500 rows per
        keypress is wasted work while the user is still typing."""
        job = self._filter_jobs.get(which)
        if job:
            self.after_cancel(job)
        fn = self._filter_recorder if which == "rec" else self._filter_saved
        self._filter_jobs[which] = self.after(250, fn)

    def _filter_recorder(self):
        self._filter_jobs["rec"] = None
        self._filter_tree(self._tree, list(self._rows),
                          self._v_filter_rec.get(), "_site_",
                          self._status_filter_set("rec"))

    def _filter_saved(self):
        self._filter_jobs["saved"] = None
        if not self._saved_built:
            return  # rows not built yet — _populate_saved_tab applies it
        visible = self._filter_tree(self._stree, list(self._saved_rows),
                                    self._v_filter_saved.get(), "_ssite_",
                                    self._status_filter_set("saved"))
        self._update_saved_count(visible)

    def _filter_tree(self, tree, row_iids, query, hdr_prefix,
                     statuses: Optional[set] = None) -> int:
        """Show only rows whose model name contains `query` AND (when a
        status set is given) whose status is in it; the rest are detached
        (hidden, not deleted — values keep updating and reattach when the
        filter clears). Site headers with no visible rows are hidden too.
        Returns the number of visible rows."""
        q = query.strip().lower()
        sites_seen: list[str] = []
        sites_visible: set[str] = set()
        shown = 0
        for iid in row_iids:
            parts = iid.split(":")
            site, name = parts[-2], parts[-1]
            hdr = f"{hdr_prefix}{site}"
            if site not in sites_seen:
                sites_seen.append(site)
            if not tree.exists(iid) or not tree.exists(hdr):
                continue
            ok = not q or q in name.lower()
            if ok and statuses is not None:
                ok = STATUS_BY_LABEL.get(tree.set(iid, "status")) in statuses
            if ok:
                tree.move(iid, hdr, "end")
                sites_visible.add(site)
                shown += 1
            else:
                tree.selection_remove(iid)  # never leave hidden rows selected
                tree.detach(iid)
        order = [s for s in self._SITE_ORDER if s in sites_seen] + \
                [s for s in sites_seen if s not in self._SITE_ORDER]
        for idx, site in enumerate(order):
            hdr = f"{hdr_prefix}{site}"
            if not tree.exists(hdr):
                continue
            if site in sites_visible:
                tree.move(hdr, "", idx)
            else:
                tree.detach(hdr)
        # Rows were re-attached in insertion order — restore the user's sort.
        last = self._last_sort.get(hdr_prefix)
        if last:
            self._apply_sort(tree, last[0], last[1], hdr_prefix)
        return shown

    def _saved_ensure_site(self, site: str):
        site_id = f"_ssite_{site}"
        if self._stree.exists(site_id):
            return
        label = {"chaturbate": "CHATURBATE", "stripchat": "STRIPCHAT",
                 "camsoda": "CAMSODA",
                 "myfreecams": "MYFREECAMS"}.get(site, site.upper())
        self._stree.insert("", "end", iid=site_id, text=f"  {label}",
                           values=("", "", "", ""), tags=("site_hdr",), open=True)

    def _saved_key(self, name: str, site: str) -> str:
        return f"saved:{site}:{name.lower()}"

    @staticmethod
    def _rank_stars(rank: int) -> str:
        """Render a 0-5 rank as five star glyphs (filled + empty), e.g.
        3 → '★★★☆☆'. Always five glyphs so an unranked row is still
        clickable star-by-star."""
        r = max(0, min(5, int(rank or 0)))
        return "★" * r + "☆" * (5 - r)

    @staticmethod
    def _rank_from_stars(text: str) -> int:
        return text.count("★")

    @staticmethod
    def _rank_key(name: str, site: str) -> str:
        return f"{site.lower()}:{name.lower()}"

    def _get_rank(self, name: str, site: str) -> int:
        return int(self._ranks.get(self._rank_key(name, site), 0) or 0)

    def _confirm_rank_change(self, name: str, site: str, new: int) -> bool:
        """Star-click misclick guard: changing or clearing an EXISTING rank
        asks first; first-time ranking (no rank yet) stays one-click. Only
        used by the in-app star-click handlers (main thread) — never call
        from the API path (modal would freeze the Tk loop)."""
        cur = self._get_rank(name, site)
        if cur == 0:
            return True
        if new == 0:
            msg = (f"Clear the rank of {name} ({site})?\n\n"
                   f"{self._rank_stars(cur)}  →  {self._rank_stars(0)}")
        else:
            msg = (f"Change the rank of {name} ({site})?\n\n"
                   f"{self._rank_stars(cur)}  →  {self._rank_stars(new)}")
        return messagebox.askyesno("Change rank", msg, parent=self)

    def _set_rank_many(self, items, rank: int, source: str = "ui") -> int:
        """Set the 0-5 star rank on a list of (name, site) models — the single
        place ranks change. Updates the shared store, refreshes the matching
        rows in BOTH trees (whichever exist), persists once, and logs the
        change (Activity Log + audit trail). Returns the number of models
        whose rank actually changed."""
        rank = max(0, min(5, int(rank)))
        stars = self._rank_stars(rank)
        items = self._expand_linked(items)
        changed = 0
        first_old = None
        for name, site in items:
            k = self._rank_key(name, site)
            old = int(self._ranks.get(k, 0) or 0)
            if old != rank:
                changed += 1
                if first_old is None:
                    first_old = old
                audit.log_event("rank_change", name=name, site=site,
                                source=source, old=old, new=rank)
            if rank:
                self._ranks[k] = rank
            else:
                self._ranks.pop(k, None)
            rkey = f"{site}:{name.lower()}"
            if self._tree.exists(rkey):
                self._tree.set(rkey, "rank", stars)
            sid = self._saved_key(name, site)
            if self._stree.exists(sid):
                self._stree.set(sid, "rank", stars)
        if changed:
            if len(items) == 1:
                n0, s0 = items[0]
                self._log_add(f"⭐ Rank {self._rank_stars(first_old)} → {stars}: "
                              f"{n0} ({s0})", "accent")
            else:
                self._log_add(f"⭐ Rank → {stars} for {changed} model(s)",
                              "accent")
            self._persist_models()
        return changed

    # ── Cross-site identity links (aka groups) ────────────────────────────────
    # A group of "site:name" keys = one person with accounts on several sites
    # (see links.py). Ranks stay stored per-model exactly as before; linking
    # only makes rank changes propagate to every member.

    def _expand_linked(self, items):
        """(name, site) list → same list plus every linked alias, deduped.
        Applied inside _set_rank_many so one person keeps one rank across all
        of her accounts, no matter which one you rated."""
        out, seen = [], set()
        for name, site in items:
            key = model_links.norm_key(name, site)
            for k in [key] + model_links.aka(self._links, key):
                if k not in seen:
                    seen.add(k)
                    n, s = model_links.split_key(k)
                    out.append((n, s))
        return out

    def _link_aka(self, name: str, site: str) -> list:
        return model_links.aka(self._links,
                               model_links.norm_key(name, site))

    def _tracked_link_keys(self):
        with (getattr(self, "_rows_lock", None) or contextlib.nullcontext()):
            keys = set(k.lower() for k in self._rows)
            keys.update(sid.split(":", 1)[1].lower() for sid in self._saved_data)
        return keys

    def _links_payload(self) -> dict:
        """Groups + same-name suggestions (read-only, safe off-thread)."""
        return {
            "ok": True,
            "links": [list(g) for g in self._links],
            "suggestions": model_links.suggestions(
                self._links, self._tracked_link_keys(), self._link_ignores),
        }

    def _finish_link(self, key_a: str, key_b: str, source: str = "api"):
        """Post-link bookkeeping (main thread): highest rank in the group wins
        everywhere, then persist + audit + Activity Log. The group membership
        itself was already merged synchronously by the caller."""
        group = model_links.find_group(self._links, key_a) or [key_a, key_b]
        pairs = [model_links.split_key(k) for k in group]
        top = max((self._get_rank(n, s) for n, s in pairs), default=0)
        if top:
            # _set_rank_many expands to the whole group and persists once.
            self._set_rank_many([pairs[0]], top, source=source)
        else:
            self._persist_models()
        na, sa = model_links.split_key(key_a)
        audit.log_event("link_add", name=na, site=sa, source=source,
                        other=key_b, group=list(group))
        self._log_add(f"🔗 Linked {key_a} ↔ {key_b}"
                      f"{f' (rank ★{top} applied to all)' if top else ''}",
                      "accent")

    def _finish_unlink(self, key: str, source: str = "api"):
        self._persist_models()
        n, s = model_links.split_key(key)
        audit.log_event("link_remove", name=n, site=s, source=source)
        self._log_add(f"🔗 Unlinked {key}", "accent")

    def _saved_rank(self, sid: str) -> int:
        _, site, name = sid.split(":", 2)
        return self._get_rank(name, site)

    def _set_saved_rank(self, sids, rank: int) -> int:
        """Saved-tab entry point: rank one or many rows (by sid)."""
        if isinstance(sids, str):
            sids = [sids]
        items = []
        for sid in sids:
            if sid in self._saved_data or self._stree.exists(sid):
                _, site, name = sid.split(":", 2)
                items.append((name, site))
        return self._set_rank_many(items, rank)

    def _insert_saved_model(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        sid = self._saved_key(name, site)
        if sid in self._saved_rows:
            return
        self._saved_ensure_site(site)
        parent = f"_ssite_{site}"
        self._stree.insert(parent, "end", iid=sid, text=f"☐  {name}",
                           values=(self._rank_stars(self._saved_rank(sid)),
                                   "●  OFFLINE", "—", "—"),
                           tags=("s_offline",))
        self._saved_rows[sid] = True
        # Refresh initial size/status
        self._saved_sync_from_recorder(name, site)
        if self._saved_built and (self._v_filter_saved.get().strip()
                                  or self._status_filter_set("saved") is not None):
            self._schedule_filter("saved")  # respect an active filter

    def _saved_sync_from_recorder(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        sid = self._saved_key(name, site)
        if not self._stree.exists(sid):
            return
        cfg = self.recorder.models.get(key)
        if not cfg:
            return
        color, label = STATUS_COLORS.get(cfg.status, (TEXT3, "● UNKNOWN"))
        tag = STATUS_TAGS.get(cfg.status, "s_offline")
        self._stree.item(sid, tags=(tag,))
        self._stree.set(sid, "status", label)
        if cfg.session and cfg.session.current_file:
            self._stree.set(sid, "file", os.path.basename(cfg.session.current_file))

    def _add_to_saved(self, name: str, site: str):
        sid = self._saved_key(name, site)
        if sid in self._saved_data:
            messagebox.showerror("Already saved",
                                 f"{name} ({site}) is already in Saved Models.")
            return
        self._saved_data[sid] = {"name": name, "site": site}
        self._finish_add_saved(name, site)

    def _finish_add_saved(self, name: str, site: str, source: str = "ui"):
        """Widget/engine side of adding to Saved Models. The API path
        (_handle_add) writes the _saved_data entry synchronously — before this
        runs on the Tk thread — so a /rank call arriving right after /add sees
        the model as saved instead of racing the deferred insert. This only
        does the UI/engine work, so it's safe whether or not the dict entry
        was already there."""
        # Engine registration is only needed while the scanner runs; otherwise
        # it happens in bulk at scanner start.
        if self._monitoring_saved:
            self.recorder.add_model(name, site, "saved")
        self._insert_saved_model(name, site)
        self._update_saved_count()
        self._persist_models()
        audit.log_event("saved_add", name=name, site=site, source=source)
        self._log_add(f"⭐  Added to Saved Models: {name} ({site})", "accent")
        key = f"{site}:{name.lower()}"
        if self._tree.exists(key):
            self._tree.set(key, "saved", "✔️")
        self._update_saved_btn()

    def _add_to_recorder(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        if key in self._rows:
            messagebox.showerror("Already in Recorder",
                                 f"{name} ({site}) is already in the Recorder list.")
            return
        self._rows[key] = True
        self._finish_add_recorder(name, site)

    def _finish_add_recorder(self, name: str, site: str, source: str = "ui"):
        """Widget/engine side of adding to Recorder — see _finish_add_saved
        for why this is split from the dict write."""
        self.recorder.add_model(name, site, "recorder")
        self._insert_model(name, site)
        self._persist_models()
        self._update_stats()
        audit.log_event("recorder_add", name=name, site=site, source=source)
        self._log_add(f"Added to Recorder: {name} ({site})", "accent")

    def _remove_saved(self, sid: str, persist: bool = True, source: str = "ui"):
        # The row may not exist yet (lazy tab) — the data entry is what counts
        if sid not in self._saved_data and not self._stree.exists(sid):
            return
        self._saved_data.pop(sid, None)
        _, site, name = sid.split(":", 2)
        audit.log_event("saved_remove", name=name, site=site, source=source)
        # Drop the rank if the model is now on neither list (no orphans).
        if f"{site}:{name}" not in self._rows:
            self._ranks.pop(self._rank_key(name, site), None)
        if self._stree.exists(sid):
            self._stree.delete(sid)
        self._saved_rows.pop(sid, None)
        self._saved_checked.discard(sid)
        site_id = f"_ssite_{site}"
        # _saved_rows includes filter-detached rows; get_children() doesn't
        if (self._stree.exists(site_id)
                and not any(s.split(":")[1] == site for s in self._saved_rows)):
            self._stree.delete(site_id)
        self.recorder.remove_model(name, site, "saved")
        rec_key = f"{site}:{name}"
        if self._tree.exists(rec_key):
            self._tree.set(rec_key, "saved", "❌")
        if persist:  # bulk callers persist/log once at the end instead
            self._update_saved_count()
            self._persist_models()
            self._log_add(f"Removed from Saved Models: {name} ({site})", "warn")
            self._update_saved_btn()

    def _remove_saved_many(self, sids: list):
        for sid in list(sids):
            self._remove_saved(sid, persist=False)
        self._update_saved_count()
        self._persist_models()
        self._update_saved_btn()
        self._log_add(f"Removed {len(sids)} model(s) from Saved Models", "warn")

    def _add_many_to_recorder(self, sids: list):
        added = 0
        for sid in sids:
            _, site, name = sid.split(":", 2)
            key = f"{site}:{name}"
            if key in self._rows:
                continue  # already in the Recorder tab
            self.recorder.add_model(name, site, "recorder", quiet=True)
            self._insert_model(name, site)
            added += 1
        if added:
            self._persist_models()
            self._update_stats()
        self._log_add(f"Added {added} model(s) to Recorder"
                      + (f" ({len(sids) - added} already there)"
                         if added < len(sids) else ""), "accent")

    def _saved_targets(self, clicked: Optional[str] = None) -> list:
        """Rows a saved-tab bulk action operates on: checked boxes first,
        then the multi-selection, then just the clicked row."""
        if self._saved_checked:
            return [s for s in self._saved_checked if self._stree.exists(s)]
        sel = [s for s in self._stree.selection() if not s.startswith("_ssite_")]
        if sel:
            return sel
        return [clicked] if clicked else []

    def _on_stree_click(self, event):
        """Toggle the row checkbox in the Saved tab."""
        region = self._stree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return
        col = self._stree.identify_column(event.x)
        sid = self._stree.identify_row(event.y)
        if not sid or sid.startswith("_ssite_"):
            return
        modified = event.state & 0x0005  # Shift / Control held
        if col == "#0" and event.x <= 48 and not modified:
            self._toggle_check(self._stree, sid, self._saved_checked)
            return "break"
        # Click a star in the RANK column to set 1-5 stars; clicking the
        # star that's already the rank clears it back to 0.
        if col == "#1" and not modified:
            bbox = self._stree.bbox(sid, col)
            if bbox:
                bx, _, bw, _ = bbox
                star = int((event.x - bx) / (bw / 5)) + 1
                star = max(1, min(5, star))
                new = 0 if self._saved_rank(sid) == star else star
                _, s_site, s_name = sid.split(":", 2)
                if self._confirm_rank_change(s_name, s_site, new):
                    self._set_saved_rank(sid, new)
            return "break"
        if not modified:
            self._drag_anchor["saved"] = sid

    def _on_stree_right_click(self, event):
        sid = self._stree.identify_row(event.y)
        if not sid or sid.startswith("_ssite_"):
            return
        if sid not in self._stree.selection():
            self._stree.selection_set(sid)
        targets = self._saved_targets(sid)
        n = len(targets)
        m = self._sctx
        m.delete(0, "end")
        if n == 1:
            _, site, name = targets[0].split(":", 2)
            m.add_command(label=f"＋  Add to Recorder  {name}",
                          command=lambda: self._add_to_recorder(name, site))
            m.add_separator()
            m.add_command(label="▶  Preview",
                          command=lambda: self._preview_model(name, site))
            m.add_command(label="🔗  Copy Model URL",
                          command=lambda: self._copy_model_url(name, site))
            m.add_command(label="🌐  Open in Browser",
                          command=lambda t=targets: self._open_in_browser(t, saved=True))
            m.add_command(label="🌐  Open in Browser (choose…)",
                          command=lambda t=targets: self._open_in_browser(t, saved=True, force_choose=True))
            m.add_separator()
            m.add_command(label="✕  Remove from Saved Models",
                          command=lambda t=targets[0]: self._remove_saved(t))
        else:
            src = "checked" if self._saved_checked else "selected"
            m.add_command(label=f"＋  Add to Recorder  ({n} {src})",
                          command=lambda t=targets: self._add_many_to_recorder(t))
            m.add_command(label=f"🌐  Open in Browser  ({n} {src})",
                          command=lambda t=targets: self._open_in_browser(t, saved=True))
            m.add_command(label=f"🌐  Open in Browser (choose…)  ({n} {src})",
                          command=lambda t=targets: self._open_in_browser(t, saved=True, force_choose=True))
            m.add_command(label=f"📋  Copy as OneTab List  ({n} {src})",
                          command=lambda t=targets: self._copy_onetab(t, saved=True))
            m.add_separator()
            m.add_command(label=f"✕  Remove from Saved  ({n} {src})",
                          command=lambda t=targets: self._remove_saved_many(t))
        # Set Rank submenu — works for one row or the whole selection/check set
        m.add_separator()
        rank_lbl = "Set Rank" if n == 1 else f"Set Rank  ({n})"
        rank_menu = tk.Menu(m, tearoff=0, bg=BG3, fg=TEXT,
                            activebackground=BG2, activeforeground=ACCENT,
                            font=UI, relief="flat", bd=0)
        for r in (5, 4, 3, 2, 1):
            rank_menu.add_command(
                label=self._rank_stars(r),
                command=lambda t=targets, rr=r: self._set_saved_rank(t, rr))
        rank_menu.add_separator()
        rank_menu.add_command(
            label="☆  Clear rank",
            command=lambda t=targets: self._set_saved_rank(t, 0))
        m.add_cascade(label=f"⭐  {rank_lbl}", menu=rank_menu)
        m.add_separator()
        raw_sel = [s for s in self._stree.selection()
                   if not s.startswith("_ssite_")]
        if raw_sel:
            m.add_command(label=f"☑  Check Selected  ({len(raw_sel)})",
                          command=lambda: self._check_selection("saved"))
        m.add_command(label="☑  Check All Visible",
                      command=lambda: self._check_all_visible("saved"))
        if self._saved_checked:
            m.add_command(label=f"☐  Uncheck All  ({len(self._saved_checked)})",
                          command=lambda: self._uncheck_all("saved"))
        m.tk_popup(event.x_root, event.y_root)

    def _saved_add_prompt(self):
        # Quick helper: pop a small dialog to add a username/URL into Saved Models
        from tkinter import simpledialog
        raw = simpledialog.askstring("Add to Saved Models",
                                      "Username or URL (chaturbate / stripchat / camsoda / myfreecams):",
                                      parent=self)
        if not raw:
            return
        name, site = self._parse_model_input(raw)
        if not name:
            messagebox.showwarning("Input required", "Could not parse a username.")
            return
        self._add_to_saved(name, site)

    def _saved_export(self):
        if not self._saved_data:
            messagebox.showinfo("Nothing to export", "Saved Models list is empty.")
            return
        path = filedialog.asksaveasfilename(
            title="Export Saved Models",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            initialfile="saved_models.json",
        )
        if not path:
            return
        # Merge into an existing export rather than overwriting it: entries
        # already in the file are kept, ranks are refreshed from the current
        # list, and models the file has but we don't are preserved untouched.
        merged: dict = {}
        existing = 0
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    prev = json.load(f)
                for m in (prev.get("saved_models") if isinstance(prev, dict)
                          else []) or []:
                    if not isinstance(m, dict):
                        continue
                    n = (m.get("name") or "").strip()
                    s = (m.get("site") or "").strip()
                    if n and s:
                        merged[f"{s.lower()}:{n.lower()}"] = {
                            "name": n, "site": s,
                            "rank": int(m.get("rank", 0) or 0)}
                existing = len(merged)
            except Exception:
                pass  # unreadable/garbage file — fall back to a clean write
        updated = 0
        for d in self._saved_data.values():
            k = f"{d['site'].lower()}:{d['name'].lower()}"
            if k in merged:
                updated += 1
            merged[k] = {"name": d["name"], "site": d["site"],
                         "rank": self._get_rank(d["name"], d["site"])}
        items = list(merged.values())
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "saved_models": items}, f, indent=2)
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
            return
        kept = existing - updated  # file-only entries left untouched
        audit.log_event("export", source="ui", count=len(items), path=path)
        self._log_add(f"Exported {len(items)} saved model(s) → {path}", "success")
        messagebox.showinfo(
            "Export complete",
            f"Wrote {len(items)} model(s) to:\n{path}" +
            (f"\n\nMerged with existing file: {kept} kept, "
             f"{updated} updated." if existing else ""))

    def _saved_import(self):
        path = filedialog.askopenfilename(
            title="Import Saved Models",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Import failed", f"Could not read file:\n{e}")
            return
        items = data.get("saved_models") if isinstance(data, dict) else None
        if not isinstance(items, list):
            messagebox.showerror("Import failed",
                                 "File is not a valid saved-models export.")
            return
        added = skipped = invalid = ranked = 0
        for m in items:
            if not isinstance(m, dict):
                invalid += 1
                continue
            n, s = (m.get("name") or "").strip().lower(), (m.get("site") or "").strip().lower()
            if not n or not s:
                invalid += 1
                continue
            rank = max(0, min(5, int(m.get("rank", 0) or 0)))
            sid = self._saved_key(n, s)
            rkey = self._rank_key(n, s)
            if sid in self._saved_data:
                # Already on the watchlist — still pull in a rank if the file
                # carries one and ours is unset, so importing a ranked export
                # back-fills stars onto existing models.
                if rank and self._get_rank(n, s) == 0:
                    self._ranks[rkey] = rank
                    if self._stree.exists(sid):
                        self._stree.set(sid, "rank", self._rank_stars(rank))
                    audit.log_event("rank_change", name=n, site=s,
                                    source="import", old=0, new=rank)
                    ranked += 1
                else:
                    skipped += 1
                continue
            self._saved_data[sid] = {"name": n, "site": s}
            if rank:
                self._ranks[rkey] = rank
            if self._monitoring_saved:
                self.recorder.add_model(n, s, "saved", quiet=True)
            self._insert_saved_model(n, s)
            audit.log_event("saved_add", name=n, site=s, source="import",
                            rank=rank or None)
            added += 1
        if added or ranked:
            self._update_saved_count()
            self._persist_models()
        audit.log_event("import", source="ui", path=path, added=added,
                        ranked=ranked, skipped=skipped, invalid=invalid)
        self._log_add(
            f"Import: added {added}, ranked {ranked}, "
            f"skipped {skipped} duplicate(s)" +
            (f", {invalid} invalid" if invalid else ""),
            "success" if (added or ranked) else "warn",
        )
        messagebox.showinfo(
            "Import complete",
            f"Added: {added}\nRanks applied to existing: {ranked}\n"
            f"Skipped (already present): {skipped}" +
            (f"\nInvalid entries: {invalid}" if invalid else ""),
        )

    # ── Output / Upload tab (Pipeline) ───────────────────────────────────────

    def _build_output_tab(self, p):
        from pipeline_worker import PipelineWorker, PipelineConfig
        self._PipelineWorker = PipelineWorker
        self._PipelineConfig = PipelineConfig
        self.pipeline: Optional[PipelineWorker] = None

        # Top control bar
        bar = tk.Frame(p, bg=BG2, height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._btn_pipeline = ttk.Button(bar, text="▶  START PIPELINE",
            style="Green.TButton", command=self._toggle_pipeline)
        self._btn_pipeline.pack(side="left", padx=12, pady=8)

        self._lbl_pipe_state = tk.Label(bar, text="● STOPPED",
            fg=TEXT3, bg=BG2, font=("Segoe UI Semibold", 10))
        self._lbl_pipe_state.pack(side="left", padx=8)

        ttk.Button(bar, text="🔑  Re-auth / Switch Account",
            style="Flat.TButton",
            command=self._pipeline_reauth).pack(side="right", padx=6, pady=8)

        ttk.Button(bar, text="🧙  Setup Wizard",
            style="Flat.TButton",
            command=self._telegram_wizard).pack(side="right", padx=6, pady=8)

        # Pipeline stage toggles — run either stage alone or both together
        stages = tk.Frame(p, bg=BG2)
        stages.pack(fill="x")
        tk.Label(stages, text="Stages:", fg=TEXT3, bg=BG2, font=UI).pack(
            side="left", padx=(14, 6), pady=4)
        self._v_do_convert = tk.BooleanVar(value=self.settings.pipeline_do_convert)
        self._v_do_upload = tk.BooleanVar(value=self.settings.pipeline_do_upload)
        tk.Checkbutton(stages, text="① Convert .ts → .mp4",
                       variable=self._v_do_convert, command=self._on_stage_toggle,
                       bg=BG2, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT, font=UI,
                       relief="flat").pack(side="left", padx=(0, 12))
        tk.Checkbutton(stages, text="② Upload .mp4 to Telegram",
                       variable=self._v_do_upload, command=self._on_stage_toggle,
                       bg=BG2, fg=TEXT2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=TEXT, font=UI,
                       relief="flat").pack(side="left")

        # Settings grid
        cfg_frame = tk.LabelFrame(p, text=" Telegram / Pipeline settings ",
            bg=BG, fg=TEXT2, font=("Segoe UI Semibold", 9),
            relief="flat", bd=0, padx=10, pady=8)
        cfg_frame.pack(fill="x", padx=12, pady=(8, 4))

        self._pipe_vars = {}
        fields = [
            ("telegram_api_id",      "API ID",            30),
            ("telegram_api_hash",    "API Hash",          50),
            ("telegram_group_id",    "Chat / Group ID",   30),
            ("_topic_id",            "Topic ID (0 if none)", 10),
            ("pipeline_converted_dir", "Converted .mp4 folder", 60),
            ("telegram_session_dir", "TDLib session folder", 60),
        ]
        for i, (key, label, width) in enumerate(fields):
            r, c = divmod(i, 2)
            cell = tk.Frame(cfg_frame, bg=BG)
            cell.grid(row=r, column=c, sticky="ew", padx=6, pady=3)
            cfg_frame.columnconfigure(c, weight=1)
            tk.Label(cell, text=label, fg=TEXT3, bg=BG,
                     font=("Segoe UI", 9)).pack(anchor="w")
            var = tk.StringVar()
            if key == "_topic_id":
                var.set(str(getattr(self.settings, "telegram_topic_id", "") or "1"))
            elif key == "pipeline_converted_dir":
                var.set(getattr(self.settings, "pipeline_converted_dir", "") or "")
            elif key == "telegram_session_dir":
                var.set(getattr(self.settings, "telegram_session_dir", "") or "")
            else:
                var.set(str(getattr(self.settings, key, "") or ""))
            e = ttk.Entry(cell, textvariable=var, width=width)
            e.pack(fill="x")
            self._pipe_vars[key] = var

        ttk.Button(cfg_frame, text="💾  Save Pipeline Settings",
                   style="Flat.TButton",
                   command=self._save_pipeline_settings).grid(
            row=99, column=0, columnspan=2, sticky="e", pady=(8, 0))

        # Progress / status area
        prog = tk.Frame(p, bg=BG2)
        prog.pack(fill="x", padx=12, pady=6)
        self._pipe_convert_lbl = tk.Label(prog, text="Convert:  idle",
            fg=YELLOW, bg=BG2, font=MONO, anchor="w")
        self._pipe_convert_lbl.pack(fill="x", padx=10, pady=(6, 2))
        self._pipe_upload_lbls: list = []
        for i in range(2):
            lbl = tk.Label(prog, text=f"Upload {i+1}: idle",
                fg=GREEN, bg=BG2, font=MONO, anchor="w")
            lbl.pack(fill="x", padx=10, pady=(0, 2))
            self._pipe_upload_lbls.append(lbl)

        # Pipeline log
        logf = tk.Frame(p, bg=BG)
        logf.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self._pipe_log = tk.Text(logf, bg=BG, fg=TEXT2, font=MONO,
            relief="flat", bd=0, padx=10, pady=6,
            state="disabled", wrap="word")
        ls = ttk.Scrollbar(logf, command=self._pipe_log.yview)
        self._pipe_log.configure(yscrollcommand=ls.set)
        ls.pack(side="right", fill="y")
        self._pipe_log.pack(fill="both", expand=True)

    def _save_pipeline_settings(self):
        s = self.settings
        s.pipeline_do_convert = self._v_do_convert.get()
        s.pipeline_do_upload  = self._v_do_upload.get()
        s.telegram_api_id   = self._pipe_vars["telegram_api_id"].get().strip()
        s.telegram_api_hash = self._pipe_vars["telegram_api_hash"].get().strip()
        s.telegram_group_id = self._pipe_vars["telegram_group_id"].get().strip()
        s.telegram_topic_id = self._pipe_vars["_topic_id"].get().strip()
        s.pipeline_converted_dir = self._pipe_vars["pipeline_converted_dir"].get().strip()
        s.telegram_session_dir   = self._pipe_vars["telegram_session_dir"].get().strip()
        save_pipeline_settings(s)
        self._pipe_log_add("Pipeline settings saved.", "success")

    def _telegram_wizard(self):
        """Guided first-time setup for the Telegram upload pipeline. Collects API
        credentials + destination, writes them into the tab's fields, saves via
        the normal saver, and can start the pipeline (the actual phone/code login
        runs through the existing _pipeline_prompt flow). User-triggered modal —
        never call from the API/background path (it blocks the Tk loop)."""
        win = tk.Toplevel(self)
        win.title("Telegram Pipeline — Setup Wizard")
        win.configure(bg=BG2)
        win.resizable(False, False)
        win.transient(self)

        # Working copies, prefilled from the current fields.
        v_api_id   = tk.StringVar(value=self._pipe_vars["telegram_api_id"].get().strip())
        v_api_hash = tk.StringVar(value=self._pipe_vars["telegram_api_hash"].get().strip())
        v_group    = tk.StringVar(value=self._pipe_vars["telegram_group_id"].get().strip())
        v_topic    = tk.StringVar(value=self._pipe_vars["_topic_id"].get().strip() or "0")
        v_conv     = tk.StringVar(value=self._pipe_vars["pipeline_converted_dir"].get().strip())
        v_sess     = tk.StringVar(value=self._pipe_vars["telegram_session_dir"].get().strip())
        v_start    = tk.BooleanVar(value=True)

        header = tk.Label(win, bg=BG2, fg=TEXT, font=("Segoe UI Semibold", 13))
        header.pack(anchor="w", padx=20, pady=(16, 0))
        step_lbl = tk.Label(win, bg=BG2, fg=TEXT3, font=UI)
        step_lbl.pack(anchor="w", padx=20)
        body = tk.Frame(win, bg=BG2)
        body.pack(fill="both", expand=True, padx=20, pady=10)
        btns = tk.Frame(win, bg=BG2)
        btns.pack(fill="x", padx=16, pady=(4, 16))

        state = {"step": 0}
        TOTAL = 4

        def _clear(frame):
            for w in frame.winfo_children():
                w.destroy()

        def _label(text, fg=TEXT2, font=UI, pady=(8, 0)):
            tk.Label(body, text=text, bg=BG2, fg=fg, font=font,
                     justify="left", wraplength=440).pack(anchor="w", pady=pady)

        def _entry(var, show=None, width=46):
            ttk.Entry(body, textvariable=var, width=width, show=show).pack(
                anchor="w", pady=(2, 6))

        def render():
            _clear(body)
            s = state["step"]
            step_lbl.configure(text=f"Step {s + 1} of {TOTAL}")
            if s == 0:
                header.configure(text="Welcome")
                _label("This wizard sets up uploading your recordings to a Telegram "
                       "group or topic.\n\nYou'll need:\n"
                       "  •  A Telegram account\n"
                       "  •  An API ID + API Hash from my.telegram.org\n"
                       "  •  The target group/chat ID (and topic ID, if any)")
                _label("Get your API ID / Hash at:")
                link = tk.Label(body, text="https://my.telegram.org  →  API development tools",
                                bg=BG2, fg=ACCENT, font=UI, cursor="hand2")
                link.pack(anchor="w")
                link.bind("<Button-1>", lambda e: self._launch_urls(
                    [("Telegram", "", "https://my.telegram.org")],
                    self.settings.preferred_browser))
            elif s == 1:
                header.configure(text="Telegram API credentials")
                _label("API ID (numbers only):")
                _entry(v_api_id, width=24)
                _label("API Hash:")
                _entry(v_api_hash)
            elif s == 2:
                header.configure(text="Destination")
                _label("Chat / Group ID (e.g. -1001234567890):")
                _entry(v_group, width=28)
                _label("Topic ID (0 if the group has no topics):")
                _entry(v_topic, width=12)
                _label("Tip: forward a message from the group to a getId bot "
                       "(e.g. @userinfobot) to find the numeric ID.", fg=TEXT3)
            else:
                header.configure(text="Review & finish")
                _label("Optional — leave blank for sensible defaults:")
                _label("Converted .mp4 folder:", pady=(6, 0))
                _entry(v_conv)
                _label("TDLib session folder:", pady=(2, 0))
                _entry(v_sess)
                tk.Checkbutton(body, text="Start the pipeline with Upload enabled now",
                               variable=v_start, bg=BG2, fg=TEXT2, selectcolor=BG3,
                               activebackground=BG2, activeforeground=TEXT, font=UI,
                               relief="flat").pack(anchor="w", pady=(10, 0))
                _label("On first start you'll be asked for your phone number and the "
                       "Telegram login code.", fg=TEXT3)
            _render_buttons()

        def _render_buttons():
            _clear(btns)
            s = state["step"]
            ttk.Button(btns, text="Cancel", style="Ghost.TButton",
                       command=win.destroy).pack(side="left")
            if s == TOTAL - 1:
                ttk.Button(btns, text="✓  Save & Finish", style="Green.TButton",
                           command=_finish).pack(side="right")
            else:
                ttk.Button(btns, text="Next  →", style="Flat.TButton",
                           command=_next).pack(side="right")
            if s > 0:
                ttk.Button(btns, text="←  Back", style="Ghost.TButton",
                           command=_back).pack(side="right", padx=(0, 8))

        def _next():
            s = state["step"]
            if s == 1:
                if not v_api_id.get().strip().isdigit():
                    messagebox.showwarning("Setup Wizard", "API ID must be a number.",
                                           parent=win)
                    return
                if not v_api_hash.get().strip():
                    messagebox.showwarning("Setup Wizard", "API Hash is required.",
                                           parent=win)
                    return
            elif s == 2:
                if not v_group.get().strip():
                    messagebox.showwarning("Setup Wizard",
                                           "Chat / Group ID is required.", parent=win)
                    return
            state["step"] = min(TOTAL - 1, s + 1)
            render()

        def _back():
            state["step"] = max(0, state["step"] - 1)
            render()

        def _finish():
            self._pipe_vars["telegram_api_id"].set(v_api_id.get().strip())
            self._pipe_vars["telegram_api_hash"].set(v_api_hash.get().strip())
            self._pipe_vars["telegram_group_id"].set(v_group.get().strip())
            self._pipe_vars["_topic_id"].set(v_topic.get().strip() or "0")
            self._pipe_vars["pipeline_converted_dir"].set(v_conv.get().strip())
            self._pipe_vars["telegram_session_dir"].set(v_sess.get().strip())
            self._save_pipeline_settings()
            win.destroy()
            if v_start.get():
                self._v_do_upload.set(True)
                if self.pipeline and self.pipeline.running:
                    self._on_stage_toggle()
                else:
                    self._toggle_pipeline()

        render()
        win.update_idletasks()
        win.minsize(520, max(320, win.winfo_height()))
        win.grab_set()

    def _on_stage_toggle(self):
        """Apply a Convert/Upload checkbox change. Persists the choice, and if the
        pipeline is already running, updates it live (no restart needed)."""
        dc = self._v_do_convert.get()
        du = self._v_do_upload.get()
        self.settings.pipeline_do_convert = dc
        self.settings.pipeline_do_upload = du
        save_pipeline_settings(self.settings)

        if self.pipeline and self.pipeline.running:
            cfg = self.pipeline.cfg
            # When enabling Upload live, refresh the Telegram credentials from the
            # fields so a freshly-filled account is picked up on the next attempt.
            if du and not cfg.do_upload:
                fresh = self._build_pipeline_config()
                cfg.api_id, cfg.api_hash = fresh.api_id, fresh.api_hash
                cfg.chat_id, cfg.topic_id = fresh.chat_id, fresh.topic_id
            cfg.do_convert = dc
            cfg.do_upload = du
            self._pipe_log_add(
                f"Stages updated live — convert: {'on' if dc else 'off'}, "
                f"upload: {'on' if du else 'off'}.", "accent")
        self._update_pipe_mode_label()

    def _pipe_mode_text(self):
        """State-label text/colour reflecting which stages are active."""
        dc = self._v_do_convert.get()
        du = self._v_do_upload.get()
        if dc and du:
            return "● CONVERTING & UPLOADING", GREEN
        if dc:
            return "● CONVERTING", GREEN
        if du:
            return "● UPLOADING", GREEN
        return "● STAND BY", YELLOW

    def _update_pipe_mode_label(self):
        if not (self.pipeline and self.pipeline.running):
            return
        text, color = self._pipe_mode_text()
        self._lbl_pipe_state.configure(text=text, fg=color)

    def _build_pipeline_config(self):
        cfg = self._PipelineConfig()
        s = self.settings
        try:
            cfg.api_id = int(self._pipe_vars["telegram_api_id"].get().strip() or "0")
        except ValueError:
            cfg.api_id = 0
        cfg.api_hash = self._pipe_vars["telegram_api_hash"].get().strip()
        cfg.phone = ""  # phone only needed for first-time auth; left blank reuses session
        try:
            cfg.chat_id = int(self._pipe_vars["telegram_group_id"].get().strip() or "0")
        except ValueError:
            cfg.chat_id = 0
        try:
            cfg.topic_id = int(self._pipe_vars["_topic_id"].get().strip() or "0")
        except ValueError:
            cfg.topic_id = 0
        cfg.do_convert = self._v_do_convert.get()
        cfg.do_upload = self._v_do_upload.get()
        cfg.watch_folder = s.output_dir
        cfg.output_folder = (self._pipe_vars["pipeline_converted_dir"].get().strip()
                             or os.path.join(s.output_dir, "converted"))
        cfg.tdlib_dir = (self._pipe_vars["telegram_session_dir"].get().strip()
                         or os.path.join(os.path.dirname(os.path.dirname(__file__)), "Pipeline", ".tdlib"))
        cfg.uploaded_log = os.path.join(cfg.tdlib_dir, "uploaded.txt")
        # ffmpeg bundled with the app
        cfg.ffmpeg_path = self.recorder.ffmpeg_path or "ffmpeg"
        cfg.ffprobe_path = "ffprobe"
        from pipeline_worker import TELEGRAM_MAX_BYTES
        user_cap = (s.max_size_mb * 1024 * 1024) if s.max_size_mb else None
        if cfg.do_upload:
            cfg.max_bytes = min(user_cap, TELEGRAM_MAX_BYTES) if user_cap else TELEGRAM_MAX_BYTES
        else:
            cfg.max_bytes = user_cap if user_cap else float("inf")
        return cfg

    def _toggle_pipeline(self, silent: bool = False):
        if self.pipeline and self.pipeline.running:
            self.pipeline.stop()
            self._btn_pipeline.configure(text="▶  START PIPELINE", style="Green.TButton")
            self._lbl_pipe_state.configure(text="● STOPPING", fg=ORANGE)
            return

        # The pipeline always starts (even with no stage checked) and sits in
        # stand-by — stages are turned on/off live afterwards. No up-front
        # validation: missing Telegram settings are reported by the worker only
        # if/when the Upload stage is actually enabled.
        cfg = self._build_pipeline_config()
        os.makedirs(cfg.output_folder, exist_ok=True)
        os.makedirs(cfg.tdlib_dir, exist_ok=True)

        self.pipeline = self._PipelineWorker(
            cfg,
            on_log=lambda line: self._log_queue.append(("pipe", line, "info")),
            on_state=lambda st: self.after(0, lambda: self._pipeline_state_changed(st)),
            on_progress=lambda k, n, p, s=0.0: self.after(0,
                lambda: self._pipeline_progress(k, n, p, s)),
            prompt_cb=self._pipeline_prompt,
        )
        # Remember the chosen stages so they persist across restarts.
        self.settings.pipeline_do_convert = cfg.do_convert
        self.settings.pipeline_do_upload  = cfg.do_upload
        save_pipeline_settings(self.settings)

        self.pipeline.start()
        self._btn_pipeline.configure(text="⏹  STOP PIPELINE", style="Red.TButton")
        self._lbl_pipe_state.configure(text="● STARTING", fg=YELLOW)
        self._pipe_log_add("Starting pipeline...", "accent")

    def _pipeline_reauth(self):
        import shutil
        dir_ = (self._pipe_vars["telegram_session_dir"].get().strip()
                or os.path.join(os.path.dirname(os.path.dirname(__file__)), "Pipeline", ".tdlib"))
        if not os.path.isdir(dir_):
            messagebox.showinfo("Re-auth",
                "No TDLib session folder found — next Start will begin a fresh login.")
            return
        if not messagebox.askyesno("Re-auth",
                f"Delete cached Telegram session in:\n{dir_}\n\nYou'll need to log in again on next Start."):
            return
        try:
            shutil.rmtree(dir_)
            self._pipe_log_add(f"Cleared session at {dir_}.", "warn")
        except Exception as e:
            messagebox.showerror("Re-auth failed", str(e))

    def _pipeline_state_changed(self, state: str):
        if state == "running":
            # Show which stage(s) are active rather than a generic "RUNNING".
            self._update_pipe_mode_label()
            return
        colors = {
            "starting":  (YELLOW, "● STARTING"),
            "stopping":  (ORANGE, "● STOPPING"),
            "stopped":   (TEXT3,  "● STOPPED"),
            "error":     (RED,    "● ERROR"),
        }
        c, label = colors.get(state, (TEXT3, f"● {state.upper()}"))
        self._lbl_pipe_state.configure(text=label, fg=c)
        if state in ("stopped", "error"):
            self._btn_pipeline.configure(text="▶  START PIPELINE", style="Green.TButton")
            self._pipe_convert_lbl.configure(text="Convert:  idle")
            for i, lbl in enumerate(self._pipe_upload_lbls):
                lbl.configure(text=f"Upload {i+1}: idle")

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        if bps <= 0:
            return ""
        if bps >= 1_048_576:
            return f"  {bps/1_048_576:.1f} MB/s"
        return f"  {bps/1024:.0f} KB/s"

    def _pipeline_progress(self, kind: str, name: str, pct: float, speed: float = 0.0):
        bar_len = 22
        filled = int(round(bar_len * pct / 100.0))
        bar = "█" * filled + "░" * (bar_len - filled)
        short_name = name[:34]
        spd = self._fmt_speed(speed)
        if kind == "convert":
            idle = pct >= 100.0 and not name
            txt = ("Convert:  idle" if idle
                   else f"Convert:  [{bar}] {pct:5.1f}%  {short_name}")
            self._pipe_convert_lbl.configure(text=txt)
        elif kind.startswith("upload"):
            slot = int(kind.replace("upload", "") or "1") - 1
            slot = max(0, min(slot, len(self._pipe_upload_lbls) - 1))
            idle = pct >= 100.0 and not name
            prefix = f"Upload {slot+1}:"
            txt = (f"{prefix} idle" if idle
                   else f"{prefix} [{bar}] {pct:5.1f}%{spd}  {short_name}")
            self._pipe_upload_lbls[slot].configure(text=txt)

    def _pipeline_prompt(self, label: str) -> str:
        """Called from worker thread — must block until GUI dialog returns."""
        from tkinter import simpledialog
        result = {"val": ""}
        done = threading.Event()

        def _ask():
            try:
                val = simpledialog.askstring("Telegram", label,
                                              parent=self, show="•")
                result["val"] = val or ""
            finally:
                done.set()

        self.after(0, _ask)
        done.wait(timeout=300)
        return result["val"]

    def _pipe_log_add(self, msg: str, tag: str = "info"):
        self._pipe_log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        self._pipe_log.insert("end", f"[{ts}]  {msg}\n")
        self._pipe_log.see("end")
        # Cap at 1000 lines
        n = int(self._pipe_log.index("end-1c").split(".")[0])
        if n > 1000:
            self._pipe_log.delete("1.0", f"{n - 1000}.0")
        self._pipe_log.configure(state="disabled")

    # ── Log tab ───────────────────────────────────────────────────────────────

    def _build_log_tab(self, p):
        bar = tk.Frame(p, bg=BG2, height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="Activity Log", fg=TEXT2, bg=BG2,
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=12)
        ttk.Button(bar, text="Clear", style="Flat.TButton",
                   command=self._clear_log).pack(side="right", padx=8, pady=4)

        f = tk.Frame(p, bg=BG)
        f.pack(fill="both", expand=True)
        self._log = tk.Text(f, bg=BG, fg=TEXT2, font=MONO,
                             relief="flat", bd=0, padx=12, pady=8,
                             state="disabled", wrap="word")
        ls = ttk.Scrollbar(f, command=self._log.yview)
        self._log.configure(yscrollcommand=ls.set)
        ls.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True)
        for tag, color in [("info", TEXT2), ("success", GREEN), ("warn", ORANGE),
                            ("error", RED), ("accent", ACCENT)]:
            self._log.tag_configure(tag, foreground=color)

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log_add(self, msg: str, tag: str = "info"):
        self._log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        # avoid double-timestamp if msg already has one
        line = msg if msg.startswith("[") else f"[{ts}]  {msg}"
        self._log.insert("end", line + "\n", tag)
        # Cap log at 2000 lines to prevent unbounded memory growth
        line_count = int(self._log.index("end-1c").split(".")[0])
        if line_count > 2000:
            self._log.delete("1.0", f"{line_count - 2000}.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # ── Settings ──────────────────────────────────────────────────────────────

    def _pick_folder(self):
        f = filedialog.askdirectory(initialdir=self._e_folder.get())
        if f:
            self._e_folder.delete(0, "end")
            self._e_folder.insert(0, f)

    def _update_disk_dim(self):
        state = "normal" if self._v_lowdisk.get() else "disabled"
        self._e_disk_stop.configure(state=state)
        self._e_disk_resume.configure(state=state)

    def _save_settings(self):
        self.settings.output_dir            = self._e_folder.get().strip()
        self.settings.max_size_mb           = self._parse_int(self._v_maxsize.get())
        self.settings.check_interval        = self._parse_int(self._v_interval.get(), 30)
        self.settings.minimize_to_tray       = self._v_tray.get()
        self.settings.notifications_enabled = self._v_notif.get()
        self.settings.gap_warnings_enabled  = self._v_gapwarn.get()
        self.settings.max_quality = QUALITY_OPTIONS.get(self._v_quality.get(), 0)
        self.settings.auto_downgrade_enabled = self._v_autodown.get()
        self.settings.playwright_fallback_enabled = self._v_pwfallback.get()
        self.settings.low_disk_guard_enabled = self._v_lowdisk.get()
        self.settings.low_disk_stop_gb   = max(1.0, self._parse_float(
            self._v_disk_stop.get(), self.settings.low_disk_stop_gb) or self.settings.low_disk_stop_gb)
        self.settings.low_disk_resume_gb = self._parse_float(
            self._v_disk_resume.get(), self.settings.low_disk_resume_gb) or self.settings.low_disk_resume_gb
        if self.settings.low_disk_resume_gb <= self.settings.low_disk_stop_gb:
            self.settings.low_disk_resume_gb = self.settings.low_disk_stop_gb + 1
        self.recorder.quality_global = self.settings.max_quality
        self.recorder.auto_downgrade_enabled = self.settings.auto_downgrade_enabled
        self.recorder.playwright_fallback_enabled = self.settings.playwright_fallback_enabled
        self.recorder.low_disk_guard_enabled = self.settings.low_disk_guard_enabled
        self.recorder.low_disk_stop_gb = self.settings.low_disk_stop_gb
        self.recorder.low_disk_resume_gb = self.settings.low_disk_resume_gb
        self.settings.privacy_mode_enabled  = self._v_privacy.get()
        disp = self._v_browser.get()
        for d, v in self._browser_choices:
            if d == disp:
                self.settings.preferred_browser = v
                break
        self.settings.preview_mode = ("embedded"
            if "Embedded" in self._v_preview_mode.get() else "external")
        self.settings.preview_engine = {"Auto": "auto", "mpv": "mpv",
            "VLC": "vlc"}.get(self._v_preview_engine.get(), "auto")
        self.settings.preview_player_path = self._v_preview_path.get().strip()
        self.recorder.gap_warnings_enabled  = self.settings.gap_warnings_enabled
        self._persist_models()
        save_settings(self.settings)
        self.recorder.output_dir     = self.settings.output_dir
        self.recorder.max_size_mb    = self.settings.max_size_mb
        self.recorder.check_interval = self.settings.check_interval
        os.makedirs(self.settings.output_dir, exist_ok=True)
        if _is_cloud_synced(self.settings.output_dir):
            self._log_add("⚠ Output folder is inside a cloud-synced directory "
                          "(OneDrive/Dropbox) — a local folder is strongly "
                          "recommended.", "warn")
        note = self._preview_engine_note()
        self._flash_saved(note)
        self._log_add("Settings saved." + (f" ({note})" if note else ""), "success")

    def _preview_engine_note(self) -> str:
        """If the chosen preview engine isn't actually available, explain that it
        will fall back — so the user understands why the player doesn't change."""
        eng = (self.settings.preview_engine or "auto").lower()
        embedded = self.settings.preview_mode == "embedded"
        if eng == "mpv":
            ok = (self._import_mpv() is not None) if embedded else bool(self._detect_player("mpv"))
            if not ok:
                return ("mpv isn't installed — preview falls back to VLC"
                        + ("" if embedded else "/ffplay") + ".")
        elif eng == "vlc":
            ok = (self._import_vlc() is not None) if embedded else bool(self._detect_player("vlc"))
            if not ok:
                return "VLC isn't installed — preview falls back to another engine."
        return ""

    def _flash_saved(self, note: str = ""):
        """Briefly show a 'Settings saved' confirmation that auto-clears."""
        if not hasattr(self, "_lbl_saved"):
            return
        txt = "✓ Settings saved" + (f"  —  {note}" if note else "")
        self._lbl_saved.configure(text=txt, fg=(ORANGE if note else GREEN))
        job = getattr(self, "_saved_flash_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._saved_flash_job = self.after(
            5000, lambda: self._lbl_saved.configure(text=""))

    # ── System check (dependency / PATH validator) ────────────────────────────

    def _run_system_check(self):
        """(Re)build the dependency status rows in the Settings tab."""
        f = getattr(self, "_syscheck_frame", None)
        if f is None:
            return
        for w in f.winfo_children():
            w.destroy()
        for c in self._gather_dependency_status():
            self._render_syscheck_row(f, c)

    def _render_syscheck_row(self, parent, c):
        row = tk.Frame(parent, bg=BG2)
        row.pack(fill="x", pady=1)
        icon = {"ok": "✓", "warn": "⚠", "err": "✕"}[c["state"]]
        color = {"ok": GREEN, "warn": ORANGE, "err": RED}[c["state"]]
        tk.Label(row, text=icon, bg=BG2, fg=color, width=2,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        for lbl, cb in c.get("actions", []):
            ttk.Button(row, text=lbl, style="Ghost.TButton",
                       command=cb).pack(side="right", padx=(4, 0))
        txt = c["label"] + (f"  —  {c['detail']}" if c.get("detail") else "")
        tk.Label(row, text=txt, bg=BG2, fg=TEXT2, anchor="w", justify="left",
                 font=("Segoe UI", 9), wraplength=320).pack(
            side="left", fill="x", expand=True)

    def _module_ok(self, name: str) -> bool:
        try:
            import importlib
            importlib.import_module(name)
            return True
        except Exception:
            return False

    def _find_installed_dir(self, kind: str):
        """Find the folder of an installed-but-maybe-unpathed binary."""
        exe = {"mpv": "mpv.exe", "vlc": "vlc.exe"}.get(kind)
        dirs = {
            "mpv": [r"%ProgramFiles%\MPV Player", r"%ProgramFiles%\mpv",
                    r"%ProgramFiles(x86)%\mpv", r"%LOCALAPPDATA%\Programs\mpv"],
            "vlc": [r"%ProgramFiles%\VideoLAN\VLC", r"%ProgramFiles(x86)%\VideoLAN\VLC"],
        }.get(kind, [])
        for d in dirs:
            d = os.path.expandvars(d)
            if exe and os.path.isfile(os.path.join(d, exe)):
                return d
        return None

    def _playwright_chromium_ok(self) -> bool:
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright")
        try:
            return os.path.isdir(base) and any(
                n.startswith("chromium-") for n in os.listdir(base))
        except Exception:
            return False

    def _gather_dependency_status(self):
        import shutil
        out = []
        # ffmpeg (required for recording)
        ff = (getattr(self.recorder, "ffmpeg_path", "") or "") or (shutil.which("ffmpeg") or "")
        out.append({"label": "ffmpeg (recording)", "state": "ok" if ff else "err",
                    "detail": ff or "NOT FOUND — recording won't work", "actions": []})
        # ffplay (external preview fallback)
        fp = self._detect_player("ffplay")
        out.append({"label": "ffplay (external preview)", "state": "ok" if fp else "warn",
                    "detail": fp or "not found", "actions": []})
        # mpv (optional preview) — the headline case: installed but not on PATH
        wmpv = shutil.which("mpv")
        if wmpv:
            out.append({"label": "mpv (preview)", "state": "ok", "detail": wmpv,
                        "actions": []})
        else:
            loc = self._find_installed_dir("mpv")
            if loc:
                out.append({"label": "mpv (preview)", "state": "warn",
                            "detail": f"installed at {loc} but not on PATH",
                            "actions": [("Add to PATH",
                                         lambda d=loc: self._fix_add_to_path(d))]})
            else:
                out.append({"label": "mpv (preview)", "state": "warn",
                            "detail": "not installed (optional — VLC/ffplay cover preview)",
                            "actions": []})
        # VLC (optional preview) — doesn't need PATH (app checks Program Files;
        # python-vlc finds it via the registry)
        vlc_exe = self._detect_player("vlc")
        out.append({"label": "VLC (preview)", "state": "ok" if vlc_exe else "warn",
                    "detail": vlc_exe or "not installed (optional)", "actions": []})
        # python-vlc (embedded VLC)
        pv = self._import_vlc() is not None
        out.append({"label": "python-vlc (embedded VLC)", "state": "ok" if pv else "warn",
                    "detail": "installed" if pv else "missing",
                    "actions": [] if pv else [("Install", lambda: self._pip_install(
                        "python-vlc", on_done=lambda ok: self._run_system_check()))]})
        # python-mpv + libmpv (embedded mpv)
        pm = self._import_mpv() is not None
        out.append({"label": "python-mpv + libmpv (embedded mpv)",
                    "state": "ok" if pm else "warn",
                    "detail": "installed" if pm else "missing (needs python-mpv + libmpv-2.dll in src/)",
                    "actions": [] if pm else [("Install python-mpv", lambda: self._pip_install(
                        "python-mpv", on_done=lambda ok: self._run_system_check()))]})
        # tdjson (Telegram pipeline)
        td = self._module_ok("tdjson")
        out.append({"label": "tdjson (Telegram pipeline)", "state": "ok" if td else "warn",
                    "detail": "installed" if td else "missing",
                    "actions": [] if td else [("Install", lambda: self._pip_install(
                        "tdjson", on_done=lambda ok: self._run_system_check()))]})
        # Playwright Chromium (Stripchat browser fallback)
        pc = self._playwright_chromium_ok()
        out.append({"label": "Playwright Chromium (Stripchat fallback)",
                    "state": "ok" if pc else "warn",
                    "detail": "installed" if pc else "missing",
                    "actions": [] if pc else [("Install", self._install_chromium)]})
        return out

    def _fix_add_to_path(self, d: str):
        """Append a folder to the USER PATH (no admin), make it effective for the
        running app immediately, and broadcast the change to other apps."""
        try:
            import winreg, ctypes
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            try:
                cur, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                cur = ""
            parts = [p for p in (cur or "").split(";") if p]
            if d not in parts:
                parts.append(d)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
                ctypes.windll.user32.SendMessageTimeoutW(
                    0xFFFF, 0x1A, 0, "Environment", 0x2, 5000, None)
            winreg.CloseKey(key)
            # Effective for THIS process right now (no restart needed to use it).
            if d not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + d
            try:
                if hasattr(os, "add_dll_directory") and os.path.isdir(d):
                    os.add_dll_directory(d)
            except Exception:
                pass
            self._log_add(f"Added to PATH: {d}", "success")
            self._flash_saved(f"Added to PATH: {os.path.basename(d)}")
        except Exception as e:
            self._log_add(f"Could not modify PATH: {e}", "error")
        self._run_system_check()

    def _bg_command(self, cmd, desc, on_done=None):
        """Run a setup command (e.g. playwright install) in the background."""
        self._log_add(f"{desc}… (one-time, needs internet)")
        def _do():
            ok, detail = False, ""
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = (r.returncode == 0)
                if not ok:
                    lines = (r.stderr or r.stdout or "").strip().splitlines()
                    detail = lines[-1] if lines else "failed"
            except Exception as e:
                detail = str(e)
            self.after(0, lambda: (
                self._log_add(f"{desc}: {'done' if ok else 'failed — ' + detail}",
                              "success" if ok else "error"),
                on_done(ok) if on_done else None))
        threading.Thread(target=_do, daemon=True, name="bg-cmd").start()

    def _install_chromium(self):
        self._bg_command([sys.executable, "-m", "playwright", "install", "chromium"],
                         "Installing Playwright Chromium",
                         on_done=lambda ok: self._run_system_check())

    def _persist_models(self):
        self.settings.models = [
            {
                "name": k.split(":")[1],
                "site": k.split(":")[0],
                "auto_rec": self._auto_rec.get(k, False),
                "max_q": self._model_q.get(k, 0),
            }
            for k in self._rows
        ]
        # From _saved_data (source of truth), NOT the UI rows — with the lazy
        # tab the rows may not exist yet, and persisting from them would wipe
        # the whole watchlist on the first save.
        self.settings.saved_models = [
            {"name": d["name"], "site": d["site"]}
            for d in self._saved_data.values()
        ]
        # Ranks are keyed by model identity and persisted on their own (so a
        # saved model's rank survives even if it's also added to / removed from
        # the Recorder). Only keep ranks for models that are actually on a list
        # — a rank with no row is an "orphan" with nowhere to manage it, so we
        # drop those (and zero entries) here.
        self.settings.ranks = {
            k: v for k, v in self._ranks.items()
            if v and (k in self._rows or f"saved:{k}" in self._saved_data)
        }
        # Links are deliberately NOT pruned to tracked models — a Clear
        # Recorder (or a temporary remove) must never dissolve the user's
        # hand-made identity groups.
        self.settings.model_links = [list(g) for g in self._links]
        self.settings.link_ignores = [list(p) for p in self._link_ignores]
        save_settings(self.settings)

    def _restore_models(self):
        for m in self.settings.models:
            n, s = m.get("name"), m.get("site")
            if n and s:
                self.recorder.add_model(n, s, "recorder")
                self._insert_model(n, s, auto_rec=bool(m.get("auto_rec", False)))
                q = int(m.get("max_q", 0) or 0)
                if q:
                    self._model_q[f"{s}:{n}"] = q
                r = int(m.get("rank", 0) or 0)  # migrate older inline ranks
                if r:
                    self._ranks.setdefault(self._rank_key(n, s), r)
        self._update_stats()

    # Dashboard rows — emoji colors match each site's brand
    # (CB yellow, SC red, CS blue, MFC green). ▶ recording · ● online · ○ offline.
    _DASH_SITES = [
        ("chaturbate", "🟡", "CB"),
        ("stripchat",  "🔴", "SC"),
        ("camsoda",    "🔵", "CS"),
        ("myfreecams", "🟢", "MFC"),
    ]

    def _dashboard_counts(self):
        """Per-site [total, recording, online, offline] + grand totals.
        Snapshots the row keys so it's safe to call from the API handler
        thread (same read-only pattern as the /status endpoint)."""
        tally = {s: [0, 0, 0, 0] for s, _e, _l in self._DASH_SITES}
        for k in list(self._rows):
            t = tally.get(k.split(":", 1)[0])
            if t is None:
                continue
            t[0] += 1
            cfg = self.recorder.models.get(k)
            st = cfg.status if cfg else None
            if st == ModelStatus.RECORDING:
                t[1] += 1
            elif st == ModelStatus.ONLINE:
                t[2] += 1
            else:                       # offline/error/checking/private → offline
                t[3] += 1
        total = [0, 0, 0, 0]
        for c in tally.values():
            for i in range(4):
                total[i] += c[i]
        return tally, total

    def _build_stats_panel(self, p):
        """Compact color-coded status grid: a '● N LIVE' hero (recording count
        in red) over per-site rows. Built once; _update_stats only re-sets text
        and color in place, so live status flips don't flicker the panel."""
        wrap = tk.Frame(p, bg=BG2)
        wrap.pack(anchor="w", fill="x", padx=16, pady=8)

        hero = tk.Frame(wrap, bg=BG2)
        hero.pack(fill="x")
        self._lbl_live = tk.Label(hero, text="● 0 LIVE", fg=ACCENT, bg=BG2,
                                  font=("Consolas", 13, "bold"), anchor="w")
        self._lbl_live.pack(side="left")
        self._lbl_total = tk.Label(hero, text="0 models", fg=TEXT3, bg=BG2,
                                   font=("Consolas", 9), anchor="e")
        self._lbl_total.pack(side="right")

        grid = tk.Frame(wrap, bg=BG2)
        grid.pack(fill="x", pady=(6, 0))
        grid.grid_columnconfigure(0, weight=1)
        for col in (1, 2, 3):
            grid.grid_columnconfigure(col, minsize=30, uniform="num")

        # Header glyph row doubles as the color legend: ▶ red · ● green · ○ grey.
        for col, (txt, fg) in enumerate(
                (("", TEXT3), ("▶", ACCENT), ("●", GREEN), ("○", TEXT3))):
            tk.Label(grid, text=txt, fg=fg, bg=BG2, font=("Consolas", 9),
                     anchor="e" if col else "w").grid(
                row=0, column=col, sticky="e" if col else "w", pady=(0, 2))

        self._stat_cells = {}
        row = 1
        for site, emoji, lbl in self._DASH_SITES:
            code = tk.Label(grid, text=f"{emoji} {lbl}", fg=TEXT, bg=BG2,
                            font=("Consolas", 10), anchor="w")
            nums = [tk.Label(grid, text="0", fg=TEXT3, bg=BG2,
                             font=("Consolas", 10), anchor="e")
                    for _ in range(3)]
            code.grid(row=row, column=0, sticky="w")
            for i, n in enumerate(nums):
                n.grid(row=row, column=i + 1, sticky="e")
            self._stat_cells[site] = (code, *nums)
            row += 1

        tk.Frame(grid, bg=BORDER, height=1).grid(
            row=row, column=0, columnspan=4, sticky="ew", pady=4)
        row += 1

        tk.Label(grid, text="ALL", fg=TEXT2, bg=BG2,
                 font=("Consolas", 10, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w")
        self._tot_cells = [tk.Label(grid, text="0", bg=BG2,
                           font=("Consolas", 10, "bold"), anchor="e")
                           for _ in range(3)]
        for i, n in enumerate(self._tot_cells):
            n.grid(row=row, column=i + 1, sticky="e")

    def _update_stats(self):
        tally, total = self._dashboard_counts()
        self._lbl_live.configure(text=f"● {total[1]} LIVE")
        self._lbl_total.configure(text=f"{total[0]} models")
        palette = (ACCENT, GREEN, TEXT2)            # recording, online, offline
        for site, _e, _l in self._DASH_SITES:
            code, *nums = self._stat_cells[site]
            c = tally[site]
            if c[0]:
                code.grid()
                for i, n in enumerate(nums):
                    v = c[i + 1]
                    n.configure(text=str(v), fg=palette[i] if v else TEXT3)
                    n.grid()
            else:
                code.grid_remove()
                for n in nums:
                    n.grid_remove()
        for i, n in enumerate(self._tot_cells):
            v = total[i + 1]
            n.configure(text=str(v), fg=palette[i] if v else TEXT3)

    def _api_dashboard(self) -> dict:
        """Aggregate dashboard snapshot for the bot/API (GET /dashboard)."""
        tally, total = self._dashboard_counts()
        keys = ("total", "recording", "online", "offline")
        sites = {lbl: dict(zip(keys, tally[s]))
                 for s, _e, lbl in self._DASH_SITES}
        return {"ok": True, "sites": sites, "all": dict(zip(keys, total))}

    @staticmethod
    def _parse_int(val: str, default: int = None) -> Optional[int]:
        try:
            return int(val.strip()) if val.strip() else default
        except ValueError:
            return default

    @staticmethod
    def _parse_float(val: str, default: float = None) -> Optional[float]:
        try:
            return float(val.strip()) if val.strip() else default
        except ValueError:
            return default

    # ── Close ─────────────────────────────────────────────────────────────────

    def _start_api_server(self):
        _ApiHandler._app = self
        try:
            self._api_server = HTTPServer(("127.0.0.1", _API_PORT), _ApiHandler)
            t = threading.Thread(target=self._api_server.serve_forever,
                                 daemon=True, name="api-server")
            t.start()
        except OSError as e:
            # Almost always "port already in use" — i.e. another Scr33nX is
            # already running and owns the API port. Make it loud instead of
            # silently starting a second, half-working instance whose tray icon
            # and browser extension talk to the OTHER process.
            self._api_server = None
            logging.getLogger().warning(
                "API server could not bind 127.0.0.1:%s (%s) — another "
                "Scr33nX is already running; exiting.", _API_PORT, e)
            # Two live instances share one config file: whichever saves last
            # overwrites the other's models AND RANKS with its own stale
            # snapshot — the classic "my ranks vanished after a restart".
            # So a second instance is refused outright. Startup path, main
            # thread — modal is safe here.
            messagebox.showerror(
                "Scr33nX is already running",
                "You can only open one instance of this app.\n\n"
                f"Another Scr33nX is already running (the control port "
                f"{_API_PORT} is in use — check the system tray or taskbar).",
                parent=self)
            os._exit(0)  # nothing recording yet; exit before any save

    def _stop_api_server(self):
        if getattr(self, "_api_server", None):
            self._api_server.shutdown()

    # ── System tray ───────────────────────────────────────────────────────────

    def _on_window_unmap(self, event):
        if event.widget is not self:
            return
        if self._hiding_to_tray:
            return
        if not self._v_tray.get():
            return
        if self.state() != "iconic":
            return
        self.after_idle(self._minimize_to_tray)

    def _minimize_to_tray(self):
        if WinTray is None or not self._v_tray.get():
            return
        self._hiding_to_tray = True
        try:
            self.withdraw()
            self._ensure_tray()
        finally:
            self._hiding_to_tray = False

    def _ensure_tray(self):
        if WinTray is None:
            return
        if self._tray is None:
            self._tray = WinTray(
                "Scr33nX",
                on_show=self._tray_show_evt.set,
                on_quit=self._tray_quit_evt.set,
                on_terminate=self._tray_term_evt.set,
            )
            # Asynchronous — failure is picked up by _poll_tray, so the Tk
            # thread never blocks waiting for the tray thread.
            self._tray.add()
        if self._tray_poll_id is None:
            self._tray_poll_id = self.after(150, self._poll_tray)

    def _poll_tray(self):
        self._tray_poll_id = None
        if self._tray is not None and self._tray.failed():
            # Tray icon couldn't be created — bring the window back
            self._remove_tray()
            self.deiconify()
            return
        if self._tray_term_evt.is_set():
            self._tray_term_evt.clear()
            # Restore first so the confirm dialog (if any) isn't stuck behind
            # a hidden window, then hard-kill the process tree.
            self._do_restore_from_tray()
            self._force_terminate()
            return
        if self._tray_quit_evt.is_set():
            self._tray_quit_evt.clear()
            self._on_close()
            return
        if self._tray_show_evt.is_set():
            self._tray_show_evt.clear()
            self._do_restore_from_tray()
        if self._tray is not None:
            self._tray_poll_id = self.after(150, self._poll_tray)

    def _active_recording_count(self) -> int:
        """Read-only count of models currently in the RECORDING state."""
        try:
            return sum(1 for cfg in self.recorder.models.values()
                       if getattr(cfg, "status", None) == ModelStatus.RECORDING)
        except Exception:
            return 0

    def _force_terminate(self):
        """Hard-kill Scr33nX and its whole child-process tree (ffmpeg, the
        relay, any Playwright/Chromium) immediately — the equivalent of Task
        Manager's End Task. Skips the graceful flush, so it confirms first only
        when a recording is active to avoid losing footage on a misclick.
        Called only from the Tk thread (header button / tray poll)."""
        active = self._active_recording_count()
        if active > 0:
            if not messagebox.askyesno(
                    "Terminate Scr33nX",
                    f"{active} recording(s) still active.\n\n"
                    "Force-terminate now? Their final segments will be dropped.",
                    icon="warning", parent=self):
                return
        try:
            # taskkill /T kills the children first, then this process, so no
            # orphaned ffmpeg keeps writing. CREATE_NO_WINDOW: no console flash.
            import subprocess
            subprocess.Popen(
                ["taskkill", "/F", "/T", "/PID", str(os.getpid())],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            # Last-resort fallback if taskkill is somehow unavailable.
            os._exit(1)

    def _do_restore_from_tray(self):
        # Window was withdrawn while iconic — force normal state first so
        # deiconify doesn't bring it back minimized.
        try:
            self.state("normal")
        except tk.TclError:
            pass
        self.deiconify()
        self.lift()
        # Briefly toggle topmost so Windows actually brings us to the front.
        self.attributes("-topmost", True)
        self.after(100, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _remove_tray(self):
        if self._tray_poll_id is not None:
            try:
                self.after_cancel(self._tray_poll_id)
            except Exception:
                pass
            self._tray_poll_id = None
        if self._tray:
            try:
                self._tray.remove()
            except Exception:
                pass
            self._tray = None

    def _on_close(self):
        if getattr(self, "_closing", False):
            return
        recording = any(
            self.recorder.models.get(k) and
            self.recorder.models[k].status == ModelStatus.RECORDING
            for k in self._rows
        )
        if recording:
            # Quitting from the tray menu: the window is withdrawn, so the
            # confirm dialog needs a visible parent first.
            if self.state() == "withdrawn":
                self._do_restore_from_tray()
            if not messagebox.askyesno("Quit", "Recordings are active. Stop and exit?"):
                return
        self._closing = True
        # Drop any queued launches so shutdown doesn't kick off new recordings.
        self._rec_pool.shutdown(wait=False, cancel_futures=True)
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._lbl_hdr_status.configure(text="● STOPPING…", fg=ORANGE)
        self._stop_api_server()
        self._remove_tray()
        self._save_settings()

        # Flushing every active ffmpeg takes up to ~20 s with many
        # recordings — run it off the Tk thread so the window doesn't
        # freeze, then destroy from the Tk thread.
        def _shutdown():
            try:
                self.recorder.stop_monitor()
            except Exception:
                pass
            if hasattr(self, "pipeline"):
                try:
                    self.pipeline.stop()
                except Exception:
                    pass
            try:
                self.after(0, self.destroy)
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=_shutdown, daemon=True, name="shutdown").start()


if __name__ == "__main__":
    try:
        StreamRecorderApp().mainloop()
    except Exception:
        # pythonw discards stderr — without this, a startup crash is an
        # invisible flash. Log it and tell the user where to look.
        logging.getLogger().exception("Fatal error — app crashed")
        try:
            _root = tk.Tk(); _root.withdraw()
            messagebox.showerror(
                "Scr33nX crashed",
                "A fatal error occurred.\n\nDetails: streamrecorder.log in "
                "%LOCALAPPDATA%\\Scr33nX")
            _root.destroy()
        except Exception:
            pass
        raise
