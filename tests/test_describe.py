"""Descriptions written from the article body, offline: fetches and model mocked."""

import pytest

from src.funding_radar import describe as describe_mod
from src.funding_radar import pipeline
from src.funding_radar.db import FundingDatabase
from src.funding_radar.discovery import Candidate
from src.funding_radar.models import Article, Round
from src.funding_radar.qualify import Rules

RULES = Rules(min_amount=0, stages=("pre-seed", "seed", "series a", "series b"),
              regions=("uk", "europe"), investor_floors={})
CONFIG = {"filters": {"window_days": 3650}}
BODY = "Acme builds scheduling software for NHS clinics, replacing paper rotas. " * 8


@pytest.fixture
def db(tmp_path):
    with FundingDatabase(tmp_path / "d.db") as database:
        yield database


def _store(db, company, url, region="uk", source_kind="rss"):
    article = Article(source="Outlet", source_kind=source_kind, title=f"{company} raises £2m",
                      url=url, published_at="2026-09-20T00:00:00+00:00")
    round_ = Round(company=company, stage="seed", amount_value=2_000_000, currency="GBP",
                   region=region, round_date="2026-09-20T00:00:00+00:00", summary="Short.")
    round_id, _ = pipeline.store_round(db, round_, Candidate(article=article), RULES)
    return round_id


@pytest.fixture
def no_network(monkeypatch):
    monkeypatch.setattr(describe_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(describe_mod, "resolve_google_news", lambda url, client=None: "")
    monkeypatch.setattr(describe_mod, "fetch_article_text", lambda url, client=None: "")


def _model(descriptions):
    def fake(system, user, schema, **kw):
        return {"results": [{"index": i, "description": d} for i, d in enumerate(descriptions)]}
    return fake


def test_a_company_is_described_from_its_article(db, no_network, monkeypatch):
    _store(db, "Acme", "https://uktech.test/acme")
    monkeypatch.setattr(describe_mod, "fetch_article_text", lambda url, client=None: BODY)
    monkeypatch.setattr(describe_mod, "complete_json", _model(["Acme builds scheduling software for NHS clinics."]))
    stats = describe_mod.describe(db, CONFIG, client=object())
    company = db.find_company("", "Acme")
    assert company["description"] == "Acme builds scheduling software for NHS clinics."
    assert company["description_url"] == "https://uktech.test/acme"
    assert stats["described"] == 1 and stats["bodies_read"] == 1


def test_a_google_link_is_resolved_stored_and_read(db, no_network, monkeypatch):
    gn = "https://news.google.com/rss/articles/CBMiABC"
    round_id = _store(db, "Acme", gn, source_kind="google_news")
    monkeypatch.setattr(describe_mod, "resolve_google_news", lambda url, client=None: "https://sifted.test/acme")
    read = []
    monkeypatch.setattr(describe_mod, "fetch_article_text", lambda url, client=None: read.append(url) or BODY)
    monkeypatch.setattr(describe_mod, "complete_json", _model(["Scheduling software for clinics."]))
    stats = describe_mod.describe(db, CONFIG, client=object())
    assert read == ["https://sifted.test/acme"]
    assert db.round_sources(round_id)[0]["resolved_url"] == "https://sifted.test/acme"
    assert stats["resolved_links"] == 1


def test_out_of_brief_companies_are_not_described(db, no_network, monkeypatch):
    _store(db, "Yankee", "https://tc.test/y", region="us")
    monkeypatch.setattr(describe_mod, "complete_json", lambda *a, **k: pytest.fail("model called"))
    assert describe_mod.describe(db, CONFIG, client=object())["needed"] == 0


def test_an_unreadable_company_is_tried_once_not_every_run(db, no_network, monkeypatch):
    _store(db, "Acme", "https://blocked.test/acme")
    monkeypatch.setattr(describe_mod, "complete_json", lambda *a, **k: pytest.fail("model called"))
    first = describe_mod.describe(db, CONFIG, client=object())
    assert first["no_text"] == 1
    assert describe_mod.describe(db, CONFIG, client=object())["needed"] == 0


def test_an_empty_answer_is_not_stored_as_a_description(db, no_network, monkeypatch):
    _store(db, "Acme", "https://uktech.test/acme")
    monkeypatch.setattr(describe_mod, "fetch_article_text", lambda url, client=None: BODY)
    monkeypatch.setattr(describe_mod, "complete_json", _model([""]))
    stats = describe_mod.describe(db, CONFIG, client=object())
    company = db.find_company("", "Acme")
    assert company["description"] == "" and company["description_url"] == ""
    assert stats["described"] == 0 and stats["no_text"] == 1


def test_a_failed_model_call_leaves_the_company_for_the_next_run(db, no_network, monkeypatch):
    from src.funding_radar.llm import LLMError

    _store(db, "Acme", "https://uktech.test/acme")
    monkeypatch.setattr(describe_mod, "fetch_article_text", lambda url, client=None: BODY)

    def boom(*a, **k):
        raise LLMError("overloaded")

    monkeypatch.setattr(describe_mod, "complete_json", boom)
    assert describe_mod.describe(db, CONFIG, client=object())["failed_batches"] == 1
    assert describe_mod.describe(db, CONFIG, client=object())["needed"] == 1


def test_a_short_page_is_not_mistaken_for_an_article(db, no_network, monkeypatch):
    _store(db, "Acme", "https://paywall.test/acme")
    monkeypatch.setattr(describe_mod, "fetch_article_text", lambda url, client=None: "Subscribe to read.")
    monkeypatch.setattr(describe_mod, "complete_json", lambda *a, **k: pytest.fail("model called"))
    assert describe_mod.describe(db, CONFIG, client=object())["bodies_read"] == 0


def test_the_search_fallback_only_runs_when_switched_on(db, no_network, monkeypatch):
    from src.funding_radar.sources import web_search
    from src.funding_radar.sources.web_search import SearchCost

    _store(db, "Acme", "https://blocked.test/acme")
    calls = []
    monkeypatch.setattr(web_search, "describe_by_search", lambda *a, **k: (
        calls.append(a) or ("Acme makes rota software.", "https://acme.test/about", SearchCost(usd=0.04))))
    monkeypatch.setattr(describe_mod.settings, "ANTHROPIC_API_KEY", "k")
    describe_mod.describe(db, CONFIG, client=object())
    assert calls == []
    stats = describe_mod.describe(db, CONFIG | {"describe": {"web_search_fallback": True}}, client=object())
    assert len(calls) == 1 and stats["described_by_search"] == 1
    assert db.find_company("", "Acme")["description_url"] == "https://acme.test/about"
