#!/usr/bin/env python3
"""
TRANSCRIPT INTEL - everything Edit Factory knows from your own voiceover
========================================================================
Reads the SRTs the pipeline already wrote (output/<ASIN>/<ASIN>.srt; the
latest manifest row per ASIN wins, since later runs overwrite the masters)
and produces:

  hook_audit()           first spoken words -> hook strength (0-4)
  compliance_scan()      spoken-claims risk + flagged words w/ timestamps
  broll_plan()           no-speech windows in the clean master
  extract_broll()        cut those windows into reusable b-roll clips
  unsorted_rescue()      match UNSORTED transcripts to product_codes /
                         campaign ASINs (TypeSafe noul, local fallback)
  mention_mine()         per-ASIN "everything I said about it" index
                         -> output/mentions.json
  campaign_compliance()  pre-upload check: are the brand's requirements
                         covered, and are there risky claims?

Read-only except b-roll extraction and mentions.json. Local heuristics by
default; TypeSafe judgments are used when configured (see judgments.py).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import edit_factory as ef
import judgments as jd
import oink_data
import campaigns

ROOT = Path(__file__).resolve().parent
_TS_RE = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _out(out_root=None) -> Path:
    return Path(out_root) if out_root else ef.output_dir(ef.load_config())


def _cfg(cfg=None):
    return cfg if cfg is not None else ef.load_config()


# ------------------------------------------------------------------ parsing

def _read_srt(path: Path) -> list[tuple[float, float, str]]:
    """Parse an SRT into [(start, end, text)] - matches edit_factory output."""
    try:
        raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    events = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        ts_idx = next((i for i, ln in enumerate(lines) if _TS_RE.search(ln)), None)
        if ts_idx is None:
            continue
        g = [int(x) for x in _TS_RE.search(lines[ts_idx]).groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(ln.strip() for ln in lines[ts_idx + 1:])
        if text:
            events.append((start, end, text))
    return events


def _latest_rows(out_root: Path) -> dict[str, dict]:
    """asin -> newest manifest row (later runs overwrite the masters)."""
    rows: dict[str, dict] = {}
    for r in oink_data.read_manifest(out_root):
        asin = (r.get("asin") or "").strip().upper()
        at = r.get("processed_at") or ""
        if asin and at >= (rows.get(asin, {}).get("processed_at") or ""):
            rows[asin] = r
    return rows


def transcript_for(asin: str, out_root=None) -> dict | None:
    """Latest transcript bundle for one ASIN (or UNSORTED)."""
    out = _out(out_root)
    row = _latest_rows(out).get(asin.upper())
    if not row:
        return None
    events: list[tuple[float, float, str]] = []
    srt_rel = (row.get("srt") or "").strip()
    if srt_rel and (out / srt_rel).exists():
        events = _read_srt(out / srt_rel)
    return {"row": row, "events": events,
            "text": " ".join(t for _, _, t in events),
            "clean": (row.get("clean") or "").strip()}


# ---------------------------------------------------------------- hook audit

def hook_audit(out_root=None, cfg=None, asin: str | None = None) -> dict:
    """How strong is the opening of each video's speech?"""
    out = _out(out_root)
    targets = [asin.upper()] if asin else sorted(_latest_rows(out))
    hooks = {}
    for a in targets:
        tr = transcript_for(a, out)
        if not tr or not tr["events"]:
            continue
        opening: list[str] = []
        first_start = tr["events"][0][0]
        for s, _e, t in tr["events"]:
            if s <= first_start + 4.0 and len(" ".join(opening).split()) < 30:
                opening.append(t)
            else:
                break
        if opening:
            hooks[a] = {"opening": " ".join(opening),
                        **jd.hook_strength(" ".join(opening), cfg)}
    return {"hooks": hooks}


# --------------------------------------------------------- compliance scan

