"""Soapbox: the sitemap, investor pages and announcement pages, offline."""

from src.funding_radar.sources import soapbox

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.soapbox.vc/</loc></url>
  <url><loc>https://www.soapbox.vc/feed/standardx-seed</loc></url>
  <url><loc>https://www.soapbox.vc/feed/phoenix-court-2026-vintage</loc></url>
  <url><loc>https://www.soapbox.vc/investors/antler</loc><lastmod>2026-09-28</lastmod></url>
  <url><loc>https://www.soapbox.vc/companies/jack-jill</loc></url>
  <url><loc>https://www.soapbox.vc/feed/standardx-seed</loc></url>
  <url><loc>https://www.soapbox.vc/feed/</loc></url>
</urlset>"""


def test_the_sitemap_gives_announcements_only_in_order_once():
    assert soapbox.item_urls_from_sitemap(SITEMAP) == [
        "https://www.soapbox.vc/feed/standardx-seed",
        "https://www.soapbox.vc/feed/phoenix-court-2026-vintage",
    ]


def test_an_investor_page_gives_its_announcement_links():
    html = """<html><body>
      <a href="/feed/pesto-800k-seed">Pesto raises £800k seed</a>
      <a href="https://www.soapbox.vc/feed/unravel-5m-series-a?ref=x#top">Unravel</a>
      <a href="/feed/pesto-800k-seed">again</a>
      <a href="/investors/fuel-ventures">Fuel Ventures</a>
      <a href="https://other.test/feed/not-soapbox">elsewhere</a>
    </body></html>"""
    assert soapbox.item_urls_from_page(html) == [
        "https://www.soapbox.vc/feed/pesto-800k-seed",
        "https://www.soapbox.vc/feed/unravel-5m-series-a",
    ]


# Shaped like https://www.soapbox.vc/feed/standardx-seed as the runner saw it on
# 2026-09-29: a Next.js page with two JSON-LD blocks, the second a NewsArticle.
ITEM = """<!DOCTYPE html><html><head>
<title>StandardX raises a £10m seed round led by Vsquared Ventures and East X Ventures to build scalable refinery for rare isotopes</title>
<meta name="description" content="StandardX builds an accelerator-based isotope refinery to manufacture rare isotopes at commercial scale."/>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebSite","name":"Soapbox"},{"@type":"Organization","name":"Soapbox"}]}</script>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"NewsArticle","headline":"StandardX raises a £10m seed round led by Vsquared Ventures and East X Ventures to build scalable refinery for rare isotopes","description":"StandardX builds an accelerator-based isotope refinery to manufacture rare isotopes at commercial scale. StandardX serves hospitals, drug-makers and fusion energy developers.","url":"https://www.soapbox.vc/feed/standardx-seed","datePublished":"2026-09-23","dateModified":"2026-09-24T10:00:00+00:00"}</script>
</head><body><main>
<h1>StandardX raises a £10m seed round led by Vsquared Ventures and East X Ventures to build scalable refinery for rare isotopes</h1>
<p>23 Sept 2026</p>
<p>StandardX, a deep tech startup building an isotope refinery, has raised £10 million seed funding to increase production of rare isotopes for medicine and energy.</p>
<p>The round was led by Vsquared Ventures and East X Ventures, with additional backing from firstminute capital and the UK Innovation and Science Seed Fund.</p>
</main></body></html>"""


def test_an_announcement_page_becomes_an_article():
    article = soapbox.parse_item(ITEM, "https://www.soapbox.vc/feed/standardx-seed")
    assert article.title.startswith("StandardX raises a £10m seed round led by Vsquared")
    assert article.published_at.startswith("2026-09-23")
    assert article.source == "Soapbox" and article.source_kind == "soapbox"
    assert "accelerator-based isotope refinery" in article.summary
    assert "led by Vsquared Ventures and East X Ventures, with additional backing" in article.summary
    assert "UK startup funding" in article.summary        # so region is not guessed as "other"
    assert "23 Sept 2026" not in article.summary          # short fragments are not press text
    assert len(article.summary) <= 1200


def test_a_page_without_structured_data_falls_back_to_the_heading():
    html = "<html><h1>Acme raises £2m seed</h1><meta name='description' content='Acme makes rotas.'></html>"
    article = soapbox.parse_item(html, "https://www.soapbox.vc/feed/acme-seed")
    assert article.title == "Acme raises £2m seed" and article.published_at is None
    assert "Acme makes rotas." in article.summary


def test_a_page_with_no_headline_is_not_an_article():
    assert soapbox.parse_item("<html><body>Not found</body></html>", "https://www.soapbox.vc/feed/x") is None


# Shaped like /investors/antler: each card is one link holding company, date, headline.
FUND_PAGE = """<html><body><h1>Antler</h1>
<a href="/feed/jack-and-jill-series-a"><div>Jack &amp; Jill</div><div>15 Sept 2026</div><h3>Jack &amp; Jill unlocks £30m Series A</h3></a>
<a href="/feed/souk-pre-seed"><div>Souk</div><div>3 Sept 2026</div><h3>Souk raises a £1.2m pre-seed round led by SVV</h3></a>
</body></html>"""


class FakeClient:
    """Serves Soapbox pages from memory and records what was asked for."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url, headers=None, **kw):
        import httpx

        self.asked.append(url)
        if url not in self.pages:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(200, text=self.pages[url], request=httpx.Request("GET", url))

    def close(self):
        pass


