"""
recorder.py — Core stream recording engine
Stripchat: custom HLS downloader using edge-hls.doppiocdn.com
Chaturbate: ffmpeg direct recording
"""

import os
import re
import sys
import glob
import json
import time
import shutil
import threading
import subprocess
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, wait as _futures_wait
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

# Shared session for connection pooling — reduces socket churn under many models
_http = requests.Session()
_http.headers.update(HEADERS)
# Chaturbate stream tokens are bound to the IP that called its API, and the
# mmcdn edges refuse IPv6 — so CB API calls must go out over IPv4 to match
# the relay's IPv4-only edge fetches (cb_relay.IPv4Adapter).
from cb_relay import IPv4Adapter as _IPv4Adapter
# Sized for a Player wall resolving at once alongside monitor checks and
# room-list sweep workers, so none of them waits on a pooled connection.
_http.mount("https://chaturbate.com",
            _IPv4Adapter(pool_connections=4, pool_maxsize=48))


class ModelStatus(Enum):
    OFFLINE   = "offline"
    ONLINE    = "online"
    RECORDING = "recording"
    ERROR     = "error"
    CHECKING  = "checking"
    PRIVATE   = "private"   # in ticket/private/group show — not publicly streaming


@dataclass
class RecordingSession:
    model_name: str
    site: str
    output_dir: str
    max_size_mb: Optional[int]
    stream_url: str
    part: int = 1
    # Shared base ("modelname_SITE_YYYYMMDD_HHMMSS") for every part of this
    # recording — computed once so split parts differ only by the _partNNN tag.
    base_name: Optional[str] = None
    process: Optional[subprocess.Popen] = None
    start_time: Optional[float] = None
    current_file: Optional[str] = None
    stopped: bool = False
    last_size: int = 0              # last known file size (bytes)
    last_size_change: float = 0.0   # timestamp when size last grew
    stall_probed: bool = False      # an offline-probe is in flight / done for this stall
    advert_stop: bool = False       # relay saw the SC advert loop → model offline


@dataclass
class ModelConfig:
    name: str
    site: str
    status: ModelStatus = ModelStatus.OFFLINE
    session: Optional[RecordingSession] = None
    last_checked: float = 0
    error_message: str = ""
    stream_url: str = ""
    restart_count: int = 0
    groups: set = field(default_factory=set)  # {"recorder", "saved"}
    # True after an explicit user stop (stop button / stop monitor) — blocks
    # the delayed auto-restart from resurrecting a recording the user ended.
    stop_requested: bool = False
    # Serialises split/stall/exit handling for this model. A model can be in
    # BOTH the "recorder" and "saved" groups, each polled by its own monitor
    # thread on its own timer — without this lock both threads could run
    # _session_housekeeping for the same session at once, each see the split
    # threshold crossed, and each launch a replacement ffmpeg process. Only
    # the last one to run wins cfg.session, orphaning the other: it keeps
    # downloading/writing forever, invisible to Stop All / Clear Recorder /
    # the low-disk guard (they only ever kill what's referenced by cfg.session).
    session_lock: threading.Lock = field(default_factory=threading.Lock)


# ── Chaturbate ────────────────────────────────────────────────────────────────

_CB_OFFLINE_STATUSES   = {"offline", "away", "private", "hidden", ""}

# Adaptive request pacing for chaturbate.com. Background requests (monitor
# checks, saved-scan lookups, room-list pages) each reserve the next slot
# _cb_interval apart, but many can be in flight at once. A throttle signal
# (429/403/503, empty or non-JSON body) doubles the spacing up to
# _CB_MAX_INTERVAL. Each clean response eases it back toward the base.
# Interactive requests (opening the Player, a manual REC) skip the queue
# unless the limiter is currently backing off.
#
# This replaced a strict one-at-a-time 1.5 s gate. Measured 2026-09-28: 40
# room-list pages on 8 threads and 60 chatvideocontext calls on 8 threads
# came back all 200. The old gate made a room-list sweep take ~2.5 min and
# a 16-tile Player wall take ~24 s to resolve.
_CB_BASE_INTERVAL = 0.2
_CB_MAX_INTERVAL  = 4.0
_CB_PACE_LOCK     = threading.Lock()
_cb_interval      = _CB_BASE_INTERVAL
_cb_next_slot     = 0.0
_CB_INTERACTIVE_SLOTS = threading.BoundedSemaphore(8)


def _cb_wait(interactive: bool = False):
    global _cb_next_slot
    with _CB_PACE_LOCK:
        if interactive and _cb_interval <= _CB_BASE_INTERVAL:
            return
        now = time.time()
        slot = max(now, _cb_next_slot)
        _cb_next_slot = slot + _cb_interval
    if slot > now:
        time.sleep(slot - now)


def _cb_get(url: str, interactive: bool = False, **kwargs) -> requests.Response:
    """GET a chaturbate.com API URL through the pacer, feeding the response
    back into it. Returns the response; callers decide what it means."""
    global _cb_interval
    _cb_wait(interactive)
    if interactive and _cb_interval <= _CB_BASE_INTERVAL:
        # Cap a Player wall's burst at the concurrency measured safe.
        with _CB_INTERACTIVE_SLOTS:
            r = _http.get(url, **kwargs)
    else:
        r = _http.get(url, **kwargs)
    is_json = "json" in r.headers.get("Content-Type", "")
    # A JSON 403 is a per-room refusal, not Cloudflare — don't slow every
    # other request down for it (the lookup still reads as unknown).
    throttled = (r.status_code in (429, 503) or not r.content
                 or (r.status_code in (200, 403) and not is_json))
    with _CB_PACE_LOCK:
        if throttled:
            new = min(_CB_MAX_INTERVAL, _cb_interval * 2)
            if new != _cb_interval:
                logger.warning(f"[CB] throttled (HTTP {r.status_code}) — "
                               f"request spacing now {new:.1f}s")
            _cb_interval = new
        elif _cb_interval > _CB_BASE_INTERVAL:
            _cb_interval = max(_CB_BASE_INTERVAL, _cb_interval * 0.9)
    return r


# Resolved-URL cache, same contract as stripchat_native.page_info(): every
# success is stored, but it is only read when the caller passes a max_age it
# will accept. Liveness checks pass 0 and always go to the network. Opening
# the Player reuses the URL the monitor or saved scan just stored, so a tile
# starts without its own API round-trip. The token in a CB URL is
# effectively single-use (a second master fetch within ~40 s is 403). The
# relay caches each CB master playlist, so a reused URL is still served.
_CB_URL_CACHE: dict[str, tuple[float, str]] = {}
_CB_URL_LOCK = threading.Lock()
_CB_URL_CACHE_MAX = 512  # prune oldest entries past this, so a long run can't grow forever

# Names whose most recent lookup couldn't tell online from offline
# (throttled, network error, garbled reply). The monitor keeps the previous
# status for these instead of flipping them OFFLINE.
_CB_UNKNOWN: set[str] = set()

# How stale a cached URL the Player / Preview will accept. Those actions are
# already gated on a status the monitor refreshed at most one check_interval
# ago, so reusing a URL of the same age adds no staleness the user isn't
# already looking at. Nothing that decides online-vs-offline uses this.
PREVIEW_URL_MAX_AGE = 30.0


def _fetch_chaturbate_once(model_name: str,
                           interactive: bool = False) -> tuple[Optional[str], str]:
    """Single attempt. Returns (hls_url_or_None, room_status).
    room_status == 'unknown' means the answer couldn't be read (throttled,
    network error, non-JSON reply) — neither online nor offline."""
    try:
        r = _cb_get(f"https://chaturbate.com/api/chatvideocontext/{model_name}/",
                    interactive=interactive, timeout=12)
    except requests.RequestException as e:
        logger.debug(f"[CB] {model_name}: {e}")
        return None, "unknown"
    if r.status_code == 404:
        return None, "offline"
    if r.status_code != 200:
        return None, "unknown"
    try:
        data = r.json()
    except ValueError:
        return None, "unknown"
    hls = data.get("hls_source") or data.get("stream_url") or ""
    room = data.get("room_status") or ""
    return (hls.strip() or None), room.strip()


