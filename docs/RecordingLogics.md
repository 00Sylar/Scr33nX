# Scr33nX — Recording Logics

**"HOW TO MAKE IT WORK" — full technical reference for how Scr33nX records live
streams from each supported site.**

This document is the single source of truth for the recording pipeline. It is
written so that another engineer — or another AI/LLM — can read it cold and
understand exactly how each site is resolved, recorded, and kept healthy,
without having to reverse-engineer the code first. When a site breaks, this is
where you start.

---

## 0. The big picture

Every recording follows the same skeleton, regardless of site:

```
  status check ──► resolve stream URL ──► (relay) ──► ffmpeg -c copy ──► .ts file
       │                  │                  │              │
   "is she live          per-site         local HTTP     graceful
    & public?"          resolver          proxy on        shutdown
                                          127.0.0.1       flushes trailer
```

Four sites are supported, each with its own **resolver** but a shared
**recording core**:

| Site | Tag | Resolver file | Recording path |
|---|---|---|---|
| Chaturbate | `CB` | `recorder.get_chaturbate_stream_url` | relay → `ffmpeg -c copy` |
| Stripchat | `ST` | `recorder.get_stripchat_stream_url` + `stripchat_native` | relay → ffmpeg (native), else Playwright browser |
| Camsoda | `CS` | `recorder.get_camsoda_stream_url` | relay → `ffmpeg -c copy` |
| MyFreeCams | `MFC` | `mfc.get_stream_url` | relay → `ffmpeg -c copy` |

The orchestration logic (monitoring, auto-start/stop, file splitting, stall
detection, restarts) lives in `recorder.py` in the `StreamRecorder` class and is
**identical for all four sites**. Only URL resolution and (for Stripchat) the
recording mechanism differ per site.

**Key files:**
- `recorder.py` — resolvers + `StreamRecorder` orchestration + ffmpeg launch
- `cb_relay.py` — local HTTP relay (quality pinning, prefetch, bandwidth meter)
- `stripchat_native.py` — browserless Stripchat (MOUFLON decryption)
- `stripchat_live.py` — Playwright/Chromium fallback for Stripchat
- `mfc.py` — MyFreeCams FCS websocket resolver
- `app.py` — GUI + extension HTTP API (port 5200)

---

## 1. The local relay (`cb_relay.py`) — the heart of recording

ffmpeg **never talks to the CDN directly** for Chaturbate, Camsoda, and
MyFreeCams (and for the native Stripchat path). Instead it is pointed at a small
HTTP server running on `127.0.0.1` (plain HTTP, random port), and that relay
fetches upstream using Python's `requests`. This solves four problems at once:

1. **TLS resets** — Chaturbate's LL-HLS CDN edges reset ffmpeg's TLS connection
   mid-segment (Schannel error -10054, "session has been invalidated"), which
   truncates fMP4 segments and corrupts the `.ts`. `requests` downloads the same
   bytes reliably.
2. **Quality pinning & caps** — the relay rewrites the master playlist to keep
   **only one variant** (`_select_highest_variant`), so ffmpeg physically
   cannot fall back to a lower resolution mid-recording. By default that's the
   highest-BANDWIDTH variant; if a quality cap applies (see §1b), it's the
   highest-bandwidth variant whose `RESOLUTION` height is ≤ the cap (lowest
   available as fallback if every variant is above the cap).
3. **Parallel prefetch** — ffmpeg's HLS demuxer fetches segments one at a time;
   if a download is slower than the segment duration it falls permanently behind
   the live window and segments expire (causing 1–2 s timestamp jumps). The
   relay already knows upcoming segment URLs (it rewrote the playlist), so it
   prefetches them with **64 worker threads** into an in-memory cache and serves
   ffmpeg instantly.
4. **Bandwidth metering + drop detection** — every byte fetched upstream is
   counted (`bytes_downloaded()` drives the `↓ Mbps` meter). Segments that
   expire from the playlist before they could be fetched are reported via a gap
   callback (Activity Log + optional toast).

### Concurrency sizing (learned the hard way)

