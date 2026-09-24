"""Does this round belong on the list?

Three questions, in cheap-to-expensive order: is it in a region we cover, at a
stage we cover, and large enough. The size floor is per-investor: a company out of
Antler or Entrepreneur First raises far less at the same point in its life, and is
exactly the sort of company that needs fractional product help.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.funding_radar.models import Round, normalize_company

# Rough conversion, used only to apply the size floor. A round reported in dollars,
# pounds or euros is compared as-is; anything else is converted approximately.
APPROX_USD = {"USD": 1.0, "GBP": 1.0, "EUR": 1.0, "CHF": 1.1, "SEK": 0.095, "NOK": 0.09,
              "DKK": 0.14, "PLN": 0.25, "ILS": 0.27}

# Real rates, for comparing two outlets' figures with each other rather than with a
# floor. Sifted's "£8m" and Dealroom's "$11M" are the same round, and treating the
# pound as a dollar would report them as a contradiction.
COMPARISON_USD = {"USD": 1.0, "GBP": 1.34, "EUR": 1.08, "CHF": 1.25, "SEK": 0.105,
                  "NOK": 0.10, "DKK": 0.145, "PLN": 0.27, "ILS": 0.30}

STAGE_PATTERNS = (
    ("pre-seed", r"\bpre[\s-]?seed\b"),
    ("seed", r"\bseed\b"),
    ("series a", r"\bseries\s*a\b"),
    ("series b", r"\bseries\s*b\b"),
    ("series c", r"\bseries\s*c\b"),
    ("series d+", r"\bseries\s*[d-z]\b"),
    ("growth", r"\b(growth|pre[\s-]?ipo|late stage)\b"),
    ("grant", r"\bgrant\b"),
    ("debt", r"\b(venture debt|debt facility|loan)\b"),
)


def normalize_stage(text: str) -> str:
    """Map whatever the article said onto one label. Empty when it says nothing."""
    lowered = (text or "").lower()
    for label, pattern in STAGE_PATTERNS:
        if re.search(pattern, lowered):
            return label
    return ""


# A studio raises money to build other companies, so it is a real round but a
# different kind of prospect. Flagged rather than dropped, for the site's filter.
STUDIO_RE = re.compile(
    r"\b(venture|startup|start-up|company|venture-)?\s?(studio|builder)\b|"
    r"\bventure building\b|\bcompany builder\b|\bbuilds? (other )?(startups|companies)\b", re.I)


def looks_like_studio(name: str, summary: str = "") -> bool:
    text = f"{name} {summary}"
    if not STUDIO_RE.search(text):
        return False
    # "game studio", "design studio" and the like are ordinary companies.
    return not re.search(r"\b(game|games|gaming|design|film|music|animation|yoga|pilates|photo|recording)\s+studio\b", text, re.I)


def approx_usd(amount: float | None, currency: str) -> float | None:
    """For the size floor, where $2M, £2M and €2M all count as the bar."""
    if amount is None:
        return None
    return amount * APPROX_USD.get((currency or "USD").upper(), 1.0)


def comparable_usd(amount: float | None, currency: str) -> float | None:
    """For comparing outlets against each other, where the rate has to be real."""
    if amount is None:
        return None
    return amount * COMPARISON_USD.get((currency or "USD").upper(), 1.0)


@dataclass
class Rules:
    min_amount: float
    stages: tuple[str, ...]
    regions: tuple[str, ...]
    investor_floors: dict[str, float]  # normalised investor name -> its own floor

    @classmethod
    def from_config(cls, config: dict) -> "Rules":
        filters = config.get("filters", {})
        floors = {
            normalize_company(name): float(amount)
            for name, amount in (filters.get("investor_floors") or {}).items()
        }
        return cls(
            min_amount=float(filters.get("min_amount", 2_000_000)),
            stages=tuple(s.lower() for s in filters.get("stages", [])),
            regions=tuple(r.lower() for r in filters.get("regions", [])),
            investor_floors=floors,
        )

    def floor_for(self, investors: list[str]) -> float:
        """The lowest floor any of this round's investors earns."""
        floors = [self.investor_floors[normalize_company(name)]
                  for name in investors if normalize_company(name) in self.investor_floors]
        return min([self.min_amount, *floors]) if floors else self.min_amount


@dataclass
class Verdict:
    qualified: bool
    reason: str = ""


def qualifies(round_: Round, rules: Rules) -> Verdict:
    stage = normalize_stage(round_.stage or round_.stage_raw)
    if rules.regions and (round_.region or "").lower() not in rules.regions:
        return Verdict(False, f"region {round_.region or 'unknown'} is outside the brief")
    if stage and rules.stages and stage not in rules.stages:
        return Verdict(False, f"stage {stage} is outside the brief")
    # Headlines often name no stage at all. Dropping those would lose real rounds,
    # so they qualify on size alone and stay in the unconfirmed tier.

    floor = rules.floor_for(round_.investors)
    amount = approx_usd(round_.amount_value, round_.currency)
    if amount is None:
        # An undisclosed amount at the right stage is still worth seeing; it stays
        # unconfirmed until a number appears.
        return Verdict(True, "amount undisclosed")
    if amount < floor:
        return Verdict(False, f"{amount:,.0f} is below the {floor:,.0f} floor")
    if floor < rules.min_amount:
        return Verdict(True, f"below the usual floor but backed by a tracked early-stage fund")
    return Verdict(True, "")


def is_confirmed(round_: Round, *, distinct_outlets: int = 1) -> bool:
    """Confirmed means we know who raised, how much, and at what stage.

    A resolved company website is the strongest signal, but two independent outlets
    reporting the same round is equally good evidence that it happened.
    """
    has_specifics = bool(round_.amount_value and normalize_stage(round_.stage or round_.stage_raw))
    resolved = bool(round_.company_domain) or distinct_outlets >= 2
    return bool(has_specifics and resolved)
