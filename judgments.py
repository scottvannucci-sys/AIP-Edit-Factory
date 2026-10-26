#!/usr/bin/env python3
"""
JUDGMENTS - small semantic decisions with a dual backend
========================================================
Feature code asks plain questions ("is this claim risky?", "does the script
cover the brand requirement?") and gets a typed answer plus a probability.

Two backends, chosen per call:
  1. TypeSafe System One (config.json -> "typesafe") when an api_key is
     configured and enabled is not false. One HTTP call, stdlib only.
  2. Local keyword heuristics otherwise - and automatically on ANY
     TypeSafe failure (offline, bad key, rate limit, timeout).

Every public function returns {"value": ..., "confidence": float|None,
"backend": "typesafe"|"local"}. The API key is never logged or returned.

Privacy: when the TypeSafe backend is on, only the small text span needed
for the question is sent (a sentence, a script section) - never files,
paths, or manifests. Set "enabled": false in the typesafe block to force
the 100%-local mode.
"""
from __future__ import annotations

import json
import re
import urllib.request

import edit_factory as ef

TS_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


# ------------------------------------------------------------------ config

def _cfg(cfg):
    return cfg if cfg is not None else ef.load_config()


def ts_config(cfg=None) -> dict | None:
    """Parsed TypeSafe settings, or None when unconfigured/disabled."""
    ts = (_cfg(cfg).get("typesafe") or {})
    key = str(ts.get("api_key") or "").strip()
    if not key or ts.get("enabled") is False:
        return None
    try:
        timeout = float(ts.get("timeout") or 25)
    except (TypeError, ValueError):
        timeout = 25.0
    return {"key": key, "model": str(ts.get("model") or DEFAULT_MODEL),
            "timeout": timeout}


def status(cfg=None) -> dict:
    """Safe status for the UI - contains no key material."""
    tsc = ts_config(cfg)
    return {"configured": tsc is not None,
            "backend": "typesafe" if tsc else "local",
            "model": tsc["model"] if tsc else None}


def _post(state, questions: dict, tsc: dict) -> dict:
    """One System One request -> answers dict. Raises on any failure."""
    body = json.dumps({"model": tsc["model"], "state": state,
                       "questions": questions}).encode("utf-8")
    req = urllib.request.Request(
        TS_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {tsc['key']}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=tsc["timeout"]) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("answers") or {}


def ask(state, questions: dict, cfg=None) -> dict:
    """Raw passthrough for other modules (transcript_intel, studio).

    Returns the TypeSafe `answers` dict; raises if TypeSafe is not
    configured or the request fails. Callers own their fallbacks.
    """
    tsc = ts_config(cfg)
    if not tsc:
        raise RuntimeError("TypeSafe is not configured")
    return _post(state, questions, tsc)


# ------------------------------------------------------- claims risk check

_RISK_CRITERIA = {
    "low": "Everyday product praise with no health, safety, or absolute claims.",
    "medium": ("Exaggerated or comparative marketing claims (best, #1, "
               "clinically proven, doctor recommended) that may need "
               "substantiation."),
    "high": ("Medical or regulatory claims (cures, treats, heals, diagnoses, "
             "FDA approval) or guaranteed outcomes - risky to publish."),
}
_HIGH_WORDS = ["cure", "cures", "treat", "treats", "heal", "heals", "healing",
               "diagnose", "diagnoses", "fda", "miracle", "cancer",
               "guaranteed results"]
_MED_WORDS = ["clinically proven", "doctor recommended", "number one",
              "pain relief", "weight loss", "antibacterial", "hypoallergenic",
              "anti aging", "boosts immune", "detox", "best"]


def _local_risk(text: str) -> str:
    t = f" {re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower())} "
    if any(re.search(rf"\b{re.escape(w)}\b", t) for w in _HIGH_WORDS):
        return "high"
    if any(f" {w} " in t for w in _MED_WORDS):
        return "medium"
    return "low"


def claims_risk(text: str, cfg=None) -> dict:
    """How risky is this text to publish? -> low | medium | high."""
    c = _cfg(cfg)
    tsc = ts_config(c)
    if tsc:
        try:
            answers = _post(str(text or ""), {
                "risk_level": {"type": "choice",
                               "instructions": ("How risky is this "
                                 "product-review text to publish on Amazon?"),
                               "criteria": _RISK_CRITERIA}}, tsc)
            ans = answers.get("risk_level") or {}
            if ans.get("type") == "choice" and ans.get("choice") in _RISK_CRITERIA:
                return {"value": ans["choice"],
                        "confidence": ans.get("confidence"),
                        "backend": "typesafe"}
        except Exception:  # noqa: BLE001 - any failure -> local fallback
            pass
    return {"value": _local_risk(text), "confidence": None, "backend": "local"}


