"""RSS feeds and Google News searches. Both are RSS; only the URL differs."""

from __future__ import annotations

import logging
import urllib.parse

import feedparser

from src.funding_radar.models import Article
from src.funding_radar.sources.base import SourceResult, strip_html, to_iso

logger = logging.getLogger(__name__)

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def _entries_to_articles(entries: list, source: str, kind: str) -> list[Article]:
    articles = []
    for entry in entries:
        title = strip_html(getattr(entry, "title", ""))
        link = getattr(entry, "link", "")
        if not title or not link:
            continue
        # Google News reports the original outlet in <source>; keep it, because the
        # link itself is a Google redirect and the outlet matters for credibility.
        publisher = ""
        if isinstance(getattr(entry, "source", None), dict):
            publisher = entry.source.get("title", "")
        articles.append(
            Article(
                source=f"{source} · {publisher}" if publisher else source,
                source_kind=kind,
                title=title,
                url=link,
                summary=strip_html(getattr(entry, "summary", ""))[:1200],
                published_at=to_iso(getattr(entry, "published_parsed", None) or getattr(entry, "published", None)),
            )
        )
    return articles


def fetch_rss(name: str, url: str) -> SourceResult:
    try:
        parsed = feedparser.parse(url)
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            raise RuntimeError(str(getattr(parsed, "bozo_exception", "unparseable feed"))[:200])
        return SourceResult(name, "rss", _entries_to_articles(parsed.entries, name, "rss"))
    except Exception as exc:  # noqa: BLE001 - one broken feed must not stop the run
        logger.warning("%s: feed failed: %s", name, exc)
        return SourceResult(name, "rss", [], error=str(exc)[:300])


def google_news_url(query: str, locale: dict, window: str) -> str:
    full_query = f"{query} {window}".strip()
    params = {
        "q": full_query,
        "hl": locale.get("hl", "en-GB"),
        "gl": locale.get("gl", "GB"),
        "ceid": locale.get("ceid", "GB:en"),
    }
    return f"{GOOGLE_NEWS_RSS}?{urllib.parse.urlencode(params)}"


def fetch_google_news(query: str, locale: dict, window: str) -> SourceResult:
    name = f"Google News: {query[:48]}"
    try:
        parsed = feedparser.parse(google_news_url(query, locale, window))
        if getattr(parsed, "bozo", 0) and not parsed.entries:
            raise RuntimeError(str(getattr(parsed, "bozo_exception", "unparseable feed"))[:200])
        return SourceResult(name, "google_news", _entries_to_articles(parsed.entries, "Google News", "google_news"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed: %s", name, exc)
        return SourceResult(name, "google_news", [], error=str(exc)[:300])
