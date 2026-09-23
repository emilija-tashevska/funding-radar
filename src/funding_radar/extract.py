"""Stage 2: read candidate articles into structured rounds.

Claude Sonnet reads headlines and summaries in small batches. The prompt's job is
mostly to say what is *not* a prospect: a VC raising its own fund, debt, grants,
secondaries, and valuation stories all read like funding rounds in a headline.
"""

from __future__ import annotations

import json
import logging

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
- amount_value and currency: the headline figure, as reported, in its own currency;
  amount_value is a number without separators. Leave both empty when undisclosed.
- round_date: ISO date of the round if stated, else ""
- investors: named participating investors; lead_investor when the text says who led
- hq_city and hq_country: where the company is based, when stated
- region: uk, europe, us, or other — where the company is based, not its investors
- sector: exactly one label from the supplied list; "Other" when none fit
- ai_native: true when AI is the product or core to it, not when merely mentioned
- evidence: the sentence that states the amount, quoted verbatim from the text
- confidence: 0-1, how sure you are of company, amount and stage together

A headline alone is often all you get. That is fine: extract what it supports and
lower the confidence."""

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
                    "rejection": {"type": "string"},
                    "company": {"type": "string"},
                    "company_domain": {"type": "string"},
                    "summary": {"type": "string"},
                    "stage": {"type": "string"},
                    "amount_value": {"type": ["number", "null"]},
                    "currency": {"type": "string"},
                    "amount_text": {"type": "string"},
                    "round_date": {"type": "string"},
                    "investors": {"type": "array", "items": {"type": "string"}},
                    "lead_investor": {"type": "string"},
                    "hq_city": {"type": "string"},
                    "hq_country": {"type": "string"},
                    "region": {"type": "string", "enum": ["uk", "europe", "us", "other", ""]},
                    "sector": {"type": "string"},
                    "ai_native": {"type": "boolean"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
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


def _clean_round(payload: dict, article) -> Round:
    """Turn one model result into a Round, normalising what we can check ourselves."""
    amount = payload.get("amount_value")
    try:
        amount = float(amount) if amount not in (None, "") else None
    except (TypeError, ValueError):
        amount = None
    stage_raw = str(payload.get("stage") or "").strip()
    return Round(
        company=str(payload.get("company") or "").strip(),
        summary=str(payload.get("summary") or "").strip(),
        stage=normalize_stage(stage_raw) or normalize_stage(article.title),
        stage_raw=stage_raw,
        amount_text=str(payload.get("amount_text") or "").strip(),
        amount_value=amount,
        currency=str(payload.get("currency") or "").strip().upper(),
        round_date=str(payload.get("round_date") or "").strip() or article.published_at,
        investors=[str(name).strip() for name in payload.get("investors") or [] if str(name).strip()],
        lead_investor=str(payload.get("lead_investor") or "").strip(),
        hq_city=str(payload.get("hq_city") or "").strip(),
        hq_country=str(payload.get("hq_country") or "").strip(),
        region=str(payload.get("region") or "").strip().lower(),
        sector=str(payload.get("sector") or "").strip(),
        ai_native=bool(payload.get("ai_native")),
        company_domain=str(payload.get("company_domain") or "").strip(),
        evidence=str(payload.get("evidence") or "").strip(),
        confidence=float(payload.get("confidence") or 0),
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
            results[index] = str(item.get("rejection") or "not a funding round")
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
