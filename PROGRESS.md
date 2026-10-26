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
- `webui.py` - 16 new endpoints: campaigns CRUD/parse_paste/import_csv/
  submit, film_next, oink earnings import (upload-as-body + path), roi,
  earners_without_video, judgments/status. Old endpoints untouched.
- `web/index.html` - three new cards: Film next, CC Campaigns (paste ->
  parse -> save; state chips click-to-mark-submitted; CSV path import),
  Oink earnings -> ROI table.
- `judgments.py` - dual-backend semantic layer: TypeSafe System One
  (noul/choice/score via POST /v1/systemone) with automatic local-heuristic
  fallback on any failure. judgments: claims_risk, is_product_switch,
  brief_supported, hook_strength. Key never logged/returned.
  Config: `config.json -> "typesafe": {"api_key": ..., "enabled": true,
  "model": "jev-latest", "timeout": 25}`. Set enabled:false for 100% local.
- `selftest_foundation.py` (36 checks) + `selftest_judgments.py`
  (22 checks incl. optional `--live` real API call). Both green.

## Verification commands

```
python selftest_foundation.py
python selftest_judgments.py          # add --live for one real API call
python -m py_compile *.py
```

## NEXT (in order)

1. `transcript_intel.py` - hook audit, spoken-claims checker, b-roll
   extraction (no-speech spans), UNSORTED rescue (fuzzy ASIN suggestions),
   per-ASIN mention mining. Consumes judgments.py.
2. Wire compliance pre-check into the pipeline (transcript vs campaign
   requirements + claims risk) and surface results in the UI.
3. Platform render presets (YouTube Shorts / FB+IG Reels / TikTok /
   Pinterest idea pins) + thumbnail still extraction + upload-queue state.
4. Script Studio 2.0 - campaign brief -> script, brand pitch drafts.
5. README update once features land.

## Notes

- `config.json` is UNTRACKED on purpose (holds API keys). Fresh clones
  recreate it from defaults; re-paste keys manually.
- Waiting on user: `samples/` drops (Oink earnings CSV, CC campaign
  paste) to tune importers to real exports.
- Real manifest columns: asin,duration_s,words,clean,captions_mp4,srt,
  processed_at (UTF-8-BOM CSV).
