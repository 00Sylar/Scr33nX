"""
mfc.py — browserless MyFreeCams (MFC) status + HLS resolver.

MFC has no public JSON API that maps a model name to her live stream; every
working tool (streamlink forks, MFCAuto, ctbrec, mfc-node) speaks MFC's FCS
chat protocol over a websocket with a guest login:

  1. GET https://www.myfreecams.com/_js/serverconfig.js  (server maps, cached 1h)
  2. connect wss://{xchat}.myfreecams.com/fcsl
  3. send  "hello fcserver\\n\\0"
          "1 0 0 20071025 0 {rand}@guest:guest\\n"        (FCTYPE 1 = LOGIN)
          "10 0 0 20 0 {model_name}\\n"                   (FCTYPE 10 = USERNAMELOOKUP)
  4. server frames are "{6-char length}{FCTYPE} {from} {to} {arg1} {arg2} {payload}";
     the lookup payload is URI-encoded JSON with uid / vs / u.camserv.

Video state (vs): 0 = public chat (recordable), 2 = away, 12 = private,
13 = group show, 14 = club/curtain, 90 = cam off, 127 = offline.

HLS edge URL: uid_video = uid + 100000000; camserv maps to a video host via
serverconfig (h5video_servers → prefix "mfc_", wzobs_servers → "mfc_a_",
heuristic fallback video{camserv-500}); candidates are probed and the first
playlist answering 200 + #EXTM3U wins (hedges the f4v_mobile → f4v_cmaf
migration and serverconfig gaps).

Protocol constants follow streamlink's myfreecams plugin (back-to fork),
Damianonymous/MFCAuto and horacio9a/mfc-node. On protocol drift every entry
point returns None and MFC models simply read OFFLINE — the rest of the app
is unaffected.
"""

import json
import logging
import random
import re
import time
import urllib.parse
from typing import Optional

import requests

import netproxy

logger = logging.getLogger("StreamRecorder")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://www.myfreecams.com/"}
_session = requests.Session()

_SERVERCONFIG_URL = "https://www.myfreecams.com/_js/serverconfig.js"
_LOGIN_VERSION = "20071025"   # FCS protocol constant (see module docstring)

_serverconfig: Optional[dict] = None
_serverconfig_ts = 0.0
_SC_TTL = 3600  # 1 h

# Last known video-state per model (lowercase name) — lets the recorder show
# PRIVATE instead of OFFLINE without a second websocket round-trip.
_last_status: dict = {}


def _get_serverconfig() -> Optional[dict]:
    global _serverconfig, _serverconfig_ts
    if _serverconfig is not None and time.time() - _serverconfig_ts < _SC_TTL:
        return _serverconfig
    try:
        r = _session.get(_SERVERCONFIG_URL, headers=_HEADERS, timeout=15,
                         proxies=netproxy.requests_proxies("myfreecams"))
        r.raise_for_status()
        # File is a bare JSON object (occasionally with a `var x =` wrapper)
        m = re.search(r"\{.*\}", r.text, re.DOTALL)
        if not m:
            return _serverconfig
        _serverconfig = json.loads(m.group(0))
        _serverconfig_ts = time.time()
        return _serverconfig
    except Exception as e:
        logger.error(f"[MFC] serverconfig fetch failed: {e}")
        return _serverconfig  # possibly stale — better than nothing


class _FCSClient:
    """Minimal FCS websocket client: guest login + name lookups."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.ws = None
        self._buf = ""

    def connect(self) -> bool:
        import websocket  # websocket-client
        cfg = _get_serverconfig()
        if not cfg:
            return False
        servers = [h for h, proto in (cfg.get("websocket_servers") or {}).items()
                   if proto == "rfc6455"]
        if not servers:
            return False
        random.shuffle(servers)
        for host in servers[:2]:  # one retry on a different xchat server
            try:
                self.ws = websocket.create_connection(
                    f"wss://{host}.myfreecams.com/fcsl",
                    timeout=self.timeout,
                    origin="https://www.myfreecams.com",
                    header=[f"User-Agent: {_UA}"],
                    **netproxy.ws_kwargs("myfreecams"),
                )
                self.ws.send("hello fcserver\n\0")
                rand = random.randint(10_000_000, 99_999_999)
                self.ws.send(f"1 0 0 {_LOGIN_VERSION} 0 {rand}@guest:guest\n")
                return True
            except Exception as e:
                logger.error(f"[MFC] websocket connect to {host} failed: {e}")
                self.ws = None
        return False

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    def _frames(self):
        """Yield decoded server messages as (fctype, payload) tuples."""
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            # Drain complete frames already in the buffer
            while len(self._buf) >= 6 and self._buf[:6].strip().isdigit():
                n = int(self._buf[:6])
                if len(self._buf) < 6 + n:
                    break
                msg, self._buf = self._buf[6:6 + n], self._buf[6 + n:]
                parts = msg.split(" ", 5)
                if len(parts) >= 5 and parts[0].isdigit():
                    yield int(parts[0]), (parts[5] if len(parts) > 5 else "")
            if self._buf and not self._buf[:6].strip().isdigit():
                # Unexpected framing (protocol drift) — drop the buffer
                self._buf = ""
            try:
                data = self.ws.recv()
            except Exception:
                return
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            self._buf += data or ""

    def lookup(self, name: str) -> Optional[dict]:
        """USERNAMELOOKUP. Returns {'uid', 'vs', 'camserv'} for a model,
        {} when the name doesn't exist, None on transport/protocol failure."""
        try:
            self.ws.send(f"10 0 0 20 0 {name.lower()}\n")
        except Exception as e:
            logger.error(f"[MFC] lookup send failed for {name}: {e}")
            return None
        for fctype, payload in self._frames():
            if fctype != 10:
                continue
            decoded = urllib.parse.unquote(payload).strip()
            if not decoded.startswith("{"):
                return {}  # no such user — payload echoes the queried name
            try:
                obj = json.loads(decoded)
            except Exception:
                return None
            if obj.get("lv") != 4:
                return {}  # exists but is not a model
            return {
                "uid": obj.get("uid"),
                "vs": obj.get("vs"),
                "camserv": (obj.get("u") or {}).get("camserv"),
            }
        return None  # timed out


