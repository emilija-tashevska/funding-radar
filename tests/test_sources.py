import feedparser
import pytest

from src.funding_radar.sources import feeds
from src.funding_radar.sources.base import headline_key, to_iso

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Metris Energy raises &#8364;4.35 million seed</title>
<link>https://eu-startups.com/metris</link>
<description>&lt;p&gt;London-based &lt;b&gt;Metris&lt;/b&gt; raised &#8364;4.35M&lt;/p&gt;</description>
<pubDate>Mon, 21 Sep 2026 07:15:35 GMT</pubDate></item>
<item><title>No link here</title><link></link></item>
</channel></rss>"""


def test_feed_entries_become_articles_with_clean_text(monkeypatch):
    parsed = feedparser.parse(RSS)
    monkeypatch.setattr(feeds.feedparser, "parse", lambda url, **kwargs: parsed)
    result = feeds.fetch_rss("EU-Startups", "https://example.test/feed")
    assert result.error == ""
    assert len(result.articles) == 1  # the entry without a link is dropped
    article = result.articles[0]
    assert article.title == "Metris Energy raises €4.35 million seed"
    assert "raised €4.35M" in article.summary and "<b>" not in article.summary
    assert article.published_at.startswith("2026-09-21")


def test_a_broken_feed_is_reported_not_raised(monkeypatch):
    def boom(url, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(feeds.feedparser, "parse", boom)
    result = feeds.fetch_rss("Sifted", "https://sifted.eu/feed")
    assert result.articles == [] and "connection reset" in result.error


def test_google_news_keeps_the_original_publisher(monkeypatch):
    entry = feedparser.parse(RSS).entries[0]

    entry["source"] = {"title": "Reuters"}
    monkeypatch.setattr(feeds.feedparser, "parse", lambda url: type("F", (), {"entries": [entry], "bozo": 0})())
    result = feeds.fetch_google_news("raises series a London", {"hl": "en-GB"}, "when:2d")
    assert result.articles[0].source == "Google News · Reuters"
    assert result.kind == "google_news"


def test_the_google_news_query_url_carries_query_locale_and_window():
    url = feeds.google_news_url('"raises" London', {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"}, "when:2d")
    assert "q=%22raises%22+London+when%3A2d" in url and "ceid=GB%3AEN".lower() in url.lower()


@pytest.mark.parametrize(
    "title",
    [
        "Metris Energy raises €4.35 million - EU-Startups",
        "Metris Energy raises €4.35 million | UKTN",
        "Metris Energy raises &#8364;4.35 million – Tech.eu",
        "Metris Energy raises €4.35 million",
    ],
)
def test_the_same_story_across_outlets_shares_a_headline_key(title):
    assert headline_key(title) == "metris energy raises 4 35 million"


def test_dates_parse_from_the_formats_feeds_actually_use():
    assert to_iso("Mon, 21 Sep 2026 07:15:35 GMT").startswith("2026-09-21")
    assert to_iso("20260921T071535Z").startswith("2026-09-21")
    assert to_iso("2026-09-21T07:15:35Z").startswith("2026-09-21")
    assert to_iso("not a date") is None


VC_HTML = """
<html><body><ul>
  <li><time datetime="2026-09-18">18 Sep</time>
      <a href="/news/acme-raises-series-a">Acme raises $12M Series A led by Index</a></li>
  <li><a href="/news/short">Short</a></li>
  <li><a href="https://techcrunch.com/elsewhere">Coverage on another site about a raise</a></li>
  <li><a href="/privacy">Our privacy policy and what it means for you</a></li>
  <li><a href="/news/acme-raises-series-a">Acme raises $12M Series A led by Index</a></li>
