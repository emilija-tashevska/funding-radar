import pytest

from src.funding_radar import pipeline
from src.funding_radar.db import FundingDatabase
from src.funding_radar.discovery import Candidate
from src.funding_radar.models import Article, Round
from src.funding_radar.qualify import Rules

CONFIG = {
    "filters": {
        "min_amount": 2_000_000,
        "stages": ["pre-seed", "seed", "series a", "series b"],
        "regions": ["uk", "europe"],
        "investor_floors": {"Antler": 300_000},
        "keywords": ["raises"],
    },
    "sectors": ["AI applications", "Other"],
    "vc_pages": [{"name": "Index Ventures", "url": "https://x.test"}],
}
RULES = Rules.from_config(CONFIG)


@pytest.fixture
def db(tmp_path):
    database = FundingDatabase(tmp_path / "f.db")
    yield database
    database.close()


def _candidate(*articles) -> Candidate:
    return Candidate(article=articles[0], duplicates=list(articles[1:]))


def _article(title, url, source="TechCrunch"):
    return Article(source=source, source_kind="rss", title=title, url=url,
                   published_at="2026-09-20T00:00:00+00:00")


def _round(**overrides) -> Round:
    values = dict(company="Acme AI", stage="series a", amount_value=12_000_000, currency="USD",
                  region="uk", company_domain="acme.ai", round_date="2026-09-20T00:00:00+00:00")
    values.update(overrides)
    return Round(**values)


def test_a_stored_round_carries_company_investors_and_evidence(db):
    candidate = _candidate(_article("Acme AI raises $12M Series A", "https://tc.test/a"))
    round_id, created = pipeline.store_round(db, _round(investors=["Index Ventures"], lead_investor="Index Ventures"), candidate, RULES)
    assert created
    stored = db.list_rounds(window_days=120)[0]
    assert stored["company"] == "Acme AI" and stored["investors"] == ["Index Ventures"]
    assert stored["confirmed"] == 1 and stored["qualified"] == 1
    assert len(stored["sources"]) == 1


def test_two_outlets_confirm_a_round_without_a_domain(db):
    candidate = _candidate(
        _article("Acme AI raises $12M Series A", "https://tc.test/a", "TechCrunch"),
        _article("Acme AI raises $12M Series A", "https://sifted.eu/a", "Sifted"),
    )
    round_id, _ = pipeline.store_round(db, _round(company_domain=""), candidate, RULES)
    assert db.get_round(round_id)["confirmed"] == 1
    assert db.distinct_outlets(round_id) == 2


def test_a_single_unresolved_outlet_stays_unconfirmed(db):
    candidate = _candidate(_article("Acme AI raises $12M Series A", "https://tc.test/a"))
    round_id, _ = pipeline.store_round(db, _round(company_domain=""), candidate, RULES)
    assert db.get_round(round_id)["confirmed"] == 0


def test_outlets_disagreeing_on_the_amount_is_flagged(db):
    first = _candidate(_article("Acme AI raises $12M", "https://tc.test/a", "TechCrunch"))
    pipeline.store_round(db, _round(amount_value=12_000_000), first, RULES)
    second = _candidate(_article("Acme AI raises $20M", "https://sifted.eu/a", "Sifted"))
    round_id, created = pipeline.store_round(db, _round(amount_value=20_000_000), second, RULES)
    assert not created  # same round, second report
    assert db.get_round(round_id)["amount_disputed"] == 1


def test_small_disagreements_are_not_flagged(db):
    first = _candidate(_article("Acme AI raises $12M", "https://tc.test/a", "TechCrunch"))
    pipeline.store_round(db, _round(amount_value=12_000_000), first, RULES)
    second = _candidate(_article("Acme AI raises $12.5M", "https://sifted.eu/a", "Sifted"))
    round_id, _ = pipeline.store_round(db, _round(amount_value=12_500_000), second, RULES)
    assert db.get_round(round_id)["amount_disputed"] == 0


