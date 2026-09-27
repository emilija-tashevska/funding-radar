"""Can the owner see, for every in-brief company, what it raised, what it does and
where that comes from? Prints the gaps as numbers, then every in-brief round.

    python scripts/data_audit.py [--days 120] [--all]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.funding_radar.db import FundingDatabase  # noqa: E402
from src.funding_radar.resolve import is_google_news  # noqa: E402
from src.funding_radar.settings import settings  # noqa: E402
from src.funding_radar.site import build_payload  # noqa: E402


def audit(payload: dict) -> dict:
    rounds = [r for r in payload["rounds"] if r["qualified"]]
    direct = lambda r: any(not is_google_news(s["url"]) for s in r["sources"])  # noqa: E731
    return {
        "in_brief": len(rounds),
        "with_amount": sum(1 for r in rounds if r["amount"]),
        "with_any_link": sum(1 for r in rounds if r["sources"]),
        "with_direct_link": sum(1 for r in rounds if direct(r)),
        "with_description": sum(1 for r in rounds if r["description"]),
        "description_with_source": sum(1 for r in rounds if r["description"] and r["description_url"]),
        "only_headline_summary": sum(1 for r in rounds if not r["description"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=120)
    args = parser.parse_args()
    with FundingDatabase(settings.DATABASE_PATH) as db:
        payload = build_payload(db, window_days=args.days)
    numbers = audit(payload)
    lines = ["## Data audit (in-brief rounds)", "", "| check | count |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in numbers.items()]
    lines += ["", "| company | raised | what it does | links |", "|---|---|---|---|"]
    for r in (r for r in payload["rounds"] if r["qualified"]):
        amount = f"{r['amount']:,.0f} {r['currency']}" if r["amount"] else "undisclosed"
        text = (r["description"] or f"(headline only) {r['summary']}").replace("|", "/")[:160]
        links = ", ".join(("direct " if not is_google_news(s["url"]) else "google ") + s["outlet"]
                          for s in r["sources"][:3]).replace("|", "/")
        lines.append(f"| {r['company']} | {amount} | {text} | {links} |")
    out = "\n".join(lines)
    print(out)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(out + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
