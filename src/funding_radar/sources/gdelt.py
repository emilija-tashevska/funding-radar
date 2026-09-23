"""GDELT article search: free, global, and useful for non-English European coverage."""

from __future__ import annotations

import logging
import time
import urllib.parse

from src.funding_radar.models import Article
from src.funding_radar.sources.base import SourceResult, http_client, to_iso

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 75
RETRY_WAITS = (5, 15)


def fetch_gdelt(query: str, timespan: str, *, client=None) -> SourceResult:
    """One GDELT query. It rate-limits aggressively, so back off rather than hammer it."""
    name = f"GDELT: {query[:48]}"
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": MAX_RECORDS,
        "format": "json",
        "timespan": timespan,
    }
    url = f"{GDELT_DOC_API}?{urllib.parse.urlencode(params)}"
    own_client = client is None
    client = client or http_client()
    try:
        for attempt, wait in enumerate((0, *RETRY_WAITS)):
            if wait:
                time.sleep(wait)
            response = client.get(url)
            if response.status_code == 429:
                logger.info("%s: rate limited, backing off", name)
                continue
            response.raise_for_status()
            # GDELT answers 200 with HTML when it is unhappy with a query.
            if "application/json" not in response.headers.get("content-type", ""):
                raise RuntimeError(f"non-JSON response: {response.text[:120]}")
            payload = response.json()
            articles = [
                Article(
                    source=f"GDELT · {item.get('domain', '')}".strip(" ·"),
                    source_kind="gdelt",
                    title=item.get("title", "").strip(),
                    url=item.get("url", "").strip(),
                    summary="",
                    published_at=to_iso(item.get("seendate")),
                )
                for item in payload.get("articles", [])
                if item.get("title") and item.get("url")
            ]
            return SourceResult(name, "gdelt", articles)
        return SourceResult(name, "gdelt", [], error="rate limited after retries")
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: failed: %s", name, exc)
        return SourceResult(name, "gdelt", [], error=str(exc)[:300])
    finally:
        if own_client:
            client.close()
