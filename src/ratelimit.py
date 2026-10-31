"""Download / upload speed limits (Settings → Speed limits).

DOWNLOAD caps every byte the relay fetches from the stream CDNs (recordings
and Player tiles share it). UPLOAD caps what the Telegram upload workers
send: TDLib has no speed setting, so when a cap is set each TDLib client is
pointed at a tiny local SOCKS5 proxy (started here on demand) that throttles
the client → Telegram direction. Both limits are totals across all streams /
workers and change live; 0 means unlimited.
"""

import socket
import struct
import threading
import time


class RateLimiter:
    """Shared token bucket in bytes/s. consume(n) blocks the caller until n
    more bytes fit under the rate. Waits are computed under the lock and slept
    outside it, so many threads queue up fairly instead of serializing."""

    def __init__(self):
        self._lock = threading.Lock()
        self._rate = 0.0      # bytes/s; 0 = unlimited
        self._next = 0.0      # monotonic time the reserved budget runs out

    def set_mbps(self, mbps):
        try:
            rate = max(0.0, float(mbps or 0)) * 1e6 / 8
        except (TypeError, ValueError):
            rate = 0.0
        with self._lock:
            self._rate = rate
            self._next = time.monotonic()

    @property
    def active(self) -> bool:
        return self._rate > 0

    def consume(self, n: int):
        with self._lock:
            rate = self._rate
            if rate <= 0 or n <= 0:
                return
            now = time.monotonic()
            # At most ~1 s of unused allowance carries over, so an idle
            # period can't turn into a large burst.
            self._next = max(self._next, now - 1.0) + n / rate
            wait = self._next - now
        if wait > 0:
            time.sleep(wait)


DOWNLOAD = RateLimiter()
UPLOAD = RateLimiter()


def configure(dl_mbps, ul_mbps):
    DOWNLOAD.set_mbps(dl_mbps)
    UPLOAD.set_mbps(ul_mbps)


# ── Local throttling SOCKS5 proxy (Telegram uploads) ─────────────────────────

_proxy_lock = threading.Lock()
_proxy_port = 0


def upload_proxy_port() -> int:
    """Start the throttling proxy once (127.0.0.1, random port) and return
    its port. It keeps running for the life of the process."""
    global _proxy_port
    with _proxy_lock:
        if _proxy_port:
            return _proxy_port
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        _proxy_port = srv.getsockname()[1]
        threading.Thread(target=_accept_loop, args=(srv,), daemon=True,
                         name="ul-throttle-proxy").start()
        return _proxy_port


def _accept_loop(srv):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


def _recv_exact(s, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        part = s.recv(n - len(buf))
        if not part:
            raise ConnectionError("closed")
        buf += part
    return buf


def _handle(conn):
    up = None
    try:
        conn.settimeout(15)
        ver, nmeth = _recv_exact(conn, 2)
        if ver != 5:
            return
        _recv_exact(conn, nmeth)
        conn.sendall(b"\x05\x00")                 # no authentication
        ver, cmd, _, atyp = _recv_exact(conn, 4)
        if atyp == 1:
            host = socket.inet_ntoa(_recv_exact(conn, 4))
        elif atyp == 3:
            host = _recv_exact(conn, _recv_exact(conn, 1)[0]).decode()
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, _recv_exact(conn, 16))
        else:
            return
        port = struct.unpack("!H", _recv_exact(conn, 2))[0]
        if cmd != 1:                              # CONNECT only
            conn.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
            return
        try:
            up = socket.create_connection((host, port), timeout=15)
        except OSError:
            conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
            return
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        conn.settimeout(None)
        up.settimeout(None)
        back = threading.Thread(target=_pump, args=(up, conn, None), daemon=True)
        back.start()
        _pump(conn, up, UPLOAD)                   # client → Telegram: throttled
        back.join()
    except (OSError, ValueError, ConnectionError):
        pass
    finally:
        for s in (conn, up):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def _pump(src, dst, limiter):
    """Copy src → dst. EOF is passed on as a half-close (the other direction
    keeps flowing); an error tears down both sockets so the peer pump ends."""
    try:
        while True:
            data = src.recv(16384)
            if not data:
                break
            if limiter is not None:
                limiter.consume(len(data))
            dst.sendall(data)
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