def compliance_scan(out_root=None, cfg=None, asin: str | None = None) -> dict:
    """Spoken-claims risk per ASIN, with flagged words and timestamps."""
    out = _out(out_root)
    targets = [asin.upper()] if asin else sorted(_latest_rows(out))
    results = {}
    for a in targets:
        tr = transcript_for(a, out)
        if not tr or not tr["text"]:
            continue
        risk = jd.claims_risk(tr["text"], cfg)
        flags = []
        for s, _e, t in tr["events"]:
            tl = f" {re.sub(r'[^a-z0-9]+', ' ', t.lower())} "
            for w in getattr(jd, "_HIGH_WORDS", []):
                if re.search(rf"\b{re.escape(w)}\b", tl):
                    flags.append({"t": round(s, 1), "word": w, "level": "high"})
            for w in getattr(jd, "_MED_WORDS", []):
                if re.search(rf"\b{re.escape(w)}\b", tl):
                    flags.append({"t": round(s, 1), "word": w, "level": "medium"})
        results[a] = {**risk, "flags": flags,
                      "words": len(tr["text"].split())}
    return {"scan": results}


# ------------------------------------------------------------------- b-roll

def broll_plan(asin: str, out_root=None, min_len: float = 1.5,
               duration: float | None = None) -> dict:
    """No-speech windows in the clean master (>= min_len seconds)."""
    tr = transcript_for(asin, _out(out_root))
    if not tr:
        raise KeyError(f"No manifest rows for {asin}")
    clean = tr["clean"]
    if not clean:
        raise FileNotFoundError(f"{asin} has no clean master yet")
    if duration is None:
        c = _cfg()
        clean_path = _out(out_root) / clean
        if not clean_path.exists():
            raise FileNotFoundError(f"Missing clean master: {clean_path}")
        duration = ef.duration_of(ef.probe(clean_path, c["ffprobe"]))
    busy = sorted((max(0.0, s - 0.3), min(duration, e + 0.3))
                  for s, e, _t in tr["events"])
    windows, t = [], 0.0
    for a, b in busy:
        if a - t >= min_len:
            windows.append((round(t, 2), round(a, 2)))
        t = max(t, b)
    if duration - t >= min_len:
        windows.append((round(t, 2), round(duration, 2)))
    return {"asin": asin.upper(), "clean": clean, "duration_s": round(duration, 1),
            "windows": windows, "total_broll_s": round(sum(b - a for a, b in windows), 1)}


def extract_broll(asin: str, cfg=None, min_len: float = 1.5, max_clips: int = 6,
                  out_root=None) -> dict:
    """Cut the planned windows into output/<ASIN>/broll/ clips (re-encoded)."""
    c = _cfg(cfg)
    out = _out(out_root)
    plan = broll_plan(asin, out_root=out_root, min_len=min_len)
    bdir = out / plan["asin"] / "broll"
    bdir.mkdir(parents=True, exist_ok=True)
    clean = out / plan["clean"]
    written = []
    for i, (a, b) in enumerate(plan["windows"][:max_clips], 1):
        dest = bdir / f"{plan['asin']}_broll_{i:02d}.mp4"
        cmd = [c["ffmpeg"], "-hide_banner", "-nostats", "-y",
               "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(clean),
               "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
               str(dest)]
        r = ef.run(cmd)
        if r.returncode == 0:
            written.append(str(dest.relative_to(out)))
    return {"asin": plan["asin"], "written": written,
            "planned": len(plan["windows"])}


# ---------------------------------------------------------- UNSORTED rescue

