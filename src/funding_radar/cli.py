"""Command line: python -m src.funding_radar.cli <command>"""

from __future__ import annotations

import argparse
import json
import logging

from src.funding_radar.db import FundingDatabase
from src.funding_radar.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Funding Radar")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Discover, extract, qualify and store one run")
    run_p.add_argument("--limit", type=int, help="Cap extraction calls (useful for a first look)")
    run_p.add_argument("--dry-run", action="store_true", help="Discover only; no model calls, nothing stored")

    list_p = sub.add_parser("rounds", help="Print stored rounds")
    list_p.add_argument("--days", type=int, default=120)
    list_p.add_argument("--all", action="store_true", help="Include rounds that did not qualify")

    sub.add_parser("sources", help="Per-source health from the last run")

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "run":
        if args.dry_run:
            from src.funding_radar.discovery import discover
            from src.funding_radar.sources.base import load_config

            with FundingDatabase(settings.DATABASE_PATH) as db:
                result = discover(db, load_config())
            print(json.dumps(result.stats, indent=2))
            for candidate in result.candidates[:40]:
                print(f"  [{candidate.article.source_kind}] {candidate.article.title[:96]}")
            return
        from src.funding_radar.pipeline import run

        if args.limit:
            settings.MAX_EXTRACTIONS_PER_RUN = args.limit
        print(json.dumps(run(), indent=2))

    elif args.command == "rounds":
        with FundingDatabase(settings.DATABASE_PATH) as db:
            rounds = db.list_rounds(window_days=args.days, qualified_only=not args.all)
        for row in rounds:
            amount = f"{row['currency']} {row['amount_value']:,.0f}" if row["amount_value"] else "undisclosed"
            flags = "".join(["✓" if row["confirmed"] else "·", "!" if row["amount_disputed"] else " "])
            investors = ", ".join(row["investors"][:3])
            print(f"{flags} {(row['announced_date'] or '')[:10]}  {row['company'][:28]:<28} "
                  f"{(row['stage'] or '?'):<10} {amount:<18} {row['region']:<7} {row['sector'][:24]:<24} {investors[:40]}")
        print(f"\n{len(rounds)} round(s)")

    elif args.command == "sources":
        with FundingDatabase(settings.DATABASE_PATH) as db:
            for row in db.list_source_health():
                print(f"{row['status']:<7} {row['items_found']:>4} (median {row['median_items'] or 0:>5}) "
                      f"{row['source'][:58]:<58} {row['error'][:40]}")


if __name__ == "__main__":
    main()
