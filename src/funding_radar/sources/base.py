"""Shared helpers for every source: HTTP, config, and headline keys."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import struct_time

import httpx
import yaml

from src.funding_radar.settings import settings

_TAG_RE = re.compile(r"<[^>]+>")
# " - Reuters", " | UKTN", " – EU-Startups": separator is space-delimited, but the
# publisher itself may contain hyphens.
_PUBLISHER_SUFFIX_RE = re.compile(r"\s+[-–—|]\s+[\w .&'-]{2,40}$")
_NOISE_RE = re.compile(r"[^a-z0-9 ]+")


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=settings.HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": settings.USER_AGENT},
    )


def load_config(path: Path | None = None) -> dict:
    with open(path or settings.CONFIG_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def strip_html(text: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text or "")).split())


def headline_key(title: str) -> str:
    """Normalised headline used to spot the same story across outlets.

    Google News appends the publisher ("... - Reuters"), so that tail is dropped
    before comparing; two outlets covering one raise then collapse to one key.
    """
    cleaned = _PUBLISHER_SUFFIX_RE.sub("", html.unescape(title or "").strip())
    cleaned = unicodedata.normalize("NFKD", cleaned)
    cleaned = "".join(ch for ch in cleaned if not unicodedata.combining(ch)).lower()
    return " ".join(_NOISE_RE.sub(" ", cleaned).split())


def to_iso(value: str | struct_time | None) -> str | None:
    if isinstance(value, struct_time):
        return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
    if not value:
        return None
    text = str(value).strip()
    for parser in (
        lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
        lambda t: datetime.strptime(t, "%a, %d %b %Y %H:%M:%S %Z"),
        lambda t: datetime.strptime(t, "%a, %d %b %Y %H:%M:%S %z"),
        lambda t: datetime.strptime(t, "%Y%m%dT%H%M%SZ"),
    ):
        try:
            parsed = parser(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except (ValueError, TypeError):
            continue
    return None


@dataclass
class SourceResult:
    """What one source returned in one run, so health can be recorded per source."""

    name: str
    kind: str
    articles: list
    error: str = ""


# "London's Metris Energy raises €4.35 million ..." -> "metris energy".
# These run against the normalised key, where punctuation is already gone, so a
# possessive reads as "london s" and a compound as "berlin based".
_PREFIX_RES = (
    re.compile(r"^(?:exclusive|breaking|update|just in)\s+"),
    re.compile(r"^[a-z]+\s+s\s+"),          # "london s"  <- "London's"
    re.compile(r"^[a-z]+\s+based\s+"),      # "berlin based" <- "Berlin-based"
    re.compile(
        r"^(?:uk|us|eu|british|dutch|french|german|swedish|danish|norwegian|finnish|"
        r"spanish|italian|irish|swiss|polish|estonian|icelandic|london|berlin|paris|"
        r"amsterdam|stockholm|madrid|dublin|munich|helsinki|copenhagen|oslo|zurich|"
        r"milan|barcelona|lisbon|warsaw|tallinn|manchester|cambridge|oxford)\s+"
    ),
    re.compile(
        r"^(?:ai|fintech|healthtech|insurtech|traveltech|deeptech|climate|legal|cyber|"
        r"defence|defense|biotech|edtech|proptech|adtech|foodtech|agritech|cloud|saas)\s+"
        r"(?:startup|scaleup|company|firm|platform|group|business)?\s*"
    ),
)
_FUNDING_VERB_SPLIT_RE = re.compile(
    r"\b(raises?|raised|raising|secures?|secured|closes?|closed|lands?|landed|nets?|netted|"
    r"bags?|bagged|announces?|announced|valued|becomes|hits|picks up|scores?|gets|receives|"
    r"doubles|triples|quadruples|quintuples|nearly|valuation|valued at)\b"
)


def company_hint(title: str) -> str:
    """Best guess at the company name from a headline, for coarse matching.

    Headlines vary across outlets ("raises £8m" vs "raises $11M"), so comparing
    them directly understates coverage; the company name is the stable part.
    """
    cleaned = headline_key(title)
    changed = True
    while changed:
        changed = False
        for pattern in _PREFIX_RES:
            stripped = pattern.sub("", cleaned, count=1).strip()
            if stripped != cleaned and stripped:
                cleaned, changed = stripped, True
    head = _FUNDING_VERB_SPLIT_RE.split(cleaned)[0].strip()
    words = head.split()
    # Company names are short; anything longer is usually a sentence about a sector.
    return " ".join(words[:4]) if words else ""