The prefetch pool is **shared by all streams**: with N concurrent recordings
and ~2 s segments it must complete ~N/2 downloads per second. The original
16 workers — each stallable for up to 60 s (3 × 20 s retries) — starved at
~10–15 streams, making *every* stream drop segments at once. Current sizing,
validated at ~50 concurrent recordings:

| Knob | Value | Why |
|---|---|---|
| `_PREFETCH_WORKERS` | 64 | ~N/2 downloads/s for N≈50 streams with headroom |
| prefetch fetch budget | 2 tries, 5 s connect / 10 s read | a live segment only exists ~10–20 s; fail fast, next playlist refresh retries |
| `_CACHE_MAX_BYTES` | 768 MB | 300 MB silently disabled prefetching at high N; a warning now logs if the cap is hit |
| HTTP pool | 32 hosts / 128 connections | workers must never block on a free connection |
| `request_queue_size` | 128 | listen-backlog default of 5 refused bursts of ffmpeg connections (ffmpeg "Error number -138") |

### How wrapping works

```python
relay_url = cb_relay.wrap(upstream_master_url, USER_AGENT, mode=site, label="site:model")
# returns e.g. http://127.0.0.1:54321/p.m3u8?m=chaturbate&l=...&u=<encoded upstream url>
```

- `mode` selects the playlist transform: `chaturbate` / `camsoda` / `myfreecams`
  (pin highest variant, strip LL-HLS tags) or `stripchat` (MOUFLON decrypt).
- `label` (e.g. `chaturbate:alice`) names the stream for gap reporting.
- The relay rewrites **every** URI in every playlist (segments, `EXT-X-MAP`,
  `EXT-X-MEDIA`, `EXT-X-PART`) to also route through the relay.

### Extension normalization gotcha

ffmpeg whitelists segment URLs by **path extension**. The relay normalizes every
non-`.m3u8` URL to end in `.m4s` (a universally whitelisted fragmented-MP4
extension) so odd upstream extensions (e.g. Camsoda's `.fmp4`) aren't rejected.
ffmpeg is also launched with `-allowed_extensions ALL` for relayed sites as a
belt-and-suspenders measure.

### Referer requirement

doppiocdn (Stripchat) and the other CDNs reject segment requests without a
matching `Referer`/`Origin`. The relay injects the correct ones per `mode`
(see `_REFERERS` in `cb_relay.py`).

### Chaturbate edges are gated by IP family — and it flips

Chaturbate's mmcdn edges (`edgeN-xxx.live.mmcdn.com`) answer **403** (bare
nginx page) to every request — master, media playlist, init, segments — that
arrives over the *wrong* IP family, and which family is wrong has changed:

| Date | Edge refuses | Edge accepts |
|---|---|---|
| ~2026-09-23 | IPv6 | IPv4 |
| 2026-10-02 | IPv4 | IPv6 |

The symptom either way is that the model resolves ("url ok") and then every
playlist 403s: ffmpeg logs `Server returned 403 Forbidden`, Player tiles log
`manifestLoadError`, nothing records. Measured 2026-10-02 (same machine,
dual-stack): `mint over v4 → fetch over v6 = 200`, `mint v6 → fetch v4 = 403`
— so **only the fetch family matters; the token is not bound to the minting
IP**, and a 403 from the wrong family does **not** spend the token (the same
token then succeeds over the other family). The earlier V2.6 fix hard-pinned
IPv4 (and believed the token was IP-bound), which is exactly what broke when
the edge flipped.

So nothing is pinned any more. `cb_relay._cb_edge_get` (used for every
`mode="chaturbate"` upstream GET via `_get`, including the streaming
cache-miss path) tries the preferred family first (`_cb_order`, IPv6 by
default), and on a **403** or a **connection error** (no route for that
family) retries on the other. A **2xx** from a non-preferred family promotes
it to the front (logged once: `Chaturbate edges now reached over IPv6 (the
other family was refused)`), so the cost of a flip is one extra request, once.
404/5xx are returned as is (the edge accepted the family). Both families
403 → the last 403 is returned (a genuinely spent token; the master cache
below still handles it).

