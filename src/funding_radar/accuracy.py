"""Score the extractor against a hand-labelled set of real articles.

The labels in tests/fixtures/labelled_articles.json were written from the article
text, not from model output, so this measures the extractor rather than its
self-consistency. A null label means the headline does not support an answer and
the field is not scored; a "judgement" row is reported separately, because its
right answer is a preference rather than a fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.funding_radar.models import Article, Round, normalize_company
from src.funding_radar.qualify import normalize_stage

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "labelled_articles.json"
SCORED_FIELDS = ("company", "amount_value", "currency", "stage", "region")


def load_fixtures(path: Path | str = FIXTURES) -> list[dict]:
    data = json.loads(Path(path).read_text())
    articles = data["articles"]
    ids = [item["id"] for item in articles]
    if len(set(ids)) != len(ids):
        raise ValueError("labelled set has duplicate ids")
    return articles


def to_articles(fixtures: list[dict]) -> list[Article]:
    return [
        Article(
            source=item.get("outlet", "test"),
            source_kind="rss",
            title=item["title"],
            url=item.get("url") or f"https://labelled.test/{item['id']}",
            summary=item.get("summary", ""),
            published_at=item.get("published_at"),
        )
        for item in fixtures
    ]


def _matches(field_name: str, expected, result: Round) -> bool:
    if field_name == "company":
        return normalize_company(result.company) == normalize_company(str(expected))
    if field_name == "amount_value":
        return result.amount_value is not None and abs(result.amount_value - float(expected)) < 1
    if field_name == "currency":
        return result.currency.upper() == str(expected).upper()
    if field_name == "stage":
        return result.stage == (normalize_stage(str(expected)) or str(expected))
    if field_name == "region":
        return result.region == str(expected)
    raise KeyError(field_name)


@dataclass
class Miss:
    id: str
    field: str
    expected: object
    got: object
    judgement: bool = False

    def __str__(self) -> str:
        mark = "?" if self.judgement else "x"
        return f"  {mark} {self.id:<18} {self.field:<13} expected {self.expected!r}, got {self.got!r}"


@dataclass
class Report:
    totals: dict[str, list[int]] = field(default_factory=dict)  # field -> [correct, scored]
    misses: list[Miss] = field(default_factory=list)

    def _add(self, name: str, correct: bool) -> None:
        bucket = self.totals.setdefault(name, [0, 0])
        bucket[0] += int(correct)
        bucket[1] += 1

    @property
    def overall(self) -> tuple[int, int]:
        correct = sum(bucket[0] for bucket in self.totals.values())
        scored = sum(bucket[1] for bucket in self.totals.values())
        return correct, scored

    def format(self) -> str:
        lines = []
        for name, (correct, scored) in self.totals.items():
            pct = 100.0 * correct / scored if scored else 0.0
            lines.append(f"  {name:<14} {correct:>2}/{scored:<2}  {pct:5.1f}%")
        correct, scored = self.overall
        pct = 100.0 * correct / scored if scored else 0.0
        lines.append(f"  {'OVERALL':<14} {correct:>2}/{scored:<2}  {pct:5.1f}%")
        hard = [m for m in self.misses if not m.judgement]
        soft = [m for m in self.misses if m.judgement]
        if hard:
            lines.append("\nMisses:")
            lines += [str(m) for m in hard]
        if soft:
            lines.append("\nJudgement rows (not scored):")
            lines += [str(m) for m in soft]
        return "\n".join(lines)


def score(fixtures: list[dict], results: dict[int, Round | str]) -> Report:
    """Compare extractor output, keyed by fixture position, against the labels."""
    report = Report()
    for index, item in enumerate(fixtures):
        expect = item["expect"]
        judgement = bool(item.get("judgement"))
        result = results.get(index)
        got_round = isinstance(result, Round)
        correct = got_round == bool(expect["is_round"])
        if judgement:
            if not correct:
                report.misses.append(Miss(item["id"], "is_round", expect["is_round"], got_round, True))
        else:
            report._add("is_round", correct)
            if not correct:
                reason = result if isinstance(result, str) else "extracted a round"
                report.misses.append(Miss(item["id"], "is_round", expect["is_round"], reason))
        if not got_round:
            continue
        for name in SCORED_FIELDS:
            expected = expect.get(name)
            if expected is None:
                continue
            ok = _matches(name, expected, result)
            got = getattr(result, name)
            if judgement:
                if not ok:
                    report.misses.append(Miss(item["id"], name, expected, got, True))
                continue
            report._add(name, ok)
            if not ok:
                report.misses.append(Miss(item["id"], name, expected, got))
    return report
