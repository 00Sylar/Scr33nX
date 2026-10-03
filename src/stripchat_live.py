"""
Stripchat live recorder — launched as a subprocess by recorder.py.

Behaves like a Popen ffmpeg process:
  - Writes a live-growing MPEG-TS file at <output_path>.
  - Reads stdin; when stdin is closed (or receives 'q\\n'), shuts down
    cleanly, finalizing the .ts trailer.

Usage:  python stripchat_live.py <model_name> <output_path>

Strategy:
  Headless Chromium plays the stream (handles MOUFLON DRM internally).
  We intercept segment responses, buffer by sequence number to preserve
  order under LL-HLS parallel part fetching, and pipe ordered fMP4 bytes
  into a child ffmpeg that transmuxes to MPEG-TS on the fly.
"""
from __future__ import annotations
import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from playwright.async_api import async_playwright

# Exit codes
EXIT_OK             = 0
EXIT_USAGE          = 1
EXIT_LAUNCH_ERR     = 2
EXIT_NAV_ERR        = 3
EXIT_IDLE_TIMEOUT   = 4   # page loaded but no public segments in time
EXIT_PRIVATE_SHOW   = 5   # ticket/private/group show detected

IDLE_TIMEOUT_SECS   = 45  # kill recording if no segment arrives for this long

PRIVATE_MARKERS = (
    "invites you to Ticket show",
    "Ticket show",
    "Get Ticket",
    "Private show",
    "invites you to Private",
    "Group show",
    "invites you to Group",
    "Spy show",
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")

SEG_RE = re.compile(r"/b-hls-\d+/\d+/\d+_.*\.mp4($|\?)")
# Grab the sequence number (last large decimal token before .mp4) and optional
# part index. Works for both legacy format "..._<id>_<seq>_part0.mp4" and the
# newer codec-tagged format "..._<id>_720p_h264_<bitrate>_<key>_<seq>_part0.mp4".
SEQ_RE = re.compile(r"_(\d{6,})(?:_part(\d+))?\.mp4(?:$|\?)")


def find_ffmpeg() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for c in [
        os.path.join(here, "ffmpeg.exe"),
        os.path.join(here, "..", "ffmpeg.exe"),             # src/ -> repo root
        os.path.join(here, "ffmpeg", "ffmpeg.exe"),
        "ffmpeg.exe", "ffmpeg",
    ]:
        try:
            r = subprocess.run([c, "-version"], capture_output=True, timeout=5)
            if r.returncode == 0:
                return c
        except Exception:
            continue
    raise FileNotFoundError("ffmpeg not found")


async def run(model: str, output_path: str) -> int:
    ffmpeg = find_ffmpeg()
    ff_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "mp4", "-i", "pipe:0",
        "-c", "copy", "-copyts",
        "-f", "mpegts",
        "-y", output_path,
    ]
    ff = subprocess.Popen(
        ff_cmd,
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()

    init_written = False
    pending: dict[tuple[int, int], bytes] = {}
    flushed_upto = -1  # last fully flushed seq
    last_activity = time.time()  # updated on every successful segment write

    def safe_write(data: bytes) -> bool:
        nonlocal last_activity
        try:
            ff.stdin.write(data)
            last_activity = time.time()
            return True
        except (BrokenPipeError, OSError):
            return False

    def flush_seq(seq: int) -> None:
        keys = sorted([k for k in pending if k[0] == seq])
        for k in keys:
            data = pending.pop(k)
            safe_write(data)

    async def on_response(resp):
        nonlocal init_written, flushed_upto
        try:
            url = resp.url
            if not SEG_RE.search(url):
                return
            if resp.status != 200:
                return
            body = await resp.body()
            if "_init_" in url:
                if not init_written:
                    if safe_write(body):
                        init_written = True
                        print(f"[init] {len(body)}b", file=sys.stderr)
                return
            if not init_written:
                return
            m = SEQ_RE.search(url)
            if not m:
                return
            seq = int(m.group(1))
            if seq <= flushed_upto:
                return  # already past this seq
            part = int(m.group(2)) if m.group(2) is not None else 99
            key = (seq, part)
            if key in pending:
                return
            pending[key] = body
            # Flush any sequence < newest_seq (assumed complete)
            newest = max(k[0] for k in pending)
            while True:
                ready = [s for s in {k[0] for k in pending} if s < newest]
                if not ready:
                    break
                s = min(ready)
                flush_seq(s)
                flushed_upto = max(flushed_upto, s)
        except Exception as e:
            print(f"[resp-err] {e}", file=sys.stderr)

    def stdin_watcher():
        try:
            # Block until parent closes stdin (or sends 'q'). Reading any
            # data is fine — we just treat any input/EOF as "shut down now".
            sys.stdin.buffer.read()
        except Exception:
            pass
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=stdin_watcher, daemon=True).start()

    async with async_playwright() as p:
        try:
            proxy = os.environ.get("SCR33NX_PROXY", "")
            browser = await p.chromium.launch(
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required",
                      "--mute-audio"],
                **({"proxy": {"server": proxy}} if proxy else {}))
        except Exception as e:
            print(f"[launch-err] {e}", file=sys.stderr)
            try: ff.stdin.close()
            except Exception: pass
            ff.wait(timeout=10)
            return 2

        ctx = await browser.new_context(user_agent=UA,
                                        viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()
        page.on("response", on_response)

        try:
            await page.goto(f"https://stripchat.com/{model}",
                            wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"[nav-err] {e}", file=sys.stderr)
            await browser.close()
            try: ff.stdin.close()
            except Exception: pass
            ff.wait(timeout=10)
            return EXIT_NAV_ERR

        # Reset idle baseline to "after nav"; give the page time to load assets
        last_activity = time.time()

        # Private/ticket-show detection — scan page text 2s after nav.
        private_rc: int = 0
        async def _detect_private():
            nonlocal private_rc
            try:
                await asyncio.sleep(2.0)
                text = await page.evaluate("document.body ? document.body.innerText : ''")
                for marker in PRIVATE_MARKERS:
                    if marker in text:
                        print(f"[private-show] marker detected: {marker!r}",
                              file=sys.stderr)
                        private_rc = EXIT_PRIVATE_SHOW
                        stop.set()
                        return
            except Exception as e:
                print(f"[private-detect-err] {e}", file=sys.stderr)
        asyncio.create_task(_detect_private())

        # Run until parent closes stdin OR we hit an idle timeout OR private detected.
        idle_rc: int = 0
        while not stop.is_set():
            await asyncio.sleep(0.5)
            if time.time() - last_activity > IDLE_TIMEOUT_SECS:
                print(f"[idle-timeout] no segments for {IDLE_TIMEOUT_SECS}s "
                      f"(init_written={init_written})", file=sys.stderr)
                idle_rc = EXIT_IDLE_TIMEOUT
                break

        # Flush everything still buffered before closing ffmpeg.
        for seq in sorted({k[0] for k in pending}):
            flush_seq(seq)
        try:
            await browser.close()
        except Exception:
            pass

    try:
        ff.stdin.close()
    except Exception:
        pass
    try:
        ff.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ff.kill()

    # Private/idle exits override ffmpeg's own return code so the parent
    # recorder can distinguish "no public stream" from a normal shutdown.
    if private_rc:
        return private_rc
    if idle_rc:
        return idle_rc
    return ff.returncode if ff.returncode is not None else 0


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: stripchat_live.py <model> <output_path>", file=sys.stderr)
        return 1
    model = sys.argv[1]
    output = sys.argv[2]
    return asyncio.run(run(model, output))


if __name__ == "__main__":
    sys.exit(main())
