"""Core types: an article found by a source, and a funding round extracted from it."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Optional

# Legal suffixes stripped before comparing company names, as token sequences so
# punctuation-free forms ("b v", "s a") match too.
_SUFFIX_TOKENS = (
    ("ltd",), ("limited",), ("inc",), ("llc",), ("plc",), ("gmbh",), ("ag",),
    ("oy",), ("ab",), ("as",), ("aps",), ("srl",), ("sas",), ("nv",), ("bv",),
    ("b", "v"), ("s", "a"), ("sa",), ("co",), ("corp",), ("holdings",), ("group",),
)
_NOISE_RE = re.compile(r"[^a-z0-9 ]+")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_company(name: str) -> str:
    """Casefold, de-accent, drop punctuation and legal suffixes for stable matching."""
    cleaned = _NOISE_RE.sub(" ", _strip_accents(name or "").lower())
    tokens = cleaned.split()
    changed = True
    while changed and tokens:
        changed = False
        for suffix in _SUFFIX_TOKENS:
            if len(tokens) > len(suffix) and tuple(tokens[-len(suffix):]) == suffix:
                tokens = tokens[: -len(suffix)]
                changed = True
                break
    return " ".join(tokens)


def normalize_domain(url: str) -> str:
    """Bare host: https://www.Acme.co.uk/about -> acme.co.uk"""
    if not url:
        return ""
    host = re.sub(r"^\w+://", "", url.strip().lower()).split("/")[0].split("?")[0]
    return host[4:] if host.startswith("www.") else host


@dataclass
class Article:
    """A candidate article, before any model call."""

    source: str
    source_kind: str  # rss | google_news | gdelt | vc_page | companies_house
    title: str
    url: str
    summary: str = ""
    published_at: Optional[str] = None

    @property
    def article_id(self) -> str:
        return sha256(self.url.strip().lower().encode()).hexdigest()[:32]


@dataclass
class Round:
    """A funding round as extracted from an article, before it is stored."""

    company: str
    summary: str = ""
    stage: str = ""            # normalised: pre-seed | seed | series a | ...
    stage_raw: str = ""        # what the article actually said
    amount_text: str = ""
    amount_value: float | None = None   # as reported, in `currency`
    currency: str = ""
    amount_usd: float | None = None     # rough, only for the size cut-off
    round_date: str | None = None
    investors: list[str] = field(default_factory=list)
    lead_investor: str = ""
    hq_city: str = ""
    hq_country: str = ""
    region: str = ""           # uk | europe | us | other
    sector: str = ""
    ai_native: bool = False
    company_domain: str = ""
    evidence: str = ""         # the sentence the amount came from
    confidence: float = 0.0
    confirmed: bool = False
    amount_disputed: bool = False
    qualified: bool = False

    @property
    def company_key(self) -> str:
        return normalize_company(self.company)

    def round_id_for(self, company_id: str) -> str:
        """Identity of a round: the company plus when it was announced.

        Stage is deliberately not part of the key. Outlets disagree about whether a
        raise is a seed or a Series A, and a round that changed label is still the
        same round; the date window in the store is what keeps them together.
        """
        month = (self.round_date or "")[:7]
        return sha256(f"{company_id}|{month}".encode()).hexdigest()[:32]