def _site(sitemap_urls, items):
    sitemap = "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in sitemap_urls) + "</urlset>"
    pages = {f"{soapbox.BASE}/sitemap.xml": sitemap, f"{soapbox.BASE}/investors/antler": FUND_PAGE}
    for url in items:
        slug = url.rsplit("/", 1)[-1]
        name = "".join(part.title() for part in slug.split("-")[:-1])   # unique per page
        pages[url] = ITEM.replace("standardx-seed", slug).replace("StandardX", name)
    return pages


def test_only_unseen_announcements_are_fetched_fund_pages_first_and_capped(monkeypatch):
    monkeypatch.setattr(soapbox.time, "sleep", lambda s: None)
    base = soapbox.BASE + "/feed/"
    sitemap = [base + s for s in ("alpha-seed", "beta-seed", "gamma-seed", "delta-seed")]
    items = sitemap + [base + "jack-and-jill-series-a", base + "souk-pre-seed"]
    client = FakeClient(_site(sitemap, items))
    seen = {base + "beta-seed", base + "souk-pre-seed"}
    result = soapbox.fetch_soapbox({"investor_pages": ["antler"], "max_items_per_run": 3},
                                   seen=lambda url: url in seen, client=client)
    fetched = [a.url for a in result.articles]
    assert fetched == [base + "jack-and-jill-series-a", base + "alpha-seed", base + "gamma-seed"]
    assert not any(url in client.asked for url in seen)
    assert result.error == "" and result.max_age_days == 120


def test_a_missing_fund_page_does_not_stop_the_rest(monkeypatch):
    monkeypatch.setattr(soapbox.time, "sleep", lambda s: None)
    base = soapbox.BASE + "/feed/"
    client = FakeClient(_site([base + "alpha-seed"], [base + "alpha-seed"]))
    result = soapbox.fetch_soapbox({"investor_pages": ["no-such-fund"]}, client=client)
    assert [a.url for a in result.articles] == [base + "alpha-seed"]
    assert result.error == ""


def test_no_sitemap_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setattr(soapbox.time, "sleep", lambda s: None)
    result = soapbox.fetch_soapbox({}, client=FakeClient({}))
    assert result.articles == [] and "sitemap" in result.error


def test_the_crawler_never_asks_for_what_robots_txt_disallows(monkeypatch):
    monkeypatch.setattr(soapbox.time, "sleep", lambda s: None)
    base = soapbox.BASE + "/feed/"
    client = FakeClient(_site([base + "alpha-seed"], [base + "alpha-seed"]))
    soapbox.fetch_soapbox({"investor_pages": ["antler"]}, client=client)
    assert client.asked and not any("/api/" in url for url in client.asked)


# ---- how it fails in production --------------------------------------------------

def _no_sleep(monkeypatch):
    monkeypatch.setattr(soapbox.time, "sleep", lambda s: None)


def test_an_undated_page_is_refused_so_the_backlog_cannot_flood_the_extractor(monkeypatch):
    _no_sleep(monkeypatch)
    base = soapbox.BASE + "/feed/"
    pages = _site([base + "alpha-seed"], [base + "alpha-seed"])
    pages[base + "alpha-seed"] = pages[base + "alpha-seed"].replace('"datePublished":"2026-09-23",', "")
    result = soapbox.fetch_soapbox({}, client=FakeClient(pages))
    assert result.articles == []
    assert "no date" in result.error


def test_a_changed_layout_is_reported_not_silently_empty(monkeypatch):
    _no_sleep(monkeypatch)
    base = soapbox.BASE + "/feed/"
    pages = _site([base + "alpha-seed"], [])
    pages[base + "alpha-seed"] = "<html><body><div>redesigned</div></body></html>"
    result = soapbox.fetch_soapbox({}, client=FakeClient(pages))
    assert result.articles == [] and "no headline" in result.error


