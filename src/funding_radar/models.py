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
    """A funding round extracted from one article."""

    company: str
    summary: str = ""
    stage: str = ""
    amount_text: str = ""
    amount_value: Optional[float] = None
    currency: str = ""
    round_date: Optional[str] = None
    investors: str = ""
    hq_city: str = ""
    hq_country: str = ""
    region: str = ""  # uk | europe | us | other
    sector: str = ""
    ai_native: bool = False
    company_domain: str = ""
    evidence: str = ""  # the sentence the amount came from
    confidence: float = 0.0
    # Filled in later stages
    fit_score: Optional[float] = None
    fit_reason: str = ""
    angle: str = ""  # product leadership | pricing | growth
    confirmed: bool = False
    amount_disputed: bool = False
    sources: list[dict] = field(default_factory=list)

    @property
    def company_key(self) -> str:
        return normalize_company(self.company)

    @property
    def round_id(self) -> str:
        """Identity of the round itself, not the article.

        Domain when we have one, else the normalised name; the month keeps repeat
        raises apart. Cross-month duplicates are caught by the resolver's date window.
        """
        key = self.company_domain or normalize_company(self.company)
        month = (self.round_date or "")[:7]
        return sha256(f"{key}|{self.stage.lower()}|{month}".encode()).hexdigest()[:32]
