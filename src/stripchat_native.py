"""
stripchat_native.py — browserless Stripchat HLS recording.

Stripchat protects its doppiocdn playlists with "MOUFLON": the master
playlist at edge-hls.doppiocdn.com lists accepted key-ids as
`#EXT-X-MOUFLON:PSCH:v2:<keyId>` lines, and the real media playlist (fetched
with `?psch=v2&pkey=<keyId>` appended to a variant URL) carries each true
segment URL inside a `#EXT-X-MOUFLON:URI:` tag, with a dummy line after it
for players that lack the key.

This module resolves a model to a single pinned-quality variant URL and
rewrites its media playlist into clean HLS, so plain `ffmpeg -c copy` can
record it — same lightweight path as Chaturbate, no headless Chromium, and
a fixed resolution (no adaptive-bitrate quality drift).

The model's numeric stream id comes from scraping the server-rendered model
page (see page_info): the former `/api/front/v2/models/username/<n>/cam`
endpoint is now bot-blocked with HTTP 418 on every request.

Key table + algorithm are public knowledge from kesamom/stripchat_mouflon
and lossless1024/StreaMonitor. When Stripchat rotates keys, none of the
master's PSCH ids will match and resolve() returns None — the caller then
falls back to the Playwright recorder.
"""

import base64
import hashlib
import itertools
import json
import os
import re
import threading
import time
from typing import Optional

import requests

import netproxy

# keyId → decryption key. Override/extend via stripchat_mouflon_keys.json
# placed next to this file (no code change needed when keys rotate).
MOUFLON_KEYS = {
    "Zokee2OhPh9kugh4": "Quean4cai9boJa5a",
    "Zeechoej4aleeshi": "ubahjae7goPoodi6",
    "Ook7quaiNgiyuhai": "EQueeGh2kaewa3ch",
    "Fq6m2TO2ZeBkRPm9": "xb6di1NF9EFXHUwb",
    "GrRncsoByZmsiT6L": "NigHYyOD9l4rvAEb",
    "1Dzcc6OjP73LKbtI": "Y64UVwX5RrIWnOLp",
    "N2oLovTIXb0o28Uj": "ABE7Sj8jh3oPM2ae",
    "NTK9aqcLmNFMWrpQ": "tOcYOap4Ty1l9Jzb",
}

_KEYS_FILE = os.path.join(os.path.dirname(__file__), "stripchat_mouflon_keys.json")
try:
    if os.path.exists(_KEYS_FILE):
        with open(_KEYS_FILE, encoding="utf-8") as _f:
            MOUFLON_KEYS.update(json.load(_f))
except Exception:
    pass

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://stripchat.com/"}
# The model page is only server-rendered (and only returns 200) when the
# request looks like a document navigation: without an `Accept: text/html…`
# header stripchat answers 406 with an empty body, and with a *full* browser
# Accept/Sec-Fetch set it serves a client-rendered shell that carries no
# streamName. This exact combination is the one that yields the SSR page.
_HTML_HEADERS = {
    **_HEADERS,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}
_session = requests.Session()

# Model-page cache. The page is ~430 KB and gets asked for repeatedly in one
# user action (online check → preview → record start), so a fresh result is
# always *stored* here — but it is only *read* when the caller explicitly says
# how stale an answer it will accept (`max_age`). The default is 0: liveness
# checks always go to the network, so a cached page can never make a model who
# just went offline look online, whatever the user's Check Interval is set to.
# Anything the site actually answered (200, or 404 = no such model) is stored,
# including "she's offline"; only an unreadable response is left out so callers
# keep their previous status. Storing an offline answer is safe precisely
# because the readers are the ones that pass max_age > 0, and they already
# refuse to act on a model who isn't showing as online.
_page_cache: dict[str, tuple[float, dict]] = {}
_page_lock = threading.Lock()
_PAGE_CACHE_MAX = 512  # prune oldest entries past this, so a long run can't grow forever

_DUMMY = "media.mp4"


def _decode(b64: str, key: str) -> str:
    """MOUFLON cipher: base64 → XOR with cyclic SHA256(key)."""
    h = hashlib.sha256(key.encode("utf-8")).digest()
    data = base64.b64decode(b64 + "==")
    return bytes(a ^ b for a, b in zip(data, itertools.cycle(h))).decode(
        "utf-8", "replace"
    )


def _mouflon_key(text: str):
    """Return (psch, pdkey) for the first PSCH line whose key we know."""
    for line in text.splitlines():
        if line.startswith("#EXT-X-MOUFLON:PSCH:"):
            parts = line.strip().split(":")
            if len(parts) >= 4 and parts[3] in MOUFLON_KEYS:
                return parts[2], MOUFLON_KEYS[parts[3]]
    return None, None