def chaturbate_lookup(model_name: str, max_retries: int = 1, max_age: float = 0.0,
                      interactive: bool = False) -> tuple[Optional[str], Optional[bool]]:
    """(hls_url, live) for a Chaturbate model; live is None when the answer
    was unreadable. See get_chaturbate_stream_url() for the parameters."""
    key = model_name.lower()
    if max_age > 0:
        with _CB_URL_LOCK:
            hit = _CB_URL_CACHE.get(key)
            if hit and time.time() - hit[0] < max_age:
                return hit[1], True
    room = ""
    for attempt in range(max_retries + 1):
        hls, room = _fetch_chaturbate_once(model_name, interactive)
        if hls:
            if attempt:
                logger.debug(f"[CB] {model_name}: got URL on attempt {attempt + 1}")
            with _CB_URL_LOCK:
                _CB_URL_CACHE[key] = (time.time(), hls)
                if len(_CB_URL_CACHE) > _CB_URL_CACHE_MAX:
                    for k in sorted(_CB_URL_CACHE,
                                    key=lambda k: _CB_URL_CACHE[k][0]
                                    )[:_CB_URL_CACHE_MAX // 4]:
                        del _CB_URL_CACHE[k]
            return hls, True
        if room == "unknown":
            logger.debug(f"[CB] {model_name}: status unreadable (throttled?)")
            return None, None
        if room in _CB_OFFLINE_STATUSES:
            return None, False
        if attempt < max_retries:
            # room=public but no URL — CDN warmup, retry is valid
            logger.debug(f"[CB] {model_name}: room={room!r} no URL yet — retry {attempt + 1}/{max_retries} in 2 s")
            time.sleep(2)
    logger.debug(f"[CB] {model_name}: exhausted retries (room={room!r})")
    return None, False


def get_chaturbate_stream_url(model_name: str, max_retries: int = 1,
                              max_age: float = 0.0,
                              interactive: bool = False) -> Optional[str]:
    """
    Fetch the HLS stream URL for a Chaturbate model.

    max_retries controls CDN-warmup retries (room=public but no URL yet):
      1  — monitor path: fast, move on if not ready
      4  — manual REC path: persistent, gives CDN time to serve the URL
    An unreadable answer (throttled, network error) bails without retrying
    and marks the name in _CB_UNKNOWN (see chaturbate_status_unknown()).

    max_age is the oldest cached URL the caller will accept, in seconds — 0
    (the default) always goes to the network. See _CB_URL_CACHE.
    interactive skips the pacing queue (see _cb_wait()).
    """
    key = model_name.lower()
    try:
        hls, live = chaturbate_lookup(model_name, max_retries, max_age, interactive)
    except Exception as e:
        logger.error(f"[CB] {model_name}: {e}")
        hls, live = None, None
    with _CB_URL_LOCK:
        if live is None:
            _CB_UNKNOWN.add(key)
        else:
            _CB_UNKNOWN.discard(key)
    return hls


def chaturbate_status_unknown(model_name: str) -> bool:
    """True if the most recent lookup for this model couldn't tell whether
    she's online — the caller should keep the previous status."""
    with _CB_URL_LOCK:
        return model_name.lower() in _CB_UNKNOWN


# ── Stripchat ─────────────────────────────────────────────────────────────────

def get_stripchat_stream_url(model_name: str, max_age: float = 0.0) -> Optional[str]:
    """Master-playlist URL for a live Stripchat model, or None when she isn't
    live / the page couldn't be read. See get_stream_url() for `max_age`.

    The id comes from stripchat_native.stream_id(), which scrapes the
    server-rendered model page (the old `/api/front/v2/…/cam` endpoint is
    bot-blocked with HTTP 418) and caches it briefly, so the online check and
    a record/preview that follows it share a single page fetch.

    This URL is what the online scanner treats as "she's up"; the actual
    recording resolves its own keyed variant in launch_stripchat_native().
    """
    import stripchat_native
    try:
        sid = stripchat_native.stream_id(model_name, max_age=max_age)
    except Exception as e:
        logger.error(f"[ST] Error for {model_name}: {e}")
        return None
    if not sid:
        return None
    logger.debug(f"[ST] {model_name} stream_id={sid}")
    return f"https://edge-hls.doppiocdn.com/hls/{sid}/master/{sid}_auto.m3u8"


def get_camsoda_stream_url(model_name: str) -> Optional[str]:
    """
    Camsoda live HLS resolver.
    Public endpoint:  https://www.camsoda.com/api/v1/video/vtoken/<name>
    Response JSON:    { "token": "...", "edge_servers": ["host/path"], "stream_name": "...", "status": "online" }
    Builds: https://{edge}/{stream_name}_v1/index.m3u8?token={token}
    (edge already includes its path segment; stream_name embeds the resolution.)
    """
    api_url = f"https://www.camsoda.com/api/v1/video/vtoken/{model_name}"
    try:
        r = _http.get(api_url, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        token = data.get("token")
        edges = data.get("edge_servers") or []
        stream_name = data.get("stream_name")
        status = (data.get("status") or "").lower()
        if not (token and edges and stream_name):
            return None
        if status and status != "online":
            return None
        edge = edges[0]
        return f"https://{edge}/{stream_name}_v1/index.m3u8?token={token}"
    except Exception as e:
        logger.error(f"[CS] {model_name}: {e}")
        return None


_CB_ROOMLIST_URL  = "https://chaturbate.com/api/ts/roomlist/room-list/"
_CB_ROOMLIST_PAGE = 90   # the API rejects limit > 90 with HTTP 400


def get_chaturbate_online_rooms(should_continue: Optional[Callable[[], bool]] = None,
                                on_progress: Optional[Callable[[int, int], None]] = None,
                                workers: int = 4) -> Optional[set]:
    """
    Fetch usernames of ALL publicly online Chaturbate rooms via the paginated
    room-list API (90 rooms/page, ~90 pages for the whole site). Page 0 gives
    the total. The remaining pages are fetched on `workers` threads through
    the pacer, so a full sweep takes ~20 s.

    on_progress(rooms_fetched, total_rooms) is called every ~25 pages.
    Returns a set of lowercase usernames, or None on failure/abort so callers
    keep previous statuses instead of marking everything offline.
    """
    def fetch(offset: int) -> list:
        if should_continue and not should_continue():
            raise InterruptedError
        r = _cb_get(_CB_ROOMLIST_URL, params={"limit": _CB_ROOMLIST_PAGE,
                                              "offset": offset}, timeout=15)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"HTTP {r.status_code} at offset {offset}")
        data = r.json()
        if offset == 0:
            fetch.total = int(data.get("total_count") or 0)
        return data.get("rooms") or []

    rooms: set = set()

    def add(page: list):
        for room in page:
            u = (room.get("username") or "").lower()
            if u:
                rooms.add(u)

    try:
        first = fetch(0)
    except InterruptedError:
        return None
    except Exception as e:
        logger.warning(f"[CB] room-list fetch failed: {e}")
        return None
    add(first)
    total = getattr(fetch, "total", 0)
    if not first or not total:
        return rooms
    # Step by the page size (a page can carry an extra room; overlap is
    # harmless in a set) and fetch one page past total_count to catch rooms
    # that came online mid-sweep.
    offsets = list(range(_CB_ROOMLIST_PAGE, total + _CB_ROOMLIST_PAGE,
                         _CB_ROOMLIST_PAGE))
    done = 1
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="cb-roomlist") as pool:
        futures = [pool.submit(fetch, o) for o in offsets]
        for fut in as_completed(futures):
            try:
                add(fut.result())
            except InterruptedError:
                pool.shutdown(wait=False, cancel_futures=True)
                return None
            except Exception as e:
                pool.shutdown(wait=False, cancel_futures=True)
                logger.warning(f"[CB] room-list fetch failed: {e}")
                return None
            done += 1
            if on_progress and done % 25 == 0:
                on_progress(min(done * _CB_ROOMLIST_PAGE, total), total)
    return rooms


def _stripchat_is_live(model_name: str) -> Optional[bool]:
    """Lightweight online check for the saved-models scanner.
    Returns True/False, or None when the page couldn't be fetched
    (so the caller keeps the previous status)."""
    import stripchat_native
    try:
        info = stripchat_native.page_info(model_name)
    except Exception as e:
        logger.error(f"[ST] live-check error for {model_name}: {e}")
        return None
    if info is None:
        return None
    return bool(info.get("is_live"))


def get_stream_url(site: str, model_name: str, thorough: bool = False,
                   max_age: float = 0.0) -> Optional[str]:
    """Resolve a model's stream URL. `max_age` is the oldest cached answer the
    caller will accept, in seconds; 0 (the default) always goes to the network,
    which is what every liveness check wants. Only user actions that are
    already gated on a fresh status — opening the Player, previewing — should
    pass a non-zero value."""
    if site == "chaturbate":
        # thorough (manual REC) and max_age (Player/Preview) both mean a user
        # is waiting — let those skip the CB pacing queue.
        return get_chaturbate_stream_url(model_name, max_retries=4 if thorough else 1,
                                         max_age=max_age,
                                         interactive=thorough or max_age > 0)
    elif site == "stripchat":
        return get_stripchat_stream_url(model_name, max_age=max_age)
    elif site == "camsoda":
        return get_camsoda_stream_url(model_name)
    elif site == "myfreecams":
        import mfc
        return mfc.get_stream_url(model_name, max_retries=3 if thorough else 1)
    return None


# ── FFmpeg ────────────────────────────────────────────────────────────────────

