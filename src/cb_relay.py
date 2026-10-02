"""
cb_relay.py — local HTTP relay for site HLS streams.

Chaturbate's llhls CDN edges reset ffmpeg's TLS connections mid-segment
(ffmpeg/Schannel "The specified session has been invalidated", error -10054),
which truncates fMP4 segments and corrupts recordings. Python's requests
stack downloads the same segments reliably, so ffmpeg is pointed at this
relay on 127.0.0.1 (plain HTTP) and the relay fetches upstream via requests.

Playlists are rewritten so every URI (segments, EXT-X-MAP, EXT-X-MEDIA,
EXT-X-PART) also goes through the relay.

A per-stream `mode` (carried in each wrapped URL) selects the playlist
transform: "chaturbate" strips LL-HLS partial-segment tags; "stripchat"
applies MOUFLON decryption (stripchat_native.rewrite_playlist) so plain
ffmpeg can record Stripchat without a browser.
"""

import re
import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_port: int | None = None
_user_agent = _DEFAULT_UA
_session = requests.Session()
# All streams hit the same CDN host; the urllib3 default of 10 pooled
# connections per host forces fresh TLS handshakes under concurrent load.
# Sized to match the prefetch pool plus playlist/miss traffic so workers
# never block waiting for a free connection.
_adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=128)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


class _FamilyAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that only connects over one IP family. Binding the source
    address to the family's wildcard ("0.0.0.0" / "::") makes every candidate
    of the other family from getaddrinfo fail locally, so urllib3 falls
    through to the host's A (or AAAA) record."""

    def __init__(self, source: str, **kwargs):
        self._source = source  # set first: super().__init__ builds the pool
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["source_address"] = (self._source, 0)
        super().init_poolmanager(*args, **kwargs)


# Chaturbate's mmcdn edges 403 every request (master, media playlist, init,
# segments) that arrives over the wrong IP family, and Chaturbate has flipped
# which one that is: IPv6 was refused from ~2026-09-23, then by 2026-10-02 it
# was IPv4 that got refused and IPv6 that worked. The family only gates the
# *fetch* — a token minted over one family plays fine over the other — and a
# 403 from the wrong family doesn't spend the token. So rather than pin a
# family, CB edge requests try the preferred one, fall back to the other on a
# 403 or a connection failure, and make whichever answered 2xx the preferred
# one (see _cb_edge_get). Other sites use the default dual-stack session.
_cb_sessions: dict[str, requests.Session] = {}
for _fam, _src in (("v6", "::"), ("v4", "0.0.0.0")):
    _s = requests.Session()
    _a = _FamilyAdapter(_src, pool_connections=32, pool_maxsize=128)
    _s.mount("https://", _a)
    _s.mount("http://", _a)
    _cb_sessions[_fam] = _s
del _fam, _src, _s, _a
_cb_order = ["v6", "v4"]   # preference order; head is tried first
_cb_order_lock = threading.Lock()


def _cb_prefer(fam: str):
    """Make `fam` the first family tried for Chaturbate edge requests."""
    with _cb_order_lock:
        if _cb_order[0] == fam:
            return
        _cb_order.remove(fam)
        _cb_order.insert(0, fam)
    logger.info("Chaturbate edges now reached over %s (the other family was "
                "refused)", "IPv6" if fam == "v6" else "IPv4")


