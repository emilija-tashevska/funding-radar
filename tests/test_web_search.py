"""Search API responses turned into articles, offline, from recorded shapes."""

import json
from datetime import datetime, timezone

import httpx

from src.funding_radar.models import Article
from src.funding_radar.sources import web_search as ws
from src.funding_radar.sources.base import SourceResult

NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def test_page_ages_in_every_format_become_dates():
    assert ws.parse_page_age("2 days ago", now=NOW).startswith("2026-09-25")
    assert ws.parse_page_age("5 hours ago", now=NOW).startswith("2026-09-27T07")
    assert ws.parse_page_age("September 24, 2026").startswith("2026-09-24")
    assert ws.parse_page_age("2026-09-23").startswith("2026-09-23")
    assert ws.parse_page_age("Tue, 22 Sep 2026 10:00:00 GMT").startswith("2026-09-22")
    assert ws.parse_page_age("") is None and ws.parse_page_age("sometime") is None


ANTHROPIC_CONTENT = [
    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
     "input": {"query": "UK startup raises seed round"}},
    {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": [
        {"type": "web_search_result", "title": "Acme raises £3m seed", "url": "https://uk.test/acme",
         "encrypted_content": "xx", "page_age": "September 24, 2026"},
        {"type": "web_search_result", "title": "Beta secures €2m", "url": "https://eu.test/beta",
         "encrypted_content": "xx", "page_age": None},
        {"type": "web_search_result", "title": "Acme raises £3m seed", "url": "https://uk.test/acme",
         "encrypted_content": "xx", "page_age": None},
    ]},
    {"type": "text", "text": "Acme raises £3m seed | https://uk.test/acme",
     "citations": [{"type": "web_search_result_location", "url": "https://uk.test/acme",
                    "title": "Acme raises £3m seed", "cited_text": "London-based Acme has raised £3m.",
                    "encrypted_index": "x"}]},
]


def test_anthropic_results_become_articles_with_cited_text_as_summary():
    articles = ws.anthropic_articles(ANTHROPIC_CONTENT, "Anthropic search: q")
    assert [a.url for a in articles] == ["https://uk.test/acme", "https://eu.test/beta"]
    assert articles[0].summary == "London-based Acme has raised £3m."
    assert articles[0].published_at.startswith("2026-09-24")
    assert articles[1].published_at is None and articles[0].source_kind == "web_search"


def test_an_anthropic_search_error_yields_no_articles():
    content = [{"type": "web_search_tool_result", "tool_use_id": "x",
                "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}}]
    assert ws.anthropic_articles(content, "q") == []


def test_anthropic_cost_counts_searches_and_tokens():
    usage = {"input_tokens": 9000, "output_tokens": 500, "cache_read_input_tokens": 0,
             "server_tool_use": {"web_search_requests": 1}}
    cost = ws.anthropic_cost(usage)
    assert cost.searches == 1
    assert abs(cost.usd - (0.01 + 9000 * 2e-6 + 500 * 1e-5)) < 1e-9


TAVILY_PAYLOAD = {"query": "q", "results": [
    {"title": "Gamma raises $5M Series A", "url": "https://news.test/gamma",
     "content": "Gamma, the Leeds-based insurer, raised $5M.", "score": 0.9,
     "published_date": "Thu, 24 Sep 2026 09:00:00 GMT"},
    {"title": "No link", "url": "", "content": ""},
]}


def test_tavily_results_become_articles():
    articles = ws.tavily_articles(TAVILY_PAYLOAD, "Tavily: q")
    assert len(articles) == 1
    assert articles[0].summary.startswith("Gamma, the Leeds-based")
    assert articles[0].published_at.startswith("2026-09-24")


def test_tavily_http_errors_are_reported_not_raised():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="bad key"))
    result, cost = ws.search_tavily("q", api_key="k", client=httpx.Client(transport=transport))
    assert result.articles == [] and "401" in result.error and "bad key" in result.error
    assert cost.usd == 0


def test_tavily_asks_for_recent_news():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=TAVILY_PAYLOAD)

    result, cost = ws.search_tavily("q", api_key="k", days=3,
                                    client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["topic"] == "news" and seen["days"] == 3
    assert len(result.articles) == 1 and cost.credits == 1


OPENAI_PAYLOAD = {
    "output": [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {"type": "message", "content": [{"type": "output_text", "text":
            '{"articles": [{"headline": "Delta raises £1.2m pre-seed", "url": "https://uk.test/delta",'
            ' "published": "2026-09-25", "snippet": "Delta, based in Bristol, raised £1.2m."}]}'}]},
    ],
    "usage": {"input_tokens": 8200, "output_tokens": 300},
}


def test_openai_structured_reply_becomes_articles():
    articles = ws.openai_articles(OPENAI_PAYLOAD, "OpenAI search: q")
    assert len(articles) == 1 and articles[0].title == "Delta raises £1.2m pre-seed"
    assert articles[0].published_at.startswith("2026-09-25")
    cost = ws.openai_cost(OPENAI_PAYLOAD)
    assert cost.searches == 1 and cost.input_tokens == 8200


def test_an_openai_reply_that_is_not_json_yields_nothing():
    payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "sorry"}]}]}
    assert ws.openai_articles(payload, "q") == []


def test_the_source_checker_counts_fresh_funding_items():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "verify_sources", Path(__file__).resolve().parent.parent / "scripts" / "verify_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    articles = [
        Article("X", "rss", "Acme raises £3m seed round", "https://x.test/1",
                published_at="2026-09-25T00:00:00+00:00"),
        Article("X", "rss", "Our thoughts on founder wellbeing", "https://x.test/2",
                published_at="2026-01-01T00:00:00+00:00"),
    ]
    row = module.assess(SourceResult("X", "rss", articles), now=NOW)
    assert row["ok"] and row["items"] == 2 and row["funding_like"] == 1
    assert row["last_14_days"] == 1 and row["newest"] == "2026-09-25"
    failed = module.assess(SourceResult("Y", "rss", [], error="403 Forbidden"), now=NOW)
    assert not failed["ok"]


class _FakeResponse:
    def __init__(self, text):
        from types import SimpleNamespace as NS

        self.content = [
            {"type": "web_search_tool_result", "tool_use_id": "s", "content": [
                {"type": "web_search_result", "title": "Acme — about", "url": "https://acme.test/about",
                 "encrypted_content": "x", "page_age": None}]},
            NS(type="text", text=text, citations=None),
        ]
        self.usage = {"input_tokens": 5000, "output_tokens": 100,
                      "server_tool_use": {"web_search_requests": 1}}


def _fake_anthropic(monkeypatch, text):
    import anthropic

    class Client:
        def __init__(self, **kw):
            self.messages = self

        def create(self, **kw):
            return _FakeResponse(text)

    monkeypatch.setattr(anthropic, "Anthropic", Client)


def test_a_search_description_keeps_a_url_the_search_returned(monkeypatch):
    _fake_anthropic(monkeypatch, '{"description": "Acme makes rota software for clinics.", '
                                 '"source_url": "https://acme.test/about"}')
    text, url, cost = ws.describe_by_search("Acme", "", model="m", api_key="k")
    assert (text, url) == ("Acme makes rota software for clinics.", "https://acme.test/about")
    assert cost.searches == 1


def test_a_search_description_citing_an_unreturned_url_is_dropped(monkeypatch):
    _fake_anthropic(monkeypatch, '{"description": "Acme makes rota software.", '
                                 '"source_url": "https://made-up.test/acme"}')
    assert ws.describe_by_search("Acme", "", model="m", api_key="k")[:2] == ("", "")
