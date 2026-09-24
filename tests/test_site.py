"""The site build: what the page is given, and that it stays a valid page."""

import json

import pytest

from src.funding_radar.db import FundingDatabase
from src.funding_radar.models import Round
from src.funding_radar.qualify import Rules
from src.funding_radar import pipeline, site

RULES = Rules(min_amount=2_000_000, stages=("pre-seed", "seed", "series a", "series b"),
              regions=("uk", "europe"), investor_floors={})


@pytest.fixture
def db(tmp_path):
    with FundingDatabase(tmp_path / "site.db") as database:
        yield database


def _article(title, url, outlet="TechCrunch"):
    from src.funding_radar.models import Article

    return Article(source=outlet, source_kind="rss", title=title, url=url,
                   published_at="2026-09-20T00:00:00+00:00")


def _candidate(article):
    from src.funding_radar.discovery import Candidate

    return Candidate(article=article)


def _store(db, round_, article):
    return pipeline.store_round(db, round_, _candidate(article), RULES)


def test_the_payload_carries_what_the_page_shows(db):
    round_ = Round(company="Metris Energy", company_domain="metris.energy", stage="seed",
                   amount_value=4_350_000, currency="EUR", region="uk", sector="Climate and energy",
                   ai_native=True, summary="AI for renewable assets", evidence="Metris raises €4.35 million",
                   investors=["Ada Ventures"], lead_investor="Ada Ventures", hq_city="London",
                   hq_country="United Kingdom", round_date="2026-09-20T00:00:00+00:00")
    _store(db, round_, _article("Metris Energy raises €4.35 million", "https://tc.test/metris"))
    payload = site.build_payload(db, window_days=120)
    row = payload["rounds"][0]
    assert row["company"] == "Metris Energy" and row["amount"] == 4_350_000 and row["currency"] == "EUR"
    assert row["hq"] == "London, United Kingdom" and row["investors"] == ["Ada Ventures"]
    assert row["qualified"] and row["ai_native"] and not row["is_studio"]
    assert row["sources"][0]["url"] == "https://tc.test/metris"
    assert payload["meta"]["total"] == 1 and payload["meta"]["outlets"] == 1


def test_out_of_brief_rounds_reach_the_page_with_their_reason(db):
    round_ = Round(company="Basecamp Research", stage="series c", amount_value=140_000_000,
                   currency="USD", region="uk", round_date="2026-09-20T00:00:00+00:00")
    _store(db, round_, _article("Basecamp Research raises $140M Series C", "https://tc.test/basecamp"))
    row = site.build_payload(db, window_days=120)["rounds"][0]
    assert not row["qualified"] and "series c" in row["qualified_reason"]
    assert site.build_payload(db, window_days=120)["meta"]["qualified"] == 0


def test_stages_and_regions_offered_as_filters_are_the_ones_present(db):
    for index, (stage, region) in enumerate((("seed", "uk"), ("series b", "europe"))):
        round_ = Round(company=f"Co {index}", stage=stage, amount_value=5_000_000, currency="USD",
                       region=region, round_date="2026-09-20T00:00:00+00:00")
        _store(db, round_, _article(f"Co {index} raises $5M", f"https://tc.test/{index}"))
    meta = site.build_payload(db, window_days=120)["meta"]
    assert meta["stages"] == ["seed", "series b"]  # in pipeline order, not insertion order
    assert meta["regions"] == ["uk", "europe"]


def test_the_page_is_rendered_with_its_data_inlined(tmp_path, db):
    round_ = Round(company="Acme AI", stage="seed", amount_value=5_000_000, currency="USD",
                   region="uk", round_date="2026-09-20T00:00:00+00:00")
    _store(db, round_, _article("Acme AI raises $5M", "https://tc.test/acme"))
    page = site.build(out_dir=tmp_path, window_days=120, db=db)
    html = page.read_text()
    assert site.PLACEHOLDER not in html and "Acme AI" in html
    assert json.loads((tmp_path / "data.json").read_text())["rounds"][0]["company"] == "Acme AI"


def test_a_closing_script_tag_in_the_data_cannot_break_out_of_the_page(tmp_path, db):
    """An outlet's headline is untrusted text sitting inside a <script> block."""
    round_ = Round(company="</script><script>alert(1)</script>", stage="seed", amount_value=5_000_000,
                   currency="USD", region="uk", round_date="2026-09-20T00:00:00+00:00")
    _store(db, round_, _article("</script> raises $5M", "https://tc.test/xss"))
    html = site.build(out_dir=tmp_path, window_days=120, db=db).read_text()
    # Only the page's own two script blocks close; the data's closing tags are escaped,
    # so the parser never leaves the JSON block early.
    assert html.count("</script>") == 2
    assert r"<\/script><script>alert(1)<\/script>" in html


def test_a_publisher_is_named_once_and_direct_links_come_first(db):
    from src.funding_radar.models import Round

    round_ = Round(company="mika", stage="seed", amount_value=6_000_000, currency="EUR",
                   region="europe", round_date="2026-09-20T00:00:00+00:00")
    company_id = db.upsert_company(round_)
    round_id, _ = db.upsert_round(round_, company_id)
    for outlet, url in (("Google News · Tech.eu", "https://news.google.com/rss/articles/abc"),
                        ("Google News · Tech.eu", "https://news.google.com/rss/articles/def"),
                        ("Finsmes", "https://finsmes.com/mika")):
        db.add_round_source(round_id, _article(f"mika raises €6M", url, outlet), 6_000_000, "EUR")
    sources = site.build_payload(db, window_days=120)["rounds"][0]["sources"]
    assert [s["outlet"] for s in sources] == ["Finsmes", "Tech.eu"]
    assert all("·" not in s["outlet"] for s in sources)