</ul></body></html>
"""


class _Resp:
    def __init__(self, text="", status=200, headers=None, payload=None):
        self.text, self.status_code = text, status
        self.headers = headers or {"content-type": "text/html"}
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self._response = response

    def get(self, url, **kwargs):
        return self._response


def test_vc_page_keeps_headlines_and_drops_navigation():
    from src.funding_radar.sources.vc_pages import fetch_vc_page

    result = fetch_vc_page("Index Ventures", "https://indexventures.com/news/", client=_Client(_Resp(VC_HTML)))
    titles = [a.title for a in result.articles]
    assert titles == ["Acme raises $12M Series A led by Index"]  # short, off-site, policy and duplicate all dropped
    assert result.articles[0].url == "https://indexventures.com/news/acme-raises-series-a"
    assert result.articles[0].published_at.startswith("2026-09-18")


def test_gdelt_returns_articles_and_reports_html_error_pages():
    from src.funding_radar.sources import gdelt

    payload = {"articles": [{"title": "Acme raises $12M", "url": "https://x.test/a", "domain": "x.test", "seendate": "20260921T071535Z"}]}
    ok = gdelt.fetch_gdelt("q", "2d", client=_Client(_Resp(payload=payload, headers={"content-type": "application/json"})))
    assert [a.title for a in ok.articles] == ["Acme raises $12M"]
    assert ok.articles[0].published_at.startswith("2026-09-21")

    bad = gdelt.fetch_gdelt("q", "2d", client=_Client(_Resp(text="<html>error</html>")))
    assert bad.articles == [] and "non-JSON" in bad.error


@pytest.mark.parametrize(
    "title,expected",
    [
        ("London’s Metris Energy raises €4.35 million to scale AI platform", "metris energy"),
        ("Metris Energy raises $5M seed to unify fragmented energy data - Dealroom", "metris energy"),
        ("Berlin-based mika raises €6 million to scale its AI-native tax alternative", "mika"),
        ("Exclusive: NIF backs defence startup Terasi in €11m raise", "nif backs defence startup"),
        ("Magic AI raises £8m to take its fitness mirror to the US", "magic ai"),
        ("Magic AI raises $11M to take its hologram fitness mirror to the US", "magic ai"),
        ("AI startup Mantic raises $25 million for superhuman forecasting", "mantic"),
    ],
)
def test_company_hint_survives_different_headlines(title, expected):
    from src.funding_radar.sources.base import company_hint

    assert company_hint(title) == expected


def test_valuation_headlines_still_resolve_to_the_company():
    from src.funding_radar.sources.base import company_hint

    # Same company, three headline shapes; coarse matching has to see one company.
    assert company_hint("Tekever nearly quintuples valuation to $6.4B with $580M round") == "tekever"
    assert company_hint("TEKEVER raises $580M Series D at $6.4B valuation") == "tekever"
    assert company_hint("Heidi valuation doubles to $900M on $340M funding round") == "heidi"


def test_each_agent_is_tried_before_a_source_is_called_blocked(monkeypatch):
    """Finsmes served this laptop and refused GitHub's runners with a 403."""
    import httpx

    from src.funding_radar.sources import base

    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)
    seen = []

    class Client:
        def get(self, url, headers=None, **kwargs):
            agent = (headers or {}).get("User-Agent", "")
            seen.append(agent)
            request = httpx.Request("GET", url)
            status = 403 if len(seen) < 3 else 200
            return httpx.Response(status, request=request, text="<rss/>")

    response = base.get_with_agents(Client(), "https://finsmes.com/feed")
    assert response.status_code == 200
    assert seen == base.agents()  # in order, browser agent first


def test_a_source_that_refuses_every_agent_raises(monkeypatch):
    import httpx
    import pytest

    from src.funding_radar.sources import base

    monkeypatch.setattr(base.time, "sleep", lambda seconds: None)

    class Client:
        def get(self, url, headers=None, **kwargs):
            return httpx.Response(429, request=httpx.Request("GET", url))

    with pytest.raises(httpx.HTTPStatusError):
        base.get_with_agents(Client(), "https://atomico.com/news")

