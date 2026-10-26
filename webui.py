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
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import edit_factory as ef
import studio as st
import campaigns as camp
import oink_data as oink
import judgments as jd

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse
    from pydantic import BaseModel
except ImportError:
    raise SystemExit("Missing dependencies. Run:  pip install -r requirements.txt")

app = FastAPI(title="Edit Factory")


@app.on_event("startup")
def _ensure_output_dir() -> None:
    """Make sure the output folder exists so 'Open output folder' never 404s."""
    try:
        ef.output_dir(_cfg()).mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass

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
    path: str = ""


class PickIn(BaseModel):
    initial: str = ""


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


_PICK_LOCK = threading.Lock()


@app.post("/api/pick_folder")
def pick_folder(body: PickIn) -> dict:
    """Open the REAL Windows folder-picker dialog (supports network shares).
    Blocks until the user chooses or cancels; returns {"path": str|None}."""
    if not _PICK_LOCK.acquire(blocking=False):
        raise HTTPException(409, "A folder dialog is already open - choose it or cancel it first.")
    try:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Folder dialog unavailable: {e}")
        initial = body.initial.strip()
        if not (initial and Path(initial).exists()):
            initial = str(Path.home())
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            root.focus_force()
            try:
                chosen = filedialog.askdirectory(
                    title="Edit Factory - choose the folder",
                    initialdir=initial,
                )
            finally:
                root.destroy()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Could not open the folder dialog: {e}")
        return {"path": chosen.replace("/", "\\") if chosen else None}
    finally:
        _PICK_LOCK.release()


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


def _explorer_open(target: Path) -> str:
    """Open a folder in Explorer via several strategies; returns which worked.
    A spawned child that dies with 0xc0000142 (DLL init blocked) counts as a
    failure even though Popen itself succeeded."""
    DLL_INIT_FAILED = 3221225794  # 0xC0000142

    def _spawn_result(cmd: list[str]) -> str:
        proc = subprocess.Popen(cmd, close_fds=True)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            rc = proc.poll()
            if rc is not None:
                if rc == DLL_INIT_FAILED:
                    raise RuntimeError(f"{cmd[0]} died with 0xc0000142")
                return "ok"       # rc==1 = normal hand-off; anything else = assume ok
            time.sleep(0.2)
        return "still-running"    # ambiguous; treat as ok

    win = os.environ.get("SystemRoot", r"C:\Windows")
    attempts = [
        ("explorer.exe", [str(Path(win) / "explorer.exe"), str(target)]),
        ("explorer (PATH)", ["explorer", str(target)]),
        ("cmd start", ["cmd", "/c", "start", "", str(target)]),
    ]
    errors = []
    for name, cmd in attempts:
        try:
            if _spawn_result(cmd) == "ok":
                return name
            errors.append(f"{name}: child exited abnormally")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
    # last resort
    try:
        os.startfile(str(target))
        return "os.startfile"
    except Exception as e:  # noqa: BLE001
        errors.append(f"os.startfile: {e}")
    raise RuntimeError("; ".join(errors))


@app.post("/api/open")
def open_folder(body: OpenIn) -> dict:
    """Open a folder in Explorer. Empty path = the configured output folder.
    The folder is created first if missing, so this never 404s."""
    cfg = _cfg()
    p = Path(body.path.strip()) if body.path.strip() else ef.output_dir(cfg)
    p.mkdir(parents=True, exist_ok=True)
    target = p if p.is_dir() else p.parent
    try:
        method = _explorer_open(target)
    except Exception as e:  # noqa: BLE001
        # not fatal: the UI still serves the folder over HTTP
        return {"opened": str(target), "method": "none",
                "note": f"Windows blocked opening Explorer from this app ({e}). "
                        f"Use the file links in the results table, or if the "
                        f"server was started from a restricted window, close it "
                        f"and double-click START_WEB_UI.bat in File Explorer."}
    return {"opened": str(target), "method": method}


@app.get("/media/{rel_path:path}")
def media(rel_path: str):
    """Serve processed files over HTTP so the UI can preview/download them
    without touching Explorer at all."""
    out_root = ef.output_dir(_cfg()).resolve()
    p = (out_root / rel_path).resolve()
    if not p.is_relative_to(out_root):
        raise HTTPException(400, "Invalid path")
    if not p.is_file():
        raise HTTPException(404, f"Not found: {rel_path}")
    return FileResponse(str(p))


# ------------------------------------------------------------- script studio

@app.get("/studio")
def studio_page():
    return FileResponse(str(WEB_DIR / "studio.html"))


class GenIn(BaseModel):
    asin: str = ""
    title: str = ""
    bullets: list[str] = []
    themes: list[str] = []
    price: str = ""
    tone: str = "friendly"
    seed: str | None = None


@app.post("/api/script/generate")
def script_generate(body: GenIn) -> dict:
    if not body.title.strip() and not body.bullets:
        raise HTTPException(400, "Add at least a product title or one bullet point.")
    return st.generate_script(body.asin, body.title, body.bullets,
                              themes=body.themes, price=body.price,
                              tone=body.tone, seed=body.seed)


class SaveIn(BaseModel):
    script: dict


@app.post("/api/script/save")
def script_save(body: SaveIn) -> dict:
    try:
        return st.save_script(body.script)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Could not save: {e}")


@app.get("/api/scripts")
def scripts_list() -> list[dict]:
    return st.list_scripts()


@app.get("/api/script/{sid}")
def script_get(sid: str) -> dict:
    try:
        return st.load_script(sid)
    except ValueError:
        raise HTTPException(400, "Bad script id")
    except FileNotFoundError:
        raise HTTPException(404, "Script not found")