def test_being_blocked_stops_early_instead_of_running_the_scan_out_of_time(monkeypatch):
    _no_sleep(monkeypatch)
    base = soapbox.BASE + "/feed/"
    sitemap = [base + f"item-{i}" for i in range(30)]
    client = FakeClient(_site(sitemap, []))            # every item page 404s
    result = soapbox.fetch_soapbox({"max_items_per_run": 30}, client=client)
    item_pages = {u for u in client.asked if "/feed/" in u}
    assert len(item_pages) == soapbox.MAX_CONSECUTIVE_FAILURES
    # Each refused page is tried with every agent: bounded at 3 requests a page.
    assert len([u for u in client.asked if "/feed/" in u]) <= 3 * soapbox.MAX_CONSECUTIVE_FAILURES
    assert "stopped after" in result.error


def test_the_time_budget_is_a_hard_ceiling(monkeypatch):
    _no_sleep(monkeypatch)
    clock = iter(range(0, 10_000, 200))                # every page "takes" 200 seconds
    monkeypatch.setattr(soapbox.time, "monotonic", lambda: next(clock))
    base = soapbox.BASE + "/feed/"
    sitemap = [base + f"item-{i}-seed" for i in range(10)]
    result = soapbox.fetch_soapbox({"max_items_per_run": 10}, client=FakeClient(_site(sitemap, sitemap)))
    assert 0 < len(result.articles) < 10 and "time budget" in result.error


def test_an_empty_sitemap_is_a_failure(monkeypatch):
    _no_sleep(monkeypatch)
    result = soapbox.fetch_soapbox({}, client=FakeClient({f"{soapbox.BASE}/sitemap.xml": "<urlset/>"}))
    assert "lists no announcements" in result.error


def test_health_counts_what_the_sitemap_lists_so_a_quiet_day_is_not_an_alarm(monkeypatch):
    _no_sleep(monkeypatch)
    base = soapbox.BASE + "/feed/"
    sitemap = [base + "alpha-seed", base + "beta-seed"]
    result = soapbox.fetch_soapbox({}, seen=lambda u: True, client=FakeClient(_site(sitemap, sitemap)))
    assert result.articles == [] and result.error == ""
    assert result.health_items == 2


def test_one_bad_page_among_good_ones_is_not_a_failure(monkeypatch):
    _no_sleep(monkeypatch)
    base = soapbox.BASE + "/feed/"
    sitemap = [base + "alpha-seed", base + "broken-seed", base + "gamma-seed"]
    pages = _site(sitemap, [base + "alpha-seed", base + "gamma-seed"])
    result = soapbox.fetch_soapbox({}, client=FakeClient(pages))
    assert len(result.articles) == 2 and result.error == ""


# ---- end to end through discovery, twice, against a real database -----------------

def test_two_scans_work_through_the_backlog_without_refetching(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from src.funding_radar import discovery
    from src.funding_radar.db import FundingDatabase

    _no_sleep(monkeypatch)
    base = soapbox.BASE + "/feed/"
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    old = datetime.now(timezone.utc) - timedelta(days=400)
    slugs = [f"new-{i}-seed" for i in range(3)] + [f"old-{i}-seed" for i in range(3)]
    urls = [base + s for s in slugs]
    pages = _site(urls, urls)
    for url in urls:
        when = recent if "/new-" in url else old
        pages[url] = pages[url].replace("2026-09-23", when.date().isoformat())
    client = FakeClient(pages)
    config = {"filters": {"keywords": ["raises"]},
              "soapbox": {"enabled": True, "max_items_per_run": 4, "max_age_days": 120}}
    real_collect = discovery.collect
    monkeypatch.setattr(discovery, "collect",
                        lambda cfg, client=None, seen=None: real_collect(cfg, client=client_for_test, seen=seen))
    client_for_test = client

    with FundingDatabase(tmp_path / "e2e.db") as db:
        first = discovery.discover(db, config)
        first_fetch = [u for u in client.asked if "/feed/" in u]
        assert len(first_fetch) == 4
        assert {c.article.url for c in first.candidates} == {base + f"new-{i}-seed" for i in range(3)}
        assert first.stats["stale"] == 1                           # old item: recorded, not extracted
        for candidate in first.candidates:                          # as the pipeline would
            db.record_article(candidate.article, discovery.headline_key(candidate.article.title), "extracted")

        client.asked.clear()
        second = discovery.discover(db, config)
        second_fetch = [u for u in client.asked if "/feed/" in u]
        assert set(second_fetch) == {base + "old-1-seed", base + "old-2-seed"}   # the rest, nothing twice
        assert second.candidates == [] and second.stats["stale"] == 2

        client.asked.clear()
        discovery.discover(db, config)
        assert [u for u in client.asked if "/feed/" in u] == []   # steady state: nothing to fetch
        health = {h["source"]: h for h in db.list_source_health()}["Soapbox"]
        assert health["status"] == "ok" and health["items_found"] == 6
