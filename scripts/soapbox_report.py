"""What Soapbox has brought into a database: rounds, how they were read, and gaps.

    DATABASE_PATH=data/x.db python scripts/soapbox_report.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.funding_radar.settings import settings  # noqa: E402


def main() -> int:
    c = sqlite3.connect(settings.DATABASE_PATH)
    c.row_factory = sqlite3.Row
    fetched = c.execute("SELECT outcome, COUNT(*) n FROM articles WHERE url LIKE '%soapbox.vc/feed/%' "
                        "GROUP BY outcome").fetchall()
    rows = c.execute("""
        SELECT co.canonical_name, co.region, r.stage, r.amount_value, r.currency, r.qualified,
               r.qualified_reason, r.announced_date, co.description,
               (SELECT COUNT(DISTINCT outlet) FROM round_sources s2 WHERE s2.round_id = r.round_id) outlets
        FROM rounds r JOIN companies co USING (company_id)
        WHERE r.round_id IN (SELECT round_id FROM round_sources WHERE article_url LIKE '%soapbox.vc/feed/%')
        ORDER BY r.announced_date DESC""").fetchall()
    lines = ["## Soapbox in the database", "",
             "Announcement pages seen, by outcome: " + ", ".join(f"{r['outcome']} {r['n']}" for r in fetched), "",
             f"Rounds evidenced by Soapbox: {len(rows)}; in brief {sum(r['qualified'] for r in rows)}; "
             f"also reported elsewhere {sum(1 for r in rows if r['outlets'] > 1)}",
             "Regions: " + str(dict(Counter(r["region"] or "?" for r in rows))),
             "Stages: " + str(dict(Counter(r["stage"] or "unstated" for r in rows))),
             "Out-of-brief reasons: " + str(dict(Counter(r["qualified_reason"] for r in rows if not r["qualified"]))),
             f"With a description: {sum(1 for r in rows if r['description'])}", "",
             "| company | region | stage | amount | in brief | date | outlets |", "|---|---|---|---|---|---|---|"]
    for r in rows[:40]:
        amount = f"{r['amount_value']:,.0f} {r['currency']}" if r["amount_value"] else "undisclosed"
        lines.append(f"| {r['canonical_name']} | {r['region']} | {r['stage'] or '-'} | {amount} | "
                     f"{'yes' if r['qualified'] else r['qualified_reason']} | {(r['announced_date'] or '')[:10]} | {r['outlets']} |")
    text = "\n".join(lines)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