Each family is its own `requests.Session` whose adapter binds the socket's
source address to the family wildcard (`0.0.0.0` / `::`) so urllib3 skips the
other family's DNS records (`cb_relay._FamilyAdapter`). The Chaturbate **API**
(`recorder._http`, `https://chaturbate.com`) is plain dual-stack — it works
over both families and the token doesn't care where it was minted. The other
sites keep the default dual-stack session.

### Chaturbate edges can also refuse a whole network (2026-10-03)

A third, different break: from some networks **every** edge request 403s over
**both** IPv4 and IPv6 — the mint × fetch family matrix is all 403, whatever
the headers (UA/Referer/Origin/full Chrome/ffmpeg/VLC), a cookie-warmed
session, Chrome's TLS fingerprint (curl_cffi), other URL shapes or other edge
hosts. The site's own `get_edge_hls_url_ajax/` returns a URL that 403s too, and
the browser falls back to Chaturbate's JPEG-snapshot stream (`stream?room=…`)
instead of HLS. From a different network address (a VPN, Cloudflare WARP) the
same room plays HLS — the edge region follows the client IP. So the edge
refuses that network's address ranges: nothing in the request chain can fix it.

The answer is the per-site **proxy** (`netproxy.py`, Settings → Proxy). With a
proxy set for `chaturbate`, `cb_relay._get` fetches via that proxy through the
plain `_session` and skips `_cb_edge_get`'s family fallback (the proxy picks
the route; a 403 is returned as is). Resolution order per site: its own entry
(`direct` = bypass) → the default proxy → none. Chaturbate's room lookups
(`recorder._http` → chaturbate.com) always stay direct — only the mmcdn edges
are gated. The other relay modes (`stripchat`, `camsoda`, `myfreecams`), their
resolvers and the MFC websocket use their site's proxy for everything; the
Stripchat Playwright fallback gets it via the `SCR33NX_PROXY` env var read by
`stripchat_live.py`. Everything is validated in `netproxy.validate` (bare
`host:port` → `socks5h://`; `socks5` → `socks5h` so the proxy resolves names).
Diagnosing next time: if the mint×fetch probe shows all-403 *and* a VPN/WARP
exit fixes it, it's network-level — use a proxy; don't chase headers.

---

## 1b. Quality caps & auto-downgrade

The relay calls `cb_relay.set_quality_callback(fn)` — registered by
`StreamRecorder` as `effective_quality(label)` — each time it fetches a
**master** playlist (i.e. on recording start/restart, never mid-stream).
The returned int is the max variant height in pixels (0 = unlimited).

**Resolution order** (`recorder.effective_quality`):

```
session auto-downgrade  >  per-model override  >  global setting  >  unlimited
   (recorder._session_q)   (app right-click menu)  (Settings dropdown)
```

- The per-model overrides live in the app (`_model_q`, persisted per model as
  `max_q` in the config JSON) and are shared with the recorder by reference.
- Caps only apply to `chaturbate` / `camsoda` / `myfreecams` modes —
  **Stripchat bypasses `_select_highest_variant`** (its native path resolves a
  keyed variant directly; the Playwright path bypasses the relay entirely).

**Auto-downgrade** (`recorder._maybe_downgrade`, opt-in via the Settings
checkbox → `auto_downgrade_enabled`): driven by the relay's gap callback. If a
stream loses ≥ `10 s` of video within a `60 s` window, the recorder:

1. picks the next rung below the current effective cap from `(720, 480, 240)`,
2. records it in `_session_q[label]` (session-only),
3. gracefully stops ffmpeg — the normal exit handler restarts it, the relay
   re-queries `effective_quality()`, and the lower variant gets pinned.

Guards: 2-minute cooldown per stream after each step (the restart itself is
briefly unstable); models with a per-model override are **never** touched
(explicit user choice); stripchat labels are skipped; at the bottom rung it
just logs. `_session_q` is cleared when the recording ends naturally (offline,
private, manual stop) so the next session starts back at configured quality.
A downgrade restart resets `restart_count` so it doesn't consume the
3-attempt crash-restart budget.

---

## 2. The recording core (`recorder.py`)

### ffmpeg invocation (`launch_ffmpeg_hls`)

For relayed sites the command is essentially:

