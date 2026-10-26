#!/usr/bin/env python3
"""
Self-test for judgments.py (dual-backend judgment layer).

Run:  python selftest_judgments.py            # offline: heuristics + mocked API
      python selftest_judgments.py --live     # + one real API call if a key
                                              #   is configured (synthetic text)

Never prints or asserts on the API key itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import judgments as jd  # noqa: E402

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
    live = "--live" in sys.argv

    print("== config detection ==")
    check(jd.status({}) == {"configured": False, "backend": "local",
                            "model": None}, "status: unconfigured -> local")
    check(jd.ts_config({"typesafe": {"api_key": "", "enabled": True}}) is None,
          "ts_config: empty key -> None")
    check(jd.ts_config({"typesafe": {"api_key": "k", "enabled": False}}) is None,
          "ts_config: enabled false -> None")
    tsc = jd.ts_config({"typesafe": {"api_key": " k ", "timeout": "12"}})
    check(tsc == {"key": "k", "model": jd.DEFAULT_MODEL, "timeout": 12.0},
          "ts_config: trims key, defaults model, coerces timeout")

    print("== local heuristics (no backend configured) ==")
    r = jd.claims_risk("This cream cures eczema overnight.", cfg={})
    check(r == {"value": "high", "confidence": None, "backend": "local"},
          "risk local: medical claim -> high")
    r = jd.claims_risk("Great little kettle, boils fast and pours clean.",
                       cfg={})
    check(r["value"] == "low" and r["backend"] == "local",
          "risk local: plain praise -> low")
    r = jd.claims_risk("Doctor recommended and clinically proven.", cfg={})
    check(r["value"] == "medium" and r["backend"] == "local",
          "risk local: puffery -> medium")
    r = jd.hook_strength("", cfg={})
    check(r["value"] == 0.0 and r["backend"] == "local", "hook local: empty -> 0")
    r = jd.hook_strength("Stop scrolling - this is worth it.", cfg={})
    check(r["value"] >= 3.0 and r["backend"] == "local",
          "hook local: pattern interrupt -> >=3")
    r = jd.is_product_switch("okay, next product: the yoga mat",
                             prev_text="kettle talk", cfg={})
    check(r["value"] is True and r["backend"] == "local",
          "switch local: slate word -> True")
    r = jd.is_product_switch("it also has a nice handle",
                             prev_text="kettle talk", cfg={})
    check(r["value"] is False and r["backend"] == "local",
          "switch local: continuation -> False")
    r = jd.brief_supported("I love the self-cleaning brush on this kit.",
                           "Mention the self-cleaning brush", cfg={})
    check(r["value"] is True and r["backend"] == "local",
          "brief local: requirement covered -> True")
    r = jd.brief_supported("It boils water really quickly.",
                           "Mention the self-cleaning brush", cfg={})
    check(r["value"] is False and r["backend"] == "local",
          "brief local: requirement missing -> False")

    print("== typesafe backend via mocked transport ==")
    orig_post = jd._post
    try:
        cfg_ts = {"typesafe": {"api_key": "test-key-123"}}

        jd._post = lambda state, questions, tsc: {
            "risk_level": {"type": "choice", "choice": "medium",
                            "confidence": 0.8,
                            "probabilities": {"low": 0.1, "medium": 0.8,
                                              "high": 0.1}}}
        r = jd.claims_risk("some text", cfg=cfg_ts)
        check(r == {"value": "medium", "confidence": 0.8,
                    "backend": "typesafe"}, "risk: mocked choice parsed")
        check("test-key-123" not in json.dumps(r), "risk: no key in result")

        jd._post = lambda state, questions, tsc: {
            "switch": {"type": "noul", "noul": 0.93}}
        r = jd.is_product_switch("okay next one", cfg=cfg_ts)
        check(r == {"value": True, "confidence": 0.93, "backend": "typesafe"},
              "switch: mocked noul parsed")

        jd._post = lambda state, questions, tsc: {
            "hook": {"type": "score", "score": 1.7, "confidence": 0.55,
                     "legend": {}, "probabilities": {}}}
        r = jd.hook_strength("some hook", cfg=cfg_ts)
        check(r == {"value": 1.7, "confidence": 0.55, "backend": "typesafe"},
              "hook: mocked score parsed")

        jd._post = lambda state, questions, tsc: {
            "supported": {"type": "noul", "noul": 0.1}}
        r = jd.brief_supported("text", "req", cfg=cfg_ts)
        check(r == {"value": False, "confidence": 0.1, "backend": "typesafe"},
              "brief: mocked noul parsed")

        print("== graceful fallback on backend failure ==")
        def _boom(state, questions, tsc):
            raise OSError("network down")
        jd._post = _boom
        r = jd.claims_risk("Doctor recommended.", cfg=cfg_ts)
        check(r["value"] == "medium" and r["backend"] == "local",
              "risk: API failure -> local fallback")
        r = jd.is_product_switch("okay, next: the mat", cfg=cfg_ts)
        check(r["backend"] == "local", "switch: API failure -> local fallback")
    finally:
        jd._post = orig_post

    if live:
        print("== LIVE TypeSafe call (synthetic text only) ==")
        st = jd.status()
        print(f"  status: {st}")
        if st["configured"]:
            r = jd.claims_risk("This tea detoxes your liver and cures bloating.")
            print(f"  live result: {r}")
            check(r["backend"] == "typesafe", "live: typesafe backend used")
            check(r["value"] in ("low", "medium", "high"), "live: typed answer")
        else:
            print("  (no key configured - skipped)")

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
