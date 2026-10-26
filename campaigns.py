#!/usr/bin/env python3
"""
CAMPAIGNS - Creator Connections (and any ASIN batch) tracking for Edit Factory
==============================================================================
A "campaign" is a brand brief: a name, the ASINs it wants featured, optional
required talking points, and a deadline. Edit Factory uses campaigns to:

  - prioritize which clips to process first (deadline-aware "film next" list)
  - report footage/output gaps per ASIN (footage = archive/ or the inbox)
  - power the transcript-based compliance pre-check (transcript_intel.py)
  - assemble ready-to-submit packets for the brand

Storage: campaigns.json next to config.json. 100% local, stdlib only.
Every function takes an optional `path` (campaign store) / `out_root`
(output folder) so tests can run against temp dirs.

Public API (used by webui.py):
    add_campaign(...) / update_campaign(...) / delete_campaign(id)
    list_campaigns()
    campaign_status(cid)       -> per-ASIN gap analysis
    mark_submitted(cid, asin)  -> track what was already sent to the brand
    film_next(limit)           -> ranked "what to film next" suggestions
    parse_campaign_paste(txt)  -> flexible CC page paste -> campaign fields
    import_cc_products_csv(p)  -> Oink "My CC Campaign Products Report" parser
"""
from __future__ import annotations

import csv
import json
import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

import edit_factory as ef

ROOT = Path(__file__).resolve().parent
DEFAULT_FILE = ROOT / "campaigns.json"
_LOCK = threading.Lock()

DATE_RE = re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4})\b")
BULLET_RE = re.compile(r"^(?:[-*\u2022\u00b7]|\d+[.)])\s*(.+)$")
BRAND_RE = re.compile(r"(?i)^\s*brand\s*[:\-]\s*(.+)$")
DEADLINE_WORD_RE = re.compile(r"(?i)\b(deadline|ends?|due|closes?|expires?)\b")
ASIN_LINE_RE = re.compile(r"^(?:the\s+)?B0[A-Z0-9]{8}\s*$", re.IGNORECASE)


# ----------------------------------------------------------------- storage