def _cb_edge_get(url: str, headers: dict, timeout, stream: bool = False
                 ) -> requests.Response:
    """GET a Chaturbate edge URL over whichever IP family the edge accepts.

    Tries the preferred family; a 403 (the edge refusing that family) or a
    connection error (no route for it) moves on to the other. A 2xx from a
    non-preferred family promotes it. Any other status — 404, 5xx — means the
    edge accepted the family, so it's returned as is. If every family 403s
    the last 403 is returned (a genuinely spent/expired token).

    If the *preferred* family raised (a timeout on a slow edge, a reset) and
    nothing else succeeded, the exception is re-raised rather than reported
    as the other family's 403 — that 403 is just the refused family, and
    raising lets the caller's retry loop try the preferred family again."""
    with _cb_order_lock:
        order = list(_cb_order)
    last_resp, last_exc, pref_exc = None, None, None
    for fam in order:
        try:
            r = _cb_sessions[fam].get(url, timeout=timeout, headers=headers,
                                      stream=stream)
        except requests.RequestException as exc:
            last_exc = exc
            if fam == order[0]:
                pref_exc = exc
            continue
        if r.status_code != 403:
            if 200 <= r.status_code < 300:
                _cb_prefer(fam)
            if last_resp is not None:
                last_resp.close()
            return r
        if last_resp is not None:
            last_resp.close()
        last_resp = r
    if pref_exc is not None:
        if last_resp is not None:
            last_resp.close()
        raise pref_exc
    if last_resp is not None:
        return last_resp
    raise last_exc


def _get(url: str, mode: str, timeout, stream: bool = False) -> requests.Response:
    """One upstream GET for `mode` (family-adaptive for Chaturbate)."""
    headers = _headers(mode)
    if mode == "chaturbate":
        return _cb_edge_get(url, headers, timeout, stream)
    return _session.get(url, timeout=timeout, headers=headers, stream=stream)


# ── Bandwidth accounting ──────────────────────────────────────────────────────
# Every byte fetched upstream is counted here; the app polls bytes_downloaded()
# to drive the bandwidth meter.

_bytes_lock = threading.Lock()
_bytes_total = 0


def _count(n: int):
    global _bytes_total
    with _bytes_lock:
        _bytes_total += n


def bytes_downloaded() -> int:
    """Total bytes fetched from upstream CDNs since process start."""
    with _bytes_lock:
        return _bytes_total


# ── Segment prefetch cache ────────────────────────────────────────────────────
# ffmpeg's hls demuxer downloads segments one at a time; if a download takes
# longer than the segment duration the demuxer falls permanently behind and
# segments expire from the live window (1.6 s × N jumps in the output). The
# relay already rewrites every media playlist, so it knows upcoming segment
# URLs before ffmpeg asks: fetch them in parallel into a small in-memory
# cache and serve ffmpeg's requests instantly, letting slow streams catch up.

# Workers are shared by ALL active streams. A live segment is ~2 s, so with
# N streams the pool must complete ~N/2 downloads per second; a slow segment
# holds a worker for several seconds. 16 workers starved out around ~10-15
# concurrent recordings, making every stream lose segments at once.
_PREFETCH_WORKERS = 64
_CACHE_MAX_BYTES = 768 * 1024 * 1024   # hard cap; beyond it, fall back to miss path
_ENTRY_TTL = 90.0                      # s: drop fetched segments never requested
_FETCHED_TTL = 600.0                   # s: how long to remember served URLs

_executor = ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS,
                               thread_name_prefix="relay-prefetch")
_state_lock = threading.Lock()
_cache: dict[str, "_Entry"] = {}       # upstream URL → in-flight/ready segment
_cache_bytes = 0                       # running total of cached payload bytes
                                       # (counted on successful fetch, subtracted
                                       # on pop — replaces summing the whole
                                       # cache under the lock per refresh)
_fetched: dict[str, float] = {}        # upstream URL → ts of successful fetch
_streams: dict[str, dict] = {}         # stream key → {"segs": [...], "dur": avg}

# Called as fn(stream_label, missed_segments, est_seconds_lost) whenever
# segments expire from the live playlist before they could be downloaded.
_gap_cb = None
_last_cap_warn = 0.0

# Called as fn(stream_label) → max variant height in pixels (0 = unlimited)
# when a master playlist is fetched; lets the app cap stream quality globally
# or per model. Applies when a recording (re)starts, not mid-stream.
_quality_cb = None


def set_gap_callback(fn):
    global _gap_cb
    _gap_cb = fn