def unsorted_rescue(out_root=None, path=None, cfg=None) -> dict:
    """Suggest which product an UNSORTED clip is actually about."""
    out = _out(out_root)
    c = _cfg(cfg)
    tr = transcript_for("UNSORTED", out)
    if not tr or not tr["text"]:
        return {"candidates": [], "checked": 0,
                "note": "No UNSORTED transcript found - process clips with speech first."}
    text = tr["text"][:4000]

    candidates: list[dict] = []
    for nick, asin in (c.get("product_codes") or {}).items():
        candidates.append({"kind": "nickname", "label": nick, "asin": asin})
    for camp in campaigns.list_campaigns(path):
        for asin in camp.get("asins", []):
            label = f"{camp.get('name', '')} ({camp.get('brand', '')})".strip(" ()")
            candidates.append({"kind": "campaign", "label": label, "asin": asin})
    seen, deduped = set(), []
    for cand in candidates:
        if cand["asin"] not in seen:
            seen.add(cand["asin"])
            deduped.append(cand)
    candidates = deduped
    if not candidates:
        return {"candidates": [], "checked": 0,
                "note": "No candidates - add product_codes or campaigns."}

    results: list[dict] = []
    tsc = jd.ts_config(cfg)
    if tsc:
        questions = {}
        for i, cand in enumerate(candidates):
            questions[f"match_{i}"] = {
                "type": "noul",
                "instructions": {
                    "question": ("Is this shoppable-video transcript about "
                                 "the product described in `candidate`?"),
                    "candidate": {"label": cand["label"],
                                  "asin": cand["asin"]}}}
        try:
            answers = jd.ask(text, questions, cfg)
            for i, cand in enumerate(candidates):
                a = answers.get(f"match_{i}") or {}
                if a.get("type") == "noul" and isinstance(a.get("noul"), (int, float)):
                    results.append({**cand, "probability": a["noul"]})
        except Exception:  # noqa: BLE001 - fall through to local
            results = []

    if not results:
        tl = f" {re.sub(r'[^a-z0-9]+', ' ', text.lower())} "
        for cand in candidates:
            words = [w for w in re.findall(r"[a-z0-9]+", cand["label"].lower())
                     if len(w) > 2]
            if not words:
                continue
            hits = sum(1 for w in words if f" {w} " in tl)
            results.append({**cand, "probability": round(hits / len(words), 2)})

    results.sort(key=lambda r: -r.get("probability", 0))
    return {"candidates": [r for r in results if r.get("probability", 0) >= 0.3][:8],
            "checked": len(candidates)}


# ------------------------------------------------------------ mention index

def mention_mine(out_root=None) -> dict:
    """Write output/mentions.json: what was said about every ASIN, ever."""
    out = _out(out_root)
    index = {}
    for asin in sorted(_latest_rows(out)):
        tr = transcript_for(asin, out)
        if not tr:
            continue
        index[asin] = {
            "has_transcript": bool(tr["events"]),
            "word_count": len(tr["text"].split()),
            "text": tr["text"][:2000],
            "clean": tr["clean"],
            "srt": (tr["row"].get("srt") or "").strip(),
        }
    data = {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "asins": index}
    out.mkdir(parents=True, exist_ok=True)
    (out / "mentions.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


# --------------------------------------------------- campaign pre-upload

def campaign_compliance(cid: str, path=None, out_root=None, cfg=None) -> dict:
    """Pre-upload check for one campaign: requirements covered? risky claims?

    Only ASINs with an export ('ready'/'submitted') can be checked; others
    are reported with their gap state so the UI can show what's missing.
    """
    camp = campaigns.get_campaign(cid, path)
    st = campaigns.campaign_status(cid, path=path, out_root=out_root)
    out = _out(out_root)
    per = []
    for a in st["asins"]:
        if a["state"] in ("missing", "footage"):
            per.append({"asin": a["asin"], "state": a["state"]})
            continue
        tr = transcript_for(a["asin"], out)
        text = tr["text"] if tr else ""
        reqs = [{"requirement": req,
                 **jd.brief_supported(text, req, cfg)}
                for req in camp.get("requirements", [])]
        risk = jd.claims_risk(text, cfg) if text.strip() else \
            {"value": "low", "confidence": None, "backend": "local"}
        per.append({
            "asin": a["asin"], "state": a["state"],
            "requirements": reqs, "claims_risk": risk,
            "ok": bool(reqs) and all(r["value"] for r in reqs)
                  and risk["value"] != "high",
            "checked": bool(text.strip()),
        })
    return {"campaign_id": cid, "campaign": camp.get("name", ""), "asins": per}
