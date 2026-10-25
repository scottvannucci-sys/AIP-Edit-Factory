# 🎬 Edit Factory

**Automated video pipeline for the Amazon Influencer Program.**
Drop your raw clips in a folder → get back per-product, caption-burned, ready-to-upload vertical videos.

Works alongside Oink: Oink finds the products, Edit Factory ships the videos.

---

## Your daily workflow (2 minutes of effort)

1. **Film** your products however you like. Two ways to keep it organized — use either or both:
   - **Name the file after the product** — e.g. `B0CKWGQY9Z_kettle.mp4`
   - **Just say it on camera** when you switch products, e.g.
     *"Okay, next product: the yoga mat."*
   You can film your **entire day in one long clip** — Edit Factory splits it for you.
2. **Drop the files** into the `inbox/` folder.
3. **Double-click `START_EDIT_FACTORY.bat`** and wait.
4. Upload from `output/`:
   - **Fastest:** `<ASIN>_captions.mp4` — edited, vertical 1080×1920, captions burned in. Upload straight to the ASIN.
   - **CapCut route:** import `<ASIN>_clean.mp4` + `<ASIN>.srt` — your cleaned takes and timed captions, ready for your own polish.

Raw files are moved to `archive/` so nothing is ever lost.

## What it does automatically

| Step | Detail |
|---|---|
| 🗣 **Transcribes** | Local Whisper AI — nothing leaves your PC |
| 🔪 **Sorts by product** | From filenames, or from spoken slates ("next product: the yoga mat") |
| ✂️ **Cuts dead air** | Removes silences >0.45s, keeps natural pacing |
| 🔊 **Loudness** | Normalizes to −16 LUFS (standard for social video) |
| 📝 **Captions** | Word-timed SRT + burned-in styled captions |
| 🖼 **Reframes** | Horizontal footage → blurred-background 1080×1920 |
| 📊 **Logs** | `output/manifest.csv` + a markdown session report |

## Setup (one time)

1. Install [Python 3.10+](https://python.org) and [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) (or `winget install Gyan.FFmpeg`).
2. In this folder run: `pip install -r requirements.txt`
   (pins `faster-whisper==1.2.1` and `av==14.2.0` — newer PyAV 19+ breaks faster-whisper 1.2.1)
3. Double-click `START_EDIT_FACTORY.bat` once — it will download the Whisper model (~460 MB, one time only).

## Test drive

No footage needed: `pwsh ./test_media.ps1` synthesizes three demo clips (edge-tts + FFmpeg), then run the factory on them:

```
python edit_factory.py process --inbox test_clips --no-archive
```

Expected: two products auto-split from spoken slates, one tagged from its filename, one silent clip in `UNSORTED`.

## Nicknames (`config.json`)

Don't want to say ASINs out loud? Define nicknames:

```json
"product_codes": {
  "kettle": "B0CKWGQY9Z",
  "yoga mat": "B0YOGAMAT01"
}
```

Say *"okay, next product: the yoga mat"* → everything after that line is tagged to that ASIN.
(Change the defaults in `config.json` to your real ASINs — the defaults are just examples.)

## Tweaks

Everything lives in `config.json`:
- `whisper_model`: `tiny` (fastest) / `base` / `small` (default, best on CPU)
- `silence.min_silence`: how long a pause must be before it gets cut
- `captions.size` / `margin_v`: caption look and position
- `export.reframe`: `blur` (default) or `crop` for horizontal footage

## Commands

```
python edit_factory.py process             # run the pipeline on inbox/
python edit_factory.py process --no-archive
python edit_factory.py stats               # what have I processed?
```

## Troubleshooting

- **"ffmpeg not found"** → install FFmpeg and reopen the terminal (PATH refresh).
- **No captions** → the clip had no detectable speech, or the model failed to load; the clean master is still exported.
- **Wrong product split** → say the slate more clearly, or rename the file with the ASIN; reprocess from `archive/`.
