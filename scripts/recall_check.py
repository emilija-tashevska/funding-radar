"""Measure what the pipeline misses.

Extraction accuracy says nothing about rounds we never saw. This runs a set of
benchmark queries the pipeline does not use, and reports how many of the funding
stories they surface are already known to us.

    python scripts/recall_check.py
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402

from src.funding_radar.discovery import looks_like_funding  # noqa: E402
from src.funding_radar.sources.base import company_hint  # noqa: E402
from src.funding_radar.sources.feeds import google_news_url  # noqa: E402

# Deliberately different phrasing and outlets from config/sources.yaml.
BENCHMARK_QUERIES = [
    '"has raised" startup London funding',
    'startup "funding round" UK investors',
    '"backed by" startup Europe raises millions',
    'European startup investment round announced',
]
BENCHMARK_FEEDS = [
    ("AlleyWatch", "https://www.alleywatch.com/feed/"),
    ("Startup Daily", "https://www.startupdaily.net/feed/"),
]
# Coverage is only meaningful for rounds inside the brief: UK and Europe. The
# benchmark deliberately includes global outlets, so those stories are separated
# out rather than counted as misses.
IN_SCOPE_RE = re.compile(
    r"\b(uk|u\.k\.|britain|british|england|scotland|wales|ireland|irish|london|manchester|"
    r"edinburgh|bristol|cambridge|oxford|leeds|dublin|europe|european|eu|germany|german|"
    r"berlin|munich|hamburg|france|french|paris|netherlands|dutch|amsterdam|spain|spanish|"
    r"madrid|barcelona|italy|italian|milan|rome|sweden|swedish|stockholm|denmark|danish|"
    r"copenhagen|norway|oslo|finland|finnish|helsinki|poland|polish|warsaw|portugal|lisbon|"
    r"switzerland|swiss|zurich|austria|vienna|belgium|brussels|estonia|tallinn|iceland|"
    r"€|£)\b",
    re.I,
)


def pipeline_companies() -> tuple[set[str], int]:
    """Run discovery in memory and return the companies it would put in front of us."""
    from src.funding_radar.db import FundingDatabase
    from src.funding_radar.discovery import discover
    from src.funding_radar.sources.base import load_config

    with tempfile.TemporaryDirectory() as tmp:
        db = FundingDatabase(Path(tmp) / "recall.db")
        try:
            result = discover(db, load_config())
        finally:
            db.close()
    companies = {company_hint(c.article.title) for c in result.candidates}
    return {c for c in companies if c}, len(result.candidates)


def benchmark_stories() -> dict[str, str]:
    """Funding stories from queries and outlets the pipeline does not use."""
    found: dict[str, str] = {}
    for query in BENCHMARK_QUERIES:
        feed = google_news_url(query, {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"}, "when:3d")
        for entry in feedparser.parse(feed).entries:
            if looks_like_funding(entry.title):
                found[company_hint(entry.title)] = entry.title
    for name, url in BENCHMARK_FEEDS:
        for entry in feedparser.parse(url).entries:
            title = getattr(entry, "title", "")
            if looks_like_funding(title):
                found[company_hint(title)] = f"{title} [{name}]"
    found.pop("", None)
    return found


def main() -> None:
    companies, candidate_count = pipeline_companies()
    found = benchmark_stories()
    if not found:
        raise SystemExit("Benchmark returned nothing; check the queries.")

    in_scope = {k: t for k, t in found.items() if IN_SCOPE_RE.search(t)}
    out_of_scope = {k: t for k, t in found.items() if k not in in_scope}
    missed = {k: t for k, t in in_scope.items() if k not in companies}
    covered = len(in_scope) - len(missed)
    print(f"Pipeline candidates this run: {candidate_count} ({len(companies)} distinct companies)")
    print(f"Benchmark stories:            {len(found)}  ({len(in_scope)} UK/Europe, {len(out_of_scope)} elsewhere)")
    print(f"In-scope coverage:            {covered}/{len(in_scope)}  ({covered / max(len(in_scope), 1):.0%})")
    print()
    for title in list(missed.values())[:20]:
        print(f"  MISSED  {title[:104]}")


if __name__ == "__main__":
    main()
