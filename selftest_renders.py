#!/usr/bin/env python3
"""
Self-test for renders.py (platform variants, thumbnails, upload queue).

Run:  python selftest_renders.py     (Python 3.10+)

Pure-logic and queue tests always run; a real-ffmpeg block (synthetic 3s
clip -> platform renders + thumbnails) runs when ffmpeg/ffprobe are on PATH.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import renders as rd  # noqa: E402

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
    print("== preset definitions ==")
    check(len(rd.PLATFORMS) == 4, "platforms: four targets")
    check(len({p["suffix"] for p in rd.PLATFORMS.values()}) == 4,
          "platforms: unique suffixes")
    check(all(p["max_duration_s"] > 0 for p in rd.PLATFORMS.values()),
          "platforms: positive max durations")

    print("== plan_render ==")
    check(rd.plan_render(95.0, rd.PLATFORMS["facebook_reels"])
          == {"trim": True, "duration": 90.0}, "plan: 95s -> trim to 90")
    check(rd.plan_render(30.0, rd.PLATFORMS["tiktok"])
          == {"trim": False, "duration": 30.0}, "plan: short -> untouched")
    check(rd.plan_render(60.4, rd.PLATFORMS["youtube_shorts"])["trim"] is False,
          "plan: within 0.5s tolerance -> no trim")
    check(rd.plan_render(61.0, rd.PLATFORMS["youtube_shorts"])
          == {"trim": True, "duration": 60.0}, "plan: 61s -> trim to 60")

    print("== thumb_timestamps ==")
    ts = rd.thumb_timestamps(60.0, count=6, hook_t=0.0)
    check(len(ts) == 7 and ts[0] == 0.5 and ts == sorted(ts),
          "thumbs: hook frame first, sorted, 7 total")
    check(len(rd.thumb_timestamps(60.0, count=1)) == 1, "thumbs: count honored")
    check(all(0.05 <= t <= 0.15 for t in
              rd.thumb_timestamps(0.2, count=3)), "thumbs: clamped to duration")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out = tmp / "out"
        (out / "B0AAAA0001").mkdir(parents=True)
        (out / "manifest.csv").write_text(
            "asin,duration_s,words,clean,captions_mp4,srt,processed_at\n"
            "B0AAAA0001,60.0,20,B0AAAA0001/missing_clean.mp4,"
            "B0AAAA0001/cap.mp4,,2026-10-25T10:00:00\n", encoding="utf-8-sig")
        (out / "B0AAAA0001" / "cap.mp4").write_bytes(b"\x00\x01\x02")
        (out / "B0AAAA0001" / "B0AAAA0001_yt.mp4").write_bytes(b"\x00\x01\x02")

        print("== upload queue ==")
        e1 = rd.queue_add("B0AAAA0001", "B0AAAA0001/cap.mp4",
                          platform="storefront", out_root=out)
        check(e1["state"] == "pending" and e1["id"], "queue: added pending")
        rd.queue_add("B0AAAA0001", "B0AAAA0001/cap.mp4",
                     platform="storefront", out_root=out)
        check(len(rd.queue_load(out)["entries"]) == 1, "queue: dedupe")
        e1b = rd.queue_set(e1["id"], "uploaded", out_root=out)
        check(e1b["state"] == "uploaded" and e1b["updated_at"],
              "queue: state change stamps time")
        try:
            rd.queue_set(e1["id"], "bogus", out_root=out)
            check(False, "queue: invalid state raises")
        except ValueError:
            check(True, "queue: invalid state raises")
        try:
            rd.queue_set("nope", "uploaded", out_root=out)
            check(False, "queue: unknown id raises")
        except KeyError:
            check(True, "queue: unknown id raises")

        print("== queue seed ==")
        seed = rd.queue_seed(out_root=out)
        plats = sorted(e["platform"] for e in seed["queue"]["entries"])
        check(plats == ["storefront", "youtube_shorts"],
              "seed: captions master + _yt render tracked, missing clean skipped")
        seed2 = rd.queue_seed(out_root=out)
        check(seed2["added"] == 0, "seed: re-scan adds nothing")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not (ffmpeg and ffprobe):
        print("\n(ffmpeg not on PATH - skipping real-render block)")
        print(f"\n{PASS} passed, {FAIL} failed")
        sys.exit(1 if FAIL else 0)

    print("== real ffmpeg render + thumbnails ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out = tmp / "out"
        (out / "B0TEST0001").mkdir(parents=True)
        (out / "manifest.csv").write_text(
            "asin,duration_s,words,clean,captions_mp4,srt,processed_at\n"
            "B0TEST0001,3.0,0,B0TEST0001/c.mp4,"
            "B0TEST0001/B0TEST0001_captions.mp4,,2026-10-25T10:00:00\n",
            encoding="utf-8-sig")
        cap = out / "B0TEST0001" / "B0TEST0001_captions.mp4"
        r = rd.ef.run([ffmpeg, "-hide_banner", "-nostats", "-y",
                       "-f", "lavfi", "-i", "color=c=red:s=1080x1920:d=3:r=30",
                       "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                       "-shortest", "-pix_fmt", "yuv420p", "-t", "3", str(cap)])
        check(r.returncode == 0 and cap.exists(), "fixture: synthetic clip made")

        cfg = {"ffmpeg": ffmpeg, "ffprobe": ffprobe,
               "export": {"crf": 30, "preset": "veryfast"}}
        res = rd.render_platform_variants("B0TEST0001",
                                          platforms=["youtube_shorts", "tiktok"],
                                          cfg=cfg, out_root=out)
        files = [Path(out / r_["file"]) for r_ in res["renders"]]
        check(len(files) == 2 and all(f.exists() for f in files),
              "render: both platform files written")
        probe = rd.ef.run([ffprobe, "-v", "error", "-show_entries",
                           "format=duration", "-of", "csv=p=0",
                           str(files[0])])
        check(abs(float(probe.stdout.strip()) - 3.0) < 0.5,
              "render: duration preserved (no trim under limit)")
        entries = rd.queue_load(out)["entries"]
        check(len(entries) == 2 and all(e["state"] == "pending"
                                        for e in entries),
              "render: results auto-queued as pending")

        thumbs = rd.extract_thumbnails("B0TEST0001", cfg=cfg, count=3,
                                       out_root=out)
        tdir = out / "B0TEST0001" / "thumbs"
        check(len(thumbs["written"]) == 3
              and all((out / w).exists() for w in thumbs["written"]),
              "thumbs: three stills extracted")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
