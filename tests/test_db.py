import pytest

from src.funding_radar.db import FundingDatabase, company_id_for
from src.funding_radar.models import Article, Round


@pytest.fixture
def db(tmp_path):
    database = FundingDatabase(tmp_path / "funding.db")
    yield database
    database.close()


def _round(**overrides) -> Round:
    values = dict(company="Acme AI", stage="series a", amount_value=12_000_000, currency="USD",
                  round_date="2026-09-20T00:00:00+00:00", company_domain="acme.ai", region="uk")
    values.update(overrides)
    return Round(**values)


def _article(url="https://tc.test/acme", source="TechCrunch"):
    return Article(source=source, source_kind="rss", title="Acme AI raises $12M", url=url)


# ---- company identity -------------------------------------------------

def test_a_company_is_created_once_and_enriched_later(db):
    first = db.upsert_company(_round(summary="", sector=""))
    second = db.upsert_company(_round(summary="AI for logistics", sector="AI applications"))
    assert first == second == "d:acme.ai"
    company = db.get_company(first)
    assert company["summary"] == "AI for logistics" and company["sector"] == "AI applications"


def test_a_company_first_seen_without_a_domain_is_merged_when_one_appears(db):
    by_name = db.upsert_company(_round(company_domain=""))
    assert by_name == "n:acme ai"
    db.upsert_round(_round(company_domain=""), by_name)

    by_domain = db.upsert_company(_round())
    assert by_domain == "d:acme.ai"
    # The old identity is gone and its round came across with it.
    assert db.get_company("n:acme ai") is None
    assert len(db.list_rounds(window_days=365, qualified_only=False)) == 1
    assert "acme ai" in db.get_company(by_domain)["aliases"]


def test_a_renamed_company_is_recognised_through_its_alias(db):
    db.upsert_company(_round(company="Acme AI"))
    same = db.upsert_company(_round(company="Acme AI Ltd."))
    assert same == "d:acme.ai"
    assert db.find_company("", "Acme AI")["company_id"] == "d:acme.ai"


def test_two_companies_with_similar_names_stay_apart(db):
    db.upsert_company(_round(company="Orbital", company_domain="orbital.co"))
    other = db.upsert_company(_round(company="Orbital Materials", company_domain="orbitalmaterials.com"))
    assert other == "d:orbitalmaterials.com"
    assert db.get_company("d:orbital.co") is not None


def test_company_ids_prefer_domain_over_name():
    assert company_id_for("https://www.Acme.ai/about", "Acme AI") == "d:acme.ai"
    assert company_id_for("", "Acme AI Ltd.") == "n:acme ai"


# ---- round identity ---------------------------------------------------

def test_the_same_raise_reported_twice_is_one_round(db):
    company_id = db.upsert_company(_round())
    first_id, created = db.upsert_round(_round(), company_id)
    assert created
    second_id, created_again = db.upsert_round(_round(round_date="2026-09-28T00:00:00+00:00"), company_id)
    assert second_id == first_id and not created_again


def test_a_round_reported_either_side_of_a_month_boundary_is_one_round(db):
    company_id = db.upsert_company(_round())
    first, _ = db.upsert_round(_round(round_date="2026-09-30T00:00:00+00:00"), company_id)
    second, created = db.upsert_round(_round(round_date="2026-10-02T00:00:00+00:00"), company_id)
    assert second == first and not created


def test_a_later_raise_is_a_new_round(db):
    company_id = db.upsert_company(_round())
    seed, _ = db.upsert_round(_round(stage="seed", round_date="2026-01-15T00:00:00+00:00"), company_id)
    series_a, created = db.upsert_round(_round(stage="series a", round_date="2026-09-20T00:00:00+00:00"), company_id)
    assert created and series_a != seed


def test_a_relabelled_round_is_not_duplicated(db):
    """One outlet calls it seed, another Series A, on the same date."""
    company_id = db.upsert_company(_round())
    first, _ = db.upsert_round(_round(stage="seed"), company_id)
    second, created = db.upsert_round(_round(stage="series a"), company_id)
    assert second == first and not created
    assert db.get_round(first)["stage"] == "series a"


def test_facts_are_filled_in_but_never_blanked_by_a_thinner_report(db):
    company_id = db.upsert_company(_round())
    round_id, _ = db.upsert_round(_round(evidence="raised $12M", confidence=0.9), company_id)
    db.upsert_round(_round(evidence="", confidence=0.2, currency=""), company_id)
    stored = db.get_round(round_id)
    assert stored["evidence"] == "raised $12M"
    assert stored["currency"] == "USD"
    assert stored["confidence"] == 0.9


# ---- investors --------------------------------------------------------

def test_investors_are_shared_across_rounds_and_leads_are_marked(db):
    company_id = db.upsert_company(_round())
    round_id, _ = db.upsert_round(_round(), company_id)
    db.upsert_investor("Antler", tracked=True, min_amount=300_000)
    db.link_investors(round_id, ["Index Ventures", "Antler"], lead="Index Ventures")
    names = [(i["name"], i["is_lead"], i["tracked"], i["min_amount"]) for i in db.round_investors(round_id)]
    assert names[0] == ("Index Ventures", 1, 0, None)
    assert ("Antler", 0, 1, 300_000.0) in names
    assert "antler" in db.tracked_investors()


def test_investor_names_are_matched_regardless_of_spelling(db):
    first = db.upsert_investor("Entrepreneur First")
    second = db.upsert_investor("Entrepreneur First Ltd.")
    assert first == second == "entrepreneur first"