def rewrite_playlist(text: str) -> str:
    """Decrypt MOUFLON segment URLs and strip MOUFLON tags → clean HLS.

    v2: the `#EXT-X-MOUFLON:URI:` value is a near-real URL whose 2nd-to-last
    `_`-delimited token is the segment name reversed + XOR-encrypted; decrypt
    it, then the resulting relative path replaces the dummy `media.mp4` line
    that follows. v1: the `#EXT-X-MOUFLON:FILE:` value decodes to the filename.
    (Algorithm per lossless1024/StreaMonitor.)
    """
    psch, pdkey = _mouflon_key(text)
    if not pdkey:
        return text  # unknown key → caller falls back to Playwright
    attr = "#EXT-X-MOUFLON:URI:" if psch == "v2" else "#EXT-X-MOUFLON:FILE:"

    out = []
    last = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(attr):
            val = s[len(attr):]
            try:
                if psch == "v2":
                    enc = val.split("_")[-2]
                    dec = _decode(enc[::-1], pdkey)
                    last = val.replace(enc, dec).split("/", 4)[4]
                else:
                    last = _decode(val, pdkey)
            except Exception:
                last = None
        elif s.startswith("#EXT-X-MOUFLON"):
            continue  # PSCH / ADVERT markers — drop
        elif last and s.endswith(_DUMMY):
            out.append(line.replace(_DUMMY, last))
            last = None
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def page_info(model_name: str, max_age: float = 0.0) -> Optional[dict]:
    """Scrape the model page for {"is_live", "stream_id", "status"}.

    `max_age` is the oldest cached answer the caller will accept, in seconds.
    Leave it at 0 (the default) for anything that decides whether a model is
    online — those must always hit the network. Pass a few tens of seconds
    from a user action that is already gated on a fresh status (opening the
    Player, previewing) to reuse the page the online check just fetched.

    Returns None when the page couldn't be read (network error / non-200 that
    isn't a 404) so callers can keep whatever status they already had; a 404
    yields a normal record with is_live False.

    Why the page and not the API: `/api/front/v2/models/username/<n>/cam`
    (the old source of the stream id) is now answered with HTTP 418 by
    stripchat's bot filter for every request, no matter the headers, cookies
    or preceding page visit — so it can no longer be used at all. The
    server-rendered page still carries `"isLive"`, `"streamName"` and
    `"status"`, and `streamName` is the same numeric id the CDN wants.
    """
    now = time.time()
    key = model_name.lower()
    if max_age > 0:
        with _page_lock:
            hit = _page_cache.get(key)
            if hit and now - hit[0] < max_age:
                return hit[1]

    try:
        r = _session.get(f"https://stripchat.com/{model_name}",
                         headers=_HTML_HEADERS, timeout=20,
                         proxies=netproxy.requests_proxies("stripchat"))
    except requests.RequestException:
        return None
    if r.status_code == 404:
        info = {"is_live": False, "stream_id": None, "status": ""}
    elif r.status_code != 200:
        return None
    else:
        html = r.text
        m = re.search(r'"streamName"\s*:\s*"(\d+)"', html)
        st = re.search(r'"status"\s*:\s*"(\w+)"', html)
        info = {
            "is_live": bool(re.search(r'"isLive"\s*:\s*true', html, re.I)),
            "stream_id": m.group(1) if m else None,
            "status": st.group(1) if st else "",
        }

    with _page_lock:
        _page_cache[key] = (now, info)
        if len(_page_cache) > _PAGE_CACHE_MAX:
            for k in sorted(_page_cache, key=lambda k: _page_cache[k][0])[:_PAGE_CACHE_MAX // 4]:
                del _page_cache[k]
    return info


def stream_id(model_name: str, max_age: float = 0.0) -> Optional[str]:
    """Numeric stream id for a live model, or None if it isn't live / the page
    couldn't be read. Publicness (vs group/private/advert) is validated later
    in resolve() against the actual playlist, which answers "Forbidden" for
    shows the anonymous viewer isn't entitled to.

    See page_info() for `max_age`."""
    info = page_info(model_name, max_age=max_age)
    if not info or not info.get("is_live"):
        return None
    return info.get("stream_id")


def _pick_variant(master: str) -> Optional[str]:
    """Highest-BANDWIDTH variant URL from the master playlist."""
    best_url, best_bw = None, -1
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            mbw = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(mbw.group(1)) if mbw else 0
            url = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if url and not url.startswith("#") and bw > best_bw:
                best_url, best_bw = url, bw
    return best_url


def resolve(model_name: str, max_age: float = 0.0) -> Optional[str]:
    """Return a keyed, single-quality Stripchat variant URL ready for the relay,
    or None if the model isn't publicly live / no known key matches / only an
    advert loop is being served (caller should fall back to Playwright).

    See page_info() for `max_age`."""
    sid = stream_id(model_name, max_age=max_age)
    if not sid:
        return None
    master_url = (
        f"https://edge-hls.doppiocdn.com/hls/{sid}/master/{sid}_auto.m3u8"
    )
    try:
        master = _session.get(master_url, headers=_HEADERS, timeout=20,
                              proxies=netproxy.requests_proxies("stripchat")).text
    except requests.RequestException:
        return None

    key_ids = re.findall(r"#EXT-X-MOUFLON:PSCH:v2:(\S+)", master)
    key_id = next((k for k in key_ids if k in MOUFLON_KEYS), None)
    if not key_id:
        return None  # keys rotated — fall back to browser method

    variant = _pick_variant(master)
    if not variant:
        return None
    sep = "&" if "?" in variant else "?"
    keyed = f"{variant}{sep}psch=v2&pkey={key_id}"

    # Validate it's a real public stream, not an advert placeholder loop.
    try:
        media = _session.get(keyed, headers=_HEADERS, timeout=20,
                             proxies=netproxy.requests_proxies("stripchat")).text
    except requests.RequestException:
        return None
    if "MOUFLON-ADVERT" in media or "/cpa/" in media:
        return None
    if "#EXT-X-MOUFLON:" not in media and "#EXTINF" not in media:
        return None
    return keyed
