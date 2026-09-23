"""Stage 1: gather candidate articles and cut them down before any model call.

Order matters. Cheapest checks first: headline/URL duplicates, then the date window,
then the keyword gate. Only what survives all three is worth Claude's attention.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.funding_radar.db import FundingDatabase
from src.funding_radar.models import Article
from src.funding_radar.sources.base import headline_key, http_client, load_config
from src.funding_radar.sources.feeds import fetch_google_news, fetch_rss
from src.funding_radar.sources.gdelt import fetch_gdelt
from src.funding_radar.sources.vc_pages import fetch_vc_page

logger = logging.getLogger(__name__)

# Feeds carry undated items; allow a little slack rather than dropping them.
DEFAULT_MAX_AGE_DAYS = 10


@dataclass
class DiscoveryResult:
    candidates: list[Article] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _is_recent(article: Article, max_age_days: int) -> bool:
    if not article.published_at:
        return True  # undated: let the extractor judge from the text
    try:
        published = datetime.fromisoformat(article.published_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return published >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


# A bare currency symbol is not a funding signal: "save up to $200" is a conference
# advert. Require either a named round, or a funding verb next to a real amount.
STAGE_RE = re.compile(
    r"\b(pre[\s-]?seed|seed round|seed funding|seed extension|series\s+[a-d]\b|"
    r"funding round|investment round|venture round|growth round)\b",
    re.I,
)
AMOUNT_RE = re.compile(
    r"(?:[$€£]\s?\d[\d,.]*\s?(?:k|m|bn|b|million|billion)?|"
    r"\d[\d,.]*\s?(?:million|billion|m|bn)\s?(?:dollars|euros|pounds|usd|eur|gbp|[$€£])?)",
    re.I,
)
FUNDING_VERB_RE = re.compile(
    r"\b(raises|raised|raising|secures|secured|closes|closed|lands|landed|nets|netted|"
    r"bags|bagged|backs|backed|invests|invested|funding|fundraise|financing)\b",
    re.I,
)


def looks_like_funding(text: str) -> bool:
    """True when the text names a round, or pairs a funding verb with an amount."""
    if STAGE_RE.search(text):
        return True
    return bool(FUNDING_VERB_RE.search(text) and AMOUNT_RE.search(text))


def _mentions_funding(article: Article, keywords: list[str]) -> bool:
    return looks_like_funding(f"{article.title} {article.summary}")


def collect(config: dict, *, client=None) -> list:
    """Run every configured source once, returning one SourceResult each."""
    own_client = client is None
    client = client or http_client()
    results = []
    try:
        for feed in config.get("rss", []):
            results.append(fetch_rss(feed["name"], feed["url"]))

        google = config.get("google_news", {})
        for query in google.get("queries", []):
            results.append(fetch_google_news(query, google.get("locale", {}), google.get("window", "when:2d")))

        gdelt = config.get("gdelt", {})
        if gdelt.get("enabled"):
            for query in gdelt.get("queries", []):
                results.append(fetch_gdelt(query, gdelt.get("timespan", "2d"), client=client))

        for page in config.get("vc_pages", []):
            results.append(fetch_vc_page(page["name"], page["url"], client=client))
    finally:
        if own_client:
            client.close()
    return results


def discover(db: FundingDatabase, config: dict, *, client=None, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> DiscoveryResult:
    keywords = config.get("filters", {}).get("keywords", [])
    results = collect(config, client=client)

    seen_keys: set[str] = set()
    candidates: list[Article] = []
    issues: list[dict] = []
    stats = {"sources": len(results), "found": 0, "duplicates": 0, "stale": 0, "off_topic": 0, "candidates": 0}

    for result in results:
        issue = db.record_source(result.name, result.kind, len(result.articles), error=result.error)
        if issue:
            issues.append(issue)
        stats["found"] += len(result.articles)

        for article in result.articles:
            key = headline_key(article.title)
            # Within this run, and against every run before it.
            if key in seen_keys or db.is_article_seen(article.article_id, key):
                stats["duplicates"] += 1
                continue
            seen_keys.add(key)
            if not _is_recent(article, max_age_days):
                stats["stale"] += 1
                db.record_article(article.article_id, article.url, key, outcome="stale")
                continue
            if not _mentions_funding(article, keywords):
                stats["off_topic"] += 1
                db.record_article(article.article_id, article.url, key, outcome="off_topic")
                continue
            candidates.append(article)

    stats["candidates"] = len(candidates)
    logger.info(
        "Discovery: %d sources, %d articles, %d duplicates, %d stale, %d off-topic, %d candidates",
        stats["sources"], stats["found"], stats["duplicates"], stats["stale"], stats["off_topic"], stats["candidates"],
    )
    return DiscoveryResult(candidates=candidates, issues=issues, stats=stats)
