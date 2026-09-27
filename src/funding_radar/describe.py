"""Stage 6: one or two sentences on what each in-brief company does.

The extractor reads headlines, and a headline says who raised, not what they do,
so its one-line summary is often a fragment ("Life sciences AI platform."). Here
the article itself is read and Claude writes the description from that text only.

A round seen only through Google News has no link to read, so its Google link is
resolved to the publisher's URL first; that also gives the page a direct link.
When no article can be read, a paid web search can stand in, if config allows it.
"""

from __future__ import annotations

import logging
import time

from src.funding_radar.db import FundingDatabase
from src.funding_radar.fulltext import fetch_article_text
from src.funding_radar.llm import LLMError, complete_json
from src.funding_radar.resolve import is_google_news, resolve_google_news
from src.funding_radar.settings import settings
from src.funding_radar.sources.base import http_client

logger = logging.getLogger(__name__)

BATCH_SIZE = 6
MIN_BODY_CHARS = 300
RESOLVE_PAUSE_SECONDS = 1.0   # Google throttles bursts

SYSTEM_PROMPT = """You describe startups for someone deciding whether to call them.

For each company you are given the text of an article about its funding round.
Write one or two plain sentences saying what the company does: the product, and who
it is for. Use only what the article says. Do not mention the funding round, the
amount or the investors. Do not use marketing language ("revolutionary",
"leading"). If the article does not say what the company does, return an empty
description rather than guessing."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "description": {"type": "string"}},
                "required": ["index", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def needs_description(row: dict) -> bool:
    """Not yet tried. A company already tried and left empty waits for the fallback."""
    return not (row.get("description") or "").strip() and not row.get("described_at")


def readable_urls(db: FundingDatabase, row: dict, *, client, budget: dict) -> list[str]:
    """Direct links first, then Google links resolved to the publisher (and stored)."""
    direct, resolved = [], []
    for source in row.get("sources") or []:
        url = source.get("article_url") or ""
        if not is_google_news(url):
            direct.append(url)
        elif source.get("resolved_url"):
            resolved.append(source["resolved_url"])
        elif budget["resolves"] > 0:
            budget["resolves"] -= 1
            found = resolve_google_news(url, client=client)
            time.sleep(RESOLVE_PAUSE_SECONDS)
            if found:
                db.set_resolved_url(row["round_id"], url, found)
                resolved.append(found)
                budget["resolved"] += 1
    return [u for u in direct + resolved if u]


def describe(db: FundingDatabase, config: dict, *, limit: int = 60, max_resolves: int = 80,
             client=None) -> dict:
    """Describe up to `limit` in-brief companies that have no description yet."""
    conf = config.get("describe", {}) or {}
    window = int((config.get("filters", {}) or {}).get("window_days", 120))
    stats = {"needed": 0, "resolved_links": 0, "bodies_read": 0, "described": 0,
             "described_by_search": 0, "no_text": 0, "search_usd": 0.0, "failed_batches": 0}
    seen, todo = set(), []
    for row in db.list_rounds(window_days=window, qualified_only=True):
        if row["company_id"] in seen or not needs_description(row):
            continue
        seen.add(row["company_id"])
        todo.append(row)
    stats["needed"] = len(todo)
    todo = todo[:limit]

    own = client is None
    client = client or http_client()
    budget = {"resolves": max_resolves, "resolved": 0}
    readable, unreadable = [], []
    try:
        for row in todo:
            body, used = "", ""
            for url in readable_urls(db, row, client=client, budget=budget):
                body = fetch_article_text(url, client=client)
                if len(body) >= MIN_BODY_CHARS:
                    used = url
                    break
            if used:
                readable.append((row, body, used))
            else:
                unreadable.append(row)
    finally:
        if own:
            client.close()
    stats["resolved_links"] = budget["resolved"]
    stats["bodies_read"] = len(readable)

    for start in range(0, len(readable), BATCH_SIZE):
        batch = readable[start:start + BATCH_SIZE]
        prompt = "\n\n".join(f"[{i}] {row['company']}\n{body}" for i, (row, body, _) in enumerate(batch))
        try:
            payload = complete_json(SYSTEM_PROMPT, prompt, SCHEMA, model=settings.EXTRACT_MODEL,
                                    effort=settings.EXTRACT_EFFORT)
        except LLMError as exc:
            logger.warning("Description batch failed: %s", exc)
            stats["failed_batches"] += 1
            continue   # not marked as tried, so the next run tries again
        written = {item.get("index"): (item.get("description") or "").strip()
                   for item in payload.get("results", [])}
        for i, (row, _, url) in enumerate(batch):
            text = written.get(i, "")
            db.set_description(row["company_id"], text, url if text else "")
            if text:
                stats["described"] += 1
            else:
                unreadable.append(row)

    for row in unreadable:
        db.set_description(row["company_id"], "", "")   # tried; the fallback may fill it
    stats["no_text"] = len(unreadable)

    if conf.get("web_search_fallback"):
        _describe_by_search(db, window, int(conf.get("max_searches", 20)), stats)
    return stats


def _describe_by_search(db: FundingDatabase, window: int, cap: int, stats: dict) -> None:
    from src.funding_radar.sources.web_search import describe_by_search

    if not settings.ANTHROPIC_API_KEY:
        return
    done = set()
    for row in db.list_rounds(window_days=window, qualified_only=True):
        if cap <= 0:
            break
        if row["company_id"] in done or (row.get("description") or "").strip():
            continue
        done.add(row["company_id"])
        cap -= 1
        text, url, cost = describe_by_search(row["company"], row.get("summary") or "",
                                             model=settings.EXTRACT_MODEL,
                                             api_key=settings.ANTHROPIC_API_KEY)
        stats["search_usd"] += cost.usd
        if text:
            db.set_description(row["company_id"], text, url)
            stats["described_by_search"] += 1
