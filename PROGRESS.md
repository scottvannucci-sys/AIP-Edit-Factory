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
- `webui.py` - 23 new endpoints total: campaigns CRUD/parse_paste/
  import_csv/submit, film_next, oink earnings import (upload-as-body +
  path), roi, earners_without_video, judgments/status, intel/*
  (hook_audit, compliance, broll plan/extract, unsorted_rescue, mentions,
  campaign_compliance). Old endpoints untouched.
- `web/index.html` - four new cards: Film next, CC Campaigns (paste ->
  parse -> save; state chips click-to-mark-submitted; CSV path import;
  per-campaign "⚖ check" compliance pre-upload button), Oink earnings ->
  ROI table, Transcript intelligence (hooks / claims / rescue / mentions).
- `selftest_foundation.py` (36) + `selftest_judgments.py` (22, optional
  `--live`) + `selftest_transcript_intel.py` (25). All green.
- E2E: sandboxed app copy with fixture output tree boot-tested; every
  intel endpoint exercised over live HTTP incl. 404 paths.

## Verification commands

```
python selftest_foundation.py
python selftest_judgments.py          # add --live for one real API call
python selftest_transcript_intel.py
python -m py_compile *.py
```

## NEXT (in order)

1. Platform render presets (YouTube Shorts / FB+IG Reels / TikTok /
   Pinterest idea pins) + thumbnail still extraction + upload-queue state.
2. Script Studio 2.0 - campaign brief -> script, brand pitch drafts.
3. README update once features land. (Also consider persisting word-level
   transcripts at pipeline time for richer b-roll/mentions.)

## Notes

- `config.json` is UNTRACKED on purpose (holds API keys). Fresh clones
  recreate it from defaults; re-paste keys manually.
- Waiting on user: `samples/` drops (Oink earnings CSV, CC campaign
  paste) to tune importers to real exports.
- Real manifest columns: asin,duration_s,words,clean,captions_mp4,srt,
  processed_at (UTF-8-BOM CSV).