def _vs_to_status(vs) -> str:
    if vs == 0:
        return "online"
    if vs == 2:
        return "away"
    if vs in (12, 13, 14):
        return "private"
    return "offline"  # 90 (cam off), 127 (offline), unknown


def _do_lookups(names: list) -> dict:
    """One websocket connection, sequential lookups.
    Returns {lowercase_name: info_dict|{}|None}."""
    out = {n.lower(): None for n in names}
    client = _FCSClient()
    if not client.connect():
        return out
    try:
        for n in names:
            info = client.lookup(n)
            out[n.lower()] = info
            if info:  # non-empty dict → cache the video state
                _last_status[n.lower()] = _vs_to_status(info.get("vs"))
            elif info == {}:
                _last_status[n.lower()] = "offline"
    finally:
        client.close()
    return out


def lookup_models(names: list) -> dict:
    """Bulk lookup for the saved-models scanner.
    Returns {lowercase_name: True|False|None} (online / not-public / unknown)."""
    res = _do_lookups(names)
    out = {}
    for n, info in res.items():
        if info is None:
            out[n] = None          # transport failure — keep previous status
        elif not info:
            out[n] = False         # not found / not a model
        else:
            out[n] = info.get("vs") == 0
    return out


def get_status(name: str) -> Optional[str]:
    """'online' | 'away' | 'private' | 'offline', or None on failure."""
    info = _do_lookups([name]).get(name.lower())
    if info is None:
        return None
    if not info:
        return "offline"
    return _vs_to_status(info.get("vs"))


def last_status(name: str) -> Optional[str]:
    """Video state cached by the most recent lookup (no network)."""
    return _last_status.get(name.lower())


def _candidate_urls(uid: int, camserv) -> list:
    cfg = _get_serverconfig() or {}
    uid_video = uid + 100_000_000
    cs = str(camserv) if camserv is not None else ""
    pairs = []  # (server_host, prefix)
    h5 = (cfg.get("h5video_servers") or {}).get(cs)
    wz = (cfg.get("wzobs_servers") or {}).get(cs)
    if h5:
        pairs.append((h5, "mfc_"))
    if wz:
        pairs.append((wz, "mfc_a_"))
    ng = (cfg.get("ngvideo_servers") or {}).get(cs)
    if ng:
        pairs.append((ng, "mfc_"))
    if not pairs and cs.isdigit() and int(cs) > 500:
        pairs.append((f"video{int(cs) - 500}", "mfc_"))
    urls = []
    for server, prefix in pairs:
        for alt in (prefix, "mfc_a_" if prefix == "mfc_" else "mfc_"):
            urls.append(f"https://{server}.myfreecams.com/NxServer/"
                        f"ngrp:{alt}{uid_video}.f4v_cmaf/playlist_sfm4s.m3u8")
            urls.append(f"https://{server}.myfreecams.com/NxServer/"
                        f"ngrp:{alt}{uid_video}.f4v_mobile/playlist.m3u8")
    # De-dup preserving order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def get_stream_url(name: str, max_retries: int = 1) -> Optional[str]:
    """Resolve a model's public HLS playlist URL, or None if she isn't
    publicly streaming (away/private/offline) or on any failure."""
    info = _do_lookups([name]).get(name.lower())
    if not info:
        return None
    if info.get("vs") != 0 or not info.get("uid"):
        return None
    candidates = _candidate_urls(info["uid"], info.get("camserv"))
    for attempt in range(max_retries + 1):
        for url in candidates:
            try:
                r = _session.get(url, headers=_HEADERS, timeout=10,
                                 proxies=netproxy.requests_proxies("myfreecams"))
                if r.status_code == 200 and "#EXTM3U" in r.text[:64]:
                    return url
            except Exception:
                continue
        if attempt < max_retries:
            time.sleep(2)  # CDN warm-up: vs==0 but edge not serving yet
    logger.error(f"[MFC] {name}: vs=0 but no playlist candidate answered "
                 f"({len(candidates)} probed)")
    return None
