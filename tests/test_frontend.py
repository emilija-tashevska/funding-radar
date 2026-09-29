"""The page, driven in a real browser: every card and every filter.

Builds the page from a small database chosen to hit the edges (pounds and kronor,
an undisclosed amount, a studio, a round outside the brief, a hostile name), then
clicks through it in Chromium. Skipped when Playwright or a browser is missing,
unless REQUIRE_FRONTEND=1 (as in CI), where a missing browser is a failure.
"""

from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.funding_radar import pipeline, site
from src.funding_radar.db import FundingDatabase
from src.funding_radar.discovery import Candidate
from src.funding_radar.models import Article, Round
from src.funding_radar.qualify import Rules

if os.getenv("REQUIRE_FRONTEND"):
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

RULES = Rules(min_amount=0, stages=("pre-seed", "seed", "series a", "series b"),
              regions=("uk", "europe"), investor_floors={})
FUNDS = ["Antler", "Entrepreneur First", "Creandum", "Techstars"]
GOOGLE = "https://news.google.com/rss/articles/CBMiXYZ"


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# company, region, stage, amount, currency, days ago, investors, ai, sources, description
ROUNDS = [
    ("Acme Rota", "uk", "seed", 3_000_000, "GBP", 1, ["Antler"], True,
     [("UKTN", "https://www.uktech.news/acme")], ("Acme makes rota software for NHS clinics.", "https://www.uktech.news/acme")),
    ("Borealis Freight", "europe", "series a", 12_000_000, "EUR", 2, ["Northzone"], False,
     [("Google News · Sifted", GOOGLE)], ("Borealis runs a freight marketplace for Nordic hauliers.", "https://sifted.eu/borealis")),
    ("Forge Venture Studio", "europe", "pre-seed", 500_000, "EUR", 3, [], False,
     [("Tech.eu", "https://tech.eu/forge")], None),
    ("Quiet Labs", "uk", "seed", None, "", 4, [], True,
     [("Business Cloud", "https://www.businesscloud.co.uk/quiet")], None),
    ("Yankee Robotics", "us", "series a", 20_000_000, "USD", 5, ["Techstars"], True,
     [("TechCrunch", "https://techcrunch.com/yankee")], None),
    ("Bigco Health", "uk", "series b", 40_000_000, "GBP", 6, ["Creandum"], False,
     [("Sifted", "https://sifted.eu/bigco"), ("City AM", "https://www.cityam.com/bigco")], None),
    ("Tiny Nordic", "europe", "seed", 10_000_000, "SEK", 7, [], False,
     [("Breakit", "javascript:alert(1)"), ("Di Digital", "https://digital.di.se/tiny")], None),
    ('<img src=x onerror="window.pwned=1">', "uk", "seed", 1_500_000, "GBP", 8, [], False,
     [("UKTN", "https://www.uktech.news/evil")], None),
]
IN_BRIEF = 7   # everything but Yankee


