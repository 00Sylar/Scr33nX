# Browser Extension

The extension adds a model to Scr33nX with **one click** while you're on their
page, and lets you set a **1–5 star rank** from the popup once the model is in
Saved Models or the Recorder. Scr33nX must be running — the extension talks to
the app's local API at `http://localhost:5200`.

> The Chromium and Firefox versions are functionally identical; they differ
> only in `chrome.` vs `browser.` API calls.

## Install — Chromium (Chrome / Brave / Opera / Edge)

1. Open `chrome://extensions` (or `brave://`, `opera://`, `edge://extensions`).
2. Enable **Developer mode** (toggle in the corner).
3. Click **Load unpacked** and select the `extension/Chromium` folder.

## Install — Firefox

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add‑on…**.
3. Select `extension/Firefox/manifest.json`.

> Firefox removes temporary add‑ons on restart — reload it after restarting, or
> package it as a signed add‑on for a permanent install.

> **Firefox permissions:** MV3 host permissions are opt‑in. After loading the
> add‑on, open `about:addons` → Scr33nX → **Permissions** and grant access to
> the cam sites — without it the listing badges (below) won't appear and the
> background badge can't read tab URLs.

## What the popup does

- **Add** the current model's page to the Recorder or Saved Models in one click
  (the site is auto‑detected from the URL).
- **Rank** the model 1–5 stars — enabled only once the model is in Saved Models
  or the Recorder (otherwise the stars show disabled with a hint, to avoid
  "orphan" ranks).
- **Live status** — while the popup is open it keeps polling the backend, so the
  model's status, rank, and list membership update in place.
- **API token** — if you set a token in Scr33nX (⚙ Settings → Local API), the
  popup automatically switches to a token form the first time the app rejects
  it; paste the token once and it's stored in the extension. Change it later
  via the small **⚙ API token…** link at the bottom of the popup.

## Dynamic toolbar badge

The extension icon carries a live badge, updated on every tab switch /
navigation and every 30 s in the background:

| Badge | Meaning |
|---|---|
| **REC** (red, on a model's page) | that model is **recording right now** |
| **REC** (amber) | a **linked account** of this model is recording on another site (see [[linked identities\|Features#linked-identities]]) — starting here would double‑record the same person |
| **ON** (green) | in your Recorder and online, not recording |
| **OFF** (grey) | in your Recorder, offline / idle |
| **★** (blue) | in Saved Models only |
| **number** (red, any other tab) | count of active recordings across the app |

Hovering the icon on a model's page shows the model name + state as a tooltip.
No badge at all means the app isn't running (or the model isn't tracked and
nothing is recording).

When the model has **linked accounts** on other sites, the popup lists each
alias with its live state (`🔗 bobby @ Stripchat — RECORDING`) and shows an
amber "⚠ Already recording on … as …" banner when one of them is being
recorded. The amber **REC** variant also appears on listing‑page thumbnails.

If you click **⏺ Start Recording** while a linked account is already
recording, the button swaps to an inline confirm — **✔ Yes, record** /
**✕ Cancel** — instead of starting immediately. Recording is never blocked
outright; the confirm just stops an accidental double‑record. Cancel returns
you to the normal popup with nothing started.

## Listing badges (browse pages)

On Chaturbate / Stripchat / Camsoda / MyFreeCams **browse/listing pages**,
thumbnails of models you already track get a corner badge: a large, white-bordered,
glowing red **● REC** (amber if a linked account is recording), green **ON**, or
grey **OFF**. Saved Models show their **star rank** instead (e.g. ★★★☆☆, gold
for the rank, dimmed for the rest). You can tell at a glance who is already
being recorded — and how you rated them — without opening their room. Hover a badged card for the model's name and state. Badges
refresh every ~10 s (and as new cards load while you scroll); untracked models
get no badge. Everything is fed by **one** cached `/models` call, so a page
with 100 thumbnails costs the same as one status check.

## Highlight filter

In the popup, under **Highlight thumbnails**, pick one or more of **● REC**,
**Online**, **★5**, **★4**, **★3**, **★2**, **★1** or **No rank**. On the browse
pages, every thumbnail that matches none of your picks goes dark — still
visible, and it lights back up while you hover it. Ranks match exactly (★3 =
only 3‑star models); pick several to combine them. Untracked models never
match. **clear** switches the filter off. The change applies to open tabs
immediately.

## Hover mini player

Rest the mouse on the thumbnail of a model that is **online or recording in
your Recorder** and, after the hover delay, a small live player opens next to
the card — the same stream a Player tile uses. Click it to toggle sound. It
closes when the pointer leaves both the card and the player, or when you
scroll. Only one is open at a time, and nothing connects until the delay has
passed. Turn on **Preview any model** to also preview models that are only
in Saved Models or not tracked at all — their stream is looked up when you
hover, so the player takes a few seconds longer, and each preview is one extra
request to the cam site (off by default). Settings live in the popup under
**⚙ Mini player**: on/off, preview any model, size
(Small–X‑Large), hover delay (0.5–3 s), quality (360p–Max, default 480p) and
start muted. It needs the default (web) UI. Firefox: also grant
the `127.0.0.1` permission in `about:addons`.

## Right‑click add

Right‑click any model link or thumbnail on the cam sites →
**Add to Scr33nX Recorder** or **Add to Scr33nX Saved Models** — adds the
model without opening their page. The listing badge appears on the card a few
seconds later as confirmation.

Under the hood the extension calls the [[Local Control API|Local-Control-API]]
(`/status`, `/models`, `/add`, `/rank`, …). If clicks do nothing, confirm
Scr33nX is running and only **one** instance is open (see
[[Troubleshooting]]).
