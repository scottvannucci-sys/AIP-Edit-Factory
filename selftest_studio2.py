#!/usr/bin/env python3
"""
Self-test for Studio 2.0 (campaign_script + brand_pitch).

Run:  python selftest_studio2.py     (Python 3.10+, stdlib only)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import studio as st  # noqa: E402

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


def main() -> None:
    empty_cfg = {}

    print("== campaign_script ==")
    reqs = ["Mention the 3-year warranty",
            "Show the self-cleaning brush on camera"]
    s1 = st.campaign_script("B0TEST0001", reqs, campaign="Spring Refresh",
                            brand="Acme", title="Turbo Kettle 1.7L",
                            bullets=["1500W rapid boil"], tone="friendly",
                            seed="seed1", cfg=empty_cfg)
    tele = st.script_to_teleprompter(s1)
    check(all(r.lower() in tele.lower() for r in reqs),
          "camp: requirements verbatim in script")
    check(len(s1["campaign"]["coverage"]) == 2
          and all(c["value"] is True for c in s1["campaign"]["coverage"]),
          "camp: coverage checks pass (local backend)")
    check(all(c["backend"] == "local" for c in s1["campaign"]["coverage"]),
          "camp: coverage backends reported")
    cr = s1["campaign"]["claims_risk"]
    check(cr["backend"] == "local" and cr["value"] in ("low", "medium", "high"),
          "camp: claims risk shape + clean copy stays low")
    s2 = st.campaign_script("B0TEST0001", reqs, campaign="Spring Refresh",
                            brand="Acme", title="Turbo Kettle 1.7L",
                            bullets=["1500W rapid boil"], tone="friendly",
                            seed="seed1", cfg=empty_cfg)
    check(s1["hooks"] == s2["hooks"] and s1["points"] == s2["points"],
          "camp: deterministic for same seed")

    s3 = st.campaign_script("B0TEST0002",
                            ["Cures eczema", "Mention the 3-year warranty"],
                            title="Cream", tone="friendly", seed="s",
                            cfg=empty_cfg)
    check(s3["campaign"]["claims_risk"]["value"] == "high",
          "camp: risky requirement language flags high")

    print("== requirements take priority over bullets ==")
    s4 = st.campaign_script("B0TEST0003",
                            ["Req one", "Req two", "Req three"],
                            bullets=["Bullet one", "Bullet two", "Bullet three"],
                            title="Gadget", tone="friendly", seed="s",
                            cfg=empty_cfg)
    pts = " ".join(p["text"] for p in s4["points"]).lower()
    check(all(f"req {n}" in pts for n in ("one", "two", "three")),
          "camp: all three requirements made it into the 3-point script")

    print("== dedup requirement vs bullet ==")
    s5 = st.campaign_script("B0TEST0004", ["Auto shut-off"],
                            bullets=["Auto shut-off", "Cool-touch handle"],
                            title="Kettle", tone="friendly", seed="s",
                            cfg=empty_cfg)
    flat = " ".join(p["text"] for p in s5["points"]).lower()
    check(flat.count("auto shut-off") == 1, "camp: requirement not duplicated")

    print("== brand_pitch ==")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out"
        (out / "B0TEST0001").mkdir(parents=True)
        (out / "manifest.csv").write_text(
            "asin,duration_s,words,clean,captions_mp4,srt,processed_at\n"
            "B0TEST0001,30,50,B0TEST0001/c.mp4,B0TEST0001/cap.mp4,,2026-10-25T10:00:00\n"
            "B0TEST0001,31,52,B0TEST0001/c2.mp4,B0TEST0001/cap2.mp4,"
            "B0TEST0001/B0TEST0001.srt,2026-10-26T10:00:00\n"
            "B0OTHER0001,20,30,B0OTHER0001/c.mp4,B0OTHER0001/cap.mp4,,2026-10-26T09:00:00\n",
            encoding="utf-8-sig")
        (out / "B0TEST0001" / "B0TEST0001.srt").write_text(
            "1\n00:00:00,000 --> 00:00:02,000\nThis kettle boils fast and "
            "looks great.\n\n2\n00:00:02,000 --> 00:00:04,000\nGrab it on my "
            "storefront.\n", encoding="utf-8")

        p = st.brand_pitch("B0TEST0001", campaign="Spring Refresh",
                           brand="Acme", requirements=["Mention the warranty"],
                           out_root=out)
        check(p["videos"] == 2, "pitch: manifest history counted")
        check(p["has_transcript"] is True, "pitch: transcript detected")
        check("boils fast" in p["opening_quote"], "pitch: opening quote taken from SRT")
        check("Track record: 2 video(s)" in p["pitch"],
              "pitch: track-record line present")
        check('"Spring Refresh"' in p["pitch"] and "Acme" in p["pitch"],
              "pitch: campaign + brand interpolated")
        check("* Mention the warranty" in p["pitch"],
              "pitch: requirements listed verbatim")

        p2 = st.brand_pitch("B0OTHER0001", out_root=out)
        check(p2["videos"] == 1 and p2["has_transcript"] is False,
              "pitch: video w/o srt -> history but no quote")
        p3 = st.brand_pitch("B0NONE0000", out_root=out)
        check(p3["videos"] == 0 and "first video" in p3["pitch"],
              "pitch: unknown ASIN -> first-video framing")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
