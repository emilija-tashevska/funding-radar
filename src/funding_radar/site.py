"""Build the static site: one self-contained page with its data inlined.

Inlining keeps it working from a file:// path as well as from Pages, and leaves
one obvious place to encrypt later when the passphrase goes on.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.funding_radar.db import FundingDatabase
from src.funding_radar.qualify import approx_usd
from src.funding_radar.settings import PROJECT_ROOT, settings

TEMPLATE = PROJECT_ROOT / "templates" / "index.html"
PLACEHOLDER = "__DATA__"
STAGE_ORDER = ["pre-seed", "seed", "series a", "series b", "series c", "series d+", "growth", "unstated"]
REGION_ORDER = ["uk", "europe", "us", "other"]


def _round_payload(row: dict) -> dict:
    return {
        "id": row["round_id"],
        "company": row["company"],
        "domain": row.get("domain") or "",
        "summary": row.get("summary") or row.get("company_summary") or "",
        "stage": row.get("stage") or "",
        "amount": row.get("amount_value"),
        "currency": row.get("currency") or "",
        "amount_usd": row.get("amount_usd") or approx_usd(row.get("amount_value"), row.get("currency") or "USD") or 0,
        "date": row.get("announced_date") or row.get("first_seen_at"),
        "region": row.get("region") or "other",
        "sector": row.get("sector") or "",
        "hq": ", ".join(part for part in (row.get("hq_city"), row.get("hq_country")) if part),
        "ai_native": bool(row.get("ai_native")),
        "is_studio": bool(row.get("is_studio")),
        "confirmed": bool(row.get("confirmed")),
        "disputed": bool(row.get("amount_disputed")),
        "qualified": bool(row.get("qualified")),
        "qualified_reason": row.get("qualified_reason") or "",
        "investors": row.get("investors") or [],
        "evidence": row.get("evidence") or "",
        "sources": [
            {"outlet": source.get("outlet") or "", "url": source.get("url") or source.get("article_url") or "",
             "title": source.get("title") or ""}
            for source in row.get("sources") or []
        ],
    }


def build_payload(db: FundingDatabase, *, window_days: int = 120) -> dict:
    rows = db.list_rounds(window_days=window_days, qualified_only=False)
    rounds = [_round_payload(row) for row in rows]
    stages = [stage for stage in STAGE_ORDER
              if any((r["stage"] or "unstated") == stage for r in rounds)]
    regions = [region for region in REGION_ORDER if any(r["region"] == region for r in rounds)]
    outlets = {source["outlet"] for r in rounds for source in r["sources"] if source["outlet"]}
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": window_days,
            "total": len(rounds),
            "qualified": sum(1 for r in rounds if r["qualified"]),
            "confirmed": sum(1 for r in rounds if r["confirmed"]),
            "outlets": len(outlets),
            "stages": stages,
            "regions": regions,
        },
        "rounds": rounds,
    }


def render(payload: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise ValueError(f"{TEMPLATE} no longer contains {PLACEHOLDER}")
    # </script> inside the data would close the tag that carries it.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, data)


def build(*, out_dir: Path | None = None, window_days: int = 120,
          db: FundingDatabase | None = None) -> Path:
    own_db = db is None
    db = db or FundingDatabase(settings.DATABASE_PATH)
    try:
        payload = build_payload(db, window_days=window_days)
    finally:
        if own_db:
            db.close()
    out_dir = Path(out_dir or settings.SITE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "index.html"
    page.write_text(render(payload), encoding="utf-8")
    # Also on its own, for eyeballing what the page was given.
    (out_dir / "data.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return page
