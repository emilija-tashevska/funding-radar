"""Soapbox (soapbox.vc): a UK register of funding announcements, one page per round.

There is no RSS feed on the site. Explored from the runner on 2026-09-29:
- robots.txt allows everything except /api/, which this never touches.
- sitemap.xml lists every announcement at /feed/<slug> (1,475 then), without dates.
- /feed, the news page, loads its list with JavaScript: no links in the HTML.
- /investors/<fund> pages list that fund's announcements as ordinary links.
- Each announcement page carries the headline, date and body (see parse_item).

So: read the sitemap and the followed funds' investor pages, and fetch the
announcement pages we have not seen yet, a capped number per run and paced.
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.funding_radar.models import Article
from src.funding_radar.sources.base import SourceResult, get_with_agents, http_client, strip_html, to_iso

logger = logging.getLogger(__name__)

BASE = "https://www.soapbox.vc"
ITEM_PREFIX = "/feed/"
NAME = "Soapbox"
PAUSE_SECONDS = 1.0
# If Soapbox starts refusing us, stop rather than try every page: each refused page
# costs three agents and their timeouts, enough to run the whole scan out of time.
MAX_CONSECUTIVE_FAILURES = 5
# A hard ceiling on the time Soapbox may take in one scan, whatever happens.
TIME_BUDGET_SECONDS = 300


def is_item(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("soapbox.vc") and parsed.path.startswith(ITEM_PREFIX) \
        and len(parsed.path) > len(ITEM_PREFIX)


def item_urls_from_sitemap(xml: str) -> list[str]:
    """Announcement URLs in the order the sitemap lists them, without duplicates."""
    soup = BeautifulSoup(xml, "xml")
    urls, seen = [], set()
    for loc in soup.find_all("loc"):
        url = loc.get_text(strip=True)
        if is_item(url) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def item_urls_from_page(html: str, base: str = BASE) -> list[str]:
    """Announcement links on a server-rendered page, such as /investors/<fund>."""
    soup = BeautifulSoup(html, "lxml")
    urls, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        url = urljoin(base, anchor["href"].strip()).split("#")[0].split("?")[0]
        if is_item(url) and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _news_article(soup: BeautifulSoup) -> dict:
    """The schema.org NewsArticle each announcement page carries, or {}."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in (data.get("@graph") or [data]) if isinstance(data, dict) else data:
            if isinstance(node, dict) and node.get("@type") == "NewsArticle":
                return node
    return {}


def _body_text(soup: BeautifulSoup, headline: str) -> str:
    """The press text: the page's paragraphs, from the first one that reads like prose."""
    paragraphs = []
    for p in soup.find_all("p"):
        text = " ".join(p.get_text(" ", strip=True).split())
        if len(text) >= 60 and text != headline:
            paragraphs.append(text)
    return " ".join(paragraphs)


def parse_item(html: str, url: str) -> Article | None:
    """One announcement page as an Article: headline, date, what the company does, press text.

    Soapbox is a register of UK funding rounds, and its headlines rarely say where the
    company is; the summary says so, so the extractor does not fall back to "other".
    """
    soup = BeautifulSoup(html, "lxml")
    news = _news_article(soup)
    headline = strip_html(news.get("headline") or "")
    if not headline:
        h1 = soup.find("h1")
        headline = " ".join(h1.get_text(" ", strip=True).split()) if h1 else ""
    if not headline:
        return None
    meta = soup.find("meta", attrs={"name": "description"})
    description = strip_html(news.get("description") or (meta.get("content") if meta else "") or "")
    body = _body_text(soup, headline)
    summary = " ".join(part for part in (
        "Reported by Soapbox, a register of UK startup funding rounds.", description, body) if part)
    return Article(source=NAME, source_kind="soapbox", title=headline, url=url,
                   summary=summary[:1200], published_at=to_iso(news.get("datePublished")))