@pytest.fixture(scope="module")
def page_path(tmp_path_factory):
    folder = tmp_path_factory.mktemp("frontend")
    with FundingDatabase(folder / "f.db") as db:
        for (name, region, stage, amount, currency, days, investors, ai, sources, described) in ROUNDS:
            articles = [Article(source=outlet, source_kind="google_news" if "news.google" in url else "rss",
                                title=f"{name} raises", url=url, published_at=_days_ago(days))
                        for outlet, url in sources]
            round_ = Round(company=name, stage=stage, amount_value=amount, currency=currency, region=region,
                           round_date=_days_ago(days), investors=investors, ai_native=ai,
                           summary=f"{name} headline summary.", sector="Other")
            round_id, _ = pipeline.store_round(
                db, round_, Candidate(article=articles[0], duplicates=articles[1:]), RULES)
            company_id = db.get_round(round_id)["company_id"]
            if described:
                db.set_description(company_id, *described)
            if any("news.google" in url for _, url in sources):
                db.set_resolved_url(round_id, GOOGLE, "https://sifted.eu/borealis")
        page = site.build(out_dir=folder / "site", db=db, window_days=120)
    return page


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        executable = os.getenv("FRONTEND_CHROMIUM") or next(
            iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None)
        try:
            launched = p.chromium.launch(executable_path=executable) if executable else p.chromium.launch()
        except Exception as exc:  # noqa: BLE001
            if os.getenv("REQUIRE_FRONTEND"):
                raise
            pytest.skip(f"no browser: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def page(browser, page_path):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    tab = context.new_page()
    errors: list[str] = []
    tab.on("pageerror", lambda exc: errors.append(str(exc)))
    tab.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    tab.goto(page_path.as_uri())
    yield tab
    assert errors == [], f"the page logged errors: {errors}"
    context.close()


def names(tab) -> list[str]:
    return [n.strip() for n in tab.locator("article.card .name").all_inner_texts()]


def chip(tab, group: str, label: str):
    return tab.locator(f"#{group} button.chip", has_text=label)


# ---- every card carries what the owner needs --------------------------------

def test_only_in_brief_rounds_show_by_default(page):
    assert len(names(page)) == IN_BRIEF
    assert "Yankee Robotics" not in names(page)
    assert page.locator("#count").inner_text().startswith(f"{IN_BRIEF}")


def test_every_card_has_an_amount_a_description_and_a_link_that_opens(page):
    for card in page.locator("article.card").all():
        assert card.locator(".amount").inner_text().strip()
        assert card.locator(".summary").inner_text().strip(), card.inner_text()
        links = card.locator(".read a")
        assert links.count() >= 1, card.inner_text()
        for link in links.all():
            assert link.get_attribute("href").startswith("https://")
            assert link.get_attribute("target") == "_blank"
            assert "noopener" in link.get_attribute("rel")


def test_amounts_read_as_reported(page):
    cards = {n: page.locator("article.card", has_text=n) for n in ("Acme Rota", "Quiet Labs", "Tiny Nordic")}
    assert cards["Acme Rota"].locator(".amount").inner_text() == "£3M"
    assert cards["Quiet Labs"].locator(".amount").inner_text() == "undisclosed"
    assert "10M" in cards["Tiny Nordic"].locator(".amount").inner_text()


def test_a_description_shows_with_the_page_it_came_from(page):
    card = page.locator("article.card", has_text="Acme Rota")
    assert "rota software for NHS clinics" in card.locator(".summary").inner_text()
    source = card.locator(".summary a.src")
    assert source.get_attribute("href") == "https://www.uktech.news/acme"
    assert source.inner_text() == "uktech.news"


def test_a_company_without_a_description_falls_back_to_its_headline_summary(page):
    card = page.locator("article.card", has_text="Quiet Labs")
    assert card.locator(".summary").inner_text() == "Quiet Labs headline summary."
    assert card.locator(".summary a.src").count() == 0


def test_a_resolved_google_link_points_at_the_publisher(page):
    card = page.locator("article.card", has_text="Borealis Freight")
    hrefs = [a.get_attribute("href") for a in card.locator(".read a").all()]
    assert hrefs == ["https://sifted.eu/borealis"]


def test_a_non_http_link_is_never_rendered(page):
    card = page.locator("article.card", has_text="Tiny Nordic")
    hrefs = [a.get_attribute("href") for a in card.locator(".read a").all()]
    assert hrefs == ["https://digital.di.se/tiny"]
    assert page.locator('a[href^="javascript"]').count() == 0


def test_a_hostile_company_name_is_shown_as_text(page):
    assert page.evaluate("window.pwned") is None
    assert any("onerror" in n for n in names(page))


# ---- filters ------------------------------------------------------------------

def test_region_chips_filter_and_combine(page):
    chip(page, "regions", "UK").click()
    assert set(names(page)) == {"Acme Rota", "Quiet Labs", "Bigco Health", '<img src=x onerror="window.pwned=1">'}
    chip(page, "regions", "Europe").click()
    assert len(names(page)) == IN_BRIEF
    chip(page, "regions", "UK").click()
    assert set(names(page)) == {"Borealis Freight", "Forge Venture Studio", "Tiny Nordic"}


def test_stage_chips_show_counts_that_match_what_they_filter(page):
    seed = chip(page, "stages", "seed").filter(has_not_text="pre-seed")
    count = int(seed.locator(".n").inner_text())
    seed.click()
    assert len(names(page)) == count == 4


def test_minimum_size_counts_pounds_euros_and_dollars_one_for_one(page):
    page.select_option("#minamt", "2000000")
    shown = set(names(page))
    assert {"Acme Rota", "Borealis Freight", "Bigco Health"} <= shown     # £3M, €12M, £40M
    assert "Tiny Nordic" not in shown          # SEK 10M is about $1M
    assert "Quiet Labs" not in shown           # undisclosed never passes a size filter


def test_maximum_size_hides_bigger_and_undisclosed_rounds(page):
    page.select_option("#maxamt", "10000000")
    assert set(names(page)) == {"Acme Rota", "Forge Venture Studio", "Tiny Nordic",
                                '<img src=x onerror="window.pwned=1">'}


def test_minimum_and_maximum_together_give_a_range(page):
    page.select_option("#minamt", "1000000")
    page.select_option("#maxamt", "5000000")
    # SEK 10M is about $950k, so it falls under a 1M minimum.
    assert set(names(page)) == {"Acme Rota", '<img src=x onerror="window.pwned=1">'}


def test_an_impossible_range_says_so_rather_than_breaking(page):
    page.select_option("#minamt", "25000000")
    page.select_option("#maxamt", "1000000")
    assert names(page) == []
    assert page.locator("#list").inner_text().strip()


def test_funds_i_follow(page):
    page.click("#f-followed")
    assert set(names(page)) == {"Acme Rota", "Bigco Health"}     # Yankee is Techstars but US
    tags = page.locator("article.card", has_text="Acme Rota").locator(".tag").all_inner_texts()
    assert "Antler-backed" in tags


def test_ai_native_and_hide_studios(page):
    page.click("#f-ai")
    assert set(names(page)) == {"Acme Rota", "Quiet Labs"}
    page.click("#f-ai")
    page.click("#f-studio")
    assert "Forge Venture Studio" not in names(page) and len(names(page)) == IN_BRIEF - 1


def test_search_reads_names_descriptions_and_investors(page):
    page.fill("#q", "hauliers")
    assert names(page) == ["Borealis Freight"]
    page.fill("#q", "creandum")
    assert names(page) == ["Bigco Health"]
    page.fill("#q", "")
    assert len(names(page)) == IN_BRIEF


def test_filters_stack(page):
    chip(page, "regions", "UK").click()
    page.select_option("#maxamt", "10000000")
    page.click("#f-ai")
    assert names(page) == ["Acme Rota"]


def test_sorting_by_size_and_by_date(page):
    assert names(page)[0] == "Acme Rota"            # newest first by default
    page.select_option("#sort", "amount")
    assert names(page)[:2] == ["Bigco Health", "Borealis Freight"]


def test_working_notes_show_out_of_brief_rounds_with_their_reason(page):
    page.click("#mode")
    page.click("#f-brief")
    card = page.locator("article.card", has_text="Yankee Robotics")
    assert card.count() == 1
    assert "region us is outside the brief" in card.inner_text()


# ---- layout ---------------------------------------------------------------------

def test_the_page_fits_a_phone_without_sideways_scrolling(browser, page_path):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    tab = context.new_page()
    tab.goto(page_path.as_uri())
    assert tab.evaluate("document.documentElement.scrollWidth") <= 390
    assert tab.locator("article.card").count() == IN_BRIEF
    context.close()


def test_the_tab_has_an_icon(page):
    href = page.get_attribute('link[rel="icon"]', "href")
    assert href.startswith("data:image/svg+xml,")
    # The icon itself renders: load it as an image and check it has a size.
    width = page.evaluate("""href => new Promise(ok => { const i = new Image();
        i.onload = () => ok(i.naturalWidth); i.onerror = () => ok(0); i.src = href; })""", href)
    assert width > 0
