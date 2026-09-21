# Telegram Upload Pipeline (optional)

The **Output / Upload** tab can convert finished `.ts` recordings to `.mp4` and
upload them to a Telegram group/topic automatically.

## Two independent stages

Tick either or both, at any time, with the **Stages** checkboxes at the top of
the tab:

- **① Convert .ts → .mp4** — converts (and size‑splits) finished recordings
  into `.mp4` files in the converted folder, and *keeps* them. Use alone for
  `.mp4` files with no Telegram setup needed. The split size follows the same
  **Max File Size** setting as the recorder; if Upload (②) is also enabled,
  it's additionally capped at Telegram's own ~3.8 GB per‑file upload limit,
  whichever is smaller. Splitting seeks the input directly (fast even on
  multi‑GB files), and a `.ts` that repeatedly fails to convert is skipped
  after 3 attempts (until the pipeline is restarted) instead of retrying
  forever.
- **② Upload .mp4 to Telegram** — uploads `.mp4` files from the converted
  folder. Use with ① for the full convert‑then‑upload flow, or alone to
  upload `.mp4`s you already have. As of V2.0, uploading no longer waits for
  an in‑progress conversion batch to finish — an uploader grabs a file the
  moment it's ready.
  Each `.mp4` is deleted once it's safely on Telegram; if Windows still has
  the file locked, the delete is retried for ~15 s and, failing that, the file
  is moved to a **`stuck\`** sub‑folder of the converted folder so it stays
  visible rather than lingering unnoticed (already-uploaded files are never
  re-sent, so anything in `stuck\` is yours to delete or keep).

**Stand‑by model:** the pipeline starts even with *no* stage checked — it
sits in **● STAND BY** doing nothing. Ticking a stage takes effect
immediately (header shows **● CONVERTING**, **● UPLOADING**, or
**● CONVERTING & UPLOADING**); unticking stops it after the current task
finishes — nothing in flight is ever interrupted. No restart is ever needed,
including turning Upload on for the first time (it connects to Telegram on
demand, reusing your saved session).

## Setup

**First time? Use the wizard.** Click **🧙 Setup Wizard** on the Output /
Upload tab — it walks you through the API ID/Hash (with a link to
my.telegram.org), the destination group/topic, and optional folders, saves
everything, and can start the pipeline for you (you'll be prompted for your
phone number and login code on first connect).

To set it up manually instead:

1. Get your `api_id` / `api_hash` from <https://my.telegram.org>.
2. Fill in the **Telegram / Pipeline settings** (group ID, optional topic
   ID) and save.
3. Start the pipeline and tick the stages you want, whenever you want.

Telegram credentials are only required once you enable **②**; if they're
missing, the pipeline log says so and Upload stays idle until you fill them
in and re‑tick it. Settings (including stage choices) are stored in
`Pipeline/pipeline_settings.json`. Upload requires the `tdjson` package
(installed via `requirements.txt`); Convert‑only does not.

## Re‑auth

**🔑 Re‑auth / Switch Account** clears the cached TDLib session — use it to
log in as a different Telegram account, or if your session gets stuck.

## Controlling it remotely

Via the [[Local Control API|Local-Control-API]]:

- `POST /pipeline {enabled}` — start/stop (starts in stand‑by).
- `POST /pipeline/stage {convert?, upload?}` — tick/untick a stage live.

...and therefore from the [[OpenClaw Bot|OpenClaw-Bot]] ("start/stop the
pipeline", "turn on pipeline convert/upload").
