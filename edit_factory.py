#!/usr/bin/env python3
"""
EDIT FACTORY - automated video pipeline for Amazon Influencers
==============================================================
Drop raw clips into `inbox/`, run this script, get back per-ASIN,
ready-to-upload vertical videos with burned-in captions, plus
CapCut-ready clean masters and SRT files.

What it does per clip:
  1. Transcribes speech (faster-whisper, local, no cloud)
  2. Detects which product (ASIN) each part of the footage belongs to
     - from the filename (e.g.  B0CKWGQY9Z_kettle.mp4)
     - or from spoken "slates" (say "okay, next product: the yoga mat")
     - or nicknames you define in config.json (product_codes)
  3. Cuts out silences / dead air and normalizes loudness (EBU R128)
  4. Groups all footage by ASIN, concatenates the takes in order
  5. Writes:
       output/<ASIN>/<ASIN>_clean.mp4      - edited, no captions (CapCut-ready)
       output/<ASIN>/<ASIN>_captions.mp4   - 1080x1920, captions burned in
       output/<ASIN>/<ASIN>.srt            - captions sidecar for CapCut
  6. Updates output/manifest.csv and writes a session report

Usage:
  python edit_factory.py process          # process everything in inbox/
  python edit_factory.py init             # (re)write default config.json
  python edit_factory.py stats            # show manifest summary
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts"}
ASIN_RE = re.compile(r"(?<![A-Z0-9])B0[A-Z0-9]{8}(?![A-Z0-9])")

DEFAULT_CONFIG = {
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffprobe",
    "whisper_model": "small",          # tiny | base | small  (small = best accuracy/speed on CPU)
    "whisper_language": "en",
    "silence": {
        "noise_db": -35,               # anything quieter than this counts as silence
        "min_silence": 0.45,           # silences shorter than this are kept (natural pauses)
        "min_gap": 0.60,               # merge kept segments separated by less than this
        "min_segment": 0.40,           # drop kept segments shorter than this (noise blips)
        "padding": 0.15,               # seconds kept around each cut point
        "skip_cut_threshold": 0.02     # if <2% would be removed, don't bother cutting
    },
    "audio": {"lufs": -16, "true_peak": -1.5, "lra": 11},
    "export": {
        "width": 1080, "height": 1920,
        "crf": 20, "preset": "medium",
        "reframe": "blur"              # horizontal footage: "blur" pad or "crop"
    },
    "captions": {
        "font": "Arial", "size": 60, "bold": True,
        "max_words": 7, "max_span": 3.0, "margin_v": 380,
        "outline": 3
    },
    "slate_words": ["next", "okay", "ok", "alright", "moving", "now", "up", "so", "and"],
    "product_codes": {
        "yoga mat": "B0YOGAMAT01",
        "phone stand": "B0PHNSTAND9"
    },
    "paths": {
        "inbox": "",    # empty = ./inbox ; may be a local folder or a UNC share (\\NAS\videos)
        "output": ""    # empty = ./output
    },
    "web": {"port": 8765},
    "archive_processed": True
}


# ---------------------------------------------------------------- utilities

LOG_SINK: list[str] = []   # tail of log lines, read by the web UI


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG_SINK.append(msg)
    del LOG_SINK[:-500]


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    # merge missing keys from defaults
    def merge(base, extra):
        for k, v in base.items():
            if k not in extra:
                extra[k] = v
            elif isinstance(v, dict) and isinstance(extra[k], dict):
                merge(v, extra[k])
    merge(DEFAULT_CONFIG, cfg)
    return cfg


def save_config(cfg: dict) -> None:
    (ROOT / "config.json").write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def probe(path: Path, ffprobe: str) -> dict:
    r = run([ffprobe, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {r.stderr[:400]}")
    return json.loads(r.stdout)


def has_audio(info: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def duration_of(info: dict) -> float:
    return float(info.get("format", {}).get("duration", 0) or 0)


# ------------------------------------------------------------ audio analysis

def detect_silences(path: Path, ffmpeg: str, cfg: dict) -> list[tuple[float, float]]:
    """Return [(start, end)] silence spans using ffmpeg silencedetect."""
    s = cfg["silence"]
    r = run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
             "-af", f"silencedetect=noise={s['noise_db']}dB:d={s['min_silence']}",
             "-f", "null", "-"])
    text = (r.stderr or "")
    silences, pending = [], None
    for m in re.finditer(r"silence_(start|end): ([0-9.]+)", text):
        kind, val = m.group(1), float(m.group(2))
        if kind == "start":
            pending = val
        elif pending is not None:
            silences.append((pending, val))
            pending = None
    return silences


def kept_segments(silences: list, dur: float, cfg: dict) -> list[tuple[float, float]]:
    """Complement of silences, padded, merged, and filtered."""
    s = cfg["silence"]
    keeps, t = [], 0.0
    for (a, b) in sorted(silences):
        if a - t > 0.01:
            keeps.append((t, a))
        t = max(t, b)
    if dur - t > 0.01:
        keeps.append((t, dur))
    # pad each side, clamp to neighbours
    padded = []
    for i, (a, b) in enumerate(keeps):
        a2 = max(0.0, a - s["padding"])
        b2 = min(dur, b + s["padding"])
        if i > 0:
            a2 = max(a2, keeps[i - 1][1])
        if i < len(keeps) - 1:
            b2 = min(b2, keeps[i + 1][0])
        padded.append((a2, b2))
    # merge segments separated by small gaps
    merged = []
    for a, b in padded:
        if merged and a - merged[-1][1] < s["min_gap"]:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    # drop tiny blips
    merged = [(a, b) for a, b in merged if b - a >= s["min_segment"]]
    if not merged:
        merged = [(0.0, dur)]
    return merged


# ------------------------------------------------------------------- whisper

_model = None
_model_failed = False


def get_model(cfg: dict):
    global _model, _model_failed
    if _model_failed:
        return None
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            log("  ! faster-whisper not installed -> transcription/captions disabled")
            _model_failed = True
            return None
        log(f"  ... loading whisper model '{cfg['whisper_model']}' (first run downloads it)")
        try:
            _model = WhisperModel(cfg["whisper_model"], device="cpu", compute_type="int8")
        except Exception as e:  # noqa: BLE001
            log(f"  ! whisper failed to load ({e}) -> captions disabled")
            _model_failed = True
            return None
    return _model


def transcribe(path: Path, cfg: dict) -> list[dict]:
    """Return [{'w': word, 's': start, 'e': end}] or [] on any failure."""
    model = get_model(cfg)
    if model is None:
        return []
    try:
        segments, _info = model.transcribe(
            str(path),
            language=cfg.get("whisper_language") or None,
            word_timestamps=True,
            vad_filter=True,
        )
        words = []
        for seg in segments:
            for w in (seg.words or []):
                txt = w.word.strip()
                if txt:
                    words.append({"w": txt, "s": float(w.start), "e": float(w.end)})
        return words
    except Exception as e:  # noqa: BLE001
        log(f"  ! transcription failed ({e})")
        return []


# -------------------------------------------------------- asin / slate logic

def detect_switches(words: list[dict], cfg: dict) -> list[tuple[float, str]]:
    """(time, asin) points where she announces a new product."""
    codes = {k.lower(): v for k, v in (cfg.get("product_codes") or {}).items()}
    slate = set(cfg.get("slate_words", []))
    max_len = max((len(k.split()) for k in codes), default=1)

    def strip_tok(t: str) -> str:
        return t.strip(".,!?\"'").lower()

    out = []
    n = len(words)
    for i, wd in enumerate(words):
        token = wd["w"].strip(".,!?\"'").upper()
        if ASIN_RE.fullmatch(token):
            out.append((wd["s"], token))
            continue
        # longest-phrase-first match, supports multi-word nicknames ("yoga mat")
        for size in range(min(max_len, n - i), 0, -1):
            phrase = " ".join(strip_tok(words[i + j]["w"]) for j in range(size))
            if phrase not in codes:
                continue
            prev = [strip_tok(words[j]["w"]) for j in range(max(0, i - 4), i)]
            if any(p in slate for p in prev) or i == 0:
                out.append((words[i + size - 1]["e"], codes[phrase]))
            break
    # collapse repeats of same asin in a row
    collapsed = []
    for t, a in sorted(out):
        if collapsed and collapsed[-1][1] == a:
            continue
        collapsed.append((t, a))
    return collapsed


def build_spans(words: list[dict], fname_asin: str | None, dur: float,
                cfg: dict) -> list[tuple[float, float, str]]:
    """[(start, end, asin)] covering the whole file."""
    switches = detect_switches(words, cfg)
    if switches:
        spans, prev_t = [], 0.0
        prev_asin = switches[0][1]  # footage before the first slate belongs to product 1
        for t, asin in switches:
            if t - prev_t > 0.5:
                spans.append((prev_t, t, prev_asin))
            prev_t, prev_asin = t, asin
        if dur - prev_t > 0.5:
            spans.append((prev_t, dur, prev_asin))
    elif fname_asin:
        spans = [(0.0, dur, fname_asin)]
    else:
        spans = [(0.0, dur, "UNSORTED")]
    # merge adjacent same-asin spans
    merged = []
    for s, e, a in spans:
        if merged and merged[-1][2] == a:
            merged[-1] = (merged[-1][0], e, a)
        else:
            merged.append((s, e, a))
    return merged


# ---------------------------------------------------------------- extraction

def extract_segments(src: Path, segs: list[tuple[float, float]], out: Path,
                     cfg: dict, audio: bool) -> float:
    """Cut kept segments out of src into out (re-encode + loudnorm). Returns duration."""
    s = cfg["silence"]
    a = cfg["audio"]
    sel_v = "+".join(f"between(t,{x:.3f},{y:.3f})" for x, y in segs)
    total = sum(y - x for x, y in segs)
    cmd = [cfg["ffmpeg"], "-hide_banner", "-nostats", "-y", "-i", str(src)]
    if audio:
        cmd += [
            "-vf", f"select='{sel_v}',setpts=N/FRAME_RATE/TB",
            "-af", (f"aselect='{sel_v}',asetpts=N/SR/TB,"
                    f"loudnorm=I={a['lufs']}:TP={a['true_peak']}:LRA={a['lra']}"),
        ]
        cmd += ["-c:v", "libx264", "-crf", str(cfg["export"]["crf"]),
                "-preset", cfg["export"]["preset"], "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    else:
        cmd += ["-vf", f"select='{sel_v}',setpts=N/FRAME_RATE/TB",
                "-an", "-c:v", "libx264", "-crf", str(cfg["export"]["crf"]),
                "-preset", cfg["export"]["preset"], "-pix_fmt", "yuv420p"]
    cmd += ["-movflags", "+faststart", str(out)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg extract failed: {r.stderr[-500:]}")
    return total


# ------------------------------------------------------------------ captions

def map_words(words: list[dict], segs: list[tuple[float, float]]) -> list[dict]:
    """Re-time words into the cut timeline; drop words inside removed parts."""
    out, offset = [], 0.0
    for a, b in segs:
        for w in words:
            if w["s"] >= a - 0.01 and w["e"] <= b + 0.01:
                out.append({"w": w["w"], "s": offset + (w["s"] - a),
                            "e": offset + (w["e"] - a)})
        offset += b - a
    return out


def group_captions(words: list[dict], cfg: dict) -> list[tuple[str, float, float]]:
    cap = cfg["captions"]
    lines, cur = [], []
    for w in words:
        cur.append(w)
        span = cur[-1]["e"] - cur[0]["s"]
        if (len(cur) >= cap["max_words"] or span >= cap["max_span"]
                or re.search(r"[.!?]$", cur[-1]["w"])):
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    events = []
    for i, ln in enumerate(lines):
        s = ln[0]["s"]
        e = ln[-1]["e"]
        if i + 1 < len(lines):
            e = min(e, lines[i + 1][0]["s"] - 0.05)
        e = max(e, s + 0.4)
        events.append((" ".join(x["w"] for x in ln), s, e))
    return events


def _ts_srt(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{int((sec % 1) * 1000):03d}"


def _ts_ass(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def write_srt(events, path: Path) -> None:
    lines = []
    for i, (txt, s, e) in enumerate(events, 1):
        lines += [str(i), f"{_ts_srt(s)} --> {_ts_srt(e)}", txt, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_ass(events, path: Path, cfg: dict, w: int, h: int) -> None:
    cap = cfg["captions"]
    bold = -1 if cap["bold"] else 0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cap['font']},{cap['size']},&H00FFFFFF,&H00FFFFFF,&H00101010,&H7F000000,{bold},0,0,0,100,100,0,0,1,{cap['outline']},1,2,60,60,{cap['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = []
    for txt, s, e in events:
        safe = txt.replace("{", "(").replace("}", ")")
        body.append(f"Dialogue: 0,{_ts_ass(s)},{_ts_ass(e)},Cap,,0,0,0,,{safe}")
    path.write_text(header + "\n".join(body) + "\n", encoding="utf-8")


def _esc_filter_path(p: Path) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def burn_captions(clean: Path, ass: Path, out: Path, cfg: dict) -> None:
    info = probe(clean, cfg["ffprobe"])
    stream = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w, h = int(stream["width"]), int(stream["height"])
    tw, th = cfg["export"]["width"], cfg["export"]["height"]
    assf = f"ass=filename='{_esc_filter_path(ass)}'"
    target_ar = tw / th
    src_ar = w / h
    if abs(src_ar - target_ar) < 0.02:
        vf = f"scale={tw}:{th},{assf}"
        cmd = [cfg["ffmpeg"], "-hide_banner", "-nostats", "-y", "-i", str(clean),
               "-vf", vf, "-c:v", "libx264", "-crf", str(cfg["export"]["crf"]),
               "-preset", cfg["export"]["preset"], "-pix_fmt", "yuv420p",
               "-c:a", "copy", "-movflags", "+faststart", str(out)]
    elif cfg["export"]["reframe"] == "crop":
        vf = (f"crop=ih*{target_ar:.4f}:ih,scale={tw}:{th},{assf}")
        cmd = [cfg["ffmpeg"], "-hide_banner", "-nostats", "-y", "-i", str(clean),
               "-vf", vf, "-c:v", "libx264", "-crf", str(cfg["export"]["crf"]),
               "-preset", cfg["export"]["preset"], "-pix_fmt", "yuv420p",
               "-c:a", "copy", "-movflags", "+faststart", str(out)]
    else:  # blurred pad
        fc = (f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=increase,"
              f"crop={tw}:{th},gblur=sigma=24[bg];"
              f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{assf}")
        cmd = [cfg["ffmpeg"], "-hide_banner", "-nostats", "-y", "-i", str(clean),
               "-filter_complex", fc, "-c:v", "libx264",
               "-crf", str(cfg["export"]["crf"]), "-preset", cfg["export"]["preset"],
               "-pix_fmt", "yuv420p", "-c:a", "copy",
               "-movflags", "+faststart", str(out)]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"caption burn failed: {r.stderr[-500:]}")


def concat_clips(clips: list[Path], out: Path, cfg: dict) -> None:
    list_file = out.parent / (out.stem + "_concat.txt")
    lines = [f"file '{str(c.resolve()).replace(chr(92), '/').replace(chr(39), chr(92) + chr(39))}'"
             for c in clips]
    list_file.write_text("\n".join(lines), encoding="utf-8")
    r = run([cfg["ffmpeg"], "-hide_banner", "-nostats", "-y", "-f", "concat",
             "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)])
    if r.returncode != 0:
        # fallback: re-encode concat (mixed parameters)
        r = run([cfg["ffmpeg"], "-hide_banner", "-nostats", "-y", "-f", "concat",
                 "-safe", "0", "-i", str(list_file),
                 "-c:v", "libx264", "-crf", str(cfg["export"]["crf"]),
                 "-preset", cfg["export"]["preset"], "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])
        if r.returncode != 0:
            raise RuntimeError(f"concat failed: {r.stderr[-500:]}")
    list_file.unlink(missing_ok=True)


# ------------------------------------------------------------------ pipeline

def process(inbox: Path, cfg: dict, archive: bool = True) -> None:
    """CLI entry: process every video found in `inbox`."""
    inbox = Path(inbox)
    files = sorted([p for p in inbox.iterdir()
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTS]) if inbox.exists() else []
    if not files:
        log(f"No video files found in {inbox}")
        log("Drop your raw clips there and run again.")
        return
    run_pipeline(files, cfg, archive=archive)


def run_pipeline(files: list[Path], cfg: dict, archive: bool = True,
                 out_root: Path | None = None) -> list[dict]:
    """Core pipeline over an explicit list of files. Returns manifest rows."""
    t0 = time.time()
    ffmpeg, ffprobe = cfg["ffmpeg"], cfg["ffprobe"]
    out_root = Path(out_root) if out_root else ROOT / "output"
    tmp = ROOT / "_work"

    log(f"EDIT FACTORY - {len(files)} clip(s) in inbox\n")
    groups: dict[str, list] = {}   # asin -> [{"clip": Path, "words": [...]}]
    per_file_meta = []

    for idx, f in enumerate(files, 1):
        log(f"[{idx}/{len(files)}] {f.name}")
        info = probe(f, ffprobe)
        dur = duration_of(info)
        audio = has_audio(info)
        fname_asin = None
        m = ASIN_RE.search(f.stem.upper())
        if m:
            fname_asin = m.group(0)

        words = transcribe(f, cfg) if audio else []
        log(f"  {dur:.1f}s, {len(words)} words transcribed")

        spans = build_spans(words, fname_asin, dur, cfg)
        for s, e, asin in spans:
            log(f"  -> {asin}  {s:7.1f}s - {e:7.1f}s")

        silences = detect_silences(f, ffmpeg, cfg) if audio else []
        keeps = kept_segments(silences, dur, cfg)
        removed = 1 - sum(b - a for a, b in keeps) / dur if dur else 0
        if removed < cfg["silence"]["skip_cut_threshold"]:
            keeps = [(0.0, dur)]
            log(f"  dead air: negligible ({removed * 100:.1f}%) - single pass")
        else:
            log(f"  dead air: cutting {removed * 100:.1f}%")

        # intersect spans with keeps -> per-asin segment extractions
        for s, e, asin in spans:
            sub = []
            for a, b in keeps:
                lo, hi = max(a, s), min(b, e)
                if hi - lo > 0.4:
                    sub.append((lo, hi))
            if not sub:
                continue
            tmp.mkdir(exist_ok=True)
            seg_path = tmp / f"{f.stem}_{s:.1f}.mp4"
            extract_segments(f, sub, seg_path, cfg, audio)
            span_words = [w for w in words if s - 0.01 <= w["s"] and w["e"] <= e + 0.01]
            seg_words = map_words(span_words, sub)
            groups.setdefault(asin, []).append({"clip": seg_path, "words": seg_words})

        per_file_meta.append((f.name, dur, len(words)))
        if archive and cfg.get("archive_processed", True):
            arch = ROOT / "archive"
            arch.mkdir(exist_ok=True)
            shutil.move(str(f), str(arch / f.name))
            log("  archived raw file")

    if not groups:
        log("\nNothing to export.")
        return

    out_root.mkdir(exist_ok=True)
    manifest_rows = []
    report = [f"# Edit Factory session - {datetime.now():%Y-%m-%d %H:%M}\n"]

    for asin, items in sorted(groups.items()):
        adir = out_root / asin
        adir.mkdir(parents=True, exist_ok=True)
        clean = adir / f"{asin}_clean.mp4"
        log(f"\n=== {asin}: {len(items)} segment(s) ===")

        if len(items) == 1:
            shutil.copyfile(items[0]["clip"], clean)
        else:
            concat_clips([it["clip"] for it in items], clean, cfg)

        all_words = []
        offset = 0.0
        for it in items:
            for w in it["words"]:
                all_words.append({"w": w["w"], "s": w["s"] + offset, "e": w["e"] + offset})
            pi = probe(it["clip"], ffprobe)
            offset += duration_of(pi)

        dur = duration_of(probe(clean, ffprobe))
        srt = adir / f"{asin}.srt"
        cap_mp4 = adir / f"{asin}_captions.mp4"
        if all_words:
            events = group_captions(all_words, cfg)
            write_srt(events, srt)
            ass = adir / f"{asin}.ass"
            write_ass(events, ass, cfg, cfg["export"]["width"], cfg["export"]["height"])
            try:
                burn_captions(clean, ass, cap_mp4, cfg)
                ass.unlink(missing_ok=True)
                log(f"  clean {dur:.0f}s + captions burned")
            except Exception as e:  # noqa: BLE001
                log(f"  ! caption burn failed ({e}) - clean master is still ready")
                cap_mp4 = None
        else:
            log(f"  clean {dur:.0f}s (no speech detected - no captions)")
            cap_mp4 = None

        nwords = len(all_words)
        manifest_rows.append({
            "asin": asin, "duration_s": round(dur, 1), "words": nwords,
            "clean": str(clean.relative_to(out_root)),
            "captions_mp4": str(cap_mp4.relative_to(out_root)) if cap_mp4 else "",
            "srt": str(srt.relative_to(out_root)) if all_words else "",
            "processed_at": datetime.now().isoformat(timespec="seconds"),
        })
        report += [
            f"## {asin}",
            f"- Final length: **{dur:.0f}s** from {len(items)} take(s), {nwords} words",
            f"- Upload-ready (captions burned in): `{cap_mp4.name}`" if cap_mp4
            else "- No captions (no speech detected) - upload the clean master",
            f"- CapCut route: import `{clean.name}`" + (f" + `{srt.name}`" if all_words else ""),
            "",
        ]

    # manifest
    mpath = out_root / "manifest.csv"
    new_file = not mpath.exists()
    with mpath.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(manifest_rows[0].keys()))
        if new_file:
            writer.writeheader()
        writer.writerows(manifest_rows)

    (out_root / f"_report_{datetime.now():%Y%m%d_%H%M}.md").write_text(
        "\n".join(report), encoding="utf-8")

    # cleanup temp segments
    shutil.rmtree(tmp, ignore_errors=True)

    mins = (time.time() - t0) / 60
    log(f"\nDONE in {mins:.1f} min - {len(groups)} product(s) exported to {out_root}")
    for r in manifest_rows:
        log(f"  {r['asin']}: {r['duration_s']}s -> {r['clean']}")
    log("Next: upload the *_captions.mp4 files (or open *_clean.mp4 + .srt in CapCut).")
    return manifest_rows


def output_dir(cfg: dict) -> Path:
    """Configured output folder (default ./output)."""
    p = (cfg.get("paths") or {}).get("output") or ""
    return Path(p) if p else ROOT / "output"


def stats() -> None:
    mpath = output_dir(load_config()) / "manifest.csv"
    if not mpath.exists():
        log("No manifest yet - run a session first.")
        return
    with mpath.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    total = sum(float(r["duration_s"]) for r in rows)
    log(f"{len(rows)} products processed, {total / 60:.1f} min total footage")
    for r in rows[-15:]:
        log(f"  {r['asin']:<14} {r['duration_s']:>7}s  words={r['words']:<5} {r['processed_at']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Edit Factory - Amazon Influencer video pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process", help="process everything in the inbox")
    p.add_argument("--inbox", default=str(ROOT / "inbox"))
    p.add_argument("--no-archive", action="store_true", help="leave raw files in inbox")
    sub.add_parser("init", help="write default config.json")
    sub.add_parser("stats", help="show manifest summary")
    args = ap.parse_args()

    if args.cmd == "init":
        save_config(DEFAULT_CONFIG)
        log(f"Wrote {ROOT / 'config.json'}")
        return
    cfg = load_config()
    if args.cmd == "process":
        process(Path(args.inbox), cfg, archive=not args.no_archive)
    elif args.cmd == "stats":
        stats()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    main()
