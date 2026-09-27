"""Search APIs as a discovery source: Anthropic web search, Tavily, OpenAI web search.

Each one only finds articles. The structure (company, amount, stage, investors,
region) still comes from extract.py, so a round found here goes through the same
extractor, rules and identity code as one found in a feed. The searches run on the
provider's side, not from GitHub's runner, which is the point: some publishers
refuse the runner's IP.

Every call reports what it cost, in the provider's own units, so the providers can
be compared on new in-brief rounds per dollar rather than on how they feel.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from src.funding_radar.models import Article
from src.funding_radar.sources.base import SourceResult, strip_html, to_iso

logger = logging.getLogger(__name__)

# Anthropic: $10 per 1,000 searches, plus tokens (Sonnet 5: $2 / $10 per MTok).
ANTHROPIC_SEARCH_USD = 0.01
ANTHROPIC_TOKEN_USD = {"input": 2.0 / 1_000_000, "output": 10.0 / 1_000_000}
# Tavily: a basic search is one credit; pay-as-you-go is $0.008 a credit, and the
# first 1,000 credits a month are free.
TAVILY_CREDIT_USD = 0.008
# OpenAI: $10 per 1,000 web search calls, plus tokens at the model's own rate, which
# is reported as tokens rather than guessed in dollars.
OPENAI_SEARCH_USD = 0.01

ANTHROPIC_TOOL = "web_search_20250305"   # basic search: discovery needs no filtering code
TAVILY_URL = "https://api.tavily.com/search"
OPENAI_URL = "https://api.openai.com/v1/responses"

SEARCH_SYSTEM = (
    "You find startup funding announcements. Search once for the query you are given. "
    "Then list every article in the results that reports a company raising money, one "
    "per line, as: headline | url. Nothing else."
)

# What OpenAI is asked to return, through its structured outputs.
OPENAI_SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "url": {"type": "string"},
                    "published": {"type": "string"},
                    "snippet": {"type": "string"},
                },
                "required": ["headline", "url", "published", "snippet"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["articles"],
    "additionalProperties": False,
}


@dataclass
class SearchCost:
    searches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    credits: int = 0
    usd: float = 0.0
    notes: list[str] = field(default_factory=list)

    def add(self, other: "SearchCost") -> None:
        self.searches += other.searches
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.credits += other.credits
        self.usd += other.usd
        self.notes.extend(other.notes)


_AGO_RE = re.compile(r"^(\d+)\s+(minute|hour|day|week)s?\s+ago$", re.I)


def parse_page_age(value: str | None, *, now: datetime | None = None) -> str | None:
    """Search engines date results loosely: "2 days ago", "September 24, 2026", ISO."""
    if not value:
        return None
    text = str(value).strip()
    match = _AGO_RE.match(text)
    if match:
        now = now or datetime.now(timezone.utc)
        amount, unit = int(match.group(1)), match.group(2).lower()
        return (now - timedelta(**{f"{unit}s": amount})).isoformat()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return to_iso(text)


# ---- Anthropic ------------------------------------------------------------

def anthropic_articles(content: list, name: str) -> list[Article]:
    """Articles from the web_search_tool_result blocks of one response.

    Result titles and URLs are plain text; the page content is encrypted, so a
    citation's quoted text, when the model cites a result, is the only snippet.
    """
    snippets: dict[str, str] = {}
    for block in content:
        for citation in _get(block, "citations") or []:
            url, cited = _get(citation, "url"), _get(citation, "cited_text")
            if url and cited:
                snippets.setdefault(url, cited)

    articles, seen = [], set()
    for block in content:
        if _get(block, "type") != "web_search_tool_result":
            continue
        results = _get(block, "content")
        if not isinstance(results, list):   # an error arrives as one object
            continue
        for result in results:
            url = _get(result, "url")
            if not url or url in seen:
                continue
            seen.add(url)
            articles.append(Article(
                source=name, source_kind="web_search", title=strip_html(_get(result, "title") or ""),
                url=url, summary=snippets.get(url, ""),
                published_at=parse_page_age(_get(result, "page_age"))))
    return articles


def anthropic_cost(usage) -> SearchCost:
    server = _get(usage, "server_tool_use") or {}
    searches = int(_get(server, "web_search_requests") or 0)
    input_tokens = int(_get(usage, "input_tokens") or 0) + int(_get(usage, "cache_read_input_tokens") or 0)
    output_tokens = int(_get(usage, "output_tokens") or 0)
    usd = (searches * ANTHROPIC_SEARCH_USD + input_tokens * ANTHROPIC_TOKEN_USD["input"]
           + output_tokens * ANTHROPIC_TOKEN_USD["output"])
    return SearchCost(searches=searches, input_tokens=input_tokens, output_tokens=output_tokens, usd=usd)


def search_anthropic(query: str, *, model: str, api_key: str, country: str = "GB",
                     max_uses: int = 1) -> tuple[SourceResult, SearchCost]:
    import anthropic

    name = f"Anthropic search: {query[:60]}"
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=4000, system=SEARCH_SYSTEM,
            output_config={"effort": "low"},
            tools=[{"type": ANTHROPIC_TOOL, "name": "web_search", "max_uses": max_uses,
                    "user_location": {"type": "approximate", "country": country}}],
            messages=[{"role": "user", "content": query}],
        )
    except Exception as exc:  # noqa: BLE001 - one failed search must not end the probe
        return SourceResult(name, "web_search", [], error=str(exc)[:300]), SearchCost()
    return (SourceResult(name, "web_search", anthropic_articles(response.content, name)),
            anthropic_cost(response.usage))


# ---- Tavily ---------------------------------------------------------------

def tavily_articles(payload: dict, name: str) -> list[Article]:
    articles = []
    for result in payload.get("results") or []:
        if not result.get("url"):
            continue
        articles.append(Article(
            source=name, source_kind="web_search", title=strip_html(result.get("title") or ""),
            url=result["url"], summary=strip_html(result.get("content") or "")[:1200],
            published_at=parse_page_age(result.get("published_date"))))
    return articles


def search_tavily(query: str, *, api_key: str, days: int = 3, max_results: int = 20,
                  client: httpx.Client | None = None) -> tuple[SourceResult, SearchCost]:
    name = f"Tavily: {query[:60]}"
    body = {"query": query, "topic": "news", "days": days, "search_depth": "basic",
            "max_results": max_results}
    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        response = client.post(TAVILY_URL, json=body, headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return SourceResult(name, "web_search", [], error=_http_error(exc)), SearchCost()
    finally:
        if own:
            client.close()
    return (SourceResult(name, "web_search", tavily_articles(payload, name)),
            SearchCost(searches=1, credits=1, usd=TAVILY_CREDIT_USD))


# ---- OpenAI ---------------------------------------------------------------

def openai_articles(payload: dict, name: str) -> list[Article]:
    """Articles from the structured JSON in a Responses API reply."""
    text = ""
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                text += part.get("text") or ""
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("%s: reply was not JSON", name)
        return []
    articles = []
    for entry in parsed.get("articles") or []:
        if not entry.get("url"):
            continue
        articles.append(Article(
            source=name, source_kind="web_search", title=strip_html(entry.get("headline") or ""),
            url=entry["url"], summary=strip_html(entry.get("snippet") or "")[:1200],
            published_at=parse_page_age(entry.get("published"))))
    return articles


def openai_cost(payload: dict) -> SearchCost:
    searches = sum(1 for item in payload.get("output") or [] if item.get("type") == "web_search_call")
    usage = payload.get("usage") or {}
    return SearchCost(searches=searches, input_tokens=int(usage.get("input_tokens") or 0),
                      output_tokens=int(usage.get("output_tokens") or 0),
                      usd=searches * OPENAI_SEARCH_USD,
                      notes=["OpenAI usd covers the search fee only; tokens are billed on top"])


def search_openai(query: str, *, model: str, api_key: str, country: str = "GB",
                  client: httpx.Client | None = None) -> tuple[SourceResult, SearchCost]:
    name = f"OpenAI search: {query[:60]}"
    body = {
        "model": model,
        "tools": [{"type": "web_search",
                   "user_location": {"type": "approximate", "country": country}}],
        "instructions": SEARCH_SYSTEM.replace("one per line, as: headline | url. Nothing else.",
                                              "in the requested JSON."),
        "input": query,
        "text": {"format": {"type": "json_schema", "name": "articles", "schema": OPENAI_SCHEMA,
                            "strict": True}},
    }
    own = client is None
    client = client or httpx.Client(timeout=120)
    try:
        response = client.post(OPENAI_URL, json=body, headers={"Authorization": f"Bearer {api_key}"})
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return SourceResult(name, "web_search", [], error=_http_error(exc)), SearchCost()
    finally:
        if own:
            client.close()
    return SourceResult(name, "web_search", openai_articles(payload, name)), openai_cost(payload)


# ---- helpers --------------------------------------------------------------

def _get(obj, key):
    """Read a field from an SDK object or a plain dict alike."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _http_error(exc: Exception) -> str:
    body = getattr(getattr(exc, "response", None), "text", "") or ""
    return f"{exc}"[:200] + (f" | {body[:300]}" if body else "")