def find_ffmpeg(override: str = "") -> str:
    """Resolve an absolute path to ffmpeg.exe by checking the filesystem.
    No probe subprocess: spawning `ffmpeg -version` can fail transiently
    (post-boot churn, Defender first-scan, console-less pythonw quirks) and
    used to make the monitor refuse to start even though ffmpeg was fine.
    On a miss, the error lists every candidate checked — never a mystery."""
    here  = os.path.dirname(os.path.abspath(__file__))
    local = os.environ.get("LOCALAPPDATA", "")

    def candidates():
        if override:
            yield "settings ffmpeg_path", override
        yield "app folder", os.path.join(here, "ffmpeg", "ffmpeg.exe")
        yield "app folder", os.path.join(here, "ffmpeg.exe")
        # repo root (one level up from src/) — where the README says to drop ffmpeg
        root = os.path.dirname(here)
        yield "project root", os.path.join(root, "ffmpeg", "ffmpeg.exe")
        yield "project root", os.path.join(root, "ffmpeg.exe")
        yield "PATH", shutil.which("ffmpeg") or "(no 'ffmpeg' on PATH)"
        if local:
            yield "WinGet links", os.path.join(
                local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")
            for hit in sorted(glob.glob(os.path.join(
                    local, "Microsoft", "WinGet", "Packages",
                    "*FFmpeg*", "**", "bin", "ffmpeg.exe"), recursive=True)):
                yield "WinGet package", hit

    checked = []
    for label, path in candidates():
        if os.path.isfile(path):
            return os.path.abspath(path)
        checked.append(f"{path} [{label}]")
    detail = "; ".join(checked)
    logger.error("ffmpeg not found. Checked: %s", detail)
    raise FileNotFoundError(
        f"ffmpeg not found. Checked: {detail}. Install it "
        f"(winget install Gyan.FFmpeg) or set ffmpeg_path in "
        f"~/.streamrecorder_config.json")


SITE_TAGS = {"chaturbate": "CB", "stripchat": "ST", "camsoda": "CS",
             "myfreecams": "MFC"}


def build_output_path(session: RecordingSession) -> str:
    """Path for the session's current part. A recording that never splits keeps
    NO suffix; once a split occurs (part > 1) every part carries _partNNN. The
    base name (incl. timestamp) is computed once and reused for all parts."""
    if session.base_name is None:
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        site_tag = SITE_TAGS.get(session.site, session.site[:2].upper())
        session.base_name = f"{session.model_name}_{site_tag}_{ts}"
    part_tag = f"_part{session.part:03d}" if session.part > 1 else ""
    return os.path.join(session.output_dir,
                        f"{session.base_name}{part_tag}.ts")


def _popen_ffmpeg(cmd: list) -> subprocess.Popen:
    """Launch ffmpeg with stdin pipe so we can request a graceful 'q' shutdown.
    On Windows we also create a new process group so CTRL_BREAK_EVENT is an option."""
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags = 0
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        # stdout is never read — a PIPE would silently fill and block the
        # child; stderr IS drained (see StreamRecorder._drain_stderr)
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def graceful_stop(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """Tell ffmpeg to finish cleanly so the MPEG-TS trailer is flushed
    (prevents corrupted .ts files). Falls back to terminate/kill."""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    try:
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass


def launch_ffmpeg_hls(stream_url: str, output_path: str, ffmpeg_path: str,
                      site: str = "", label: str = "") -> subprocess.Popen:
    """Launch ffmpeg to record an HLS stream.
    Adds flags that prevent corrupt-packet propagation and reconnect on drop —
    fixes truncated .ts files seen in the wild."""
    headers_map = {
        "stripchat": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://stripchat.com\r\nReferer: https://stripchat.com/\r\n"
        ),
        "chaturbate": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://chaturbate.com\r\nReferer: https://chaturbate.com/\r\n"
        ),
        "camsoda": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://www.camsoda.com\r\nReferer: https://www.camsoda.com/\r\n"
        ),
        "myfreecams": (
            f"User-Agent: {USER_AGENT}\r\n"
            "Origin: https://www.myfreecams.com\r\nReferer: https://www.myfreecams.com/\r\n"
        ),
    }
    headers = headers_map.get(site, f"User-Agent: {USER_AGENT}\r\n")
    if site in ("chaturbate", "camsoda", "myfreecams"):
        # Route through the local relay: it pins the highest-bitrate variant
        # and (for CB) survives the edge's mid-segment TLS resets. Plain HTTP
        # to 127.0.0.1; requests fetches upstream reliably.
        import cb_relay
        stream_url = cb_relay.wrap(stream_url, USER_AGENT, mode=site,
                                   label=label)
        headers = ""
    cmd = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error",
    ]
    if headers:
        cmd += ["-headers", headers]
    cmd += [
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "10",
    ]
    if site in ("chaturbate", "camsoda", "myfreecams"):
        # Relay segment URLs may use extensions outside ffmpeg's HLS default
        # whitelist (e.g. Camsoda's .fmp4) — accept them all.
        cmd += ["-allowed_extensions", "ALL"]
    if site in ("chaturbate", "myfreecams"):
        cmd += ["-m3u8_hold_counters", "20"]
    cmd += [
        "-i", stream_url,
        "-c", "copy",
        "-copyts",
        output_path,
    ]
    return _popen_ffmpeg(cmd)


def launch_stripchat_playwright(model_name: str, output_path: str) -> subprocess.Popen:
    """
    Spawn the browser-based Stripchat recorder (stripchat_live.py) as a
    subprocess. It behaves like a Popen ffmpeg process: writes a live-growing
    .ts file at output_path, honors stdin-close as a graceful shutdown signal.

    Why browser-based: Stripchat's MOUFLON DRM encrypts variant-playlist URIs
    so ffmpeg-over-HLS hangs. Headless Chromium decodes them for us.
    """
    script = os.path.join(os.path.dirname(__file__), "stripchat_live.py")
    cmd = [sys.executable, script, model_name, output_path]
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags = 0
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        # stdout is never read — a PIPE would silently fill and block the
        # recorder script; stderr IS drained by _drain_stderr
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def launch_stripchat_native(model_name: str, output_path: str,
                            ffmpeg_path: str) -> Optional[subprocess.Popen]:
    """Browserless Stripchat path: resolve the MOUFLON-keyed variant, serve it
    through the local relay, and record with plain ffmpeg -c copy (light,
    single-quality). Returns None if the native path can't be used (model not
    public, keys rotated, advert loop) — caller should fall back to Playwright.
    """
    import stripchat_native
    import cb_relay
    try:
        keyed = stripchat_native.resolve(model_name)
    except Exception:
        return None
    if not keyed:
        return None
    relay_url = cb_relay.wrap(keyed, USER_AGENT, mode="stripchat",
                              label=f"stripchat:{model_name}")
    return launch_ffmpeg_hls(relay_url, output_path, ffmpeg_path,
                             site="stripchat")


# ── StreamRecorder ────────────────────────────────────────────────────────────

# Low-disk guard thresholds (user-configurable via Settings): with the guard
# enabled, all recordings stop and no new ones may start once free space drops
# below the stop threshold; they stay blocked until free space climbs back up
# to the (higher) resume threshold. The gap between the two is a hysteresis
# margin — without it, a recording that trips the guard would immediately be
# allowed to restart the moment a few MB free back up, re-trip on the next
# tick, and loop forever (see CHANGELOG).
LOW_DISK_STOP_GB_DEFAULT = 20
LOW_DISK_RESUME_GB_DEFAULT = 40