```
ffmpeg -hide_banner -loglevel error
       -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10
       -allowed_extensions ALL
       [-m3u8_hold_counters 20]      # chaturbate, myfreecams only
       -i <relay_url>
       -c -copy -copyts
       <output_path>.ts
```

- `-c copy` — **no re-encoding**; the stream is muxed as-is into MPEG-TS. Fast,
  lossless, low CPU.
- `-copyts` — preserve timestamps.
- The process is spawned with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` and
  a `stdin` pipe so we can send `q\n` for a **graceful shutdown** (`graceful_stop`),
  which flushes the MPEG-TS trailer and prevents corrupt files. Fallback is
  `terminate()` then `kill()`.

### Output filename format

```
<model>_<TAG>_<YYYYMMDD>_<HHMMSS>[_partNNN].ts
# e.g. alice_CB_20240515_143022_part001.ts
```

`_partNNN` only appears when a Max File Size is configured. Tags: `CB`, `ST`,
`CS`, `MFC` (see `SITE_TAGS`).

### Orchestration (`StreamRecorder`)

- **Monitoring groups** — two independent monitor threads: `recorder` (the
  active record list) and `saved` (the view-only watchlist). A model can be in
  both.
  - The `recorder` loop checks each model sequentially every `check_interval`
    seconds (default 30), with a 5 s base tick for session housekeeping.
  - The `saved` loop uses a **bulk scanner** because per-model polling doesn't
    scale to hundreds of watchlist entries (see §3 Chaturbate).
  - Because a model can be in both groups, session housekeeping
    (`_session_housekeeping` → split/stall/exit handling, and `_begin_recording`)
    is serialized per model via `ModelConfig.session_lock`. Without it, both
    monitor threads could act on the same session at a split point at the same
    time and each launch a replacement ffmpeg process — only one wins the
    `cfg.session` reference, so the other ran forever, invisible to Stop All /
    Clear Recorder / the low-disk guard (all of which only ever act on what
    `cfg.session` currently points to). Fixed — see the [Unreleased] entry in
    CHANGELOG.md.
- **Auto start/stop** — when a resolver returns a URL the model is `ONLINE`;
  recording begins automatically. When she goes offline ffmpeg exits and status
  returns to `OFFLINE`.
- **File splitting** (`_check_split`) — when the current file reaches Max File
  Size: graceful-stop ffmpeg (flush trailer), re-resolve the URL, bump the part
  number, start a new file.
- **Stall detection** (`_check_stall`) — if the output file hasn't grown for
  60 s, ffmpeg is gracefully killed; the exit handler then restarts it. A
  missing output file counts as 0 bytes (a recorder that hangs before ever
  creating its file used to be undetectable and stayed `RECORDING` forever).
- **Offline probe on stall** (`_probe_offline_while_stalled`) — because ffmpeg
  often keeps *reconnecting* (rather than exiting) when a model goes offline,
  waiting out the full 60 s left the status stuck on `RECORDING`. So after ~20 s
  of no growth the recorder fires a one-shot resolver check **off the monitor
  thread**: if the model is offline it stops ffmpeg immediately (→ `OFFLINE` in
  ≈25 s); if she's still online (buffering) the recording is left alone and the
  60 s hard-stop remains the backstop. Re-armed whenever the file grows again.
- **Restart logic** (`_handle_ffmpeg_exit`) — on ffmpeg exit code 0/1 the
  recording auto-restarts up to **3 times** (3 s delay, URL re-resolved). For
  Stripchat, exit codes 4 (idle/no segments) and 5 (ticket/private/group show)
  mean "online but not public" → status `PRIVATE`, no restart, 5-minute cooldown.
- **Advert-loop detection (Stripchat)** — when a model goes offline mid-stream,
  doppiocdn often swaps the *same* variant playlist for the `MOUFLON-ADVERT` /
  `/cpa/` placeholder loop. ffmpeg keeps downloading those filler segments, the
  file keeps growing, and stall detection never fires → stuck on `RECORDING`
  (recording adverts). The relay checks every playlist refresh for the advert
  markers (zero extra network — it fetches the playlist anyway): on detection
  it 404s the playlist to ffmpeg and fires `set_advert_callback(label)`; the
  recorder stops the session and `_handle_ffmpeg_exit` flips it straight to
  `OFFLINE` (no restart — a restart would just re-record the advert loop).
- **Low-disk guard** (`_enforce_low_disk_guard`, opt-in Settings checkbox) —
  every monitor tick (and each session-watcher pass when monitors are off)
  checks free space on the output drive via `shutil.disk_usage`. Below
  `LOW_DISK_MIN_FREE_GB` (20 GB) it stops all active downloads and
  `_begin_recording` refuses every start (manual REC, auto-rec, restart) with
  status `ERROR: Low disk`; `_check_split` also refuses to open the next part.
  A probe failure never blocks (`_disk_free_gb` returns `None` → not blocked).
  One toast on trip, one log line on recovery; refused-start log lines are
  throttled to one per minute. `stop_all_recordings` (which the trip calls)
  also sweeps every process ever launched via `_launch_proc` — not just the
  ones a `cfg.session` currently references — and force-kills any that are
  still alive but untracked, so a bookkeeping mismatch elsewhere can't leave
  a process running (and downloading/writing) past the guard tripping.
- **Session watcher** — an always-on background loop handles split/stall/exit
  even when the monitor is off (e.g. user clicked REC manually). It idles while
  any monitor is running to avoid double-handling.

---

## 3. Per-site resolver logic

### Chaturbate (`CB`)

**Single model:** `GET https://chaturbate.com/api/chatvideocontext/{name}/`
returns JSON with `hls_source` (the master playlist URL) and `room_status`.