@app.delete("/api/script/{sid}")
def script_delete(sid: str) -> dict:
    try:
        st.delete_script(sid)
        return {"deleted": sid}
    except ValueError:
        raise HTTPException(400, "Bad script id")


class PaApiIn(BaseModel):
    asin: str


@app.post("/api/paapi/fetch")
def paapi_fetch(body: PaApiIn) -> dict:
    try:
        return st.fetch_product(body.asin, _cfg())
    except RuntimeError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------- campaigns (CC workflow)

class CampaignIn(BaseModel):
    id: str | None = None
    name: str = ""
    brand: str = ""
    asins: list[str] = []
    requirements: list[str] = []
    deadline: str | None = None
    notes: str = ""
    status: str | None = None


@app.get("/api/campaigns")
def campaigns_list() -> dict:
    out = []
    for c in camp.list_campaigns():
        try:
            info = camp.campaign_status(c["id"])
        except Exception as e:  # noqa: BLE001 - one bad campaign != dead UI
            info = {"error": str(e)}
        out.append({**c, "status_info": info})
    return {"campaigns": out}


@app.post("/api/campaigns")
def campaigns_upsert(body: CampaignIn) -> dict:
    if body.id:
        fields = {k: v for k, v in body.model_dump(exclude={"id"}).items()
                  if v is not None}
        try:
            return camp.update_campaign(body.id, **fields)
        except KeyError:
            raise HTTPException(404, "Campaign not found")
    if not body.name.strip() and not body.asins:
        raise HTTPException(400, "Give the campaign a name or at least one ASIN.")
    return camp.add_campaign(name=body.name, brand=body.brand, asins=body.asins,
                             requirements=body.requirements,
                             deadline=body.deadline, notes=body.notes,
                             source="manual")


class PasteIn(BaseModel):
    text: str


@app.post("/api/campaigns/parse_paste")
def campaigns_parse_paste(body: PasteIn) -> dict:
    parsed = camp.parse_campaign_paste(body.text)
    if not parsed["asins"] and not parsed["name"]:
        raise HTTPException(
            400, "Couldn't find an ASIN or a campaign name in that text.")
    return parsed


class ImportPathIn(BaseModel):
    path: str = ""


@app.post("/api/campaigns/import_csv")
def campaigns_import_csv(body: ImportPathIn) -> dict:
    try:
        return camp.import_cc_products_csv(body.path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class SubmitIn(BaseModel):
    asin: str
    file: str = ""


@app.post("/api/campaigns/{cid}/submit")
def campaigns_submit(cid: str, body: SubmitIn) -> dict:
    try:
        return camp.mark_submitted(cid, body.asin, body.file)
    except KeyError:
        raise HTTPException(404, "Campaign not found")


@app.delete("/api/campaigns/{cid}")
def campaigns_delete(cid: str) -> dict:
    try:
        camp.delete_campaign(cid)
        return {"deleted": cid}
    except KeyError:
        raise HTTPException(404, "Campaign not found")


@app.get("/api/film_next")
def film_next() -> dict:
    return camp.film_next()


# --------------------------------------------------- oink earnings import

@app.post("/api/oink/import_earnings")
async def oink_import_earnings(request: Request) -> dict:
    """Upload an Oink earnings CSV as the raw request body (the UI reads the
    file with <input type=file> and POSTs its text - no multipart needed)."""
    try:
        text = (await request.body()).decode("utf-8-sig", errors="replace")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read upload: {e}")
    if not text.strip():
        raise HTTPException(
            400, "Empty upload - select your Oink earnings CSV first.")
    try:
        return oink.import_earnings_text(text, original_name="upload.csv")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/oink/import_earnings_path")
def oink_import_earnings_path(body: ImportPathIn) -> dict:
    """Import by path - a CSV file or a folder to scan (default oink_import/)."""
    try:
        return oink.import_earnings_path(body.path or None)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/oink/roi")
def oink_roi() -> dict:
    return oink.roi_report()


@app.get("/api/oink/earners_without_video")
def oink_earners_without_video() -> dict:
    return {"rows": oink.earners_without_video()}


# --------------------------------------------------- judgments (TypeSafe)

@app.get("/api/judgments/status")
def judgments_status() -> dict:
    """Whether the semantic-judgment layer is on TypeSafe or local
    heuristics. Contains no key material."""
    return jd.status()


if __name__ == "__main__":
    import socket
    import urllib.request
    import uvicorn

    def _serving_edit_factory(port: int) -> bool:
        """True if an Edit Factory instance already answers on this port."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config",
                                        timeout=1.5) as r:
                return b"whisper_model" in r.read(4096)
        except Exception:  # noqa: BLE001
            return False

    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    cfg = _cfg()
    base_port = int(os.environ.get("EF_PORT")
                    or (cfg.get("web") or {}).get("port") or 8765)
    url = f"http://127.0.0.1:{base_port}"

    if _serving_edit_factory(base_port):
        print(f"Edit Factory is already running at {url} - opening it.", flush=True)
        webbrowser.open(url)
        raise SystemExit(0)

    port = next((p for p in range(base_port, base_port + 11) if _port_free(p)), None)
    if port is None:
        raise SystemExit(f"No free port in {base_port}-{base_port + 10}. "
                         f"Close the app using port {base_port} and retry.")
    if port != base_port:
        print(f"Port {base_port} is busy - using {port} instead.", flush=True)
    url = f"http://127.0.0.1:{port}"

    print(f"EDIT FACTORY WEB UI -> {url}", flush=True)
    if os.environ.get("EF_NO_BROWSER") != "1":
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
