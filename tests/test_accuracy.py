"""The labelled set and its scorer, offline.

The scorer is the thing that tells us whether extraction regressed, so it gets
tested itself: a perfect run must score 100%, and a wrong answer must be caught.
"""

import pytest

from src.funding_radar.accuracy import SCORED_FIELDS, load_fixtures, score, to_articles
from src.funding_radar.models import Round


def _round_from_label(expect, **overrides):
    """Build the Round a perfect extractor would have returned for a label."""
    data = {
        "company": expect["company"] or "Unnamed",
        "stage": expect["stage"] or "",
        "amount_value": expect["amount_value"],
        "currency": expect["currency"] or "",
        "region": expect["region"] or "",
    }
    data.update(overrides)
    return Round(**data)


def _perfect_results(fixtures):
    return {
        index: _round_from_label(item["expect"]) if item["expect"]["is_round"] else "not a funding round"
        for index, item in enumerate(fixtures)
    }


def test_the_labelled_set_is_well_formed():
    fixtures = load_fixtures()
    assert 10 <= len(fixtures) <= 40
    for item in fixtures:
        assert item["title"] and item["id"]
        expect = item["expect"]
        assert set(expect) == {"is_round", *SCORED_FIELDS}
        if not expect["is_round"]:
            assert all(expect[name] is None for name in SCORED_FIELDS), item["id"]
    # Both answers are represented, or the set only measures one direction.
    assert any(item["expect"]["is_round"] for item in fixtures)
    assert any(not item["expect"]["is_round"] for item in fixtures)


def test_fixtures_become_articles_the_extractor_can_read():
    fixtures = load_fixtures()
    articles = to_articles(fixtures)
    assert len(articles) == len(fixtures)
    assert all(article.title and article.url and article.published_at for article in articles)
    assert len({article.url for article in articles}) == len(articles)


def test_a_perfect_run_scores_everything():
    fixtures = load_fixtures()
    report = score(fixtures, _perfect_results(fixtures))
    correct, scored = report.overall
    assert correct == scored and scored > 0
    assert report.misses == []


def test_a_wrong_amount_is_caught_and_named():
    fixtures = load_fixtures()
    results = _perfect_results(fixtures)
    index, item = next((i, f) for i, f in enumerate(fixtures)
                       if f["expect"]["amount_value"] and not f.get("judgement"))
    # The classic failure: the model reports "$140M" as 140.
    results[index] = _round_from_label(item["expect"], amount_value=item["expect"]["amount_value"] / 1_000_000)
    report = score(fixtures, results)
    correct, scored = report.overall
    assert correct == scored - 1
    assert [(m.id, m.field) for m in report.misses] == [(item["id"], "amount_value")]


def test_a_non_round_extracted_as_a_round_is_caught():
    fixtures = load_fixtures()
    results = _perfect_results(fixtures)
    index, item = next((i, f) for i, f in enumerate(fixtures)
                       if not f["expect"]["is_round"] and not f.get("judgement"))
    results[index] = Round(company="Snorkel AI", amount_value=3_500_000_000, currency="USD")
    report = score(fixtures, results)
    assert [(m.id, m.field) for m in report.misses] == [(item["id"], "is_round")]


def test_judgement_rows_are_reported_but_not_scored():
    fixtures = load_fixtures()
    judgement = [i for i, f in enumerate(fixtures) if f.get("judgement")]
    assert judgement, "the set should carry at least one judgement row"
    perfect = score(fixtures, _perfect_results(fixtures)).overall
    results = _perfect_results(fixtures)
    for index in judgement:
        item = fixtures[index]["expect"]
        results[index] = "not a funding round" if item["is_round"] else Round(company="Whoever")
    report = score(fixtures, results)
    assert report.overall == perfect  # the headline number does not move
    assert {m.id for m in report.misses} == {fixtures[i]["id"] for i in judgement}
    assert all(m.judgement for m in report.misses)


def test_company_comparison_ignores_suffixes_and_case():
    fixtures = [{"id": "x", "title": "t", "expect": {"is_round": True, "company": "Metris Energy Ltd",
                                                     "amount_value": None, "currency": None,
                                                     "stage": None, "region": None}}]
    report = score(fixtures, {0: Round(company="metris energy")})
    assert report.misses == []


def test_stage_comparison_uses_the_same_normalisation_as_the_pipeline():
    fixtures = [{"id": "x", "title": "t", "expect": {"is_round": True, "company": None,
                                                     "amount_value": None, "currency": None,
                                                     "stage": "series a", "region": None}}]
    assert score(fixtures, {0: Round(company="X", stage="series a")}).misses == []
    assert score(fixtures, {0: Round(company="X", stage="seed")}).misses