- `room_status` in `{offline, away, private, hidden, ""}`, or HTTP 404 → offline.
- Throttled (HTTP 429/403/503, empty body, or a 200 that isn't JSON, e.g. a
  Cloudflare challenge), a network error, or a garbled reply → **unknown**.
  `chaturbate_lookup()` returns `live=None`, and `get_chaturbate_stream_url()`
  marks the name via `chaturbate_status_unknown()`. The monitor
  (`_check_online`) and the saved scan then **keep the previous status**
  rather than flipping the model OFFLINE.
- The monitor path retries once for CDN warmup; the manual-REC path retries 4×.

**Pacing (`_cb_get` / `_cb_wait`).** Every chaturbate.com API call goes
through an adaptive pacer:
- Background requests (monitor checks, saved-scan lookups, room-list pages)
  reserve slots **0.2 s** apart, and several can be in flight at once.
- A throttle signal doubles the spacing, up to 4 s (logged as
  `[CB] throttled … request spacing now Xs`). Each clean response eases it
  back down.
- Interactive requests skip the queue unless the pacer is backing off. These
  are the Player/Preview (`max_age > 0`) and manual REC (`thorough`). They
  don't take slots, so they can't starve the sweep.

It replaced a strict one-at-a-time lock with a 1.5 s gap. Measured
2026-09-28: 2 full sweeps plus 16 parallel Player resolves were 196 requests,
all 200, with no backoff.
- A room-list sweep went from **~150 s to ~18 s**.
- A 16-tile Player wall went from **~24 s to ~1.2 s** to resolve.

**Bulk (saved watchlist).** Two paths, chosen by count:
- **≤ 45 saved CB models** (`_CB_PER_MODEL_MAX`): per-model `chaturbate_lookup`
  on 6 threads. This takes fewer requests than a sweep, and it fills the URL
  cache, so the Player opens those models without an API call.
- **More than 45:** one full room-list sweep
  (`/api/ts/roomlist/room-list/`, 90 rooms/page, since the API returns 400
  above 90; ~90 pages) and a membership test.
  - Page 0 gives `total_count`. The rest are fetched on 4 threads, plus one
    page past the total to catch rooms that came online mid-sweep.
  - It runs at most once per `_CB_SWEEP_MIN_INTERVAL` (60 s), so a short
    check interval can't turn it into constant load.
  - It returns `None` on any failed page, so statuses are preserved.

**Recording:** master URL → relay (`mode=chaturbate`) → ffmpeg. The relay strips
LL-HLS partial-segment tags so only full segments are recorded.

