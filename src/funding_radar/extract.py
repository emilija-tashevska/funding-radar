"""Stage 2: read candidate articles into structured rounds.

Claude Sonnet reads headlines and summaries in small batches. The prompt's job is
mostly to say what is *not* a prospect: a VC raising its own fund, debt, grants,
secondaries, and valuation stories all read like funding rounds in a headline.
"""

from __future__ import annotations

import json
import logging
import re

from src.funding_radar.llm import LLMError, complete_json
from src.funding_radar.models import Round
from src.funding_radar.qualify import normalize_stage
from src.funding_radar.settings import settings

logger = logging.getLogger(__name__)

BATCH_SIZE = 8
RUBRIC_VERSION = "extract-v1"

SYSTEM_PROMPT = """You read startup funding news and return structured facts. You are
accurate rather than generous: an empty field is better than an invented one.

For each article decide first whether it reports a NEW funding round raised by an
operating company. These are NOT rounds, and must be returned with is_round false:
- a venture firm raising or closing its own fund ("first close of Fund III")
- debt, loans, credit facilities, or grants, unless equity is also raised
- secondaries, tender offers, IPOs, acquisitions, or share buybacks
- a valuation change, funding rumour, or a round reported without new money
- market commentary, weekly round-ups, or lists of several companies
- a round you can tell was announced more than three months before the article

When it is a round, extract only what the text supports:
- company: the company that raised, not its investors or customers
- company_domain: only if the text gives it; never guess a domain from the name
- stage: exactly as described (pre-seed, seed, Series A...); "" when unstated
- amount_value and currency: the headline figure in its own currency, written in
  WHOLE UNITS, not millions: $12M is 12000000, €4.35 million is 4350000, £800k is
  800000. Leave both empty when the amount is undisclosed.
- investors: named participating investors, the lead first when the text says who led
- hq: "City, Country" where the company is based, when stated
- region: exactly one of uk, europe, us, other — where the company is based, not
  where its investors are. Always give one; infer from the city or country when the
  text does not say it outright.
- sector: exactly one label from the supplied list; "Other" when none fit
- ai_native: true when AI is the product or core to it, not when merely mentioned
- evidence: the sentence that states the amount, quoted verbatim from the text
- summary: one sentence on what the company does

When is_round is false, leave the other fields empty and put the reason in summary.

Judge every article on its own. Two articles covering the same round are both
rounds, even when they appear side by side and even when the amounts differ because
one outlet converted the currency: report each one. Merging duplicates happens later,
and a round reported twice is stronger evidence, not weaker.

A headline alone is often all you get. That is fine: extract what it supports and
lower the confidence."""

