"""Fund announcement pages. Layouts differ, so this reads links generically.

Precision is deliberately low here: the keyword gate and the extractor decide what
is a funding announcement. The job of this module is to miss nothing obvious.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from src.funding_radar.models import Article
from src.funding_radar.sources.base import SourceResult, http_client, strip_html, to_iso

logger = logging.getLogger(__name__)

MIN_HEADLINE_CHARS = 25
MAX_HEADLINE_CHARS = 200
SKIP_URL_RE = re.compile(
    r"(mailto:|/tag/|/category/|/author/|/page/|linkedin\.com|twitter\.com|x\.com|"
    r"\.pdf$|/privacy|/terms|/cookie|/contact|/team|/jobs)",
    re.I,
)
DATE_ATTRS = ("datetime", "data-date", "data-published")


def fetch_vc_page(name: str, url: str, *, client=None) -> SourceResult:
    own_client = client is None
    client = client or http_client()
    try:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        host = urlparse(url).netloc
        seen: set[str] = set()
        articles: list[Article] = []
        for anchor in soup.find_all("a", href=True):
            headline = strip_html(anchor.get_text())
            if not (MIN_HEADLINE_CHARS <= len(headline) <= MAX_HEADLINE_CHARS):
                continue
            link = urljoin(url, anchor["href"].strip())
            if SKIP_URL_RE.search(link) or link in seen:
                continue
            # Off-site links on a fund's news page are usually press coverage, which
            # other sources already cover; keep to the fund's own posts.
            if urlparse(link).netloc and urlparse(link).netloc != host:
                continue
            seen.add(link)
            time_tag = anchor.find_parent().find("time") if anchor.find_parent() else None
            published = None
            if time_tag:
                published = to_iso(next((time_tag.get(attr) for attr in DATE_ATTRS if time_tag.get(attr)), None))
            articles.append(
                Article(
                    source=name,
                    source_kind="vc_page",
                    title=headline,
                    url=link,
                    summary="",
                    published_at=published,
                )
            )
        return SourceResult(name, "vc_page", articles)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: page failed: %s", name, exc)
        return SourceResult(name, "vc_page", [], error=str(exc)[:300])
    finally:
        if own_client:
            client.close()
