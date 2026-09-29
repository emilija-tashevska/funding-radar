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
        pages[url] = ITEM.replace("standardx-seed", slug).replace("StandardX", slug.split("-")[0].title())
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