> **CB master tokens are effectively single-use.** Fetching the same
> `…/llhls.m3u8?token=…` twice within ~40 s is 403, even from the same IP
> and connection. The variant/audio playlists it lists carry a `?session=`
> that stays valid while something polls it (a tile polling alongside
> ffmpeg got only 200s), but expires once idle (403 about 55 s after the last
> poll). So the relay caches each CB master playlist
> (`_master_cached` / `_master_store`):
> - It replays a copy younger than 30 s (`_MASTER_FRESH`) as is.
> - An older copy, up to 300 s, is only a fallback when a fresh fetch is 403.
> Without this, a Player tile retry, or a tile reusing a URL that a
> recording already opened, fails with `manifestLoadError`.

### Stripchat (`ST`)

Stripchat is the most complex because its playlists are DRM-protected
("MOUFLON"). There are **two recording paths**, tried in order:

**Path A — native browserless (preferred, `stripchat_native.py`):**
1. Resolve numeric stream id by scraping the **server-rendered model page**
   (`stripchat_native.page_info`): `GET https://stripchat.com/{name}` with
   `Accept: text/html,application/xhtml+xml,*/*;q=0.8` → `"isLive"`,
   `"streamName"` (= the numeric id) and `"status"`. The header matters:
   with no `Accept` the site answers **406** with an empty body, and with a
   full browser `Accept`/`Sec-Fetch` set it serves a client-rendered shell
   with no `streamName`. Successful lookups are cached (see
   **Resolve caching** below) so the Player can reuse the page the online
   check just fetched instead of pulling ~430 KB again.
   > The old source, `GET /api/front/v2/models/username/{name}/cam`
   > (`cam.streamName` / `cam.isCamAvailable`), is **dead** — stripchat's bot
   > filter answers it with HTTP **418** for every request regardless of
   > headers, cookies or a preceding page visit. Don't reintroduce it.
2. Fetch master playlist
   `https://edge-hls.doppiocdn.com/hls/{id}/master/{id}_auto.m3u8`.
3. The master lists accepted key-ids as `#EXT-X-MOUFLON:PSCH:v2:<keyId>`. We
   maintain a **key table** (`MOUFLON_KEYS`, extendable via
   `stripchat_mouflon_keys.json` next to the module). If no listed key-id matches
   our table → keys rotated → return None → fall back to Path B.
4. Pick the highest-BANDWIDTH variant, append `?psch=v2&pkey=<keyId>`.
5. Validate it's a real public stream: reject an advert loop (`MOUFLON-ADVERT`
   or `/cpa/` present) and reject a playlist with neither `#EXT-X-MOUFLON:` nor
   `#EXTINF` — a ticket/group show the anonymous viewer isn't entitled to
   answers the keyed variant with a bare `Forbidden` body, which is what gates
   non-public shows now that `isCamAvailable` is gone.
6. The keyed variant URL goes to the relay with `mode=stripchat`. The relay's
   `rewrite_playlist` (in `stripchat_native.py`) **decrypts** each segment URL:
   the `#EXT-X-MOUFLON:URI:` value's 2nd-to-last `_`-token is the real segment
   name reversed + XOR-encrypted with `SHA256(key)`; decrypt it and substitute
   it for the dummy `media.mp4` line that follows. Then plain `ffmpeg -c copy`
   records it.

> **MOUFLON cipher:** `base64-decode → XOR with cyclic SHA256(key)`. Key table
> is public knowledge from `kesamom/stripchat_mouflon` and
> `lossless1024/StreaMonitor`. When Stripchat rotates keys, add the new
> `keyId: key` pair to `stripchat_mouflon_keys.json` — no code change needed.

**Path B — Playwright/Chromium fallback (`stripchat_live.py`):**
Used when the native path fails (keys rotated, not public, advert loop). A
headless Chromium plays the page (decoding MOUFLON internally); we intercept
segment HTTP responses, reorder them by sequence number (LL-HLS fetches parts in
parallel), and pipe ordered fMP4 bytes into a child ffmpeg that transmuxes to
MPEG-TS. It behaves like a Popen ffmpeg process (live-growing `.ts`, stdin-close
= graceful stop). Exit codes: 4 = idle timeout (no segments in 45 s), 5 =
ticket/private/group show detected.