# Structured outputs cap an item at 14 properties, so every field here earns its
# place. Casualties, and how they are covered instead:
#   round_date  -> the article's publication date, which is within days of the round
#   lead investor -> the prompt asks for investors lead-first, so it is investors[0]
#   rejection reason -> reuses `summary` when is_round is false
#   confidence  -> inferred from whether amount and stage are present
MAX_SCHEMA_FIELDS = 14

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "is_round": {"type": "boolean"},
                    "company": {"type": "string"},
                    "company_domain": {"type": "string"},
                    "summary": {"type": "string"},
                    "stage": {"type": "string"},
                    "amount_value": {"type": "number"},
                    "currency": {"type": "string"},
                    "investors": {"type": "array", "items": {"type": "string"}},
                    "region": {"type": "string"},
                    "hq": {"type": "string"},
                    "sector": {"type": "string"},
                    "ai_native": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["index", "is_round"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def build_prompt(candidates: list, sectors: list[str]) -> str:
    articles = []
    for index, candidate in enumerate(candidates):
        article = candidate.article if hasattr(candidate, "article") else candidate
        articles.append({
            "index": index,
            "title": article.title,
            "outlet": article.source,
            "published_at": article.published_at or "",
            "summary": (article.summary or "")[:1200],
        })
    return (
        f"Sector labels (choose exactly one per company):\n{json.dumps(sectors, indent=2)}\n\n"
        f"Articles:\n{json.dumps(articles, indent=2)}"
    )


# Country to region, for when the model leaves region blank but names a location.
UK_NAMES = {"united kingdom", "uk", "u.k.", "great britain", "england", "scotland", "wales",
            "northern ireland", "britain"}
EUROPE_NAMES = {
    "ireland", "france", "germany", "spain", "italy", "netherlands", "belgium", "portugal",
    "sweden", "norway", "denmark", "finland", "iceland", "poland", "czechia", "czech republic",
    "austria", "switzerland", "estonia", "latvia", "lithuania", "greece", "romania", "bulgaria",
    "hungary", "slovakia", "slovenia", "croatia", "serbia", "ukraine", "luxembourg", "malta",
    "cyprus", "europe",
}
US_NAMES = {"united states", "usa", "us", "u.s.", "u.s.a."}
# A round written in millions rather than whole units: "12" cannot be a real round size.
IMPLAUSIBLY_SMALL = 100_000
MILLION_RE = re.compile(r"\b(m|mn|million|millions|millionen|miljoen|miljoner|millones)\b|\d\s*m\b", re.I)
BILLION_RE = re.compile(r"\b(bn|billion|billions|milliarde[nr]?|miljard)\b|\d\s*b\b", re.I)


def region_for(country: str, city: str = "") -> str:
    """Map a stated location onto our four regions."""
    for value in (country, city):
        text = (value or "").strip().lower().rstrip(".")
        if not text:
            continue
        if text in UK_NAMES or text in {"london", "manchester", "cambridge", "oxford", "edinburgh", "bristol"}:
            return "uk"
        if text in US_NAMES or text in {"san francisco", "new york", "boston", "seattle", "austin"}:
            return "us"
        if text in EUROPE_NAMES or text in {"berlin", "paris", "amsterdam", "stockholm", "madrid",
                                            "milan", "lisbon", "dublin", "helsinki", "copenhagen",
                                            "oslo", "zurich", "munich", "vienna", "warsaw", "tallinn"}:
            return "europe"
    return ""


def rescale_amount(amount: float | None, text: str) -> float | None:
    """Correct an amount written in millions or billions rather than whole units.

    Models report "$140M" as 140 often enough that trusting the raw number would
    silently drop every large round below the size floor.
    """
    if amount is None or amount >= IMPLAUSIBLY_SMALL:
        return amount
    if BILLION_RE.search(text):
        return amount * 1_000_000_000
    if MILLION_RE.search(text):
        return amount * 1_000_000
    return amount


def _clean_round(payload: dict, article) -> Round:
    """Turn one model result into a Round, normalising what we can check ourselves."""
    amount = payload.get("amount_value")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    evidence = str(payload.get("evidence") or "").strip()
    amount = rescale_amount(amount, f"{evidence} {article.title}")

    stage_raw = str(payload.get("stage") or "").strip()
    investors = [str(name).strip() for name in payload.get("investors") or [] if str(name).strip()]
    hq = str(payload.get("hq") or "").strip()
    city, _, country = hq.partition(",")
    stage = normalize_stage(stage_raw) or normalize_stage(article.title)
    region = str(payload.get("region") or "").strip().lower()
    # "other" is also worth checking: a Helsinki company came back as other when the
    # location in the same result says plainly that it is European.
    if region not in ("uk", "europe", "us"):
        region = region_for(country, city) or region
    return Round(
        company=str(payload.get("company") or "").strip(),
        summary=str(payload.get("summary") or "").strip(),
        stage=stage,
        stage_raw=stage_raw,
        amount_value=amount,
        currency=str(payload.get("currency") or "").strip().upper(),
        # Boards rarely date the round itself; the article is within days of it.
        round_date=article.published_at,
        investors=investors,
        lead_investor=investors[0] if investors else "",
        hq_city=city.strip(),
        hq_country=country.strip(),
        region=region,
        sector=str(payload.get("sector") or "").strip(),
        ai_native=bool(payload.get("ai_native")),
        company_domain=str(payload.get("company_domain") or "").strip(),
        evidence=evidence,
        # Nothing to calibrate a self-reported score against, so confidence reflects
        # how much of the round the article actually pinned down.
        confidence=round(0.4 + 0.3 * bool(amount) + 0.2 * bool(stage) + 0.1 * bool(payload.get("company_domain")), 2),
    )


def extract_batch(candidates: list, sectors: list[str], *, model: str | None = None) -> dict[int, Round | str]:
    """Extract one batch. Maps candidate index to a Round, or to a rejection reason."""
    if not candidates:
        return {}
    payload = complete_json(
        SYSTEM_PROMPT,
        build_prompt(candidates, sectors),
        RESULT_SCHEMA,
        model=model or settings.EXTRACT_MODEL,
        effort=settings.EXTRACT_EFFORT,
    )
    results: dict[int, Round | str] = {}
    for item in payload.get("results", []):
        index = item.get("index")
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            logger.warning("Extraction returned an out-of-range index: %r", index)
            continue
        candidate = candidates[index]
        article = candidate.article if hasattr(candidate, "article") else candidate
        if not item.get("is_round"):
            # The prompt reuses `summary` for the reason when this is not a round.
            results[index] = str(item.get("summary") or "not a funding round")
            continue
        round_ = _clean_round(item, article)
        if not round_.company:
            results[index] = "no company named"
            continue
        # A sector outside the supplied list would break the site's filters.
        if round_.sector not in sectors:
            round_.sector = "Other"
        results[index] = round_
    missing = set(range(len(candidates))) - set(results)
    for index in missing:
        results[index] = "no result returned"
    return results


def extract(candidates: list, sectors: list[str], *, batch_size: int = BATCH_SIZE,
            max_calls: int | None = None) -> tuple[dict[int, Round | str], dict]:
    """Extract every candidate, in batches, tolerating a failed batch."""
    stats = {"batches": 0, "failed_batches": 0, "rounds": 0, "rejected": 0, "skipped_over_cap": 0}
    results: dict[int, Round | str] = {}
    cap = settings.MAX_EXTRACTIONS_PER_RUN if max_calls is None else max_calls
    allowed = candidates[:cap]
    stats["skipped_over_cap"] = len(candidates) - len(allowed)
    if stats["skipped_over_cap"]:
        logger.warning("Extraction cap reached: %d candidate(s) left for the next run", stats["skipped_over_cap"])

    for start in range(0, len(allowed), batch_size):
        batch = allowed[start : start + batch_size]
        stats["batches"] += 1
        try:
            batch_results = extract_batch(batch, sectors)
        except LLMError as exc:
            # One bad batch must not lose the rest of the run; these candidates are
            # simply not recorded, so tomorrow's run sees them again.
            logger.warning("Extraction batch failed: %s", exc)
            stats["failed_batches"] += 1
            continue
        for offset, result in batch_results.items():
            results[start + offset] = result
            if isinstance(result, Round):
                stats["rounds"] += 1
            else:
                stats["rejected"] += 1
    return results, stats
