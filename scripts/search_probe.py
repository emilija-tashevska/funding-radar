"""Which search API finds new in-brief UK rounds, and for how much?

Runs the queries in config `web_search` through every provider whose key is set
(Anthropic, Tavily, OpenAI), puts everything they find through the real funding
gate, extractor and brief, and compares each provider against the database:

    provider | searches | cost | articles | recent | funding-like | rounds |
             in brief | UK in brief | new to the database | new UK in brief

Extraction runs once over the union of articles, so the comparison costs one
extractor pass, not three. Run after a discovery dry run on the same database,
seen_by_current_sources counts what the existing feeds and searches find too. Also tests whether Anthropic web search and structured
outputs work in one call. Spends real money: about six searches per provider plus a
few extraction batches.

    python scripts/search_probe.py [--out probe_report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.funding_radar.db import FundingDatabase  # noqa: E402
from src.funding_radar.discovery import Candidate, _is_recent, looks_like_funding  # noqa: E402
from src.funding_radar.extract import extract  # noqa: E402
from src.funding_radar.models import Round  # noqa: E402
from src.funding_radar.qualify import Rules, qualifies  # noqa: E402
from src.funding_radar.settings import settings  # noqa: E402
from src.funding_radar.sources.base import headline_key, load_config  # noqa: E402
from src.funding_radar.sources import web_search as ws  # noqa: E402
from src.funding_radar.sources.feeds import fetch_google_news  # noqa: E402

BLOCKED_PUBLISHERS = ("finsmes.com", "atomico.com", "speedinvest.com")
RECENT_DAYS = 10


def composition_test(model: str, api_key: str) -> dict:
    """Does web search work alongside output_config.format in a single call?"""
    import anthropic

    schema = {"type": "object", "properties": {"headlines": {"type": "array", "items": {"type": "string"}}},
              "required": ["headlines"], "additionalProperties": False}
    try:
        response = anthropic.Anthropic(api_key=api_key).messages.create(
            model=model, max_tokens=2000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            tools=[{"type": ws.ANTHROPIC_TOOL, "name": "web_search", "max_uses": 1}],
            messages=[{"role": "user", "content": "UK startup raises seed round: list the headlines."}],
        )
    except Exception as exc:  # noqa: BLE001
        body = getattr(getattr(exc, "response", None), "text", "") or ""
        return {"works": False, "error": f"{exc}"[:200] + (f" | {body[:300]}" if body else "")}
    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"works": False, "error": f"reply was not JSON: {text[:200]}"}
    return {"works": True, "headlines": len(parsed.get("headlines", [])),
            "cost_usd": round(ws.anthropic_cost(response.usage).usd, 4)}


def run_provider(provider: str, queries: list[str], conf: dict) -> tuple[list, ws.SearchCost, list[str]]:
    articles, cost, errors = [], ws.SearchCost(), []
    for query in queries:
        if provider == "anthropic":
            result, spent = ws.search_anthropic(query, model=settings.EXTRACT_MODEL,
                                                api_key=settings.ANTHROPIC_API_KEY,
                                                country=conf.get("country", "GB"))
        elif provider == "google_news":
            google = load_config().get("google_news", {})
            result = fetch_google_news(query, google.get("locale", {}), google.get("window", "when:3d"))
            spent = ws.SearchCost(searches=1)
        elif provider == "tavily":
            result, spent = ws.search_tavily(query, api_key=os.environ["TAVILY_API_KEY"],
                                             days=int(conf.get("days", 3)))
        else:
            result, spent = ws.search_openai(query, model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                                             api_key=os.environ["OPENAI_API_KEY"],
                                             country=conf.get("country", "GB"))
        if result.error:
            errors.append(f"{query}: {result.error}")
        articles.extend(result.articles)
        cost.add(spent)
    return articles, cost, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="probe_report.json")
    parser.add_argument("--set", default="queries", choices=["queries", "fund_queries"],
                        help="general UK queries, or the tracked-fund queries")
    parser.add_argument("--skip-composition", action="store_true")
    args = parser.parse_args()

    config = load_config()
    conf = config.get("web_search", {})
    queries = conf.get(args.set, [])
    rules = Rules.from_config(config)
    # Google News needs no key and costs nothing: the free baseline to beat.
    keys = {"anthropic": settings.ANTHROPIC_API_KEY, "google_news": "free", "tavily": os.getenv("TAVILY_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", "")}
    report: dict = {"query_set": args.set, "queries": queries, "providers": {}, "skipped": [p for p, k in keys.items() if not k]}

    if keys["anthropic"] and not args.skip_composition:
        report["anthropic_search_with_structured_output"] = composition_test(
            settings.EXTRACT_MODEL, keys["anthropic"])

    found: dict[str, list] = {}
    for provider, key in keys.items():
        if not key:
            continue
        articles, cost, errors = run_provider(provider, queries, conf)
        found[provider] = articles
        report["providers"][provider] = {"cost": cost.__dict__, "errors": errors}

    # One extraction pass over everything the providers found that looks like a round.
    by_url, providers_for = {}, defaultdict(set)
    for provider, articles in found.items():
        for article in articles:
            providers_for[article.url].add(provider)
            by_url.setdefault(article.url, article)
    fundingish = [a for a in by_url.values()
                  if _is_recent(a, RECENT_DAYS) and looks_like_funding(f"{a.title} {a.summary}")]
    fundingish_urls = {a.url for a in fundingish}
    results: dict = {}
    extract_stats: dict = {}
    if fundingish and keys["anthropic"]:
        results, extract_stats = extract([Candidate(article=a) for a in fundingish], config.get("sectors", []))
    report["extraction"] = extract_stats
    # Why each funding-like article did or did not become a round, to read by eye.
    decisions = []
    for index, article in enumerate(fundingish):
        result = results.get(index, "not extracted")
        if isinstance(result, Round):
            verdict = qualifies(result, rules)
            outcome = (f"ROUND {result.company} | {result.amount_value} {result.currency} "
                       f"{result.stage or 'stage?'} | {result.region or 'region?'} | "
                       f"{'in brief' if verdict.qualified else verdict.reason}")
        else:
            outcome = f"rejected: {result}"
        decisions.append({"title": article.title, "url": article.url,
                          "published": article.published_at, "has_snippet": bool(article.summary),
                          "outcome": outcome})
    report["decisions"] = decisions

    with FundingDatabase(settings.DATABASE_PATH) as db:
        rounds_by_url = {}
        for index, result in results.items():
            if isinstance(result, Round):
                round_ = result
                verdict = qualifies(round_, rules)
                known = db.find_company(round_.company_domain, round_.company)
                rounds_by_url[fundingish[index].url] = (round_, verdict.qualified, known is None)

        for provider, articles in found.items():
            urls = {a.url for a in articles}
            recent = [a for a in articles if _is_recent(a, RECENT_DAYS)]
            funding = [a for a in recent if a.url in fundingish_urls]
            seen = sum(1 for a in articles if db.is_article_seen(a.article_id, headline_key(a.title)))
            rounds = [rounds_by_url[u] for u in urls if u in rounds_by_url]
            new_uk = [r for r, ok, new in rounds if ok and new and r.region == "uk"]
            entry = report["providers"][provider]
            entry.update({
                "articles": len(urls),
                "seen_by_current_sources": seen,
                "recent": len(recent),
                "funding_like": len(funding),
                "rounds": len(rounds),
                "in_brief": sum(1 for _, ok, _ in rounds if ok),
                "uk_in_brief": sum(1 for r, ok, _ in rounds if ok and r.region == "uk"),
                "new_in_brief": sum(1 for _, ok, new in rounds if ok and new),
                "new_uk_in_brief": len(new_uk),
                "only_this_provider": sum(1 for u in urls if providers_for[u] == {provider}),
                "blocked_publisher_hits": sorted({urlparse(u).netloc for u in urls
                                                  if urlparse(u).netloc.endswith(BLOCKED_PUBLISHERS)}),
                "new_uk_rounds": [
                    {"company": r.company, "amount": r.amount_value, "currency": r.currency,
                     "stage": r.stage, "hq": ", ".join(filter(None, [r.hq_city, r.hq_country]))}
                    for r in new_uk],
            })
            cost = entry["cost"]
            entry["usd_per_new_in_brief"] = (round(cost["usd"] / entry["new_in_brief"], 3)
                                             if entry["new_in_brief"] else None)

    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(render(report))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(render(report) + "\n")
    return 0


def render(report: dict) -> str:
    lines = [f"## Search probe: {report.get('query_set', 'queries')}", ""]
    comp = report.get("anthropic_search_with_structured_output")
    if comp:
        lines.append(f"Anthropic web search + structured outputs in one call: **{'works' if comp['works'] else 'fails'}**"
                     + (f" ({comp.get('error')})" if not comp["works"] else ""))
        lines.append("")
    if report["skipped"]:
        lines.append(f"Skipped (no key set): {', '.join(report['skipped'])}")
        lines.append("")
    cols = ["searches", "usd", "articles", "seen_by_current_sources", "recent", "funding_like", "rounds",
            "in_brief", "uk_in_brief", "new_in_brief", "new_uk_in_brief", "usd_per_new_in_brief"]
    lines.append("| provider | " + " | ".join(cols) + " |")
    lines.append("|---" * (len(cols) + 1) + "|")
    for name, entry in report["providers"].items():
        cost = entry.get("cost", {})
        values = [cost.get("searches"), f"{cost.get('usd', 0):.3f}"] + [entry.get(c) for c in cols[2:]]
        lines.append(f"| {name} | " + " | ".join(str(v) for v in values) + " |")
    for name, entry in report["providers"].items():
        if entry.get("errors"):
            lines.append(f"\n{name} errors: " + "; ".join(entry["errors"][:3]))
        if entry.get("blocked_publisher_hits"):
            lines.append(f"\n{name} reached blocked publishers: {', '.join(entry['blocked_publisher_hits'])}")
        for r in entry.get("new_uk_rounds", []):
            lines.append(f"- {name}: {r['company']} — {r['amount']} {r['currency']} {r['stage']} ({r['hq']})")
    if report.get("extraction"):
        lines.append(f"\nExtraction: {report['extraction']}")
    if report.get("decisions"):
        lines += ["", "| funding-like article | snippet | outcome |", "|---|---|---|"]
        for d in report["decisions"]:
            lines.append(f"| {d['title'][:90].replace('|', '/')} | {'yes' if d['has_snippet'] else 'no'} "
                         f"| {d['outcome'][:140].replace('|', '/')} |")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
