"""
netproxy.py — per-site outbound proxy settings.

Some networks get refused by a site's video servers (Chaturbate's mmcdn edges
answer 403 to whole ISP ranges). A proxy (e.g. Cloudflare WARP in proxy mode,
`socks5h://127.0.0.1:40000`, or any VPN/SSH tunnel exposing SOCKS/HTTP) lets
just Scr33nX's traffic for that site take another route.

Resolution for a site: its own entry ("direct" forces no proxy) → the default
proxy → none. The local relay, the control API and OpenClaw talk to 127.0.0.1
and never use this. Chaturbate's room lookups (chaturbate.com API) always go
direct — only its video servers are gated, and direct is faster; every other
site's traffic (API + video) uses its proxy.
"""

import logging
import re
import threading
import urllib.parse

import requests

logger = logging.getLogger(__name__)

SITES = ("chaturbate", "stripchat", "camsoda", "myfreecams")
DIRECT = "direct"          # per-site value: bypass the default proxy

_SCHEMES = ("socks5", "socks5h", "socks4", "socks4a", "http", "https")

_lock = threading.Lock()
_default = ""
_sites: dict[str, str] = {}


def validate(value: str) -> tuple[str, str]:
    """Normalize a proxy string → (normalized, error). '' and 'direct' pass
    through. A bare host:port means SOCKS5; socks5 is upgraded to socks5h so
    the proxy resolves hostnames (local DNS would give the wrong region)."""
    v = (value or "").strip()
    if not v or v.lower() == DIRECT:
        return v.lower() if v else "", ""
    if "://" not in v:
        v = "socks5h://" + v
    try:
        u = urllib.parse.urlparse(v)
        port = u.port
    except ValueError:
        return "", "Invalid proxy address."
    scheme = u.scheme.lower()
    if scheme not in _SCHEMES:
        return "", f"Unsupported proxy type '{scheme}' (use socks5://, http://)."
    if not u.hostname or not port:
        return "", "Proxy needs a host and port, e.g. socks5h://127.0.0.1:40000"
    if scheme == "socks5":
        scheme = "socks5h"
    elif scheme == "socks4":
        scheme = "socks4a"
    netloc = u.netloc
    return f"{scheme}://{netloc}", ""


def configure(default: str, sites: dict | None):
    """Install the settings. Invalid entries are dropped (and logged), never
    raised — a bad value in the config file must not stop the app."""
    global _default
    d, err = validate(default)
    if err or d == DIRECT:
        if err:
            logger.warning("[proxy] ignoring invalid default proxy: %s", err)
        d = ""
    clean = {}
    for site, val in (sites or {}).items():
        site = str(site).lower()
        if site not in SITES:
            continue
        n, err = validate(val)
        if err:
            logger.warning("[proxy] ignoring invalid %s proxy: %s", site, err)
            continue
        if n:
            clean[site] = n
    with _lock:
        _default = d
        _sites.clear()
        _sites.update(clean)


def proxy_url(site: str, kind: str = "media") -> str | None:
    """Proxy URL for `site`, or None for a direct connection. `kind` is
    "api" (lookups) or "media" (video); see the module docstring."""
    site = (site or "").lower()
    if site == "chaturbate" and kind == "api":
        return None
    with _lock:
        v = _sites.get(site, "")
        d = _default
    if v == DIRECT:
        return None
    return v or d or None


def requests_proxies(site: str, kind: str = "media") -> dict | None:
    """`proxies=` argument for requests, or None (→ requests' default)."""
    u = proxy_url(site, kind)
    return {"http": u, "https": u} if u else None


def ws_kwargs(site: str) -> dict:
    """Extra kwargs for websocket.create_connection()."""
    u = proxy_url(site, "api")
    if not u:
        return {}
    p = urllib.parse.urlparse(u)
    kw = {"http_proxy_host": p.hostname, "http_proxy_port": p.port,
          "proxy_type": "http" if p.scheme.startswith("http") else p.scheme}
    if p.username:
        kw["http_proxy_auth"] = (urllib.parse.unquote(p.username),
                                 urllib.parse.unquote(p.password or ""))
    return kw


def playwright_proxy(site: str) -> str | None:
    """Proxy for the headless browser. Chromium resolves names on a SOCKS5
    proxy itself, so `socks5h` is passed as plain `socks5`."""
    u = proxy_url(site, "media")
    if u and u.startswith("socks5h://"):
        u = "socks5://" + u[len("socks5h://"):]
    return u


def _scrub(text: str) -> str:
    """Strip credentials from an error message before it reaches the UI."""
    return re.sub(r"://[^/@\s]+@", "://***@", str(text))


def test(proxy: str, site: str = "") -> dict:
    """Probe a proxy value ('' = direct). Never raises. Returns
    {ok, message, exit_ip?, country?}. For Chaturbate it also fetches a live
    room's playlist from the video servers (the thing that gets refused)."""
    norm, err = validate(proxy)
    if err:
        return {"ok": False, "message": err}
    u = None if norm in ("", DIRECT) else norm
    proxies = {"http": u, "https": u} if u else None
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
    out = {"ok": False, "message": ""}
    try:
        r = requests.get("https://www.cloudflare.com/cdn-cgi/trace",
                         proxies=proxies, timeout=10, headers=ua)
        info = dict(l.split("=", 1) for l in r.text.splitlines() if "=" in l)
        out["exit_ip"] = info.get("ip", "")
        out["country"] = info.get("loc", "")
    except requests.RequestException as e:
        logger.debug("[proxy] test failed: %s", _scrub(e))
        if u:
            host = urllib.parse.urlparse(u).netloc.rsplit("@", 1)[-1]
            return {"ok": False, "message": f"Can't connect through the proxy "
                    f"— is it running at {host}?"}
        return {"ok": False, "message": "No internet connection."}
    where = f"{out['exit_ip']} ({out['country']})" if out.get("exit_ip") else "ok"
    site = (site or "").lower()
    if site == "chaturbate":
        try:
            lst = requests.get(
                "https://chaturbate.com/api/ts/roomlist/room-list/?limit=3",
                headers=ua, timeout=12).json().get("rooms") or []
            ctx = requests.get(
                f"https://chaturbate.com/api/chatvideocontext/{lst[0]['username']}/",
                headers=ua, timeout=12).json()
            hls = ctx.get("hls_source")
            if not hls:
                raise ValueError("no live room")
            r = requests.get(hls, headers={**ua, "Referer": "https://chaturbate.com/"},
                             proxies=proxies, timeout=12)
        except (requests.RequestException, ValueError, KeyError, IndexError):
            out.update(ok=True, message=f"Proxy works, exit {where}. Couldn't "
                       "check Chaturbate's video servers just now (try again).")
            return out
        if 200 <= r.status_code < 300:
            out.update(ok=True, message=f"Chaturbate video servers accept this "
                       f"connection — exit {where}.")
        else:
            out.update(ok=False, message=f"Chaturbate video servers refuse this "
                       f"connection (HTTP {r.status_code}) — exit {where}.")
        return out
    out.update(ok=True, message=f"Connection works — exit {where}.")
    return out