# ------------------------------------------------- product switch (noul)

def is_product_switch(sentence: str, prev_text: str = "", cfg=None) -> dict:
    """Does this sentence announce a transition to a DIFFERENT product?"""
    c = _cfg(cfg)
    tsc = ts_config(c)
    if tsc:
        try:
            state = {"sentence": str(sentence or ""),
                     "preceding_text": str(prev_text or "")[:600]}
            answers = _post(state, {
                "switch": {
                    "type": "noul",
                    "instructions": ("Does `sentence` announce a transition "
                                     "to a DIFFERENT product being reviewed "
                                     "next, rather than continuing about "
                                     "the same item?"),
                    "criteria": {
                        "true": "A transition introducing a new product.",
                        "false": "Still about the same product, or not a "
                                 "transition."}}}, tsc)
            ans = answers.get("switch") or {}
            if ans.get("type") == "noul" and isinstance(ans.get("noul"),
                                                        (int, float)):
                return {"value": bool(ans["noul"] >= 0.7),
                        "confidence": ans["noul"], "backend": "typesafe"}
        except Exception:  # noqa: BLE001
            pass
    slate = set(c.get("slate_words")
                or ef.DEFAULT_CONFIG.get("slate_words") or [])
    words = re.findall(r"[a-z']+", str(sentence or "").lower())
    return {"value": bool(words and words[0] in slate),
            "confidence": None, "backend": "local"}


# ------------------------------------------- brand requirement matching

def brief_supported(text: str, requirement: str, cfg=None) -> dict:
    """Does `text` satisfy one brand/campaign requirement?"""
    c = _cfg(cfg)
    tsc = ts_config(c)
    if tsc:
        try:
            state = {"text": str(text or "")[:4000]}
            answers = _post(state, {
                "supported": {
                    "type": "noul",
                    "instructions": {
                        "question": ("Does the text satisfy this brand "
                                     "requirement?"),
                        "requirement": str(requirement or "")},
                    "criteria": {
                        "true": "The requirement is clearly satisfied.",
                        "false": "The requirement is missing or only "
                                 "vaguely touched."}}}, tsc)
            ans = answers.get("supported") or {}
            if ans.get("type") == "noul" and isinstance(ans.get("noul"),
                                                        (int, float)):
                return {"value": bool(ans["noul"] >= 0.7),
                        "confidence": ans["noul"], "backend": "typesafe"}
        except Exception:  # noqa: BLE001
            pass
    req_words = set(re.findall(r"[a-z0-9]+", str(requirement or "").lower()))
    req_words -= {"the", "a", "an", "and", "or", "of", "to", "in", "on",
                  "with", "for", "must", "be", "is", "it", "mention",
                  "show", "include", "talk", "about"}
    text_l = f" {re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower())} "
    if not req_words:
        return {"value": True, "confidence": None, "backend": "local"}
    hits = sum(1 for w in req_words if f" {w} " in text_l)
    return {"value": hits / len(req_words) >= 0.6,
            "confidence": None, "backend": "local"}


# --------------------------------------------------------- hook strength

_HOOK_LEVELS = [
    "No hook - starts with filler or housekeeping.",
    "Weak - states the product but gives no reason to keep watching.",
    "Decent - names the product and one concrete benefit.",
    ("Strong - opens with a question, bold claim, story, or pattern "
     "interrupt aimed at the viewer."),
    ("Exceptional - irresistible curiosity gap or bold contrarian claim "
     "in the first sentence."),
]


def _local_hook(text: str) -> float:
    t = str(text or "").strip().lower()
    if not t:
        return 0.0
    score = 1.0
    if t.startswith(("have you", "did you", "why ", "what ", "can we",
                     "stop scrolling", "okay, have you")):
        score = 3.0
    if any(p in t for p in ("stop scrolling", "wait for it", "plot twist",
                            "nobody tells you", "thank me later")):
        score = 3.0
    if len(t.split()) <= 4:
        score = max(score, 2.0)
    return score


def hook_strength(text: str, cfg=None) -> dict:
    """0-4 expected score for how strong the opening hook is."""
    c = _cfg(cfg)
    tsc = ts_config(c)
    if tsc:
        try:
            answers = _post(str(text or "")[:1000], {
                "hook": {"type": "score",
                         "instructions": ("Rate the opening hook of this "
                                          "shoppable video script."),
                         "criteria": _HOOK_LEVELS}}, tsc)
            ans = answers.get("hook") or {}
            if ans.get("type") == "score" and isinstance(ans.get("score"),
                                                         (int, float)):
                return {"value": ans["score"],
                        "confidence": ans.get("confidence"),
                        "backend": "typesafe"}
        except Exception:  # noqa: BLE001
            pass
    return {"value": _local_hook(text), "confidence": None, "backend": "local"}
