"""Turn a Google News link into the publisher's own URL.

Google News RSS links (news.google.com/rss/articles/<id>) open a Google page that
redirects with JavaScript, and in the UK that page is a cookie-consent wall first.
So a round found only through Google News had neither a link that opens the
article directly nor a body we could read.

Google resolves the id itself in two steps: the article page carries a signature
and timestamp, and a batchexecute call exchanges id + signature for the URL. This
is undocumented and may change; every failure returns "" and the caller keeps the
Google link, so the worst case is the old behaviour.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

from src.funding_radar.sources.base import http_client

logger = logging.getLogger(__name__)

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_ID_RE = re.compile(r"/(?:rss/)?articles/([A-Za-z0-9_-]+)")


def is_google_news(url: str) -> bool:
    return urlparse(url or "").netloc.endswith("news.google.com")


def article_id(url: str) -> str:
    match = _ID_RE.search(urlparse(url or "").path)
    return match.group(1) if match else ""


def decoding_params(html: str) -> tuple[str, str]:
    """The signature and timestamp Google puts on the article page."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one("[data-n-a-sg][data-n-a-ts]")
    if node is None:
        return "", ""
    return node.get("data-n-a-sg", ""), node.get("data-n-a-ts", "")


def batch_payload(gn_id: str, signature: str, timestamp: str) -> str:
    inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gn_id}",{timestamp},"{signature}"]')
    return "f.req=" + quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))


def parse_batch_response(text: str) -> str:
    """The URL inside batchexecute's reply, or "" if it is not there."""
    try:
        body = text.split("\n\n", 1)[1]
        for entry in json.loads(body):
            if isinstance(entry, list) and len(entry) > 2 and entry[0] == "wrb.fr" and entry[2]:
                url = json.loads(entry[2])[1]
                if isinstance(url, str) and url.startswith("http"):
                    return url
    except (IndexError, ValueError, TypeError):
        pass
    return ""


def resolve_google_news(url: str, *, client=None) -> str:
    """The publisher URL behind a Google News link, or "" when it cannot be had."""
    gn_id = article_id(url)
    if not is_google_news(url) or not gn_id:
        return ""
    own = client is None
    client = client or http_client()
    try:
        page = client.get(f"https://news.google.com/rss/articles/{gn_id}")
        page.raise_for_status()
        signature, timestamp = decoding_params(page.text)
        if not signature or not timestamp:
            return ""
        reply = client.post(BATCH_URL, content=batch_payload(gn_id, signature, timestamp),
                            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        reply.raise_for_status()
        resolved = parse_batch_response(reply.text)
        return "" if is_google_news(resolved) else resolved
    except Exception as exc:  # noqa: BLE001 - best effort; the Google link still works
        logger.info("Could not resolve %s: %s", url[:80], exc)
        return ""
    finally:
        if own:
            client.close()
