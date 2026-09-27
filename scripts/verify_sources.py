"""Fetch every candidate source from where the scan runs, and say which work.

A URL goes into config/sources.yaml only after this has fetched it from GitHub's
runner: Finsmes served a laptop and refused the runner, and seven of fifteen
earlier guessed VC URLs 404'd.

    python scripts/verify_sources.py [--candidates config/candidate_sources.yaml]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from src.funding_radar.discovery import looks_like_funding  # noqa: E402
from src.funding_radar.sources.feeds import fetch_rss  # noqa: E402
from src.funding_radar.sources.vc_pages import fetch_vc_page  # noqa: E402

FRESH_DAYS = 14


def assess(result, *, now: datetime | None = None) -> dict:
    """What a source returned, reduced to the numbers that decide whether to add it."""
    now = now or datetime.now(timezone.utc)
    dates = []
    for article in result.articles:
        if article.published_at:
            try:
                dates.append(datetime.fromisoformat(article.published_at))
            except ValueError:
                pass
    fresh = [d for d in dates if d >= now - timedelta(days=FRESH_DAYS)]
    funding = [a for a in result.articles if looks_like_funding(f"{a.title} {a.summary}")]
    return {
        "name": result.name,
        "ok": not result.error and bool(result.articles),
        "error": result.error,
        "items": len(result.articles),
        "dated": len(dates),
        "newest": max(dates).date().isoformat() if dates else None,
        f"last_{FRESH_DAYS}_days": len(fresh),
        "funding_like": len(funding),
        "examples": [a.title for a in funding[:3]],
    }


def render(rows: list[dict]) -> str:
    lines = ["## Candidate sources, fetched from the runner", "",
             f"| source | ok | items | newest | last {FRESH_DAYS} days | funding-like | error / examples |",
             "|---|---|---|---|---|---|---|"]
    for row in rows:
        note = row["error"][:80] if row["error"] else "; ".join(row["examples"])[:160]
        lines.append(f"| {row['name']} | {'yes' if row['ok'] else 'NO'} | {row['items']} | {row['newest'] or '-'} "
                     f"| {row[f'last_{FRESH_DAYS}_days']} | {row['funding_like']} | {note.replace('|', '/')} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="config/candidate_sources.yaml")
    parser.add_argument("--out", default="verify_report.json")
    args = parser.parse_args()
    candidates = yaml.safe_load(Path(args.candidates).read_text()) or {}

    rows = [assess(fetch_rss(c["name"], c["url"])) | {"url": c["url"], "kind": "rss"}
            for c in candidates.get("rss", [])]
    rows += [assess(fetch_vc_page(c["name"], c["url"])) | {"url": c["url"], "kind": "page"}
             for c in candidates.get("pages", [])]

    Path(args.out).write_text(json.dumps(rows, indent=2))
    text = render(rows)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
