"""Fetch the body of an article, for the few cases where the headline is not enough.

Most headlines name the company that raised. A minority do not ("A startup that
builds other startups raised $100M"), and those are real rounds we would otherwise
throw away, so we pay for one fetch before giving up on them.
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from src.funding_radar.sources.base import http_client

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 4000
# A consent redirect, not an article: fetching it returns Google's interstitial.
UNFETCHABLE = ("news.google.com",)


def fetch_article_text(url: str, *, client=None, max_chars: int = MAX_BODY_CHARS) -> str:
    """Return the readable text of an article, or "" when it cannot be read."""
    if not url or any(host in url for host in UNFETCHABLE):
        return ""
    own_client = client is None
    client = client or http_client()
    try:
        response = client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        container = soup.find("article") or soup.find("main") or soup.body
        if container is None:
            return ""
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = " ".join(p for p in paragraphs if p) or container.get_text(" ", strip=True)
        return " ".join(text.split())[:max_chars]
    except Exception as exc:  # network, parsing, anything: this is best-effort
        logger.info("Could not read the body of %s: %s", url, exc)
        return ""
    finally:
        if own_client:
            client.close()
