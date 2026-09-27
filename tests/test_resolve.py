"""Google News links resolved to the publisher, offline, from recorded shapes."""

import json
from urllib.parse import unquote

import httpx

from src.funding_radar import resolve

GN = "https://news.google.com/rss/articles/CBMiQUFVX3lxTE5hYmM?oc=5"
PAGE = ('<html><body><c-wiz><div jscontroller="x" data-n-a-sg="SIG123" data-n-a-ts="1727400000">'
        '</div></c-wiz></body></html>')


def _reply(url):
    inner = json.dumps(["garturlres", url, 1])
    return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"],
                                    ["di", 42], ["af.httprm", 41, "123", 1]])


def test_the_id_is_read_from_rss_and_plain_article_links():
    assert resolve.article_id(GN) == "CBMiQUFVX3lxTE5hYmM"
    assert resolve.article_id("https://news.google.com/articles/ABC_d-9") == "ABC_d-9"
    assert resolve.article_id("https://tech.eu/2026/09/acme") == ""


def test_signature_and_timestamp_come_off_the_page():
    assert resolve.decoding_params(PAGE) == ("SIG123", "1727400000")
    assert resolve.decoding_params("<html></html>") == ("", "")


def test_the_payload_carries_id_timestamp_and_signature():
    payload = unquote(resolve.batch_payload("ID1", "SIG", "99"))
    assert payload.startswith("f.req=")
    assert '\\"ID1\\",99,\\"SIG\\"' in payload


def test_the_url_is_read_out_of_the_reply():
    assert resolve.parse_batch_response(_reply("https://sifted.eu/articles/acme")) == "https://sifted.eu/articles/acme"
    assert resolve.parse_batch_response(")]}'\n\n[]") == ""
    assert resolve.parse_batch_response("garbage") == ""


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_google_link_resolves_to_the_publisher():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, text=PAGE)
        assert "SIG123" in unquote(request.content.decode())
        return httpx.Response(200, text=_reply("https://www.uktech.news/funding/acme-raises"))

    assert resolve.resolve_google_news(GN, client=_client(handler)) == "https://www.uktech.news/funding/acme-raises"


def test_every_failure_returns_nothing_rather_than_raising():
    assert resolve.resolve_google_news("https://tech.eu/a", client=_client(lambda r: httpx.Response(500))) == ""
    assert resolve.resolve_google_news(GN, client=_client(lambda r: httpx.Response(429))) == ""
    assert resolve.resolve_google_news(GN, client=_client(lambda r: httpx.Response(200, text="<html/>"))) == ""

    def loops_back(request):
        return httpx.Response(200, text=PAGE if request.method == "GET" else _reply(GN))

    assert resolve.resolve_google_news(GN, client=_client(loops_back)) == ""
