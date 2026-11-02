"""
cb_guard.py — Chaturbate block protection (Settings → Chaturbate protection).

Chaturbate's video servers (mmcdn edges) start answering 403 to an IP that
hits them too hard — measured 2026-11-02: ~40 recording starts a minute plus
14 Player tiles retrying every 30 s got the home IP refused on IPv4 and IPv6;
a new IP played immediately. Every retry mints a fresh token and hits the
edge again, so blind retrying keeps the block alive. Two parts:

* Circuit breaker. The relay reports every *upstream* master-playlist fetch
  (report_master). When `trip_models` different models are refused within
  `trip_window` seconds, Chaturbate is paused: the relay refuses new master
  fetches without touching the edge, recordings aren't started, Player tiles
  wait. After a pause (first_pause, doubling up to max_pause) one probe
  fetches a single stream; success resumes, failure pauses longer. Optional
  fallback proxy: instead of pausing, route Chaturbate video through it while
  the direct route is blocked (probes still test direct).
* Launch throttle. Chaturbate recording starts go through a token bucket
  (`per_min` a minute). After the breaker closes the bucket always applies
  for RAMP_SECS — even with the throttle off — so 40 held recordings don't
  all restart in the same second and re-trip the block.

No dialogs; callers poll status() and get events through the on_event hook.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

RAMP_SECS = 300.0          # after resuming, starts are paced for this long
STEP_DECAY_SECS = 1800.0   # healthy this long → the next pause starts short again
PROBE_RETRY_SECS = 60.0    # probe couldn't test (no live room) → try again in

_clock = time.monotonic    # replaced by tests
_lock = threading.Lock()

# Settings (configure)
_enabled = True
_trip_models = 4
_trip_window = 60.0
_first_pause = 120.0       # seconds
_max_pause = 1800.0
_throttle = True
_per_min = 10.0
_fallback = ""             # proxy used while paused ("" = just pause)

# State
_refused: dict[str, float] = {}     # model → ts of its last upstream refusal
_fb_refused: dict[str, float] = {}  # same, through the fallback proxy
_open_until = 0.0          # >0 while tripped: when the next probe is due
_tripped_at = 0.0
_fallback_dead = False     # the fallback proxy got refused too → pause
_step = 0                  # pauses in a row (sets the pause length)
_closed_at = 0.0
_ramp_until = 0.0
_probing = False
_tokens = 0.0
_tokens_ts = 0.0
_held = 0                  # starts refused since the last summary line
_held_log_ts = 0.0
_last_event = ""
_probe_names: list = []    # models refused at the trip — probe candidates

_on_event = None           # fn(msg, kind) — app log / notification
_probe = None              # fn(candidate_names) -> True | False | None
_thread = None


def configure(enabled=True, trip_models=4, trip_window=60, first_pause_min=2,
              max_pause_min=30, throttle=True, per_min=10, fallback=""):
    """Install the settings (live). Turning the breaker off while paused
    resumes at once."""
    global _enabled, _trip_models, _trip_window, _first_pause, _max_pause
    global _throttle, _per_min, _fallback
    with _lock:
        _enabled = bool(enabled)
        _trip_models = max(2, int(trip_models or 4))
        _trip_window = max(10.0, float(trip_window or 60))
        _first_pause = max(30.0, float(first_pause_min or 2) * 60)
        _max_pause = max(_first_pause, float(max_pause_min or 30) * 60)
        _throttle = bool(throttle)
        _per_min = max(1.0, float(per_min or 10))
        _fallback = (fallback or "").strip()
        was_open = _open_until > 0
    if was_open and not _enabled:
        _close("protection turned off — Chaturbate resumed")
    _ensure_thread()


def apply_settings(s):
    """configure() from an AppSettings."""
    configure(s.cb_guard_enabled, s.cb_guard_trip_models, s.cb_guard_trip_window,
              s.cb_guard_first_pause_min, s.cb_guard_max_pause_min,
              s.cb_throttle_enabled, s.cb_throttle_per_min,
              s.cb_guard_fallback_proxy)


def set_hooks(on_event=None, probe=None):
    global _on_event, _probe
    _on_event = on_event
    _probe = probe


def _emit(msg, kind="warn"):
    global _last_event
    _last_event = msg
    logger.log(logging.INFO if kind in ("info", "accent") else logging.WARNING,
               "[CB guard] %s", msg)
    if _on_event:
        try:
            _on_event(msg, kind)
        except Exception:
            pass


# ── Detection (called by the relay) ──────────────────────────────────────────

def report_master(name: str, ok: bool):
    """One upstream Chaturbate master fetch: ok = 2xx, else a 403 the relay
    couldn't paper over with a cached copy. A success clears the window —
    a block refuses everything, so refusals mixed with successes are just
    spent tokens."""
    global _fallback_dead
    name = (name or "").lower()
    now = _clock()
    trip = dead = False
    with _lock:
        if not _enabled:
            return
        if _open_until > 0:
            # Tripped: only fetches through the fallback proxy get here.
            if ok:
                _fb_refused.clear()
            elif not _fallback_dead:
                _fb_refused[name] = now
                for k in [k for k, t in _fb_refused.items() if now - t > _trip_window]:
                    del _fb_refused[k]
                if len(_fb_refused) >= _trip_models:
                    _fallback_dead = dead = True
        elif ok:
            _refused.clear()
        else:
            _refused[name] = now
            for k in [k for k, t in _refused.items() if now - t > _trip_window]:
                del _refused[k]
            trip = len(_refused) >= _trip_models
    if trip:
        _trip()
    if dead:
        _emit("the fallback proxy is refused too — Chaturbate paused until "
              "the next check")


def _trip():
    global _open_until, _tripped_at, _step, _fallback_dead, _probe_names
    now = _clock()
    with _lock:
        if _open_until > 0:
            return
        if _step and now - _closed_at > STEP_DECAY_SECS:
            _step = 0
        _step += 1
        pause = min(_max_pause, _first_pause * 2 ** (_step - 1))
        _open_until = now + pause
        _tripped_at = now
        _fallback_dead = False
        names = sorted(_refused)
        _probe_names = names
        _fb_refused.clear()
        fb = _fallback
    how = (f"routing Chaturbate video through the fallback proxy; direct is "
           f"checked again in {_fmt(pause)}" if fb else
           f"Chaturbate paused for {_fmt(pause)}, then one stream is tested")
    _emit(f"⛔ Chaturbate is refusing this connection ({len(names)} models "
          f"refused: {', '.join(names[:6])}) — {how}.", "error")


def _close(msg):
    global _open_until, _closed_at, _ramp_until, _tokens, _tokens_ts
    global _fallback_dead
    now = _clock()
    with _lock:
        _open_until = 0.0
        _fallback_dead = False
        _closed_at = now
        _ramp_until = now + RAMP_SECS
        _tokens, _tokens_ts = 1.0, now
        _refused.clear()
        _fb_refused.clear()
    _emit(msg, "accent")


def resume_now():
    """Settings → Resume now: close without probing (ramp still applies)."""
    if _open_until > 0:
        _close("✅ Chaturbate resumed manually — recordings restart gradually")
    return status()


# ── Gates ────────────────────────────────────────────────────────────────────

def paused_for() -> float:
    """Seconds until the next check while Chaturbate traffic is held back
    (0 = not paused, or the fallback proxy is carrying it)."""
    with _lock:
        if _open_until <= 0 or (_fallback and not _fallback_dead):
            return 0.0
        return max(1.0, _open_until - _clock())


def fallback_proxy() -> str:
    """Proxy for Chaturbate video while tripped, else ''."""
    with _lock:
        if _open_until > 0 and _fallback and not _fallback_dead:
            return _fallback
    return ""


def admit(name: str = "") -> tuple[bool, float]:
    """May a Chaturbate recording start now? (ok, seconds to wait if not).
    Takes one launch token when it says yes."""
    global _tokens, _tokens_ts, _held, _held_log_ts
    now = _clock()
    msg = None
    with _lock:
        wait = 0.0
        if _open_until > 0 and not (_fallback and not _fallback_dead):
            wait = max(1.0, _open_until - now)
        elif _throttle or now < _ramp_until:
            rate = _per_min / 60.0
            cap = max(2.0, _per_min / 4)
            _tokens = min(cap, _tokens + (now - _tokens_ts) * rate)
            _tokens_ts = now
            if _tokens >= 1.0:
                _tokens -= 1.0
            else:
                wait = (1.0 - _tokens) / rate
        if wait:
            _held += 1
            if now - _held_log_ts >= 30:
                paused = _open_until > 0
                msg = (f"holding {_held} Chaturbate start(s) — "
                       + ("paused" if paused else "launch throttle")
                       + f", next in {_fmt(wait)}")
                _held, _held_log_ts = 0, now
    if msg:
        logger.info("[CB guard] %s", msg)
        if _on_event:
            try:
                _on_event(msg, "info")
            except Exception:
                pass
    return (not wait), wait


# ── Probe loop ───────────────────────────────────────────────────────────────

def _ensure_thread():
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=_loop, daemon=True, name="cb-guard")
        _thread.start()


def _loop():
    while True:
        time.sleep(5)
        try:
            tick()
        except Exception:
            logger.exception("[CB guard] tick failed")


def tick():
    """Run a probe when the pause is over (one at a time, in this thread)."""
    global _probing, _open_until, _step
    with _lock:
        due = _open_until > 0 and _clock() >= _open_until and not _probing
        if due:
            _probing = True
        names = list(_probe_names)
    if not due:
        return
    try:
        res = _probe(names) if _probe else None
    except Exception:
        logger.exception("[CB guard] probe failed")
        res = None
    finally:
        with _lock:
            _probing = False
    if res is True:
        _close("✅ Chaturbate accepts this connection again — recordings "
               "restart gradually")
        return
    with _lock:
        if _open_until <= 0:
            return                      # resumed manually meanwhile
        if res is None:
            _open_until = _clock() + PROBE_RETRY_SECS
            pause = PROBE_RETRY_SECS
        else:
            _step += 1
            pause = min(_max_pause, _first_pause * 2 ** (_step - 1))
            _open_until = _clock() + pause
    if res is None:
        _emit(f"couldn't test Chaturbate (no live room answered) — trying "
              f"again in {_fmt(pause)}", "info")
    else:
        _emit(f"Chaturbate still refuses this connection — next check in "
              f"{_fmt(pause)}")


# ── Status (Settings card) ───────────────────────────────────────────────────

def status() -> dict:
    now = _clock()
    with _lock:
        tripped = _open_until > 0
        fb = bool(tripped and _fallback and not _fallback_dead)
        if not _enabled:
            text = "Off — Chaturbate is never paused automatically."
        elif tripped and fb:
            text = (f"Blocked — using the fallback proxy. Direct is checked "
                    f"again in {_fmt(max(0, _open_until - now))}.")
        elif tripped:
            text = (f"Paused — Chaturbate refused this connection. Next check "
                    f"in {_fmt(max(0, _open_until - now))}"
                    + (" (checking now…)" if _probing else "") + ".")
        elif now < _ramp_until:
            text = "OK — resuming gradually after a pause."
        else:
            text = "OK — watching for refusals."
        return {"enabled": _enabled, "tripped": tripped, "fallback": fb,
                "step": _step, "text": text, "last": _last_event}


def _fmt(secs: float) -> str:
    secs = int(round(secs))
    if secs < 90:
        return f"{secs}s"
    return f"{round(secs / 60)}m"
