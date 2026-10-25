#!/usr/bin/env python3
"""
EDIT FACTORY WEB UI - local dashboard for the video pipeline
============================================================
Run:  python webui.py          (or double-click START_WEB_UI.bat)
Then: http://127.0.0.1:8765

Features:
  - Pick ANY inbox folder (local path or UNC network share \\NAS\videos)
  - See every video in it (duration, size, ASIN-in-name badge)
  - Choose exactly which files to process
  - Live progress log while the pipeline runs
  - Results table per ASIN + one-click "open output folder"

Local-only by design: binds to 127.0.0.1, no data leaves the machine.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from pathlib import Path

import edit_factory as ef

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
except ImportError:
    raise SystemExit("Missing dependencies. Run:  pip install -r requirements.txt")

app = FastAPI(title="Edit Factory")

# ---------------------------------------------------------------- job state

STATE: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "rows": None,        # manifest rows of the last run
    "error": None,
    "inbox": None,
    "archive": None,
}
_JOB_LOCK = threading.Lock()


def _cfg() -> dict:
    return ef.load_config()


def _inbox(cfg: dict) -> Path:
    p = (cfg.get("paths") or {}).get("inbox") or ""
    return Path(p) if p else ROOT / "inbox"


def _save_paths(inbox: str | None = None, output: str | None = None) -> dict:
    cfg = _cfg()
    cfg.setdefault("paths", {})
    if inbox is not None:
        cfg["paths"]["inbox"] = inbox.strip()
    if output is not None:
        cfg["paths"]["output"] = output.strip()
    ef.save_config(cfg)
    return cfg


# ------------------------------------------------------------------- worker

def _worker(files: list[Path], archive: bool, out_root: Path) -> None:
    try:
        rows = ef.run_pipeline(files, _cfg(), archive=archive, out_root=out_root)
        STATE["rows"] = rows
        STATE["error"] = None
    except Exception as e:  # noqa: BLE001
        STATE["error"] = f"{type(e).__name__}: {e}"
        ef.log(f"! FAILED: {STATE['error']}")
    finally:
        STATE["running"] = False
        STATE["finished_at"] = time.strftime("%H:%M:%S")


# -------------------------------------------------------------------- models

class PathsIn(BaseModel):
    inbox: str | None = None
    output: str | None = None


class ProcessIn(BaseModel):
    files: list[str]
    archive: bool = True


class OpenIn(BaseModel):
    path: str


# --------------------------------------------------------------------- api

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    cfg = _cfg()
    inbox, out = _inbox(cfg), ef.output_dir(cfg)
    return {
        "inbox": str(inbox),
        "inbox_exists": inbox.exists(),
        "output": str(out),
        "whisper_model": cfg.get("whisper_model", "small"),
        "running": STATE["running"],
    }


@app.post("/api/config")
def set_config(body: PathsIn) -> dict:
    cfg = _save_paths(body.inbox, body.output)
    inbox, out = _inbox(cfg), ef.output_dir(cfg)
    return {"inbox": str(inbox), "output": str(out),
            "inbox_exists": inbox.exists()}


@app.get("/api/drives")
def drives() -> dict:
    import string
    found = [f"{L}:\\" for L in string.ascii_uppercase
             if Path(f"{L}:\\").exists()]
    return {"drives": found}


@app.get("/api/browse")
def browse(path: str = "") -> dict:
    p = Path(path) if path else Path("\\\\")  # empty -> roots view handled client side
    if not path:
        return {"exists": True, "parent": None, "dirs": [], "drives_hint": True}
    p = p.resolve() if not str(p).endswith(("\\", "/")) else p
    if not p.exists():
        return {"exists": False, "path": str(p), "parent": None, "dirs": [],
                "error": "Path not found or unreachable"}
    dirs = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child)})
            except OSError:
                continue  # inaccessible entries are skipped
    except OSError as e:
        return {"exists": True, "path": str(p), "parent": None, "dirs": [],
                "error": f"Cannot list: {e}"}
    parent = str(p.parent) if p.parent != p else None
    return {"exists": True, "path": str(p), "parent": parent, "dirs": dirs}


@app.get("/api/files")
def files(path: str = "") -> dict:
    cfg = _cfg()
    folder = Path(path) if path else _inbox(cfg)
    if not folder.exists():
        raise HTTPException(404, f"Folder not found: {folder}")
    out = []
    for f in sorted(folder.iterdir(), key=lambda c: c.name.lower()):
        if not (f.is_file() and f.suffix.lower() in ef.VIDEO_EXTS):
            continue
        try:
            info = ef.probe(f, cfg["ffprobe"])
            dur = ef.duration_of(info)
            audio = ef.has_audio(info)
        except Exception:  # noqa: BLE001
            dur, audio = None, None
        out.append({
            "name": f.name,
            "path": str(f),
            "size_mb": round(f.stat().st_size / 1e6, 1),
            "duration_s": round(dur, 1) if dur else None,
            "has_audio": audio,
            "asin_in_name": bool(ef.ASIN_RE.search(f.stem.upper())),
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)),
        })
    return {"folder": str(folder), "count": len(out), "files": out}


@app.post("/api/process")
def start_process(body: ProcessIn) -> dict:
    with _JOB_LOCK:
        if STATE["running"]:
            raise HTTPException(409, "A job is already running")
        cfg = _cfg()
        inbox = _inbox(cfg)
        out_root = ef.output_dir(cfg)
        resolved = []
        for name in body.files:
            p = Path(name)
            if not p.is_absolute():
                p = inbox / name
            if not p.is_file():
                raise HTTPException(400, f"File not found: {p}")
            resolved.append(p)
        if not resolved:
            raise HTTPException(400, "No files selected")

        STATE.update(running=True, started_at=time.strftime("%H:%M:%S"),
                     finished_at=None, rows=None, error=None,
                     inbox=str(inbox), archive=body.archive)
        ef.LOG_SINK.clear()
        th = threading.Thread(target=_worker, args=(resolved, body.archive, out_root),
                              daemon=True)
        th.start()
        return {"started": True, "count": len(resolved)}


@app.get("/api/status")
def status() -> dict:
    return {
        **{k: STATE[k] for k in ("running", "started_at", "finished_at", "rows",
                                 "error", "inbox", "archive")},
        "lines": ef.LOG_SINK[-300:],
        "output": str(ef.output_dir(_cfg())),
    }


@app.post("/api/open")
def open_folder(body: OpenIn) -> dict:
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(404, f"Path not found: {p}")
    target = p if p.is_dir() else p.parent
    os.startfile(str(target))  # noqa: S606 - opens Explorer on the user's desktop
    return {"opened": str(target)}


if __name__ == "__main__":
    import uvicorn
    cfg = _cfg()
    port = int((cfg.get("web") or {}).get("port") or 8765)
    url = f"http://127.0.0.1:{port}"
    print(f"EDIT FACTORY WEB UI -> {url}", flush=True)
    if os.environ.get("EF_NO_BROWSER") != "1":
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
