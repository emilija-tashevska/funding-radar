"""Learn how a news site is built before writing a source for it. Runs on the runner.

Prints what a crawler would need: robots.txt (what we may fetch, and how often), the
sitemap (every item URL and whether it carries dates), any RSS the site hides, how
the listing page links to items and paginates, what one item page carries (title,
date, description, structured data, body), and a set of extra pages to probe.

    python scripts/explore_site.py https://www.soapbox.vc --listing /feed \\
        --probe /feed/rss.xml --probe /investor/antler
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from src.funding_radar.sources.base import get_with_agents, http_client  # noqa: E402

DATE_RE = re.compile(
    r"\b(\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2})\b")
OUT: list[str] = []


def say(text: str = "") -> None:
    print(text)
    OUT.append(text)


def get(client, url: str):
    try:
        return get_with_agents(client, url)
    except Exception as exc:  # noqa: BLE001
        say(f"  ! {url}: {str(exc)[:160]}")
        return None


def section(title: str) -> None:
    say(f"\n### {title}")


def robots(client, base: str) -> None:
    section("robots.txt")
    response = get(client, urljoin(base, "/robots.txt"))
    if response is not None:
        say("```\n" + response.text[:1500] + "\n```")


def sitemap(client, base: str, item_prefix: str) -> None:
    section("sitemap")
    response = get(client, urljoin(base, "/sitemap.xml"))
    if response is None:
        return
    soup = BeautifulSoup(response.text, "xml")
    children = [loc.get_text(strip=True) for loc in soup.select("sitemap > loc")]
    urls = soup.find_all("url")
    for child in children[:10]:
        say(f"  child sitemap: {child}")
        more = get(client, child)
        if more is not None:
            urls += BeautifulSoup(more.text, "xml").find_all("url")
    entries = [(u.loc.get_text(strip=True) if u.loc else "", u.lastmod.get_text(strip=True) if u.lastmod else "")
               for u in urls]
    say(f"  {len(entries)} URLs; {sum(1 for _, m in entries if m)} carry <lastmod>")
    sections = Counter("/" + (urlparse(u).path.strip("/").split("/") or [""])[0] for u, _ in entries)
    say("  by first path segment: " + ", ".join(f"{k} {v}" for k, v in sections.most_common(12)))
    items = [(u, m) for u, m in entries if urlparse(u).path.startswith(item_prefix + "/")]
    say(f"  {len(items)} item URLs under {item_prefix}/")
    for url, mod in sorted(items, key=lambda e: e[1], reverse=True)[:8]:
        say(f"    {mod or '(no date)'}  {url}")


def feeds(client, base: str, paths: list[str]) -> None:
    section("hidden feeds")
    for path in paths:
        response = get(client, urljoin(base, path))
        if response is None:
            continue
        parsed = feedparser.parse(response.text)
        head = " ".join(response.text[:120].split())
        say(f"  {path}: HTTP {response.status_code} {response.headers.get('content-type', '?')}; "
            f"{len(parsed.entries)} entries; starts: {head}")
        for entry in parsed.entries[:3]:
            say(f"    - {entry.get('published', '?')} | {entry.get('title', '')[:110]}")


def listing(client, base: str, path: str, item_prefix: str) -> list[str]:
    section(f"listing page {path}")
    response = get(client, urljoin(base, path))
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "lxml")
    say(f"  HTTP {response.status_code}, {len(response.text):,} bytes, title: {soup.title.get_text(strip=True) if soup.title else '-'}")
    for link in soup.find_all("link", rel="alternate"):
        say(f"  <link rel=alternate type={link.get('type')} href={link.get('href')}>")
    items, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"])
        if urlparse(href).path.startswith(item_prefix + "/") and href not in seen:
            seen.add(href)
            card = anchor.find_parent(["div", "article", "li"]) or anchor
            text = " ".join(anchor.get_text(" ", strip=True).split())
            dates = DATE_RE.findall(card.get_text(" ", strip=True))
            items.append(href)
            if len(items) <= 12:
                say(f"  item: {text[:120]!r} date-on-card={dates[:1]} -> {href}")
    say(f"  {len(items)} item links on the page")
    pages = sorted({a["href"] for a in soup.find_all("a", href=True) if "_page=" in a["href"]})
    say(f"  pagination links: {pages[:6]}")
    say(f"  Webflow site id: {soup.html.get('data-wf-site') if soup.html else None}; "
        f"collection lists: {len(soup.select('.w-dyn-list'))}; items rendered: {len(soup.select('.w-dyn-item'))}")
    return items


def item_page(client, url: str) -> None:
    section(f"item page {url}")
    response = get(client, url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, "lxml")
    say(f"  <title>: {soup.title.get_text(strip=True) if soup.title else '-'}")
    say(f"  <h1>: {[h.get_text(' ', strip=True) for h in soup.find_all('h1')][:2]}")
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name")
        if key and any(k in key for k in ("description", "og:", "article:", "date", "twitter:title")):
            say(f"  meta {key}: {(meta.get('content') or '')[:200]}")
    for script in soup.find_all("script", type="application/ld+json"):
        say(f"  JSON-LD: {' '.join((script.string or '').split())[:600]}")
    for tag in soup.find_all("time"):
        say(f"  <time>: {tag.get('datetime')} {tag.get_text(strip=True)}")
    body = soup.select_one(".w-richtext") or soup.find("article") or soup.find("main") or soup.body
    text = " ".join(body.get_text(" ", strip=True).split()) if body else ""
    say(f"  dates in page: {DATE_RE.findall(soup.get_text(' ', strip=True))[:5]}")
    say(f"  body ({len(text)} chars, from {'.w-richtext' if soup.select_one('.w-richtext') else 'fallback'}): {text[:1200]}")
    classes = Counter(c for el in soup.find_all(class_=True) for c in el.get("class", []))
    say(f"  common classes: {[c for c, _ in classes.most_common(25)]}")


def probes(client, base: str, paths: list[str], item_prefix: str) -> None:
    section("probed pages")
    for path in paths:
        response = get(client, urljoin(base, path))
        if response is None:
            continue
        soup = BeautifulSoup(response.text, "lxml")
        links = {urljoin(base, a["href"]) for a in soup.find_all("a", href=True)
                 if urlparse(urljoin(base, a["href"])).path.startswith(item_prefix + "/")}
        say(f"  {path}: HTTP {response.status_code}, title {soup.title.get_text(strip=True)[:80] if soup.title else '-'}, "
            f"{len(links)} item links")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--listing", default="/")
    parser.add_argument("--items", default="/feed", help="path prefix of item pages")
    parser.add_argument("--feed", action="append", default=[], help="candidate feed paths")
    parser.add_argument("--probe", action="append", default=[])
    args = parser.parse_args()
    say(f"## Exploring {args.base}")
    with http_client() as client:
        robots(client, args.base)
        sitemap(client, args.base, args.items)
        feeds(client, args.base, args.feed)
        items = listing(client, args.base, args.listing, args.items)
        if items:
            item_page(client, items[0])
        probes(client, args.base, args.probe, args.items)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("\n".join(OUT) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
