#!/usr/bin/env python3
"""
Self-test for transcript_intel.py.

Run:  python selftest_transcript_intel.py     (Python 3.10+, no deps)

Builds a synthetic output tree (manifest + SRTs) in a temp dir - never
touches your real output. ffmpeg-dependent extraction is excluded; the
b-roll planner is tested with an injected duration.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import transcript_intel as ti  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}")


SRT_KETTLE = """1
00:00:00,000 --> 00:00:03,000
Stop scrolling - this kettle is worth thirty seconds.

2
00:00:03,200 --> 00:00:05,000
It boils fast and pours clean.
"""

SRT_RISKY = """1
00:00:01,000 --> 00:00:03,000
This cream cures eczema and heals skin overnight.
"""

SRT_UNSORTED = """1
00:00:00,500 --> 00:00:04,000
This yoga mat has amazing grip and comes with carrying straps.
"""

SRT_OLD = """1
00:00:00,000 --> 00:00:02,000
old blah old blah old blah.
"""

MANIFEST = (
    "asin,duration_s,words,clean,captions_mp4,srt,processed_at\n"
    "B0AAAA0001,60.0,20,B0AAAA0001/c.mp4,,B0AAAA0001/old.srt,2026-10-24T09:00:00\n"
    "B0AAAA0001,60.0,20,B0AAAA0001/c.mp4,,B0AAAA0001/k.srt,2026-10-25T10:00:00\n"
    "B0BBBB0002,30.0,0,B0BBBB0002/c.mp4,,,\"\" 2026-10-25T10:00:00\n"
    "UNSORTED,45.0,15,UNSORTED/c.mp4,,UNSORTED/u.srt,2026-10-25T11:00:00\n"
    "B0DDDD0004,20.0,10,B0DDDD0004/c.mp4,,B0DDDD0004/r.srt,2026-10-25T12:00:00\n"
)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out = tmp / "out"
        (out / "B0AAAA0001").mkdir(parents=True)
        (out / "B0BBBB0002").mkdir(parents=True)
        (out / "UNSORTED").mkdir(parents=True)
        (out / "B0DDDD0004").mkdir(parents=True)
        (out / "manifest.csv").write_text(MANIFEST, encoding="utf-8-sig")
        (out / "B0AAAA0001" / "k.srt").write_text(SRT_KETTLE, encoding="utf-8")
        (out / "B0AAAA0001" / "old.srt").write_text(SRT_OLD, encoding="utf-8")
        (out / "B0DDDD0004" / "r.srt").write_text(SRT_RISKY, encoding="utf-8")
        (out / "UNSORTED" / "u.srt").write_text(SRT_UNSORTED, encoding="utf-8")

        print("== srt parsing + latest-wins ==")
        events = ti._read_srt(out / "B0AAAA0001" / "k.srt")
        check(len(events) == 2, "srt: two events parsed")
        check(abs(events[0][0] - 0.0) < 0.01 and abs(events[1][1] - 5.0) < 0.01,
              "srt: timestamps correct")
        tr = ti.transcript_for("B0AAAA0001", out)
        check(tr is not None and tr["events"]
              and tr["events"][0][2].startswith("Stop scrolling"),
              "transcript: newest manifest row wins")

        print("== hook audit ==")
        res = ti.hook_audit(out_root=out, cfg={})
        check("B0AAAA0001" in res["hooks"], "hook: transcript asin present")
        check(res["hooks"]["B0AAAA0001"]["value"] >= 3.0,
              "hook: pattern-interrupt opener scored high")
        check("UNSORTED" in res["hooks"], "hook: UNSORTED handled like an asin")
        one = ti.hook_audit(out_root=out, cfg={}, asin="B0DDDD0004")
        check(list(one["hooks"].keys()) == ["B0DDDD0004"],
              "hook: single-asin filter")

        print("== compliance scan ==")
        scan = ti.compliance_scan(out_root=out, cfg={})
        check(scan["scan"]["B0AAAA0001"]["value"] == "low",
              "comply: plain praise -> low")
        check(scan["scan"]["B0DDDD0004"]["value"] == "high",
              "comply: 'cures eczema' -> high")
        check(any(f["word"] == "cures" and f["t"] == 1.0
                  for f in scan["scan"]["B0DDDD0004"]["flags"]),
              "comply: flagged word carries timestamp")
        check("B0BBBB0002" not in scan["scan"],
              "comply: asin without speech skipped")

        print("== b-roll planning ==")
        plan = ti.broll_plan("B0AAAA0001", out_root=out, duration=60.0)
        check(len(plan["windows"]) == 1 and
              abs(plan["windows"][0][0] - 5.3) < 0.01,
              "broll: speech-free window after last caption")
        check(plan["total_broll_s"] > 50.0, "broll: window length sane")
        try:
            ti.broll_plan("NOPE0000", out_root=out)
            check(False, "broll: unknown asin raises")
        except KeyError:
            check(True, "broll: unknown asin raises")

        print("== unsorted rescue ==")
        rescue = ti.unsorted_rescue(out_root=out,
                                    cfg={"product_codes": {
                                        "yoga mat": "B0CCCC0003",
                                        "kettle": "B0AAAA0001"}})
        check(rescue["checked"] == 2, "rescue: both candidates checked")
        check(rescue["candidates"]
              and rescue["candidates"][0]["asin"] == "B0CCCC0003",
              "rescue: yoga mat ranked first")

        print("== mention mining ==")
        data = ti.mention_mine(out_root=out)
        check("B0AAAA0001" in data["asins"], "mentions: asin indexed")
        check(data["asins"]["B0AAAA0001"]["word_count"] == 15,
              "mentions: word count from latest srt")
        check(data["asins"]["B0BBBB0002"]["has_transcript"] is False,
              "mentions: speechless asin flagged")
        check((out / "mentions.json").exists(), "mentions: json written")

        print("== campaign compliance pre-check ==")
        store = tmp / "campaigns.json"
        camp = __import__("campaigns")
        c = camp.add_campaign(name="CC Kettle", brand="HotCo",
                              asins=["B0AAAA0001", "B0BBBB0002"],
                              requirements=["Mention the self-cleaning brush",
                                            "boils fast"],
                              path=store)
        cc = ti.campaign_compliance(c["id"], path=store, out_root=out, cfg={})
        by_asin = {a["asin"]: a for a in cc["asins"]}
        check(by_asin["B0AAAA0001"]["checked"] is True,
              "precheck: transcript asin checked")
        reqs = {r["requirement"]: r["value"]
                for r in by_asin["B0AAAA0001"]["requirements"]}
        check(reqs["boils fast"] is True, "precheck: covered requirement -> True")
        check(reqs["Mention the self-cleaning brush"] is False,
              "precheck: missing requirement -> False")
        check(by_asin["B0AAAA0001"]["ok"] is False,
              "precheck: not ok while a requirement is missing")
        check(by_asin["B0BBBB0002"].get("checked") is False,
              "precheck: speechless export marked unchecked")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