def set_quality_callback(fn):
    global _quality_cb
    _quality_cb = fn


# Called as fn(stream_label) when a Stripchat playlist that was live turns
# into the MOUFLON advert placeholder loop — i.e. the model went offline but
# the CDN keeps serving filler segments. Lets the recorder stop immediately
# instead of recording adverts forever (the file keeps growing, so the
# stall detector never triggers).
_advert_cb = None


def set_advert_callback(fn):
    global _advert_cb
    _advert_cb = fn


class _Entry:
    __slots__ = ("event", "data", "ctype", "status", "ts", "error")

    def __init__(self):
        self.event = threading.Event()
        self.data = b""
        self.ctype = "application/octet-stream"
        self.status = 200
        self.ts = time.monotonic()
        self.error = False


def _prefetch_one(url: str, mode: str, entry: _Entry):
    global _cache_bytes
    try:
        # Tight budget: a live segment only exists for ~10-20 s, and a worker
        # stuck on one stalled download starves every other stream. Better to
        # fail fast and let the next playlist refresh retry.
        r = _fetch(url, mode, tries=2, timeout=(5, 10))
        entry.data = r.content
        entry.status = r.status_code
        entry.ctype = r.headers.get("Content-Type", "application/octet-stream")
        _count(len(entry.data))
        if 200 <= r.status_code < 300:
            with _state_lock:
                _fetched[url] = time.monotonic()
                if _cache.get(url) is entry:
                    _cache_bytes += len(entry.data)
        else:
            entry.error = True
    except Exception:
        entry.error = True
    finally:
        entry.ts = time.monotonic()
        entry.event.set()
        if entry.error:
            # Drop failed entries so the next playlist refresh retries them.
            # (Never counted into _cache_bytes — only the success path adds.)
            with _state_lock:
                _cache.pop(url, None)