# ---- evidence, scores, feedback --------------------------------------

def test_sources_accumulate_and_outlets_are_counted(db):
    company_id = db.upsert_company(_round())
    round_id, _ = db.upsert_round(_round(), company_id)
    db.add_round_source(round_id, _article("https://tc.test/a", "TechCrunch"), 12_000_000)
    db.add_round_source(round_id, _article("https://sifted.eu/a", "Sifted"), 15_000_000)
    db.add_round_source(round_id, _article("https://tc.test/a", "TechCrunch"), 12_000_000)  # same article again
    assert db.distinct_outlets(round_id) == 2
    assert sorted(s["amount_value"] for s in db.round_sources(round_id)) == [12_000_000, 15_000_000]


def test_scores_are_history_not_a_column(db):
    company_id = db.upsert_company(_round())
    round_id, _ = db.upsert_round(_round(), company_id)
    db.add_score(round_id, fit_score=0.6, angle="growth", reason="early", model="sonnet", rubric_version="v1")
    db.add_score(round_id, fit_score=0.9, angle="pricing", reason="better fit", model="opus", rubric_version="v2")
    latest = db.latest_score(round_id)
    assert (latest["fit_score"], latest["angle"], latest["rubric_version"]) == (0.9, "pricing", "v2")


def test_a_round_reads_back_with_company_score_investors_and_sources(db):
    company_id = db.upsert_company(_round(summary="AI for logistics", sector="AI applications"))
    round_id, _ = db.upsert_round(_round(qualified=True), company_id)
    db.link_investors(round_id, ["Index Ventures"], lead="Index Ventures")
    db.add_round_source(round_id, _article(), 12_000_000)
    db.add_score(round_id, fit_score=0.8, angle="pricing", reason="Series A, AI-native", model="opus", rubric_version="v1")
    db.set_feedback(round_id, "useful")

    row = db.list_rounds(window_days=120)[0]
    assert row["company"] == "Acme AI" and row["sector"] == "AI applications"
    assert row["investors"] == ["Index Ventures"] and row["fit_score"] == 0.8
    assert row["angle"] == "pricing" and row["feedback"] == "useful"
    assert len(row["sources"]) == 1


def test_out_of_brief_rounds_are_listed_by_default_and_filterable(db):
    """The site filters; the pipeline does not hide. Late-stage and US rounds stay visible."""
    company_id = db.upsert_company(_round())
    db.upsert_round(_round(qualified=False), company_id)
    assert len(db.list_rounds(window_days=120)) == 1
    assert db.list_rounds(window_days=120, qualified_only=True) == []


def test_the_digest_takes_the_best_confirmed_rounds_once(db):
    for name, domain, score in (("Acme AI", "acme.ai", 0.7), ("Beta", "beta.io", 0.9)):
        company_id = db.upsert_company(_round(company=name, company_domain=domain))
        round_id, _ = db.upsert_round(_round(company=name, company_domain=domain, qualified=True, confirmed=True), company_id)
        db.add_score(round_id, fit_score=score, angle="pricing", reason="", model="opus", rubric_version="v1")
    ordered = [r["company"] for r in db.undigested_rounds()]
    assert ordered == ["Beta", "Acme AI"]
    db.mark_digested([r["round_id"] for r in db.undigested_rounds()])
    assert db.undigested_rounds() == []


# ---- articles and source health --------------------------------------

def test_an_article_is_skipped_by_url_or_repeated_headline(db):
    article = _article()
    assert not db.is_article_seen(article.article_id, "acme ai raises 12m")
    db.record_article(article, "acme ai raises 12m", outcome="extracted")
    assert db.is_article_seen(article.article_id, "acme ai raises 12m")
    assert db.is_article_seen("different-id", "acme ai raises 12m")


def test_a_source_that_goes_quiet_is_flagged_but_a_new_one_is_not(db):
    assert db.record_source("Google News: London", "google_news", 20) is None
    assert db.record_source("Google News: London", "google_news", 0)["status"] == "quiet"
    assert db.record_source("Cherry Ventures", "vc_page", 0) is None


def test_consecutive_failures_are_counted(db):
    db.record_source("GDELT", "gdelt", 0, error="429")
    assert db.record_source("GDELT", "gdelt", 0, error="429")["status"] == "failed"
    assert db.list_source_health()[0]["consecutive_failures"] == 2


def test_deleting_a_company_takes_its_rounds_and_evidence_with_it(db):
    company_id = db.upsert_company(_round())
    round_id, _ = db.upsert_round(_round(), company_id)
    db.add_round_source(round_id, _article(), 12_000_000)
    db._conn.execute("DELETE FROM companies WHERE company_id = ?", (company_id,))
    db._conn.commit()
    assert db.get_round(round_id) is None
    assert db.round_sources(round_id) == []


def test_a_studio_company_carries_its_flag_into_the_round_list(db):
    from src.funding_radar.models import Round

    db.upsert_company(Round(company="OWOW Venture Studio", summary="Builds B2B startups", region="europe"))
    round_ = Round(company="OWOW Venture Studio", summary="Builds B2B startups", region="europe",
                   amount_value=2_650_000, currency="EUR", round_date="2026-09-24T00:00:00+00:00")
    company_id = db.upsert_company(round_)
    db.upsert_round(round_, company_id)
    assert db.list_rounds(window_days=120)[0]["is_studio"] == 1
