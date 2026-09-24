from datetime import datetime, timedelta, timezone

import pytest

from src.funding_radar import discovery
from src.funding_radar.db import FundingDatabase
from src.funding_radar.models import Article
from src.funding_radar.sources.base import SourceResult

CONFIG = {"filters": {"keywords": ["raises", "funding", "series a"]}}


@pytest.fixture
def db(tmp_path):
    database = FundingDatabase(tmp_path / "f.db")
    yield database
    database.close()


def _article(title, url, source="TechCrunch", kind="rss", published=None):
    return Article(source=source, source_kind=kind, title=title, url=url, published_at=published)


def _collect(results):
    return lambda config, client=None: results


def test_only_recent_on_topic_new_articles_survive(db, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    monkeypatch.setattr(
        discovery,
        "collect",
        _collect([
            SourceResult("TechCrunch", "rss", [
                _article("Acme raises $12M Series A", "https://tc.test/acme"),
                _article("Acme raises $12M Series A - UKTN", "https://uktn.test/acme", source="UKTN"),  # same story
                _article("Our thoughts on hiring juniors", "https://tc.test/opinion"),   # off topic
                _article("Beta raises seed funding", "https://tc.test/beta", published=old),  # stale
            ]),
        ]),
    )
    result = discovery.discover(db, CONFIG)
    assert [c.article.url for c in result.candidates] == ["https://tc.test/acme"]
    assert result.stats["duplicates"] == 1
    # The second outlet is kept against the same story as corroboration.
    assert result.candidates[0].source_count == 2
    assert result.stats["off_topic"] == 1
    assert result.stats["stale"] == 1


def test_an_article_already_handled_is_not_offered_again(db, monkeypatch):
    articles = [_article("Acme raises $12M Series A", "https://tc.test/acme")]
    monkeypatch.setattr(discovery, "collect", _collect([SourceResult("TechCrunch", "rss", articles)]))
    first = discovery.discover(db, CONFIG)
    primary = first.candidates[0].article
    db.record_article(primary, "acme raises 12m series a", outcome="extracted")
    assert discovery.discover(db, CONFIG).candidates == []


def test_source_problems_are_surfaced_not_swallowed(db, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "collect",
        _collect([
            SourceResult("GDELT", "gdelt", [], error="429 Too Many Requests"),
            SourceResult("Sifted", "rss", [_article("Acme raises $12M", "https://s.test/a")]),
        ]),
    )
    result = discovery.discover(db, CONFIG)
    assert [issue["source"] for issue in result.issues] == ["GDELT"]
    assert result.stats["candidates"] == 1


def test_undated_articles_are_kept_for_the_extractor_to_judge(db, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "collect",
        _collect([SourceResult("Index Ventures", "vc_page", [_article("Acme raises $12M Series A", "https://iv.test/a", kind="vc_page")])]),
    )
    assert len(discovery.discover(db, CONFIG).candidates) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Acme raises $12M Series A led by Index",
        "London's Metris Energy raises €4.35 million to scale AI platform",
        "Conduct secures $60m in funding from Index and ICONIQ",
        "Fintech startup closes seed round",
        "Gradient Labs raised 13 million dollars",
        "Pre-seed funding for a robotics startup",
    ],
)
def test_real_funding_headlines_pass_the_gate(text):
    assert discovery.looks_like_funding(text)


@pytest.mark.parametrize(
    "text",
    [
        "3 days left to save up to $200 at TechCrunch Disrupt 2026",
        "Prices go up in 7 days — get your Disrupt ticket now",
        "Meet the next wave of VCs judging Startup Battlefield 200",
        "Where will the next breakout startup come from?",
        "Our thoughts on hiring your first product manager",
        "Snorkel AI triples valuation to $3.5B as demand booms",
    ],
)
def test_marketing_and_commentary_do_not(text):
    assert not discovery.looks_like_funding(text)


def test_a_direct_link_is_preferred_over_a_google_redirect(db, monkeypatch):
    google = _article("Acme raises $12M Series A", "https://news.google.com/rss/articles/CBMi", source="Google News · Reuters", kind="google_news")
    direct = _article("Acme raises $12M Series A - Sifted", "https://sifted.eu/acme", source="Sifted")
    monkeypatch.setattr(
        discovery,
        "collect",
        _collect([SourceResult("Google News: q", "google_news", [google]), SourceResult("Sifted", "rss", [direct])]),
    )
    candidate = discovery.discover(db, CONFIG).candidates[0]
    assert candidate.article.url == "https://sifted.eu/acme"
    assert [a.source_kind for a in candidate.duplicates] == ["google_news"]


@pytest.mark.parametrize(
    "text",
    [
        "Spiich erhält 3 Millionen Euro für Vertriebs-KI-Agenten",
        "PRIMO lève près de 7 millions d’euros pour son IT",
        "Antwerps Apicbase haalt 4 miljoen op bij investeerders",
        "Svenska Terasi hämtar in 11 miljoner i finansieringsrunda",
    ],
)
def test_non_english_funding_headlines_pass_the_gate(text):
    assert discovery.looks_like_funding(text)


@pytest.mark.parametrize(
    "text",
    [
        "Exclusive: Connect Ventures raises $55m in first close of fifth fund to chase deeptech",
        "Balderton closes $1.3bn fund to back European founders",
        "Seedcamp announces Fund VI at $180M",
        "New VC firm raises debut fund of €100M",
    ],
)
def test_vc_funds_raising_their_own_money_are_not_prospects(text):
    assert discovery.looks_like_fund_raise(text)
    assert not discovery.looks_like_funding(text)


@pytest.mark.parametrize(
    "text",
    [
        "Fundamental raises $12M Series A led by Index",
        "Crusoe raises $3.9B to build data centers",
        "Basecamp Research raises $140M Series C",
    ],
)
def test_operating_companies_are_not_mistaken_for_funds(text):
    assert not discovery.looks_like_fund_raise(text)
    assert discovery.looks_like_funding(text)

