"""
pipeline_worker.py — GUI-friendly refactor of Pipeline/pipeline.py.

Watches a folder for .ts files produced by the recorder, converts/splits them
into .mp4 with ffmpeg, then uploads each .mp4 to a Telegram chat/topic via TDLib.

Designed to run as a background thread from the Tkinter app:
  w = PipelineWorker(cfg, on_log=..., on_state=..., prompt_cb=...)
  w.start()       # non-blocking
  w.stop()        # graceful
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional


TELEGRAM_MAX_BYTES = 3.8 * 1e9   # Telegram's hard per-file upload cap


@dataclass
class PipelineConfig:
    api_id: int = 0
    api_hash: str = ""
    phone: str = ""
    chat_id: int = 0
    topic_id: int = 0
    watch_folder: str = ""          # where .ts files are written
    output_folder: str = ""         # where converted .mp4 files live before upload
    tdlib_dir: str = ""             # holds clientN/ subfolders (tdlib db)
    uploaded_log: str = ""          # text log of already-uploaded basenames
    do_convert: bool = True         # stage 1: convert .ts → .mp4
    do_upload: bool = True          # stage 2: upload .mp4 to Telegram
    upload_workers: int = 2
    # Caller should set this from the user's "Max File Size" setting,
    # clamped to TELEGRAM_MAX_BYTES when do_upload is set — see
    # _build_pipeline_config() in app_web.py / app.py. This default only
    # applies if a caller forgets to set it explicitly.
    max_bytes: float = TELEGRAM_MAX_BYTES
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"


class PipelineWorker:
    """Runs the convert+upload pipeline in a background thread.

    Callbacks (all optional, invoked from worker thread — marshal to GUI yourself):
      on_log(line: str)               — human-readable log line
      on_state(state: str)            — lifecycle: 'starting','running','stopping','stopped','error'
      on_progress(kind, name, pct, speed_bps) — pct 0..100; speed in bytes/sec
      prompt_cb(label) -> str         — block until user enters OTP/2FA; runs on worker thread
    """

    def __init__(
        self,
        cfg: PipelineConfig,
        on_log: Optional[Callable[[str], None]] = None,
        on_state: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[str, str, float], None]] = None,
        prompt_cb: Optional[Callable[[str], str]] = None,
    ):
        self.cfg = cfg
        self.on_log = on_log or (lambda s: None)
        self.on_state = on_state or (lambda s: None)
        self.on_progress = on_progress or (lambda k, n, p, s=0.0: None)
        self.prompt_cb = prompt_cb or (lambda label: "")

        self._shutdown = False
        self._ul_done = 0            # bytes of fully-uploaded files
        self._ul_inflight: dict = {} # upload slot → bytes sent of current file
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._clients: list = []
        self._has_uploaders = False   # True once a TDLib client has connected

    # ── Public control ───────────────────────────────────────────────────────

    def bytes_uploaded(self) -> int:
        """Total bytes sent to Telegram (completed files + in-flight progress)."""
        return self._ul_done + sum(self._ul_inflight.values())

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return
        self._shutdown = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._shutdown = True
        self.on_state("stopping")
        # The async loop checks _shutdown on every iteration.

    # ── Thread entrypoint ────────────────────────────────────────────────────

    def _run(self):
        self.on_state("starting")
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._main())
        except Exception as e:
            self.on_log(f"[pipeline] fatal: {e}")
            self.on_state("error")
            return
        finally:
            try:
                if self._loop:
                    self._loop.close()
            except Exception:
                pass
        self.on_state("stopped")

    # ── Core async pipeline ──────────────────────────────────────────────────

    async def _main(self):
        cfg = self.cfg
        for d in (cfg.watch_folder, cfg.output_folder, cfg.tdlib_dir):
            if d:
                os.makedirs(d, exist_ok=True)

        uploaded = self._load_uploaded()
        processed: set = set()      # .ts paths already converted
        queued: set = set()         # .mp4 basenames already handed to the uploaders
        upload_queue: asyncio.Queue = asyncio.Queue()
        self._clients = []
        self._has_uploaders = False  # set True once a TDLib client connects

        # The pipeline always starts and sits in stand-by. Nothing happens until
        # a stage is checked. A single coordinator converts .ts → .mp4 when the
        # Convert stage is on and feeds .mp4s to the upload queue when the Upload
        # stage is on; N uploader workers lazily connect TDLib the first time
        # Upload is enabled (reusing the cached session, so normally no re-login).
        # All of it is driven by the live cfg flags, so checking/unchecking a box
        # takes effect immediately and a stage stops after its current task.
        self.on_state("running")
        self.on_log("[pipeline] running (stand by) — toggle Convert / Upload any "
                    "time; changes apply immediately.")

        loop = asyncio.get_running_loop()
        # Convert and upload run as fully independent coroutines: the converter
        # only produces .mp4s, a separate feeder queues any un-uploaded .mp4 the
        # instant it appears (converted now, converted earlier, or already on
        # disk from a previous run), and the uploaders drain that queue
        # concurrently. Uploads therefore never wait for the conversion batch to
        # finish — they start as soon as a .mp4 is available.
        tasks = [
            loop.create_task(self._converter_worker(loop, processed)),
            loop.create_task(self._feeder_worker(upload_queue, queued, uploaded)),
        ]
        for i in range(cfg.upload_workers):
            tasks.append(loop.create_task(
                self._uploader_worker(i + 1, upload_queue, queued, uploaded)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for td in self._clients:
                try:
                    await td.stop()
                except Exception:
                    pass

    async def _connect_client(self, slot: int):
        """Lazily connect a TDLib client for `slot`. Returns the client, or None
        on failure (missing bindings/credentials/auth). Sets _has_uploaders."""
        try:
            import tdjson  # type: ignore
        except Exception as e:
            self.on_log(f"[upload #{slot}] tdjson import failed: {e}. "
                        f"Install TDLib python bindings to enable uploads.")
            return None
        cfg = self.cfg
        missing = [n for n, v in (("API ID", cfg.api_id), ("API Hash", cfg.api_hash),
                                   ("Chat ID", cfg.chat_id)) if not v]
        if missing:
            self.on_log(f"[upload #{slot}] missing Telegram settings: "
                        f"{', '.join(missing)}. Fill them in, Save, then re-enable Upload.")
            return None
        _start_global_recv_once(tdjson)
        db = os.path.join(cfg.tdlib_dir, f"client{slot}")
        try:
            os.makedirs(db, exist_ok=True)
        except Exception:
            pass
        td = _TDLibClient(tdjson, cfg, db_path=db,
                          on_log=self.on_log, prompt_cb=self.prompt_cb)
        self.on_log(f"[pipeline] Connecting Telegram client {slot}…")
        try:
            await td.start()
        except Exception as e:
            self.on_log(f"[upload #{slot}] connect/auth failed: {e}")
            return None
        self._clients.append(td)
        self._has_uploaders = True
        return td

    # ── Workers ──────────────────────────────────────────────────────────────

    async def _converter_worker(self, loop, processed: set):
        """Stage ①: convert .ts → .mp4 while the Convert stage is on. Produces
        files only — queuing for upload is the feeder's job — so a long
        conversion batch never blocks uploads. Gated on the live cfg flag, so
        toggling Convert takes effect on the next scanned file."""
        attempts: dict = {}   # ts path → failed attempts this run
        while True:
            if self._shutdown:
                return

            if self.cfg.do_convert:
                try:
                    ts_files = [
                        f for f in os.listdir(self.cfg.watch_folder)
                        if f.endswith(".ts")
                        and os.path.isfile(os.path.join(self.cfg.watch_folder, f))
                    ]
                except Exception as e:
                    self.on_log(f"[convert] scan error: {e}")
                    ts_files = []

                for f in ts_files:
                    if self._shutdown or not self.cfg.do_convert:
                        break
                    path = os.path.join(self.cfg.watch_folder, f)
                    if path in processed or not _is_free(path):
                        continue
                    processed.add(path)
                    self.on_log(f"[convert] → {f}")
                    try:
                        outputs = await loop.run_in_executor(
                            None, lambda p=path: self._convert_and_split(p)
                        )
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        self.on_progress("convert", "", 100.0, 0.0)   # → idle
                        valid = [o for o in outputs if os.path.isfile(o)]
                        if len(valid) != len(outputs):
                            self.on_log(f"[convert] ⚠ {f}: {len(outputs)-len(valid)} part(s) missing (ffmpeg error)")
                        self.on_log(f"[convert] ✓ {f} → {len(valid)} part(s)")
                        attempts.pop(path, None)
                        # The .mp4s are on disk now; the feeder queues them for
                        # upload independently (convert-only just keeps them).
                    except Exception as e:
                        # Same 3-attempt cap as uploads: a permanently broken
                        # .ts must not re-convert (and burn CPU) forever on
                        # every scan pass.
                        n = attempts.get(path, 0) + 1
                        attempts[path] = n
                        if n < 3:
                            self.on_log(f"[convert] ✗ {f}: {e} — will retry "
                                        f"(attempt {n}/3)")
                            processed.discard(path)
                        else:
                            self.on_log(f"[convert] ✗ {f}: {e} — giving up "
                                        f"after 3 attempts; will retry when "
                                        f"the pipeline is restarted.")

            for _ in range(50):
                if self._shutdown:
                    break
                await asyncio.sleep(0.1)

    async def _feeder_worker(self, upload_queue: asyncio.Queue,
                             queued: set, uploaded: set):
        """Stage ② feed: independently of conversion, queue every un-uploaded
        .mp4 for the uploaders as soon as it appears. Runs whenever Upload is on
        and a client has connected; dedups via `queued`. Because it's its own
        coroutine, files already converted (or left from a previous run) upload
        immediately instead of waiting for the current convert batch."""
        while True:
            if self._shutdown:
                return
            if self.cfg.do_upload and self._has_uploaders:
                try:
                    for f in sorted(os.listdir(self.cfg.output_folder)):
                        if (f.endswith(".mp4") and f not in uploaded
                                and f not in queued):
                            path = os.path.join(self.cfg.output_folder, f)
                            # Skip files ffmpeg is still writing — uploading a
                            # half-converted .mp4 sends a corrupt video.
                            if not _is_free(path):
                                continue
                            queued.add(f)
                            await upload_queue.put(path)
                except Exception as e:
                    self.on_log(f"[upload] scan error: {e}")
            # Scan a bit faster than the converter's cycle so freshly produced
            # .mp4s are picked up promptly.
            for _ in range(10):
                if self._shutdown:
                    break
                await asyncio.sleep(0.1)

    async def _uploader_worker(self, slot: int, upload_queue: asyncio.Queue,
                                queued: set, uploaded: set):
        td = None
        connect_failed = False
        prev_upload = False
        attempts: dict = {}   # basename -> failed attempts this run
        while True:
            # Stop pulling new files the moment the pipeline is stopping. An
            # in-flight send_video already completed this iteration (finish
            # current, drop the rest) — without this top-of-loop check the
            # worker would drain the whole backlog before noticing _shutdown.
            if self._shutdown:
                return
            du = self.cfg.do_upload
            if du and not prev_upload:
                connect_failed = False     # re-enabled → allow a fresh attempt
            prev_upload = du
            if not du:
                await asyncio.sleep(0.5)        # Upload stage off — idle
                continue
            # Lazily connect this worker's TDLib client the first time Upload is
            # active. On failure, idle until the user toggles Upload off→on again.
            if td is None:
                if connect_failed:
                    await asyncio.sleep(0.5)
                    continue
                td = await self._connect_client(slot)
                if td is None:
                    connect_failed = True
                    continue
            try:
                path = await asyncio.wait_for(upload_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if self._shutdown:
                    return
                continue
            if path is None:
                upload_queue.task_done()
                continue

            name = os.path.basename(path)
            if not os.path.isfile(path):
                self.on_log(f"[upload #{slot}] ✗ {name}: file not found (conversion failed)")
                upload_queue.task_done()
                continue
            file_size = max(1, os.path.getsize(path))

            kind = f"upload{slot}"
            _speed_state = {"last_sent": 0, "last_ts": 0.0, "speed": 0.0}

            def _cb(sent, _k=kind, _n=name, _fs=file_size, _st=_speed_state):
                import time as _time
                now = _time.monotonic()
                dt = now - _st["last_ts"]
                if dt >= 0.5:          # recalculate every 0.5 s
                    _st["speed"] = max(0.0, (sent - _st["last_sent"]) / dt)
                    _st["last_sent"] = sent
                    _st["last_ts"] = now
                pct = min(100.0, sent * 100.0 / _fs)
                self._ul_inflight[_k] = sent
                self.on_progress(_k, _n, pct, _st["speed"])

            caption = _extract_model(name)
            try:
                await td.send_video(path, caption=caption, progress_cb=_cb)
                self._ul_done += file_size
                self._mark_uploaded(name)
                await self._discard_uploaded(slot, path, name)
                uploaded.add(name)
                attempts.pop(name, None)
                self.on_progress(kind, "", 100.0, 0.0)   # empty name → "idle"
                self.on_log(f"[upload #{slot}] ✓ {name}")
            except Exception as e:
                n = attempts.get(name, 0) + 1
                attempts[name] = n
                if n < 3:
                    self.on_log(f"[upload #{slot}] ✗ {name}: {e} — "
                                f"retrying (attempt {n}/3)")
                    await asyncio.sleep(5)
                    queued.discard(name)   # feeder re-queues on its next scan
                else:
                    self.on_log(f"[upload #{slot}] ✗ {name}: {e} — giving up "
                                f"after 3 attempts; will retry when the "
                                f"pipeline is restarted.")
                self.on_progress(kind, "", 100.0, 0.0)
            finally:
                self._ul_inflight[kind] = 0
                upload_queue.task_done()

    # ── Conversion ───────────────────────────────────────────────────────────

    def _convert_and_split(self, ts_path: str) -> list:
        size = os.path.getsize(ts_path)
        base = os.path.splitext(os.path.basename(ts_path))[0]
        duration = _get_duration(self.cfg.ffprobe_path, ts_path)
        outputs: list = []

        if size > self.cfg.max_bytes and duration > 0:
            # Target 95% of the limit: part_dur is derived from the file's
            # *average* bitrate, so a variable-bitrate stream can overshoot
            # a target computed at exactly max_bytes.
            num_parts = max(1, math.ceil(size / (self.cfg.max_bytes * 0.95)))
            part_dur = duration / num_parts
            for i in range(num_parts):
                start = i * part_dur
                out = os.path.join(self.cfg.output_folder,
                                   f"{base}_part{i+1:03d}.mp4")
                # -ss BEFORE -i = input seeking: ffmpeg jumps straight to the
                # nearest keyframe instead of demux-discarding everything up
                # to the cut point. With -ss after -i, part N re-read the file
                # from byte 0 — splitting a big recording was several times
                # slower for no accuracy gain (stream copy snaps to keyframes
                # either way).
                cmd = [self.cfg.ffmpeg_path,
                       "-ss", str(start),
                       "-i", ts_path,
                       *(["-t", str(part_dur)] if i < num_parts - 1 else []),
                       "-c", "copy", out, "-y"]
                self._run_ffmpeg(cmd, part_dur, f"{base} ({i+1}/{num_parts})")
                out_size = os.path.getsize(out) if os.path.exists(out) else 0
                if out_size > self.cfg.max_bytes:
                    self.on_log(f"⚠ {os.path.basename(out)} is "
                                f"{out_size / 1e6:.0f}MB, over the "
                                f"{self.cfg.max_bytes / 1e6:.0f}MB limit "
                                f"(variable bitrate) — not re-split.")
                outputs.append(out)
        else:
            out = os.path.join(self.cfg.output_folder, f"{base}.mp4")
            cmd = [self.cfg.ffmpeg_path, "-i", ts_path, "-c", "copy", out, "-y"]
            self._run_ffmpeg(cmd, duration or 1.0, base)
            outputs.append(out)

        return outputs

    def _run_ffmpeg(self, cmd: list, total_secs: float, label: str):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(
            cmd + ["-loglevel", "error", "-progress", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            creationflags=flags,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            m = re.search(r"out_time_ms=(\d+)", line)
            if m:
                done = int(m.group(1)) / 1e6
                pct = min(100.0, done * 100.0 / max(total_secs, 0.1))
                self.on_progress("convert", label, pct)
        proc.wait()
        self.on_progress("convert", label, 100.0)

    # ── Persistence helpers ──────────────────────────────────────────────────

    async def _discard_uploaded(self, slot: int, path: str, name: str):
        """Delete a file we just sent, retrying while TDLib still holds it.

        TDLib releases the local file handle a moment *after* it reports the
        send as succeeded, so an immediate os.remove() loses a race on Windows
        and the .mp4 lingers for ever: it is already in uploaded.txt, so the
        feeder skips it and the user only sees a growing folder. Retry with
        backoff, and if it still will not go, move it out of the feeder's scan
        path into `stuck/` so it is visible instead of silently orphaned.
        """
        last = None
        for attempt in range(5):
            try:
                os.remove(path)
                return
            except Exception as e:
                last = e
                await asyncio.sleep(0.5 * (2 ** attempt))   # 0.5,1,2,4,8 s
        stuck_dir = os.path.join(self.cfg.output_folder, "stuck")
        try:
            os.makedirs(stuck_dir, exist_ok=True)
            os.replace(path, os.path.join(stuck_dir, name))
            self.on_log(f"[upload #{slot}] ⚠ uploaded {name} but couldn't "
                        f"delete it ({last}) — moved to stuck\\")
        except Exception as e:
            self.on_log(f"[upload #{slot}] ⚠ uploaded {name} but couldn't "
                        f"delete ({last}) or move it ({e}) — it will stay "
                        f"in the converted folder and be skipped.")

    def _load_uploaded(self) -> set:
        p = self.cfg.uploaded_log
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return set(l.strip() for l in f if l.strip())
            except Exception:
                return set()
        return set()

    def _mark_uploaded(self, name: str):
        p = self.cfg.uploaded_log
        if not p:
            return
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(name + "\n")
        except Exception:
            pass


# ── Module-level helpers (shared by all clients) ─────────────────────────────

_GLOBAL_RECV_LOCK = threading.Lock()
_GLOBAL_RECV_STARTED = False
_GLOBAL_CLIENTS: dict = {}
_GLOBAL_CLIENTS_LOCK = threading.Lock()


def _start_global_recv_once(tdjson) -> bool:
    """Start the single TDLib receive thread that fans out updates to clients."""
    global _GLOBAL_RECV_STARTED
    with _GLOBAL_RECV_LOCK:
        if _GLOBAL_RECV_STARTED:
            return False
        _GLOBAL_RECV_STARTED = True

    def _loop():
        while True:
            try:
                raw = tdjson.td_receive(0.1)
                if not raw:
                    continue
                upd = json.loads(raw)
                cid = upd.get("@client_id")
                with _GLOBAL_CLIENTS_LOCK:
                    client = _GLOBAL_CLIENTS.get(cid)
                if client and client._loop:
                    asyncio.run_coroutine_threadsafe(
                        client._dispatch(upd), client._loop
                    )
            except Exception:
                # Never let the global receiver die silently
                continue

    threading.Thread(target=_loop, daemon=True).start()
    return True


# Recording filenames tag Stripchat as "ST"; captions use "SC" per request.
_CAPTION_SITE = {"CB": "CB", "ST": "SC", "CS": "CS", "MFC": "MFC"}


def _extract_model(filename: str) -> str:
    base = re.sub(r'[_-]part\d+$', '',
                   os.path.splitext(filename)[0], flags=re.IGNORECASE)
    site = ""
    m = re.match(r'^(.+?)_(CB|ST|CS|MFC)_\d{8}_\d{6}', base, re.IGNORECASE)
    if m:
        site = _CAPTION_SITE.get(m.group(2).upper(), "")
    else:
        m = re.match(r'^(.+?)-\d{4}_', base)
    name = (m.group(1) if m else base).strip('_')
    tag = re.sub(r'[^a-zA-Z0-9_]', '', name)
    caption = f"#{tag}"
    if site:
        caption += f" #{site}"
    return caption


def _is_free(path: str) -> bool:
    try:
        os.rename(path, path)
        return True
    except Exception:
        return False


def _get_duration(ffprobe: str, path: str) -> float:
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ── TDLib client wrapper ─────────────────────────────────────────────────────

class _TDLibClient:
    def __init__(self, tdjson, cfg: PipelineConfig, db_path: str,
                 on_log: Callable[[str], None],
                 prompt_cb: Callable[[str], str]):
        self._tdjson   = tdjson
        self._cfg      = cfg
        self._db_path  = db_path
        self._on_log   = on_log
        self._prompt   = prompt_cb
        self._cid      = tdjson.td_create_client_id()
        with _GLOBAL_CLIENTS_LOCK:
            _GLOBAL_CLIENTS[self._cid] = self
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._futures: dict = {}
        self._file_evts: dict = {}
        self._send_evts: dict = {}
        self._send_errors: dict = {}   # old_message_id -> "code: message" on send failure
        self._lock = threading.Lock()
        self._counter = 0
        self._ready: Optional[asyncio.Event] = None

    def _next_extra(self) -> str:
        with self._lock:
            self._counter += 1
            return str(self._counter)

    def _raw_send(self, data: dict):
        self._tdjson.td_send(self._cid, json.dumps(data).encode())

    async def _invoke(self, data: dict):
        extra = self._next_extra()
        data["@extra"] = extra
        assert self._loop
        fut = self._loop.create_future()
        with self._lock:
            self._futures[extra] = fut
        self._raw_send(data)
        return await fut

    async def _dispatch(self, upd: dict):
        if not isinstance(upd, dict):
            return
        t = upd.get("@type", "")
        extra = upd.get("@extra")
        if extra:
            with self._lock:
                fut = self._futures.pop(extra, None)
            if fut and not fut.done():
                fut.set_result(upd)
                return

        if t == "error":
            # Errors without @extra (e.g. failed auth requests) were silently
            # dropped, leaving the pipeline stuck at "Connecting" — surface them.
            msg = upd.get("message", "")
            self._on_log(f"[tdlib] error {upd.get('code')}: {msg}")
            # Wrong auth input leaves TDLib waiting in the same state without
            # re-emitting it — re-prompt the user so login can be retried.
            retry_state = {
                "PHONE_CODE_INVALID":   "authorizationStateWaitCode",
                "PHONE_CODE_EXPIRED":   "authorizationStateWaitCode",
                "PASSWORD_HASH_INVALID": "authorizationStateWaitPassword",
                "PHONE_NUMBER_INVALID": "authorizationStateWaitPhoneNumber",
            }.get(msg)
            if retry_state:
                if retry_state == "authorizationStateWaitPhoneNumber":
                    self._cfg.phone = ""  # force a fresh prompt
                await self._on_auth({"@type": retry_state})
        elif t == "updateAuthorizationState":
            await self._on_auth(upd["authorization_state"])
        elif t == "updateFile":
            f = upd["file"]
            fid = f["id"]
            remote = f.get("remote", {})
            done = remote.get("is_uploading_completed", False)
            sent = remote.get("uploaded_size", 0)
            with self._lock:
                entry = self._file_evts.get(fid)
            if entry:
                ev, cb = entry
                if cb:
                    try:
                        cb(sent)
                    except Exception:
                        pass
                if done:
                    ev.set()
        elif t == "updateMessageSendSucceeded":
            old = upd.get("old_message_id")
            with self._lock:
                ev = self._send_evts.pop(old, None)
            if ev:
                ev.set()
        elif t == "updateMessageSendFailed":
            # A failed send MUST surface as an error — treating it like
            # success once marked hundreds of never-sent files as uploaded,
            # permanently skipping them (they're deduped by basename).
            old = upd.get("old_message_id")
            err = upd.get("error") or {}
            code = err.get("code", upd.get("error_code", "?"))
            msg = err.get("message", upd.get("error_message", "send failed"))
            with self._lock:
                ev = self._send_evts.pop(old, None)
                self._send_errors[old] = f"{code}: {msg}"
            if ev:
                ev.set()

    async def _on_auth(self, state: dict):
        t = state["@type"]
        if t == "authorizationStateWaitTdlibParameters":
            self._raw_send({
                "@type":                 "setTdlibParameters",
                "api_id":                self._cfg.api_id,
                "api_hash":              self._cfg.api_hash,
                "phone_number":          self._cfg.phone,
                "database_directory":    self._db_path,
                "files_directory":       self._db_path + "/files",
                "use_message_database":  True,
                "use_secret_chats":      False,
                "system_language_code":  "en",
                "device_model":          "Desktop",
                "system_version":        "Windows",
                "application_version":   "1.0",
            })
        elif t == "authorizationStateWaitPhoneNumber":
            phone = self._cfg.phone
            if not phone:
                # Fresh login (no cached session) — ask the user.
                assert self._loop
                phone = await self._loop.run_in_executor(
                    None,
                    lambda: self._prompt("Enter Telegram phone number (intl. format, e.g. +1555...):"),
                )
                if not phone:
                    self._on_log("[tdlib] no phone number provided — login aborted.")
                    return
                self._cfg.phone = phone
            self._raw_send({
                "@type":        "setAuthenticationPhoneNumber",
                "phone_number": phone,
            })
        elif t == "authorizationStateWaitCode":
            assert self._loop
            code = await self._loop.run_in_executor(
                None, lambda: self._prompt("Enter Telegram login code:")
            )
            self._raw_send({"@type": "checkAuthenticationCode", "code": code})
        elif t == "authorizationStateWaitPassword":
            assert self._loop
            pwd = await self._loop.run_in_executor(
                None, lambda: self._prompt("Enter 2FA password:")
            )
            self._raw_send({"@type": "checkAuthenticationPassword", "password": pwd})
        elif t == "authorizationStateReady":
            self._on_log("[tdlib] authorized.")
            if self._ready:
                self._ready.set()
        elif t == "authorizationStateClosed":
            pass

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._ready = asyncio.Event()
        self._tdjson.td_execute(json.dumps(
            {"@type": "setLogVerbosityLevel", "new_verbosity_level": 0}
        ).encode())
        self._raw_send({"@type": "getAuthorizationState"})
        await self._ready.wait()

    async def stop(self):
        self._raw_send({"@type": "close"})

    async def send_video(self, file_path: str, caption: str = "",
                          progress_cb=None):
        abs_path = os.path.abspath(file_path).replace("\\", "/")
        file_size = os.path.getsize(file_path)

        result = await self._invoke({
            "@type":             "sendMessage",
            "chat_id":           self._cfg.chat_id,
            "message_thread_id": self._cfg.topic_id,
            "input_message_content": {
                "@type":  "inputMessageVideo",
                "video":  {"@type": "inputFileLocal", "path": abs_path},
                "caption": {"@type": "formattedText",
                             "text": caption, "entities": []},
                "supports_streaming": True,
                "duration": 0, "width": 0, "height": 0,
            },
        })
        if result.get("@type") == "error":
            raise Exception(f"TDLib: {result.get('message')}")

        temp_msg_id = result.get("id")
        file_id = (result.get("content", {})
                         .get("video", {})
                         .get("video", {})
                         .get("id"))

        send_ev = asyncio.Event()
        with self._lock:
            self._send_evts[temp_msg_id] = send_ev
        if file_id:
            file_ev = asyncio.Event()
            with self._lock:
                self._file_evts[file_id] = (file_ev, progress_cb)

        try:
            await asyncio.wait_for(send_ev.wait(), timeout=7200)
        except asyncio.TimeoutError:
            raise Exception("Upload timed out")
        finally:
            if file_id:
                with self._lock:
                    self._file_evts.pop(file_id, None)

        with self._lock:
            err = self._send_errors.pop(temp_msg_id, None)
        if err:
            raise Exception(f"TDLib: {err}")

        if progress_cb:
            try:
                progress_cb(file_size)
            except Exception:
                pass