> **Note:** the Playwright path does NOT go through the relay, so its traffic is
> **not counted** by the bandwidth meter, and it records a single quality (no
> highest-variant pinning beyond what the player chooses).

**Lightweight online check (saved scanner):** `stripchat_native.page_info`
(same cached page fetch as step 1) and test `is_live` — cheaper than the full
resolve. `recorder.get_stripchat_stream_url` builds the step-2 master URL from
the same lookup; it is the online scanner's "she's up" signal only, since the
recording resolves its own keyed variant.

### Resolve caching (`max_age`)

`get_stream_url()`, `get_chaturbate_stream_url()` and
`stripchat_native.page_info()/stream_id()/resolve()` all take a **`max_age`**:
the oldest cached answer the caller will accept, in seconds.

- Every successful resolve is **stored** in the cache, always.
- A cached answer is only **read** when `max_age > 0`.
- The default is `0`, so **every liveness check goes to the network.** This is
  deliberate: `check_interval` is user-configurable with no floor, so a fixed
  TTL could otherwise outlive the check that reads it and report a model who
  just went offline as still online.
- Only the Player / Preview paths pass a non-zero value
  (`recorder.PREVIEW_URL_MAX_AGE`, 30 s). They already refuse to open a model
  that isn't ONLINE/RECORDING, so the URL they reuse is no staler than the
  status the user is looking at.

The win is largest on Chaturbate: a cache hit sends no request at all, so
the Player opens a model the monitor or saved scan just checked in **0 ms**.
Reusing the URL is safe even though CB tokens are single-use, because the
relay replays its cached master playlist (see above).

### Camsoda (`CS`)

Simplest resolver. Public endpoint:

```
GET https://www.camsoda.com/api/v1/video/vtoken/{name}
→ { token, edge_servers: ["host/path"], stream_name, status }
```

Build: `https://{edge}/{stream_name}_v1/index.m3u8?token={token}` (the edge
already includes its path segment; `stream_name` embeds the resolution). If
`status` is present and not `"online"`, or any field is missing → not recordable.

**Recording:** URL → relay (`mode=camsoda`) → ffmpeg. Camsoda segments use the
`.fmp4` extension, which is why the relay's extension normalization + ffmpeg's
`-allowed_extensions ALL` matter here.

### MyFreeCams (`MFC`)

MFC has **no public JSON API** mapping a name to a stream. Every working tool
speaks MFC's **FCS chat protocol over a websocket** with a guest login. This is
implemented in `mfc.py`:

1. `GET https://www.myfreecams.com/_js/serverconfig.js` — server maps (cached 1 h).
2. Connect `wss://{xchat}.myfreecams.com/fcsl` (pick an `rfc6455` websocket
   server from the config).
3. Send the handshake frames:
   ```
   hello fcserver\n\0
   1 0 0 20071025 0 {rand}@guest:guest\n     (FCTYPE 1  = LOGIN)
   10 0 0 20 0 {model_name}\n                (FCTYPE 10 = USERNAMELOOKUP)
   ```
4. Server frames are `{6-char length}{FCTYPE} {from} {to} {arg1} {arg2} {payload}`.
   The lookup payload is URI-encoded JSON with `uid`, `vs` (video state), and
   `u.camserv`.

**Video state (`vs`) mapping:**

| vs | meaning | recordable? |
|---|---|---|
| 0 | public chat | ✅ yes → `online` |
| 2 | away | no → `away` |
| 12 / 13 / 14 | private / group / club-curtain | no → `private` |
| 90 / 127 / other | cam off / offline | no → `offline` |

**HLS edge resolution:** `uid_video = uid + 100_000_000`. `camserv` maps to a
video host via serverconfig (`h5video_servers` → prefix `mfc_`, `wzobs_servers`
→ `mfc_a_`, `ngvideo_servers` → `mfc_`; heuristic fallback `video{camserv-500}`).
Candidate playlist URLs are probed and the first answering **200 + `#EXTM3U`**
wins (this hedges the `f4v_mobile` → `f4v_cmaf` CDN migration and serverconfig
gaps):

```
https://{server}.myfreecams.com/NxServer/ngrp:{prefix}{uid_video}.f4v_cmaf/playlist_sfm4s.m3u8
https://{server}.myfreecams.com/NxServer/ngrp:{prefix}{uid_video}.f4v_mobile/playlist.m3u8
```

