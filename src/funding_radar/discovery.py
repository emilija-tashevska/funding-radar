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
from src.funding_radar.sources.base import company_hint, headline_key, http_client, load_config
from src.funding_radar.sources.feeds import fetch_google_news, fetch_rss
from src.funding_radar.sources.gdelt import fetch_gdelt
from src.funding_radar.sources.vc_pages import fetch_vc_page

logger = logging.getLogger(__name__)

# Feeds carry undated items; allow a little slack rather than dropping them.
DEFAULT_MAX_AGE_DAYS = 10


@dataclass
class Candidate:
    """One story, plus every other article covering it.

    Two outlets reporting the same raise is corroboration, so the duplicates are
    kept rather than dropped. The primary is a directly-linked article where one
    exists, because Google News links are consent redirects.
    """

    article: Article
    duplicates: list[Article] = field(default_factory=list)

    @property
    def articles(self) -> list[Article]:
        return [self.article, *self.duplicates]

    @property
    def source_count(self) -> int:
        return len({a.source.split(" · ")[0] for a in self.articles})


@dataclass
class DiscoveryResult:
    candidates: list[Candidate] = field(default_factory=list)
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
# Non-English patterns matter because Google News locales and German/French/Nordic
# feeds carry rounds the English-language press never covers.
STAGE_RE = re.compile(
    r"\b(pre[\s-]?seed|seed round|seed funding|seed extension|series\s+[a-d]\b|"
    r"funding round|investment round|venture round|growth round|"
    r"finanzierungsrunde|kapitalrunde|wachstumsrunde|"          # de
    r"lev[ée]e de fonds|tour de table|"                          # fr
    r"financieringsronde|investeringsronde|"                     # nl
    r"finansieringsrunda|emissionsrunda|"                        # sv
    r"ronda de financiaci[óo]n|ronda de inversi[óo]n)\b",        # es
    re.I,
)
AMOUNT_RE = re.compile(
    r"(?:[$€£]\s?\d[\d,.]*\s?(?:k|m|bn|b|million|billion)?|"
    r"\d[\d,.]*\s?(?:million|billion|m|bn|millionen|millions|miljoen|miljoner|millones|mln)"
    r"\s?(?:euro|euros|dollars|pounds|usd|eur|gbp|[$€£])?)",
    re.I,
)
FUNDING_VERB_RE = re.compile(
    r"\b(raises|raised|raising|secures|secured|closes|closed|lands|landed|nets|netted|"
    r"bags|bagged|backs|backed|invests|invested|funding|fundraise|financing|"
    r"erh[äa]lt|sammelt|sichert|einsammeln|finanzierung|"                      # de
    r"l[èe]ve|lev[ée]e|obtient|financement|"                                   # fr
    r"haalt|ophaalt|opgehaald|investering|"                                           # nl
    r"h[äa]mtar in|reser|finansiering|"                                        # sv
    r"recauda|capta|financiaci[óo]n)\b",                                      # es
    re.I,
)


# A VC announcing its own fund reads exactly like a company raising a round, and
# there are enough of them to be worth excluding before paying for extraction.
FUND_RAISE_RE = re.compile(
    r"\b(first|final|second|third)\s+clos(e|ing)\b|"
    r"\b(fund\s+(i{1,3}|iv|v|vi{0,3}|\d+)|(debut|maiden|new|third|fourth|fifth|sixth)\s+fund)\b|"
    r"\braises?\s+[^.]{0,40}\bfund\b|\bfund\s+to\s+(back|invest|chase)\b|"
    r"\b(venture (capital )?(firm|fund)|vc firm)\b",
    re.I,
)


def looks_like_fund_raise(text: str) -> bool:
    """True when the money is going to an investor, not an operating company."""
    return bool(FUND_RAISE_RE.search(text))


def looks_like_funding(text: str) -> bool:
    """True when the text names a round, or pairs a funding verb with an amount."""
    if looks_like_fund_raise(text):
        return False
    if STAGE_RE.search(text):
        return True
    if looks_like_fund_raise(text):
        return False
    return bool(FUNDING_VERB_RE.search(text) and AMOUNT_RE.search(text))


def _mentions_funding(article: Article, keywords: list[str]) -> bool:
    return looks_like_funding(f"{article.title} {article.summary}")


def _prefer_direct_link(candidate: Candidate) -> None:
    """Promote a directly-linked article over a Google News redirect."""
    if candidate.article.source_kind != "google_news":
        return
    direct = next((a for a in candidate.duplicates if a.source_kind != "google_news"), None)
    if direct:
        candidate.duplicates.remove(direct)
        candidate.duplicates.append(candidate.article)
        candidate.article = direct


def collect(config: dict, *, client=None) -> list:
    """Run every configured source once, returning one SourceResult each."""
    own_client = client is None
    client = client or http_client()
    results = []
    try:
        for feed in config.get("rss", []):
            results.append(fetch_rss(feed["name"], feed["url"]))

        google = config.get("google_news", {})
        window = google.get("window", "when:3d")
        for query in google.get("queries", []):
            results.append(fetch_google_news(query, google.get("locale", {}), window))
        for locale in google.get("extra_locales", []):
            results.append(fetch_google_news(locale["query"], locale, window))

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

    by_key: dict[str, Candidate] = {}
    issues: list[dict] = []
    stats = {"sources": len(results), "found": 0, "duplicates": 0, "stale": 0, "off_topic": 0, "candidates": 0}

    for result in results:
        issue = db.record_source(result.name, result.kind, len(result.articles), error=result.error)
        if issue:
            issues.append(issue)
        stats["found"] += len(result.articles)

        for article in result.articles:
            key = headline_key(article.title)
            if key in by_key:
                # Same story from another outlet: corroboration, not waste.
                by_key[key].duplicates.append(article)
                stats["duplicates"] += 1
                continue
            if db.is_article_seen(article.article_id, key):
                stats["duplicates"] += 1
                continue
            if not _is_recent(article, max_age_days):
                stats["stale"] += 1
                db.record_article(article, key, outcome="stale", company_hint=company_hint(article.title))
                continue
            if not _mentions_funding(article, keywords):
                stats["off_topic"] += 1
                db.record_article(article, key, outcome="off_topic", company_hint=company_hint(article.title))
                continue
            by_key[key] = Candidate(article=article)

    for candidate in by_key.values():
        _prefer_direct_link(candidate)
    candidates = list(by_key.values())
    stats["candidates"] = len(candidates)
    stats["corroborated"] = sum(1 for c in candidates if c.source_count > 1)
    logger.info(
        "Discovery: %d sources, %d articles, %d duplicates, %d stale, %d off-topic, "
        "%d candidates (%d corroborated)",
        stats["sources"], stats["found"], stats["duplicates"], stats["stale"],
        stats["off_topic"], stats["candidates"], stats["corroborated"],
    )
    return DiscoveryResult(candidates=candidates, issues=issues, stats=stats)