def _track_media_playlist(text: str, base_url: str, mode: str, key: str):
    """Scan a media playlist: prefetch every segment not yet handled, detect
    segments that expired before we could fetch them, and prune the cache."""
    segs, durs = [], []
    last_dur = 0.0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXTINF"):
            try:
                last_dur = float(s.split(":", 1)[1].split(",")[0])
            except (ValueError, IndexError):
                last_dur = 0.0
        elif s and not s.startswith("#"):
            segs.append(urllib.parse.urljoin(base_url, s))
            durs.append(last_dur)
    if not segs:
        return  # master playlist or empty — nothing to do
    known = [d for d in durs if d > 0]
    avg = (sum(known) / len(known)) if known else 2.0

    now = time.monotonic()
    submit, missed = [], 0
    with _state_lock:
        st = _streams.setdefault(key, {"segs": []})
        new = set(segs)
        # A URL from the previous refresh that vanished without ever being
        # fetched (no cache entry, not in _fetched) expired upstream → gap.
        for u in st["segs"]:
            if u not in new and u not in _fetched and u not in _cache:
                missed += 1
        st["segs"] = segs
        st["dur"] = avg

        # _cache_bytes is maintained incrementally; expired entries are pruned
        # by the janitor thread — no O(cache) work under the lock per refresh.
        if _cache_bytes < _CACHE_MAX_BYTES:
            for u in segs:
                if u not in _cache and u not in _fetched:
                    e = _Entry()
                    _cache[u] = e
                    submit.append((u, e))
        else:
            global _last_cap_warn
            if now - _last_cap_warn > 30:
                _last_cap_warn = now
                logger.warning(
                    "relay prefetch cache full (%d MB, %d entries) — "
                    "prefetch suspended, streams will fall behind",
                    _cache_bytes // (1024 * 1024), len(_cache))
    for u, e in submit:
        _executor.submit(_prefetch_one, u, mode, e)
    if missed and _gap_cb:
        try:
            _gap_cb(key, missed, missed * avg)
        except Exception:
            pass


def _janitor_loop():
    """Prune expired cache entries / fetched-URL records on a fixed cadence.
    Pruning used to happen only inside playlist refreshes — O(cache) work
    under the state lock on every refresh, and once all recordings stopped
    (no more refreshes) the full cache stayed in RAM indefinitely."""
    global _cache_bytes
    while True:
        time.sleep(15)
        now = time.monotonic()
        with _state_lock:
            for u, e in list(_cache.items()):
                if e.event.is_set() and now - e.ts > _ENTRY_TTL:
                    _cache.pop(u, None)
                    if not e.error:
                        _cache_bytes -= len(e.data)
            for u, t in list(_fetched.items()):
                if now - t > _FETCHED_TTL:
                    _fetched.pop(u, None)


# doppiocdn (Stripchat) rejects segment requests without a matching Referer.
_REFERERS = {
    "stripchat":  "https://stripchat.com/",
    "chaturbate": "https://chaturbate.com/",
    "camsoda":    "https://www.camsoda.com/",
    "myfreecams": "https://www.myfreecams.com/",
}


def _headers(mode: str) -> dict:
    headers = {"User-Agent": _user_agent}
    ref = _REFERERS.get(mode)
    if ref:
        headers["Referer"] = ref
        headers["Origin"] = ref.rstrip("/")
    return headers


def _fetch(url: str, mode: str = "chaturbate", tries: int = 3,
           timeout=20) -> requests.Response:
    last_exc = None
    for _ in range(tries):
        try:
            return _get(url, mode, timeout)
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc


def _wrap_url(url: str, mode: str = "chaturbate", label: str = "") -> str:
    # ffmpeg's hls demuxer whitelists segment URLs by *path* extension. Keep
    # .m3u8 for playlists; normalize every other (segment/init) extension to
    # .m4s — a universally-whitelisted fragmented-MP4 extension — so odd
    # upstream extensions (e.g. Camsoda's .fmp4) aren't rejected.
    upath = urllib.parse.urlparse(url).path
    last = upath.rsplit("/", 1)[-1].lower()
    ext = ".m3u8" if last.endswith(".m3u8") else ".m4s"
    extra = f"&l={urllib.parse.quote(label, safe='')}" if label else ""
    return (
        f"http://127.0.0.1:{_port}/p{ext}"
        f"?m={mode}{extra}&u={urllib.parse.quote(url, safe='')}"
    )


# LL-HLS-only tags: stripped so ffmpeg records full segments only and never
# mixes partial segments (which duplicate/overlap data) into the output.
_LL_TAGS = (
    "#EXT-X-PART",          # also matches #EXT-X-PART-INF
    "#EXT-X-PRELOAD-HINT",
    "#EXT-X-RENDITION-REPORT",
    "#EXT-X-SERVER-CONTROL",
)


def _select_highest_variant(text: str, max_height: int = 0) -> str:
    """For a master playlist, keep only the highest-BANDWIDTH video variant
    (and its referenced audio rendition) so ffmpeg can't fall back to a lower
    bitrate. With `max_height` set (e.g. 720), pick the highest-bandwidth
    variant whose RESOLUTION height is ≤ the cap; if every variant is above
    the cap, fall back to the smallest one so the stream still records.
    Media playlists (no EXT-X-STREAM-INF) pass through unchanged."""
    lines = text.splitlines()
    variants = []  # (line_index, bandwidth, height — 0 if no RESOLUTION attr)
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(m.group(1)) if m else 0
            r = re.search(r"RESOLUTION=\d+x(\d+)", line)
            h = int(r.group(1)) if r else 0
            variants.append((i, bw, h))
    if not variants:
        return text  # not a master playlist
    cands = variants
    if max_height > 0 and any(v[2] > 0 for v in variants):
        sized = [v for v in variants if v[2] > 0]
        cands = [v for v in sized if v[2] <= max_height] \
            or [min(sized, key=lambda v: v[2])]
    best_i = max(cands, key=lambda v: v[1])[0]
    inf = lines[best_i]
    url = lines[best_i + 1] if best_i + 1 < len(lines) else ""
    am = re.search(r'AUDIO="([^"]+)"', inf)
    audio_group = am.group(1) if am else None

    out = []
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF") or line.startswith("#EXT-X-MEDIA:"):
            continue  # drop all variant/rendition declarations; re-add chosen below
        s = line.strip()
        if s and not s.startswith("#"):
            continue  # drop variant URL lines
        out.append(line)
    # Re-add the chosen audio rendition (if any) then the chosen video variant.
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:") and audio_group and \
                f'GROUP-ID="{audio_group}"' in line:
            out.append(line)
    out.append(inf)
    out.append(url)
    return "\n".join(out) + "\n"


def _rewrite_playlist(text: str, base_url: str, mode: str = "chaturbate",
                      label: str = "") -> str:
    if mode == "stripchat":
        # Resolve MOUFLON segment URLs / strip MOUFLON tags first, then wrap.
        import stripchat_native
        text = stripchat_native.rewrite_playlist(text)
    elif mode in ("chaturbate", "camsoda", "myfreecams"):
        # Pin the highest-bitrate variant (within the quality cap, if any)
        # on the master playlist.
        cap = 0
        if _quality_cb:
            try:
                cap = int(_quality_cb(label) or 0)
            except Exception:
                cap = 0
        text = _select_highest_variant(text, cap)
    # Kick off parallel segment prefetch (no-op for master playlists).
    _track_media_playlist(text, base_url, mode,
                          label or urllib.parse.urlparse(base_url).path)
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif mode in ("chaturbate", "camsoda", "myfreecams") and s.startswith(_LL_TAGS):
            continue
        elif s.startswith("#"):
            out.append(
                re.sub(
                    r'URI="([^"]+)"',
                    lambda m: 'URI="%s"'
                    % _wrap_url(urllib.parse.urljoin(base_url, m.group(1)),
                                mode, label),
                    line,
                )
            )
        else:
            out.append(_wrap_url(urllib.parse.urljoin(base_url, s), mode, label))
    return "\n".join(out) + "\n"


# Chaturbate master playlists, keyed by upstream URL. The token= in a CB
# master URL is effectively single-use: fetching the same master again within
# ~40 s is 403. So a Player tile retry, or a tile opened on a URL a recording
# already used, would fail. The master only lists variant/audio playlists
# carrying a ?session=. That session stays valid while something polls it
# (measured: a tile polling alongside ffmpeg got only 200s), but it expires
# once idle. So a copy younger than _MASTER_FRESH is replayed as is. An older
# one is used only as a fallback when a fresh fetch of the master is refused.
_MASTER_FRESH = 30.0
_MASTER_TTL = 300.0
_masters: dict[str, tuple[float, str]] = {}
_masters_lock = threading.Lock()


def _master_cached(url: str, max_age: float = _MASTER_FRESH) -> str | None:
    with _masters_lock:
        hit = _masters.get(url)
        if hit and time.monotonic() - hit[0] < max_age:
            return hit[1]
    return None


def _master_store(url: str, text: str):
    now = time.monotonic()
    with _masters_lock:
        _masters[url] = (now, text)
        for k in [k for k, (t, _) in _masters.items() if now - t >= _MASTER_TTL]:
            del _masters[k]


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    # Default listen backlog is 5; with dozens of ffmpeg processes opening
    # connections to this one port, bursts overflow it and connections get
    # refused (ffmpeg "Error number -138").
    request_queue_size = 128

    def handle_error(self, request, client_address):
        pass  # ffmpeg resets idle keep-alive sockets; not an error for us


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence per-request console spam
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        target = qs.get("u", [None])[0]
        mode = qs.get("m", ["chaturbate"])[0]
        label = qs.get("l", [""])[0]
        if not parsed.path.startswith("/p") or not target:
            self.send_error(404)
            return

        is_playlist = parsed.path.endswith(".m3u8")
        if not is_playlist:
            # Segment: usually already prefetched (or in flight) — serve from
            # cache so ffmpeg never waits on the upstream CDN.
            with _state_lock:
                entry = _cache.get(target)
            if entry is not None:
                entry.event.wait(timeout=30)
                if entry.event.is_set() and not entry.error:
                    global _cache_bytes
                    with _state_lock:
                        # ffmpeg fetches each URL once — drop after serving
                        if _cache.pop(target, None) is entry:
                            _cache_bytes -= len(entry.data)
                    self._send(entry.status, entry.ctype, entry.data)
                    return
            self._proxy_stream(target, mode)
            return

        text = _master_cached(target) if mode == "chaturbate" else None
        if text is not None:
            status = 200
        else:
            try:
                r = _fetch(target, mode)
            except Exception:
                self.send_error(502)
                return
            _count(len(r.content))
            status = r.status_code
            text = r.content.decode("utf-8", "replace")
            if mode == "chaturbate":
                if status == 200 and "#EXT-X-STREAM-INF" in text:
                    _master_store(target, text)
                elif status == 403:
                    # Token already spent — replay an older copy; its session
                    # is still good if a recording keeps it polled.
                    old = _master_cached(target, _MASTER_TTL)
                    if old is not None:
                        status, text = 200, old
        # Same advert markers stripchat_native.resolve() rejects at start;
        # mid-recording the CDN swaps a live playlist for this loop when the
        # model goes offline. 404 so ffmpeg stops instead of recording ads.
        if mode == "stripchat" and ("MOUFLON-ADVERT" in text or "/cpa/" in text):
            if _advert_cb:
                try:
                    _advert_cb(label)
                except Exception:
                    pass
            self.send_error(404)
            return
        body = _rewrite_playlist(text, target, mode, label).encode("utf-8")
        self._send(status, "application/vnd.apple.mpegurl", body)

    def _send(self, status: int, ctype: str, body: bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Loopback-only server; the header lets the app's own WebView-based
            # UI (different origin) play previews through the relay via hls.js.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass  # ffmpeg dropped the connection; nothing to do

    def _proxy_stream(self, target: str, mode: str):
        """Cache miss: forward the segment, streaming chunks to ffmpeg as they
        arrive instead of buffering the whole segment first."""
        try:
            r = _get(target, mode, 20, stream=True)
        except requests.RequestException:
            self.send_error(502)
            return
        try:
            clen = r.headers.get("Content-Length")
            self.send_response(r.status_code)
            self.send_header("Content-Type", r.headers.get(
                "Content-Type", "application/octet-stream"))
            if clen:
                self.send_header("Content-Length", clen)
            else:
                self.close_connection = True
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            for chunk in r.iter_content(65536):
                _count(len(chunk))
                self.wfile.write(chunk)
            if 200 <= r.status_code < 300:
                with _state_lock:
                    _fetched[target] = time.monotonic()
        except (ConnectionError, OSError, requests.RequestException):
            self.close_connection = True
        finally:
            r.close()


def wrap(url: str, user_agent: str | None = None,
         mode: str = "chaturbate", label: str = "") -> str:
    """Start the relay (once) and return a localhost URL proxying `url`.
    `mode` selects the playlist transform: "chaturbate" or "stripchat".
    `label` names the stream (e.g. "chaturbate:model") for gap reporting."""
    global _server, _port, _user_agent
    with _lock:
        if user_agent:
            _user_agent = user_agent
        if _server is None:
            _server = _QuietServer(("127.0.0.1", 0), _Handler)
            _port = _server.server_address[1]
            threading.Thread(
                target=_server.serve_forever, daemon=True,
                name="cb-relay",
            ).start()
            threading.Thread(
                target=_janitor_loop, daemon=True,
                name="relay-janitor",
            ).start()
    return _wrap_url(url, mode, label)