def _load(path: Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_FILE
    if not p.exists():
        return {"campaigns": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt store should not kill the UI
        backup = p.with_suffix(".corrupt.json")
        p.rename(backup)
        return {"campaigns": []}
    if not isinstance(data, dict) or not isinstance(data.get("campaigns"), list):
        return {"campaigns": []}
    return data


def _save(data: dict, path: Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_FILE
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_asins(values) -> list[str]:
    """Extract every valid ASIN from a string or list of strings, deduped."""
    if values is None:
        values = []
    if isinstance(values, str):
        blob = values
    else:
        blob = " ".join(str(v) for v in values)
    seen: list[str] = []
    for m in ef.ASIN_RE.finditer(blob.upper()):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


# ------------------------------------------------------------------ CRUD

def add_campaign(name: str = "", brand: str = "", asins=None,
                 requirements=None, deadline: str | None = None,
                 notes: str = "", source: str = "manual",
                 path: Path | None = None) -> dict:
    asins = _normalize_asins(asins if asins is not None else [])
    requirements = [str(r).strip() for r in (requirements or []) if str(r).strip()]
    campaign = {
        "id": f"c_{datetime.now():%Y%m%d_%H%M}_{uuid.uuid4().hex[:4]}",
        "name": (name or "").strip() or "Untitled campaign",
        "brand": (brand or "").strip(),
        "asins": asins,
        "requirements": requirements,
        "deadline": (deadline or "").strip() or None,
        "notes": (notes or "").strip(),
        "status": "active",
        "source": source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "submitted": {},   # asin -> {"file": str, "at": iso}
    }
    with _LOCK:
        data = _load(path)
        data["campaigns"].append(campaign)
        _save(data, path)
    return campaign


def update_campaign(cid: str, name=None, brand=None, asins=None,
                    requirements=None, deadline=None, notes=None,
                    status=None, path: Path | None = None) -> dict:
    with _LOCK:
        data = _load(path)
        for c in data["campaigns"]:
            if c["id"] == cid:
                if name is not None:
                    c["name"] = str(name).strip() or c["name"]
                if brand is not None:
                    c["brand"] = str(brand).strip()
                if asins is not None:
                    c["asins"] = _normalize_asins(asins)
                if requirements is not None:
                    c["requirements"] = [str(r).strip() for r in requirements
                                         if str(r).strip()]
                if deadline is not None:
                    c["deadline"] = str(deadline).strip() or None
                if notes is not None:
                    c["notes"] = str(notes).strip()
                if status is not None:
                    c["status"] = str(status).strip() or "active"
                _save(data, path)
                return c
    raise KeyError(cid)


def delete_campaign(cid: str, path: Path | None = None) -> None:
    with _LOCK:
        data = _load(path)
        n = len(data["campaigns"])
        data["campaigns"] = [c for c in data["campaigns"] if c["id"] != cid]
        if len(data["campaigns"]) == n:
            raise KeyError(cid)
        _save(data, path)


def get_campaign(cid: str, path: Path | None = None) -> dict:
    for c in _load(path)["campaigns"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def list_campaigns(path: Path | None = None) -> list[dict]:
    return list(_load(path)["campaigns"])


def mark_submitted(cid: str, asin: str, file: str = "",
                   path: Path | None = None) -> dict:
    """Record that a video for `asin` was uploaded/submitted to the brand."""
    m = ef.ASIN_RE.search((asin or "").upper())
    asin = m.group(0) if m else (asin or "").strip().upper()
    with _LOCK:
        data = _load(path)
        for c in data["campaigns"]:
            if c["id"] == cid:
                c.setdefault("submitted", {})[asin] = {
                    "file": file, "at": datetime.now().isoformat(timespec="seconds"),
                }
                _save(data, path)
                return c
    raise KeyError(cid)


# --------------------------------------------------------------- paste parser

def _parse_deadline(text: str):
    """Best-effort date parse; returns date or None."""
    t = text.strip()
    fmts = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y",
            "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y")
    for f in fmts:
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None


def parse_campaign_paste(text: str) -> dict:
    """Flexibly extract campaign fields from a copy-pasted CC page (or any text).

    Returns {name, brand, asins, requirements, deadline, found_dates} - nothing
    is persisted; the UI shows a preview and lets the user correct it.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    asins = _normalize_asins(text)

    name, brand = "", ""
    requirements: list[str] = []
    line_dates: list[str] = []
    deadline_line_date = None

    for i, ln in enumerate(lines):
        m = BRAND_RE.match(ln)
        if m and not brand:
            brand = m.group(1).strip()
        # deadline: prefer a date on a line that talks about deadlines
        if DEADLINE_WORD_RE.search(ln):
            for d in DATE_RE.findall(ln):
                parsed = _parse_deadline(d)
                if parsed and deadline_line_date is None:
                    deadline_line_date = parsed.isoformat()
        for d in DATE_RE.findall(ln):
            line_dates.append(d)

    if not deadline_line_date and line_dates:
        for d in line_dates:
            parsed = _parse_deadline(d)
            if parsed:
                deadline_line_date = parsed.isoformat()
                break

    # name = first line that isn't just an ASIN and isn't a brand line
    for ln in lines:
        if ASIN_LINE_RE.match(ln) or BRAND_RE.match(ln):
            continue
        if len(ln) > 2:
            name = ln[:80]
            break

    for ln in lines:
        m = BULLET_RE.match(ln)
        body = m.group(1).strip() if m else ""
        if not body and re.search(r"(?i)\b(must|include|mention|talk about)\b", ln):
            body = ln
        if body and len(body) > 3 and not ef.ASIN_RE.fullmatch(body.upper()):
            if body not in requirements:
                requirements.append(body[:200])
        if len(requirements) >= 20:
            break

    return {
        "name": name,
        "brand": brand,
        "asins": asins,
        "requirements": requirements,
        "deadline": deadline_line_date,
        "found_dates": line_dates,
    }


# ------------------------------------------------------------- CSV importer

def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def import_cc_products_csv(csv_path: str | Path) -> dict:
    """Parse an Oink 'My CC Campaign Products Report' (or similar) export.

    Column mapping is flexible: whatever headers exist, we look for ones that
    contain 'asin', 'campaign', 'brand', 'product/title', 'status', 'deadline'.
    Returns a preview dict - nothing is persisted until the UI confirms.
    """
    p = Path(csv_path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise ValueError(f"Empty file: {p}")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:  # noqa: PERF203 - fall back to plain comma
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError(f"No header row found in {p.name}")

    headers = list(reader.fieldnames)
    norm = [_norm_header(h) for h in headers]

    def find_col(*needles: str) -> str | None:
        for needle in needles:
            for h, n in zip(headers, norm):
                if needle == n:
                    return h
        for needle in needles:
            for h, n in zip(headers, norm):
                if needle in n:
                    return h
        return None

    col_asin = find_col("asin")
    col_campaign = find_col("campaign")
    col_brand = find_col("brand", "company", "seller")
    col_product = find_col("product", "title", "item")
    col_status = find_col("status")
    col_deadline = find_col("deadline", "end date", "end date ")

    groups: dict[str, dict] = {}
    rows = skipped = 0
    for row in reader:
        rows += 1
        raw_asin = (row.get(col_asin, "") if col_asin else "")
        blob = f"{raw_asin} {row.get(col_product, '') if col_product else ''}"
        found = _normalize_asins([blob])
        if not found:
            skipped += 1
            continue
        cname = (row.get(col_campaign, "") if col_campaign else "").strip() \
            or "Imported campaigns"
        g = groups.setdefault(cname, {
            "name": cname, "brand": "", "asins": [], "products": [],
            "deadline": None,
        })
        if col_brand and not g["brand"]:
            g["brand"] = (row.get(col_brand, "") or "").strip()
        if col_deadline and not g["deadline"]:
            raw_d = (row.get(col_deadline, "") or "").strip()
            parsed = _parse_deadline(raw_d)
            g["deadline"] = parsed.isoformat() if parsed else raw_d or None
        for a in found:
            if a not in g["asins"]:
                g["asins"].append(a)
        if col_product:
            prod = (row.get(col_product, "") or "").strip()
            if prod and prod not in g["products"] and len(g["products"]) < 50:
                g["products"].append(prod)

    return {
        "ok": True,
        "file": str(p),
        "rows": rows,
        "skipped": skipped,
        "headers": headers,
        "columns_used": {"asin": col_asin, "campaign": col_campaign,
                         "brand": col_brand, "product": col_product,
                         "status": col_status, "deadline": col_deadline},
        "campaigns": list(groups.values()),
    }


# -------------------------------------------------------------- gap analysis

def _out_root(out_root: Path | None = None) -> Path:
    return Path(out_root) if out_root else ef.output_dir(ef.load_config())


def _inbox_dir() -> Path:
    p = (ef.load_config().get("paths") or {}).get("inbox") or ""
    return Path(p) if p else ROOT / "inbox"


def _read_manifest(out_root: Path) -> list[dict]:
    mpath = out_root / "manifest.csv"
    if not mpath.exists():
        return []
    with mpath.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _footage_names(extra_dirs: list[Path] | None = None) -> dict[str, list[str]]:
    """asin -> [filenames] for raw clips sitting in archive/ or the inbox."""
    folders = [ROOT / "archive", _inbox_dir()] + [Path(d) for d in (extra_dirs or [])]
    found: dict[str, list[str]] = {}
    for folder in folders:
        if not folder.exists():
            continue
        for f in folder.iterdir():
            if not (f.is_file() and f.suffix.lower() in ef.VIDEO_EXTS):
                continue
            for asin in set(ef.ASIN_RE.findall(f.stem.upper())):
                found.setdefault(asin, []).append(f.name)
    return found


def _days_left(deadline: str | None) -> int | None:
    if not deadline:
        return None
    d = _parse_deadline(deadline) if "/" in deadline or "-" in deadline else None
    if d is None:
        d = _parse_deadline(str(deadline))
    if d is None:
        return None
    return (d - date.today()).days


def campaign_status(cid: str, path: Path | None = None,
                    out_root: Path | None = None,
                    extra_dirs: list[Path] | None = None) -> dict:
    """Per-ASIN gap analysis for one campaign.

    state: submitted | ready (export exists, not submitted) |
           footage (raw clip exists, no export) | missing
    """
    campaign = get_campaign(cid, path)
    out = _out_root(out_root)
    manifest = _read_manifest(out)
    by_asin: dict[str, list[dict]] = {}
    for row in manifest:
        by_asin.setdefault((row.get("asin") or "").strip().upper(), []).append(row)
    footage = _footage_names(extra_dirs)

    asins = []
    for asin in campaign.get("asins", []):
        rows = by_asin.get(asin, [])
        last = max((r.get("processed_at") or "" for r in rows), default="")
        cap_file = ""
        if rows:
            cap = (rows[-1].get("captions_mp4") or "").strip()
            if cap and (out / cap).exists():
                cap_file = cap
        submitted = asin in (campaign.get("submitted") or {})
        if submitted:
            state = "submitted"
        elif rows:
            state = "ready"
        elif footage.get(asin):
            state = "footage"
        else:
            state = "missing"
        asins.append({
            "asin": asin,
            "state": state,
            "videos": len(rows),
            "last_processed": last,
            "captions_file": cap_file,
            "footage_files": footage.get(asin, []),
        })

    n_ready = sum(1 for a in asins if a["state"] in ("ready", "submitted"))
    return {
        "campaign_id": cid,
        "asins": asins,
        "days_left": _days_left(campaign.get("deadline")),
        "progress": {"done": n_ready, "total": len(asins)},
    }


# ----------------------------------------------------------------- film next

def film_next(limit: int = 8, path: Path | None = None,
              out_root: Path | None = None,
              import_dir: Path | None = None) -> dict:
    """Ranked 'what to film next' across all active campaigns + earnings.

    Score components (deterministic, no AI):
      base 40 for any campaign ASIN without an export yet
      + deadline urgency (sooner = higher, past-due = penalized)
      +15 if raw footage already exists (one step from done)
      + up to 25 if that ASIN has earned money before (from Oink earnings)
    Also returns standalone earnings-driven suggestions and the
    ready-to-submit list (export exists, brand not yet notified).
    """
    suggestions: list[dict] = []
    ready: list[dict] = []
    earnings: dict[str, dict] = {}

    try:
        import oink_data
        earnings = oink_data.earnings_by_asin(import_dir=import_dir)
    except Exception:  # noqa: BLE001 - earnings are optional everywhere
        earnings = {}

    for c in list_campaigns(path):
        if (c.get("status") or "active") == "closed":
            continue
        st = campaign_status(c["id"], path=path, out_root=out_root)
        days = st["days_left"]
        for a in st["asins"]:
            if a["state"] == "submitted":
                continue
            if a["state"] == "ready":
                ready.append({"campaign": c["name"], "campaign_id": c["id"],
                              "asin": a["asin"], "captions_file": a["captions_file"]})
                continue
            score = 40.0
            reasons = [f"campaign '{c['name']}' needs it"]
            if days is not None:
                if days < 0:
                    score -= 20
                    reasons.append("deadline passed")
                elif days <= 3:
                    score += 45
                    reasons.append(f"deadline in {days}d")
                elif days <= 7:
                    score += 30
                    reasons.append(f"deadline in {days}d")
                elif days <= 14:
                    score += 18
                    reasons.append(f"deadline in {days}d")
                else:
                    score += 8
            if a["state"] == "footage":
                score += 15
                reasons.append("footage already filmed")
            earned = earnings.get(a["asin"], {}).get("total", 0) or 0
            if earned > 0:
                score += min(25.0, earned * 2)
                reasons.append(f"earned ${earned:,.2f} before")
            suggestions.append({
                "asin": a["asin"], "score": round(score, 1),
                "reason": "; ".join(reasons), "campaign": c["name"],
                "campaign_id": c["id"], "days_left": days, "state": a["state"],
            })

    # standalone: earned money before but never made a video
    manifest_asins = {r.get("asin", "").strip().upper()
                      for r in _read_manifest(_out_root(out_root))}
    campaign_asins = {a for c in list_campaigns(path) for a in c.get("asins", [])}
    for asin, agg in earnings.items():
        if asin in manifest_asins or asin in campaign_asins:
            continue
        if (agg.get("total") or 0) > 0:
            suggestions.append({
                "asin": asin, "score": round(30 + min(20.0, agg["total"]), 1),
                "reason": (f"earned ${agg['total']:,.2f} on {agg['orders']} order(s) "
                           f"but you have no video for it yet"),
                "campaign": None, "campaign_id": None, "days_left": None,
                "state": "missing",
            })

    suggestions.sort(key=lambda s: -s["score"])
    return {"suggestions": suggestions[:limit], "ready_to_submit": ready}