class StreamRecorder:
    def __init__(self):
        self.models: dict[str, ModelConfig] = {}
        self.ffmpeg_path: str = ""
        self.output_dir: str  = os.path.expanduser("~/Videos/StreamRecorder")
        self.max_size_mb: Optional[int] = None
        self.check_interval: int = 30
        self.browser: str = "brave"

        self.on_status_change: Optional[Callable[[str, ModelStatus, str], None]] = None
        # (title, body, model_key=None). model_key is "site:name" for per-model
        # events (started/stopped/dropped/downgraded) so the UI can VIP-filter.
        self.on_notification:  Optional[Callable[..., None]] = None
        self.on_log:           Optional[Callable[[str], None]] = None

        # Relay reports segments that expired before they could be downloaded
        # (bandwidth saturated). Always logged; notification is opt-out.
        self.gap_warnings_enabled: bool = True
        self._gap_warn_ts: dict[str, float] = {}

        # Low-disk guard: when enabled, every recording is stopped and new ones
        # are refused while the output drive is below low_disk_stop_gb; it
        # stays tripped until free space climbs back up to low_disk_resume_gb.
        self.low_disk_guard_enabled: bool = False
        self.low_disk_stop_gb: float = LOW_DISK_STOP_GB_DEFAULT
        self.low_disk_resume_gb: float = LOW_DISK_RESUME_GB_DEFAULT
        self._low_disk_tripped: bool = False   # guard is currently blocking
        self._low_disk_log_ts: float = 0.0     # throttle refused-start log lines

        # Stripchat only: when the browserless native path can't be used, fall
        # back to the Playwright browser recorder. When disabled, the stream is
        # simply not recorded and Playwright never launches (app-owned flag).
        self.playwright_fallback_enabled: bool = True

        # Quality caps: the relay asks effective_quality(label) for the max
        # variant height each time a master playlist is fetched (recording
        # start/restart). Per-model override beats global; an auto-downgrade
        # (session-only) beats both but never applies to models the user
        # capped manually.
        self.quality_global: int = 0                 # 0 = unlimited
        self.quality_overrides: dict[str, int] = {}  # label → height (app-owned)
        self.auto_downgrade_enabled: bool = False
        self._session_q: dict[str, int] = {}         # label → downgraded height
        self._gap_window: dict[str, list] = {}       # label → [start_ts, sec_lost]
        self._downgrade_ts: dict[str, float] = {}    # label → last downgrade time

        import cb_relay
        cb_relay.set_gap_callback(self._on_relay_gap)
        cb_relay.set_quality_callback(self.effective_quality)
        cb_relay.set_advert_callback(self._on_relay_advert)

        self._lock    = threading.Lock()
        # Per-group monitor flags — one thread per group ("recorder", "saved")
        self._running: dict[str, bool] = {"recorder": False, "saved": False}
        # Always-on session watcher — handles split/stall/exit even when the
        # monitor is off (e.g. user clicked REC without starting monitoring).
        self._session_watcher_started = False

        # Every ffmpeg/recorder process ever launched, independent of which
        # one (if any) a ModelConfig currently references. stop_all_recordings
        # kills from this list rather than trusting cfg.session bookkeeping
        # alone, so a model whose tracked session got out of sync with reality
        # (e.g. a bug that let two processes get launched for one split) can't
        # leave an orphan running and consuming bandwidth/disk after "stop all".
        self._all_procs: list[subprocess.Popen] = []
        self._all_procs_lock = threading.Lock()

    def _on_relay_gap(self, label: str, missed: int, seconds: float):
        """Relay callback (prefetch thread): segments expired unfetched."""
        self._log(f"⚠ {label}: {missed} segment(s) (~{seconds:.0f}s) lost — "
                  f"download can't keep up with the live stream")
        self._maybe_downgrade(label, seconds)
        if not self.gap_warnings_enabled or not self.on_notification:
            return
        now = time.time()
        if now - self._gap_warn_ts.get(label, 0.0) < 60:
            return  # at most one toast per stream per minute
        self._gap_warn_ts[label] = now
        # Defer the toast: when a model simply goes OFFLINE, the last live-edge
        # segments expire unfetched and look exactly like bandwidth loss. Wait a
        # few seconds and only notify if the model is STILL recording — that
        # rules out the "stream just ended" false positive. (The gap is always
        # logged above regardless.)
        try:
            site, name = label.split(":", 1)
            pretty = f"{name} ({site})"
        except ValueError:
            pretty = label

        def _deferred(lbl=label, secs=seconds, pretty=pretty):
            time.sleep(5)
            cfg = self.models.get(lbl)
            if not cfg or not cfg.session:
                return  # stream ended in the meantime → it was just going offline
            if self.on_notification:
                self.on_notification(
                    "Dropped segments",
                    f"{pretty}: ~{secs:.0f}s of video lost — your internet "
                    f"bandwidth can't keep up with all active recordings.", lbl)
        threading.Thread(target=_deferred, daemon=True,
                         name=f"gap-notify-{label}").start()

    def _on_relay_advert(self, label: str):
        """Relay callback (server thread): a Stripchat playlist that was live
        switched to the advert placeholder loop — the model went offline but
        the CDN keeps serving looping filler segments, so the file keeps
        growing and the stall detector never fires. Stop the session; the
        exit handler flips it to OFFLINE (no restart)."""
        cfg = self.models.get(label)
        session = cfg.session if cfg else None
        if not cfg or not session or session.advert_stop or cfg.stop_requested:
            return
        session.advert_stop = True
        self._log(f"{cfg.name} ({cfg.site}): advert loop detected — model went "
                  f"offline, stopping recording.")
        # Off-thread: graceful_stop blocks up to 10 s and this runs on the
        # relay's HTTP-server thread.
        threading.Thread(target=graceful_stop, args=(session.process,),
                         kwargs={"timeout": 5}, daemon=True,
                         name=f"advert-stop-{cfg.name}").start()

    # ── Quality caps & auto-downgrade ─────────────────────────────────────────

    # Downgrade ladder, thresholds: a stream losing ≥10 s of video within a
    # 60 s window steps down one rung; 2 min cooldown after each step so the
    # restart's own instability doesn't immediately trigger the next one.
    _DOWNGRADE_STEPS = (720, 480, 240)
    _DOWNGRADE_WINDOW = 60.0
    _DOWNGRADE_THRESHOLD = 10.0
    _DOWNGRADE_COOLDOWN = 120.0

    def effective_quality(self, label: str) -> int:
        """Max variant height for a stream (relay callback). 0 = unlimited."""
        ov = self.quality_overrides
        return (self._session_q.get(label)
                or ov.get(label) or ov.get(label.lower())
                or self.quality_global or 0)

    def _reset_session_quality(self, cfg):
        """Forget any auto-downgrade when a recording ends naturally — the
        next session starts fresh at the configured quality."""
        label = f"{cfg.site}:{cfg.name}"
        self._session_q.pop(label, None)
        self._gap_window.pop(label, None)

    def _maybe_downgrade(self, label: str, seconds: float):
        """Accumulate segment losses; if a stream persistently can't keep up,
        restart it one quality step lower (session-only, opt-in)."""
        if not self.auto_downgrade_enabled:
            return
        ov = self.quality_overrides
        if label in ov or label.lower() in ov:
            return  # user pinned a quality manually — respect it
        if label.startswith("stripchat:"):
            return  # stripchat bypasses variant selection — nothing to cap
        now = time.time()
        if now - self._downgrade_ts.get(label, 0.0) < self._DOWNGRADE_COOLDOWN:
            return
        w = self._gap_window.get(label)
        if not w or now - w[0] > self._DOWNGRADE_WINDOW:
            w = [now, 0.0]
            self._gap_window[label] = w
        w[1] += seconds
        if w[1] < self._DOWNGRADE_THRESHOLD:
            return
        self._gap_window.pop(label, None)
        self._downgrade_ts[label] = now
        cur = self._session_q.get(label) or self.quality_global or 0
        nxt = next((s for s in self._DOWNGRADE_STEPS if not cur or s < cur), None)
        if nxt is None:
            self._log(f"⬇ {label}: already at lowest quality and still "
                      f"losing segments — bandwidth is saturated")
            return
        cfg = self.models.get(label)
        if not cfg or not cfg.session or not cfg.session.process:
            return
        self._session_q[label] = nxt
        cfg.restart_count = 0  # quality restarts don't burn the crash budget
        self._log(f"⬇ Auto-downgrading {label} to {nxt}p — kept losing "
                  f"segments; restarting recording")
        if self.on_notification:
            self.on_notification(
                "Quality downgraded",
                f"{label} kept losing segments — restarting at {nxt}p.", label)
        graceful_stop(cfg.session.process, timeout=5)
        # _handle_ffmpeg_exit auto-restarts; the relay then asks
        # effective_quality() again and picks the lower variant.

    def add_model(self, name: str, site: str, group: str = "recorder",
                  quiet: bool = False):
        """`quiet` skips the log line — used when bulk-registering a large
        saved-models watchlist (1500+ lines would flood the Activity Log)."""
        key = f"{site}:{name.lower()}"
        with self._lock:
            cfg = self.models.get(key)
            if cfg is None:
                cfg = ModelConfig(name=name.lower(), site=site)
                self.models[key] = cfg
            cfg.groups.add(group)
        if not quiet:
            self._log(f"Added {site}/{name} [{group}]")

    def remove_model(self, name: str, site: str, group: str = "recorder"):
        key = f"{site}:{name.lower()}"
        killed = None
        with self._lock:
            cfg = self.models.get(key)
            if not cfg:
                return
            cfg.groups.discard(group)
            if cfg.groups:
                self._log(f"Removed {site}/{name} [{group}] (still in {cfg.groups})")
                return
            self.models.pop(key, None)
            killed = cfg.session
            cfg.session = None
        if killed:
            # Session is already detached — flush it in the background so
            # GUI-thread callers don't freeze on graceful_stop.
            threading.Thread(target=self._kill_session, args=(killed,),
                             daemon=True, name=f"kill-{key}").start()
        self._log(f"Removed {site}/{name}")

    def start_monitor(self, group: Optional[str] = None) -> bool:
        """Start monitor thread(s). With no arg, starts both 'recorder' and 'saved'.
        Returns False when startup failed (so the GUI doesn't show MONITORING)."""
        groups = [group] if group else ["recorder", "saved"]
        try:
            self.ffmpeg_path = find_ffmpeg()
            self._log(f"ffmpeg: {self.ffmpeg_path}")
        except FileNotFoundError as e:
            self._log(f"ERROR: {e}")
            if self.on_notification:
                self.on_notification("FFmpeg Missing", str(e))
            return False
        os.makedirs(self.output_dir, exist_ok=True)
        for g in groups:
            if self._running.get(g):
                continue
            self._running[g] = True
            threading.Thread(target=self._monitor_loop, args=(g,),
                             daemon=True, name=f"mon-{g}").start()
            self._log(f"Monitor [{g}] started.")
        return True

    def stop_monitor(self, group: Optional[str] = None):
        """Stop monitor thread(s) and kill their sessions. With no arg, stops both."""
        groups = [group] if group else ["recorder", "saved"]
        for g in groups:
            self._running[g] = False
        with self._lock:
            victims = []
            for cfg in self.models.values():
                if not cfg.session:
                    continue
                # Only kill sessions whose groups are ALL being stopped
                # (so a shared-group model keeps recording under the other monitor)
                if cfg.groups.issubset(set(groups)) or not cfg.groups:
                    victims.append(cfg.session)
                    cfg.session = None
                    cfg.stop_requested = True
        # Kill OUTSIDE the lock (so monitor/GUI threads aren't blocked) and in
        # parallel — graceful_stop waits up to ~15 s per process, so a serial
        # loop over many recordings froze the app for minutes.
        threads = [
            threading.Thread(target=self._kill_session, args=(s,), daemon=True)
            for s in victims
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        for g in groups:
            self._log(f"Monitor [{g}] stopped.")

    def start_recording(self, name: str, site: str) -> bool:
        key = f"{site}:{name.lower()}"
        with self._lock:
            cfg = self.models.get(key)
            if not cfg:
                return False
            if cfg.session:
                self._log(f"Already recording {name}")
                return False
            self._set_status(cfg, ModelStatus.CHECKING, "")

        # Ensure ffmpeg is available (may not be set if monitor is off)
        if not self.ffmpeg_path:
            try:
                self.ffmpeg_path = find_ffmpeg()
                self._log(f"ffmpeg: {self.ffmpeg_path}")
            except FileNotFoundError as e:
                self._log(f"ERROR: {e}")
                with self._lock:
                    self._set_status(cfg, ModelStatus.ERROR, str(e))
                return False
        os.makedirs(self.output_dir, exist_ok=True)

        self._log(f"Checking if {name} ({site}) is online...")
        url = get_stream_url(site, name, thorough=True)
        if not url:
            with self._lock:
                self._set_status(cfg, ModelStatus.OFFLINE, "")
            self._log(f"{name} ({site}) is offline — cannot record.")
            return False
        with self._lock:
            cfg.stream_url = url
        self._begin_recording(cfg, url)
        return True

    def stop_recording(self, name: str, site: str):
        key = f"{site}:{name.lower()}"
        with self._lock:
            cfg = self.models.get(key)
            if not cfg or not cfg.session:
                return
            session = cfg.session
            cfg.session = None
            cfg.stop_requested = True
            # Always set OFFLINE after explicit stop to avoid triggering
            # auto-rec again in the GUI (user intentionally stopped)
            cfg.stream_url = ""
            self._reset_session_quality(cfg)
            self._set_status(cfg, ModelStatus.OFFLINE, "")
        self._kill_session(session)
        self._log(f"Stopped recording {name} ({site})")

    def stop_all_recordings(self) -> int:
        """Force-stop every active download on every site without touching
        the monitor threads. Returns how many sessions were stopped."""
        with self._lock:
            victims = []
            tracked_procs = set()
            for cfg in self.models.values():
                if not cfg.session:
                    continue
                victims.append(cfg.session)
                if cfg.session.process:
                    tracked_procs.add(cfg.session.process)
                cfg.session = None
                cfg.stop_requested = True
                # OFFLINE so the GUI doesn't immediately auto-rec it again
                cfg.stream_url = ""
                self._reset_session_quality(cfg)
                self._set_status(cfg, ModelStatus.OFFLINE, "")
        # Kill outside the lock and in parallel (same as stop_monitor)
        threads = [
            threading.Thread(target=self._kill_session, args=(s,), daemon=True)
            for s in victims
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        # Safety net: kill any launched process that's still alive but NOT
        # referenced by any cfg.session — an orphan left over from a bug that
        # let two processes get launched for the same split/restart (only the
        # last writer wins cfg.session; the loser used to run forever, immune
        # to this function, invisible to the low-disk guard). Sweeping the
        # full launch history instead of trusting the per-model bookkeeping
        # means "stop everything" really means everything.
        with self._all_procs_lock:
            live = [p for p in self._all_procs if p.poll() is None]
            self._all_procs = live
            orphans = [p for p in live if p not in tracked_procs]
        if orphans:
            self._log(f"⚠ Found {len(orphans)} orphaned recorder process(es) "
                      f"not tracked by any model — force-killing.")
            orphan_threads = [
                threading.Thread(target=graceful_stop, args=(p,),
                                 kwargs={"timeout": 5}, daemon=True)
                for p in orphans
            ]
            for t in orphan_threads:
                t.start()
            for t in orphan_threads:
                t.join(timeout=15)

        self._log(f"Stopped all downloads ({len(victims)} active"
                  f"{f', {len(orphans)} orphaned' if orphans else ''}).")
        return len(victims) + len(orphans)

    # ── Low-disk guard ────────────────────────────────────────────────────────

    def _disk_free_gb(self) -> Optional[float]:
        """Free space (GB) on the drive holding output_dir, or None if it
        can't be determined (never block recordings on a probe failure)."""
        path = self.output_dir
        try:
            # output_dir may not exist yet — walk up to the nearest existing dir
            while path and not os.path.exists(path):
                parent = os.path.dirname(path)
                if parent == path:
                    break
                path = parent
            return shutil.disk_usage(path).free / (1024 ** 3)
        except Exception:
            return None

    def low_disk_blocked(self) -> Optional[float]:
        """If the guard is currently tripped, return the current free GB;
        otherwise None (not blocked). Reads the tripped flag maintained by
        _enforce_low_disk_guard — it does not itself decide trip/resume, so
        callers between ticks see a stable answer instead of re-deciding
        against a single threshold (which is what caused the restart loop:
        see LOW_DISK_STOP_GB_DEFAULT / LOW_DISK_RESUME_GB_DEFAULT above)."""
        if not self.low_disk_guard_enabled or not self._low_disk_tripped:
            return None
        return self._disk_free_gb()

    def _enforce_low_disk_guard(self):
        """Called once per monitor/watcher tick. Hysteresis: trips when free
        space drops below low_disk_stop_gb, stays tripped (blocking all
        starts) until free space climbs back up to low_disk_resume_gb. The
        gap between the two thresholds stops the guard from flapping — a
        single shared threshold would let a just-resumed recording eat the
        sliver of headroom and re-trip on the very next tick."""
        if not self.low_disk_guard_enabled:
            self._low_disk_tripped = False
            return
        free = self._disk_free_gb()
        if free is None:
            return   # never block on a probe failure
        if self._low_disk_tripped:
            if free >= self.low_disk_resume_gb:
                self._low_disk_tripped = False
                self._log(f"✓ Disk space recovered — {free:.1f} GB free "
                          f"(>= {self.low_disk_resume_gb:.0f} GB resume "
                          f"threshold) — recordings are allowed again.")
            return
        if free < self.low_disk_stop_gb:
            self._low_disk_tripped = True
            with self._lock:
                has_active = any(c.session for c in self.models.values())
            self._log(f"⛔ LOW DISK: {free:.1f} GB free on the output drive "
                      f"(< {self.low_disk_stop_gb:.0f} GB) — stopping all "
                      f"downloads; new recordings blocked until free space "
                      f"reaches {self.low_disk_resume_gb:.0f} GB or the guard "
                      f"is disabled in Settings.")
            if self.on_notification:
                self.on_notification(
                    "Low disk space",
                    f"Only {free:.1f} GB free — all recordings stopped. "
                    f"Recording resumes automatically at "
                    f"{self.low_disk_resume_gb:.0f} GB free (or disable the "
                    f"disk guard).")
            if has_active:
                self.stop_all_recordings()

    # Online checks for due models run in a small shared pool: the old serial
    # pass meant one slow site response delayed every other model's check AND
    # the split/stall housekeeping of active sessions. CB calls still go
    # through the shared pacer (_cb_wait), so this doesn't hammer Cloudflare.
    _CHECK_POOL_SIZE = 8

    def _monitor_loop(self, group: str):
        """One pass per 5s tick: session housekeeping for every model
        (serial, local, fast), then online checks for the models whose
        check_interval is due — those run in parallel in a small pool.

        The 'saved' group uses a bulk scanner instead — per-model checks don't
        scale to watchlists with hundreds of models (Cloudflare rate-limits the
        hammering and everything reports as a false OFFLINE)."""
        pool = None
        try:
            if group == "saved":
                self._saved_monitor_loop()
                return
            pool = ThreadPoolExecutor(max_workers=self._CHECK_POOL_SIZE,
                                      thread_name_prefix=f"chk-{group}")
            while self._running.get(group):
                self._enforce_low_disk_guard()
                with self._lock:
                    configs = [c for c in self.models.values() if group in c.groups]
                now = time.time()
                due = []
                for cfg in configs:
                    if not self._running.get(group):
                        break
                    # One bad model/session must never kill the whole loop
                    try:
                        self._session_housekeeping(cfg)
                    except Exception:
                        logger.exception(f"[mon-{group}] housekeeping failed "
                                         f"for {cfg.site}/{cfg.name}")
                    if (not cfg.session
                            and now - cfg.last_checked >= self.check_interval):
                        cfg.last_checked = now   # claim before submitting
                        due.append(cfg)
                if due and self._running.get(group):
                    futs = [pool.submit(self._check_online_safe, c) for c in due]
                    _futures_wait(futs, timeout=90)
                time.sleep(5)
        except Exception as e:
            logger.exception(f"Monitor [{group}] crashed")
            self._log(f"Monitor [{group}] CRASHED: {e!r} — see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)")
        finally:
            self._running[group] = False
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

    def _session_housekeeping(self, cfg: ModelConfig):
        """Split/stall/exit handling for an active session (fast, no network).
        Locked per-model: a model in both the "recorder" and "saved" groups
        gets ticked by two monitor threads, and this path launches replacement
        ffmpeg processes (split/restart) — without the lock both threads can
        race past the same split/exit check and each launch one, orphaning
        whichever loses the cfg.session assignment (see ModelConfig.session_lock)."""
        if not cfg.session:
            return
        with cfg.session_lock:
            if not cfg.session:
                return
            self._check_split(cfg)
            if cfg.session and cfg.session.process:
                if cfg.session.process.poll() is not None:
                    self._handle_ffmpeg_exit(cfg)
                else:
                    self._check_stall(cfg)

    def _check_online_safe(self, cfg: ModelConfig):
        try:
            self._check_online(cfg)
        except Exception:
            logger.exception(f"online check failed for {cfg.site}/{cfg.name}")

    def _check_online(self, cfg: ModelConfig):
        if cfg.session:
            return
        prev = cfg.status
        self._set_status(cfg, ModelStatus.CHECKING, "")
        url = get_stream_url(cfg.site, cfg.name)
        # Re-check session after slow network call — auto-rec may
        # have started a recording while we were fetching the URL
        if cfg.session:
            return
        if (not url and cfg.site == "chaturbate"
                and prev in (ModelStatus.ONLINE, ModelStatus.OFFLINE)
                and chaturbate_status_unknown(cfg.name)):
            # Throttled / network blip: no answer isn't "offline". Keep the
            # last known status; the next check will try again.
            self._set_status(cfg, prev, "")
            return
        if url:
            cfg.stream_url = url
            self._set_status(cfg, ModelStatus.ONLINE, "")
        else:
            cfg.stream_url = ""
            if cfg.site == "myfreecams":
                # The lookup already told us the video state — show PRIVATE
                # (with a 5-min cooldown) instead of OFFLINE when she's in a
                # private/group show or away.
                import mfc
                if mfc.last_status(cfg.name) in ("private", "away"):
                    cfg.last_checked = time.time() + 300
                    self._set_status(cfg, ModelStatus.PRIVATE, "")
                    return
            self._set_status(cfg, ModelStatus.OFFLINE, "")

    def _drain_stderr(self, proc: Optional[subprocess.Popen], name: str):
        """Forward a recorder process's stderr to the log from a daemon
        thread. EVERY launch path needs one: an undrained stderr pipe fills
        up (~64 KB) and blocks ffmpeg mid-write, which stalls the recording
        until the stall detector kills it."""
        if proc is None or proc.stderr is None:
            return
        def _pump(p=proc, n=name):
            try:
                for line in p.stderr:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        self._log(f"[ffmpeg/{n}] {decoded}")
            except Exception:
                pass
        threading.Thread(target=_pump, daemon=True,
                         name=f"stderr-{name}").start()

    def _launch_proc(self, cfg: ModelConfig, output_path: str,
                     stream_url: str) -> Optional[subprocess.Popen]:
        """Start the recording process for a model. Stripchat tries the
        browserless native path first and falls back to Playwright; other
        sites use ffmpeg directly."""
        if cfg.site == "stripchat":
            proc = launch_stripchat_native(cfg.name, output_path,
                                           self.ffmpeg_path)
            if proc is not None:
                self._log(f"{cfg.name}: native HLS path (no browser)")
            elif self.playwright_fallback_enabled:
                self._log(f"{cfg.name}: native path unavailable — browser fallback")
                proc = launch_stripchat_playwright(cfg.name, output_path)
            else:
                self._log(f"{cfg.name}: native path unavailable — Playwright "
                          f"fallback disabled, not recording")
                return None
        else:
            proc = launch_ffmpeg_hls(stream_url, output_path, self.ffmpeg_path,
                                     site=cfg.site,
                                     label=f"{cfg.site}:{cfg.name}")
        self._drain_stderr(proc, cfg.name)
        if proc is not None:
            with self._all_procs_lock:
                # Prune exited processes here (rather than a separate timer)
                # so the list can't grow unbounded over a long-running session.
                self._all_procs = [p for p in self._all_procs if p.poll() is None]
                self._all_procs.append(proc)
        return proc

    def _saved_monitor_loop(self):
        """Saved-group monitor: 5s session housekeeping tick + a bulk status
        scan every check_interval, run in a worker thread so housekeeping
        stays responsive while the scan runs."""
        try:
            last_scan = 0.0
            scan_thread: Optional[threading.Thread] = None
            while self._running.get("saved"):
                self._enforce_low_disk_guard()
                with self._lock:
                    configs = [c for c in self.models.values() if "saved" in c.groups]
                for cfg in configs:
                    if not self._running.get("saved"):
                        break
                    if not cfg.session:
                        continue
                    try:
                        self._check_split(cfg)
                        if cfg.session and cfg.session.process:
                            if cfg.session.process.poll() is not None:
                                self._handle_ffmpeg_exit(cfg)
                            else:
                                self._check_stall(cfg)
                    except Exception:
                        logger.exception(f"[mon-saved] housekeeping failed for "
                                         f"{cfg.site}/{cfg.name}")
                now = time.time()
                if ((scan_thread is None or not scan_thread.is_alive())
                        and now - last_scan >= self.check_interval):
                    last_scan = now
                    scan_thread = threading.Thread(target=self._scan_saved_pass,
                                                   daemon=True, name="saved-scan")
                    scan_thread.start()
                time.sleep(5)
        except Exception as e:
            logger.exception("Monitor [saved] crashed")
            self._log(f"Monitor [saved] CRASHED: {e!r} — see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)")
        finally:
            self._running["saved"] = False

    def _scan_saved_pass(self):
        try:
            self._scan_saved_pass_inner()
        except Exception as e:
            logger.exception("Saved scan crashed")
            self._log(f"Saved scan CRASHED: {e!r} — see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)")

    # Saved scan, Chaturbate: up to this many saved CB models are checked one
    # by one; above it a full room-list sweep (~90 requests) is cheaper. The
    # sweep is also capped to once per _CB_SWEEP_MIN_INTERVAL seconds, so a
    # short check_interval can't turn it into constant load.
    _CB_PER_MODEL_MAX = 45
    _CB_SWEEP_MIN_INTERVAL = 60
    _cb_last_sweep = 0.0

    def _scan_saved_pass_inner(self):
        """One bulk status pass over all saved-group models:
        - Chaturbate: per-model lookups for small lists, else one room-list
          sweep with a membership test (see _CB_PER_MODEL_MAX)
        - Stripchat/Camsoda: per-model checks through a small thread pool
        Models with an active session are skipped; statuses only change on a
        definitive online/offline answer (failures keep the previous status)."""
        running = lambda: self._running.get("saved", False)
        t0 = time.time()
        with self._lock:
            configs = [c for c in self.models.values() if "saved" in c.groups]
        cb = [c for c in configs if c.site == "chaturbate"]
        others = [c for c in configs if c.site in ("stripchat", "camsoda")]
        mfcs = [c for c in configs if c.site == "myfreecams"]
        if not configs:
            return
        self._log(f"Saved scan started ({len(configs)} models)…")
        counts = {"chaturbate": 0, "stripchat": 0, "camsoda": 0,
                  "myfreecams": 0}

        # Stripchat/Camsoda per-model checks run CONCURRENTLY with the long
        # Chaturbate sweep so the first statuses appear within seconds
        def scan_others():
            def check(cfg: ModelConfig):
                if cfg.site == "stripchat":
                    return cfg, _stripchat_is_live(cfg.name)
                return cfg, (get_camsoda_stream_url(cfg.name) is not None)
            try:
                self._log(f"Saved scan: checking {len(others)} "
                          f"Stripchat/Camsoda models…")
                done = 0
                with ThreadPoolExecutor(max_workers=6,
                                        thread_name_prefix="saved-scan") as pool:
                    futures = [pool.submit(check, c) for c in others]
                    for fut in as_completed(futures):
                        if not running():
                            pool.shutdown(wait=False, cancel_futures=True)
                            break
                        try:
                            cfg, live = fut.result()
                        except Exception:
                            logger.exception("[saved-scan] SC/CS check failed")
                            continue
                        done += 1
                        if done % 150 == 0:
                            self._log(f"Saved scan: Stripchat/Camsoda "
                                      f"{done}/{len(others)} checked…")
                        if live is None:
                            continue  # fetch failed — keep previous status
                        counts[cfg.site] += live
                        self._apply_scan_status(cfg, live)
            except Exception as e:
                logger.exception("Saved scan (Stripchat/Camsoda) crashed")
                self._log(f"Saved scan (SC/CS) CRASHED: {e!r} "
                          f"— see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)")

        # MyFreeCams: one websocket connection per sweep, sequential lookups
        def scan_mfc():
            try:
                import mfc
                self._log(f"Saved scan: checking {len(mfcs)} MyFreeCams models…")
                res = mfc.lookup_models([c.name for c in mfcs])
                for cfg in mfcs:
                    if not running():
                        break
                    live = res.get(cfg.name.lower())
                    if live is None:
                        continue  # lookup failed — keep previous status
                    counts["myfreecams"] += live
                    self._apply_scan_status(cfg, live)
            except Exception as e:
                logger.exception("Saved scan (MyFreeCams) crashed")
                self._log(f"Saved scan (MFC) CRASHED: {e!r} "
                          f"— see streamrecorder.log (%LOCALAPPDATA%\\Scr33nX)")

        t_others = None
        if others and running():
            t_others = threading.Thread(target=scan_others, daemon=True,
                                        name="saved-scan-others")
            t_others.start()
        t_mfc = None
        if mfcs and running():
            t_mfc = threading.Thread(target=scan_mfc, daemon=True,
                                     name="saved-scan-mfc")
            t_mfc.start()

        if cb and running() and len(cb) <= self._CB_PER_MODEL_MAX:
            # Few CB models: look each one up directly. That's fewer requests
            # than a ~90-page sweep, and it caches each online model's URL,
            # so opening her in the Player skips its own API call.
            def check_cb(cfg: ModelConfig):
                _url, live = chaturbate_lookup(cfg.name)
                return cfg, live
            with ThreadPoolExecutor(max_workers=6,
                                    thread_name_prefix="saved-scan-cb") as pool:
                futures = [pool.submit(check_cb, c) for c in cb]
                for fut in as_completed(futures):
                    if not running():
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    try:
                        cfg, live = fut.result()
                    except Exception:
                        logger.exception("[saved-scan] CB check failed")
                        continue
                    if live is None:
                        continue  # unreadable answer — keep previous status
                    counts["chaturbate"] += live
                    self._apply_scan_status(cfg, live)
            if running():
                self._log(f"Saved scan: Chaturbate done — "
                          f"{counts['chaturbate']}/{len(cb)} online.")
        elif cb and running() and (time.time() - self._cb_last_sweep
                                   >= self._CB_SWEEP_MIN_INTERVAL):
            self._cb_last_sweep = time.time()
            rooms = get_chaturbate_online_rooms(
                running,
                lambda got, total: self._log(
                    f"Saved scan: Chaturbate sweep {got}/{total} rooms…"))
            if rooms is None:
                if running():
                    self._log("Saved scan: Chaturbate room list unavailable "
                              "(rate-limited?) — keeping previous statuses.")
            else:
                for cfg in cb:
                    online = cfg.name.lower() in rooms
                    counts["chaturbate"] += online
                    self._apply_scan_status(cfg, online)
                self._log(f"Saved scan: Chaturbate done — "
                          f"{counts['chaturbate']}/{len(cb)} online.")
        elif cb:
            # Sweep ran less than _CB_SWEEP_MIN_INTERVAL ago — CB statuses
            # stand; don't count them as offline in the summary below.
            cb = []

        if t_others is not None:
            t_others.join()
        if t_mfc is not None:
            t_mfc.join()

        if running():
            n_sc = sum(1 for c in others if c.site == "stripchat")
            n_cs = len(others) - n_sc
            parts = []
            if cb:
                parts.append(f"CB {counts['chaturbate']}/{len(cb)} online")
            if n_sc:
                parts.append(f"SC {counts['stripchat']}/{n_sc} online")
            if n_cs:
                parts.append(f"CS {counts['camsoda']}/{n_cs} online")
            if mfcs:
                parts.append(f"MFC {counts['myfreecams']}/{len(mfcs)} online")
            self._log(f"Saved scan done: {', '.join(parts)} "
                      f"({time.time() - t0:.0f}s)")

    def _apply_scan_status(self, cfg: ModelConfig, online: bool):
        with self._lock:
            if cfg.session:
                return
            new = ModelStatus.ONLINE if online else ModelStatus.OFFLINE
            if not online:
                cfg.stream_url = ""
            if cfg.status != new:
                self._set_status(cfg, new, "")

    def _begin_recording(self, cfg: ModelConfig, stream_url: str):
        # Serialised with _session_housekeeping (same per-model lock): manual
        # REC, auto-rec, and the delayed auto-restart can all reach this, and
        # without the lock two callers racing here would both pass the
        # `cfg.session` check below and each launch an ffmpeg process —
        # exactly the orphaned-process bug this lock exists to prevent.
        with cfg.session_lock:
            self._begin_recording_locked(cfg, stream_url)

    def _begin_recording_locked(self, cfg: ModelConfig, stream_url: str):
        if cfg.session:
            return   # another thread already started a session for this model
        # Low-disk guard: refuse every start (manual REC, auto-rec, restart)
        # while the output drive is below the threshold.
        free = self.low_disk_blocked()
        if free is not None:
            self._low_disk_tripped = True
            now = time.time()
            if now - self._low_disk_log_ts >= 60:   # don't spam per model/tick
                self._low_disk_log_ts = now
                self._log(f"⛔ Not recording {cfg.name} ({cfg.site}): only "
                          f"{free:.1f} GB free (guard resumes at "
                          f"{self.low_disk_resume_gb:.0f} GB). Free up space "
                          f"or disable the disk guard.")
            self._set_status(cfg, ModelStatus.ERROR,
                             f"Low disk: {free:.1f} GB free")
            return
        cfg.stop_requested = False
        session = RecordingSession(
            model_name=cfg.name, site=cfg.site,
            output_dir=self.output_dir, max_size_mb=self.max_size_mb,
            stream_url=stream_url,
        )
        output_path          = build_output_path(session)
        session.current_file = output_path
        session.start_time   = time.time()
        session.last_size_change = time.time()

        try:
            proc = self._launch_proc(cfg, output_path, stream_url)

            if proc is None:
                self._set_status(cfg, ModelStatus.ERROR, "Could not get stream URL")
                return

            session.process = proc
            cfg.session     = session
            self._set_status(cfg, ModelStatus.RECORDING, output_path)
            self._ensure_session_watcher()
            self._log(f"Recording {cfg.site}/{cfg.name} → {output_path}")
            if self.on_notification:
                self.on_notification("Recording Started",
                                     f"{cfg.name} ({cfg.site}) is now recording.",
                                     f"{cfg.site}:{cfg.name}")
        except Exception as e:
            self._set_status(cfg, ModelStatus.ERROR, str(e))
            self._log(f"Recording failed for {cfg.name}: {e}")

    def _ensure_session_watcher(self):
        """Lazy-start the always-on session watcher on the first recording."""
        if self._session_watcher_started:
            return
        self._session_watcher_started = True
        threading.Thread(target=self._session_watch_loop,
                         daemon=True, name="session-watcher").start()
        self._log("Session watcher started.")

    def _session_watch_loop(self):
        """Handles split, stall detection, and ffmpeg-exit → OFFLINE
        transitions for any active session WHEN THE MONITOR IS OFF.
        When the monitor is running it already does these checks, so this
        loop idles to avoid duplicated work and race conditions."""
        while True:
            time.sleep(60)
            # Skip entirely while any monitor is running — it handles sessions
            if any(self._running.values()):
                continue
            self._enforce_low_disk_guard()
            with self._lock:
                active = [c for c in self.models.values() if c.session]
            for cfg in active:
                if not cfg.session:
                    continue
                try:
                    self._check_split(cfg)
                    if cfg.session and cfg.session.process:
                        if cfg.session.process.poll() is not None:
                            self._handle_ffmpeg_exit(cfg)
                        else:
                            self._check_stall(cfg)
                except Exception as e:
                    self._log(f"session-watcher error for {cfg.name}: {e}")

    def _check_split(self, cfg: ModelConfig):
        session = cfg.session
        if not session or not session.max_size_mb or not session.current_file:
            return
        try:
            size_mb = os.path.getsize(session.current_file) / (1024 * 1024)
        except OSError:
            return
        if size_mb < session.max_size_mb:
            return
        self._log(f"Split {cfg.name}: {size_mb:.0f}/{session.max_size_mb} MB")
        # Graceful stop so the .ts trailer flushes before we open the next part
        graceful_stop(session.process, timeout=10)
        # First split: the unsuffixed part-1 file becomes _part001 so the set
        # reads _part001/_part002/… Done synchronously right after the handle
        # closes to minimise the window where the pipeline could grab it.
        if session.part == 1 and session.current_file and session.base_name:
            first = os.path.join(session.output_dir,
                                 f"{session.base_name}_part001.ts")
            try:
                os.replace(session.current_file, first)
            except OSError as e:
                self._log(f"{cfg.name}: couldn't rename first part to _part001: {e}")
        session.part += 1
        if self.low_disk_blocked() is not None:
            # Low-disk guard tripped between ticks — don't open the next part.
            cfg.session = None
            self._set_status(cfg, ModelStatus.OFFLINE, "")
            return
        url = get_stream_url(cfg.site, cfg.name) or session.stream_url
        if url:
            output_path          = build_output_path(session)
            session.current_file = output_path
            session.stream_url   = url
            session.last_size    = 0
            session.last_size_change = time.time()
            try:
                proc = self._launch_proc(cfg, output_path, url)
                if proc:
                    session.process = proc
                    self._set_status(cfg, ModelStatus.RECORDING, output_path)
                    self._log(f"Part {session.part} → {output_path}")
            except Exception as e:
                self._set_status(cfg, ModelStatus.ERROR, str(e))
        else:
            cfg.session = None
            self._set_status(cfg, ModelStatus.OFFLINE, "")

    def _check_stall(self, cfg: ModelConfig):
        """Kill ffmpeg if the output file hasn't grown for 60 seconds."""
        session = cfg.session
        if not session or not session.current_file:
            return
        try:
            size = os.path.getsize(session.current_file)
        except OSError:
            # File not created (yet). Treat as 0 bytes so the stall clock keeps
            # running — an early return here made a recorder that never writes
            # its output file (hung connect) undetectable: RECORDING forever.
            size = 0
        now = time.time()
        if size > session.last_size:
            session.last_size = size
            session.last_size_change = now
            session.stall_probed = False     # growing again — re-arm the probe
            return
        # Allow 60s grace period (stream buffering, brief interruptions)
        stall_secs = now - session.last_size_change
        # Active offline probe: after a short stall, confirm via the resolver
        # ONCE (off the monitor thread so housekeeping stays fast). If she's
        # actually offline, stop now instead of waiting out the full 60s — this
        # is what makes RECORDING→OFFLINE fast. If she's still online (just
        # buffering), we leave the recording alone and the 60s hard-kill below
        # remains as the backstop.
        if 20 <= stall_secs < 60 and not session.stall_probed:
            session.stall_probed = True
            threading.Thread(
                target=self._probe_offline_while_stalled, args=(cfg, session),
                daemon=True, name=f"stall-probe-{cfg.site}-{cfg.name}").start()
        if stall_secs < 60:
            return
        self._log(f"Stall detected for {cfg.name} — no data for {stall_secs:.0f}s, flushing ffmpeg")
        graceful_stop(session.process, timeout=5)
        # _handle_ffmpeg_exit will run on the next loop iteration
        # and handle restart logic

    def _probe_offline_while_stalled(self, cfg: "ModelConfig", session):
        """Worker thread: a recording has stalled — ask the resolver whether the
        model is still online. If she's offline, stop the (reconnect-hanging)
        ffmpeg now so _handle_ffmpeg_exit flips it to OFFLINE quickly. If she's
        still online it's just buffering; re-arm so we can probe again later."""
        # Bail if the session changed/ended while we were queued.
        if cfg.stop_requested or cfg.session is not session:
            return
        try:
            url = get_stream_url(cfg.site, cfg.name)
        except Exception:
            session.stall_probed = False     # probe failed — allow a retry
            return
        if cfg.stop_requested or cfg.session is not session:
            return
        if not url:
            self._log(f"{cfg.name} ({cfg.site}) confirmed offline while stalled — "
                      f"stopping recording.")
            if session.process:
                graceful_stop(session.process, timeout=5)
            # _session_housekeeping → _handle_ffmpeg_exit sets OFFLINE next tick.
        else:
            # Still online — genuine buffering. Let it keep going; allow another
            # probe if it stays stalled.
            session.stall_probed = False

    def _handle_ffmpeg_exit(self, cfg: ModelConfig):
        if not cfg.session:
            return
        rc  = cfg.session.process.returncode if cfg.session.process else -1
        self._log(f"ffmpeg exited for {cfg.name} (rc={rc})")

        # Advert-loop stop: the relay confirmed the model went offline (the
        # playlist turned into the SC advert placeholder). Straight to OFFLINE —
        # restarting would just re-record the advert loop.
        if cfg.session.advert_stop:
            cfg.restart_count = 0
            cfg.session       = None
            cfg.stream_url    = ""
            self._reset_session_quality(cfg)
            self._set_status(cfg, ModelStatus.OFFLINE, "")
            if self.on_notification:
                self.on_notification("Recording Stopped",
                                     f"{cfg.name} ({cfg.site}) went offline.",
                                     f"{cfg.site}:{cfg.name}")
            return

        # Stripchat: rc 4 = idle (no segments), rc 5 = ticket/private/group show
        # Model is online but not publicly broadcasting — show PRIVATE, no restart.
        if cfg.site == "stripchat" and rc in (4, 5):
            # Clean up stale .ts (usually 0-byte or moov-only, unplayable)
            try:
                if (cfg.session.current_file
                        and os.path.exists(cfg.session.current_file)
                        and os.path.getsize(cfg.session.current_file) < 64 * 1024):
                    os.remove(cfg.session.current_file)
            except OSError:
                pass
            cfg.restart_count = 0
            cfg.session       = None
            cfg.stream_url    = ""
            self._reset_session_quality(cfg)
            # 5-minute cooldown so the monitor doesn't immediately flip back to ONLINE
            cfg.last_checked  = time.time() + 300
            label = "ticket/private show" if rc == 5 else "no public segments"
            self._log(f"{cfg.name} ({cfg.site}) is in {label} — status=PRIVATE, retry in 5 min")
            self._set_status(cfg, ModelStatus.PRIVATE, "")
            return

        if rc in (0, 1) and cfg.restart_count < 3:
            cfg.restart_count += 1
            self._log(f"Auto-restarting {cfg.name} (attempt {cfg.restart_count}/3)...")
            cfg.session = None
            # Push last_checked forward to prevent monitor loop from
            # re-checking this model during the restart delay window
            cfg.last_checked = time.time() + 10

            def _delayed_restart(c=cfg):
                time.sleep(3)
                # Don't resurrect a session the user stopped or removed.
                # (The old guard `if not self._running:` was dead code —
                # _running is a dict, which is always truthy.)
                if c.stop_requested or self.models.get(f"{c.site}:{c.name}") is not c:
                    return
                # Another recording may have been started while we waited
                if c.session:
                    return
                # Re-resolve the URL — after an ffmpeg exit the old one is
                # usually expired, and starting on a stale URL just burns a
                # restart attempt on a guaranteed failure. Retry once.
                new_url = get_stream_url(c.site, c.name)
                if not new_url:
                    time.sleep(3)
                    if c.stop_requested or c.session:
                        return
                    new_url = get_stream_url(c.site, c.name)
                if new_url:
                    c.stream_url = new_url
                    self._begin_recording(c, new_url)
                else:
                    c.restart_count = 0
                    self._set_status(c, ModelStatus.OFFLINE, "")
            threading.Thread(target=_delayed_restart, daemon=True).start()
            return

        cfg.restart_count = 0
        cfg.session    = None
        cfg.stream_url = ""
        self._reset_session_quality(cfg)
        self._set_status(cfg, ModelStatus.OFFLINE, "")
        if self.on_notification:
            self.on_notification("Recording Stopped",
                                 f"{cfg.name} ({cfg.site}) stream ended.",
                                 f"{cfg.site}:{cfg.name}")

    def _kill_session(self, session: RecordingSession):
        session.stopped = True
        # Graceful 'q' flush so the MPEG-TS trailer is written — fixes the
        # corrupted .ts files observed on prior recordings.
        graceful_stop(session.process, timeout=10)

    def _set_status(self, cfg: ModelConfig, status: ModelStatus, detail: str):
        cfg.status        = status
        cfg.error_message = detail if status == ModelStatus.ERROR else ""
        if self.on_status_change:
            self.on_status_change(f"{cfg.site}:{cfg.name}", status, detail)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        logger.info(msg)
        if self.on_log:
            self.on_log(f"[{ts}] {msg}")
