#!/usr/bin/env python3
"""
OINK DATA - earnings CSV import -> per-ASIN ROI and film-next signals
=====================================================================
Oink for Influencers can download an Earnings Report CSV. This module imports
it with flexible column mapping (exports rename columns between versions),
normalizes it to oink_import/earnings_normalized.csv, and joins it with
output/manifest.csv to answer:

  - which ASINs actually earn money?
  - earnings per minute of finished footage (ROI per product)
  - which earning ASINs have no video yet (film-next candidates)

100% local, stdlib only. Every function takes optional `import_dir` /
`out_root` so tests can run against temp dirs.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path

import edit_factory as ef

ROOT = Path(__file__).resolve().parent
IMPORT_DIR = ROOT / "oink_import"

NORMALIZED_NAME = "earnings_normalized.csv"
NORMALIZED_FIELDS = ["date", "asin", "amount", "status", "product"]

_DATE_FMTS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y",
              "%d %b %Y", "%b %d, %Y", "%B %d, %Y", "%d %B %Y")


# ------------------------------------------------------------------ helpers

def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(h).lower()).strip()


def _pick_col(headers: list[str], norm: list[str], *needles: str) -> str | None:
    """First header that equals a needle, else first that contains one."""
    for needle in needles:
        for h, n in zip(headers, norm):
            if n == needle:
                return h
    for needle in needles:
        for h, n in zip(headers, norm):
            if needle in n:
                return h
    return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    t = str(value).strip()
    t = t.replace("USD", "").replace("$", "").replace(",", "").strip()
    if not t:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _norm_date(value) -> str:
    """Best-effort normalize to ISO YYYY-MM-DD; '' when unparseable."""
    if value is None:
        return ""
    t = str(value).strip()
    if not t:
        return ""
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", t):
        try:
            return datetime.strptime(t, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return ""
    for f in _DATE_FMTS:
        try:
            return datetime.strptime(t, f).date().isoformat()
        except ValueError:
            continue
    return ""


def _sniff(text: str):
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:  # noqa: PERF203 - fall back to plain comma
        return csv.excel


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "earnings.csv"


# ------------------------------------------------------------------- parser

def parse_earnings_csv(text: str) -> dict:
    """Parse raw CSV text into normalized rows with flexible column mapping.

    Returns {headers, columns, rows, skipped, total_rows, has_amount_column}.
    `rows` items: {date, asin, amount, status, product}.
    """
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        raise ValueError("Empty CSV - nothing to import.")
    reader = csv.DictReader(io.StringIO(text), dialect=_sniff(text))
    if not reader.fieldnames:
        raise ValueError("No header row found in the CSV.")

    headers = list(reader.fieldnames)
    norm = [_norm_header(h) for h in headers]

    col_date = _pick_col(headers, norm, "order date", "purchase date",
                         "shipped date", "date")
    col_asin = _pick_col(headers, norm, "asin")
    col_amount = None
    for needle in ("commission", "earning", "revenue", "payout",
                   "amount", "total", "price"):
        col_amount = _pick_col(headers, norm, needle)
        if col_amount:
            break
    col_product = _pick_col(headers, norm, "product name", "product", "title", "item")
    col_status = _pick_col(headers, norm, "status")

    rows: list[dict] = []
    skipped = 0
    for row in reader:
        blob = ""
        if col_asin:
            blob += f" {row.get(col_asin) or ''}"
        if col_product:
            blob += f" {row.get(col_product) or ''}"
        m = ef.ASIN_RE.search(blob.upper())
        if not m:
            skipped += 1
            continue
        rows.append({
            "date": _norm_date(row.get(col_date)) if col_date else "",
            "asin": m.group(0),
            "amount": _to_float(row.get(col_amount)) if col_amount else 0.0,
            "status": (str(row.get(col_status)).strip()
                       if col_status and row.get(col_status) is not None else ""),
            "product": ((str(row.get(col_product)).strip()[:120])
                        if col_product and row.get(col_product) is not None else ""),
        })

    return {
        "headers": headers,
        "columns": {"date": col_date, "asin": col_asin, "amount": col_amount,
                    "status": col_status, "product": col_product},
        "rows": rows,
        "skipped": skipped,
        "total_rows": len(rows) + skipped,
        "has_amount_column": col_amount is not None,
    }


# ------------------------------------------------------------------ import

def import_earnings_text(text: str, original_name: str = "earnings.csv",
                         import_dir: Path | None = None) -> dict:
    """Parse an earnings CSV passed as text; archive the raw copy and write
    the normalized CSV (replacing any previous normalized file)."""
    parsed = parse_earnings_csv(text)
    if not parsed["columns"]["asin"]:
        raise ValueError(
            "No ASIN column found in that CSV. Columns seen: "
            + ", ".join(parsed["headers"]))
    if not parsed["rows"]:
        raise ValueError(
            "Found the columns but zero rows with an ASIN in them - "
            f"skipped {parsed['skipped']} row(s).")

    d = Path(import_dir) if import_dir else IMPORT_DIR
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_copy = d / f"earnings_raw_{ts}_{_slug(original_name)}"
    raw_copy.write_text(text.lstrip("\ufeff"), encoding="utf-8")
    normalized = d / NORMALIZED_NAME
    with normalized.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=NORMALIZED_FIELDS)
        writer.writeheader()
        writer.writerows(parsed["rows"])

    return {**{k: parsed[k] for k in ("headers", "columns", "skipped",
                                      "total_rows", "has_amount_column")},
            "imported": len(parsed["rows"]),
            "raw_copy": str(raw_copy),
            "normalized": str(normalized)}


def import_earnings_path(path: str | Path | None = None,
                         import_dir: Path | None = None) -> dict:
    """Import from a file path, or auto-pick the newest *.csv in the import
    folder (default oink_import/) when `path` is a directory or empty."""
    p = Path(path) if path else IMPORT_DIR
    if p.is_dir():
        candidates = sorted([f for f in p.iterdir()
                             if f.is_file() and f.suffix.lower() == ".csv"
                             and f.name != NORMALIZED_NAME],
                            key=lambda f: f.stat().st_mtime, reverse=True)
        if not candidates:
            raise FileNotFoundError(
                f"No CSV files found in {p} - drop your Oink earnings export "
                f"there (or use the upload button in the UI).")
        p = candidates[0]
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return import_earnings_text(p.read_text(encoding="utf-8-sig", errors="replace"),
                                original_name=p.name, import_dir=import_dir)


# ------------------------------------------------------------------ readers

def read_normalized(import_dir: Path | None = None) -> list[dict]:
    d = Path(import_dir) if import_dir else IMPORT_DIR
    f = d / NORMALIZED_NAME
    if not f.exists():
        return []
    with f.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def read_manifest(out_root: Path | None = None) -> list[dict]:
    out = Path(out_root) if out_root else ef.output_dir(ef.load_config())
    mpath = out / "manifest.csv"
    if not mpath.exists():
        return []
    with mpath.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# -------------------------------------------------------------------- joins

def earnings_by_asin(import_dir: Path | None = None) -> dict[str, dict]:
    """{asin: {total, orders, last_date}} aggregated over the normalized CSV."""
    out: dict[str, dict] = {}
    for row in read_normalized(import_dir):
        agg = out.setdefault(row.get("asin", "").strip().upper(),
                             {"total": 0.0, "orders": 0, "last_date": ""})
        amt = _to_float(row.get("amount"))
        agg["total"] += amt or 0.0
        agg["orders"] += 1
        d = row.get("date") or ""
        if d and d > agg["last_date"]:
            agg["last_date"] = d
    return out


def roi_report(out_root: Path | None = None,
               import_dir: Path | None = None) -> dict:
    """Per-ASIN join of manifest.csv (what you made) with earnings (what it
    earned). Sorted by earnings, highest first."""
    manifest = read_manifest(out_root)
    per: dict[str, dict] = {}
    for row in manifest:
        asin = (row.get("asin") or "").strip().upper()
        if not asin:
            continue
        agg = per.setdefault(asin, {"videos": 0, "duration_s": 0.0,
                                    "words": 0, "last_processed": ""})
        try:
            agg["duration_s"] += float(row.get("duration_s") or 0)
        except ValueError:
            pass
        try:
            agg["words"] += int(float(row.get("words") or 0))
        except ValueError:
            pass
        agg["videos"] += 1
        at = row.get("processed_at") or ""
        if at > agg["last_processed"]:
            agg["last_processed"] = at

    earnings = earnings_by_asin(import_dir)
    all_asins = sorted(set(per) | set(earnings))
    rows = []
    for asin in all_asins:
        m = per.get(asin, {"videos": 0, "duration_s": 0.0, "words": 0,
                           "last_processed": ""})
        e = earnings.get(asin, {"total": 0.0, "orders": 0, "last_date": ""})
        mins = m["duration_s"] / 60.0
        rows.append({
            "asin": asin,
            "videos": m["videos"],
            "duration_s": round(m["duration_s"], 1),
            "words": m["words"],
            "last_processed": m["last_processed"],
            "earnings_total": round(e["total"], 2),
            "orders": e["orders"],
            "last_earn_date": e["last_date"],
            "earnings_per_min": round(e["total"] / mins, 2) if mins > 0 else None,
        })
    rows.sort(key=lambda r: (-r["earnings_total"], r["asin"]))

    return {
        "rows": rows,
        "summary": {
            "total_earnings": round(sum(r["earnings_total"] for r in rows), 2),
            "earning_asins": sum(1 for r in rows if r["earnings_total"] > 0),
            "videos": sum(r["videos"] for r in rows),
            "duration_s": round(sum(r["duration_s"] for r in rows), 1),
        },
    }


def earners_without_video(out_root: Path | None = None,
                          import_dir: Path | None = None) -> list[dict]:
    """ASINs that earned money but have no manifest row yet."""
    manifest_asins = {(r.get("asin") or "").strip().upper()
                      for r in read_manifest(out_root)}
    return [
        {"asin": asin, "total": round(agg["total"], 2), "orders": agg["orders"],
         "last_date": agg["last_date"]}
        for asin, agg in sorted(earnings_by_asin(import_dir).items(),
                                key=lambda kv: -kv[1]["total"])
        if asin not in manifest_asins
    ]
