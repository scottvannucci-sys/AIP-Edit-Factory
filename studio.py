"""Script Studio: ASIN -> shoppable-video script + teleprompter text.

Template-based generation (no LLM/API bills) with an optional PA-API 5.0
client for pulling real product data when the user configures keys.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STUDIO_DIR = ROOT / "studio"

# ---------------------------------------------------------------- phrase pools

HOOKS = {
    "question": [
        "Okay, have you seen this {thing}? I need to talk about it.",
        "If you've been on the fence about a {thing}, this is your sign.",
        "Why did nobody tell me about this {thing} sooner?",
        "Can we talk about this {thing} for a second?",
    ],
    "claim": [
        "This {thing} just made my {place} so much easier.",
        "I did not expect to love this {thing} as much as I do.",
        "This {thing} solved a problem I didn't know I could solve this cheaply.",
        "Stop scrolling — this {thing} is worth thirty seconds.",
    ],
    "story": [
        "So I ordered this {thing} because {reason}, and honestly? Best decision.",
        "I almost returned this {thing}. Then I actually used it.",
        "I've been testing this {thing} for a bit — here's my honest take.",
    ],
}
REASONS = [
    "my old one finally gave up on me",
    "it kept showing up in my feed",
    "I was tired of the cheap version I had",
    "a friend wouldn't stop recommending it",
]
LEADINS = {
    "friendly": ["Here's the thing —", "What sold me:", "The part I didn't expect:",
                 "Real talk:", "My favorite bit:"],
    "expert": ["Spec worth knowing:", "Key detail:", "Build quality note:",
               "The technical bit:", "What sets it apart:"],
    "playful": ["Okay, the fun part —", "Wait for it:", "The flex:",
                "Chef's kiss detail:", "Plot twist:"],
}
CTAS = {
    "friendly": ["It's linked right on my storefront — go check it out!",
                 "Grab it through the link on my storefront if it fits your setup."],
    "expert": ["Full specs are in the link on my storefront.",
               "If the specs fit your needs, it's linked on my storefront."],
    "playful": ["It's on my storefront — thank me later!",
                "Link's on my storefront. You're welcome."],
}
PLACES = ["kitchen", "setup", "desk", "routine", "day"]
STOPWORDS = {"the", "a", "an", "for", "with", "and", "of", "to", "in", "on",
             "my", "your", "set", "pack", "new", "inch", "oz", "pc"}


def _short_title(title: str, cap: int = 60) -> str:
    t = (title or "").split("|")[0].split(",")[0].strip()
    return (t[: cap - 1] + "…") if len(t) > cap else t


def _keyword(title: str) -> str:
    words = re.findall(r"[A-Za-z]+", _short_title(title, 80).lower())
    for w in words:
        if w not in STOPWORDS and len(w) > 2:
            return w
    return words[0] if words else "product"


def _clean_bullet(b: str) -> str:
    b = re.sub(r"\s+", " ", (b or "").strip()).rstrip(".")
    return b[0].lower() + b[1:] if b[:1].isupper() and len(b) > 2 else b


def _rng(seed) -> random.Random:
    if seed is None:
        seed = f"{time.time_ns()}"[-9:]
    return random.Random(str(seed))


# ---------------------------------------------------------------- generation

def generate_script(asin: str, title: str, bullets: list[str],
                    themes: list[str] | None = None, price: str = "",
                    tone: str = "friendly", seed=None) -> dict:
    """Build a structured 30-60s shoppable-video script. Pure function."""
    tone = tone if tone in HOOKS else "friendly"
    rng = _rng(seed)
    thing = _short_title(title) or "product"
    kw = _keyword(title or thing)

    hooks = [{"style": s, "text": rng.choice(pool).format(thing=thing, reason=rng.choice(REASONS))}
             for s, pool in HOOKS.items()]

    points = []
    leadins = rng.sample(LEADINS[tone], k=min(3, len(LEADINS[tone])))
    for i, b in enumerate([_clean_bullet(x) for x in bullets if x.strip()][:3]):
        points.append({"text": f"{leadins[i % len(leadins)]} {b}."})
    if themes:
        th = _clean_bullet(themes[0])
        points.append({"text": f"Reviewers keep mentioning {th}, and that matched my experience."})

    cta = rng.choice(CTAS[tone])
    if price:
        cta += f" And at {price}, it's an easy yes."

    onscreen = []
    for b in [thing] + [_clean_bullet(x) for x in bullets if x.strip()][:3]:
        onscreen.append(" ".join(b.split()[:5]).upper().rstrip(",."))
    onscreen = list(dict.fromkeys(onscreen))[:4]

    shots = [
        f"Hook: hold the {kw} up to camera, start talking immediately",
        f"B-roll: hands-only close-up of the {kw} in use",
        "Mid: point at the on-screen caption text as you mention it",
        f"CTA: smile, tap toward the storefront link",
    ]
    if len(points) > 2:
        shots.insert(2, "Cut to a different angle or distance to keep pace")

    flat = hooks[0]["text"] + " " + " ".join(p["text"] for p in points) + " " + cta
    words = len(flat.split())
    duration_s = round(words / 2.6)  # ~155 wpm delivery

    return {
        "asin": (asin or "").strip().upper(),
        "title": title or thing,
        "price": price or "",
        "tone": tone,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "hooks": hooks,
        "points": points,
        "cta": cta,
        "onscreen": onscreen,
        "shots": shots,
        "words": words,
        "duration_s": duration_s,
    }


def script_to_teleprompter(script: dict, hook_index: int = 0) -> str:
    hooks = script.get("hooks") or [{}]
    hook = hooks[min(hook_index, len(hooks) - 1)].get("text", "")
    body = "\n\n".join(p["text"] for p in script.get("points", []))
    return "\n\n".join(x for x in [hook, body, script.get("cta", "")] if x)


# ------------------------------------------------- campaign mode (Studio 2.0)

def campaign_script(asin: str, requirements=None, campaign: str = "",
                    brand: str = "", title: str = "", bullets=None,
                    themes=None, price: str = "", tone: str = "friendly",
                    seed=None, cfg=None) -> dict:
    """Weave a Creator Connections brief into a shoppable-video script.

    Requirements become priority talking points (they lead the point list,
    since generate_script uses at most three); the finished teleprompter
    text is then checked against each requirement (judgments.brief_supported)
    and scanned for risky claims (judgments.claims_risk) - so the creator
    sees coverage and risk BEFORE filming. Deterministic like the rest of
    Studio; local heuristics unless TypeSafe is configured.
    """
    import judgments as jd  # lazy: keeps studio importable standalone

    requirements = [str(r).strip() for r in (requirements or []) if str(r).strip()]
    reqs_clean = [_clean_bullet(r) for r in requirements]
    have = {r.lower() for r in reqs_clean}
    user_bullets = [b for b in (_clean_bullet(x) for x in (bullets or []))
                    if b.lower() not in have]
    merged = reqs_clean + user_bullets
    fallback_title = " ".join(x for x in [brand, campaign] if x) or "campaign product"
    script = generate_script(asin, title or fallback_title, merged,
                             themes=themes, price=price, tone=tone,
                             seed=seed or asin)
    tele = script_to_teleprompter(script)
    script["campaign"] = {
        "name": campaign,
        "brand": brand,
        "requirements": requirements,
        "coverage": [{"requirement": r,
                      **jd.brief_supported(tele, r, cfg)}
                     for r in requirements],
        "claims_risk": jd.claims_risk(tele, cfg),
    }
    return script


def brand_pitch(asin: str, campaign: str = "", brand: str = "",
                requirements=None, cfg=None, out_root=None) -> dict:
    """Draft a Creator Connections submission message grounded in the
    creator's own transcript history for this ASIN. Deterministic; the
    creator edits before sending (Oink handles sending at scale)."""
    import oink_data
    import transcript_intel as ti

    asin = (asin or "").strip().upper()
    reqs = [str(r).strip() for r in (requirements or []) if str(r).strip()]

    videos = 0
    for row in oink_data.read_manifest(out_root):
        if (row.get("asin") or "").strip().upper() == asin:
            videos += 1

    opening_quote = ""
    has_transcript = False
    tr = ti.transcript_for(asin, out_root)
    if tr and tr["events"]:
        has_transcript = True
        opening_quote = tr["events"][0][2]

    lines = [f"Hi {brand or 'there'},"]
    lines.append("")
    camp_bit = f'"{campaign}" ' if campaign else "your campaign "
    lines.append(f"I'd love to create content for {camp_bit}featuring {asin}.")
    lines.append("")
    lines.append("What I'd deliver:")
    lines.append("- A 30-45s vertical shoppable video on my Amazon storefront "
                 "(hook in the first two seconds, captions burned in)")
    lines.append("- An on-camera demo of the product in use")
    if reqs:
        lines.append("- Your required talking points, verbatim:")
        lines.extend(f"  * {r}" for r in reqs)
    lines.append("- Ready-to-submit file named for the ASIN, plus "
                 "platform-ready cuts on request")
    lines.append("")
    if videos > 0:
        lines.append(f"Track record: {videos} video(s) for this ASIN already "
                     f"live on my storefront.")
        if opening_quote:
            lines.append(f'Most recent opening line: "{opening_quote}"')
    else:
        lines.append("This will be my first video for this ASIN - I already "
                     "have the script and shot list drafted.")
    lines.append("")
    lines.append("Happy to share a draft before it goes live. Thanks for "
                 "considering me!")

    return {"asin": asin, "campaign": campaign, "brand": brand,
            "requirements": reqs, "videos": videos,
            "has_transcript": has_transcript,
            "opening_quote": opening_quote, "pitch": "\n".join(lines)}


# ---------------------------------------------------------------- PA-API 5.0

def _paapi_cfg(cfg: dict) -> dict:
    p = cfg.get("pa_api") or {}
    return {k: (p.get(k) or "").strip()
            for k in ("access_key", "secret_key", "partner_tag", "marketplace")}


def paapi_configured(cfg: dict) -> bool:
    p = _paapi_cfg(cfg)
    return all((p["access_key"], p["secret_key"], p["partner_tag"]))


def fetch_product(asin: str, cfg: dict) -> dict:
    """PA-API GetItems -> {title, features, price}. Raises RuntimeError when
    unconfigured or the call fails. AWS SigV4 via stdlib only."""
    p = _paapi_cfg(cfg)
    if not all((p["access_key"], p["secret_key"], p["partner_tag"])):
        raise RuntimeError(
            "PA-API not configured. Add your access_key / secret_key / "
            "partner_tag under \"pa_api\" in config.json (get them from "
            "Associates Central > Tools > Product Advertising API), or just "
            "fill the product details manually.")
    asin = asin.strip().upper()
    if not re.fullmatch(r"B0[A-Z0-9]{8}", asin):
        raise RuntimeError(f"Not a valid ASIN: {asin!r}")

    host, region, service = "webservices.amazon.com", "us-east-1", "ProductAdvertisingAPI"
    path = "/paapi5/getitems"
    amz_date = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz_date[:8]
    payload = json.dumps({
        "ItemIds": [asin],
        "Resources": ["ItemInfo.Title", "ItemInfo.Features",
                      "Offers.Listings.Price"],
    }, separators=(",", ":"))

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    canonical = "\n".join([
        "POST", path, "",  # no query
        f"content-encoding:amz-1.0\ncontent-type:application/json; charset=utf-8\n"
        f"host:{host}\nx-amz-date:{amz_date}\n"
        f"x-amz-target:com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems",
        f"content-encoding;content-type;host;x-amz-date;x-amz-target",
        hashlib.sha256(payload.encode()).hexdigest(),
    ])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical.encode()).hexdigest(),
    ])
    k = _sign(_sign(_sign(_sign(f"AWS4{p['secret_key']}".encode(), datestamp),
                          region), service), "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        f"https://{host}{path}", data=payload.encode(), method="POST",
        headers={
            "Content-Encoding": "amz-1.0",
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-Amz-Date": amz_date,
            "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems",
            "Authorization": (f"AWS4-HMAC-SHA256 Credential="
                              f"{p['access_key']}/{scope}, "
                              f"SignedHeaders=content-encoding;content-type;host;"
                              f"x-amz-date;x-amz-target, Signature={signature}"),
        })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"PA-API returned {e.code}: {body}") from None
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"PA-API request failed: {e}") from None

    try:
        item = data["ItemsResult"]["Items"][0]
        info = item.get("ItemInfo", {})
        title = info.get("Title", {}).get("DisplayValue", "")
        features = info.get("Features", {}).get("DisplayValues", [])
        offers = item.get("Offers", {}).get("Listings", [])
        price = offers[0].get("Price", {}).get("DisplayAmount", "") if offers else ""
        return {"asin": asin, "title": title, "features": features, "price": price}
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected PA-API response shape ({e})") from None


# ---------------------------------------------------------------- storage

def _sid() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1000:03d}"


def save_script(script: dict) -> dict:
    STUDIO_DIR.mkdir(exist_ok=True)
    sid = _sid()
    script = dict(script)
    script["id"] = sid
    (STUDIO_DIR / f"{sid}.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"id": sid}


def _read_sid(sid: str) -> Path:
    if not re.fullmatch(r"\d{8}_\d{6}_\d{3}", sid or ""):
        raise ValueError("bad id")
    return STUDIO_DIR / f"{sid}.json"


def load_script(sid: str) -> dict:
    return json.loads(_read_sid(sid).read_text(encoding="utf-8"))


def delete_script(sid: str) -> None:
    _read_sid(sid).unlink(missing_ok=True)


def list_scripts() -> list[dict]:
    if not STUDIO_DIR.exists():
        return []
    out = []
    for f in sorted(STUDIO_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d.get("id", f.stem),
                        "asin": d.get("asin", ""),
                        "title": d.get("title", ""),
                        "tone": d.get("tone", ""),
                        "duration_s": d.get("duration_s", 0),
                        "created_at": d.get("created_at", "")})
        except Exception:  # noqa: BLE001
            continue
    return out
