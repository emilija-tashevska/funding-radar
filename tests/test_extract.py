import json

import pytest

from src.funding_radar import extract as extractor
from src.funding_radar.llm import LLMError, parse_json
from src.funding_radar.models import Article, Round

SECTORS = ["AI applications", "Fintech and payments", "Climate and energy", "Other"]


def _article(title, summary="", published="2026-09-20T00:00:00+00:00", source="TechCrunch"):
    return Article(source=source, source_kind="rss", title=title, url=f"https://x.test/{abs(hash(title))}",
                   summary=summary, published_at=published)


def _reply(*items):
    """Stand in for Claude: returns the payload the schema promises."""
    def fake(system, user, schema, *, model, effort="low", max_tokens=16000):
        return {"results": list(items)}
    return fake


def test_a_clean_round_is_extracted_with_its_evidence(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Metris Energy", "company_domain": "metrisenergy.com",
        "summary": "AI platform for renewable energy assets", "stage": "Seed", "amount_value": 4350000,
        "currency": "EUR", "investors": ["Ada Ventures", "Antler"], "hq": "London, United Kingdom",
        "region": "uk", "sector": "Climate and energy", "ai_native": True,
        "evidence": "Metris Energy raises €4.35 million",
    }))
    results = extractor.extract_batch([_article("Metris Energy raises €4.35 million")], SECTORS)
    round_ = results[0]
    assert isinstance(round_, Round)
    assert (round_.company, round_.stage, round_.amount_value, round_.currency) == ("Metris Energy", "seed", 4350000.0, "EUR")
    # The prompt asks for the lead first, so investors[0] is the lead.
    assert round_.investors == ["Ada Ventures", "Antler"] and round_.lead_investor == "Ada Ventures"
    assert (round_.hq_city, round_.hq_country) == ("London", "United Kingdom")
    assert round_.region == "uk" and round_.ai_native and round_.evidence.startswith("Metris Energy raises")


@pytest.mark.parametrize(
    "rejection",
    ["venture fund raising its own capital", "debt facility, no equity", "valuation change only", "weekly round-up"],
)
def test_non_rounds_come_back_as_rejections(monkeypatch, rejection):
    monkeypatch.setattr(extractor, "complete_json", _reply({"index": 0, "is_round": False, "summary": rejection}))
    results = extractor.extract_batch([_article("Something that is not a round")], SECTORS)
    assert results[0] == rejection


def test_a_round_with_no_company_named_is_rejected(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({"index": 0, "is_round": True, "company": "  "}))
    assert extractor.extract_batch([_article("Someone raised something")], SECTORS)[0] == "no company named"


def test_an_unknown_sector_falls_back_to_other(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Acme", "sector": "Quantum teleportation", "stage": "Seed",
    }))
    assert extractor.extract_batch([_article("Acme raises seed")], SECTORS)[0].sector == "Other"


def test_the_stage_is_read_from_the_headline_when_the_model_omits_it(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Acme", "stage": "", "amount_value": 12000000, "currency": "USD",
    }))
    result = extractor.extract_batch([_article("Acme raises $12M Series A led by Index")], SECTORS)[0]
    assert result.stage == "series a" and result.stage_raw == ""


def test_the_article_date_is_used_as_the_round_date(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Acme", "stage": "Seed",
    }))
    result = extractor.extract_batch([_article("Acme raises seed", published="2026-09-18T09:00:00+00:00")], SECTORS)[0]
    assert result.round_date == "2026-09-18T09:00:00+00:00"


def test_a_messy_amount_does_not_crash_the_batch(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Acme", "amount_value": "twelve million", "stage": "Seed",
    }))
    assert extractor.extract_batch([_article("Acme raises seed")], SECTORS)[0].amount_value is None


def test_every_candidate_gets_an_outcome_even_if_the_model_skips_one(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({"index": 0, "is_round": True, "company": "Acme"}))
    results = extractor.extract_batch([_article("Acme raises seed"), _article("Beta raises seed")], SECTORS)
    assert isinstance(results[0], Round) and results[1] == "no result returned"


def test_an_out_of_range_index_is_ignored(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply(
        {"index": 7, "is_round": True, "company": "Ghost"},
        {"index": 0, "is_round": True, "company": "Acme"},
    ))
    results = extractor.extract_batch([_article("Acme raises seed")], SECTORS)
    assert list(results) == [0] and results[0].company == "Acme"


def test_work_is_split_into_batches(monkeypatch):
    seen = []

    def fake(candidates, sectors, model=None):
        seen.append(len(candidates))
        return {i: "not a funding round" for i in range(len(candidates))}

    monkeypatch.setattr(extractor, "extract_batch", fake)
    articles = [_article(f"Company {i} raises seed") for i in range(20)]
    results, stats = extractor.extract(articles, SECTORS, batch_size=8, max_calls=100)
    assert seen == [8, 8, 4] and stats["batches"] == 3 and len(results) == 20


