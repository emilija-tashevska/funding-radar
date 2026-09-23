from datetime import datetime, timedelta, timezone

import pytest

from src.funding_radar.db import FundingDatabase
from src.funding_radar.models import Article, Round


@pytest.fixture
def db(tmp_path):
    database = FundingDatabase(tmp_path / "funding.db")
    yield database
    database.close()


def _round(**overrides) -> Round:
    values = dict(
        company="Acme AI",
        stage="Series A",
        round_date="2026-09-20T00:00:00+00:00",
        amount_value=12_000_000,
        company_domain="acme.ai",
        confirmed=True,
    )
    values.update(overrides)
    return Round(**values)


def test_a_round_is_stored_once_and_refreshed_on_the_next_run(db):
    first = _round()
    assert db.upsert_round(first) is True
    assert db.upsert_round(_round(summary="Updated summary")) is False
    stored = db.get_round(first.round_id)
    assert stored["summary"] == "Updated summary"
    assert stored["company_key"] == "acme ai"


def test_the_same_raise_either_side_of_a_month_boundary_is_found(db):
    db.upsert_round(_round(round_date="2026-09-30T00:00:00+00:00"))
    match = db.find_round_by_company("acme.ai", "acme ai", within_days=45, around="2026-10-02T00:00:00+00:00")
    assert match is not None and match["company"] == "Acme AI"


def test_an_unrelated_company_is_not_matched(db):
    db.upsert_round(_round())
    assert db.find_round_by_company("other.io", "other", within_days=45, around="2026-09-21T00:00:00+00:00") is None


def test_articles_are_skipped_by_url_or_by_repeated_headline(db):
    article = Article(source="TechCrunch", source_kind="rss", title="Acme AI raises $12M", url="https://tc.com/a")
    assert db.is_article_seen(article.article_id, "acme ai raises 12m") is False
    db.record_article(article.article_id, article.url, "acme ai raises 12m", outcome="extracted")
    assert db.is_article_seen(article.article_id, "acme ai raises 12m") is True
    # Same story, different outlet and URL: the headline key stops a second model call.
    other = Article(source="UKTN", source_kind="rss", title="Acme AI raises $12M", url="https://uktn.com/b")
    assert db.is_article_seen(other.article_id, "acme ai raises 12m") is True


def test_sources_are_recorded_per_round_and_disagreements_are_visible(db):
    round_ = _round()
    db.upsert_round(round_)
    for url, amount in (("https://tc.com/a", 12_000_000), ("https://uktn.com/b", 15_000_000)):
        db.add_round_source(
            round_.round_id,
            Article(source="x", source_kind="rss", title="t", url=url),
            amount,
        )
    assert sorted(db.round_source_amounts(round_.round_id)) == [12_000_000, 15_000_000]


def test_a_source_that_goes_quiet_is_flagged_but_a_new_one_is_not(db):
    assert db.record_source("Google News: London", "google_news", 20) is None
    issue = db.record_source("Google News: London", "google_news", 0)
    assert issue["status"] == "quiet"
    # A brand-new source with nothing yet is not a failure.
    assert db.record_source("Cherry Ventures", "vc_page", 0) is None


def test_a_failing_source_counts_consecutive_failures(db):
    db.record_source("GDELT", "gdelt", 0, error="429 Too Many Requests")
    issue = db.record_source("GDELT", "gdelt", 0, error="429 Too Many Requests")
    assert issue["status"] == "failed"
    assert db.list_source_health()[0]["consecutive_failures"] == 2


def test_only_recent_rounds_are_published(db):
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    db.upsert_round(_round(company="Old Co", company_domain="old.co", round_date=old))
    db.upsert_round(_round())
    assert [r["company"] for r in db.list_rounds(window_days=120)] == ["Acme AI"]


def test_digest_takes_confirmed_scored_rounds_once(db):
    round_ = _round()
    db.upsert_round(round_)
    db.set_scores(round_.round_id, fit_score=0.8, fit_reason="Series A, AI-native", angle="pricing")
    assert [r["round_id"] for r in db.undigested_rounds()] == [round_.round_id]
    db.mark_digested([round_.round_id])
    assert db.undigested_rounds() == []
