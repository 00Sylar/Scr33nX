"""On-demand performance snapshot: where does this process spend CPU?

Settings → Performance snapshot calls capture(). For `seconds` it samples every
Python thread's current frame (sys._current_frames) and reads each thread's
real CPU time from Windows (GetThreadTimes), then reports CPU per thread
group (relay request handlers, prefetch workers, UI bridge, …) with the
functions those threads were most often in. Nothing leaves the machine.
"""

import collections
import ctypes
import os
import re
import sys
import threading
import time
from ctypes import wintypes

_THREAD_QUERY_LIMITED_INFORMATION = 0x0800
_IDLE_FUNCS = {
    "wait", "select", "poll", "accept", "sleep", "recv", "recv_into", "read",
    "readinto", "_wait_for_tstate_lock", "get", "_recv", "_worker",
    "serve_forever", "_poll", "acquire", "join",
}


def _thread_cpu_seconds(native_id):
    """Kernel + user CPU seconds of one OS thread, or None."""
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenThread(_THREAD_QUERY_LIMITED_INFORMATION, False, int(native_id))
        if not h:
            return None
        try:
            c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
            if not k32.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e),
                                      ctypes.byref(kt), ctypes.byref(ut)):
                return None
            def secs(ft):
                return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7
            return secs(kt) + secs(ut)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _group(name: str) -> str:
    """Collapse numbered worker names into one group."""
    m = re.search(r"\(([^)]+)\)", name)
    if m:
        return m.group(1)
    return re.sub(r"[-_]?\d+$", "", name) or name


def capture(seconds: float = 10.0, interval: float = 0.02) -> str:
    seconds = max(2.0, min(60.0, float(seconds)))
    me = threading.get_ident()
    # Sampling: ident -> Counter of leaf frames; plus idle-sample counts.
    leaves = collections.defaultdict(collections.Counter)
    idle = collections.Counter()
    seen = collections.Counter()

    def cpu_map():
        out = {}
        for t in threading.enumerate():
            nid = getattr(t, "native_id", None)
            if nid is None:
                continue
            cpu = _thread_cpu_seconds(nid)
            if cpu is not None:
                out[t.ident] = (t.name, cpu)
        return out

    try:
        import cb_relay
        b0 = cb_relay.bytes_downloaded()
    except Exception:
        cb_relay, b0 = None, 0
    p0, w0 = time.process_time(), time.monotonic()
    start = cpu_map()
    end_at = w0 + seconds
    while time.monotonic() < end_at:
        for ident, frame in sys._current_frames().items():
            if ident == me:
                continue
            code = frame.f_code
            fn = os.path.basename(code.co_filename)
            seen[ident] += 1
            if code.co_name in _IDLE_FUNCS:
                idle[ident] += 1
            else:
                leaves[ident][f"{fn}:{code.co_name}:{frame.f_lineno}"] += 1
        time.sleep(interval)
    wall = time.monotonic() - w0
    proc_cpu = time.process_time() - p0
    end = cpu_map()

    groups = {}
    for ident, (name, cpu1) in end.items():
        cpu0 = start.get(ident, (name, 0.0))[1]
        g = groups.setdefault(_group(name), {"n": 0, "cpu": 0.0,
                                             "leaves": collections.Counter(),
                                             "seen": 0, "idle": 0})
        g["n"] += 1
        g["cpu"] += max(0.0, cpu1 - cpu0)
        g["leaves"].update(leaves.get(ident, {}))
        g["seen"] += seen.get(ident, 0)
        g["idle"] += idle.get(ident, 0)

    ncpu = os.cpu_count() or 1
    lines = [
        f"Scr33nX performance snapshot — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"window: {wall:.1f} s   python threads: {len(end)}   logical cores: {ncpu}",
        f"process CPU: {proc_cpu / wall * 100:.0f}% of one core "
        f"= {proc_cpu / wall / ncpu * 100:.1f}% of the machine",
        "(Python bytecode runs on one core at a time (GIL); a process near "
        "100% of one core means the app itself is the bottleneck.)",
    ]
    if cb_relay is not None:
        try:
            with cb_relay._state_lock:
                entries, cbytes = len(cb_relay._cache), cb_relay._cache_bytes
            mbps = (cb_relay.bytes_downloaded() - b0) * 8 / wall / 1e6
            lines += [
                f"relay: downloaded {mbps:.1f} Mbps avg   cache {entries} segments / "
                f"{cbytes / 1048576:.0f} MB of {cb_relay._CACHE_MAX_BYTES // 1048576} MB   "
                f"tiles active: {cb_relay._tiles_active()}",
                f"relay queues: recording prefetch {cb_relay._executor._work_queue.qsize()} "
                f"waiting, tile prefetch {cb_relay._tile_executor._work_queue.qsize()} waiting",
            ]
        except Exception as e:  # diagnostics must never raise
            lines.append(f"relay stats unavailable: {e}")
    lines += ["", f"{'thread group':<34}{'thr':>4}{'cpu s':>8}{'%core':>7}{'idle':>6}   top functions (non-idle)"]
    for name, g in sorted(groups.items(), key=lambda kv: -kv[1]["cpu"]):
        if g["cpu"] < 0.02 and g["n"] < 3:
            continue
        idle_pct = (g["idle"] / g["seen"] * 100) if g["seen"] else 0
        top = ", ".join(f"{f} ×{c}" for f, c in g["leaves"].most_common(3)) or "—"
        lines.append(f"{name[:33]:<34}{g['n']:>4}{g['cpu']:>8.2f}"
                     f"{g['cpu'] / wall * 100:>6.0f}%{idle_pct:>5.0f}%   {top}")
    return "\n".join(lines)