def test_the_same_round_in_two_currencies_is_not_a_disagreement(db):
    """50skills was reported as €5.3M and as $6M: one round, two outlets, no dispute."""
    first = _candidate(_article("50skills raises EUR 5.3 million", "https://eu.test/a", "EU-Startups"))
    pipeline.store_round(db, _round(amount_value=5_300_000, currency="EUR"), first, RULES)
    second = _candidate(_article("50skills raises $6M", "https://tech.eu/a", "Tech.eu"))
    round_id, created = pipeline.store_round(db, _round(amount_value=6_000_000, currency="USD"), second, RULES)
    assert not created
    assert db.get_round(round_id)["amount_disputed"] == 0
    assert {s["currency"] for s in db.round_sources(round_id)} == {"EUR", "USD"}


def test_a_real_disagreement_survives_the_currency_conversion(db):
    first = _candidate(_article("Acme AI raises GBP 3M", "https://tc.test/a", "TechCrunch"))
    pipeline.store_round(db, _round(amount_value=3_000_000, currency="GBP"), first, RULES)
    second = _candidate(_article("Acme AI raises $12M", "https://sifted.eu/a", "Sifted"))
    round_id, _ = pipeline.store_round(db, _round(amount_value=12_000_000, currency="USD"), second, RULES)
    assert db.get_round(round_id)["amount_disputed"] == 1


def test_an_out_of_scope_round_is_stored_and_marked_rather_than_dropped(db):
    candidate = _candidate(_article("Acme AI raises $12M Series A", "https://tc.test/a"))
    round_id, _ = pipeline.store_round(db, _round(region="us"), candidate, RULES)
    stored = db.get_round(round_id)
    assert stored["qualified"] == 0 and "region us" in stored["qualified_reason"]
    assert len(db.list_rounds(window_days=120)) == 1


def test_an_antler_company_qualifies_below_the_usual_floor(db):
    db.upsert_investor("Antler", tracked=True, min_amount=300_000)
    candidate = _candidate(_article("Tiny raises £500k pre-seed", "https://tc.test/t"))
    round_id, _ = pipeline.store_round(
        db, _round(company="Tiny", company_domain="tiny.ai", stage="pre-seed", amount_value=500_000,
                   currency="GBP", investors=["Antler"]), candidate, RULES)
    assert db.get_round(round_id)["qualified"] == 1


def test_tracked_investors_are_registered_from_config(db):
    pipeline.register_tracked_investors(db, CONFIG)
    tracked = db.tracked_investors()
    assert tracked["antler"]["min_amount"] == 300_000
    assert "index ventures" in tracked


def test_a_run_stores_rounds_and_marks_rejected_articles(db, monkeypatch):
    from src.funding_radar.discovery import DiscoveryResult

    good = _candidate(_article("Acme AI raises $12M Series A", "https://tc.test/a"))
    bad = _candidate(_article("Connect Ventures raises $55m fund", "https://tc.test/f"))
    monkeypatch.setattr(pipeline, "discover", lambda db, config: DiscoveryResult(
        candidates=[good, bad], issues=[], stats={"sources": 1, "found": 2, "candidates": 2}))
    monkeypatch.setattr(pipeline, "extract", lambda candidates, sectors: (
        {0: _round(), 1: "venture fund raising its own capital"},
        {"batches": 1, "failed_batches": 0, "rounds": 1, "rejected": 1, "skipped_over_cap": 0},
    ))
    monkeypatch.setattr(pipeline.settings, "require_llm", lambda: None)

    stats = pipeline.run(config=CONFIG, db=db)
    assert stats["new_rounds"] == 1 and stats["qualified"] == 1 and stats["rejected"] == 1
    assert [r["company"] for r in db.list_rounds(window_days=120)] == ["Acme AI"]
    # The rejected article is remembered so it is never paid for twice.
    assert db.is_article_seen(bad.article.article_id, "connect ventures raises 55m fund")


def test_rounds_outside_the_brief_keep_the_reason_so_the_site_can_filter(db):
    candidate = _candidate(_article("Basecamp Research raises $140M Series C", "https://tc.test/b"))
    round_id, _ = pipeline.store_round(
        db, _round(company="Basecamp Research", company_domain="basecamp.bio", stage="series c",
                   amount_value=140_000_000), candidate, RULES)
    stored = db.get_round(round_id)
    assert stored["qualified"] == 0
    assert "series c" in stored["qualified_reason"]
    # It is published with everything else; the site filters, the pipeline does not hide.
    assert [r["company"] for r in db.list_rounds(window_days=120)] == ["Basecamp Research"]
    assert db.list_rounds(window_days=120, qualified_only=True) == []
