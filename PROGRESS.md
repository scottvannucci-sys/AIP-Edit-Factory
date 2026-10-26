# PROGRESS - Oink integration build

Living dev notes. Update as work lands. (Feature roadmap came from the
"what could we add for Oink members" discussion; goal tracked in session.)

## DONE (all committed, tested, boot-verified)

- `campaigns.py` - Creator Connections campaign model (`campaigns.json`,
  gitignored): paste parser (name/brand/ASINs/deadline/requirements),
  flexible "My CC Campaign Products Report" CSV importer, per-ASIN gap
  analysis (missing/footage/ready/submitted), deadline days-left,
  `film_next()` ranking (urgency + footage-exists + earnings boost),
  `mark_submitted()` tracking.
- `oink_data.py` - Oink earnings CSV import with flexible column mapping
  (raw copy archived + `oink_import/earnings_normalized.csv`), ROI join
  with `output/manifest.csv` ($/min per ASIN), `earners_without_video()`.
- `transcript_intel.py` - reads the SRTs the pipeline already wrote
  (latest manifest row per ASIN wins): `hook_audit`, `compliance_scan`
  (flagged words w/ timestamps), `broll_plan`/`extract_broll`
  (no-speech windows -> output/<ASIN>/broll/), `unsorted_rescue`
  (nicknames + campaign ASINs, TypeSafe noul batch or local overlap),
  `mention_mine` (-> output/mentions.json), `campaign_compliance`
  (pre-upload requirements + claims check per campaign).
- `judgments.py` - dual-backend semantic layer: TypeSafe System One
  (noul/choice/score via POST /v1/systemone) with automatic local-heuristic
  fallback on any failure. judgments: claims_risk, is_product_switch,
  brief_supported, hook_strength. Key never logged/returned.
  Config: `config.json -> "typesafe": {"api_key": ..., "enabled": true,
  "model": "jev-latest", "timeout": 25}`. Set enabled:false for 100% local.
- `renders.py` - post-processing handoff: 4 platform presets
  (youtube_shorts/_yt 60s, facebook_reels/_reels 90s, tiktok/_tiktok 10m,
  pinterest/_pin 5m; trim via plan_render, re-encode crf/preset from
  config, +faststart), `render_platform_variants` (auto-queues results),
  `extract_thumbnails` (evenly spread + hook frame at first spoken word
  -> output/<ASIN>/thumbs/), upload queue state machine
  (output/upload_queue.json: pending -> uploaded/rejected/dismissed,
  dedupe, `queue_seed` scans manifest + existing platform files).
- `webui.py` - 30 new endpoints total (adds: renders presets/render/
  thumbs, queue list/seed/update). Old endpoints untouched.
- `web/index.html` - five new cards: Film next, CC Campaigns (+ per-
  campaign "⚖ check"), Oink earnings -> ROI, Transcript intelligence,
  Platform renders & upload queue (per-ASIN render buttons w/ platform
  checkboxes, thumbnail button, queue table w/ state buttons + scan).
- `selftest_foundation.py` (36) + `selftest_judgments.py` (22, optional
  `--live`) + `selftest_transcript_intel.py` (25) +
  `selftest_renders.py` (22, incl. real-ffmpeg block when on PATH).
  All green.
- E2E: two sandboxed app copies (no keys) boot-tested: every intel +
  render endpoint exercised over live HTTP with REAL ffmpeg encodes
  (synthetic 3s clip -> platform variants + thumbs + queue + media
  serving + 404 paths).

## Verification commands

```
python selftest_foundation.py
python selftest_judgments.py          # add --live for one real API call
python selftest_transcript_intel.py
python -m py_compile *.py
```

## NEXT (in order)

1. Script Studio 2.0 - campaign brief -> script (weave required talking
   points + compliance language), brand pitch drafts from transcript
   history. Wire campaign_compliance results into studio generation.
2. README update once features land. (Also consider persisting word-level
   transcripts at pipeline time for richer b-roll/mentions.)
3. Optional: pipeline-time transcript persistence (words.json per source
   clip) to improve b-roll precision and hook detection.

## Notes

- `config.json` is UNTRACKED on purpose (holds API keys). Fresh clones
  recreate it from defaults; re-paste keys manually.
- Waiting on user: `samples/` drops (Oink earnings CSV, CC campaign
  paste) to tune importers to real exports.
- Real manifest columns: asin,duration_s,words,clean,captions_mp4,srt,
  processed_at (UTF-8-BOM CSV).
