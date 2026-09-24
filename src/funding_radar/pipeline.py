"""One run: discover, extract, qualify, store.

Scoring, the digest and the site come later; this is everything up to having
trustworthy rows in the database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.funding_radar.db import FundingDatabase
from src.funding_radar.discovery import discover
from src.funding_radar.extract import extract
from src.funding_radar.models import Round
from src.funding_radar.qualify import Rules, comparable_usd, is_confirmed, qualifies
from src.funding_radar.settings import settings
from src.funding_radar.sources.base import company_hint, headline_key, load_config

logger = logging.getLogger(__name__)

# Two outlets disagreeing by more than this about the size is worth flagging.
AMOUNT_DISAGREEMENT = 0.2


def register_tracked_investors(db: FundingDatabase, config: dict) -> None:
    """Investors named in config get their per-fund size floor stored alongside them."""
    floors = (config.get("filters", {}) or {}).get("investor_floors") or {}
    for name, floor in floors.items():
        db.upsert_investor(name, tracked=True, min_amount=float(floor))
    for page in config.get("vc_pages", []):
        db.upsert_investor(page["name"], tracked=True)


def _amounts_disagree(sources: list[dict]) -> bool:
    """Outlets convert currencies, so compare in one of them before crying foul."""
    values = [usd for usd in (comparable_usd(s.get("amount_value"), s.get("currency") or "USD")
                              for s in sources) if usd]
    if len(values) < 2:
        return False
    return (max(values) - min(values)) / max(values) > AMOUNT_DISAGREEMENT


def round_from_row(row: dict) -> Round | None:
    """Rebuild the round the flags are judged on from a stored row."""
    if not row:
        return None
    return Round(
        company=row.get("company") or "", company_domain=row.get("domain") or "",
        stage=row.get("stage") or "", stage_raw=row.get("stage_raw") or "",
        amount_value=row.get("amount_value"), currency=row.get("currency") or "",
        region=row.get("region") or "", investors=row.get("investors") or [],
    )


def store_round(db: FundingDatabase, round_: Round, candidate, rules: Rules) -> tuple[str, bool]:
    """Write one extracted round and everything that evidences it."""
    company_id = db.upsert_company(round_)
    round_id, created = db.upsert_round(round_, company_id)

    for article in candidate.articles:
        db.add_round_source(round_id, article, round_.amount_value, round_.currency)
        db.record_article(article, headline_key(article.title), outcome="extracted",
                          company_hint=company_hint(article.title), round_id=round_id)
    if round_.investors:
        db.link_investors(round_id, round_.investors, lead=round_.lead_investor)

    # Corroboration and disagreement are properties of the evidence, so they are
    # judged after the sources are stored, not from the single article in hand. The
    # round is judged the same way: a later, thinner report of a round we already
    # hold in full must not demote it.
    merged = round_from_row(db.get_merged_round(round_id) or {}) or round_
    outlets = db.distinct_outlets(round_id)
    disputed = _amounts_disagree(db.round_sources(round_id))
    verdict = qualifies(merged, rules)
    db.set_round_flags(
        round_id,
        confirmed=is_confirmed(merged, distinct_outlets=outlets),
        amount_disputed=disputed,
        qualified=verdict.qualified,
        qualified_reason=verdict.reason,
    )
    return round_id, created


def run(*, config: dict | None = None, db: FundingDatabase | None = None) -> dict:
    """Run the pipeline once and return its stats."""
    settings.require_llm()
    config = config or load_config()
    rules = Rules.from_config(config)
    sectors = config.get("sectors", [])
    own_db = db is None
    db = db or FundingDatabase(settings.DATABASE_PATH)
    run_id, started_at = uuid4().hex, datetime.now(timezone.utc).isoformat()

    try:
        register_tracked_investors(db, config)
        found = discover(db, config)
        results, extract_stats = extract(found.candidates, sectors)

        stats = {**found.stats, **extract_stats, "new_rounds": 0, "updated_rounds": 0, "qualified": 0}
        for index, result in results.items():
            candidate = found.candidates[index]
            if isinstance(result, str):
                for article in candidate.articles:
                    db.record_article(article, headline_key(article.title), outcome="rejected",
                                      company_hint=company_hint(article.title))
                continue
            round_id, created = store_round(db, result, candidate, rules)
            stats["new_rounds" if created else "updated_rounds"] += 1
            if db.get_round(round_id)["qualified"]:
                stats["qualified"] += 1

        stats["source_issues"] = len(found.issues)
        db.record_run(run_id, started_at, stats)
        logger.info("Run complete: %s", stats)
        return stats
    finally:
        if own_db:
            db.close()


def recheck(db: FundingDatabase, config: dict | None = None) -> dict:
    """Recompute the flags on stored rounds after a rule or parsing change.

    Nothing is re-extracted and no model is called: this only re-applies the rules
    to what we already hold, so a change to the brief or to the currency handling
    does not need a full re-run to show up.
    """
    config = config or load_config()
    rules = Rules.from_config(config)
    changed = {"rounds": 0, "qualified_changed": 0, "disputed_changed": 0, "confirmed_changed": 0}
    for row in db.list_rounds(window_days=36500, qualified_only=False):
        round_ = round_from_row(row)
        verdict = qualifies(round_, rules)
        confirmed = is_confirmed(round_, distinct_outlets=db.distinct_outlets(row["round_id"]))
        disputed = _amounts_disagree(db.round_sources(row["round_id"]))
        changed["rounds"] += 1
        changed["qualified_changed"] += int(bool(row["qualified"]) != verdict.qualified)
        changed["confirmed_changed"] += int(bool(row["confirmed"]) != confirmed)
        changed["disputed_changed"] += int(bool(row["amount_disputed"]) != disputed)
        db.set_round_flags(row["round_id"], confirmed=confirmed, amount_disputed=disputed,
                           qualified=verdict.qualified, qualified_reason=verdict.reason)
    return changed


def dedupe(db: FundingDatabase, config: dict | None = None) -> dict:
    """Fold companies stored twice under two names into one, then re-apply the rules.

    "Kasvu" and "Kasvu Therapeutics" reported the same EUR 30M on the same day: one
    company, one round, two names. Nothing is re-extracted and no model is called.
    """
    stats = {"companies_merged": 0, "rounds_collapsed": 0}
    for source_id, target_id in db.duplicate_company_pairs():
        if db.get_company(source_id) is None or db.get_company(target_id) is None:
            continue  # already folded in by an earlier pair
        stats["rounds_collapsed"] += db.absorb_company(source_id, target_id)
        stats["companies_merged"] += 1
    if stats["companies_merged"]:
        stats.update(recheck(db, config))
    return stats