def test_a_failed_batch_is_skipped_so_the_rest_of_the_run_survives(monkeypatch):
    def fake(candidates, sectors, model=None):
        if candidates[0].title.startswith("Company 0"):
            raise LLMError("overloaded")
        return {i: "not a funding round" for i in range(len(candidates))}

    monkeypatch.setattr(extractor, "extract_batch", fake)
    articles = [_article(f"Company {i} raises seed") for i in range(16)]
    results, stats = extractor.extract(articles, SECTORS, batch_size=8, max_calls=100)
    # The failed batch is simply not recorded, so tomorrow's run sees it again.
    assert stats["failed_batches"] == 1 and stats["batches"] == 2 and len(results) == 8


def test_the_cap_stops_a_source_spike_running_up_a_bill(monkeypatch):
    monkeypatch.setattr(extractor, "extract_batch", lambda c, s, model=None: {i: "no" for i in range(len(c))})
    articles = [_article(f"Company {i} raises seed") for i in range(30)]
    _, stats = extractor.extract(articles, SECTORS, batch_size=8, max_calls=10)
    assert stats["skipped_over_cap"] == 20 and stats["rejected"] == 10


def test_the_prompt_carries_the_articles_and_the_sector_list():
    prompt = extractor.build_prompt([_article("Acme raises $12M", summary="London-based Acme...")], SECTORS)
    assert "Acme raises $12M" in prompt and "London-based Acme" in prompt
    assert "Fintech and payments" in prompt


@pytest.mark.parametrize(
    "text,expected",
    [
        ('{"results": []}', {"results": []}),
        ('```json\n{"results": []}\n```', {"results": []}),
        ('  {"results": [{"index": 0}]}  ', {"results": [{"index": 0}]}),
    ],
)
def test_responses_parse_through_a_stray_code_fence(text, expected):
    assert parse_json(text) == expected


@pytest.mark.parametrize("text", ["not json at all", "[1, 2, 3]", ""])
def test_unusable_responses_raise_rather_than_return_junk(text):
    with pytest.raises(LLMError):
        parse_json(text)


def test_the_schema_stays_inside_the_structured_output_limit():
    """The API rejects more than 14 properties per item; this is the guard rail."""
    fields = extractor.RESULT_SCHEMA["properties"]["results"]["items"]["properties"]
    assert len(fields) <= extractor.MAX_SCHEMA_FIELDS


def test_confidence_reflects_how_much_the_article_pinned_down(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Acme", "stage": "Seed",
        "amount_value": 5_000_000, "company_domain": "acme.ai",
    }))
    detailed = extractor.extract_batch([_article("Acme raises $5M seed")], SECTORS)[0]

    monkeypatch.setattr(extractor, "complete_json", _reply({"index": 0, "is_round": True, "company": "Acme"}))
    vague = extractor.extract_batch([_article("Acme raises funding")], SECTORS)[0]
    assert detailed.confidence > vague.confidence


@pytest.mark.parametrize(
    "amount,text,expected",
    [
        (140, "Basecamp Research raises $140M Series C", 140_000_000),
        (4.35, "raises €4.35 million seed", 4_350_000),
        (8, "Magic AI raises £8m", 8_000_000),
        (3.9, "Crusoe raises $3.9B to build data centers", 3_900_000_000),
        (12_000_000, "Acme raises $12M", 12_000_000),      # already whole units
        (800_000, "Acme raises £800k", 800_000),           # small but plausible
        (None, "Acme raises an undisclosed sum", None),
    ],
)
def test_amounts_written_in_millions_are_corrected(amount, text, expected):
    assert extractor.rescale_amount(amount, text) == expected


@pytest.mark.parametrize(
    "country,city,expected",
    [
        ("United Kingdom", "London", "uk"), ("", "London", "uk"), ("Germany", "Berlin", "europe"),
        ("", "Helsinki", "europe"), ("United States", "San Francisco", "us"), ("", "", ""),
        ("Singapore", "Singapore", ""),
    ],
)
def test_region_is_inferred_when_the_model_leaves_it_blank(country, city, expected):
    assert extractor.region_for(country, city) == expected


def test_a_blank_region_is_filled_in_from_the_location(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Verda", "hq": "Helsinki, Finland",
        "region": "", "amount_value": 164.8, "currency": "EUR", "evidence": "raising €164.8 million",
    }))
    result = extractor.extract_batch([_article("Helsinki's Verda raises €164.8 million")], SECTORS)[0]
    assert result.region == "europe"
    assert result.amount_value == 164_800_000


def test_a_location_overrides_an_other_region(monkeypatch):
    """A Helsinki company labelled "other" is European; the location is the better evidence."""
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Verda", "hq": "Helsinki, Finland",
        "region": "other", "amount_value": 164_800_000, "currency": "EUR",
    }))
    assert extractor.extract_batch([_article("Verda raises €164.8 million")], SECTORS)[0].region == "europe"


def test_a_stated_region_outside_europe_is_respected(monkeypatch):
    monkeypatch.setattr(extractor, "complete_json", _reply({
        "index": 0, "is_round": True, "company": "Corridor", "hq": "New York, United States",
        "region": "us", "amount_value": 25_000_000, "currency": "USD",
    }))
    assert extractor.extract_batch([_article("Corridor raises $25M seed")], SECTORS)[0].region == "us"