def fetch_soapbox(conf: dict, *, seen=None, client=None) -> SourceResult:
    """New announcements: from the followed funds' pages first, then the sitemap.

    Only pages not already stored are fetched, at most `max_items_per_run`, paced,
    so the first runs work through the backlog a slice at a time.
    """
    seen = seen or (lambda url: False)
    cap = int(conf.get("max_items_per_run", 40))
    max_age = int(conf.get("max_age_days", 120))
    own = client is None
    client = client or http_client()
    queue: list[str] = []
    errors: list[str] = []
    try:
        for fund in conf.get("investor_pages", []):
            try:
                page = get_with_agents(client, f"{BASE}/investors/{fund}")
                queue += item_urls_from_page(page.text)
            except Exception as exc:  # noqa: BLE001 - one missing fund page must not stop the rest
                errors.append(f"investors/{fund}: {str(exc)[:80]}")
            time.sleep(PAUSE_SECONDS)
        sitemap_count = 0
        try:
            listed = item_urls_from_sitemap(get_with_agents(client, f"{BASE}/sitemap.xml").text)
            sitemap_count = len(listed)
            if not listed:
                errors.append("sitemap: lists no announcements (format changed?)")
            queue += listed
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sitemap: {str(exc)[:120]}")

        todo, queued = [], set()
        for url in queue:
            if url not in queued and not seen(url):
                queued.add(url)
                todo.append(url)
        articles: list[Article] = []
        failures_in_a_row, started = 0, time.monotonic()
        for url in todo[:cap]:
            if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
                errors.append(f"stopped after {failures_in_a_row} failures in a row")
                break
            if time.monotonic() - started > TIME_BUDGET_SECONDS:
                errors.append(f"stopped at the {TIME_BUDGET_SECONDS}s time budget")
                break
            try:
                article = parse_item(get_with_agents(client, url).text, url)
            except Exception as exc:  # noqa: BLE001
                article = None
                errors.append(f"{urlparse(url).path}: {str(exc)[:80]}")
            else:
                if article is None:
                    errors.append(f"{urlparse(url).path}: no headline")
                elif not article.published_at:
                    # Undated, it would pass every freshness check and send the whole
                    # backlog to the extractor. A dateless page means the layout changed.
                    errors.append(f"{urlparse(url).path}: no date")
                    article = None
            if article:
                articles.append(article)
                failures_in_a_row = 0
            else:
                failures_in_a_row += 1
            time.sleep(PAUSE_SECONDS)
        logger.info("Soapbox: %d unseen announcements, %d fetched this run, %d errors",
                    len(todo), len(articles), len(errors))
        # Health: no sitemap, or nothing fetched when there was something to fetch, is
        # a failure; a few pages or one fund page failing is not.
        sitemap_failed = any(e.startswith("sitemap") for e in errors)
        stopped = any(e.startswith("stopped") for e in errors)
        broken = sitemap_failed or stopped or (errors and todo and not articles)
        error = "; ".join(errors[-3:] if stopped else errors[:3]) if broken else ""
        return SourceResult(NAME, "soapbox", articles, error=error, max_age_days=max_age,
                            health_items=sitemap_count)
    finally:
        if own:
            client.close()


if __name__ == "__main__":
    # A standalone look from the runner: what one scan would take from Soapbox.
    #   DATABASE_PATH=data/x.db python -m src.funding_radar.sources.soapbox
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    from src.funding_radar.db import FundingDatabase
    from src.funding_radar.discovery import looks_like_funding
    from src.funding_radar.settings import settings
    from src.funding_radar.sources.base import load_config

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conf = load_config().get("soapbox", {})
    with FundingDatabase(settings.DATABASE_PATH) as db:
        result = fetch_soapbox(conf, seen=lambda u: db.is_article_seen(Article("", "", "", u).article_id, ""))
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(conf.get("max_age_days", 120)))
    dated = [a for a in result.articles if a.published_at]
    recent = [a for a in dated if datetime.fromisoformat(a.published_at) >= cutoff]
    months = Counter(a.published_at[:7] for a in dated)
    print(f"\nSoapbox: {len(result.articles)} announcements fetched, {len(dated)} dated, "
          f"{len(recent)} inside {conf.get('max_age_days', 120)} days, "
          f"{sum(1 for a in recent if looks_like_funding(a.title + ' ' + a.summary))} look like funding; "
          f"error: {result.error or 'none'}")
    print("by month:", dict(sorted(months.items(), reverse=True)))
    for article in result.articles[:15]:
        print(f"  {article.published_at[:10] if article.published_at else '?':10} {article.title[:110]}")