**Recording:** winning URL → relay (`mode=myfreecams`) → ffmpeg.

**Bulk lookup:** `lookup_models([names])` opens one websocket and does
sequential lookups for the whole watchlist. `last_status(name)` returns the
cached video state so the recorder can show `PRIVATE` without a second round-trip.

> On any protocol drift, every MFC entry point returns None and MFC models
> simply read OFFLINE — the rest of the app is unaffected.

---

## 4. Quick troubleshooting map

| Symptom | Likely cause | Where to look |
|---|---|---|
| CB statuses stop updating / `[CB] throttled` in the log | Cloudflare rate-limit; the pacer is backing off (statuses are kept, not flipped OFFLINE) | `_cb_get` / `_CB_BASE_INTERVAL`; raise the base spacing if it recurs |
| CB resolves ("url ok") but every playlist 403s / `manifestLoadError` | Edge refusing the IP family in use (it has flipped before); the relay should auto-switch | §1 "Chaturbate edges are gated by IP family"; `cb_relay._cb_edge_get`; look for the `Chaturbate edges now reached over …` log line. If both families 403, the cause is something else (headers/token) — probe with `requests` over each family |
| CB resolves but every playlist 403s on **both** IP families, and the browser shows slow JPEG instead of video | The edge refuses your whole network (not the family) | §1 "…can also refuse a whole network"; set a proxy for Chaturbate in Settings → Proxy (e.g. WARP proxy mode `socks5h://127.0.0.1:40000`) and press **Test** |
| Stripchat won't record, no browser opens | MOUFLON keys rotated; should fall back | `stripchat_native.resolve` returns None → check Playwright |
| Stripchat records but bandwidth meter ignores it | On Playwright fallback (expected — bypasses relay) | `launch_stripchat_playwright` |
| Camsoda "extension not whitelisted" | Relay extension-normalize regressed | `_wrap_url` (.m4s) + `-allowed_extensions ALL` |
| MFC always OFFLINE | serverconfig/protocol drift, or no playlist candidate answered | `mfc._candidate_urls`, `mfc.lookup` |
| `.ts` files corrupt/unplayable | ffmpeg killed instead of graceful 'q' | `graceful_stop` |
| Recording drops segments (⚠ warnings) | Total bandwidth saturated | set a Max Quality cap / enable auto-downgrade; relay gap callback |
| ALL streams drop segments at once | Prefetch pool starved or cache cap hit | §1 concurrency sizing; look for "prefetch cache full" in `streamrecorder.log` |
| ffmpeg "Error number -138" to 127.0.0.1 | Relay listen backlog overflow | `_QuietServer.request_queue_size` |
| Quality silently dropped mid-recording | `_select_highest_variant` not applied | relay `mode` not set, or master not pinned |
| Stream records at lower quality than expected | Quality cap or session auto-downgrade active | §1b; Activity Log "⬇" lines; right-click → Max Quality |
| Upload meter shows impossible speeds | TDLib dedupe/resume reports instant upload | `_bw_tick` spike filter in `app.py` |

---

## 5. Bandwidth budget

Each 1080p stream needs roughly **5–6 Mbps sustained**. If many simultaneous
recordings drop segments (watch for ⚠ warnings), the total internet connection
is the bottleneck. Remedies, in order: set a global **Max Quality** cap
(720p roughly halves usage vs. unlimited), enable **⬇ Auto-Downgrade** so only
the streams that can't keep up lose quality (§1b), or record fewer models at
once. The `↓ Mbps` header meter shows Scr33nX's total upstream download
traffic (relay-routed sites only).

## 6. Beta logging

`streamrecorder.log` (in `%LOCALAPPDATA%\Scr33nX`, rotating 5 MB × 3, UTF-8)
receives everything: Activity Log lines, per-stream ffmpeg stderr, relay
warnings (including "prefetch cache full"), and background-thread tracebacks —
with thread names. `streamrecorder_crash.log` (same folder, reset at startup
once it exceeds 1 MB) captures hard interpreter crashes via `faulthandler`.
When something misbehaves, start there.
