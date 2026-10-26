#!/usr/bin/env python3
"""
RENDERS + HANDOFF - platform variants, thumbnails, upload queue
===============================================================
Turns the finished masters into per-platform posts and tracks what has
been uploaded where:

  PLATFORMS             render presets (YouTube Shorts, FB/IG Reels,
                        TikTok, Pinterest idea pin) - trim limits, suffixes
  render_platform_variants(asin, platforms) -> output/<ASIN>/<ASIN><suffix>.mp4
  extract_thumbnails(asin) -> output/<ASIN>/thumbs/*.jpg (evenly spread,
                        plus one frame at the first spoken hook moment)
  queue_add/list/set/seed - upload_queue.json state machine:
                        pending -> uploaded | rejected | dismissed

Sources prefer the captions master (ready-to-post) and fall back to the
clean master. Everything lands next to the masters so Oink's
cross-posting center / storefront upload finds them by name.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import edit_factory as ef
import oink_data
import transcript_intel as ti

ROOT = Path(__file__).resolve().parent

PLATFORMS = {
    "youtube_shorts": {
        "label": "YouTube Shorts", "suffix": "_yt", "max_duration_s": 60,
        "notes": "Hard-trimmed to 60s; vertical 1080x1920; upload via YouTube then link in Oink's cross-posting center."},
    "facebook_reels": {
        "label": "FB / Instagram Reels", "suffix": "_reels", "max_duration_s": 90,
        "notes": "Trimmed to 90s; keep captions clear of the bottom-third UI."},
    "tiktok": {
        "label": "TikTok", "suffix": "_tiktok", "max_duration_s": 600,
        "notes": "Up to 10 min allowed; only trims if the master is longer."},
    "pinterest": {
        "label": "Pinterest idea pin", "suffix": "_pin", "max_duration_s": 300,
        "notes": "Trimmed to 5 min; idea pins favor 15-60s."},
}
_SUFFIX_TO_PLATFORM = {p["suffix"]: key for key, p in PLATFORMS.items()}
_QUEUE_STATES = ("pending", "uploaded", "rejected", "dismissed")


def _out(out_root=None) -> Path:
    return Path(out_root) if out_root else ef.output_dir(ef.load_config())


def _cfg(cfg=None):
    return cfg if cfg is not None else ef.load_config()


def _queue_path(out_root=None) -> Path:
    return _out(out_root) / "upload_queue.json"


# ------------------------------------------------------------------ presets

def plan_render(source_duration: float, preset: dict) -> dict:
    """Trim decision for one preset (pure function, easy to test)."""
    max_d = float(preset["max_duration_s"])
    trim = source_duration > max_d + 0.5
    return {"trim": trim,
            "duration": round(min(source_duration, max_d), 2) if trim
            else round(source_duration, 2)}


def _master_for(asin: str, out_root=None) -> tuple[Path, dict]:
    """(path, manifest_row) for the best source master of this ASIN."""
    out = _out(out_root)
    row = ti._latest_rows(out).get(asin.upper())
    if not row:
        raise KeyError(f"No manifest rows for {asin}")
    for key in ("captions_mp4", "clean"):
        rel = (row.get(key) or "").strip()
        if rel and (out / rel).exists():
            return out / rel, row
    raise FileNotFoundError(f"{asin} has no captions or clean master on disk")


# ------------------------------------------------------------------ render

def render_platform_variants(asin: str, platforms=None, cfg=None,
                             out_root=None) -> dict:
    """Render <ASIN><suffix>.mp4 for each platform; auto-queues each result."""
    c = _cfg(cfg)
    out = _out(out_root)
    src, row = _master_for(asin, out_root)
    dest_dir = src.parent
    duration = ef.duration_of(ef.probe(src, c["ffprobe"]))
    platforms = list(platforms) if platforms else list(PLATFORMS)
    unknown = [p for p in platforms if p not in PLATFORMS]
    if unknown:
        raise ValueError(f"Unknown platform(s): {', '.join(unknown)} - "
                         f"valid: {', '.join(PLATFORMS)}")

    renders = []
    for p in platforms:
        preset = PLATFORMS[p]
        plan = plan_render(duration, preset)
        dest = dest_dir / f"{asin.upper()}{preset['suffix']}.mp4"
        cmd = [c["ffmpeg"], "-hide_banner", "-nostats", "-y", "-i", str(src)]
        if plan["trim"]:
            cmd += ["-t", f"{plan['duration']:.2f}"]
        cmd += ["-c:v", "libx264", "-crf", str(c["export"]["crf"]),
                "-preset", c["export"]["preset"], "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                str(dest)]
        r = ef.run(cmd)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg render failed for {p}: {r.stderr[-400:]}")
        rel = str(dest.relative_to(out))
        renders.append({"platform": p, "label": preset["label"],
                        "file": rel, "trimmed": plan["trim"],
                        "duration_s": plan["duration"]})
        queue_add(asin.upper(), rel, platform=p, out_root=out)
    return {"asin": asin.upper(), "source": str(src.relative_to(out)),
            "source_duration_s": round(duration, 1), "renders": renders}


# --------------------------------------------------------------- thumbnails

def thumb_timestamps(duration: float, count: int = 6,
                     hook_t: float | None = None) -> list[float]:
    """Evenly spread candidate timestamps, hook frame first (pure function)."""
    fracs = [0.12, 0.28, 0.44, 0.60, 0.76, 0.92, 0.5, 0.2, 0.36, 0.68, 0.84]
    ts = [round(min(max(duration * f, 0.05), max(duration - 0.05, 0.05)), 2)
          for f in fracs[:max(1, count)]]
    if hook_t is not None:
        ts.insert(0, round(min(max(hook_t + 0.5, 0.05),
                               max(duration - 0.05, 0.05)), 2))
    return sorted(set(ts))


def extract_thumbnails(asin: str, cfg=None, count: int = 6,
                       out_root=None) -> dict:
    """Grab stills from the captions master into output/<ASIN>/thumbs/."""
    c = _cfg(cfg)
    out = _out(out_root)
    src, _row = _master_for(asin, out_root)
    duration = ef.duration_of(ef.probe(src, c["ffprobe"]))

    hook_t = None
    tr = ti.transcript_for(asin, out)
    if tr and tr["events"]:
        hook_t = tr["events"][0][0]

    tdir = src.parent / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    written = []
    stamps = thumb_timestamps(duration, count=count, hook_t=hook_t)
    for i, t in enumerate(stamps):
        name = "thumb_hook.jpg" if (hook_t is not None and i == 0) \
            else f"thumb_{i:02d}.jpg"
        dest = tdir / name
        cmd = [c["ffmpeg"], "-hide_banner", "-nostats", "-y",
               "-ss", f"{t:.2f}", "-i", str(src),
               "-frames:v", "1", "-q:v", "2", str(dest)]
        r = ef.run(cmd)
        if r.returncode == 0 and dest.exists():
            written.append(str(dest.relative_to(out)))
    return {"asin": asin.upper(), "written": written,
            "timestamps": stamps, "hook_t": hook_t}


# ------------------------------------------------------------- upload queue

def queue_load(out_root=None) -> dict:
    p = _queue_path(out_root)
    if not p.exists():
        return {"entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt queue should not kill the UI
        return {"entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"entries": []}
    return data


def queue_save(data: dict, out_root=None) -> None:
    _queue_path(out_root).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def queue_add(asin: str, file: str, platform: str = "storefront",
              out_root=None) -> dict:
    """Add a pending entry; no-op if that file+platform already tracked."""
    data = queue_load(out_root)
    for e in data["entries"]:
        if e["file"] == file and e["platform"] == platform:
            return e
    entry = {"id": uuid.uuid4().hex[:8], "asin": asin.upper(),
             "file": file, "platform": platform, "state": "pending",
             "added_at": datetime.now().isoformat(timespec="seconds"),
             "updated_at": "", "note": ""}
    data["entries"].append(entry)
    queue_save(data, out_root)
    return entry


def queue_set(entry_id: str, state: str, note: str | None = None,
              out_root=None) -> dict:
    if state not in _QUEUE_STATES:
        raise ValueError(f"state must be one of {', '.join(_QUEUE_STATES)}")
    data = queue_load(out_root)
    for e in data["entries"]:
        if e["id"] == entry_id:
            e["state"] = state
            e["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if note is not None:
                e["note"] = note
            queue_save(data, out_root)
            return e
    raise KeyError(entry_id)


def queue_remove(entry_id: str, out_root=None) -> None:
    data = queue_load(out_root)
    n = len(data["entries"])
    data["entries"] = [e for e in data["entries"] if e["id"] != entry_id]
    if len(data["entries"]) == n:
        raise KeyError(entry_id)
    queue_save(data, out_root)


def queue_seed(out_root=None) -> dict:
    """Scan the manifest + existing platform renders for untracked files."""
    out = _out(out_root)
    rows = ti._latest_rows(out)
    existing = {(e["file"], e["platform"]) for e in queue_load(out)["entries"]}
    added = 0
    for asin, row in rows.items():
        adir = None
        for key, platform in (("captions_mp4", "storefront"), ("clean", "master")):
            rel = (row.get(key) or "").strip()
            if rel and (out / rel).exists():
                adir = (out / rel).parent
                if (rel, platform) not in existing:
                    queue_add(asin, rel, platform=platform, out_root=out)
                    existing.add((rel, platform))
                    added += 1
        if adir and adir.exists():
            for f in adir.glob(f"{asin}*.mp4"):
                suffix = f.stem[len(asin):]
                platform = _SUFFIX_TO_PLATFORM.get(suffix)
                if platform and (str(f.relative_to(out)), platform) not in existing:
                    queue_add(asin, str(f.relative_to(out)), platform=platform,
                              out_root=out)
                    existing.add((str(f.relative_to(out)), platform))
                    added += 1
    return {"added": added, "queue": queue_load(out)}
