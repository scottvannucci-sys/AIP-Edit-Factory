# 🎬 Edit Factory

**Automated video pipeline + script studio for the Amazon Influencer Program.**
Drop your raw clips in a folder → get back per-product, caption-burned, ready-to-upload vertical videos. Type an ASIN → get a shoppable-video script and a built-in teleprompter.

Works alongside [Oink for Influencers](https://oinkforinfluencers.com): Oink finds the products and tracks commissions in the browser; Edit Factory handles everything that involves your files, your words, and what happens after the upload — including the Creator Connections workflow, earnings ROI, per-platform renders, and a transcript-powered pre-upload compliance check. Runs 100% locally by default (an optional TypeSafe key adds AI judgment; everything falls back to local heuristics without it). Nothing leaves your PC.

---

## Contents

- [Quick start](#quick-start)
- [Your daily workflow](#your-daily-workflow)
- [The edit pipeline in detail](#the-edit-pipeline-in-detail)
- [Output files reference](#output-files-reference)
- [Creator Connections workflow](#creator-connections-workflow)
- [Oink earnings & ROI](#oink-earnings--roi)
- [Transcript intelligence](#transcript-intelligence)
- [Platform renders, thumbnails & upload queue](#platform-renders-thumbnails--upload-queue)
- [Script Studio](#script-studio)
- [Teleprompter](#teleprompter)
- [Tagging products: filenames, slates, nicknames](#tagging-products-filenames-slates-nicknames)
- [Configuration reference](#configuration-reference)
- [Command line](#command-line)
- [Troubleshooting](#troubleshooting)
- [Project layout](#project-layout)
- [Test drive](#test-drive)

---

## Quick start

> ⚠️ **Do NOT install or run this from inside OneDrive/Dropbox/Google Drive sync folders.**
> Sync-root folders break child-process spawning on Windows (`0xc0000142`,
> "Access is denied"). Use a plain local path like `C:\EditFactory`.

**Easy way:** double-click **`SETUP.bat`** — it installs Python 3.12 and FFmpeg (via `winget`), installs the Python packages, and validates everything. Then double-click **`START_WEB_UI.bat`**. Done.

**Manual way:**

1. **Python 3.10+** from [python.org](https://python.org) — during install, check **"Add python.exe to PATH"**.
2. **FFmpeg**: `winget install Gyan.FFmpeg` (then open a *new* terminal), or download the "full build" from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) and add its `bin` folder to PATH.
3. **Get the code**:
   ```
   git clone https://github.com/scottvannucci-sys/AIP-Edit-Factory.git
   cd AIP-Edit-Factory
   ```
   (No git? On the repo page choose **Code ▾ → Download ZIP**, unzip, and open that folder.)
4. **Python packages**: `pip install -r requirements.txt`
   (pins `faster-whisper==1.2.1` and `av==14.2.0` — newer PyAV 19+ breaks faster-whisper 1.2.1)
5. Double-click **`START_WEB_UI.bat`** once — it will download the Whisper model (~460 MB, one time only).

The launcher finds a working Python automatically (and rejects the fake Microsoft Store `python.exe` alias). It prints `Using: <path to python>` so you can always see what it picked.

---

## Your daily workflow

### One product per video (the common case)

1. **Name the file after the product's ASIN** — e.g. `B0CKWGQY9Z_kettle.mp4`. (The rest of the name is free-form; an ASIN anywhere in the filename counts.)
2. **Drop it into your watch folder** (`inbox/` by default — or any folder you pick in the UI, including a network share `\\NAS\videos`).
3. Select it and hit **▶ Process selected** (or enable the whole-folder flow with `START_EDIT_FACTORY.bat`).
4. Upload `output/<ASIN>/<ASIN>_captions.mp4` straight to the ASIN. Done.

### Multiple products in one long shot (supported too)

Film your whole day in one clip and **say a slate when you switch products**:

- *"Okay, next product: the yoga mat."* — if `yoga mat` is mapped to an ASIN in `config.json → product_codes`, everything after that sentence belongs to that product.
- Or just say the ASIN itself on camera (as one word works best).

The clip is split into per-product segments automatically, and footage **before the first slate belongs to the first product**.

### Two ways to use the output

| Route | Files | When |
|---|---|---|
| **Fastest** | `<ASIN>_captions.mp4` | Upload directly — edited, loudness-normalized, vertical 1080×1920, captions burned in |
| **CapCut** | `<ASIN>_clean.mp4` + `<ASIN>.srt` | You want to polish it yourself — cleaned takes + timed captions |

Raw files are moved to `archive/` so nothing is ever lost.

---

## The edit pipeline in detail

When you hit **Process selected**, here is exactly what happens.

### Stage 1 — per selected clip

1. **Probe** — ffprobe reads duration and checks for an audio track. Supported inputs: `.mp4 .mov .m4v .webm .mkv .avi .mts .m2ts`.
2. **Filename ASIN scan** — regex `B0` + 8 alphanumeric characters, matched even when glued to underscores (`B0CKWGQY9Z_kettle.mp4`).
3. **Transcription** — local Whisper AI (`small` model by default) with word-level timestamps and VAD. This is the slow step: budget roughly **2–4× the clip's length** on a typical CPU. Fully offline.
4. **Product segmentation** — the timeline is split into spans per ASIN, from (in priority order): spoken ASIN tokens, nickname phrases after a slate word (*"okay… next…"*), or the filename ASIN. Clips with no attribution at all are tagged `UNSORTED` — still fully processed, just grouped separately.
5. **Dead-air cutting** — silences are detected (default: anything quieter than −35 dB for longer than 0.45 s) and removed, keeping 0.15 s of padding around each cut. If less than 2% would be removed, the clip is passed through uncut ("negligible — single pass"). Caption word timestamps are **remapped across the cuts** so subtitles stay in sync.
6. **Extraction** — each product span is cut out in one ffmpeg pass: re-encode (H.264, CRF 20) **with EBU R128 loudness normalization** (−16 LUFS, −1.5 dBTP) applied in the same step.
7. **Archive** — the original is moved to `archive/` (disable with `--no-archive` or `"archive_processed": false`).

### Stage 2 — per ASIN

8. Segments are concatenated into **`<ASIN>_clean.mp4`** — the "CapCut route" master (original framing).
9. **If speech was detected:** a word-timed `.srt` is written and **`<ASIN>_captions.mp4`** is rendered — reframed to **1080×1920** (horizontal footage gets the blurred-background pad; vertical footage scales; `"reframe": "crop"` crops instead) with styled captions **burned into the pixels**. No speech → no captions, clean master only.
10. **Bookkeeping** — a row is appended to `output/manifest.csv` (ASIN, length, word count, file paths, timestamp) and a session summary `_report_YYYYMMDD_HHMM.md` is written.

The results table in the UI then shows **🎬 captions.mp4 / 🎞 clean.mp4 / 📄 .srt links per ASIN** — click to preview or download straight from the browser (the app serves your output folder over HTTP; no Explorer needed). The **📂 Open output folder** button additionally opens Explorer, with automatic fallbacks and a clear message if Windows blocks the launch.

---

## Output files reference

| Path | What it is |
|---|---|
| `output/<ASIN>/<ASIN>_captions.mp4` | **Upload-ready.** Vertical 1080×1920, loudness-normalized, captions burned in |
| `output/<ASIN>/<ASIN>_clean.mp4` | **CapCut route.** Silence-cut + normalized master, original framing |
| `output/<ASIN>/<ASIN>.srt` | Word-timed subtitles (only when speech was detected) |
| `output/<ASIN>/<ASIN>_yt/_reels/_tiktok/_pin.mp4` | Platform renders from [Renders & upload queue](#platform-renders-thumbnails--upload-queue) |
| `output/<ASIN>/thumbs/` | Thumbnail stills (evenly spread + a hook frame at your first spoken word) |
| `output/<ASIN>/broll/` | Extracted no-speech b-roll clips (from Transcript intelligence) |
| `output/manifest.csv` | Every product ever processed: ASIN, length, words, files, timestamp |
| `output/upload_queue.json` | Upload state per file: pending → uploaded / rejected / dismissed |
| `output/mentions.json` | Everything you've ever said about each ASIN (mention index) |
| `output/_report_YYYYMMDD_HHMM.md` | Per-session markdown summary |
| `campaigns.json` | Creator Connections campaigns (from paste or CSV import) |
| `oink_import/` | Your Oink earnings CSVs (raw + normalized) |
| `archive/` | Your raw originals, moved here after processing |
| `studio/*.json` | Saved Script Studio scripts |

---

## Creator Connections workflow

Paste a campaign brief from Oink Creator Connections (or import the "My CC
Campaign Products Report" CSV) and Edit Factory turns it into a work queue:

- **📋 Campaign card** — paste the email/brief → **🪄 Parse** extracts campaign
  name, brand, ASINs, deadline, and required talking points → **💾 Save**.
  The CSV importer accepts flexible headers, so column renames don't break it.
- **Gap analysis per ASIN** — each product shows its state:
  `missing` (never filmed) → `footage` (processed, no captions) → `ready`
  (captions master exists) → `submitted` (click the chip to mark it).
- **🎯 Film next** — ranks what to shoot today by deadline urgency, whether
  footage already exists, and how much that ASIN has earned you (when
  earnings are imported).
- **⚖ Pre-upload check** — one button runs the campaign's required talking
  points against your actual transcript (did you *say* it?) plus a claims
  risk scan with timestamps, before anything goes to the brand.

## Oink earnings & ROI

Drop your Oink earnings CSV into **💰 Import earnings** (file picker or a
server-side path — handy for the OneDrive download folder). The importer
maps columns flexibly (`date/asin/amount/status/product` and friends),
archives the raw file under `oink_import/`, and writes a normalized copy.
The **ROI table** joins earnings to `manifest.csv` for **$/minute per ASIN**
— your best-earning videos first — and lists **earners without video**
(products that paid you but you never filmed).

## Transcript intelligence

Reads the SRTs the pipeline already wrote (no re-transcription), per latest
manifest row:

- **🪝 Hook audit** — the first words of every video, scored 0–4, so you can
  see which openers work before filming more.
- **⚖️ Claims scan** — full-transcript risk rating with **flagged words +
  timestamps** — jump straight to the risky sentence.
- **🛟 UNSORTED rescue** — mystery clips matched against your `product_codes`
  nicknames and campaign ASINs (batched AI judgment, or local word overlap).
- **🎞 B-roll** — finds no-speech windows in each clean master and cuts them
  to `output/<ASIN>/broll/` for reuse.
- **🗂 Mention index** — everything you've ever said about each ASIN.

## Platform renders, thumbnails & upload queue

Type an ASIN, tick platforms, **🎬 Render**: YouTube Shorts (60s), FB/IG
Reels (90s), TikTok (10 min), Pinterest idea pin (5 min). Presets hard-trim
when needed (0.5s tolerance), re-encode with your CRF/preset, and add
`+faststart` — files land next to the masters as `<ASIN>_yt.mp4`,
`<ASIN>_reels.mp4`, `<ASIN>_tiktok.mp4`, `<ASIN>_pin.mp4`. **🖼 Thumbnail
stills** extracts evenly-spread frames plus a **hook frame** at your first
spoken word. Every rendered file is auto-added to the **📤 upload queue**
(`pending → uploaded / rejected / dismissed`) — and **🔄 Scan outputs**
back-fills anything already on disk.

---

## Script Studio

Open it from the link in the web UI header (or directly at `/studio`). It turns product data into a ready-to-read shoppable-video script.

1. **Fill the product**: title, key features (one per line), optional review themes, price, and tone (Friendly / Expert / Playful).
2. **Optional — 🛒 Fetch from Amazon**: paste an ASIN and the app fills title/features/price automatically via the **Product Advertising API**. Requires your PA-API credentials in `config.json → pa_api` (get them from *Associates Central → Tools → Product Advertising API*). Without keys, filling the fields manually works exactly the same.
3. **✨ Generate script** — template-based (no AI bills, deterministic per seed):
   - **3 hook styles** to pick from: question, bold claim, story.
   - Tone-matched body built from *your* bullets ("My favorite bit: 1500W rapid boil.").
   - CTA with the price woven in ("And at $39.99, it's an easy yes.").
   - **On-screen text ideas** (short overlay phrases) and **shot notes** (camera directions per section).
   - Duration estimate (word count ÷ ~155 wpm) so you know it fits the format.
4. **💾 Save** — scripts persist locally (`studio/`), listed for reopening; **📋 Copy text** for your phone.

### 🎯 Campaign mode (Studio 2.0)

The **Campaign brief** card weaves a Creator Connections requirement list
straight into the script: requirements become priority talking points (they
lead the point list), and after generation you get a **per-requirement
coverage check** (✅/❌ against your own script text) plus a **claims-risk
scan** — so a brief that asks you to say something you can't legally say
gets caught *before* filming. **📨 Draft brand pitch** writes a
Creator Connections submission message grounded in your real history for
that ASIN — video count and your most recent opening line pulled from your
transcripts. You edit before sending; Oink sends at scale.

## Teleprompter

Click **▶ Teleprompter** and the script becomes a full-screen scrolling prompter:

| Control | Action |
|---|---|
| Space | Play / pause |
| ↑ / ↓ | Speed −/+ (15–400 px/s) |
| Esc | Exit |
| Font slider | 24–140 px |
| Mirror checkbox | Horizontal flip for glass teleprompter rigs |
| Restart button | 3-2-1 countdown and back to the top |

The selected hook style is what the prompter shows.

---

## Tagging products: filenames, slates, nicknames

| Method | How | Good for |
|---|---|---|
| **Filename** | Put the ASIN anywhere in the file name | One product per video (simplest) |
| **Nickname slate** | Map `"yoga mat": "B0YOGAMAT01"` in `config.json`, then say *"next product: the yoga mat"* | Multi-product clips, natural speech |
| **Spoken ASIN** | Say the ASIN as one word on camera | No config needed |

Slate words that introduce a product: `next, okay, ok, alright, moving, now, up, so, and` (configurable via `slate_words`). Nickname matching is longest-phrase-first, so multi-word nicknames work.

## Configuration reference

Everything lives in `config.json` (created on first run; new keys merge in automatically).

| Key | Default | Meaning |
|---|---|---|
| `whisper_model` | `"small"` | `tiny` (fastest) / `base` / `small` (best accuracy/speed on CPU) |
| `whisper_language` | `"en"` | Spoken language hint for transcription |
| `silence.noise_db` | `-35` | Below this level (dB) counts as silence |
| `silence.min_silence` | `0.45` | Pauses shorter than this are kept (natural pacing) |
| `silence.min_gap` / `min_segment` | `0.6` / `0.4` | Merge/drop thresholds for kept segments |
| `silence.padding` | `0.15` | Seconds kept around each cut point |
| `silence.skip_cut_threshold` | `0.02` | If <2% would be removed, don't cut at all |
| `audio.lufs` / `true_peak` / `lra` | `-16` / `-1.5` / `11` | EBU R128 loudness normalization targets |
| `export.width` / `height` | `1080` / `1920` | Output resolution |
| `export.crf` / `preset` | `20` / `"medium"` | H.264 quality/speed |
| `export.reframe` | `"blur"` | Horizontal→vertical reframe: `"blur"` pad or `"crop"` |
| `captions.font` / `size` / `bold` | `"Arial"` / `60` / `true` | Caption typography |
| `captions.max_words` / `max_span` | `7` / `3.0` | Caption chunking |
| `captions.margin_v` / `outline` | `380` / `3` | Vertical position and outline width |
| `slate_words` | `["next","okay","ok","alright","moving","now","up","so","and"]` | Words that introduce a new product |
| `product_codes` | examples | Nickname → ASIN map (`"kettle": "B0CKWGQY9Z"`) |
| `paths.inbox` / `paths.output` | `""` | Watch folder / output folder (empty = app defaults; supports UNC shares) |
| `web.port` | `8765` | Web UI port (also overridable with the `EF_PORT` env var) |
| `archive_processed` | `true` | Move raw files to `archive/` after processing |
| `pa_api.access_key` / `secret_key` / `partner_tag` | `""` | PA-API credentials for Script Studio's Amazon fetch |
| `typesafe.api_key` | `""` | Optional TypeSafe key enabling AI judgment (claims risk, brief matching, UNSORTED rescue, hook scores) |
| `typesafe.enabled` | `true` | Set `false` to force 100% local heuristics even with a key present |
| `typesafe.model` | `"jev-latest"` | TypeSafe System One model |
| `typesafe.timeout` | `25` | Seconds before falling back to local heuristics |

**AI judgment, dual backend:** every semantic feature (claims risk, brief
coverage, UNSORTED rescue, hook scores, campaign script checks) uses
TypeSafe System One when a key is configured and reachable, and silently
falls back to local heuristics (word overlap, risk-word lists) otherwise —
same result shape either way, `"backend"` tells you which ran. No key?
Everything still works, fully offline. Your key never appears in logs or
API responses.

If the port is busy, the server detects an already-running Edit Factory and opens it instead of failing; with `EF_PORT` set it starts on that port regardless.

## Command line

```
python edit_factory.py process                        # process everything in inbox/
python edit_factory.py process --inbox D:\clips       # different watch folder
python edit_factory.py process --no-archive           # leave raw files in place
python edit_factory.py stats                          # manifest summary (recent products)
python edit_factory.py init                           # write default config.json
```

## Troubleshooting

- **"Python was not found"** → run `SETUP.bat` once, or install Python with *Add to PATH* checked. The launchers reject the Microsoft Store alias automatically.
- **`0xc0000142` or "Access is denied" when opening folders** → the app is running from inside a OneDrive/Dropbox sync folder. Move it to a plain local path (this is the #1 cause; verified on-machine).
- **"ffmpeg not found"** → install FFmpeg and open a new terminal (PATH refresh), or reboot after `SETUP.bat`.
- **Port already in use** → another Edit Factory is running; the UI opens it instead. Use `EF_PORT=8766` for a second instance.
- **Explorer blocked but no error shown** → use the file links in the results table (they work without Explorer) — the open button reports what happened either way.
- **No captions** → the clip had no detectable speech; the clean master is still exported.
- **Wrong product split** → say the slate more clearly, rename the file with the ASIN, or check `product_codes`. Reprocess from `archive/` (move the file back to the watch folder first).
- **First run is slow** → the Whisper model downloads once (~460 MB) to `C:\Users\<you>\.cache\huggingface`.

## Project layout

```
edit_factory.py        pipeline core (transcribe, segment, cut, normalize, caption, export)
webui.py               FastAPI server + endpoints (files, process, config, media, scripts,
                       campaigns, oink ROI, judgments, transcript intel, renders, queue)
studio.py              Script Studio engine + PA-API client + script storage
                       (+ Studio 2.0: campaign scripts, brand pitches)
campaigns.py           Creator Connections campaign model + gap analysis + film-next
oink_data.py           Oink earnings import + ROI join
judgments.py           dual-backend semantic layer (TypeSafe System One / local heuristics)
transcript_intel.py    hook audit, claims scan, b-roll, UNSORTED rescue, mentions
renders.py             platform presets, thumbnails, upload-queue state machine
web/index.html         single-page editor UI (no build step)
web/studio.html        Script Studio + teleprompter + campaign mode page
config.json            your settings (created on first run; never committed)
START_WEB_UI.bat       launcher → http://127.0.0.1:8765
START_EDIT_FACTORY.bat launcher → classic whole-inbox CLI flow
SETUP.bat              one-click installer (Python + FFmpeg + packages)
selftest_*.py          offline test suites (36 + 22 + 25 + 22 + 16 checks)
test_media.ps1         synthesizes demo clips for a test drive
requirements.txt       pinned dependencies (faster-whisper 1.2.1, av 14.2.0, fastapi, uvicorn)
```

## Test drive

No footage needed: `pwsh ./test_media.ps1` synthesizes three demo clips (edge-tts + FFmpeg), then run the factory on them:

```
python edit_factory.py process --inbox test_clips --no-archive
```

Expected: two products auto-split from spoken slates, one tagged from its filename, one silent clip in `UNSORTED`.

## License

[MIT](LICENSE) — © 2026 Scott Vannucci. Free to use, modify, and share.
