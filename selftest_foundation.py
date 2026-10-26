#!/usr/bin/env python3
"""
Self-test for the foundation modules (campaigns.py + oink_data.py).

Run:  python selftest_foundation.py      (any Python 3.10+, no dependencies)

Builds synthetic fixtures inside a temp dir (never touches your real
archive/inbox/output) and asserts the behavior of:
  - campaign paste parsing / ASIN normalization
  - earnings CSV flexible column mapping + normalization roundtrip
  - manifest <-> earnings ROI join
  - campaign gap analysis states (ready / footage / missing / submitted)
  - film_next ranking + ready-to-submit list
  - update / delete / mark_submitted lifecycle

Exits 0 on success, 1 on any failure.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import campaigns as camp  # noqa: E402
import oink_data as oink  # noqa: E402

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


PASTE = """Pet Grooming Kit Campaign
Brand: FurryFriends Co
Deadline: 2026-12-01
Please feature the following items:

- Mention the self-cleaning brush
- Must show the kit on camera
B0AAAA0001 - brush kit
B0BBBB0002
"""

CSV_TEXT = """Order Date,Product Name,ASIN,Commission,Status
11/01/2026,Kettle B0AAAA0001,B0AAAA0001,$12.50,Shipped
11/03/2026,Yoga mat,B0BBBB0002,3.25,Refunded
bad row no asin
"""


def main() -> None:
    print("== parse_campaign_paste ==")
    parsed = camp.parse_campaign_paste(PASTE)
    check(parsed["name"] == "Pet Grooming Kit Campaign", "paste: name")
    check(parsed["brand"] == "FurryFriends Co", "paste: brand")
    check(parsed["asins"] == ["B0AAAA0001", "B0BBBB0002"], "paste: asins")
    check(parsed["deadline"] == "2026-12-01", "paste: deadline")
    check(any("self-cleaning" in r for r in parsed["requirements"]),
          "paste: requirements captured")

    print("== oink_data.parse_earnings_csv ==")
    ep = oink.parse_earnings_csv(CSV_TEXT)
    check(ep["columns"]["asin"] == "ASIN", "csv: asin column found")
    check(ep["columns"]["amount"] == "Commission", "csv: amount column found")
    check(ep["columns"]["date"] == "Order Date", "csv: date column found")
    check(len(ep["rows"]) == 2 and ep["skipped"] == 1, "csv: 2 rows, 1 skipped")
    check(ep["rows"][0]["date"] == "2026-11-01", "csv: date normalized to ISO")
    check(abs(ep["rows"][0]["amount"] - 12.5) < 1e-9, "csv: '$12.50' -> 12.50")

    print("== oink_data._to_float edges ==")
    check(oink._to_float("(5.00)") == -5.0, "to_float: parentheses = negative")
    check(oink._to_float("USD 7") == 7.0, "to_float: currency prefix")
    check(oink._to_float("n/a") is None, "to_float: junk -> None")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        imp = tmp / "imp"
        out_root = tmp / "out"
        out_root.mkdir()
        (out_root / "manifest.csv").write_text(
            "asin,duration_s,words,clean,captions_mp4,srt,processed_at\n"
            "B0AAAA0001,95.0,210,a,b,c,2026-10-25T10:00:00\n",
            encoding="utf-8-sig")

        print("== import_earnings_text roundtrip ==")
        res = oink.import_earnings_text(CSV_TEXT, "oink_export.csv", import_dir=imp)
        check(res["imported"] == 2, "import: 2 rows imported")
        check((imp / "earnings_normalized.csv").exists(), "import: normalized written")
        check(bool(res["raw_copy"]) and Path(res["raw_copy"]).exists(),
              "import: raw copy archived")
        agg = oink.earnings_by_asin(import_dir=imp)
        check(abs(agg["B0AAAA0001"]["total"] - 12.5) < 1e-9, "agg: total per asin")
        check(agg["B0BBBB0002"]["orders"] == 1, "agg: order count")

        print("== import_earnings_path (folder scan) ==")
        with tempfile.TemporaryDirectory() as td2:
            d2 = Path(td2)
            (d2 / "oink_export.csv").write_text(CSV_TEXT, encoding="utf-8-sig")
            res2 = oink.import_earnings_path(d2, import_dir=d2 / "imp")
            check(res2["imported"] == 2, "path import: newest csv picked up")

        print("== roi_report ==")
        roi = oink.roi_report(out_root=out_root, import_dir=imp)
        top = roi["rows"][0]
        check(top["asin"] == "B0AAAA0001", "roi: earning asin ranked first")
        check(top["earnings_total"] == 12.5, "roi: earnings joined")
        check(abs(top["earnings_per_min"] - 12.5 / (95 / 60)) < 0.01,
              "roi: earnings per minute math")
        check(roi["rows"][1]["videos"] == 0, "roi: asin with earnings but no video")
        nw = oink.earners_without_video(out_root=out_root, import_dir=imp)
        check([r["asin"] for r in nw] == ["B0BBBB0002"],
              "roi: earners_without_video")

        print("== campaign lifecycle ==")
        store = tmp / "campaigns.json"
        arch = tmp / "archive"
        arch.mkdir()
        (arch / "B0BBBB0002_review.mp4").write_bytes(b"x")

        c = camp.add_campaign(name="CC Pet", brand="FurryFriends",
                              asins=["b0aaaa0001 junk", "B0BBBB0002"],
                              deadline="2026-12-01", path=store, source="paste")
        check(c["id"].startswith("c_"), "campaign: id format")
        check(c["asins"] == ["B0AAAA0001", "B0BBBB0002"],
              "campaign: asins normalized (case + junk stripped)")

        st = camp.campaign_status(c["id"], path=store, out_root=out_root,
                                  extra_dirs=[arch])
        states = {a["asin"]: a["state"] for a in st["asins"]}
        check(states["B0AAAA0001"] == "ready", "gap: manifest row -> ready")
        check(states["B0BBBB0002"] == "footage", "gap: archive clip -> footage")
        check(st["days_left"] is not None and st["days_left"] > 30,
              "gap: days_left computed")

        fn = camp.film_next(path=store, out_root=out_root, import_dir=imp)
        check("B0BBBB0002" in [s["asin"] for s in fn["suggestions"]],
              "film_next: footage+campaign asin suggested")
        check([r["asin"] for r in fn["ready_to_submit"]] == ["B0AAAA0001"],
              "film_next: ready-to-submit listed")

        camp.mark_submitted(c["id"], "b0aaaa0001",
                            file="B0AAAA0001_captions.mp4", path=store)
        st2 = camp.campaign_status(c["id"], path=store, out_root=out_root,
                                   extra_dirs=[arch])
        states2 = {a["asin"]: a["state"] for a in st2["asins"]}
        check(states2["B0AAAA0001"] == "submitted", "gap: mark_submitted")

        camp.update_campaign(c["id"], name="CC Pet Grooming", path=store)
        check(camp.get_campaign(c["id"], path=store)["name"] == "CC Pet Grooming",
              "campaign: update")
        camp.delete_campaign(c["id"], path=store)
        check(camp.list_campaigns(path=store) == [], "campaign: delete")

        try:
            camp.delete_campaign("nope", path=store)
            check(False, "campaign: delete missing raises")
        except KeyError:
            check(True, "campaign: delete missing raises")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
